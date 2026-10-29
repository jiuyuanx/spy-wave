#!/usr/bin/env python3
"""
Signal engine for watcher_v4 — EXTRACTED from gen_week_k3_clean.py (backtest).

100% identical signal logic to the backtest. Stateless: given bars, returns actions.
No capital/P&L/dashboard — signals only.

actions format: (bar_idx, time_str, action, direction, price, reason)
  action: "开" (open) | "平" (close) | "持" (hold-marker, e.g. 3K wait start)
  direction: "call" | "put"
"""
import pandas as pd
import numpy as np

# 热力区边缘缓存（供dashboard取用）
_heat_edges_cache = []
_heat_arr_cache = None
_heat_sm_cache = None
_heat_bins_cache = None
def get_heat_edges():
    """返回最近一次run_signals的heat_edges: [(tstr, up, lo, in_heat, disp_up, disp_lo), ...]"""
    return _heat_edges_cache
def get_heat_arr():
    """返回热力数组（1-9999范围，0.5档）"""
    return _heat_arr_cache
def get_heat_bins():
    """返回热力bins"""
    return _heat_bins_cache
def get_heat_sm():
    """返回blur过的final heat数组（信号实际用的）"""
    return _heat_sm_cache
import sys

sys.path.insert(0, "/home/hatch/workspace/qqe_backtest")
sys.path.insert(0, "/home/hatch/workspace/alpaca_watcher")
from qqe_mod import qqe_mod

SIG_MIN_LEN = 4
_SKIP_DATES = {"2026-06-17"}  # Warsh FOMC, permanently excluded


# ---------- detect functions (from watcher_v3, pure) ----------
def _detect_peak_pullback(hist, mask, opp_mask, lookback=10, rise_mult=2.0,
                          pullback=0.8, min_peak=6, target_sign=1, skip_rise_mult=False,
                          day=None):
    """峰值回落衰减: 从当前往回找连续同色段 (遇真反色或空柱断开, 最多 lookback 根),
    段内同色峰值>=min_peak 且 >= 段内最小*2 ("涨起来了"), 当前回落到峰值*0.8 -> 信号。
    灰柱: 段内可计入目标色用于算峰值 (须与目标色同号), 触发柱可以是真色或灰柱
    (|hist|>=SIG_MIN_LEN) (2026-10-06 用户: "灰柱子感觉比蓝柱子更算衰减")。
    2026-10-14: lookback 6→10 (LB10)。
    skip_rise_mult: 早盘跳过"2倍"检查 (2026-10-01 用户: 早盘一上来就很长)
    target_sign: +1 目标为蓝柱(hist>0), -1 目标为红柱(hist<0)。
    day: 可选, 与hist等长的日期数组; 回看不跨日 (2026-10-02 用户: "这里蓝色哪有衰减?"
    周一早盘拿上周五的峰值误触发)"""
    n = len(hist)
    out = []
    i = 300
    while i < n:
        cur = hist[i]
        ca = abs(cur) if not np.isnan(cur) else 0
        # 从 i 往回找连续段: 遇真反色或空柱(|hist|<=3)断开 (2026-10-01: 空柱断开, 不连前高)
        # 2026-10-02: 遇跨日断开, 不拿昨天的峰值
        lo = max(300, i - lookback)
        seg_lo = lo
        for j in range(i, lo - 1, -1):
            cj = hist[j]; caj = abs(cj) if not np.isnan(cj) else 0
            if day is not None and j < i and day[j] != day[i]:
                seg_lo = j + 1
                break
            if bool(opp_mask[j]) or caj <= 3 or np.isnan(cj):
                seg_lo = j + 1
                break
        # 段内颜色主导 (段内无真反色, 只需目标色存在)
        n_target = 0
        for j in range(seg_lo, i + 1):
            if bool(mask[j]): n_target += 1
        target_dominant = n_target > 0
        def _is_target(j):
            if bool(mask[j]): return True
            # 灰柱永不当峰值 (2026-10-08 用户: 否掉中间版, 恢复原始规则)
            return False
        # 2026-10-06 用户: 灰柱触发不需要 |hist|>=4，只要衰减到80%以下就行
        _is_gray_trigger = not bool(mask[i])
        if not _is_gray_trigger and ca < SIG_MIN_LEN:
            i += 1; continue
        peaks = []
        base_min = 999
        for j in range(seg_lo, i + 1):
            cj = hist[j]
            caj = abs(cj) if not np.isnan(cj) else 0
            if caj < base_min:
                base_min = caj
            if _is_target(j) and caj > 3:
                peaks.append(caj)
        if not peaks:
            i += 1; continue
        peak = max(peaks)
        if ca >= peak:
            i += 1; continue  # 当前就是峰值, 不算回落
        # "涨起来"判定 (2026-10-02 用户): 2倍 OR 3根连续高柱(|hist|>=8) OR 3根连续递增(动能强)
        # 早盘宽松版: 2根连续、第二根更长即可 (2026-10-02 用户: "一上来就很长，第二根更长再进")
        _tall3 = False; _mom3 = False; _mom2 = False
        for j in range(seg_lo, i):
            if j+2 > i: break
            _c0 = abs(hist[j]) if not np.isnan(hist[j]) else 0
            _c1 = abs(hist[j+1]) if not np.isnan(hist[j+1]) else 0
            _c2 = abs(hist[j+2]) if not np.isnan(hist[j+2]) else 0
            if _is_target(j) and _is_target(j+1) and _is_target(j+2):
                if _c0 >= 8 and _c1 >= 8 and _c2 >= 8:
                    _tall3 = True
                if _c2 > _c1 > _c0:
                    _mom3 = True
            if _is_target(j) and _is_target(j+1) and _c1 > _c0:
                _mom2 = True
        if skip_rise_mult:
            _rise_ok = _mom2 or _tall3 or _mom3
        else:
            _rise_ok = (peak >= max(base_min, 3) * rise_mult) or _tall3 or _mom3
        # 衰减需同时满足: <=80%峰值 且 <=90%前一根 (2026-10-02 用户)
        # 2026-10-07 用户: 去掉"涨起来了"条件
        _prev_ca = abs(hist[i-1]) if i-1 >= seg_lo and not np.isnan(hist[i-1]) else 999
        _decay_ok = ca <= peak * pullback and ca <= _prev_ca * 0.9
        if peak >= min_peak and _decay_ok:
            out.append((i, peak))
        i += 1
    return out

def detect_red_decay(hist, down, up=None, skip_rise_mult=False, day=None):
    """红衰减 -> 开call: 红柱(含主导窗口内的灰柱)涨起来后从峰值回落。"""
    opp = up if up is not None else [False]*len(hist)
    return _detect_peak_pullback(hist, down, opp, target_sign=-1, skip_rise_mult=skip_rise_mult, day=day)

def detect_blue_decay(hist, up, down=None, skip_rise_mult=False, day=None):
    """蓝衰减 -> 开put: 蓝柱(含主导窗口内的灰柱)涨起来后从峰值回落。"""
    opp = down if down is not None else [False]*len(hist)
    return _detect_peak_pullback(hist, up, opp, target_sign=+1, skip_rise_mult=skip_rise_mult, day=day)

def count_lengthening(hist, cur_idx, direction, q_up, q_down):
    """数当前连续变长 bar 数 (只认真红/真蓝，且长度>=SIG_MIN_LEN)。
    灰柱跳过不计数不断裂; 空柱(|hist|<=3)或反向色则断裂。
    direction='red' 用 down, 'blue' 用 up。"""
    count = 0
    j = cur_idx
    prev_abs = None
    while j > 0:
        cur = hist[j]
        cur_abs = abs(cur)
        # 信号级真红/真蓝: 长度>=SIG_MIN_LEN
        is_up_sig = bool(q_up[j]) and cur_abs >= SIG_MIN_LEN
        is_down_sig = bool(q_down[j]) and cur_abs >= SIG_MIN_LEN
        # 基础颜色判断 (用于灰/空识别)
        is_up = bool(q_up[j]) and cur_abs > 3
        is_down = bool(q_down[j]) and cur_abs > 3
        is_gray = (not is_up and not is_down and cur_abs > 3)
        is_empty = cur_abs <= 3 or np.isnan(cur)
        want_red = (direction == "red")
        is_want = is_down_sig if want_red else is_up_sig
        is_opposite = is_up_sig if want_red else is_down_sig

        if is_gray:
            j -= 1
            continue  # 灰柱跳过
        if is_empty or is_opposite:
            break  # 空柱或反向信号色断裂
        if not is_want:
            break  # 非信号级长度，断裂
        # 信号级真红/真蓝
        if prev_abs is None:
            prev_abs = cur_abs
            j -= 1
            continue
        if cur_abs < prev_abs:
            # 往回找时变小 = 时间正向变长
            count += 1
            prev_abs = cur_abs
        else:
            break
        j -= 1
    return count



def run_signals(bars_df, start_idx=None, end_idx=None, use_30s_stop=False, m1_dict=None,
                div_thresh=0.2, div_qqe_gate=False, stop30s_y=0.5, eod_1m_dict=None,
                heat_signals=True):
    """Run backtest signal logic on bars. Returns actions list.

    bars_df: DataFrame with t, open/high/low/close (or o/h/l/c)
    start_idx/end_idx: bar range to simulate (default: full)
    eod_1m_dict: {date_str: 15:55 1m close} for EOD precise flat (optional)
    heat_signals: False=展示冷冻版: 热力区不发做T信号、不跳过QQE (热力计算照常, 供get_heat_*显示); True=默认原行为
    Returns: [(bar_idx, time_str, action, direction, price, reason), ...]
    """
    import os as _os
    _USE_30S_STOP = use_30s_stop
    _30S_THRESH = stop30s_y
    _M1_DICT = m1_dict or {}
    _DIV_THRESH = div_thresh
    _DIV_QQE_GATE = div_qqe_gate
    _eod_1m = eod_1m_dict or {}
    # ---------- indicators ----------
    global _heat_edges_cache, _heat_arr_cache, _heat_bins_cache, _heat_sm_cache
    _heat_edges_cache = []  # 每次run_signals清空
    _heat_sm_cache = None
    # _heat_arr_cache/_heat_bins_cache在循环结束时更新（取最后一天的值）
    warm = bars_df.reset_index(drop=True).copy()
    # normalize column names: accept t/o/h/l/c or t/open/high/low/close
    _colmap = {"o": "open", "h": "high", "l": "low", "c": "close"}
    for _k, _v in _colmap.items():
        if _k in warm.columns and _v not in warm.columns:
            warm[_v] = warm[_k]
    if "t" not in warm.columns and "time" in warm.columns:
        warm["t"] = warm["time"]
    warm["t"] = pd.to_datetime(warm["t"], utc=True)
    try:
        from zoneinfo import ZoneInfo
        warm["t"] = warm["t"].dt.tz_convert(ZoneInfo("America/New_York"))
    except Exception:
        pass
    close = warm["close"].values.astype(float)
    high = warm["high"].values.astype(float)
    low = warm["low"].values.astype(float)
    res = qqe_mod(close)
    hist = np.asarray(res["hist"], dtype=float)
    q_up = np.asarray(res["up"]).astype(bool)
    q_down = np.asarray(res["down"]).astype(bool)
    n = len(hist)
    dates = warm["t"].dt.date.astype(str).values
    _day_arr = warm["t"].dt.date.values
    # decay triggers (full pass)
    trig_red = detect_red_decay(hist, q_down, q_up, day=_day_arr)
    trig_blue = detect_blue_decay(hist, q_up, q_down, day=_day_arr)
    trig_red_m = detect_red_decay(hist, q_down, q_up, skip_rise_mult=True, day=_day_arr)
    trig_blue_m = detect_blue_decay(hist, q_up, q_down, skip_rise_mult=True, day=_day_arr)
    peak_red = {idx: pk for idx, pk in trig_red}
    peak_blue = {idx: pk for idx, pk in trig_blue}
    peak_red_m = {idx: pk for idx, pk in trig_red_m}
    peak_blue_m = {idx: pk for idx, pk in trig_blue_m}
    set_red = set(peak_red.keys())
    set_blue = set(peak_blue.keys())

    # ---------- 9mK聚合 + cumslope + 红侧反弹惩罚 (2026-10-17 红0.05上线) ----------
    # CUMSLOPE = (上一根走完9m收 - 当日首根9m收) / 已走完根数, _prev>=1
    # 强下跌日(cumslope < -0.05)红侧翻仓(put→call)加惩罚: need *= min(1,max(0.2,2.0*rebound_rate))
    _REBOUND_SLOPE = 2.0
    _CUM_SLOPE_TH = 0.05
    _9m_h4 = np.full(n, np.nan)
    _9m_l4 = np.full(n, np.nan)
    _9m_cumslope = np.full(n, np.nan)
    _wt = warm["t"]
    _wdate = _wt.dt.date.astype(str).values
    for _d in sorted(set(_wdate)):
        _idx = np.where(_wdate == _d)[0]
        if len(_idx) < 6:
            continue
        _t0 = _wt.iloc[_idx[0]]
        _day0 = _t0.replace(hour=9, minute=30, second=0, microsecond=0)
        _k9 = np.array([int((_wt.iloc[_j] - _day0).total_seconds() // 540) for _j in _idx])
        _maxk = _k9.max()
        _9h, _9l, _9c = {}, {}, {}
        for _kk in range(_maxk + 1):
            _m = _idx[_k9 == _kk]
            if len(_m) == 0:
                continue
            _9h[_kk] = float(high[_m].max())
            _9l[_kk] = float(low[_m].min())
            _9c[_kk] = float(close[_m[-1]])
        for _ii, _j in enumerate(_idx):
            _kk = _k9[_ii]
            _prev = _kk - 1
            _kk4 = [_prev - 3, _prev - 2, _prev - 1, _prev]
            if all(_k in _9h for _k in _kk4):
                _9m_h4[_j] = max(_9h[_k] for _k in _kk4)
                _9m_l4[_j] = min(_9l[_k] for _k in _kk4)
        if 0 in _9c:
            _open_c = _9c[0]
            for _ii, _j in enumerate(_idx):
                _kk = _k9[_ii]
                _prev = _kk - 1
                if _prev >= 1 and _prev in _9c:
                    _9m_cumslope[_j] = (_9c[_prev] - _open_c) / _prev
    def _rebound_factor_red(_i):
        _cs = _9m_cumslope[_i]
        if np.isnan(_cs) or _cs >= -_CUM_SLOPE_TH:
            return 1.0
        if np.isnan(_9m_h4[_i]) or np.isnan(_9m_l4[_i]):
            return 1.0
        _rng = _9m_h4[_i] - _9m_l4[_i]
        if _rng <= 0:
            return 1.0
        _rate = (float(close[_i]) - _9m_l4[_i]) / _rng
        return min(1.0, max(0.2, _REBOUND_SLOPE * _rate))

    # ---------- state ----------
    actions = []  # (bar_idx, time_str, action, direction, price, reason)
    pos = None  # None | 'call' | 'put'
    last_action_bar = -999
    _streak_block_until = -999
    _resume_block_put_until = -999
    _resume_block_call_until = -999
    _resume_block_put_peak = 0
    _stall_reopen_dir = None  # 停滞平仓后重开方向 ("call"/"put"/None)
    _stall_reopen_hist = 0  # 停滞时的|hist|
    _stall_reopen_ws = -1  # 停滞时的波浪起点
    _stall_reopen_bar = -1  # 停滞时的bar下标
    _resume_block_call_peak = 0
    _entry_peak = 0
    _decay_entry_hist = 0
    _follow_entry_bar = None  # 跟随开仓的bar, 下一根缩80%即平 (2026-10-03 用户)
    _follow_do_div_wk = True  # 波浪5根内才查背离 (2026-10-03 用户)
    _DIV_THRESH = float(_os.environ.get("DIV_THRESH", "0.2"))  # 跟随背离止损阈值(点); sweep用, 默认0.2
    _DIV_QQE_GATE = _os.environ.get("DIV_QQE_GATE", "0") == "1"  # QQE还在加强则不触发背离平 (sweep变体)
    _NO_DASH = _os.environ.get("NO_DASH", "0") == "1"  # sweep时不写dashboard文件
    _USE_30S_STOP = _os.environ.get("USE_30S_STOP", "0") == "1"  # 30秒提前止损 (2026-10-07): 1m逆向>0.5即平, 默认关
    _30S_THRESH = float(_os.environ.get("STOP30S_Y", "0.5"))  # 30秒止损阈值(点)
    _follow_entry_hist = 0
    # 碎波暂停 (2026-10-04 用户)
    _frag_pause_until_wk = -1
    _follow_pnls_wk = []  # list of (dir, peak, pnl, date)
    _follow_entry_dir_wk = None
    _follow_entry_peak_wk = 0
    _follow_entry_spy_wk = 0
    _follow_entry_date_wk = ""
    # 期权止损20%建模 (2026-10-04 用户): 跟踪期权价值, 跌破80%即平, 不设止盈
    _stop_entry_dir = None
    _stop_entry_px = 0
    _stop_entry_bar = -1
    # 止损窗口 (s20fz新baseline 2026-10-29): 20%触发、6根窗口、等50%回弹平、窗口内冻结
    _stop_win = None  # None=无窗口; 有窗口时为dict(dr, trig_bar, entry_px, trig_close, threshold)
    _stop_triggered = False
    # 大K双机制 (2026-10-09 用户): 等反弹+不跟随
    _bigwait_target = None
    _bigwait_bars = 0
    _bigwait_is_put = False
    # 大K止盈 (2026-10-09 用户): ≥4.0点顺向大K → 3根后止盈平仓
    _BIGTP_N = 3
    _bigtp_bar = None
    _bigtp_count = 0
    # 3-bar大K (空头版, 宽松, XX=2.5; 2026-10-11): 与单根大K完全独立的变量, k3_前缀
    _K3_THRESH = 2.5
    _K3_TP_N = 3
    k3_wait_target = None
    k3_wait_bars = 0
    k3_wait_is_put = False
    k3_wait_count = 0
    k3_tp_bar = None
    k3_tp_count = 0
    k3_skip_count = 0
    # 1分钟数据 (2026-10-07 30秒止损用): {(date_str, "HH:MM"): (o,h,l,c)}
    _M1_DICT = {}
    if _USE_30S_STOP:
        try:
            _m1df = pd.read_csv("/home/hatch/workspace/qqe_backtest/spy_1m.csv")
            _m1df["t"] = pd.to_datetime(_m1df["time"], utc=True).dt.tz_convert(ET)
            for _r in _m1df.itertuples():
                _M1_DICT[(_r.t.strftime("%Y-%m-%d"), _r.t.strftime("%H:%M"))] = (float(_r.open), float(_r.high), float(_r.low), float(_r.close))
            print(f"1m bars loaded: {len(_M1_DICT)}", flush=True)
        except Exception as _e:
            print(f"1m load failed: {_e}", flush=True)
    _hold_watch = None  # HOLD后跟踪: {peak, need, dir, bar}, 衰减加深则翻 (2026-10-03)
    _fav_peak = 0  # 动量死亡跟踪: 同向波峰值(含灰) (2026-10-03 用户)
    _fav_sign = 0  # 1=call/-1=put
    _recent_flips = []  # 反复翻仓惩罚: 最近flip的bar (2026-10-03 用户)
    _put_blue_ws = None  # 翻仓开put时的蓝波起点, 防止同波"蓝出现"误杀
    _call_red_ws = None  # 翻仓开call时的红波起点, 防止同波"红出现"误杀
    _call_gap_wk = 0  # (2026-10-06: 持call时2根超短/空柱后算新波，与watcher.py同步)
    _put_gap_wk = 0  # (2026-10-06: 持put时2根超短/空柱后算新波，与watcher.py同步)
    was_on_top = False
    was_on_bottom = False
    _was_above_zero_wk = False  # 2026-10-07 用户: 波浪管理 - call持仓期间是否上过≥0
    _is_red_flip_wk = False  # 2026-10-07 用户: 是否红波翻仓带出的call
    _was_below_zero_wk = False  # 2026-10-07 用户: 波浪管理 - put持仓期间是否下过≤0
    wait_red = False
    wait_blue = False
    wait_red_count = 0
    wait_blue_count = 0

    # ---------- helpers ----------
    def is_true_blue(j):
        return bool(q_up[j]) and abs(hist[j]) >= SIG_MIN_LEN
    def is_true_red(j):
        return bool(q_down[j]) and abs(hist[j]) >= SIG_MIN_LEN
    # 2026-10-05 用户: 同波判断, 空柱(|h|<=3)断波, 灰柱不断, 只有真反色或空柱切断
    def _ws_cont_blue(j):
        return abs(hist[j]) > 3 and not is_true_red(j)
    def _ws_cont_red(j):
        return abs(hist[j]) > 3 and not is_true_blue(j)
    def is_exit_red(j):
        return bool(q_down[j]) and abs(hist[j]) >= 6
    def is_exit_blue(j):
        return bool(q_up[j]) and abs(hist[j]) >= 6
    def prev_wave_color_gw(j, max_lookback=40):
        """往回看找上一个有意义的波浪 (2026-10-03 用户: 续波跟随)。
        (2026-10-07 用户: 最近4根里有|h|<3短柱也算波断=新波),
        跳过最近4根, 往回找第一根 |h|>=8, (2026-10-07 用户: 碰到|h|<3短柱=波断=新波直接返回None),
        返回 'red'/'blue'/None. 不跨天."""
        # 2026-10-07 用户: 先检查最近4根有没有短柱
        for k in range(j - 1, max(j - 5, -1), -1):
            if k < 0 or k >= len(hist):
                break
            try:
                if _day_arr[k] != _day_arr[j]:
                    break
            except Exception:
                pass
            if abs(hist[k]) < 3:
                return None
        for k in range(j - 4, max(-1, j - 4 - max_lookback), -1):
            if k < 0 or k >= len(hist):
                break
            try:
                if _day_arr[k] != _day_arr[j]:
                    break
            except Exception:
                pass
            h = hist[k]
            if abs(h) < 3:
                return None  # 2026-10-07 用户: 短柱=波断=新波
            if abs(h) <= 3:
                continue
            if abs(h) >= 8:
                return 'red' if h < 0 else 'blue'
        return None
    def wave_len_gw(j, sign, max_lookback=30):
        """从 j 往回数 sign 方向波的长度 (2026-10-03 用户: 长波优化)。
        sign=-1数红波, sign=1数蓝波. |h|<=3最多容忍连续2根, 真反色即断.
        不跨交易日 (2026-10-03 用户反馈08-28)."""
        n = 0
        _cs = 0
        for k in range(j, max(-1, j - max_lookback), -1):
            if k < 0 or k >= len(hist):
                break
            try:
                if _day_arr[k] != _day_arr[j]:
                    break
            except Exception:
                pass
            h = hist[k]
            if np.isnan(h):
                break
            # 2026-10-07 用户: |h|<3短柱直接断波, 不容忍
            if abs(h) < 3:
                break
            if h * sign > 0 and abs(h) > 3:
                n += 1
                _cs = 0
            elif abs(h) <= 3 and h * sign >= 0:
                # (2026-10-07 fix: 零轴下方的小柱子也算断波)
                _cs += 1
                if _cs > 2:
                    break
            else:
                break
        return n

    # ---------- stop vars ----------
    def _init_stop_vars(_pos, _entry_px, _bar_i):
        nonlocal _stop_entry_dir, _stop_entry_px, _stop_entry_bar, _stop_win
        _stop_entry_dir = _pos
        _stop_entry_px = _entry_px
        _stop_entry_bar = _bar_i
        _stop_win = None  # 新仓位一律无窗口 (s20fz: 翻仓/跟随开仓都走这里, 旧窗口绝不带到新仓位)

    # ---------- loop setup ----------
    start_idx = 0 if start_idx is None else start_idx
    # 周末尾bar (2026-10-04 fix: 之前跑到数据末尾去了)
    if end_idx is None:
            end_idx = n
    _day_start = start_idx
    _prev_day = None
    # 2026-10-08 fix: 开盘gap ignore每天只算一次
    _day_ign_n = 1
    _day_ign_day = -1
    # 2026-10-08 用户: 躲鞭 - 延迟翻仓只一次
    _pending_flip_to = None
    _pending_flip_bar = -1
    _pending_flip_reason = ""
    _last_delay_bar = -10
    # 躲鞭统计计数器 (2026-10-08 分析用)
    _n_delay = 0
    _n_confirm = 0
    _n_cancel = 0
    # 止损变量即时初始化 (2026-10-11 fix: 开仓当根立即用真实入场价初始化, 不再延迟到下一根bar)

    # ---------- 做T热力规则初始化 (2026-10-23, pierce2宽版上线) ----------
    import numpy as _np
    try:
        from scipy.ndimage import gaussian_filter1d as _gf1d
        _heat_has_scipy = True
    except:
        _heat_has_scipy = False
    _HEAT_DECAY = 0.98
    _HEAT_W_RE = 1.6
    _HEAT_W_DW = 0.8
    _HEAT_BIN_W = 0.5
    _HEAT_THRESH = 7.0
    _HEAT_EDGE_PCT = 0.15
    _heat_arr = None
    _heat_bins = None
    _heat_nz = 0
    _heat_prev_zi = None
    _heat_plo = None
    _heat_phi = None
    _heat_rule_last_day = None
    _pos_in_heat = False  # 当前持仓是否在热力区开的

    # ---------- main signal loop (from backtest) ----------
    for i in range(start_idx, end_idx):
        t = warm["t"].iloc[i]
        if t.strftime("%Y-%m-%d") in _SKIP_DATES:
            continue
        day = t.strftime("%m-%d")
        hm = t.strftime("%H:%M")
        if day != _prev_day:
            _day_start = i
            _prev_day = day
            _stop_win = None  # 止损窗口不跨日 (s20fz)
            # 碎波暂停不跨天 (2026-10-07 fix)
            _frag_pause_until_wk = -1
            _follow_pnls_wk = []
            _follow_entry_dir_wk = None
            # 开盘gap ignore (2026-10-08 用户, 回测最优): gap>2.0则ignore前2根, 否则1根
            # 2026-10-08 fix: 每天只算一次, 不能每根bar重算 (第二根bar的gap是盘中价差)
            if i == _day_start and i > 0:
                _gap = abs(float(warm["open"].iloc[i]) - float(close[i-1]))
                _day_ign_n = 2 if _gap > 2.0 else 0
                _day_ign_day = _day_start
            elif _day_ign_day != _day_start:
                _day_ign_n = 1
            _ign_n_wk = _day_ign_n
        # 开盘ignore: 前N根跳过所有信号
        if i - _day_start < _ign_n_wk:
            continue
        px = float(close[i])
        # EOD顶级优先 (回测同款次序: EOD在窗口之前; 热力段EOD只在7区内, 窗口期7区外会漏, 故窗口前再加一道)
        if hm >= "15:55" and pos is not None:
            _eod_px = _eod_1m.get(str(_day_arr[i]), px)
            actions.append((i, f"{day}T{hm}", "平", pos, _eod_px, "日终平仓(1m15:55)"))
            pos = None; _pos_in_heat = False
            last_action_bar = i
            _was_above_zero_wk = False; _was_below_zero_wk = False
            _call_gap_wk = 0; _put_gap_wk = 0
            was_on_top = was_on_bottom = False
            wait_red = wait_blue = False
            _follow_entry_bar = None
            _hold_watch = None
            _fav_peak = 0; _fav_sign = 0
            _stop_win = None
            continue
        # 止损窗口 (s20fz新baseline 2026-10-29: 20%触发、6根窗口、等50%回弹平、窗口内冻结)
        #   窗口内: 回弹平/到期平为唯二出口, 其余信号全跳过 (冻结, 含热力做T信号);
        #   触发见止损检查段; 任何开仓/平仓/EOD/跨日都清窗口 (开仓清见_init_stop_vars, 跨日清见日边界)
        if _stop_win is not None and pos is None:
            _stop_win = None  # 安全网: 仓位已无则窗口作废
        if _stop_win is not None and pos is not None:
            _win_age = i - _stop_win["trig_bar"]
            if _win_age >= 1:
                _wdr = _stop_win["dr"]; _wth = _stop_win["threshold"]
                if (_wdr == "call" and px >= _wth) or (_wdr == "put" and px <= _wth):
                    actions.append((i, f"{day}T{hm}", "平", pos, px, f"止损窗口回50%平"))
                    pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; _call_gap_wk = 0; _put_gap_wk = 0; last_action_bar = i; was_on_top = was_on_bottom = False
                    wait_red = wait_blue = False
                    _follow_entry_bar = None
                    _hold_watch = None
                    _fav_peak = 0; _fav_sign = 0
                    _stop_entry_dir = None
                    k3_tp_bar = None; _bigtp_bar = None; _bigwait_target = None; _bigwait_bars = 0; k3_wait_target = None; k3_wait_bars = 0
                    _stop_win = None
                    _stop_triggered = True
                    continue
                if _win_age <= 6:
                    actions.append((i, f"{day}T{hm}", "持", pos, px, "止损窗口(续)"))
                    _stop_triggered = True
                    if _win_age == 6:
                        actions.append((i, f"{day}T{hm}", "平", pos, px, "止损窗口6根平"))
                        pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; _call_gap_wk = 0; _put_gap_wk = 0; last_action_bar = i; was_on_top = was_on_bottom = False
                        wait_red = wait_blue = False
                        _follow_entry_bar = None
                        _hold_watch = None
                        _fav_peak = 0; _fav_sign = 0
                        _stop_entry_dir = None
                        k3_tp_bar = None; _bigtp_bar = None; _bigwait_target = None; _bigwait_bars = 0; k3_wait_target = None; k3_wait_bars = 0
                        _stop_win = None
                        _stop_triggered = True
                        continue
                    continue
                if _win_age >= 7:
                    actions.append((i, f"{day}T{hm}", "平", pos, px, "止损窗口6根平(补)"))
                    pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; _call_gap_wk = 0; _put_gap_wk = 0; last_action_bar = i; was_on_top = was_on_bottom = False
                    wait_red = wait_blue = False
                    _follow_entry_bar = None
                    _hold_watch = None
                    _fav_peak = 0; _fav_sign = 0
                    _stop_entry_dir = None
                    k3_tp_bar = None; _bigtp_bar = None; _bigwait_target = None; _bigwait_bars = 0; k3_wait_target = None; k3_wait_bars = 0
                    _stop_win = None
                    _stop_triggered = True
                    continue
        # === 做T热力规则 (2026-10-23 pierce2宽版): 7区内优先做T，跳过QQE ===
        _cur_day_str = t.strftime("%Y-%m-%d")
        # heat只累积当天09:30之后的bar，之前的跳过（2026-10-23 fix）
        _hm = t.strftime("%H:%M")
        _skip_heat = _hm < "09:30"
        if _heat_plo is None or _cur_day_str != _heat_rule_last_day:
            _heat_arr = None
            _heat_prev_zi = None
            _heat_rule_last_day = _cur_day_str
        if _heat_arr is None:
            # 固定大范围1-9999（2026-10-23 fix：bins永远对齐，不用动态算）
            _heat_plo = 1.0
            _heat_phi = 9999.0
            _heat_bins = _np.arange(_heat_plo, _heat_phi, _HEAT_BIN_W)
            _heat_nz = len(_heat_bins) - 1
            _heat_arr = _np.zeros(_heat_nz)
        if not _skip_heat:
            _heat_arr *= _HEAT_DECAY
            _czi = int(_np.digitize(float(close[i]), _heat_bins)) - 1
            if 0 <= _czi < _heat_nz:
                if _heat_prev_zi is not None and _czi != _heat_prev_zi:
                    _heat_arr[_czi] += _HEAT_W_RE
                else:
                    _heat_arr[_czi] += _HEAT_W_DW
            _heat_prev_zi = _czi if 0 <= _czi < _heat_nz else None
        if _heat_has_scipy:
            _heat_sm = _gf1d(_heat_arr, sigma=1.0, mode='constant', cval=0)
        else:
            _heat_sm = _heat_arr
        _in_heat_zone = False
        _t_sig_dir = None
        _cpx = float(close[i])
        _opx = float(warm["open"].iloc[i]); _hpx = float(warm["high"].iloc[i]); _lpx = float(warm["low"].iloc[i])
        _close_zi = int(_np.digitize(_cpx, _heat_bins)) - 1
        _orig_up, _orig_lo = None, None
        if 0 <= _close_zi < _heat_nz and _heat_sm[_close_zi] >= _HEAT_THRESH:
            _lo, _hi = _close_zi, _close_zi
            while _lo > 0 and _heat_sm[_lo-1] >= _HEAT_THRESH:
                _lo -= 1
            while _hi < _heat_nz-1 and _heat_sm[_hi+1] >= _HEAT_THRESH:
                _hi += 1
            _orig_up = float(_heat_bins[_hi+1])
            _orig_lo = float(_heat_bins[_lo])
        if _orig_up is None:
            for _zi in range(_heat_nz):
                if _heat_sm[_zi] >= _HEAT_THRESH:
                    _tlo, _thi = _zi, _zi
                    while _tlo > 0 and _heat_sm[_tlo-1] >= _HEAT_THRESH: _tlo -= 1
                    while _thi < _heat_nz-1 and _heat_sm[_thi+1] >= _HEAT_THRESH: _thi += 1
                    _tu = float(_heat_bins[_thi+1]); _tl = float(_heat_bins[_tlo])
                    _th = _tu - _tl
                    _exp_up = _tu + 0.15 * _th; _exp_lo = _tl - 0.15 * _th
                    if _exp_lo <= _cpx <= _exp_up:
                        _orig_up, _orig_lo = _tu, _tl
                        break
        if _orig_up is not None:
            _ztop, _zbot = _orig_up, _orig_lo
            _zh = _ztop - _zbot
            _exp_up = _ztop + 0.15 * _zh; _exp_lo = _zbot - 0.15 * _zh
            if _exp_lo <= _cpx <= _exp_up:
                _in_heat_zone = True
            if _cpx >= _ztop - _HEAT_EDGE_PCT * _zh:
                _t_sig_dir = "put"
            elif _cpx <= _zbot + _HEAT_EDGE_PCT * _zh:
                _t_sig_dir = "call"
            _up_trigger = _ztop - _HEAT_EDGE_PCT * _zh
            _lo_trigger = _zbot + _HEAT_EDGE_PCT * _zh
            if _cpx < _opx and _hpx > _up_trigger and _cpx < _up_trigger:
                _t_sig_dir = "put"
            elif _cpx > _opx and _lpx < _lo_trigger and _cpx > _lo_trigger:
                _t_sig_dir = "call"
        # 记录heat_edges供dashboard用: (tstr, up, lo, in_heat, disp_up, disp_lo)
        # 与research/gen_heat_html_pierce2.py格式一致
        _he_tstr = t.strftime("%Y-%m-%d %H:%M")
        _he_up = float(_orig_up) if _orig_up is not None else None
        _he_lo = float(_orig_lo) if _orig_lo is not None else None
        _he_disp_up = _he_disp_lo = None
        if _he_up is not None and _he_lo is not None:
            _he_zh = _he_up - _he_lo
            _he_disp_up = _he_up + 0.15 * _he_zh
            _he_disp_lo = _he_lo - 0.15 * _he_zh
        _heat_edges_cache.append((_he_tstr, _he_up, _he_lo, _in_heat_zone, _he_disp_up, _he_disp_lo))
        # 展示冷冻版 (2026-10-29 用户: 热力图可显示, 信号须为冷冻版): heat_signals=False时热力区不发做T信号、
        # 不跳过QQE (热力计算在上, 照常进缓存供显示; EOD由窗口前EOD兜底, 故整块可跳过)
        if heat_signals and _in_heat_zone:
            # 强制补丁（2026-10-24 用户）：区高 <= 0.5 跳过做T信号（区太窄不可靠）
            _zone_h = (_he_up - _he_lo) if (_he_up is not None and _he_lo is not None) else 999
            _skip_t = _zone_h <= 0.5
            if _t_sig_dir and not _skip_t:
                _tstr = t.strftime("%m-%dT%H:%M")
                # 执行价：下一根开盘（回测口径）；实盘用当前价由watcher决定
                _next_same_day = (i+1 < len(warm)) and (warm["t"].iloc[i+1].strftime("%m-%d") == day)
                _px_exec = float(warm["open"].iloc[i+1]) if _next_same_day else px
                if pos is None:
                    actions.append((i, _tstr, "开", _t_sig_dir, _px_exec, f"做T开{_t_sig_dir}(7区)"))
                    pos = _t_sig_dir; _pos_in_heat = True
                    last_action_bar = i
                elif pos != _t_sig_dir:
                    _close_tag = "" if _pos_in_heat else "[非做T仓]"
                    actions.append((i, _tstr, "平", pos, _px_exec, f"做T信号平{pos}{_close_tag}(7区)"))
                    actions.append((i, _tstr, "开", _t_sig_dir, _px_exec, f"做T开{_t_sig_dir}(7区)"))
                    pos = _t_sig_dir; _pos_in_heat = True
                    last_action_bar = i
            # EOD 平仓优先（2026-10-24 修复）：即使在7区，15:55 也必须平仓，顶级优先级，不隔夜
            if hm >= "15:55" and pos is not None:
                _eod_px = _eod_1m.get(str(_day_arr[i]), px)
                _tstr_eod = t.strftime("%m-%dT%H:%M")
                actions.append((i, _tstr_eod, "平", pos, _eod_px, "日终平仓(1m15:55)"))
                pos = None; _pos_in_heat = False
                last_action_bar = i
                _was_above_zero_wk = False; _was_below_zero_wk = False
                _call_gap_wk = 0; _put_gap_wk = 0
                was_on_top = was_on_bottom = False
                wait_red = wait_blue = False
                _follow_entry_bar = None
                _hold_watch = None
                _fav_peak = 0; _fav_sign = 0
            continue  # 跳过QQE逻辑
        # === 做T热力规则结束 ===
        # 3-bar大K等反弹 (空头版, 2026-10-11): 持call+3根净跌超2.5 → 等30%反弹, 最多5根, 最高优先
        if k3_wait_target is not None:
            k3_wait_bars += 1
            _k3_hit = (px <= k3_wait_target) if k3_wait_is_put else (px >= k3_wait_target)
            if _k3_hit or k3_wait_bars >= 5:
                _tstr_kw = f"{day}T{hm}"
                actions.append((i, _tstr_kw, "平", pos, px, f"3K等待平仓(等{k3_wait_bars}根)"))
                pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; _call_gap_wk = 0; _put_gap_wk = 0; last_action_bar = i; was_on_top = was_on_bottom = False
                wait_red = wait_blue = False
                _follow_entry_bar = None
                _hold_watch = None
                _fav_peak = 0; _fav_sign = 0
                k3_wait_target = None; k3_wait_bars = 0
                k3_wait_count += 1
                _stop_triggered = True
                continue
            else:
                _stop_triggered = True
                continue  # 继续等待, 跳过其他信号
        # 大K双机制1 等反弹 (2026-10-09 用户): ≥4.0点反向大K线 → 等50%反弹, 最多5根, 最高优先
        if _bigwait_target is not None:
            _bigwait_bars += 1
            _bw_hit = (px <= _bigwait_target) if _bigwait_is_put else (px >= _bigwait_target)
            if _bw_hit or _bigwait_bars >= 5:
                _tstr_bw = f"{day}T{hm}"
                actions.append((i, _tstr_bw, "平", pos, px, f"大K等待平仓(等{_bigwait_bars}根)"))
                pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; _call_gap_wk = 0; _put_gap_wk = 0; last_action_bar = i; was_on_top = was_on_bottom = False
                wait_red = wait_blue = False
                _follow_entry_bar = None
                _hold_watch = None
                _fav_peak = 0; _fav_sign = 0
                _bigwait_target = None; _bigwait_bars = 0
                _stop_triggered = True
                continue
            else:
                _stop_triggered = True
                continue  # 继续等待, 跳过其他信号
        # 检查是否触发新的3K等待 (空头版, 宽松XX=2.5): 持call+3根净跌超2.5 → 等30%反弹
        if pos == "call" and i < len(high) and k3_wait_target is None:
            try:
                _k3_net = None; _k3_low = None
                if i >= 2:
                    _k3_d0 = warm["t"].iloc[i].strftime("%Y-%m-%d")
                    _k3_d2 = warm["t"].iloc[i-2].strftime("%Y-%m-%d")
                    if _k3_d0 == _k3_d2:  # 不跨交易日
                        _k3_net = float(close[i]) - float(warm["open"].iloc[i-2])
                        _k3_low = min(float(low[i-2]), float(low[i-1]), float(low[i]))
                if _k3_net is not None and _k3_net < -_K3_THRESH and hm < "15:55":
                    _k3_drop = abs(_k3_net)
                    k3_wait_target = _k3_low + _k3_drop * 0.30
                    k3_wait_bars = 0
                    k3_wait_is_put = False
                    _tstr_kws = f"{day}T{hm}"
                    # Fix 2026-10-14: 确保记录的pos与触发条件一致
                    actions.append((i, _tstr_kws, "持", "call", px, f"3K等待开始(净{_k3_net:.2f})"))
                    _stop_triggered = True
                    continue
            except Exception:
                pass
        # 检查是否触发新的等待
        if pos and i < len(high):
            try:
                _bwo = float(warm["open"].iloc[i]); _bwh = float(high[i]); _bwl = float(low[i]); _bwc = float(close[i])
                _bwrng = _bwh - _bwl
                if _bwrng >= 4.0 and hm < "15:55":
                    _bw_bull = _bwc > _bwo
                    _bw_opp = (pos == "put" and _bw_bull) or (pos == "call" and not _bw_bull)
                    if _bw_opp:
                        _bigwait_target = (_bwh - _bwrng * 0.5) if pos == "put" else (_bwl + _bwrng * 0.5)
                        _bigwait_bars = 0
                        _bigwait_is_put = (pos == "put")
                        _tstr_bws = f"{day}T{hm}"
                        actions.append((i, _tstr_bws, "持", pos, px, f"大K等待开始(幅度{_bwrng:.2f})"))
                        _stop_triggered = True
                        continue
            except Exception:
                pass
        # 3-bar大K止盈 (空头版, 2026-10-11): 持put+3根净跌超2.5 → 3根后止盈平仓, 最高优先
        if k3_tp_bar is not None and pos is None:
            k3_tp_bar = None  # 持仓已被其他路径平掉, 清除过期标记
        if k3_tp_bar is not None and pos:
            if i - k3_tp_bar >= _K3_TP_N:
                _tstr_ktp = f"{day}T{hm}"
                actions.append((i, _tstr_ktp, "平", pos, px, f"3K止盈平仓(等{_K3_TP_N}根)"))
                pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; _call_gap_wk = 0; _put_gap_wk = 0; last_action_bar = i; was_on_top = was_on_bottom = False
                wait_red = wait_blue = False
                _follow_entry_bar = None
                _hold_watch = None
                _fav_peak = 0; _fav_sign = 0
                k3_tp_bar = None
                k3_tp_count += 1
                _stop_entry_dir = None
                _stop_triggered = True
                continue
            else:
                _stop_triggered = True
                continue  # 等待期间跳过其他信号
        # 大K止盈 (2026-10-09 用户): ≥4.0点顺向大K → 3根后止盈平仓, 最高优先(与等反弹同级)
        if _bigtp_bar is not None and pos is None:
            _bigtp_bar = None  # 持仓已被其他路径平掉, 清除过期标记
        if _bigtp_bar is not None and pos:
            if i - _bigtp_bar >= _BIGTP_N:
                _tstr_tp = f"{day}T{hm}"
                actions.append((i, _tstr_tp, "平", pos, px, f"大K止盈平仓(等{_BIGTP_N}根)"))
                pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; _call_gap_wk = 0; _put_gap_wk = 0; last_action_bar = i; was_on_top = was_on_bottom = False
                wait_red = wait_blue = False
                _follow_entry_bar = None
                _hold_watch = None
                _fav_peak = 0; _fav_sign = 0
                _bigtp_bar = None
                _bigtp_count += 1
                _stop_entry_dir = None
                _stop_triggered = True
                continue
            else:
                _stop_triggered = True
                continue  # 等待期间跳过其他信号
        # 检查是否触发新的3K止盈等待 (空头版, 宽松XX=2.5): 持put+3根净跌超2.5 → 3根后止盈
        if pos == "put" and i < len(high) and k3_tp_bar is None:
            try:
                _k3_nett = None
                if i >= 2:
                    _k3_d0t = warm["t"].iloc[i].strftime("%Y-%m-%d")
                    _k3_d2t = warm["t"].iloc[i-2].strftime("%Y-%m-%d")
                    if _k3_d0t == _k3_d2t:  # 不跨交易日
                        _k3_nett = float(close[i]) - float(warm["open"].iloc[i-2])
                if _k3_nett is not None and _k3_nett < -_K3_THRESH and hm < "15:55":
                    k3_tp_bar = i
                    _tstr_ktps = f"{day}T{hm}"
                    # Fix 2026-10-14: 7/10 bug - 确保记录的pos与触发条件一致，用字面量"put"而非变量pos
                    actions.append((i, _tstr_ktps, "持", "put", px, f"3K止盈开始(净{_k3_nett:.2f})"))
                    _stop_triggered = True
                    continue
            except Exception:
                pass
        # 检查是否触发新的止盈等待 (顺向大K)
        if pos and i < len(high):
            try:
                _tpo = float(warm["open"].iloc[i]); _tph = float(high[i]); _tpl = float(low[i]); _tpc = float(close[i])
                _tprng = _tph - _tpl
                if _tprng >= 4.0 and hm < "15:55":
                    _tp_bull = _tpc > _tpo
                    _tp_fav = (pos == "call" and _tp_bull) or (pos == "put" and not _tp_bull)
                    if _tp_fav:
                        _bigtp_bar = i
                        _tstr_tps = f"{day}T{hm}"
                        actions.append((i, _tstr_tps, "持", pos, px, f"大K止盈开始(幅度{_tprng:.2f})"))
                        _stop_triggered = True
                        continue
            except Exception:
                pass
        # 期权20%止损检查 (2026-10-04 用户): 优先于信号平仓
        _stop_triggered = False
        if pos:
            if _stop_entry_dir != pos:
                # Fallback兜底 (2026-10-11 fix): 正常开仓当根已用真实入场价初始化,
                # 这里仅防漏网之鱼, 用当前px初始化
                _init_stop_vars(pos, px, i)
            else:
                # 持仓中: Gamma ROI触发 (2026-10-28用户令: 线性价值模型作废, 收益率只认Gamma公式)
                _d_spy = px - _stop_entry_px
                if pos == "call":
                    _g_roi = 9.88 * _d_spy + 0.311 * _d_spy * _d_spy
                    if _d_spy < 0:
                        _g_roi = min(_g_roi, 0)
                else:
                    _g_roi = -9.88 * _d_spy + 0.311 * _d_spy * _d_spy
                    if _d_spy > 0:
                        _g_roi = min(_g_roi, 0)
                if _stop_win is None and _g_roi <= -20.0:
                    # 止损窗口触发 (s20fz新baseline, nf口径): Gamma ROI跌破-20%即进窗口, 不论同bar有无翻仓信号
                    # (触发bar的翻仓被窗口压住; 窗口内W1-W5翻仓/平仓信号全跳过, 只留回弹平/到期平)
                    _w_thr = px + 0.50 * (_stop_entry_px - px)
                    _stop_win = {"dr": pos, "trig_bar": i, "entry_px": _stop_entry_px, "trig_close": px, "threshold": _w_thr}
                    _tstr_sl = f"{day}T{hm}"
                    actions.append((i, _tstr_sl, "持", pos, px, f"止损窗口开始(Gamma{_g_roi:.1f}%,回50%价{_w_thr:.2f})"))
                    _stop_triggered = True
                    continue
            # 30秒提前止损 (2026-10-07): 持仓中检查当前3m对应的3根1m, 逆向波动超阈值即按1m收盘价平
            if _USE_30S_STOP and pos and not _stop_triggered:
                _t3 = warm["t"].iloc[i]
                _dstr = _t3.strftime("%Y-%m-%d")
                for _mo in range(3):
                    _t1 = _t3 + pd.Timedelta(minutes=_mo)
                    _mk = (_dstr, _t1.strftime("%H:%M"))
                    _m1 = _M1_DICT.get(_mk)
                    if not _m1:
                        continue
                    _mo_, _mh_, _ml_, _mc_ = _m1
                    _adv = (_mo_ - _ml_) if pos == "call" else (_mh_ - _mo_)
                    if _adv > _30S_THRESH:
                        _tstr_30 = f"{day}T{_t1.strftime('%H:%M')}"
                        actions.append((i, _tstr_30, "平", pos, float(_mc_), f"30秒止损(1m逆向{_adv:.2f}>{_30S_THRESH})"))
                        pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; _call_gap_wk = 0; _put_gap_wk = 0; last_action_bar = i; was_on_top = was_on_bottom = False
                        wait_red = wait_blue = False
                        _follow_entry_bar = None
                        _hold_watch = None
                        _fav_peak = 0; _fav_sign = 0
                        _stop_entry_dir = None
                        _stop_triggered = True
                        break
        else:
            _stop_entry_dir = None
        # 从当前开仓开始数操作 (2026-10-03 用户反馈: 技术波浪起点太远)
        def _count_ops_from_open(_pk_idx):
            # 从第一个峰开始数 (buildup不算)
            return sum(1 for (bi, _, act, _, _, _) in actions if bi >= _pk_idx and act in ("开", "平"))
        def _damp_factor_gw():
            # 4根没操作就关减震 (2026-10-03 用户)
            if last_action_bar is None:
                return 1.0
            _quiet = i - last_action_bar
            return 1.0 if _quiet < 4 else 0.0
        cur_hist = float(hist[i])
        skip_open = False  # 短峰值翻仓只平不开时, 跳过本轮开仓
        _flip_to = None  # 翻仓目标方向, 同bar直接开
        _flip_is_huibu = False  # 长波回补标记 (显示用, 与正式gen_week.py一致)
        # 2026-10-08 用户: 翻仓确认 - 检查上一根的延迟翻仓
        if _pending_flip_to and i == _pending_flip_bar + 1:
            # 确认条件: 只看柱子比延迟bar短 (用户2026-10-08: 不要颜色检查)
            _confirmed = False
            try:
                if _pending_flip_to and pos and i-1 >= 0:
                    # 当前bar的|h| < 延迟bar的|h| 即确认
                    if abs(hist[i]) < abs(hist[i-1]):
                        _confirmed = True
            except:
                pass
            if _confirmed:
                # 执行延迟的翻仓
                actions.append((i, tstr, "平", pos, px, f"躲鞭确认翻仓({_pending_flip_reason})"))
                _flip_to = _pending_flip_to
                _n_confirm += 1
            else:
                _n_cancel += 1
            # 无论是否确认，都清除pending (只延迟一次)
            _last_delay_bar = _pending_flip_bar
            _pending_flip_to = None
            _pending_flip_bar = -1
            _pending_flip_reason = ""
        _flip_peak = 0
        in_cooldown = False  # 2026-10-05 用户: 去掉两根冷却
        # 续波跟随信号 (2026-10-03 用户): 上一波红 + 两根hist<0/|h|>=4/递增 -> 跟put; 蓝对称
        # 新波间隔 (2026-10-03 用户): 必须是新波起点, i-2往回至少4根连续|h|<=3, 否则算同一波不触发
        _cont_put = _cont_call = False
        if i >= 1 and i < len(_day_arr) and _day_arr[i] == _day_arr[i-1]:
            _NEW_WAVE_GAP = 4  # 助理暂定: 与系统4根安静规则一致, 用户可调
            _gap_n = 0; _is_new_wave = False
            for _gk in range(i-2, max(-1, i-2-20), -1):
                if _gk < 0 or _gk >= len(hist):
                    break
                if abs(hist[_gk]) <= 3:
                    _gap_n += 1
                    if _gap_n >= _NEW_WAVE_GAP:
                        _is_new_wave = True
                        break
                else:
                    break
            if _is_new_wave:
                _pw0 = prev_wave_color_gw(i)
                if _pw0 == 'red' and hist[i] < -SIG_MIN_LEN and hist[i-1] < -SIG_MIN_LEN \
                   and abs(hist[i]) > abs(hist[i-1]):
                    _cont_put = True
                elif _pw0 == 'blue' and hist[i] > SIG_MIN_LEN and hist[i-1] > SIG_MIN_LEN \
                   and abs(hist[i]) > abs(hist[i-1]):
                    _cont_call = True
        # 长波降低开仓门槛的信号 (2026-10-03 用户)
        # 2026-10-08 用户: 不光递增，要自适应门槛 (回补第2/3条)
        # 2026-10-08 用户更新: 前根>6只要递增，前根<=3必须翻倍 (不用1.2x)
        _long_put_sig = _long_call_sig = False
        if i >= 1 and i < len(_day_arr) and _day_arr[i] == _day_arr[i-1]:
            try:
                _rl0 = wave_len_gw(i, -1)
                _bl0 = wave_len_gw(i, 1)
                # 自适应门槛: >6只要递增，<=3必须翻倍，中间(3,6]也只要递增
                def _need_mult(_base):
                    if _base < 3:
                        return 2.0
                    elif _base < 5:
                        return 1.2
                    else:
                        return 1.0  # >3都只要递增
                # 检查红长波
                if _rl0 >= 15:
                    _hc = hist[i]
                    # 第2条: 比前一根长 (自适应)
                    _ok2 = False
                    if not np.isnan(_hc) and _hc < -3:
                        _hp = hist[i-1]
                        if not np.isnan(_hp) and _hp < -3:
                            _ahp = abs(_hp)
                            if abs(_hc) > _ahp * _need_mult(_ahp):
                                _ok2 = True
                    # 第3条: 三根全递增，第三根比第一根 (自适应)
                    _ok3 = False
                    if i >= 2 and not np.isnan(_hc) and _hc < -3:
                        _h1 = hist[i-2]; _h2 = hist[i-1]
                        if (not np.isnan(_h1) and not np.isnan(_h2)
                                and _h1 < -3 and _h2 < -3
                                and abs(_h2) > abs(_h1) and abs(_hc) > abs(_h2)):
                            _ah1 = abs(_h1)
                            if abs(_hc) > _ah1 * _need_mult(_ah1):
                                _ok3 = True
                    if _ok2 or _ok3:
                        _long_put_sig = True
                # 检查蓝长波
                elif _bl0 >= 15:
                    _hc = hist[i]
                    # 第2条: 比前一根长 (自适应)
                    _ok2 = False
                    if not np.isnan(_hc) and _hc > 3:
                        _hp = hist[i-1]
                        if not np.isnan(_hp) and _hp > 3:
                            _ahp = abs(_hp)
                            if abs(_hc) > _ahp * _need_mult(_ahp):
                                _ok2 = True
                    # 第3条: 三根全递增，第三根比第一根 (自适应)
                    _ok3 = False
                    if i >= 2 and not np.isnan(_hc) and _hc > 3:
                        _h1 = hist[i-2]; _h2 = hist[i-1]
                        if (not np.isnan(_h1) and not np.isnan(_h2)
                                and _h1 > 3 and _h2 > 3
                                and abs(_h2) > abs(_h1) and abs(_hc) > abs(_h2)):
                            _ah1 = abs(_h1)
                            if abs(_hc) > _ah1 * _need_mult(_ah1):
                                _ok3 = True
                    if _ok2 or _ok3:
                        _long_call_sig = True
            except Exception:
                pass
        sig = None
        # 早盘(10:00前)用跳过2倍检查的信号
        _is_morning = hm < "10:00"
        _set_r = set(peak_red_m.keys()) if _is_morning else set_red
        _set_b = set(peak_blue_m.keys()) if _is_morning else set_blue
        if i in _set_r and i in _set_b:
            # 同时触发取后者 (与 watcher 一致: 比较 trigger index, 这里同为 i, 取蓝)
            sig = "BLUE_DECAY_SHORT"
        elif i in _set_r:
            sig = "RED_DECAY_LONG"
        elif i in _set_b:
            sig = "BLUE_DECAY_SHORT"

        tstr = f"{day}T{hm}"

        # --- 持仓管理 (15:31版) ---
        if pos:
            # EOD 平仓 (2026-10-08 用户: 用1分钟15:55价格, 更接近实盘)
            if hm >= "15:55":
                _eod_px = _eod_1m.get(str(_day_arr[i]), px)  # 找不到则回退到3m收盘
                actions.append((i, tstr, "平", pos, _eod_px, "日终平仓(1m15:55)"))
                pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; _call_gap_wk = 0; _put_gap_wk = 0; last_action_bar = i; was_on_top = was_on_bottom = False
                wait_red = wait_blue = False
                _follow_entry_bar = None
                _hold_watch = None
                _fav_peak = 0; _fav_sign = 0
                continue
            # 动量死亡平仓 (2026-10-03 用户): 同向波(含灰)峰值>=10后衰减, 非真同色即平
            # 灰柱更敏感(阈值8), 空柱/真反色阈值4 (2026-10-03 用户: 灰矮柱要早平)
            # 更新峰值
            if _fav_sign != 0 and not np.isnan(hist[i]) and hist[i] * _fav_sign > 0 and abs(hist[i]) > _fav_peak:
                _fav_peak = abs(hist[i])
            _fav_died = False
            _is_g = not is_true_blue(i) and not is_true_red(i) and abs(hist[i]) > 3
            _dthr = 8 if _is_g else 4
            if _fav_sign == 1 and pos == "call" and _fav_peak >= 10:
                if not is_true_blue(i) and abs(hist[i]) <= _dthr:
                    _fav_died = True
            elif _fav_sign == -1 and pos == "put" and _fav_peak >= 10:
                if not is_true_red(i) and abs(hist[i]) <= _dthr:
                    _fav_died = True
            if _fav_died:
                actions.append((i, tstr, "平", pos, px, f"动量死亡平{pos}(峰{_fav_peak:.0f})"))
                pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; _call_gap_wk = 0; _put_gap_wk = 0; last_action_bar = i; was_on_top = was_on_bottom = False
                wait_red = wait_blue = False
                _decay_entry_hist = 0
                _follow_entry_bar = None
                _hold_watch = None
                _fav_peak = 0; _fav_sign = 0
                continue
            # HOLD后跟踪翻仓 (2026-10-03): HOLD后若衰减加深到<=need, 则翻仓
            # (不continue, 让_flip_to在section 2开仓)
            if _hold_watch and pos == _hold_watch.get("dir") and i > _hold_watch.get("bar", -1):
                # 若开仓bar晚于HOLD bar, 说明是新仓位, hold-watch已stale
                if last_action_bar > _hold_watch.get("bar", -1):
                    _hold_watch = None
                else:
                    _hwp = _hold_watch.get("peak") or 0
                    _hwr = abs(hist[i]) / _hwp if _hwp > 0 else 1.0
                    if _hwr <= _hold_watch.get("need", 0):
                        _hwdir = _hold_watch["dir"]
                        _hold_watch = None
                        if _hwdir == "put":
                            actions.append((i, tstr, "平", "put", px, f"hold-watch翻仓(峰{_hwp:.0f},比{_hwr:.2f})"))
                            _flip_to = "call"; _flip_peak = _hwp
                            # 记录红波起点, 防止翻仓后"红出现"误杀同波 (2026-10-04 fix)
                            _wsr = i
                            while _wsr > 0 and _ws_cont_red(_wsr-1):
                                _wsr -= 1
                            _call_red_ws = _wsr
                        else:
                            actions.append((i, tstr, "平", "call", px, f"hold-watch翻仓(峰{_hwp:.0f},比{_hwr:.2f})"))
                            _flip_to = "put"; _flip_peak = _hwp
                            # 记录蓝波起点, 防止翻仓后"蓝出现"误杀同波 (2026-10-04 fix)
                            _wsb = i
                            while _wsb > 0 and _ws_cont_blue(_wsb-1):
                                _wsb -= 1
                            _put_blue_ws = _wsb
                        pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; last_action_bar = i
                        was_on_top = was_on_bottom = False
                        wait_red = wait_blue = False
                        _decay_entry_hist = 0
                        _follow_entry_bar = None
            # 跟随失败即平 (2026-10-03 用户): 跟随开仓后下一根|h|<=入场柱80%即平. 只检查一次.
            if _follow_entry_bar == i - 1:
                _feh = _follow_entry_hist
                _follow_entry_bar = None
                # 跟随背离止损 (2026-10-03 用户; 2026-10-05 用户: 11点后不查; 2026-10-07 用户: 去掉)
                # 下一根收盘价反向超0.2点即平; 波浪5根以上不查; 11:00后不查
                _div_closed = False
                _dd = False  # 2026-10-07 用户: 去掉背离检查
                _follow_do_div_wk = True
                _hhmm_wk = tstr[6:11] if len(tstr) >= 11 else "00:00"
                if _dd and _hhmm_wk < "11:00" and pos and i >= 1 and i < len(close):
                    _ec = close[i-1]; _cc = close[i]
                    _div_qqe_ok = not (_DIV_QQE_GATE and _feh > 0 and abs(hist[i]) > _feh)  # 加强中不触发
                    if pos == "put" and _cc > _ec + _DIV_THRESH and _div_qqe_ok:
                        actions.append((i, tstr, "平", pos, px, f"跟随背离平put(入{_ec:.2f},现{_cc:.2f})"))
                        pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; _call_gap_wk = 0; _put_gap_wk = 0; last_action_bar = i; was_on_top = was_on_bottom = False
                        wait_red = wait_blue = False; _div_closed = True
                    elif pos == "call" and _cc < _ec - _DIV_THRESH and _div_qqe_ok:
                        actions.append((i, tstr, "平", pos, px, f"跟随背离平call(入{_ec:.2f},现{_cc:.2f})"))
                        pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; _call_gap_wk = 0; _put_gap_wk = 0; last_action_bar = i; was_on_top = was_on_bottom = False
                        wait_red = wait_blue = False; _div_closed = True
                # 顺强趋势跟随失败线收紧 (回测10-18/10-22定版, 引擎补同步 2026-10-29): 红gate开时60%, 否则80%
                #   回测原样: 只查红gate (11:00-15:00且cumslope<-0.20), 蓝侧仍80%, 不自加蓝侧
                _ff_ratio = 0.6 if (hm >= "11:00" and hm < "15:00" and i < len(_9m_cumslope) and not np.isnan(_9m_cumslope[i]) and _9m_cumslope[i] < -0.20) else 0.8
                if not _div_closed and _feh > 0 and abs(hist[i]) <= _feh * _ff_ratio:
                    actions.append((i, tstr, "平", pos, px, f"跟随失败平{pos}"))
                    pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; _call_gap_wk = 0; _put_gap_wk = 0; last_action_bar = i; was_on_top = was_on_bottom = False
                    wait_red = wait_blue = False
                    _decay_entry_hist = 0
                    continue
            # 续波翻仓 (2026-10-03 用户): 上一波红+新红柱 -> 持call翻put; 蓝对称. 同bar直接翻.
            # 长波翻仓 (2026-10-03 用户): 长波里降低开仓门槛, 持反向也翻.
            if pos == "call" and (_cont_put or _long_put_sig):
                actions.append((i, tstr, "平", "call", px, "续波翻put" if _cont_put else "长波回补平call"))
                pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; last_action_bar = i
                _flip_to = "put"; _flip_peak = 0; _flip_is_huibu = (not _cont_put and _long_put_sig)
            elif pos == "put" and (_cont_call or _long_call_sig):
                actions.append((i, tstr, "平", "put", px, "续波翻call" if _cont_call else "长波回补平put"))
                pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; last_action_bar = i
                _flip_to = "call"; _flip_peak = 0; _flip_is_huibu = (not _cont_call and _long_call_sig)
            # (已删除: 红/蓝连续变长 & 柱子消失平仓, 2026-10-01 10天回测胜率3%/0%)
            # 反向翻仓: 长峰值才翻, 短峰值只平仓观望
            # 2026-10-14: 红14/蓝11不对称 (蓝侧更容易翻)
            FLIP_MIN_PEAK_RED = 14
            FLIP_MIN_PEAK_BLUE = 11
            if pos and not in_cooldown:
                # 波浪自适应翻仓 (2026-10-03 用户): 起伏越厉害越减震. 起伏率=方向反转次数/(根数-1).
                # 灰柱两边同向不断浪. wl_adj = wl*(1.0+chop).
                def _fav_wave_info(_sign):
                    _ws = i
                    _consec_small = 0
                    # 当天第一根的 index，硬边界，绝不跨日 (2026-10-06 用户: 肯定不能跨日)
                    _cur_day = _day_arr[i]
                    _day_start = i
                    while _day_start > 0 and _day_arr[_day_start-1] == _cur_day:
                        _day_start -= 1
                    while _ws > _day_start:
                        _h = hist[_ws-1]
                        if np.isnan(_h):
                            break
                        # (2026-10-07 fix: 零轴下方的柱子即断波，不分大小)
                        # (2026-10-09 V1: |h|<1的微小反色不打断波浪)
                        if _h * _sign < 0 and abs(_h) >= 1:
                            break
                        if abs(_h) <= 4:
                            _consec_small += 1
                            if _consec_small > 8:
                                break
                        else:
                            _consec_small = 0
                        _ws -= 1
                    # 找第一个峰 (buildup结束、起伏开始), 之后创新高不再重置
                    _pk_idx = i; _pk_v = 0
                    _run_max = 0; _run_max_idx = _ws
                    for _j in range(_ws, i+1):
                        if not np.isnan(hist[_j]):
                            _v = abs(hist[_j])
                            if _v > _run_max:
                                _run_max = _v; _run_max_idx = _j
                            if _pk_idx == i and _run_max > 0 and _v < _run_max * 0.85:
                                _pk_idx = _run_max_idx; _pk_v = _run_max
                    if _pk_idx == i:
                        for _j in range(_ws, i+1):
                            if not np.isnan(hist[_j]):
                                _v = abs(hist[_j])
                                if _v > _pk_v:
                                    _pk_v = _v; _pk_idx = _j
                    _wl = i - _pk_idx
                    # 2026-10-07 用户: choppiness改10根滚动窗口(波内、日内截断)，用震荡比=累计变化/净变化
                    _win_start = max(i-9, _ws, _day_start)
                    _bars = [abs(hist[_j]) for _j in range(_win_start, i+1) if not np.isnan(hist[_j])]
                    _chop = 0
                    if len(_bars) >= 3:
                        _total = sum(abs(_bars[_b]-_bars[_b-1]) for _b in range(1, len(_bars)))
                        _net = abs(_bars[-1]-_bars[0])
                        _ratio = _total/_net if _net > 0.1 else 99
                        _chop = min(1.0, max(0.0, (_ratio-1.0)/4.0))
                    return _wl, _chop, _wl * (1.0 + _chop), _ws, _pk_idx
                if sig == "RED_DECAY_LONG" and pos == "put":
                    _pk_map = peak_red_m if _is_morning else peak_red
                    pk = _pk_map.get(i, 0)
                    if pk >= FLIP_MIN_PEAK_RED:
                        _wl, _chop, _wl_adj, _ws, _pk = _fav_wave_info(-1)
                        # 4次操作后才减震, 且随时间衰减
                        _op_cnt = _count_ops_from_open(_pk)
                        if _op_cnt >= 4:
                            _base_need = max(0.40, 0.8 * (0.97 ** max(0, _wl_adj - 3)))
                            _need = 0.8 - (0.8 - _base_need) * _damp_factor_gw()
                        else:
                            _need = 0.8
                        # 长波翻仓更严 (2026-10-03 用户): wl_adj>=15时need*0.7
                        if _wl_adj >= 15:
                            _need *= 0.7
                        # 反复翻仓惩罚 (2026-10-03 用户): 30根内翻过2次以上, need*0.7, 抑制颠簸浪反复翻
                        # 2026-10-06: 只数今天的, 不跨日
                        _rf_r = [b for b in _recent_flips if i - b <= 30 and _day_arr[b] == _day_arr[i]]
                        if len(_rf_r) >= 2:
                            _need *= 0.7
                        # 2026-10-07 用户: 最不济40%, need不低于0.40
                        _need = max(0.30, _need)
                        # 2026-10-17 红0.05上线: 红侧翻仓反弹惩罚(强下跌日弱反弹更难翻)
                        _need *= _rebound_factor_red(i)
                        _ratio = abs(hist[i]) / pk if pk > 0 else 1
                        if _ratio <= _need:
                            # 前根对比抗震 (2026-10-03 用户): 长波(wl_adj>=10)且前4根内同日操作>=2次, 要求<=前根70%才翻
                            _df_gw = _damp_factor_gw()
                            _prev_h = abs(hist[i-1]) if i-1 >= 0 and not np.isnan(hist[i-1]) else 999
                            _cur_day = tstr[:5]
                            _ops_4 = [a for a in actions if i-4 <= a[0] < i and a[2] in ("开", "平") and a[1][:5] == _cur_day]
                            _prev_thr = 0.7 if (len(_ops_4) >= 2 and _wl_adj >= 10) else 0.9
                            _pr = abs(hist[i]) / _prev_h if _prev_h > 0 else 1
                            if _pr <= _prev_thr:
                                # 2026-10-08 用户: 躲鞭 - 这根涨幅超开仓到前根区间90%
                                _delay_flip = False
                                try:
                                    if pos == "put" and _stop_entry_px > 0 and _stop_entry_bar >= 0 and i - _last_delay_bar > 1 and i-1 >= 0:
                                        _bars_held = i - _stop_entry_bar
                                        _prev_c = float(close[i-1])
                                        # 开仓到前根的区间长度
                                        _range = abs(_prev_c - _stop_entry_px)
                                        # 这根的涨幅
                                        _bar_move = px - _prev_c
                                        # 涨幅超区间90% 且现价超开仓价
                                        if False and _bars_held >= 2 and _range > 0 and _bar_move > _range * 0.9 and px > _stop_entry_px:
                                            _delay_flip = True
                                except:
                                    pass
                                if _delay_flip:
                                    # 延迟，不立即翻
                                    actions.append((i, tstr, "持", "put", px, f"红衰减延迟确认(峰{pk:.0f})"))
                                    _pending_flip_to = "call"
                                    _pending_flip_bar = i
                                    _pending_flip_reason = f"红衰减翻仓(峰{pk:.0f})"
                                    _flip_to = None
                                    _n_delay += 1
                                else:
                                    actions.append((i, tstr, "平", "put", px, f"红衰减翻仓(峰{pk:.0f},波{_wl})"))
                                    _flip_to = "call"; _flip_peak = pk; _is_red_flip_wk = True
                                # 记录红波起点, 防止翻仓后"红出现"误杀同波
                                _wsr = i
                                while _wsr > 0 and _ws_cont_red(_wsr-1):
                                    _wsr -= 1
                                _call_red_ws = _wsr
                            else:
                                actions.append((i, tstr, "持", "put", px, f"红衰减持有(峰{pk:.0f},波{_wl},前比{_pr:.2f}>{_prev_thr:.2f})"))
                                _flip_to = None
                        else:
                            # 衰减不够深: 持有不动 (不平不翻) (2026-10-10 用户: HOLD改平仓已拒绝回滚, 保持持有+_hold_watch跟踪)
                            # HOLD后继续跟踪 (2026-10-03): 记录peak/need, 后续bar衰减加深则翻
                            actions.append((i, tstr, "持", "put", px, f"红衰减持有(峰{pk:.0f},波{_wl},比{_ratio:.2f}>{_need:.2f})"))
                            _flip_to = None
                            _hold_watch = {"peak": pk, "need": _need, "dir": "put", "bar": i}
                    else:
                        actions.append((i, tstr, "平", "put", px, f"红衰减短峰只平(峰{pk:.0f})"))
                        skip_open = True
                    if _flip_to or "短峰只平" in actions[-1][5]:
                        pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; wait_red = wait_blue = False; _decay_entry_hist = 0
                    # 注意: 翻仓时 last_action_bar 在开仓后才更新, 保证同bar能开
                    if not _flip_to:
                        last_action_bar = i
                elif sig == "BLUE_DECAY_SHORT" and pos == "call":
                    _pk_map = peak_blue_m if _is_morning else peak_blue
                    pk = _pk_map.get(i, 0)
                    if pk >= FLIP_MIN_PEAK_BLUE:
                        _wl, _chop, _wl_adj, _ws, _pk = _fav_wave_info(1)
                        _op_cnt = _count_ops_from_open(_pk)
                        if _op_cnt >= 4:
                            _base_need = max(0.40, 0.8 * (0.97 ** max(0, _wl_adj - 3)))
                            _need = 0.8 - (0.8 - _base_need) * _damp_factor_gw()
                        else:
                            _need = 0.8
                        # 长波翻仓更严 (2026-10-03 用户, 蓝对称): wl_adj>=15时need*0.7
                        if _wl_adj >= 15:
                            _need *= 0.7
                        # 反复翻仓惩罚 (2026-10-13 用户, 蓝同红): 30根内(只数当天)翻过2次以上, need*0.7
                        _rf_b = [b for b in _recent_flips if i - b <= 30 and _day_arr[b] == _day_arr[i]]
                        if len(_rf_b) >= 2:
                            _need *= 0.7
                        # 2026-10-07 用户: 最不济40%, need不低于0.40
                        _need = max(0.30, _need)
                        _ratio = abs(hist[i]) / pk if pk > 0 else 1
                        if _ratio <= _need:
                            # 前根对比抗震 (2026-10-03 用户, 蓝对称): 长波(wl_adj>=10)且前4根内同日操作>=2次, 要求<=前根70%才翻
                            _df_gw_b = _damp_factor_gw()
                            _prev_hb = abs(hist[i-1]) if i-1 >= 0 and not np.isnan(hist[i-1]) else 999
                            _cur_day_b = tstr[:5]
                            _ops_4b = [a for a in actions if i-4 <= a[0] < i and a[2] in ("开", "平") and a[1][:5] == _cur_day_b]
                            _prev_thrb = 0.7 if (len(_ops_4b) >= 2 and _wl_adj >= 10) else 0.9
                            _prb = abs(hist[i]) / _prev_hb if _prev_hb > 0 else 1
                            if _prb <= _prev_thrb:
                                # 2026-10-08 用户: 躲鞭 - 这根跌幅超开仓到前根区间90% (对称)
                                _delay_flip_b = False
                                try:
                                    if pos == "call" and _stop_entry_px > 0 and _stop_entry_bar >= 0 and i - _last_delay_bar > 1 and i-1 >= 0:
                                        _bars_held_b = i - _stop_entry_bar
                                        _prev_c_b = float(close[i-1])
                                        _range_b = abs(_prev_c_b - _stop_entry_px)
                                        _bar_move_b = _prev_c_b - px  # 跌幅
                                        if False and _bars_held_b >= 2 and _range_b > 0 and _bar_move_b > _range_b * 0.9 and px < _stop_entry_px:
                                            _delay_flip_b = True
                                except:
                                    pass
                                if _delay_flip_b:
                                    actions.append((i, tstr, "持", "call", px, f"蓝衰减延迟确认(峰{pk:.0f})"))
                                    _pending_flip_to = "put"
                                    _pending_flip_bar = i
                                    _pending_flip_reason = f"蓝衰减翻仓(峰{pk:.0f})"
                                    _n_delay += 1
                                    _flip_to = None
                                else:
                                    actions.append((i, tstr, "平", "call", px, f"蓝衰减翻仓(峰{pk:.0f},波{_wl})"))
                                    _flip_to = "put"; _flip_peak = pk
                                # 记录蓝波起点, 防止翻仓后"蓝出现"误杀同波 (2026-10-04 fix: 只有真红才切断)
                                _wsb = i
                                while _wsb > 0 and _ws_cont_blue(_wsb-1):
                                    _wsb -= 1
                                _put_blue_ws = _wsb
                            else:
                                actions.append((i, tstr, "持", "call", px, f"蓝衰减持有(峰{pk:.0f},波{_wl},前比{_prb:.2f}>{_prev_thrb:.2f})"))
                                _flip_to = None
                        else:
                            # 衰减不够深: 持有不动 (不平不翻) (2026-10-10 用户: HOLD改平仓已拒绝回滚, 保持持有+_hold_watch跟踪)
                            # HOLD后继续跟踪 (2026-10-03): 记录peak/need, 后续bar衰减加深则翻
                            actions.append((i, tstr, "持", "call", px, f"蓝衰减持有(峰{pk:.0f},波{_wl},比{_ratio:.2f}>{_need:.2f})"))
                            _flip_to = None
                            _hold_watch = {"peak": pk, "need": _need, "dir": "call", "bar": i}
                    else:
                        actions.append((i, tstr, "平", "call", px, f"蓝衰减短峰只平(峰{pk:.0f})"))
                        skip_open = True
                    if _flip_to or "短峰只平" in actions[-1][5]:
                        pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; wait_red = wait_blue = False; _decay_entry_hist = 0
                    if not _flip_to:
                        last_action_bar = i
            # 波浪减震平仓 (2026-10-03 用户): 长有利波浪里, 平顺势仓需要多根确认
            def _need_confirm_bars_gw(_sign):
                _wl, _chop, _wl_adj, _ws, _pk = _fav_wave_info(_sign)
                _op_cnt = _count_ops_from_open(_pk)
                if _op_cnt < 4:
                    return 1
                _base_bars = 1 + max(0, int((_wl_adj - 5) // 8))
                return 1 + int((_base_bars - 1) * _damp_factor_gw())
            # (2026-10-06: 持call/put时2根超短/空柱后算新波，与watcher.py同步)
            # 短柱(|h|<=3)则gap+1；非短且非同色则gap清零；同色短衰减中保持gap
            _h_abs = abs(hist[i]) if not np.isnan(hist[i]) else 999
            _is_short_wk = _h_abs <= 3
            _h_abs = abs(hist[i]) if not np.isnan(hist[i]) else 999
            _is_short_wk = _h_abs <= 3
            # 2026-10-07 用户: 空仓时不攒gap，只在持仓期间数新波
            if _is_short_wk and pos is not None:
                _call_gap_wk += 1
                _put_gap_wk += 1
            elif pos is None:
                _call_gap_wk = 0; _put_gap_wk = 0
            else:
                if not (not np.isnan(hist[i]) and hist[i] < 0):
                    _call_gap_wk = 0
                if not (not np.isnan(hist[i]) and hist[i] > 0):
                    _put_gap_wk = 0
            # 反向出现即平: 持call真红出现(前3根无真红) -> 平; 持put真蓝出现(前3根无真蓝) -> 平
            # (2026-10-06: 新波里红/蓝出现才平，需2根超短/空柱后的新波，与watcher.py同步)
            if pos and (i - last_action_bar) >= 2:
                if pos == "call":
                    _need_r = _need_confirm_bars_gw(1)
                    _consec = 0
                    for _j in range(i, -1, -1):
                        if is_true_red(_j):
                            _consec += 1
                        else:
                            break
                    _seq_s = i - _consec + 1
                    _had = any(is_true_red(j) for j in range(max(start_idx, _seq_s-3), _seq_s))
                    # 同波红翻仓开的不算"新出现"
                    _ws_r = i
                    while _ws_r > 0 and _ws_cont_red(_ws_r-1):
                        _ws_r -= 1
                    _same_wave = (_call_red_ws is not None and _ws_r == _call_red_ws)
                    # (2026-10-06: 需2根超短/空柱后的新波才平，与watcher.py同步)
                    # (2026-10-06 用户20:48: 放宽到1根真柱即平)
                    # (2026-10-07 用户: 新波里灰柱也平，2根递增或1根>=5，对称)
                    def _is_gray_gw(j):
                        if j < 0 or j >= len(hist): return False
                        _hj = hist[j]
                        if np.isnan(_hj) or abs(_hj) <= 3: return False
                        return not is_true_blue(j) and not is_true_red(j)
                    _new_wave_call_close = False
                    _nw_reason = ""
                    if _call_gap_wk >= 2:
                        # 2026-10-07 用户: 灰柱在零下也算红出现
                        _is_gray_below_gw = _is_gray_gw(i) and hist[i] < 0 if i < len(hist) and not np.isnan(hist[i]) else False
                        if _consec >= 1 or _is_gray_below_gw:
                            _new_wave_call_close = True; _nw_reason = "红出现平call(需1根)" if _consec >= 1 else "灰零下平call"
                        elif _is_gray_gw(i) and _is_gray_gw(i-1) and abs(hist[i]) > abs(hist[i-1]):
                            _new_wave_call_close = True; _nw_reason = "灰2连增平call"
                        elif _is_gray_gw(i) and abs(hist[i]) >= 5:
                            _new_wave_call_close = True; _nw_reason = "灰中柱平call"
                    if _new_wave_call_close:
                        actions.append((i, tstr, "平", "call", px, _nw_reason))
                        pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; wait_red = wait_blue = False; _decay_entry_hist = 0
                        last_action_bar = i
                    # (2026-10-06 用户16:19对称: 下穿零线就平call，与watcher.py同步)
                    # (2026-10-07 用户: 波浪管理版 - 上过≥0后到<-3就平)
                    elif pos == "call" and (i - last_action_bar) >= 2:
                        _h_n_c = hist[i] if not np.isnan(hist[i]) else 0
                        if _h_n_c >= 0:
                            _was_above_zero_wk = True
                        if _was_above_zero_wk and _h_n_c < -3:
                            actions.append((i, tstr, "平", "call", px, f"下穿零线平call(波浪管理)"))
                            pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; wait_red = wait_blue = False; _decay_entry_hist = 0
                            _was_above_zero_wk = False
                            last_action_bar = i

                elif pos == "put":
                    _need_b = _need_confirm_bars_gw(-1)
                    _consec = 0
                    for _j in range(i, -1, -1):
                        if is_true_blue(_j):
                            _consec += 1
                        else:
                            break
                    _seq_s = i - _consec + 1
                    _had = any(is_true_blue(j) for j in range(max(start_idx, _seq_s-3), _seq_s))
                    # 同波蓝翻仓开的不算"新出现" (2026-10-04 fix: 只有真红才切断波, 灰/空柱不切断)
                    _ws_b = i
                    while _ws_b > 0 and _ws_cont_blue(_ws_b-1):
                        _ws_b -= 1
                    _same_wave_b = (_put_blue_ws is not None and _ws_b == _put_blue_ws)
                    # (2026-10-06: 需2根超短/空柱后的新波才平，与watcher.py同步)
                    # (2026-10-06 用户20:48: 放宽到1根真柱即平)
                    # (2026-10-07 用户: 新波里灰柱也平，2根递增或1根>=5，对称)
                    def _is_gray_gw2(j):
                        if j < 0 or j >= len(hist): return False
                        _hj = hist[j]
                        if np.isnan(_hj) or abs(_hj) <= 3: return False
                        return not is_true_blue(j) and not is_true_red(j)
                    _new_wave_put_close = False
                    _nw_reason2 = ""
                    if _put_gap_wk >= 2:
                        # 2026-10-07 用户: 灰柱在零上也算蓝出现
                        _is_gray_above_gw = _is_gray_gw2(i) and hist[i] > 0 if i < len(hist) and not np.isnan(hist[i]) else False
                        if _consec >= 1 or _is_gray_above_gw:
                            _new_wave_put_close = True; _nw_reason2 = "蓝出现平put(需1根)" if _consec >= 1 else "灰零上平put"
                        elif _is_gray_gw2(i) and _is_gray_gw2(i-1) and abs(hist[i]) > abs(hist[i-1]):
                            _new_wave_put_close = True; _nw_reason2 = "灰2连增平put"
                        elif _is_gray_gw2(i) and abs(hist[i]) >= 5:
                            _new_wave_put_close = True; _nw_reason2 = "灰中柱平put"
                    if _new_wave_put_close:
                        actions.append((i, tstr, "平", "put", px, _nw_reason2))
                        pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; wait_red = wait_blue = False; _decay_entry_hist = 0
                        last_action_bar = i
                    # (2026-10-06 用户16:19: 上穿零线就平put，与watcher.py同步)
                    # (2026-10-07 用户: 波浪管理版 - 下过≤0后到>3就平)
                    elif pos == "put" and (i - last_action_bar) >= 2:
                        _h_n = hist[i] if not np.isnan(hist[i]) else 0
                        if _h_n <= 0:
                            _was_below_zero_wk = True
                        if _was_below_zero_wk and _h_n > 3:
                            actions.append((i, tstr, "平", "put", px, f"上穿零线平put(波浪管理)"))
                            pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; wait_red = wait_blue = False; _decay_entry_hist = 0
                            _was_below_zero_wk = False
                            last_action_bar = i

            # 同向消失即平: 真反色/空柱/反向灰柱才平 (2026-10-08 用户: 灰色反向了当然要平)
            # 波浪减震: 长波里需要连续多根才算消失
            if pos and (i - last_action_bar) >= 2:
                if pos == "put":
                    _need_gr = _need_confirm_bars_gw(-1)
                    _consec_gr = 0
                    for _j in range(i, -1, -1):
                        _hj = hist[_j]
                        _gray_pos = False  # 对照测试临时关闭灰反向
                        if (is_true_blue(_j) or abs(_hj) <= 3 or np.isnan(_hj) or _gray_pos):
                            _consec_gr += 1
                        else:
                            break
                    _had_r = any(is_true_red(j) for j in range(max(start_idx, i-3), i))
                    if _had_r and _consec_gr >= _need_gr:
                        actions.append((i, tstr, "平", "put", px, f"红消失平put(需{_need_gr}根)"))
                        pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; wait_red = wait_blue = False; _decay_entry_hist = 0
                        last_action_bar = i
                elif pos == "call":
                    _need_gb = _need_confirm_bars_gw(1)
                    _consec_gb = 0
                    for _j in range(i, -1, -1):
                        _hj = hist[_j]
                        _gray_neg = False  # 对照测试临时关闭灰反向
                        if (is_true_red(_j) or abs(_hj) <= 3 or np.isnan(_hj) or _gray_neg):
                            _consec_gb += 1
                        else:
                            break
                    _had_b = any(is_true_blue(j) for j in range(max(start_idx, i-3), i))
                    if _had_b and _consec_gb >= _need_gb:
                        actions.append((i, tstr, "平", "call", px, f"蓝消失平call(需{_need_gb}根)"))
                        pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; wait_red = wait_blue = False; _decay_entry_hist = 0
                        last_action_bar = i
            # 重涨回补 (2026-10-02 用户优化; 2026-10-05 用户: 改成反手, 同bar开反向; 2026-10-06 用户: 2根都比翻仓bar长, 或1根长20%+; 2026-10-07 用户: 或比前一根长20%)
            # 蓝重涨->翻call, 红重涨->翻put. 2026-10-19 用户: 不检查持仓, 止损平仓后同bar也照常翻
            for _tdir, _tsgn in (("call", 1), ("put", -1)):
                if pos == _tdir or _decay_entry_hist <= 0:
                    continue
                _entry_hist = _decay_entry_hist
                _tsign = _tsgn
                _found = False
                _hc = hist[i]
                if not np.isnan(_hc) and _hc * _tsign > 0 and abs(_hc) > 3:
                    # 单根长20%+ (比开仓)
                    if abs(_hc) > _entry_hist * 1.2:
                        _found = True
                    # 2026-10-07 用户: 比前一根长(自适应: 3要翻倍, 8要1.2就行)
                    elif i - 1 >= 0:
                        _hp = hist[i - 1]
                        if (not np.isnan(_hp) and _hp * _tsign > 0 and abs(_hp) > 3):
                            _ahp = abs(_hp)
                            _need = 2.0 if _ahp <= 5 else 1.2
                            if abs(_hc) > _ahp * _need:
                                _found = True
                    # 2026-10-07 用户: 三根全递增，第三根比第一根(门槛更低)
                    if not _found and i - 2 >= 0:
                        _h1 = hist[i - 2]; _h2 = hist[i - 1]
                        if (not np.isnan(_h1) and not np.isnan(_h2)
                                and _h1 * _tsign > 0 and _h2 * _tsign > 0
                                and abs(_h1) > 3 and abs(_h2) > 3
                                and abs(_h2) > abs(_h1) and abs(_hc) > abs(_h2)):
                            _ah1 = abs(_h1)
                            _need1 = 2.0 if _ah1 <= 5 else 1.2
                            if abs(_hc) > _ah1 * _need1:
                                _found = True
                    # 连续2根都比翻仓bar长
                    if not _found and i - 1 >= 0:
                        _hp = hist[i - 1]
                        if (not np.isnan(_hp) and _hp * _tsign > 0 and abs(_hp) > 3
                                and abs(_hp) > _entry_hist and abs(_hc) > _entry_hist):
                            _found = True
                if _found:
                    if pos:
                        _lbl = "蓝重涨平put" if pos == "put" else "红重涨平call"
                        actions.append((i, tstr, "平", pos, px, _lbl))
                        pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; wait_red = wait_blue = False
                    # 2026-10-05 用户: 经典回补, 同bar反手
                    actions.append((i, tstr, "开", _tdir, px, f"重涨反手开{_tdir}"))
                    _decay_entry_hist = 0
                    pos = _tdir; _init_stop_vars(_tdir, px, i); last_action_bar = i
                    _fav_peak = abs(hist[i]); _fav_sign = 1 if _tdir == "call" else -1
                    wait_red = wait_blue = False
                    # 设置同波保护 (防止刚开的被"出现"误杀)
                    if _tdir == "call":
                        _wsr = i
                        while _wsr > 0 and _ws_cont_red(_wsr-1):
                            _wsr -= 1
                        _call_red_ws = _wsr
                    else:
                        _wsb = i
                        while _wsb > 0 and _ws_cont_blue(_wsb-1):
                            _wsb -= 1
                        _put_blue_ws = _wsb
            # 同色极短平仓 (2026-10-02 用户): 同一波≥4根真色后, 单根缩到|hist|<=4 -> 平.
            # 波浪减震 (2026-10-03): 长波里需要连续多根极短才平
            if pos and (i - last_action_bar) >= 2:
                _fsign = -1 if pos == "put" else 1
                _is_true = (lambda j: bool(q_down[j]) and abs(hist[j]) > 3) if pos == "put" else (lambda j: bool(q_up[j]) and abs(hist[j]) > 3)
                _wstart = i
                while _wstart > 0:
                    _ph = hist[_wstart - 1]
                    if np.isnan(_ph) or _ph * _fsign <= 0 or abs(_ph) <= 3:
                        break
                    _wstart -= 1
                _true_cnt = sum(1 for j in range(_wstart, i) if _is_true(j))
                _h = hist[i] if not np.isnan(hist[i]) else 0
                if _true_cnt >= 4 and _h * _fsign > 0 and abs(_h) <= 4:
                    _wl_t, _chop_t, _wl_adj_t, _ws_t, _pk_t = _fav_wave_info(_fsign)
                    _op_cnt_t = _count_ops_from_open(_pk_t)
                    if _op_cnt_t >= 4:
                        _base_tiny = 1 + max(0, int((_wl_adj_t - 5) // 8))
                        _need_tiny = 1 + int((_base_tiny - 1) * _damp_factor_gw())
                    else:
                        _need_tiny = 1
                    _consec_tiny = 0
                    for _jj in range(i, -1, -1):
                        _hj = hist[_jj]
                        if _hj * _fsign > 0 and abs(_hj) <= 4:
                            _consec_tiny += 1
                        else:
                            break
                    if _consec_tiny >= _need_tiny:
                        _lbl = ("红极短平put" if pos == "put" else "蓝极短平call") + f"(需{_need_tiny}根)"
                        actions.append((i, tstr, "平", pos, px, _lbl))
                        pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; wait_red = wait_blue = False; _decay_entry_hist = 0
                        last_action_bar = i
            # 衰减停滞平仓 (2026-10-02 用户): decay开仓后连续4根同色不低于开仓80% -> 平.
            # 2026-10-07: 入场后必须经过4根不同bar才查停滞 (与live一致)
            if pos and (i - last_action_bar) >= 4 and _decay_entry_hist > 0:
                _ssign = 1 if pos == "put" else -1
                _stall = True
                for _j in range(max(0, i-3), i+1):
                    _sh = hist[_j]
                    if np.isnan(_sh) or _sh * _ssign <= 0 or abs(_sh) < _decay_entry_hist * 0.8:
                        _stall = False; break
                if _stall:
                    _lbl = "蓝停滞平put" if pos == "put" else "红停滞平call"
                    actions.append((i, tstr, "平", pos, px, _lbl))
                    # 记录停滞重开信息 (2026-10-06 用户): 同波内继续衰减则重开
                    _stall_reopen_dir = pos
                    _stall_reopen_hist = abs(hist[i]) if not np.isnan(hist[i]) else 0
                    _stall_reopen_bar = i
                    # 找波浪起点
                    _ws = i
                    _wcont = _ws_cont_red if _ssign < 0 else _ws_cont_blue
                    while _ws > 0 and _wcont(_ws-1):
                        _ws -= 1
                    _stall_reopen_ws = _ws
                    pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; wait_red = wait_blue = False; _decay_entry_hist = 0
                    last_action_bar = i
            # 连色走强平仓: 持call连续8根真红且比开仓涨50% -> 平; 持put连续8根真蓝且涨50% -> 平
            if pos and (i - last_action_bar) >= 2:
                _entry = last_action_bar
                _entry_h = abs(hist[_entry]) if _entry >= 0 else 0
                if pos == "call":
                    _c = 0
                    for j in range(i, max(_entry, i-7)-1, -1):
                        if is_true_red(j):
                            _c += 1
                        else:
                            break
                    _grew = _entry_h > 0 and abs(hist[i]) >= _entry_h * 1.5
                    if _c >= 8 and _grew:
                        actions.append((i, tstr, "平", "call", px, f"连红{_c}根涨{_entry_h:.0f}->{abs(hist[i]):.0f}平call"))
                        pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; wait_red = wait_blue = False; _decay_entry_hist = 0
                        last_action_bar = i
                        _streak_block_until = i + 5
                elif pos == "put":
                    _c = 0
                    for j in range(i, max(_entry, i-7)-1, -1):
                        if is_true_blue(j):
                            _c += 1
                        else:
                            break
                    _grew = _entry_h > 0 and abs(hist[i]) >= _entry_h * 1.5
                    if _c >= 8 and _grew:
                        actions.append((i, tstr, "平", "put", px, f"连蓝{_c}根涨{_entry_h:.0f}->{abs(hist[i]):.0f}平put"))
                        pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; wait_red = wait_blue = False; _decay_entry_hist = 0
                        last_action_bar = i
                        _streak_block_until = i + 5
        # --- 开仓 (15:31版) ---
        # 停滞重开 (2026-10-06 用户): 停滞平仓后，同波内继续衰减20%+则重开
        if _stall_reopen_dir and not pos and not _flip_to:
            _sr_sign = -1 if _stall_reopen_dir == "call" else 1  # call对应红衰减，put对应蓝衰减
            _cur_ws = i
            _wcont = _ws_cont_red if _sr_sign < 0 else _ws_cont_blue
            while _cur_ws > 0 and _wcont(_cur_ws-1):
                _cur_ws -= 1
            # 同一波浪且继续衰减20%+
            if _cur_ws == _stall_reopen_ws and not np.isnan(hist[i]):
                _ch = hist[i]
                # 2026-10-07 用户: 比停滞那根衰减80%以上才重开; 停滞根到当前之间有更长的柱子则跳过
                _skip_ro = False
                try:
                    if _stall_reopen_bar >= 0:
                        for _bj in range(_stall_reopen_bar+1, i):
                            _bh = hist[_bj] if _bj < len(hist) else float('nan')
                            if not np.isnan(_bh) and abs(_bh) > _stall_reopen_hist:
                                _skip_ro = True
                                break
                except Exception:
                    pass
                if _ch * _sr_sign > 0 and abs(_ch) > 3 and _stall_reopen_hist > 0 and abs(_ch) < _stall_reopen_hist * 0.8 and not _skip_ro:
                    _lbl = "红继续衰减重开call" if _stall_reopen_dir == "call" else "蓝继续衰减重开put"
                    actions.append((i, tstr, "开", _stall_reopen_dir, px, _lbl))
                    pos = _stall_reopen_dir; _init_stop_vars(_stall_reopen_dir, px, i); last_action_bar = i
                    _decay_entry_hist = abs(_ch)
                    wait_red = wait_blue = False
            # 波浪结束或已重开，清除标记
            if pos or _cur_ws != _stall_reopen_ws:
                _stall_reopen_dir = None; _stall_reopen_hist = 0; _stall_reopen_ws = -1; _stall_reopen_bar = -1
        if _flip_to:
            # 翻仓: 同bar直接开反向, 不受冷静期限制
            actions.append((i, tstr, "开", _flip_to, px, f"长波回补开{_flip_to}" if _flip_is_huibu else f"翻仓开{_flip_to}"))
            pos = _flip_to; _init_stop_vars(_flip_to, px, i); last_action_bar = i; _entry_peak = _flip_peak; _flip_is_huibu = False
            _decay_entry_hist = abs(hist[i]) if not np.isnan(hist[i]) else 0
            _fav_peak = abs(hist[i]) if (not np.isnan(hist[i]) and ((_flip_to == "call" and hist[i] > 0) or (_flip_to == "put" and hist[i] < 0))) else 0
            _fav_sign = (1 if _flip_to == "call" else -1) if _fav_peak > 0 else 0
            # 反复翻仓惩罚跟踪 (2026-10-03 用户)
            _recent_flips.append(i)
            _recent_flips = _recent_flips[-5:]
        elif not pos and not skip_open and (i - last_action_bar) >= 2 and i >= _streak_block_until and hm < "15:55":
            # 开仓要求峰值>=15 (和翻仓一样, 短衰减只平不开)
            # 长波逆势开仓要求深衰减 (2026-10-03 用户): 红长浪(波>=15)开call需衰减到峰值50%以下, 蓝对称
            _OPEN_MIN_PEAK = 14
            _pk_r = (peak_red_m if _is_morning else peak_red).get(i, 0)
            _pk_b = (peak_blue_m if _is_morning else peak_blue).get(i, 0)
            _rwlo = wave_len_gw(i, -1)
            _bwlo = wave_len_gw(i, 1)
            _deep_c = True
            _deep_p = True
            if _rwlo >= 15 and _pk_r > 0 and not np.isnan(hist[i]):
                if abs(hist[i]) / _pk_r > 0.5:
                    _deep_c = False
            if _bwlo >= 15 and _pk_b > 0 and not np.isnan(hist[i]):
                if abs(hist[i]) / _pk_b > 0.5:
                    _deep_p = False
            if sig == "RED_DECAY_LONG" and _pk_r >= _OPEN_MIN_PEAK and _deep_c and tstr[6:] >= "15:00":
                actions.append((i, tstr, "开", "call", px, f"红衰减信号(峰{_pk_r:.0f})"))
                pos = "call"; _init_stop_vars("call", px, i); _was_above_zero_wk = False; _was_below_zero_wk = False; was_on_top = False
                _fav_peak = abs(hist[i]) if (not np.isnan(hist[i]) and hist[i] > 0) else 0; _fav_sign = 1 if (not np.isnan(hist[i]) and hist[i] > 0) else 0
                last_action_bar = i
                wait_red = wait_blue = False
                _entry_peak = _pk_r
                _decay_entry_hist = abs(hist[i]) if not np.isnan(hist[i]) else 0
                # 记录红波起点, 防止"红出现"误杀同波 (2026-10-05 fix)
                _wsr = i
                while _wsr > 0 and _ws_cont_red(_wsr-1):
                    _wsr -= 1
                _call_red_ws = _wsr
            elif sig == "BLUE_DECAY_SHORT" and _pk_b >= _OPEN_MIN_PEAK and _deep_p and tstr[6:] < "10:00":
                actions.append((i, tstr, "开", "put", px, f"蓝衰减信号(峰{_pk_b:.0f})"))
                pos = "put"; _init_stop_vars("put", px, i); _was_above_zero_wk = False; _was_below_zero_wk = False; was_on_bottom = False
                _fav_peak = abs(hist[i]) if (not np.isnan(hist[i]) and hist[i] < 0) else 0; _fav_sign = -1 if (not np.isnan(hist[i]) and hist[i] < 0) else 0
                last_action_bar = i
                wait_red = wait_blue = False
                _entry_peak = _pk_b
                _decay_entry_hist = abs(hist[i]) if not np.isnan(hist[i]) else 0
                # 记录蓝波起点, 防止"蓝出现"误杀同波 (2026-10-05 fix, 与 watcher.py 同步)
                _wsb = i
                while _wsb > 0 and _ws_cont_blue(_wsb-1):
                    _wsb -= 1
                _put_blue_ws = _wsb
            elif wait_red and is_true_red(i):
                actions.append((i, tstr, "开", "put", px, "等到红柱开put"))
                pos = "put"; _init_stop_vars("put", px, i); _was_above_zero_wk = False; _was_below_zero_wk = False; was_on_bottom = False; wait_red = False
                _fav_peak = abs(hist[i]) if (not np.isnan(hist[i]) and hist[i] < 0) else 0; _fav_sign = -1 if (not np.isnan(hist[i]) and hist[i] < 0) else 0
                last_action_bar = i
            elif wait_blue and is_true_blue(i):
                actions.append((i, tstr, "开", "call", px, "等到蓝柱开call"))
                pos = "call"; _init_stop_vars("call", px, i); _was_above_zero_wk = False; _was_below_zero_wk = False; was_on_top = False; wait_blue = False
                _fav_peak = abs(hist[i]) if (not np.isnan(hist[i]) and hist[i] > 0) else 0; _fav_sign = 1 if (not np.isnan(hist[i]) and hist[i] > 0) else 0
                last_action_bar = i
            # 碎波暂停触发检查 (2026-10-04 用户): 刚平的跟随单
            if _follow_entry_dir_wk and pos is None:
                try:
                    _fspy_out = close[i]
                    _fp = (_fspy_out - _follow_entry_spy_wk) if _follow_entry_dir_wk == "call" else (_follow_entry_spy_wk - _fspy_out)
                    _follow_pnls_wk.append((_follow_entry_dir_wk, _follow_entry_peak_wk, _fp, _follow_entry_date_wk))
                    _follow_pnls_wk = _follow_pnls_wk[-5:]
                    # 2026-10-07 用户: 先不要碎波暂停
                    # if len(_follow_pnls_wk) >= 2:
                    #     _aa, _bb = _follow_pnls_wk[-2], _follow_pnls_wk[-1]
                    #     if (_aa[0] == _bb[0] and _aa[3] == _bb[3] and _aa[3] != "" and
                    #         _aa[1] < 18 and _bb[1] < 18 and _aa[2] < 0 and _bb[2] < 0):
                    #         _frag_pause_until_wk = i + 10
                except Exception:
                    pass
                _follow_entry_dir_wk = None
            # 跟随逻辑独立执行 (2026-10-05 fix: 原来在 else 里, 被碎波检查吞掉)
            if True:
                # 两根长柱跟随: 新波开始才跟 (往前连色不超过4根); 第二根比第一根长
                # (2026-10-01 用户: 红后蓝/灰后蓝都算新波开始)
                FOLLOW_MIN_LEN = 8
                # 大K双机制2 不跟随 (2026-10-09 用户): ≥4.5点当根跳过跟随
                _bigk_skip_wk = False
                try:
                    if i < len(high):
                        _sk_rng = float(high[i]) - float(low[i])
                        if _sk_rng >= 4.5:
                            _bigk_skip_wk = True
                            actions.append((i, f"{day}T{hm}", "持", pos if pos else "-", px, f"大K跳过跟随(幅度{_sk_rng:.2f})"))
                except Exception:
                    pass
                # 3-bar大K不跟随 (空头版, 宽松XX=2.5, 2026-10-11): 3根净跌超2.5 → 当根跳过put标准跟随
                # 只拦"两根长红跟随", 不拦灰后/长波/续波跟随
                k3_skip = False
                try:
                    if i >= 2 and i < len(high):
                        _k3_d0s = warm["t"].iloc[i].strftime("%Y-%m-%d")
                        _k3_d2s = warm["t"].iloc[i-2].strftime("%Y-%m-%d")
                        if _k3_d0s == _k3_d2s:  # 不跨交易日
                            _k3_nets = float(close[i]) - float(warm["open"].iloc[i-2])
                            if _k3_nets < -_K3_THRESH:
                                k3_skip = True
                                actions.append((i, f"{day}T{hm}", "持", pos if pos else "-", px, f"3K跳过跟随P(净{_k3_nets:.2f})"))
                                k3_skip_count += 1
                except Exception:
                    pass
                # 波浪长度 (2026-10-03 用户: 波浪走了5根以上就不查背离)
                _fwl = 1
                try:
                    _wsg = 1 if hist[i] > 0 else (-1 if hist[i] < 0 else 0)
                    for _wj in range(i-1, -1, -1):
                        _hsj = 1 if hist[_wj] > 0 else (-1 if hist[_wj] < 0 else 0)
                        if _hsj == _wsg and _wsg != 0:
                            _fwl += 1
                        else:
                            break
                except Exception:
                    _fwl = 1
                _follow_do_div = _fwl <= 5
                # 波浪峰值 (2026-10-04 用户: 碎波暂停用)
                _fwp = abs(hist[i])
                try:
                    _ws2 = 1 if hist[i] > 0 else (-1 if hist[i] < 0 else 0)
                    for _wj2 in range(i, -1, -1):
                        _hs2 = 1 if hist[_wj2] > 0 else (-1 if hist[_wj2] < 0 else 0)
                        if _hs2 == _ws2 and _ws2 != 0 and abs(hist[_wj2]) > 3:
                            _fwp = max(_fwp, abs(hist[_wj2]))
                        else:
                            break
                except Exception:
                    pass
                # 碎波暂停检查 (2026-10-04 用户)
                _frag_paused_wk = i < _frag_pause_until_wk
                # 跟随不跨天 (2026-10-02 用户: 08-10开盘跟随误用周五柱子)
                _same_day = (_day_arr[i] == _day_arr[i-1]) if i >= 1 and i < len(_day_arr) else False
                # 2026-10-08 用户: 开盘忽略期的柱子不参与跟随计算 (i-1在忽略期内则不跟随)
                _prev_in_ignore = ((i-1) - _day_start) < _ign_n_wk if i >= 1 else True
                if i >= 1 and _same_day and not _frag_paused_wk and not _prev_in_ignore:
                    # 2026-10-07 用户: 去掉第一根长度限制，只要求同色真红/真蓝
                    # 2026-10-08 用户: 谨慎跟随 — 前面有2+根不达标真柱(|h|<8)时，要3根递增且第三根|h|>=10
                    _fr = is_true_red(i) and abs(hist[i]) >= FOLLOW_MIN_LEN and \
                          is_true_red(i-1)
                    _fb = is_true_blue(i) and abs(hist[i]) >= FOLLOW_MIN_LEN and \
                          is_true_blue(i-1)
                    _growing = abs(hist[i]) > abs(hist[i-1])
                    # 顺强趋势跟随 (2026-10-19 用户定版): 从开盘cumslope, 11:00-15:00, 红<-0.20/蓝>+0.15
                    # 强趋势只影响开仓信号, 不碰平仓/跟踪状态
                    _st_sig = False
                    _cs_st = _9m_cumslope[i] if i < len(_9m_cumslope) else np.nan
                    _st_gate_red = (hm >= "11:00" and hm < "15:00" and not np.isnan(_cs_st) and _cs_st < -0.20)
                    if _st_gate_red:
                        # 灰零下算真红: hist<-3即可 (真红或灰红)
                        _st_red_i = hist[i] < -3 and hist[i] == hist[i]
                        _st_red_prev = hist[i-1] < -3 and hist[i-1] == hist[i-1]
                        # 放宽松版两根跟随: 两根零下, 第二根|h|>=8且递增 + 新波(前8根有正柱)
                        _st_fr = _st_red_i and abs(hist[i]) >= FOLLOW_MIN_LEN and _st_red_prev and _growing
                        if _st_fr:
                            _has_pos = False
                            for _stq in range(max(start_idx, i-9), i-1):
                                _hq = hist[_stq]
                                if _hq == _hq and _hq > 0:
                                    _has_pos = True
                                    break
                            if not _has_pos:
                                _st_fr = False
                        # 新波条件①: 前根hist<0, 本根递增且|h|>=6 + 新波
                        _st_new1 = hist[i-1] < 0 and hist[i] < 0 and abs(hist[i]) > abs(hist[i-1]) and abs(hist[i]) >= 6
                        if _st_new1:
                            _has_pos = False
                            for _stq in range(max(start_idx, i-9), i-1):
                                _hq = hist[_stq]
                                if _hq == _hq and _hq > 0:
                                    _has_pos = True
                                    break
                            if not _has_pos:
                                _st_new1 = False
                        # 新波条件②: 新波第一根|h|>=6 (前8根安静 |h|<=3)
                        _st_new2 = False
                        if hist[i] < 0 and abs(hist[i]) >= 6:
                            _st_quiet = True
                            for _stq in range(max(start_idx, i-8), i):
                                _hq = hist[_stq]
                                if not (_hq == _hq and abs(_hq) <= 3):
                                    _st_quiet = False
                                    break
                            _st_new2 = _st_quiet and (i-8 >= start_idx)
                        if _st_fr or _st_new1 or _st_new2:
                            _st_sig = True
                    # (2026-10-06: 新信号优先于仓位，持反向仓时先平再开，与watcher.py同步)
                    # 2026-10-18: 已持有put时不重复开仓
                    if _st_sig and pos != "put" and not _bigk_skip_wk and not k3_skip:
                        if pos == "call":
                            actions.append((i, tstr, "平", "call", px, "跟随翻仓(持call遇强趋势跟随)"))
                            pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; last_action_bar = i
                        actions.append((i, tstr, "开", "put", px, "强趋势跟随P"))
                        pos = "put"; _init_stop_vars("put", px, i); _was_above_zero_wk = False; _was_below_zero_wk = False; _call_gap_wk = 0; _put_gap_wk = 0; last_action_bar = i; _decay_entry_hist = 0
                        _fav_peak = abs(hist[i]) if (not np.isnan(hist[i]) and hist[i] < 0) else 0; _fav_sign = -1 if (not np.isnan(hist[i]) and hist[i] < 0) else 0
                        _follow_entry_bar = i; _follow_entry_hist = abs(hist[i]); _follow_do_div_wk = _follow_do_div; _follow_entry_dir_wk = pos; _follow_entry_peak_wk = _fwp; _follow_entry_spy_wk = close[i]; _follow_entry_date_wk = _day_arr[i]
                        wait_red = wait_blue = False
                    elif _fr and _growing and not _bigk_skip_wk and not k3_skip:
                        if pos == "call":
                            actions.append((i, tstr, "平", "call", px, "跟随翻仓(持call遇红跟随)"))
                            pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; last_action_bar = i
                        actions.append((i, tstr, "开", "put", px, "两根长红跟随"))
                        pos = "put"; _init_stop_vars("put", px, i); _was_above_zero_wk = False; _was_below_zero_wk = False; _call_gap_wk = 0; _put_gap_wk = 0; last_action_bar = i; _decay_entry_hist = 0
                        _fav_peak = abs(hist[i]) if (not np.isnan(hist[i]) and hist[i] < 0) else 0; _fav_sign = -1 if (not np.isnan(hist[i]) and hist[i] < 0) else 0
                        _follow_entry_bar = i; _follow_entry_hist = abs(hist[i]); _follow_do_div_wk = _follow_do_div; _follow_entry_dir_wk = pos; _follow_entry_peak_wk = _fwp; _follow_entry_spy_wk = close[i]; _follow_entry_date_wk = _day_arr[i]
                        wait_red = wait_blue = False
                    # 顺强趋势跟随(上涨对称版, 2026-10-19 用户定版): 11:00-15:00, cumslope>+0.15, 放宽松开call
                    _st_sig_up = False
                    _st_gate_blue = (hm >= "11:00" and hm < "15:00" and not np.isnan(_cs_st) and _cs_st > 0.15)
                    if _st_gate_blue:
                        # 灰零上算真蓝: hist>3即可 (真蓝或灰蓝)
                        _st_blue_i = hist[i] > 3 and hist[i] == hist[i]
                        _st_blue_prev = hist[i-1] > 3 and hist[i-1] == hist[i-1]
                        # 放宽松版两根跟随: 两根零上, 第二根|h|>=8且递增 + 新波(前8根有负柱)
                        _st_fr_up = _st_blue_i and abs(hist[i]) >= FOLLOW_MIN_LEN and _st_blue_prev and _growing
                        if _st_fr_up:
                            _has_neg = False
                            for _stq in range(max(start_idx, i-9), i-1):
                                _hq = hist[_stq]
                                if _hq == _hq and _hq < 0:
                                    _has_neg = True
                                    break
                            if not _has_neg:
                                _st_fr_up = False
                        # 新波条件①: 前根hist>0, 本根递增且|h|>=6 + 新波
                        _st_new1_up = hist[i-1] > 0 and hist[i] > 0 and abs(hist[i]) > abs(hist[i-1]) and abs(hist[i]) >= 6
                        if _st_new1_up:
                            _has_neg = False
                            for _stq in range(max(start_idx, i-9), i-1):
                                _hq = hist[_stq]
                                if _hq == _hq and _hq < 0:
                                    _has_neg = True
                                    break
                            if not _has_neg:
                                _st_new1_up = False
                        # 新波条件②: 新波第一根|h|>=6 (前8根安静 |h|<=3)
                        _st_new2_up = False
                        if hist[i] > 0 and abs(hist[i]) >= 6:
                            _st_quiet_up = True
                            for _stq in range(max(start_idx, i-8), i):
                                _hq = hist[_stq]
                                if not (_hq == _hq and abs(_hq) <= 3):
                                    _st_quiet_up = False
                                    break
                            _st_new2_up = _st_quiet_up and (i-8 >= start_idx)
                        if _st_fr_up or _st_new1_up or _st_new2_up:
                            _st_sig_up = True
                    if _st_sig_up and pos != "call" and not _bigk_skip_wk:
                        if pos == "put":
                            actions.append((i, tstr, "平", "put", px, "跟随翻仓(持put遇强趋势跟随)"))
                            pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; last_action_bar = i
                        actions.append((i, tstr, "开", "call", px, "强趋势跟随C"))
                        pos = "call"; _init_stop_vars("call", px, i); _was_above_zero_wk = False; _was_below_zero_wk = False; _call_gap_wk = 0; _put_gap_wk = 0; last_action_bar = i; _decay_entry_hist = 0
                        _fav_peak = abs(hist[i]) if (not np.isnan(hist[i]) and hist[i] > 0) else 0; _fav_sign = 1 if (not np.isnan(hist[i]) and hist[i] > 0) else 0
                        _follow_entry_bar = i; _follow_entry_hist = abs(hist[i]); _follow_do_div_wk = _follow_do_div; _follow_entry_dir_wk = pos; _follow_entry_peak_wk = _fwp; _follow_entry_spy_wk = close[i]; _follow_entry_date_wk = _day_arr[i]
                        wait_red = wait_blue = False
                    elif _fb and _growing and not _bigk_skip_wk:
                        if pos == "put":
                            actions.append((i, tstr, "平", "put", px, "跟随翻仓(持put遇蓝跟随)"))
                            pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; last_action_bar = i
                        actions.append((i, tstr, "开", "call", px, "两根长蓝跟随"))
                        pos = "call"; _init_stop_vars("call", px, i); _was_above_zero_wk = False; _was_below_zero_wk = False; _call_gap_wk = 0; _put_gap_wk = 0; last_action_bar = i; _decay_entry_hist = 0
                        _fav_peak = abs(hist[i]) if (not np.isnan(hist[i]) and hist[i] > 0) else 0; _fav_sign = 1 if (not np.isnan(hist[i]) and hist[i] > 0) else 0
                        _follow_entry_bar = i; _follow_entry_hist = abs(hist[i]); _follow_do_div_wk = _follow_do_div; _follow_entry_dir_wk = pos; _follow_entry_peak_wk = _fwp; _follow_entry_spy_wk = close[i]; _follow_entry_date_wk = _day_arr[i]
                        wait_red = wait_blue = False
                    elif (not bool(q_down[i-1]) and not bool(q_up[i-1]) and abs(hist[i-1]) >= FOLLOW_MIN_LEN):
                        # 灰后跟随 (2026-10-04 用户; 2026-10-07 用户重构: 要求真色, 自适应20%/翻倍, 三连增比第一根)
                        _gdir = None
                        _gh1 = abs(hist[i-1])
                        _need_g = 2.0 if _gh1 < 5 else 1.2
                        # 第二根必须是真色
                        _is_tr = is_true_red(i); _is_tb = is_true_blue(i)
                        if (_is_tr or _is_tb) and abs(hist[i]) >= _gh1 * _need_g and abs(hist[i]) >= FOLLOW_MIN_LEN:
                            _gdir = "put" if _is_tr else "call"
                        # 三连增: 第三根真色比第一根(灰可), 同阈值
                        elif i >= 2 and not _gdir:
                            _h0, _h1, _h2 = hist[i-2], hist[i-1], hist[i]
                            _g0_gray = not bool(q_down[i-2]) and not bool(q_up[i-2])
                            _g1_gray = not bool(q_down[i-1]) and not bool(q_up[i-1])
                            if (_g0_gray or _g1_gray) and abs(_h0) >= FOLLOW_MIN_LEN \
                               and abs(_h2) > abs(_h1) > abs(_h0) \
                               and (_is_tr or _is_tb):
                                _need3 = 2.0 if abs(_h0) < 5 else 1.2
                                if abs(_h2) >= abs(_h0) * _need3:
                                    _gdir = "put" if _is_tr else "call"
                        if _gdir:
                            # (2026-10-06: 新信号优先于仓位，持反向仓时先平再开，与watcher.py同步)
                            if (_gdir == "put" and pos == "call") or (_gdir == "call" and pos == "put"):
                                actions.append((i, tstr, "平", pos, px, f"跟随翻仓(持{pos}遇灰后跟随)"))
                                pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; last_action_bar = i
                            actions.append((i, tstr, "开", _gdir, px, "灰后跟随"))
                            pos = _gdir; _init_stop_vars(_gdir, px, i); last_action_bar = i; _decay_entry_hist = 0
                            _fav_peak = abs(hist[i]) if (not np.isnan(hist[i]) and ((_gdir == "put" and hist[i] < 0) or (_gdir == "call" and hist[i] > 0))) else 0; _fav_sign = -1 if _gdir == "put" else 1
                            _follow_entry_bar = i; _follow_entry_hist = abs(hist[i]); _follow_do_div_wk = _follow_do_div; _follow_entry_dir_wk = pos; _follow_entry_peak_wk = _fwp; _follow_entry_spy_wk = close[i]; _follow_entry_date_wk = _day_arr[i]
                            wait_red = wait_blue = False
                    else:
                        # 长波降低开仓门槛 (2026-10-03 用户): 长波里单根长红或两根中等红即开
                        if _long_put_sig:
                            actions.append((i, tstr, "开", "put", px, "长波跟随红"))
                            pos = "put"; _init_stop_vars("put", px, i); _was_above_zero_wk = False; _was_below_zero_wk = False; _call_gap_wk = 0; _put_gap_wk = 0; last_action_bar = i; _decay_entry_hist = 0
                            _fav_peak = abs(hist[i]) if (not np.isnan(hist[i]) and hist[i] < 0) else 0; _fav_sign = -1 if (not np.isnan(hist[i]) and hist[i] < 0) else 0
                            _follow_entry_bar = i; _follow_entry_hist = abs(hist[i]); _follow_do_div_wk = _follow_do_div; _follow_entry_dir_wk = pos; _follow_entry_peak_wk = _fwp; _follow_entry_spy_wk = close[i]; _follow_entry_date_wk = _day_arr[i]
                            wait_red = wait_blue = False
                        elif _long_call_sig:
                            actions.append((i, tstr, "开", "call", px, "长波跟随蓝"))
                            pos = "call"; _init_stop_vars("call", px, i); _was_above_zero_wk = False; _was_below_zero_wk = False; _call_gap_wk = 0; _put_gap_wk = 0; last_action_bar = i; _decay_entry_hist = 0
                            _fav_peak = abs(hist[i]) if (not np.isnan(hist[i]) and hist[i] > 0) else 0; _fav_sign = 1 if (not np.isnan(hist[i]) and hist[i] > 0) else 0
                            _follow_entry_bar = i; _follow_entry_hist = abs(hist[i]); _follow_do_div_wk = _follow_do_div; _follow_entry_dir_wk = pos; _follow_entry_peak_wk = _fwp; _follow_entry_spy_wk = close[i]; _follow_entry_date_wk = _day_arr[i]
                            wait_red = wait_blue = False
                        # 续波跟随 (2026-10-03 用户): 空仓时上一波红+新红柱 -> 跟put; 蓝对称 (受冷静期限制)
                        # (2026-10-06: 新信号优先于仓位，持反向仓时先平再开，与watcher.py同步)
                        elif _cont_put:
                            if pos == "call":
                                actions.append((i, tstr, "平", "call", px, "续波跟随翻仓(持call)"))
                                pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; last_action_bar = i
                            actions.append((i, tstr, "开", "put", px, "续波跟随红"))
                            pos = "put"; _init_stop_vars("put", px, i); _was_above_zero_wk = False; _was_below_zero_wk = False; _call_gap_wk = 0; _put_gap_wk = 0; last_action_bar = i; _decay_entry_hist = 0
                            _fav_peak = abs(hist[i]) if (not np.isnan(hist[i]) and hist[i] < 0) else 0; _fav_sign = -1 if (not np.isnan(hist[i]) and hist[i] < 0) else 0
                            _follow_entry_bar = i; _follow_entry_hist = abs(hist[i]); _follow_do_div_wk = _follow_do_div; _follow_entry_dir_wk = pos; _follow_entry_peak_wk = _fwp; _follow_entry_spy_wk = close[i]; _follow_entry_date_wk = _day_arr[i]
                            wait_red = wait_blue = False
                        elif _cont_call:
                            if pos == "put":
                                actions.append((i, tstr, "平", "put", px, "续波跟随翻仓(持put)"))
                                pos = None; _was_above_zero_wk = False; _was_below_zero_wk = False; last_action_bar = i
                            actions.append((i, tstr, "开", "call", px, "续波跟随蓝"))
                            pos = "call"; _init_stop_vars("call", px, i); _was_above_zero_wk = False; _was_below_zero_wk = False; _call_gap_wk = 0; _put_gap_wk = 0; last_action_bar = i; _decay_entry_hist = 0
                            _fav_peak = abs(hist[i]) if (not np.isnan(hist[i]) and hist[i] > 0) else 0; _fav_sign = 1 if (not np.isnan(hist[i]) and hist[i] > 0) else 0
                            _follow_entry_bar = i; _follow_entry_hist = abs(hist[i]); _follow_do_div_wk = _follow_do_div; _follow_entry_dir_wk = pos; _follow_entry_peak_wk = _fwp; _follow_entry_spy_wk = close[i]; _follow_entry_date_wk = _day_arr[i]
                            wait_red = wait_blue = False

    # 保存heat_arr/bins供dashboard画热力背景（取循环最后的值）
    # _heat_sm_cache存blur过的final output（信号实际用的）
    try:
        _heat_arr_cache = _heat_arr.copy() if _heat_arr is not None else None
        _heat_bins_cache = _heat_bins.copy() if _heat_bins is not None else None
        if _heat_arr is not None:
            try:
                from scipy.ndimage import gaussian_filter1d as _gf2
                _heat_sm_cache = _gf2(_heat_arr, sigma=1.0, mode='constant', cval=0)
            except:
                _heat_sm_cache = _heat_arr.copy()
    except NameError:
        pass
    return actions
