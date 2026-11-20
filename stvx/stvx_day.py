# -*- coding: utf-8 -*-
"""ST VX 日页管线 (2026-11-17 user 令新增日页「ST VX版」)。

口径 = 当前默认 ST VX, 与 summary 页 ST VX 行、两周图 VX 轨同一口径:
研究基线 esFSW3GF (年龄滑梯 + slope5 压带原形 K0.5/CAP0.4 + 止损只在翻仓
信号根查、窗口 3 根 + GF 大跳空规则 |今开-昨收|>=10 点 -> 10:00 ET 前不开仓)
+ 双侧上午门控 (>=55% / <=-30% -> 13:30-15:00 只平不开、15:00 状态恢复)。
止损窗口 2026-11-20 白天曾改动态窗 (STOP_WIN_DYN=1, dynwin 定版)、当晚
user 令改回固定 3 根: 现行 STOP_WIN_DYN=0, dyn 代码保留在内核、仅关开关。
压带 2026-11-20 晚改分时 K (user 令): DG_S5_K_PM=0.15 + DG_S5_SPLIT_MIN=630
—— bar 开盘 <10:30 用 K=0.5 (早盘认错保持现行), >=10:30 用 K=0.15;
CAP 0.4 等其余压带参数不动 (内核 pf[44]/pf[45], 与研究分时 K 副本同)。

实现纪律 (与 research/st_gap_digest_20261109 逐字同源, 不拼凑、不近似):
- 内核 stvx_kernel.py = research st_kernel_gf_amgate.py 逐字 vendored
  (numba 本体, AMGATE 机制写在内核主循环内)。
- prep_frame = gapflat_batch.prep_week 的逐字移植, 仅两处替换:
  (1) load_week_frame 换成入参帧 (gen 侧 1m 聚合 3m, 暖机 = 目标周周一
      前 45 天起, 与研究 load_week_frame 同窗);
  (2) 数组内核用 KAMG.st_arrays_kernel (与 st_kernel_gf 版仅注释差,
      2026-11-17 diff 实证)。
- trade_actions = vxamgate_fastbatch.trade_actions 逐字移植;
  env = vxamgate_dual_fastbatch.ENV_DUAL 逐字 (win_env(3) + GF + dual),
  仅 DG_OP_SK_LISTFILE 改指本目录 vendored 的 combo_hit_F.json。
- 结算: 信号 bar 记时、下根开盘结算 (内核 px 口径); 单笔 ROI = Gamma 公式
  (settle_official.gamma_roi, 上限 -20 钳制), 罚后 = v2 (put 腿
  clamp(2.3*max(0, d-1.0), 0, 20)); 日收益 = 罚后逐笔连乘。
验证闸 (2026-11-17): 本管线在研究帧上复现 esFSW3GFamg2s 存盘 actions
逐笔全等 (2026-11-09 周 + 2026-11-16 周); 1m 缓存聚合帧上 2026-11-16
单子与两周图页 VX 轨逐笔/罚后连乘全等。
"""
import os
import sys

import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import stvx_kernel as KAMG  # noqa: E402

# vxamgate_dual_fastbatch.ENV_DUAL 逐字 (GB.cell_env(GF_EXTRA) + dual 门控);
# DG_OP_SK_LISTFILE 在 _env() 里改指 vendored 名单。
ENV_DUAL = {
    "CUM_SLOPE_THRESHOLD": "0.05", "DG_AGE_CUT": "17", "DG_AMG": "1",
    "DG_AMG_LO": "-30", "DG_AMG_MODE": "dual", "DG_AMG_PCT": "55",
    "DG_C4D0": "0.65", "DG_C4T": "60", "DG_GF": "1", "DG_GF_G": "10",
    "DG_GF_TMIN": "600", "DG_NC": "1", "DG_OLD": "0", "DG_OP_K": "2",
    "DG_OP_POLICY": "delay",
    "DG_OP_SK_LISTFILE": "/home/hatch/workspace/research/st_gap_digest_20261109/combo_hit_F.json",
    "DG_QF": "half", "DG_QF_RATIO": "0.75", "DG_S5": "1", "DG_S5_CAP": "0.4",
    "DG_S5_K": "0.5", "DG_S5_K_PM": "0.15", "DG_S5_SPLIT_MIN": "630",
    "DG_YD0": "0.5", "REBOUND_RED": "1",
    "STDIGEST": "c4age+qf", "STOP_WIN_BARS": "3", "STOP_WIN_DYN": "0",
    "STOP_WIN_RECOV": "0.50",
    "STRONG_TREND": "1", "ST_FLIP": "0", "ST_ONLY": "1",
}


def _env():
    e = dict(ENV_DUAL)
    e["DG_OP_SK_LISTFILE"] = os.path.join(_HERE, "combo_hit_F.json")
    return e


def _aggregate_1m_to_3m(df1m):
    """1m -> 09:30 对齐 3m (与 gen_wave_today._env3_aggregate_1m_to_3m 同款分桶:
    O=首根开, H=最高, L=最低, C=末根收)。"""
    from datetime import datetime, timedelta
    d = df1m.copy()
    d["date"] = d["t"].dt.date
    mins = d["t"].dt.hour * 60 + d["t"].dt.minute
    d["bin"] = (mins - 570) // 3
    rows = []
    for (dt_, b), grp in d.groupby(["date", "bin"]):
        t0 = datetime(dt_.year, dt_.month, dt_.day, 9, 30) + timedelta(minutes=int(b) * 3)
        rows.append({"t": t0, "open": grp["open"].iloc[0], "high": grp["high"].max(),
                     "low": grp["low"].min(), "close": grp["close"].iloc[-1],
                     "bin": int(b)})
    return pd.DataFrame(rows).sort_values("t").reset_index(drop=True)


def prep_frame(warm, eod_1m, test_days, env0):
    """gapflat_batch.prep_week 逐字移植 (帧与 eod 由入参给, 数组内核换 KAMG)。"""
    pf, pi = KAMG.make_params(env0)
    o = warm["open"].to_numpy(dtype=float)
    h = warm["high"].to_numpy(dtype=float)
    l = warm["low"].to_numpy(dtype=float)
    c = warm["close"].to_numpy(dtype=float)
    dates = warm["t"].dt.date.astype(str).values
    n = len(c)
    uniq, day_ord = np.unique(dates, return_inverse=True)
    day_ord = day_ord.astype(np.int64)
    day_start = np.zeros(n, dtype=bool)
    day_start[0] = True
    day_start[1:] = dates[1:] != dates[:-1]
    bar_in_day = np.zeros(n, dtype=np.int64)
    cnt = -1
    for bi in range(n):
        cnt = 0 if day_start[bi] else cnt + 1
        bar_in_day[bi] = cnt
    hm_min = (warm["t"].dt.hour.values * 60 + warm["t"].dt.minute.values).astype(np.int64)
    is_1500 = (hm_min == 900)
    hl2 = (h + l) / 2
    tr = np.empty(n, dtype=float)
    tr[0] = h[0] - l[0]
    if n > 1:
        tr[1:] = np.maximum(np.maximum(h[1:] - l[1:], np.abs(h[1:] - c[:-1])), np.abs(l[1:] - c[:-1]))
        if pi[2] == 1:
            m = day_start.copy(); m[0] = False
            tr[m] = (h - l)[m]
    atr_init = float(tr[:10].mean()) if n >= 10 else np.nan
    starts_le10 = np.flatnonzero(day_start[:11])
    cur_ds0 = int(starts_le10[-1]) if len(starts_le10) else 0
    atr = np.full(n, np.nan); mult = np.full(n, 3.0)
    bu = np.full(n, np.nan); bl = np.full(n, np.nan)
    fu = np.full(n, np.nan); fl = np.full(n, np.nan)
    trend = np.zeros(n, dtype=np.int64)
    ee_flag_bar = np.zeros(n, dtype=bool); skip_flag_bar = np.zeros(n, dtype=bool)
    dec_p = np.full(n, np.nan); dec_m = np.full(n, np.nan); slp5 = np.full(n, np.nan)
    if pi[35] == 1 or pi[43] == 1:
        _root = os.path.dirname(_HERE)
        if _root not in sys.path:
            sys.path.insert(0, _root)
        from qqe_mod import qqe_mod
        hist_nb = np.asarray(qqe_mod(c)["hist"], dtype=float)
        if pi[36] == 1:
            for sgn, arr in ((1, dec_p), (-1, dec_m)):
                for i2 in range(n):
                    pk = 0.0
                    for j2 in range(i2, max(i2 - 10, -1), -1):
                        if dates[j2] != dates[i2]:
                            break
                        hj = hist_nb[j2]
                        if np.isnan(hj):
                            break
                        if (hj > 3 and sgn > 0) or (hj < -3 and sgn < 0):
                            pk = max(pk, abs(hj))
                        else:
                            break
                    if pk > 0:
                        arr[i2] = 1.0 - abs(hist_nb[i2]) / pk
        ts = warm["t"]
        for d0 in sorted(set(dates)):
            idx = np.where(dates == d0)[0]
            if len(idx) < 6:
                continue
            t0 = ts.iloc[idx[0]].replace(hour=9, minute=30, second=0, microsecond=0)
            k9 = np.array([int((ts.iloc[j] - t0).total_seconds() // 540) for j in idx])
            c9 = {}
            for kk in range(int(k9.max()) + 1):
                mm = idx[k9 == kk]
                if len(mm):
                    c9[kk] = float(c[mm[-1]])
            for ii, j in enumerate(idx):
                prev = int(k9[ii]) - 1
                avail = [k for k in (prev - 4, prev - 3, prev - 2, prev - 1, prev) if k in c9]
                if len(avail) >= 2:
                    ys = np.array([c9[k] for k in avail], dtype=float)
                    xs = np.arange(len(avail), dtype=float)
                    xm, ym = xs.mean(), ys.mean()
                    den = ((xs - xm) ** 2).sum()
                    if den > 0:
                        slp5[j] = float(((xs - xm) * (ys - ym)).sum() / den)
    opskip_day = np.zeros(n, dtype=bool)
    if pi[48] == 1:
        import json as _json
        with open(env0.get("DG_OP_SK_LISTFILE", "")) as _f:
            _sk_dates = set(_json.load(_f))
        opskip_day = np.isin(dates, list(_sk_dates))
    dbg = np.zeros((n, 8), dtype=float)
    s5_npput = np.zeros(n, dtype=float)
    _npd = env0.get("DG_S5_NOPRESS_PUT_DAY", "")
    if _npd:
        s5_npput[dates == _npd] = 1.0
    KAMG.st_arrays_kernel(h, l, c, o, hl2, tr, atr_init, day_start, bar_in_day, is_1500,
                          cur_ds0, pf, pi, atr, mult, bu, bl, fu, fl, trend, ee_flag_bar,
                          skip_flag_bar, dec_p, dec_m, slp5, dbg, s5_npput, hm_min)
    ee_day_bar = np.zeros(n, dtype=bool); skip_day_bar = np.zeros(n, dtype=bool)
    if ee_flag_bar.any():
        ords = np.unique(day_ord[ee_flag_bar])
        ee_day_bar = np.isin(day_ord, ords)
    if skip_flag_bar.any():
        ords = np.unique(day_ord[skip_flag_bar])
        skip_day_bar = np.isin(day_ord, ords)
    eod_px = np.array([eod_1m.get(d, np.nan) for d in dates], dtype=float)
    skip_bar = np.isin(dates, list(KAMG._SKIP_DATES))
    start_idx = int(np.where(dates == test_days[0])[0][0])
    end_idx = int(np.where(dates == test_days[-1])[0][-1]) + 1
    hm_str = np.array([f"{int(m // 60):02d}:{int(m % 60):02d}" for m in hm_min])
    return dict(warm=warm, c=c, o=o, day_ord=day_ord, hm_min=hm_min, eod_px=eod_px,
                skip_bar=skip_bar, trend=trend, atr=atr, bar_in_day=bar_in_day,
                ee_day_bar=ee_day_bar, skip_day_bar=skip_day_bar, opskip_day=opskip_day,
                start_idx=start_idx, end_idx=end_idx, dates=dates, hm_str=hm_str,
                fu=fu, fl=fl, bu=bu, bl=bl)


def trade_actions(P, env):
    """vxamgate_fastbatch.trade_actions 逐字移植。"""
    pf, pi = KAMG.make_params(env)
    maxa = 6 * (P["end_idx"] - P["start_idx"]) + 16
    a_bar = np.zeros(maxa, dtype=np.int64); a_act = np.zeros(maxa, dtype=np.int8)
    a_dir = np.zeros(maxa, dtype=np.int8); a_px = np.zeros(maxa, dtype=float)
    a_rsn = np.zeros(maxa, dtype=np.int8); a_p1 = np.zeros(maxa, dtype=float)
    a_p2 = np.zeros(maxa, dtype=float)
    day_ord = P["day_ord"]; hm_min = P["hm_min"]
    ndays = int(day_ord.max()) + 1
    am_j13 = np.full(ndays, -1, dtype=np.int64)
    for d in range(ndays):
        idx = np.where((day_ord == d) & (hm_min == 780))[0]
        if len(idx):
            am_j13[d] = int(idx[0])
    am_ret = np.full(ndays, np.nan, dtype=float)
    am_trig = np.zeros(ndays, dtype=np.int64)
    diag = np.zeros(8, dtype=float)
    na = KAMG.st_trade_kernel(P["c"], P["o"], P["day_ord"], P["hm_min"], P["eod_px"],
                              P["skip_bar"], P["trend"], P["atr"], P["bar_in_day"],
                              P["ee_day_bar"], P["skip_day_bar"], P["opskip_day"],
                              P["start_idx"], P["end_idx"], pf, pi,
                              a_bar, a_act, a_dir, a_px, a_rsn, a_p1, a_p2,
                              am_j13, am_ret, am_trig, diag)
    recov = pf[32]; ee_atr_v = pf[33]
    act_str = {0: "开", 1: "平", 2: "持"}
    actions = []
    for k in range(na):
        bi = int(a_bar[k]); d = int(a_dir[k])
        tstr = f"{P['dates'][bi][5:]}T{P['hm_str'][bi]}"
        actions.append((bi, tstr, act_str[int(a_act[k])], "call" if d == 1 else "put",
                        float(a_px[k]),
                        KAMG._fmt_reason(int(a_rsn[k]), d, float(a_p1[k]),
                                         float(a_p2[k]), recov, ee_atr_v)))
    dates = P["dates"]
    test_days = sorted(set(dates[P["start_idx"]:P["end_idx"]].tolist()))
    am_info = {}
    for dstr in test_days:
        d = int(np.where(np.unique(dates) == dstr)[0][0])
        am_info[dstr] = (float(am_ret[d]), int(am_trig[d]))
    return actions, am_info, diag


def _gamma_roi(dr, d):
    """settle_official.gamma_roi 逐字 (返回百分比, 下限 -20, 逆向钳制)。"""
    if dr == "call":
        r = 9.88 * d + 0.311 * d * d
        if d < 0:
            r = min(r, 0.0)
    else:
        r = -9.88 * d + 0.311 * d * d
        if d > 0:
            r = min(r, 0.0)
    return max(r, -20.0)


def _pen_roi(dr, d):
    """vp2_eval.pen_roi 逐字 (v2: put 腿 clamp(2.3*max(0, d-1.0), 0, 20))。"""
    r = _gamma_roi(dr, d)
    if dr == "put":
        r -= min(max(2.3 * max(0.0, d - 1.0), 0.0), 20.0)
    return r


def day_payload(df1, day_str, day_complete=True):
    """df1: 1m bars (t=naive ET, open/high/low/close), 覆盖 [目标周周一-45天, day]。
    返回 (payload, err): payload = {actions, trades, pen_pct, bands, bands_main,
    main_fill, main_flips, trend_day, am, day_hms}。
    actions: [(hm, act, dr, px, rs)] 仅目标日 (bi 已换成日间序号, 见 trades)。
    trades: [{ot, ct, dir, opx, cpx, orsn, crsn, roi, roi_pen, closed, obi, cbi}]
            (obi/cbi = 目标日日间 bar 序号; 未平仓 closed=False, ct=None,
             cpx = 当日最后一根收盘, 仅作浮动标记不入连乘)。
    bands: [(hm, fu, fl)] 目标日每根的最终带 (压带后, 与内核同源) = 影子主轨。
    bands_main: [(hm, fu, fl)] 主轨 (本体未压带最终带, 同次内核运行 bu/bl
            按内核棘轮公式推) 的上下两条最终带全程值 (2026-11-17 user 令:
            不再按活动侧拆段, 上下轨都画全成一条完整实线轨道);
    main_fill: [[hm, val, close, hm_next, val_next, close_next, trend]]
            主轨填充四边形数据 (与日页 VH 节 stFill 同格式)。
    main_flips: [(hm, val, trend)] 主轨 buy/sell 标记点 (与 VH 节 stFlips
            同款取点: 首根开盘状态 + 本体趋势翻转根; trend 1=Buy / -1=Sell,
            val = 该根活动带)。注意与 VX 自身单子 (走压带趋势) 是两套标记。
    盘中 (day_complete=False) 时帧尾 rsn=1 强平是帧尾伪平, 还原为持仓中
    (与实盘 stvx 引擎 2026-11-16 修法同义: <15:55 帧尾平不算真平)。"""
    from datetime import datetime as _dt, timedelta as _td
    if df1 is None or len(df1) == 0:
        return None, "no_1m"
    d = df1.copy()
    d["t"] = pd.to_datetime(d["t"])
    _mins = d["t"].dt.hour * 60 + d["t"].dt.minute
    d = d[(_mins >= 570) & (_mins <= 959)].sort_values("t").drop_duplicates("t", keep="last")
    if len(d) == 0:
        return None, "no_1m_rth"
    agg = _aggregate_1m_to_3m(d[["t", "open", "high", "low", "close"]])
    warm = agg[["t", "open", "high", "low", "close"]].copy()
    warm["t"] = pd.to_datetime(warm["t"])
    eod_1m = {}
    _tmp = d[(d["t"].dt.hour * 60 + d["t"].dt.minute) == 955]
    for _, r in _tmp.iterrows():
        eod_1m[r["t"].strftime("%Y-%m-%d")] = float(r["close"])
    _day = _dt.strptime(day_str, "%Y-%m-%d").date()
    _monday = _day - _td(days=_day.weekday())
    _all_days = sorted(set(warm["t"].dt.strftime("%Y-%m-%d")))
    test_days = [x for x in _all_days if _monday.strftime("%Y-%m-%d") <= x <= day_str]
    if day_str not in test_days:
        return None, "target_day_missing"
    env = _env()
    P = prep_frame(warm, eod_1m, test_days, env)
    actions, am_info, _diag = trade_actions(P, env)
    dates = P["dates"]
    idx_day = np.where(dates == day_str)[0]
    i0 = int(idx_day[0])
    day_hms = [str(P["hm_str"][j]) for j in idx_day]
    day_acts = []
    for (bi, ts, act, dr, px, rs) in actions:
        if str(dates[bi]) == day_str and act != "持":
            day_acts.append([str(P["hm_str"][bi]), act, dr, float(px), rs, int(bi - i0)])
    if not day_complete and day_acts:
        _lhm, _lact, _ldr, _lpx, _lrs, _lbi = day_acts[-1]
        if _lact == "平" and _lrs.startswith("日终平仓(最后一根)"):
            day_acts.pop()
    trades = []
    pos = None
    for (hm, act, dr, px, rs, xi) in day_acts:
        if act == "开":
            pos = (dr, px, hm, rs, xi)
        elif act == "平" and pos is not None:
            odr, opx, ots, ors, oxi = pos
            pos = None
            dd = px - opx
            trades.append(dict(ot=ots, ct=hm, dir=odr, opx=opx, cpx=px,
                               orsn=ors, crsn=rs, roi=_gamma_roi(odr, dd),
                               roi_pen=_pen_roi(odr, dd), closed=True,
                               obi=oxi, cbi=xi))
    if pos is not None:
        odr, opx, ots, ors, oxi = pos
        cpx = float(P["c"][idx_day[-1]])
        dd = cpx - opx
        trades.append(dict(ot=ots, ct=None, dir=odr, opx=opx, cpx=cpx,
                           orsn=ors, crsn=None, roi=_gamma_roi(odr, dd),
                           roi_pen=_pen_roi(odr, dd), closed=False,
                           obi=oxi, cbi=int(idx_day[-1] - i0)))
    pen = 1.0
    for t in trades:
        if t["closed"]:
            pen *= 1.0 + t["roi_pen"] / 100.0
    bands = []
    for j in idx_day:
        fu = P["fu"][j]; fl = P["fl"][j]
        bands.append((str(P["hm_str"][j]),
                      None if np.isnan(fu) else round(float(fu), 2),
                      None if np.isnan(fl) else round(float(fl), 2)))
    # 主轨 (本体未压带最终带): 同一次内核运行的 bu/bl (未压原始带) 按内核
    # 自身棘轮公式 (stvx_kernel 内 fu/fl 同式, bu_e=bu/bl_e=bl) 在整帧上推;
    # 本体趋势按同式判定 (收盘 vs 前根最终带), 与日页 VH 节 ST 慢版画法同款。
    _bu = P["bu"]; _bl = P["bl"]; _cf = P["c"]; _nf = len(_cf)
    _mfu = np.full(_nf, np.nan); _mfl = np.full(_nf, np.nan)
    _mtr = np.zeros(_nf, dtype=np.int64)
    if _nf > 9:
        _mfu[9] = _bu[9]; _mfl[9] = _bl[9]; _mtr[9] = 1
        for _i in range(10, _nf):
            if np.isnan(_bu[_i]) or np.isnan(_bl[_i]):
                _mtr[_i] = _mtr[_i - 1]
                continue
            if np.isnan(_mfu[_i - 1]):
                _mfu[_i] = _bu[_i]; _mfl[_i] = _bl[_i]
                _mtr[_i] = _mtr[_i - 1] if _mtr[_i - 1] != 0 else 1
                continue
            _mfu[_i] = _bu[_i] if (_bu[_i] < _mfu[_i - 1] or _cf[_i - 1] > _mfu[_i - 1]) else _mfu[_i - 1]
            _mfl[_i] = _bl[_i] if (_bl[_i] > _mfl[_i - 1] or _cf[_i - 1] < _mfl[_i - 1]) else _mfl[_i - 1]
            if _cf[_i] > _mfu[_i - 1]:
                _mtr[_i] = 1
            elif _cf[_i] < _mfl[_i - 1]:
                _mtr[_i] = -1
            else:
                _mtr[_i] = _mtr[_i - 1]
    _day_rows = []
    for j in idx_day:
        _tr = int(_mtr[j])
        _val = _mfl[j] if _tr == 1 else _mfu[j]
        _day_rows.append((str(P["hm_str"][j]),
                          None if np.isnan(_val) else round(float(_val), 2),
                          _tr, round(float(_cf[j]), 2)))
    bands_main = [(str(P["hm_str"][j]),
                   None if np.isnan(_mfu[j]) else round(float(_mfu[j]), 2),
                   None if np.isnan(_mfl[j]) else round(float(_mfl[j]), 2))
                  for j in idx_day]
    main_fill = []
    for _k, (_hm, _val, _tr, _cc) in enumerate(_day_rows):
        _nk = _k + 1 if _k + 1 < len(_day_rows) else _k
        _nhm, _nval, _ntr, _ncc = _day_rows[_nk]
        main_fill.append([_hm, _val, _cc, _nhm, _nval, _ncc, _tr])
    # 主轨 buy/sell 翻转标记 (与 VH 节 stFlips 同款取点): 当日首根按开盘本体
    # 趋势状态标一个, 其后本体趋势翻转的每根各标一个; val = 该根活动带。
    main_flips = []
    for _k, (_hm, _val, _tr, _cc) in enumerate(_day_rows):
        if _val is None or _tr == 0:
            continue
        if _k == 0 or _tr != _day_rows[_k - 1][2]:
            main_flips.append((_hm, _val, _tr))
    return dict(actions=[(a[0], a[1], a[2], a[3], a[4]) for a in day_acts],
                trades=trades, pen_pct=(pen - 1.0) * 100.0,
                bands=bands, bands_main=bands_main, main_fill=main_fill,
                main_flips=main_flips,
                # VX 交易趋势逐根 (内核 trend 原样, 与压带 fu/fl 同次运行;
                # 日页 ST VX 节分段淡化拆轨用, 2026-11-20 user 令)
                trend_day=[int(P["trend"][j]) for j in idx_day],
                am=am_info.get(day_str), day_hms=day_hms), None
