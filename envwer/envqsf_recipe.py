#!/usr/bin/env python3
"""双版 Envelope 逐日载荷配方 (WER冠军 + F1+WER+QQE转向, 2026-11-22
主线同步现行生产臂, user 2026-11-22 令: 日页双版与突破页现行口径
一致, 明天起原生生成)。

引擎 = envwer/envqsf_engine.py (研究 fork_qsf_flipchase 逐字
vendored): F1 底盘 (WER 本体 + 止损反手 p=0.25/mid_gate、反手止损
2.5×ATR、本体 SL_K=0.7875) + 四件套 (div16 / 跟随 W5 门 / 衰减断段
本根豁免 / 回补次根递增确认) + ER 段内 QQE 转向 (影子状态机
envqsf_state, ER_QQE_ON) + 惯性单 T2.8 (INE_TRIG/ATR60) + ER 衰减门
G4 (ER_DECAY_GATE) + A1 翻仓优先 (FLIP_FIRST)。
两臂同引擎同帧: qsf 臂 = STOPFLIP 开 + QQE 转向腿开 (反手+QQE);
champ 臂 = STOPFLIP 关 + QQE 转向腿关 (WER冠军)。与研究侧
compute_alldates_staging 的 arm() 逐项同式; 影子状态由
envqsf_state.compute_state 在本暖帧上原生算 (移植等价锚: 研究
extended 帧与 qqe_state_3y.json 逐根全等)。

装配口径与原 qsf 配方同: 指标 = envwer_recipe._set_params_sl()
(BASE_PARAMS + SL_OVER{ER_LEN 15, ASYM_SHIFT 1.35, MULT 1.225});
CE = envwer_defs.set_ce_ctx + run_ce(3.75)[0]; WER = 12 窗非对称
0.98/0.96; simulate(dff, ind, 1, trend_entry=True, use_uturn=False,
use_bo=False, er_cfg=envwer_recipe.CFG); dd 装配走 envwer_defs
(classify_fills/assemble_all), 图口径价缺失回退同 envf1。
qf_arrows 供 envwer_page 画 qfFlip 箭头与 QQE 面板圈 (两臂共用
qsf 臂的箭头, gen 主脚本侧处理)。

注: fork 无帧尾补丁 —— 完整日无碍; 盘中未完日按帧内末根当 EOD
(与冠军 fork 同类行为), 展示层不另补合成日终平。
"""
import copy

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

import envqsf_engine as forkQ
import envqsf_state as _qs
import envwer_defs as defs
import envwer_recipe as _wr
import tv_v67_sl_sweep as sl
import wer_deep_common as W

_MEMO = {}


def day_payload_warm_qsf(dff, NEWDAY):
    """暖机版 F1+WER+QQE转向 day_payload (返回 (payload, err))。"""
    try:
        return _both(dff, NEWDAY)[0], None
    except Exception as e:  # noqa: BLE001 - gen 侧据此标注该节失败
        import traceback
        traceback.print_exc()
        return None, f"{type(e).__name__}: {e}"


def day_payload_warm_champ(dff, NEWDAY):
    """暖机版 WER冠军 day_payload (与 qsf 同帧同引擎, 返回
    (payload, err))。"""
    try:
        return _both(dff, NEWDAY)[1], None
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        return None, f"{type(e).__name__}: {e}"


def _both(dff, NEWDAY):
    key = (NEWDAY, len(dff), str(dff["t"].iloc[0]), str(dff["t"].iloc[-1]))
    if key not in _MEMO:
        _MEMO.clear()
        _MEMO[key] = _day_payloads_both(dff, NEWDAY)
    return _MEMO[key]


def _rma(x, n):
    out = np.full(len(x), np.nan)
    a = 1.0 / n
    s = np.nan
    for i, v in enumerate(x):
        s = v if np.isnan(s) else a * v + (1 - a) * s
        out[i] = s
    return out


def _build_trig(close, high, low, upper, lower, chan_mid, atr60, dates_f, T):
    """惯性单触发 (T2.8, 全因果; 与研究 compute_flipfirst_staging
    build_trig 逐字同式)。"""
    N = len(close)
    trig = np.zeros(N, dtype=np.int64)
    ep = None
    for i in range(N):
        if i > 0 and dates_f[i] != dates_f[i - 1]:
            ep = None
        if not np.isfinite(atr60[i]) or atr60[i] <= 0:
            continue
        dfd = (close[i] - lower[i]) / atr60[i]
        dfu = (upper[i] - close[i]) / atr60[i]
        s = 1 if (dfd >= T and close[i] >= chan_mid[i]) else \
            (-1 if (dfu >= T and close[i] < chan_mid[i]) else 0)
        if ep is None:
            if s != 0:
                ep = dict(sd=s, ext=close[i], start=i, last_ext=i, last_on=i)
            continue
        sd = ep["sd"]
        if s == sd:
            ep["last_on"] = i
        if (sd > 0 and close[i] > ep["ext"]) or \
                (sd < 0 and close[i] < ep["ext"]):
            ep["ext"] = close[i]
            ep["last_ext"] = i
        retr = (ep["ext"] - close[i]) if sd > 0 else (close[i] - ep["ext"])
        if retr >= 0.35 * atr60[i] and i > ep["start"]:
            trig[i] = -sd
            ep = None
            continue
        if i - max(ep["last_on"], ep["last_ext"]) > 3 or i - ep["start"] > 15:
            ep = None
    return trig


def _decay_gate4(hist, dates_f):
    """ER 衰减门 G4 (与研究 decay_gate4 逐字同式)。"""
    n = len(hist)
    gate = np.zeros(n, dtype=int)
    for sgn_ in (1, -1):
        peak = 0.0
        for i in range(n):
            if i > 0 and dates_f[i] != dates_f[i - 1]:
                peak = 0.0
            hv = float(hist[i])
            inw = (hv * sgn_ > 0) and abs(hv) >= 3
            if not inw:
                peak = 0.0
                continue
            a = abs(hv)
            peak = max(peak, a)
            if peak >= 8 and a <= 0.80 * peak:
                gate[i] = sgn_
    return gate


def _day_payloads_both(dff, NEWDAY):
    dff = dff.reset_index(drop=True).copy()
    if getattr(dff["t"].dt, "tz", None) is not None:
        dff["t"] = dff["t"].dt.tz_convert(
            "America/New_York").dt.tz_localize(None)
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

    # ---- 指标: _set_params_sl(BASE_PARAMS) + SL_OVER (整帧) ----
    _wr._set_params_sl()
    saved = {g: getattr(sl, g) for g in _wr.SL_GLOBALS}
    for k, v in _wr.SL_OVER.items():
        setattr(sl, k, v)
    try:
        ind = sl.compute_indicators(dff)
    finally:
        for k, v in saved.items():
            setattr(sl, k, v)
    c1 = np.asarray(ind["close"], dtype=float)
    o1 = dff["open"].to_numpy(dtype=float)

    defs.set_ce_ctx(NF, np.asarray(ind["atr"], dtype=float), c1,
                    np.asarray(ind["high"], dtype=float),
                    np.asarray(ind["low"], dtype=float))
    CE = defs.run_ce(3.75)[0]

    # ---- WER 12 窗非对称 0.98/0.96 (整帧, wer_series1 同式) ----
    dc = np.diff(c1, prepend=c1[0])
    dc[0] = 0.0
    if NF >= 12:
        win = sliding_window_view(dc, 12)
        wn = 0.98 ** np.arange(11, -1, -1, dtype=float)
        wd = 0.96 ** np.arange(11, -1, -1, dtype=float)
        den = np.abs(win) @ wd
        vals = np.where(den > 0, np.abs(win @ wn) / np.where(den > 0, den, 1.0), 0.0)
        WERAS1 = np.full(NF, np.nan)
        WERAS1[12:] = vals[1:]
    else:
        WERAS1 = np.full(NF, np.nan)
    EFF1, DOSE1, RAMP1 = _wr.eff_parts(WERAS1)

    # ---- 影子状态 / 惯性触发 / G4 门 (本帧原生算) ----
    sm_arr = dff["t"].dt.hour.values * 60 + dff["t"].dt.minute.values
    QS = _qs.compute_state(dates_f, sm_arr, ind, forkQ)
    _pc = np.empty(NF)
    _pc[0] = c1[0]
    _pc[1:] = c1[:-1]
    _hi = np.asarray(ind["high"], dtype=float)
    _lo = np.asarray(ind["low"], dtype=float)
    _trr = np.maximum(_hi - _lo,
                      np.maximum(np.abs(_hi - _pc), np.abs(_lo - _pc)))
    ATR60 = _rma(_trr, 60)
    TRIG28 = _build_trig(c1, _hi, _lo,
                         np.asarray(ind["upper"], dtype=float),
                         np.asarray(ind["lower"], dtype=float),
                         np.asarray(ind["chan_mid"], dtype=float),
                         ATR60, dates_f, 2.8)
    from qqe_mod import qqe_mod as _qqe_mod
    q_hist = np.asarray(_qqe_mod(c1)["hist"], dtype=float)
    G4 = _decay_gate4(q_hist, dates_f)

    # ---- fork 全局装配 (两臂共用部分) ----
    for k, attr in _wr.PARAM_MAP.items():
        setattr(forkQ, attr, _wr.BASE_PARAMS[k])
    forkQ.ER_TH, forkQ.ADX_TH, forkQ.TAKEOVER_SLOPE = 0.35, 30, 0.10
    forkQ.SL_MODE = "atr"
    forkQ.ER_ARRAYS = {12: WERAS1}
    forkQ.ASYM_SHIFT = 1.35
    forkQ.START_DATE, forkQ.END_DATE = frame_days[0], NEWDAY
    forkQ.CE_STATE = CE
    forkQ.CE_CST = None
    forkQ.SL_K = 0.7875
    forkQ.QQE_STATE = QS
    forkQ.ER_QQE_ON = True
    forkQ.INE_TRIG = TRIG28
    forkQ.INE_ATR60 = ATR60
    forkQ.INE_TAKEOVER = False
    forkQ.INE_SL_K = 1.5
    forkQ.ER_DECAY_GATE = G4
    forkQ.FLIP_FIRST = True
    forkQ.SL_FLIP = False
    forkQ.CHASE_ON = False
    forkQ.QQESF_TRIG_BASE = 0.80
    forkQ.QQESF_TRIG_DIV = 16.0
    forkQ.QQESF_NOCONFIRM = False
    forkQ.QQESF_DEEP_THETA = None
    forkQ.QQESF_PEAK_MIN = 12.0
    forkQ.QFOL_MAXWIN = 5
    forkQ.QFOL_GRAY_THR = 9

    defs.set_cef_ctx(o1, c1)
    DAYCTX1 = {}
    for _d in frame_days:
        _gi = np.where(dates_f == _d)[0]
        DAYCTX1[_d] = (_gi, {int(g): int(j) for j, g in enumerate(_gi)})
    defs.set_f3_ctx(DAYCTX1, [NEWDAY], dates_f)

    def run_arm(sf_on, qqe_on):
        forkQ.SF_SL_K = 2.5
        forkQ.STOPFLIP = {"p": 0.25, "mid_gate": True} if sf_on else None
        forkQ.QQESF_ON = bool(qqe_on)
        forkQ.QFOL_ON = bool(qqe_on)
        forkQ.QQESF_HB_ON = bool(qqe_on)
        forkQ.INE_INFO = {"blocked_edge": 0, "opens": 0, "mid_exit": 0,
                          "trig_skipped": 0}
        forkQ.ER_DECAY_INFO = {"gated": 0}
        forkQ.A_INFO = {"preempt": 0, "slflip": 0}
        forkQ.CHASE_STATE = {"armed": None}
        forkQ.CHASED_BARS = []
        tr = forkQ.simulate(dff, ind, 1, trend_entry=True, use_uturn=False,
                            use_bo=False, er_cfg=_wr.CFG)
        modes = copy.deepcopy(forkQ.V67_STATS["entry_modes"])
        segs = copy.deepcopy(forkQ.ER_INFO.get("segments", []))
        trig = copy.deepcopy(forkQ.ER_INFO.get("ramp_trig", []))
        flips_q = copy.deepcopy(forkQ.QQESF_INFO.get("flips", []))
        hb_list = copy.deepcopy(forkQ.QQESF_INFO.get("hb_list", []))
        bad = sum(1 for i, dstr, dd_, rd_, wer_i, _f in trig
                  if abs(DOSE1[i] - dd_) > 1e-6 or abs(RAMP1[i] - rd_) > 1e-6)
        assert bad == 0, f"{NEWDAY} ramp_trig mismatches {bad}/{len(trig)}"
        cross = [s0 for s0, s1, _ in [(s[0], s[1], s[2]) for s in segs]
                 if not (WERAS1[s0] >= EFF1[s0] - 1e-12
                         and (s0 == 0 or WERAS1[s0 - 1] < EFF1[s0]))]
        assert not cross, f"{NEWDAY} seg crossing FAIL {cross[:5]}"
        recs_all = W.to_recs(tr, modes, dates_f)
        recs = [t for t in recs_all if t["ed"] == NEWDAY]
        fills_by_day, cnt = defs.classify_fills(recs, segs, "f1", "375")
        defs_, _flips = defs.assemble_all(recs, segs,
                                          extra_by_day=fills_by_day,
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
        for t in strades:
            if t[9] is None:
                t[9] = round(float(o1[g0 + t[7]]), 3) if t[7] >= 0 \
                    else round(float(c1[g0 + t[0]]), 3)
            if t[10] is None:
                t[10] = round(float(o1[g0 + t[8]]), 3) if t[8] >= 0 \
                    else round(float(c1[g0 + t[1]]), 3)
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
        by_eb = {int(t["eb"]): t for t in recs}
        by_xb = {int(t["xb"]): t for t in recs}
        qf_arrows = []
        for f in flips_q:
            if f["date"] != NEWDAY:
                continue
            cb = int(f["conf_bar"])
            sf_t = by_xb.get(cb)
            qf_t = by_eb.get(cb)
            qf_arrows.append([cb - g0, int(f["flip_to"]),
                              round(float(sf_t["r"]), 2) if sf_t else None,
                              round(float(qf_t["r"]), 2) if qf_t else None])
        qf_arrows.sort()
        fol_arrows = [[int(t["eb"]) - g0, int(t["dir"]),
                       round(float(t["r"]), 2)]
                      for t in recs if t["mode"] == "qfol"]
        fol_arrows.sort()
        hb_arrows = []
        for h in hb_list:
            if h["date"] != NEWDAY:
                continue
            eb = int(h["exec_bar"])
            qhb_t = by_eb.get(eb)
            hb_arrows.append([eb - g0, int(h["hb_to"]),
                              round(float(qhb_t["r"]), 2) if qhb_t else None])
        hb_arrows.sort()
        return sday, qf_arrows, fol_arrows, hb_arrows

    sday_q, qf_q, fol_q, hb_q = run_arm(True, True)
    sday_c, qf_c, fol_c, hb_c = run_arm(False, False)

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
    slope = np.asarray(ind["slope"], dtype=float)
    day = dict(
        times=[str(x) for x in hm],
        close=r2(c1[slc]), up=r2(np.asarray(ind["upper"], dtype=float)[slc]),
        lo=r2(np.asarray(ind["lower"], dtype=float)[slc]),
        chan=r2(np.asarray(ind["chan_mid"], dtype=float)[slc]),
        mid=r2(np.asarray(ind["mid"], dtype=float)[slc]), open=r2(o1[slc]),
        werchamp=r4(WERAS1[slc]), effstack=r3(EFF1[slc]),
        slope=r4(np.where(np.isfinite(slope), slope, np.nan)[slc]),
        dev=r4(_wr._dev_series(slope[slc])))
    pay_q = dict(date=NEWDAY, sday=sday_q, day=day, n_bars=N, half=half,
                 gap=gap, warm_days=warm_days, qf_arrows=qf_q,
                 fol_arrows=fol_q, hb_arrows=hb_q)
    pay_c = dict(date=NEWDAY, sday=sday_c, day=day, n_bars=N, half=half,
                 gap=gap, warm_days=warm_days, qf_arrows=qf_c,
                 fol_arrows=fol_c, hb_arrows=hb_c)
    return pay_q, pay_c
