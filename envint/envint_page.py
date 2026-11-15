#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""日页 Envelope 整合版常量构造 (2026-11-15 vendored)。

与 research/er_leg_backtest_20261110/brk1112/build_daypage_env.py 逐行同源
(norm_runs / build_trades / settle / build_consts 函数体一字未改), 唯一
差别: build_consts 的页面派生量 (vals/times/line1m/ENVELOPE_TIMES1M/gap
文本) 在原版从页面 HTML 提取, 在 gen 里直接传内存值 (同一批数)。
呈现口径与样板日 2026-11-13 一致: slope 段天蓝染色线 (无背景带)、
slope 圈天蓝边、平仓圈灰、日收益连乘、单子表整合版逐笔。
"""
import json

SKY = "#38bdf8"
SKY_DOT = "#0ea5e9"
OPEN_CALL = "#00cc00"     # 开 call 正绿 (同 theme.js pinSpec.openCall)
OPEN_PUT = "#ff0000"      # 开 put 正红 (同 theme.js pinSpec.openPut)
CLOSE_FILL = "#9ca3af"    # 平仓圈灰填充 (同 theme.js pinSpec.close)
CLOSE_BORDER = "#6b7280"  # 终栈平仓圈灰边

STACK_ENTRY_RS = {"edge": ("边缘买入", "边缘卖出"),
                  "er": ("ER买入", "ER卖出"),
                  "trend": ("趋势买入", "趋势卖出")}
STACK_CLOSE_RS = {"SL": "止损", "FLIP": "翻仓", "ER_END": "ER段结束",
                  "PREEMPT": "slope抢占平仓", "SEG_END": "段结束平仓",
                  "EOD": "日终平仓", "TREND_EXIT": "趋势出段"}
SLOPE_CLOSE_RS = {"FLIP": "中线转向翻仓", "SEG_END": "段结束平仓",
                  "EOD": "日终平仓"}


def money(x):
    return ("+$" if x >= 0 else "-$") + f"{abs(x):,.0f}"


def norm_runs(sday, day):
    """slope_int 的 runs 只有 [s, e]; 方向 = 段始 slope 符号 (定义即如此)。"""
    out = []
    for r in sday["runs"]:
        s, e = r[0], r[1]
        d = 0
        sv = day["slope"][s] if day.get("slope") else None
        if sv:
            d = 1 if sv > 0 else -1
        if not d:
            dm = day["mid"][e] - day["mid"][s]
            d = 1 if dm >= 0 else -1
        out.append([s, e, d])
    return out


def build_trades(sday, runs):
    """合并 slope 单 + 终栈单为单仓位时间线 (引擎收盘口径)。"""
    trades = []
    for t in sday["trades"]:
        trades.append(dict(obi=t["eb"], cbi=t["xb"], dir=t["dir"],
                           px0=t["epx"], px1=t["xpx"], roi=t["r"] / 100.0,
                           rsn=t["rsn"], src="slope", mode=None))
    for t in sday["stack_dd"]["trades"]:
        trades.append(dict(obi=t[0], cbi=t[1], dir=t[2], mode=t[3],
                           px0=t[9], px1=t[10], roi=t[5] / 100.0,
                           rsn=t[6], src="stack"))
    trades.sort(key=lambda z: (z["obi"], 0 if z["src"] == "stack" else 1))
    for i, t in enumerate(trades):
        t["i"] = i
        t["flip_close"] = (t["rsn"] == "FLIP")
        prev = trades[i - 1] if i else None
        t["flip_entry"] = bool(prev and prev["flip_close"]
                               and prev["cbi"] == t["obi"])
        buy = t["dir"] > 0
        if t["src"] == "slope":
            run = next((r for r in runs
                        if r[0] <= t["obi"] <= r[1] and r[2] == t["dir"]), None)
            if t["flip_entry"]:
                base = "slope翻仓买入" if buy else "slope翻仓卖出"
            elif run is not None and t["obi"] > run[0]:
                base = "slope补开买入" if buy else "slope补开卖出"
            else:
                base = "slope买入" if buy else "slope卖出"
            t["entry_rs"] = base
            t["close_rs"] = SLOPE_CLOSE_RS.get(t["rsn"], t["rsn"])
        else:
            pair = STACK_ENTRY_RS.get(t["mode"], ("买入", "卖出"))
            t["entry_rs"] = pair[0] if buy else pair[1]
            t["close_rs"] = STACK_CLOSE_RS.get(t["rsn"], t["rsn"])
    return trades


def settle(trades):
    """页面既有口径: 日内 $100k sequential 复利 (= 日收益连乘)。"""
    cap, tot = 100000.0, 0.0
    for t in trades:
        t["pnl"] = cap * t["roi"]
        cap += t["pnl"]
        tot += t["pnl"]
    return tot, tot / 100000.0


def build_consts(vals, times3, line1m, times1m, gap_txt, gap_rich,
                 sday, day, trades, runs, tot, tot_pct, date):
    """按 gen_wave_today.py 日页原生格式构造 ENVELOPE_* 常量 (live15 模板)。

    与 build_daypage_env.build_consts 逐行同源, 唯一差别: 原版从页面 HTML
    正则提取 vals/times/line1m/ENVELOPE_TIMES1M 与 TITLES3/3E 的 gap 文本,
    这里由 gen 直接传内存中的同名值 (页面常量就是这些值写的)。"""
    close, opn = day["close"], day["open"]
    n = len(close)

    def snap_slot(hhmm):
        idx = 0
        for k, ts in enumerate(times3):
            if ts <= hhmm:
                idx = k
            else:
                break
        return idx

    def qqe_y(slot):
        v = vals[slot]
        if v is None:
            return 5
        return round(v * 1.15 + (0.5 if v > 0 else -0.5), 1)

    def hm(idx):
        return times1m[idx]

    def pin_color(kind, d):
        if kind in ("flip", "open"):
            return OPEN_CALL if d > 0 else OPEN_PUT  # 开仓类一律正绿/正红
        return CLOSE_FILL  # 平仓圈一律灰填充, 不分方向

    last_i = len(trades) - 1
    nf_pins, f_pins = [], []

    def add_pin(t, kind):
        is_slope = t["src"] == "slope"
        if kind == "close":
            slot = snap_slot(hm(min(t["cbi"], n - 1)))
            lab = "平" + ("C" if t["dir"] > 0 else "P")
            rs, col = t["close_rs"], pin_color("close", t["dir"])
        elif kind == "flip":
            slot = snap_slot(hm(t["obi"]))
            lab = "翻" + ("C" if t["dir"] > 0 else "P")
            rs, col = t["entry_rs"], pin_color("flip", t["dir"])
        else:
            slot = snap_slot(hm(t["obi"]))
            lab = "开" + ("C" if t["dir"] > 0 else "P")
            rs, col = t["entry_rs"], pin_color("open", t["dir"])
        st = {"color": col}
        if is_slope:
            st["borderColor"] = SKY
            st["borderWidth"] = 2.5
        elif kind == "close":
            st["borderColor"] = CLOSE_BORDER
            st["borderWidth"] = 1
        item = {"coord": [times3[slot], qqe_y(slot)], "value": lab,
                "itemStyle": st, "rs": rs}
        (f_pins if kind == "flip" else nf_pins).append(item)

    for t in trades:
        if t["flip_entry"]:
            add_pin(t, "flip")
        else:
            add_pin(t, "open")
        if not (t["flip_close"] and t["i"] != last_i):
            add_pin(t, "close")
    pins = nf_pins + f_pins

    def spin_item(x, y, value, col, rs, is_call, is_slope, is_close=False):
        d = {"coord": [x, y], "value": value,
             "itemStyle": {"color": col}, "rs": rs}
        if is_slope:
            d["itemStyle"]["borderColor"] = SKY
            d["itemStyle"]["borderWidth"] = 2.5
        elif is_close:
            d["itemStyle"]["borderColor"] = CLOSE_BORDER
            d["itemStyle"]["borderWidth"] = 1
        if is_call:
            d["symbolRotate"] = 180
            d["label"] = {"offset": [0, 5]}
        return d

    spins = []
    for t in trades:
        is_call = t["dir"] > 0
        kind = "flip" if t["flip_entry"] else "open"
        lab = ("翻" if t["flip_entry"] else "开") + ("C" if is_call else "P")
        spins.append(spin_item(round(t["obi"] / 3, 4), round(close[t["obi"]], 2),
                               lab, pin_color(kind, t["dir"]), t["entry_rs"],
                               is_call, t["src"] == "slope"))
        if not (t["flip_close"] and t["i"] != last_i):
            spins.append(spin_item(round(t["cbi"] / 3, 4), round(close[t["cbi"]], 2),
                                   "平" + ("C" if is_call else "P"),
                                   pin_color("close", t["dir"]), t["close_rs"],
                                   is_call, t["src"] == "slope", is_close=True))
    open_keys = {(s["coord"][0], s["value"][-1]) for s in spins
                 if s["value"].startswith("开")}
    spins = [s for s in spins
             if not (s["value"].startswith("平")
                     and (s["coord"][0], s["value"][-1]) in open_keys)]

    # ---- scat: gen 原生错行算法 (30 分钟窗同行冲突递增行号) ----
    hi_c, lo_c = max(close), min(close)
    rng = (hi_c - lo_c) or 1.0
    placed, scat = [], []
    for t in sorted(trades, key=lambda z: z["cbi"]):
        mnt = 570 + t["cbi"]
        row = 0
        while any(abs(mnt - om) < 30 and orow == row for om, orow in placed):
            row += 1
        placed.append((mnt, row))
        yy = round(hi_c + rng * 0.03 + row * rng * 0.05, 2)
        scat.append({"value": [hm(t["cbi"]), yy],
                     "lbl": "(" + format(t["roi"], "+.1%") + ")",
                     "label": {"color": "#1a9e54" if t["roi"] >= 0 else "#e5484d"}})

    # ---- markarea: 原生 JS 表达式 (THEME.pnlBgColor), 非 JSON ----
    ma_parts = []
    for t in trades:
        pct = round(t["roi"] * 100, 2)
        ma_parts.append("[{xAxis:'%s',itemStyle:{color:THEME.pnlBgColor(%s)}},{xAxis:'%s'}]"
                        % (hm(t["obi"]), pct, hm(min(t["cbi"], n - 1))))
    markarea_raw = "[" + ",".join(ma_parts) + "]"

    acts = {}
    for t in trades:
        L = "C" if t["dir"] > 0 else "P"
        acts.setdefault(hm(t["obi"]), []).append(
            f"开{L}@{str(float(t['px0']))} {t['entry_rs']}")
        acts.setdefault(hm(min(t["cbi"], n - 1)), []).append(
            f"平{L}@{str(float(t['px1']))} {t['close_rs']}")

    # ---- 单子表: gen 原生 13 列 chrome ----
    rows = []
    for t in trades:
        L = "C" if t["dir"] > 0 else "P"
        dot = (f"<span style='color:{SKY_DOT}'>●</span> " if t["src"] == "slope" else "")
        ec = "#1a9e54" if t["pnl"] >= 0 else "#e5484d"
        rows.append(
            "<tr><td>%d</td><td>%s</td><td>%s%s%s</td><td>—</td><td>—</td>"
            "<td>%.2f</td><td>%s</td><td>—</td><td>%.2f</td>"
            "<td style=\"color:#888888;font-weight:bold\">—</td>"
            "<td style=\"color:#888888\">—</td>"
            "<td style=\"color:%s\">~%s</td>"
            "<td style=\"color:%s\">~%s</td></tr>" % (
                t["i"] + 1, hm(t["obi"]), dot, t["entry_rs"], L, t["px0"],
                hm(min(t["cbi"], n - 1)), t["px1"],
                ec, money(t["pnl"]), ec, format(t["roi"], "+.1%")))
    rows.append(
        "<tr style=\"background:#f2f2f2;font-weight:bold\">"
        "<td colspan=\"9\">合计</td><td>—</td><td></td>"
        "<td>~%s</td><td></td></tr>" % money(tot))
    n_slope = sum(1 for t in trades if t["src"] == "slope")
    inner = (
        "<div style=\"font-size:14px;font-weight:bold;margin-bottom:6px\">"
        "Envelope版信号单（整合版 · Gamma理论预估，-100口径）</div>"
        "<div style=\"font-size:12px;color:#555;margin-bottom:6px\">"
        "整合版：WER终栈⑪＋slope层（imbalance bonus 定版 · 无止盈）· "
        "引擎信号根收盘口径 · slope单 %d 笔（蓝点＋slope 前缀标记）</div>"
        "<div style=\"overflow-x:auto\"><table border=\"1\" cellspacing=\"0\" "
        "cellpadding=\"6\" style=\"border-collapse:collapse;font-size:12px;"
        "width:100%%;text-align:center;white-space:nowrap\">"
        "<tr style=\"background:#f2f2f2\"><th>#</th><th>开仓</th><th>操作</th>"
        "<th>张数</th><th>开均价</th><th>开SPY</th><th>平仓</th><th>平均价</th>"
        "<th>平SPY</th><th>实际收益</th><th>实际收益率</th>"
        "<th>预估收益</th><th>预估收益率</th></tr>" % n_slope
        + "".join(rows) + "</table></div>")

    # ---- tooltip: 原生对象格式 {o,h,l,c,up,mid,lo,why} (mid=中轨chan) ----
    open_at, close_at, in_run = {}, {}, {}
    for t in trades:
        open_at.setdefault(t["obi"], []).append(t)
        close_at.setdefault(t["cbi"], []).append(t)
    for r in runs:
        for i in range(r[0], r[1] + 1):
            in_run[i] = r[2]
    tip = {}
    for i in range(n):
        why = []
        for t in close_at.get(i, []):
            lab = "平" + ("C" if t["dir"] > 0 else "P")
            col = "#089981" if t["dir"] > 0 else "#f23645"
            why.append(f"<b style='color:{col}'>{lab}@{t['px1']:.2f} {t['close_rs']}</b>")
        for t in open_at.get(i, []):
            lab = "开" + ("C" if t["dir"] > 0 else "P")
            col = "#1a9e54" if t["dir"] > 0 else "#e5484d"
            why.append(f"<b style='color:{col}'>{lab}@{t['px0']:.2f} {t['entry_rs']}</b>")
        if i in in_run:
            why.append("<span style='color:#0284c7'>slope段内</span>")
        def b2(x):
            return round(x, 2) if x is not None else "—"
        tip[hm(i)] = {"o": round(opn[i], 2),
                      "h": round(max(opn[i], close[i]), 2),
                      "l": round(min(opn[i], close[i]), 2),
                      "c": round(close[i], 2),
                      "up": b2(day["up"][i]), "mid": b2(day["chan"][i]),
                      "lo": b2(day["lo"][i]), "why": "<br/>".join(why)}

    def band(key):
        return [round(x, 2) if x is not None else None for x in day[key]]

    # ---- slope 染色线 (单系列, 段外 null 断开; 段首前一根接入); 无背景带 ----
    dye = []
    for j, pt in enumerate(line1m):
        inside = any(r[0] - 1 <= j <= r[1] for r in runs)
        dye.append([pt[0], pt[1] if inside else None])

    # ---- WER 面板: 终栈引擎口径 ----
    segs = sday["stack_dd"]["segs"]
    wer = day["werchamp"]
    wer_const = {
        "wer": [round(x, 4) if x is not None else None for x in wer],
        "eff": [round(x, 3) if x is not None else None for x in day["effstack"]],
        "areas": [[s, e, d] for s, e, d, *_ in segs],
        "entries": [[s, round(wer[s], 4) if wer[s] is not None else None, d]
                    for s, e, d, *_ in segs],
        "exits": [[e, round(wer[e], 4) if wer[e] is not None else None]
                  for s, e, d, *_ in segs if e < n - 1],
    }

    pct = f"{tot_pct:+.1%}"
    sign_tag = "pnl_pos" if tot >= 0 else "pnl_neg"
    title = f"{date} (Envelope版) | {gap_txt} | {money(tot)} ({pct})"
    titlee = (f"{date} ({{envelope|Envelope}}版) | {gap_rich} | "
              f"{{{sign_tag}|{money(tot)} ({pct})}}")

    return {
        "ENVELOPE_PINS": pins, "ENVELOPE_SPINS": spins,
        "ENVELOPE_SCAT_1M": scat, "ENVELOPE_MARKAREA_1M": markarea_raw,
        "ENVELOPE_INNER": inner, "ENVELOPE_ACTS": acts,
        "ENVELOPE_TITLE": title, "ENVELOPE_TITLEE": titlee,
        "ENVELOPE_UPPER_1M": band("up"), "ENVELOPE_LOWER_1M": band("lo"),
        "ENVELOPE_CHANMID_1M": band("chan"), "ENVELOPE_MID_1M": band("mid"),
        "ENVELOPE_TIP": tip, "ENVELOPE_TREND_SEGS": [],
        "ENVELOPE_WER": wer_const,
        "ENVELOPE_SLOPE_DYE": dye,
    }, title, titlee
