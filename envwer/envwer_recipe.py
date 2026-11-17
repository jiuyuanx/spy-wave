#!/usr/bin/env python3
"""WER 终栈⑪ (冠军版) Envelope 逐日载荷配方 (2026-11-17 vendored)。

配方与 research/er_leg_backtest_20261110/brk1112/compute_2weeks.py 的
run_day 冠军臂逐字同源: fork = tv_v67_er_overlay_wcent2f3sr (无 slope
层), er_cfg = CHAMP_CFG + RAMP_OVER + CE RF/F2/F3/shield (终栈⑪),
SL_K = 0.7875。day_payload = 冷启动单日跑 (指标/WER/CE/simulate 全
在当日帧内, 与 data_2weeks.json 口径一致); day_payload_warm = 暖机版
(2026-11-17 起 gen 改用: 前序交易日 + 目标日连续帧, 对齐定版整帧
口径, 冷版保留作对照)。骨架 (入参/校验/half-gap 标记/装配/产出
schema) 与 envint/envint_recipe.py 的 day_payload 同形, 以便 gen 与
页面构造层 (envwer_page) 沿用整合版接口。

与原配方的差别只有数据接入 (不动引擎数学):
  - df1 由入参传入 (gen 给当日 1m, 列 t/open/high/low/close, 美东
    墙钟 naive datetime64); 原配方从研究缓存 df_all 切当日;
  - 不再断言 N==390 与末根 15:59 (盘中部分日也要出数): 只要求首根
    09:30; wer_series1 用 envint 的 N<窗守卫版 (N>=窗时逐值一致);
  - exec 切片函数 (run_ce / assemble_cef / assemble_all /
    classify_fills) 用 envwer_defs 的模块函数 (函数体逐字)。
原配方的自验断言保留: ramp_trig 与本地 dose/ramp 逐项对齐、段首
WER 越 EFF 检查、dd total/n 与 recs 对齐、stack total 对齐。

产出 payload 与 envint 同构: {date, sday, day, n_bars, half, gap};
sday.trades/runs 恒空 (冠军版无 slope 层), stack_dd = 终栈逐笔
(含 ER 段 segs)。失败返回 (None, 错误信息), 由调用方决定呈现
(gen: 该节标注计算失败, 不回退旧口径)。
"""
import copy

import numpy as np

import envwer_defs as defs
import tv_v67_er_overlay_wcent2f3sr as forkW
import tv_v67_sl_sweep as sl
import wer_deep_common as W
from settle_official import gamma_roi  # noqa: F401  (与原管线同名导出)

RAMP_OVER = dict(brk=5, ramp_on=True, ramp_mode="max", ramp_K=6,
                 ramp_S0=0.2875, ramp_CAP=0.1625, ramp_BETA=0.25)
SL_OVER = {"ER_LEN": 15, "ASYM_SHIFT": 1.35, "MULT": 1.225}
SL_GLOBALS = ["ASYM_SHIFT", "GAUSS_LEN", "GAUSS_BW", "ATR_LEN", "SLOPE_LEN",
              "ER_LEN", "DI_LEN", "ADX_SMOOTH", "MULT", "MOM_LOOKBACK",
              "FAST_EMA_LEN", "MOM_SENS", "MAX_SHIFT_ATR", "EDGE_PCT",
              "MAX_OUTSIDE", "TAKEOVER_SLOPE", "ER_TH", "ADX_TH"]
CHAMP0 = dict(n=12, th=0.60, mode="A", branch="surge", k=5,
              **{"from": 0.40}, end=0.35, to=0.575)
CHAMP_CFG = dict(CHAMP0, brk=0, exit_caliber="new", to=0.675, end=0.30,
                 exit_bars=2, **{"from": 0.50},
                 dose_R=0.50, dose_CAP=0.025, dose_W=10)
CFG = dict(CHAMP_CFG, **RAMP_OVER, ce_method="RF", ce_M=3.75, ce_cf=1,
           ce_f2=True, ce_f3=True, ce_f3_shield=True)

# rs.BASES["1m_v2"] (run_sl_sweep) 的 params 与 PARAM_MAP, 逐字收录
BASE_PARAMS = dict(asym=1.35, momlb=10, maxsh=0.5, ema=3, sens=0.55,
                   gauss_len=30, gauss_bw=3.5, mult=1.00, atr_len=8,
                   er=12, di=16, adx=16, slope=7,
                   edge=20.0, max_outside=0.55, tslope=0.20)
PARAM_MAP = dict(asym="ASYM_SHIFT", momlb="MOM_LOOKBACK", maxsh="MAX_SHIFT_ATR",
                 ema="FAST_EMA_LEN", sens="MOM_SENS", gauss_len="GAUSS_LEN",
                 gauss_bw="GAUSS_BW", mult="MULT", atr_len="ATR_LEN",
                 er="ER_LEN", di="DI_LEN", adx="ADX_SMOOTH", slope="SLOPE_LEN",
                 edge="EDGE_PCT", max_outside="MAX_OUTSIDE",
                 tslope="TAKEOVER_SLOPE")


def _set_params_sl():
    """rs.set_params(BASES['1m_v2']) 等价 (fric=None 一支不触发)。"""
    for k, attr in PARAM_MAP.items():
        setattr(sl, attr, BASE_PARAMS[k])
    sl.ER_TH = 0.35
    sl.ADX_TH = 30


for k, attr in PARAM_MAP.items():
    setattr(forkW, attr, BASE_PARAMS[k])
forkW.ER_TH = 0.35
forkW.ADX_TH = 30
forkW.TAKEOVER_SLOPE = 0.10
forkW.SL_MODE = "atr"
forkW.CE_CST = None

ER_TO = 0.675
DOSE_R, DOSE_CAP, DOSE_W = 0.50, 0.025, 10
RAMP_K, RAMP_S0, RAMP_CAP, RAMP_BETA = 6, 0.2875, 0.1625, 0.25


def eff_parts(arr):
    n = len(arr)
    dose = np.zeros(n)
    ramp = np.zeros(n)
    for i in range(n):
        if i >= DOSE_W:
            dw = arr[i - DOSE_W:i]
            if np.all(np.isfinite(dw)):
                dr = DOSE_R - float(np.mean(dw))
                if dr > 0.0:
                    dose[i] = min(DOSE_CAP, dr)
        if i >= RAMP_K - 1:
            rw = arr[i - RAMP_K + 1:i + 1]
            if np.all(np.isfinite(rw)):
                G = float(rw[-1] - np.min(rw))
                if G > 0.0:
                    rdl = np.diff(rw)
                    rsum = float(np.sum(np.abs(rdl)))
                    if rsum > 0.0:
                        rmax = float(np.max(rdl))
                        S = (G / rsum) * (1.0 - RAMP_BETA * rmax / G)
                        if S < 0.0:
                            S = 0.0
                        if S > RAMP_S0:
                            ramp[i] = RAMP_CAP * min(
                                1.0, (S - RAMP_S0) / (1.0 - RAMP_S0))
    return ER_TO - np.maximum(dose, ramp), dose, ramp


def day_payload(df1, NEWDAY):
    """df1 = 当日 1m (t/open/high/low/close, 已按时段过滤并排序)。
    返回 (payload, None) 或 (None, err)。"""
    try:
        return _day_payload(df1, NEWDAY), None
    except Exception as e:  # noqa: BLE001 - gen 侧据此标注该节失败
        import traceback
        traceback.print_exc()
        return None, f"{type(e).__name__}: {e}"


def _day_payload(df1, NEWDAY):
    df1 = df1.reset_index(drop=True)
    N = len(df1)
    if N == 0:
        raise ValueError("no_data")
    hm = df1["t"].dt.strftime("%H:%M").values
    if hm[0] != "09:30":
        raise ValueError(f"first_bar {hm[0]}")
    half = bool(hm[-1] != "15:59")
    gap = bool(300 <= N < 380 and not half)
    dates1 = np.array([NEWDAY] * N)

    _set_params_sl()
    saved1 = {g: getattr(sl, g) for g in SL_GLOBALS}
    for k, v in SL_OVER.items():
        setattr(sl, k, v)
    try:
        ind1 = sl.compute_indicators(df1)
    finally:
        for k, v in saved1.items():
            setattr(sl, k, v)
    c1 = np.asarray(ind1["close"], dtype=float)
    o1 = df1["open"].to_numpy(dtype=float)
    dc = np.diff(c1, prepend=c1[0])
    dc[0] = 0.0

    def wer_series1(nn, lam_num, lam_den):
        from numpy.lib.stride_tricks import sliding_window_view
        if N < nn:
            return np.full(N, np.nan)
        win = sliding_window_view(dc, nn)
        wn = lam_num ** np.arange(nn - 1, -1, -1, dtype=float)
        wd = lam_den ** np.arange(nn - 1, -1, -1, dtype=float)
        num = np.abs(win @ wn)
        den = np.abs(win) @ wd
        vals = np.where(den > 0, num / np.where(den > 0, den, 1.0), 0.0)
        out = np.full(N, np.nan)
        out[nn:] = vals[1:]
        return out

    WERAS1 = wer_series1(12, 0.98, 0.96)
    EFF1, DOSE1, RAMP1 = eff_parts(WERAS1)

    defs.set_ce_ctx(N, np.asarray(ind1["atr"], dtype=float), c1,
                    np.asarray(ind1["high"], dtype=float),
                    np.asarray(ind1["low"], dtype=float))
    CE375_1 = defs.run_ce(3.75)

    defs.set_cef_ctx(o1, c1)
    DAYCTX1 = {NEWDAY: (np.arange(N), {int(g): int(g) for g in range(N)})}
    defs.set_f3_ctx(DAYCTX1, [NEWDAY], dates1)
    assemble_all1 = defs.assemble_all
    classify_fills1 = defs.classify_fills

    forkW.ER_ARRAYS = {12: WERAS1}
    forkW.ASYM_SHIFT = 1.35
    forkW.START_DATE, forkW.END_DATE = NEWDAY, NEWDAY
    forkW.SL_K = 0.7875
    forkW.CE_STATE = CE375_1[0]
    forkW.CE_CST = None
    tr = forkW.simulate(df1, ind1, 1, trend_entry=True, use_uturn=False,
                        use_bo=False, er_cfg=CFG)
    modes = copy.deepcopy(forkW.V67_STATS["entry_modes"])
    segs = copy.deepcopy(forkW.ER_INFO.get("segments", []))
    trig = copy.deepcopy(forkW.ER_INFO.get("ramp_trig", []))
    bad = sum(1 for i, dstr, dd_, rd_, wer_i, _f in trig
              if abs(DOSE1[i] - dd_) > 1e-6 or abs(RAMP1[i] - rd_) > 1e-6)
    assert bad == 0, f"{NEWDAY} ramp_trig mismatches {bad}/{len(trig)}"
    cross = [s0 for s0, s1, _ in [(s[0], s[1], s[2]) for s in segs]
             if not (WERAS1[s0] >= EFF1[s0] - 1e-12
                     and (s0 == 0 or WERAS1[s0 - 1] < EFF1[s0]))]
    assert not cross, f"{NEWDAY} seg crossing FAIL {cross[:5]}"
    recs = W.to_recs(tr, modes, dates1)

    fills_by_day, cnt = classify_fills1(recs, segs, "stack", "375")
    defs_, flips = assemble_all1(recs, segs, extra_by_day=fills_by_day,
                                 extra_key="fills")
    dd = defs_[NEWDAY]
    assert dd["total"] == round(sum(r["r"] for r in recs), 1), \
        f"{NEWDAY} dd total 对不上"
    assert dd["n"] == len(recs), f"{NEWDAY} dd n 对不上"

    slp = [t for t in recs if t["mode"] == "slope"]
    tot_slp = round(sum(float(t["r"]) for t in slp), 1)
    tot_stack = round(sum(float(t["r"]) for t in recs
                          if t["mode"] != "slope"), 1)
    strades = [t for t in dd["trades"] if t[3] != "slope"]
    n_slope_in_dd = sum(1 for t in dd["trades"] if t[3] == "slope")
    assert n_slope_in_dd == len(slp), \
        f"{NEWDAY} dd slope {n_slope_in_dd} != recs {len(slp)}"
    skeys = {f"{t[0]}:{t[2]}" for t in strades}
    stack_dd = dict(
        trades=strades,
        flips=[k for k in dd.get("flips", []) if k in skeys],
        fills=[k for k in dd.get("fills", []) if k in skeys],
        segs=dd["segs"],
        n=len(strades),
        n_er=sum(1 for t in strades if t[4] == 1),
        total=dd["total"])
    assert abs(stack_dd["total"] - tot_stack) < 1.0, \
        f"{NEWDAY} stack total 对不上"
    sday = dict(date=NEWDAY, runs=[], counts={}, trades=[],
                stack_dd=stack_dd, n_slope=len(slp),
                n_flips=sum(1 for t in slp if t["rsn"] == "FLIP"),
                total_slope=tot_slp, total_stack=tot_stack,
                total_all=round(tot_slp + tot_stack, 1))

    def r2(arr):
        return [None if not np.isfinite(v) else round(float(v), 2)
                for v in arr]

    def r4(arr):
        return [None if not np.isfinite(v) else round(float(v), 4)
                for v in arr]

    def r3(arr):
        return [None if not np.isfinite(v) else round(float(v), 3)
                for v in arr]

    slope = np.asarray(ind1["slope"], dtype=float)
    day = dict(
        times=[str(x) for x in hm],
        close=r2(c1), up=r2(np.asarray(ind1["upper"], dtype=float)),
        lo=r2(np.asarray(ind1["lower"], dtype=float)),
        chan=r2(np.asarray(ind1["chan_mid"], dtype=float)),
        mid=r2(np.asarray(ind1["mid"], dtype=float)), open=r2(o1),
        werchamp=r4(WERAS1), effstack=r3(EFF1),
        slope=r4(np.where(np.isfinite(slope), slope, np.nan)))
    return dict(date=NEWDAY, sday=sday, day=day, n_bars=N, half=half,
                gap=gap)


def day_payload_warm(dff, NEWDAY):
    """暖机版 day_payload (2026-11-17 user 令: 对齐定版整帧连续口径)。

    dff = 连续帧 1m (t/open/high/low/close, RTH 已过滤、排序、去重):
    若干前序交易日 + NEWDAY, NEWDAY 必须是帧的最后一个交易日。
    与冷版 day_payload 的差别只有帧语义 (配方本体/参数/断言逐字同源):
      - compute_indicators / WER / eff / CE 全部在整帧上算 (跨日连续,
        ADX/ER 的 RMA 播种头落在暖机段, 不再封锁目标日开盘段);
      - DAYCTX 含帧内全部日期 (loc = 全局->日局部), CHART_DAYS 只目标日;
      - simulate 记录窗 = [帧首日, NEWDAY] (状态机整帧演化, 与定版
        全帧 run 同构), 逐笔随后按 ed == NEWDAY 过滤;
      - day 展示序列切目标日段。
    返回结构与冷版一致, 另加 warm_days = 帧内目标日前的交易日数。
    失败返回 (None, 错误信息), 与冷版 wrapper 同款。"""
    try:
        return _day_payload_warm(dff, NEWDAY), None
    except Exception as e:  # noqa: BLE001 - gen 侧据此标注该节失败
        import traceback
        traceback.print_exc()
        return None, f"{type(e).__name__}: {e}"


def _day_payload_warm(dff, NEWDAY):
    dff = dff.reset_index(drop=True)
    NF = len(dff)
    if NF == 0:
        raise ValueError("no_data")
    dates_f = dff["t"].dt.strftime("%Y-%m-%d").values
    gidx = np.where(dates_f == NEWDAY)[0]
    if len(gidx) == 0:
        raise ValueError("target_day_missing")
    g0, g1 = int(gidx[0]), int(gidx[-1])
    if g1 != NF - 1 or len(gidx) != g1 - g0 + 1:
        raise ValueError("target_day_not_last_or_split")
    df1 = dff.iloc[g0:g1 + 1].reset_index(drop=True)
    N = len(df1)
    hm = df1["t"].dt.strftime("%H:%M").values
    if hm[0] != "09:30":
        raise ValueError(f"first_bar {hm[0]}")
    half = bool(hm[-1] != "15:59")
    gap = bool(300 <= N < 380 and not half)
    frame_days = list(dict.fromkeys(dates_f.tolist()))
    warm_days = len(frame_days) - 1

    _set_params_sl()
    saved1 = {g: getattr(sl, g) for g in SL_GLOBALS}
    for k, v in SL_OVER.items():
        setattr(sl, k, v)
    try:
        ind1 = sl.compute_indicators(dff)
    finally:
        for k, v in saved1.items():
            setattr(sl, k, v)
    c1 = np.asarray(ind1["close"], dtype=float)
    o1 = dff["open"].to_numpy(dtype=float)
    dc = np.diff(c1, prepend=c1[0])
    dc[0] = 0.0

    def wer_series1(nn, lam_num, lam_den):
        from numpy.lib.stride_tricks import sliding_window_view
        if NF < nn:
            return np.full(NF, np.nan)
        win = sliding_window_view(dc, nn)
        wn = lam_num ** np.arange(nn - 1, -1, -1, dtype=float)
        wd = lam_den ** np.arange(nn - 1, -1, -1, dtype=float)
        num = np.abs(win @ wn)
        den = np.abs(win) @ wd
        vals = np.where(den > 0, num / np.where(den > 0, den, 1.0), 0.0)
        out = np.full(NF, np.nan)
        out[nn:] = vals[1:]
        return out

    WERAS1 = wer_series1(12, 0.98, 0.96)
    EFF1, DOSE1, RAMP1 = eff_parts(WERAS1)

    defs.set_ce_ctx(NF, np.asarray(ind1["atr"], dtype=float), c1,
                    np.asarray(ind1["high"], dtype=float),
                    np.asarray(ind1["low"], dtype=float))
    CE375_1 = defs.run_ce(3.75)

    defs.set_cef_ctx(o1, c1)
    DAYCTX1 = {}
    for _d in frame_days:
        _gi = np.where(dates_f == _d)[0]
        DAYCTX1[_d] = (_gi, {int(g): int(j) for j, g in enumerate(_gi)})
    defs.set_f3_ctx(DAYCTX1, [NEWDAY], dates_f)
    assemble_all1 = defs.assemble_all
    classify_fills1 = defs.classify_fills

    forkW.ER_ARRAYS = {12: WERAS1}
    forkW.ASYM_SHIFT = 1.35
    forkW.START_DATE, forkW.END_DATE = frame_days[0], NEWDAY
    forkW.SL_K = 0.7875
    forkW.CE_STATE = CE375_1[0]
    forkW.CE_CST = None
    tr = forkW.simulate(dff, ind1, 1, trend_entry=True, use_uturn=False,
                        use_bo=False, er_cfg=CFG)
    modes = copy.deepcopy(forkW.V67_STATS["entry_modes"])
    segs = copy.deepcopy(forkW.ER_INFO.get("segments", []))
    trig = copy.deepcopy(forkW.ER_INFO.get("ramp_trig", []))
    bad = sum(1 for i, dstr, dd_, rd_, wer_i, _f in trig
              if abs(DOSE1[i] - dd_) > 1e-6 or abs(RAMP1[i] - rd_) > 1e-6)
    assert bad == 0, f"{NEWDAY} ramp_trig mismatches {bad}/{len(trig)}"
    cross = [s0 for s0, s1, _ in [(s[0], s[1], s[2]) for s in segs]
             if not (WERAS1[s0] >= EFF1[s0] - 1e-12
                     and (s0 == 0 or WERAS1[s0 - 1] < EFF1[s0]))]
    assert not cross, f"{NEWDAY} seg crossing FAIL {cross[:5]}"
    recs_all = W.to_recs(tr, modes, dates_f)
    recs = [t for t in recs_all if t["ed"] == NEWDAY]

    fills_by_day, cnt = classify_fills1(recs, segs, "stack", "375")
    defs_, flips = assemble_all1(recs, segs, extra_by_day=fills_by_day,
                                 extra_key="fills")
    dd = defs_[NEWDAY]
    assert dd["total"] == round(sum(r["r"] for r in recs), 1), \
        f"{NEWDAY} dd total 对不上"
    assert dd["n"] == len(recs), f"{NEWDAY} dd n 对不上"

    slp = [t for t in recs if t["mode"] == "slope"]
    tot_slp = round(sum(float(t["r"]) for t in slp), 1)
    tot_stack = round(sum(float(t["r"]) for t in recs
                          if t["mode"] != "slope"), 1)
    strades = [t for t in dd["trades"] if t[3] != "slope"]
    n_slope_in_dd = sum(1 for t in dd["trades"] if t[3] == "slope")
    assert n_slope_in_dd == len(slp), \
        f"{NEWDAY} dd slope {n_slope_in_dd} != recs {len(slp)}"
    skeys = {f"{t[0]}:{t[2]}" for t in strades}
    stack_dd = dict(
        trades=strades,
        flips=[k for k in dd.get("flips", []) if k in skeys],
        fills=[k for k in dd.get("fills", []) if k in skeys],
        segs=dd["segs"],
        n=len(strades),
        n_er=sum(1 for t in strades if t[4] == 1),
        total=dd["total"])
    assert abs(stack_dd["total"] - tot_stack) < 1.0, \
        f"{NEWDAY} stack total 对不上"
    sday = dict(date=NEWDAY, runs=[], counts={}, trades=[],
                stack_dd=stack_dd, n_slope=len(slp),
                n_flips=sum(1 for t in slp if t["rsn"] == "FLIP"),
                total_slope=tot_slp, total_stack=tot_stack,
                total_all=round(tot_slp + tot_stack, 1))

    def r2(arr):
        return [None if not np.isfinite(v) else round(float(v), 2)
                for v in arr]

    def r4(arr):
        return [None if not np.isfinite(v) else round(float(v), 4)
                for v in arr]

    def r3(arr):
        return [None if not np.isfinite(v) else round(float(v), 3)
                for v in arr]

    slc = slice(g0, g1 + 1)
    slope = np.asarray(ind1["slope"], dtype=float)
    day = dict(
        times=[str(x) for x in hm],
        close=r2(c1[slc]), up=r2(np.asarray(ind1["upper"], dtype=float)[slc]),
        lo=r2(np.asarray(ind1["lower"], dtype=float)[slc]),
        chan=r2(np.asarray(ind1["chan_mid"], dtype=float)[slc]),
        mid=r2(np.asarray(ind1["mid"], dtype=float)[slc]), open=r2(o1[slc]),
        werchamp=r4(WERAS1[slc]), effstack=r3(EFF1[slc]),
        slope=r4(np.where(np.isfinite(slope), slope, np.nan)[slc]))
    return dict(date=NEWDAY, sday=sday, day=day, n_bars=N, half=half,
                gap=gap, warm_days=warm_days)
