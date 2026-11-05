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
refresh_tag = f'<meta http-equiv="refresh" content="{_ar}">' if _ar > 0 else ""
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


# ---- Envelope (TradingView SPY Adaptive Envelope v3.9 With-trend, 止损1.5) ----
# 移植自 research/tv_envelope_v39_withtrend_trendflip.py (compute_indicators + simulate):
# 高斯中线权重已纠正 (最新根权重最大); 平移通道 + 20% 边缘区; 趋势政策 With-trend-only;
# 新参数 1m 特调 + 趋势翻仓开 (强趋势上升沿顺势开, 可绕过 EXIT T 同根翻仓, EOD 不可绕过)。
# 生成器内联 (与 CE 的 _ce_compute 同款做法, 保证 GitHub Actions 部署包自包含)。
_ENV_GAUSS_LEN = 30
_ENV_GAUSS_BW = 3.5
_ENV_ATR_LEN = 12
_ENV_MULT = 1.05
_ENV_SLOPE_LEN = 7
_ENV_ASYM_SHIFT = 1.35
_ENV_FAST_EMA_LEN = 5
_ENV_MOM_LOOKBACK = 15
_ENV_MOM_SENS = 0.55
_ENV_MAX_SHIFT_ATR = 0.50
_ENV_EDGE_PCT = 20.0
_ENV_MAX_OUTSIDE = 0.55
_ENV_ER_LEN = 17
_ENV_ER_TH = 0.35
_ENV_DI_LEN = 8
_ENV_ADX_SMOOTH = 12
_ENV_ADX_TH = 20
_ENV_TAKEOVER_SLOPE = 0.13
_ENV_SL_ATR = 1.5


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
    atr = _env_rma(tr, _ENV_ATR_LEN)
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
    return pd.DataFrame({"t": df["_t"].values, "open": df["open"].values,
                         "high": df["high"].values, "low": df["low"].values,
                         "close": df["close"].values}).reset_index(drop=True)


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
    # 颜色层级: 跟/翻/补深色, 持/开浅色
    def _dir_color(dr, act=None):
        is_call = (dr == "call")
        if act == "翻":
            return "#005000" if is_call else "#a80000"  # 翻: 深(少一点黑)
        elif act == "跟":
            return "#006600" if is_call else "#cc0000"  # 跟: 深
        elif act == "补":
            return "#00a0a0" if is_call else "#ff4500"  # 补: C青色/P橘红
        else:
            return "#66cc66" if is_call else "#ff6666"  # 开: 浅
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
            _pc = "#b27a7a" if dr == "call" else "#7ab27a"
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
            _pc = "#b27a7a" if dr == "call" else "#7ab27a"
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
    def _emit(obi, odr, opx, cbi, cpx):
        try:
            pnl = (float(cpx) - float(opx)) if odr == "call" else (float(opx) - float(cpx))
        except (TypeError, ValueError):
            return
        # 2026-10-19 用户: alpha公式拆到theme.js (THEME.pnlBgColor)，这里只传收益率百分比
        _pct = round(gamma_roi(odr, opx, cpx), 2)
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
        _roi = gamma_roi(rt["odr"], rt["opx"], rt["cpx"]) / 100.0
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

    # ---- VH版色带/单标签/段标签 (ST单30分钟切段=每10根3m一段、段间复利;
    # QQE单整单一段、无段标签; 色带/标签走THEME ST专用色, 照抄backfill VH版) ----
    def _vh_trade_roi(rt):
        """返回 (总ROI fraction, [(段ROI, 段开bar, 段平bar), ...])。"""
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
        _roi2 = gamma_roi(_ar["odr"], _ar["_opx"], _ar["_cpx"]) / 100.0
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
        "预估=Gamma公式ROI×期权成本(实盘按实际成本,回放按$100k基准);开/平SPY=成交时刻最接近1m bar收盘价;(浮)=未实现</div>"
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
        [{**rt, "est_roi": gamma_roi_100(rt["odr"], rt["opx"], rt["cpx"])} for rt in rts],
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

    # ---- CE版 (Chandelier Exit, ATR1x1.85 Heikin Ashi): 与研究 ce_backtest.py 同算法。
    # 方向在全 sig (warmup+当日) 上连续算, 不按日重置; 当日交易按日页口径
    # (-100口径Gamma, 日内$100k sequential, 信号bar记时间、下根开盘结算)。
    # 盘中未平仓只算浮动 (与 VH/QQE 持仓中行一致); 仅完整日 (回放/15:57/早收12:57) 日终平。
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
    _ce_eod = bool(WAVE_DATE) or (_ce_last_hm == "15:57") or (
        vh_live._is_early_close_day(sig["t"].iloc[today_idx[-1]]) and _ce_last_hm == "12:57")
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
            return "#005000" if d == "call" else "#a80000"
        if kind == "open":
            return "#66cc66" if d == "call" else "#ff6666"
        return "#b27a7a" if d == "call" else "#7ab27a"

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

    # ---- Envelope版 (TradingView SPY Adaptive Envelope v3.9 With-trend, 止损1.5) ----
    # 移植自 research/envelope_common.py (与注入器逐字一致的口径):
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
        for _j, (_eb, _xb, _d, _epx, _xpx, _rs) in enumerate(_env_raw):
            _is_flip = (_j > 0 and _env_raw[_j - 1][1] == _eb and _env_raw[_j - 1][5] == "FLIP")
            _dr = "call" if _d == "long" else "put"
            _env_trades.append({
                "obi": int(_eb), "cbi": int(_xb), "dir": _dr,
                "px0": float(_epx), "px1": float(_xpx),
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
            return "#005000" if d == "call" else "#a80000"
        if kind == "open":
            return "#66cc66" if d == "call" else "#ff6666"
        return "#b27a7a" if d == "call" else "#7ab27a"

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
    env_inner = (
        "<div style=\"font-size:14px;font-weight:bold;margin-bottom:6px\">"
        "Envelope版信号单（Gamma理论预估，-100口径）</div>"
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
            _tcol = {1: ("#14532d", "THEME.envelope.trendUp"),
                     -1: ("#7f1d1d", "THEME.envelope.trendDown"),
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
        _stfu[_i] = _stbu[_i] if (_stbu[_i] < _stfu[_i-1] or _stc[_i-1] > _stfu[_i-1]) else _stfu[_i-1]
        _stfl[_i] = _stbl[_i] if (_stbl[_i] > _stfl[_i-1] or _stc[_i-1] < _stfl[_i-1]) else _stfl[_i-1]
        if _stc[_i] > _stfu[_i-1]: _sttrd[_i]=1
        elif _stc[_i] < _stfl[_i-1]: _sttrd[_i]=-1
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
    _sw.append("const ENVELOPE_PINS = " + json.dumps(env_pins, ensure_ascii=False) + ";")
    _sw.append("const ENVELOPE_SPINS = " + json.dumps(env_spins, ensure_ascii=False) + ";")
    _sw.append("const ENVELOPE_SCAT_1M = " + env_scat_js + ";")
    _sw.append("const ENVELOPE_MARKAREA_1M = " + env_mark_area_js + ";")
    _sw.append("const ENVELOPE_INNER = " + json.dumps(env_inner, ensure_ascii=False) + ";")
    _sw.append("const ENVELOPE_ACTS = " + json.dumps(env_acts, ensure_ascii=False) + ";")
    _sw.append("const ENVELOPE_TITLE = " + json.dumps(env_title_txt, ensure_ascii=False) + ";")
    _sw.append("const ENVELOPE_TITLEE = " + json.dumps(env_title_echart, ensure_ascii=False) + ";")
    _sw.append("const ENVELOPE_TIMES1M = " + json.dumps(_env_times1m) + ";")
    _sw.append("const ENVELOPE_UPPER_1M = " + json.dumps(env_upper) + ";")
    _sw.append("const ENVELOPE_LOWER_1M = " + json.dumps(env_lower) + ";")
    _sw.append("const ENVELOPE_CHANMID_1M = " + json.dumps(env_chanmid) + ";")
    _sw.append("const ENVELOPE_MID_1M = " + json.dumps(env_mid) + ";")
    _sw.append("const ENVELOPE_TIP = " + json.dumps(env_tip, ensure_ascii=False) + ";")
    _sw.append("const ENVELOPE_TREND_SEGS = " + json.dumps(_env_trend_segs) + ";")
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
} catch (err) {}
return _origEnvTip(ps, barInfo);
};
})();""")
    _sw.append("const TITLES3 = {live: " + json.dumps(title_txt, ensure_ascii=False)
               + ", vh: " + json.dumps(vh_title_txt, ensure_ascii=False)
               + ", qqe: " + json.dumps(qqe_title_txt, ensure_ascii=False)
               + ", ce: " + json.dumps(ce_title_txt, ensure_ascii=False)
               + ", envelope: " + json.dumps(env_title_txt, ensure_ascii=False) + "};")
    _sw.append("const TITLES3E = {vh: " + json.dumps(vh_title_echart, ensure_ascii=False)
               + ", qqe: " + json.dumps(qqe_title_echart, ensure_ascii=False)
               + ", ce: " + json.dumps(ce_title_echart, ensure_ascii=False)
               + ", envelope: " + json.dumps(env_title_echart, ensure_ascii=False) + "};")
    _sw.append("const RICH3 = {dn:{color:'#e5484d',fontWeight:'bold',fontSize:18},up:{color:'#2e9e5b',fontWeight:'bold',fontSize:18},flat:{color:'#e5484d',fontWeight:'bold'},st:{color:'#00a0a0',fontWeight:'bold'},qqe:{color:'#2b7fff',fontWeight:'bold'},ce:{color:'#e5484d',fontWeight:'bold'},envelope:{color:'#2563eb',fontWeight:'bold'},pnl_pos:{color:'#1a9e54'},pnl_neg:{color:'#e5484d'}};")
    _sw.append("const VH_INNER = " + json.dumps(vh_inner, ensure_ascii=False) + ";")
    _sw.append("const QQE_INNER = " + json.dumps(qqe_inner, ensure_ascii=False) + ";")
    _sw.append("var LIVE_INNER = (function(){var tw=document.getElementById('tblWrap');return tw?tw.innerHTML:'';})();")
    _sw.append("var curVer3 = 'live';")
    _sw.append("function switchVersion3(v){")
    _sw.append("  curVer3 = v;")
    _sw.append("  var isLive = (v === 'live');")
    _sw.append("  var isVH = (v === 'vh');")
    _sw.append("  var useVH = isLive || isVH;")
    _sw.append("  var isCE = (v === 'ce');")
    _sw.append("  var isENVELOPE = (v === 'envelope');")
    _sw.append("  var pinsData = isENVELOPE ? ENVELOPE_PINS : (isCE ? CE_PINS : (useVH ? pins : QQE_PINS));")
    _sw.append("  var spinsData = isENVELOPE ? ENVELOPE_SPINS : (isCE ? CE_SPINS : (useVH ? spins : QQE_SPINS));")
    _sw.append("  var scatData = isENVELOPE ? ENVELOPE_SCAT_1M : (isCE ? CE_SCAT : (useVH ? VH_SCAT : QQE_SCAT));")
    _sw.append("  var scatFS = (isCE || useVH) ? 11 : 8;")
    _sw.append("  if (typeof THEME !== 'undefined' && THEME.applyColors) { THEME.applyColors(spinsData); THEME.applyColors(pinsData); }")
    _sw.append("  ch.setOption({")
    _sw.append("    title: {text: isLive ? TITLES3.live : (isENVELOPE ? TITLES3E.envelope : (isCE ? TITLES3E.ce : (isVH ? TITLES3E.vh : TITLES3E.qqe))), left: 'center', textStyle: {fontSize: 14, rich: RICH3}},")
    _sw.append("    series: [")
    _sw.append("      {name: '1m曲线', data: (isENVELOPE && ENVELOPE_TREND_SEGS.length) ? [] : line1m, lineStyle: {color: isENVELOPE ? '#1e40af' : '#4da6ff', width: isENVELOPE ? 2 : 1.2, opacity: isENVELOPE ? 1 : 0.55}, markPoint: {data: spinsData}},")
    for _si, _sg in enumerate(_env_trend_segs):
        _sw.append("      {id: 'env-trend-seg-%d', name: '1m趋势段%d', type: 'line', xAxisIndex: 3, yAxisIndex: 0, symbol: 'none', showSymbol: false, lineStyle: {color: %s, width: 2}, data: isENVELOPE ? ENVELOPE_TREND_SEGS[%d].data : []}," % (_si, _si, _sg["theme"], _si))
    _sw.append("      {name: 'QQE', markPoint: {data: pinsData}},")
    _sw.append("      {name: '每单盈亏', data: scatData, label: {show: isENVELOPE ? false : true, opacity: 1, fontSize: scatFS, fontWeight: 'bold', position: 'top', distance: 1, formatter: function(p){return p.data.lbl;}}, markArea: {silent: true, data: isENVELOPE ? ENVELOPE_MARKAREA_1M : []}},")
    _sw.append("      {name: '3mK线', markArea: {silent: true, data: isENVELOPE ? [] : (isCE ? CE_MARKAREA : (useVH ? VH_MARKAREA : QQE_MARKAREA))}, markLine: {silent: true, symbol: 'none', label: {show: false}, lineStyle: {color: 'rgba(0,0,0,0.20)', width: 1, type: 'solid'}, data: useVH ? VH_MARKLINE : []}},")
    _sw.append("      {name: '段收益率', type: 'scatter', xAxisIndex: 0, yAxisIndex: 0, symbolSize: 1, itemStyle: {opacity: 0}, label: {show: true, opacity: 1, fontSize: 8, fontWeight: 'bold', position: 'top', distance: 1, formatter: function(p){return p.data.lbl;}}, data: useVH ? VH_SEG : []},")
    _sw.append("      {id: 'ce-signal', name: 'CE信号', type: 'scatter', xAxisIndex: 0, yAxisIndex: 0, symbolSize: 1, itemStyle: {opacity: 0}, label: {show: true, fontSize: 12}, z: 6, data: isCE ? CE_BS : []},")
    _sw.append("      {id: 'env-upper', name: 'Envelope上轨', type: 'line', xAxisIndex: 0, yAxisIndex: 0, symbolSize: 0, showSymbol: false, lineStyle: {color: THEME.envelope.upper, width: 2}, data: isENVELOPE ? ENVELOPE_UPPER_1M : []},")
    _sw.append("      {id: 'env-lower', name: 'Envelope下轨', type: 'line', xAxisIndex: 0, yAxisIndex: 0, symbolSize: 0, showSymbol: false, lineStyle: {color: THEME.envelope.lower, width: 2}, data: isENVELOPE ? ENVELOPE_LOWER_1M : []},")
    _sw.append("      {id: 'env-chanmid', name: 'Envelope中轨', type: 'line', xAxisIndex: 0, yAxisIndex: 0, symbolSize: 0, showSymbol: false, lineStyle: {color: THEME.envelope.chanmid, width: 2}, data: isENVELOPE ? ENVELOPE_CHANMID_1M : []},")
    _sw.append("      {id: 'env-mid', name: 'Envelope均线', type: 'line', xAxisIndex: 0, yAxisIndex: 0, symbolSize: 0, showSymbol: false, lineStyle: {color: THEME.envelope.mid, width: 2}, data: isENVELOPE ? ENVELOPE_MID_1M : []},")
    _sw.append("      {name: '成交点', data: isENVELOPE ? [] : filldots}")
    _sw.append("    ], legend: {selected: {'cumslope': false, 'slope5': false, 'st快版': false, 'st慢版': isENVELOPE ? false : true, '9mK线': isENVELOPE ? false : true, '9m QQE': isENVELOPE ? false : true, '3mK线': isENVELOPE ? false : true, 'QQE': isENVELOPE ? false : true, '80%衰减线': isENVELOPE ? false : true, '翻仓线': isENVELOPE ? false : true, '回补门槛': isENVELOPE ? false : true}}, xAxis: [{data: isENVELOPE ? ENVELOPE_TIMES1M : times}, {show: isENVELOPE ? false : true, axisPointer: {label: {show: isENVELOPE ? false : true}}, data: isENVELOPE ? ENVELOPE_TIMES1M : times}, {show: isENVELOPE ? false : true, axisPointer: {label: {show: isENVELOPE ? false : true}}, data: isENVELOPE ? ENVELOPE_TIMES1M : times}, {min: isENVELOPE ? -1/6 : -0.5, max: isENVELOPE ? 779/6 : 129.5}], yAxis: [{min: isENVELOPE ? " + env_min_js + " : null, max: isENVELOPE ? " + env_max_js + " : " + str(y_max_js) + "}, {show: isENVELOPE ? false : true}], grid: [{height: isENVELOPE ? '83%' : '38%'}, {height: isENVELOPE ? '0%' : '42%'}]")
    _sw.append("  });")
    _sw.append("  var tw = document.getElementById('tblWrap');")
    _sw.append("  if (tw) { tw.innerHTML = isLive ? LIVE_INNER : (isENVELOPE ? ENVELOPE_INNER : (isCE ? CE_INNER : (isVH ? VH_INNER : QQE_INNER))); }")
    _sw.append("  var actsData = isENVELOPE ? ENVELOPE_ACTS : (isCE ? CE_ACTS : (useVH ? VH_ACTS : QQE_ACTS));")
    _sw.append("  for (var t in barInfo) { barInfo[t].acts = actsData[t] || []; }")
    _sw.append("  document.title = TITLES3[v];")
    _sw.append("  if (window._verBtns3) { window._verBtns3.forEach(function(b){ var on = (b.dataset.ver === v); b.style.background = on ? '#2b7fff' : '#fff'; b.style.borderColor = on ? '#2b7fff' : '#ccc'; b.style.color = on ? '#fff' : '#333'; }); }")
    _sw.append("}")
    _sw.append("(function(){")
    _sw.append("  var sel = document.getElementById('daypick');")
    _sw.append("  if (!sel) return;")
    _sw.append("  var wrap = document.createElement('span');")
    _sw.append("  [['live','实盘版'],['vh','ST VH版'],['qqe','QQE版'],['ce','CE版'],['envelope','Envelope版']].forEach(function(it){")
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
    _sw.append("  if (!sel.querySelector('option[value=\"summary.html\"]')) { var so = document.createElement('option'); so.value = 'summary.html'; so.textContent = '📊 回测版本总览'; sel.insertBefore(so, sel.options[0]); }")
    _sw.append("})();")
    _sw.append("switchVersion3('live');")
    _sw.append("</script>")
    html = html.replace("</body>", "\n".join(_sw) + "</body>", 1)

    open(out, "w").write(html)
    print(f"wrote {out} ({len(times)} bars, {len(qpins)+len(spins)} pins, pos={pos_txt})")
    # 今日动作摘要
    for (bi, ts, act, dr, px, rs) in acts_sorted:
        if bi in today_idx and act in ("开", "平"):
            print(f"  {ts} {act} {dr} @ {px} | {rs}")


if __name__ == "__main__":
    main()
