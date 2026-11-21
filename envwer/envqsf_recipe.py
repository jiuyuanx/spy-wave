#!/usr/bin/env python3
"""F1+WER+QQE转向 Envelope 逐日载荷配方 (2026-11-20 主线化, user
2026-11-20 18:17 令: 此前只存在于临时 worktree 变体 + 预计算 JSON
(page_qqesfflip.json), 每轮 workflow 用 gen 主线重生成会把变体冲掉;
现把计算逻辑正式并入 gen, 对任意 WAVE_DATE 用当天暖帧原生算出)。

引擎 = envwer/envqsf_engine.py (研究 fork
tv_v67_stopflip_fork_qqesfflip 逐字 vendored): F1 底盘 (WER 本体 +
止损反手 p=0.25/mid_gate、反手止损 2.5×ATR、本体 SL_K=0.7875) +
QQESF_ON —— QQE 衰减翻仓转向: 持 sf 反手仓时 QQE 衰减信号登记、
次根确认 (异号直翻 / 同号更短才翻), 确认根砍 sf 仓 (rsn
QQE_SF_FLIP) 反向开 qf 仓接棒。QQE 由引擎 simulate 内现场自算
(qqe_mod, 自 gen 根目录 import), 无外部数据依赖。

装配口径与研究侧 compute_qqesfflip_page.py / 原型验证逐项同:
  指标 = envwer_recipe._set_params_sl() (BASE_PARAMS, rs.set_params
  的 1m_v2 参数) + SL_OVER{ER_LEN 15, ASYM_SHIFT 1.35, MULT 1.225}
  —— 绝不能用 tv_v67_sl_sweep 模块默认值 (Pine baseline, 晨盘即
  分叉); CE = envwer_defs.set_ce_ctx + run_ce(3.75)[0]; WER = 12 窗
  非对称 0.98/0.96 (与 envwer_recipe.wer_series1 同式, 整帧版);
  fork 全局 = PARAM_MAP 套 BASE_PARAMS + ER_TH 0.35 / ADX_TH 30 /
  TAKEOVER_SLOPE 0.10 / SL_MODE "atr" / ER_ARRAYS{12:WER} /
  ASYM_SHIFT 1.35 / CE_STATE / CE_CST None / SL_K 0.7875 /
  SF_SL_K 2.5 / STOPFLIP{"p":0.25,"mid_gate":True} / QQESF_ON True /
  QFOL_ON True + QFOL_MAXWIN 5 / QQESF_HB_ON True (四件套定版,
  user 2026-11-20 23:04-23:06 令: div16 + 跟随腿 W5 门 + 衰减断段
  本根豁免 + 转向腿回补, 引擎与研究 fork_qsf_final 同步);
  simulate(dff, ind, 1, trend_entry=True, use_uturn=False,
  use_bo=False, er_cfg=envwer_recipe.CFG) (CFG 与研究 CFG_A 逐项同)。
组帧/装配/展示 schema 与 envf1_recipe 逐项同形 (defs.set_cef_ctx /
set_f3_ctx → classify_fills(recs, segs, "f1", "375") → assemble_all;
图口径价缺失回退同 envf1)。产出比 envf1 多 qf_arrows:
[确认根当日局部下标, flip_to 方向, 被砍 sf 单 r, 接棒 qf 单 r]
(经 recs 按 xb/eb 查), 供 envwer_page 画 qfFlip 箭头与 QQE 面板圈。

验证锚 (2026-11-20): gen 9 日暖帧与研究整帧切片帧上均逐项复现
page_qqesfflip.json 当日 89 笔 / +158.3 / trades 全等 / 箭头 8 全等。

注: fork 无帧尾补丁 —— 完整日无碍; 盘中未完日它按帧内末根当
EOD (与冠军 fork 同类行为), 展示层不另补合成日终平。
"""
import copy

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

import envqsf_engine as forkQ
import envwer_defs as defs
import envwer_recipe as _wr
import tv_v67_sl_sweep as sl
import wer_deep_common as W


def day_payload_warm_qsf(dff, NEWDAY):
    """暖机版 F1+WER+QQE转向 day_payload (入参/返回与 envf1_recipe.
    day_payload_warm_f1 同形, 多 qf_arrows 键)。失败返回
    (None, 错误信息)。"""
    try:
        return _day_payload_warm_qsf(dff, NEWDAY), None
    except Exception as e:  # noqa: BLE001 - gen 侧据此标注该节失败
        import traceback
        traceback.print_exc()
        return None, f"{type(e).__name__}: {e}"


def _day_payload_warm_qsf(dff, NEWDAY):
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

    # ---- fork 全局装配 + simulate (整帧演化, 逐笔只取当日) ----
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
    forkQ.SF_SL_K = 2.5
    forkQ.STOPFLIP = {"p": 0.25, "mid_gate": True}
    forkQ.QQESF_ON = True
    forkQ.QFOL_ON = True
    forkQ.QFOL_MAXWIN = 5
    forkQ.QQESF_HB_ON = True
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

    defs.set_cef_ctx(o1, c1)
    DAYCTX1 = {}
    for _d in frame_days:
        _gi = np.where(dates_f == _d)[0]
        DAYCTX1[_d] = (_gi, {int(g): int(j) for j, g in enumerate(_gi)})
    defs.set_f3_ctx(DAYCTX1, [NEWDAY], dates_f)
    fills_by_day, cnt = defs.classify_fills(recs, segs, "f1", "375")
    defs_, _flips = defs.assemble_all(recs, segs, extra_by_day=fills_by_day,
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
    # 图口径价缺失回退: 同 envf1 (开/平仓根为当日最后一根时次根
    # 开盘尚不存在, assemble_cef 两侧同置 None; 先按 di/dx 恢复
    # 仍有次根的一侧, 真缺的一侧用信号根收盘)。
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

    # ---- qf 箭头: [确认根当日局部下标, flip_to, sf 单 r, qf 单 r] ----
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

    # ---- 跟随/回补箭头字段 (四件套): [入场根当日局部下标, 方向,
    # 该单 r]; 跟随取 recs 中 mode=qfol 的当日单, 回补取 hb_list
    # 当日执行根 (qhb 单经 by_eb 查 r)。页面字段已备, 冠军侧不消费。
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
    return dict(date=NEWDAY, sday=sday, day=day, n_bars=N, half=half,
                gap=gap, warm_days=warm_days, qf_arrows=qf_arrows,
                fol_arrows=fol_arrows, hb_arrows=hb_arrows)
