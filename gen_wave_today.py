#!/usr/bin/env python3
"""今日波浪dashboard刷新: QQE柱 + 80%衰减线 + 翻仓门槛线, 白底ECharts风格。

数据源: 与 watcher_v4 完全一致 (warmup缓存 + 今日1m->3m聚合, 只取完整bar)。
QQE/真色/衰减/翻仓need 全部按 signal_engine(k3clean L25当前基线) 的真实算法逐bar重放,
不手绘。显示窗口: 今日 09:30 -> 最新完整bar。

用法: python3 gen_wave_today.py
输出: ~/workspace/alpaca_watcher/wave-today-YYYYMMDD.html
"""
import sys, os, json, re
import bisect
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # 同目录优先 (GitHub Actions 部署包)
sys.path.insert(0, "/home/hatch/workspace/qqe_backtest")
sys.path.insert(0, "/home/hatch/workspace/alpaca_watcher")
from qqe_mod import qqe_mod

ET = ZoneInfo("America/New_York")
WDIR = os.environ.get("WAVE_OUT_DIR", "/home/hatch/workspace/alpaca_watcher")
WAVE_OUT_NAME_TMPL = os.environ.get("WAVE_OUT_NAME", "")  # 设了就用固定名 (如 index.html)
_ar = int(os.environ.get("WAVE_AUTOREFRESH", "0") or 0)
refresh_tag = ""  # user 2026-11-19 定版: 页面永不自动刷新浏览器 (想看最新自己点刷新); underlying 页面由 workflow 持续更新即可, WAVE_AUTOREFRESH 不再用
WAVE_DATE = os.environ.get("WAVE_DATE", "").strip()  # 回放日期 YYYY-MM-DD, 空=今天

import watcher_v4 as w4
import vh_live as vh_live
# 热力背景色 (昨天gen_heat_html_pierce2.py的15色)
HEAT_COLORS = [
    'rgba(120,180,220,0.12)', 'rgba(138,182,201,0.16)', 'rgba(156,185,182,0.19)',
    'rgba(174,188,164,0.23)', 'rgba(192,190,145,0.27)', 'rgba(210,193,126,0.30)',
    'rgba(228,196,108,0.34)', 'rgba(246,198,89,0.38)', 'rgba(255,186,74,0.41)',
    'rgba(255,160,64,0.45)', 'rgba(255,133,53,0.49)', 'rgba(255,106,42,0.52)',
    'rgba(255,79,31,0.56)', 'rgba(255,53,21,0.60)', 'rgba(255,26,10,0.63)',
]
def heat_color(v):
    idx = int(max(0, min(14, v)))
    return HEAT_COLORS[idx]

from signal_engine import (run_signals, get_heat_edges, get_heat_arr, get_heat_bins, get_heat_sm, detect_red_decay, detect_blue_decay,
                           SIG_MIN_LEN)

BLUE, RED, GRAY = "#2f80ed", "#e5484d", "#b0b0b0"

def gamma_roi(pos, open_px, close_px):
    """Gamma公式，返回百分比。"""
    d = close_px - open_px
    if pos == 'call':
        r = 9.88 * d + 0.311 * d * d
        if d < 0:
            r = min(r, 0.0)
    else:
        r = -9.88 * d + 0.311 * d * d
        if d > 0:
            r = min(r, 0.0)
    return max(r, -20.0)


def gamma_roi_100(pos, open_px, close_px):
    """Gamma公式, 单笔floor -100 (VH/QQE预估表用; 不要用 gamma_roi 的-20 cap)。返回 fraction。"""
    d = close_px - open_px
    if pos == 'call':
        r = 9.88 * d + 0.311 * d * d
        if d < 0:
            r = min(r, 0.0)
    else:
        r = -9.88 * d + 0.311 * d * d
        if d > 0:
            r = min(r, 0.0)
    return max(r, -100.0) / 100.0


# ---- Envelope (TradingView SPY Adaptive Envelope v3.9 With-trend) ----
# 移植自 research/tv_envelope_v39_withtrend_trendflip.py (compute_indicators + simulate):
# 高斯中线权重已纠正 (最新根权重最大); 平移通道 + 20% 边缘区; 趋势政策 With-trend-only;
# 趋势翻仓开 (强趋势上升沿顺势开, 可绕过 EXIT T 同根翻仓, EOD 不可绕过)。
# 参数 (用户 2026-11-09 拍板全覆盖): 1mEnvelope V2 (研究基线) + 止损 0.5xATR,
# 替代原 envelope_adx30 套 (MULT 1.05/ATR 12/ER 17/DI 8/ADX平滑 12/MOM 15/EMA 5/
# TSLOPE 0.13/SL 1.5); 展示层另把止损+同向接点单合并成一笔 (见 _env_merge_junctions)。
# 生成器内联 (与 CE 的 _ce_compute 同款做法, 保证 GitHub Actions 部署包自包含)。
_ENV_GAUSS_LEN = 30
_ENV_GAUSS_BW = 3.5
_ENV_ATR_LEN = 8
_ENV_MULT = 1.00
_ENV_SLOPE_LEN = 7
_ENV_ASYM_SHIFT = 1.35
_ENV_FAST_EMA_LEN = 3
_ENV_MOM_LOOKBACK = 10
_ENV_MOM_SENS = 0.55
_ENV_MAX_SHIFT_ATR = 0.50
_ENV_EDGE_PCT = 20.0
_ENV_MAX_OUTSIDE = 0.55
_ENV_ER_LEN = 12
_ENV_ER_TH = 0.35
_ENV_DI_LEN = 16
_ENV_ADX_SMOOTH = 16
_ENV_ADX_TH = 30
_ENV_TAKEOVER_SLOPE = 0.20
_ENV_SL_ATR = 0.5


def _env_rma(x, n):
    """Pine ta.rma: 前 n-1 根 na, 第 n 根 = 前 n 根 SMA, 之后 alpha=1/n 平滑。"""
    x = np.asarray(x, dtype=float)
    out = np.full(len(x), np.nan)
    if len(x) < n:
        return out
    out[n - 1] = np.nanmean(x[:n])
    a = 1.0 / n
    for i in range(n, len(x)):
        out[i] = a * x[i] + (1 - a) * out[i - 1]
    return out


def _env_indicators(df):
    """df: 1m bars (t/open/high/low/close)。返回 dict of arrays (与 tv 模块同款)。"""
    o = df["open"].values.astype(float)
    h = df["high"].values.astype(float)
    l = df["low"].values.astype(float)
    c = df["close"].values.astype(float)
    n = len(df)
    prev_c = np.empty(n); prev_c[0] = np.nan; prev_c[1:] = c[:-1]
    tr = np.maximum(h - l, np.maximum(np.abs(h - prev_c), np.abs(l - prev_c)))
    tr[0] = h[0] - l[0]
    # Gap-Adjusted ATR (envelope_adx30 正式版 2026-11-07): 每日首根 RTH bar (09:30)
    # 的 TR = high-low, 不含隔夜跳空; 其余 bar 用正常 TR。DMI (DI/ADX) 始终用正常 TR。
    times = df["t"]
    sm = times.dt.hour.values * 60 + times.dt.minute.values
    dates = times.dt.strftime("%Y-%m-%d").values
    in_rth = (sm >= 570) & (sm < 960)
    first_rth = np.zeros(n, dtype=bool)
    for i in range(n):
        if not in_rth[i]:
            continue
        if i == 0 or (not in_rth[i - 1]) or (dates[i] != dates[i - 1]):
            first_rth[i] = True
    tr_atr = np.where(first_rth, h - l, tr)
    atr = _env_rma(tr_atr, _ENV_ATR_LEN)
    w = np.exp(-0.5 * (np.arange(_ENV_GAUSS_LEN) / _ENV_GAUSS_BW) ** 2)
    mid = np.full(n, np.nan)
    for i in range(n):
        k = min(i + 1, _ENV_GAUSS_LEN)
        # Pine close[i] = i根前, 最新根权重 w[0]=1 最大 (原移植把权重配反过, 已纠正)
        mid[i] = np.sum(c[i - k + 1:i + 1] * w[:k][::-1]) / np.sum(w[:k])
    slope = np.full(n, np.nan)
    slope[_ENV_SLOPE_LEN:] = (mid[_ENV_SLOPE_LEN:] - mid[:-_ENV_SLOPE_LEN]) / _ENV_SLOPE_LEN
    ema = np.full(n, np.nan)
    if n > 0:
        ema[0] = c[0]
        a_ema = 2.0 / (_ENV_FAST_EMA_LEN + 1)
        for i in range(1, n):
            ema[i] = a_ema * c[i] + (1 - a_ema) * ema[i - 1]
    mom = np.full(n, np.nan)
    mom[_ENV_MOM_LOOKBACK:] = (ema[_ENV_MOM_LOOKBACK:] - ema[:-_ENV_MOM_LOOKBACK]) / _ENV_MOM_LOOKBACK
    with np.errstate(invalid="ignore", divide="ignore"):
        nmom = mom / (atr * _ENV_MOM_SENS)
        smooth = nmom / np.sqrt(1.0 + nmom * nmom)
        raw_shift = atr * _ENV_MULT * _ENV_ASYM_SHIFT * smooth
        shift = np.clip(raw_shift, -_ENV_MAX_SHIFT_ATR * atr, _ENV_MAX_SHIFT_ATR * atr)
    width = _ENV_MULT * atr
    upper = mid + shift + width
    lower = mid + shift - width
    chan_mid = (upper + lower) / 2.0
    chg = np.abs(np.diff(c, prepend=np.nan))
    chg[0] = 0.0
    er = np.full(n, np.nan)
    for i in range(_ENV_ER_LEN, n):
        noise = np.sum(chg[i - _ENV_ER_LEN + 1:i + 1])
        er[i] = abs(c[i] - c[i - _ENV_ER_LEN]) / noise if noise > 0 else 0.0
    prev_h = np.empty(n); prev_h[0] = np.nan; prev_h[1:] = h[:-1]
    prev_l = np.empty(n); prev_l[0] = np.nan; prev_l[1:] = l[:-1]
    up = h - prev_h; dn = prev_l - l
    up[0] = 0.0; dn[0] = 0.0
    pdm = np.where((up > 0) & (up > dn), up, 0.0)
    mdm = np.where((dn > 0) & (dn > up), dn, 0.0)
    r_tr = _env_rma(tr, _ENV_DI_LEN)
    r_pdm = _env_rma(pdm, _ENV_DI_LEN)
    r_mdm = _env_rma(mdm, _ENV_DI_LEN)
    with np.errstate(invalid="ignore", divide="ignore"):
        dip = 100 * r_pdm / r_tr
        dim = 100 * r_mdm / r_tr
        dx = 100 * np.abs(dip - dim) / (dip + dim)
    adx = _env_rma(np.where(np.isnan(dx), 0.0, dx), _ENV_ADX_SMOOTH)
    adx[:_ENV_DI_LEN + _ENV_ADX_SMOOTH - 1] = np.nan
    return dict(open=o, high=h, low=l, close=c, atr=atr, mid=mid, slope=slope,
                upper=upper, lower=lower, chan_mid=chan_mid, er=er, adx=adx)


def _env_simulate(df, ind, day_str):
    """Pine 语义模拟 (bar 收盘执行), 返回 (trades, open_pos, trend_dir)。
    trades: [(entry_bar, exit_bar, dir, entry_px, exit_px, reason)] (仅当日进场);
    open_pos: 盘末未平仓 {dir, px0, obi, entry_kind} 或 None (日内盘中用, 仅完整日才日终平,
    由调用方按 _env_eod 决定是否转为日终平仓单)。
    trend_dir: 每根趋势方向 +1 强涨 / -1 强跌 / 0 中性, 与 df 行对齐。"""
    n = len(df)
    times = df["t"]
    sm = times.dt.hour.values * 60 + times.dt.minute.values
    dates = times.dt.strftime("%Y-%m-%d").values
    c, h, l = ind["close"], ind["high"], ind["low"]
    atr, upper, lower, chan_mid = ind["atr"], ind["upper"], ind["lower"], ind["chan_mid"]
    slope, er, adx = ind["slope"], ind["er"], ind["adx"]
    pos = 0
    entry_px = entry_atr = None
    entry_bar = -1
    entry_date = None
    entry_kind = None
    prev_strong_up = prev_strong_down = False
    trades = []
    trend_dir = []  # 每根趋势方向: +1 强涨 / -1 强跌 / 0 中性 (供 1m 曲线分段着色)

    def close_pos(bar, px, reason):
        nonlocal pos, entry_px, entry_atr, entry_bar, entry_date, entry_kind
        if pos == 0:
            return
        d = "long" if pos == 1 else "short"
        if entry_date is not None and entry_date == day_str:
            trades.append((entry_bar, bar, d, entry_px, px, reason))
        pos = 0; entry_px = entry_atr = None; entry_bar = -1; entry_date = None
        entry_kind = None

    for i in range(n):
        dstr = dates[i]
        if dstr >= day_str and pos != 0 and (entry_date or "") < day_str:
            pos = 0; entry_px = entry_atr = None; entry_bar = -1; entry_date = None
            entry_kind = None
        s = sm[i]
        close_min = s + 1
        in_session = (570 <= s <= 955) and (close_min < 945)
        eod = (957 <= close_min <= 960)
        long_sig = short_sig = False
        if i >= 1 and not (np.isnan(upper[i - 1]) or np.isnan(lower[i - 1])
                            or np.isnan(atr[i]) or np.isnan(chan_mid[i])):
            tU, tL = upper[i - 1], lower[i - 1]
            width = tU - tL
            if width > 0:
                buy_top = tL + width * _ENV_EDGE_PCT / 100.0
                sell_bot = tU - width * _ENV_EDGE_PCT / 100.0
                low_touch = (l[i] <= buy_top) and (h[i] >= tL - _ENV_MAX_OUTSIDE * atr[i])
                up_touch = (h[i] >= sell_bot) and (l[i] <= tU + _ENV_MAX_OUTSIDE * atr[i])
                ls = low_touch and (c[i] <= chan_mid[i])
                ss = up_touch and (c[i] >= chan_mid[i])
                if not (ls and ss):
                    long_sig = ls and in_session
                    short_sig = ss and in_session
        ind_ok = not (np.isnan(er[i]) or np.isnan(adx[i])
                      or np.isnan(slope[i]) or np.isnan(atr[i]))
        allow_long = allow_short = False
        strong_down = strong_up = False
        if ind_ok:
            trending = (er[i] >= _ENV_ER_TH) and (adx[i] >= _ENV_ADX_TH)
            strong_down = trending and (slope[i] < -_ENV_TAKEOVER_SLOPE * atr[i])
            strong_up = trending and (slope[i] > _ENV_TAKEOVER_SLOPE * atr[i])
            allow_long = not strong_down
            allow_short = not strong_up
        trend_dir.append(1 if strong_up else (-1 if strong_down else 0))
        # ---- 趋势入场: 强趋势上升沿 (edge-triggered), 顺势开 ----
        edge_up = strong_up and not prev_strong_up
        edge_down = strong_down and not prev_strong_down
        trend_long_sig = edge_up and in_session
        trend_short_sig = edge_down and in_session
        if pos != 0 and i > entry_bar:
            if pos == 1 and l[i] <= entry_px - _ENV_SL_ATR * entry_atr:
                close_pos(i, entry_px - _ENV_SL_ATR * entry_atr, "SL")
            elif pos == -1 and h[i] >= entry_px + _ENV_SL_ATR * entry_atr:
                close_pos(i, entry_px + _ENV_SL_ATR * entry_atr, "SL")
        force_exit = False
        force_exit_regime = False
        if pos != 0:
            exit_regime = (strong_down and pos == 1) or (strong_up and pos == -1)
            exit_eod = eod
            if exit_regime or exit_eod:
                force_exit = True
                # 翻仓版: 只有纯 EXIT T (非 EOD) 的 force_exit 才可被趋势信号绕过
                force_exit_regime = bool(exit_regime and not exit_eod)
                close_pos(i, c[i], "EOD" if exit_eod else "EXIT T")
        # 边缘信号与趋势信号 OR 合并 (同向同bar只开一单); 趋势信号可绕过 EXIT T 的 force_exit
        # (同根翻仓); EOD 不可绕过, 边缘信号也不可绕过。
        long_bypass = bool(trend_long_sig and force_exit_regime)
        short_bypass = bool(trend_short_sig and force_exit_regime)
        can_long = allow_long and ((not force_exit) or long_bypass) and (long_sig or trend_long_sig) and (pos == 0 or pos == -1)
        can_short = allow_short and ((not force_exit) or short_bypass) and (short_sig or trend_short_sig) and (pos == 0 or pos == 1)
        if can_long:
            entry_kind = "flip" if pos == -1 else "open"
            if pos == -1:
                close_pos(i, c[i], "FLIP")
            pos = 1; entry_px = c[i]; entry_atr = atr[i]; entry_bar = i; entry_date = dstr
        elif can_short:
            entry_kind = "flip" if pos == 1 else "open"
            if pos == 1:
                close_pos(i, c[i], "FLIP")
            pos = -1; entry_px = c[i]; entry_atr = atr[i]; entry_bar = i; entry_date = dstr
        prev_strong_up = strong_up
        prev_strong_down = strong_down
    open_pos = None
    if pos != 0:
        open_pos = {"dir": "long" if pos == 1 else "short", "px0": float(entry_px),
                    "obi": int(entry_bar), "entry_kind": entry_kind or "open"}
    return trades, open_pos, trend_dir


def _env_merge_junctions(raw):
    """接点合并 (展示层, 与价差合并口径同一定义; 用户 2026-11-09 令):
    一笔以 SL 出场、出场 bar == 下一笔入场 bar、且同向 -> 并成一笔
    (入场取链首、出场取链末, 出场原因取链末); 链式接点继续并。
    raw: [(entry_bar, exit_bar, dir, entry_px, exit_px, reason), ...] (时间序)。
    返回 (merged_raw, n_groups, is_chain): n_groups = 发生合并的组数 (每条链
    算 1 组); is_chain 与 merged_raw 对齐, True = 该笔由接点链并成 (仅作
    标记; 展示层把合并行当作一笔普通单画圈 — 链首入场 + 链尾出场 + 合并
    ROI 散点, 只隐藏链内止损+同向重开的中间标记; 用户 2026-11-09 纠正:
    初版整链不画圈误伤了翻仓/反向开仓圈)。"""
    out = []
    flags = []
    groups = 0
    i = 0
    while i < len(raw):
        j = i
        while (j + 1 < len(raw) and raw[j][5] == "SL"
               and raw[j + 1][0] == raw[j][1] and raw[j + 1][2] == raw[j][2]):
            j += 1
        if j > i:
            groups += 1
            out.append((raw[i][0], raw[j][1], raw[i][2],
                        raw[i][3], raw[j][4], raw[j][5]))
            flags.append(True)
        else:
            out.append(raw[i])
            flags.append(False)
        i = j + 1
    return out, groups, flags


def _vh_kind(rs):
    """按开仓原因分 ST/QQE 单 (照抄 research/settle_hybrid.classify)。"""
    rs = rs or ""
    if "ST反向" in rs:
        return "inv"
    if "ST" in rs or "11:00强制切开" in rs:
        return "st"
    return "qqe"


def _data_api(path, params, retries=3):
    """Alpaca data API 直调 (不依赖 w4.api)。
    根因 (2026-11-05): 旧代码调 w4.api(..., base=w4.DATA); 本地因 sys.path 影子
    (alpaca_watcher/watcher_v4 从 trader 导入带 base 参数的 api) 能跑, GitHub
    Actions 干净 checkout 下 w4 是部署包自带的 watcher_v4, 其 api(path, params,
    retries) 没有 base 参数 -> TypeError, 被 envelope 的 try/except 吃掉, 当天
    Envelope 版全天 $0、轨道全空。"""
    import time as _time
    import urllib.parse as _up
    import urllib.request as _ur
    base = "https://data.alpaca.markets/v2"
    key = os.environ["ALPACA_KEY"]
    secret = os.environ["ALPACA_SECRET"]
    q = _up.urlencode(params)
    last_err = None
    for attempt in range(retries):
        try:
            req = _ur.Request(base + path + "?" + q,
                              headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret})
            with _ur.urlopen(req, timeout=25) as r:
                return json.load(r)
        except Exception as e:
            last_err = e
            if attempt < retries - 1:
                _time.sleep(2 ** attempt)
    raise last_err


def _warmup_before(day):
    """某日前 7 天的 3m warmup (回放用, 不碰生产 load_history)。"""
    start = (day - timedelta(days=7)).strftime("%Y-%m-%dT00:00:00Z")
    end = day.strftime("%Y-%m-%dT00:00:00Z")
    params = {"symbols": "SPY", "timeframe": "3Min", "limit": 1000, "feed": "iex",
              "sort": "desc", "start": start, "end": end}
    data = _data_api("/stocks/bars", params)
    bars = data.get("bars", {}).get("SPY", [])
    if not bars:
        raise RuntimeError(f"no warmup bars before {day}")
    df = pd.DataFrame(bars)
    df["t"] = pd.to_datetime(df["t"], utc=True).dt.tz_convert(ET)
    df = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
    df = df[(df["t"].dt.strftime("%H:%M") >= "09:30") &
            (df["t"].dt.strftime("%H:%M") < "16:00")].copy()
    return pd.DataFrame({"t": df["t"], "o": df["open"], "h": df["high"],
                         "l": df["low"], "c": df["close"], "v": df["volume"]})


def _fetch_1m_day(day):
    """某天的全部 1m (回放用)。"""
    s_et = datetime(day.year, day.month, day.day, 9, 30, tzinfo=ET)
    e_et = datetime(day.year, day.month, day.day, 16, 0, tzinfo=ET)
    params = {"symbols": "SPY", "timeframe": "1Min",
              "start": s_et.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "end": e_et.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "limit": 10000, "feed": "iex", "sort": "asc"}
    data = _data_api("/stocks/bars", params)
    bars = data.get("bars", {}).get("SPY", [])
    df = pd.DataFrame(bars)
    if df.empty:
        raise RuntimeError(f"no 1m bars for {day}")
    df["t"] = pd.to_datetime(df["t"], utc=True).dt.tz_convert(ET)
    return df[["t", "o", "h", "l", "c", "v"]].sort_values("t").reset_index(drop=True)


def _fetch_1m_range(start_day, end_day):
    """start_day 09:30 -> end_day 16:00 的全部 1m (Envelope warmup 用)。
    API 数据带时区, utc=True 转美东是正确归一化 (与 _fetch_1m_day 同款);
    绝不用于本地缓存 (缓存 naive 行是美东墙钟, 不可 utc=True)。"""
    s_et = datetime(start_day.year, start_day.month, start_day.day, 9, 30, tzinfo=ET)
    e_et = datetime(end_day.year, end_day.month, end_day.day, 16, 0, tzinfo=ET)
    params = {"symbols": "SPY", "timeframe": "1Min",
              "start": s_et.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "end": e_et.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "limit": 10000, "feed": "iex", "sort": "asc"}
    data = _data_api("/stocks/bars", params)
    bars = data.get("bars", {}).get("SPY", [])
    df = pd.DataFrame(bars)
    if df.empty:
        raise RuntimeError(f"no 1m bars for {start_day}..{end_day}")
    df["t"] = pd.to_datetime(df["t"], utc=True).dt.tz_convert(ET)
    return df[["t", "o", "h", "l", "c", "v"]].sort_values("t").reset_index(drop=True)


def _env_load_cache_1m(start_day, end_day):
    """回放时读本地 1m 缓存 (与注入器同源)。缓存是 IEX/SIP 混合:
    10-30/10-22 与 IEX API 逐根一致, 10-13 与 SIP API 逐根一致,
    单一 feed 的 API 无法同时复现三日 (10-13 IEX 缺 13:43/14:34 且 283 根收盘差几分)。
    缓存不存在/覆盖不足时返回 None (调用方走 API)。
    时区铁律: naive 行就是美东墙钟 (与 tv.load_bars 修后同款分流), 绝不 utc=True。"""
    pkl = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                       "research", "data_cache", "spy_1m.pkl")
    if not os.path.exists(pkl):
        return None
    try:
        df = pd.read_pickle(pkl)
    except Exception:
        return None
    s = df["t"].astype(str)
    aware_mask = s.str.contains(r"[+-]\d{2}:?\d{2}$", regex=True)
    t_aware = pd.to_datetime(df.loc[aware_mask, "t"], utc=True
                             ).dt.tz_convert("America/New_York").dt.tz_localize(None)
    t_naive = pd.to_datetime(df.loc[~aware_mask, "t"])
    df = df.assign(_t=pd.concat([t_aware, t_naive]).sort_index())
    df["_t"] = pd.to_datetime(df["_t"])
    m = (df["_t"].dt.date >= start_day) & (df["_t"].dt.date <= end_day)
    df = df[m].copy()
    if df.empty:
        return None
    mins = df["_t"].dt.hour * 60 + df["_t"].dt.minute
    df = df[(mins >= 570) & (mins <= 959)].sort_values("_t").drop_duplicates("_t", keep="last")
    n_today = int((df["_t"].dt.date == end_day).sum())
    if n_today < 100:  # 覆盖不足 (如 10-21 仅 151 根下午缺失) -> 走 API
        return None
    out = pd.DataFrame({"t": df["_t"].values, "open": df["open"].values,
                        "high": df["high"].values, "low": df["low"].values,
                        "close": df["close"].values}).reset_index(drop=True)
    _sanitize_bars(out)  # 坏bar剔除: 本地 1m 缓存的幻影 print 不得毒化 Envelope 暖机
    return out


def fetch_day_fills(day_str, live=True):
    """当日 Alpaca 期权成交 + 持仓未实现 + 账户权益 (仅实盘日用, 回放跳过)。"""
    import trader as trt
    fills, poss, equity = [], {}, None
    try:
        d = datetime.strptime(day_str, "%Y-%m-%d").date()
        s_utc = datetime(d.year, d.month, d.day, 0, 0, tzinfo=ET).astimezone(
            ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")
        e_utc = (datetime(d.year, d.month, d.day, 0, 0, tzinfo=ET)
                 + timedelta(days=1)).astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")
        orders = trt.api("/v2/orders", params={"status": "all", "direction": "desc",
                                               "limit": 500, "after": s_utc, "until": e_utc})
        for o in orders or []:
            if o.get("status") not in ("filled", "partially_filled"):
                continue
            fq = float(o.get("filled_qty") or 0)
            if fq <= 0:
                continue
            try:
                ft = datetime.fromisoformat(
                    str(o.get("filled_at")).replace("Z", "+00:00")).astimezone(ET)
            except Exception:
                continue
            if ft.strftime("%Y-%m-%d") != day_str:
                continue
            fills.append({"t": ft, "symbol": o.get("symbol"), "side": o.get("side"),
                          "qty": fq, "px": float(o.get("filled_avg_price") or 0)})
    except Exception as e:
        print(f"fills fetch failed: {e}", flush=True)
    day_open_equity = None
    if live:
        try:
            for p in trt.api("/v2/positions") or []:
                poss[p["symbol"]] = {"qty": float(p["qty"]),
                                     "avg_px": float(p.get("avg_entry_price") or 0),
                                     "upl": float(p.get("unrealized_pl") or 0),
                                     "uplpc": float(p.get("unrealized_plpc") or 0)}
        except Exception as e:
            print(f"positions fetch failed: {e}", flush=True)
        try:
            equity = float((trt.api("/v2/account") or {}).get("equity") or 0)
        except Exception:
            pass
        # 当日开盘资金：从portfolio history拿09:30的equity (2026-10-14 用户要求直接从Alpaca拉)
        try:
            _ph = trt.api("/v2/account/portfolio/history?period=1D&timeframe=5Min") or {}
            _eq = _ph.get("equity") or []
            if _eq:
                # 取第一个有效值作为开盘资金
                day_open_equity = float(_eq[0]) if _eq[0] else None
        except Exception:
            pass
    fills.sort(key=lambda x: x["t"])
    return fills, poss, equity, day_open_equity


# --- bar_sanitize: 坏 bar(单边影线幻影 print)剔除 (2026-11-20 user 令全修; 各管道同文) ---
# 规则: 第 i 根(i>=10) ATRref = 仅用 <i 的 bar 的 Wilder RMA(10) TR;
#   high 侧命中: 上影 high-max(o,c) > 4*ATRref 且占整根 range >60% 且下影 <= 1*ATRref; low 侧对称。
#   真实暴涨/暴跌 bar 收盘贴近极端值、影线占比小, 不触发。
# 修复 = 修字段不删根: high -> max(o,c,前根 high,后根 high); low -> min(o,c,前根 low,后根 low)。
# 除命中根外逐值不变。
def _badbar_hits(o, h, l, c):
    import numpy as np
    o = np.asarray(o, float); h = np.asarray(h, float)
    l = np.asarray(l, float); c = np.asarray(c, float)
    n = len(c); hits = []
    if n <= 10:
        return hits
    tr = np.empty(n); tr[0] = h[0] - l[0]
    pc = c[:-1]
    tr[1:] = np.maximum(h[1:] - l[1:], np.maximum(np.abs(h[1:] - pc), np.abs(l[1:] - pc)))
    atr = np.full(n, np.nan); a = tr[:10].mean(); atr[10] = a
    for i in range(11, n):
        a = a + (tr[i - 1] - a) / 10.0; atr[i] = a
    body_hi = np.maximum(o, c); body_lo = np.minimum(o, c)
    up = h - body_hi; dn = body_lo - l; rng = h - l
    for i in range(n):
        a = atr[i]
        if not np.isfinite(a) or a <= 0:
            continue
        if up[i] > 4.0 * a and up[i] > 0.6 * rng[i] and dn[i] <= 1.0 * a:
            cand = [o[i], c[i]] + ([h[i - 1]] if i > 0 else []) + ([h[i + 1]] if i < n - 1 else [])
            hits.append((i, "high", float(max(cand))))
        if dn[i] > 4.0 * a and dn[i] > 0.6 * rng[i] and up[i] <= 1.0 * a:
            cand = [o[i], c[i]] + ([l[i - 1]] if i > 0 else []) + ([l[i + 1]] if i < n - 1 else [])
            hits.append((i, "low", float(min(cand))))
    return hits


def _sanitize_bars(df, o_col="open", h_col="high", l_col="low", c_col="close"):
    """就地修复 df(须按时间升序) 的坏字段, 返回命中数。"""
    if df is None or len(df) == 0:
        return 0
    hits = _badbar_hits(df[o_col].values, df[h_col].values, df[l_col].values, df[c_col].values)
    if not hits:
        return 0
    hi = df.columns.get_loc(h_col); lo = df.columns.get_loc(l_col)
    for (i, side, new) in hits:
        df.iloc[i, hi if side == "high" else lo] = new
    return len(hits)


def build_bars(day=None):
    if day is None:
        hist3 = w4.load_history()
        df1m = w4.fetch_1m()
        now = datetime.now(ET)
    else:
        hist3 = _warmup_before(day)
        df1m = _fetch_1m_day(day)
    day3 = w4.to_3m(df1m)
    bars3 = pd.concat([hist3, day3], ignore_index=True)
    # 与 watcher_v4 一致: (t,n1m)排序优先保留n1m=3的行 (NaN=server聚合视为完整)
    # Fix 2026-10-13: 13:43的1m永久缺失致13:42桶n1m=2, 旧sort(t)+break逻辑会把13:39之后全卡掉
    bars3 = bars3.sort_values(["t", "n1m"]).drop_duplicates("t", keep="last").reset_index(drop=True)
    if day is not None:
        n_complete = len(bars3)  # 回放: 当天所有bar都已走完, 缺分钟的bin也照算
    else:
        # 与 watcher_v4 一致: n1m!=3只跳过不break (未来bar才break), 否则中间缺1m会卡住后面所有bar
        n_complete = 0
        for idx in range(len(bars3)):
            bt = bars3["t"].iloc[idx]
            bar_end = bt + pd.Timedelta(minutes=3)
            if bar_end > now:
                break  # 未来 bar，停止
            n1m = bars3["n1m"].iloc[idx] if "n1m" in bars3.columns else 3
            if pd.isna(n1m):
                n1m = 3
            if n1m == 3:
                n_complete = idx + 1
            # n1m!=3: 跳过这根，继续往后找完整的
    if n_complete == 0:
        raise RuntimeError("no complete 3m bar yet")
    sig = pd.DataFrame({
        "t": bars3["t"].iloc[:n_complete],
        "open": bars3["o"].iloc[:n_complete],
        "high": bars3["h"].iloc[:n_complete],
        "low": bars3["l"].iloc[:n_complete],
        "close": bars3["c"].iloc[:n_complete],
    })
    _sanitize_bars(sig)  # 坏bar剔除: 幻影 print 不得毒化 QQE/ST/Envelope
    _sanitize_bars(df1m, "o", "h", "l", "c")
    return sig, df1m


def fav_wave_info(hist, i, day_arr):
    """照抄 signal_engine._fav_wave_info (|h|<1不断波 V1)。"""
    _ws = i
    _consec_small = 0
    _cur_day = day_arr[i]
    _day_start = i
    while _day_start > 0 and day_arr[_day_start - 1] == _cur_day:
        _day_start -= 1
    _sign = -1 if hist[i] < 0 else 1
    while _ws > _day_start:
        _h = hist[_ws - 1]
        if np.isnan(_h):
            break
        if _h * _sign < 0 and abs(_h) >= 1:
            break
        if abs(_h) <= 4:
            _consec_small += 1
            if _consec_small > 8:
                break
        else:
            _consec_small = 0
        _ws -= 1
    _pk_idx = i
    _pk_v = 0
    _run_max = 0
    _run_max_idx = _ws
    for _j in range(_ws, i + 1):
        if not np.isnan(hist[_j]):
            _v = abs(hist[_j])
            if _v > _run_max:
                _run_max = _v
                _run_max_idx = _j
            if _pk_idx == i and _run_max > 0 and _v < _run_max * 0.85:
                _pk_idx = _run_max_idx
                _pk_v = _run_max
    if _pk_idx == i:
        for _j in range(_ws, i + 1):
            if not np.isnan(hist[_j]):
                _v = abs(hist[_j])
                if _v > _pk_v:
                    _pk_v = _v
                    _pk_idx = _j
    _wl = i - _pk_idx
    _win_start = max(i - 9, _ws, _day_start)
    _bars = [abs(hist[_j]) for _j in range(_win_start, i + 1) if not np.isnan(hist[_j])]
    _chop = 0
    if len(_bars) >= 3:
        _total = sum(abs(_bars[_b] - _bars[_b - 1]) for _b in range(1, len(_bars)))
        _net = abs(_bars[-1] - _bars[0])
        _ratio = _total / _net if _net > 0.1 else 99
        _chop = min(1.0, max(0.0, (_ratio - 1.0) / 4.0))
    return _wl, _chop, _wl * (1.0 + _chop), _ws, _pk_idx


def wave_len_gw(hist, day_arr, j, sign, max_lookback=30):
    """照抄 signal_engine.wave_len_gw: 从 j 往回数 sign 方向波的长度。"""
    n = 0
    _cs = 0
    for k in range(j, max(-1, j - max_lookback), -1):
        if k < 0 or k >= len(hist):
            break
        try:
            if day_arr[k] != day_arr[j]:
                break
        except Exception:
            pass
        h = hist[k]
        if np.isnan(h):
            break
        if abs(h) < 3:
            break
        if h * sign > 0 and abs(h) > 3:
            n += 1
            _cs = 0
        elif abs(h) <= 3 and h * sign >= 0:
            _cs += 1
            if _cs > 2:
                break
        else:
            break
    return n


def huibu_mult(base):
    """照抄引擎长波回补自适应倍数: |前|<3要2倍, <5要1.2, 否则只要递增。"""
    if base < 3:
        return 2.0
    elif base < 5:
        return 1.2
    return 1.0


def main():
    day = datetime.strptime(WAVE_DATE, "%Y-%m-%d").date() if WAVE_DATE else None
    sig, df1m = build_bars(day)
    day_str = WAVE_DATE or datetime.now(ET).strftime("%Y-%m-%d")
    page_title = "实盘dashboard回放" if WAVE_DATE else "实盘dashboard"
    rtag = "" if WAVE_DATE else refresh_tag  # 回放页不自动刷新
    n = len(sig)
    close = sig["close"].values.astype(float)
    res = qqe_mod(close)
    hist = np.asarray(res["hist"], dtype=float)
    q_up = np.asarray(res["up"]).astype(bool)
    q_down = np.asarray(res["down"]).astype(bool)
    day_arr = sig["t"].dt.date.values

    # 展示信号=冷冻版 (2026-10-29 用户: 热力图可显示, 信号须为冷冻版, 不发做T信号; 热力计算照常, 下行get_heat_*供显示)
    actions = run_signals(sig, heat_signals=False)
    # ---- VH (ST版) 当日动作: ST版展示用纯 ST (force_st, 跳空也不切 QQE);
    # 实盘版 (live) 仍是 Alpaca 实际成交, 不受影响; EC2 实盘引擎不传 force_st ----
    # EOD价: 当日1m取15:55收盘价; 提前收盘日取当日最后一根1m收盘价;
    # 盘中(1m未走完且未过期)不传, vh回退到3m收盘价。
    _eod_1m = None
    try:
        _df1s = df1m.sort_values("t")
        _b1555 = _df1s[_df1s["t"].dt.strftime("%H:%M") == "15:55"]
        if len(_b1555):
            _eod_1m = {day_str: float(_b1555["c"].iloc[-1])}
        elif WAVE_DATE:
            _eod_1m = {day_str: float(_df1s["c"].iloc[-1])}
        elif len(_df1s) and (datetime.now(ET) - _df1s["t"].iloc[-1]).total_seconds() > 1800:
            _eod_1m = {day_str: float(_df1s["c"].iloc[-1])}
    except Exception as _e:
        print(f"  WARN eod_1m failed: {_e}", flush=True)
        _eod_1m = None
    vh_actions = vh_live.run_vh(sig, eod_1m=_eod_1m, force_st=True)
    vh_info = vh_live.day_info(sig, force_st=True)
    heat_edges = get_heat_edges()
    heat_arr = get_heat_arr()
    heat_bins = get_heat_bins()
    heat_sm = get_heat_sm()

    # 今日的动作
    tstrs = sig["t"].dt.strftime("%H:%M").values
    dates = sig["t"].dt.date.astype(str).values
    today_idx = [i for i in range(n) if dates[i] == day_str]
    if not today_idx:
        # 盘前/开盘5分钟内没数据是正常的，等下轮（不报错）；Actions 里同时写
        # skip=1 让 workflow 跳过存档/deploy 步，避免拿旧 index 覆盖线上页
        now_et = datetime.now(ET)
        if not WAVE_DATE and (now_et.hour < 9 or (now_et.hour == 9 and now_et.minute < 35)):
            print(f"[{now_et.strftime('%H:%M:%S')}] market not open yet / just opened, no bars yet, skip gracefully")
            _gho = os.environ.get("GITHUB_OUTPUT")
            if _gho:
                with open(_gho, "a") as _f:
                    _f.write("skip=1\n")
            return
        raise RuntimeError("no today bars")

    # 盘中未完结日判定 (2026-11-19 user 令): 实盘生成 (WAVE_DATE 空) 且当日
    # 最后一根 3m 未到 15:57 (早收日 12:57) = 未完结日; 各版把最新一根当作
    # 日终平 (与 Envelope WER 版引擎帧尾平仓同行为: 未平仓位在最新一根按
    # 收盘合成日终平仓、计入当日连乘与标题)。历史回放 (WAVE_DATE 有值) 与
    # 已完结日一律不触发, 逐笔逐数与改前一致。
    _last_hm3 = tstrs[today_idx[-1]] if today_idx else ""
    _day_partial = (not WAVE_DATE) and _last_hm3 not in ("15:57", "12:57")

    # 真色
    # 颜色输出"up"/"down"/"gray"（模板用THEME.qqe3[colors[i]]查表, 和backfill一致; 不是hex）
    colors = []
    for j in range(n):
        if q_up[j] and abs(hist[j]) >= SIG_MIN_LEN:
            colors.append("up")
        elif q_down[j] and abs(hist[j]) >= SIG_MIN_LEN:
            colors.append("down")
        else:
            colors.append("gray")

    # 翻仓bar集合 (同bar既平又开)
    by_bar = {}
    for (bi, ts, act, dr, px, rs) in actions:
        by_bar.setdefault(bi, []).append(act)
    # 回补bar (长波回补/重涨回补的开仓): 只标'补', 不标'翻'
    repl_bars = set(bi for (bi, ts, act, dr, px, rs) in actions
                    if act == "开" and ("回补" in rs or "重涨" in rs))
    flip_bars = sorted(b for b, acts in by_bar.items()
                       if "平" in acts and "开" in acts and b not in repl_bars)

    last_action_bar = -999
    # 按bar顺序先扫一遍actions, 建 bar->last_action_bar 映射 (重放_damp_factor_gw)
    lab_at = {}
    cur_lab = -999
    acts_sorted = sorted(actions, key=lambda a: a[0])
    ai = 0
    for i in range(n):
        while ai < len(acts_sorted) and acts_sorted[ai][0] <= i:
            if acts_sorted[ai][2] in ("开", "平"):
                cur_lab = acts_sorted[ai][0]
            ai += 1
        lab_at[i] = cur_lab

    decay80, flip_line, hubu_line = [None] * n, [None] * n, [None] * n
    for i in today_idx:
        if abs(hist[i]) < 1 or np.isnan(hist[i]):
            continue
        sgn = -1 if hist[i] < 0 else 1
        _wl, _chop, _wl_adj, _ws, _pk_idx = fav_wave_info(hist, i, day_arr)
        seg = [abs(hist[j]) for j in range(_ws, i + 1) if not np.isnan(hist[j])]
        if not seg:
            continue
        run_peak = max(seg)
        if run_peak < 6:
            continue
        decay80[i] = round(sgn * run_peak * 0.8, 2)
        # need 重放 (照抄引擎公式; 只数过去的bar, 不偷看未来)
        _op_cnt = sum(1 for (bi, _, act, _, _, _) in actions
                      if _pk_idx <= bi <= i and act in ("开", "平"))
        _damp = 1.0 if (i - lab_at[i]) < 4 else 0.0
        if _op_cnt >= 4:
            _base = max(0.40, 0.8 * (0.97 ** max(0, _wl_adj - 3)))
            _need = 0.8 - (0.8 - _base) * _damp
        else:
            _need = 0.8
        if _wl_adj >= 15:
            _need *= 0.7
        _rf = [b for b in flip_bars if 0 <= i - b <= 30 and day_arr[b] == day_arr[i]]
        if len(_rf) >= 2:
            _need *= 0.7
        _need = max(0.30, _need)
        flip_line[i] = round(sgn * run_peak * _need, 2)
        # 回补门槛 (长波回补, 照抄引擎): 同向波长>=15, 当根与前根同色且|h|>3,
        # 门槛 = |前根| x 自适应倍数
        _wlen = wave_len_gw(hist, day_arr, i, sgn)
        if _wlen >= 15 and i >= 1 and day_arr[i] == day_arr[i - 1]:
            _hp = hist[i - 1]
            if (not np.isnan(_hp) and _hp * sgn > 0 and abs(_hp) > 3
                    and abs(hist[i]) > 3):
                _ahp = abs(_hp)
                hubu_line[i] = round(sgn * _ahp * huibu_mult(_ahp), 2)

    # markPoints: 信号点(开/平/翻/补/持)点在1m曲线上; QQE上同步一份
    # (2026-10-12 用户: 不用显示蓝峰红峰圈圈)
    qpins = []

    # 信号点: 点在1m曲线上 (x=分数类目坐标, y=信号bar收盘价); QQE上同步一份
    flip_set = set(flip_bars)
    flip_info = {}
    for (bi, ts, act, dr, px, rs) in acts_sorted:
        if bi in flip_set and act == "开":
            flip_info[bi] = (dr, rs)
    spins = []

    def _qpins_add(bi, value, color, rs_txt=""):
        """信号点同时在QQE柱上标一份 (和1m曲线上那套一致)。"""
        h = float(hist[bi])
        yq = round(h * 1.15 + (0.5 if h > 0 else -0.5), 1) if not np.isnan(h) else 5
        qpins.append({"coord": [tstrs[bi], yq], "value": value,
                      "itemStyle": {"color": color}, "rs": rs_txt})
    # 方向颜色 (2026-10-12 用户): 做多(call)=绿色, 做空(put)=红色
    # 2026-11-15 规范色: 开仓类一律正绿/正红, 平仓一律灰 (同 theme.js pinSpec; 运行时以 theme 为准, 此处为烘焙兜底)
    def _dir_color(dr, act=None):
        is_call = (dr == "call")
        if act == "翻":
            return "#00cc00" if is_call else "#ff0000"  # 翻: 正绿/正红 (2026-11-15 规范色, 同 theme.js pinSpec)
        elif act == "跟":
            return "#00cc00" if is_call else "#ff0000"  # 跟: 正绿/正红
        elif act == "补":
            return "#00cc00" if is_call else "#ff0000"  # 补: 正绿/正红
        else:
            return "#00cc00" if is_call else "#ff0000"  # 开: 正绿/正红
    for (bi, ts, act, dr, px, rs) in acts_sorted:
        if bi not in today_idx:
            continue
        p = today_idx.index(bi)
        xp = round(p + 2.5 / 3, 4)
        yp = round(float(sig["close"].iloc[bi]), 2)
        rsc = rs.replace("'", "")
        _dc = _dir_color(dr, act)
        # 回补 (长波回补 + 重涨回补都算): 整根bar只标一个'补C'/'补P' (按方向着色)
        if bi in repl_bars:
            if act == "开":
                v = "补" + ("C" if dr == "call" else "P")
                _bdc = _dir_color(dr, "补")
                spins.append({"coord": [xp, yp], "value": v,
                              "itemStyle": {"color": _bdc}, "rs": rsc})
                _qpins_add(bi, v, _bdc, rsc)
            continue
        if bi in flip_bars:
            continue  # 纯翻仓只在后面标一个'翻C'/'翻P'
        if act == "开":
            v = "跟" + ("C" if dr == "call" else "P")
            _gdc = _dir_color(dr, "跟")
            spins.append({"coord": [xp, yp], "value": v,
                          "itemStyle": {"color": _gdc}, "rs": rsc})
            _qpins_add(bi, v, _gdc, rsc)
        elif act == "平":
            # 止损单独标签 (2026-10-12 用户: 止损C/止损P要看得出来)
            # 平仓: 平C灰红 / 平P灰绿 (2026-10-12 用户)
            _pc = "#9ca3af"  # 平: 灰 (2026-11-15 规范色)
            if "止损" in rs:
                v = "止损" + ("C" if dr == "call" else "P")
            else:
                v = "平" + ("C" if dr == "call" else "P")
            spins.append({"coord": [xp, yp], "value": v,
                          "itemStyle": {"color": _pc}, "rs": rsc})
            _qpins_add(bi, v, _pc, rsc)
        elif act == "持":
            # 2026-10-12 用户: 不显示持的圈圈
            pass
    for b in flip_bars:
        if b not in today_idx:
            continue
        p = today_idx.index(b)
        xp = round(p + 2.5 / 3, 4)
        yp = round(float(sig["close"].iloc[b]), 2)
        d, frs = flip_info.get(b, (None, ""))
        frs = frs.replace("'", "")
        v = "翻" + ("C" if d == "call" else "P")
        _dc = _dir_color(d, "翻")
        spins.append({"coord": [xp, yp], "value": v,
                      "itemStyle": {"color": _dc}, "rs": frs})
        _qpins_add(b, v, _dc, frs)

    # ---- VH版图钉/round-trips (与 QQE 图钉同一套标记法, 数据源换成 vh_actions) ----
    vh_sorted = sorted(vh_actions, key=lambda a: a[0])
    vh_by_bar = {}
    for (bi, _ts, act, dr, px, rs) in vh_sorted:
        vh_by_bar.setdefault(bi, []).append(act)
    vh_repl_bars = set(bi for (bi, _ts, act, dr, px, rs) in vh_sorted
                       if act == "开" and ("回补" in rs or "重涨" in rs))
    vh_flip_bars = sorted(b for b, acts_ in vh_by_bar.items()
                          if "平" in acts_ and "开" in acts_ and b not in vh_repl_bars)
    vh_flip_set = set(vh_flip_bars)
    vh_flip_info = {}
    for (bi, _ts, act, dr, px, rs) in vh_sorted:
        if bi in vh_flip_set and act == "开":
            vh_flip_info[bi] = (dr, rs)
    vh_qpins, vh_spins = [], []

    def _vh_qpins_add(bi, value, color, rs_txt=""):
        h = float(hist[bi])
        yq = round(h * 1.15 + (0.5 if h > 0 else -0.5), 1) if not np.isnan(h) else 5
        vh_qpins.append({"coord": [tstrs[bi], yq], "value": value,
                         "itemStyle": {"color": color}, "rs": rs_txt})
    for (bi, _ts, act, dr, px, rs) in vh_sorted:
        if bi not in today_idx:
            continue
        p = today_idx.index(bi)
        xp = round(p + 2.5 / 3, 4)
        yp = round(float(sig["close"].iloc[bi]), 2)
        rsc = rs.replace("'", "")
        if bi in vh_repl_bars:
            if act == "开":
                v = "补" + ("C" if dr == "call" else "P")
                _bdc = _dir_color(dr, "补")
                vh_spins.append({"coord": [xp, yp], "value": v,
                                 "itemStyle": {"color": _bdc}, "rs": rsc})
                _vh_qpins_add(bi, v, _bdc, rsc)
            continue
        if bi in vh_flip_bars:
            continue
        if act == "开":
            v = "跟" + ("C" if dr == "call" else "P")
            _gdc = _dir_color(dr, "跟")
            vh_spins.append({"coord": [xp, yp], "value": v,
                             "itemStyle": {"color": _gdc}, "rs": rsc})
            _vh_qpins_add(bi, v, _gdc, rsc)
        elif act == "平":
            _pc = "#9ca3af"  # 平: 灰 (2026-11-15 规范色)
            if "止损" in rs:
                v = "止损" + ("C" if dr == "call" else "P")
            else:
                v = "平" + ("C" if dr == "call" else "P")
            vh_spins.append({"coord": [xp, yp], "value": v,
                             "itemStyle": {"color": _pc}, "rs": rsc})
            _vh_qpins_add(bi, v, _pc, rsc)
    for b in vh_flip_bars:
        if b not in today_idx:
            continue
        p = today_idx.index(b)
        xp = round(p + 2.5 / 3, 4)
        yp = round(float(sig["close"].iloc[b]), 2)
        d, frs = vh_flip_info.get(b, (None, ""))
        frs = frs.replace("'", "")
        v = "翻" + ("C" if d == "call" else "P")
        _dc = _dir_color(d, "翻")
        vh_spins.append({"coord": [xp, yp], "value": v,
                         "itemStyle": {"color": _dc}, "rs": frs})
        _vh_qpins_add(b, v, _dc, frs)
    # VH tooltip acts + round-trips (与 QQE rts 同一套配对)
    vh_acts_by_bar = {}
    for (bi, _ts, act, dr, px, rs) in vh_sorted:
        if bi in today_idx:
            vh_acts_by_bar.setdefault(bi, []).append(
                f"{act}{dr or ''}@{px} {rs}".replace("'", ""))
    vh_rts = []
    _vh_open = None
    for (bi, _ts, act, dr, px, rs) in vh_sorted:
        if act == "平" and _vh_open is not None:
            obi, odr, opx, ors = _vh_open
            vh_rts.append({"obi": obi, "odr": odr, "opx": opx, "ors": ors,
                           "cbi": bi, "cpx": px, "crs": rs, "closed": True})
            _vh_open = None
        elif act == "开":
            if _vh_open is not None:
                obi, odr, opx, ors = _vh_open
                vh_rts.append({"obi": obi, "odr": odr, "opx": opx, "ors": ors,
                               "cbi": bi, "cpx": px, "crs": rs, "closed": True})
            _vh_open = (bi, dr, px, rs)
    if _vh_open is not None and today_idx:
        obi, odr, opx, ors = _vh_open
        vh_rts.append({"obi": obi, "odr": odr, "opx": opx, "ors": ors,
                       "cbi": today_idx[-1],
                       "cpx": float(sig["close"].iloc[today_idx[-1]]),
                       "crs": None, "closed": False})
    # 盘中未完结日: VH 未平仓位在最新一根合成日终平仓 (2026-11-19 user 令,
    # 与 Envelope 版帧尾平同行为; 只改 VH 版展示: 单子/图钉/tooltip/标题,
    # vh_actions 原始动作与实盘版不动)
    if _day_partial and vh_rts and not vh_rts[-1]["closed"] \
            and vh_rts[-1]["obi"] in today_idx:
        _vrt = vh_rts[-1]
        _vrt["closed"] = True
        _vrt["crs"] = "日终平仓"
        _lb = today_idx[-1]
        _p = today_idx.index(_lb)
        _xp = round(_p + 2.5 / 3, 4)
        _yp = round(float(sig["close"].iloc[_lb]), 2)
        _v = "平" + ("C" if _vrt["odr"] == "call" else "P")
        vh_spins.append({"coord": [_xp, _yp], "value": _v,
                         "itemStyle": {"color": "#9ca3af"}, "rs": "日终平仓"})
        _vh_qpins_add(_lb, _v, "#9ca3af", "日终平仓")
        vh_acts_by_bar.setdefault(_lb, []).append(
            f"平{_vrt['odr']}@{_vrt['cpx']} 日终平仓")

    # 1m曲线数据: x=分数类目坐标(分钟中点), y=1m收盘
    t0 = sig["t"].iloc[today_idx[0]]
    df1 = df1m[df1m["t"] >= t0].sort_values("t")
    line1m = [[round((r["t"] - t0).total_seconds() / 180.0, 4),
               round(float(r["c"]), 2)] for _, r in df1.iterrows()]
    # 实际成交点 (live): 绿=买入 红=卖出, 落在1m曲线上 (y取所在1m bar收盘价)
    filldots = []
    fills = []  # 回放模式无实盘成交, 先初始化避免 UnboundLocalError
    if fills:
        _ts1 = df1["t"]
        for _f in fills:
            _ft = _f["t"]
            _i1 = _ts1.searchsorted(_ft, side="right") - 1
            if _i1 < 0 or _i1 >= len(df1):
                continue
            _bt = _ts1.iloc[_i1]
            if not (_bt <= _ft < _bt + pd.Timedelta(minutes=1)):
                continue
            _fx = round((_bt - t0).total_seconds() / 180.0, 4)
            _fy = round(float(df1["c"].iloc[_i1]), 2)
            _fc = "#00aa00" if _f["side"] == "buy" else "#ff0000"
            _ftip = ("%s %s %.2f x%d" % (
                _ft.strftime("%H:%M:%S"),
                "买入" if _f["side"] == "buy" else "卖出",
                _f["px"], int(_f["qty"])))
            filldots.append({"value": [_fx, _fy],
                             "itemStyle": {"color": _fc}, "tip": _ftip})
    # tooltip用: 每根bar的OHLC + QQE + 动作原因
    acts_by_bar = {}
    for (bi, ts, act, dr, px, rs) in acts_sorted:
        if bi in today_idx:
            acts_by_bar.setdefault(bi, []).append(
                f"{act}{dr or ''}@{px} {rs}".replace("'", ""))
    bar_info = {}
    for i in today_idx:
        t = tstrs[i]
        bar_info[t] = {
            "o": round(float(sig["open"].iloc[i]), 2),
            "h": round(float(sig["high"].iloc[i]), 2),
            "l": round(float(sig["low"].iloc[i]), 2),
            "c": round(float(sig["close"].iloc[i]), 2),
            "qqe": round(float(hist[i]), 2) if not np.isnan(hist[i]) else None,
            "col": "蓝" if colors[i] == BLUE else ("红" if colors[i] == RED else "灰"),
            "acts": acts_by_bar.get(i, []),
        }
    # 当前信号持仓
    pos = None
    for (bi, ts, act, dr, px, rs) in acts_sorted:
        if act == "开":
            pos = dr
        elif act == "平":
            pos = None
    pos_txt = {"call": "call", "put": "put"}.get(pos, "空仓")

    times = [tstrs[i] for i in today_idx]
    # 固定x轴为全天 09:30-16:00 (130根3m), 数据左对齐, 右侧留白
    _FULL_DAY_TIMES = []
    _h, _m = 9, 30
    for _k in range(130):
        _FULL_DAY_TIMES.append(f"{_h:02d}:{_m:02d}")
        _m += 3
        if _m >= 60:
            _h += 1
            _m -= 60
    times = _FULL_DAY_TIMES
    vals = [round(float(hist[i]), 2) if not np.isnan(hist[i]) else None for i in today_idx]
    cols = [colors[i] for i in today_idx]
    d80 = [decay80[i] for i in today_idx]
    fl = [flip_line[i] for i in today_idx]
    hb = [hubu_line[i] for i in today_idx]
    # 持仓区间背景: 按信号柱配对 开->平, 用信号价算每笔方向盈亏 (绿赚红亏)
    # 用户原则: 只看信号柱, 不管实际下单
    today_set = set(today_idx)
    first_t = tstrs[today_idx[0]]
    mark_areas = []
    _open = None  # (bi, dr, opx)
    def _vh_trade_roi(rt):
        """返回 (总ROI fraction, [(段ROI, 段开bar, 段平bar), ...])。
        ST单按30分钟切段复利(gamma_roi_100, -100下限); QQE单整单一段。
        2026-11-05 用户令: 实盘预估与ST VH版统一口径, 此处定义前移以便实盘表复用。"""
        dr, bi0, bi1 = rt["odr"], rt["obi"], rt["cbi"]
        px0, px1 = rt["opx"], rt["cpx"]
        if _vh_kind(rt["ors"]) == "qqe":
            _r = gamma_roi_100(dr, px0, px1)
            return _r, [(_r, bi0, bi1)]
        bounds = []
        b = bi0 + 10
        while b < bi1:
            bounds.append(b)
            b += 10
        pts = [px0] + [float(sig["close"].iloc[bb]) for bb in bounds] + [px1]
        bis = [bi0] + bounds + [bi1]
        segs = []
        cap = 1.0
        for _si in range(len(pts) - 1):
            _r = gamma_roi_100(dr, pts[_si], pts[_si + 1])
            segs.append((_r, bis[_si], bis[_si + 1]))
            cap *= (1.0 + _r)
        return cap - 1.0, segs

    def _emit(obi, odr, opx, cbi, cpx):
        try:
            pnl = (float(cpx) - float(opx)) if odr == "call" else (float(opx) - float(cpx))
        except (TypeError, ValueError):
            return
        # 2026-10-19 用户: alpha公式拆到theme.js (THEME.pnlBgColor)，这里只传收益率百分比
        # 2026-11-05 用户令统一: -100下限(gamma_roi_100), 与各版预估表同口径
        _pct = round(gamma_roi_100(odr, opx, cpx) * 100, 2)
        s = tstrs[obi] if obi in today_set else first_t
        mark_areas.append([
            {"xAxis": s, "pnlPct": _pct},
            {"xAxis": tstrs[cbi]},
        ])
    rts = []  # round-trips: {obi,odr,opx,ors,cbi,cpx,crs,closed}
    for (bi, ts, act, dr, px, rs) in acts_sorted:
        if act == "平" and _open is not None:
            obi, odr, opx, ors = _open
            if bi in today_set:
                _emit(obi, odr, opx, bi, px)
                rts.append({"obi": obi, "odr": odr, "opx": opx, "ors": ors,
                            "cbi": bi, "cpx": px, "crs": rs, "closed": True})
            _open = None
        elif act == "开":
            if _open is not None and bi in today_set:
                obi, odr, opx, ors = _open
                _emit(obi, odr, opx, bi, px)
                rts.append({"obi": obi, "odr": odr, "opx": opx, "ors": ors,
                            "cbi": bi, "cpx": px, "crs": rs, "closed": True})
            _open = (bi, dr, px, rs)
    if _open is not None and today_idx:  # 未平仓: 按最新bar算浮动方向
        obi, odr, opx, ors = _open
        _emit(obi, odr, opx, today_idx[-1], float(sig["close"].iloc[today_idx[-1]]))
        rts.append({"obi": obi, "odr": odr, "opx": opx, "ors": ors,
                    "cbi": today_idx[-1],
                    "cpx": float(sig["close"].iloc[today_idx[-1]]),
                    "crs": None, "closed": False})
    rts_qqe_disp = rts  # QQE版展示用 (盘中合成日终平时换副本; rts 本体供实盘配对, 不动)
    # ---- 实盘每单收益: 信号配对实际成交 ----
    fills, poss, equity, day_open_equity = fetch_day_fills(day_str, live=not WAVE_DATE)
    for rt in rts:
        o_t = sig["t"].iloc[rt["obi"]]
        o_f = [f for f in fills if f["side"] == "buy"
               and o_t - timedelta(minutes=2) <= f["t"] <= o_t + timedelta(minutes=12)]
        rt["o_qty"] = sum(f["qty"] for f in o_f)
        rt["o_cost"] = sum(f["qty"] * f["px"] for f in o_f)
        rt["o_avg"] = rt["o_cost"] / rt["o_qty"] if rt["o_qty"] else None
        rt["o_sym"] = o_f[0]["symbol"] if o_f else None
        c_f, c_qty, c_avg, pnl, ret = [], 0, None, None, None
        if rt["closed"]:
            c_t = sig["t"].iloc[rt["cbi"]]
            c_f = [f for f in fills if f["side"] == "sell"
                   and c_t - timedelta(minutes=2) <= f["t"] <= c_t + timedelta(minutes=12)]
            c_qty = sum(f["qty"] for f in c_f)
            c_pro = sum(f["qty"] * f["px"] for f in c_f)
            c_avg = c_pro / c_qty if c_qty else None
            if rt["o_qty"] and c_qty:
                pnl = (c_pro - rt["o_cost"]) * 100  # 1张=100股
                ret = pnl / (rt["o_cost"] * 100) if rt["o_cost"] else None
        else:  # 持仓中: 未实现盈亏
            p = poss.get(rt["o_sym"]) if rt["o_sym"] else None
            if p:
                pnl, ret = p["upl"], p["uplpc"]
        rt.update({"c_qty": c_qty, "c_avg": c_avg, "pnl": pnl, "ret": ret})
        # 严格配对: 买卖张数必须基本一致, 否则不算实际 (防时间窗误配)
        if rt["o_qty"] and c_qty:
            _qok = abs(rt["o_qty"] - c_qty) / max(rt["o_qty"], c_qty) < 0.05
        else:
            _qok = False
        rt["qok"] = _qok
        if not _qok:
            rt["pnl"] = None
            rt["ret"] = None
        # 预估统一Gamma公式ROI (2026-10-20 用户: 实盘/回放都用ROI公式, 不再用delta线性模型)
        # 2026-11-05 用户令统一: -100下限 + 30分钟切段复利, 与ST VH版同口径(_vh_trade_roi)
        _roi = _vh_trade_roi(rt)[0]
        if rt["o_qty"]:
            # 有实际成交: 预估=ROI×实际期权成本
            rt["est_ret"] = _roi
            rt["est_pnl"] = _roi * rt["o_avg"] * rt["o_qty"] * 100 if rt["o_avg"] else 0.0
            rt["est_bt"] = False
        else:
            # 无实际成交(回放): 预估=ROI×$100k基准 (sequential口径: 全资金即成本)
            rt["est_ret"] = _roi
            rt["est_pnl"] = _roi * 100000
            rt["est_bt"] = True
    realized = sum(r["pnl"] for r in rts if r["closed"] and r["pnl"] is not None)
    unreal = sum(r["pnl"] for r in rts if not r["closed"] and r["pnl"] is not None)
    day_pnl = realized + unreal
    has_live = bool(fills or poss or equity)
    # ---- 稳健口径 (直接按成交算, 不依赖信号配对; 标题用这个) ----
    _buy_cost = sum(f["qty"] * f["px"] for f in fills if f["side"] == "buy")
    _sell_pro = sum(f["qty"] * f["px"] for f in fills if f["side"] == "sell")
    _open_cost = sum(p["qty"] * p["avg_px"] for p in poss.values())
    robust_realized = (_sell_pro - _buy_cost + _open_cost) * 100
    robust_unreal = sum(p["upl"] for p in poss.values())
    robust_day = robust_realized + robust_unreal
    # ---- Alpaca真实持仓 ----
    def _parse_opt(sym):
        # SPY261015C00777000 -> (call, 777.0)
        try:
            cp = "call" if sym[9:10] == "C" else "put"
            strike = int(sym[-8:]) / 1000.0
            return cp, strike
        except Exception:
            return None, None
    _api_poss = []
    for _sym, _p in poss.items():
        _cp, _sk = _parse_opt(_sym)
        if _cp:
            _api_poss.append(f"{'call' if _cp=='call' else 'put'} {abs(_p['qty']):.0f}张@{_sk:.0f}")
    api_pos_txt = "、".join(_api_poss) if _api_poss else "空仓"

    def _money(x):
        return "—" if x is None else f"{'+' if x >= 0 else '-'}" \
            f"${abs(x):,.0f}"

    def _pct(x):
        return "—" if x is None else f"{x:+.1%}"

    def _op_name(rs, dr):
        d = "C" if dr == "call" else "P"
        s = (rs or "").replace("call", "C").replace("put", "P")
        if s.startswith("翻仓开"):
            return "翻" + d
        s = s.replace("翻仓", "翻")
        if d not in s:
            s += d
        return s or d

    # ---- 图上每单标签 (回测dashboard式: 顶部横排 $+%, 防重叠错行) ----
    _hi = float(sig["high"].iloc[today_idx].max())
    _lo = float(sig["low"].iloc[today_idx].min())
    _rng = _hi - _lo or 1.0
    _closed = [r for r in rts if r["closed"]]
    _row_of, _placed = {}, []
    for _k in sorted(range(len(_closed)), key=lambda k: _closed[k]["cbi"]):
        _cbi = _closed[_k]["cbi"]
        _row = 0
        while any(abs(_cbi - _oc) < 15 and _or == _row for (_oc, _or) in _placed):
            _row += 1
        _row_of[_k] = _row
        _placed.append((_cbi, _row))
    _max_row = max(_row_of.values()) if _row_of else 0
    _row_h = _rng * 0.075
    _base = _hi + _rng * 0.03  # 标签带底 (高于所有K线)
    y_max_js = round(_base + (_max_row + 1) * _row_h + _rng * 0.05, 2)
    scat_data = []
    for k, rt in enumerate(_closed):
        # 2026-10-19: lbl只留百分比, 不带dollar (和backfill一致; 用户规则)
        if rt["pnl"] is not None:
            txt = f"({_pct(rt['ret'])})"
            col = "#1a9e54" if rt["pnl"] >= 0 else "#e5484d"
        else:
            txt = f"~({_pct(rt['est_ret'])})"
            col = "#888888"
        _yy = round(_base + _row_of[k] * _row_h, 2)
        scat_data.append(
            "{\"value\":[\"%s\",%.2f],\"lbl\":\"%s\",\"label\":{\"color\":\"%s\"}}" % (
                tstrs[rt["cbi"]], _yy, txt, col))
    scat_js = "[" + ",".join(scat_data) + "]"

    # 盘中未完结日: QQE 未平仓位在最新一根合成日终平仓 (2026-11-19 user 令,
    # 与 Envelope 版帧尾平同行为)。只动 QQE 版展示 (表/标题走 rts_qqe_disp
    # 副本、图钉/单标签/tooltip 补一份); rts 本体/实盘配对/pos_txt 不动。
    if _day_partial and rts and not rts[-1]["closed"] \
            and rts[-1]["obi"] in today_idx:
        _qrt = rts[-1]
        rts_qqe_disp = [dict(_r) for _r in rts]
        rts_qqe_disp[-1]["closed"] = True
        rts_qqe_disp[-1]["crs"] = "日终平仓"
        _lb = today_idx[-1]
        _p = today_idx.index(_lb)
        _xp = round(_p + 2.5 / 3, 4)
        _yp = round(float(sig["close"].iloc[_lb]), 2)
        _v = "平" + ("C" if _qrt["odr"] == "call" else "P")
        spins.append({"coord": [_xp, _yp], "value": _v,
                      "itemStyle": {"color": "#9ca3af"}, "rs": "日终平仓"})
        _qpins_add(_lb, _v, "#9ca3af", "日终平仓")
        bar_info[tstrs[_lb]]["acts"].append(
            f"平{_qrt['odr']}@{_qrt['cpx']} 日终平仓")
        _row = 0
        while any(abs(_lb - _oc) < 15 and _or == _row
                  for (_oc, _or) in _placed):
            _row += 1
        _placed.append((_lb, _row))
        _max_row = max(_max_row, _row)
        _yy = round(_base + _row * _row_h, 2)
        _er = _qrt.get("est_ret") or 0.0
        scat_data.append(
            "{\"value\":[\"%s\",%.2f],\"lbl\":\"%s\",\"label\":{\"color\":\"#888888\"}}"
            % (tstrs[_lb], _yy, f"~({_pct(_er)})"))
        scat_js = "[" + ",".join(scat_data) + "]"
        y_max_js = max(y_max_js, round(
            _base + (_max_row + 1) * _row_h + _rng * 0.05, 2))

    # ---- VH版色带/单标签/段标签 (ST单30分钟切段=每10根3m一段、段间复利;
    # QQE单整单一段、无段标签; 色带/标签走THEME ST专用色, 照抄backfill VH版) ----
    vh_mark_bands = []  # (t0, pct, t1), ST色
    vh_scat_raw = []    # (close_tstr, roi) 已平仓单
    vh_seg_raw = []     # (seg_end_tstr, seg_end_bi, roi) 多段ST单的每段
    vh_refresh = []     # 段界bar时间 (markLine竖线)
    for rt in vh_rts:
        _roi, _segs = _vh_trade_roi(rt)
        rt["vh_roi"] = _roi
        for (_r, _b0, _b1) in _segs:
            vh_mark_bands.append((tstrs[_b0], round(_r * 100, 2), tstrs[_b1]))
        if rt["closed"]:
            vh_scat_raw.append((tstrs[rt["cbi"]], _roi))
        if len(_segs) > 1:
            for (_r, _b0, _b1) in _segs:
                vh_seg_raw.append((tstrs[_b1], _b1, _r))
                if _b1 != rt["cbi"]:
                    vh_refresh.append(tstrs[_b1])
    vh_refresh = list(dict.fromkeys(vh_refresh))
    _vh_placed, vh_scat_data = [], []
    _vh_max_row = _max_row
    for rt in [r for r in vh_rts if r["closed"]]:
        _cbi = rt["cbi"]
        _row = 0
        while any(abs(_cbi - _oc) < 15 and _or == _row for (_oc, _or) in _vh_placed):
            _row += 1
        _vh_placed.append((_cbi, _row))
        _vh_max_row = max(_vh_max_row, _row)
        _yy = round(_base + _row * _row_h, 2)
        _vpct = rt["vh_roi"] * 100
        vh_scat_data.append(
            '{"value":["%s",%.2f],"lbl":"%s","label":{"color":THEME.tradeLabelColor(%s)}}' % (
                tstrs[_cbi], _yy, f"{_vpct:+.1f}%", repr(float(_vpct))))
    vh_scat_js = "[" + ",".join(vh_scat_data) + "]"
    _seg_placed, vh_seg_data = [], []
    for (ehm, ebi, r) in vh_seg_raw:
        _row = 0
        while any(abs(ebi - _ob) < 15 and _or == _row for (_ob, _or) in _seg_placed):
            _row += 1
        _seg_placed.append((ebi, _row))
        _srow = _row + 1
        _vh_max_row = max(_vh_max_row, _srow)
        _yy = round(_base + _srow * _row_h, 2)
        _vpct = r * 100
        vh_seg_data.append(
            '{"value":["%s",%.2f],"lbl":"%s","label":{"color":THEME.segLabelColor(%s)}}' % (
                ehm, _yy, f"{_vpct:+.1f}%", repr(float(_vpct))))
    vh_seg_js = "[" + ",".join(vh_seg_data) + "]"
    vh_markarea_trade_js = "[" + ",".join(
        "[{xAxis:'%s',itemStyle:{color:THEME.pnlBgColorST(%s)}},{xAxis:'%s'}]" % (
            t0, pct, t1) for (t0, pct, t1) in vh_mark_bands) + "]"
    vh_markline_js = json.dumps([{"xAxis": t} for t in vh_refresh], ensure_ascii=False)
    y_max_js = round(_base + (_vh_max_row + 1) * _row_h + _rng * 0.05, 2)


    # ---- FIFO实际成交配对 (不依赖信号重算, 直接按成交算出真实round-trip) ----
    _fifo_lots = {}  # symbol -> [[qty, px, t], ...]
    actual_rts = []
    for f in fills:  # fills已按时间排序
        _sym = f["symbol"]
        if f["side"] == "buy":
            _fifo_lots.setdefault(_sym, []).append([f["qty"], f["px"], f["t"]])
        else:
            _lots = _fifo_lots.get(_sym, [])
            _sq, _spx = f["qty"], f["px"]
            _mq, _mc = 0.0, 0.0
            _ot = None
            while _sq > 0.001 and _lots:
                _lq, _lpx, _lt = _lots[0]
                _take = min(_lq, _sq)
                _mq += _take
                _mc += _take * _lpx
                if _ot is None:
                    _ot = _lt
                _lq -= _take
                _sq -= _take
                if _lq <= 0.001:
                    _lots.pop(0)
                else:
                    _lots[0][0] = _lq
            if _mq > 0.001:
                _oa = _mc / _mq
                _pnl = (_spx - _oa) * _mq * 100
                _ret = _pnl / (_mc * 100) if _mc else None
                actual_rts.append({"sym": _sym, "o_t": _ot, "c_t": f["t"],
                                   "qty": _mq, "o_avg": _oa, "c_avg": _spx,
                                   "pnl": _pnl, "ret": _ret, "closed": True})
    # 未平仓部分
    for _sym, _lots in _fifo_lots.items():
        if _lots:
            _tq = sum(l[0] for l in _lots)
            _tc = sum(l[0] * l[1] for l in _lots)
            _p = poss.get(_sym, {})
            actual_rts.append({"sym": _sym, "o_t": _lots[0][2], "c_t": None,
                               "qty": _tq, "o_avg": _tc / _tq if _tq else 0,
                               "c_avg": None, "pnl": _p.get("upl"),
                               "ret": _p.get("uplpc"), "closed": False})
    def _spy_at(ts):
        # 成交精确时刻的SPY点数: 最接近的1m bar收盘价 (2026-10-20 用户口径)
        if ts is None or df1m is None or len(df1m) == 0:
            return None
        _jj = (df1m["t"] - ts).abs().argsort()[:1]
        return float(df1m["c"].iloc[_jj].iloc[0]) if len(_jj) else None

    # 给每个actual_rt找最近的信号 (用于操作名, 方向必须一致)
    # 2026-11-02: 实盘跑VH信号, 贴名须用VH信号表vh_rts (原用QQE rts会把ST单标成QQE名)
    for _ar in actual_rts:
        _sym_cp = "call" if _ar["sym"][9:10] == "C" else "put"
        _best, _best_s = None, 1e9
        for _rt in vh_rts:
            if _rt["odr"] != _sym_cp:
                continue
            _st = sig["t"].iloc[_rt["obi"]]
            _ds = abs((_ar["o_t"] - _st).total_seconds())
            if _ds < _best_s:
                _best_s, _best = _ds, _rt
        if _best is not None and _best_s < 900:  # 15分钟内
            _ar["op"] = _op_name(_best.get("ors"), _best["odr"])
            _ar["odr"] = _best["odr"]
        else:
            _ar["op"] = f"实盘{_sym_cp.upper()}"
            _ar["odr"] = _sym_cp
        # 开/平SPY一律用成交时刻最接近的1m收盘价 (2026-10-20 用户: 有实盘不用信号价);
        # 取不到1m价时才回退信号价/最新价
        _fb_o = _best["opx"] if (_best is not None and _best_s < 900) else None
        _fb_c = _best["cpx"] if (_best is not None and _best_s < 900) else None
        _last1m = float(df1m["c"].iloc[-1]) if (df1m is not None and len(df1m)) else None
        _ar["_opx"] = _spy_at(_ar["o_t"]) or _fb_o or _last1m or float(sig["close"].iloc[-1])
        if _ar["c_t"] is not None:
            _ar["_cpx"] = _spy_at(_ar["c_t"]) or _fb_c or _last1m or float(sig["close"].iloc[-1])
        else:
            _ar["_cpx"] = _last1m or _fb_c or float(sig["close"].iloc[-1])
        # 预估: Gamma公式ROI×实际期权成本, ROI按成交时刻SPY价算 (2026-10-20 用户统一口径)
        # 2026-11-05 用户令统一: -100下限 + 30分钟切段复利, 与ST VH版同口径(_vh_trade_roi)
        # 实际成交时间映射到3m bar序号; 配对信号的ors定ST/QQE种类, 无配对按QQE整单一段
        try:
            _o_bi = int((sig["t"] - _ar["o_t"]).abs().argsort().iloc[0])
            _c_bi = int((sig["t"] - _ar["c_t"]).abs().argsort().iloc[0]) if _ar.get("c_t") is not None else len(sig) - 1
            _ors2 = _best.get("ors") if (_best is not None and _best_s < 900) else ""
            _roi2 = _vh_trade_roi({"odr": _ar["odr"], "obi": _o_bi, "cbi": max(_c_bi, _o_bi),
                                   "opx": _ar["_opx"], "cpx": _ar["_cpx"], "ors": _ors2})[0]
        except Exception:
            _roi2 = gamma_roi_100(_ar["odr"], _ar["_opx"], _ar["_cpx"])
        _ocost = _ar["o_avg"] * _ar["qty"] * 100
        _ar["est_ret"] = _roi2
        _ar["est_pnl"] = _roi2 * _ocost
    actual_rts.sort(key=lambda x: x["o_t"])

    # ---- 下方信号单表格 ----
    trows = []
    if has_live:
        # 实盘: 以实际成交FIFO为主
        _table_rows = actual_rts
        _is_live = True
    else:
        # 回放: 显示信号单+回测估算
        _table_rows = rts
        _is_live = False
    for k, ar in enumerate(_table_rows, 1):
        if _is_live:
            o_tm = ar["o_t"].strftime("%H:%M")
            c_tm = ar["c_t"].strftime("%H:%M") if ar["closed"] else "持仓中"
            op_txt = ar["op"]
            q_txt = f"{ar['qty']:.0f}"
            oa_txt = f"{ar['o_avg']:.2f}"
            ca_txt = f"{ar['c_avg']:.2f}" if ar["c_avg"] else "—"
            so_txt = f"{ar['_opx']:.2f}" if ar.get("_opx") is not None else "—"
            sc_txt = f"{ar['_cpx']:.2f}" if ar.get("_cpx") is not None else "—"
            pnl_txt, ret_txt = _money(ar["pnl"]), _pct(ar["ret"])
            if not ar["closed"] and ar["pnl"] is not None:
                pnl_txt += "(浮)"
            est_txt, est_ret_txt = _money(ar["est_pnl"]), _pct(ar["est_ret"])
            pc = "#1a9e54" if (ar["pnl"] or 0) >= 0 else "#e5484d"
        else:
            # 回放: 信号时间+操作名+预估 (实际列空)
            o_tm = sig["t"].iloc[ar["obi"]].strftime("%H:%M")
            c_tm = sig["t"].iloc[ar["cbi"]].strftime("%H:%M") if ar["closed"] else "持仓中"
            op_txt = _op_name(ar.get("ors"), ar["odr"])
            q_txt, oa_txt, ca_txt = "—", "—", "—"
            so_txt = f"{ar['opx']:.2f}"
            sc_txt = f"{ar['cpx']:.2f}" if ar["closed"] else "—"
            pnl_txt, ret_txt = "—", "—"
            est_txt = "~" + _money(ar["est_pnl"])
            est_ret_txt = "~" + _pct(ar["est_ret"])
            pc = "#888888"
        ec = "#1a9e54" if ar["est_pnl"] >= 0 else "#e5484d"
        trows.append(
            f"<tr><td>{k}</td><td>{o_tm}</td><td>{op_txt}</td><td>{q_txt}</td>"
            f"<td>{oa_txt}</td><td>{so_txt}</td><td>{c_tm}</td><td>{ca_txt}</td><td>{sc_txt}</td>"
            f"<td style=\"color:{pc};font-weight:bold\">{pnl_txt}</td>"
            f"<td style=\"color:{pc}\">{ret_txt}</td>"
            f"<td style=\"color:{ec}\">{est_txt}</td>"
            f"<td style=\"color:{ec}\">{est_ret_txt}</td></tr>")
    if _is_live:
        tot_est = sum(r["est_pnl"] for r in actual_rts)
        tot_est_txt = _money(tot_est)
        _tot_real = (robust_realized + robust_unreal)
    else:
        tot_est = sum(r["est_pnl"] for r in rts)
        tot_est_txt = "~" + _money(tot_est)
        _tot_real = 0
    trows.append(
        f"<tr style=\"background:#f2f2f2;font-weight:bold\"><td colspan=\"9\">合计"
        f"</td><td>{_money(_tot_real)}</td><td></td>"
        f"<td>{tot_est_txt}</td><td></td></tr>")
    table_html = (
        "<div id=\"tblWrap\" style=\"max-width:1150px;margin:14px auto;padding:0 8px\">"
        "<div style=\"font-size:14px;font-weight:bold;margin-bottom:6px\">"
        "实盘信号单（实际成交 vs 信号预估）</div>"
        "<div style=\"overflow-x:auto\"><table border=\"1\" cellspacing=\"0\" cellpadding=\"6\" "
        "style=\"border-collapse:collapse;font-size:12px;width:100%;text-align:center;white-space:nowrap\">"
        "<tr style=\"background:#f2f2f2\"><th>#</th><th>开仓</th><th>操作</th><th>张数</th>"
        "<th>开均价</th><th>开SPY</th><th>平仓</th><th>平均价</th><th>平SPY</th><th>实际收益</th><th>实际收益率</th>"
        "<th>预估收益</th><th>预估收益率</th></tr>"
        + "".join(trows) + "</table></div>"
        "<div style=\"font-size:11px;color:#888;margin-top:4px\">"
        "实际=FIFO按成交配对(买→卖),与Alpaca一致;操作名取最近15分钟内同向信号,无则标实盘;"
        "预估=Gamma公式ROI×期权成本(实盘按实际成本,回放按$100k基准),单笔floor -100%;ST单按30分钟切段复利,QQE单整单一段;开/平SPY=成交时刻最接近1m bar收盘价;(浮)=未实现</div>"
        f"<div style=\"font-size:11px;color:#888;\">Alpaca实际成交: {len(fills)}笔"
        + (f", 最新{fills[-1]['t'].strftime('%H:%M:%S')}" if fills else "") + "</div></div>")

    # ---- 三版本: VH/QQE Gamma预估表 (-100口径, 日内$100k sequential复利) ----
    def _est_table_inner(rows_rts, heading, footnote):
        """返回 (inner_html, total_pnl, total_pct); inner不含外层div, 供tblWrap切换用。
        rows_rts 每项须含 obi/odr/opx/ors/cbi/cpx/closed/est_roi; 未平仓行只算浮动预估、不推进cap。"""
        _cap = 100000.0
        _rows = []
        for k, rt in enumerate(rows_rts, 1):
            _roi = rt["est_roi"]
            if rt["closed"]:
                _pnl = _cap * _roi
                _cap *= (1.0 + _roi)
            else:
                _pnl = _cap * _roi
            o_tm = sig["t"].iloc[rt["obi"]].strftime("%H:%M")
            c_tm = sig["t"].iloc[rt["cbi"]].strftime("%H:%M") if rt["closed"] else "持仓中"
            op_txt = _op_name(rt.get("ors"), rt["odr"])
            so_txt = f"{rt['opx']:.2f}"
            sc_txt = f"{rt['cpx']:.2f}" if rt["closed"] else "—"
            ec = "#1a9e54" if _pnl >= 0 else "#e5484d"
            _rows.append(
                f"<tr><td>{k}</td><td>{o_tm}</td><td>{op_txt}</td><td>—</td>"
                f"<td>—</td><td>{so_txt}</td><td>{c_tm}</td><td>—</td><td>{sc_txt}</td>"
                f"<td style=\"color:#888888;font-weight:bold\">—</td>"
                f"<td style=\"color:#888888\">—</td>"
                f"<td style=\"color:{ec}\">~{_money(_pnl)}</td>"
                f"<td style=\"color:{ec}\">~{_pct(_roi)}</td></tr>")
        _tot = _cap - 100000.0
        _tot_pct = _cap / 100000.0 - 1.0
        _rows.append(
            f"<tr style=\"background:#f2f2f2;font-weight:bold\"><td colspan=\"9\">合计"
            f"</td><td>—</td><td></td>"
            f"<td>~{_money(_tot)}</td><td></td></tr>")
        _inner = (
            "<div style=\"font-size:14px;font-weight:bold;margin-bottom:6px\">"
            f"{heading}</div>"
            "<div style=\"overflow-x:auto\"><table border=\"1\" cellspacing=\"0\" cellpadding=\"6\" "
            "style=\"border-collapse:collapse;font-size:12px;width:100%;text-align:center;white-space:nowrap\">"
            "<tr style=\"background:#f2f2f2\"><th>#</th><th>开仓</th><th>操作</th><th>张数</th>"
            "<th>开均价</th><th>开SPY</th><th>平仓</th><th>平均价</th><th>平SPY</th><th>实际收益</th><th>实际收益率</th>"
            "<th>预估收益</th><th>预估收益率</th></tr>"
            + "".join(_rows) + "</table></div>"
            f"<div style=\"font-size:11px;color:#888;margin-top:4px\">{footnote}</div>")
        return _inner, _tot, _tot_pct

    vh_inner, vh_tot, vh_tot_pct = _est_table_inner(
        [{**rt, "est_roi": rt["vh_roi"]} for rt in vh_rts],
        "ST VH版信号单（Gamma理论预估，-100口径）",
        "预估=Gamma公式ROI×$100k日内sequential复利；ST单按30分钟切段理论预估（段标签为理论切段预估，非实际成交），QQE单整单一段；非实际成交")
    qqe_inner, qqe_tot, qqe_tot_pct = _est_table_inner(
        [{**rt, "est_roi": gamma_roi_100(rt["odr"], rt["opx"], rt["cpx"])} for rt in rts_qqe_disp],
        "QQE版信号单（Gamma理论预估，-100口径）",
        "预估=Gamma公式ROI×$100k日内sequential复利，单笔floor -100%；非实际成交")
    if not has_live:
        # 无真实成交时实盘版表退回QQE预估表 (旧回放行为, 口径改为-100)
        table_html = ('<div id="tblWrap" style="max-width:1150px;margin:14px auto;padding:0 8px">'
                      + qqe_inner + '</div>')
    # VH/QQE版标题 (策略文案以 vh_live.day_info 为准)
    _gap = float(vh_info.get("gap", 0.0))
    _gap_abs = abs(_gap)
    if _gap_abs < 5:
        gap_txt = "不跳空"
    else:
        gap_txt = f"{'上跳' if _gap > 0 else '下跳'}{_gap_abs:.1f}点"
    st_strat = "全天ST"  # ST版展示用纯 ST, 不再分上午/下午腿
    vh_title_txt = (f"{day_str} (ST版) | {gap_txt} | {st_strat} | "
                    f"{'+' if vh_tot >= 0 else '-'}${abs(vh_tot):,.0f} ({vh_tot_pct:+.1%})")
    qqe_title_txt = (f"{day_str} (QQE版) | {gap_txt} | 全天QQE | "
                     f"{'+' if qqe_tot >= 0 else '-'}${abs(qqe_tot):,.0f} ({qqe_tot_pct:+.1%})")

    # VH/QQE版图表标题富文本版 (与backfill页同款: 版本/跳空/策略/盈亏着色)
    if _gap_abs < 5:
        gap_e = "不跳空"
    elif _gap > 0:
        gap_e = "{up|" + gap_txt + "}"
    else:
        gap_e = "{dn|" + gap_txt + "}"
    def _strat_e(s):
        return (s.replace("上午QQE", "上午{qqe|QQE}").replace("上午空仓", "上午{flat|空仓}")
                 .replace("上午ST", "上午{st|ST}").replace("下午QQE", "下午{qqe|QQE}")
                 .replace("下午ST", "下午{st|ST}").replace("全天QQE", "全天{qqe|QQE}")
                 .replace("全天ST", "全天{st|ST}"))
    vh_pnl_e = ("{pnl_pos|+" if vh_tot >= 0 else "{pnl_neg|-") + f"${abs(vh_tot):,.0f} ({vh_tot_pct:+.1%})" + "}"
    qqe_pnl_e = ("{pnl_pos|+" if qqe_tot >= 0 else "{pnl_neg|-") + f"${abs(qqe_tot):,.0f} ({qqe_tot_pct:+.1%})" + "}"
    vh_title_echart = day_str + " ({st|ST}版) | " + gap_e + " | " + _strat_e(st_strat) + " | " + vh_pnl_e
    qqe_title_echart = day_str + " ({qqe|QQE}版) | " + gap_e + " | 全天{qqe|QQE} | " + qqe_pnl_e

    # ---- ST VX版 (2026-11-17 user 令): vendored 双侧门控内核 (stvx/ 子包),
    # 与 summary ST VX 行/两周图 VX 轨同口径 (esFSW3GF + 双侧上午门控)。
    # 暖机 = 目标周周一前 45 天起的 1m 聚合 3m 连续帧 (与研究 load_week_frame
    # 同窗; ST 带/趋势/年龄跨日接续、GF 用昨收), 只取当日单子。
    # 计算失败回退空节 + WARN, 页面不挂 (与本文件各节一贯的降级方式一致)。
    stvx_pins, stvx_spins = [], []
    stvx_scat_js = "[]"
    stvx_mark_area_js = "[]"
    stvx_acts = {}
    stvx_upper, stvx_lower = [], []
    stvx_main_up, stvx_main_dn = [], []
    # 分段淡化拆分 (strong/faint 双系列, 2026-11-20 user 纠正口径)
    stvx_upper_s, stvx_upper_f = [], []
    stvx_lower_s, stvx_lower_f = [], []
    stvx_main_up_s, stvx_main_up_f = [], []
    stvx_main_dn_s, stvx_main_dn_f = [], []
    stvx_main_fill_js = "[]"
    stvx_main_flips_js = "[]"
    stvx_inner, stvx_tot, stvx_tot_pct = _est_table_inner(
        [], "ST VX版信号单（Gamma理论预估，v2罚后口径）",
        "ST VX=年龄滑梯+slope5压带(分时K:10:30前0.5/起0.15)+止损只在翻仓根查(固定窗3根)+大跳空GF+双侧上午门控；非实际成交")
    try:
        import sys as _sys3
        _stvx_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stvx")
        if _stvx_dir not in _sys3.path:
            _sys3.path.insert(0, _stvx_dir)
        import stvx_day as _svx
        _day_c = datetime.strptime(day_str, "%Y-%m-%d").date()
        _monday_c = _day_c - timedelta(days=_day_c.weekday())
        _fstart = _monday_c - timedelta(days=45)
        _df1 = None
        if WAVE_DATE:
            _df1 = _env_load_cache_1m(_fstart, _day_c)
        if _df1 is None:
            _chunks = []
            _cs = _fstart
            while _cs <= _day_c:
                _ce = min(_cs + timedelta(days=6), _day_c)
                _cdf = _fetch_1m_range(_cs, _ce)
                _cdf = _cdf.rename(columns={"o": "open", "h": "high",
                                            "l": "low", "c": "close"})
                _cdf["t"] = _cdf["t"].dt.tz_localize(None)
                _chunks.append(_cdf[["t", "open", "high", "low", "close"]])
                _cs = _ce + timedelta(days=1)
            _df1 = (pd.concat(_chunks).sort_values("t")
                    .drop_duplicates("t", keep="last").reset_index(drop=True))
        _sanitize_bars(_df1)  # 坏bar剔除: 自拉 API 帧的幻影 print 不得毒化 ST VX (731.85 曾冻上带挡翻仓)
        _last_hm = tstrs[today_idx[-1]] if today_idx else ""
        _stvx_complete = bool(WAVE_DATE) or _last_hm in ("15:57", "12:57")
        _pl, _perr = _svx.day_payload(_df1, day_str, day_complete=_stvx_complete)
        if _pl is None:
            print(f"  WARN stvx 载荷失败: {_perr}", flush=True)
        else:
            _hm2bi = {tstrs[bi]: bi for bi in today_idx}
            _pos_of = {bi: k for k, bi in enumerate(today_idx)}
            stvx_rts = []
            for _t in _pl["trades"]:
                _obi = _hm2bi.get(_t["ot"])
                _cbi = (_hm2bi.get(_t["ct"]) if _t["closed"]
                        else (today_idx[-1] if today_idx else None))
                if _obi is None or _cbi is None:
                    continue
                stvx_rts.append({"obi": _obi, "odr": _t["dir"], "opx": _t["opx"],
                                 "ors": _t["orsn"], "cbi": _cbi, "cpx": _t["cpx"],
                                 "crs": _t["crsn"], "closed": _t["closed"],
                                 "roi_pen": _t["roi_pen"]})
            # 盘中未完结日: 未平仓位在最新一根合成日终平仓 (2026-11-19 user
            # 令, 与 Envelope 版帧尾平同行为)。day_payload 的 day_complete
            # 语义不动, 合成只在本节展示层 (单子/图钉/tooltip/标题); 翻仓
            # 判定 _by_hm 仍走原始 actions, 不受合成平仓影响。
            _svx_actions = list(_pl["actions"])
            if not _stvx_complete and stvx_rts and not stvx_rts[-1]["closed"]:
                _srt = stvx_rts[-1]
                _srt["closed"] = True
                _srt["crs"] = "日终平仓"
                _svx_actions.append((tstrs[_srt["cbi"]], "平", _srt["odr"],
                                     _srt["cpx"], "日终平仓"))
            stvx_inner, stvx_tot, stvx_tot_pct = _est_table_inner(
                [{**rt, "est_roi": rt["roi_pen"] / 100.0} for rt in stvx_rts],
                "ST VX版信号单（Gamma理论预估，v2罚后口径）",
                "预估=Gamma公式ROI(v2逆势put罚后)×$100k日内sequential复利；"
                "ST VX=年龄滑梯+slope5压带(分时K:10:30前0.5/起0.15)+止损只在翻仓根查(固定窗3根)+大跳空日10:00前不开仓"
                "+双侧上午门控(≥55%/≤-30%→13:30-15:00只平不开、15:00状态恢复)；非实际成交")
            for (hm, act, dr, px, rs) in _svx_actions:
                _bi = _hm2bi.get(hm)
                if _bi is not None:
                    stvx_acts.setdefault(tstrs[_bi], []).append(
                        f"{act}{dr or ''}@{px} {rs}".replace("'", ""))
            _by_hm = {}
            for (hm, act, dr, px, rs) in _pl["actions"]:
                _by_hm.setdefault(hm, []).append((act, dr, rs))
            _flip_hms = {hm for hm, lst in _by_hm.items()
                         if any(a == "平" for a, _, _ in lst)
                         and any(a == "开" for a, _, _ in lst)}

            def _stvx_qpins_add(bi, value, color, rs_txt=""):
                h = float(hist[bi])
                yq = round(h * 1.15 + (0.5 if h > 0 else -0.5), 1) if not np.isnan(h) else 5
                stvx_pins.append({"coord": [tstrs[bi], yq], "value": value,
                                  "itemStyle": {"color": color}, "rs": rs_txt})
            for (hm, act, dr, px, rs) in _svx_actions:
                _bi = _hm2bi.get(hm)
                if _bi is None or hm in _flip_hms:
                    continue
                _xp = round(_pos_of[_bi] + 2.5 / 3, 4)
                _yp = round(float(sig["close"].iloc[_bi]), 2)
                _rsc = rs.replace("'", "")
                if act == "开":
                    _v = "开" + ("C" if dr == "call" else "P")
                    _c = _dir_color(dr, "开")
                    stvx_spins.append({"coord": [_xp, _yp], "value": _v,
                                       "itemStyle": {"color": _c}, "rs": _rsc})
                    _stvx_qpins_add(_bi, _v, _c, _rsc)
                elif act == "平":
                    _v = ("止损" if "止损" in rs else "平") + ("C" if dr == "call" else "P")
                    _c = "#9ca3af"  # 平: 灰 (2026-11-15 规范色)
                    stvx_spins.append({"coord": [_xp, _yp], "value": _v,
                                       "itemStyle": {"color": _c}, "rs": _rsc})
                    _stvx_qpins_add(_bi, _v, _c, _rsc)
            for hm in sorted(_flip_hms):
                _bi = _hm2bi.get(hm)
                if _bi is None:
                    continue
                _ndr = next((dr for a, dr, _ in _by_hm[hm] if a == "开"), None)
                _nrs = next((rs for a, dr, rs in _by_hm[hm] if a == "开"), "")
                _xp = round(_pos_of[_bi] + 2.5 / 3, 4)
                _yp = round(float(sig["close"].iloc[_bi]), 2)
                _v = "翻" + ("C" if _ndr == "call" else "P")
                _c = _dir_color(_ndr, "翻")
                _rsc = _nrs.replace("'", "")
                stvx_spins.append({"coord": [_xp, _yp], "value": _v,
                                   "itemStyle": {"color": _c}, "rs": _rsc})
                _stvx_qpins_add(_bi, _v, _c, _rsc)
            _sx_placed, _sx_data = [], []
            _sx_max_row = 0
            for rt in stvx_rts:
                if not rt["closed"]:
                    continue
                _cbi = rt["cbi"]
                _row = 0
                while any(abs(_cbi - _oc) < 15 and _or == _row
                          for (_oc, _or) in _sx_placed):
                    _row += 1
                _sx_placed.append((_cbi, _row))
                _sx_max_row = max(_sx_max_row, _row)
                _yy = round(_base + _row * _row_h, 2)
                _vp = rt["roi_pen"]
                _sx_data.append(
                    '{"value":["%s",%.2f],"lbl":"%s","label":{"color":THEME.tradeLabelColor(%s)}}'
                    % (tstrs[_cbi], _yy, f"{_vp:+.1f}%", repr(float(_vp))))
            stvx_scat_js = "[" + ",".join(_sx_data) + "]"
            if _sx_data:
                y_max_js = max(y_max_js, round(
                    _base + (_sx_max_row + 1) * _row_h + _rng * 0.05, 2))
            stvx_mark_area_js = "[" + ",".join(
                "[{xAxis:'%s',itemStyle:{color:THEME.pnlBgColorST(%s)}},{xAxis:'%s'}]" % (
                    tstrs[rt["obi"]], round(rt["roi_pen"], 2), tstrs[rt["cbi"]])
                for rt in stvx_rts) + "]"
            _bd = {hm: (fu, fl) for (hm, fu, fl) in _pl["bands"]}
            for _bi in today_idx:
                _fu, _fl = _bd.get(tstrs[_bi], (None, None))
                stvx_upper.append(_fu)
                stvx_lower.append(_fl)
            _bm = {hm: (u, d) for (hm, u, d) in _pl["bands_main"]}
            for _bi in today_idx:
                _mu, _md = _bm.get(tstrs[_bi], (None, None))
                stvx_main_up.append(_mu)
                stvx_main_dn.append(_md)
            # main_fill 着色段归属改按 VX 交易趋势 (2026-11-20 18:00 user
            # 令: 段统一到交易信号——信号翻了就是新段); 四边形数值/几何
            # (本体活动带/收盘) 一律不动, 只换逐根 trend 字段 (JS
            # renderItem 按它选绿/红填充)。0 沿用上一根, 帧首默认 buy,
            # 与下方 _seg2 拆轨同规则。
            _btr = {hm: tr for hm, tr in zip(_pl["day_hms"], _pl["trend_day"])}
            _mf_rows = []
            _last_vx = 1
            for _row in _pl["main_fill"]:
                _t = _btr.get(_row[0], 0)
                if _t == 1 or _t == -1:
                    _last_vx = _t
                _mf_rows.append([_row[0], _row[1], _row[2], _row[3],
                                 _row[4], _row[5], _last_vx])
            stvx_main_fill_js = json.dumps(_mf_rows)
            _mf = []
            for (_hm, _val, _tr) in _pl["main_flips"]:
                _buy = _tr == 1
                _col = "#089981" if _buy else "#f23645"
                _mf.append({"value": [_hm, _val],
                            "itemStyle": {"color": _col},
                            "label": {"formatter": "Buy" if _buy else "Sell",
                                      "position": "bottom" if _buy else "top",
                                      "backgroundColor": _col, "color": "#fff",
                                      "borderRadius": 4, "padding": [3, 6],
                                      "fontSize": 12}})
            stvx_main_flips_js = json.dumps(_mf, ensure_ascii=False)
            # ---- 分段淡化拆轨 (2026-11-20 user 纠正口径, 推翻整轨角色淡化):
            # 棘轮 vs 辅助轨按段分——辅助轨 = 趋势反侧的那条带线 (淡化),
            # 主棘轮 = 趋势同侧、实际托/压价格的那条 (全强度): sell 段下轨
            # 是辅助轨 (faint)、上轨是棘轮 (strong); buy 段上轨是辅助轨
            # (faint)、下轨是棘轮 (strong)。(首版 eeb8a15ab 做反, 已对调。)
            # 同一根
            # 只进一条系列、另一条 null。分段来源 2026-11-20 18:00 user
            # 令再统一: 压带轨与本体轨都按 VX 交易趋势分段 (payload
            # trend_day = 内核 trend 原样)——信号翻了就是新段, 本体轨的
            # 主/辅角色与段色都跟着交易信号换 (此前本体轨按 main_fill
            # 逐根本体 trend 分段, 与交易信号分家时视觉上像在反段开仓);
            # 本体带线数值本身不动。趋势 0 的根沿用上一根的段 (帧首默认
            # buy)。
            _trs_vx = [_btr.get(tstrs[_bi], 0) for _bi in today_idx]

            def _seg2(_vals, _trs, _upper):
                _so, _fo, _last = [], [], 1
                for _v, _t in zip(_vals, _trs):
                    if _t == 1 or _t == -1:
                        _last = _t
                    _strong = (_last == -1) if _upper else (_last == 1)
                    _so.append(_v if _strong else None)
                    _fo.append(None if _strong else _v)
                return _so, _fo

            stvx_upper_s, stvx_upper_f = _seg2(stvx_upper, _trs_vx, True)
            stvx_lower_s, stvx_lower_f = _seg2(stvx_lower, _trs_vx, False)
            stvx_main_up_s, stvx_main_up_f = _seg2(stvx_main_up, _trs_vx, True)
            stvx_main_dn_s, stvx_main_dn_f = _seg2(stvx_main_dn, _trs_vx, False)
            print(f"  stvx: {len(stvx_rts)} 单, 罚后连乘 {stvx_tot_pct:+.1%}, "
                  f"am={_pl['am']}", flush=True)
    except Exception as _se:
        print(f"  WARN stvx 计算失败, 本节为空: {_se}", flush=True)
    stvx_title_txt = (f"{day_str} (ST VX版) | {gap_txt} | 全天ST VX | "
                      f"{'+' if stvx_tot >= 0 else '-'}${abs(stvx_tot):,.0f} "
                      f"({stvx_tot_pct:+.1%})")
    stvx_pnl_e = (("{pnl_pos|+" if stvx_tot >= 0 else "{pnl_neg|-")
                  + f"${abs(stvx_tot):,.0f} ({stvx_tot_pct:+.1%})" + "}")
    stvx_title_echart = (day_str + " ({stvx|ST VX}版) | " + gap_e
                         + " | 全天{stvx|ST VX} | " + stvx_pnl_e)

    # ---- CE版 (Chandelier Exit, ATR1x1.85 Heikin Ashi): 与研究 ce_backtest.py 同算法。
    # 方向在全 sig (warmup+当日) 上连续算, 不按日重置; 当日交易按日页口径
    # (-100口径Gamma, 日内$100k sequential, 信号bar记时间、下根开盘结算)。
    # 日终平: 完整日 (回放/15:57/早收12:57) + 盘中未完结日 (最新一根当作
    # 日终平, 2026-11-19 user 令, 与 Envelope 版帧尾平同行为)。
    def _ce_compute(df):
        o = df["open"].to_numpy(float)
        h = df["high"].to_numpy(float)
        l = df["low"].to_numpy(float)
        c = df["close"].to_numpy(float)
        n = len(df)
        ha_c = (o + h + l + c) / 4.0
        ha_o = np.empty(n)
        ha_o[0] = (o[0] + c[0]) / 2.0
        for i in range(1, n):
            ha_o[i] = (ha_o[i - 1] + ha_c[i - 1]) / 2.0
        ha_h = np.maximum(np.maximum(h, ha_o), ha_c)
        ha_l = np.minimum(np.minimum(l, ha_o), ha_c)
        tr_arr = np.empty(n)
        tr_arr[0] = ha_h[0] - ha_l[0]
        for i in range(1, n):
            tr_arr[i] = max(ha_h[i] - ha_l[i], abs(ha_h[i] - ha_c[i - 1]),
                            abs(ha_l[i] - ha_c[i - 1]))
        atr_val = 1.85 * tr_arr  # RMA(1) = TR 本身
        long_raw = ha_h - atr_val
        short_raw = ha_l + atr_val
        long_stop = np.empty(n)
        short_stop = np.empty(n)
        long_stop[0] = long_raw[0]
        short_stop[0] = short_raw[0]
        for i in range(1, n):
            long_stop[i] = (max(long_raw[i], long_stop[i - 1])
                            if ha_c[i - 1] > long_stop[i - 1] else long_raw[i])
            short_stop[i] = (min(short_raw[i], short_stop[i - 1])
                             if ha_c[i - 1] < short_stop[i - 1] else short_raw[i])
        dir_ = np.ones(n, dtype=int)
        for i in range(1, n):
            if ha_c[i] > short_stop[i - 1]:
                dir_[i] = 1
            elif ha_c[i] < long_stop[i - 1]:
                dir_[i] = -1
            else:
                dir_[i] = dir_[i - 1]
        buy_sig = np.zeros(n, dtype=bool)
        sell_sig = np.zeros(n, dtype=bool)
        buy_sig[1:] = (dir_[1:] == 1) & (dir_[:-1] == -1)
        sell_sig[1:] = (dir_[1:] == -1) & (dir_[:-1] == 1)
        return dir_, buy_sig, sell_sig

    _ce_dir, _ce_buy, _ce_sell = _ce_compute(sig)
    _ce_today_o = sig["open"].iloc[today_idx].to_numpy(float)
    _ce_today_c = sig["close"].iloc[today_idx].to_numpy(float)
    _ce_today_l = sig["low"].iloc[today_idx].to_numpy(float)
    _ce_today_h = sig["high"].iloc[today_idx].to_numpy(float)
    _ce_last_hm = tstrs[today_idx[-1]]
    # 日终平判定: 完整日 (回放/15:57/早收12:57) 照旧; 盘中未完结日也平
    # (2026-11-19 user 令: 最新一根当作日终平, 与 Envelope 版帧尾平同行为)
    _ce_eod = bool(WAVE_DATE) or (_ce_last_hm == "15:57") or (
        vh_live._is_early_close_day(sig["t"].iloc[today_idx[-1]]) and _ce_last_hm == "12:57") \
        or _day_partial
    _ce_trades = []  # 已平: {dir,px0,obi,reason,cbi,px1,close_reason} (obi/cbi = 当日位置)
    _ce_open = None  # 盘中未平: {dir,px0,obi,reason}
    _ce_m = len(today_idx)
    if day_str != "2026-06-17" and _ce_m >= 2:
        for k in range(_ce_m):
            bi = today_idx[k]
            is_first = (k == 0)
            is_last = (k == _ce_m - 1)
            if _ce_open is None and is_first:
                _d0 = "call" if _ce_dir[bi] == 1 else "put"
                _ce_open = {"dir": _d0, "px0": float(_ce_today_o[1]), "obi": 0,
                            "reason": "CE开盘"}
                continue
            if _ce_open is not None:
                if _ce_buy[bi] and _ce_open["dir"] != "call":
                    _px1 = float(_ce_today_o[k + 1]) if not is_last else float(_ce_today_c[k])
                    _ce_trades.append({**_ce_open, "cbi": k, "px1": _px1,
                                       "close_reason": "CE翻转"})
                    _ce_open = ({"dir": "call", "px0": float(_ce_today_o[k + 1]), "obi": k,
                                 "reason": "CE买入"} if not is_last else None)
                    continue
                if _ce_sell[bi] and _ce_open["dir"] != "put":
                    _px1 = float(_ce_today_o[k + 1]) if not is_last else float(_ce_today_c[k])
                    _ce_trades.append({**_ce_open, "cbi": k, "px1": _px1,
                                       "close_reason": "CE翻转"})
                    _ce_open = ({"dir": "put", "px0": float(_ce_today_o[k + 1]), "obi": k,
                                 "reason": "CE卖出"} if not is_last else None)
                    continue
            if is_last and _ce_open is not None and _ce_eod:
                _ce_trades.append({**_ce_open, "cbi": k, "px1": float(_ce_today_c[k]),
                                   "close_reason": "日终平"})
                _ce_open = None
    _ce_rts = []
    for tr in _ce_trades:
        _ce_rts.append({"obi": today_idx[tr["obi"]], "odr": tr["dir"], "opx": tr["px0"],
                        "ors": tr["reason"], "cbi": today_idx[tr["cbi"]], "cpx": tr["px1"],
                        "crs": tr["close_reason"], "closed": True,
                        "est_roi": gamma_roi_100(tr["dir"], tr["px0"], tr["px1"])})
    if _ce_open is not None:
        _ce_rts.append({"obi": today_idx[_ce_open["obi"]], "odr": _ce_open["dir"],
                        "opx": _ce_open["px0"], "ors": _ce_open["reason"],
                        "cbi": today_idx[-1], "cpx": float(_ce_today_c[-1]),
                        "crs": None, "closed": False,
                        "est_roi": gamma_roi_100(_ce_open["dir"], _ce_open["px0"],
                                                 float(_ce_today_c[-1]))})
    ce_inner, ce_tot, ce_tot_pct = _est_table_inner(
        _ce_rts,
        "CE版信号单（Gamma理论预估，-100口径）",
        "预估=Gamma公式ROI×$100k日内sequential复利，单笔floor -100%；非实际成交")
    ce_title_txt = (f"{day_str} (CE版) | {gap_txt} | 全天CE | "
                    f"{'+' if ce_tot >= 0 else '-'}${abs(ce_tot):,.0f} ({ce_tot_pct:+.1%})")
    ce_pnl_e = ("{pnl_pos|+" if ce_tot >= 0 else "{pnl_neg|-") + f"${abs(ce_tot):,.0f} ({ce_tot_pct:+.1%})" + "}"
    ce_title_echart = day_str + " ({ce|CE}版) | " + gap_e + " | 全天{ce|CE} | " + ce_pnl_e
    # CE 图钉/价格钉: 非翻在前(时间序), 翻在后(时间序), 与注入器一致
    def _ce_letter(d):
        return "C" if d == "call" else "P"

    def _ce_pin_color(kind, d):
        if kind == "flip":
            return "#00cc00" if d == "call" else "#ff0000"
        if kind == "open":
            return "#00cc00" if d == "call" else "#ff0000"
        return "#9ca3af"  # 平: 灰 (2026-11-15 规范色)

    def _ce_yq(p):
        hv = vals[p] if p < len(vals) else None
        if hv is None or (isinstance(hv, float) and np.isnan(hv)):
            return 5
        return round(hv * 1.15 + (0.5 if hv > 0 else -0.5), 1)

    _ce_nonflip = []
    _ce_flips = []
    _ce_all_opens = [(tr["obi"], tr["reason"], tr["dir"]) for tr in _ce_trades]
    if _ce_open is not None:
        _ce_all_opens.append((_ce_open["obi"], _ce_open["reason"], _ce_open["dir"]))
    for (_obi, _rs, _dr) in _ce_all_opens:
        if _rs == "CE开盘":
            _ce_nonflip.append((_obi, "开" + _ce_letter(_dr),
                                _ce_pin_color("open", _dr), "CE开盘"))
        else:
            _ce_flips.append((_obi, "翻" + _ce_letter(_dr),
                              _ce_pin_color("flip", _dr),
                              "CE买入" if _dr == "call" else "CE卖出"))
    if _ce_trades:
        _lt = _ce_trades[-1]
        _ce_nonflip.append((_lt["cbi"], "平" + _ce_letter(_lt["dir"]),
                            _ce_pin_color("close", _lt["dir"]),
                            "日终平仓" if _lt["close_reason"] == "日终平" else "CE翻转"))
    _ce_nonflip.sort()
    _ce_flips.sort()
    _ce_ordered = _ce_nonflip + _ce_flips
    ce_pins = [{"coord": [tstrs[today_idx[p]], _ce_yq(p)], "value": v,
                "itemStyle": {"color": c}, "rs": rs} for (p, v, c, rs) in _ce_ordered]
    ce_spins = [{"coord": [round(p + 2.5 / 3, 4), round(float(_ce_today_c[p]), 2)],
                 "value": v, "itemStyle": {"color": c}, "rs": rs}
                for (p, v, c, rs) in _ce_ordered]
    # CE 每单标签 (VH 风格) + 行错开, 共用 VH/QQE 的 _base/_row_h
    _ce_placed, _ce_scat_data = [], []
    _ce_max_row = 0
    for tr in _ce_trades:
        _cbi = tr["cbi"]
        _row = 0
        while any(abs(_cbi - _oc) < 15 and _or == _row for (_oc, _or) in _ce_placed):
            _row += 1
        _ce_placed.append((_cbi, _row))
        _ce_max_row = max(_ce_max_row, _row)
        _yy = round(_base + _row * _row_h, 2)
        _vpct = gamma_roi_100(tr["dir"], tr["px0"], tr["px1"]) * 100
        _ce_scat_data.append(
            '{"value":["%s",%.2f],"lbl":"%s","label":{"color":THEME.tradeLabelColor(%s)}}' % (
                tstrs[today_idx[_cbi]], _yy, f"{_vpct:+.1f}%", repr(float(_vpct))))
    ce_scat_js = "[" + ",".join(_ce_scat_data) + "]"
    y_max_js = max(y_max_js, round(_base + (_ce_max_row + 1) * _row_h + _rng * 0.05, 2))
    # CE 色带 (已平单; 盘中未平画浮动带到最后一根, 与 QQE 持仓带一致)
    _ce_ma = []
    for tr in _ce_trades:
        _pct = round(gamma_roi_100(tr["dir"], tr["px0"], tr["px1"]) * 100, 2)
        _ce_ma.append("[{xAxis:'%s',itemStyle:{color:THEME.pnlBgColor(%s)}},{xAxis:'%s'}]" % (
            tstrs[today_idx[tr["obi"]]], _pct, tstrs[today_idx[tr["cbi"]]]))
    if _ce_open is not None:
        _fpct = round(gamma_roi_100(_ce_open["dir"], _ce_open["px0"],
                                    float(_ce_today_c[-1])) * 100, 2)
        _ce_ma.append("[{xAxis:'%s',itemStyle:{color:THEME.pnlBgColor(%s)}},{xAxis:'%s'}]" % (
            tstrs[today_idx[_ce_open["obi"]]], _fpct, tstrs[today_idx[-1]]))
    ce_mark_area_js = "[" + ",".join(_ce_ma) + "]"
    # CE tooltip 动作 (与生成器 acts 格式一致: 开call@px 原因)
    ce_acts = {}
    for tr in _ce_trades:
        _ot = tstrs[today_idx[tr["obi"]]]
        _ct = tstrs[today_idx[tr["cbi"]]]
        ce_acts.setdefault(_ot, []).append(
            f"开{tr['dir']}@{tr['px0']:.2f} {tr['reason']}")
        _crs = "日终平仓" if tr["close_reason"] == "日终平" else "CE翻转"
        ce_acts.setdefault(_ct, []).append(
            f"平{tr['dir']}@{tr['px1']:.2f} {_crs}")
    if _ce_open is not None:
        ce_acts.setdefault(tstrs[today_idx[_ce_open["obi"]]], []).append(
            f"开{_ce_open['dir']}@{_ce_open['px0']:.2f} {_ce_open['reason']}")
    # CE 红绿 B/S 标签 (信号bar锚定, 跳过首根 dayopen; 与注入器一致)
    ce_bs = []
    for k in range(1, _ce_m):
        bi = today_idx[k]
        if _ce_buy[bi]:
            ce_bs.append({"value": [tstrs[bi], round(float(_ce_today_l[k]), 2)],
                          "label": {"formatter": "B", "position": "bottom",
                                    "backgroundColor": "#1a9e54", "color": "#fff",
                                    "borderRadius": 4, "padding": [3, 6], "fontSize": 12}})
        elif _ce_sell[bi]:
            ce_bs.append({"value": [tstrs[bi], round(float(_ce_today_h[k]), 2)],
                          "label": {"formatter": "S", "position": "top",
                                    "backgroundColor": "#e5484d", "color": "#fff",
                                    "borderRadius": 4, "padding": [3, 6], "fontSize": 12}})
    ce_bs_js = json.dumps(ce_bs, ensure_ascii=False)

    # ---- Envelope版 (V2 参数 + 0.5 止损, 接点合并显示; 2026-11-09 全覆盖) ----
    # 信号柱收盘进出, 止损按止损价, 日终=15:56柱收盘; 日内$100k sequential, 单笔floor -100%。
    # 1m 来源: 回放优先本地缓存 (与注入器同源; 缓存是 IEX/SIP 混合, API 单一 feed 无法复现),
    # 缓存不存在/覆盖不足时走 API (warmup IEX + 当日 df1m); 实盘 (WAVE_DATE 为空) 一律走 API。
    # EOD 只在完整日: _env_eod 用 bool(WAVE_DATE) (绝不用 is not None, 见 CE 假日终平 bug)。
    _env_day = day if day is not None else datetime.now(ET).date()
    _env_df = None
    _env_src = "api"
    if WAVE_DATE:
        _env_df = _env_load_cache_1m(_env_day - timedelta(days=10), _env_day)
        if _env_df is not None:
            _env_src = "cache"
    if _env_df is None:
        try:
            _warm = _fetch_1m_range(_env_day - timedelta(days=10), _env_day - timedelta(days=1))
            _today1 = df1m[["t", "o", "h", "l", "c", "v"]].copy()
            _today1.columns = ["t", "open", "high", "low", "close", "v"]
            _env_df = pd.concat([_warm.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close"}),
                                 _today1[["t", "open", "high", "low", "close"]]],
                                ignore_index=True)
            _env_df = _env_df.sort_values("t").drop_duplicates("t", keep="last").reset_index(drop=True)
            _m1 = _env_df["t"].dt.hour * 60 + _env_df["t"].dt.minute
            _env_df = _env_df[(_m1 >= 570) & (_m1 <= 959)].reset_index(drop=True)
        except Exception as _e:
            print(f"  WARN envelope 1m 拉取失败: {_e}", flush=True)
            _env_df = None
    _env_trades = []  # 已平对象化: {obi,cbi,dir,px0,px1,entry_kind,entry_rs,close_rs,roi,pnl}
    _env_open = None  # 盘中未平: {dir,px0,obi,entry_kind,entry_rs}
    _env_jx_groups = 0  # 接点合并组数 (展示口径)
    _env_n_orig = 0     # 合并前已平单数 (展示口径)
    _env_ind = None
    _env_day_idx = []
    _env_trend_raw = []  # 每根趋势方向 (与 _env_df 行对齐)
    if _env_df is not None and len(_env_df):
        _env_ind = _env_indicators(_env_df)
        _env_dates = _env_df["t"].dt.strftime("%Y-%m-%d").values
        _env_mins = _env_df["t"].dt.hour.values * 60 + _env_df["t"].dt.minute.values
        _env_day_idx = [i for i in range(len(_env_df))
                        if _env_dates[i] == day_str and 570 <= int(_env_mins[i]) <= 959]
        _env_raw, _env_open_raw, _env_trend_raw = _env_simulate(_env_df, _env_ind, day_str)
        if day_str == "2026-06-17":
            _env_raw, _env_open_raw = [], None
        _env_last_hm = _env_df["t"].dt.strftime("%H:%M").iloc[_env_day_idx[-1]] if _env_day_idx else ""
        _env_eod = bool(WAVE_DATE) or (_env_last_hm == "15:59") or (
            vh_live._is_early_close_day(_env_df["t"].iloc[_env_day_idx[-1]]) and _env_last_hm == "12:57") \
            if _env_day_idx else bool(WAVE_DATE)
        if _env_open_raw is not None and _env_eod and _env_day_idx:
            _last_i = _env_day_idx[-1]
            _env_raw.append((_env_open_raw["obi"], _last_i, _env_open_raw["dir"],
                             _env_open_raw["px0"], float(_env_df["close"].iloc[_last_i]), "EOD"))
            _env_open_raw = None
        # 接点合并 (展示层): 止损出场根同向重开的链并成一笔, 列表/色带/日合计
        # 全部按合并后序列走 (与存量回填页同一副面孔)。盘中未平仓不并 (仍浮动显示),
        # 日终平仓补单已在上面入列, 完整日会自然并入链。
        _env_n_orig = len(_env_raw)
        _env_raw, _env_jx_groups, _env_chain_flags = _env_merge_junctions(_env_raw)
        for _j, (_eb, _xb, _d, _epx, _xpx, _rs) in enumerate(_env_raw):
            _is_flip = (_j > 0 and _env_raw[_j - 1][1] == _eb and _env_raw[_j - 1][5] == "FLIP")
            _dr = "call" if _d == "long" else "put"
            _env_trades.append({
                "obi": int(_eb), "cbi": int(_xb), "dir": _dr,
                "px0": float(_epx), "px1": float(_xpx),
                "is_chain": bool(_env_chain_flags[_j]),
                "entry_kind": "flip" if _is_flip else "open",
                "entry_rs": "Envelope买入" if _d == "long" else "Envelope卖出",
                "close_rs": {"SL": "止损", "FLIP": "Envelope翻转",
                             "EXIT T": "EXIT T", "EOD": "日终平仓"}.get(_rs, _rs),
                "roi": gamma_roi_100(_dr, float(_epx), float(_xpx))})
        if _env_open_raw is not None:
            _env_open = {"dir": "call" if _env_open_raw["dir"] == "long" else "put",
                         "px0": float(_env_open_raw["px0"]), "obi": int(_env_open_raw["obi"]),
                         "entry_kind": _env_open_raw["entry_kind"] or "open",
                         "entry_rs": "Envelope买入" if _env_open_raw["dir"] == "long" else "Envelope卖出"}
    _env_cap = 100000.0
    for _tr in _env_trades:
        _tr["pnl"] = _env_cap * _tr["roi"]
        _env_cap *= (1.0 + _tr["roi"])
    env_tot = _env_cap - 100000.0
    env_tot_pct = _env_cap / 100000.0 - 1.0
    print(f"  envelope({_env_src}): {_env_trades and len(_env_trades) or 0} 单, "
          f"tot=${env_tot:,.0f} ({env_tot_pct:+.1%})"
          + (f", 持仓中 {_env_open['dir']}@{_env_open['px0']:.2f}" if _env_open else ""),
          flush=True)
    # ---- Envelope 显示对象 (与 envelope_common.build_consts 同款) ----
    _env_tstr = _env_df["t"].dt.strftime("%H:%M").tolist() if _env_df is not None else []
    _env_closes = _env_df["close"].to_numpy(float) if _env_df is not None else []
    # 固定x轴为全天 09:30-15:59 (390根1m), 数据左对齐, 右侧留白 (与3m times同款;
    # 只用已走bars会使类目轴被拉满全宽, 轨道/色带相对股价曲线横向拉伸 390/N 倍,
    # 盘中 N=280 时拉伸 1.39 倍: 14:09 的轨道画到 100% 宽处, 股价只到 71.7%)
    _env_times1m = []
    _h1, _m1 = 9, 30
    for _k in range(390):
        _env_times1m.append(f"{_h1:02d}:{_m1:02d}")
        _m1 += 1
        if _m1 >= 60:
            _h1 += 1
            _m1 -= 60
    _pmins = []
    for _t in times:
        _hh, _mm = _t.split(":")
        _pmins.append(int(_hh) * 60 + int(_mm))

    def _env_snap(t1m):
        _hh, _mm = t1m.split(":")
        _mnt = int(_hh) * 60 + int(_mm)
        return max(0, bisect.bisect_right(_pmins, _mnt) - 1)

    def _env_letter(d):
        return "C" if d == "call" else "P"

    def _env_pin_color(kind, d):
        if kind == "flip":
            return "#00cc00" if d == "call" else "#ff0000"
        if kind == "open":
            return "#00cc00" if d == "call" else "#ff0000"
        return "#9ca3af"  # 平: 灰 (2026-11-15 规范色)

    def _env_yq(slot):
        _hv = vals[slot] if slot < len(vals) else None
        if _hv is None or (isinstance(_hv, float) and np.isnan(_hv)):
            return 5
        return round(_hv * 1.15 + (0.5 if _hv > 0 else -0.5), 1)

    _env_nonflip, _env_flips = [], []
    for _j, _tr in enumerate(_env_trades):
        _slot_o = _env_snap(_env_tstr[_tr["obi"]])
        if _tr["entry_kind"] == "flip":
            _env_flips.append((_slot_o, "翻" + _env_letter(_tr["dir"]),
                               _env_pin_color("flip", _tr["dir"]), _tr["entry_rs"]))
        else:
            _env_nonflip.append((_slot_o, "开" + _env_letter(_tr["dir"]),
                                 _env_pin_color("open", _tr["dir"]), _tr["entry_rs"]))
        _is_last = _j == len(_env_trades) - 1
        # 翻仓平仓圈与下一笔的翻仓开仓圈同点只留一个 (下一笔照常画翻仓圈)
        if _tr["close_rs"] != "Envelope翻转" or _is_last:
            _slot_c = _env_snap(_env_tstr[_tr["cbi"]])
            _env_nonflip.append((_slot_c, "平" + _env_letter(_tr["dir"]),
                                 _env_pin_color("close", _tr["dir"]), _tr["close_rs"]))
    if _env_open is not None:
        _slot_o = _env_snap(_env_tstr[_env_open["obi"]])
        if _env_open["entry_kind"] == "flip":
            _env_flips.append((_slot_o, "翻" + _env_letter(_env_open["dir"]),
                               _env_pin_color("flip", _env_open["dir"]), _env_open["entry_rs"]))
        else:
            _env_nonflip.append((_slot_o, "开" + _env_letter(_env_open["dir"]),
                                 _env_pin_color("open", _env_open["dir"]), _env_open["entry_rs"]))
    _env_nonflip.sort()
    _env_flips.sort()
    _env_ordered = _env_nonflip + _env_flips
    env_pins = [{"coord": [times[s], _env_yq(s)], "value": v,
                 "itemStyle": {"color": c}, "rs": rs} for (s, v, c, rs) in _env_ordered]

    def _env_spin_x(t1m):
        _hh, _mm = t1m.split(":")
        return round((int(_hh) * 60 + int(_mm) - 570) / 3, 4)

    def _env_spin_item(x, y, value, color, rs, is_call):
        _d = {"coord": [x, y], "value": value, "itemStyle": {"color": color}, "rs": rs}
        if is_call:
            _d["symbolRotate"] = 180
            _d["label"] = {"offset": [0, 5]}
        return _d

    _env_nf_spins, _env_f_spins = [], []
    for _j, _tr in enumerate(_env_trades):
        _ic = _tr["dir"] == "call"
        if _tr["entry_kind"] == "flip":
            _env_f_spins.append(_env_spin_item(_env_spin_x(_env_tstr[_tr["obi"]]),
                                                round(float(_env_closes[_tr["obi"]]), 2),
                                                "翻" + _env_letter(_tr["dir"]),
                                                _env_pin_color("flip", _tr["dir"]),
                                                _tr["entry_rs"], _ic))
        else:
            _env_nf_spins.append(_env_spin_item(_env_spin_x(_env_tstr[_tr["obi"]]),
                                                 round(float(_env_closes[_tr["obi"]]), 2),
                                                 "开" + _env_letter(_tr["dir"]),
                                                 _env_pin_color("open", _tr["dir"]),
                                                 _tr["entry_rs"], _ic))
        _is_last = _j == len(_env_trades) - 1
        if _tr["close_rs"] != "Envelope翻转" or _is_last:
            _env_nf_spins.append(_env_spin_item(_env_spin_x(_env_tstr[_tr["cbi"]]),
                                                 round(float(_env_closes[_tr["cbi"]]), 2),
                                                 "平" + _env_letter(_tr["dir"]),
                                                 _env_pin_color("close", _tr["dir"]),
                                                 _tr["close_rs"], _ic))
    if _env_open is not None:
        _ic = _env_open["dir"] == "call"
        if _env_open["entry_kind"] == "flip":
            _env_f_spins.append(_env_spin_item(_env_spin_x(_env_tstr[_env_open["obi"]]),
                                                round(float(_env_closes[_env_open["obi"]]), 2),
                                                "翻" + _env_letter(_env_open["dir"]),
                                                _env_pin_color("flip", _env_open["dir"]),
                                                _env_open["entry_rs"], _ic))
        else:
            _env_nf_spins.append(_env_spin_item(_env_spin_x(_env_tstr[_env_open["obi"]]),
                                                 round(float(_env_closes[_env_open["obi"]]), 2),
                                                 "开" + _env_letter(_env_open["dir"]),
                                                 _env_pin_color("open", _env_open["dir"]),
                                                 _env_open["entry_rs"], _ic))
    _env_nf_spins.sort(key=lambda s: (s["coord"][0], s["value"]))
    _env_f_spins.sort(key=lambda s: (s["coord"][0], s["value"]))
    env_spins = _env_nf_spins + _env_f_spins
    _env_open_keys = {(s["coord"][0], s["value"][-1]) for s in env_spins if s["value"].startswith("开")}
    env_spins = [s for s in env_spins
                 if not (s["value"].startswith("平") and (s["coord"][0], s["value"][-1]) in _env_open_keys)]
    # Envelope 每单标签 (30 分钟错行, 行高 rng*0.05, 与注入器一致; 标签在 envelope 版隐藏)
    _env_row_h = _rng * 0.05
    _env_base = _hi + _rng * 0.03
    _env_placed, _env_scat_data = [], []
    for _tr in sorted(_env_trades, key=lambda t: t["cbi"]):
        _t_c = _env_tstr[_tr["cbi"]]
        _mnt = int(_t_c[:2]) * 60 + int(_t_c[3:5])
        _row = 0
        while any(abs(_mnt - _om) < 30 and _orow == _row for (_om, _orow) in _env_placed):
            _row += 1
        _env_placed.append((_mnt, _row))
        _yy = round(_env_base + _row * _env_row_h, 2)
        _col = "#1a9e54" if _tr["roi"] >= 0 else "#e5484d"
        _env_scat_data.append({"value": [_t_c, _yy], "lbl": "(%s)" % format(_tr["roi"], "+.1%"),
                               "label": {"color": _col}})
    env_scat_js = json.dumps(_env_scat_data, ensure_ascii=False)
    # Envelope 色带 (1m 时间; 盘中未平画浮动带到最后一根)
    _env_ma = []
    for _tr in _env_trades:
        _pct = round(_tr["roi"] * 100, 2)
        _env_ma.append("[{xAxis:'%s',itemStyle:{color:THEME.pnlBgColor(%s)}},{xAxis:'%s'}]" % (
            _env_tstr[_tr["obi"]], _pct, _env_tstr[_tr["cbi"]]))
    if _env_open is not None and _env_day_idx:
        _fpct = round(gamma_roi_100(_env_open["dir"], _env_open["px0"],
                                    float(_env_closes[_env_day_idx[-1]])) * 100, 2)
        _env_ma.append("[{xAxis:'%s',itemStyle:{color:THEME.pnlBgColor(%s)}},{xAxis:'%s'}]" % (
            _env_tstr[_env_open["obi"]], _fpct, _env_tstr[_env_day_idx[-1]]))
    env_mark_area_js = "[" + ",".join(_env_ma) + "]"
    # Envelope 信号单表 (1m 时间; 持仓中行只算浮动、不推进 cap, 与 _est_table_inner 一致)
    def _env_money(x):
        return ("%s$%s" % ("+" if x >= 0 else "-", format(abs(x), ",.0f")))
    _env_rows = []
    for _k, _tr in enumerate(_env_trades, 1):
        _ec = "#1a9e54" if _tr["pnl"] >= 0 else "#e5484d"
        _op = _tr["entry_rs"] + _env_letter(_tr["dir"])
        _env_rows.append(
            "<tr><td>%d</td><td>%s</td><td>%s</td><td>—</td>"
            "<td>—</td><td>%.2f</td><td>%s</td><td>—</td><td>%.2f</td>"
            "<td style=\"color:#888888;font-weight:bold\">—</td>"
            "<td style=\"color:#888888\">—</td>"
            "<td style=\"color:%s\">~%s</td>"
            "<td style=\"color:%s\">~%s</td></tr>" % (
                _k, _env_tstr[_tr["obi"]], _op, _tr["px0"], _env_tstr[_tr["cbi"]], _tr["px1"],
                _ec, _env_money(_tr["pnl"]), _ec, format(_tr["roi"], "+.1%")))
    if _env_open is not None and _env_day_idx:
        _froi = gamma_roi_100(_env_open["dir"], _env_open["px0"],
                              float(_env_closes[_env_day_idx[-1]]))
        _fpnl = _env_cap * _froi
        _ec = "#1a9e54" if _fpnl >= 0 else "#e5484d"
        _op = _env_open["entry_rs"] + _env_letter(_env_open["dir"])
        _env_rows.append(
            "<tr><td>%d</td><td>%s</td><td>%s</td><td>—</td>"
            "<td>—</td><td>%.2f</td><td>持仓中</td><td>—</td><td>%.2f</td>"
            "<td style=\"color:#888888;font-weight:bold\">—</td>"
            "<td style=\"color:#888888\">—</td>"
            "<td style=\"color:%s\">~%s</td>"
            "<td style=\"color:%s\">~%s</td></tr>" % (
                len(_env_trades) + 1, _env_tstr[_env_open["obi"]], _op, _env_open["px0"],
                float(_env_closes[_env_day_idx[-1]]),
                _ec, _env_money(_fpnl), _ec, format(_froi, "+.1%")))
    _env_rows.append(
        "<tr style=\"background:#f2f2f2;font-weight:bold\"><td colspan=\"9\">合计"
        "</td><td>—</td><td></td>"
        "<td>~%s</td><td></td></tr>" % _env_money(env_tot))
    _env_note = ("V2参数 · 0.5止损 · 止损同向接点已合并 %d 组（原 %d 单）"
                 % (_env_jx_groups, _env_n_orig))
    env_inner = (
        "<div style=\"font-size:14px;font-weight:bold;margin-bottom:6px\">"
        "Envelope版信号单（Gamma理论预估，-100口径）</div>"
        "<div style=\"font-size:12px;color:#555;margin-bottom:6px\">"
        + _env_note + "</div>"
        "<div style=\"overflow-x:auto\"><table border=\"1\" cellspacing=\"0\" cellpadding=\"6\" "
        "style=\"border-collapse:collapse;font-size:12px;width:100%;text-align:center;white-space:nowrap\">"
        "<tr style=\"background:#f2f2f2\"><th>#</th><th>开仓</th><th>操作</th><th>张数</th>"
        "<th>开均价</th><th>开SPY</th><th>平仓</th><th>平均价</th><th>平SPY</th><th>实际收益</th><th>实际收益率</th>"
        "<th>预估收益</th><th>预估收益率</th></tr>"
        + "".join(_env_rows) + "</table></div>"
        "<div style=\"font-size:11px;color:#888;margin-top:4px\">"
        "预估=Gamma公式ROI×$100k日内sequential复利，单笔floor -100%；信号按1m算，"
        "Envelope版 x 轴为 1m，标记按精确 1m 时间；非实际成交</div>")
    env_acts = {}
    for _tr in _env_trades:
        _t_o = _env_tstr[_tr["obi"]]
        env_acts.setdefault(_t_o, []).append(
            "开%s@%s %s" % (_env_letter(_tr["dir"]), str(float(_tr["px0"])), _tr["entry_rs"]))
        _t_c = _env_tstr[_tr["cbi"]]
        env_acts.setdefault(_t_c, []).append(
            "平%s@%s %s" % (_env_letter(_tr["dir"]), str(float(_tr["px1"])), _tr["close_rs"]))
    if _env_open is not None:
        _t_o = _env_tstr[_env_open["obi"]]
        env_acts.setdefault(_t_o, []).append(
            "开%s@%s %s" % (_env_letter(_env_open["dir"]), str(float(_env_open["px0"])),
                            _env_open["entry_rs"]))
    env_title_txt = "%s (Envelope版) | %s | 全天Envelope | %s (%s)" % (
        day_str, gap_txt, _env_money(env_tot), format(env_tot_pct, "+.1%"))
    if gap_txt.startswith("上跳"):
        _env_gap_e = "{up|" + gap_txt + "}"
    elif gap_txt.startswith("下跳"):
        _env_gap_e = "{dn|" + gap_txt + "}"
    else:
        _env_gap_e = gap_txt
    _env_pnl_e = ("{pnl_pos|+" if env_tot >= 0 else "{pnl_neg|-") + \
        "$%s (%s)" % (format(abs(env_tot), ",.0f"), format(env_tot_pct, "+.1%")) + "}"
    env_title_echart = day_str + " ({envelope|Envelope}版) | " + _env_gap_e + \
        " | 全天{envelope|Envelope} | " + _env_pnl_e
    # Envelope 轨道 (全 1m) + 悬浮窗 TIP (390 条全覆盖, 3 行依据)
    def _env_v1(a, i):
        _x = float(a[i])
        return round(_x, 2) if _x == _x else None
    if _env_ind is not None and _env_day_idx:
        _up_a, _dn_a, _md_a, _cm_a = (_env_ind["upper"], _env_ind["lower"],
                                      _env_ind["mid"], _env_ind["chan_mid"])
        env_upper = [_env_v1(_up_a, i) for i in _env_day_idx]
        env_lower = [_env_v1(_dn_a, i) for i in _env_day_idx]
        env_mid = [_env_v1(_md_a, i) for i in _env_day_idx]
        env_chanmid = [_env_v1(_cm_a, i) for i in _env_day_idx]
        # 1m 曲线按 Envelope 趋势分段着色: 强涨深绿 / 强跌深红 / 其余深蓝。
        # visualMap 只能给散点上色、线段本身不变色 (探针实测), 故改用分段系列: 每段一个
        # line 系列、各自定 lineStyle 颜色; 相邻段重叠 1 根 bar 保证线不断。
        _env_trend_segs = []
        _tday = [_env_trend_raw[i] for i in _env_day_idx] if _env_trend_raw else []
        # line1m 可能含盘后 bar (实盘 API 常多出 16:00 后的几根, 本地缓存没有),
        # 长度不必与趋势严格相等: 取重叠前缀对齐, 差几根只丢尾巴不断整段。
        if _tday and line1m:
            _nseg = min(len(line1m), len(_tday))
            if len(line1m) != len(_tday):
                print(f"  WARN trend segs: line1m {len(line1m)} vs trend {len(_tday)}, 用前 {_nseg} 根对齐", flush=True)
            _tday = _tday[:_nseg]
            _lm = line1m[:_nseg]
            # 趋势段颜色统一走 theme (hex 仅记在 const 里备查, 线色以 THEME 为准)。
            _tcol = {1: ("#14b8a6", "THEME.envelope.trendUp"),
                     -1: ("#b91c1c", "THEME.envelope.trendDown"),
                     0: ("#1e40af", "THEME.envelope.trendFlat")}
            _runs = []
            _rd, _rs = _tday[0], 0
            for _k in range(1, len(_tday) + 1):
                if _k == len(_tday) or _tday[_k] != _rd:
                    _runs.append((_rs, _k - 1, _rd))
                    if _k < len(_tday):
                        _rd, _rs = _tday[_k], _k
            for _j, (_s, _e, _d) in enumerate(_runs):
                if _j < len(_runs) - 1:
                    _seg = _lm[_s:_e + 2]
                elif _e > _s:
                    _seg = _lm[_s:_e + 1]
                else:
                    continue  # 最后一根单 bar: 前一段的重叠已覆盖, 不单独成段
                if len(_seg) >= 2:
                    _hex, _thm = _tcol[_d]
                    _env_trend_segs.append({"color": _hex, "theme": _thm, "data": _seg})
        _env_opens = _env_df["open"].to_numpy(float)
        _env_highs = _env_df["high"].to_numpy(float)
        _env_lows = _env_df["low"].to_numpy(float)
        _env_edge = _ENV_EDGE_PCT / 100.0
        env_tip = {}
        for _k, _i in enumerate(_env_day_idx):
            _t1m = _env_tstr[_i]
            _up, _mid, _lo = env_upper[_k], env_chanmid[_k], env_lower[_k]
            _pi = _i - 1
            _zt = _zb = None
            if _pi >= 0:
                _up_p, _lo_p = _env_v1(_up_a, _pi), _env_v1(_dn_a, _pi)
                if _up_p is not None and _lo_p is not None:
                    _wp = _up_p - _lo_p
                    _zt = round(_lo_p + _env_edge * _wp, 2)
                    _zb = round(_up_p - _env_edge * _wp, 2)
            _o = round(float(_env_opens[_i]), 2)
            _h = round(float(_env_highs[_i]), 2)
            _l = round(float(_env_lows[_i]), 2)
            _c = round(float(_env_closes[_i]), 2)
            _entries = []
            _closes = []
            for _tr in _env_trades:
                if _tr["obi"] == _i:
                    _entries.append((("翻" if _tr["entry_kind"] == "flip" else "开")
                                     + _env_letter(_tr["dir"]), _tr["dir"], float(_tr["px0"])))
                if _tr["cbi"] == _i and (_tr["close_rs"] != "Envelope翻转"
                                         or _tr is _env_trades[-1]):
                    _closes.append(("平" + _env_letter(_tr["dir"]), _tr["dir"],
                                    float(_tr["px1"]), _tr["close_rs"]))
            if _env_open is not None and _env_open["obi"] == _i:
                _entries.append((("翻" if _env_open["entry_kind"] == "flip" else "开")
                                 + _env_letter(_env_open["dir"]), _env_open["dir"],
                                 float(_env_open["px0"])))
            _open_letters = {_lab[-1] for (_lab, _, _) in _entries if _lab.startswith("开")}
            _closes = [_cl for _cl in _closes if _cl[0][-1] not in _open_letters]
            _segs = []
            for (_lab, _d, _px) in _entries:
                _col = "#1a9e54" if _d == "call" else "#e5484d"
                if _d == "call":
                    _cop = "<" if _c <= _mid else ">"
                    _lseg = "" if _zt is None else (
                        "，<br/>低%.2f ≤ 下区顶%.2f" % (_l, _zt) if _l <= _zt else
                        "，<br/>低%.2f > 下区顶%.2f" % (_l, _zt))
                    _segs.append("<b style='color:%s'>收%.2f %s 中轨%.2f%s →<br/>%s@%.2f</b>"
                                 % (_col, _c, _cop, _mid, _lseg, _lab, _px))
                else:
                    _cop = ">" if _c >= _mid else "<"
                    _hseg = "" if _zb is None else (
                        "，<br/>高%.2f ≥ 上区底%.2f" % (_h, _zb) if _h >= _zb else
                        "，<br/>高%.2f < 上区底%.2f" % (_h, _zb))
                    _segs.append("<b style='color:%s'>收%.2f %s 中轨%.2f%s →<br/>%s@%.2f</b>"
                                 % (_col, _c, _cop, _mid, _hseg, _lab, _px))
            for (_lab, _d, _px, _rs) in _closes:
                _col = "#1a9e54" if _d == "call" else "#e5484d"
                _segs.append("<b style='color:%s'>%s@%.2f %s</b>" % (_col, _lab, _px, _rs))
            env_tip[_t1m] = {"o": _o, "h": _h, "l": _l, "c": _c, "up": _up, "mid": _mid,
                             "lo": _lo, "why": "<br/>".join(_segs)}
        _env_day_closes = [float(_env_closes[i]) for i in _env_day_idx]
        _env_px_hi, _env_px_lo = max(_env_day_closes), min(_env_day_closes)
        env_max = round(_env_px_hi + 1.0, 2)
        env_min = round(_env_px_lo - 1.0, 2)
    else:
        env_upper = env_lower = env_mid = env_chanmid = []
        _env_trend_segs = []
        env_tip = {}
        env_max = env_min = None
    env_min_js = str(env_min) if env_min is not None else "null"
    env_max_js = str(env_max) if env_max is not None else "null"

    # ---- Envelope WER 面板 (dose 0.10; 仅显示, 2026-11-11 用户令) ----
    # 移植自 research/er_leg_backtest_20261110: wer_deep_common.wer_series
    # (WER12 非对称 λ 分子 0.98/分母 0.96) + tv_v67_er_overlay_wdose 突变支
    # 段状态机 (to 0.675/from 0.50/end 0.30/k5/出段迟滞 2 根; dose:
    # eff_to = 0.675 - min(0.10, max(0, 0.50 - avg10)), avg10 = 前 10 根 WER
    # 均值 (不含当根, 窗内任一 NaN 不降))。纯指标展示: 不产生交易、不动
    # 任何一版口径。数据 = 与 Envelope 版同一份 _env_df (warmup+当日 1m),
    # 段不过夜/触发根门 (in_session 且 s>=580) 与引擎逐条同款。
    wer_panel = {"wer": [], "eff": [], "areas": [], "entries": [], "exits": []}
    try:
        if _env_df is not None and len(_env_df) and _env_day_idx:
            _wc = _env_df["close"].to_numpy(float)
            _wn = len(_wc)
            _wdc = np.diff(_wc, prepend=_wc[0])
            _wdc[0] = 0.0
            _wnn = 12
            _wer = np.full(_wn, np.nan)
            if _wn > _wnn:
                from numpy.lib.stride_tricks import sliding_window_view as _swv
                _win = _swv(_wdc, _wnn)
                _wnum = 0.98 ** np.arange(_wnn - 1, -1, -1, dtype=float)
                _wden = 0.96 ** np.arange(_wnn - 1, -1, -1, dtype=float)
                _num = np.abs(_win @ _wnum)
                _den = np.abs(_win) @ _wden
                _vals = np.where(_den > 0, _num / np.where(_den > 0, _den, 1.0), 0.0)
                _wer[_wnn:] = _vals[1:]
            _wsm = (_env_df["t"].dt.hour.values * 60
                    + _env_df["t"].dt.minute.values)
            _wdt = _env_df["t"].dt.strftime("%Y-%m-%d").values
            _wlast = {}
            for _d, _s in zip(_wdt, _wsm):
                _wlast[_d] = int(_s) + 1
            _WTO, _WFROM, _WEND, _WK = 0.675, 0.50, 0.30, 5
            _WCAP, _WR, _WW = 0.10, 0.50, 10

            def _weff(i):
                if i >= _WW:
                    _dw = _wer[i - _WW:i]
                    if np.all(np.isfinite(_dw)):
                        _dr = _WR - float(np.mean(_dw))
                        if _dr > 0.0:
                            return _WTO - min(_WCAP, _dr)
                return _WTO

            _segs = []  # [start, end, dir, date, ended]
            _act = False
            _sdir = 0
            _sdate = None
            _below = 0
            for i in range(_wn):
                _dstr = _wdt[i]
                _s = int(_wsm[i])
                _cm = _s + 1
                _eod = (_wlast[_dstr] - 3) <= _cm <= _wlast[_dstr]
                _ins = (570 <= _s <= 955) and (_cm < 945) and (not _eod)
                if _act and _dstr != _sdate:
                    _act = False
                if not _act:
                    if _ins and _s >= 580 and i >= _WK + 1:
                        _to_i = _weff(i)
                        if (not np.isnan(_wer[i]) and _wer[i] >= _to_i
                                and not np.isnan(_wer[i - 1])
                                and _wer[i - 1] < _to_i):
                            _ww = _wer[i - _WK:i]
                            if (np.all(np.isfinite(_ww))
                                    and float(np.min(_ww)) < _WFROM):
                                _dd = int(np.sign(_wc[i] - _wc[i - _wnn]))
                                if _dd != 0:
                                    _act = True
                                    _sdir = _dd
                                    _sdate = _dstr
                                    _below = 0
                                    _segs.append([i, i, _dd, _dstr, False])
                else:
                    _segs[-1][1] = i
                    if np.isnan(_wer[i]) or _wer[i] < _WEND:
                        _below += 1
                    else:
                        _below = 0
                    if _below >= 2:
                        _act = False
                        _segs[-1][4] = True
                        _below = 0
            _pos = {gi: k for k, gi in enumerate(_env_day_idx)}

            def _wr4(x):
                return round(float(x), 4) if x == x else None

            wer_panel["wer"] = [_wr4(_wer[i]) for i in _env_day_idx]
            wer_panel["eff"] = [round(_weff(i), 3) for i in _env_day_idx]
            for (_s0, _s1, _sd, _sdt2, _ended) in _segs:
                if _sdt2 != day_str or _s0 not in _pos or _s1 not in _pos:
                    continue
                _sgn = 1 if _sd > 0 else -1
                wer_panel["areas"].append([_pos[_s0], _pos[_s1], _sgn])
                wer_panel["entries"].append([_pos[_s0], _wr4(_wer[_s0]), _sgn])
                if _ended:
                    wer_panel["exits"].append([_pos[_s1], _wr4(_wer[_s1])])
            if wer_panel["eff"]:
                print(f"  wer-panel(dose0.10): 当日段 {len(wer_panel['areas'])}, "
                      f"eff {min(wer_panel['eff']):.3f}~{max(wer_panel['eff']):.3f}",
                      flush=True)
    except Exception as _we:
        print(f"  WARN wer-panel 计算失败: {_we}", flush=True)
        wer_panel = {"wer": [], "eff": [], "areas": [], "entries": [], "exits": []}

    # ---- JSON 存档 ----
    os.makedirs(WDIR, exist_ok=True)
    tj = {"day": day_str, "equity": equity,
          "realized": round(robust_realized, 2) if has_live else round(realized, 2),
          "unrealized": round(robust_unreal, 2) if has_live else round(unreal, 2),
          "api_pos": api_pos_txt if has_live else None,
          "orders": [{"o_t": r["o_t"].strftime("%H:%M"),
                      "c_t": r["c_t"].strftime("%H:%M") if r["closed"] else None,
                      "dir": r["odr"], "op": r["op"],
                      "qty": round(r["qty"], 2), "symbol": r["sym"],
                      "o_avg": round(r["o_avg"], 4), "c_avg": round(r["c_avg"], 4) if r["c_avg"] else None,
                      "pnl": round(r["pnl"], 2) if r["pnl"] is not None else None,
                      "ret": r["ret"],
                      "est_pnl": round(r["est_pnl"], 2), "est_ret": r["est_ret"]}
                     for r in actual_rts] if has_live else
                    [{"o_t": sig["t"].iloc[r["obi"]].strftime("%H:%M"),
                      "c_t": sig["t"].iloc[r["cbi"]].strftime("%H:%M") if r["closed"] else None,
                      "dir": r["odr"], "op": _op_name(r.get("ors"), r["odr"]),
                      "qty": r["o_qty"], "symbol": r["o_sym"],
                      "o_avg": r["o_avg"], "c_avg": r["c_avg"],
                      "pnl": r["pnl"], "ret": r["ret"],
                      "est_pnl": round(r["est_pnl"], 2), "est_ret": r["est_ret"]}
                     for r in rts]}
    open(f"{WDIR}/{day_str}.trades.json", "w").write(
        json.dumps(tj, ensure_ascii=False))

    if has_live:
        _s = "+" if robust_day >= 0 else "-"
        # 当日收益率按开盘资金算：优先用Alpaca portfolio history的开盘equity，fallback用 当前-当日盈亏
        # (2026-10-14 用户要求直接从Alpaca拉开盘资金)
        _day_open = day_open_equity or (equity - robust_day if equity else 0)
        _pt = f" ({robust_day / _day_open:+.1%})" if _day_open else ""
        _pos_show = f"实盘:{api_pos_txt}"
        if pos_txt not in ("空仓",) and api_pos_txt == "空仓":
            _pos_show += f"(信号:{pos_txt})"
        elif api_pos_txt != "空仓" and pos_txt != "空仓":
            # 信号和实盘方向都非空但可能不一致, 都显示
            _sig_dr = "call" if "call" in pos_txt else ("put" if "put" in pos_txt else pos_txt)
            _pos_show += f"(信号:{_sig_dr})"
        title_txt = (f"{page_title} {day_str} | 今日 {_s}${abs(robust_day):,.0f}{_pt} "
                     f"| 持仓:{_pos_show} | k3clean L25")
    else:
        # 回放模式: 显示预估收益 (-100口径日内复利, 与QQE版表一致)
        _replay_est = qqe_tot
        _replay_ret = qqe_tot_pct
        _s = "+" if _replay_est >= 0 else "-"
        title_txt = (f"{page_title} {day_str} | 预估 {_s}${abs(_replay_est):,.0f} ({_replay_ret:+.1%}) "
                     f"| 持仓:{pos_txt} | k3clean L25")

    mark_area_js = "[" + ",".join(
        "[{xAxis:'%s',itemStyle:{color:THEME.pnlBgColor(%s)}},{xAxis:'%s'}]" % (
            a[0]["xAxis"], a[0]["pnlPct"], a[1]["xAxis"])
        for a in mark_areas) + "]"
    # 热力背景markArea: 横向色带 (按昨天gen_heat_html_pierce2.py方法)
    heat_markarea = []
    _har = heat_sm if heat_sm is not None else get_heat_arr()
    _hbn = get_heat_bins()
    if _har is not None and _hbn is not None:
        try:
            import numpy as _np2
            _ha2 = _np2.array(_har, dtype=float)
            _hb2 = _np2.array(_hbn, dtype=float)
            _valid2 = _np2.where(_ha2 > 0.5)[0]
            if len(_valid2) > 0:
                _plo2 = float(_hb2[_valid2[0]])
                _phi2 = float(_hb2[_valid2[-1]+1]) if _valid2[-1]+1 < len(_hb2) else float(_hb2[-1])
                _pp = _plo2
                while _pp < _phi2:
                    _zi2 = int(_np2.digitize(_pp + 0.25, _hb2)) - 1
                    _hv2 = float(_ha2[_zi2]) if 0 <= _zi2 < len(_ha2) else 0
                    _hv2 = max(0, min(15, _hv2))
                    if _hv2 > 0.5:
                        heat_markarea.append(
                            '[{"yAxis":' + str(round(_pp,2)) +
                            ',"itemStyle":{"color":"' + heat_color(_hv2) + '"}},{"yAxis":' +
                            str(round(_pp+0.5,2)) + '}]'
                        )
                    _pp += 0.5
        except: pass
    heat_markarea_js = "[" + ",".join(heat_markarea) + "]"
    # 2026-11-01 user's: 实盘日页隐藏热力区 (过去式) — 色带不再并入任何markArea
    heat_markarea_js = "[]"
    # 合并热力背景 (yAxis横向色带)
    if 'heat_markarea_js' in dir() and heat_markarea_js != "[]":
        # 把heat数据加到mark_area_js里
        _ma_inner = mark_area_js[1:-1]  # 去掉[]
        _heat_inner = heat_markarea_js[1:-1]
        if _ma_inner:
            mark_area_js = "[" + _ma_inner + "," + _heat_inner + "]"
        else:
            mark_area_js = heat_markarea_js
    qqe_mark_area_js = mark_area_js
    _vh_ma = vh_markarea_trade_js
    if heat_markarea_js != "[]":
        _vi = _vh_ma[1:-1]
        _hi2 = heat_markarea_js[1:-1]
        vh_mark_area_js = "[" + (_vi + "," + _hi2 if _vi else _hi2) + "]"
    else:
        vh_mark_area_js = _vh_ma
    mark_area_opt = "{silent:true,data:" + vh_mark_area_js + "}"
    ohlc = [[round(float(sig["open"].iloc[i]), 2), round(float(sig["close"].iloc[i]), 2),
             round(float(sig["low"].iloc[i]), 2), round(float(sig["high"].iloc[i]), 2)]
            for i in today_idx]
    # 7区内的3mK线加白边 (in_heat flag from heat_edges尾部n3条)
    _he_inheat = []
    for _e in heat_edges:
        try:
            _he_inheat.append(bool(_e[3]) if len(_e) >= 4 else False)
        except:
            _he_inheat.append(False)
    _n3tmp = len(today_idx)
    if len(_he_inheat) > _n3tmp:
        _he_inheat = _he_inheat[-_n3tmp:]
    while len(_he_inheat) < _n3tmp:
        _he_inheat.insert(0, False)
    _ohlc_parts = []
    for _idx, (o, cc, l, h) in enumerate(ohlc):
        if _idx < len(_he_inheat) and _he_inheat[_idx]:
            # 白边：用fallback，THEME没有就用#ffffff
            _ohlc_parts.append(
                '{value:[' + f"{o},{cc},{l},{h}" + '],itemStyle:{borderColor:"#ffffff",borderWidth:1}}'
            )
        else:
            _ohlc_parts.append(f"[{o},{cc},{l},{h}]")
    ohlc_js = "[" + ",".join(_ohlc_parts) + "]"

    # 9m 聚合 (3根3m合成1根): O=首根开, H=3根最高, L=3根最低, C=末根收, QQE=末根hist
    # 放在每组最后一根3m的位置, 其余位置null
    n3 = len(today_idx)
    ohlc9_js_parts = ["[NaN,NaN,NaN,NaN]"] * n3
    qqe9_js_parts = ["null"] * n3
    m9closes = []
    # 9m放在每组中间那根(k=1,4,7...)，280%宽度以该位置为中心覆盖3根
    for k in range(1, n3, 3):
        g = [today_idx[k - 1], today_idx[k]]
        if k + 1 < n3:
            g.append(today_idx[k + 1])
        # else: 最后一组不足3根，用现有2根
        o9 = round(float(sig["open"].iloc[g[0]]), 2)
        h9 = round(max(float(sig["high"].iloc[j]) for j in g), 2)
        l9 = round(min(float(sig["low"].iloc[j]) for j in g), 2)
        c9 = round(float(sig["close"].iloc[g[-1]]), 2)
        ohlc9_js_parts[k] = f"[{o9},{c9},{l9},{h9}]"
        hv = hist[g[-1]]
        if not np.isnan(hv):
            hv = round(float(hv), 2)
            if hv >= 0:
                qqe9_js_parts[k] = (
                    "{value:%s,itemStyle:THEME.qqe9.up}" % hv)
            else:
                qqe9_js_parts[k] = (
                    "{value:%s,itemStyle:THEME.qqe9.down}" % hv)
        m9closes.append(c9)
    ohlc9_js = "[" + ",".join(ohlc9_js_parts) + "]"
    qqe9_js = "[" + ",".join(qqe9_js_parts) + "]"

    # cumslope（无未来函数；与backfill_strongtrend.py一致）
    cumslope_parts = ["null"] * n3
    if len(m9closes) >= 2:
        _first_c = m9closes[0]
        for _j in range(n3):
            _n_done = (_j + 1) // 3 if _j >= 2 else 0
            if _n_done >= 2:
                _cs = round((m9closes[_n_done - 1] - _first_c) / (_n_done - 1), 3)
                cumslope_parts[_j] = str(_cs)
    cumslope_js = "[" + ",".join(cumslope_parts) + "]"

    # slope5（无未来函数；与backfill_strongtrend.py一致）
    slope5_parts = ["null"] * n3
    for _j in range(n3):
        _n_done = (_j + 1) // 3 if _j >= 2 else 0
        _win = m9closes[max(0, _n_done - 5):_n_done]
        if len(_win) >= 2:
            _ys = np.array(_win, dtype=float)
            _xs = np.arange(len(_win), dtype=float)
            _xm, _ym = _xs.mean(), _ys.mean()
            _den = ((_xs - _xm) ** 2).sum()
            if _den > 0:
                _s5 = round(float(((_xs - _xm) * (_ys - _ym)).sum() / _den), 4)
                slope5_parts[_j] = str(_s5)
    slope5_js = "[" + ",".join(slope5_parts) + "]"
    # Supertrend (TradingView final-band版, 10/hl2/3, RMA ATR; 与build_supertrend_html.py一致)
    # 对sig全部3m bar (warmup+当日) 算, 再取当日部分; ST线按趋势拆两条, 填充用custom四边形
    # ST_TOL 翻转容差 (2026-11-17 user 定版 k=0.15; 与 vh_live.py 的 ST_TOL_K 同判据同 env, 只加慢版):
    # 展示慢版翻转须 close 越前根最终带 >= K*当根ATR, 与出单引擎同判据 (此前展示版漏加, 轨道标记与单子对不上); 快版是另一条参数线, 不加
    try:
        _ST_TOL_K = float(os.environ.get("ST_TOL_K", "0.15") or 0.0)
    except ValueError:
        _ST_TOL_K = 0.15
    # ST_RST_LAM 部分后退 λ (2026-11-17 user 定版 λ=0.70; 与 vh_live.py 同口径同 env, 只加慢版):
    # 前根收盘戳进容差区 (0 < margin <= K*atr[i-1]) 触发的后退重置只退 λ 比例, 真翻转全额。
    try:
        _ST_RST_LAM = float(os.environ.get("ST_RST_LAM", "") or "0.70")
    except ValueError:
        _ST_RST_LAM = 0.70
    _sth = sig["high"].values.astype(float); _stl = sig["low"].values.astype(float); _stc = sig["close"].values.astype(float)
    _sthl2 = (_sth + _stl) / 2
    _sttr = np.empty(len(sig)); _sttr[0] = _sth[0] - _stl[0]
    for _i in range(1, len(sig)):
        _sttr[_i] = max(_sth[_i]-_stl[_i], abs(_sth[_i]-_stc[_i-1]), abs(_stl[_i]-_stc[_i-1]))
    _statr = np.full(len(sig), np.nan)
    if len(sig) >= 10:
        _statr[9] = _sttr[:10].mean()
        for _i in range(10, len(sig)):
            _statr[_i] = (_statr[_i-1]*9 + _sttr[_i]) / 10
    _stbu = _sthl2 + 3*_statr; _stbl = _sthl2 - 3*_statr
    _stfu = np.full(len(sig), np.nan); _stfl = np.full(len(sig), np.nan); _sttrd = np.zeros(len(sig), dtype=int)
    _stfu[9]=_stbu[9]; _stfl[9]=_stbl[9]; _sttrd[9]=1
    for _i in range(10, len(sig)):
        if np.isnan(_statr[_i]): continue
        _tolp = _ST_TOL_K * _statr[_i-1]
        _cup = _stc[_i-1] - _stfu[_i-1]; _cdn = _stfl[_i-1] - _stc[_i-1]
        if _stbu[_i] < _stfu[_i-1] or _cup > _tolp:
            _stfu[_i] = _stbu[_i]
        elif _cup > 0 and _stbu[_i] > _stfu[_i-1]:
            _stfu[_i] = _stfu[_i-1] + _ST_RST_LAM * (_stbu[_i] - _stfu[_i-1])
        elif _cup > 0:
            _stfu[_i] = _stbu[_i]
        else:
            _stfu[_i] = _stfu[_i-1]
        if _stbl[_i] > _stfl[_i-1] or _cdn > _tolp:
            _stfl[_i] = _stbl[_i]
        elif _cdn > 0 and _stbl[_i] < _stfl[_i-1]:
            _stfl[_i] = _stfl[_i-1] - _ST_RST_LAM * (_stfl[_i-1] - _stbl[_i])
        elif _cdn > 0:
            _stfl[_i] = _stbl[_i]
        else:
            _stfl[_i] = _stfl[_i-1]
        _tolb = _ST_TOL_K * _statr[_i]
        if _stc[_i] > _stfu[_i-1] + _tolb: _sttrd[_i]=1
        elif _stc[_i] < _stfl[_i-1] - _tolb: _sttrd[_i]=-1
        else: _sttrd[_i]=_sttrd[_i-1]
    _stval = np.where(_sttrd==1, _stfl, _stfu)
    _stup_parts, _stdn_parts, _stfill_parts = [], [], []
    for _k, _bi in enumerate(today_idx):
        _v = round(float(_stval[_bi]), 2) if not np.isnan(_stval[_bi]) else None
        _cc = round(float(sig["close"].iloc[_bi]), 2)
        if _sttrd[_bi]==1:
            _stup_parts.append(str(_v) if _v is not None else "null"); _stdn_parts.append("null")
        else:
            _stup_parts.append("null"); _stdn_parts.append(str(_v) if _v is not None else "null")
        _nk = _k+1 if _k+1 < len(today_idx) else _k
        _nbi = today_idx[_nk]
        _nv = round(float(_stval[_nbi]), 2) if not np.isnan(_stval[_nbi]) else None
        _ncc = round(float(sig["close"].iloc[_nbi]), 2)
        _stfill_parts.append(f'["{times[_k]}",{_v if _v is not None else "null"},{_cc},"{times[_nk]}",{_nv if _nv is not None else "null"},{_ncc},{int(_sttrd[_bi])}]')
    stUp_js = "[" + ",".join(_stup_parts) + "]"
    stDn_js = "[" + ",".join(_stdn_parts) + "]"
    stFill_js = "[" + ",".join(_stfill_parts) + "]"
    # ST翻转标记 (仅盘中翻转; Buy绿点+下方标签, Sell红点+上方标签)
    def _stmk(_k, _lbl):
        _col = '#089981' if _lbl=='Buy' else '#f23645'
        _pos = 'bottom' if _lbl=='Buy' else 'top'
        _sv = round(float(_stval[today_idx[_k]]), 2)
        return '{"value":["' + times[_k] + '",' + str(_sv) + '],"itemStyle":{"color":"' + _col + '"},"label":{"formatter":"' + _lbl + '","position":"' + _pos + '","backgroundColor":"' + _col + '","color":"#fff","borderRadius":4,"padding":[3,6],"fontSize":12}}'
    _stflip_items = [_stmk(0, 'Buy' if _sttrd[today_idx[0]]==1 else 'Sell')]  # 开盘标记 (当日开盘趋势状态)
    for _k in range(1, len(today_idx)):
        if _sttrd[today_idx[_k]] != _sttrd[today_idx[_k-1]]:
            _stflip_items.append(_stmk(_k, 'Buy' if _sttrd[today_idx[_k]]==1 else 'Sell'))
    stFlips_js = "[" + ",".join(_stflip_items) + "]"
    # 快版ST (与慢版同一ATR序列, 仅乘数2 vs 3; 与双确认回测快版一致)
    _stbu_f = _sthl2 + 2*_statr; _stbl_f = _sthl2 - 2*_statr
    _stfu_f = np.full(len(sig), np.nan); _stfl_f = np.full(len(sig), np.nan); _sttrd_f = np.zeros(len(sig), dtype=int)
    _stfu_f[9]=_stbu_f[9]; _stfl_f[9]=_stbl_f[9]; _sttrd_f[9]=1
    for _i in range(10, len(sig)):
        if np.isnan(_statr[_i]): continue
        _stfu_f[_i] = _stbu_f[_i] if (_stbu_f[_i] < _stfu_f[_i-1] or _stc[_i-1] > _stfu_f[_i-1]) else _stfu_f[_i-1]
        _stfl_f[_i] = _stbl_f[_i] if (_stbl_f[_i] > _stfl_f[_i-1] or _stc[_i-1] < _stfl_f[_i-1]) else _stfl_f[_i-1]
        if _stc[_i] > _stfu_f[_i-1]: _sttrd_f[_i]=1
        elif _stc[_i] < _stfl_f[_i-1]: _sttrd_f[_i]=-1
        else: _sttrd_f[_i]=_sttrd_f[_i-1]
    _stval_f = np.where(_sttrd_f==1, _stfl_f, _stfu_f)
    _stfup_parts, _stfdn_parts = [], []
    for _k, _bi in enumerate(today_idx):
        _vf = round(float(_stval_f[_bi]), 2) if not np.isnan(_stval_f[_bi]) else None
        if _sttrd_f[_bi]==1:
            _stfup_parts.append(str(_vf) if _vf is not None else "null"); _stfdn_parts.append("null")
        else:
            _stfup_parts.append("null"); _stfdn_parts.append(str(_vf) if _vf is not None else "null")
    stFastUp_js = "[" + ",".join(_stfup_parts) + "]"
    stFastDn_js = "[" + ",".join(_stfdn_parts) + "]"
    # 快版ST翻转标记 (与慢版同款；快Buy深蓝+下方标签，快Sell橘红+上方标签；标签不带“快”字、比慢版小一号)
    def _stmk_f(_k, _lbl):
        _col = '#1e40af' if _lbl=='Buy' else '#ff4500'
        _pos = 'bottom' if _lbl=='Buy' else 'top'
        _sv = round(float(_stval_f[today_idx[_k]]), 2)
        return '{"value":["' + times[_k] + '",' + str(_sv) + '],"itemStyle":{"color":"' + _col + '"},"label":{"formatter":"' + _lbl + '","position":"' + _pos + '","backgroundColor":"' + _col + '","color":"#fff","borderRadius":4,"padding":[2,4],"fontSize":10}}'
    _stflip_fast_items = [_stmk_f(0, 'Buy' if _sttrd_f[today_idx[0]]==1 else 'Sell')]  # 开盘标记 (当日快版开盘趋势状态)
    for _k in range(1, len(today_idx)):
        if _sttrd_f[today_idx[_k]] != _sttrd_f[today_idx[_k-1]]:
            _stflip_fast_items.append(_stmk_f(_k, 'Buy' if _sttrd_f[today_idx[_k]]==1 else 'Sell'))
    stFastFlips_js = "[" + ",".join(_stflip_fast_items) + "]"
    # heat_up/heat_lo: 7区上下缘线 (从heat_edges取今日部分)
    _n3h2 = len(today_idx)
    _he_tail = heat_edges[-_n3h2:] if len(heat_edges) >= _n3h2 else heat_edges
    try:
        for _dbg_i in range(min(3, len(_he_tail))):
            _de = _he_tail[_dbg_i]
            print(f"[DEBUG] tail[{_dbg_i}] tstr={_de[0]}, up={_de[1]}, lo={_de[2]}")
    except: pass
    # forward fill: 区内bar用区边缘，区外bar延续上一个值
    _hu2, _hl2 = None, None
    _up_list, _lo_list = [], []
    for _e in _he_tail:
        try:
            if len(_e) >= 3 and _e[1] is not None:
                _hu2 = round(float(_e[1]), 2); _hl2 = round(float(_e[2]), 2)
        except: pass
        if _hu2 is not None:
            _up_list.append(str(_hu2)); _lo_list.append(str(_hl2))
        else:
            _up_list.append("null"); _lo_list.append("null")
    # 补齐到_n3h2长度
    while len(_up_list) < _n3h2:
        _up_list.insert(0, "null"); _lo_list.insert(0, "null")
    heat_up_js = "[" + ",".join(_up_list) + "]"
    heat_lo_js = "[" + ",".join(_lo_list) + "]"
    # 2026-11-01 user's: 实盘日页隐藏7区上下缘线 (过去式) — 边缘线数据置空
    heat_up_js = "[]"
    heat_lo_js = "[]"
    print(f"[DEBUG] heat_up non-null={sum(1 for x in _up_list if x != 'null')}")
    # 触发线: 区内15% (从_up_list/_lo_list直接算)
    _trig_up_list, _trig_lo_list = [], []
    for _uu, _ll in zip(_up_list, _lo_list):
        try:
            if _uu != "null" and _ll != "null":
                _zhu = float(_uu); _zhl = float(_ll)
                _zhh = _zhu - _zhl
                _trig_up_list.append(str(round(_zhu - 0.15*_zhh, 2)))
                _trig_lo_list.append(str(round(_zhl + 0.15*_zhh, 2)))
            else:
                _trig_up_list.append("null"); _trig_lo_list.append("null")
        except:
            _trig_up_list.append("null"); _trig_lo_list.append("null")
    trig_up_js = "[" + ",".join(_trig_up_list) + "]"
    trig_lo_js = "[" + ",".join(_trig_lo_list) + "]"
    # 2026-11-01 user's: 触发线由7区边缘算出, 与边缘线同属过去式, 一并隐藏
    trig_up_js = "[]"
    trig_lo_js = "[]"
    # heatmap数据: 横向色带 [时间索引, 价格, 热度值]
    # 用最终热度分布，在价格轴上画横向色带
    heatmap_data = []
    if heat_arr is not None and heat_bins is not None:
        try:
            import numpy as _np
            _ha = _np.array(heat_arr, dtype=float)
            _hb = _np.array(heat_bins, dtype=float)
            # 只取有热度的价格带
            for _bi in range(len(_ha)):
                _bv = float(_ha[_bi])
                if _bv > 0.5 and _bi < len(_hb)-1:
                    _price = float((_hb[_bi] + _hb[_bi+1]) / 2)
                    # 横向：在所有时间点都画这个价格带的热度
                    for _ti in range(0, n3, 5):  # 每5根取样，减少点数
                        heatmap_data.append([_ti, round(_price, 2), round(_bv, 2)])
        except: pass
    heatmap_js = "[" + ",".join([f"[{d[0]},{d[1]},{d[2]}]" for d in heatmap_data]) + "]"
    # 2026-11-01 user's: heat_bg热力格子与热力区同属过去式, 一并隐藏
    heatmap_js = "[]"

    def js_arr(a):
        return "[" + ",".join("null" if v is None else str(v) for v in a) + "]"

    # 2026-10-19 根源统一: 实盘dashboard改用backfill模板2026-04-02.html, 不再内联模板
    # (tooltip半透明/cumslope/slope5/legend等与backfill永远一致)
    # 2026-10-20: 模板改相对脚本目录找 (仓库根目录有一份; 旧硬编码本机wave-deploy路径在Actions上不存在)
    _tpl_cands = [os.path.join(os.path.dirname(os.path.abspath(__file__)), '2026-04-02.html'),
                  '/home/hatch/workspace/wave-deploy/2026-04-02.html']
    _tpl = next((p for p in _tpl_cands if os.path.exists(p)), _tpl_cands[0])
    html = open(_tpl).read()
    # 2026-11-11 布局: 主图#c原95vh手机上纵向过长, 压到55vh (约原值58%); 只改输出不改模板源文件
    # 2026-11-12 布局: 55vh 只剩 Envelope 两版与初始态; 实盘/ST VH/QQE/CE 四版由 switchVersion3 里
    # _fitChartH 按视口动态撑满 (innerHeight - #c文档偏移), 用户反馈四版在手机上被压扁
    html = html.replace('#c{width:100%;height:95vh}', '#c{width:100%;height:55vh}', 1)
    html = re.sub(r'<title>.*?</title>', f'<title>{title_txt}</title>', html, count=1)
    html = re.sub(r"title:\{text:'.*?', left:'center'",
                  f"title:{{text:'{title_txt}', left:'center'", html, count=1)
    if rtag:  # 实盘自动刷新; 回放页不加
        html = html.replace('<meta charset="utf-8">', '<meta charset="utf-8">' + rtag, 1)

    def pad130(a, fill=None):
        return a + [fill] * (130 - len(a))
    vals130 = pad130(vals)
    cols130 = pad130(cols, "gray")
    d80130 = pad130(d80)
    fl130 = pad130(fl)
    hb130 = pad130(hb)

    def rep_const(name, new_val):
        nonlocal html
        pat = r'const %s\s*=\s*.*?;\n' % re.escape(name)
        html, nsub = re.subn(pat, f'const {name} = {new_val};\n', html, count=1, flags=re.S)
        if nsub == 0:
            print(f"  WARN: const {name} not found!")

    rep_const('times', json.dumps(times))
    rep_const('vals', js_arr(vals130))
    rep_const('colors', json.dumps(cols130))
    rep_const('decayReal', js_arr(d80130))
    rep_const('flipBlue', js_arr(fl130))
    rep_const('hubuThr', js_arr(hb130))
    rep_const('pins', json.dumps(vh_qpins, ensure_ascii=False))
    rep_const('spins', json.dumps(vh_spins, ensure_ascii=False))
    rep_const('line1m', json.dumps(line1m))
    rep_const('filldots', json.dumps(filldots, ensure_ascii=False))
    rep_const('barInfo', json.dumps(bar_info, ensure_ascii=False))
    rep_const('stUp', stUp_js)
    rep_const('stDn', stDn_js)
    rep_const('stFill', stFill_js)
    rep_const('stFlips', stFlips_js)
    rep_const('stFastUp', stFastUp_js)
    rep_const('stFastDn', stFastDn_js)
    rep_const('stFastFlips', stFastFlips_js)

    html, n1 = re.subn(r"\{name:'3mK线', type:'candlestick', data:\[.*?\], xAxisIndex",
                       "{name:'3mK线', type:'candlestick', data:" + ohlc_js + ", xAxisIndex",
                       html, count=1, flags=re.S)
    html, n2 = re.subn(r"\{name:'9mK线', type:'candlestick', data:\[.*?\], xAxisIndex",
                       "{name:'9mK线', type:'candlestick', data:" + ohlc9_js + ", xAxisIndex",
                       html, count=1, flags=re.S)
    html, n3s = re.subn(r"\{name:'9m QQE', type:'bar', data:\[.*?\], xAxisIndex",
                        "{name:'9m QQE', type:'bar', data:" + qqe9_js + ", xAxisIndex",
                        html, count=1, flags=re.S)
    html, n4 = re.subn(r"\{name:'cumslope', type:'line', data:\[.*?\], xAxisIndex",
                       "{name:'cumslope', type:'line', data:" + cumslope_js + ", xAxisIndex",
                       html, count=1, flags=re.S)
    html, n5 = re.subn(r"\{name:'slope5', type:'line', data:\[.*?\], xAxisIndex",
                       "{name:'slope5', type:'line', data:" + slope5_js + ", xAxisIndex",
                       html, count=1, flags=re.S)
    html, n_hu = re.subn(r"\{name:'heat_up', type:'line', data:\[.*?\], xAxisIndex",
                       "{name:'heat_up', type:'line', data:" + heat_up_js + ", xAxisIndex",
                       html, count=1, flags=re.S)
    print(f"[DEBUG] heat_up替换次数={n_hu}, 数据长度={len(heat_up_js)}")
    html, n_hl = re.subn(r"\{name:'heat_lo', type:'line', data:\[.*?\], xAxisIndex",
                       "{name:'heat_lo', type:'line', data:" + heat_lo_js + ", xAxisIndex",
                       html, count=1, flags=re.S)
    html, n_hm = re.subn(r"\\{name:'heat_bg', type:'heatmap', data:\\[.*?\\], xAxisIndex",
                       "{name:'heat_bg', type:'heatmap', data:" + heatmap_js + ", xAxisIndex",
                       html, count=1, flags=re.S)
    html, n_tu = re.subn(r"\\{name:'trig_up', type:'line', data:\\[.*?\\], xAxisIndex",
                       "{name:'trig_up', type:'line', data:" + trig_up_js + ", xAxisIndex",
                       html, count=1, flags=re.S)
    html, n_tl = re.subn(r"\\{name:'trig_lo', type:'line', data:\\[.*?\\], xAxisIndex",
                       "{name:'trig_lo', type:'line', data:" + trig_lo_js + ", xAxisIndex",
                       html, count=1, flags=re.S)
    html, n_hu = re.subn(r"\\{name:'heat_up', type:'line', data:\\[.*?\\], xAxisIndex",
                       "{name:'heat_up', type:'line', data:" + heat_up_js + ", xAxisIndex",
                       html, count=1, flags=re.S)
    html, n_hl = re.subn(r"\\{name:'heat_lo', type:'line', data:\\[.*?\\], xAxisIndex",
                       "{name:'heat_lo', type:'line', data:" + heat_lo_js + ", xAxisIndex",
                       html, count=1, flags=re.S)

    def _replace_balanced(html, start_pat, new_text):
        m = re.search(start_pat, html)
        if not m:
            return html, 0
        s = m.start()
        j = html.find('{', s)
        depth = 0
        k = j
        while k < len(html):
            if html[k] == '{':
                depth += 1
            elif html[k] == '}':
                depth -= 1
                if depth == 0:
                    break
            k += 1
        end = k + 1
        return html[:j] + new_text + html[end:], 1

    html, n6 = _replace_balanced(html, r"markArea:\{", mark_area_opt)
    scat_series = ("{name:'每单盈亏', type:'scatter', xAxisIndex:0, yAxisIndex:0, symbolSize:1, itemStyle:{opacity:0}," +
                   "label:{show:true, opacity:1, fontSize:11, fontWeight:'bold', position:'top', distance:1," +
                   "formatter:function(p){return p.data.lbl;}}, data:" + vh_scat_js + "}")
    html, n7 = _replace_balanced(html, r"\{name:'每单盈亏'", scat_series)
    html, n8 = re.subn(r'<div style="max-width:1150px.*?</div>\s*<script>',
                       table_html + '\n<script>',
                       html, count=1, flags=re.S)
    html = re.sub(r"(yAxis:\[\{type:'value',gridIndex:0,scale:true,max:)[\d.]+",
                  r"\g<1>" + str(y_max_js), html, count=1)

    if 0 in (n1, n2, n3s, n4, n5, n6, n7, n8):
        print(f"  WARN subs: 3mK={n1} 9mK={n2} 9mQQE={n3s} cumslope={n4} slope5={n5} markArea={n6} scat={n7} table={n8}")
    if WAVE_DATE:
        _out_name = WAVE_OUT_NAME_TMPL or f"{WAVE_DATE}.html"
    else:
        _out_name = WAVE_OUT_NAME_TMPL or f"wave-today-{day_str.replace('-', '')}.html"
    out = f"{WDIR}/{_out_name}"
    os.makedirs(WDIR, exist_ok=True)
    # ---- Envelope 版 = WER 冠军 / F1+WER 双版同算, 页内按钮切换
    # (2026-11-20 user 令: 默认显示冠军版, user 在浏览器里自己切;
    # 今晨先换 F1+WER 单版 (09826da57)、中午改双版并存) ----
    # 两套引擎/配方都保留、每轮都算 (同一暖帧, 见下):
    #   champion: envwer_recipe.day_payload_warm —— WER 终栈⑪ 冠军版
    #     (envwer/ vendored 管线与 brk1112 compute_2weeks.run_day
    #     冠军臂逐字同源: fork tv_v67_er_overlay_wcent2f3sr + er_cfg
    #     终栈⑪ (CHAMP_CFG + RAMP_OVER + CE RF/F2/F3/shield),
    #     SL_K=0.7875);
    #   f1: envqsf_recipe.day_payload_warm_qsf —— F1+WER+QQE转向
    #     (2026-11-20 晚 user 令主线化, 取代纯 F1+WER 的 envf1_recipe;
    #     envwer/envqsf_engine.py = 研究 qqesfflip fork 逐字 vendored):
    #     F1 底盘 (WER 冠军本体 + 止损反手 p=0.25, 反手止损 2.5xATR,
    #     本体 SL_K=0.7875) + ER 腿 CFG_A + QQESF 转向 (QQE 衰减翻仓
    #     砍 sf 仓反向接棒 qf 仓, 带 qfFlip 箭头); 当天暖帧原生算,
    #     不再依赖预计算 JSON。envf1_recipe 保留备查、不再调用。
    # 装配/展示同走 envwer_page (mode 参数分表头文案与 sf 标签;
    # 两配方产出 schema 同形)。
    # 喂帧 = 暖帧 (2026-11-17 16:14 user 令改, 对齐定版整帧连续口径):
    # _env_df 本含目标日前 10 日历日 + 当日 (回放=缓存/API 同款), 整帧
    # 喂配方 (指标/WER/CE 全帧算、simulate 整帧演化、逐笔只取当日)。
    # 冷版单日跑曾因 ADX 前 31 根 NaN 被 ind_ok 门放大成 10:01 前
    # 全场封锁、且整上午指标偏离定版 —— 已弃用 (冷版函数保留在
    # envwer_recipe 作对照)。暖版复现闸 (2026-11-17): 2026-11-13 /
    # 11-16 两天逐笔与定版整帧 run (merge4b 原班机器扩窗复跑, 其
    # <=11-10 段与 merge4b.csv 73,150 笔逐笔全等) 逐项全等。
    # 呈现沿用整合版面孔: WER 段内 1m 曲线天蓝染色 (无背景带)、圈锚
    # 信号根收盘、平仓圈灰、日收益连乘、WER 面板与主图同段同源。
    # 共用常量 (轨道/WER 面板/slope-dev/QQE1m 面板) 两版只发一份:
    # 两版同帧同指标, 2026-11-19 A/B 实测逐字节同值; 构建时逐项
    # 比对、不同打 WARN (页面取冠军版那份)。成交类常量 (圈/单子
    # 表/acts/tip/标题/段染色) 两版各一份进 ENVELOPE_SETS, 页内
    # switchEnvMode 整套换 (见下方 _sw 注入段; 默认冠军)。
    # 失败口径: 任一版计算失败只标该版失败 (标题 + 单子表红字)、
    # 不回退旧口径、不拖累另一版, 构建日志打 ERROR。缘起:
    # 2026-11-17 上午 envint warm 调用混入 main 致页面静默回退
    # 2026-11-09 老引擎一整天 —— 失败必须可见。
    # (上方旧引擎块保留: 仍供 _env_df/_env_trend_segs/env_min/max 等
    # 页面脚手架, 其单子不再进 Envelope 节。)
    _ENV_MODES = (("champion", "WER终栈⑪", "envelope-wer"),
                  ("f1", "反手+QQE", "envelope-f1"),
                  ("imb", "WER失衡版", "envelope-imb"))
    _env_sets = {}    # mode -> build_consts 常量 dict (该版计算成功时)
    _env_fail = {}    # mode -> 错误文本 (该版计算失败时)
    _env_int_ok = False
    _env_int_consts = None
    _env_wer_failed = False
    slpdev_panel = {"slope": [], "dev": [],
                    "th": {"up": 0.05, "dn": 0.07, "dev": 0.079}}
    env_qqe1m = {"hist": [], "col": []}
    import sys as _sys
    _idf = None
    try:
        if _env_df is None or not len(_env_df):
            raise RuntimeError("envelope 1m 数据为空 (_env_df 空)")
        _idf = _env_df.copy()
        if getattr(_idf["t"].dt, "tz", None) is not None:
            _idf["t"] = _idf["t"].dt.tz_convert(
                "America/New_York").dt.tz_localize(None)
        _im = _idf["t"].dt.hour * 60 + _idf["t"].dt.minute
        _idf = _idf[(_im >= 570) & (_im <= 959)].sort_values(
            "t").drop_duplicates("t", keep="last").reset_index(drop=True)
    except Exception as _fe:
        _frame_err = f"{type(_fe).__name__}: {_fe}"
        for _m, _mn, _lt in _ENV_MODES:
            _env_fail[_m] = _frame_err
            print(f"  ERROR {_lt} 计算失败, 该节标注错误 (不回退旧口径): "
                  f"{_frame_err}", flush=True)
            print(f"  ERROR {_lt} 计算失败: {_frame_err}",
                  file=_sys.stderr, flush=True)
    if _idf is not None:
        _envwer_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "envwer")
        if _envwer_dir not in _sys.path:
            _sys.path.insert(0, _envwer_dir)
        import envwer_recipe as _ewr
        import envqsf_recipe as _eqsf
        import envwer_page as _ewp
        # ---- 1m QQE 面板数据 (2026-11-19 user 令): 与 Envelope 同一
        # 暖帧 _idf 全帧算 QQE MOD (qqe_mod.py 正本), 只取当日、按
        # 固定 390 槽 (09:30-15:59) 对齐; 颜色 = QQE MOD 状态
        # (up 真蓝 #2f80ed / down 真红 #e5484d / neutral 灰 / 其余
        # |hist|<=3 浅灰, 与 theme qqe3 同色)。失败只丢面板。
        try:
            _qq = qqe_mod(_idf["close"].to_numpy(float))
            _qh = _qq["hist"]
            _qmap = {}
            for _qi in np.where(
                    (_idf["t"].dt.strftime("%Y-%m-%d")
                     == day_str).to_numpy())[0]:
                _qmap[_idf["t"].iloc[_qi].strftime("%H:%M")] = _qi
            _hist390, _col390 = [], []
            for _tm in _env_times1m:
                _qi = _qmap.get(_tm)
                if _qi is None or not np.isfinite(_qh[_qi]):
                    _hist390.append(None)
                    _col390.append(None)
                    continue
                _hist390.append(round(float(_qh[_qi]), 2))
                _col390.append("#2f80ed" if _qq["up"][_qi] else
                               "#e5484d" if _qq["down"][_qi] else
                               "#b0b0b0" if _qq["neutral"][_qi] else
                               "#e0e0e0")
            env_qqe1m = {"hist": _hist390, "col": _col390}
        except Exception as _qe:
            print(f"  WARN envelope qqe1m 面板计算失败 (面板不显示): "
                  f"{_qe}", flush=True)
        # qsf 载荷备忘 (user 2026-11-20 19:41 令): 冠军版也要画 QQE
        # 转向信号 (只在 1m QQE 面板展示、不参与冠军策略), 与 f1 版
        # 共用同一次 day_payload_warm_qsf 调用 (同暖帧、同入参、
        # 同结果), 不重复跑引擎。
        _qsf_memo = []

        def _qsf_payload():
            if not _qsf_memo:
                _qsf_memo.append(_eqsf.day_payload_warm_qsf(
                    _idf[["t", "open", "high", "low", "close"]], day_str))
            return _qsf_memo[0]

        for _mode, _mname, _logtag in _ENV_MODES:
            try:
                if _mode == "f1":
                    _payload, _perr = _qsf_payload()
                elif _mode == "imb":
                    _payload, _perr = _eqsf.day_payload_warm_imb(
                        _idf[["t", "open", "high", "low", "close"]], day_str)
                else:
                    _payload, _perr = _eqsf.day_payload_warm_champ(
                        _idf[["t", "open", "high", "low", "close"]], day_str)
                if _payload is None:
                    raise RuntimeError(f"载荷失败: {_perr}")
                _sday, _day = _payload["sday"], _payload["day"]
                _runs = _ewp.norm_runs(_sday, _day)
                _itrades = _ewp.build_trades(
                    _sday, _runs,
                    mode="f1" if _mode in ("f1", "imb") else _mode)
                _roi_sum = sum(t["roi"] for t in _itrades) * 100
                _exp = _sday["total_slope"] + _sday["total_stack"]
                assert abs(_roi_sum - _exp) < 0.15, "ROI 合计与管线对不上, 停"
                _tot, _tot_pct = _ewp.settle(_itrades)
                _cmode = "qsf" if _mode == "f1" else _mode
                _qfa = _payload.get("qf_arrows")
                if _mode == "champion":
                    # QQE 转向箭头同画价格图 (user 2026-11-20 20:31
                    # 令): 冠军版借 qsf 备忘载荷的 qf_arrows 喂
                    # build_consts, 只产主图 qfFlip 箭头 (与 f1 版
                    # 同位同偏移); 冠军无 qf 成交, 价格图不会多出
                    # qf 圈 (圈仍只在 QQE 面板), 成交/标题/INNER
                    # 一律不动, 纯展示。qsf 载荷失败时只丢箭头,
                    # 不拖累冠军版。
                    try:
                        _qpl0, _qerr0 = _qsf_payload()
                        if _qpl0 is not None and _qpl0.get("qf_arrows"):
                            _qfa = _qpl0["qf_arrows"]
                            print(f"  envelope-qf展示(冠军版价格图箭头): "
                                  f"{len(_qfa)} 个确认根", flush=True)
                        elif _qpl0 is None:
                            print(f"  WARN envelope 冠军版 qf 箭头缺失 "
                                  f"(qsf 载荷失败, 仅丢箭头): {_qerr0}",
                                  flush=True)
                    except Exception as _qe0:
                        print(f"  WARN envelope 冠军版 qf 箭头计算失败 "
                              f"(仅丢箭头): {_qe0}", flush=True)
                _consts, _ititle, _ititlee = _ewp.build_consts(
                    vals130, times, line1m, _env_times1m, gap_txt, _env_gap_e,
                    _sday, _day, _itrades, _runs, _tot, _tot_pct, day_str,
                    mode=_cmode, qf_arrows=_qfa)
                _env_sets[_mode] = _consts
                print(f"  {_logtag}({_mname},暖帧{_payload.get('warm_days', 0)}日): "
                      f"{len(_itrades)} 单, tot=${_tot:,.0f} ({_tot_pct:+.1%})",
                      flush=True)
                if _mode == "champion":
                    # QQE 转向信号只展示 (user 2026-11-20 19:41 令):
                    # 冠军版成交/标题/INNER/价格图一律不动, 只把 qf
                    # 确认根槽位喂 ENVELOPE_QF1M, 页内 1m QQE 面板在
                    # 冠军版下同画圈+箭头 (与 f1 版同位, 经 qsf 载荷
                    # 自身 day 时间轴换算、与 f1 版逐项同值)。qsf
                    # 载荷失败时只丢展示 (面板无圈), 不拖累冠军版。
                    try:
                        _qpl, _qerr = _qsf_payload()
                        if _qpl is not None and _qpl.get("qf_arrows"):
                            _consts["ENVELOPE_QF1M"] = _ewp.qf1m_slots(
                                _qpl["qf_arrows"], _qpl["day"]["times"])
                            print(f"  envelope-qf展示(冠军版QQE面板圈): "
                                  f"{len(_consts['ENVELOPE_QF1M'])} 个确认根",
                                  flush=True)
                        elif _qpl is None:
                            print(f"  WARN envelope 冠军版 qf 展示缺失 "
                                  f"(qsf 载荷失败, 仅丢面板标记): {_qerr}",
                                  flush=True)
                    except Exception as _qe3:
                        print(f"  WARN envelope 冠军版 qf 展示计算失败 "
                              f"(仅丢面板标记): {_qe3}", flush=True)
            except Exception as _ee:
                _env_fail[_mode] = f"{type(_ee).__name__}: {_ee}"
                print(f"  ERROR {_logtag} 计算失败, 该节标注错误 (不回退旧口径): "
                      f"{_env_fail[_mode]}", flush=True)
                print(f"  ERROR {_logtag} 计算失败: {_env_fail[_mode]}",
                      file=_sys.stderr, flush=True)

    # ---- 共用常量 (轨道/WER 面板/slope-dev): 取成功版 (冠军优先);
    # 两版都成功时逐项比对, 不一致打 WARN (同帧同指标应同值) ----
    _shared_mode = ("champion" if "champion" in _env_sets else
                    ("f1" if "f1" in _env_sets else None))
    _env_wer_failed = _shared_mode is None
    if _shared_mode is not None:
        _env_int_ok = True
        _env_int_consts = _env_sets[_shared_mode]
        _C = _env_int_consts
        env_upper = _C["ENVELOPE_UPPER_1M"]
        env_lower = _C["ENVELOPE_LOWER_1M"]
        env_chanmid = _C["ENVELOPE_CHANMID_1M"]
        env_mid = _C["ENVELOPE_MID_1M"]
        wer_panel = _C["ENVELOPE_WER"]
        slpdev_panel = _C["ENVELOPE_SLPDEV"]
        if "champion" in _env_sets and "f1" in _env_sets:
            _Co = _env_sets["f1" if _shared_mode == "champion" else "champion"]
            for _k in ("ENVELOPE_UPPER_1M", "ENVELOPE_LOWER_1M",
                       "ENVELOPE_CHANMID_1M", "ENVELOPE_MID_1M",
                       "ENVELOPE_WER", "ENVELOPE_SLPDEV"):
                if _C[_k] != _Co[_k]:
                    print(f"  WARN envelope 双版共用常量 {_k} 不一致, "
                          f"页面共用面板取{_shared_mode}版", flush=True)
    else:
        env_upper = env_lower = env_chanmid = env_mid = []
        wer_panel = {"wer": [], "eff": [], "areas": [],
                     "entries": [], "exits": []}
    # ---- 每版成交类常量: 成功用真常量; 失败用该版失败标注常量 ----
    import html as _html_mod

    def _env_fail_consts(_mname, _err):
        _err_txt = _html_mod.escape(_err)
        return {
            "ENVELOPE_PINS": [], "ENVELOPE_SPINS": [],
            "ENVELOPE_SCAT_1M": [], "ENVELOPE_MARKAREA_1M": "[]",
            "ENVELOPE_INNER": (
                "<div style=\"font-size:14px;font-weight:bold;margin-bottom:6px\">"
                f"Envelope版信号单（{_mname} · Gamma理论预估，-100口径）</div>"
                "<div style=\"font-size:13px;color:#e5484d;font-weight:bold\">"
                f"⚠ Envelope版计算失败（{_mname}）：{_err_txt}<br/>"
                "本节暂无数据（未回退旧口径），请看构建日志 ERROR 行。</div>"),
            "ENVELOPE_ACTS": {}, "ENVELOPE_TIP": {},
            "ENVELOPE_TITLE": f"{day_str} (Envelope版) | 计算失败（{_mname}）",
            "ENVELOPE_TITLEE": f"{day_str} ({{envelope|Envelope}}版) | 计算失败",
            "ENVELOPE_SLOPE_DYE": [], "ENVELOPE_QF1M": [],
            "ENVELOPE_WER": {"wer": [], "eff": [], "areas": [],
                             "entries": [], "exits": []}}

    _env_set_consts = {}
    for _mode, _mname, _logtag in _ENV_MODES:
        _env_set_consts[_mode] = _env_sets.get(_mode) or _env_fail_consts(
            _mname, _env_fail.get(_mode, "未知错误"))
    # TITLES3 的 envelope 初始标题 = 默认版 (冠军); 页内切换时 JS 同步换
    env_title_txt = _env_set_consts["champion"]["ENVELOPE_TITLE"]
    env_title_echart = _env_set_consts["champion"]["ENVELOPE_TITLEE"]
    # ---- hama 版 (SSL x HAMA x QQE, 1m 策略; 2026-11-23 user 规格
    # 修正: 不是页尾独立节, 而是与 ST VX 等平级的版本按钮。页骨架照
    # ST VX (标题/逐笔表/按钮切换), 主图换 1m 蜡烛 + 1m 轴 (390 槽,
    # 参考 Envelope 版 1m 化), 去 Supertrend, 加 HAMA/SSL 线 (画法照
    # 研究 build_html.py 的 chanSeries/pushHama), QQE 面板换 hama
    # 自家 1m QQE。数据 = envwer/hama_recipe.day_payload (研究定版
    # sim 移植, 30% 杠杆真 0DTE); 标题收益率 = 逐笔 ROI 相加
    # (hama 口径)。计算失败回退空数据集 + 标题标注, 页面不挂。----
    hama_pins, hama_spins = [], []
    hama_scat_js = "[]"
    hama_gb_js = "[]"
    hama_mark_area_js = "[]"
    hama_acts = {}
    hama_tip = {}
    hama_times1m, hama_ohlc, hama_qqe = [], [], []
    hama_line_segs, hama_ssl_fills = [], []
    hama_inner = ("<div style=\"font-size:14px;font-weight:bold;margin-bottom:6px\">"
                  "Hama版信号单（30%杠杆真0DTE口径）</div>"
                  "<div style=\"font-size:13px;color:#e5484d;font-weight:bold\">"
                  "⚠ Hama版计算失败，本节暂无数据。</div>")
    hama_title_txt = f"{day_str} (Hama版) | 计算失败"
    hama_title_echart = f"{day_str} ({{hama|hama}}版) | 计算失败"
    try:
        import sys as _sys_h
        _hama_dir2 = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "envwer")
        if _hama_dir2 not in _sys_h.path:
            _sys_h.path.insert(0, _hama_dir2)
        import hama_recipe as _hama_r
        _hpl, _herr = _hama_r.day_payload(
            day_str, today_bars=None if WAVE_DATE else _idf)
        if _hpl is None:
            raise RuntimeError(f"hama 载荷失败: {_herr}")
        _hb = _hpl["bars"]
        _hn = len(_hb)
        # 固定 x 轴为全天 09:30-15:59 (390 根 1m), 数据左对齐、右侧留白
        # (与 Envelope _env_times1m 同款; 只用已走 bars 会把类目轴拉满全宽,
        # 盘中 hama 图被横向拉伸、与其它版不对齐 — user 2026-11-24 令改)
        hama_times1m = []
        _hh9, _mm9 = 9, 30
        for _k9 in range(390):
            hama_times1m.append(f"{_hh9:02d}:{_mm9:02d}")
            _mm9 += 1
            if _mm9 >= 60:
                _hh9 += 1
                _mm9 -= 60
        _hclose = [b[4] for b in _hb]
        hama_ohlc = [[b[1], b[4], b[3], b[2]] for b in _hb]
        _hqcol_c = {1: "#2563eb", -1: "#dc2626", 0: "#9ca3af"}
        hama_qqe = [{"value": _hv,
                     "itemStyle": {"color": _hqcol_c.get(_qc, "#9ca3af")}}
                    for _hv, _qc in zip(_hpl["hist"], _hpl["qcol"])]
        if _hn < 390:  # 盘中未走完: 对齐数组尾部补 null 到全天 390
            hama_ohlc = hama_ohlc + [[None, None, None, None]] * (390 - _hn)
            hama_qqe = hama_qqe + [None] * (390 - _hn)
        _RSN_CN = {"DECAY": "衰减出场", "DECAYF": "衰减加速出场",
                   "CTRX": "逆势衰减出场",
                   "D4": "连减4根出场", "GREY": "QQE转灰",
                   "FLIP": "新波反向", "HB": "回补门槛平仓", "BG": "背景破",
                   "SL": "止损", "EOD": "日终平仓"}
        _SRC_CN = {"STD": "顺势凑齐", "REV": "衰减反转·等确认",
                   "REVF": "衰减反转·加速", "HB": "回补进场"}

        def _src_cn(_t):
            # 逆势单按子路分标签 (定版 2026-11-24), 其余走 _SRC_CN
            if _t.get("src") == "CTR":
                return "逆势·" + {"F": "标准跟随", "DF": "衰减快速"}.get(
                    _t.get("csub"), "衰减翻仓")
            return _SRC_CN.get(_t["src"], _t["src"])
        _htrds = _hpl["trades"]
        # 事件按 1m bar 归并: 同根平+开 (翻仓) 合成一个翻标记 (同 STVX)
        _hev = {}
        for _t in _htrds:
            _hev.setdefault(_t["xs"], {})["close"] = _t
            # 开仓标记一律挂决策根 (eb-1): STD/REVF 的 trig 即 eb-1 不受影响;
            # REV 的 trig 是 pending 建立根, 与真正开仓根可差数根, 挂 trig 会
            # 让圈跑到开仓之前 (2026-11-23 两笔 REV 分别差 11/6 根)。
            _hev.setdefault(_t["eb"] - 1, {})["open"] = _t
            hama_acts.setdefault(hama_times1m[_t["eb"] - 1], []).append(
                f"开{'call' if _t['dir'] == 1 else 'put'}@{_t['ep']:.2f} "
                f"{_src_cn(_t)}".replace("'", ""))
            hama_acts.setdefault(hama_times1m[_t["xs"]], []).append(
                f"平{'call' if _t['dir'] == 1 else 'put'}@{_t['xp']:.2f} "
                f"{_RSN_CN.get(_t['rsn'], _t['rsn'])}".replace("'", ""))

        # ---- 回吐止盈/回补红绿点 (user 2026-11-27 令; 上线口径, 只画点
        # 不算钱, 与研究 overlay_yield 状态机逐式一致): 只挂出场时仍逆势
        # 的单; 峰值门 4, 回吐线 = 固定 0.25 + 峰值 x 8.75%, 窗口第 3..99
        # 根 (进场根 = 第 1 根); 红点 = 止盈 (全卖口径), 止盈根去跳空
        # ATR <= 0.2125 才有回补资格, 其后顺向走满 3.0 个 ATR 一次全补
        # (绿点); 峰值从开仓起算、回补后不重置, 绿点后须先收复到回吐
        # 小于触发线才重新待发; 绿点上限 = min(平仓信号根-1, 下一笔
        # 信号根-1)。----
        _gb_atr = _hpl.get("atr") or []

        def _gb_roi(_dir, _ep, _px):
            _dd = _px - _ep
            _r = (9.88 * _dd + 0.311 * _dd * _dd) if _dir == 1 else \
                (-9.88 * _dd + 0.311 * _dd * _dd)
            return max(_r, -100.0)

        def _gb_settle(_dir, _p0, _p1):
            # 结算口径与引擎单一致: 30 倍 gamma, 下限 -100 (只算带回吐
            # 层的钱时用; 触发判定用上面的 L10 公式, 与研究页同分工)
            _dd = _p1 - _p0
            _r = (30.0 * _dd + 2.4 * _dd * _dd) if _dir == 1 else \
                (-30.0 * _dd + 2.4 * _dd * _dd)
            return max(_r, -100.0)

        _gb_pts = []
        _gb_sum = 0.0
        for _j, _t in enumerate(_htrds):
            if not _t.get("ctr"):
                _gb_sum += _t["r"]
                continue
            _lots = [[1.0, _t["ep"]]]
            _real = 0.0
            _touched = False
            _nxt = _htrds[_j + 1]["trig"] if _j + 1 < len(_htrds) else None
            _eb, _xs, _xb = _t["eb"], _t["xs"], _t["xb"]
            _lim = min(_xs - 1, (_nxt - 1) if _nxt is not None else _hn - 1)
            _pk = -1e18
            _stage = 0
            _red = -1
            _held = 1.0
            _sold = 0.0
            _pend = []
            _cyc = 0
            for _i in range(_eb, _xs + 1):
                _px = _hclose[_i] if _i < _xb else _t["xp"]
                _rt = _gb_roi(_t["dir"], _t["ep"], _px)
                if _rt > _pk:
                    _pk = _rt
                if _pk <= 0:
                    continue
                _line = 0.25 + 0.0875 * _pk
                _gbv = _pk - _rt
                _age = _i - _eb + 1
                if _stage == 0:
                    if _pk >= 4 and _gbv >= _line and 3 <= _age <= 99:
                        _red = _i
                        if _held > 1e-12:
                            _cyc += 1
                            _sold = _held
                            _held = 0.0
                            _touched = True
                            _h0 = sum(_lf for _lf, _ in _lots)
                            _new_lots = []
                            for _lf, _lp in _lots:
                                _si = _sold * (_lf / _h0) \
                                    if _h0 > 0 else 0.0
                                _real += _si * _gb_settle(
                                    _t["dir"], _lp, _hclose[_i])
                                if _lf - _si > 1e-12:
                                    _new_lots.append([_lf - _si, _lp])
                            _lots = _new_lots
                            _gate_ok = (_i < len(_gb_atr)
                                        and _gb_atr[_i] is not None
                                        and _gb_atr[_i] <= 0.2125)
                            _pend = [3.0] if _gate_ok else []
                            _gb_pts.append({
                                "value": [hama_times1m[_i],
                                          round(_hclose[_i], 2)],
                                "itemStyle": {"color": "#dc2626"}})
                            _gtxt = ("回吐止盈 · 回吐 %.1f ≥ 线 %.1f "
                                     "(峰值 %.1f)" % (_gbv, _line, _pk))
                            if _cyc > 1:
                                _gtxt += " · 第 %d 轮" % _cyc
                            if not _gate_ok:
                                _gtxt += (" · 止盈根 ATR %.3f 超 0.2125, "
                                          "不回补" % (_gb_atr[_i]
                                                     if _i < len(_gb_atr)
                                                     else -1))
                            hama_acts.setdefault(
                                hama_times1m[_i], []).append(
                                _gtxt.replace("'", ""))
                        else:
                            _sold = 0.0
                            _pend = []
                        _stage = 1
                elif _stage == 1:
                    if _i > _lim:
                        break
                    _ar = (_gb_atr[_red]
                           if _red < len(_gb_atr) else None)
                    if _ar:
                        _mv = (_hclose[_i] - _hclose[_red]) * _t["dir"]
                        _still = []
                        for _k in _pend:
                            if _mv >= _k * _ar:
                                _lots.append([_sold, _hclose[_i]])
                                _gb_pts.append({
                                    "value": [hama_times1m[_i],
                                              round(_hclose[_i], 2)],
                                    "itemStyle": {"color": "#16a34a"}})
                                hama_acts.setdefault(
                                    hama_times1m[_i], []).append(
                                    ("止盈回补 · 顺向走满 3.0 个 ATR"
                                     + (" · 第 %d 轮" % _cyc if _cyc > 1
                                        else "")).replace("'", ""))
                                _held = _sold
                            else:
                                _still.append(_k)
                        _pend = _still
                        if not _pend:
                            _stage = 2
                else:
                    if _gbv < _line:
                        _stage = 0
            if _touched:
                _gb_sum += _real + sum(
                    _lf * _gb_settle(_t["dir"], _lp, _t["xp"])
                    for _lf, _lp in _lots)
            else:
                _gb_sum += _t["r"]
        hama_gb_js = json.dumps(_gb_pts, ensure_ascii=False)

        # 非成交事件 (pending/午间门, recipe 仪表化输出) 并入 acts,
        # 与开/平行同根合并; 再据合并后的 acts 建悬浮窗 TIP (每 1m bar
        # 一条: 开/收 + 自家 1m QQE 值与颜色 + 全部说明行)。
        for _ebi, _etxt in _hpl.get("events", []):
            hama_acts.setdefault(hama_times1m[_ebi], []).append(
                str(_etxt).replace("'", ""))
        _QCOL_CN = {1: "蓝", -1: "红", 0: "灰"}

        def _hdir(v, pos_c, neg_c, zero_txt="黄色"):
            # 方向词带色 (user 2026-11-23 令: 悬浮窗写 SSL/HAMA 方向与
            # 背景满足与否, 字体给颜色)
            if v is None or v == 0:
                return "<span style='color:#d97706'>%s</span>" % zero_txt
            if v > 0:
                return ("<span style='color:%s'><b>多头</b></span>"
                        % pos_c)
            return ("<span style='color:%s'><b>空头</b></span>" % neg_c)

        def _shade_tc(sh):
            if sh is None or sh == 0:
                return "#d97706"
            if sh > 0:
                return "#00b300" if sh >= 2 else "#2fbf2f"
            return "#ff0000" if sh <= -2 else "#ff4020"

        for _i in range(_hn):
            _ts = hama_times1m[_i]
            _hv = _hpl["hist"][_i]
            _sh1 = _hpl["hama_shade"][_i]
            _sh3 = _hpl["hama3_shade"][_i]
            _bgv = _hpl["bg"][_i]
            if _bgv == 1:
                _bg_txt = ("<span style='color:#2563eb'>"
                           "<b>满足多头</b></span>")
            elif _bgv == -1:
                _bg_txt = ("<span style='color:#dc2626'>"
                           "<b>满足空头</b></span>")
            else:
                _bg_txt = "<span style='color:#888'>不满足多空</span>"
            _bgx = ("SSL " + _hdir(_hpl["ssl_st"][_i], "#089981",
                                   "#f23645", "无向")
                    + " · HAMA1m " + _hdir(_sh1, _shade_tc(_sh1),
                                           _shade_tc(_sh1))
                    + " · HAMA3m " + _hdir(_sh3, _shade_tc(_sh3),
                                           _shade_tc(_sh3))
                    + "<br/>背景 " + _bg_txt)
            hama_tip[_ts] = {
                "o": round(float(_hb[_i][1]), 2),
                "c": round(float(_hb[_i][4]), 2),
                "qqe": round(float(_hv), 1) if _hv is not None else 0,
                "col": _QCOL_CN.get(_hpl["qcol"][_i], "灰"),
                "bgx": _bgx,
                "why": "<br/>".join(hama_acts.get(_ts, []))}

        def _hama_pin(bar, value, color, rs_txt, rev_edge=False,
                      wait=False, ctr_edge=False):
            _h = _hpl["hist"][bar]
            _yq = (round(_h * 1.15 + (0.5 if _h > 0 else -0.5), 1)
                   if _h is not None else 5)
            _ist = {"color": color}
            if ctr_edge:
                # 逆势单亮蓝边圈 (定版 2026-11-24, 与研究页同式)
                _ist["borderColor"] = "#3b82f6"
                _ist["borderWidth"] = 3
            elif rev_edge:
                _ist["borderColor"] = "#111"
                _ist["borderWidth"] = 2.6
            if wait:
                # 等待圈: 淡填同色 + 虚线边, 与实心开仓圈区分 (user 2026-11-23 令:
                # 等待与确认都要画、要能一眼分清)
                _hx = color.lstrip("#")
                _ist = {"color": "rgba(%d,%d,%d,0.22)" % (
                            int(_hx[0:2], 16), int(_hx[2:4], 16),
                            int(_hx[4:6], 16)),
                        "borderColor": color, "borderWidth": 2,
                        "borderType": "dashed"}
            hama_pins.append({"coord": [hama_times1m[bar], _yq],
                              "value": value, "itemStyle": _ist,
                              "rs": rs_txt})
            hama_spins.append({"coord": [hama_times1m[bar],
                                         round(_hclose[bar], 2)],
                               "value": value,
                               "itemStyle": dict(_ist), "rs": rs_txt})

        for _bar in sorted(_hev):
            _e = _hev[_bar]
            if "close" in _e and "open" in _e:
                _nt = _e["open"]
                _ndr = "call" if _nt["dir"] == 1 else "put"
                _hama_pin(_bar, "翻" + ("C" if _nt["dir"] == 1 else "P"),
                          _dir_color(_ndr, "翻"),
                          _src_cn(_nt),
                          rev_edge=_nt["src"] in ("REV", "REVF"),
                          ctr_edge=bool(_nt.get("ctr0")))
                continue
            if "open" in _e:
                _t = _e["open"]
                _dr = "call" if _t["dir"] == 1 else "put"
                _hama_pin(_t["eb"] - 1,
                          "开" + ("C" if _t["dir"] == 1 else "P"),
                          _dir_color(_dr, "开"),
                          _src_cn(_t),
                          rev_edge=_t["src"] in ("REV", "REVF"),
                          ctr_edge=bool(_t.get("ctr0")))
            if "close" in _e:
                _t = _e["close"]
                _lbl = ("止损" if _t["rsn"] == "SL" else "平") + \
                    ("C" if _t["dir"] == 1 else "P")
                _hama_pin(_t["xs"], _lbl, "#9ca3af",
                          _RSN_CN.get(_t["rsn"], _t["rsn"]))
        # REV 等待圈: 画在 pending 链头第一根 (trig0, recipe 随单记录;
        # 链中途的重复登记只刷新 trig 不动 trig0, user 2026-11-23 令画头
        # 一根), 虚线淡圈「等C/等P」, 与 eb-1 的实心开仓圈成对出现。
        for _t in _htrds:
            _wb = _t.get("trig0", _t["trig"])
            if _t["src"] == "REV" and _wb < _t["eb"] - 1:
                _wdr = "call" if _t["dir"] == 1 else "put"
                _hama_pin(_wb,
                          "等" + ("C" if _t["dir"] == 1 else "P"),
                          _dir_color(_wdr, "开"), "衰减反转·等待确认",
                          wait=True)
        # 每单盈亏标签 (照 STVX scat 行堆叠算法, x = 平仓 1m bar)
        _sx_placed, _sx_data = [], []
        _sx_max_row = 0
        for _t in _htrds:
            _cbi = _t["xb"]
            _row = 0
            while any(abs(_cbi - _oc) < 15 and _or == _row
                      for (_oc, _or) in _sx_placed):
                _row += 1
            _sx_placed.append((_cbi, _row))
            _sx_max_row = max(_sx_max_row, _row)
            _yy = round(_base + _row * _row_h, 2)
            _vp = _t["r"]
            _sx_data.append(
                '{"value":["%s",%.2f],"lbl":"%s","label":{"color":THEME.tradeLabelColor(%s)}}'
                % (hama_times1m[_cbi], _yy, f"{_vp:+.1f}%", repr(float(_vp))))
        hama_scat_js = "[" + ",".join(_sx_data) + "]"
        if _sx_data:
            y_max_js = max(y_max_js, round(
                _base + (_sx_max_row + 1) * _row_h + _rng * 0.05, 2))
        hama_mark_area_js = "[" + ",".join(
            "[{xAxis:'%s',itemStyle:{color:THEME.pnlBgColorST(%s)}},{xAxis:'%s'}]"
            % (hama_times1m[_t["eb"]], round(_t["r"], 2),
               hama_times1m[_t["xb"]])
            for _t in _htrds) + "]"
        # ---- HAMA/SSL 线段 (build_html.py chanSeries/pushHama 逐式) ----
        _SHADE_C = {0: "#facc15", 1: "#66d95e", 2: "#2fbf2f", 3: "#00b300",
                    -1: "#ff8c42", -2: "#ff4020", -3: "#ff0000"}
        _SSL_C = {1: "#089981", -1: "#f23645", 0: "#9ca3af"}

        def _runs_of(arr):
            _out, _s = [], 0
            for _i in range(1, _hn + 1):
                if _i == _hn or arr[_i] != arr[_s]:
                    _out.append((_s, _i - 1, arr[_s]))
                    _s = _i
            return _out

        def _seg_data(vals, a, b):
            _d = [None] * 390
            for _i in range(a, b + 1):
                _d[_i] = vals[_i]
            return _d

        for (_a, _b, _sv) in _runs_of(_hpl["ssl_st"]):
            _ext = min(_b + 1, _hn - 1)
            hama_line_segs.append({"name": "SSL", "w": 1.6,
                                   "c": _SSL_C[_sv],
                                   "d": _seg_data(_hpl["ssl_mah"], _a, _ext)})
            hama_line_segs.append({"name": "SSL", "w": 1.6,
                                   "c": _SSL_C[_sv],
                                   "d": _seg_data(_hpl["ssl_mal"], _a, _ext)})
            if _sv != 0:
                _base_d = _seg_data(_hpl["ssl_mal"], _a, _b)
                _delt_d = [None] * 390
                for _i in range(_a, _b + 1):
                    if _hpl["ssl_mah"][_i] is not None and \
                            _hpl["ssl_mal"][_i] is not None:
                        _delt_d[_i] = round(_hpl["ssl_mah"][_i]
                                            - _hpl["ssl_mal"][_i], 2)
                hama_ssl_fills.append({
                    "b": _base_d, "d": _delt_d,
                    "c": "rgba(8,153,129,0.10)" if _sv == 1
                    else "rgba(242,54,69,0.10)"})
        for (_a, _b, _sv) in _runs_of(_hpl["hama_shade"]):
            hama_line_segs.append({
                "name": "HAMA", "w": 2.6, "c": _SHADE_C[_sv],
                "d": _seg_data(_hpl["hama"], _a, min(_b + 1, _hn - 1))})
        for (_a, _b, _sv) in _runs_of(_hpl["hama3_shade"]):
            hama_line_segs.append({
                "name": "HAMA3", "w": 1.6, "c": _SHADE_C[_sv],
                "d": _seg_data(_hpl["hama3_line"], _a,
                               min(_b + 1, _hn - 1))})
        # ---- 逐笔表 (照 _est_table_inner 同表头; 口径 = 每笔 $100k
        # 定额、合计逐笔相加, 与标题同口径) ----
        _hrows = []
        _htot_usd = 0.0
        for _k, _t in enumerate(_htrds, 1):
            _pnl = 100000.0 * _t["r"] / 100.0
            _htot_usd += _pnl
            _ec = "#1a9e54" if _pnl >= 0 else "#e5484d"
            _hrows.append(
                f"<tr><td>{_k}</td><td>{hama_times1m[_t['eb']]}</td>"
                f"<td>开{'Call' if _t['dir'] == 1 else 'Put'}</td><td>—</td>"
                f"<td>—</td><td>{_t['ep']:.2f}</td>"
                f"<td>{hama_times1m[_t['xb']]}</td><td>—</td>"
                f"<td>{_t['xp']:.2f}</td>"
                f"<td style=\"color:#888888;font-weight:bold\">—</td>"
                f"<td style=\"color:#888888\">—</td>"
                f"<td style=\"color:{_ec}\">~{'+' if _pnl >= 0 else '-'}${abs(_pnl):,.0f}</td>"
                f"<td style=\"color:{_ec}\">~{_t['r']:+.1f}%</td></tr>")
        # 合计用载荷 sum (未四舍五入 ROI 逐笔相加, 与标题/sim_out 同
        # 口径; 逐笔行 r 已四舍五入到 2dp, 行合计与总计可差几美元)
        _htot_usd = _hpl["sum"] * 1000.0
        _hrows.append(
            f"<tr style=\"background:#f2f2f2;font-weight:bold\"><td colspan=\"9\">合计"
            f"</td><td>—</td><td></td>"
            f"<td>~{'+' if _htot_usd >= 0 else '-'}${abs(_htot_usd):,.0f}</td><td></td></tr>")
        hama_inner = (
            "<div style=\"font-size:14px;font-weight:bold;margin-bottom:6px\">"
            "Hama版信号单（SSL×HAMA×QQE，30%杠杆真0DTE口径）</div>"
            "<div style=\"overflow-x:auto\"><table border=\"1\" cellspacing=\"0\" cellpadding=\"6\" "
            "style=\"border-collapse:collapse;font-size:12px;width:100%;text-align:center;white-space:nowrap\">"
            "<tr style=\"background:#f2f2f2\"><th>#</th><th>开仓</th><th>操作</th><th>张数</th>"
            "<th>开均价</th><th>开SPY</th><th>平仓</th><th>平均价</th><th>平SPY</th><th>实际收益</th><th>实际收益率</th>"
            "<th>预估收益</th><th>预估收益率</th></tr>"
            + "".join(_hrows) + "</table></div>"
            "<div style=\"font-size:11px;color:#888;margin-top:4px\">"
            "预估=30%杠杆真0DTE公式ROI×$100k/笔、合计逐笔相加（非复利）；"
            "背景=SSL 1m+HAMA 1m/3m 与 QQE 同色即进场，衰减反转 REV、逆势单 CTR（蓝边圈），出场 DECAY/D4/HB/FLIP/BG，"
            "午间斜率门，FOMC 日不做；非实际成交</div>")
        _hsum = _hpl["sum"]
        _htot2 = _hsum * 1000.0
        _gsum = round(_gb_sum, 2)
        _gtot2 = _gsum * 1000.0
        hama_title_txt = (f"{day_str} (Hama版) | {gap_txt} | "
                          f"{'+' if _hsum >= 0 else '-'}${abs(_htot2):,.0f} "
                          f"({_hsum:+.1f}%)"
                          f" | 止盈回吐回补 "
                          f"{'+' if _gsum >= 0 else '-'}${abs(_gtot2):,.0f} "
                          f"({_gsum:+.1f}%)")
        _hpnl_e = (("{pnl_pos|+" if _hsum >= 0 else "{pnl_neg|-")
                   + f"${abs(_htot2):,.0f} ({_hsum:+.1f}%)" + "}")
        _gpnl_e = (("{pnl_pos|+" if _gsum >= 0 else "{pnl_neg|-")
                   + f"${abs(_gtot2):,.0f} ({_gsum:+.1f}%)" + "}")
        hama_title_echart = (day_str + " ({hama|hama}版) | " + gap_e
                             + " | " + _hpnl_e
                             + " | 止盈回吐回补 " + _gpnl_e)
        print(f"  hama: {_hpl['n']} 单, 当日收益率(逐笔相加) {_hsum:+.2f}%",
              flush=True)
    except Exception as _he2:  # noqa: BLE001
        print(f"  WARN hama 计算失败, 本版为空: {_he2}", flush=True)

    _qqe_acts = {t: (v.get("acts") or []) for t, v in bar_info.items()}
    _vh_acts = {tstrs[bi]: acts for bi, acts in vh_acts_by_bar.items()}
    _sw = []
    _sw.append("<script>")
    _sw.append("const QQE_PINS = " + json.dumps(qpins, ensure_ascii=False) + ";")
    _sw.append("const QQE_SPINS = " + json.dumps(spins, ensure_ascii=False) + ";")
    _sw.append("const QQE_SCAT = " + scat_js + ";")
    _sw.append("const QQE_MARKAREA = " + qqe_mark_area_js + ";")
    _sw.append("const QQE_ACTS = " + json.dumps(_qqe_acts, ensure_ascii=False) + ";")
    _sw.append("const VH_SCAT = " + vh_scat_js + ";")
    _sw.append("const VH_SEG = " + vh_seg_js + ";")
    _sw.append("const VH_MARKAREA = " + vh_mark_area_js + ";")
    _sw.append("const VH_MARKLINE = " + vh_markline_js + ";")
    _sw.append("const VH_ACTS = " + json.dumps(_vh_acts, ensure_ascii=False) + ";")
    _sw.append("const CE_PINS = " + json.dumps(ce_pins, ensure_ascii=False) + ";")
    _sw.append("const CE_SPINS = " + json.dumps(ce_spins, ensure_ascii=False) + ";")
    _sw.append("const CE_SCAT = " + ce_scat_js + ";")
    _sw.append("const CE_MARKAREA = " + ce_mark_area_js + ";")
    _sw.append("const CE_ACTS = " + json.dumps(ce_acts, ensure_ascii=False) + ";")
    _sw.append("const CE_INNER = " + json.dumps(ce_inner, ensure_ascii=False) + ";")
    _sw.append("const CE_BS = " + ce_bs_js + ";")
    # ---- Envelope 双版数据集 (2026-11-20): 成交类常量两版各一份进
    # ENVELOPE_SETS, 工作变量默认取冠军版; 页内 switchEnvMode 整套
    # 换 (圈/单子表/acts/tip/标题/段染色)。共用常量 (轨道/TIMES1M/
    # TREND_SEGS) 仍只发一份。 ----
    _WER_TITLES = {"champion": "WER · WER冠军/反手+QQE 共用（引擎口径）",
                   "f1": "WER · WER冠军/反手+QQE 共用（引擎口径）",
                   "imb": "WER · WER失衡版（窗口 8 · 引擎口径）"}

    def _env_set_js(_C, _m):
        return ("{PINS: " + json.dumps(_C["ENVELOPE_PINS"], ensure_ascii=False)
                + ", SPINS: " + json.dumps(_C["ENVELOPE_SPINS"], ensure_ascii=False)
                + ", SCAT_1M: " + json.dumps(_C["ENVELOPE_SCAT_1M"], ensure_ascii=False)
                + ", MARKAREA_1M: " + _C["ENVELOPE_MARKAREA_1M"]
                + ", INNER: " + json.dumps(_C["ENVELOPE_INNER"], ensure_ascii=False)
                + ", ACTS: " + json.dumps(_C["ENVELOPE_ACTS"], ensure_ascii=False)
                + ", TIP: " + json.dumps(_C["ENVELOPE_TIP"], ensure_ascii=False)
                + ", TITLE: " + json.dumps(_C["ENVELOPE_TITLE"], ensure_ascii=False)
                + ", TITLEE: " + json.dumps(_C["ENVELOPE_TITLEE"], ensure_ascii=False)
                + ", SLOPE_DYE: " + json.dumps(_C["ENVELOPE_SLOPE_DYE"], ensure_ascii=False)
                + ", QF1M: " + json.dumps(_C.get("ENVELOPE_QF1M", []), ensure_ascii=False)
                + ", WER: " + json.dumps(_C["ENVELOPE_WER"], ensure_ascii=False)
                + ", WTITLE: " + json.dumps(_WER_TITLES[_m], ensure_ascii=False)
                + "}")
    _sw.append("const ENVELOPE_SETS = {" + ", ".join(
        f"{_m}: " + _env_set_js(_env_set_consts[_m], _m)
        for _m, _mn, _lt in _ENV_MODES) + "};")
    _sw.append("var ENVELOPE_PINS = ENVELOPE_SETS.champion.PINS;")
    _sw.append("var ENVELOPE_SPINS = ENVELOPE_SETS.champion.SPINS;")
    _sw.append("var ENVELOPE_SCAT_1M = ENVELOPE_SETS.champion.SCAT_1M;")
    _sw.append("var ENVELOPE_MARKAREA_1M = ENVELOPE_SETS.champion.MARKAREA_1M;")
    _sw.append("var ENVELOPE_INNER = ENVELOPE_SETS.champion.INNER;")
    _sw.append("var ENVELOPE_ACTS = ENVELOPE_SETS.champion.ACTS;")
    _sw.append("var ENVELOPE_TIP = ENVELOPE_SETS.champion.TIP;")
    _sw.append("var ENVELOPE_SLOPE_DYE = ENVELOPE_SETS.champion.SLOPE_DYE;")
    _sw.append("var ENVELOPE_QF1M = ENVELOPE_SETS.champion.QF1M;")
    _sw.append("const ENVELOPE_TITLE = ENVELOPE_SETS.champion.TITLE;")
    _sw.append("const ENVELOPE_TITLEE = ENVELOPE_SETS.champion.TITLEE;")
    _sw.append("const ENVELOPE_TIMES1M = " + json.dumps(_env_times1m) + ";")
    _sw.append("const ENVELOPE_UPPER_1M = " + json.dumps(env_upper) + ";")
    _sw.append("const ENVELOPE_LOWER_1M = " + json.dumps(env_lower) + ";")
    _sw.append("const ENVELOPE_CHANMID_1M = " + json.dumps(env_chanmid) + ";")
    _sw.append("const ENVELOPE_MID_1M = " + json.dumps(env_mid) + ";")
    _sw.append("const ENVELOPE_TREND_SEGS = " + json.dumps(
        _env_int_consts["ENVELOPE_TREND_SEGS"] if _env_int_ok else (
            [{"color": _sg.get("color"), "theme": _sg.get("theme"),
              "data": []} for _sg in _env_trend_segs]
            if _env_wer_failed else _env_trend_segs)) + ";")
    _sw.append("const STVX_PINS = " + json.dumps(stvx_pins, ensure_ascii=False) + ";")
    _sw.append("const STVX_SPINS = " + json.dumps(stvx_spins, ensure_ascii=False) + ";")
    _sw.append("const STVX_SCAT = " + stvx_scat_js + ";")
    _sw.append("const STVX_MARKAREA = " + stvx_mark_area_js + ";")
    _sw.append("const STVX_INNER = " + json.dumps(stvx_inner, ensure_ascii=False) + ";")
    _sw.append("const STVX_ACTS = " + json.dumps(stvx_acts, ensure_ascii=False) + ";")
    _sw.append("const STVX_UPPER = " + json.dumps(stvx_upper_s) + ";")
    _sw.append("const STVX_UPPER_F = " + json.dumps(stvx_upper_f) + ";")
    _sw.append("const STVX_LOWER = " + json.dumps(stvx_lower_s) + ";")
    _sw.append("const STVX_LOWER_F = " + json.dumps(stvx_lower_f) + ";")
    _sw.append("const STVX_MAINUP = " + json.dumps(stvx_main_up_s) + ";")
    _sw.append("const STVX_MAINUP_F = " + json.dumps(stvx_main_up_f) + ";")
    _sw.append("const STVX_MAINDN = " + json.dumps(stvx_main_dn_s) + ";")
    _sw.append("const STVX_MAINDN_F = " + json.dumps(stvx_main_dn_f) + ";")
    _sw.append("const STVX_MAINFILL = " + stvx_main_fill_js + ";")
    _sw.append("const STVX_MAINFLIPS = " + stvx_main_flips_js + ";")
    _sw.append("const HAMA_PINS = " + json.dumps(hama_pins, ensure_ascii=False) + ";")
    _sw.append("const HAMA_SPINS = " + json.dumps(hama_spins, ensure_ascii=False) + ";")
    _sw.append("const HAMA_GB = " + hama_gb_js + ";")
    _sw.append("const HAMA_SCAT = " + hama_scat_js + ";")
    _sw.append("const HAMA_MARKAREA = " + hama_mark_area_js + ";")
    _sw.append("const HAMA_INNER = " + json.dumps(hama_inner, ensure_ascii=False) + ";")
    _sw.append("const HAMA_ACTS = " + json.dumps(hama_acts, ensure_ascii=False) + ";")
    _sw.append("const HAMA_TIP = " + json.dumps(hama_tip, ensure_ascii=False) + ";")
    _sw.append("const HAMA_TIMES1M = " + json.dumps(hama_times1m) + ";")
    _sw.append("const HAMA_OHLC = " + json.dumps(hama_ohlc) + ";")
    _sw.append("const HAMA_QQE = " + json.dumps(hama_qqe) + ";")
    _sw.append("const HAMA_LINE_SEGS = " + json.dumps(hama_line_segs) + ";")
    _sw.append("const HAMA_SSL_FILLS = " + json.dumps(hama_ssl_fills) + ";")
    _sw.append("var ENVELOPE_WER = " + json.dumps(wer_panel) + ";")
    _sw.append("const ENVELOPE_SLPDEV = " + json.dumps(slpdev_panel) + ";")
    _sw.append("const ENVELOPE_QQE1M = " + json.dumps(env_qqe1m) + ";")
    _sw.append("""(function(){
if (typeof THEME === 'undefined' || !THEME.tooltipFormatter || THEME._envTipWrapped) return;
var _origEnvTip = THEME.tooltipFormatter;
THEME._envTipWrapped = true;
THEME.tooltipFormatter = function(ps, barInfo) {
try {
if (typeof curVer3 !== 'undefined' && curVer3 === 'envelope' && typeof ENVELOPE_TIP !== 'undefined') {
var t = ps[0].axisValue;
var e = ENVELOPE_TIP[t];
if (e) {
var s = '<b>' + t + '</b><br/>开' + e.o + ' 收' + e.c;
s += '<br/><span style=\\'color:' + THEME.envelope.upper + '\\'>上' + e.up + '</span> <span style=\\'color:#b58900\\'>中' + e.mid + '</span> <span style=\\'color:' + THEME.envelope.lower + '\\'>下' + e.lo + '</span>';
if (e.why) s += '<br/>' + e.why;
else s += '<br/><span style=\\'color:#888\\'>无Envelope动作</span>';
return s;
}
}
if (typeof curVer3 !== 'undefined' && curVer3 === 'hama' && typeof HAMA_TIP !== 'undefined') {
var t2 = ps[0].axisValue;
var h = HAMA_TIP[t2];
if (h) {
var qc2 = {'蓝': '#2563eb', '红': '#dc2626', '灰': '#9ca3af'}[h.col] || '#9ca3af';
var s2 = '<b>' + t2 + '</b><br/>开' + h.o + ' 收' + h.c;
s2 += '<br/>QQE <span style=\\'color:' + qc2 + '\\'><b>' + h.qqe + '</b></span>（' + h.col + '）';
if (h.bgx) s2 += '<br/>' + h.bgx;
if (h.why) s2 += '<br/>' + h.why;
else s2 += '<br/><span style=\\'color:#888\\'>无Hama动作</span>';
return s2;
}
}
} catch (err) {}
return _origEnvTip(ps, barInfo);
};
})();""")
    _sw.append("const TITLES3 = {live: " + json.dumps(title_txt, ensure_ascii=False)
               + ", vh: " + json.dumps(vh_title_txt, ensure_ascii=False)
               + ", qqe: " + json.dumps(qqe_title_txt, ensure_ascii=False)
               + ", ce: " + json.dumps(ce_title_txt, ensure_ascii=False)
               + ", envelope: " + json.dumps(env_title_txt, ensure_ascii=False)
               + ", stvx: " + json.dumps(stvx_title_txt, ensure_ascii=False)
               + ", hama: " + json.dumps(hama_title_txt, ensure_ascii=False) + "};")
    _sw.append("const TITLES3E = {vh: " + json.dumps(vh_title_echart, ensure_ascii=False)
               + ", qqe: " + json.dumps(qqe_title_echart, ensure_ascii=False)
               + ", ce: " + json.dumps(ce_title_echart, ensure_ascii=False)
               + ", envelope: " + json.dumps(env_title_echart, ensure_ascii=False)
               + ", stvx: " + json.dumps(stvx_title_echart, ensure_ascii=False)
               + ", hama: " + json.dumps(hama_title_echart, ensure_ascii=False) + "};")
    _sw.append("const RICH3 = {dn:{color:'#e5484d',fontWeight:'bold',fontSize:18},up:{color:'#2e9e5b',fontWeight:'bold',fontSize:18},flat:{color:'#e5484d',fontWeight:'bold'},st:{color:'#00a0a0',fontWeight:'bold'},qqe:{color:'#2b7fff',fontWeight:'bold'},ce:{color:'#e5484d',fontWeight:'bold'},envelope:{color:'#2563eb',fontWeight:'bold'},stvx:{color:'#00a0a0',fontWeight:'bold'},hama:{color:'#7c3aed',fontWeight:'bold'},pnl_pos:{color:'#1a9e54'},pnl_neg:{color:'#e5484d'}};")
    _sw.append("const VH_INNER = " + json.dumps(vh_inner, ensure_ascii=False) + ";")
    _sw.append("const QQE_INNER = " + json.dumps(qqe_inner, ensure_ascii=False) + ";")
    _sw.append("var LIVE_INNER = (function(){var tw=document.getElementById('tblWrap');return tw?tw.innerHTML:'';})();")
    _sw.append("var curVer3 = 'live';")
    _sw.append("var QQE_DEF_DATA = null; var K3_DEF_DATA = null;")
    _sw.append("(function(){ try { var _o0 = ch.getOption(); if (_o0 && _o0.series) { for (var _si0 = 0; _si0 < _o0.series.length; _si0++) { if (_o0.series[_si0].name === 'QQE' && QQE_DEF_DATA === null) QQE_DEF_DATA = _o0.series[_si0].data; if (_o0.series[_si0].name === '3mK线' && K3_DEF_DATA === null) K3_DEF_DATA = _o0.series[_si0].data; } } } catch(e) {} })();")
    _sw.append("window._fitChartH = function(){ var host = document.getElementById('c'); if (!host) return; var docTop = host.getBoundingClientRect().top + (window.pageYOffset || document.documentElement.scrollTop || 0); var fitH = window.innerHeight - docTop - 4; if (fitH < 380) fitH = 380; host.style.height = fitH + 'px'; if (typeof ch !== 'undefined' && ch) ch.resize(); };")
    _sw.append("window.addEventListener('resize', function(){ if (curVer3 !== 'envelope' && window._fitChartH) window._fitChartH(); });")
    _sw.append("function switchVersion3(v){")
    _sw.append("  curVer3 = v;")
    _sw.append("  try { localStorage.setItem('waveVer3', v); } catch(e) {}")
    _sw.append("  var isLive = (v === 'live');")
    _sw.append("  var isVH = (v === 'vh');")
    _sw.append("  var useVH = isLive || isVH;")
    _sw.append("  var isCE = (v === 'ce');")
    _sw.append("  var isENVELOPE = (v === 'envelope');")
    _sw.append("  var isSTVX = (v === 'stvx');")
    _sw.append("  var isHAMA = (v === 'hama');")
    _sw.append("  var pinsData = isHAMA ? HAMA_PINS : (isENVELOPE ? ENVELOPE_PINS : (isCE ? CE_PINS : (isSTVX ? STVX_PINS : (useVH ? pins : QQE_PINS))));")
    _sw.append("  var spinsData = isHAMA ? HAMA_SPINS : (isENVELOPE ? ENVELOPE_SPINS : (isCE ? CE_SPINS : (isSTVX ? STVX_SPINS : (useVH ? spins : QQE_SPINS))));")
    _sw.append("  var scatData = isHAMA ? HAMA_SCAT : (isENVELOPE ? ENVELOPE_SCAT_1M : (isCE ? CE_SCAT : (isSTVX ? STVX_SCAT : (useVH ? VH_SCAT : QQE_SCAT))));")
    _sw.append("  var scatFS = (isCE || useVH) ? 11 : 8;")
    _sw.append("  if (typeof THEME !== 'undefined' && THEME.applyColors) { THEME.applyColors(spinsData); THEME.applyColors(pinsData); }")
    # Envelope 进场圈按单子类型的边缘色/透明度 (user 2026-11-20 19:15
    # 令): 规格在 THEME.pinEdge, applyPinEdges 按圈上 pt 标记刷; 无 pt
    # 的圈 (平仓圈/他节圈) 不碰, 与 applyColors 同口子。
    _sw.append("  if (typeof THEME !== 'undefined' && THEME.applyPinEdges) { THEME.applyPinEdges(spinsData); THEME.applyPinEdges(pinsData); }")
    _sw.append("  ch.setOption({")
    _sw.append("    title: {text: isLive ? TITLES3.live : (isHAMA ? TITLES3E.hama : (isENVELOPE ? TITLES3E.envelope : (isCE ? TITLES3E.ce : (isSTVX ? TITLES3E.stvx : (isVH ? TITLES3E.vh : TITLES3E.qqe))))), left: 'center', textStyle: {fontSize: 14, rich: RICH3}},")
    _sw.append("    series: [")
    # 冠军版 (envwer 成功时): 1m 曲线在 envelope 下恢复显示 (WER 段
    # 染色线叠加其上); 失败时保持原条件 (染色段数据已置空)。
    _env_1m_expr = ("line1m"
                    if _env_int_ok else
                    "(isENVELOPE && ENVELOPE_TREND_SEGS.length) ? [] : line1m")
    _sw.append("      {name: '1m曲线', xAxisIndex: isHAMA ? 0 : 3, data: isHAMA ? [] : " + _env_1m_expr + ", lineStyle: {color: isENVELOPE ? '#1e40af' : '#4da6ff', width: isENVELOPE ? 2 : 1.2, opacity: isENVELOPE ? 1 : 0.55}, markPoint: {data: spinsData}},")
    if _env_int_ok and not _env_trend_segs:
        _sw.append("      {id: 'env-trend-seg-0', name: 'WER段染色', type: 'line', xAxisIndex: 3, yAxisIndex: 0, symbol: 'none', showSymbol: false, silent: true, lineStyle: {color: '#38bdf8', width: 3}, z: 6, data: isENVELOPE ? ENVELOPE_SLOPE_DYE : []},")
    for _si, _sg in enumerate(_env_trend_segs):
        if _env_int_ok and _si == 0:
            _sw.append("      {id: 'env-trend-seg-0', name: 'WER段染色', type: 'line', xAxisIndex: 3, yAxisIndex: 0, symbol: 'none', showSymbol: false, silent: true, lineStyle: {color: '#38bdf8', width: 3}, z: 6, data: isENVELOPE ? ENVELOPE_SLOPE_DYE : []},")
        elif _env_int_ok:
            _sw.append("      {id: 'env-trend-seg-%d', name: '1m趋势段%d', type: 'line', xAxisIndex: 3, yAxisIndex: 0, symbol: 'none', showSymbol: false, lineStyle: {color: %s, width: THEME.envelope.trendWidth}, data: []}," % (_si, _si, _sg["theme"]))
        else:
            _sw.append("      {id: 'env-trend-seg-%d', name: '1m趋势段%d', type: 'line', xAxisIndex: 3, yAxisIndex: 0, symbol: 'none', showSymbol: false, lineStyle: {color: %s, width: THEME.envelope.trendWidth}, data: isENVELOPE ? ENVELOPE_TREND_SEGS[%d].data : []}," % (_si, _si, _sg["theme"], _si))
    _sw.append("      {name: 'QQE', data: isHAMA ? HAMA_QQE : (QQE_DEF_DATA ? QQE_DEF_DATA : undefined), markPoint: {data: pinsData}},")
    _sw.append("      {name: '每单盈亏', data: scatData, label: {show: isENVELOPE ? false : true, opacity: 1, fontSize: scatFS, fontWeight: 'bold', position: 'top', distance: 1, formatter: function(p){return p.data.lbl;}}, markArea: {silent: true, data: isENVELOPE ? ENVELOPE_MARKAREA_1M : []}},")
    _sw.append("      {name: '3mK线', data: isHAMA ? [] : (K3_DEF_DATA ? K3_DEF_DATA : undefined), markArea: {silent: true, data: (isENVELOPE || isHAMA) ? [] : (isCE ? CE_MARKAREA : (isSTVX ? STVX_MARKAREA : (useVH ? VH_MARKAREA : QQE_MARKAREA)))}, markLine: {silent: true, symbol: 'none', label: {show: false}, lineStyle: {color: 'rgba(0,0,0,0.20)', width: 1, type: 'solid'}, data: useVH ? VH_MARKLINE : []}},")
    _sw.append("      {name: '段收益率', type: 'scatter', xAxisIndex: 0, yAxisIndex: 0, symbolSize: 1, itemStyle: {opacity: 0}, label: {show: true, opacity: 1, fontSize: 8, fontWeight: 'bold', position: 'top', distance: 1, formatter: function(p){return p.data.lbl;}}, data: useVH ? VH_SEG : []},")
    _sw.append("      {id: 'ce-signal', name: 'CE信号', type: 'scatter', xAxisIndex: 0, yAxisIndex: 0, symbolSize: 1, itemStyle: {opacity: 0}, label: {show: true, fontSize: 12}, z: 6, data: isCE ? CE_BS : []},")
    _sw.append("      {id: 'hama-gb', name: '回吐止盈回补', type: 'scatter', xAxisIndex: 0, yAxisIndex: 0, symbol: 'circle', symbolSize: 10, itemStyle: {borderColor: '#fff', borderWidth: 1.2}, z: 7, tooltip: {show: false}, data: isHAMA ? HAMA_GB : []},")
    _sw.append("      {id: 'env-upper', name: 'Envelope上轨', type: 'line', xAxisIndex: 0, yAxisIndex: 0, symbolSize: 0, showSymbol: false, lineStyle: {color: THEME.envelope.upper, width: 2}, data: isENVELOPE ? ENVELOPE_UPPER_1M : []},")
    _sw.append("      {id: 'env-lower', name: 'Envelope下轨', type: 'line', xAxisIndex: 0, yAxisIndex: 0, symbolSize: 0, showSymbol: false, lineStyle: {color: THEME.envelope.lower, width: 2}, data: isENVELOPE ? ENVELOPE_LOWER_1M : []},")
    _sw.append("      {id: 'env-chanmid', name: 'Envelope中轨', type: 'line', xAxisIndex: 0, yAxisIndex: 0, symbolSize: 0, showSymbol: false, lineStyle: {color: THEME.envelope.chanmid, width: 2}, data: isENVELOPE ? ENVELOPE_CHANMID_1M : []},")
    _sw.append("      {id: 'env-mid', name: 'Envelope均线', type: 'line', xAxisIndex: 0, yAxisIndex: 0, symbolSize: 0, showSymbol: false, lineStyle: {color: THEME.envelope.mid, width: 2}, data: isENVELOPE ? ENVELOPE_MID_1M : []},")
    _sw.append("      {id: 'stvx-upper', name: '影子主轨', type: 'line', xAxisIndex: 0, yAxisIndex: 0, symbolSize: 0, showSymbol: false, lineStyle: {color: '#9333ea', width: 1.2, type: [6, 4], opacity: 0.9}, z: 4, data: isSTVX ? STVX_UPPER : []},")
    _sw.append("      {id: 'stvx-upper-f', name: '影子主轨', type: 'line', xAxisIndex: 0, yAxisIndex: 0, symbolSize: 0, showSymbol: false, lineStyle: {color: '#9333ea', width: 1.2, type: [6, 4], opacity: 0.3}, z: 4, data: isSTVX ? STVX_UPPER_F : []},")
    _sw.append("      {id: 'stvx-lower', name: '影子主轨', type: 'line', xAxisIndex: 0, yAxisIndex: 0, symbolSize: 0, showSymbol: false, lineStyle: {color: '#9333ea', width: 1.2, type: [6, 4], opacity: 0.9}, z: 4, data: isSTVX ? STVX_LOWER : []},")
    _sw.append("      {id: 'stvx-lower-f', name: '影子主轨', type: 'line', xAxisIndex: 0, yAxisIndex: 0, symbolSize: 0, showSymbol: false, lineStyle: {color: '#9333ea', width: 1.2, type: [6, 4], opacity: 0.3}, z: 4, data: isSTVX ? STVX_LOWER_F : []},")
    _sw.append("      {name: '成交点', data: (isENVELOPE || isSTVX || isHAMA) ? [] : filldots},")
    # ST VX 主轨 (2026-11-17 user 令): 美术照 ST VH 节 st慢版——本体未压带
    # 最终带的上下两条轨线都画全成实线轨道 (上轨 #f23645 / 下轨 #089981,
    # 宽2, z5; 不再只画活动侧) + 价格与活动带间半透明填充
    # (custom 四边形, 同色 0.12 alpha, z1, renderItem 与 VH 模板逐字同款)
    # + 本体翻转 buy/sell 标记 (scatter 圆点10/z6, 标签同 VH stFlips 规格;
    # 与 VX 自身单子圈是两套标记, 单子走压带趋势、此标记只作本体参考);
    # 影子主轨 (压带 fu/fl) = 紫罗兰 #9333ea 1.2px 虚线无填充 (上方两条, z4)。
    _sw.append("      {id: 'stvx-mainfill', name: '主轨', type: 'custom', silent: true, z: 1, xAxisIndex: 0, yAxisIndex: 0, renderItem: function(params, api){ var v=api.value(0), v1=api.value(1), v2=api.value(2), v3=api.value(3), v4=api.value(4), v5=api.value(5), tr=api.value(6); if (v===v3) return null; var p1=api.coord([v,v1]), p2=api.coord([v3,v4]), p3=api.coord([v3,v5]), p4=api.coord([v,v2]); return {type: 'polygon', shape: {points: [p1,p2,p3,p4]}, style: {fill: tr===1 ? 'rgba(8,153,129,0.12)' : 'rgba(242,54,69,0.12)', stroke: null}}; }, data: isSTVX ? STVX_MAINFILL : []},")
    _sw.append("      {id: 'stvx-mainup', name: '主轨', type: 'line', xAxisIndex: 0, yAxisIndex: 0, symbolSize: 0, showSymbol: false, lineStyle: {color: '#f23645', width: 2}, z: 5, data: isSTVX ? STVX_MAINUP : []},")
    _sw.append("      {id: 'stvx-mainup-f', name: '主轨', type: 'line', xAxisIndex: 0, yAxisIndex: 0, symbolSize: 0, showSymbol: false, lineStyle: {color: '#f23645', width: 2, opacity: 0.3}, z: 5, data: isSTVX ? STVX_MAINUP_F : []},")
    _sw.append("      {id: 'stvx-maindn', name: '主轨', type: 'line', xAxisIndex: 0, yAxisIndex: 0, symbolSize: 0, showSymbol: false, lineStyle: {color: '#089981', width: 2}, z: 5, data: isSTVX ? STVX_MAINDN : []},")
    _sw.append("      {id: 'stvx-maindn-f', name: '主轨', type: 'line', xAxisIndex: 0, yAxisIndex: 0, symbolSize: 0, showSymbol: false, lineStyle: {color: '#089981', width: 2, opacity: 0.3}, z: 5, data: isSTVX ? STVX_MAINDN_F : []},")
    _sw.append("      {id: 'stvx-mainflips', name: '主轨', type: 'scatter', xAxisIndex: 0, yAxisIndex: 0, symbol: 'circle', symbolSize: 10, z: 6, label: {show: true, fontSize: 12}, data: isSTVX ? STVX_MAINFLIPS : []},")
    _sw.append("      {id: 'hama-kline', name: '1mK线', type: 'candlestick', xAxisIndex: 0, yAxisIndex: 0, itemStyle: {color: '#1a9e54', color0: '#e5484d', borderColor: '#1a9e54', borderColor0: '#e5484d'}, data: isHAMA ? HAMA_OHLC : [], markArea: {silent: true, data: isHAMA ? HAMA_MARKAREA : []}},")
    for _hk in range(len(hama_line_segs)):
        _sw.append("      {id: 'hama-ls-%d', name: HAMA_LINE_SEGS[%d].name, type: 'line', xAxisIndex: 0, yAxisIndex: 0, symbol: 'none', showSymbol: false, silent: true, lineStyle: {width: HAMA_LINE_SEGS[%d].w, color: HAMA_LINE_SEGS[%d].c}, itemStyle: {color: HAMA_LINE_SEGS[%d].c}, z: 5, data: isHAMA ? HAMA_LINE_SEGS[%d].d : []}," % (_hk, _hk, _hk, _hk, _hk, _hk))
    for _hk in range(len(hama_ssl_fills)):
        _sw.append("      {id: 'hama-sslf-b-%d', name: 'SSL', type: 'line', stack: 'hamaSsl%d', xAxisIndex: 0, yAxisIndex: 0, symbol: 'none', showSymbol: false, silent: true, lineStyle: {width: 0}, data: isHAMA ? HAMA_SSL_FILLS[%d].b : []}," % (_hk, _hk, _hk))
        _sw.append("      {id: 'hama-sslf-d-%d', name: 'SSL', type: 'line', stack: 'hamaSsl%d', xAxisIndex: 0, yAxisIndex: 0, symbol: 'none', showSymbol: false, silent: true, lineStyle: {width: 0}, areaStyle: {color: HAMA_SSL_FILLS[%d].c}, data: isHAMA ? HAMA_SSL_FILLS[%d].d : []}," % (_hk, _hk, _hk, _hk))
    _sw.append("    ], legend: {selected: {'cumslope': false, 'slope5': false, 'st快版': false, 'st慢版': (isENVELOPE || isSTVX || isHAMA) ? false : true, '9mK线': (isENVELOPE || isHAMA) ? false : true, '9m QQE': (isENVELOPE || isHAMA) ? false : true, '3mK线': (isENVELOPE || isHAMA) ? false : true, 'QQE': isENVELOPE ? false : true, '80%衰减线': (isENVELOPE || isHAMA) ? false : true, '翻仓线': (isENVELOPE || isHAMA) ? false : true, '回补门槛': (isENVELOPE || isHAMA) ? false : true}}, xAxis: [{data: isHAMA ? HAMA_TIMES1M : (isENVELOPE ? ENVELOPE_TIMES1M : times)}, {show: isENVELOPE ? false : true, axisPointer: {label: {show: isENVELOPE ? false : true}}, data: isHAMA ? HAMA_TIMES1M : (isENVELOPE ? ENVELOPE_TIMES1M : times)}, {show: isENVELOPE ? false : true, axisPointer: {label: {show: isENVELOPE ? false : true}}, data: isHAMA ? HAMA_TIMES1M : (isENVELOPE ? ENVELOPE_TIMES1M : times)}, {type: isHAMA ? 'category' : 'value', data: isHAMA ? HAMA_TIMES1M : undefined, min: isHAMA ? null : (isENVELOPE ? -1/6 : -0.5), max: isHAMA ? null : (isENVELOPE ? 779/6 : 129.5)}], yAxis: [{min: isENVELOPE ? " + env_min_js + " : null, max: isENVELOPE ? " + env_max_js + " : " + str(y_max_js) + "}, {show: isENVELOPE ? false : true}], grid: [{height: isENVELOPE ? '88%' : ('38%')}, {height: isENVELOPE ? '0%' : '42%'}]")
    _sw.append("  });")
    # Envelope 版隐藏主图 dataZoom 滑条 (2026-11-19 user 令: 省空间;
    # 缩放走鼠标/双指 inside + 子面板联动, 切回其他版本自动恢复)。
    _sw.append("  try { ch.setOption({dataZoom: [{}, {show: !isENVELOPE}]}); } catch(e) {}")
    # ST VX 主轨/影子主轨图例 (2026-11-17): 模板 legend.data 固定, 这里把两项
    # 幂等追加进去 (主轨=绿/红双段图标同 st慢版, 影子主轨=紫虚线图标)。
    _sw.append("  try {")
    _sw.append("    var _lo2 = ch.getOption();")
    _sw.append("    var _ld2 = (_lo2 && _lo2.legend && _lo2.legend[0] && _lo2.legend[0].data) ? _lo2.legend[0].data.slice() : null;")
    _sw.append("    if (_ld2) {")
    _sw.append("      var _hasL2 = function(n){ for (var i=0;i<_ld2.length;i++){ var d=_ld2[i]; if ((typeof d==='string' ? d : d.name)===n) return true; } return false; };")
    _sw.append("      var _chgL2 = false;")
    _sw.append("      if (!_hasL2('主轨')) { _ld2.push({name: '主轨', icon: 'image://data:image/svg+xml;utf8,<svg xmlns=%22http://www.w3.org/2000/svg%22 width=%2224%22 height=%2212%22><line x1=%220%22 y1=%226%22 x2=%2212%22 y2=%226%22 stroke=%22%23089981%22 stroke-width=%222%22/><line x1=%2212%22 y1=%226%22 x2=%2224%22 y2=%226%22 stroke=%22%23f23645%22 stroke-width=%222%22/></svg>'}); _chgL2 = true; }")
    _sw.append("      if (!_hasL2('影子主轨')) { _ld2.push({name: '影子主轨', icon: 'image://data:image/svg+xml;utf8,<svg xmlns=%22http://www.w3.org/2000/svg%22 width=%2224%22 height=%2212%22><line x1=%220%22 y1=%226%22 x2=%2224%22 y2=%226%22 stroke=%22%239333ea%22 stroke-width=%222%22 stroke-dasharray=%224,2%22/></svg>'}); _chgL2 = true; }")
    _sw.append("      if (!_hasL2('HAMA')) { _ld2.push({name: 'HAMA', icon: 'image://data:image/svg+xml;utf8,<svg xmlns=%22http://www.w3.org/2000/svg%22 width=%2224%22 height=%2212%22><line x1=%220%22 y1=%226%22 x2=%2224%22 y2=%226%22 stroke=%22%232fbf2f%22 stroke-width=%223%22/></svg>'}); _chgL2 = true; }")
    _sw.append("      if (!_hasL2('HAMA3')) { _ld2.push({name: 'HAMA3', icon: 'image://data:image/svg+xml;utf8,<svg xmlns=%22http://www.w3.org/2000/svg%22 width=%2224%22 height=%2212%22><line x1=%220%22 y1=%226%22 x2=%2224%22 y2=%226%22 stroke=%22%2366d95e%22 stroke-width=%222%22/></svg>'}); _chgL2 = true; }")
    _sw.append("      if (!_hasL2('SSL')) { _ld2.push({name: 'SSL', icon: 'image://data:image/svg+xml;utf8,<svg xmlns=%22http://www.w3.org/2000/svg%22 width=%2224%22 height=%2212%22><line x1=%220%22 y1=%226%22 x2=%2212%22 y2=%226%22 stroke=%22%23089981%22 stroke-width=%222%22/><line x1=%2212%22 y1=%226%22 x2=%2224%22 y2=%226%22 stroke=%22%23f23645%22 stroke-width=%222%22/></svg>'}); _chgL2 = true; }")
    _sw.append("      if (!_hasL2('1mK线')) { _ld2.push({name: '1mK线', icon: 'image://data:image/svg+xml;utf8,<svg xmlns=%22http://www.w3.org/2000/svg%22 width=%2224%22 height=%2212%22><line x1=%220%22 y1=%226%22 x2=%2224%22 y2=%226%22 stroke=%22%231a9e54%22 stroke-width=%222%22/></svg>'}); _chgL2 = true; }")
    _sw.append("      if (!window._LEG_ALL) window._LEG_ALL = _ld2.slice();")
    _sw.append("      var _lt = isHAMA ? window._LEG_ALL.filter(function(d){ var n = (typeof d==='string' ? d : d.name); return ['1mK线','1m曲线','QQE','HAMA','HAMA3','SSL'].indexOf(n) >= 0; }) : window._LEG_ALL;")
    _sw.append("      if (_lt.length !== _ld2.length || _chgL2) ch.setOption({legend: {data: _lt}});")
    _sw.append("    }")
    _sw.append("  } catch(e) {}")
    _sw.append("  var _chHost = document.getElementById('c');")
    _sw.append("  if (_chHost) { if (isENVELOPE) { _chHost.style.height = ''; if (typeof ch !== 'undefined' && ch) ch.resize(); } else if (window._fitChartH) { window._fitChartH(); } }")
    _sw.append("  var tw = document.getElementById('tblWrap');")
    _sw.append("  if (tw) { tw.innerHTML = isLive ? LIVE_INNER : (isHAMA ? HAMA_INNER : (isENVELOPE ? ENVELOPE_INNER : (isCE ? CE_INNER : (isSTVX ? STVX_INNER : (isVH ? VH_INNER : QQE_INNER))))); }")
    _sw.append("  if (window._werSync) window._werSync(isENVELOPE);")
    _sw.append("  var actsData = isHAMA ? HAMA_ACTS : (isENVELOPE ? ENVELOPE_ACTS : (isCE ? CE_ACTS : (isSTVX ? STVX_ACTS : (useVH ? VH_ACTS : QQE_ACTS))));")
    _sw.append("  for (var t in barInfo) { barInfo[t].acts = actsData[t] || []; }")
    _sw.append("  document.title = TITLES3[v];")
    _sw.append("  if (window._verBtns3) { window._verBtns3.forEach(function(b){ var on = (b.dataset.ver === v); b.style.background = on ? '#2b7fff' : '#fff'; b.style.borderColor = on ? '#2b7fff' : '#ccc'; b.style.color = on ? '#fff' : '#333'; }); }")
    _sw.append("  var _emw = document.getElementById('envModeWrap'); if (_emw) _emw.style.display = isENVELOPE ? '' : 'none';")
    _sw.append("}")
    # ---- Envelope 双版切换 (2026-11-20): 整套换成交类工作变量 +
    # TITLES3 标题, 当前在 Envelope 版时立即重渲染; 按钮选中态同步。
    # 选择经 localStorage('waveEnvMode') 跨刷新保持 (user 2026-11-20
    # 18:36 令: 在 QQE 转向版手动刷新不许跳回默认冠军版); 首次访问
    # 无记录时仍默认冠军。页面本身仍无 meta refresh (既有规矩)。 ----
    _sw.append("window._envMode = 'champion';")
    _sw.append("window.switchEnvMode = function(m){")
    _sw.append("  var S = ENVELOPE_SETS[m]; if (!S) return;")
    _sw.append("  window._envMode = m;")
    _sw.append("  try { localStorage.setItem('waveEnvMode', m); } catch(e) {}")
    _sw.append("  ENVELOPE_PINS = S.PINS; ENVELOPE_SPINS = S.SPINS; ENVELOPE_SCAT_1M = S.SCAT_1M; ENVELOPE_MARKAREA_1M = S.MARKAREA_1M; ENVELOPE_INNER = S.INNER; ENVELOPE_ACTS = S.ACTS; ENVELOPE_TIP = S.TIP; ENVELOPE_SLOPE_DYE = S.SLOPE_DYE; ENVELOPE_QF1M = S.QF1M || [];")
    _sw.append("  if (S.WER) { ENVELOPE_WER = S.WER; window._envWerTitle = S.WTITLE; if (window._werRefresh) window._werRefresh(); }")
    _sw.append("  TITLES3.envelope = S.TITLE; TITLES3E.envelope = S.TITLEE;")
    _sw.append("  if (window._qqeRefresh) window._qqeRefresh();")
    _sw.append("  if (window._envModeBtns) window._envModeBtns.forEach(function(b){ var on = (b.dataset.envmode === m); b.style.background = on ? '#2b7fff' : '#fff'; b.style.borderColor = on ? '#2b7fff' : '#ccc'; b.style.color = on ? '#fff' : '#333'; });")
    _sw.append("  if (typeof curVer3 !== 'undefined' && curVer3 === 'envelope') switchVersion3('envelope');")
    _sw.append("};")
    _sw.append("(function(){")
    _sw.append("  var sel = document.getElementById('daypick');")
    _sw.append("  if (!sel) return;")
    _sw.append("  var wrap = document.createElement('span');")
    _sw.append("  [['live','实盘版'],['vh','ST VH版'],['qqe','QQE版'],['ce','CE版'],['envelope','Envelope版'],['stvx','ST VX版'],['hama','Hama']].forEach(function(it){")
    _sw.append("    var b = document.createElement('button');")
    _sw.append("    b.type = 'button'; b.textContent = it[1]; b.dataset.ver = it[0];")
    _sw.append("    b.style.fontSize = '13px'; b.style.padding = '2px 8px'; b.style.marginLeft = '6px';")
    _sw.append("    b.style.borderRadius = '10px'; b.style.border = '1px solid #ccc'; b.style.background = '#fff';")
    _sw.append("    b.style.color = '#333'; b.style.cursor = 'pointer';")
    _sw.append("    b.onclick = function(){ switchVersion3(it[0]); };")
    _sw.append("    wrap.appendChild(b);")
    _sw.append("  });")
    _sw.append("  sel.parentNode.insertBefore(wrap, sel.nextSibling);")
    _sw.append("  window._verBtns3 = wrap.querySelectorAll('button');")
    # Envelope 双版切换按钮 (版本排内, 仅 Envelope 版时显示;
    # _verBtns3 已先行快照, 不含这两个按钮, 免被版本按钮刷色覆盖)
    _sw.append("  var emw = document.createElement('span');")
    _sw.append("  emw.id = 'envModeWrap'; emw.style.display = 'none';")
    _sw.append("  [['champion','WER冠军'],['f1','反手+QQE'],['imb','WER失衡版']].forEach(function(it){")
    _sw.append("    var b = document.createElement('button');")
    _sw.append("    b.type = 'button'; b.textContent = it[1]; b.dataset.envmode = it[0];")
    _sw.append("    b.style.fontSize = '13px'; b.style.padding = '2px 8px'; b.style.marginLeft = '6px';")
    _sw.append("    b.style.borderRadius = '10px'; b.style.border = '1px solid #ccc'; b.style.cursor = 'pointer';")
    _sw.append("    var on0 = (it[0] === 'champion'); b.style.background = on0 ? '#2b7fff' : '#fff'; b.style.borderColor = on0 ? '#2b7fff' : '#ccc'; b.style.color = on0 ? '#fff' : '#333';")
    _sw.append("    b.onclick = function(){ switchEnvMode(it[0]); };")
    _sw.append("    emw.appendChild(b);")
    _sw.append("  });")
    _sw.append("  wrap.appendChild(emw);")
    _sw.append("  window._envModeBtns = emw.querySelectorAll('button');")
    # 刷新保版恢复 (user 2026-11-20 18:36 令): 读上次选择, 非冠军才
    # 切 (冠军即默认值、无记录时不动); 须在 _envModeBtns 就绪后调,
    # switchEnvMode 内的按钮刷色依赖它。此刻 curVer3 还是 'live',
    # 版本恢复行 (脚本末尾 switchVersion3(saved)) 随后按已换好的
    # 工作变量渲染 Envelope 版, 顺序无冲突。
    _sw.append("  var _savedEnvMode = null; try { _savedEnvMode = localStorage.getItem('waveEnvMode'); } catch(e) {}")
    _sw.append("  if (_savedEnvMode && _savedEnvMode !== 'champion' && typeof ENVELOPE_SETS !== 'undefined' && ENVELOPE_SETS[_savedEnvMode]) switchEnvMode(_savedEnvMode);")
    _sw.append("  if (!sel.querySelector('option[value=\"summary.html\"]')) { var so = document.createElement('option'); so.value = 'summary.html'; so.textContent = '📊 回测版本总览'; sel.insertBefore(so, sel.options[0]); }")
    _sw.append("  if (!sel.querySelector('option[value=\"compound.html\"]')) { var co = document.createElement('option'); co.value = 'compound.html'; co.textContent = '🧮 复利模拟'; sel.insertBefore(co, sel.options[0]); }")
    _sw.append("})();")
    # ---- WER 面板 (Envelope 版股票图下方; 仅 envelope 时显示) ----
    _sw.append("(function(){")
    _sw.append("  var host = document.getElementById('c');")
    _sw.append("  if (!host || typeof ENVELOPE_WER === 'undefined' || !ENVELOPE_WER.wer.length) return;")
    _sw.append("  var wrap = document.createElement('div');")
    _sw.append("  wrap.id = 'werPanel';")
    _sw.append("  wrap.style.cssText = 'max-width:1150px;margin:0 auto;padding:0 8px;display:none';")
    _wer_title = ("WER · WER冠军/反手+QQE 共用（引擎口径）" if _env_int_ok
                  else "WER · WER冠军/反手+QQE（计算失败）")
    _sw.append("  wrap.innerHTML = '<div id=\"werTitle\" style=\"font-size:13px;font-weight:bold;margin:2px 0 0\">" + _wer_title + "</div><div id=\"werCh\" style=\"width:100%;height:150px\"></div>';")
    _sw.append("  host.parentNode.insertBefore(wrap, host.nextSibling);")
    _sw.append("  var werCh = null, werBound = false, werBoundMain = null, werZoomLock = false, werTipLock = false, werMainSIdx = -1;")
    # ---- 1m QQE 面板 (2026-11-19 user 令): 股票图与 WER 图之间,
    # 高度同 WER 图 150px; 数据 ENVELOPE_QQE1M (gen 侧同暖帧 QQE
    # MOD 全帧算、当日 390 槽对齐); 联动与 WER/slope 面板同套手动
    # 同步 (见 werBindQqe)。
    _sw.append("  var qqeCh = null, qqeBound = false, qqeBoundMain = null, qwrap = null;")
    _sw.append("  if (typeof ENVELOPE_QQE1M !== 'undefined' && ENVELOPE_QQE1M.hist && ENVELOPE_QQE1M.hist.length) {")
    _sw.append("    qwrap = document.createElement('div');")
    _sw.append("    qwrap.id = 'qqePanel';")
    _sw.append("    qwrap.style.cssText = 'max-width:1150px;margin:0 auto;padding:0 8px;display:none';")
    _sw.append("    qwrap.innerHTML = '<div style=\\\"font-size:13px;font-weight:bold;margin:2px 0 0\\\">QQE · 1m（QQE MOD 口径）</div><div id=\\\"qqe1mCh\\\" style=\\\"width:100%;height:150px\\\"></div>';")
    _sw.append("    host.parentNode.insertBefore(qwrap, wrap);")
    _sw.append("  }")
    # ---- slope/dev 面板 (2026-11-17 user 令): WER 图下方, 样式照突破页
    # slope/dev 阈值面板 (线 + 带标签阈值虚线); 数据 ENVELOPE_SLPDEV
    # (envwer_page 构建, slope7=引擎 ind slope, dev=引擎 _slope_segments
    # 同式, 阈值构建侧注入)。联动与 WER 面板同套手动同步 (见 werBindSlp)。
    _sw.append("  var slpCh = null, slpBound = false, slpBoundMain = null, swrap = null;")
    _sw.append("  if (typeof ENVELOPE_SLPDEV !== 'undefined' && ENVELOPE_SLPDEV.slope && ENVELOPE_SLPDEV.slope.length) {")
    _sw.append("    swrap = document.createElement('div');")
    _sw.append("    swrap.id = 'slpPanel';")
    _sw.append("    swrap.style.cssText = 'max-width:1150px;margin:0 auto;padding:0 8px;display:none';")
    _sw.append("    swrap.innerHTML = '<div style=\"font-size:13px;font-weight:bold;margin:2px 0 0\">slope / dev · 阈值（引擎口径）</div><div id=\"slpCh\" style=\"width:100%;height:150px\"></div>';")
    _sw.append("    wrap.parentNode.insertBefore(swrap, wrap.nextSibling);")
    _sw.append("  }")
    _sw.append("  var evIn = {}, evOut = {};")
    _sw.append("  ENVELOPE_WER.entries.forEach(function(e){ evIn[ENVELOPE_TIMES1M[e[0]]] = e[2]; });")
    _sw.append("  ENVELOPE_WER.exits.forEach(function(e){ evOut[ENVELOPE_TIMES1M[e[0]]] = 1; });")
    _sw.append("  function qqeOpt(){")
    _sw.append("    var Q = ENVELOPE_QQE1M;")
    _sw.append("    var data = Q.hist.map(function(v, i){ return v == null ? null : {value: v, itemStyle: {color: Q.col[i]}}; });")
    # qf 转向信号圈 (user 2026-11-20 18:36 令改版): 落在确认根的
    # QQE 柱位置, 突破页 ⑧ panelOptQqeSig 同款 —— 圆 13px、圈内
    # 「翻c/翻p」白字 7px 加粗、橙边 #ff8c00 宽 2; 填充走日页规范色
    # 正绿/正红 (与主图 qf 圈一致)。数据 ENVELOPE_QF1M 随 Envelope
    # 双版切换 (双版同值: 冠军版只展示不参与策略, 2026-11-20
    # 19:41 令), switchEnvMode 后经 _qqeRefresh 重渲。
    _sw.append("    var qfByIdx = {}; (typeof ENVELOPE_QF1M !== 'undefined' ? ENVELOPE_QF1M : []).forEach(function(m){ qfByIdx[m[0]] = m[1]; });")
    # QF1M 圈的橙边/透明度引用 THEME.pinEdge.qf (user 2026-11-20 19:15
    # 令: 特殊圈不透明度 80%); 下行字面对象仅作 theme 缺失时兜底
    # (同 slpdev TD 先例, 值同 theme)。边宽 2 维持本面定版。
    _sw.append("    var QFE = (typeof THEME !== 'undefined' && THEME.pinEdge && THEME.pinEdge.qf) ? THEME.pinEdge.qf : {edge: '#ff8c00', opacity: 0.8};")
    _sw.append("    var qfPts = Object.keys(qfByIdx).map(function(k){ var idx = Number(k), d = qfByIdx[k]; if (Q.hist[idx] == null) return null; var lab = d > 0 ? '翻c' : '翻p'; return {coord: [ENVELOPE_TIMES1M[idx], Q.hist[idx]], value: lab, label: {show: true, formatter: lab, color: '#fff', fontSize: 7, fontWeight: 'bold'}, itemStyle: {color: d > 0 ? '#00cc00' : '#ff0000', borderColor: QFE.edge, borderWidth: 2, opacity: QFE.opacity}}; }).filter(function(p){ return p; });")
    # qf 转向箭头同画在 QQE 面板 (user 2026-11-20 19:41 令): 借主图
    # 块箭头同款 (path/色同 envwer_page 的 QF_ARROW_PATH /
    # QF_ARROW_CALL / QF_ARROW_PUT: call 深青蓝 #0e7490 指上、put
    # 深红 #b91c1c 指下, 实心不透明), 落确认根, 位置在柱尖之外再
    # 偏 QFA_OFF=9 个 QQE 单位 (柱值为正则向外向上、为负向下),
    # 与信号圈同根并存。数据同走 ENVELOPE_QF1M, 双版都有 (冠军版
    # 只展示不参与策略)。
    _sw.append("    var QFA_PATH = 'path://M50 2 L98 48 L66 48 L66 98 L34 98 L34 48 L2 48 Z';")
    _sw.append("    var QFA_OFF = 9;")
    _sw.append("    var qfArrows = Object.keys(qfByIdx).map(function(k){ var idx = Number(k), d = qfByIdx[k]; var h = Q.hist[idx]; if (h == null) return null; var y = h >= 0 ? h + QFA_OFF : h - QFA_OFF; return {coord: [ENVELOPE_TIMES1M[idx], y], value: d > 0 ? 'qfFlipC' : 'qfFlipP', symbol: QFA_PATH, symbolSize: 20, symbolRotate: d > 0 ? 0 : 180, label: {show: false}, itemStyle: {color: d > 0 ? '#0e7490' : '#b91c1c', opacity: 1}}; }).filter(function(p){ return p; });")
    _sw.append("    var qfPtsAll = qfPts.concat(qfArrows);")
    _sw.append("    var ST = {'#2f80ed': 'QQE Up（真蓝）', '#e5484d': 'QQE Down（真红）', '#b0b0b0': '中性（|hist|>3 无主信号确认）', '#e0e0e0': '|hist| ≤ 3'};")
    _sw.append("    return {animation: false,")
    _sw.append("      tooltip: {trigger: 'axis', backgroundColor: 'rgba(255,255,255,0.85)', textStyle: {color: '#333', fontSize: 12}, axisPointer: {type: 'line', lineStyle: {color: '#999', type: 'dashed', width: 1}}, formatter: function(ps){ if(!ps || !ps.length) return ''; var t = ps[0].axisValue; var idx = ENVELOPE_TIMES1M.indexOf(t); if (idx < 0 || Q.hist[idx] == null) return ''; var h = t + '<br/>QQE(1m) = <b>' + Number(Q.hist[idx]).toFixed(2) + '</b><br/>' + (ST[Q.col[idx]] || ''); if (qfByIdx[idx]) h += '<br/><b style=\"color:#ff8c00\">QQE转向' + (qfByIdx[idx] > 0 ? '买入' : '卖出') + '</b>（确认根）'; return h; }},")
    _sw.append("      grid: {left: 40, right: 12, top: 12, bottom: 24},")
    _sw.append("      dataZoom: [{type: 'inside', xAxisIndex: 0}],")
    _sw.append("      xAxis: {type: 'category', data: ENVELOPE_TIMES1M, boundaryGap: false, axisLabel: {fontSize: 10}},")
    _sw.append("      yAxis: {type: 'value', axisLabel: {fontSize: 10}, splitLine: {lineStyle: {color: '#eef1f4'}}},")
    _sw.append("      series: [")
    _sw.append("        {name: 'QQE', type: 'bar', data: data, barWidth: '60%', markPoint: {symbol: 'circle', symbolSize: 13, label: {show: true, color: '#fff', fontSize: 7, fontWeight: 'bold'}, data: qfPtsAll}, markLine: {silent: true, symbol: 'none', lineStyle: {color: '#9ca3af', type: 'dashed', width: 1}, label: {color: '#9ca3af', fontSize: 10, position: 'insideEndTop'}, data: [{yAxis: 3, label: {formatter: '+3'}}, {yAxis: -3, label: {formatter: '-3'}}, {yAxis: 0, lineStyle: {color: '#d1d5db', type: 'solid'}, label: {show: false}}]}}")
    _sw.append("      ]};")
    _sw.append("  }")
    _sw.append("  function werOpt(){")
    _sw.append("    var areas = ENVELOPE_WER.areas.map(function(a){ return [{xAxis: ENVELOPE_TIMES1M[a[0]], itemStyle: {color: a[2] > 0 ? 'rgba(26,158,84,0.10)' : 'rgba(229,72,77,0.10)'}}, {xAxis: ENVELOPE_TIMES1M[a[1]]}]; });")
    _sw.append("    var pts = ENVELOPE_WER.entries.map(function(e){ return {coord: [ENVELOPE_TIMES1M[e[0]], e[1]], itemStyle: {color: '#7c3aed', borderColor: '#fff', borderWidth: 1.5}}; })")
    _sw.append("      .concat(ENVELOPE_WER.exits.map(function(e){ return {coord: [ENVELOPE_TIMES1M[e[0]], e[1]], itemStyle: {color: '#6b7280', borderColor: '#fff', borderWidth: 1.5}}; }));")
    _sw.append("    return {animation: false,")
    _sw.append("      tooltip: {trigger: 'axis', backgroundColor: 'rgba(255,255,255,0.85)', textStyle: {color: '#333', fontSize: 12}, axisPointer: {type: 'line', lineStyle: {color: '#999', type: 'dashed', width: 1}}, formatter: function(ps){ if(!ps || !ps.length) return ''; var t = ps[0].axisValue; var idx = ENVELOPE_TIMES1M.indexOf(t); if (idx < 0 || ENVELOPE_WER.wer[idx] == null) return ''; var h = t + '<br/>WER = <b>' + Number(ENVELOPE_WER.wer[idx]).toFixed(4) + '</b><br/>有效门槛 eff_to = <b>' + Number(ENVELOPE_WER.eff[idx]).toFixed(3) + '</b>'; if (evIn[t]) h += '<br/><b style=\"color:#7c3aed\">段入场</b>（' + (evIn[t] > 0 ? '上行' : '下行') + '）'; if (evOut[t]) h += '<br/><b style=\"color:#6b7280\">段出场</b>'; return h; }},")
    _sw.append("      grid: {left: 40, right: 12, top: 12, bottom: 24},")
    _sw.append("      dataZoom: [{type: 'inside', xAxisIndex: 0}],")
    _sw.append("      xAxis: {type: 'category', data: ENVELOPE_TIMES1M, boundaryGap: false, axisLabel: {fontSize: 10}},")
    _sw.append("      yAxis: {type: 'value', min: 0, max: 1.25, name: 'WER', nameTextStyle: {fontSize: 11, color: '#7c3aed'}, axisLabel: {fontSize: 10}, splitLine: {lineStyle: {color: '#eef1f4'}}},")
    _sw.append("      series: [")
    _sw.append("        {name: 'WER', type: 'line', data: ENVELOPE_WER.wer, showSymbol: false, lineStyle: {width: 1.8, color: '#7c3aed'}, itemStyle: {color: '#7c3aed'}, markLine: {silent: true, symbol: 'none', lineStyle: {color: '#9ca3af', type: 'dashed', width: 1}, label: {color: '#9ca3af', fontSize: 10, position: 'insideEndTop'}, data: [{yAxis: 0.675, label: {formatter: '0.675'}}, {yAxis: 0.5, label: {formatter: '0.50'}}, {yAxis: 0.3, label: {formatter: '0.30'}}]}, markArea: {silent: true, data: areas}, markPoint: {symbol: 'circle', symbolSize: 8, label: {show: false}, data: pts}},")
    _sw.append("        {name: '有效门槛', type: 'line', data: ENVELOPE_WER.eff, showSymbol: false, step: 'end', lineStyle: {width: 1.5, color: '#f97316', type: 'dashed'}, itemStyle: {color: '#f97316'}}")
    _sw.append("      ]};")
    _sw.append("  }")
    _sw.append("  function slpOpt(){")
    _sw.append("    var S = ENVELOPE_SLPDEV;")
    # 样式常量统一走 theme (user 2026-11-17 17:34 令): 三色/背景透明度/
    # 纵轴/阈值/线型全定义在 theme.js 的 THEME.slpdev, 本函数只引用
    # TD.*; 下行字面对象仅作 theme 缺失时兜底 (同 pinSpec 先例, 值同
    # theme)。纵轴固定 ±TD.yMax (17:22 令): 原按 |slope/dev| 99.5 分
    # 位算 mx, 开盘 slope 冲 −0.76 (2026-11-16) 时 mx 被撑到 0.76、
    # 全图压扁看不清; 写死后不再随数据变, 超界线由 ECharts 按边界裁切。
    _sw.append("    var TD = (typeof THEME !== 'undefined' && THEME.slpdev) ? THEME.slpdev : {colSlope: '#38bdf8', colDev: '#ff6700', colBoth: '#c026d3', areaAlpha: 0.12, yMax: 0.35, thUp: 0.05, thDn: 0.07, thDev: 0.079, slopeLine: '#2563eb', slopeLineWidth: 2, slopeLineType: 'dashed', devLineWidth: 1.5, devLineType: 'dotted', zeroLine: '#d1d5db'};")
    _sw.append("    var TH = {up: TD.thUp, dn: TD.thDn, dev: TD.thDev};")
    _sw.append("    var mx = TD.yMax;")
    _sw.append("    function s3(v){ return v == null ? '—' : (v >= 0 ? '+' : '') + Number(v).toFixed(3); }")
    _sw.append("    function relTxt(v, thPos, thNeg){")
    _sw.append("      if (v == null) return '';")
    _sw.append("      if (v >= 0) return v >= thPos ? ' ≥ +' + thPos.toFixed(3) + ' 已达标' : ' < +' + thPos.toFixed(3) + ' 未达';")
    _sw.append("      return v <= thNeg ? ' ≤ ' + thNeg.toFixed(3) + ' 已达标' : ' > ' + thNeg.toFixed(3) + ' 未达';")
    _sw.append("    }")
    _sw.append("    function rgba(hex, a){ var n = parseInt(hex.slice(1), 16); return 'rgba(' + ((n >> 16) & 255) + ',' + ((n >> 8) & 255) + ',' + (n & 255) + ',' + a + ')'; }")
    _sw.append("    // 过阈值区间背景染色 (user 2026-11-17 17:32 令, 取代 17:01 的打点):")
    _sw.append("    // 每根按 slope 过阈 / dev 过阈分类 (1=slope、2=dev、3=双过), 连续")
    _sw.append("    // 同类根合并成一段 markArea, 背景染对应 theme 色 (TD.col*) +")
    _sw.append("    // TD.areaAlpha 低透明; 单根段向后延一根保宽度 (末根向前延),")
    _sw.append("    // 免零宽看不见。")
    _sw.append("    var AREA_COL = {1: rgba(TD.colSlope, TD.areaAlpha), 2: rgba(TD.colDev, TD.areaAlpha), 3: rgba(TD.colBoth, TD.areaAlpha)};")
    _sw.append("    var areas = [], cls = [];")
    _sw.append("    for (var i = 0; i < S.slope.length; i++) {")
    _sw.append("      var sv2 = S.slope[i], dv2 = S.dev[i];")
    _sw.append("      var sHit = sv2 != null && (sv2 >= TH.up || sv2 <= -TH.dn);")
    _sw.append("      var dHit = dv2 != null && (dv2 >= TH.dev || dv2 <= -TH.dev);")
    _sw.append("      cls.push(sHit ? (dHit ? 3 : 1) : (dHit ? 2 : 0));")
    _sw.append("    }")
    _sw.append("    for (var j = 0; j < cls.length; j++) {")
    _sw.append("      if (!cls[j]) continue;")
    _sw.append("      var k = j;")
    _sw.append("      while (k + 1 < cls.length && cls[k + 1] === cls[j]) k++;")
    _sw.append("      var s2 = j, e2 = k;")
    _sw.append("      if (k === j) { if (k + 1 < cls.length) e2 = k + 1; else if (j > 0) s2 = j - 1; }")
    _sw.append("      areas.push([{xAxis: ENVELOPE_TIMES1M[s2], itemStyle: {color: AREA_COL[cls[j]]}}, {xAxis: ENVELOPE_TIMES1M[e2]}]);")
    _sw.append("      j = k;")
    _sw.append("    }")
    _sw.append("    return {animation: false,")
    _sw.append("      tooltip: {trigger: 'axis', backgroundColor: 'rgba(255,255,255,0.85)', textStyle: {color: '#333', fontSize: 12}, axisPointer: {type: 'line', lineStyle: {color: '#999', type: 'dashed', width: 1}}, formatter: function(ps){ if(!ps || !ps.length) return ''; var t = ps[0].axisValue; var idx = ENVELOPE_TIMES1M.indexOf(t); if (idx < 0) return ''; var sv = S.slope[idx], dv = S.dev[idx]; if (sv == null && dv == null) return ''; return t + '<br/><span style=\"color:' + TD.slopeLine + '\">slope7 = <b>' + s3(sv) + '</b>' + relTxt(sv, TH.up, -TH.dn) + '</span><br/><span style=\"color:' + TD.colDev + '\">dev = <b>' + s3(dv) + '</b>' + relTxt(dv, TH.dev, -TH.dev) + '</span>'; }},")
    _sw.append("      grid: {left: 40, right: 12, top: 12, bottom: 24},")
    _sw.append("      dataZoom: [{type: 'inside', xAxisIndex: 0}],")
    _sw.append("      xAxis: {type: 'category', data: ENVELOPE_TIMES1M, boundaryGap: false, axisLabel: {fontSize: 10}},")
    _sw.append("      yAxis: {type: 'value', min: -mx, max: mx, name: 'slope / dev', nameTextStyle: {fontSize: 11, color: '#374151'}, axisLabel: {fontSize: 10}, splitLine: {lineStyle: {color: '#eef1f4'}}},")
    _sw.append("      series: [")
    # 负阈值线可见性 (user 2026-11-17 17:01 指出 slope −0.070 看不到):
    # slope −dn 与 dev −dev 只差 0.009, 今天 mx=0.71 时像素距仅 0.65px,
    # 两线实际重合且同端 (右) 标签互盖、后画的 dev 线盖住 slope 线。
    # 修法: dev 阈值线改点线 (dotted, 与 slope 的 dashed 纹理分开) +
    # dev 两标签移到左端 insideStartTop, 与 slope 的右端标签分开。
    _sw.append("        {name: 'slope7', type: 'line', data: S.slope, showSymbol: false, lineStyle: {width: TD.slopeLineWidth, color: TD.slopeLine}, itemStyle: {color: TD.slopeLine}, markLine: {silent: true, symbol: 'none', data: [{yAxis: TH.up, lineStyle: {color: TD.slopeLine, type: TD.slopeLineType, width: 1}, label: {formatter: 'slope +' + TH.up.toFixed(3), color: TD.slopeLine, fontSize: 10, position: 'insideEndTop'}}, {yAxis: -TH.dn, lineStyle: {color: TD.slopeLine, type: TD.slopeLineType, width: 1}, label: {formatter: 'slope −' + TH.dn.toFixed(3), color: TD.slopeLine, fontSize: 10, position: 'insideEndTop'}}, {yAxis: 0, lineStyle: {color: TD.zeroLine, type: 'solid', width: 1}, label: {show: false}}]}, markArea: {silent: true, label: {show: false}, data: areas}},")
    _sw.append("        {name: 'dev', type: 'line', data: S.dev, showSymbol: false, lineStyle: {width: TD.devLineWidth, color: TD.colDev}, itemStyle: {color: TD.colDev}, markLine: {silent: true, symbol: 'none', data: [{yAxis: TH.dev, lineStyle: {color: TD.colDev, type: TD.devLineType, width: 1}, label: {formatter: 'dev +' + TH.dev.toFixed(3), color: TD.colDev, fontSize: 10, position: 'insideStartTop'}}, {yAxis: -TH.dev, lineStyle: {color: TD.colDev, type: TD.devLineType, width: 1}, label: {formatter: 'dev −' + TH.dev.toFixed(3), color: TD.colDev, fontSize: 10, position: 'insideStartTop'}}]}}")
    _sw.append("      ]};")
    _sw.append("  }")
    _sw.append("  // 手动同步主图<->WER面板 (2026-11-11: 弃 echarts.connect —— 悬停 showTip 带 escapeConnect 设计上不转发,")
    _sw.append("  // 且 connect 转发的 updateAxisPointer 靠源图样本 seriesIndex+dataIndex 在对侧反解, 主图 series0 仅130点, 索引>=130 必失效)")
    _sw.append("  function werMain(){")
    _sw.append("    try {")
    _sw.append("      var m = (typeof ch !== 'undefined' && ch) ? ch : (window.ch || null);")
    _sw.append("      if (!m || (m.isDisposed && m.isDisposed())) return null;")
    _sw.append("      return m;")
    _sw.append("    } catch(e) { return null; }")
    _sw.append("  }")
    _sw.append("  function werFindMainSIdx(){")
    _sw.append("    var main = werMain();")
    _sw.append("    werMainSIdx = -1;")
    _sw.append("    if (!main) return;")
    _sw.append("    try {")
    _sw.append("      var ss = main.getOption().series || [];")
    _sw.append("      var i;")
    _sw.append("      for (i = 0; i < ss.length; i++) { if (ss[i].id === 'env-upper') { werMainSIdx = i; return; } }")
    _sw.append("      for (i = 0; i < ss.length; i++) {")
    _sw.append("        if (ss[i].type === 'line' && (ss[i].xAxisIndex || 0) === 0 && ss[i].data && ss[i].data.length === ENVELOPE_TIMES1M.length) { werMainSIdx = i; return; }")
    _sw.append("      }")
    _sw.append("    } catch(e) {}")
    _sw.append("  }")
    _sw.append("  function werTipIdx(ev, fromMain){")
    _sw.append("    if (!ev) return -1;")
    _sw.append("    if (ev.axesInfo) {")
    _sw.append("      for (var i = 0; i < ev.axesInfo.length; i++) {")
    _sw.append("        var ai = ev.axesInfo[i];")
    _sw.append("        if (ai.axisDim === 'x' && ai.axisIndex === 0 && ai.value != null) {")
    _sw.append("          var v = (typeof ai.value === 'number') ? ai.value : ENVELOPE_TIMES1M.indexOf(ai.value);")
    _sw.append("          if (v >= 0 && v < ENVELOPE_TIMES1M.length) return v;")
    _sw.append("        }")
    _sw.append("      }")
    _sw.append("    }")
    _sw.append("    if (!fromMain && typeof ev.dataIndex === 'number' && ev.dataIndex >= 0 && ev.dataIndex < ENVELOPE_TIMES1M.length) return ev.dataIndex;")
    _sw.append("    return -1;")
    _sw.append("  }")
    _sw.append("  function werShowTip(dst, sIdx, idx){")
    _sw.append("    if (werTipLock || sIdx < 0) return;")
    _sw.append("    werTipLock = true;")
    _sw.append("    try { dst.dispatchAction({type: 'showTip', seriesIndex: sIdx, dataIndex: idx}); } catch(e) {}")
    _sw.append("    werTipLock = false;")
    _sw.append("  }")
    _sw.append("  function werHideTip(dst){")
    _sw.append("    if (werTipLock) return;")
    _sw.append("    werTipLock = true;")
    _sw.append("    try { dst.dispatchAction({type: 'updateAxisPointer', currTrigger: 'leave'}); } catch(e) {}")
    _sw.append("    try { dst.dispatchAction({type: 'hideTip'}); } catch(e) {}")
    _sw.append("    werTipLock = false;")
    _sw.append("  }")
    _sw.append("  function werZoomTo(dst, ev){")
    _sw.append("    if (werZoomLock) return;")
    _sw.append("    var z = (ev && ev.batch && ev.batch.length) ? ev.batch[0] : ev;")
    _sw.append("    if (!z) return;")
    _sw.append("    werZoomLock = true;")
    _sw.append("    try {")
    _sw.append("      if (z.start != null && z.end != null) dst.dispatchAction({type: 'dataZoom', start: z.start, end: z.end});")
    _sw.append("      else if (z.startValue != null && z.endValue != null) dst.dispatchAction({type: 'dataZoom', startValue: z.startValue, endValue: z.endValue});")
    _sw.append("    } catch(e) {}")
    _sw.append("    werZoomLock = false;")
    _sw.append("  }")
    _sw.append("  function werBind(){")
    _sw.append("    if (!werCh || typeof echarts === 'undefined') return;")
    _sw.append("    var main = werMain();")
    _sw.append("    if (!main) return;")
    _sw.append("    werFindMainSIdx();")
    _sw.append("    if (werBound && werBoundMain === main) return;")
    _sw.append("    werBound = true;")
    _sw.append("    werBoundMain = main;")
    _sw.append("    main.on('datazoom', function(ev){ if (wrap.style.display !== 'none') werZoomTo(werCh, ev); });")
    _sw.append("    werCh.on('datazoom', function(ev){ werZoomTo(main, ev); });")
    _sw.append("    main.on('updateAxisPointer', function(ev){")
    _sw.append("      if (wrap.style.display === 'none') return;")
    _sw.append("      var idx = werTipIdx(ev, true);")
    _sw.append("      if (idx >= 0) werShowTip(werCh, 0, idx); else werHideTip(werCh);")
    _sw.append("    });")
    _sw.append("    werCh.on('updateAxisPointer', function(ev){")
    _sw.append("      var idx = werTipIdx(ev, false);")
    _sw.append("      if (idx >= 0) { if (werMainSIdx >= 0) werShowTip(main, werMainSIdx, idx); } else werHideTip(main);")
    _sw.append("    });")
    _sw.append("    main.on('globalout', function(){ werHideTip(werCh); });")
    _sw.append("    werCh.on('globalout', function(){ werHideTip(main); });")
    _sw.append("    main.on('mouseout', function(){ werHideTip(werCh); });")
    _sw.append("    werCh.on('mouseout', function(){ werHideTip(main); });")
    _sw.append("  }")
    _sw.append("  function werSyncZoom(){")
    _sw.append("    var main = werMain();")
    _sw.append("    if (!main) return;")
    _sw.append("    try {")
    _sw.append("      var dz = (main.getOption().dataZoom || [])[0];")
    _sw.append("      if (dz && dz.start != null && dz.end != null && (dz.start > 0 || dz.end < 100)) {")
    _sw.append("        werZoomLock = true;")
    _sw.append("        if (werCh) werCh.dispatchAction({type: 'dataZoom', start: dz.start, end: dz.end});")
    _sw.append("        if (qqeCh) qqeCh.dispatchAction({type: 'dataZoom', start: dz.start, end: dz.end});")
    _sw.append("        if (slpCh) slpCh.dispatchAction({type: 'dataZoom', start: dz.start, end: dz.end});")
    _sw.append("        werZoomLock = false;")
    _sw.append("      }")
    _sw.append("    } catch(e) { werZoomLock = false; }")
    _sw.append("  }")
    _sw.append("  // slope/dev 图与主图/WER 图三方联动: 与 werBind 同套手动同步")
    _sw.append("  // (datazoom 转发 + updateAxisPointer->showTip, 共用防环锁)。")
    _sw.append("  function werBindSlp(){")
    _sw.append("    if (!slpCh || typeof echarts === 'undefined') return;")
    _sw.append("    var main = werMain();")
    _sw.append("    if (!main) return;")
    _sw.append("    if (werMainSIdx < 0) werFindMainSIdx();")
    _sw.append("    if (slpBound && slpBoundMain === main) return;")
    _sw.append("    slpBound = true;")
    _sw.append("    slpBoundMain = main;")
    _sw.append("    main.on('datazoom', function(ev){ if (swrap.style.display !== 'none') werZoomTo(slpCh, ev); });")
    _sw.append("    slpCh.on('datazoom', function(ev){ werZoomTo(main, ev); if (werCh) werZoomTo(werCh, ev); });")
    _sw.append("    if (werCh) werCh.on('datazoom', function(ev){ werZoomTo(slpCh, ev); });")
    _sw.append("    main.on('updateAxisPointer', function(ev){")
    _sw.append("      if (swrap.style.display === 'none') return;")
    _sw.append("      var idx = werTipIdx(ev, true);")
    _sw.append("      if (idx >= 0) werShowTip(slpCh, 0, idx); else werHideTip(slpCh);")
    _sw.append("    });")
    _sw.append("    slpCh.on('updateAxisPointer', function(ev){")
    _sw.append("      var idx = werTipIdx(ev, false);")
    _sw.append("      if (idx >= 0) { if (werMainSIdx >= 0) werShowTip(main, werMainSIdx, idx); if (werCh) werShowTip(werCh, 0, idx); } else { werHideTip(main); if (werCh) werHideTip(werCh); }")
    _sw.append("    });")
    _sw.append("    if (werCh) werCh.on('updateAxisPointer', function(ev){")
    _sw.append("      var idx = werTipIdx(ev, false);")
    _sw.append("      if (idx >= 0) werShowTip(slpCh, 0, idx); else werHideTip(slpCh);")
    _sw.append("    });")
    _sw.append("    main.on('globalout', function(){ werHideTip(slpCh); });")
    _sw.append("    slpCh.on('globalout', function(){ werHideTip(main); if (werCh) werHideTip(werCh); });")
    _sw.append("    main.on('mouseout', function(){ werHideTip(slpCh); });")
    _sw.append("    slpCh.on('mouseout', function(){ werHideTip(main); if (werCh) werHideTip(werCh); });")
    _sw.append("    if (werCh) werCh.on('globalout', function(){ werHideTip(slpCh); });")
    _sw.append("    if (werCh) werCh.on('mouseout', function(){ werHideTip(slpCh); });")
    _sw.append("  }")
    _sw.append("  // 1m QQE 图与主图/WER/slope 图四方联动: 与 werBindSlp 同套")
    _sw.append("  // 手动同步 (datazoom 转发 + updateAxisPointer->showTip)。")
    _sw.append("  function werBindQqe(){")
    _sw.append("    if (!qqeCh || typeof echarts === 'undefined') return;")
    _sw.append("    var main = werMain();")
    _sw.append("    if (!main) return;")
    _sw.append("    if (werMainSIdx < 0) werFindMainSIdx();")
    _sw.append("    if (qqeBound && qqeBoundMain === main) return;")
    _sw.append("    qqeBound = true;")
    _sw.append("    qqeBoundMain = main;")
    _sw.append("    main.on('datazoom', function(ev){ if (qwrap.style.display !== 'none') werZoomTo(qqeCh, ev); });")
    _sw.append("    qqeCh.on('datazoom', function(ev){ werZoomTo(main, ev); if (werCh) werZoomTo(werCh, ev); if (slpCh) werZoomTo(slpCh, ev); });")
    _sw.append("    if (werCh) werCh.on('datazoom', function(ev){ werZoomTo(qqeCh, ev); });")
    _sw.append("    if (slpCh) slpCh.on('datazoom', function(ev){ werZoomTo(qqeCh, ev); });")
    _sw.append("    main.on('updateAxisPointer', function(ev){")
    _sw.append("      if (qwrap.style.display === 'none') return;")
    _sw.append("      var idx = werTipIdx(ev, true);")
    _sw.append("      if (idx >= 0) werShowTip(qqeCh, 0, idx); else werHideTip(qqeCh);")
    _sw.append("    });")
    _sw.append("    qqeCh.on('updateAxisPointer', function(ev){")
    _sw.append("      var idx = werTipIdx(ev, false);")
    _sw.append("      if (idx >= 0) { if (werMainSIdx >= 0) werShowTip(main, werMainSIdx, idx); if (werCh) werShowTip(werCh, 0, idx); if (slpCh) werShowTip(slpCh, 0, idx); } else { werHideTip(main); if (werCh) werHideTip(werCh); if (slpCh) werHideTip(slpCh); }")
    _sw.append("    });")
    _sw.append("    if (werCh) werCh.on('updateAxisPointer', function(ev){")
    _sw.append("      var idx = werTipIdx(ev, false);")
    _sw.append("      if (idx >= 0) werShowTip(qqeCh, 0, idx); else werHideTip(qqeCh);")
    _sw.append("    });")
    _sw.append("    if (slpCh) slpCh.on('updateAxisPointer', function(ev){")
    _sw.append("      var idx = werTipIdx(ev, false);")
    _sw.append("      if (idx >= 0) werShowTip(qqeCh, 0, idx); else werHideTip(qqeCh);")
    _sw.append("    });")
    _sw.append("    main.on('globalout', function(){ werHideTip(qqeCh); });")
    _sw.append("    qqeCh.on('globalout', function(){ werHideTip(main); if (werCh) werHideTip(werCh); if (slpCh) werHideTip(slpCh); });")
    _sw.append("    main.on('mouseout', function(){ werHideTip(qqeCh); });")
    _sw.append("    qqeCh.on('mouseout', function(){ werHideTip(main); if (werCh) werHideTip(werCh); if (slpCh) werHideTip(slpCh); });")
    _sw.append("    if (werCh) werCh.on('globalout', function(){ werHideTip(qqeCh); });")
    _sw.append("    if (werCh) werCh.on('mouseout', function(){ werHideTip(qqeCh); });")
    _sw.append("    if (slpCh) slpCh.on('globalout', function(){ werHideTip(qqeCh); });")
    _sw.append("    if (slpCh) slpCh.on('mouseout', function(){ werHideTip(qqeCh); });")
    _sw.append("  }")
    _sw.append("  window._qqeRefresh = function(){ if (qqeCh) qqeCh.setOption(qqeOpt()); };")
    _sw.append("  window._werRefresh = function(){ var _t = document.getElementById('werTitle'); if (_t && window._envWerTitle) _t.textContent = window._envWerTitle; evIn = {}; evOut = {}; ENVELOPE_WER.entries.forEach(function(e){ evIn[ENVELOPE_TIMES1M[e[0]]] = e[2]; }); ENVELOPE_WER.exits.forEach(function(e){ evOut[ENVELOPE_TIMES1M[e[0]]] = 1; }); if (werCh) werCh.setOption(werOpt()); };")
    _sw.append("  window._werSync = function(show){")
    _sw.append("    wrap.style.display = show ? 'block' : 'none';")
    _sw.append("    if (qwrap) qwrap.style.display = show ? 'block' : 'none';")
    _sw.append("    if (swrap) swrap.style.display = show ? 'block' : 'none';")
    _sw.append("    if (show) { if (!werCh) { werCh = echarts.init(document.getElementById('werCh')); werCh.setOption(werOpt()); } else { werCh.resize(); } werBind(); if (swrap) { if (!slpCh) { slpCh = echarts.init(document.getElementById('slpCh')); slpCh.setOption(slpOpt()); } else { slpCh.resize(); } werBindSlp(); } if (qwrap) { if (!qqeCh) { qqeCh = echarts.init(document.getElementById('qqe1mCh')); qqeCh.setOption(qqeOpt()); } else { qqeCh.resize(); } werBindQqe(); } werSyncZoom(); }")
    _sw.append("  };")
    _sw.append("})();")
    _sw.append("switchVersion3((function(){ var _v = null; try { _v = localStorage.getItem('waveVer3'); } catch(e) {} return ['live','vh','qqe','ce','envelope','stvx','hama'].indexOf(_v) >= 0 ? _v : 'live'; })());")
    _sw.append("</script>")
    html = html.replace("</body>", "\n".join(_sw) + "</body>", 1)

    open(out, "w").write(html)
    print(f"wrote {out} ({len(times)} bars, {len(qpins)+len(spins)} pins, pos={pos_txt})")
    # ---- 复利模拟页 (user 2026-11-23 令): compound_chains.json 与
    # compound_template.html 在仓内时, 随本轮把 compound.html 一并渲染到
    # 同一输出目录 (数据由 build_compound_data.py 在本机从各策略定版逐笔链
    # 构建后提交; 仓内缺文件时跳过, 不影响日页) ----
    try:
        _cdir = os.path.dirname(os.path.abspath(__file__))
        _cj = os.path.join(_cdir, "compound_chains.json")
        _ct = os.path.join(_cdir, "compound_template.html")
        if os.path.exists(_cj) and os.path.exists(_ct):
            _tpl = open(_ct, encoding="utf-8").read()
            _data = open(_cj, encoding="utf-8").read()
            _cout = os.path.join(WDIR, "compound.html")
            open(_cout, "w", encoding="utf-8").write(
                _tpl.replace("/*__CHAINS__*/", _data))
            print(f"wrote {_cout} (compound)")
    except Exception as _ce:
        print(f"  compound render skipped: {_ce}")
    # 今日动作摘要
    for (bi, ts, act, dr, px, rs) in acts_sorted:
        if bi in today_idx and act in ("开", "平"):
            print(f"  {ts} {act} {dr} @ {px} | {rs}")


if __name__ == "__main__":
    main()
