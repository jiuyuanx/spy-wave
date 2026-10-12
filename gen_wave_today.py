#!/usr/bin/env python3
"""今日波浪dashboard刷新: QQE柱 + 80%衰减线 + 翻仓门槛线, 白底ECharts风格。

数据源: 与 watcher_v4 完全一致 (warmup缓存 + 今日1m->3m聚合, 只取完整bar)。
QQE/真色/衰减/翻仓need 全部按 signal_engine(k3clean L25当前基线) 的真实算法逐bar重放,
不手绘。显示窗口: 今日 09:30 -> 最新完整bar。

用法: python3 gen_wave_today.py
输出: ~/workspace/alpaca_watcher/wave-today-YYYYMMDD.html
"""
import sys, os, json
from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # 同目录优先 (GitHub Actions 部署包)
sys.path.insert(0, "/home/hatch/workspace/qqe_backtest")
sys.path.insert(0, "/home/hatch/workspace/alpaca_watcher")
from qqe_mod import qqe_mod

ET = ZoneInfo("America/New_York")
WDIR = os.environ.get("WAVE_OUT_DIR", "/home/hatch/workspace/alpaca_watcher")
WAVE_OUT_NAME_TMPL = os.environ.get("WAVE_OUT_NAME", "")  # 设了就用固定名 (如 index.html)
_ar = int(os.environ.get("WAVE_AUTOREFRESH", "0") or 0)
refresh_tag = f'<meta http-equiv="refresh" content="{_ar}">' if _ar > 0 else ""

import watcher_v4 as w4
from signal_engine import (run_signals, detect_red_decay, detect_blue_decay,
                           SIG_MIN_LEN)

BLUE, RED, GRAY = "#2f80ed", "#e5484d", "#b0b0b0"


def build_bars():
    hist3 = w4.load_history()
    df1m = w4.fetch_1m()
    today3 = w4.to_3m(df1m)
    bars3 = pd.concat([hist3, today3], ignore_index=True)
    bars3 = bars3.sort_values("t").drop_duplicates("t", keep="last").reset_index(drop=True)
    now = datetime.now(ET)
    n_complete = 0
    for idx in range(len(bars3)):
        bt = bars3["t"].iloc[idx]
        bar_end = bt + pd.Timedelta(minutes=3)
        n1m = bars3["n1m"].iloc[idx] if "n1m" in bars3.columns else 3
        if pd.isna(n1m):
            n1m = 3
        if bar_end <= now and n1m == 3:
            n_complete = idx + 1
        else:
            break
    if n_complete == 0:
        raise RuntimeError("no complete 3m bar yet")
    sig = pd.DataFrame({
        "t": bars3["t"].iloc[:n_complete],
        "open": bars3["o"].iloc[:n_complete],
        "high": bars3["h"].iloc[:n_complete],
        "low": bars3["l"].iloc[:n_complete],
        "close": bars3["c"].iloc[:n_complete],
    })
    return sig, df1m


def fav_wave_info(hist, i, day_arr):
    """照抄 signal_engine._fav_wave_info (|h|<1不断波 V1)。"""
    _ws = i
    _consec_small = 0
    _cur_day = day_arr[i]
    _day_start = i
    while _day_start > 0 and day_arr[_day_start - 1] == _cur_day:
        _day_start -= 1
    _sign = -1 if hist[i] < 0 else 1
    while _ws > _day_start:
        _h = hist[_ws - 1]
        if np.isnan(_h):
            break
        if _h * _sign < 0 and abs(_h) >= 1:
            break
        if abs(_h) <= 4:
            _consec_small += 1
            if _consec_small > 8:
                break
        else:
            _consec_small = 0
        _ws -= 1
    _pk_idx = i
    _pk_v = 0
    _run_max = 0
    _run_max_idx = _ws
    for _j in range(_ws, i + 1):
        if not np.isnan(hist[_j]):
            _v = abs(hist[_j])
            if _v > _run_max:
                _run_max = _v
                _run_max_idx = _j
            if _pk_idx == i and _run_max > 0 and _v < _run_max * 0.85:
                _pk_idx = _run_max_idx
                _pk_v = _run_max
    if _pk_idx == i:
        for _j in range(_ws, i + 1):
            if not np.isnan(hist[_j]):
                _v = abs(hist[_j])
                if _v > _pk_v:
                    _pk_v = _v
                    _pk_idx = _j
    _wl = i - _pk_idx
    _win_start = max(i - 9, _ws, _day_start)
    _bars = [abs(hist[_j]) for _j in range(_win_start, i + 1) if not np.isnan(hist[_j])]
    _chop = 0
    if len(_bars) >= 3:
        _total = sum(abs(_bars[_b] - _bars[_b - 1]) for _b in range(1, len(_bars)))
        _net = abs(_bars[-1] - _bars[0])
        _ratio = _total / _net if _net > 0.1 else 99
        _chop = min(1.0, max(0.0, (_ratio - 1.0) / 4.0))
    return _wl, _chop, _wl * (1.0 + _chop), _ws, _pk_idx


def wave_len_gw(hist, day_arr, j, sign, max_lookback=30):
    """照抄 signal_engine.wave_len_gw: 从 j 往回数 sign 方向波的长度。"""
    n = 0
    _cs = 0
    for k in range(j, max(-1, j - max_lookback), -1):
        if k < 0 or k >= len(hist):
            break
        try:
            if day_arr[k] != day_arr[j]:
                break
        except Exception:
            pass
        h = hist[k]
        if np.isnan(h):
            break
        if abs(h) < 3:
            break
        if h * sign > 0 and abs(h) > 3:
            n += 1
            _cs = 0
        elif abs(h) <= 3 and h * sign >= 0:
            _cs += 1
            if _cs > 2:
                break
        else:
            break
    return n


def huibu_mult(base):
    """照抄引擎长波回补自适应倍数: |前|<3要2倍, <5要1.2, 否则只要递增。"""
    if base < 3:
        return 2.0
    elif base < 5:
        return 1.2
    return 1.0


def main():
    sig, df1m = build_bars()
    today_str = datetime.now(ET).strftime("%Y-%m-%d")
    n = len(sig)
    close = sig["close"].values.astype(float)
    res = qqe_mod(close)
    hist = np.asarray(res["hist"], dtype=float)
    q_up = np.asarray(res["up"]).astype(bool)
    q_down = np.asarray(res["down"]).astype(bool)
    day_arr = sig["t"].dt.date.values

    actions = run_signals(sig)
    # 今日的动作
    tstrs = sig["t"].dt.strftime("%H:%M").values
    dates = sig["t"].dt.date.astype(str).values
    today_idx = [i for i in range(n) if dates[i] == today_str]
    if not today_idx:
        raise RuntimeError("no today bars")

    # 真色
    colors = []
    for j in range(n):
        if q_up[j] and abs(hist[j]) >= SIG_MIN_LEN:
            colors.append(BLUE)
        elif q_down[j] and abs(hist[j]) >= SIG_MIN_LEN:
            colors.append(RED)
        else:
            colors.append(GRAY)

    # 翻仓bar集合 (同bar既平又开)
    by_bar = {}
    for (bi, ts, act, dr, px, rs) in actions:
        by_bar.setdefault(bi, []).append(act)
    # 回补bar (长波回补/重涨回补的开仓): 只标'补', 不标'翻'
    repl_bars = set(bi for (bi, ts, act, dr, px, rs) in actions
                    if act == "开" and ("回补" in rs or "重涨" in rs))
    flip_bars = sorted(b for b, acts in by_bar.items()
                       if "平" in acts and "开" in acts and b not in repl_bars)

    last_action_bar = -999
    # 按bar顺序先扫一遍actions, 建 bar->last_action_bar 映射 (重放_damp_factor_gw)
    lab_at = {}
    cur_lab = -999
    acts_sorted = sorted(actions, key=lambda a: a[0])
    ai = 0
    for i in range(n):
        while ai < len(acts_sorted) and acts_sorted[ai][0] <= i:
            if acts_sorted[ai][2] in ("开", "平"):
                cur_lab = acts_sorted[ai][0]
            ai += 1
        lab_at[i] = cur_lab

    decay80, flip_line, hubu_line = [None] * n, [None] * n, [None] * n
    for i in today_idx:
        if abs(hist[i]) < 1 or np.isnan(hist[i]):
            continue
        sgn = -1 if hist[i] < 0 else 1
        _wl, _chop, _wl_adj, _ws, _pk_idx = fav_wave_info(hist, i, day_arr)
        seg = [abs(hist[j]) for j in range(_ws, i + 1) if not np.isnan(hist[j])]
        if not seg:
            continue
        run_peak = max(seg)
        if run_peak < 6:
            continue
        decay80[i] = round(sgn * run_peak * 0.8, 2)
        # need 重放 (照抄引擎公式; 只数过去的bar, 不偷看未来)
        _op_cnt = sum(1 for (bi, _, act, _, _, _) in actions
                      if _pk_idx <= bi <= i and act in ("开", "平"))
        _damp = 1.0 if (i - lab_at[i]) < 4 else 0.0
        if _op_cnt >= 4:
            _base = max(0.40, 0.8 * (0.97 ** max(0, _wl_adj - 3)))
            _need = 0.8 - (0.8 - _base) * _damp
        else:
            _need = 0.8
        if _wl_adj >= 15:
            _need *= 0.7
        _rf = [b for b in flip_bars if 0 <= i - b <= 30 and day_arr[b] == day_arr[i]]
        if len(_rf) >= 2:
            _need *= 0.7
        _need = max(0.30, _need)
        flip_line[i] = round(sgn * run_peak * _need, 2)
        # 回补门槛 (长波回补, 照抄引擎): 同向波长>=15, 当根与前根同色且|h|>3,
        # 门槛 = |前根| x 自适应倍数
        _wlen = wave_len_gw(hist, day_arr, i, sgn)
        if _wlen >= 15 and i >= 1 and day_arr[i] == day_arr[i - 1]:
            _hp = hist[i - 1]
            if (not np.isnan(_hp) and _hp * sgn > 0 and abs(_hp) > 3
                    and abs(hist[i]) > 3):
                _ahp = abs(_hp)
                hubu_line[i] = round(sgn * _ahp * huibu_mult(_ahp), 2)

    # markPoints: 波峰留在QQE柱上; 信号点(开/平/翻/补)点在1m曲线上
    qpins = []
    # 分波找峰
    seen_ws = set()
    for i in today_idx:
        if abs(hist[i]) < 1 or np.isnan(hist[i]):
            continue
        _wl, _chop, _wl_adj, _ws, _pk_idx = fav_wave_info(hist, i, day_arr)
        if _ws in seen_ws:
            continue
        seen_ws.add(_ws)
        seg_idx = [j for j in range(_ws, min(i + 1, n)) if not np.isnan(hist[j])]
        if not seg_idx:
            continue
        pk_j = max(seg_idx, key=lambda j: abs(hist[j]))
        if abs(hist[pk_j]) < 6:
            continue
        qpins.append({"coord": [tstrs[pk_j], round(float(hist[pk_j]) * 1.15, 1)],
                      "value": "蓝峰" if hist[pk_j] > 0 else "红峰",
                      "itemStyle": {"color": BLUE if hist[pk_j] > 0 else RED}})

    # 信号点: 点在1m曲线上 (x=分数类目坐标, y=信号bar收盘价); QQE上同步一份
    flip_set = set(flip_bars)
    flip_info = {}
    for (bi, ts, act, dr, px, rs) in acts_sorted:
        if bi in flip_set and act == "开":
            flip_info[bi] = (dr, rs)
    spins = []

    def _qpins_add(bi, value, color, rs_txt=""):
        """信号点同时在QQE柱上标一份 (和1m曲线上那套一致)。"""
        h = float(hist[bi])
        yq = round(h * 1.15 + (0.5 if h > 0 else -0.5), 1) if not np.isnan(h) else 5
        qpins.append({"coord": [tstrs[bi], yq], "value": value,
                      "itemStyle": {"color": color}, "rs": rs_txt})
    for (bi, ts, act, dr, px, rs) in acts_sorted:
        if bi not in today_idx:
            continue
        p = today_idx.index(bi)
        xp = round(p + 2.5 / 3, 4)
        yp = round(float(sig["close"].iloc[bi]), 2)
        # 回补 (长波回补 + 重涨回补都算): 整根bar只标一个橙色'补C'/'补P'
        if bi in repl_bars:
            if act == "开":
                v = "补" + ("C" if dr == "call" else "P")
                rsc = rs.replace("'", "")
                spins.append({"coord": [xp, yp], "value": v,
                              "itemStyle": {"color": "#ff6600"}, "rs": rsc})
                _qpins_add(bi, v, "#ff6600", rsc)
            continue
        if bi in flip_bars:
            continue  # 纯翻仓只在后面标一个'翻C'/'翻P'
        if act == "开":
            v = "跟" + ("C" if dr == "call" else "P")
            rsc = rs.replace("'", "")
            spins.append({"coord": [xp, yp], "value": v,
                          "itemStyle": {"color": "#00aa00"}, "rs": rsc})
            _qpins_add(bi, v, "#00aa00", rsc)
        elif act == "平":
            v = "平" + ("C" if dr == "call" else "P")
            rsc = rs.replace("'", "")
            spins.append({"coord": [xp, yp], "value": v,
                          "itemStyle": {"color": "#999999"}, "rs": rsc})
            _qpins_add(bi, v, "#999999", rsc)
    for b in flip_bars:
        if b not in today_idx:
            continue
        p = today_idx.index(b)
        xp = round(p + 2.5 / 3, 4)
        yp = round(float(sig["close"].iloc[b]), 2)
        d, frs = flip_info.get(b, (None, ""))
        frs = frs.replace("'", "")
        v = "翻" + ("C" if d == "call" else "P")
        spins.append({"coord": [xp, yp], "value": v,
                      "itemStyle": {"color": "#ff0000"}, "rs": frs})
        _qpins_add(b, v, "#ff0000", frs)

    # 1m曲线数据: x=分数类目坐标(分钟中点), y=1m收盘
    t0 = sig["t"].iloc[today_idx[0]]
    df1 = df1m[df1m["t"] >= t0].sort_values("t")
    line1m = [[round((r["t"] - t0).total_seconds() / 180.0 + 1 / 6, 4),
               round(float(r["c"]), 2)] for _, r in df1.iterrows()]
    # tooltip用: 每根bar的OHLC + QQE + 动作原因
    acts_by_bar = {}
    for (bi, ts, act, dr, px, rs) in acts_sorted:
        if bi in today_idx:
            acts_by_bar.setdefault(bi, []).append(
                f"{act}{dr or ''}@{px} {rs}".replace("'", ""))
    bar_info = {}
    for i in today_idx:
        t = tstrs[i]
        bar_info[t] = {
            "o": round(float(sig["open"].iloc[i]), 2),
            "h": round(float(sig["high"].iloc[i]), 2),
            "l": round(float(sig["low"].iloc[i]), 2),
            "c": round(float(sig["close"].iloc[i]), 2),
            "qqe": round(float(hist[i]), 2) if not np.isnan(hist[i]) else None,
            "col": "蓝" if colors[i] == BLUE else ("红" if colors[i] == RED else "灰"),
            "acts": acts_by_bar.get(i, []),
        }
    bar_info_js = json.dumps(bar_info, ensure_ascii=False)
    tooltip_js = ("tooltip:{trigger:'axis',axisPointer:{type:'cross'},"
        "formatter:function(ps){var t=ps[0].axisValue;var b=barInfo[t];if(!b)return t;"
        "var s='<b>'+t+'</b><br/>开'+b.o+' 高'+b.h+' 低'+b.l+' 收'+b.c"
        "+'<br/>QQE '+b.qqe+'('+b.col+')';"
        "if(b.acts&&b.acts.length)s+='<br/>'+b.acts.join('<br/>');return s;}},")
    click_js = ("ch.on('click',function(p){if(p.componentType==='markPoint'&&p.data&&p.data.rs){"
        "document.getElementById('info').innerHTML='<b>'+p.data.value+'</b>　'+p.data.rs;}});")

    # 当前信号持仓
    pos = None
    for (bi, ts, act, dr, px, rs) in acts_sorted:
        if act == "开":
            pos = dr
        elif act == "平":
            pos = None
    pos_txt = {"call": "call", "put": "put"}.get(pos, "空仓")

    times = [tstrs[i] for i in today_idx]
    vals = [round(float(hist[i]), 2) if not np.isnan(hist[i]) else None for i in today_idx]
    cols = [colors[i] for i in today_idx]
    d80 = [decay80[i] for i in today_idx]
    fl = [flip_line[i] for i in today_idx]
    hb = [hubu_line[i] for i in today_idx]
    # 持仓区间背景: 按信号柱配对 开->平, 用信号价算每笔方向盈亏 (绿赚红亏)
    # 用户原则: 只看信号柱, 不管实际下单
    today_set = set(today_idx)
    first_t = tstrs[today_idx[0]]
    mark_areas = []
    _open = None  # (bi, dr, opx)
    def _emit(obi, odr, opx, cbi, cpx):
        try:
            pnl = (float(cpx) - float(opx)) if odr == "call" else (float(opx) - float(cpx))
        except (TypeError, ValueError):
            return
        _op = round(min(0.08 + abs(pnl) * 0.45, 0.5), 3)  # 越赚/越亏越不透明
        _col = f"rgba(26,158,84,{_op})" if pnl >= 0 else f"rgba(229,72,77,{_op})"
        s = tstrs[obi] if obi in today_set else first_t
        mark_areas.append([
            {"xAxis": s, "itemStyle": {"color": _col}},
            {"xAxis": tstrs[cbi]},
        ])
    for (bi, ts, act, dr, px, rs) in acts_sorted:
        if act == "平" and _open is not None:
            obi, odr, opx = _open
            if bi in today_set:
                _emit(obi, odr, opx, bi, px)
            _open = None
        elif act == "开":
            if _open is not None and bi in today_set:
                obi, odr, opx = _open
                _emit(obi, odr, opx, bi, px)
            _open = (bi, dr, px)
    if _open is not None and today_idx:  # 未平仓: 按最新bar算浮动方向
        obi, odr, opx = _open
        _emit(obi, odr, opx, today_idx[-1], float(sig["close"].iloc[today_idx[-1]]))
    mark_area_js = "[" + ",".join(
        "[{xAxis:'%s',itemStyle:{color:'%s'}},{xAxis:'%s'}]" % (
            a[0]["xAxis"], a[0]["itemStyle"]["color"], a[1]["xAxis"])
        for a in mark_areas) + "]"
    mark_area_opt = "{silent:true,data:" + mark_area_js + "}"
    ohlc = [[round(float(sig["open"].iloc[i]), 2), round(float(sig["close"].iloc[i]), 2),
             round(float(sig["low"].iloc[i]), 2), round(float(sig["high"].iloc[i]), 2)]
            for i in today_idx]
    ohlc_js = "[" + ",".join(f"[{o},{c},{l},{h}]" for o, c, l, h in ohlc) + "]"

    def js_arr(a):
        return "[" + ",".join("null" if v is None else str(v) for v in a) + "]"

    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8">{refresh_tag}
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>今日波浪 {today_str}</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"></script>
<style>body{{margin:0;background:#fff;font-family:sans-serif}}#c{{width:100%;height:95vh}}</style>
</head><body>
<div id="info" style="position:absolute;top:6px;right:8px;z-index:10;background:rgba(255,255,255,.92);border:1px solid #ddd;border-radius:6px;padding:6px 10px;font-size:12px;color:#333;max-width:46%">点击信号圈查看原因</div>
<div id="c"></div><script>
const times = {times};
const vals = {js_arr(vals)};
const colors = {cols};
const decayReal = {js_arr(d80)};
const flipBlue = {js_arr(fl)};
const hubuThr = {js_arr(hb)};
const pins = {qpins};
const line1m = {line1m};
const spins = {spins};
const barInfo = {bar_info_js};
var ch = echarts.init(document.getElementById('c'));
ch.setOption({{
  title:{{text:'今日波浪 {today_str} {times[0]}→{times[-1]} ET | 持仓:{pos_txt} | k3clean L25', left:'center', textStyle:{{fontSize:14}}}},
  legend:{{data:['3mK线','1m曲线','QQE','80%衰减线','翻仓线','回补门槛'], top:30}},
  {tooltip_js}
  dataZoom:[{{type:'inside', xAxisIndex:[0,1]}},{{type:'slider', xAxisIndex:[0,1], bottom:6}}],
  grid:[{{left:'6%',right:'4%',top:'15%',height:'30%'}},{{left:'6%',right:'4%',top:'52%',height:'34%'}}],
  axisPointer:{{link:{{xAxisIndex:'all'}}}},
  xAxis:[{{type:'category',data:times,gridIndex:0,axisLabel:{{show:false}},axisTick:{{show:false}},axisLine:{{show:false}}}},
         {{type:'category',data:times,gridIndex:1,axisLabel:{{rotate:45, fontSize:10}}}}],
  yAxis:[{{type:'value',gridIndex:0,scale:true}},{{type:'value',gridIndex:1}}],
  series:[
    {{name:'3mK线', type:'candlestick', data:{ohlc_js}, xAxisIndex:0, yAxisIndex:0,
      itemStyle:{{color:'#1a9e54', color0:'#e5484d', borderColor:'#1a9e54', borderColor0:'#e5484d'}},
      markArea:{mark_area_opt}}},
    {{name:'1m曲线', type:'line', data:line1m, xAxisIndex:0, yAxisIndex:0,
      lineStyle:{{color:'#4da6ff', width:1.2, opacity:0.55}}, symbol:'none',
      markPoint:{{symbol:'pin', symbolSize:36, data:spins, label:{{color:'#fff', fontSize:10}}, itemStyle:{{opacity:0.75}}}},
    }},
    {{name:'QQE', type:'bar', data: vals.map((v,i)=>({{value:v, itemStyle:{{color:colors[i]}}}})),
      xAxisIndex:1, yAxisIndex:1,
      markPoint:{{symbol:'pin', symbolSize:36, data:pins, label:{{color:'#fff', fontSize:10}}, itemStyle:{{opacity:0.75}}}},
    }},
    {{name:'80%衰减线', type:'line', data:decayReal, xAxisIndex:1, yAxisIndex:1, lineStyle:{{color:'#ff9900', type:'dashed', width:3}}, symbol:'none'}},
    {{name:'翻仓线', type:'line', data:flipBlue, xAxisIndex:1, yAxisIndex:1, lineStyle:{{color:'#ff0000', width:2}}, symbol:'none'}},
    {{name:'回补门槛', type:'line', data:hubuThr, xAxisIndex:1, yAxisIndex:1, lineStyle:{{color:'#a020f0', type:'dashed', width:2}}, symbol:'none'}},
  ]
}});
{click_js}
</script></body></html>"""
    _out_name = WAVE_OUT_NAME_TMPL or f"wave-today-{today_str.replace('-', '')}.html"
    out = f"{WDIR}/{_out_name}"
    os.makedirs(WDIR, exist_ok=True)
    open(out, "w").write(html)
    print(f"wrote {out} ({len(times)} bars, {len(qpins)+len(spins)} pins, pos={pos_txt})")
    # 今日动作摘要
    for (bi, ts, act, dr, px, rs) in acts_sorted:
        if bi in today_idx and act in ("开", "平"):
            print(f"  {ts} {act} {dr} @ {px} | {rs}")


if __name__ == "__main__":
    main()
