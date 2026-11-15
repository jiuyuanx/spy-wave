#!/usr/bin/env python3
"""整合版 Envelope 逐日载荷配方 (2026-11-15 vendored)。

与 research/er_leg_backtest_20261110/brk1112/compute_backfill_all.py 的
day_payload 逐行同源 (配方 = compute_4weeks_slope_tail.py 第 2 趟整合
er_off=True): 真整合引擎 tv_v67_er_overlay_wcent2f3sr_slope 现行默认
(ER_CFG=None 整层关、SLOPE_ON、TREND_MODE=noentry、段首抢占开、
imbalance bonus 定版), 单日冷启动, 只产 build_daypage_env 需要的
sday/day 两件。数据源由调用方 (gen_wave_today) 给当日 1m df
(列 t/open/high/low/close, t 为美东墙钟 naive datetime64)。

与原配方的唯一差别: 原配方从研究缓存 df_all 切当日, 这里 df1 由入参
传入; exec 切片函数改为 envint_defs 的模块函数 (函数体逐字, 见该文件)。
"""
import copy
from collections import defaultdict

import numpy as np

import envint_defs as defs
import tv_v67_er_overlay_wcent2f3sr_slope as forkR
import tv_v67_sl_sweep as sl
import wer_deep_common as W
from settle_official import gamma_roi  # noqa: F401  (与原配方同名导入)

RAMP_OVER = dict(brk=5, ramp_on=True, ramp_mode="max", ramp_K=6,
                 ramp_S0=0.2875, ramp_CAP=0.1625, ramp_BETA=0.25)
SL_OVER = {"ER_LEN": 15, "ASYM_SHIFT": 1.35, "MULT": 1.225}
SL_GLOBALS = ["ASYM_SHIFT", "GAUSS_LEN", "GAUSS_BW", "ATR_LEN", "SLOPE_LEN",
              "ER_LEN", "DI_LEN", "ADX_SMOOTH", "MULT", "MOM_LOOKBACK",
              "FAST_EMA_LEN", "MOM_SENS", "MAX_SHIFT_ATR", "EDGE_PCT",
              "MAX_OUTSIDE", "TAKEOVER_SLOPE", "ER_TH", "ADX_TH"]
_CHAMP0 = dict(n=12, th=0.60, mode="A", branch="surge", k=5,
               **{"from": 0.40}, end=0.35, to=0.575)
CHAMP_CFG = dict(_CHAMP0, brk=0, exit_caliber="new", to=0.675, end=0.30,
                 exit_bars=2, **{"from": 0.50},
                 dose_R=0.50, dose_CAP=0.025, dose_W=10)
CHAMP_CFG = dict(CHAMP_CFG, **RAMP_OVER, ce_method="RF", ce_M=3.75, ce_cf=1,
                 ce_f2=True, ce_f3=True, ce_f3_shield=True)
assert CHAMP_CFG == forkR.ER_CFG_STACK11, "CHAMP_CFG 与引擎 ER_CFG_STACK11 不一致"

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
    setattr(forkR, attr, BASE_PARAMS[k])
forkR.ER_TH = 0.35
forkR.ADX_TH = 30
forkR.TAKEOVER_SLOPE = 0.10
forkR.SL_MODE = "atr"
forkR.CE_CST = None
forkR.SLOPE_ON = True

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
    返回 (payload, None) 或 (None, err)。payload 结构与 backfill_<year>.json
    的逐日记录一致: {date, sday, day, n_bars, half, gap}。"""
    df1 = df1.reset_index(drop=True)
    N = len(df1)
    if N == 0:
        return None, "no_data"
    hm = df1["t"].dt.strftime("%H:%M").values
    if hm[0] != "09:30":
        return None, f"first_bar {hm[0]}"
    # 无根数门槛 (2026-11-15 user 令): 开盘第一根起即出整合版;
    # 短日靠 wer_series1 / run_ce 的未播种守卫兜底, 不回退旧口径。
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
    EFF1, _DOSE1, _RAMP1 = eff_parts(WERAS1)

    def ce_full1(M):
        defs.set_ce_ctx(N, np.asarray(ind1["atr"], dtype=float), c1,
                        np.asarray(ind1["high"], dtype=float),
                        np.asarray(ind1["low"], dtype=float))
        return defs.run_ce(M)

    CE375_1 = ce_full1(3.75)

    defs.set_cef_ctx(o1, c1)
    DAYCTX1 = {NEWDAY: (np.arange(N), {int(g): int(g) for g in range(N)})}
    defs.set_f3_ctx(DAYCTX1, [NEWDAY], dates1)
    assemble_all1 = defs.assemble_all
    classify_fills1 = defs.classify_fills

    forkR.ER_ARRAYS = {12: WERAS1}
    forkR.ASYM_SHIFT = 1.35
    forkR.START_DATE, forkR.END_DATE = NEWDAY, NEWDAY
    forkR.SL_K = 0.7875
    forkR.CE_STATE = CE375_1[0]
    forkR.CE_CST = None
    tr = forkR.simulate(df1, ind1, 1, trend_entry=True, use_uturn=False,
                        use_bo=False, er_cfg=None, trend_mode=None)
    modes = copy.deepcopy(forkR.V67_STATS["entry_modes"])
    segs = copy.deepcopy(forkR.ER_INFO.get("segments", []))
    recs = W.to_recs(tr, modes, dates1)
    sinfo = copy.deepcopy(forkR.SLOPE_INFO)
    assert not segs, f"{NEWDAY} er_off 但 segs 非空 {len(segs)}"

    fills_by_day, cnt = classify_fills1(recs, segs, "stack", "375")
    defs_, flips = assemble_all1(recs, segs, extra_by_day=fills_by_day,
                                 extra_key="fills")
    dd = defs_[NEWDAY]
    assert dd["total"] == round(sum(r["r"] for r in recs), 1), f"{NEWDAY} dd total 对不上"
    assert dd["n"] == len(recs), f"{NEWDAY} dd n 对不上"

    runs = [[int(x), int(y)] for x, y in sinfo.get("runs", [])]
    counts = dict(sinfo.get("counts", {}))
    slp = [t for t in recs if t["mode"] == "slope"]
    tot_slp = round(sum(float(t["r"]) for t in slp), 1)
    tot_stack = round(sum(float(t["r"]) for t in recs if t["mode"] != "slope"), 1)
    trades = [dict(eb=int(t["eb"]), xb=int(t["xb"]), dir=int(t["dir"]),
                   epx=float(t["epx"]), xpx=float(t["xpx"]),
                   r=round(float(t["r"]), 2), rsn=t["rsn"]) for t in slp]
    strades = [t for t in dd["trades"] if t[3] != "slope"]
    n_slope_in_dd = sum(1 for t in dd["trades"] if t[3] == "slope")
    assert n_slope_in_dd == len(slp), f"{NEWDAY} dd slope {n_slope_in_dd} != recs {len(slp)}"
    skeys = {f"{t[0]}:{t[2]}" for t in strades}
    stack_dd = dict(
        trades=strades,
        flips=[k for k in dd.get("flips", []) if k in skeys],
        fills=[k for k in dd.get("fills", []) if k in skeys],
        segs=dd["segs"],
        n=len(strades),
        n_er=sum(1 for t in strades if t[4] == 1),
        total=round(sum(float(t[5]) for t in strades), 1))
    assert abs(stack_dd["total"] - tot_stack) < 1.0, f"{NEWDAY} stack total 对不上"
    sday = dict(date=NEWDAY, runs=runs, counts=counts, trades=trades,
                stack_dd=stack_dd, n_slope=len(slp),
                n_flips=sum(1 for t in slp if t["rsn"] == "FLIP"),
                total_slope=tot_slp, total_stack=tot_stack,
                total_all=round(tot_slp + tot_stack, 1))

    def r2(arr):
        return [None if not np.isfinite(v) else round(float(v), 2) for v in arr]

    def r4(arr):
        return [None if not np.isfinite(v) else round(float(v), 4) for v in arr]

    def r3(arr):
        return [None if not np.isfinite(v) else round(float(v), 3) for v in arr]

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
                gap=gap), None
