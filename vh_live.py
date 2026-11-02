#!/usr/bin/env python3
"""VH 实盘单脚本 (自包含, 唯一正本)。

来源: research/gen_week_strongtrend_hybrid_G_winAfterSTimm_stonly_flipcheck_11same.py
(VH 批: $16,037,223 / 148周) 的 ch25/9mK/信号/主循环原文机械移植, 仅做接口适配
(见 research/vh_live_verify/build_vh_live.py 的改动清单)。不 import research 目录、
不读行情数据文件、不读预计算缓存、不读环境变量决定策略、不含硬编码路径。

VH 开关全部写死: HYBRID=1, VARIANT=G, WIN_AFTER_ST_IMM=1, FLIPCHECK=1,
HYB_11SAME=1, REBOUND_RED=1, CUM_SLOPE_THRESHOLD=0.05, STRONG_TREND=1,
ST_FLIP=0, STOP_WIN_BARS=6, STOP_WIN_RECOV=0.50, USE_30S_STOP=0, ST_ONLY=0。
其余开关取回测批默认值 (见 build 脚本 ENV_LIT)。

接口:
  run_vh(bars, eod_1m=None) -> 当日动作列表, 元素 (bar_idx, tstr, act, direction, price, reason)
    bars: DataFrame, 列 t/o/h/l/c (或 open/high/low/close), 3分钟常规时段bar,
      按时间升序, 含 warmup + 当日; t 为 tz-aware ET 时间戳。
    bar_idx: 该 bar 在传入 bars 中的下标 (watcher 用它取时间戳做信号过期检查)。
    tstr: %m-%dT%H:%M。act: 开/平/持。direction: call/put。
    price: 执行价口径与回测一致: 信号bar、下根开盘结算, 同日; 跨天用本根收盘。
      例外只有日终平仓(1m15:55): 回测价来自 1m 数据; 实盘调用方若传
      eod_1m={日期(YYYY-MM-DD): 1m15:55收盘价} 则与回测一字不差, 不传则回退
      到该 bar 的 3m 收盘价。watcher 侧 15:55 时钟平仓优先于本文件的 15:57 bar
      日终块, 故实盘中该回退价通常走不到。
    只返回传入 bars 最后一天的动作; warmup 天动作丢弃 (回测每天日终平仓、
    当日状态独立; day_start 重置逻辑对每一天都跑, 包括 warmup 天)。
  day_info(bars) -> 当日分类 dict {gap, gap_band, morn, aft}
    gap: 带符号点数 = 当日首根开盘价 - 前一交易日末根收盘价, fallback 链与
      run_vh 的 day-change 块一致 (首根 open 缺失 -> 当日最早可用 open;
      无当日 bar 或无前一日收盘 -> gap=0, 即非跳空、全天 ST)。
    gap_band: 'big' (|gap|>=10) / 'gap' (>=5) / 'none' (<5)。
    morn: 非跳空日 'st'; 跳空日 >=10 'skip'、5<=|gap|<10 'qqe'。
    aft: 非跳空日 'st'; 跳空日 G 版 'dir' (11:00 按 gap 符号解为 qqe/st)。
    日页标题文案以 day_info 为准, 不要另算一套策略分类。
"""
import numpy as np
import pandas as pd
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")
_SKIP_DATES = {"2026-06-17"}  # 617 Fed日永久不做 (回测同款)

# ---------- inlined qqe_mod (原文函数, 不 import) ----------
def rma(x, n):
    """Wilder's smoothing, Pine rma(): sum := (x + (n-1)*nz(sum[1])) / n ; sum[0]=0."""
    x = np.asarray(x, dtype=float)
    out = np.empty(len(x))
    prev = 0.0
    for i, v in enumerate(x):
        v = 0.0 if np.isnan(v) else v
        prev = (v + (n - 1) * prev) / n
        out[i] = prev
    return out


def ema(x, n):
    """Pine ema(): alpha*x + (1-alpha)*nz(ema[1], x) -> seeds with first non-na."""
    x = np.asarray(x, dtype=float)
    alpha = 2.0 / (n + 1)
    out = np.full(len(x), np.nan)
    prev = np.nan
    for i, v in enumerate(x):
        if np.isnan(v):
            continue
        prev = v if np.isnan(prev) else alpha * v + (1 - alpha) * prev
        out[i] = prev
    return out


def pine_rsi(src, length):
    src = np.asarray(src, dtype=float)
    n = len(src)
    chg = np.full(n, np.nan)
    chg[1:] = src[1:] - src[:-1]
    u = np.where(chg > 0, chg, 0.0)
    d = np.where(chg < 0, -chg, 0.0)
    u[0] = np.nan
    d[0] = np.nan
    avg_u = rma(u, length)
    avg_d = rma(d, length)
    rs = np.full(n, np.nan)
    nz = avg_d != 0
    rs[nz] = avg_u[nz] / avg_d[nz]
    rsi = 100.0 - 100.0 / (1.0 + rs)
    flat_up = (avg_d == 0) & (avg_u > 0)
    rsi[flat_up] = 100.0
    return rsi


def _qqe_pass(src, rsi_len, sf, qqe_factor):
    src = np.asarray(src, dtype=float)
    n = len(src)
    wilders = rsi_len * 2 - 1

    rsi = pine_rsi(src, rsi_len)
    rsi_ma = ema(rsi, sf)

    atr_rsi = np.full(n, np.nan)
    atr_rsi[1:] = np.abs(rsi_ma[1:] - rsi_ma[:-1])
    ma_atr_rsi = ema(atr_rsi, wilders)
    dar = ema(ma_atr_rsi, wilders) * qqe_factor

    rsidx = rsi_ma
    newlong = rsidx - dar
    newshort = rsidx + dar

    longband = np.full(n, np.nan)
    shortband = np.full(n, np.nan)
    trend = np.ones(n)  # nz(trend[1], 1)

    for i in range(n):
        nl, ns, ri = newlong[i], newshort[i], rsidx[i]
        if np.isnan(nl) or np.isnan(ns) or np.isnan(ri):
            if i > 0:
                longband[i] = longband[i - 1]
                shortband[i] = shortband[i - 1]
                trend[i] = trend[i - 1]
            continue
        if i == 0:
            longband[i] = nl
            shortband[i] = ns
        else:
            lb_prev, sb_prev = longband[i - 1], shortband[i - 1]
            ri_prev = rsidx[i - 1]
            if ri_prev > lb_prev and ri > lb_prev:
                longband[i] = lb_prev if lb_prev > nl else nl  # max, na-safe
            else:
                longband[i] = nl
            if ri_prev < sb_prev and ri < sb_prev:
                shortband[i] = sb_prev if sb_prev < ns else ns  # min, na-safe
            else:
                shortband[i] = ns
        if i >= 2:
            # cross(RSIndex, shortband[1]) -> trend up
            a0, a1 = rsidx[i - 1], rsidx[i]
            b0, b1 = shortband[i - 2], shortband[i - 1]
            cross_up = (a0 < b0 and a1 > b1) or (a0 > b0 and a1 < b1)
            # cross(longband[1], RSIndex) -> trend down
            c0, c1 = longband[i - 2], longband[i - 1]
            d0, d1 = rsidx[i - 1], rsidx[i]
            cross_dn = (c0 < d0 and c1 > d1) or (c0 > d0 and c1 < d1)
            if cross_up:
                trend[i] = 1
            elif cross_dn:
                trend[i] = -1
            else:
                trend[i] = trend[i - 1]
        elif i > 0:
            trend[i] = trend[i - 1]

    trail = np.where(trend == 1, longband, shortband)
    return {"rsi_ma": rsi_ma, "trail": trail, "trend": trend}


def qqe_mod(close, rsi_len=6, sf=5, qqe_factor=3.0,
            bb_len=50, bb_mult=0.35,
            rsi_len2=6, sf2=5, qqe_factor2=1.61, thresh=3.0):
    """Returns dict with hist, up/down/neutral states (Pine-faithful)."""
    close = np.asarray(close, dtype=float)
    n = len(close)
    primary = _qqe_pass(close, rsi_len, sf, qqe_factor)
    secondary = _qqe_pass(close, rsi_len2, sf2, qqe_factor2)

    trail_m50 = primary["trail"] - 50.0
    s = pd.Series(trail_m50)
    basis = s.rolling(bb_len, min_periods=bb_len).mean().values
    dev = bb_mult * s.rolling(bb_len, min_periods=bb_len).std(ddof=0).values
    upper = basis + dev
    lower = basis - dev

    hist = secondary["rsi_ma"] - 50.0
    prim_m50 = primary["rsi_ma"] - 50.0

    up_state = (hist > thresh) & (prim_m50 > upper)
    down_state = (hist < -thresh) & (prim_m50 < lower)
    neutral_state = (~up_state) & (~down_state) & (np.abs(hist) > thresh)

    return {
        "hist": hist,
        "up": up_state,
        "down": down_state,
        "neutral": neutral_state,
        "upper": upper,
        "lower": lower,
        "primary_trail": primary["trail"],
        "secondary_trail": secondary["trail"],
    }

# ---------- inlined signal_engine_lb10 detect 函数 (原文, 不 import) ----------
SIG_MIN_LEN = 4
_SKIP_DATES = {"2026-06-17"}  # Warsh FOMC, permanently excluded


# ---------- detect functions (from watcher_v3, pure) ----------
def _detect_peak_pullback(hist, mask, opp_mask, lookback=10, rise_mult=2.0,
                          pullback=0.8, min_peak=6, target_sign=1, skip_rise_mult=False,
                          day=None):
    """峰值回落衰减: 从当前往回找连续同色段 (遇真反色或空柱断开, 最多 lookback 根),
    段内同色峰值>=min_peak 且 >= 段内最小*2 ("涨起来了"), 当前回落到峰值*0.8 -> 信号。
    灰柱: 段内可计入目标色用于算峰值 (须与目标色同号), 触发柱可以是真色或灰柱
    (|hist|>=SIG_MIN_LEN) (2026-10-06 用户: "灰柱子感觉比蓝柱子更算衰减")。
    skip_rise_mult: 早盘跳过"2倍"检查 (2026-10-01 用户: 早盘一上来就很长)
    target_sign: +1 目标为蓝柱(hist>0), -1 目标为红柱(hist<0)。
    day: 可选, 与hist等长的日期数组; 回看不跨日 (2026-10-02 用户: "这里蓝色哪有衰减?"
    周一早盘拿上周五的峰值误触发)"""
    n = len(hist)
    out = []
    i = 300
    while i < n:
        cur = hist[i]
        ca = abs(cur) if not np.isnan(cur) else 0
        # 从 i 往回找连续段: 遇真反色或空柱(|hist|<=3)断开 (2026-10-01: 空柱断开, 不连前高)
        # 2026-10-02: 遇跨日断开, 不拿昨天的峰值
        lo = max(300, i - lookback)
        seg_lo = lo
        for j in range(i, lo - 1, -1):
            cj = hist[j]; caj = abs(cj) if not np.isnan(cj) else 0
            if day is not None and j < i and day[j] != day[i]:
                seg_lo = j + 1
                break
            if bool(opp_mask[j]) or caj <= 3 or np.isnan(cj):
                seg_lo = j + 1
                break
        # 段内颜色主导 (段内无真反色, 只需目标色存在)
        n_target = 0
        for j in range(seg_lo, i + 1):
            if bool(mask[j]): n_target += 1
        target_dominant = n_target > 0
        def _is_target(j):
            # LB实验: 同波同向即算 (灰柱同号也算峰值, 不需要真色)
            cj0 = hist[j]
            if np.isnan(cj0): return False
            return cj0 * target_sign > 0
        # 2026-10-06 用户: 灰柱触发不需要 |hist|>=4，只要衰减到80%以下就行
        _is_gray_trigger = not bool(mask[i])
        if not _is_gray_trigger and ca < SIG_MIN_LEN:
            i += 1; continue
        peaks = []
        base_min = 999
        for j in range(seg_lo, i + 1):
            cj = hist[j]
            caj = abs(cj) if not np.isnan(cj) else 0
            if caj < base_min:
                base_min = caj
            if _is_target(j) and caj > 3:
                peaks.append(caj)
        if not peaks:
            i += 1; continue
        peak = max(peaks)
        if ca >= peak:
            i += 1; continue  # 当前就是峰值, 不算回落
        # "涨起来"判定 (2026-10-02 用户): 2倍 OR 3根连续高柱(|hist|>=8) OR 3根连续递增(动能强)
        # 早盘宽松版: 2根连续、第二根更长即可 (2026-10-02 用户: "一上来就很长，第二根更长再进")
        _tall3 = False; _mom3 = False; _mom2 = False
        for j in range(seg_lo, i):
            if j+2 > i: break
            _c0 = abs(hist[j]) if not np.isnan(hist[j]) else 0
            _c1 = abs(hist[j+1]) if not np.isnan(hist[j+1]) else 0
            _c2 = abs(hist[j+2]) if not np.isnan(hist[j+2]) else 0
            if _is_target(j) and _is_target(j+1) and _is_target(j+2):
                if _c0 >= 8 and _c1 >= 8 and _c2 >= 8:
                    _tall3 = True
                if _c2 > _c1 > _c0:
                    _mom3 = True
            if _is_target(j) and _is_target(j+1) and _c1 > _c0:
                _mom2 = True
        if skip_rise_mult:
            _rise_ok = _mom2 or _tall3 or _mom3
        else:
            _rise_ok = (peak >= max(base_min, 3) * rise_mult) or _tall3 or _mom3
        # 衰减需同时满足: <=80%峰值 且 <=90%前一根 (2026-10-02 用户)
        # 2026-10-07 用户: 去掉"涨起来了"条件
        _prev_ca = abs(hist[i-1]) if i-1 >= seg_lo and not np.isnan(hist[i-1]) else 999
        _decay_ok = ca <= peak * pullback and ca <= _prev_ca * 0.9
        if peak >= min_peak and _decay_ok:
            out.append((i, peak))
        i += 1
    return out

def detect_red_decay(hist, down, up=None, skip_rise_mult=False, day=None):
    """红衰减 -> 开call: 红柱(含主导窗口内的灰柱)涨起来后从峰值回落。"""
    opp = up if up is not None else [False]*len(hist)
    return _detect_peak_pullback(hist, down, opp, target_sign=-1, skip_rise_mult=skip_rise_mult, day=day)

def detect_blue_decay(hist, up, down=None, skip_rise_mult=False, day=None):
    """蓝衰减 -> 开put: 蓝柱(含主导窗口内的灰柱)涨起来后从峰值回落。"""
    opp = down if down is not None else [False]*len(hist)
    return _detect_peak_pullback(hist, up, opp, target_sign=+1, skip_rise_mult=skip_rise_mult, day=day)

def count_lengthening(hist, cur_idx, direction, q_up, q_down):
    """数当前连续变长 bar 数 (只认真红/真蓝，且长度>=SIG_MIN_LEN)。
    灰柱跳过不计数不断裂; 空柱(|hist|<=3)或反向色则断裂。
    direction='red' 用 down, 'blue' 用 up。"""
    count = 0
    j = cur_idx
    prev_abs = None
    while j > 0:
        cur = hist[j]
        cur_abs = abs(cur)
        # 信号级真红/真蓝: 长度>=SIG_MIN_LEN
        is_up_sig = bool(q_up[j]) and cur_abs >= SIG_MIN_LEN
        is_down_sig = bool(q_down[j]) and cur_abs >= SIG_MIN_LEN
        # 基础颜色判断 (用于灰/空识别)
        is_up = bool(q_up[j]) and cur_abs > 3
        is_down = bool(q_down[j]) and cur_abs > 3
        is_gray = (not is_up and not is_down and cur_abs > 3)
        is_empty = cur_abs <= 3 or np.isnan(cur)
        want_red = (direction == "red")
        is_want = is_down_sig if want_red else is_up_sig
        is_opposite = is_up_sig if want_red else is_down_sig

        if is_gray:
            j -= 1
            continue  # 灰柱跳过
        if is_empty or is_opposite:
            break  # 空柱或反向信号色断裂
        if not is_want:
            break  # 非信号级长度，断裂
        # 信号级真红/真蓝
        if prev_abs is None:
            prev_abs = cur_abs
            j -= 1
            continue
        if cur_abs < prev_abs:
            # 往回找时变小 = 时间正向变长
            count += 1
            prev_abs = cur_abs
        else:
            break
        j -= 1
    return count

# ---------- 输入归一化与当日分类 ----------

def _normalize_bars(bars):
    """bars -> warm DataFrame (t/open/high/low/close, 位置下标 0..n-1)。"""
    df = bars.copy()
    col = {}
    for want, alts in (("t", ("t", "time", "datetime")),
                       ("open", ("open", "o")), ("high", ("high", "h")),
                       ("low", ("low", "l")), ("close", ("close", "c"))):
        for a in alts:
            if a in df.columns:
                col[want] = a
                break
        else:
            raise ValueError(f"bars 缺少列: {want}")
    warm = pd.DataFrame({
        "t": pd.to_datetime(df[col["t"]]),
        "open": pd.to_numeric(df[col["open"]], errors="coerce").astype(float),
        "high": pd.to_numeric(df[col["high"]], errors="coerce").astype(float),
        "low": pd.to_numeric(df[col["low"]], errors="coerce").astype(float),
        "close": pd.to_numeric(df[col["close"]], errors="coerce").astype(float),
    })
    try:
        _tz = warm["t"].dt.tz
    except Exception:
        _tz = None
    if _tz is None:
        warm["t"] = warm["t"].dt.tz_localize(_ET)
    else:
        warm["t"] = warm["t"].dt.tz_convert(_ET)
    return warm.reset_index(drop=True)


def _day_gap_values(warm):
    """返回 (gap, band, morn, aft): 对 warm 的最后一天, 与主循环 day-change 块同链。
    gap = 当日首根(最早可用)open - 前一交易日末根close; 缺数据 -> gap=0。"""
    last_date = warm["t"].iloc[-1].date()
    dates = warm["t"].dt.date.values
    last_idx = [i for i, d in enumerate(dates) if d == last_date]
    day_open = float("nan")
    for i in last_idx:
        v = float(warm["open"].iloc[i])
        if v == v:
            day_open = v
            break
    prev_close = float("nan")
    prev_idx = [i for i, d in enumerate(dates) if d < last_date]
    if prev_idx:
        for i in reversed(prev_idx):
            v = float(warm["close"].iloc[i])
            if v == v:
                prev_close = v
                break
    if day_open != day_open or prev_close != prev_close:
        gap = 0.0
    else:
        gap = day_open - prev_close
    if abs(gap) >= 10.0:
        band, morn = "big", "skip"
    elif abs(gap) >= 5.0:
        band, morn = "gap", "qqe"
    else:
        band, morn = "none", "st"
    aft = "st" if band == "none" else "dir"  # G 版跳空日下午 = 方向下午
    return gap, band, morn, aft


def day_info(bars):
    """当日分类 dict, 与 run_vh 的 hybrid 分类一致。空输入返回 gap=0 的非跳空分类。"""
    warm = _normalize_bars(bars)
    if len(warm) == 0:
        return {"gap": 0.0, "gap_band": "none", "morn": "st", "aft": "st"}
    gap, band, morn, aft = _day_gap_values(warm)
    return {"gap": float(gap), "gap_band": band, "morn": morn, "aft": aft}


def _is_early_close_day(ts):
    """NYSE 13:00 提前收盘日判定 (ts 为 ET 时间戳, 取其 ET 日历日)。
    规则 (NYSE 官方日历, 已验 2024-2029 + 缓存 71 根早收日):
      感恩节次日 (11 月第 4 个周四 +1 天, 恒为周五): 恒早收;
      12/24: 仅当 12/25 落在周二至周五 (12/25 落周一/六/日则取消, 如 2027/2028);
      7/3: 仅当 7/3 本身是工作日且 7/4 落在周二至周五 (如 2026/2027 取消)。
    夏令时无关: 比较的是 ET 日历日与周几, 不涉及时区偏移。
    """
    y, m, d = ts.year, ts.month, ts.day
    if m == 11:
        nov1_wd = pd.Timestamp(y, 11, 1).weekday()  # Mon=0
        first_thu = 1 + ((3 - nov1_wd) % 7)
        return d == first_thu + 21 + 1  # 第 4 个周四 +1 天
    if m == 12 and d == 24:
        return pd.Timestamp(y, 12, 25).weekday() in (1, 2, 3, 4)
    if m == 7 and d == 3:
        return ts.weekday() < 5 and pd.Timestamp(y, 7, 4).weekday() in (1, 2, 3, 4)
    return False


def _run_all(warm, eod_1m):
    _eod_1m = dict(eod_1m) if eod_1m else {}
    close = warm["close"].values
    high = warm["high"].values
    low = warm["low"].values
    _open_arr = warm["open"].values
    _openv = warm["open"].values
    res = qqe_mod(close)
    hist = np.asarray(res["hist"], dtype=float)
    q_up = np.asarray(res["up"])
    q_down = np.asarray(res["down"])
    n = len(hist)
    _day_arr = warm["t"].dt.date.values
    trig_red = detect_red_decay(hist, q_down, q_up, day=_day_arr)
    trig_blue = detect_blue_decay(hist, q_up, q_down, day=_day_arr)
    trig_red_m = detect_red_decay(hist, q_down, q_up, skip_rise_mult=True, day=_day_arr)
    trig_blue_m = detect_blue_decay(hist, q_up, q_down, skip_rise_mult=True, day=_day_arr)
    peak_red = {idx: pk for idx, pk in trig_red}
    peak_blue = {idx: pk for idx, pk in trig_blue}
    peak_red_m = {idx: pk for idx, pk in trig_red_m}
    peak_blue_m = {idx: pk for idx, pk in trig_blue_m}
    set_red = set(peak_red.keys())
    set_blue = set(peak_blue.keys())
    # ---------- ch25 (2026-10-21 用户: 中强跟随过滤用, 因果) ----------
    _ch25 = np.full(len(hist), np.nan)
    for _ci in range(len(hist)):
        _w = hist[max(0, _ci-24):_ci+1]; _w = _w[~np.isnan(_w)]
        if len(_w) >= 13:
            _ch25[_ci] = _w.mean()
    # ---------- 9mK 聚合 (2026-10-16 反弹惩罚用) ----------
    # 9mK: 09:30起每9分钟一组3根3mK; 对每根3m bar, 取前4个走完9mK的高低点
    import os as _os9
    _REBOUND_RED = True
    _REBOUND_BLUE = False
    _BIGK_BLOCK_ALL = False
    _ST_ONLY = False
    _HYBRID = True
    _HYB_VARIANT = "G"  # A/B/C/D/E/F/G (G=V_C跳空逻辑+非跳空日全天ST; 规则见主循环hybrid块; F的5-8天按QQE全天无切换)
    _WIN_AFTER_ST_IMM = True
    _FLIPCHECK = True
    _HYB_11SAME = True
    # STONLY 2026-11-01 用户12:14纠正: 窗口出来马上开只在ST段(_hyb_run_st)触发, QQE段窗口退出不马上开 (翻转部分不动)
    _REBOUND_SLOPE = 2.0  # 2026-10-16 反弹惩罚斜率参数
    _CUM_SLOPE_TH = 0.05  # 累计slope阈值
    _STRONG_TREND = True
    _ST_FOLLOW = True
    _ST_CH25_MIN = 0.0  # 2026-10-21: 强趋势跟随ch25过滤，0=关闭
    _ST_HIST_MIN = 0.0  # 2026-10-22: 强趋势跟随hist下限，|h|<X则挡，0=关闭
    _ST_TRUE_ONLY = False
    _BETA_FLIP_SKIP = 0.0  # 2026-10-22: beta>阈值时跳过当前翻仓信号，0=关闭
    _BETA_CLOSE_ONLY = False
    _BETA_S5_THRESH = 0.5  # 2026-10-22: beta跳过的slope5上限，设999=不限制
    _BETA_ABSS5_MAX = 999.0  # 2026-10-22: abs(slope5)超过此值不跳过（动量强时放行V转）
    _BETA_CALL_ONLY = False
    _BETA_EMA_ALPHA = 0.0  # 2026-10-22: beta计算前对cumslope做EMA平滑, 0=不用
    _ST_FLIP = False
    _DAYREGIME_FLIP = False
    _DAYREGIME_FOLLOW = False
    _FLIP_S9_GATE = False
    _MIDTREND_FLIP = False
    _MIDTREND_FOLLOW = False
    _MT_FOLLOW_CH25 = 0.0  # 2026-10-21 用户: 中强跟随ch25门(0=不过滤)
    _MT_FOLLOW_DYNTH = False
    _MT_DYNTH_CHC = 0.25  # ch25系数
    _MT_DYNTH_S9C = 2.0  # s9系数
    _MT_FOLLOW_NEWWAVE = False
    _MT_NEWWAVE_MAXCONSEC = 4  # 新波: 往前同色连续不超过N根
    _MT_PUT_CH12 = False
    _MT_PUT_S9RANGE = False
    _MT_PUT_H10 = False
    _MT_CH12 = False
    _MT_H10 = False
    _MT_S9MAX = 0.0  # 2026-10-21 用户: 中强跟随(红蓝)要求|s9|<=X (0=不过滤)
    _MT_GRAY_OK = True
    _MILD_PULLBACK_FILTER = False
    _MILD_SLOPE_TH = 0.02  # slope5阈值
    _MILD_DROP_RATIO = 0.7  # 跌幅比例
    _DOWNTREND_FLIPPUT_FILTER = False
    _DOWNTREND_SLOPE_TH = 0.02  # slope5阈值
    _warm_t9 = warm["t"]
    _warm_date9 = _warm_t9.dt.date.astype(str).values
    _9m_h4 = np.full(len(warm), np.nan)  # 前4个走完9mK的最高
    _9m_l4 = np.full(len(warm), np.nan)  # 前4个走完9mK的最低
    _9m_cumslope = np.full(len(warm), np.nan)  # 累计slope(从开盘)
    _9m_cumslope_1030 = np.full(len(warm), np.nan)  # 强趋势专用cumslope(从10:30, 2026-10-18)
    _9m_cumslope_2h = np.full(len(warm), np.nan)  # 2小时滚动cumslope(2026-10-19 用户: 最多看过去2小时)
    _9m_cumslope_ema = np.full(len(warm), np.nan)  # EMA版cumslope(2026-10-19 用户: 不要忘太快)
    _9m_slope5 = np.full(len(warm), np.nan)  # 2026-10-17 温和回调过滤: 最近5根走完9mK收盘价回归斜率
    _9m_gain2 = np.full(len(warm), np.nan)  # 过去2根走完9mK收盘价差(max-min)
    _9m_low3min = np.full(len(warm), np.nan)  # 过去3根走完9mK最低low
    _9m_prev_o = np.full(len(warm), np.nan)  # 上一根走完9mK的开
    _9m_prev_c = np.full(len(warm), np.nan)  # 上一根走完9mK的收
    for _d in sorted(set(_warm_date9)):
        _idx = np.where(_warm_date9 == _d)[0]
        if len(_idx) < 6:
            continue
        _t0 = _warm_t9.iloc[_idx[0]]
        _day0 = _t0.replace(hour=9, minute=30, second=0, microsecond=0)
        _k9 = [int((_warm_t9.iloc[_j] - _day0).total_seconds() // 540) for _j in _idx]
        _k9 = np.array(_k9)
        _maxk = _k9.max()
        _9h, _9l, _9c, _9o = {}, {}, {}, {}
        for _kk in range(_maxk + 1):
            _m = _idx[_k9 == _kk]
            if len(_m) == 0:
                continue
            _9h[_kk] = float(high[_m].max())
            _9l[_kk] = float(low[_m].min())
            _9c[_kk] = float(close[_m[-1]])
            _9o[_kk] = float(_open_arr[_m[0]])
        for _ii, _j in enumerate(_idx):
            _kk = _k9[_ii]
            _prev = _kk - 1  # 上一根走完的9mK
            _kk4 = [_prev - 3, _prev - 2, _prev - 1, _prev]
            if all(_k in _9h for _k in _kk4):
                _9m_h4[_j] = max(_9h[_k] for _k in _kk4)
                _9m_l4[_j] = min(_9l[_k] for _k in _kk4)
            # 2026-10-17 温和回调翻P过滤: slope5/过去2根收盘价差/过去3根最低low (只用走完9mK, 无未来函数)
            _kk2 = [_prev - 1, _prev]
            if all(_k in _9c for _k in _kk2):
                _c1, _c2 = _9c[_kk2[0]], _9c[_kk2[1]]
                _9m_gain2[_j] = abs(_c1 - _c2)
            _kk3 = [_prev - 2, _prev - 1, _prev]
            if all(_k in _9l for _k in _kk3):
                _9m_low3min[_j] = min(_9l[_k] for _k in _kk3)
            # 上一根走完9mK的开/收 (2026-10-18 动量死亡drop9m用)
            if _prev in _9o and _prev in _9c:
                _9m_prev_o[_j] = _9o[_prev]
                _9m_prev_c[_j] = _9c[_prev]
            # slope5: 有多少根用多少根（最少2根，最多5根），不等全齐
            _kk5_all = [_prev - 4, _prev - 3, _prev - 2, _prev - 1, _prev]
            _kk5_avail = [_k for _k in _kk5_all if _k in _9c]
            if len(_kk5_avail) >= 2:
                _ys = np.array([_9c[_k] for _k in _kk5_avail], dtype=float)
                _xs = np.arange(len(_kk5_avail), dtype=float)
                _xm, _ym = _xs.mean(), _ys.mean()
                _den = ((_xs - _xm) ** 2).sum()
                if _den > 0:
                    _9m_slope5[_j] = ((_xs - _xm) * (_ys - _ym)).sum() / _den
        # 累计slope: (上一根走完9m收 - 当日首根9m收) / 已走完根数 (无未来函数)
        if 0 in _9c:
            _open_c = _9c[0]
            for _ii, _j in enumerate(_idx):
                _kk = _k9[_ii]
                _prev = _kk - 1  # 上一根走完的9mK
                if _prev >= 1 and _prev in _9c:
                    _9m_cumslope[_j] = (_9c[_prev] - _open_c) / _prev
        # 强趋势专用cumslope: 从10:30开始算 (2026-10-18 用户)
        # 10:30是9m bar 6 (09:30=0, 每9分钟一根, 3600秒//540=6)
        if 6 in _9c:
            _open_c1030 = _9c[6]
            for _ii, _j in enumerate(_idx):
                _kk = _k9[_ii]
                _prev = _kk - 1
                if _prev > 6 and _prev in _9c:
                    _9m_cumslope_1030[_j] = (_9c[_prev] - _open_c1030) / (_prev - 6)
        # 2小时滚动cumslope (2026-10-19 用户): 最多看过去2小时 ≈ 13根9m bar
        # 有多少用多少 (早盘不够13根时用实际根数)
        for _ii, _j in enumerate(_idx):
            _kk = _k9[_ii]
            _prev = _kk - 1  # 上一根走完的9mK
            _start = _prev - 13  # 2小时前
            # 找到实际可用的最早bar (不能早于当日首根)
            _s = max(0, _start)
            if _prev >= 1 and _prev in _9c and _s in _9c and _prev > _s:
                _9m_cumslope_2h[_j] = (_9c[_prev] - _9c[_s]) / (_prev - _s)
        # EMA版cumslope (2026-10-19 用户): 对9m逐根涨跌做EMA, alpha越小忘得越慢
        # _EMA_ALPHA 环境变量控制, 默认0.15
        _ema_alpha = 0.15
        _ema_val = None
        _sorted_ks = sorted(_9c.keys())
        _ema_by_k = {}
        for _k in _sorted_ks:
            if _k == 0:
                _ema_by_k[_k] = 0.0
                _ema_val = 0.0
                continue
            _pk = _k - 1
            if _pk not in _9c:
                continue
            _diff = _9c[_k] - _9c[_pk]  # 本根涨跌
            if _ema_val is None:
                _ema_val = _diff
            else:
                _ema_val = _ema_alpha * _diff + (1 - _ema_alpha) * _ema_val
            _ema_by_k[_k] = _ema_val
        for _ii, _j in enumerate(_idx):
            _kk = _k9[_ii]
            _prev = _kk - 1
            if _prev in _ema_by_k:
                _9m_cumslope_ema[_j] = _ema_by_k[_prev]
    def _rebound_factor_red(i):
        # 红侧: rebound_rate = (close-l4)/(h4-l4); factor = min(1, max(0.2, SLOPE*rate))
        # 累计slope门: 只有强下跌日(cumslope < -阈值)才启用惩罚, 否则不罚
        _cs = _9m_cumslope[i]
        if np.isnan(_cs) or _cs >= -_CUM_SLOPE_TH:
            return 1.0
        if np.isnan(_9m_h4[i]) or np.isnan(_9m_l4[i]):
            return 1.0
        _rng = _9m_h4[i] - _9m_l4[i]
        if _rng <= 0:
            return 1.0
        _rate = (float(close[i]) - _9m_l4[i]) / _rng
        return min(1.0, max(0.2, _REBOUND_SLOPE * _rate))
    def _rebound_factor_blue(i):
        # 蓝侧: drop_rate = (h4-close)/(h4-l4); factor = min(1, max(0.2, SLOPE*rate))
        if np.isnan(_9m_h4[i]) or np.isnan(_9m_l4[i]):
            return 1.0
        _rng = _9m_h4[i] - _9m_l4[i]
        if _rng <= 0:
            return 1.0
        _rate = (_9m_h4[i] - float(close[i])) / _rng
        return min(1.0, max(0.2, _REBOUND_SLOPE * _rate))
    def _st_gate(i, hm):
        # 2026-10-19 定版(用户): 从开盘cumslope, 11:00-15:00(美东)且cumslope<-0.20
        if not _STRONG_TREND:
            return False
        if hm < "11:00" or hm >= "15:00":
            return False
        _cs = _9m_cumslope[i]
        if np.isnan(_cs) or _cs >= -0.20:
            return False
        return True
    def _st_gate_up(i, hm):
        # 2026-10-19 定版(用户): 从开盘cumslope, 11:00-15:00(美东)且cumslope>+0.15
        if not _STRONG_TREND:
            return False
        if hm < "11:00" or hm >= "15:00":
            return False
        _cs = _9m_cumslope[i]
        if np.isnan(_cs) or _cs <= 0.15:
            return False
        return True
    def _dayregime(i, hm):
        # 2026-10-21 用户: 日趋势regime, running cumslope ±0.08, 11:00-15:00(美东)
        # 返回 1=强涨日, -1=强跌日, 0=正常日; 只放宽顺趋势方向
        if hm < "11:00" or hm >= "15:00":
            return 0
        if i >= len(_9m_cumslope):
            return 0
        _cs = _9m_cumslope[i]
        if np.isnan(_cs):
            return 0
        if _cs > 0.08:
            return 1
        if _cs < -0.08:
            return -1
        return 0
    def _midtrend_regime(i, hm):
        # 2026-10-21 用户: 中强趋势regime, cumslope定调
        # MIDTREND_REGIME=open: 从开盘cumslope; =ema: EMA cumslope(EMA_ALPHA, 默认0.15)
        # MIDTREND_TH: 阈值, 默认0.1; 11:00-15:00(美东)
        # 返回 1=强涨日, -1=强跌日, 0=正常日
        if hm < "11:00" or hm >= "15:00":
            return 0
        _mode = "open"
        _th = 0.1
        if _mode == "ema":
            _arr = _9m_cumslope_ema
        else:
            _arr = _9m_cumslope
        if i >= len(_arr):
            return 0
        _cs = _arr[i]
        if np.isnan(_cs):
            return 0
        if _cs > _th:
            return 1
        if _cs < -_th:
            return -1
        return 0
    def _mild_pullback_blocks_flip_put(i):
        # 2026-10-17 温和回调翻P过滤: 温和上升趋势中单根大阴线回调, 屏蔽翻P (破位则放行)
        if not _MILD_PULLBACK_FILTER:
            return False
        _s5 = _9m_slope5[i]
        if np.isnan(_s5) or _s5 <= _MILD_SLOPE_TH:
            return False
        _o, _c = float(_openv[i]), float(close[i])
        if not (_c < _o):  # 必须是阴线
            return False
        _g2 = _9m_gain2[i]
        if np.isnan(_g2) or _g2 <= 0:
            return False
        if (_o - _c) < _MILD_DROP_RATIO * _g2:  # 跌幅>=70%过去2根9mK收盘价差
            return False
        _l3 = _9m_low3min[i]
        if not np.isnan(_l3) and _c < _l3:  # 收在过去3根9mK最低点下方=破位, 不屏蔽
            return False
        return True
    def _downtrend_blocks_flip_put(i):
        # 2026-10-17 下跌震荡翻P过滤: slope5<0.02(下跌/震荡)时翻P亏钱, 直接屏蔽; 上涨时放行
        if not _DOWNTREND_FLIPPUT_FILTER:
            return False
        _s5 = _9m_slope5[i]
        if np.isnan(_s5):
            return False
        return _s5 < _DOWNTREND_SLOPE_TH
    actions = []  # (bar_idx, time_str, action, direction, price, reason)
    pos = None  # None | 'call' | 'put'
    last_action_bar = -999
    _streak_block_until = -999
    _resume_block_put_until = -999
    _resume_block_call_until = -999
    _resume_block_put_peak = 0
    _stall_reopen_dir = None  # 停滞平仓后重开方向 ("call"/"put"/None)
    _stall_reopen_hist = 0  # 停滞时的|hist|
    _stall_reopen_ws = -1  # 停滞时的波浪起点
    _stall_reopen_bar = -1  # 停滞时的bar下标
    _resume_block_call_peak = 0
    _entry_peak = 0
    _decay_entry_hist = 0
    _close_hist = 0  # 2026-10-21 HUIBU_S9: 最近一次平仓bar的|hist| (任何原因平仓都记, 开仓清零, 不跨日)
    _follow_entry_bar = None  # 跟随开仓的bar, 下一根缩80%即平 (2026-10-03 用户)
    _follow_do_div_wk = True  # 波浪5根内才查背离 (2026-10-03 用户)
    _DIV_THRESH = 0.2  # 跟随背离止损阈值(点); sweep用, 默认0.2
    _DIV_QQE_GATE = False
    _NO_DASH = True
    _USE_30S_STOP = False
    _30S_THRESH = 0.5  # 30秒止损阈值(点)
    _follow_entry_hist = 0
    # 碎波暂停 (2026-10-04 用户)
    _frag_pause_until_wk = -1
    _follow_pnls_wk = []  # list of (dir, peak, pnl, date)
    _follow_entry_dir_wk = None
    _follow_entry_peak_wk = 0
    _follow_entry_spy_wk = 0
    _follow_entry_date_wk = ""
    # 期权止损20%建模 (2026-10-04 用户): 跟踪期权价值, 跌破80%即平, 不设止盈
    _stop_entry_dir = None
    _stop_entry_px = 0
    _stop_entry_bar = -1
    # 止损窗口参数 (2026-10-28 用户): 止损触发后给N根窗口, 收盘回来50%平, 窗口内翻仓/平仓不屏蔽、跟随屏蔽, 走完强制平
    _STOP_WIN_BARS = 6
    _STOP_WIN_RECOV = 0.50
    _stop_win = None  # 当前止损窗口, None=无窗口; 有窗口时为dict(dr, trig_bar, entry_px, trig_close, threshold)
    # 大K双机制 (2026-10-09 用户): 等反弹+不跟随
    _bigwait_target = None
    _bigwait_bars = 0
    _bigwait_is_put = False
    # 大K止盈 (2026-10-09 用户): ≥4.0点顺向大K → 3根后止盈平仓
    _BIGTP_N = 3
    _bigtp_bar = None
    _bigtp_count = 0
    # 3-bar大K (空头版, 宽松, XX=2.5; 2026-10-11): 与单根大K完全独立的变量, k3_前缀
    _K3_THRESH = 2.5
    _K3_TP_N = 3
    k3_wait_target = None
    k3_wait_bars = 0
    k3_wait_is_put = False
    k3_wait_count = 0
    k3_tp_bar = None
    k3_tp_count = 0
    k3_skip_count = 0
    _fav_sign = 0  # 1=call/-1=put
    _recent_flips = []  # 反复翻仓惩罚: 最近flip的bar (2026-10-03 用户)
    _put_blue_ws = None  # 翻仓开put时的蓝波起点, 防止同波"蓝出现"误杀
    _call_red_ws = None  # 翻仓开call时的红波起点, 防止同波"红出现"误杀
    _call_gap_wk = 0  # (2026-10-06: 持call时2根超短/空柱后算新波，与watcher.py同步)
    _put_gap_wk = 0  # (2026-10-06: 持put时2根超短/空柱后算新波，与watcher.py同步)
    was_on_top = False
    was_on_bottom = False
    _was_above_zero_wk = False  # 2026-10-07 用户: 波浪管理 - call持仓期间是否上过≥0
    _is_red_flip_wk = False  # 2026-10-07 用户: 是否红波翻仓带出的call
    _was_below_zero_wk = False  # 2026-10-07 用户: 波浪管理 - put持仓期间是否下过≤0
    wait_red = False
    wait_blue = False
    wait_red_count = 0
    wait_blue_count = 0

    def is_true_blue(j):
        return bool(q_up[j]) and abs(hist[j]) >= SIG_MIN_LEN
    def is_true_red(j):
        return bool(q_down[j]) and abs(hist[j]) >= SIG_MIN_LEN
    # 2026-10-05 用户: 同波判断, 空柱(|h|<=3)断波, 灰柱不断, 只有真反色或空柱切断
    def _ws_cont_blue(j):
        return abs(hist[j]) > 3 and not is_true_red(j)
    def _ws_cont_red(j):
        return abs(hist[j]) > 3 and not is_true_blue(j)
    def is_exit_red(j):
        return bool(q_down[j]) and abs(hist[j]) >= 6
    def is_exit_blue(j):
        return bool(q_up[j]) and abs(hist[j]) >= 6
    def prev_wave_color_gw(j, max_lookback=40):
        """往回看找上一个有意义的波浪 (2026-10-03 用户: 续波跟随)。
        (2026-10-07 用户: 最近4根里有|h|<3短柱也算波断=新波),
        跳过最近4根, 往回找第一根 |h|>=8, (2026-10-07 用户: 碰到|h|<3短柱=波断=新波直接返回None),
        返回 'red'/'blue'/None. 不跨天."""
        # 2026-10-07 用户: 先检查最近4根有没有短柱
        for k in range(j - 1, max(j - 5, -1), -1):
            if k < 0 or k >= len(hist):
                break
            try:
                if _day_arr[k] != _day_arr[j]:
                    break
            except Exception:
                pass
            if abs(hist[k]) < 3:
                return None
        for k in range(j - 4, max(-1, j - 4 - max_lookback), -1):
            if k < 0 or k >= len(hist):
                break
            try:
                if _day_arr[k] != _day_arr[j]:
                    break
            except Exception:
                pass
            h = hist[k]
            if abs(h) < 3:
                return None  # 2026-10-07 用户: 短柱=波断=新波
            if abs(h) <= 3:
                continue
            if abs(h) >= 8:
                return 'red' if h < 0 else 'blue'
        return None
    def wave_len_gw(j, sign, max_lookback=30):
        """从 j 往回数 sign 方向波的长度 (2026-10-03 用户: 长波优化)。
        sign=-1数红波, sign=1数蓝波. |h|<=3最多容忍连续2根, 真反色即断.
        不跨交易日 (2026-10-03 用户反馈08-28)."""
        n = 0
        _cs = 0
        for k in range(j, max(-1, j - max_lookback), -1):
            if k < 0 or k >= len(hist):
                break
            try:
                if _day_arr[k] != _day_arr[j]:
                    break
            except Exception:
                pass
            h = hist[k]
            if np.isnan(h):
                break
            # 2026-10-07 用户: |h|<3短柱直接断波, 不容忍
            if abs(h) < 3:
                break
            if h * sign > 0 and abs(h) > 3:
                n += 1
                _cs = 0
            elif abs(h) <= 3 and h * sign >= 0:
                # (2026-10-07 fix: 零轴下方的小柱子也算断波)
                _cs += 1
                if _cs > 2:
                    break
            else:
                break
        return n

    start_idx = 0  # live: input itself is warmup+today
    # 周末尾bar (2026-10-04 fix: 之前跑到数据末尾去了)
    end_idx = n  # live: run to the last input bar
    _day_start = start_idx
    _prev_day = None
    _day_gap_hyb = 0.0  # hybrid用带符号跳空 (当日09:30开-前日15:57收, 3m; 方向下午用)
    _hyb_switched = False  # 当日11:00切换是否已执行 (每天重置)
    _hyb_active_today = False  # 当日是否走hybrid切换逻辑 (跳空日且非F的5-8天)
    _hyb_morn_today = "qqe"  # 当日早上腿: qqe/inv/skip
    _hyb_aft_today = "st"  # 当日下午腿: st/qqe/dir (dir=上跳qqe/下跳st, 在下午逻辑内再分)
    # 2026-10-08 fix: 开盘gap ignore每天只算一次
    _day_ign_n = 1
    _day_ign_day = -1
    # 2026-10-08 用户: 躲鞭 - 延迟翻仓只一次
    _pending_flip_to = None
    _pending_flip_bar = -1
    _pending_flip_reason = ""
    # 2026-10-17 flip-P slope5分析: 记录翻P开仓的slope5和逐笔gamma P&L (env DUMP_FLIPPUT=1 开启, 默认关闭)
    _DUMP_FLIPPUT = False
    _flip_put_slope5 = {}  # 翻P开仓bar idx -> _9m_slope5
    _flip_put_is_huibu = {}  # 翻P开仓bar idx -> 是否长波回补(True=回补开put, False=真翻仓开put)
    _flip_put_truecolor = {}  # 翻P开仓bar idx -> 信号bar QQE真色: blue/red/gray (hist>3蓝, hist<-3红, 否则灰)
    _flip_put_drop_ratio = {}  # 翻P开仓bar idx -> 跌幅力度 drop/gain2 (阴线且gain2>0时, 否则None)
    _flip_put_broke_support = {}  # 翻P开仓bar idx -> 是否破位 (3mK收 < 过去3根走完3mK最低low)
    _flip_put_consec_bear = {}  # 翻P开仓bar idx -> 连续阴线计数 (含当前bar, 收<开连续几根)
    _all_trades_raw = []  # (dir, open_px, close_px, is_flip_put, slope5_or_None, is_huibu_or_None, truecolor_or_None, drop_ratio_or_None, broke_support_or_None, consec_bear_or_None), 按时间顺序
    # 2026-10-18 动量死亡/跟随失败平仓反事实分析: 记录动量死亡平仓(put/call)与跟随失败平仓 (env DUMP_MOMDEATH=1 开启, 默认关闭)
    _DUMP_MOMDEATH = False
    _MOMDEATH_FLIP = False
    _MDEATH_NOPEAK = False
    _MDEATH_PEAK8 = False
    _MDEATH_PEAK6 = False
    _MDEATH_GRAY5 = False
    _MDEATH_HUIBU = False
    _FOLLOWFAIL_HUIBU = False
    _FOLLOW_PREVLEN = False
    _LONGHUIBU_MULT = False
    _HUIBU_S9 = False
    _HUIBU_S9_THRESH = 0.05  # 2026-10-21: 回补|s9|阈值
    _HUIBU_S9_MODE = "s9"  # 2026-10-21: 回补趋势度量 s9|cumslope, 默认s9行为不变
    _HUIBU_ABSHIST = 0.0  # 2026-10-21: 回补信号bar绝对|hist|下限, 0=关闭(恒为3)
    _WAVE_BRK3 = False
    _momdeath_raw = []  # (tstr, dir, px_close, truecolor, close_idx, slope5, cumslope, drop9m, exit_type), 按时间顺序; exit_type: momdeath/followfail
    _last_delay_bar = -10
    # 躲鞭统计计数器 (2026-10-08 分析用)
    _n_delay = 0
    _n_confirm = 0
    _n_cancel = 0
    _n_mild_blocked = 0  # 2026-10-17 温和回调翻P过滤: 被屏蔽的翻P信号数
    _n_beta_skip = 0  # 2026-10-22 BETA_FLIP_SKIP: 被跳过的翻仓信号数
    _n_downtrend_blocked = 0  # 2026-10-17 下跌震荡翻P过滤: 被屏蔽的翻P信号数
    _n_beta_skip = 0  # 2026-10-22 beta跳过翻仓: 被跳过的翻仓信号数
    # 止损变量即时初始化 (2026-10-11 fix: 开仓当根立即用真实入场价初始化, 不再延迟到下一根bar)
    def _init_stop_vars(_pos, _entry_px, _bar_i):
        nonlocal _stop_entry_dir, _stop_entry_px, _stop_entry_bar, _stop_win
        _stop_win = None  # 新仓位一律无窗口 (翻仓/跟随开仓都走这里, 旧窗口绝不带到新仓位)
        _stop_entry_dir = _pos
        _stop_entry_px = _entry_px
        _stop_entry_bar = _bar_i
    # Supertrend趋势数组 (2026-11-01 ST-only实验, 与gen_wave_today.py一致: TradingView final-band版, 10/hl2/3, RMA ATR)
    # 在warm全部bar (warmup+本周) 上算, 主循环下标i直接对齐; _st_trend: 1=上涨Buy, -1=下跌Sell
    _st_h = np.asarray(high, dtype=float); _st_l = np.asarray(low, dtype=float); _st_c = np.asarray(close, dtype=float)
    _st_hl2 = (_st_h + _st_l) / 2
    _st_tr = np.empty(len(_st_c)); _st_tr[0] = _st_h[0] - _st_l[0]
    for _si in range(1, len(_st_c)):
        _st_tr[_si] = max(_st_h[_si]-_st_l[_si], abs(_st_h[_si]-_st_c[_si-1]), abs(_st_l[_si]-_st_c[_si-1]))
    _st_atr = np.full(len(_st_c), np.nan)
    if len(_st_c) >= 10:
        _st_atr[9] = _st_tr[:10].mean()
        for _si in range(10, len(_st_c)):
            _st_atr[_si] = (_st_atr[_si-1]*9 + _st_tr[_si]) / 10
    _st_bu = _st_hl2 + 3*_st_atr; _st_bl = _st_hl2 - 3*_st_atr
    _st_fu = np.full(len(_st_c), np.nan); _st_fl = np.full(len(_st_c), np.nan); _st_trend = np.zeros(len(_st_c), dtype=int)
    if len(_st_c) >= 10:
        _st_fu[9] = _st_bu[9]; _st_fl[9] = _st_bl[9]; _st_trend[9] = 1
        for _si in range(10, len(_st_c)):
            if np.isnan(_st_atr[_si]):
                _st_trend[_si] = _st_trend[_si-1]; continue
            _st_fu[_si] = _st_bu[_si] if (_st_bu[_si] < _st_fu[_si-1] or _st_c[_si-1] > _st_fu[_si-1]) else _st_fu[_si-1]
            _st_fl[_si] = _st_bl[_si] if (_st_bl[_si] > _st_fl[_si-1] or _st_c[_si-1] < _st_fl[_si-1]) else _st_fl[_si-1]
            if _st_c[_si] > _st_fu[_si-1]: _st_trend[_si] = 1
            elif _st_c[_si] < _st_fl[_si-1]: _st_trend[_si] = -1
            else: _st_trend[_si] = _st_trend[_si-1]
    for i in range(start_idx, end_idx):
        _n_acts_before = len(actions)  # 2026-10-21 HUIBU_S9: bar末扫描本bar平/开
        t = warm["t"].iloc[i]
        if t.strftime("%Y-%m-%d") in _SKIP_DATES:
            continue
        day = t.strftime("%m-%d")
        hm = t.strftime("%H:%M")
        if day != _prev_day:
            _day_start = i
            _prev_day = day
            _hyb_switched = False  # hybrid: 每天11:00切换重置
            # 碎波暂停不跨天 (2026-10-07 fix)
            _frag_pause_until_wk = -1
            _follow_pnls_wk = []
            _follow_entry_dir_wk = None
            _close_hist = 0  # 2026-10-21 HUIBU_S9: 不跨日
            _stop_win = None  # 止损窗口不跨日
            # 开盘gap ignore (2026-10-08 用户, 回测最优): gap>2.0则ignore前2根, 否则1根
            # 2026-10-08 fix: 每天只算一次, 不能每根bar重算 (第二根bar的gap是盘中价差)
            if i == _day_start and i == 0:
                # 输入首日无前一日: 按任务fallback, gap=0 (非跳空, 全天ST), ign=0
                _day_gap_hyb = 0.0
                _day_ign_n = 0
                _day_ign_day = _day_start
                _hyb_active_today = False
                _hyb_morn_today = "st"
                _hyb_aft_today = "st"
            if i == _day_start and i > 0:
                # gap fallback (live): 首根open缺失 -> 当日最早可用open;
                # 无前一日收盘 -> gap=0 (非跳空, 全天ST)。与 day_info() 同链。
                _day_open_px = float('nan')
                _day_i = i
                while _day_i < len(warm) and warm["t"].iloc[_day_i].strftime("%m-%d") == day:
                    _ov = float(warm["open"].iloc[_day_i])
                    if _ov == _ov:
                        _day_open_px = _ov
                        break
                    _day_i += 1
                _prev_close_px = float(close[i-1])
                if _day_open_px != _day_open_px or _prev_close_px != _prev_close_px:
                    _gap = 0.0
                    _day_gap_hyb = 0.0
                else:
                    _gap = abs(_day_open_px - _prev_close_px)
                    _day_gap_hyb = _day_open_px - _prev_close_px
                _day_ign_n = 2 if _gap > 2.0 else 0
                _day_ign_day = _day_start
                # hybrid当日规则 (每天开盘定一次, 全天不变)
                _hyb_gap_abs = abs(_day_gap_hyb)
                _hyb_is_big_d = _hyb_gap_abs >= 10.0
                _hyb_is_gap_d = _hyb_gap_abs >= 5.0
                _hyb_active_today = _hyb_is_gap_d and not (_HYB_VARIANT == "F" and not _hyb_is_big_d)
                if _hyb_is_big_d:
                    _hyb_morn_today = "skip"
                elif _HYB_VARIANT in ("A", "C", "F", "G"):
                    _hyb_morn_today = "qqe"  # G跳空日早上同C (QQE早上)
                elif _HYB_VARIANT in ("B", "D"):
                    _hyb_morn_today = "inv"
                elif _HYB_VARIANT == "E":
                    _hyb_morn_today = "skip"
                else:
                    _hyb_morn_today = "qqe"
                if _HYB_VARIANT in ("A", "B", "E"):
                    _hyb_aft_today = "st"
                elif _HYB_VARIANT in ("C", "D", "G"):
                    _hyb_aft_today = "dir"  # G跳空日下午同C (方向下午: 上跳QQE/下跳ST)
                elif _HYB_VARIANT == "F":
                    _hyb_aft_today = "qqe"
                else:
                    _hyb_aft_today = "st"
            elif _day_ign_day != _day_start:
                _day_ign_n = 1
            _ign_n_wk = _day_ign_n
        # 开盘ignore: 前N根跳过所有信号
        if i - _day_start < _ign_n_wk:
            continue
        px = float(close[i])
        # 2026-10-15 用户统一口径: 回测执行价用下一根开盘价
        # 2026-10-19 fix: 跨天时用本根收盘价（不读下个交易日的开盘价）
        _next_same_day = (i+1 < len(_openv)) and (warm["t"].iloc[i+1].strftime("%m-%d") == day)
        px_exec = float(_openv[i+1]) if _next_same_day else px
        # Hybrid精确版 (2026-11-01 用户, 非拼接): 跳空日早上腿+11:00强制ST切换, >=10早上空仓
        # 死规则: 11:00无条件强制切换 (不管QQE止损/止盈/冷冻/等待, 一律截止不带到下午; 切换块放EOD/止损/大K之前, 故切换绝不被拖延);
        # 11:00按ST趋势强制开ST仓 (信号=11:00 bar ST趋势, 执行=下一根开盘, 与回测执行口径一致; ST趋势为0则不开, 空仓等下午翻转);
        # 11:00后纯ST规则 (翻转/20%止损/冷冻/日终), QQE信号11:00后不起作用 (方向下午上跳QQE除外, 其从空仓等QQE信号, 无强制开);
        # 止损 (20%+冷冻) 与日终平为仓位风控 (非QQE信号), 全天适用 (早上+下午, 所有策略), 故11:00后仍适用; 非跳空日/F的5-8天=QQE全天 (含大K, 与基线一致).
        _hyb_run_st = False; _hyb_inv = False
        _hyb_phase = "qqe"; _hyb_bigk_block = _ST_ONLY
        if _HYBRID and _hyb_active_today:
            if not _hyb_switched and "11:00" <= hm < "15:55":
                # 11:00切换bar (首根>=11:00的bar, 每天一次; 15:55后不切换, 防EOD bar误开)
                _hyb_switched = True
                _hyb_11same_today = (_HYB_11SAME and _hyb_morn_today == "qqe" and (_hyb_aft_today == "qqe" or (_hyb_aft_today == "dir" and _day_gap_hyb > 0)))
                _hyb_phase = "qqe" if _hyb_11same_today else "switch"; _hyb_bigk_block = False if _hyb_11same_today else True
                if pos is not None and not _hyb_11same_today:
                    actions.append((i, f"{day}T{hm}", "平", pos, px_exec, "11:00强制切平" + pos))
                    pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; _call_gap_wk = 0; _put_gap_wk = 0; last_action_bar = i; was_on_top = was_on_bottom = False
                    wait_red = wait_blue = False
                    _follow_entry_bar = None
                    _hold_watch = None
                    _fav_peak = 0; _fav_sign = 0
                    k3_tp_bar = None; _bigtp_bar = None; _bigwait_target = None; _bigwait_bars = 0; k3_wait_target = None; k3_wait_bars = 0; _stop_win = None
                _hyb_aft_now = _hyb_aft_today
                if _hyb_aft_now == "dir":
                    _hyb_aft_now = "qqe" if _day_gap_hyb > 0 else "st"
                if _hyb_aft_now == "st":
                    if _st_trend[i] != 0:
                        _hyb_new = "call" if _st_trend[i] == 1 else "put"
                        actions.append((i, f"{day}T{hm}", "开", _hyb_new, px_exec, "11:00强制切开" + _hyb_new))
                        pos = _hyb_new; _init_stop_vars(_hyb_new, px, i); last_action_bar = i
                    continue  # 切换ST bar不再跑后续块 (防ST handler在切换bar立即翻走强制仓)
                # 下午QQE: 从空仓起, 本bar落到后续QQE分支 (不continue; 后续止损/大K块对空仓无作用, 大K触发需持仓故不触发)
            elif hm < "11:00":
                _hyb_phase = _hyb_morn_today
                if _hyb_morn_today == "skip":
                    _hyb_bigk_block = True
                    continue  # 早上空仓, 不交易 (本就空仓, 后续块对空仓无作用; continue挡QQE分支)
                elif _hyb_morn_today == "inv":
                    _hyb_bigk_block = True; _hyb_run_st = True; _hyb_inv = True  # 反向ST早上 (纯ST, 大K全挡; 落到ST handler反向执行)
                else:
                    _hyb_bigk_block = False  # QQE早上 (含大K, 与基线一致; 落到QQE分支)
            else:
                _hyb_aft_now = _hyb_aft_today
                if _hyb_aft_now == "dir":
                    _hyb_aft_now = "qqe" if _day_gap_hyb > 0 else "st"
                _hyb_phase = _hyb_aft_now
                if _hyb_aft_now == "st":
                    _hyb_bigk_block = True; _hyb_run_st = True; _hyb_inv = False  # ST下午 (纯ST, 大K全挡; 落到ST handler翻转)
                else:
                    _hyb_bigk_block = False  # QQE下午 (含大K; 落到QQE分支)
        elif _HYBRID:
            if _HYB_VARIANT == "G":
                # V_G非跳空日: 全天ST (同ST-only纯版: ST handler翻转+开盘直接开, 大K全挡; 落到ST handler, 其末尾continue挡QQE分支)
                _hyb_phase = "st"; _hyb_bigk_block = True; _hyb_run_st = True; _hyb_inv = False
            else:
                _hyb_phase = "qqe"; _hyb_bigk_block = False  # 非hybrid激活日 (非跳空/F 5-8) = QQE全天含大K
        # 日终平仓最最顶级优先级 (2026-10-27 用户令, 同实盘2026-10-24修复): 15:55到了一律平, 不隔夜; 等待状态一并清零
        if pos and hm >= "15:55":
            _eod_px = _eod_1m.get(str(_day_arr[i]), px)  # 找不到则回退到3m收盘
            actions.append((i, f"{day}T{hm}", "平", pos, _eod_px, "日终平仓(1m15:55)"))
            pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; _call_gap_wk = 0; _put_gap_wk = 0; last_action_bar = i; was_on_top = was_on_bottom = False
            wait_red = wait_blue = False
            _follow_entry_bar = None
            _hold_watch = None
            _fav_peak = 0; _fav_sign = 0
            k3_tp_bar = None; _bigtp_bar = None; _bigwait_target = None; _bigwait_bars = 0; k3_wait_target = None; k3_wait_bars = 0; _stop_win = None
            continue
        # 当日最后一根bar强制平 (2026-10-27 用户令): 提前收盘日没有15:55, 靠最后一根兜底, 不隔夜; 执行价=本根收盘(跨天规则)
        # 2026-11-02 根因修: 原 `not _next_same_day` 在实盘前缀输入下每轮误触发 (当前 bar 恒无下根);
        #   改为交易日历判定: 仅提前收盘日 (NYSE 13:00 收盘, _is_early_close_day) 才走最后一根块,
        #   且只在 12:57/13:00 两根收盘 bar 中的最后一根触发 (回测 cache 含 13:00 bar、实盘 to_3m
        #   末根 12:57, not _next_same_day 用于二选一, 不再单独决定日终)。
        #   平时 (含前缀) 永不触发, 15:57 由 15:55 块覆盖。hm 与日期皆 ET 墙钟, 夏令时无关。
        if pos and _is_early_close_day(warm["t"].iloc[i]) and hm in ("12:57", "13:00") and not _next_same_day:
            actions.append((i, f"{day}T{hm}", "平", pos, px_exec, "日终平仓(最后一根)"))
            pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; _call_gap_wk = 0; _put_gap_wk = 0; last_action_bar = i; was_on_top = was_on_bottom = False
            wait_red = wait_blue = False
            _follow_entry_bar = None
            _hold_watch = None
            _fav_peak = 0; _fav_sign = 0
            k3_tp_bar = None; _bigtp_bar = None; _bigwait_target = None; _bigwait_bars = 0; k3_wait_target = None; k3_wait_bars = 0; _stop_win = None
            continue
        # 止损窗口 (2026-10-28 用户): 止损触发后给N根窗口
        #   操作1: 任意窗口bar收盘回来50% (触发bar收盘到开仓价的50%处) -> 平仓
        #   操作2: 窗口内翻仓/平仓信号正常跑, 跟随信号屏蔽(跟随在空仓分支内, 持仓时不进)
        #   到期: N根走完还拿着原单 -> 强制平 (到期检查在信号分支后, 见循环底部)
        # 防冻设计: 窗口只进不出地数bar (不看价值, 不续期); 任何开仓/平仓/EOD/跨日都清窗口;
        #   窗口内不启动新等待 (等待会把窗口拖长, 正是旧冻结bug的根源)
        if _stop_win is not None and pos is None:
            _stop_win = None  # 安全网: 仓位已无则窗口作废
        if _stop_win is not None and pos is not None:
            _win_age = i - _stop_win["trig_bar"]
            if _win_age >= 1:
                _wdr = _stop_win["dr"]; _wth = _stop_win["threshold"]
                if (_wdr == "call" and px >= _wth) or (_wdr == "put" and px <= _wth):
                    actions.append((i, f"{day}T{hm}", "平", pos, px_exec, f"止损窗口回{int(_STOP_WIN_RECOV*100)}%平"))
                    pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; _call_gap_wk = 0; _put_gap_wk = 0; last_action_bar = i; was_on_top = was_on_bottom = False
                    wait_red = wait_blue = False
                    _follow_entry_bar = None
                    _hold_watch = None
                    _fav_peak = 0; _fav_sign = 0
                    _stop_entry_dir = None
                    k3_tp_bar = None; _bigtp_bar = None; _bigwait_target = None; _bigwait_bars = 0; k3_wait_target = None; k3_wait_bars = 0
                    _close_hist = abs(hist[i]) if not np.isnan(hist[i]) else 0
                    _stop_win = None
                    _stop_triggered = True
                    if _WIN_AFTER_ST_IMM and _hyb_run_st and _st_trend[i] != 0:
                        _wa_new = "call" if _st_trend[i] == 1 else "put"
                        actions.append((i, f"{day}T{hm}", "开", _wa_new, px_exec, f"窗口出来ST开{_wa_new}"))
                        pos = _wa_new; _init_stop_vars(_wa_new, px, i); last_action_bar = i
                    continue
                if _win_age <= _STOP_WIN_BARS:
                    # W15FZ (2026-10-28 用户: 窗口内屏蔽平仓, 等待机会平仓): 窗口bar直接冻结,
                    # 记标记后continue跳过信号分支 (平仓翻仓全不跑, 只留窗口自己的回弹平/到期平);
                    # W5到期平前移至此 (原信号分支后的到期检查因continue走不到, 2532行留作安全网)
                    actions.append((i, f"{day}T{hm}", "持", pos, px_exec, "止损窗口(续)"))
                    _stop_triggered = True
                    if _win_age == _STOP_WIN_BARS:
                        actions.append((i, f"{day}T{hm}", "平", pos, px_exec, "止损窗口6根平"))
                        pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; _call_gap_wk = 0; _put_gap_wk = 0; last_action_bar = i; was_on_top = was_on_bottom = False
                        wait_red = wait_blue = False
                        _follow_entry_bar = None
                        _hold_watch = None
                        _fav_peak = 0; _fav_sign = 0
                        _stop_entry_dir = None
                        k3_tp_bar = None; _bigtp_bar = None; _bigwait_target = None; _bigwait_bars = 0; k3_wait_target = None; k3_wait_bars = 0
                        _close_hist = abs(hist[i]) if not np.isnan(hist[i]) else 0
                        _stop_win = None
                        _stop_triggered = True
                        if _WIN_AFTER_ST_IMM and _hyb_run_st and _st_trend[i] != 0:
                            _wa_new = "call" if _st_trend[i] == 1 else "put"
                            actions.append((i, f"{day}T{hm}", "开", _wa_new, px_exec, f"窗口出来ST开{_wa_new}"))
                            pos = _wa_new; _init_stop_vars(_wa_new, px, i); last_action_bar = i
                        continue
                    continue
                if _win_age >= _STOP_WIN_BARS + 1:
                    # 安全网: W5到期平若被意外截断, W6到此强制平 (正常路径走不到这里, 计数应为0)
                    actions.append((i, f"{day}T{hm}", "平", pos, px_exec, "止损窗口6根平(补)"))
                    pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; _call_gap_wk = 0; _put_gap_wk = 0; last_action_bar = i; was_on_top = was_on_bottom = False
                    wait_red = wait_blue = False
                    _follow_entry_bar = None
                    _hold_watch = None
                    _fav_peak = 0; _fav_sign = 0
                    _stop_entry_dir = None
                    k3_tp_bar = None; _bigtp_bar = None; _bigwait_target = None; _bigwait_bars = 0; k3_wait_target = None; k3_wait_bars = 0
                    _close_hist = abs(hist[i]) if not np.isnan(hist[i]) else 0
                    _stop_win = None
                    _stop_triggered = True
                    if _WIN_AFTER_ST_IMM and _hyb_run_st and _st_trend[i] != 0:
                        _wa_new = "call" if _st_trend[i] == 1 else "put"
                        actions.append((i, f"{day}T{hm}", "开", _wa_new, px_exec, f"窗口出来ST开{_wa_new}"))
                        pos = _wa_new; _init_stop_vars(_wa_new, px, i); last_action_bar = i
                    continue
                # 窗口内落空: 翻仓/平仓信号正常跑 (等待触发检查在窗口内跳过, 见下)
        # 3-bar大K等反弹 (空头版, 2026-10-11): 持call+3根净跌超2.5 → 等30%反弹, 最多5根, 最高优先
        if k3_wait_target is not None:
            k3_wait_bars += 1
            _k3_hit = (px <= k3_wait_target) if k3_wait_is_put else (px >= k3_wait_target)
            if _k3_hit or k3_wait_bars >= 5:
                _tstr_kw = f"{day}T{hm}"
                actions.append((i, _tstr_kw, "平", pos, px_exec, f"3K等待平仓(等{k3_wait_bars}根)"))
                pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; _call_gap_wk = 0; _put_gap_wk = 0; last_action_bar = i; was_on_top = was_on_bottom = False
                wait_red = wait_blue = False
                _follow_entry_bar = None
                _hold_watch = None
                _fav_peak = 0; _fav_sign = 0
                k3_wait_target = None; k3_wait_bars = 0
                k3_wait_count += 1
                _stop_triggered = True
                continue
            else:
                _stop_triggered = True
                continue  # 继续等待, 跳过其他信号
        # 大K双机制1 等反弹 (2026-10-09 用户): ≥4.0点反向大K线 → 等50%反弹, 最多5根, 最高优先
        if _bigwait_target is not None:
            _bigwait_bars += 1
            _bw_hit = (px <= _bigwait_target) if _bigwait_is_put else (px >= _bigwait_target)
            if _bw_hit or _bigwait_bars >= 5:
                _tstr_bw = f"{day}T{hm}"
                actions.append((i, _tstr_bw, "平", pos, px_exec, f"大K等待平仓(等{_bigwait_bars}根)"))
                pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; _call_gap_wk = 0; _put_gap_wk = 0; last_action_bar = i; was_on_top = was_on_bottom = False
                wait_red = wait_blue = False
                _follow_entry_bar = None
                _hold_watch = None
                _fav_peak = 0; _fav_sign = 0
                _bigwait_target = None; _bigwait_bars = 0
                _stop_triggered = True
                continue
            else:
                _stop_triggered = True
                continue  # 继续等待, 跳过其他信号
        # 检查是否触发新的3K等待 (空头版, 宽松XX=2.5): 持call+3根净跌超2.5 → 等30%反弹
        if pos == "call" and i < len(high) and k3_wait_target is None and _stop_win is None and not _hyb_bigk_block:  # ST_ONLY纯版: 大K全不要
            try:
                _k3_net = None; _k3_low = None
                if i >= 2:
                    _k3_d0 = warm["t"].iloc[i].strftime("%Y-%m-%d")
                    _k3_d2 = warm["t"].iloc[i-2].strftime("%Y-%m-%d")
                    if _k3_d0 == _k3_d2:  # 不跨交易日
                        _k3_net = float(close[i]) - float(warm["open"].iloc[i-2])
                        _k3_low = min(float(low[i-2]), float(low[i-1]), float(low[i]))
                if _k3_net is not None and _k3_net < -_K3_THRESH and hm < "15:55":
                    _k3_drop = abs(_k3_net)
                    k3_wait_target = _k3_low + _k3_drop * 0.30
                    k3_wait_bars = 0
                    k3_wait_is_put = False
                    _tstr_kws = f"{day}T{hm}"
                    actions.append((i, _tstr_kws, "持", pos, px_exec, f"3K等待开始(净{_k3_net:.2f})"))
                    _stop_triggered = True
                    continue
            except Exception:
                pass
        # 检查是否触发新的等待
        if pos and i < len(high) and _stop_win is None and not _hyb_bigk_block:
            try:
                _bwo = float(warm["open"].iloc[i]); _bwh = float(high[i]); _bwl = float(low[i]); _bwc = float(close[i])
                _bwrng = _bwh - _bwl
                if _bwrng >= 4.0 and hm < "15:55":
                    _bw_bull = _bwc > _bwo
                    _bw_opp = (pos == "put" and _bw_bull) or (pos == "call" and not _bw_bull)
                    if _bw_opp:
                        _bigwait_target = (_bwh - _bwrng * 0.5) if pos == "put" else (_bwl + _bwrng * 0.5)
                        _bigwait_bars = 0
                        _bigwait_is_put = (pos == "put")
                        _tstr_bws = f"{day}T{hm}"
                        actions.append((i, _tstr_bws, "持", pos, px_exec, f"大K等待开始(幅度{_bwrng:.2f})"))
                        _stop_triggered = True
                        continue
            except Exception:
                pass
        # 3-bar大K止盈 (空头版, 2026-10-11): 持put+3根净跌超2.5 → 3根后止盈平仓, 最高优先
        if k3_tp_bar is not None and pos is None:
            k3_tp_bar = None  # 持仓已被其他路径平掉, 清除过期标记
        if k3_tp_bar is not None and pos:
            if i - k3_tp_bar >= _K3_TP_N:
                _tstr_ktp = f"{day}T{hm}"
                actions.append((i, _tstr_ktp, "平", pos, px_exec, f"3K止盈平仓(等{_K3_TP_N}根)"))
                pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; _call_gap_wk = 0; _put_gap_wk = 0; last_action_bar = i; was_on_top = was_on_bottom = False
                wait_red = wait_blue = False
                _follow_entry_bar = None
                _hold_watch = None
                _fav_peak = 0; _fav_sign = 0
                k3_tp_bar = None
                k3_tp_count += 1
                _stop_entry_dir = None
                _stop_triggered = True
                continue
            else:
                _stop_triggered = True
                continue  # 等待期间跳过其他信号
        # 大K止盈 (2026-10-09 用户): ≥4.0点顺向大K → 3根后止盈平仓, 最高优先(与等反弹同级)
        if _bigtp_bar is not None and pos is None:
            _bigtp_bar = None  # 持仓已被其他路径平掉, 清除过期标记
        if _bigtp_bar is not None and pos:
            if i - _bigtp_bar >= _BIGTP_N:
                _tstr_tp = f"{day}T{hm}"
                actions.append((i, _tstr_tp, "平", pos, px_exec, f"大K止盈平仓(等{_BIGTP_N}根)"))
                pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; _call_gap_wk = 0; _put_gap_wk = 0; last_action_bar = i; was_on_top = was_on_bottom = False
                wait_red = wait_blue = False
                _follow_entry_bar = None
                _hold_watch = None
                _fav_peak = 0; _fav_sign = 0
                _bigtp_bar = None
                _bigtp_count += 1
                _stop_entry_dir = None
                _stop_triggered = True
                continue
            else:
                _stop_triggered = True
                continue  # 等待期间跳过其他信号
        # 检查是否触发新的3K止盈等待 (空头版, 宽松XX=2.5): 持put+3根净跌超2.5 → 3根后止盈
        if pos == "put" and i < len(high) and k3_tp_bar is None and _stop_win is None and not _hyb_bigk_block:
            try:
                _k3_nett = None
                if i >= 2:
                    _k3_d0t = warm["t"].iloc[i].strftime("%Y-%m-%d")
                    _k3_d2t = warm["t"].iloc[i-2].strftime("%Y-%m-%d")
                    if _k3_d0t == _k3_d2t:  # 不跨交易日
                        _k3_nett = float(close[i]) - float(warm["open"].iloc[i-2])
                if _k3_nett is not None and _k3_nett < -_K3_THRESH and hm < "15:55":
                    k3_tp_bar = i
                    _tstr_ktps = f"{day}T{hm}"
                    actions.append((i, _tstr_ktps, "持", pos, px_exec, f"3K止盈开始(净{_k3_nett:.2f})"))
                    _stop_triggered = True
                    continue
            except Exception:
                pass
        # 检查是否触发新的止盈等待 (顺向大K)
        if pos and i < len(high) and _stop_win is None and not _hyb_bigk_block:
            try:
                _tpo = float(warm["open"].iloc[i]); _tph = float(high[i]); _tpl = float(low[i]); _tpc = float(close[i])
                _tprng = _tph - _tpl
                if _tprng >= 4.0 and hm < "15:55":
                    _tp_bull = _tpc > _tpo
                    _tp_fav = (pos == "call" and _tp_bull) or (pos == "put" and not _tp_bull)
                    if _tp_fav:
                        _bigtp_bar = i
                        _tstr_tps = f"{day}T{hm}"
                        actions.append((i, _tstr_tps, "持", pos, px_exec, f"大K止盈开始(幅度{_tprng:.2f})"))
                        _stop_triggered = True
                        continue
            except Exception:
                pass
        # 期权20%止损检查 (2026-10-04 用户): 优先于信号平仓
        _stop_triggered = False
        if pos:
            if _stop_entry_dir != pos:
                # Fallback兜底 (2026-10-11 fix): 正常开仓当根已用真实入场价初始化,
                # 这里仅防漏网之鱼, 用当前px初始化
                _init_stop_vars(pos, px, i)
            else:
                # 持仓中: Gamma ROI触发 (2026-10-28用户令: 线性价值模型作废, 收益率只认Gamma公式)
                # d_spy = 当前bar收盘 - 入场bar收盘; call: 9.88*d + 0.311*d^2; put: -9.88*d + 0.311*d^2; 逆向钳制<=0
                _d_spy = px - _stop_entry_px
                if pos == "call":
                    _g_roi = 9.88 * _d_spy + 0.311 * _d_spy * _d_spy
                    if _d_spy < 0:
                        _g_roi = min(_g_roi, 0)
                else:
                    _g_roi = -9.88 * _d_spy + 0.311 * _d_spy * _d_spy
                    if _d_spy > 0:
                        _g_roi = min(_g_roi, 0)
                if _stop_win is None and _g_roi <= -20.0 and not (_FLIPCHECK and _hyb_run_st):
                    # 止损窗口触发 (20%版, 同2nfg的nf口径): Gamma ROI跌破-20%即进窗口, 不论同bar有无翻仓信号
                    # (触发bar的翻仓被窗口压住; 窗口内W1起翻仓/平仓信号正常跑; 基线那种"有翻仓则翻仓"在此版不适用)
                    _w_thr = px + _STOP_WIN_RECOV * (_stop_entry_px - px)
                    _stop_win = {"dr": pos, "trig_bar": i, "entry_px": _stop_entry_px, "trig_close": px, "threshold": _w_thr}
                    _tstr_sl = f"{day}T{hm}"
                    actions.append((i, _tstr_sl, "持", pos, px_exec, f"止损窗口开始(Gamma{_g_roi:.1f}%,回{int(_STOP_WIN_RECOV*100)}%价{_w_thr:.2f})"))
                    _stop_triggered = True
                    continue
                    # 有翻仓信号时不单独止损, 留给后面的信号逻辑做翻仓 (平+开)
            # 30秒提前止损 (2026-10-07): 持仓中检查当前3m对应的3根1m, 逆向波动超阈值即按1m收盘价平
            if _USE_30S_STOP and pos and not _stop_triggered:
                _t3 = warm["t"].iloc[i]
                _dstr = _t3.strftime("%Y-%m-%d")
                for _mo in range(3):
                    _t1 = _t3 + pd.Timedelta(minutes=_mo)
                    _mk = (_dstr, _t1.strftime("%H:%M"))
                    _m1 = _M1_DICT.get(_mk)
                    if not _m1:
                        continue
                    _mo_, _mh_, _ml_, _mc_ = _m1
                    _adv = (_mo_ - _ml_) if pos == "call" else (_mh_ - _mo_)
                    if _adv > _30S_THRESH:
                        _tstr_30 = f"{day}T{_t1.strftime('%H:%M')}"
                        actions.append((i, _tstr_30, "平", pos, float(_mc_), f"30秒止损(1m逆向{_adv:.2f}>{_30S_THRESH})"))
                        pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; _call_gap_wk = 0; _put_gap_wk = 0; last_action_bar = i; was_on_top = was_on_bottom = False
                        wait_red = wait_blue = False
                        _follow_entry_bar = None
                        _hold_watch = None
                        _fav_peak = 0; _fav_sign = 0
                        _stop_entry_dir = None
                        _stop_triggered = True
                        break
        else:
            _stop_entry_dir = None
        # Supertrend-only实验 (2026-11-01 用户): 只看ST买卖, 屏蔽全部QQE信号 (QQE代码保留, 本continue跳过QQE段)
        # 到此的bar已过EOD/止损窗口/大K/20%止损 (上方continue会先跳过), 故ST可直接交易; 无信号也continue (挡QQE)
        # 2026-11-01 用户纠正 (早盘不开单): 每天开盘第一根可交易bar (过ignore后), 空仓按当时ST趋势直接开 (不等翻转);
        # 盘中 (止损/平仓后空仓) 仍只等翻转, 不追趋势 (冷冻纪律不变)
        if (_ST_ONLY and not _HYBRID) or _hyb_run_st:
            _st_is_flip = (_st_trend[i] != _st_trend[i-1])
            _st_is_dayopen = (pos is None and i == _day_start + _ign_n_wk and _st_trend[i] != 0)
            # FLIPCHECK (2026-11-01 用户纠正): ST仓位止损只在翻转bar检查, 不每根bar复查;
            # ST仓只存在于ST阶段 (_hyb_run_st), 11:00切换先强制平仓故阶段内仓位即ST仓。
            # 翻转bar: 开仓收盘价->_stop_entry_px 到当前收盘价的累计Gamma <=-20% -> 进窗口 (本根不翻仓);
            # >-20% -> 落到原ST翻转逻辑。QQE仓不受影响 (本块只在ST阶段执行)。
            if _FLIPCHECK and pos is not None and _stop_win is None and _st_is_flip and _stop_entry_dir == pos:
                _fc_same_day = True
                try:
                    _fc_same_day = (warm["t"].iloc[_stop_entry_bar].strftime("%m-%d") == day)
                except Exception:
                    _fc_same_day = True
                if _fc_same_day:
                    _fc_d = px - _stop_entry_px
                    if pos == "call":
                        _fc_roi = 9.88 * _fc_d + 0.311 * _fc_d * _fc_d
                        if _fc_d < 0:
                            _fc_roi = min(_fc_roi, 0)
                    else:
                        _fc_roi = -9.88 * _fc_d + 0.311 * _fc_d * _fc_d
                        if _fc_d > 0:
                            _fc_roi = min(_fc_roi, 0)
                    if _fc_roi <= -20.0:
                        _fc_thr = px + _STOP_WIN_RECOV * (_stop_entry_px - px)
                        _stop_win = {"dr": pos, "trig_bar": i, "entry_px": _stop_entry_px, "trig_close": px, "threshold": _fc_thr}
                        actions.append((i, f"{day}T{hm}", "持", pos, px_exec, f"止损窗口开始(Gamma{_fc_roi:.1f}%,回{int(_STOP_WIN_RECOV*100)}%价{_fc_thr:.2f})"))
                        _stop_triggered = True
                        continue
            if _st_is_flip or _st_is_dayopen:
                _st_new = ("put" if _st_trend[i] == 1 else "call") if _hyb_inv else ("call" if _st_trend[i] == 1 else "put")
                _st_lbl = ("ST反向翻转" if _st_is_flip else "ST反向开盘") if _hyb_inv else ("ST翻转" if _st_is_flip else "ST开盘")
                if pos is not None and pos != _st_new:
                    actions.append((i, f"{day}T{hm}", "平", pos, px_exec, f"{_st_lbl}平{pos}"))
                    actions.append((i, f"{day}T{hm}", "开", _st_new, px_exec, f"{_st_lbl}开{_st_new}"))
                    pos = _st_new; _init_stop_vars(_st_new, px, i); last_action_bar = i
                elif pos is None:
                    actions.append((i, f"{day}T{hm}", "开", _st_new, px_exec, f"{_st_lbl}开{_st_new}"))
                    pos = _st_new; _init_stop_vars(_st_new, px, i); last_action_bar = i
            continue
        # 从当前开仓开始数操作 (2026-10-03 用户反馈: 技术波浪起点太远)
        def _count_ops_from_open(_pk_idx):
            # 从第一个峰开始数 (buildup不算)
            return sum(1 for (bi, _, act, _, _, _) in actions if bi >= _pk_idx and act in ("开", "平"))
        def _damp_factor_gw():
            # 4根没操作就关减震 (2026-10-03 用户)
            if last_action_bar is None:
                return 1.0
            _quiet = i - last_action_bar
            return 1.0 if _quiet < 4 else 0.0
        cur_hist = float(hist[i])
        skip_open = False  # 短峰值翻仓只平不开时, 跳过本轮开仓
        _flip_to = None  # 翻仓目标方向, 同bar直接开
        _flip_is_huibu = False  # 长波回补标记 (显示用, 与正式gen_week.py一致)
        # BETA_FLIP_SKIP: 计算cumslope过去5根的回归斜率beta (2026-10-22)
        _beta = 0.0
        if _BETA_FLIP_SKIP > 0:
            _bks = [k for k in range(max(0, i-4), i+1) if k < len(_9m_cumslope)]
            _bys_raw = [_9m_cumslope[k] for k in _bks if _9m_cumslope[k] == _9m_cumslope[k]]
            # BETA_EMA_ALPHA: 对cumslope做EMA平滑再算beta (2026-10-22)
            if _BETA_EMA_ALPHA > 0 and len(_bys_raw) >= 2:
                # 从当天开头算EMA到当前
                _ema_vals = []
                _ema = None
                for k in range(0, i+1):
                    if k < len(_9m_cumslope) and _9m_cumslope[k] == _9m_cumslope[k]:
                        v = float(_9m_cumslope[k])
                        _ema = v if _ema is None else _BETA_EMA_ALPHA * v + (1 - _BETA_EMA_ALPHA) * _ema
                        _ema_vals.append(_ema)
                _bys = _ema_vals[-len(_bys_raw):] if len(_ema_vals) >= len(_bys_raw) else _bys_raw
            else:
                _bys = _bys_raw
            if len(_bys) >= 2:
                _bxs = np.arange(len(_bys), dtype=float)
                _bxm, _bym = _bxs.mean(), np.mean(_bys)
                _bden = ((_bxs - _bxm) ** 2).sum()
                if _bden > 0:
                    _beta = float(((_bxs - _bxm) * (np.array(_bys) - _bym)).sum() / _bden)
        # BETA_FLIP_SKIP: 保存翻仓前状态, 跳过时恢复 (2026-10-22)
        _bskip_saved = None
        if _BETA_FLIP_SKIP > 0:
            _bskip_saved = (pos, _decay_entry_hist, wait_red, wait_blue,
                            _was_above_zero_wk, _was_below_zero_wk, was_on_top, was_on_bottom,
                            _call_red_ws, _put_blue_ws, last_action_bar,
                            _fav_peak, _fav_sign, _follow_entry_bar,
                            _is_red_flip_wk, skip_open,
                            _pending_flip_to, _pending_flip_bar, _pending_flip_reason)
            _bskip_hold_watch = dict(_hold_watch) if _hold_watch else None
        # 2026-10-08 用户: 翻仓确认 - 检查上一根的延迟翻仓
        if _pending_flip_to and i == _pending_flip_bar + 1:
            # 确认条件: 只看柱子比延迟bar短 (用户2026-10-08: 不要颜色检查)
            _confirmed = False
            try:
                if _pending_flip_to and pos and i-1 >= 0:
                    # 当前bar的|h| < 延迟bar的|h| 即确认
                    if abs(hist[i]) < abs(hist[i-1]):
                        _confirmed = True
            except:
                pass
            if _confirmed:
                # 执行延迟的翻仓
                actions.append((i, tstr, "平", pos, px_exec, f"躲鞭确认翻仓({_pending_flip_reason})"))
                _flip_to = _pending_flip_to
                _n_confirm += 1
            else:
                _n_cancel += 1
            # 无论是否确认，都清除pending (只延迟一次)
            _last_delay_bar = _pending_flip_bar
            _pending_flip_to = None
            _pending_flip_bar = -1
            _pending_flip_reason = ""
        _flip_peak = 0
        # BETA_FLIP_SKIP: 保存翻仓前状态, 跳过时恢复 (2026-10-22)
        _bskip_saved = None
        _bskip_hold_watch = None
        if _BETA_FLIP_SKIP > 0:
            _bskip_saved = (pos, _decay_entry_hist, wait_red, wait_blue,
                            _was_above_zero_wk, _was_below_zero_wk, was_on_top, was_on_bottom,
                            _call_red_ws, _put_blue_ws, last_action_bar,
                            _fav_peak, _fav_sign, _follow_entry_bar,
                            _flip_peak, _flip_is_huibu, _is_red_flip_wk, skip_open,
                            _pending_flip_to, _pending_flip_bar, _pending_flip_reason)
            _bskip_hold_watch = dict(_hold_watch) if _hold_watch else None
        in_cooldown = False  # 2026-10-05 用户: 去掉两根冷却
        # 续波跟随信号 (2026-10-03 用户): 上一波红 + 两根hist<0/|h|>=4/递增 -> 跟put; 蓝对称
        # 新波间隔 (2026-10-03 用户): 必须是新波起点, i-2往回至少4根连续|h|<=3, 否则算同一波不触发
        _cont_put = _cont_call = False
        if i >= 1 and i < len(_day_arr) and _day_arr[i] == _day_arr[i-1]:
            _NEW_WAVE_GAP = 4  # 助理暂定: 与系统4根安静规则一致, 用户可调
            _gap_n = 0; _is_new_wave = False
            for _gk in range(i-2, max(-1, i-2-20), -1):
                if _gk < 0 or _gk >= len(hist):
                    break
                if abs(hist[_gk]) <= 3:
                    _gap_n += 1
                    if _gap_n >= _NEW_WAVE_GAP:
                        _is_new_wave = True
                        break
                else:
                    break
            if _is_new_wave:
                _pw0 = prev_wave_color_gw(i)
                if _pw0 == 'red' and hist[i] < -SIG_MIN_LEN and hist[i-1] < -SIG_MIN_LEN \
                   and abs(hist[i]) > abs(hist[i-1]):
                    _cont_put = True
                elif _pw0 == 'blue' and hist[i] > SIG_MIN_LEN and hist[i-1] > SIG_MIN_LEN \
                   and abs(hist[i]) > abs(hist[i-1]):
                    _cont_call = True
        # 长波降低开仓门槛的信号 (2026-10-03 用户)
        # 2026-10-08 用户: 不光递增，要自适应门槛 (回补第2/3条)
        # 2026-10-08 用户更新: 前根>6只要递增，前根<=3必须翻倍 (不用1.2x)
        _long_put_sig = _long_call_sig = False
        if i >= 1 and i < len(_day_arr) and _day_arr[i] == _day_arr[i-1]:
            try:
                _rl0 = wave_len_gw(i, -1)
                _bl0 = wave_len_gw(i, 1)
                # 自适应门槛: >6只要递增，<=3必须翻倍，中间(3,6]也只要递增
                def _need_mult(_base):
                    if _base < 3:
                        return 2.0
                    elif _base < 5:
                        return 1.5 if _LONGHUIBU_MULT else 1.2
                    else:
                        return 1.3 if _LONGHUIBU_MULT else 1.0  # >3都只要递增
                # 检查红长波
                if _rl0 >= 15:
                    _hc = hist[i]
                    # 第2条: 比前一根长 (自适应)
                    _ok2 = False
                    if not np.isnan(_hc) and _hc < -3:
                        _hp = hist[i-1]
                        if not np.isnan(_hp) and _hp < -3:
                            _ahp = abs(_hp)
                            if abs(_hc) > _ahp * _need_mult(_ahp):
                                _ok2 = True
                    # 第3条: 三根全递增，第三根比第一根 (自适应)
                    _ok3 = False
                    if i >= 2 and not np.isnan(_hc) and _hc < -3:
                        _h1 = hist[i-2]; _h2 = hist[i-1]
                        if (not np.isnan(_h1) and not np.isnan(_h2)
                                and _h1 < -3 and _h2 < -3
                                and abs(_h2) > abs(_h1) and abs(_hc) > abs(_h2)):
                            _ah1 = abs(_h1)
                            if abs(_hc) > _ah1 * _need_mult(_ah1):
                                _ok3 = True
                    if _ok2 or _ok3:
                        _long_put_sig = True
                # 检查蓝长波
                elif _bl0 >= 15:
                    _hc = hist[i]
                    # 第2条: 比前一根长 (自适应)
                    _ok2 = False
                    if not np.isnan(_hc) and _hc > 3:
                        _hp = hist[i-1]
                        if not np.isnan(_hp) and _hp > 3:
                            _ahp = abs(_hp)
                            if abs(_hc) > _ahp * _need_mult(_ahp):
                                _ok2 = True
                    # 第3条: 三根全递增，第三根比第一根 (自适应)
                    _ok3 = False
                    if i >= 2 and not np.isnan(_hc) and _hc > 3:
                        _h1 = hist[i-2]; _h2 = hist[i-1]
                        if (not np.isnan(_h1) and not np.isnan(_h2)
                                and _h1 > 3 and _h2 > 3
                                and abs(_h2) > abs(_h1) and abs(_hc) > abs(_h2)):
                            _ah1 = abs(_h1)
                            if abs(_hc) > _ah1 * _need_mult(_ah1):
                                _ok3 = True
                    if _ok2 or _ok3:
                        _long_call_sig = True
            except Exception:
                pass
        sig = None
        # 早盘(10:00前)用跳过2倍检查的信号
        _is_morning = hm < "10:00"
        _set_r = set(peak_red_m.keys()) if _is_morning else set_red
        _set_b = set(peak_blue_m.keys()) if _is_morning else set_blue
        if i in _set_r and i in _set_b:
            # 同时触发取后者 (与 watcher 一致: 比较 trigger index, 这里同为 i, 取蓝)
            sig = "BLUE_DECAY_SHORT"
        elif i in _set_r:
            sig = "RED_DECAY_LONG"
        elif i in _set_b:
            sig = "BLUE_DECAY_SHORT"

        tstr = f"{day}T{hm}"

        # --- 持仓管理 (15:31版) ---
        if pos:
            # EOD 平仓 (2026-10-08 用户: 用1分钟15:55价格, 更接近实盘)
            if hm >= "15:55":
                _eod_px = _eod_1m.get(str(_day_arr[i]), px)  # 找不到则回退到3m收盘
                actions.append((i, tstr, "平", pos, _eod_px, "日终平仓(1m15:55)"))
                pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; _call_gap_wk = 0; _put_gap_wk = 0; last_action_bar = i; was_on_top = was_on_bottom = False
                wait_red = wait_blue = False
                _follow_entry_bar = None
                _hold_watch = None
                _fav_peak = 0; _fav_sign = 0
                continue
            # 顺强趋势翻仓 (2026-10-18 用户): 持call+11:00-15:00+cumslope<-0.08, 蓝波6根内峰值衰减50%即翻put
            # 峰值只看正柱子(蓝波), 触发bar不限颜色长度
            if pos == "call" and _ST_FLIP and _st_gate(i, hm):
                _st_pk = 0
                for _stj in range(max(start_idx, i-5), i+1):
                    _sth = hist[_stj]
                    if _sth == _sth and _sth > 0 and _sth > _st_pk:  # 只看正柱子
                        _st_pk = _sth
                if _st_pk > 0 and abs(hist[i]) <= _st_pk * 0.5:
                    actions.append((i, tstr, "平", "call", px_exec, f"强趋势翻仓平call(6根峰{_st_pk:.0f})"))
                    _flip_to = "put"; _flip_peak = _st_pk
                    # 记录蓝波起点, 防止翻仓后"蓝出现"误杀同波
                    _wsb = i
                    while _wsb > 0 and _ws_cont_blue(_wsb-1):
                        _wsb -= 1
                    _put_blue_ws = _wsb
                    pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; last_action_bar = i
                    was_on_top = was_on_bottom = False
                    wait_red = wait_blue = False
                    _decay_entry_hist = 0
                    _follow_entry_bar = None
                    # 不continue, 让_flip_to在section 2开put (与hold-watch翻仓同模式)
            # 动量死亡平仓 (2026-10-03 用户): 同向波(含灰)峰值>=10后衰减, 非真同色即平
            # 灰柱更敏感(阈值8), 空柱/真反色阈值4 (2026-10-03 用户: 灰矮柱要早平)
            # 更新峰值
            _MDEATH_PEAKN = 8 if _MDEATH_PEAK8 else (6 if _MDEATH_PEAK6 else 0)
            if _MDEATH_PEAKN:
                # 变体: 每根bar往前看N根(不含本根,不跨日), 取同向(含灰)|hist|最大; 无同向则0
                _fav_peak = 0
                if _fav_sign != 0:
                    for _pk8_j in range(max(_day_start, i-_MDEATH_PEAKN), i):
                        _pk8_h = hist[_pk8_j]
                        if not np.isnan(_pk8_h) and _pk8_h * _fav_sign > 0 and abs(_pk8_h) > _fav_peak:
                            _fav_peak = abs(_pk8_h)
            elif _fav_sign != 0 and not np.isnan(hist[i]) and hist[i] * _fav_sign > 0 and abs(hist[i]) > _fav_peak:
                _fav_peak = abs(hist[i])
            _fav_died = False
            _is_g = not is_true_blue(i) and not is_true_red(i) and abs(hist[i]) > 3
            _dthr = (5 if _MDEATH_GRAY5 else 8) if _is_g else 4
            _md_peak_ok = True if _MDEATH_NOPEAK else _fav_peak >= 10
            if _fav_sign == 1 and pos == "call" and _md_peak_ok:
                if not is_true_blue(i) and abs(hist[i]) <= _dthr:
                    _fav_died = True
            elif _fav_sign == -1 and pos == "put" and _md_peak_ok:
                if not is_true_red(i) and abs(hist[i]) <= _dthr:
                    _fav_died = True
            if _fav_died:
                _md_was = pos  # 2026-10-19 MOMDEATH_FLIP: 记录原方向
                actions.append((i, tstr, "平", pos, px_exec, f"动量死亡平{pos}(峰{_fav_peak:.0f})"))
                if _DUMP_MOMDEATH:
                    _hv_md = hist[i]
                    _tc_md = "blue" if _hv_md > 3 else ("red" if _hv_md < -3 else "gray")
                    _s5_md = _9m_slope5[i]
                    _cs_md = _9m_cumslope[i]
                    # drop9m: 上一根走完9mK的(开-收)/过去3根走完3mK收盘max-min; 9mK非阴线或gain<=0记None
                    _drop9m = None
                    _o9 = _9m_prev_o[i]; _c9 = _9m_prev_c[i]
                    if _o9 == _o9 and _c9 == _c9 and i >= 3:
                        _g3 = [float(close[i-3]), float(close[i-2]), float(close[i-1])]
                        _gain3 = max(_g3) - min(_g3)
                        if _gain3 > 0 and _o9 > _c9:
                            _drop9m = (_o9 - _c9) / _gain3
                    _momdeath_raw.append((tstr, pos, float(px_exec), _tc_md, int(i),
                        None if _s5_md != _s5_md else float(_s5_md),
                        None if _cs_md != _cs_md else float(_cs_md),
                        _drop9m, "momdeath"))
                pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; _call_gap_wk = 0; _put_gap_wk = 0; last_action_bar = i; was_on_top = was_on_bottom = False
                wait_red = wait_blue = False
                if not _MDEATH_HUIBU:  # 2026-10-21: MDEATH_HUIBU=1时保留decay-entry记忆, 动量死亡后允许回补
                    _decay_entry_hist = 0
                _follow_entry_bar = None
                _hold_watch = None
                _fav_peak = 0; _fav_sign = 0
                # 2026-10-19 MOMDEATH_FLIP: 动量死亡平仓后, 若反向衰减信号满足翻仓门则翻仓
                _md_flipped = False
                if _MOMDEATH_FLIP:
                    _md_target_sig = "BLUE_DECAY_SHORT" if _md_was == "call" else "RED_DECAY_LONG"
                    _md_new_dir = "put" if _md_was == "call" else "call"
                    if sig == _md_target_sig:
                        _md_pk_map = (peak_blue_m if _is_morning else peak_blue) if _md_was == "call" else (peak_red_m if _is_morning else peak_red)
                        _md_pk = _md_pk_map.get(i, 0)
                        _md_thr = FLIP_MIN_PEAK_BLUE if _md_was == "call" else FLIP_MIN_PEAK
                        if _md_pk >= _md_thr:
                            _md_wl, _md_chop, _md_wl_adj, _md_ws, _md_pki = _fav_wave_info(1 if _md_was == "call" else -1)
                            _md_op = _count_ops_from_open(_md_pki)
                            if _md_op >= 4:
                                _md_base = max(0.40, 0.8 * (0.97 ** max(0, _md_wl_adj - 3)))
                                _md_need = 0.8 - (0.8 - _md_base) * _damp_factor_gw()
                            else:
                                _md_need = 0.8
                            if _md_wl_adj >= 15:
                                _md_need *= 0.7
                            _md_rf = [b for b in _recent_flips if i - b <= 30 and _day_arr[b] == _day_arr[i]]
                            if len(_md_rf) >= 2:
                                _md_need *= 0.7
                            _md_need = max(0.30, _md_need)
                            if _md_was == "call" and _REBOUND_BLUE:
                                _md_need *= _rebound_factor_blue(i)
                            if _md_was == "put" and _REBOUND_RED:
                                _md_need *= _rebound_factor_red(i)
                            _md_ratio = abs(hist[i]) / _md_pk if _md_pk > 0 else 1
                            if _md_ratio <= _md_need:
                                _md_prev_h = abs(hist[i-1]) if i-1 >= 0 and not np.isnan(hist[i-1]) else 999
                                _md_ops = [a for a in actions if i-4 <= a[0] < i and a[2] in ("开", "平") and a[1][:5] == tstr[:5]]
                                _md_pthr = 0.7 if (len(_md_ops) >= 2 and _md_wl_adj >= 10) else 0.9
                                _md_pr = abs(hist[i]) / _md_prev_h if _md_prev_h > 0 else 1
                                if _md_pr <= _md_pthr:
                                    _md_blocked = False
                                    if _md_was == "call":
                                        if _mild_pullback_blocks_flip_put(i) or _downtrend_blocks_flip_put(i):
                                            _md_blocked = True
                                    if not _md_blocked:
                                        actions.append((i, tstr, "开", _md_new_dir, px_exec, f"动量死亡翻{_md_new_dir}(峰{_md_pk:.0f})"))
                                        _flip_to = _md_new_dir; _flip_peak = _md_pk
                                        _md_flipped = True
                if not _md_flipped:
                    continue
                # 翻仓: 不continue, 让_flip_to在section 2开仓
            # HOLD后跟踪翻仓 (2026-10-03): HOLD后若衰减加深到<=need, 则翻仓
            # (不continue, 让_flip_to在section 2开仓)
            if _hold_watch and pos == _hold_watch.get("dir") and i > _hold_watch.get("bar", -1):
                # 若开仓bar晚于HOLD bar, 说明是新仓位, hold-watch已stale
                if last_action_bar > _hold_watch.get("bar", -1):
                    _hold_watch = None
                else:
                    _hwp = _hold_watch.get("peak") or 0
                    _hwr = abs(hist[i]) / _hwp if _hwp > 0 else 1.0
                    if _hwr <= _hold_watch.get("need", 0):
                        _hwdir = _hold_watch["dir"]
                        _hold_watch = None
                        if _hwdir == "put":
                            actions.append((i, tstr, "平", "put", px_exec, f"hold-watch翻仓(峰{_hwp:.0f},比{_hwr:.2f})"))
                            _flip_to = "call"; _flip_peak = _hwp
                            # 记录红波起点, 防止翻仓后"红出现"误杀同波 (2026-10-04 fix)
                            _wsr = i
                            while _wsr > 0 and _ws_cont_red(_wsr-1):
                                _wsr -= 1
                            _call_red_ws = _wsr
                        else:
                            if _mild_pullback_blocks_flip_put(i):
                                actions.append((i, tstr, "持", "call", px_exec, f"温和回调屏蔽翻P(slope5={_9m_slope5[i]:.3f})"))
                                _n_mild_blocked += 1
                            elif _downtrend_blocks_flip_put(i):
                                actions.append((i, tstr, "持", "call", px_exec, f"下跌震荡屏蔽翻P(slope5={_9m_slope5[i]:.3f})"))
                                _n_downtrend_blocked += 1
                            else:
                                actions.append((i, tstr, "平", "call", px_exec, f"hold-watch翻仓(峰{_hwp:.0f},比{_hwr:.2f})"))
                                _flip_to = "put"; _flip_peak = _hwp
                            # 记录蓝波起点, 防止翻仓后"蓝出现"误杀同波 (2026-10-04 fix)
                            _wsb = i
                            while _wsb > 0 and _ws_cont_blue(_wsb-1):
                                _wsb -= 1
                            _put_blue_ws = _wsb
                        pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; last_action_bar = i
                        was_on_top = was_on_bottom = False
                        wait_red = wait_blue = False
                        _decay_entry_hist = 0
                        _follow_entry_bar = None
            # 跟随失败即平 (2026-10-03 用户): 跟随开仓后下一根|h|<=入场柱80%即平. 只检查一次.
            if _follow_entry_bar == i - 1:
                _feh = _follow_entry_hist
                _follow_entry_bar = None
                # 跟随背离止损 (2026-10-03 用户; 2026-10-05 用户: 11点后不查; 2026-10-07 用户: 去掉)
                # 下一根收盘价反向超0.2点即平; 波浪5根以上不查; 11:00后不查
                _div_closed = False
                _dd = False  # 2026-10-07 用户: 去掉背离检查
                _follow_do_div_wk = True
                _hhmm_wk = tstr[6:11] if len(tstr) >= 11 else "00:00"
                if _dd and _hhmm_wk < "11:00" and pos and i >= 1 and i < len(close):
                    _ec = close[i-1]; _cc = close[i]
                    _div_qqe_ok = not (_DIV_QQE_GATE and _feh > 0 and abs(hist[i]) > _feh)  # 加强中不触发
                    if pos == "put" and _cc > _ec + _DIV_THRESH and _div_qqe_ok:
                        actions.append((i, tstr, "平", pos, px_exec, f"跟随背离平put(入{_ec:.2f},现{_cc:.2f})"))
                        pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; _call_gap_wk = 0; _put_gap_wk = 0; last_action_bar = i; was_on_top = was_on_bottom = False
                        wait_red = wait_blue = False; _div_closed = True
                    elif pos == "call" and _cc < _ec - _DIV_THRESH and _div_qqe_ok:
                        actions.append((i, tstr, "平", pos, px_exec, f"跟随背离平call(入{_ec:.2f},现{_cc:.2f})"))
                        pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; _call_gap_wk = 0; _put_gap_wk = 0; last_action_bar = i; was_on_top = was_on_bottom = False
                        wait_red = wait_blue = False; _div_closed = True
                # 2026-10-18 顺强趋势: gate开时跟随失败线收紧到60% (原80%)
                _ST_FF60 = True
                _ST_FF_RATIO = 0.6  # 2026-10-22: 收紧比例，默认0.6
                _ff_ratio = _ST_FF_RATIO if (_ST_FF60 and _ST_FOLLOW and _st_gate(i, hm)) else 0.8
                if not _div_closed and _feh > 0 and abs(hist[i]) <= _feh * _ff_ratio:
                    actions.append((i, tstr, "平", pos, px_exec, f"跟随失败平{pos}"))
                    if _DUMP_MOMDEATH:
                        _hv_ff = hist[i]
                        _tc_ff = "blue" if _hv_ff > 3 else ("red" if _hv_ff < -3 else "gray")
                        _s5_ff = _9m_slope5[i]
                        _cs_ff = _9m_cumslope[i]
                        _momdeath_raw.append((tstr, pos, float(px_exec), _tc_ff, int(i),
                            None if _s5_ff != _s5_ff else float(_s5_ff),
                            None if _cs_ff != _cs_ff else float(_cs_ff),
                            None, "followfail"))
                    pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; _call_gap_wk = 0; _put_gap_wk = 0; last_action_bar = i; was_on_top = was_on_bottom = False
                    wait_red = wait_blue = False
                    _decay_entry_hist = _feh if _FOLLOWFAIL_HUIBU else 0  # 2026-10-21: 开=跟随失败后允许回补(参考=入场柱|hist|)
                    continue
            # 续波翻仓 (2026-10-03 用户): 上一波红+新红柱 -> 持call翻put; 蓝对称. 同bar直接翻.
            # 长波翻仓 (2026-10-03 用户): 长波里降低开仓门槛, 持反向也翻.
            # 2026-10-27 用户: 窗口灰收紧, 11:45-13:15内本根灰柱须突然涨1.5倍以上(相对前一根)且|hist|>=8才允许长波回补翻, 短小杂灰柱禁翻 (窗口外/真红/续波翻/蓝波/跟随/重涨全不动)
            # 2026-10-28 用户令: 合进基线.
            _wr15_block = (_long_put_sig and "11:45" <= hm < "13:15" and not bool(q_down[i]) and not bool(q_up[i])
                           and not (i >= 1 and abs(hist[i]) >= 1.5 * abs(hist[i-1]) and abs(hist[i]) >= 8))
            if pos == "call" and (_cont_put or (_long_put_sig and not _wr15_block)):
                if _mild_pullback_blocks_flip_put(i):
                    actions.append((i, tstr, "持", "call", px_exec, f"温和回调屏蔽翻P(slope5={_9m_slope5[i]:.3f})"))
                    _n_mild_blocked += 1
                elif _downtrend_blocks_flip_put(i):
                    actions.append((i, tstr, "持", "call", px_exec, f"下跌震荡屏蔽翻P(slope5={_9m_slope5[i]:.3f})"))
                    _n_downtrend_blocked += 1
                else:
                    actions.append((i, tstr, "平", "call", px_exec, "续波翻put" if _cont_put else "长波回补平call"))
                    pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; last_action_bar = i
                    _flip_to = "put"; _flip_peak = 0; _flip_is_huibu = (not _cont_put and _long_put_sig)
            elif pos == "put" and (_cont_call or _long_call_sig):
                actions.append((i, tstr, "平", "put", px_exec, "续波翻call" if _cont_call else "长波回补平put"))
                pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; last_action_bar = i
                _flip_to = "call"; _flip_peak = 0; _flip_is_huibu = (not _cont_call and _long_call_sig)
            # (已删除: 红/蓝连续变长 & 柱子消失平仓, 2026-10-01 10天回测胜率3%/0%)
            # 反向翻仓: 长峰值(>=10)才翻, 短峰值只平仓观望
            FLIP_MIN_PEAK = 14  # 红翻仓 (不变)
            FLIP_MIN_PEAK_BLUE = 11  # 蓝翻仓实验: 14->11
            if pos and not in_cooldown:
                # 波浪自适应翻仓 (2026-10-03 用户): 起伏越厉害越减震. 起伏率=方向反转次数/(根数-1).
                # 灰柱两边同向不断浪. wl_adj = wl*(1.0+chop).
                def _fav_wave_info(_sign):
                    _ws = i
                    _consec_small = 0
                    # 当天第一根的 index，硬边界，绝不跨日 (2026-10-06 用户: 肯定不能跨日)
                    _cur_day = _day_arr[i]
                    _day_start = i
                    while _day_start > 0 and _day_arr[_day_start-1] == _cur_day:
                        _day_start -= 1
                    while _ws > _day_start:
                        _h = hist[_ws-1]
                        if np.isnan(_h):
                            break
                        # (2026-10-07 fix: 零轴下方的柱子即断波，不分大小)
                        # (2026-10-09 V1: |h|<1的微小反色不打断波浪)
                        if _h * _sign < 0 and abs(_h) >= 1:
                            break
                        if abs(_h) <= 4:
                            _consec_small += 1
                            # 2026-10-21 WAVE_BRK3: 连3根小柱即断波(原>8); 关闭时恒为>8, 行为不变
                            if _consec_small > (2 if _WAVE_BRK3 else 8):
                                break
                        else:
                            _consec_small = 0
                        _ws -= 1
                    # 找第一个峰 (buildup结束、起伏开始), 之后创新高不再重置
                    _pk_idx = i; _pk_v = 0
                    _run_max = 0; _run_max_idx = _ws
                    for _j in range(_ws, i+1):
                        if not np.isnan(hist[_j]):
                            _v = abs(hist[_j])
                            if _v > _run_max:
                                _run_max = _v; _run_max_idx = _j
                            if _pk_idx == i and _run_max > 0 and _v < _run_max * 0.85:
                                _pk_idx = _run_max_idx; _pk_v = _run_max
                    if _pk_idx == i:
                        for _j in range(_ws, i+1):
                            if not np.isnan(hist[_j]):
                                _v = abs(hist[_j])
                                if _v > _pk_v:
                                    _pk_v = _v; _pk_idx = _j
                    _wl = i - _pk_idx
                    # 2026-10-07 用户: choppiness改10根滚动窗口(波内、日内截断)，用震荡比=累计变化/净变化
                    _win_start = max(i-9, _ws, _day_start)
                    _bars = [abs(hist[_j]) for _j in range(_win_start, i+1) if not np.isnan(hist[_j])]
                    _chop = 0
                    if len(_bars) >= 3:
                        _total = sum(abs(_bars[_b]-_bars[_b-1]) for _b in range(1, len(_bars)))
                        _net = abs(_bars[-1]-_bars[0])
                        _ratio = _total/_net if _net > 0.1 else 99
                        _chop = min(1.0, max(0.0, (_ratio-1.0)/4.0))
                    return _wl, _chop, _wl * (1.0 + _chop), _ws, _pk_idx
                if sig == "RED_DECAY_LONG" and pos == "put":
                    _pk_map = peak_red_m if _is_morning else peak_red
                    pk = _pk_map.get(i, 0)
                    # 2026-10-21 日趋势翻仓放宽: 强涨日红翻C去峰值要求(用户: 不需要6的峰值要求)
                    _dr_flip_r = _dayregime(i, hm) if _DAYREGIME_FLIP else 0
                    # 2026-10-21 |s9|门(红蓝统一): 弱波/死区不翻不平(HOLD); 强波need*0.85早翻
                    _s9_hold_r = _s9_boost_r = False
                    if _FLIP_S9_GATE and i < len(_9m_slope5) and not np.isnan(_9m_slope5[i]):
                        _s9a_r = abs(_9m_slope5[i])
                        if _s9a_r < 0.08 or (0.40 <= _s9a_r < 0.50):
                            _s9_hold_r = True
                        elif _s9a_r >= 0.50:
                            _s9_boost_r = True
                    if _s9_hold_r:
                        pass  # HOLD: 不翻不平
                    elif pk >= FLIP_MIN_PEAK or _dr_flip_r == 1:
                        _wl, _chop, _wl_adj, _ws, _pk = _fav_wave_info(-1)
                        # 4次操作后才减震, 且随时间衰减
                        _op_cnt = _count_ops_from_open(_pk)
                        if _op_cnt >= 4:
                            _base_need = max(0.40, 0.8 * (0.97 ** max(0, _wl_adj - 3)))
                            _need = 0.8 - (0.8 - _base_need) * _damp_factor_gw()
                        else:
                            _need = 0.8
                        # 长波翻仓更严 (2026-10-03 用户): wl_adj>=15时need*0.7
                        if _wl_adj >= 15:
                            _need *= 0.7
                        # 反复翻仓惩罚 (2026-10-03 用户): 30根内翻过2次以上, need*0.7, 抑制颠簸浪反复翻
                        # 2026-10-06: 只数今天的, 不跨日
                        # 2026-10-21 日趋势翻仓放宽: 强涨日红翻C去频繁翻仓惩罚
                        _rf_r = [b for b in _recent_flips if i - b <= 30 and _day_arr[b] == _day_arr[i]]
                        if len(_rf_r) >= 2 and _dr_flip_r != 1:
                            _need *= 0.7
                        # 2026-10-31 用户: 红波浪慢跌惩罚0.9版 (v2同款移植, 系数0.7->0.9, 更温和) - 取本波红波浪[_ws,i]内最陡(最小)slope5
                        # 波内最陡仍在[-0.5,-0.1]慢跌带才need*0.9 (整波都没急跌过=慢跌波, 翻仓更严; 波内有过急跌<-0.5不罚)
                        _s5_wave = [_9m_slope5[_j] for _j in range(_ws, i+1) if _j < len(_9m_slope5) and not np.isnan(_9m_slope5[_j])]
                        _s5_pen = min(_s5_wave) if _s5_wave else float("nan")
                        if not np.isnan(_s5_pen) and -0.5 <= _s5_pen <= -0.1:
                            _need *= 0.9
                        # 2026-10-07 用户: 最不济40%, need不低于0.40
                        _need = max(0.30, _need)
                        # 2026-10-21 |s9|门: 强波(|s9|>=0.50)need*0.85早翻
                        if _s9_boost_r:
                            _need *= 0.85
                        # 2026-10-16 反弹惩罚(红侧): 反弹弱则need更低(更难翻), 可突破0.30下限
                        if _REBOUND_RED:
                            _need *= _rebound_factor_red(i)
                        _ratio = abs(hist[i]) / pk if pk > 0 else 1
                        if _ratio <= _need:
                            # 前根对比抗震 (2026-10-03 用户): 长波(wl_adj>=10)且前4根内同日操作>=2次, 要求<=前根70%才翻
                            _df_gw = _damp_factor_gw()
                            _prev_h = abs(hist[i-1]) if i-1 >= 0 and not np.isnan(hist[i-1]) else 999
                            _cur_day = tstr[:5]
                            _ops_4 = [a for a in actions if i-4 <= a[0] < i and a[2] in ("开", "平") and a[1][:5] == _cur_day]
                            _prev_thr = 0.7 if (len(_ops_4) >= 2 and _wl_adj >= 10) else 0.9
                            _pr = abs(hist[i]) / _prev_h if _prev_h > 0 else 1
                            if _pr <= _prev_thr:
                                # 2026-10-08 用户: 躲鞭 - 这根涨幅超开仓到前根区间90%
                                _delay_flip = False
                                try:
                                    if pos == "put" and _stop_entry_px > 0 and _stop_entry_bar >= 0 and i - _last_delay_bar > 1 and i-1 >= 0:
                                        _bars_held = i - _stop_entry_bar
                                        _prev_c = float(close[i-1])
                                        # 开仓到前根的区间长度
                                        _range = abs(_prev_c - _stop_entry_px)
                                        # 这根的涨幅
                                        _bar_move = px - _prev_c
                                        # 涨幅超区间90% 且现价超开仓价
                                        if False and _bars_held >= 2 and _range > 0 and _bar_move > _range * 0.9 and px > _stop_entry_px:
                                            _delay_flip = True
                                except:
                                    pass
                                if _delay_flip:
                                    # 延迟，不立即翻
                                    actions.append((i, tstr, "持", "put", px_exec, f"红衰减延迟确认(峰{pk:.0f})"))
                                    _pending_flip_to = "call"
                                    _pending_flip_bar = i
                                    _pending_flip_reason = f"红衰减翻仓(峰{pk:.0f})"
                                    _flip_to = None
                                    _n_delay += 1
                                else:
                                    actions.append((i, tstr, "平", "put", px_exec, f"红衰减翻仓(峰{pk:.0f},波{_wl})"))
                                    _flip_to = "call"; _flip_peak = pk; _is_red_flip_wk = True
                                # 记录红波起点, 防止翻仓后"红出现"误杀同波
                                _wsr = i
                                while _wsr > 0 and _ws_cont_red(_wsr-1):
                                    _wsr -= 1
                                _call_red_ws = _wsr
                            else:
                                actions.append((i, tstr, "持", "put", px_exec, f"红衰减持有(峰{pk:.0f},波{_wl},前比{_pr:.2f}>{_prev_thr:.2f})"))
                                _flip_to = None
                        else:
                            # 衰减不够深: 持有不动 (不平不翻) (2026-10-10 用户: HOLD改平仓已拒绝回滚, 保持持有+_hold_watch跟踪)
                            # HOLD后继续跟踪 (2026-10-03): 记录peak/need, 后续bar衰减加深则翻
                            actions.append((i, tstr, "持", "put", px_exec, f"红衰减持有(峰{pk:.0f},波{_wl},比{_ratio:.2f}>{_need:.2f})"))
                            _flip_to = None
                            _hold_watch = {"peak": pk, "need": _need, "dir": "put", "bar": i}
                    elif _MIDTREND_FLIP and _midtrend_regime(i, hm) == 1 and pk >= 10:
                        # 中强趋势快翻仓 (2026-10-21 用户, 独立信号): 峰≥10(低于14), 去前根门, need门保留
                        _wl_m, _chop_m, _wl_adj_m, _ws_m, _pk_m = _fav_wave_info(-1)
                        _op_cnt_m = _count_ops_from_open(_pk_m)
                        if _op_cnt_m >= 4:
                            _base_m = max(0.40, 0.8 * (0.97 ** max(0, _wl_adj_m - 3)))
                            _need_m = 0.8 - (0.8 - _base_m) * _damp_factor_gw()
                        else:
                            _need_m = 0.8
                        if _wl_adj_m >= 15:
                            _need_m *= 0.7
                        _need_m = max(0.30, _need_m)
                        if _REBOUND_RED:
                            _need_m *= _rebound_factor_red(i)
                        _ratio_m = abs(hist[i]) / pk if pk > 0 else 1
                        if _ratio_m <= _need_m:
                            actions.append((i, tstr, "平", "put", px_exec, f"中强趋势快翻仓(峰{pk:.0f},比{_ratio_m:.2f})"))
                            pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; wait_red = wait_blue = False; _decay_entry_hist = 0
                            _flip_to = "call"
                        else:
                            actions.append((i, tstr, "持", "put", px_exec, f"中强快翻持有(峰{pk:.0f},比{_ratio_m:.2f}>{_need_m:.2f})"))
                            _flip_to = None
                    else:
                        actions.append((i, tstr, "平", "put", px_exec, f"红衰减短峰只平(峰{pk:.0f})"))
                        skip_open = True
                    if _flip_to or "短峰只平" in actions[-1][5]:
                        pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; wait_red = wait_blue = False; _decay_entry_hist = 0
                    # 注意: 翻仓时 last_action_bar 在开仓后才更新, 保证同bar能开
                    if not _flip_to:
                        last_action_bar = i
                elif sig == "BLUE_DECAY_SHORT" and pos == "call":
                    _pk_map = peak_blue_m if _is_morning else peak_blue
                    pk = _pk_map.get(i, 0)
                    # 2026-10-21 日趋势翻仓放宽: 强跌日蓝翻P去峰值要求(用户: 不需要6的峰值要求)
                    _dr_flip_b = _dayregime(i, hm) if _DAYREGIME_FLIP else 0
                    # 2026-10-21 |s9|门(红蓝统一): 弱波/死区不翻不平(HOLD); 强波need*0.85早翻
                    _s9_hold_b = _s9_boost_b = False
                    if _FLIP_S9_GATE and i < len(_9m_slope5) and not np.isnan(_9m_slope5[i]):
                        _s9a_b = abs(_9m_slope5[i])
                        if _s9a_b < 0.08 or (0.40 <= _s9a_b < 0.50):
                            _s9_hold_b = True
                        elif _s9a_b >= 0.50:
                            _s9_boost_b = True
                    if _s9_hold_b:
                        pass  # HOLD: 不翻不平
                    elif pk >= FLIP_MIN_PEAK_BLUE or _dr_flip_b == -1:
                        _wl, _chop, _wl_adj, _ws, _pk = _fav_wave_info(1)
                        _op_cnt = _count_ops_from_open(_pk)
                        if _op_cnt >= 4:
                            _base_need = max(0.40, 0.8 * (0.97 ** max(0, _wl_adj - 3)))
                            _need = 0.8 - (0.8 - _base_need) * _damp_factor_gw()
                        else:
                            _need = 0.8
                        # 长波翻仓更严 (2026-10-03 用户, 蓝对称): wl_adj>=15时need*0.7
                        if _wl_adj >= 15:
                            _need *= 0.7
                        # 反复翻仓惩罚 (2026-10-13 用户, 蓝同红): 30根内(只数当天)翻过2次以上, need*0.7
                        # 2026-10-21 日趋势翻仓放宽: 强跌日蓝翻P去频繁翻仓惩罚
                        _rf_b = [b for b in _recent_flips if i - b <= 30 and _day_arr[b] == _day_arr[i]]
                        if len(_rf_b) >= 2 and _dr_flip_b != -1:
                            _need *= 0.7
                        # 2026-10-31 用户: 蓝波浪慢涨惩罚0.9版 (红波浪慢跌惩罚的镜像) - 取本波蓝波浪[_ws,i]内最陡(最大)slope5
                        # 波内最陡仍在[0.1,0.5]慢涨带才need*0.9 (整波都没急涨过=慢涨波, 翻仓更严; 波内有过急涨>0.5不罚)
                        _s5_wave_b = [_9m_slope5[_j] for _j in range(_ws, i+1) if _j < len(_9m_slope5) and not np.isnan(_9m_slope5[_j])]
                        _s5_pen_b = max(_s5_wave_b) if _s5_wave_b else float("nan")
                        if not np.isnan(_s5_pen_b) and 0.1 <= _s5_pen_b <= 0.5:
                            _need *= 0.9
                        # 2026-10-07 用户: 最不济40%, need不低于0.40
                        _need = max(0.30, _need)
                        # 2026-10-21 |s9|门: 强波(|s9|>=0.50)need*0.85早翻
                        if _s9_boost_b:
                            _need *= 0.85
                        # 2026-10-16 反弹惩罚(蓝侧对称): 回吐弱则need更低(更难翻), 可突破0.30下限
                        if _REBOUND_BLUE:
                            _need *= _rebound_factor_blue(i)
                        _ratio = abs(hist[i]) / pk if pk > 0 else 1
                        if _ratio <= _need:
                            # 前根对比抗震 (2026-10-03 用户, 蓝对称): 长波(wl_adj>=10)且前4根内同日操作>=2次, 要求<=前根70%才翻
                            _df_gw_b = _damp_factor_gw()
                            _prev_hb = abs(hist[i-1]) if i-1 >= 0 and not np.isnan(hist[i-1]) else 999
                            _cur_day_b = tstr[:5]
                            _ops_4b = [a for a in actions if i-4 <= a[0] < i and a[2] in ("开", "平") and a[1][:5] == _cur_day_b]
                            _prev_thrb = 0.7 if (len(_ops_4b) >= 2 and _wl_adj >= 10) else 0.9
                            _prb = abs(hist[i]) / _prev_hb if _prev_hb > 0 else 1
                            if _prb <= _prev_thrb:
                                # 2026-10-08 用户: 躲鞭 - 这根跌幅超开仓到前根区间90% (对称)
                                _delay_flip_b = False
                                try:
                                    if pos == "call" and _stop_entry_px > 0 and _stop_entry_bar >= 0 and i - _last_delay_bar > 1 and i-1 >= 0:
                                        _bars_held_b = i - _stop_entry_bar
                                        _prev_c_b = float(close[i-1])
                                        _range_b = abs(_prev_c_b - _stop_entry_px)
                                        _bar_move_b = _prev_c_b - px  # 跌幅
                                        if False and _bars_held_b >= 2 and _range_b > 0 and _bar_move_b > _range_b * 0.9 and px < _stop_entry_px:
                                            _delay_flip_b = True
                                except:
                                    pass
                                if _delay_flip_b:
                                    if _mild_pullback_blocks_flip_put(i):
                                        actions.append((i, tstr, "持", "call", px_exec, f"温和回调屏蔽翻P(slope5={_9m_slope5[i]:.3f})"))
                                        _n_mild_blocked += 1
                                        _flip_to = None
                                    elif _downtrend_blocks_flip_put(i):
                                        actions.append((i, tstr, "持", "call", px_exec, f"下跌震荡屏蔽翻P(slope5={_9m_slope5[i]:.3f})"))
                                        _n_downtrend_blocked += 1
                                        _flip_to = None
                                    else:
                                        actions.append((i, tstr, "持", "call", px_exec, f"蓝衰减延迟确认(峰{pk:.0f})"))
                                        _pending_flip_to = "put"
                                        _pending_flip_bar = i
                                        _pending_flip_reason = f"蓝衰减翻仓(峰{pk:.0f})"
                                        _n_delay += 1
                                        _flip_to = None
                                else:
                                    if _mild_pullback_blocks_flip_put(i):
                                        actions.append((i, tstr, "持", "call", px_exec, f"温和回调屏蔽翻P(slope5={_9m_slope5[i]:.3f})"))
                                        _n_mild_blocked += 1
                                    elif _downtrend_blocks_flip_put(i):
                                        actions.append((i, tstr, "持", "call", px_exec, f"下跌震荡屏蔽翻P(slope5={_9m_slope5[i]:.3f})"))
                                        _n_downtrend_blocked += 1
                                    else:
                                        actions.append((i, tstr, "平", "call", px_exec, f"蓝衰减翻仓(峰{pk:.0f},波{_wl})"))
                                        _flip_to = "put"; _flip_peak = pk
                                # 记录蓝波起点, 防止翻仓后"蓝出现"误杀同波 (2026-10-04 fix: 只有真红才切断)
                                _wsb = i
                                while _wsb > 0 and _ws_cont_blue(_wsb-1):
                                    _wsb -= 1
                                _put_blue_ws = _wsb
                            else:
                                actions.append((i, tstr, "持", "call", px_exec, f"蓝衰减持有(峰{pk:.0f},波{_wl},前比{_prb:.2f}>{_prev_thrb:.2f})"))
                                _flip_to = None
                        else:
                            # 衰减不够深: 持有不动 (不平不翻) (2026-10-10 用户: HOLD改平仓已拒绝回滚, 保持持有+_hold_watch跟踪)
                            # HOLD后继续跟踪 (2026-10-03): 记录peak/need, 后续bar衰减加深则翻
                            actions.append((i, tstr, "持", "call", px_exec, f"蓝衰减持有(峰{pk:.0f},波{_wl},比{_ratio:.2f}>{_need:.2f})"))
                            _flip_to = None
                            _hold_watch = {"peak": pk, "need": _need, "dir": "call", "bar": i}
                    elif _MIDTREND_FLIP and _midtrend_regime(i, hm) == -1 and pk >= 10:
                        # 中强趋势快翻仓 (2026-10-21 用户, 独立信号): 峰≥10(低于11), 去前根门, need门保留
                        _wl_m, _chop_m, _wl_adj_m, _ws_m, _pk_m = _fav_wave_info(1)
                        _op_cnt_m = _count_ops_from_open(_pk_m)
                        if _op_cnt_m >= 4:
                            _base_m = max(0.40, 0.8 * (0.97 ** max(0, _wl_adj_m - 3)))
                            _need_m = 0.8 - (0.8 - _base_m) * _damp_factor_gw()
                        else:
                            _need_m = 0.8
                        if _wl_adj_m >= 15:
                            _need_m *= 0.7
                        _need_m = max(0.30, _need_m)
                        if _REBOUND_BLUE:
                            _need_m *= _rebound_factor_blue(i)
                        _ratio_m = abs(hist[i]) / pk if pk > 0 else 1
                        if _ratio_m <= _need_m:
                            actions.append((i, tstr, "平", "call", px_exec, f"中强趋势快翻仓(峰{pk:.0f},比{_ratio_m:.2f})"))
                            pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; wait_red = wait_blue = False; _decay_entry_hist = 0
                            _flip_to = "put"
                        else:
                            actions.append((i, tstr, "持", "call", px_exec, f"中强快翻持有(峰{pk:.0f},比{_ratio_m:.2f}>{_need_m:.2f})"))
                            _flip_to = None
                    else:
                        actions.append((i, tstr, "平", "call", px_exec, f"蓝衰减短峰只平(峰{pk:.0f})"))
                        skip_open = True
                    if _flip_to or "短峰只平" in actions[-1][5]:
                        pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; wait_red = wait_blue = False; _decay_entry_hist = 0
                    if not _flip_to:
                        last_action_bar = i
            # 波浪减震平仓 (2026-10-03 用户): 长有利波浪里, 平顺势仓需要多根确认
            def _need_confirm_bars_gw(_sign):
                _wl, _chop, _wl_adj, _ws, _pk = _fav_wave_info(_sign)
                _op_cnt = _count_ops_from_open(_pk)
                if _op_cnt < 4:
                    return 1
                _base_bars = 1 + max(0, int((_wl_adj - 5) // 8))
                return 1 + int((_base_bars - 1) * _damp_factor_gw())
            # (2026-10-06: 持call/put时2根超短/空柱后算新波，与watcher.py同步)
            # 短柱(|h|<=3)则gap+1；非短且非同色则gap清零；同色短衰减中保持gap
            _h_abs = abs(hist[i]) if not np.isnan(hist[i]) else 999
            _is_short_wk = _h_abs <= 3
            _h_abs = abs(hist[i]) if not np.isnan(hist[i]) else 999
            _is_short_wk = _h_abs <= 3
            # 2026-10-07 用户: 空仓时不攒gap，只在持仓期间数新波
            if _is_short_wk and pos is not None:
                _call_gap_wk += 1
                _put_gap_wk += 1
            elif pos is None:
                _call_gap_wk = 0; _put_gap_wk = 0
            else:
                if not (not np.isnan(hist[i]) and hist[i] < 0):
                    _call_gap_wk = 0
                if not (not np.isnan(hist[i]) and hist[i] > 0):
                    _put_gap_wk = 0
            # 反向出现即平: 持call真红出现(前3根无真红) -> 平; 持put真蓝出现(前3根无真蓝) -> 平
            # (2026-10-06: 新波里红/蓝出现才平，需2根超短/空柱后的新波，与watcher.py同步)
            if pos and (i - last_action_bar) >= 2:
                if pos == "call":
                    _need_r = _need_confirm_bars_gw(1)
                    _consec = 0
                    for _j in range(i, -1, -1):
                        if is_true_red(_j):
                            _consec += 1
                        else:
                            break
                    _seq_s = i - _consec + 1
                    _had = any(is_true_red(j) for j in range(max(start_idx, _seq_s-3), _seq_s))
                    # 同波红翻仓开的不算"新出现"
                    _ws_r = i
                    while _ws_r > 0 and _ws_cont_red(_ws_r-1):
                        _ws_r -= 1
                    _same_wave = (_call_red_ws is not None and _ws_r == _call_red_ws)
                    # (2026-10-06: 需2根超短/空柱后的新波才平，与watcher.py同步)
                    # (2026-10-06 用户20:48: 放宽到1根真柱即平)
                    # (2026-10-07 用户: 新波里灰柱也平，2根递增或1根>=5，对称)
                    def _is_gray_gw(j):
                        if j < 0 or j >= len(hist): return False
                        _hj = hist[j]
                        if np.isnan(_hj) or abs(_hj) <= 3: return False
                        return not is_true_blue(j) and not is_true_red(j)
                    _new_wave_call_close = False
                    _nw_reason = ""
                    if _call_gap_wk >= 2:
                        # 2026-10-07 用户: 灰柱在零下也算红出现
                        _is_gray_below_gw = _is_gray_gw(i) and hist[i] < 0 if i < len(hist) and not np.isnan(hist[i]) else False
                        if _consec >= 1 or _is_gray_below_gw:
                            _new_wave_call_close = True; _nw_reason = "红出现平call(需1根)" if _consec >= 1 else "灰零下平call"
                        elif _is_gray_gw(i) and _is_gray_gw(i-1) and abs(hist[i]) > abs(hist[i-1]):
                            _new_wave_call_close = True; _nw_reason = "灰2连增平call"
                        elif _is_gray_gw(i) and abs(hist[i]) >= 5:
                            _new_wave_call_close = True; _nw_reason = "灰中柱平call"
                    if _new_wave_call_close:
                        actions.append((i, tstr, "平", "call", px_exec, _nw_reason))
                        pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; wait_red = wait_blue = False; _decay_entry_hist = 0
                        last_action_bar = i
                    # (2026-10-06 用户16:19对称: 下穿零线就平call，与watcher.py同步)
                    # (2026-10-07 用户: 波浪管理版 - 上过≥0后到<-3就平)
                    elif pos == "call" and (i - last_action_bar) >= 2:
                        _h_n_c = hist[i] if not np.isnan(hist[i]) else 0
                        if _h_n_c >= 0:
                            _was_above_zero_wk = True
                        if _was_above_zero_wk and _h_n_c < -3:
                            actions.append((i, tstr, "平", "call", px_exec, f"下穿零线平call(波浪管理)"))
                            pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; wait_red = wait_blue = False; _decay_entry_hist = 0
                            _was_above_zero_wk = False
                            last_action_bar = i

                elif pos == "put":
                    _need_b = _need_confirm_bars_gw(-1)
                    _consec = 0
                    for _j in range(i, -1, -1):
                        if is_true_blue(_j):
                            _consec += 1
                        else:
                            break
                    _seq_s = i - _consec + 1
                    _had = any(is_true_blue(j) for j in range(max(start_idx, _seq_s-3), _seq_s))
                    # 同波蓝翻仓开的不算"新出现" (2026-10-04 fix: 只有真红才切断波, 灰/空柱不切断)
                    _ws_b = i
                    while _ws_b > 0 and _ws_cont_blue(_ws_b-1):
                        _ws_b -= 1
                    _same_wave_b = (_put_blue_ws is not None and _ws_b == _put_blue_ws)
                    # (2026-10-06: 需2根超短/空柱后的新波才平，与watcher.py同步)
                    # (2026-10-06 用户20:48: 放宽到1根真柱即平)
                    # (2026-10-07 用户: 新波里灰柱也平，2根递增或1根>=5，对称)
                    def _is_gray_gw2(j):
                        if j < 0 or j >= len(hist): return False
                        _hj = hist[j]
                        if np.isnan(_hj) or abs(_hj) <= 3: return False
                        return not is_true_blue(j) and not is_true_red(j)
                    _new_wave_put_close = False
                    _nw_reason2 = ""
                    if _put_gap_wk >= 2:
                        # 2026-10-07 用户: 灰柱在零上也算蓝出现
                        _is_gray_above_gw = _is_gray_gw2(i) and hist[i] > 0 if i < len(hist) and not np.isnan(hist[i]) else False
                        if _consec >= 1 or _is_gray_above_gw:
                            _new_wave_put_close = True; _nw_reason2 = "蓝出现平put(需1根)" if _consec >= 1 else "灰零上平put"
                        elif _is_gray_gw2(i) and _is_gray_gw2(i-1) and abs(hist[i]) > abs(hist[i-1]):
                            _new_wave_put_close = True; _nw_reason2 = "灰2连增平put"
                        elif _is_gray_gw2(i) and abs(hist[i]) >= 5:
                            _new_wave_put_close = True; _nw_reason2 = "灰中柱平put"
                    if _new_wave_put_close:
                        actions.append((i, tstr, "平", "put", px_exec, _nw_reason2))
                        pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; wait_red = wait_blue = False; _decay_entry_hist = 0
                        last_action_bar = i
                    # (2026-10-06 用户16:19: 上穿零线就平put，与watcher.py同步)
                    # (2026-10-07 用户: 波浪管理版 - 下过≤0后到>3就平)
                    elif pos == "put" and (i - last_action_bar) >= 2:
                        _h_n = hist[i] if not np.isnan(hist[i]) else 0
                        if _h_n <= 0:
                            _was_below_zero_wk = True
                        if _was_below_zero_wk and _h_n > 3:
                            actions.append((i, tstr, "平", "put", px_exec, f"上穿零线平put(波浪管理)"))
                            pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; wait_red = wait_blue = False; _decay_entry_hist = 0
                            _was_below_zero_wk = False
                            last_action_bar = i

            # 同向消失即平: 真反色/空柱/反向灰柱才平 (2026-10-08 用户: 灰色反向了当然要平)
            # 波浪减震: 长波里需要连续多根才算消失
            if pos and (i - last_action_bar) >= 2:
                if pos == "put":
                    _need_gr = _need_confirm_bars_gw(-1)
                    _consec_gr = 0
                    for _j in range(i, -1, -1):
                        _hj = hist[_j]
                        _gray_pos = False  # 对照测试临时关闭灰反向
                        if (is_true_blue(_j) or abs(_hj) <= 3 or np.isnan(_hj) or _gray_pos):
                            _consec_gr += 1
                        else:
                            break
                    _had_r = any(is_true_red(j) for j in range(max(start_idx, i-3), i))
                    if _had_r and _consec_gr >= _need_gr:
                        actions.append((i, tstr, "平", "put", px_exec, f"红消失平put(需{_need_gr}根)"))
                        pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; wait_red = wait_blue = False; _decay_entry_hist = 0
                        last_action_bar = i
                elif pos == "call":
                    _need_gb = _need_confirm_bars_gw(1)
                    _consec_gb = 0
                    for _j in range(i, -1, -1):
                        _hj = hist[_j]
                        _gray_neg = False  # 对照测试临时关闭灰反向
                        if (is_true_red(_j) or abs(_hj) <= 3 or np.isnan(_hj) or _gray_neg):
                            _consec_gb += 1
                        else:
                            break
                    _had_b = any(is_true_blue(j) for j in range(max(start_idx, i-3), i))
                    if _had_b and _consec_gb >= _need_gb:
                        actions.append((i, tstr, "平", "call", px_exec, f"蓝消失平call(需{_need_gb}根)"))
                        pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; wait_red = wait_blue = False; _decay_entry_hist = 0
                        last_action_bar = i
            # 重涨回补 (2026-10-02 用户优化; 2026-10-05 用户: 改成反手, 同bar开反向; 2026-10-06 用户: 2根都比翻仓bar长, 或1根长20%+; 2026-10-07 用户: 或比前一根长20%)
            # 蓝重涨->翻call, 红重涨->翻put. 2026-10-19 用户: 不检查持仓, 止损平仓后同bar也照常翻
            for _tdir, _tsgn in (("call", 1), ("put", -1)):
                if pos == _tdir:
                    continue
                _has_de = _decay_entry_hist > 0
                _s9ok = False
                if _HUIBU_S9 and not _has_de:
                    if _HUIBU_S9_MODE == "cumslope":
                        _tv = _9m_cumslope[i] if i < len(_9m_cumslope) else float("nan")
                    else:
                        _tv = _9m_slope5[i] if i < len(_9m_slope5) else float("nan")
                    _s9ok = (not np.isnan(_tv)) and abs(_tv) >= _HUIBU_S9_THRESH
                if not _has_de and not (_s9ok and _close_hist > 0):
                    continue
                _entry_hist = _decay_entry_hist if _has_de else _close_hist
                _tsign = _tsgn
                _found = False
                _hc = hist[i]
                if not np.isnan(_hc) and _hc * _tsign > 0 and abs(_hc) > max(3.0, _HUIBU_ABSHIST):
                    # 单根长20%+ (比开仓)
                    if abs(_hc) > _entry_hist * 1.2:
                        _found = True
                    # 2026-10-07 用户: 比前一根长(自适应: 3要翻倍, 8要1.2就行)
                    elif i - 1 >= 0:
                        _hp = hist[i - 1]
                        if (not np.isnan(_hp) and _hp * _tsign > 0 and abs(_hp) > 3):
                            _ahp = abs(_hp)
                            _need = 2.0 if _ahp <= 5 else 1.2
                            if abs(_hc) > _ahp * _need:
                                _found = True
                    # 2026-10-07 用户: 三根全递增，第三根比第一根(门槛更低)
                    if not _found and i - 2 >= 0:
                        _h1 = hist[i - 2]; _h2 = hist[i - 1]
                        if (not np.isnan(_h1) and not np.isnan(_h2)
                                and _h1 * _tsign > 0 and _h2 * _tsign > 0
                                and abs(_h1) > 3 and abs(_h2) > 3
                                and abs(_h2) > abs(_h1) and abs(_hc) > abs(_h2)):
                            _ah1 = abs(_h1)
                            _need1 = 2.0 if _ah1 <= 5 else 1.2
                            if abs(_hc) > _ah1 * _need1:
                                _found = True
                    # 连续2根都比翻仓bar长
                    if not _found and i - 1 >= 0:
                        _hp = hist[i - 1]
                        if (not np.isnan(_hp) and _hp * _tsign > 0 and abs(_hp) > 3
                                and abs(_hp) > _entry_hist and abs(_hc) > _entry_hist):
                            _found = True
                if _found:
                    if pos:
                        _lbl = "蓝重涨平put" if pos == "put" else "红重涨平call"
                        actions.append((i, tstr, "平", pos, px_exec, _lbl))
                        pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; wait_red = wait_blue = False
                    # 2026-10-05 用户: 经典回补, 同bar反手
                    actions.append((i, tstr, "开", _tdir, px_exec, f"重涨反手开{_tdir}"))
                    _decay_entry_hist = 0
                    pos = _tdir; _init_stop_vars(_tdir, px, i); last_action_bar = i
                    _fav_peak = abs(hist[i]); _fav_sign = 1 if _tdir == "call" else -1
                    wait_red = wait_blue = False
                    # 设置同波保护 (防止刚开的被"出现"误杀)
                    if _tdir == "call":
                        _wsr = i
                        while _wsr > 0 and _ws_cont_red(_wsr-1):
                            _wsr -= 1
                        _call_red_ws = _wsr
                    else:
                        _wsb = i
                        while _wsb > 0 and _ws_cont_blue(_wsb-1):
                            _wsb -= 1
                        _put_blue_ws = _wsb
            # 同色极短平仓 (2026-10-02 用户): 同一波≥4根真色后, 单根缩到|hist|<=4 -> 平.
            # 波浪减震 (2026-10-03): 长波里需要连续多根极短才平
            if pos and (i - last_action_bar) >= 2:
                _fsign = -1 if pos == "put" else 1
                _is_true = (lambda j: bool(q_down[j]) and abs(hist[j]) > 3) if pos == "put" else (lambda j: bool(q_up[j]) and abs(hist[j]) > 3)
                _wstart = i
                while _wstart > 0:
                    _ph = hist[_wstart - 1]
                    if np.isnan(_ph) or _ph * _fsign <= 0 or abs(_ph) <= 3:
                        break
                    _wstart -= 1
                _true_cnt = sum(1 for j in range(_wstart, i) if _is_true(j))
                _h = hist[i] if not np.isnan(hist[i]) else 0
                if _true_cnt >= 4 and _h * _fsign > 0 and abs(_h) <= 4:
                    _wl_t, _chop_t, _wl_adj_t, _ws_t, _pk_t = _fav_wave_info(_fsign)
                    _op_cnt_t = _count_ops_from_open(_pk_t)
                    if _op_cnt_t >= 4:
                        _base_tiny = 1 + max(0, int((_wl_adj_t - 5) // 8))
                        _need_tiny = 1 + int((_base_tiny - 1) * _damp_factor_gw())
                    else:
                        _need_tiny = 1
                    _consec_tiny = 0
                    for _jj in range(i, -1, -1):
                        _hj = hist[_jj]
                        if _hj * _fsign > 0 and abs(_hj) <= 4:
                            _consec_tiny += 1
                        else:
                            break
                    if _consec_tiny >= _need_tiny:
                        _lbl = ("红极短平put" if pos == "put" else "蓝极短平call") + f"(需{_need_tiny}根)"
                        actions.append((i, tstr, "平", pos, px_exec, _lbl))
                        pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; wait_red = wait_blue = False; _decay_entry_hist = 0
                        last_action_bar = i
            # 衰减停滞平仓 (2026-10-02 用户): decay开仓后连续4根同色不低于开仓80% -> 平.
            # 2026-10-07: 入场后必须经过4根不同bar才查停滞 (与live一致)
            if pos and (i - last_action_bar) >= 4 and _decay_entry_hist > 0:
                _ssign = 1 if pos == "put" else -1
                _stall = True
                for _j in range(max(0, i-3), i+1):
                    _sh = hist[_j]
                    if np.isnan(_sh) or _sh * _ssign <= 0 or abs(_sh) < _decay_entry_hist * 0.8:
                        _stall = False; break
                if _stall:
                    _lbl = "蓝停滞平put" if pos == "put" else "红停滞平call"
                    actions.append((i, tstr, "平", pos, px_exec, _lbl))
                    # 记录停滞重开信息 (2026-10-06 用户): 同波内继续衰减则重开
                    _stall_reopen_dir = pos
                    _stall_reopen_hist = abs(hist[i]) if not np.isnan(hist[i]) else 0
                    _stall_reopen_bar = i
                    # 找波浪起点
                    _ws = i
                    _wcont = _ws_cont_red if _ssign < 0 else _ws_cont_blue
                    while _ws > 0 and _wcont(_ws-1):
                        _ws -= 1
                    _stall_reopen_ws = _ws
                    pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; wait_red = wait_blue = False; _decay_entry_hist = 0
                    last_action_bar = i
            # 连色走强平仓: 持call连续8根真红且比开仓涨50% -> 平; 持put连续8根真蓝且涨50% -> 平
            if pos and (i - last_action_bar) >= 2:
                _entry = last_action_bar
                _entry_h = abs(hist[_entry]) if _entry >= 0 else 0
                if pos == "call":
                    _c = 0
                    for j in range(i, max(_entry, i-7)-1, -1):
                        if is_true_red(j):
                            _c += 1
                        else:
                            break
                    _grew = _entry_h > 0 and abs(hist[i]) >= _entry_h * 1.5
                    if _c >= 8 and _grew:
                        actions.append((i, tstr, "平", "call", px_exec, f"连红{_c}根涨{_entry_h:.0f}->{abs(hist[i]):.0f}平call"))
                        pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; wait_red = wait_blue = False; _decay_entry_hist = 0
                        last_action_bar = i
                        _streak_block_until = i + 5
                elif pos == "put":
                    _c = 0
                    for j in range(i, max(_entry, i-7)-1, -1):
                        if is_true_blue(j):
                            _c += 1
                        else:
                            break
                    _grew = _entry_h > 0 and abs(hist[i]) >= _entry_h * 1.5
                    if _c >= 8 and _grew:
                        actions.append((i, tstr, "平", "put", px_exec, f"连蓝{_c}根涨{_entry_h:.0f}->{abs(hist[i]):.0f}平put"))
                        pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; wait_red = wait_blue = False; _decay_entry_hist = 0
                        last_action_bar = i
                        _streak_block_until = i + 5
        # --- 开仓 (15:31版) ---
        # 停滞重开 (2026-10-06 用户): 停滞平仓后，同波内继续衰减20%+则重开
        if _stall_reopen_dir and not pos and not _flip_to:
            _sr_sign = -1 if _stall_reopen_dir == "call" else 1  # call对应红衰减，put对应蓝衰减
            _cur_ws = i
            _wcont = _ws_cont_red if _sr_sign < 0 else _ws_cont_blue
            while _cur_ws > 0 and _wcont(_cur_ws-1):
                _cur_ws -= 1
            # 同一波浪且继续衰减20%+
            if _cur_ws == _stall_reopen_ws and not np.isnan(hist[i]):
                _ch = hist[i]
                # 2026-10-07 用户: 比停滞那根衰减80%以上才重开; 停滞根到当前之间有更长的柱子则跳过
                _skip_ro = False
                try:
                    if _stall_reopen_bar >= 0:
                        for _bj in range(_stall_reopen_bar+1, i):
                            _bh = hist[_bj] if _bj < len(hist) else float('nan')
                            if not np.isnan(_bh) and abs(_bh) > _stall_reopen_hist:
                                _skip_ro = True
                                break
                except Exception:
                    pass
                if _ch * _sr_sign > 0 and abs(_ch) > 3 and _stall_reopen_hist > 0 and abs(_ch) < _stall_reopen_hist * 0.8 and not _skip_ro:
                    _lbl = "红继续衰减重开call" if _stall_reopen_dir == "call" else "蓝继续衰减重开put"
                    actions.append((i, tstr, "开", _stall_reopen_dir, px_exec, _lbl))
                    pos = _stall_reopen_dir; _init_stop_vars(_stall_reopen_dir, px, i); last_action_bar = i
                    _decay_entry_hist = abs(_ch)
                    wait_red = wait_blue = False
            # 波浪结束或已重开，清除标记
            if pos or _cur_ws != _stall_reopen_ws:
                _stall_reopen_dir = None; _stall_reopen_hist = 0; _stall_reopen_ws = -1; _stall_reopen_bar = -1
        # 2026-10-18 BIGK_BLOCK_ALL: 大K/3K跳过时挡同方向所有开仓（跟随+衰减+翻仓）
        # 大阴线→挡开P，大阳线→挡开C；反方向不挡。3K只管阴线（下跌）方向。
        _blk_put_open = False
        _blk_call_open = False
        _blk_why = ""
        if _BIGK_BLOCK_ALL:
            try:
                if i < len(high):
                    _bba_rng = float(high[i]) - float(low[i])
                    if _bba_rng >= 4.5:
                        _bba_dir = float(close[i]) - float(_openv[i]) if i < len(_openv) else 0
                        if _bba_dir < 0:
                            _blk_put_open = True; _blk_why = "bigk_bear"
                        elif _bba_dir > 0:
                            _blk_call_open = True; _blk_why = "bigk_bull"
            except Exception:
                pass
            if not _blk_put_open:
                try:
                    if i >= 2 and i < len(high):
                        _bba_d0s = warm["t"].iloc[i].strftime("%Y-%m-%d")
                        _bba_d2s = warm["t"].iloc[i-2].strftime("%Y-%m-%d")
                        if _bba_d0s == _bba_d2s:
                            _bba_nets = float(close[i]) - float(warm["open"].iloc[i-2])
                            if _bba_nets < -_K3_THRESH:
                                _blk_put_open = True; _blk_why = "k3_bear"  # 3K只管阴线
                except Exception:
                    pass
        _flip_blocked = _BIGK_BLOCK_ALL and ((_flip_to == "put" and _blk_put_open) or (_flip_to == "call" and _blk_call_open))
        # BETA_FLIP_SKIP: beta>阈值且slope5<0.5时跳过当前翻仓信号 (2026-10-22)
        # slope5>=0.5说明是V转，不跳过
        _s5_skip = _9m_slope5[i] if i < len(_9m_slope5) else 0
        _s5_skip = 0 if _s5_skip != _s5_skip else float(_s5_skip)
        _bcall_ok = (not _BETA_CALL_ONLY) or (_bskip_saved and _bskip_saved[0] == "call")
        if _flip_to and _BETA_FLIP_SKIP > 0 and _beta > _BETA_FLIP_SKIP and _s5_skip < _BETA_S5_THRESH and abs(_s5_skip) < _BETA_ABSS5_MAX and _bcall_ok and _bskip_saved:
            if _BETA_CLOSE_ONLY:
                # 只平仓不开反向: 保留翻仓平仓action (改reason), 不开反向, pos=None
                _n_rm = 0
                for _ai, _a in enumerate(actions):
                    if _a[0] == i and _a[2] == "平" and "翻仓" in str(_a[5]):
                        actions[_ai] = (_a[0], _a[1], _a[2], _a[3], _a[4], str(_a[5]).replace("翻仓", "beta平仓"))
                        _n_rm += 1
                if _n_rm:
                    # 2026-10-22: 记录beta/slope5/range_pos用于格子分析
                    _log_s5 = _s5_skip
                    _log_rpos = 50.0
                    try:
                        _lhi = float(high[max(0,i-4):i+1].max()); _llo = float(low[max(0,i-4):i+1].min())
                        _lcl = float(close[i])
                        _log_rpos = (_lcl - _llo) / (_lhi - _llo) * 100 if _lhi > _llo else 50.0
                    except:
                        pass
                    for _ai2, _a2 in enumerate(actions):
                        if _a2[0] == i and _a2[2] == "平" and "beta平仓" in str(_a2[5]):
                            actions[_ai2] = (_a2[0], _a2[1], _a2[2], _a2[3], _a2[4],
                                f"{_a2[5]}[beta={_beta:.4f},s5={_log_s5:.4f},rpos={_log_rpos:.1f}]")
                            break
                    # 恢复状态但保持pos=None (已平仓)
                    (_sv_pos, _decay_entry_hist, wait_red, wait_blue,
                     _was_above_zero_wk, _was_below_zero_wk, was_on_top, was_on_bottom,
                     _call_red_ws, _put_blue_ws, last_action_bar,
                     _fav_peak, _fav_sign, _follow_entry_bar,
                     _flip_peak, _flip_is_huibu, _is_red_flip_wk, skip_open,
                     _pending_flip_to, _pending_flip_bar, _pending_flip_reason) = _bskip_saved
                    _hold_watch = _bskip_hold_watch
                    pos = None
                    _flip_to = None
                    _n_beta_skip += 1
            else:
                # 移除本bar内所有翻仓平仓action (reason含"翻仓"且为"平"), 恢复翻仓前状态, 保持持仓
                _n_rm = 0
                _kept = []
                for _a in actions:
                    if _a[0] == i and _a[2] == "平" and "翻仓" in str(_a[5]):
                        _n_rm += 1
                        continue
                    _kept.append(_a)
                if _n_rm:
                    actions[:] = _kept
                    (pos, _decay_entry_hist, wait_red, wait_blue,
                     _was_above_zero_wk, _was_below_zero_wk, was_on_top, was_on_bottom,
                     _call_red_ws, _put_blue_ws, last_action_bar,
                     _fav_peak, _fav_sign, _follow_entry_bar,
                     _flip_peak, _flip_is_huibu, _is_red_flip_wk, skip_open,
                     _pending_flip_to, _pending_flip_bar, _pending_flip_reason) = _bskip_saved
                    _hold_watch = _bskip_hold_watch
                    _flip_to = None
                    _n_beta_skip += 1
                    actions.append((i, tstr, "持", pos, px_exec, f"beta跳过翻仓(beta={_beta:.3f}>{_BETA_FLIP_SKIP})"))
        if _flip_to and not _flip_blocked:
            # 翻仓: 同bar直接开反向, 不受冷静期限制
            actions.append((i, tstr, "开", _flip_to, px_exec, f"长波回补开{_flip_to}" if _flip_is_huibu else f"翻仓开{_flip_to}"))
            if _DUMP_FLIPPUT and _flip_to == "put":
                _s5rec = _9m_slope5[i]
                _flip_put_slope5[i] = float(_s5rec) if not np.isnan(_s5rec) else None
                _flip_put_is_huibu[i] = bool(_flip_is_huibu)
                _hv = hist[i]
                _flip_put_truecolor[i] = "blue" if _hv > 3 else ("red" if _hv < -3 else "gray")
                # 跌幅力度(用户修正定义): 过去3根走完3mK收盘价差为基准
                _dr = None
                try:
                    _o_i, _c_i = float(_openv[i]), float(close[i])
                    if _c_i < _o_i and i >= 3:
                        _c3 = [float(close[i-3]), float(close[i-2]), float(close[i-1])]
                        _g3 = max(_c3) - min(_c3)
                        if _g3 > 0:
                            _dr = (_o_i - _c_i) / _g3
                except Exception:
                    pass
                _flip_put_drop_ratio[i] = _dr
                # 破位(用户修正版): 当前3mK收 < 过去3根走完3mK的最低low
                _bs = None
                try:
                    if i >= 3:
                        _bs = bool(float(close[i]) < min(float(low[i-3]), float(low[i-2]), float(low[i-1])))
                except Exception:
                    pass
                _flip_put_broke_support[i] = _bs
                # 连续阴线计数(含当前bar): 往前数收<开连续几根, 不跨日
                _cb = 0
                try:
                    _jj = i
                    _day_i = _warm_date9[_jj]
                    while _jj >= 0 and _warm_date9[_jj] == _day_i and float(close[_jj]) < float(_openv[_jj]):
                        _cb += 1
                        _jj -= 1
                except Exception:
                    pass
                _flip_put_consec_bear[i] = _cb
            pos = _flip_to; _init_stop_vars(_flip_to, px, i); last_action_bar = i; _entry_peak = _flip_peak; _flip_is_huibu = False
            _decay_entry_hist = abs(hist[i]) if not np.isnan(hist[i]) else 0
            _fav_peak = abs(hist[i]) if (not np.isnan(hist[i]) and ((_flip_to == "call" and hist[i] > 0) or (_flip_to == "put" and hist[i] < 0))) else 0
            _fav_sign = (1 if _flip_to == "call" else -1) if _fav_peak > 0 else 0
            # 反复翻仓惩罚跟踪 (2026-10-03 用户)
            _recent_flips.append(i)
            _recent_flips = _recent_flips[-5:]
        elif not pos and not skip_open and (i - last_action_bar) >= 2 and i >= _streak_block_until and hm < "15:55":
            # 开仓要求峰值>=15 (和翻仓一样, 短衰减只平不开)
            # 长波逆势开仓要求深衰减 (2026-10-03 用户): 红长浪(波>=15)开call需衰减到峰值50%以下, 蓝对称
            _OPEN_MIN_PEAK = 14
            _pk_r = (peak_red_m if _is_morning else peak_red).get(i, 0)
            _pk_b = (peak_blue_m if _is_morning else peak_blue).get(i, 0)
            _rwlo = wave_len_gw(i, -1)
            _bwlo = wave_len_gw(i, 1)
            _deep_c = True
            _deep_p = True
            if _rwlo >= 15 and _pk_r > 0 and not np.isnan(hist[i]):
                if abs(hist[i]) / _pk_r > 0.5:
                    _deep_c = False
            if _bwlo >= 15 and _pk_b > 0 and not np.isnan(hist[i]):
                if abs(hist[i]) / _pk_b > 0.5:
                    _deep_p = False
            # 2026-10-13 实验K: Decay Open时间过滤 — 红衰减开call只允许15:00之后
            _hhmm_r = tstr[6:] if len(tstr) >= 11 else "99:99"
            if sig == "RED_DECAY_LONG" and _pk_r >= _OPEN_MIN_PEAK and _deep_c and _hhmm_r >= "15:00" and not _blk_call_open:
                actions.append((i, tstr, "开", "call", px_exec, f"红衰减信号(峰{_pk_r:.0f})"))
                pos = "call"; _init_stop_vars("call", px, i); _was_above_zero_wk = False; _was_below_zero_wk = False; was_on_top = False
                _fav_peak = abs(hist[i]) if (not np.isnan(hist[i]) and hist[i] > 0) else 0; _fav_sign = 1 if (not np.isnan(hist[i]) and hist[i] > 0) else 0
                last_action_bar = i
                wait_red = wait_blue = False
                _entry_peak = _pk_r
                _decay_entry_hist = abs(hist[i]) if not np.isnan(hist[i]) else 0
                # 记录红波起点, 防止"红出现"误杀同波 (2026-10-05 fix)
                _wsr = i
                while _wsr > 0 and _ws_cont_red(_wsr-1):
                    _wsr -= 1
                _call_red_ws = _wsr
            elif sig == "BLUE_DECAY_SHORT" and _pk_b >= _OPEN_MIN_PEAK and _deep_p and (tstr[6:] if len(tstr) >= 11 else "99:99") < "10:00" and not _blk_put_open:
                # 2026-10-13 实验K: Decay Open时间过滤 — 蓝衰减开put只允许10:00之前
                actions.append((i, tstr, "开", "put", px_exec, f"蓝衰减信号(峰{_pk_b:.0f})"))
                pos = "put"; _init_stop_vars("put", px, i); _was_above_zero_wk = False; _was_below_zero_wk = False; was_on_bottom = False
                _fav_peak = abs(hist[i]) if (not np.isnan(hist[i]) and hist[i] < 0) else 0; _fav_sign = -1 if (not np.isnan(hist[i]) and hist[i] < 0) else 0
                last_action_bar = i
                wait_red = wait_blue = False
                _entry_peak = _pk_b
                _decay_entry_hist = abs(hist[i]) if not np.isnan(hist[i]) else 0
                # 记录蓝波起点, 防止"蓝出现"误杀同波 (2026-10-05 fix, 与 watcher.py 同步)
                _wsb = i
                while _wsb > 0 and _ws_cont_blue(_wsb-1):
                    _wsb -= 1
                _put_blue_ws = _wsb
            elif wait_red and is_true_red(i):
                actions.append((i, tstr, "开", "put", px_exec, "等到红柱开put"))
                pos = "put"; _init_stop_vars("put", px, i); _was_above_zero_wk = False; _was_below_zero_wk = False; was_on_bottom = False; wait_red = False
                _fav_peak = abs(hist[i]) if (not np.isnan(hist[i]) and hist[i] < 0) else 0; _fav_sign = -1 if (not np.isnan(hist[i]) and hist[i] < 0) else 0
                last_action_bar = i
            elif wait_blue and is_true_blue(i):
                actions.append((i, tstr, "开", "call", px_exec, "等到蓝柱开call"))
                pos = "call"; _init_stop_vars("call", px, i); _was_above_zero_wk = False; _was_below_zero_wk = False; was_on_top = False; wait_blue = False
                _fav_peak = abs(hist[i]) if (not np.isnan(hist[i]) and hist[i] > 0) else 0; _fav_sign = 1 if (not np.isnan(hist[i]) and hist[i] > 0) else 0
                last_action_bar = i
            # 碎波暂停触发检查 (2026-10-04 用户): 刚平的跟随单
            if _follow_entry_dir_wk and pos is None:
                try:
                    _fspy_out = close[i]
                    _fp = (_fspy_out - _follow_entry_spy_wk) if _follow_entry_dir_wk == "call" else (_follow_entry_spy_wk - _fspy_out)
                    _follow_pnls_wk.append((_follow_entry_dir_wk, _follow_entry_peak_wk, _fp, _follow_entry_date_wk))
                    _follow_pnls_wk = _follow_pnls_wk[-5:]
                    # 2026-10-07 用户: 先不要碎波暂停
                    # if len(_follow_pnls_wk) >= 2:
                    #     _aa, _bb = _follow_pnls_wk[-2], _follow_pnls_wk[-1]
                    #     if (_aa[0] == _bb[0] and _aa[3] == _bb[3] and _aa[3] != "" and
                    #         _aa[1] < 18 and _bb[1] < 18 and _aa[2] < 0 and _bb[2] < 0):
                    #         _frag_pause_until_wk = i + 10
                except Exception:
                    pass
                _follow_entry_dir_wk = None
            # 跟随逻辑独立执行 (2026-10-05 fix: 原来在 else 里, 被碎波检查吞掉)
            if True:
                # 两根长柱跟随: 新波开始才跟 (往前连色不超过4根); 第二根比第一根长
                # (2026-10-01 用户: 红后蓝/灰后蓝都算新波开始)
                FOLLOW_MIN_LEN = 8
                # 大K双机制2 不跟随 (2026-10-09 用户): ≥4.5点当根跳过跟随
                _bigk_skip_wk = False
                try:
                    if i < len(high):
                        _sk_rng = float(high[i]) - float(low[i])
                        if _sk_rng >= 4.5:
                            _bigk_skip_wk = True
                            actions.append((i, f"{day}T{hm}", "持", pos if pos else "-", px_exec, f"大K跳过跟随(幅度{_sk_rng:.2f})"))
                except Exception:
                    pass
                # 3-bar大K不跟随 (空头版, 宽松XX=2.5, 2026-10-11): 3根净跌超2.5 → 当根跳过put标准跟随
                # 只拦"两根长红跟随", 不拦灰后/长波/续波跟随
                k3_skip = False
                try:
                    if i >= 2 and i < len(high):
                        _k3_d0s = warm["t"].iloc[i].strftime("%Y-%m-%d")
                        _k3_d2s = warm["t"].iloc[i-2].strftime("%Y-%m-%d")
                        if _k3_d0s == _k3_d2s:  # 不跨交易日
                            _k3_nets = float(close[i]) - float(warm["open"].iloc[i-2])
                            if _k3_nets < -_K3_THRESH:
                                k3_skip = True
                                actions.append((i, f"{day}T{hm}", "持", pos if pos else "-", px_exec, f"3K跳过跟随P(净{_k3_nets:.2f})"))
                                k3_skip_count += 1
                except Exception:
                    pass
                # 波浪长度 (2026-10-03 用户: 波浪走了5根以上就不查背离)
                _fwl = 1
                try:
                    _wsg = 1 if hist[i] > 0 else (-1 if hist[i] < 0 else 0)
                    for _wj in range(i-1, -1, -1):
                        _hsj = 1 if hist[_wj] > 0 else (-1 if hist[_wj] < 0 else 0)
                        if _hsj == _wsg and _wsg != 0:
                            _fwl += 1
                        else:
                            break
                except Exception:
                    _fwl = 1
                _follow_do_div = _fwl <= 5
                # 波浪峰值 (2026-10-04 用户: 碎波暂停用)
                _fwp = abs(hist[i])
                try:
                    _ws2 = 1 if hist[i] > 0 else (-1 if hist[i] < 0 else 0)
                    for _wj2 in range(i, -1, -1):
                        _hs2 = 1 if hist[_wj2] > 0 else (-1 if hist[_wj2] < 0 else 0)
                        if _hs2 == _ws2 and _ws2 != 0 and abs(hist[_wj2]) > 3:
                            _fwp = max(_fwp, abs(hist[_wj2]))
                        else:
                            break
                except Exception:
                    pass
                # 碎波暂停检查 (2026-10-04 用户)
                _frag_paused_wk = i < _frag_pause_until_wk
                # 跟随不跨天 (2026-10-02 用户: 08-10开盘跟随误用周五柱子)
                _same_day = (_day_arr[i] == _day_arr[i-1]) if i >= 1 and i < len(_day_arr) else False
                # 2026-10-08 用户: 开盘忽略期的柱子不参与跟随计算 (i-1在忽略期内则不跟随)
                _prev_in_ignore = ((i-1) - _day_start) < _ign_n_wk if i >= 1 else True
                if i >= 1 and _same_day and not _frag_paused_wk and not _prev_in_ignore:
                    # 2026-10-07 用户: 去掉第一根长度限制，只要求同色真红/真蓝
                    # 2026-10-08 用户: 谨慎跟随 — 前面有2+根不达标真柱(|h|<8)时，要3根递增且第三根|h|>=10
                    # 2026-10-21 日趋势跟随放宽: 顺趋势方向灰算真色 (强涨日call侧hist>3即算蓝, 强跌日put侧hist<-3即算红)
                    _dr_fol = _dayregime(i, hm) if _DAYREGIME_FOLLOW else 0
                    if _dr_fol == 1:
                        _fr = is_true_red(i) and abs(hist[i]) >= FOLLOW_MIN_LEN and \
                              is_true_red(i-1)
                        _fb = hist[i] > 3 and abs(hist[i]) >= FOLLOW_MIN_LEN and \
                              hist[i-1] > 3
                    elif _dr_fol == -1:
                        _fr = hist[i] < -3 and abs(hist[i]) >= FOLLOW_MIN_LEN and \
                              hist[i-1] < -3
                        _fb = is_true_blue(i) and abs(hist[i]) >= FOLLOW_MIN_LEN and \
                              is_true_blue(i-1)
                    else:
                        _fr = is_true_red(i) and abs(hist[i]) >= FOLLOW_MIN_LEN and \
                              is_true_red(i-1)
                        _fb = is_true_blue(i) and abs(hist[i]) >= FOLLOW_MIN_LEN and \
                              is_true_blue(i-1)
                    if _FOLLOW_PREVLEN and (_fr or _fb):
                        # 2026-10-21 用户: 第一根的前一根不能比第一根长; 第一根是当日首根则不查
                        if i - 1 > _day_start and abs(hist[i-2]) > abs(hist[i-1]):
                            _fr = _fb = False
                    # 中强趋势跟随信号 (2026-10-21 用户, 独立): cumslope定调+灰算真色
                    _mt_fr = _mt_fb = False
                    if _MIDTREND_FOLLOW:
                        _mt_reg = _midtrend_regime(i, hm)
                        _mt_growing = abs(hist[i]) > abs(hist[i-1])
                        _mt_ch_ok = True
                        if _MT_FOLLOW_CH25 > 0:
                            _mt_ch_ok = (not np.isnan(_ch25[i])) and abs(_ch25[i]) >= _MT_FOLLOW_CH25
                        # 动态阈值 (2026-10-21 用户): thr=clamp(10-chc*|ch25|-s9c*|s9|,6,10)
                        _mt_thr = FOLLOW_MIN_LEN
                        if _MT_FOLLOW_DYNTH:
                            _chv = abs(_ch25[i]) if not np.isnan(_ch25[i]) else 0.0
                            _s9v = abs(_9m_slope5[i]) if i < len(_9m_slope5) and not np.isnan(_9m_slope5[i]) else 0.0
                            _mt_thr = max(6.0, min(10.0, 10.0 - _MT_DYNTH_CHC*_chv - _MT_DYNTH_S9C*_s9v))
                        # 新波检查 (2026-10-21 用户): 往前同色连续不超过4根
                        _mt_newwave_ok = True
                        if _MT_FOLLOW_NEWWAVE:
                            _mt_newwave_ok = False
                            _cc = 0
                            for _k in range(i, max(start_idx, i-8), -1):
                                _hk = hist[_k]
                                if _hk != _hk: break
                                # 以当前bar颜色为准数连续
                                _cur_neg = hist[i] < 0
                                if (_hk < 0) == _cur_neg and abs(_hk) > 3:
                                    _cc += 1
                                else:
                                    break
                            _mt_newwave_ok = _cc <= _MT_NEWWAVE_MAXCONSEC
                        _mt_gate = _mt_ch_ok and _mt_newwave_ok
                        # 2026-10-21 用户: 中强跟随统一过滤(红蓝)
                        # ch>=12, |h|>=10, |s9|<=0.8(只滤太强)
                        _mt_extra = True
                        if _MT_CH12:
                            _mt_extra = _mt_extra and (not np.isnan(_ch25[i])) and abs(_ch25[i]) >= 12
                        if _MT_H10:
                            _mt_extra = _mt_extra and abs(hist[i]) >= 10
                        if _MT_S9MAX > 0:
                            _s9a = abs(_9m_slope5[i]) if i < len(_9m_slope5) and not np.isnan(_9m_slope5[i]) else float('nan')
                            _mt_extra = _mt_extra and (not np.isnan(_s9a)) and _s9a <= _MT_S9MAX
                        # 灰 vs 真色 (2026-10-21 用户测试)
                        if _MT_GRAY_OK:
                            _mt_red_ok = hist[i] < -3 and hist[i-1] < -3
                            _mt_blue_ok = hist[i] > 3 and hist[i-1] > 3
                        else:
                            _mt_red_ok = is_true_red(i) and is_true_red(i-1)
                            _mt_blue_ok = is_true_blue(i) and is_true_blue(i-1)
                        if _mt_reg == 1:
                            _mt_fb = _mt_blue_ok and abs(hist[i]) >= _mt_thr and _mt_growing and _mt_gate and _mt_extra
                        elif _mt_reg == -1:
                            _mt_fr = _mt_red_ok and abs(hist[i]) >= _mt_thr and _mt_growing and _mt_gate and _mt_extra
                    _growing = abs(hist[i]) > abs(hist[i-1])
                    # 顺强趋势跟随 (2026-10-18 用户): 11:00后+cumslope<-0.08, 放宽松
                    _st_sig = False
                    if _ST_FOLLOW and _st_gate(i, hm):
                        # 灰零下算真红: hist<-3即可 (真红或灰红); TRUE_ONLY时必须真色
                        _st_red_i = (is_true_red(i) if _ST_TRUE_ONLY else (hist[i] < -3 and hist[i] == hist[i]))
                        _st_red_prev = (is_true_red(i-1) if _ST_TRUE_ONLY else (hist[i-1] < -3 and hist[i-1] == hist[i-1]))
                        # 放宽松版两根跟随: 两根零下, 第二根|h|>=8且递增
                        # 2026-10-18: 必须是新波浪 - 前8根里有正柱子说明方向变过，是新波；全负才是延续
                        _st_fr = _st_red_i and abs(hist[i]) >= FOLLOW_MIN_LEN and _st_red_prev and _growing
                        if _st_fr:
                            _has_pos = False
                            for _stq in range(max(start_idx, i-9), i-1):
                                _hq = hist[_stq]
                                if _hq == _hq and _hq > 0:
                                    _has_pos = True
                                    break
                            if not _has_pos:
                                _st_fr = False
                        # 新波条件①: 前一根只要求hist<0, 本根递增且|h|>=6
                        # 2026-10-18: 必须是新波浪 - 前8根里有正柱子说明方向变过，是新波；全负才是延续
                        _st_new1 = hist[i-1] < 0 and hist[i] < 0 and abs(hist[i]) > abs(hist[i-1]) and abs(hist[i]) >= 6
                        if _st_new1 and _ST_TRUE_ONLY and not is_true_red(i):
                            _st_new1 = False
                        if _st_new1:
                            _has_pos = False
                            for _stq in range(max(start_idx, i-9), i-1):
                                _hq = hist[_stq]
                                if _hq == _hq and _hq > 0:
                                    _has_pos = True
                                    break
                            if not _has_pos:
                                _st_new1 = False
                        # 新波条件②: 新波第一根|h|>=6 (前8根安静 |h|<=3)
                        _st_new2 = False
                        if hist[i] < 0 and abs(hist[i]) >= 6 and (not _ST_TRUE_ONLY or is_true_red(i)):
                            _st_quiet = True
                            for _stq in range(max(start_idx, i-8), i):
                                _hq = hist[_stq]
                                if not (_hq == _hq and abs(_hq) <= 3):
                                    _st_quiet = False
                                    break
                            _st_new2 = _st_quiet and (i-8 >= start_idx)
                        if _st_fr or _st_new1 or _st_new2:
                            _st_sig = True
                            # 2026-10-21: ch25过滤
                            if _ST_CH25_MIN > 0:
                                _c25v = abs(_ch25[i]) if not np.isnan(_ch25[i]) else 0.0
                                if _c25v < _ST_CH25_MIN:
                                    _st_sig = False
                            # 2026-10-22: hist下限过滤
                            if _ST_HIST_MIN > 0:
                                if abs(hist[i]) < _ST_HIST_MIN:
                                    _st_sig = False
                    # (2026-10-06: 新信号优先于仓位，持反向仓时先平再开，与watcher.py同步)
                    # 2026-10-18: 已持有put时不重复开仓，仓位归属保持给最初开仓信号
                    if _st_sig and pos != "put" and (not _blk_put_open if _BIGK_BLOCK_ALL else (not _bigk_skip_wk and not k3_skip)):
                        if pos == "call":
                            actions.append((i, tstr, "平", "call", px_exec, "跟随翻仓(持call遇强趋势跟随)"))
                            pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; last_action_bar = i
                        actions.append((i, tstr, "开", "put", px_exec, "强趋势跟随P"))
                        pos = "put"; _init_stop_vars("put", px, i); _was_above_zero_wk = False; _was_below_zero_wk = False; _call_gap_wk = 0; _put_gap_wk = 0; last_action_bar = i; _decay_entry_hist = 0
                        _fav_peak = abs(hist[i]) if (not np.isnan(hist[i]) and hist[i] < 0) else 0; _fav_sign = -1 if (not np.isnan(hist[i]) and hist[i] < 0) else 0
                        _follow_entry_bar = i; _follow_entry_hist = abs(hist[i]); _follow_do_div_wk = _follow_do_div; _follow_entry_dir_wk = pos; _follow_entry_peak_wk = _fwp; _follow_entry_spy_wk = close[i]; _follow_entry_date_wk = _day_arr[i]
                        wait_red = wait_blue = False
                    elif _fr and _growing and (not _blk_put_open if _BIGK_BLOCK_ALL else (not _bigk_skip_wk and not k3_skip)):
                        if pos == "call":
                            actions.append((i, tstr, "平", "call", px_exec, "跟随翻仓(持call遇红跟随)"))
                            pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; last_action_bar = i
                        actions.append((i, tstr, "开", "put", px_exec, "两根长红跟随"))
                        pos = "put"; _init_stop_vars("put", px, i); _was_above_zero_wk = False; _was_below_zero_wk = False; _call_gap_wk = 0; _put_gap_wk = 0; last_action_bar = i; _decay_entry_hist = 0
                        _fav_peak = abs(hist[i]) if (not np.isnan(hist[i]) and hist[i] < 0) else 0; _fav_sign = -1 if (not np.isnan(hist[i]) and hist[i] < 0) else 0
                        _follow_entry_bar = i; _follow_entry_hist = abs(hist[i]); _follow_do_div_wk = _follow_do_div; _follow_entry_dir_wk = pos; _follow_entry_peak_wk = _fwp; _follow_entry_spy_wk = close[i]; _follow_entry_date_wk = _day_arr[i]
                        wait_red = wait_blue = False
                    # 顺强趋势跟随(上涨对称版, 2026-10-18 用户): 11:00后+cumslope>+0.20, 放宽松开call
                    _st_sig_up = False
                    if _ST_FOLLOW and _st_gate_up(i, hm):
                        # 灰零上算真蓝: hist>3即可 (真蓝或灰蓝); TRUE_ONLY时必须真色
                        _st_blue_i = (is_true_blue(i) if _ST_TRUE_ONLY else (hist[i] > 3 and hist[i] == hist[i]))
                        _st_blue_prev = (is_true_blue(i-1) if _ST_TRUE_ONLY else (hist[i-1] > 3 and hist[i-1] == hist[i-1]))
                        # 放宽松版两根跟随: 两根零上, 第二根|h|>=8且递增
                        # 2026-10-18: 必须是新波浪 - 前8根里有负柱子说明方向变过，是新波；全正才是延续
                        _st_fr_up = _st_blue_i and abs(hist[i]) >= FOLLOW_MIN_LEN and _st_blue_prev and _growing
                        if _st_fr_up:
                            _has_neg = False
                            for _stq in range(max(start_idx, i-9), i-1):
                                _hq = hist[_stq]
                                if _hq == _hq and _hq < 0:
                                    _has_neg = True
                                    break
                            if not _has_neg:
                                _st_fr_up = False
                        # 新波条件①: 前一根只要求hist>0, 本根递增且|h|>=6
                        # 2026-10-18: 必须是新波浪 - 前8根里有负柱子说明方向变过，是新波；全正才是延续
                        _st_new1_up = hist[i-1] > 0 and hist[i] > 0 and abs(hist[i]) > abs(hist[i-1]) and abs(hist[i]) >= 6
                        if _st_new1_up and _ST_TRUE_ONLY and not is_true_blue(i):
                            _st_new1_up = False
                        if _st_new1_up:
                            _has_neg = False
                            for _stq in range(max(start_idx, i-9), i-1):
                                _hq = hist[_stq]
                                if _hq == _hq and _hq < 0:
                                    _has_neg = True
                                    break
                            if not _has_neg:
                                _st_new1_up = False
                        # 新波条件②: 新波第一根|h|>=6 (前8根安静 |h|<=3)
                        _st_new2_up = False
                        if hist[i] > 0 and abs(hist[i]) >= 6 and (not _ST_TRUE_ONLY or is_true_blue(i)):
                            _st_quiet_up = True
                            for _stq in range(max(start_idx, i-8), i):
                                _hq = hist[_stq]
                                if not (_hq == _hq and abs(_hq) <= 3):
                                    _st_quiet_up = False
                                    break
                            _st_new2_up = _st_quiet_up and (i-8 >= start_idx)
                        if _st_fr_up or _st_new1_up or _st_new2_up:
                            _st_sig_up = True
                            # 2026-10-21: ch25过滤
                            if _ST_CH25_MIN > 0:
                                _c25v_up = abs(_ch25[i]) if not np.isnan(_ch25[i]) else 0.0
                                if _c25v_up < _ST_CH25_MIN:
                                    _st_sig_up = False
                            # 2026-10-22: hist下限过滤
                            if _ST_HIST_MIN > 0:
                                if abs(hist[i]) < _ST_HIST_MIN:
                                    _st_sig_up = False
                    # (2026-10-06: 新信号优先于仓位，持反向仓时先平再开，与watcher.py同步)
                    # 2026-10-18: 已持有call时不重复开仓，仓位归属保持给最初开仓信号
                    if _st_sig_up and pos != "call" and (not _blk_call_open if _BIGK_BLOCK_ALL else not _bigk_skip_wk):
                        if pos == "put":
                            actions.append((i, tstr, "平", "put", px_exec, "跟随翻仓(持put遇强趋势跟随)"))
                            pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; last_action_bar = i
                        actions.append((i, tstr, "开", "call", px_exec, "强趋势跟随C"))
                        pos = "call"; _init_stop_vars("call", px, i); _was_above_zero_wk = False; _was_below_zero_wk = False; _call_gap_wk = 0; _put_gap_wk = 0; last_action_bar = i; _decay_entry_hist = 0
                        _fav_peak = abs(hist[i]) if (not np.isnan(hist[i]) and hist[i] > 0) else 0; _fav_sign = 1 if (not np.isnan(hist[i]) and hist[i] > 0) else 0
                        _follow_entry_bar = i; _follow_entry_hist = abs(hist[i]); _follow_do_div_wk = _follow_do_div; _follow_entry_dir_wk = pos; _follow_entry_peak_wk = _fwp; _follow_entry_spy_wk = close[i]; _follow_entry_date_wk = _day_arr[i]
                        wait_red = wait_blue = False
                    elif _fb and _growing and (not _blk_call_open if _BIGK_BLOCK_ALL else not _bigk_skip_wk):
                        if pos == "put":
                            actions.append((i, tstr, "平", "put", px_exec, "跟随翻仓(持put遇蓝跟随)"))
                            pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; last_action_bar = i
                        actions.append((i, tstr, "开", "call", px_exec, "两根长蓝跟随"))
                        pos = "call"; _init_stop_vars("call", px, i); _was_above_zero_wk = False; _was_below_zero_wk = False; _call_gap_wk = 0; _put_gap_wk = 0; last_action_bar = i; _decay_entry_hist = 0
                        _fav_peak = abs(hist[i]) if (not np.isnan(hist[i]) and hist[i] > 0) else 0; _fav_sign = 1 if (not np.isnan(hist[i]) and hist[i] > 0) else 0
                        _follow_entry_bar = i; _follow_entry_hist = abs(hist[i]); _follow_do_div_wk = _follow_do_div; _follow_entry_dir_wk = pos; _follow_entry_peak_wk = _fwp; _follow_entry_spy_wk = close[i]; _follow_entry_date_wk = _day_arr[i]
                        wait_red = wait_blue = False
                    elif (not bool(q_down[i-1]) and not bool(q_up[i-1]) and abs(hist[i-1]) >= FOLLOW_MIN_LEN):
                        # 灰后跟随 (2026-10-04 用户; 2026-10-07 用户重构: 要求真色, 自适应20%/翻倍, 三连增比第一根)
                        _gdir = None
                        _gh1 = abs(hist[i-1])
                        _need_g = 2.0 if _gh1 < 5 else 1.2
                        # 第二根必须是真色
                        _is_tr = is_true_red(i); _is_tb = is_true_blue(i)
                        # 2026-10-21 日趋势跟随放宽: 顺趋势方向灰也算 (强涨日hist>3算蓝, 强跌日hist<-3算红)
                        if _dr_fol == 1 and hist[i] > 3:
                            _is_tb = True
                        elif _dr_fol == -1 and hist[i] < -3:
                            _is_tr = True
                        if (_is_tr or _is_tb) and abs(hist[i]) >= _gh1 * _need_g and abs(hist[i]) >= FOLLOW_MIN_LEN:
                            _gdir = "put" if _is_tr else "call"
                        # 三连增: 第三根真色比第一根(灰可), 同阈值
                        elif i >= 2 and not _gdir:
                            _h0, _h1, _h2 = hist[i-2], hist[i-1], hist[i]
                            _g0_gray = not bool(q_down[i-2]) and not bool(q_up[i-2])
                            _g1_gray = not bool(q_down[i-1]) and not bool(q_up[i-1])
                            if (_g0_gray or _g1_gray) and abs(_h0) >= FOLLOW_MIN_LEN \
                               and abs(_h2) > abs(_h1) > abs(_h0) \
                               and (_is_tr or _is_tb):
                                _need3 = 2.0 if abs(_h0) < 5 else 1.2
                                if abs(_h2) >= abs(_h0) * _need3:
                                    _gdir = "put" if _is_tr else "call"
                        if _gdir:
                            # (2026-10-06: 新信号优先于仓位，持反向仓时先平再开，与watcher.py同步)
                            if (_gdir == "put" and pos == "call") or (_gdir == "call" and pos == "put"):
                                actions.append((i, tstr, "平", pos, px_exec, f"跟随翻仓(持{pos}遇灰后跟随)"))
                                pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; last_action_bar = i
                            actions.append((i, tstr, "开", _gdir, px_exec, "灰后跟随"))
                            pos = _gdir; _init_stop_vars(_gdir, px, i); last_action_bar = i; _decay_entry_hist = 0
                            _fav_peak = abs(hist[i]) if (not np.isnan(hist[i]) and ((_gdir == "put" and hist[i] < 0) or (_gdir == "call" and hist[i] > 0))) else 0; _fav_sign = -1 if _gdir == "put" else 1
                            _follow_entry_bar = i; _follow_entry_hist = abs(hist[i]); _follow_do_div_wk = _follow_do_div; _follow_entry_dir_wk = pos; _follow_entry_peak_wk = _fwp; _follow_entry_spy_wk = close[i]; _follow_entry_date_wk = _day_arr[i]
                            wait_red = wait_blue = False
                    elif _MIDTREND_FOLLOW and (_mt_fr or _mt_fb):
                        # 中强趋势跟随 (2026-10-21 用户, 独立信号): cumslope定调+灰算真色
                        _mtdir = "put" if _mt_fr else "call"
                        if (_mtdir == "put" and pos == "call") or (_mtdir == "call" and pos == "put"):
                            actions.append((i, tstr, "平", pos, px_exec, f"跟随翻仓(持{pos}遇中强趋势跟随)"))
                            pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; last_action_bar = i
                        actions.append((i, tstr, "开", _mtdir, px_exec, "中强趋势跟随"))
                        pos = _mtdir; _init_stop_vars(_mtdir, px, i); last_action_bar = i; _decay_entry_hist = 0
                        _fav_peak = abs(hist[i]) if (not np.isnan(hist[i]) and ((_mtdir == "put" and hist[i] < 0) or (_mtdir == "call" and hist[i] > 0))) else 0; _fav_sign = -1 if _mtdir == "put" else 1
                        _follow_entry_bar = i; _follow_entry_hist = abs(hist[i]); _follow_do_div_wk = _follow_do_div; _follow_entry_dir_wk = pos; _follow_entry_peak_wk = _fwp; _follow_entry_spy_wk = close[i]; _follow_entry_date_wk = _day_arr[i]
                        wait_red = wait_blue = False
                    else:
                        # 长波降低开仓门槛 (2026-10-03 用户): 长波里单根长红或两根中等红即开
                        if _long_put_sig:
                            actions.append((i, tstr, "开", "put", px_exec, "长波跟随红"))
                            pos = "put"; _init_stop_vars("put", px, i); _was_above_zero_wk = False; _was_below_zero_wk = False; _call_gap_wk = 0; _put_gap_wk = 0; last_action_bar = i; _decay_entry_hist = 0
                            _fav_peak = abs(hist[i]) if (not np.isnan(hist[i]) and hist[i] < 0) else 0; _fav_sign = -1 if (not np.isnan(hist[i]) and hist[i] < 0) else 0
                            _follow_entry_bar = i; _follow_entry_hist = abs(hist[i]); _follow_do_div_wk = _follow_do_div; _follow_entry_dir_wk = pos; _follow_entry_peak_wk = _fwp; _follow_entry_spy_wk = close[i]; _follow_entry_date_wk = _day_arr[i]
                            wait_red = wait_blue = False
                        elif _long_call_sig:
                            actions.append((i, tstr, "开", "call", px_exec, "长波跟随蓝"))
                            pos = "call"; _init_stop_vars("call", px, i); _was_above_zero_wk = False; _was_below_zero_wk = False; _call_gap_wk = 0; _put_gap_wk = 0; last_action_bar = i; _decay_entry_hist = 0
                            _fav_peak = abs(hist[i]) if (not np.isnan(hist[i]) and hist[i] > 0) else 0; _fav_sign = 1 if (not np.isnan(hist[i]) and hist[i] > 0) else 0
                            _follow_entry_bar = i; _follow_entry_hist = abs(hist[i]); _follow_do_div_wk = _follow_do_div; _follow_entry_dir_wk = pos; _follow_entry_peak_wk = _fwp; _follow_entry_spy_wk = close[i]; _follow_entry_date_wk = _day_arr[i]
                            wait_red = wait_blue = False
                        # 续波跟随 (2026-10-03 用户): 空仓时上一波红+新红柱 -> 跟put; 蓝对称 (受冷静期限制)
                        # (2026-10-06: 新信号优先于仓位，持反向仓时先平再开，与watcher.py同步)
                        elif _cont_put:
                            if pos == "call":
                                actions.append((i, tstr, "平", "call", px_exec, "续波跟随翻仓(持call)"))
                                pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; last_action_bar = i
                            actions.append((i, tstr, "开", "put", px_exec, "续波跟随红"))
                            pos = "put"; _init_stop_vars("put", px, i); _was_above_zero_wk = False; _was_below_zero_wk = False; _call_gap_wk = 0; _put_gap_wk = 0; last_action_bar = i; _decay_entry_hist = 0
                            _fav_peak = abs(hist[i]) if (not np.isnan(hist[i]) and hist[i] < 0) else 0; _fav_sign = -1 if (not np.isnan(hist[i]) and hist[i] < 0) else 0
                            _follow_entry_bar = i; _follow_entry_hist = abs(hist[i]); _follow_do_div_wk = _follow_do_div; _follow_entry_dir_wk = pos; _follow_entry_peak_wk = _fwp; _follow_entry_spy_wk = close[i]; _follow_entry_date_wk = _day_arr[i]
                            wait_red = wait_blue = False
                        elif _cont_call:
                            if pos == "put":
                                actions.append((i, tstr, "平", "put", px_exec, "续波跟随翻仓(持put)"))
                                pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; last_action_bar = i
                            actions.append((i, tstr, "开", "call", px_exec, "续波跟随蓝"))
                            pos = "call"; _init_stop_vars("call", px, i); _was_above_zero_wk = False; _was_below_zero_wk = False; _call_gap_wk = 0; _put_gap_wk = 0; last_action_bar = i; _decay_entry_hist = 0
                            _fav_peak = abs(hist[i]) if (not np.isnan(hist[i]) and hist[i] > 0) else 0; _fav_sign = 1 if (not np.isnan(hist[i]) and hist[i] > 0) else 0
                            _follow_entry_bar = i; _follow_entry_hist = abs(hist[i]); _follow_do_div_wk = _follow_do_div; _follow_entry_dir_wk = pos; _follow_entry_peak_wk = _fwp; _follow_entry_spy_wk = close[i]; _follow_entry_date_wk = _day_arr[i]
                            wait_red = wait_blue = False
        # 止损窗口到期 (2026-10-28 用户): N根走完还拿着原单 -> 强制平 (W5的信号已在前面跑过, 到期才到这里)
        if _stop_win is not None and pos is not None and i - _stop_win["trig_bar"] == _STOP_WIN_BARS:
            actions.append((i, f"{day}T{hm}", "平", pos, px_exec, "止损窗口6根平"))
            pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; _call_gap_wk = 0; _put_gap_wk = 0; last_action_bar = i; was_on_top = was_on_bottom = False
            wait_red = wait_blue = False
            _follow_entry_bar = None
            _hold_watch = None
            _fav_peak = 0; _fav_sign = 0
            _stop_entry_dir = None
            k3_tp_bar = None; _bigtp_bar = None; _bigwait_target = None; _bigwait_bars = 0; k3_wait_target = None; k3_wait_bars = 0
            _close_hist = abs(hist[i]) if not np.isnan(hist[i]) else 0
            _stop_win = None
            _stop_triggered = True
            if _WIN_AFTER_ST_IMM and _hyb_run_st and _st_trend[i] != 0:
                _wa_new = "call" if _st_trend[i] == 1 else "put"
                actions.append((i, f"{day}T{hm}", "开", _wa_new, px_exec, f"窗口出来ST开{_wa_new}"))
                pos = _wa_new; _init_stop_vars(_wa_new, px, i); last_action_bar = i
            continue
        # HUIBU_S9 (2026-10-21): 记录本bar的平/开 -> _close_hist (平记|hist|, 开清零)
        if _HUIBU_S9:
            for _a in actions[_n_acts_before:]:
                if _a[2] == "平":
                    _close_hist = abs(hist[i]) if not np.isnan(hist[i]) else 0
                elif _a[2] == "开":
                    _close_hist = 0
    return actions


def run_vh(bars, eod_1m=None):
    """见模块 docstring。只返回传入 bars 最后一天的动作。"""
    warm = _normalize_bars(bars)
    if len(warm) == 0:
        return []
    actions = _run_all(warm, eod_1m)
    last_date = warm["t"].iloc[-1].date()
    dates = warm["t"].dt.date.values
    last_start = next(i for i, d in enumerate(dates) if d == last_date)
    return [a for a in actions if a[0] >= last_start]
