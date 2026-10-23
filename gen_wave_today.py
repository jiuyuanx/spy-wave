#!/usr/bin/env python3
"""今日波浪dashboard刷新: QQE柱 + 80%衰减线 + 翻仓门槛线, 白底ECharts风格。

数据源: 与 watcher_v4 完全一致 (warmup缓存 + 今日1m->3m聚合, 只取完整bar)。
QQE/真色/衰减/翻仓need 全部按 signal_engine(k3clean L25当前基线) 的真实算法逐bar重放,
不手绘。显示窗口: 今日 09:30 -> 最新完整bar。

用法: python3 gen_wave_today.py
输出: ~/workspace/alpaca_watcher/wave-today-YYYYMMDD.html
"""
import sys, os, json, re
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
from signal_engine import (run_signals, detect_red_decay, detect_blue_decay,
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


def _warmup_before(day):
    """某日前 7 天的 3m warmup (回放用, 不碰生产 load_history)。"""
    start = (day - timedelta(days=7)).strftime("%Y-%m-%dT00:00:00Z")
    end = day.strftime("%Y-%m-%dT00:00:00Z")
    params = {"symbols": "SPY", "timeframe": "3Min", "limit": 1000, "feed": "iex",
              "sort": "desc", "start": start, "end": end}
    data = w4.api("/stocks/bars", params)
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
    data = w4.api("/stocks/bars", params)
    bars = data.get("bars", {}).get("SPY", [])
    df = pd.DataFrame(bars)
    if df.empty:
        raise RuntimeError(f"no 1m bars for {day}")
    df["t"] = pd.to_datetime(df["t"], utc=True).dt.tz_convert(ET)
    return df[["t", "o", "h", "l", "c", "v"]].sort_values("t").reset_index(drop=True)


def fetch_day_fills(day_str):
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
    day_open_equity = None
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

    actions = run_signals(sig)
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

    # 1m曲线数据: x=分数类目坐标(分钟中点), y=1m收盘
    t0 = sig["t"].iloc[today_idx[0]]
    df1 = df1m[df1m["t"] >= t0].sort_values("t")
    line1m = [[round((r["t"] - t0).total_seconds() / 180.0 + 1 / 6, 4),
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
            _fx = round((_bt - t0).total_seconds() / 180.0 + 1 / 6, 4)
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
    fills, poss, equity, day_open_equity = ([], {}, None, None) if WAVE_DATE else fetch_day_fills(day_str)
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
    for _ar in actual_rts:
        _sym_cp = "call" if _ar["sym"][9:10] == "C" else "put"
        _best, _best_s = None, 1e9
        for _rt in rts:
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
        "<div style=\"max-width:1150px;margin:14px auto;padding:0 8px\">"
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
        # 回放模式: 显示预估收益 (回测逻辑)
        _replay_est = sum(r["est_pnl"] for r in rts)
        _replay_ret = _replay_est / 100000
        _s = "+" if _replay_est >= 0 else "-"
        title_txt = (f"{page_title} {day_str} | 预估 {_s}${abs(_replay_est):,.0f} ({_replay_ret:+.1%}) "
                     f"| 持仓:{pos_txt} | k3clean L25")

    mark_area_js = "[" + ",".join(
        "[{xAxis:'%s',itemStyle:{color:THEME.pnlBgColor(%s)}},{xAxis:'%s'}]" % (
            a[0]["xAxis"], a[0]["pnlPct"], a[1]["xAxis"])
        for a in mark_areas) + "]"
    mark_area_opt = "{silent:true,data:" + mark_area_js + "}"
    ohlc = [[round(float(sig["open"].iloc[i]), 2), round(float(sig["close"].iloc[i]), 2),
             round(float(sig["low"].iloc[i]), 2), round(float(sig["high"].iloc[i]), 2)]
            for i in today_idx]
    ohlc_js = "[" + ",".join(f"[{o},{c},{l},{h}]" for o, c, l, h in ohlc) + "]"

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
    rep_const('pins', json.dumps(qpins, ensure_ascii=False))
    rep_const('spins', json.dumps(spins, ensure_ascii=False))
    rep_const('line1m', json.dumps(line1m))
    rep_const('filldots', json.dumps(filldots, ensure_ascii=False))
    rep_const('barInfo', json.dumps(bar_info, ensure_ascii=False))

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
                   "label:{show:true, opacity:1, fontSize:8, fontWeight:'bold', position:'top', distance:1," +
                   "formatter:function(p){return p.data.lbl;}}, data:" + scat_js + "}")
    html, n7 = _replace_balanced(html, r"\{name:'每单盈亏'", scat_series)
    html, n8 = re.subn(r'<div style="max-width:1150px.*?</div>\s*<script>',
                       table_html + '\n<script>',
                       html, count=1, flags=re.S)
    html = re.sub(r"(yAxis:\[\{type:'value',gridIndex:0,scale:true,max:)[\d.]+",
                  r"\g<1>" + str(y_max_js), html, count=1)

    if 0 in (n1, n2, n3s, n4, n5, n6, n7, n8):
        print(f"  WARN subs: 3mK={n1} 9mK={n2} 9mQQE={n3s} cumslope={n4} slope5={n5} markArea={n6} scat={n7} table={n8}")
    if WAVE_DATE:
        _out_name = f"{WAVE_DATE}.html"
    else:
        _out_name = WAVE_OUT_NAME_TMPL or f"wave-today-{day_str.replace('-', '')}.html"
    out = f"{WDIR}/{_out_name}"
    os.makedirs(WDIR, exist_ok=True)
    open(out, "w").write(html)
    print(f"wrote {out} ({len(times)} bars, {len(qpins)+len(spins)} pins, pos={pos_txt})")
    # 今日动作摘要
    for (bi, ts, act, dr, px, rs) in acts_sorted:
        if bi in today_idx and act in ("开", "平"):
            print(f"  {ts} {act} {dr} @ {px} | {rs}")


if __name__ == "__main__":
    main()
