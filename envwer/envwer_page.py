#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""日页 Envelope 版常量构造 (2026-11-17 vendored; 2026-11-20 起
双版同算 —— WER 冠军版 (envwer_recipe) 与 F1+WER+QQE转向
(envqsf_recipe / envqsf_engine, 2026-11-20 晚主线化, 此前 F1 侧
为 envf1_recipe 纯 F1+WER) 各产一份 payload, gen 两版都调本模块
装配、页内按钮切换。本模块构造逻辑与版式不变, 仅表头文案与
sf/qf 入场标签随 mode 参数走: mode="champion" (默认) 用冠军
文案、无 sf/qf 标签; mode="qsf" 用 F1+WER+QQE转向文案 + sf/qf
标签 + qf 箭头/QQE 面板圈 (build_trades 仍以 mode="f1" 取含
sf/qf 的标签字典; mode="f1" 文案保留作历史对照)。

与 envint/envint_page.py 逐行同源 (norm_runs / build_trades / settle /
build_consts 函数体同形), 差别只有三处:
  1. 单子表表头改冠军版文案 (无 slope 层);
  2. 段染色数据源: 整合版染 slope 段 (runs), 冠军版染 WER 段
     (stack_dd.segs) —— 画法与 2026-11-15 定版一致: 段内 1m 曲线
     天蓝 #38bdf8 线条染色、无背景带; WER 面板 areas 与主图染色
     同源同段 (口径一致); tooltip 段内标记文案改「WER段内」;
  3. 冠军版无 slope 单: sday.trades/runs 恒空, build_trades 的 slope
     分支永不触发 (保留同形以便 schema 一致)。
呈现口径: 圈锚信号根自身收盘 (spinfix)、平仓圈灰、日收益连乘。
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
# sf 反手标签只在 F1+WER 模式出现 (冠军版无 sf 仓, 2026-11-20 user 令);
# qf 转向标签只在 F1+WER+QQE转向 (mode="qsf") 出现, build_trades 仍以
# mode="f1" 取本字典 (含 qf), 表头文案才分 qsf (2026-11-20 主线化)。
STACK_ENTRY_RS_F1 = dict(STACK_ENTRY_RS,
                         sf=("反手买入", "反手卖出"),
                         qf=("QQE转向买入", "QQE转向卖出"))
# qf 标记样式 (user 2026-11-20 18:36/18:37/18:38 三令定版):
# 价格图 qf 进场圈与 1m QQE 面板 qf 圈参照突破页 ⑧「F1+ER+QQE转向」
# 同款 —— 价格圈: 圆 12px、圈内单字母 C/P (白 9px 加粗)、橙边
# #ff8c00 宽 3 (突破页 buildMarks 的 qf 分支逐项); 填充走日页规范色
# (pin_color/theme 正绿正红, 与本页其余开仓圈一致)。面板圈在 gen
# 的 qqeOpt 里画 (13px + 「翻c/翻p」白字 7px, 突破页 panelOptQqeSig
# 同款)。qf 进场圈的 value 仍留 翻C/翻P (tooltip/acts/theme 按
# value 取色, 不动), 只覆盖 symbol/label/边宽。
# qfFlip 箭头: 经典直杆块状箭头 (18:46 附图定版, 覆盖 18:38 的
# 圆角版: 矩形箭杆 + 三角箭头头、直角直边, 即 PowerPoint 块箭头 /
# ➡ 那种最普通箭头形, 自定义 SVG path, 非 ECharts 默认 arrow),
# 实心不透明; 颜色 18:46 改版: call 深青蓝 / put 深红 (取代
# 18:37 的青蓝/紫红); 离价格曲线偏移 2.5 价格单位 (18:37 令)。
QF_BORDER = "#ff8c00"
QF_CIRCLE_SIZE = 12
QF_ARROW_CALL = "#0e7490"   # 深青蓝
QF_ARROW_PUT = "#b91c1c"    # 深红
QF_ARROW_OFF = 2.5
QF_ARROW_SIZE = 26
# 经典块状上箭头 path (100x100 标准多边形, 全直边直角: 尖 (50,2)、
# 头宽 96 (x 2..98)、头高 46 (y 2..48)、杆宽 32 (x 34..66)、
# 杆至 y 98; PowerPoint 上箭头块箭头同式比例);
# put 向 symbolRotate 180 翻下。
QF_ARROW_PATH = ("path://M50 2 L98 48 L66 48 L66 98 L34 98 "
                 "L34 48 L2 48 Z")
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


def build_trades(sday, runs, mode="champion"):
    """合并 slope 单 + 终栈单为单仓位时间线 (引擎收盘口径)。

    mode="f1" 时入场标签字典含 sf 反手标签, 其余与冠军版一致。"""
    entry_rs_map = STACK_ENTRY_RS_F1 if mode == "f1" else STACK_ENTRY_RS
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
            pair = entry_rs_map.get(t["mode"], ("买入", "卖出"))
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
                 sday, day, trades, runs, tot, tot_pct, date,
                 mode="champion", qf_arrows=None):
    """按 gen_wave_today.py 日页原生格式构造 ENVELOPE_* 常量 (live15 模板)。

    与 envint_page.build_consts 逐行同源, 差别见模块 docstring (表头
    文案 + WER 段染色/段内标记)。mode 切单子表表头文案:
    "champion" = WER 冠军版 (终栈⑪) 原文案; "f1" = F1+WER 文案;
    "qsf" = F1+WER+QQE转向文案。qf_arrows (仅 qsf 版传入,
    [[确认根当日下标, 方向, sf单r, qf单r], ...]) 产 qfFlip 箭头
    (主图 spins) 与 ENVELOPE_QF1M (1m QQE 面板信号圈槽位)。"""
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

    # 锚点口径 (2026-11-17 user 令修复): 时间一律取当日真实时间轴
    # day["times"], 不用位置序号推时间——源帧缺分钟 (如 2026-11-16 缺
    # 12:21) 时位置序号与真实分钟错位, 此后全部标记偏一根、落不到
    # 1m 收盘线上 (实测 11-16 午后 51 个圈 y 恰等于曲线下一根收盘)。
    dtimes = day["times"]

    def _mo(idx):
        _t = dtimes[idx]
        return int(_t[:2]) * 60 + int(_t[3:]) - 570

    def hm(idx):
        return dtimes[idx]

    def sx(idx):
        if idx < len(line1m):
            return line1m[idx][0]
        return round(_mo(idx) / 3, 4)

    # 曲线点按分钟查表: 各代页面 x 约定不一 (起点式 j/3 / 中点式
    # (j+0.5)/3 / 截断网格), 且个别页曲线与配方帧不同 feed (根数或
    # 个别收盘差几分), 按下标取点会错位; 按信号根的真实分钟取曲线
    # 自身的点 (x, y), 圈必落在曲线该分钟的收盘点上。
    _lpts = {}
    if line1m:
        _x1 = next((p[0] for p in line1m if p[0] > 0), 0.3333)
        _mid = abs(_x1 * 3 - round(_x1 * 3)) > 0.25
        for _x, _y in line1m:
            _lpts[int(round(_x * 3 - (0.5 if _mid else 0.0)))] = (_x, _y)

    def spt(idx):
        p = _lpts.get(_mo(idx))
        if p is not None:
            return p
        return sx(idx), round(close[idx], 2)

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
        elif t.get("mode") == "qf":
            st["borderColor"] = QF_BORDER
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

    def spin_item(x, y, value, col, rs, is_call, is_slope, is_close=False,
                  is_qf=False):
        d = {"coord": [x, y], "value": value,
             "itemStyle": {"color": col}, "rs": rs}
        if is_slope:
            d["itemStyle"]["borderColor"] = SKY
            d["itemStyle"]["borderWidth"] = 2.5
        elif is_qf:
            d["itemStyle"]["borderColor"] = QF_BORDER
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
        _ox, _oy = spt(t["obi"])
        _entry = spin_item(_ox, _oy,
                           lab, pin_color(kind, t["dir"]), t["entry_rs"],
                           is_call, t["src"] == "slope",
                           is_qf=(t.get("mode") == "qf"))
        if t.get("mode") == "qf":
            # 突破页 ⑧ 同款 qf 进场圈 (user 2026-11-20 18:36 令):
            # 圆 12px、圈内单字母 C/P (白 9px 加粗)、橙边宽 3;
            # value 不动 (仍 翻C/翻P), 只覆盖渲染参数。
            _entry["symbol"] = "circle"
            _entry["symbolSize"] = QF_CIRCLE_SIZE
            _entry["label"] = {"show": True,
                               "formatter": "C" if is_call else "P",
                               "color": "#fff", "fontSize": 9,
                               "fontWeight": "bold"}
            _entry["itemStyle"]["borderColor"] = QF_BORDER
            _entry["itemStyle"]["borderWidth"] = 3
        spins.append(_entry)
        if not (t["flip_close"] and t["i"] != last_i):
            _cx, _cy = spt(t["cbi"])
            # qf 单平仓圈 = 普通平仓圈 (突破页 ⑧ 同口径: qf 出场圈
            # 无橙边), 故此处不传 is_qf。
            spins.append(spin_item(_cx, _cy,
                                   "平" + ("C" if is_call else "P"),
                                   pin_color("close", t["dir"]), t["close_rs"],
                                   is_call, t["src"] == "slope", is_close=True))
    open_keys = {(s["coord"][0], s["value"][-1]) for s in spins
                 if s["value"].startswith("开")}
    spins = [s for s in spins
             if not (s["value"].startswith("平")
                     and (s["coord"][0], s["value"][-1]) in open_keys)]

    # ---- QQE 转向箭头 (仅 qsf 版): 确认根收盘下方/上方 QF_ARROW_OFF
    # 处, 经典直杆块状箭头 (QF_ARROW_PATH 自定义 path, user 2026-11-20
    # 18:46 附图定版; call 深青蓝 / put 深红、实心不透明、
    # 偏移 2.5 离曲线更远) ----
    if qf_arrows:
        for _a in qf_arrows:
            _bi, _nd = int(_a[0]), int(_a[1])
            _ax, _ay = spt(_bi)
            _ay = round(_ay + (-QF_ARROW_OFF if _nd > 0 else QF_ARROW_OFF), 2)
            _acol = QF_ARROW_CALL if _nd > 0 else QF_ARROW_PUT
            spins.append(
                {"coord": [_ax, _ay],
                 "value": "qfFlipC" if _nd > 0 else "qfFlipP",
                 "symbol": QF_ARROW_PATH, "symbolSize": QF_ARROW_SIZE,
                 "symbolRotate": 0 if _nd > 0 else 180,
                 "label": {"show": False},
                 "itemStyle": {"color": _acol, "opacity": 1},
                 "rs": "QQE衰减翻仓转向"})

    # ---- 1m QQE 面板 qf 信号圈槽位: [390 槽下标 (=确认根分钟偏移),
    # 方向]; 圈样式在 gen 的 qqeOpt 里按突破页 panelOptQqeSig 同款
    # 画 (13px、圈内「翻c/翻p」白字 7px 加粗、红绿填充 + 橙边
    # #ff8c00 宽 2, user 2026-11-20 18:36 令) ----
    qf1m = [[_mo(int(_a[0])), int(_a[1])] for _a in (qf_arrows or [])]

    # ---- scat: gen 原生错行算法 (30 分钟窗同行冲突递增行号) ----
    hi_c, lo_c = max(close), min(close)
    rng = (hi_c - lo_c) or 1.0
    placed, scat = [], []
    for t in sorted(trades, key=lambda z: z["cbi"]):
        mnt = 570 + _mo(t["cbi"])
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
    if mode == "qsf":
        _hdr_title = "Envelope版信号单（F1+WER+QQE转向 · Gamma理论预估，-100口径）"
        _hdr_desc = ("F1+WER+QQE转向：WER 本体＋F1 止损反手（p=0.25 · 反手止损 2.5×ATR）＋"
                     "ER 段/F3S 段内管理 · QQE 衰减翻仓转向（确认根砍 sf 反手仓、"
                     "反向接棒：信号圈橙边＋主图深蓝/深紫箭头） · dose/ramp/brk5 · "
                     "本体止损 0.7875×ATR · WER段内 1m 曲线天蓝染色 · ")
    elif mode == "f1":
        _hdr_title = "Envelope版信号单（F1+WER · Gamma理论预估，-100口径）"
        _hdr_desc = ("F1+WER：WER 本体＋F1 止损反手（p=0.25 · 反手止损 2.5×ATR）＋"
                     "ER 段/F3S 段内管理 · dose/ramp/brk5 · "
                     "本体止损 0.7875×ATR · WER段内 1m 曲线天蓝染色 · ")
    else:
        _hdr_title = "Envelope版信号单（WER终栈⑪ · Gamma理论预估，-100口径）"
        _hdr_desc = ("WER 冠军版（终栈⑪）：ER 段＋F3S 段内管理 · dose/ramp/brk5 · "
                     "ER腿止损 0.7875×ATR · WER段内 1m 曲线天蓝染色 · ")
    inner = (
        "<div style=\"font-size:14px;font-weight:bold;margin-bottom:6px\">"
        + _hdr_title + "</div>"
        "<div style=\"font-size:12px;color:#555;margin-bottom:6px\">"
        + _hdr_desc +
        "引擎信号根收盘口径</div>"
        "<div style=\"overflow-x:auto\"><table border=\"1\" cellspacing=\"0\" "
        "cellpadding=\"6\" style=\"border-collapse:collapse;font-size:12px;"
        "width:100%;text-align:center;white-space:nowrap\">"
        "<tr style=\"background:#f2f2f2\"><th>#</th><th>开仓</th><th>操作</th>"
        "<th>张数</th><th>开均价</th><th>开SPY</th><th>平仓</th><th>平均价</th>"
        "<th>平SPY</th><th>实际收益</th><th>实际收益率</th>"
        "<th>预估收益</th><th>预估收益率</th></tr>"
        + "".join(rows) + "</table></div>")

    # ---- tooltip: 原生对象格式 {o,h,l,c,up,mid,lo,why} (mid=中轨chan) ----
    open_at, close_at, in_seg = {}, {}, {}
    for t in trades:
        open_at.setdefault(t["obi"], []).append(t)
        close_at.setdefault(t["cbi"], []).append(t)
    for s, e, d, *_ in sday["stack_dd"]["segs"]:
        for i in range(s, e + 1):
            in_seg[i] = d
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
        if i in in_seg:
            why.append("<span style='color:#0284c7'>WER段内</span>")
        def b2(x):
            return round(x, 2) if x is not None else "—"
        tip[hm(i)] = {"o": round(opn[i], 2),
                      "h": round(max(opn[i], close[i]), 2),
                      "l": round(min(opn[i], close[i]), 2),
                      "c": round(close[i], 2),
                      "up": b2(day["up"][i]), "mid": b2(day["chan"][i]),
                      "lo": b2(day["lo"][i]), "why": "<br/>".join(why)}

    def band(key):
        # 按真实分钟槽位铺 (缺分钟留 None), 与类目轴时间标签对齐;
        # 完整日与旧位置式逐项一致。
        out = [None] * (_mo(n - 1) + 1) if n else []
        for i, x in enumerate(day[key]):
            out[_mo(i)] = round(x, 2) if x is not None else None
        return out

    def _remap(arr, nd):
        out = [None] * (_mo(n - 1) + 1) if n else []
        for i, x in enumerate(arr):
            out[_mo(i)] = round(x, nd) if x is not None else None
        return out

    # ---- WER 段染色线 (单系列, 段外 null 断开; 段首前一根接入); 无背景带 ----
    # 画法与整合版 slope 段一致 (2026-11-15 定版): 段内 1m 曲线染天蓝;
    # 段源 = 终栈 ER 段 (stack_dd.segs), 与下方 WER 面板 areas 同源。
    segs = sday["stack_dd"]["segs"]
    dye = []
    for j, pt in enumerate(line1m):
        inside = any(s - 1 <= j <= e for s, e, d, *_ in segs)
        dye.append([pt[0], pt[1] if inside else None])

    # ---- WER 面板: 终栈引擎口径 ----
    wer = day["werchamp"]
    wer_const = {
        "wer": _remap(wer, 4),
        "eff": _remap(day["effstack"], 3),
        "areas": [[_mo(s), _mo(e), d] for s, e, d, *_ in segs],
        "entries": [[_mo(s), round(wer[s], 4) if wer[s] is not None else None, d]
                    for s, e, d, *_ in segs],
        "exits": [[_mo(e), round(wer[e], 4) if wer[e] is not None else None]
                  for s, e, d, *_ in segs if e < n - 1],
    }

    # ---- slope/dev 面板 (2026-11-17 user 令): slope7 = 引擎 ind slope
    # (SLOPE_LEN=7), dev = slope - 前 20 根 slope 均值 (引擎
    # _slope_segments 同式, recipe 已算); 阈值 = 引擎常量
    # SLOPE_TH_UP 0.050 / SLOPE_TH_DN 0.070 / SLOPE_DEV_TH 0.079
    # (tv_v67_er_overlay_wcent2f3sr_slope.py 定格值, 构建侧注入) ----
    slpdev_const = {
        "slope": _remap(day["slope"], 4),
        "dev": _remap(day["dev"], 4),
        "th": {"up": 0.05, "dn": 0.07, "dev": 0.079},
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
        "ENVELOPE_SLPDEV": slpdev_const,
        "ENVELOPE_SLOPE_DYE": dye,
        "ENVELOPE_QF1M": qf1m,
    }, title, titlee
