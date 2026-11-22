#!/usr/bin/env python3
"""QQE 影子状态机 (ER 段内 QQE 转向的对齐源): 逐根 bar 后的 QQE 腿
持仓方向 (+1/-1/0)。研究侧 compute_qqe_state_3y.py 的机器段逐字
移植 (跟随/衰减/回补/减震与定版 fork 同式; 衰减当根执行、峰门 12、
除数 16、无 W5 前窗门、灰柱 >=9), 只换外壳: 帧与指标由调用方
(envqsf_recipe) 注入, 返回与帧对齐的 int 数组, 不落盘。
等价锚: 研究 extended 帧上与 qqe_state_3y.json 逐根全等 (移植时验)。
"""
from collections import defaultdict

import numpy as np


def compute_state(dates, sm, ind, eng, start_date=None, end_date=None):
    """dates: 逐根日期 str 数组; sm: 逐根分钟 (hh*60+mm); ind: 指标
    dict (含 close/open/high/low/atr/er/adx/slope); eng: envqsf_engine
    模块 (借其 _qqe_detect_decay / _qqe_trig_line, 口径 0.80/16)。
    start_date/end_date = 影子腿记账窗 (窗外状态恒 0, 与研究侧
    compute_qqe_state 的 WIN_A/TODAY 同义); 缺省 = 全帧。"""
    from qqe_mod import qqe_mod as _qqe_mod

    eng.QQESF_TRIG_BASE = 0.80
    eng.QQESF_TRIG_DIV = 16.0
    PEAK_MIN = 12.0  # ERQQE spec: 定版峰门 12 (⑨ 的 4.5 不用)
    FLIP_DAMP_M = 0.8
    SL_K_Q = 0.7875
    ER_TH_Q, ADX_TH_Q, TAKEOVER_SLOPE_Q = 0.35, 30, 0.10

    _c = np.asarray(ind["close"], dtype=float)
    _qres = _qqe_mod(_c)
    q_hist = np.asarray(_qres["hist"], dtype=float)
    q_up = np.asarray(_qres["up"]).astype(bool)
    q_down = np.asarray(_qres["down"]).astype(bool)
    q_peak_red = eng._qqe_detect_decay(q_hist, q_down, q_up, dates)
    q_peak_blue = eng._qqe_detect_decay(q_hist, q_up, q_down, dates)

    day_span = {}
    for _i, _d in enumerate(dates):
        if _d in day_span:
            day_span[_d][1] = _i + 1
        else:
            day_span[_d] = [_i, _i + 1]
    WIN_A = str(start_date) if start_date else str(dates[0])
    TODAY = str(end_date) if end_date else str(dates[-1])

    # ---- 跟随检测 (fork _qfol_dir 逐字移植) ----
    QFOL_MAXWIN = 0  # ERQQE spec: 无 W5 前窗门
    QFOL_GRAY_THR = 9  # ERQQE spec: 定版灰柱 >=9


    def _qfol_true_blue(_j):
        return bool(q_up[_j]) and abs(q_hist[_j]) >= 4


    def _qfol_true_red(_j):
        return bool(q_down[_j]) and abs(q_hist[_j]) >= 4


    def _qfol_is_gray(_j):
        _hj = q_hist[_j]
        if np.isnan(_hj) or abs(_hj) <= 3:
            return False
        return not _qfol_true_blue(_j) and not _qfol_true_red(_j)


    def _qfol_true_blue_w(_j):
        if _qfol_true_blue(_j):
            return True
        _hj = q_hist[_j]
        return _qfol_is_gray(_j) and _hj > 0 and abs(_hj) >= QFOL_GRAY_THR


    def _qfol_true_red_w(_j):
        if _qfol_true_red(_j):
            return True
        _hj = q_hist[_j]
        return _qfol_is_gray(_j) and _hj < 0 and abs(_hj) >= QFOL_GRAY_THR


    FOL_BLOCKED = []
    FOL_DETECT = []  # 全部跟随检出 (含同向持仓时无动作的检出; user 2026-11-21 16:41 指出接管门要算这一类)


    def _qfol_dir(_i):
        if _i < 1 or dates[_i - 1] != dates[_i]:
            return 0
        _h1 = q_hist[_i]
        _h0 = q_hist[_i - 1]
        if np.isnan(_h1) or np.isnan(_h0):
            return 0
        _fd = 0
        if _qfol_true_red_w(_i) and abs(_h1) >= 8 \
                and _qfol_true_red_w(_i - 1) and abs(_h0) >= 6 \
                and abs(_h1) > abs(_h0):
            _fd = -1
        elif _qfol_true_blue_w(_i) and abs(_h1) >= 8 \
                and _qfol_true_blue_w(_i - 1) and abs(_h0) >= 6 \
                and abs(_h1) > abs(_h0):
            _fd = 1
        if _fd != 0 and QFOL_MAXWIN > 0:
            _tfn = _qfol_true_blue if _fd > 0 else _qfol_true_red
            for _j in range(max(0, _i - QFOL_MAXWIN), _i):
                if _tfn(_j) and abs(q_hist[_j]) >= abs(_h1):
                    FOL_BLOCKED.append(
                        dict(bar=int(_i), date=dates[_i], dir=int(_fd),
                             hist=float(abs(_h1)),
                             win_max=float(abs(q_hist[_j])),
                             win_bar=int(_j)))
                    return 0
        return _fd


    # ---- 回补真色门 + 四条触发 (fork _hb_* 逐字移植) ----
    def _hb_true_blue(j):
        return bool(q_up[j]) and abs(q_hist[j]) >= 4


    def _hb_true_red(j):
        return bool(q_down[j]) and abs(q_hist[j]) >= 4


    def _hb_gray(j):
        if j < 0 or j >= len(q_hist):
            return False
        _hj = q_hist[j]
        if np.isnan(_hj) or abs(_hj) <= 3:
            return False
        return not _hb_true_blue(j) and not _hb_true_red(j)


    def _hb_true_blue_w(j, thr=10):
        if _hb_true_blue(j):
            return True
        _hj = q_hist[j]
        return _hb_gray(j) and not np.isnan(_hj) and _hj > 0 \
            and abs(_hj) >= thr


    def _hb_true_red_w(j, thr=10):
        if _hb_true_red(j):
            return True
        _hj = q_hist[j]
        return _hb_gray(j) and not np.isnan(_hj) and _hj < 0 \
            and abs(_hj) >= thr


    def _hb_trigger(_i, _tdir, _marker):
        _tsgn = _tdir
        _hc = q_hist[_i]
        if np.isnan(_hc):
            return False
        if not (_hb_true_blue_w(_i) if _tsgn > 0 else _hb_true_red_w(_i)):
            return False
        if abs(_hc) > _marker * 1.2:
            return True
        if _i >= 1:
            _hp = q_hist[_i - 1]
            if not np.isnan(_hp) and _hp * _tsgn > 0 and abs(_hp) > 3:
                _nd = 2.0 if abs(_hp) <= 5 else 1.2
                if abs(_hc) > abs(_hp) * _nd:
                    return True
        if _i >= 2:
            _h1, _h2 = q_hist[_i - 2], q_hist[_i - 1]
            if not np.isnan(_h1) and not np.isnan(_h2) \
                    and _h1 * _tsgn > 0 and _h2 * _tsgn > 0 \
                    and abs(_h1) > 3 and abs(_h2) > 3 \
                    and abs(_h2) > abs(_h1) and abs(_hc) > abs(_h2):
                _nd1 = 2.0 if abs(_h1) <= 5 else 1.2
                if abs(_hc) > abs(_h1) * _nd1:
                    return True
        if _i >= 1:
            _hp = q_hist[_i - 1]
            if not np.isnan(_hp) and _hp * _tsgn > 0 \
                    and abs(_hp) > 3 \
                    and abs(_hp) > _marker and abs(_hc) > _marker:
                return True
        return False


    # ---- 翻仓减震 (fork _sf_fav_wave_info / _sf_flip_blocked 逐字移植;
    # act_bars = 本视图自家开/平动作根) ----
    act_bars = []


    def _sf_fav_wave_info(_sign, _i):
        _ws = _i
        _consec_small = 0
        _cur_day = dates[_i]
        _day_start = _i
        while _day_start > 0 and dates[_day_start - 1] == _cur_day:
            _day_start -= 1
        while _ws > _day_start:
            _h = q_hist[_ws - 1]
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
        _pk_idx = _i
        _pk_v = 0
        _run_max = 0
        _run_max_idx = _ws
        for _j in range(_ws, _i + 1):
            if not np.isnan(q_hist[_j]):
                _v = abs(q_hist[_j])
                if _v > _run_max:
                    _run_max = _v
                    _run_max_idx = _j
                if _pk_idx == _i and _run_max > 0 \
                        and _v < _run_max * 0.85:
                    _pk_idx = _run_max_idx
                    _pk_v = _run_max
        if _pk_idx == _i:
            for _j in range(_ws, _i + 1):
                if not np.isnan(q_hist[_j]):
                    _v = abs(q_hist[_j])
                    if _v > _pk_v:
                        _pk_v = _v
                        _pk_idx = _j
        _wl = _i - _pk_idx
        _win_start = max(_i - 9, _ws, _day_start)
        _bars = [abs(q_hist[_j]) for _j in range(_win_start, _i + 1)
                 if not np.isnan(q_hist[_j])]
        _chop = 0
        if len(_bars) >= 3:
            _total = sum(abs(_bars[_b] - _bars[_b - 1])
                         for _b in range(1, len(_bars)))
            _net = abs(_bars[-1] - _bars[0])
            _ratio = _total / _net if _net > 0.1 else 99
            _chop = min(1.0, max(0.0, (_ratio - 1.0) / 4.0))
        return _wl, _chop, _wl * (1.0 + _chop), _ws, _pk_idx


    def _sf_flip_blocked(_sign, _i, _pk):
        _wl, _chop, _wl_adj, _ws, _pkidx = _sf_fav_wave_info(_sign, _i)
        _cnt = sum(1 for _b in act_bars if _b >= _pkidx)
        _last = act_bars[-1] if act_bars else -9999
        if _cnt < 4 or (_i - _last) >= 4:
            return False
        if abs(q_hist[_i]) <= eng._qqe_trig_line(_pk) * FLIP_DAMP_M:
            return False
        CNT["damp_blocked"] += 1
        return True


    # ================= 6. standalone 模拟 =================
    o_a = np.asarray(ind["open"], dtype=float)
    h_a = np.asarray(ind["high"], dtype=float)
    l_a = np.asarray(ind["low"], dtype=float)
    c_a = _c
    atr_a = np.asarray(ind["atr"], dtype=float)
    er_a = np.asarray(ind["er"], dtype=float)
    adx_a = np.asarray(ind["adx"], dtype=float)
    slope_a = np.asarray(ind["slope"], dtype=float)
    sm_a = sm
    n = len(dates)
    _day_last = {}
    for _d, _s in zip(dates, sm_a):
        _day_last[_d] = int(_s) + 1

    trades = []          # (eb, xb, "long"/"short", epx, xpx, rsn)
    entry_modes = {}
    marks_by_day = defaultdict(list)   # [bar_in_day, label, kind]
    flips_log = []       # 衰减翻仓执行 (画主图 qf 箭头用)
    hb_log = []          # 回补执行 (画主图 hb 箭头用)
    STATE_LOG = []  # ERQQE: (bar, pos_after) 变更流水
    CNT = dict(decay_registered=0, decay_confirmed=0, decay_voided=0,
               damp_blocked=0, fol_detected=0, fol_actions=0,
               hb_registered=0, hb_executed=0, hb_voided=0)

    pos = 0
    pos_mode = None
    entry_px = entry_atr = None
    entry_bar = -1
    entry_date = None
    sl_level = None
    qsf_pending = None   # (sig_bar, wave_sign, |hist_sig|, act_dir,
                         #  pos_at_reg, entry_bar_at_reg, peak, from_flat)
    qhb_marker = 0.0
    qhb_from = 0
    qhb_eb = -1
    qhb_pending = None   # (trig_bar, |hist_trig|, hb_dir, qf_entry_bar, qf_pos)


    def _mark(bar, ddir, kind):
        gs = day_span[dates[bar]][0]
        lab2 = {"decay_open": "开", "decay_flip": "翻",
                "fol_open": "跟", "fol_flip": "翻", "hb": "补"}[kind]
        marks_by_day[dates[bar]].append(
            [int(bar - gs), lab2 + ("c" if ddir > 0 else "p"), kind])


    def close_pos(bar, px, reason):
        nonlocal pos, pos_mode, entry_px, entry_atr, entry_bar, entry_date, sl_level
        if pos == 0:
            return
        act_bars.append(bar)
        if entry_date is not None and WIN_A <= entry_date <= TODAY:
            trades.append((entry_bar, bar,
                           "long" if pos == 1 else "short",
                           entry_px, px, reason))
        pos = 0
        pos_mode = None
        entry_px = entry_atr = None
        entry_bar = -1
        entry_date = None
        sl_level = None
        STATE_LOG.append((bar, 0))


    def open_pos(direction, mode, bar, kind=None):
        nonlocal pos, pos_mode, entry_px, entry_atr, entry_bar, entry_date, sl_level
        if pos == direction:
            return False
        if pos == -direction:
            close_pos(bar, c_a[bar], "FLIP")
        pos = direction
        pos_mode = mode
        entry_px = c_a[bar]
        entry_atr = atr_a[bar]
        entry_bar = bar
        entry_date = dates[bar]
        entry_modes[bar] = mode
        sl_level = entry_px - SL_K_Q * entry_atr if direction == 1 \
            else entry_px + SL_K_Q * entry_atr
        act_bars.append(bar)
        if kind is not None and WIN_A <= entry_date <= TODAY:
            _mark(bar, direction, kind)
        STATE_LOG.append((bar, int(direction)))
        return True


    for i in range(n):
        dstr = dates[i]
        if dstr >= WIN_A and pos != 0 and (entry_date or "") < WIN_A:
            # 窗口起点平仓重置 (同底盘 START_DATE 边界处理, 不记单)
            pos = 0
            pos_mode = None
            entry_px = entry_atr = None
            entry_bar = -1
            entry_date = None
            sl_level = None
        s = int(sm_a[i])
        close_min = s + 1
        eod = (_day_last[dstr] - 3) <= close_min <= _day_last[dstr]
        # ---- 趋势状态 (EXIT T 用; 同底盘定义) ----
        ind_ok = not (np.isnan(er_a[i]) or np.isnan(adx_a[i])
                      or np.isnan(slope_a[i]) or np.isnan(atr_a[i]))
        strong_up = strong_down = False
        if ind_ok:
            trending = (er_a[i] >= ER_TH_Q) and (adx_a[i] >= ADX_TH_Q)
            strong_down = bool(trending and
                               (slope_a[i] < -TAKEOVER_SLOPE_Q * atr_a[i]))
            strong_up = bool(trending and
                             (slope_a[i] > TAKEOVER_SLOPE_Q * atr_a[i]))
        # ---- 止损: 已停用 (user 2026-11-21 02:29 令, 参考版无止损;
        # sl_level 仍在 open_pos 计算但不再触发任何平仓) ----
        # ---- 强制平 (EOD / EXIT T; 同底盘 forced exit 的两项) ----
        if pos != 0:
            if eod:
                close_pos(i, c_a[i], "EOD")
            elif (strong_down and pos == 1) or (strong_up and pos == -1):
                close_pos(i, c_a[i], "EXIT T")
        # ---- QQE 腿 (本根收尾; 顺序同定版 fork: 衰减待确认 -> 回补
        # 待确认 -> 衰减登记 -> 回补触发评估 -> 跟随) ----
        if WIN_A <= dstr <= TODAY:
            # 1) 衰减待确认
            _pend_in = qsf_pending
            qsf_pending = None
            if _pend_in is not None:
                _pt, _pws, _pha, _pto, _ppos, _peb, _ppk, _pflat = _pend_in
                if dates[_pt] != dstr:
                    CNT["decay_voided"] += 1  # 待翻不跨日
                else:
                    if _pflat:
                        _ident = (pos == 0)
                    else:
                        _ident = (pos_mode in ("qf", "qfol", "qhb")
                                  and pos == _ppos and entry_bar == _peb)
                    if not _ident:
                        CNT["decay_voided"] += 1
                    else:
                        _hv = q_hist[i]
                        _do = False
                        if _hv * _pws < 0:
                            _do = True       # 异号直接认
                        elif abs(_hv) < _pha:
                            _do = True       # 同号更短认; 变长作废
                        if _do and not eod:
                            CNT["decay_confirmed"] += 1
                            if _pflat:
                                open_pos(_pto, "qf", i, kind="decay_open")
                            else:
                                _from_eb = entry_bar
                                _from_dir = pos
                                close_pos(i, c_a[i], "QQE_SF_FLIP")
                                open_pos(_pto, "qf", i, kind="decay_flip")
                                # 回补基准 (同定版: 开仓根 |hist| / 原向)
                                qhb_marker = abs(q_hist[i])
                                qhb_from = int(_from_dir)
                                qhb_eb = i
                                qhb_pending = None
                                flips_log.append(
                                    dict(sig_bar=int(_pt), conf_bar=int(i),
                                         date=dstr,
                                         from_entry_bar=int(_from_eb),
                                         from_dir=int(_from_dir),
                                         flip_to=int(_pto),
                                         peak=float(_ppk)))
                        else:
                            CNT["decay_voided"] += 1
            # 2) 回补待确认 (等一根递增确认, 同定版)
            _hb_in = qhb_pending
            qhb_pending = None
            if _hb_in is not None:
                _ht, _habs, _htdir, _hqeb, _hqpos = _hb_in
                if dates[_ht] != dstr:
                    CNT["hb_voided"] += 1
                elif pos_mode == "qf" and pos == _hqpos \
                        and entry_bar == _hqeb:
                    _hv2 = q_hist[i]
                    if not np.isnan(_hv2) and _hv2 * _htdir > 0 \
                            and abs(_hv2) > _habs:
                        close_pos(i, c_a[i], "QQE_SF_HB")
                        open_pos(_htdir, "qhb", i, kind="hb")
                        CNT["hb_executed"] += 1
                        hb_log.append(
                            dict(trig_bar=int(_ht), exec_bar=int(i),
                                 date=dstr, qf_entry_bar=int(_hqeb),
                                 qf_dir=int(_hqpos), hb_to=int(_htdir),
                                 marker=float(qhb_marker)))
                    else:
                        CNT["hb_voided"] += 1
                else:
                    CNT["hb_voided"] += 1
            # 3) 衰减执行 (user 2026-11-21 14:56 令: 衰减不等次根
            # 确认、信号根当根就翻/开; 减震门保留。旧「登记 -> 次根
            # 确认」版见 .bak-20261121-decaynow。例外 (user 2026-11-21
            # 17:25 令): 衰减翻仓信号根在波内前 5 根时改走登记待确认
            # (步骤 1 机制复活, 仅这类翻仓); 开仓与第 6 根起的翻仓
            # 仍即时。回补仍等一根递增确认, 不动。)
            _sig = None
            _pk = 0.0
            if i in q_peak_red and i in q_peak_blue:
                _sig, _pk = "blue", q_peak_blue[i]
            elif i in q_peak_red:
                _sig, _pk = "red", q_peak_red[i]
            elif i in q_peak_blue:
                _sig, _pk = "blue", q_peak_blue[i]
            if _sig is not None and _pk >= PEAK_MIN and not eod:
                _wsign = 1 if _sig == "blue" else -1
                if pos != 0 and entry_bar < i and _wsign == pos:
                    # 持自家仓、衰减波与仓位同向 -> 翻仓 (减震门)。
                    # user 2026-11-21 17:25 令: 信号根在波内前 5 根
                    # (WAVE_POS<=5) 的衰减翻仓改登记待确认、走步骤 1
                    # 的次根确认; 第 6 根起仍当根即时翻。
                    if not _sf_flip_blocked(pos, i, _pk):
                        if False:  # ERQQE spec: 衰减翻仓恒当根, 不走待确认
                            qsf_pending = (i, _wsign, abs(q_hist[i]),
                                           -pos, pos, entry_bar, _pk,
                                           False)
                            CNT["decay_registered"] += 1
                        else:
                            _from_eb = entry_bar
                            _from_dir = pos
                            _to = -pos
                            close_pos(i, c_a[i], "QQE_SF_FLIP")
                            open_pos(_to, "qf", i, kind="decay_flip")
                            qhb_marker = abs(q_hist[i])
                            qhb_from = int(_from_dir)
                            qhb_eb = i
                            qhb_pending = None
                            flips_log.append(
                                dict(sig_bar=int(i), conf_bar=int(i),
                                     date=dstr,
                                     from_entry_bar=int(_from_eb),
                                     from_dir=int(_from_dir),
                                     flip_to=int(_to),
                                     peak=float(_pk)))
                            CNT["decay_confirmed"] += 1
                elif pos == 0:
                    # 空仓 -> 当根开 (方向 = 衰减波反向; 开仓不走减震)
                    open_pos(-_wsign, "qf", i, kind="decay_open")
                    CNT["decay_confirmed"] += 1
            # 4) 回补触发评估 (只在持 qf 仓、同一性成立时, 同定版)
            if qhb_pending is None and pos_mode == "qf" \
                    and entry_bar == qhb_eb and entry_bar < i \
                    and qhb_marker > 0:
                if _hb_trigger(i, qhb_from, qhb_marker):
                    _qeb = entry_bar
                    close_pos(i, c_a[i], "QQE_SF_HB")
                    open_pos(qhb_from, "qhb", i, kind="hb")
                    CNT["hb_executed"] += 1
                    hb_log.append(
                        dict(trig_bar=int(i), exec_bar=int(i), date=dstr,
                             qf_entry_bar=int(_qeb), qf_dir=int(-qhb_from),
                             hb_to=int(qhb_from), marker=float(qhb_marker)))
            # 5) 跟随 (EOD 根不开, 同定版)
            if not eod:
                _fd = _qfol_dir(i)
                if _fd != 0:
                    FOL_DETECT.append((int(i), int(_fd)))
                    CNT["fol_detected"] += 1
                    if pos == 0:
                        open_pos(_fd, "qfol", i, kind="fol_open")
                        CNT["fol_actions"] += 1
                    elif pos_mode == "sf" and entry_bar < i \
                            and _fd == -pos:
                        pass  # 本视图无 sf 仓 (分支与定版同构保留)
                    elif pos_mode in ("qf", "qfol") and _fd == -pos \
                            and entry_bar < i:
                        # entry_bar<i 守卫 (2026-11-21 单根跟随改版加):
                        # 同根刚由衰减确认开的仓不被跟随同根翻回 (与衰减
                        # 登记/ sf 分支的 entry_bar<i 惯例一致), 否则会
                        # 产生零时长 qf 单且回补基准被同根翻仓废掉。
                        close_pos(i, c_a[i], "QQE_FOL_FLIP")
                        open_pos(_fd, "qfol", i, kind="fol_flip")
                        CNT["fol_actions"] += 1

    _state = {}
    for _b, _d in STATE_LOG:
        _state[int(_b)] = int(_d)
    qstate = np.zeros(n, dtype=int)
    for _b, _d in _state.items():
        qstate[_b] = _d
    for _k in range(1, n):
        if _k not in _state:
            qstate[_k] = qstate[_k - 1]
    return qstate
