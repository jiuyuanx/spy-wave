#!/usr/bin/env python3
"""F1+WER Envelope 逐日载荷配方 (2026-11-20 vendored, user 2026-11-20 令:
日页 1m Envelope 节由 WER 终栈⑪ 冠军版换成 F1+WER)。

引擎 = console envelope1m 同源 (envwer/env1m_engine.py, 自
~/workspace/console_ec2/env1m_engine.py 逐字 vendored, md5 2df7e2aa):
WER 1m Envelope 冠军本体 + F1 止损反手 (STOPFLIP p=0.25 / mid_gate,
反手单止损 SF_SL_K=2.5xATR, 本体 SL_K=0.7875) + ER 腿 (CFG_A 定版:
WER12 非对称 lam 0.98/0.96 + CE RF/F2/F3/shield, M=3.75), 无箭头。
指标/CE/WER/simulate 全部走 env1m_engine 自带函数 (run_sim), 与
console publisher 对 envelope1m 的调用同口径。

组帧沿用 gen 现行暖帧 (前 10 日历日连续帧, 与 envwer_recipe
day_payload_warm 同一入参帧); 产出 payload 与冠军版同构
{date, sday, day, n_bars, half, gap, warm_days}, 装配/校验/展示
schema 与 envwer_recipe 暖版逐项同形 (envwer_defs.assemble_all /
classify_fills + envwer_page.build_consts 沿用), 以便 gen 只换
引擎调用、页面其余部分零改动。

与冠军版配方的差别只有引擎本体与一处呈现适配:
  - simulate 换 env1m_engine.run_sim (含 console 帧尾补丁: 盘中
    未完结日的末根不当真日终, 末段开仓不被假 EOD 压掉);
  - 盘中帧尾仍有未平仓位时, 展示层补一笔合成日终平 (信号根=
    开仓根, 出场=帧尾末根收盘, rsn=EOD) —— 与冠军版引擎在未完
    结帧上自然产生的帧尾平仓、以及 gen 其余各节的帧尾合成日终
    平同口径; 完整日帧尾恒无持仓, 此笔不出现。
  - 开/平仓根为当日最后一根的单 (盘中真实信号单可平在最新根),
    图口径价 (次根开盘) 尚不存在: 缺价侧回退信号根收盘 (引擎
    自身结算口径), 见 _day_payload_warm_f1 内注释 (2026-11-20
    线上首跑实锤: ER_END 单平帧尾根致整节 float(None) 失败)。
"""
import copy

import numpy as np

import env1m_engine as f1
import envwer_defs as defs
import envwer_recipe as _wr
import wer_deep_common as W


def day_payload_warm_f1(dff, NEWDAY):
    """暖机版 F1+WER day_payload (入参/返回与 envwer_recipe.
    day_payload_warm 同形)。失败返回 (None, 错误信息)。"""
    try:
        return _day_payload_warm_f1(dff, NEWDAY), None
    except Exception as e:  # noqa: BLE001 - gen 侧据此标注该节失败
        import traceback
        traceback.print_exc()
        return None, f"{type(e).__name__}: {e}"


def _day_payload_warm_f1(dff, NEWDAY):
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

    # ---- console 同口径引擎: 指标/CE/WER/simulate 全在整帧上算 ----
    tr, modes, _events, final, ind1 = f1.run_sim(dff, frame_days[0], NEWDAY)
    tr = list(tr)
    c1 = np.asarray(ind1["close"], dtype=float)
    o1 = dff["open"].to_numpy(dtype=float)

    # 盘中帧尾未平仓位 -> 展示用合成日终平 (完整日不触发)
    if final.get("pos"):
        _eb = int(final["entry_bar"])
        if dates_f[_eb] == NEWDAY:
            tr.append((_eb, g1, "long" if final["pos"] > 0 else "short",
                       float(final["entry_px"]), float(c1[g1]), "EOD"))

    segs = copy.deepcopy(f1.ER_INFO.get("segments", []))
    trig = copy.deepcopy(f1.ER_INFO.get("ramp_trig", []))

    WERAS1 = f1.wer_series(12, c1, 0.98, 0.96)
    EFF1, DOSE1, RAMP1 = _wr.eff_parts(WERAS1)
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
    defs_, flips = defs.assemble_all(recs, segs, extra_by_day=fills_by_day,
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
    # 盘中帧尾根单的图口径价回退 (2026-11-20 修复): assemble_cef 的
    # 图口径开/平价取次根开盘 (与 settle_official 次根开盘结算同
    # 口径), 开/平仓根为当日最后一根时次根尚不存在 -> eo/xo=None
    # (defs 层 chart-miss 既有设计), 页面层 float(px) 会炸整节。
    # env1m 引擎的真实信号单可平在帧尾最新根 (如 ER_END), 冠军
    # fork 帧尾不落单故旧节从未踩到。此处回退: assemble_cef 是
    # 任一侧缺失即两侧同置 None, 故先按元组内 di/dx 恢复仍有次
    # 根的一侧 (真图口径次根开盘, 与次根出现后的重刷结果一致),
    # 真缺的一侧用信号根收盘 (引擎自身结算口径), 单子照常展示
    # 与计入 (r/总额本就不依赖图口径价)。合成日终平 (xb=帧尾
    # 末根) 同理由此覆盖。
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
        slope=r4(np.where(np.isfinite(slope), slope, np.nan)[slc]),
        dev=r4(_wr._dev_series(slope[slc])))
    return dict(date=NEWDAY, sday=sday, day=day, n_bars=N, half=half,
                gap=gap, warm_days=warm_days)
