#!/usr/bin/env python3
"""vendored 切片函数 (2026-11-15): 与 brk1112 管线 compute_backfill_all.py 的
exec 切片逐字同源, 函数体一字未改:
  run_ce        <- compute_ce_dye.py  (def run_ce .. def agree_stats 前)
  assemble_cef  <- add_ce_f_tier.py   (def assemble_cef .. J_before 前)
  derive_flips / assemble_all / classify_fills <- build_f3s_defs.py
原配方用 exec 把这些函数放进临时命名空间 (np / gamma_roi / o_arr / c /
n / atr / h / l / DAYCTX / CHART_DAYS / dates); 这里改为模块全局 + setter,
语义等价 (单线程、调用前由 envint_recipe 设好)。"""
from collections import defaultdict

import numpy as np

from settle_official import gamma_roi

# --- assemble_cef 的自由变量 (原 _nsD 命名空间) ---
o_arr = None
c = None
# --- run_ce 的自由变量 (原 ce_full 命名空间) ---
n = 0
atr = None
h = None
l = None
# --- assemble_all / classify_fills 的自由变量 (原 _ns5 命名空间) ---
DAYCTX = {}
CHART_DAYS = []
dates = None


def set_cef_ctx(o, c_):
    global o_arr, c
    o_arr, c = o, c_


def set_ce_ctx(n_, atr_, c_, h_, l_):
    global n, atr, c, h, l
    n, atr, c, h, l = n_, atr_, c_, h_, l_


def set_f3_ctx(dayctx, chart_days, dates_):
    global DAYCTX, CHART_DAYS, dates
    DAYCTX, CHART_DAYS, dates = dayctx, chart_days, dates_


def run_ce(M):
    st = np.zeros(n, dtype=int)      # 收盘后状态
    line = np.full(n, np.nan)        # 当根生效线
    hh = np.full(n, np.nan)          # 健康度 (按生效线状态符号)
    fl = []                          # 翻转根 (全局 idx)
    i0 = 12
    while not (np.isfinite(atr[i0]) and atr[i0] > 0):
        i0 += 1
    state = 1 if c[i0] - c[i0 - 12] >= 0 else -1
    ext = h[i0] if state > 0 else l[i0]
    st[i0] = state
    line[i0] = ext - M * atr[i0] if state > 0 else ext + M * atr[i0]
    hh[i0] = (c[i0] - line[i0]) / atr[i0] if state > 0 \
        else (line[i0] - c[i0]) / atr[i0]
    for i in range(i0 + 1, n):
        a = atr[i]
        if not (np.isfinite(a) and a > 0):
            st[i] = state
            line[i] = line[i - 1]
            hh[i] = hh[i - 1]
            continue
        if state > 0:
            if h[i] > ext:
                ext = h[i]
            ln = ext - M * a
            line[i] = ln
            hh[i] = (c[i] - ln) / a
            if c[i] < ln:
                state = -1
                fl.append(i)
                ext = l[i]
        else:
            if l[i] < ext:
                ext = l[i]
            ln = ext + M * a
            line[i] = ln
            hh[i] = (ln - c[i]) / a
            if c[i] > ln:
                state = 1
                fl.append(i)
                ext = h[i]
        st[i] = state
    return st, line, hh, fl

def assemble_cef(recs, segs, evs, dstr, gidx, loc):
    day_recs = [t for t in recs if t["ed"] == dstr]
    if not day_recs:
        # 窗口外日期 (2026-11-09/10): 无逐笔 -> 全空 def (段/翻仓只留在
        # 核对日志里, 不进图, 免得画出没有交易的段)
        return dict(trades=[], segs=[], flips=[], n=0, n_er=0,
                    total=0, total_chart=0, n_chart_miss=0)
    nb = len(gidx)
    g0 = int(gidx[0])
    trs, tot_c, miss = [], 0.0, 0
    for t in day_recs:
        ei, xi = loc[int(t["eb"])], loc[int(t["xb"])]
        di = ei + 1 if ei + 1 < nb else -1
        dx = xi + 1 if xi + 1 < nb else -1
        if di < 0 or dx < 0:
            rc, eo, xo = None, None, None
            miss += 1
        else:
            eo = round(float(o_arr[g0 + di]), 3)
            xo = round(float(o_arr[g0 + dx]), 3)
            rfull = gamma_roi("call" if int(t["dir"]) > 0 else "put",
                              o_arr[g0 + dx] - o_arr[g0 + di])
            rc = round(rfull, 2)
            tot_c += rfull
        trs.append([ei, xi, int(t["dir"]), t["mode"],
                    1 if t["mode"] == "er" else 0, round(float(t["r"]), 2),
                    t["rsn"], di, dx, eo, xo, rc])
    day_segs = [(s, e) for (s, e, sd) in segs if sd == dstr]
    sgs = []
    for (s, e) in day_segs:
        legs = [t for t in day_recs
                if t["mode"] == "er" and s <= int(t["eb"]) <= e]
        if legs:
            tdir = int(legs[0]["dir"])
            ent = loc[int(legs[0]["eb"])]
            ext = loc[int(legs[-1]["xb"])]
        else:
            tdir = int(np.sign(c[s] - c[s - 12]))
            ent = ext = -1
        sgs.append([loc[int(s)], loc[int(e)], tdir, ent, ext])
    flips = []
    for ev in evs:
        if ev[2] == "flip" and ev[1] == dstr:
            bar = int(ev[5])
            if bar in loc:
                flips.append(f"{loc[bar]}:{int(ev[4])}")
    return dict(trades=trs, segs=sgs, flips=sorted(flips),
                n=len(trs), n_er=sum(1 for t in trs if t[4] == 1),
                total=round(sum(float(t["r"]) for t in day_recs), 1),
                total_chart=round(tot_c, 1), n_chart_miss=miss)

def derive_flips(recs):
    by_eb = defaultdict(list)
    for t in recs:
        by_eb[t["eb"]].append(t)
    out = []
    for P in recs:
        if P["mode"] != "er" or P["rsn"] != "FLIP":
            continue
        succ = by_eb.get(P["xb"], [])
        er_succ = [t for t in succ if t["mode"] == "er" and t["ed"] == P["ed"]]
        if er_succ:
            out.append((er_succ[0]["eb"], er_succ[0]["dir"]))
            continue
        zl = [t for t in succ if t["mode"] == "trend"
              and t["eb"] == t["xb"] and t["ed"] == P["ed"]]
        if zl:
            out.append((zl[0]["eb"], zl[0]["dir"]))
    return out

def assemble_all(recs, segs, extra_by_day=None, extra_key=None):
    flips = derive_flips(recs)
    evs = [(0, str(dates[eb]), "flip", 0, d, eb) for eb, d in flips]
    defs = {}
    for dstr in CHART_DAYS:
        gidx, loc = DAYCTX[dstr]
        dd = assemble_cef(recs, segs, evs, dstr, gidx, loc)
        if extra_key is not None:
            dd2 = {"trades": dd["trades"], "segs": dd["segs"],
                   "flips": dd["flips"],
                   extra_key: (extra_by_day or {}).get(dstr, []),
                   "n": dd["n"], "n_er": dd["n_er"], "total": dd["total"],
                   "total_chart": dd["total_chart"],
                   "n_chart_miss": dd["n_chart_miss"]}
            defs[dstr] = dd2
        else:
            defs[dstr] = dd
    return defs, flips

def classify_fills(recs, segs, tier, mk):
    """图内 11 天: 段内 er 腿分 首腿/翻仓后继/补开; 返回 {day: fills} 与计数。"""
    flip_set = {(eb, d) for eb, d in derive_flips(recs)}
    seg_by_day = defaultdict(list)
    for s0, s1, dstr in segs:
        seg_by_day[dstr].append((s0, s1))
    fills_by_day = {}
    counts = dict(firsts=0, flipsucc=0, fills=0, er_chart=0, segs=0)
    for dstr in CHART_DAYS:
        legs = [r for r in recs if r["mode"] == "er" and r["ed"] == dstr]
        counts["er_chart"] += len(legs)
        assigned = set()
        day_fills = []
        for s0, s1 in sorted(seg_by_day[dstr]):
            counts["segs"] += 1
            slegs = sorted([r for r in legs if s0 <= r["eb"] <= s1],
                           key=lambda r: r["eb"])
            for r in slegs:
                assert id(r) not in assigned, \
                    f"{tier} {mk} {dstr} leg eb={r['eb']} in two segs"
                assigned.add(id(r))
            if not slegs:
                continue
            counts["firsts"] += 1
            _, loc = DAYCTX[dstr]
            for r in slegs[1:]:
                if (r["eb"], r["dir"]) in flip_set:
                    counts["flipsucc"] += 1
                else:
                    counts["fills"] += 1
                    day_fills.append((r["eb"], f"{loc[r['eb']]}:{r['dir']}"))
        assert len(assigned) == len(legs), \
            f"{tier} {mk} {dstr}: {len(legs)} er legs, {len(assigned)} in segs"
        fills_by_day[dstr] = [s for _, s in sorted(day_fills)]
    return fills_by_day, counts
