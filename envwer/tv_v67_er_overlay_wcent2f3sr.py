# _f3 fork (build_wcent2f3_fork.py 生成): wcent2f2 + ce_f3 段内状态跟随
# 开关 (默认关, 关时与 wcent2f2 逐笔等价)。F2/RF 代码原文未动。
# _f2 fork (build_wcent2f2_fork.py 生成): wcent2 + ce_f2 段内止损后
# CE 变向重开开关 (默认关, 关时与 wcent2 逐笔等价)。RF 代码原文未动。
#!/usr/bin/env python3
"""趋势接管版 (Step 19): baseline 参数 + 只开 Friction (U-Turn 全程 OFF) + 趋势接管。

tv_v67_friction_protected.py 的复制, 只在保护定义处改动; 原模块不动。
用户纠正 (2026-11-08): Step 9 的“持趋势/边缘仓不让开 BO”是过度保护;
但完全放开也不行 (冒烟: BO 翻掉趋势仓致趋势单 3 单提前出场 + 1 单出场不同)。
正确的保护 = 分档趋势接管:
1. 同 bar 趋势信号撞 BO/REV 信号: 趋势赢, BO/REV 不入场、不启动接管、不武装反手。
2. 持仓中趋势信号出现: 立即按趋势单处理 -- 反向翻仓; 同向平 BO/REV/边缘仓 +
   在趋势信号 bar 重开趋势仓; 空仓直接开。BO/REV 止损位、接管状态、反手武装随之清掉。
   (影子基线: 只有基线会开趋势单时我方才开; 基线已同向持仓时我方不开。)
3. 趋势单出场 (EXIT T / 止损 / EOD) 与基线一致: EXIT T 只被趋势信号压住
   (不被 BO/REV 压住); EXIT BO 只平 BO/REV 仓, 不碰趋势/边缘仓。
4. BO/REV 入场分档:
   - 趋势里 (持 pos_mode == "trend"): BO 铁定不让碰趋势单 -- 入场继续挡。
   - 趋势外 (持 "edge" 或空仓): BO 照常开, BO 可以翻掉边缘仓。
   门 = allow_bo_entry = (not trend_special) and (pos_mode != "trend")。
5. 接管启动分档: BO/REV 信号启动接管只在趋势信号当根或持趋势仓时被挡;
   持边缘仓/空仓时照常启动接管 (Pine 原样)。
   趋势单是否仍逐单一致由 check_takeover.py 实测, 本模块不保证。
入口: simulate(df, ind, tf_min, trend_entry=True, use_uturn=True, use_bo=True)。

Pine SPY_Gaussian_Breakout_v6_7 移植 (1m): Envelope + Friction Breakout + U-Turn。

基于 tv_envelope_v39_withtrend_trendflip.py 的 Envelope 指标与结算口径,
U-Turn 部分照搬 tv_envelope_v64_gravity.py (参数与 v6.7 一字不差),
Friction Breakout (第 11/12 组) 按 Pine 原样新写。

Pine 要点 (以文件为准, SPY_Gaussian_Breakout_v6_7.pine):
- Envelope: 高斯 30/3.5、ATR 12 (Gap-Adjusted)、mult 1.05、slope 7、
  EMA 动量 asym 1.35 / fastEMA 5 / momLookback 15 / momSens 0.55 /
  maxShift 0.50 ATR。(已改回 baseline 值; v6.7 为 1.15/16/1.50)
- 趋势过滤: ER 17/0.35、DI 8、ADX 12/30、takeoverSlope 0.13;
  regime policy "With trend only"; 趋势入场 plain 上升沿 (v6.7 拿掉了
  v6.4 的 pending 机制, 回到 v39 的 edge-triggered)。
- U-Turn (v6.7 = v6.4): 候选 (exc 8、extreme 10、minExc 2.2 ATR、wick 0.04、
  confirm window 8、cooldown 2); Occupancy = (close - lower[1]) /
  (upper[1]-lower[1]) (无 Gravity, 最终通道 = raw 通道), EMA 8、compare 8、
  threshold 0.28、confirmBars 1、occValidBars=3; utBlockBars=3、hardFail=false、
  progressFail=true (validationBars 2、minProgress 0.40 ATR)、
  exitOnFailure=false、allowOppositeUT=true、utRespectRegime=false。
- Friction Breakout (第 11 组): Wick 模式触中线; 过去 10 根 ≥6 根触线且
  10 根高低幅度 ≤5.0 ATR -> 冻结高低点 (有效期 5 根); 突破 = 收盘破冻结区间
  (±0.10 ATR 缓冲) 且逃出高斯通道 (收在外 1 根、无触线 1 根、离中线 ≥1.0 ATR);
  接管最高优先、封锁反向高斯/UT 信号、最多 10 根; 结束 = 回通道内 3 根
  (需重新摩擦 5 根里 ≥4 根) / 回冻结区间内 3 根 / 超时 / EOD。
  boImpulseATR / boCloseOpposite 在 Pine 逻辑里未用, 本移植也不用。
- BO Reversal (第 12 组): 接管中价格跌回中线另一侧 + 1 根逆势 K 线 ->
  平 BO 仓并武装反向; 2 根内向反方向逃出通道 (2 根收在外、≥1.2 ATR)
  则立即反手。
- 入场优先级: REV > BO > Trend > Gaussian/U-turn; SL 1.5xATR、无 TP、
  reverse=true; EOD 15:45 停开、15:57 平; 信号 bar 收盘执行; 结算 -100 Gamma。

ER 覆盖层 _nb 改动 (user 2026-11-10 23:20–23:25 澄清后定版, fork 自
_brk, 旧文件不动): 不分识别/入场两段 (识别根即入场, swap/趋势让位照旧),
段内屏蔽高斯照旧。唯一改动 = ER 出场根不屏蔽: 段尾平/止盈平的那一根
高斯可同根开仓 (删旧引擎「seg_ended_bar 亦屏蔽」)。实现口径: 本根若有
ER 仓被平 (ER_END/ER_TP/SL 等, 以 er_last_close 判定), 该根不屏蔽;
er_cfg["exit_caliber"]: "new" (默认) = 定版口径; "old" = 旧口径
(段内+段结束根全屏蔽, 与 _brk 逐笔全等, 保真用)。
计数 seg_gauss_entries = 段窗口内 (seg_active 或段结束根) 高斯 (edge/ut)
实际开仓数 -- 定版口径下即出场根同根开仓数 (old 口径下恒 0)。

入口: simulate(df, ind, tf_min, trend_entry=True, use_uturn=True, use_bo=True)。
"""
import os
import numpy as np
import pandas as pd

import tv_envelope_v39_withtrend_trendflip as tv

# ---------------- v6.7 参数 (Pine 原值) ----------------
GAUSS_LEN = 30
GAUSS_BW = 3.5
ATR_LEN = 12
MULT = 1.05
SLOPE_LEN = 7
ASYM_SHIFT = 1.35  # baseline; v6.7 为 1.15
FAST_EMA_LEN = 5
MOM_LOOKBACK = 15  # baseline; v6.7 为 16
MOM_SENS = 0.55
MAX_SHIFT_ATR = 0.50  # baseline; v6.7 为 1.50
EDGE_PCT = 20.0
MAX_OUTSIDE = 0.55
ER_LEN = 17
ER_TH = 0.35
DI_LEN = 8
ADX_SMOOTH = 12
ADX_TH = 30
TAKEOVER_SLOPE = 0.13
SL_ATR = 1.5

# ---- 止损变体 (Step 36 SL sweep; 默认 = 原 1.5xATR 价格止损) ----
# SL_MODE: "atr" 价格止损 | "roi" 收益率止损(替代价格止损) | "none" 无止损
#          "trail" 移动止损(1.5xATR 底 + 有利 X 后锁定入场+0.5xATR)
#          "breakeven" 保本止损(1.5xATR 底 + 浮盈 +T% 后止损移到入场价)
#          "time" 时间止损(1.5xATR 底 + 持仓 N 根仍不盈利就平)
#          "chand" Chandelier(极值 -/+ 1.5xATR, 跟随极值移动)
#          "split" 趋势/边缘分开止损 (trend 用 SL_SPLIT_TREND, 其余用 SL_SPLIT_EDGE)
SL_MODE = "atr"
SL_K = 1.5            # atr/chand 模式宽度 (xATR); trail/be/time 的底止损宽度
SL_ROI_T = 20.0       # roi 模式阈值 (%): ROI <= -T 触发
SL_TRAIL_X = 1.5      # trail 触发: 有利 excursion >= X xATR
SL_TRAIL_LOCK = 0.5   # trail 锁定: 入场价 + 0.5 xATR
SL_BE_T = 10.0        # breakeven 触发: 未实现 ROI >= +T%
SL_TIME_N = 10        # time: 持仓 N 根后仍不盈利就平
SL_SPLIT_TREND = 2.0  # split: 趋势单宽度 (xATR)
SL_SPLIT_EDGE = 1.5   # split: 边缘/BO/REV 单宽度 (xATR)


def _roi_pct(direction, d_spy):
    """Gamma ROI (%), 与 gamma_roi_100pct 同公式 (不含 -100 下限; 止损区用不到下限)。"""
    if direction == 1:  # long = call
        r = 9.88 * d_spy + 0.311 * d_spy * d_spy
        if d_spy < 0:
            r = min(r, 0.0)
    else:  # short = put
        r = -9.88 * d_spy + 0.311 * d_spy * d_spy
        if d_spy > 0:
            r = min(r, 0.0)
    return r


def _roi_stop_delta(direction, T):
    """ROI = -T% 时的 ΔSPY (解 0.311Δ² ± 9.88Δ + T = 0, 取近根)。"""
    import math as _m
    disc = 9.88 * 9.88 - 4 * 0.311 * T
    if disc < 0:
        return None
    sq = _m.sqrt(disc)
    if direction == 1:  # long: Δ<0
        return (-9.88 + sq) / (2 * 0.311)
    return (9.88 - sq) / (2 * 0.311)  # short: Δ>0

# U-Turn (v6.7 = v6.4)
UT_EXC_LOOKBACK = 8
UT_EXTREME_LOOKBACK = 10
UT_MIN_EXC_ATR = 2.2
UT_WICK_RATIO = 0.04
UT_CONFIRM_BARS = 8
UT_COOLDOWN = 2
OCC_LOOKBACK = 8
OCC_SHIFT_THRESHOLD = 0.28
OCC_CONFIRM_BARS = 1
OCC_COMPARE_BARS = 8
OCC_SMOOTHING = "EMA"
OCC_VALID_BARS = 3
UT_BLOCK_BARS = 3
UT_USE_HARD_FAIL = False
UT_FAILURE_BUFFER = 0.10
UT_USE_PROGRESS_FAIL = True
UT_VALIDATION_BARS = 2
UT_MIN_PROGRESS_ATR = 0.40
UT_EXIT_ON_FAILURE = False
UT_ALLOW_OPPOSITE_UT = True
UT_RESPECT_REGIME = False

# Friction Breakout (v6.7 第 11/12 组)
BO_RANGE_LOOKBACK = 10
BO_MIN_CROSSES = 6
BO_MAX_RANGE_ATR = 5.0
BO_BUFFER_ATR = 0.10
BO_OUTSIDE_BARS = 1
BO_NO_CROSS_BARS = 1
BO_MIN_ESCAPE_ATR = 1.0
BO_INSIDE_BARS = 3
BO_FAILURE_BARS = 3
BO_MAX_BARS = 10
BO_RANGE_MEMORY = 5
BO_EXIT_ON_MID_FAILURE = True
BO_FAILURE_MOMENTUM_BARS = 1
BO_REVERSE_OUTSIDE_BARS = 2
BO_REVERSE_DISTANCE_ATR = 1.2
BO_REVERSE_WINDOW = 2
BO_REQUIRE_FRICTION_RECOVERY = True
BO_RECOVERY_FRICTION_LOOKBACK = 5
BO_RECOVERY_MIN_FRICTION = 4
BO_IGNORE_OUTSIDE = True
BO_ENTER_DIRECTION = True
BO_BLOCK_OPPOSITE = True

MINTICK = 0.01

START_DATE = os.environ.get("TV_START", "2026-11-02")
END_DATE = os.environ.get("TV_END", "2026-11-04")

V67_STATS = dict(trend_edges_long=0, trend_edges_short=0,
                 trend_only_entries=0,
                 ut_candidates_top=0, ut_candidates_bottom=0,
                 ut_confirms_s=0, ut_confirms_b=0,
                 ut_entries=0, ut_failures=0, ut_failed_exits=0,
                 bo_freezes=0, bo_signals_up=0, bo_signals_down=0,
                 bo_entries=0, rev_entries=0, rev_arms=0,
                 bo_ends_recovered=0, bo_ends_failed=0, bo_ends_timeout=0,
                 trend_protect_convert=0, trend_protect_flip_bo=0,
                 trend_protect_bo_blocked_sig=0, trend_protect_bo_blocked_pos=0,
                 trend_protect_skip_same=0, trend_protect_skip_trend=0,
                 entry_modes={}, entry_seg={})


ER_ARRAYS = {}  # ER 覆盖层: {n: ERn 数组} (runner 按 ind['close'] 注入, 公式同 er_engine)
ER_DIR_ARRAY = None   # WER 深研 R3b: 逐根方向符号数组 (dir_mode="arr" 时用)
ER_GATE_ARRAY = None  # WER 深研 R3c: 逐根质量门数组 (gate_min 给定时用)
CE_STATE = None  # _wcent: 自由运行 CE 状态数组 (收盘后状态 ±1; runner 注入, 与 compute_ce_dye.run_ce 逐字同源)
CE_H = None      # _wcent: 自由运行 CE 健康度数组 (hh, ATR 单位; runner 注入)
CE_CST = None    # _wcent2: 确认后 CE 状态数组 (新状态连续 cf 根才生效; runner 注入; None 回退 CE_STATE)
ER_INFO = {}    # ER 覆盖层回传: segments / counts
# _wdose 改动 (fork 自 _wdeep): 突变支 eff 门槛按前 W 根 WER 均值水位
# 连续降低 -- eff_to = er_to - min(dose_CAP, max(0, dose_R - avgW));
# dose_R=None (默认) 时与 _wdeep 逐笔等价。埋点 ER_INFO["dose_drops"]。
# _wcent 改动 (fork 自 _wdose): CE 动态趋势识别进覆盖层入场/方向侧 --
# er_cfg["ce_method"] "" 关 (默认, 与 _wdose 逐笔等价) | A 段方向改用触发根
# CE 状态 | B CE 状态须 == 净位移方向才开段 | C 净位移定方向、入场等 CE
# 转同向 (<=ce_W 根, 超时弃) | E 段内自由 CE 状态翻到与段反向即出段 (与
# WER 出段先到先出; 与段锚 CE 线出段 _wchand 不同物) | F A + 段内 CE 翻转
# 同根翻仓跟随 (段/屏蔽延续)。埋点 ce_counts / ce_events / ce_flip_map。
# _wcent2 改动 (fork 自 _wcent): 段内实时方向复核 -- E/F 触发状态改用
# 确认后状态 ce_cst (CE_CST 注入, None 回退 CE_STATE); 新增 ce_method
# "RF" = 快照入场 + 段内确认 CE 翻转同根翻仓 (机制同 F, 仅入场方向源不同)。
# _sumdrop/_slope 改动 (2026-11-12): 入段降幅组合制式可切 --
# er_cfg["drop_mode"] "max" (默认, 现行取大, 与上游逐笔等价) | "sum" =
# dose + ramp (+slope) 叠加, 可加 er_cfg["drop_cap"] 总封顶; sum/slope 路径
# eff_to 硬下限 er_cfg["eff_floor"] (默认 0.40)。er_cfg["slope_on"] 开斜率
# 第三路: s = |近 slope_W 根 (Δmid/ATR) 均值| (高斯中线 mid, ATR 归一),
# slope_drop = slope_CAP * clamp((s - slope_s0)/(slope_s1 - slope_s0))。
# 埋点 ER_INFO["drop_trig"] / ["drop_counts"] / ["slope_counts"]。


def rma(x, n):
    """Pine ta.rma: 前 n-1 根 na, 第 n 根 = 前 n 根 SMA, 之后 alpha=1/n 平滑."""
    x = np.asarray(x, dtype=float)
    out = np.full(len(x), np.nan)
    if len(x) < n:
        return out
    out[n - 1] = np.nanmean(x[:n])
    a = 1.0 / n
    for i in range(n, len(x)):
        out[i] = a * x[i] + (1 - a) * out[i - 1]
    return out


def pine_ema(x, n):
    """Pine ta.ema: 首值 = 前 n 根 SMA, 之后 alpha=2/(n+1) 递推; 源 na 时沿用上一值."""
    x = np.asarray(x, dtype=float)
    out = np.full(len(x), np.nan)
    a = 2.0 / (n + 1)
    for i in range(len(x)):
        if i == 0 or np.isnan(out[i - 1]):
            if i >= n - 1:
                w = x[i - n + 1:i + 1]
                if not np.any(np.isnan(w)):
                    out[i] = np.mean(w)
        else:
            if np.isnan(x[i]):
                out[i] = out[i - 1]
            else:
                out[i] = a * x[i] + (1 - a) * out[i - 1]
    return out


def compute_indicators(df):
    """df: 1m bars (t/open/high/low/close)。与 v39 同款, 常数已改回 baseline
    (ASYM 1.35 / MOM_LOOKBACK 15 / MAX_SHIFT_ATR 0.50)。"""
    o = df["open"].values.astype(float)
    h = df["high"].values.astype(float)
    l = df["low"].values.astype(float)
    c = df["close"].values.astype(float)
    n = len(df)
    times = df["t"]
    sm = times.dt.hour.values * 60 + times.dt.minute.values
    dates = times.dt.strftime("%Y-%m-%d").values
    # ---- Gap-Adjusted ATR (与 v39 一致; DMI 用正常 TR) ----
    prev_c = np.empty(n); prev_c[0] = np.nan; prev_c[1:] = c[:-1]
    tr_normal = np.maximum(h - l, np.maximum(np.abs(h - prev_c), np.abs(l - prev_c)))
    tr_normal[0] = h[0] - l[0]
    in_rth = (sm >= 570) & (sm < 960)
    first_rth = np.zeros(n, dtype=bool)
    for i in range(n):
        if not in_rth[i]:
            continue
        if i == 0 or (not in_rth[i - 1]) or (dates[i] != dates[i - 1]):
            first_rth[i] = True
    tr_sel = np.where(first_rth, h - l, tr_normal)
    atr = rma(tr_sel, ATR_LEN)
    # ---- Gaussian mid (causal, 最新根权重最大) ----
    w = np.exp(-0.5 * (np.arange(GAUSS_LEN) / GAUSS_BW) ** 2)
    mid = np.full(n, np.nan)
    for i in range(n):
        k = min(i + 1, GAUSS_LEN)
        mid[i] = np.sum(c[i - k + 1:i + 1] * w[:k][::-1]) / np.sum(w[:k])
    slope = np.full(n, np.nan)
    slope[SLOPE_LEN:] = (mid[SLOPE_LEN:] - mid[:-SLOPE_LEN]) / SLOPE_LEN
    # ---- EMA 动量平移 ----
    ema = np.full(n, np.nan)
    if n > 0:
        ema[0] = c[0]
        a_ema = 2.0 / (FAST_EMA_LEN + 1)
        for i in range(1, n):
            ema[i] = a_ema * c[i] + (1 - a_ema) * ema[i - 1]
    mom = np.full(n, np.nan)
    mom[MOM_LOOKBACK:] = (ema[MOM_LOOKBACK:] - ema[:-MOM_LOOKBACK]) / MOM_LOOKBACK
    with np.errstate(invalid="ignore", divide="ignore"):
        nmom = mom / (atr * MOM_SENS)
        smooth = nmom / np.sqrt(1.0 + nmom * nmom)
        raw_shift = atr * MULT * ASYM_SHIFT * smooth
        shift = np.clip(raw_shift, -MAX_SHIFT_ATR * atr, MAX_SHIFT_ATR * atr)
    width = MULT * atr
    chan_mid = mid + shift
    upper = chan_mid + width
    lower = chan_mid - width
    # ---- ER / DMI (正常 TR, 与 v39 一致) ----
    chg = np.abs(np.diff(c, prepend=np.nan))
    chg[0] = 0.0
    er = np.full(n, np.nan)
    for i in range(ER_LEN, n):
        noise = np.sum(chg[i - ER_LEN + 1:i + 1])
        er[i] = abs(c[i] - c[i - ER_LEN]) / noise if noise > 0 else 0.0
    prev_h = np.empty(n); prev_h[0] = np.nan; prev_h[1:] = h[:-1]
    prev_l = np.empty(n); prev_l[0] = np.nan; prev_l[1:] = l[:-1]
    up = h - prev_h; dn = prev_l - l
    up[0] = 0.0; dn[0] = 0.0
    pdm = np.where((up > 0) & (up > dn), up, 0.0)
    mdm = np.where((dn > 0) & (dn > up), dn, 0.0)
    r_tr = rma(tr_normal, DI_LEN)
    r_pdm = rma(pdm, DI_LEN)
    r_mdm = rma(mdm, DI_LEN)
    with np.errstate(invalid="ignore", divide="ignore"):
        dip = 100 * r_pdm / r_tr
        dim = 100 * r_mdm / r_tr
        dx = 100 * np.abs(dip - dim) / (dip + dim)
    adx = rma(np.where(np.isnan(dx), 0.0, dx), ADX_SMOOTH)
    adx[:DI_LEN + ADX_SMOOTH - 1] = np.nan
    return dict(open=o, high=h, low=l, close=c, atr=atr, mid=mid, slope=slope,
                upper=upper, lower=lower, chan_mid=chan_mid, er=er, adx=adx,
                first_rth=first_rth)


def compute_occupancy(ind, occ_lookback=None, occ_compare_bars=None,
                      occ_shift_threshold=None, occ_valid_bars=None):
    """U-Turn occupancy (v64 同款; v6.7 无 Gravity, 最终通道 = raw 通道)。
    返回 occ, priorOcc, topValid, botValid, topShift, botShift。"""
    lb = occ_lookback if occ_lookback is not None else OCC_LOOKBACK
    cb = occ_compare_bars if occ_compare_bars is not None else OCC_COMPARE_BARS
    th = occ_shift_threshold if occ_shift_threshold is not None else OCC_SHIFT_THRESHOLD
    vb = occ_valid_bars if occ_valid_bars is not None else OCC_VALID_BARS
    c = ind["close"]
    upper, lower = ind["upper"], ind["lower"]
    n = len(c)
    prev_width = np.full(n, np.nan)
    prev_width[1:] = upper[:-1] - lower[:-1]
    lower_prev = np.full(n, np.nan)
    lower_prev[1:] = lower[:-1]
    with np.errstate(invalid="ignore", divide="ignore"):
        occ_pos = np.where((~np.isnan(prev_width)) & (prev_width > 0),
                           (c - lower_prev) / np.where(prev_width == 0, np.nan, prev_width),
                           np.nan)
    if OCC_SMOOTHING == "EMA":
        occ = pine_ema(occ_pos, lb)
    else:
        occ = np.full(n, np.nan)
        for i in range(lb - 1, n):
            wnd = occ_pos[i - lb + 1:i + 1]
            if not np.any(np.isnan(wnd)):
                occ[i] = np.mean(wnd)
    prior_occ = np.full(n, np.nan)
    prior_occ[cb:] = occ[:-cb]
    top_base = (~np.isnan(prior_occ)) & (~np.isnan(occ)) & ((prior_occ - occ) >= th)
    bot_base = (~np.isnan(prior_occ)) & (~np.isnan(occ)) & ((occ - prior_occ) >= th)
    top_desc = np.zeros(n, dtype=bool)
    bot_asc = np.zeros(n, dtype=bool)
    for i in range(1, n):
        if OCC_CONFIRM_BARS == 1:
            top_desc[i] = (not np.isnan(occ[i])) and (not np.isnan(occ[i - 1])) and occ[i] < occ[i - 1]
            bot_asc[i] = (not np.isnan(occ[i])) and (not np.isnan(occ[i - 1])) and occ[i] > occ[i - 1]
        else:
            td, ba = True, True
            for k in range(OCC_CONFIRM_BARS):
                j = i - k
                if j < 1 or np.isnan(occ[j]) or np.isnan(occ[j - 1]):
                    td = ba = False
                    break
                td = td and occ[j] < occ[j - 1]
                ba = ba and occ[j] > occ[j - 1]
            top_desc[i], bot_asc[i] = td, ba
    top_shift = top_base & top_desc
    bot_shift = bot_base & bot_asc
    top_valid = np.zeros(n, dtype=bool)
    bot_valid = np.zeros(n, dtype=bool)
    last_top = -10 ** 9
    last_bot = -10 ** 9
    for i in range(n):
        if top_shift[i]:
            last_top = i
        if bot_shift[i]:
            last_bot = i
        top_valid[i] = (i - last_top) <= vb
        bot_valid[i] = (i - last_bot) <= vb
    return occ, prior_occ, top_valid, bot_valid, top_shift, bot_shift


def compute_bo_arrays(ind):
    """Friction Breakout 逐 bar 预计算 (全部只用当根及之前的数据, 无前视)。
    返回 dict of arrays:
    - ready: v64 同款 ready (upper[1]/lower[1]/atr/chan_mid 非 na 且通道宽>0)
    - frictionTouch: Wick 模式影线跨中线
    - frictionCount: 过去 10 根触线数 (含当根)
    - frictionHigh/Low: 前 10 根 (不含当根) 的最高 high / 最低 low
    - frictionWidthATR: (frictionHigh-frictionLow)/atr
    - frictionQualified: ready 且 前一根 frictionCount>=6 且 宽度<=5.0 ATR
    - confirmationFriction: 触线且 (boIgnoreOutside 时收盘不在通道外)
    - noRecentFriction: 最近 1 根无 confirmationFriction
    - boUpConsecutive/boDownConsecutive: 收盘在通道外 (1 根)
    - boEscapeUp/Down: 离中线距离 (ATR)
    - reverseUpOutside/DownOutside: 连续 2 根收盘在通道外
    - reverseUpEscape/DownEscape: 反向逃出 + 距离>=1.2 ATR
    - adverseForLong/Short: 当根收盘相对前一根逆势 (动量失败 1 根)
    - renewedFriction: 过去 5 根触线数>=4
    - insideRecovery: 连续 3 根收盘在通道内
    """
    h = ind["high"]; l = ind["low"]; c = ind["close"]
    atr = ind["atr"]; upper = ind["upper"]; lower = ind["lower"]; chan_mid = ind["chan_mid"]
    n = len(c)
    upper_prev = np.full(n, np.nan); upper_prev[1:] = upper[:-1]
    lower_prev = np.full(n, np.nan); lower_prev[1:] = lower[:-1]
    ready = np.zeros(n, dtype=bool)
    ready[1:] = ((~np.isnan(upper_prev[1:])) & (~np.isnan(lower_prev[1:]))
                 & (~np.isnan(atr[1:])) & (~np.isnan(chan_mid[1:]))
                 & ((upper_prev[1:] - lower_prev[1:]) > 0))
    # 触线 (Wick): 影线跨中线
    with np.errstate(invalid="ignore"):
        friction_touch = (~np.isnan(chan_mid)) & (l <= chan_mid) & (h >= chan_mid)
    s_touch = pd.Series(friction_touch.astype(float))
    friction_count = s_touch.rolling(BO_RANGE_LOOKBACK, min_periods=1).sum().values
    # 前 10 根高低点 (不含当根): rolling(10).max().shift(1)
    friction_high = pd.Series(h).rolling(BO_RANGE_LOOKBACK).max().shift(1).values
    friction_low = pd.Series(l).rolling(BO_RANGE_LOOKBACK).min().shift(1).values
    with np.errstate(invalid="ignore", divide="ignore"):
        friction_width_atr = (friction_high - friction_low) / atr
    prev_count = np.zeros(n)
    prev_count[1:] = friction_count[:-1]
    friction_qualified = (ready & (prev_count >= BO_MIN_CROSSES)
                          & (~np.isnan(friction_width_atr))
                          & (friction_width_atr <= BO_MAX_RANGE_ATR))
    closes_outside = (c > upper) | (c < lower)
    confirmation_friction = friction_touch & (~(BO_IGNORE_OUTSIDE & closes_outside))
    no_recent_friction = ~confirmation_friction  # BO_NO_CROSS_BARS=1
    bo_up_cons = c > upper
    bo_down_cons = c < lower
    with np.errstate(invalid="ignore", divide="ignore"):
        bo_escape_up = (c - chan_mid) / atr
        bo_escape_down = (chan_mid - c) / atr
    close_prev = np.full(n, np.nan); close_prev[1:] = c[:-1]
    upper_prev2 = upper_prev; lower_prev2 = lower_prev
    rev_up_out = (c > upper) & (close_prev > upper_prev2)
    rev_down_out = (c < lower) & (close_prev < lower_prev2)
    with np.errstate(invalid="ignore"):
        rev_up_esc = rev_up_out & (bo_escape_up >= BO_REVERSE_DISTANCE_ATR)
        rev_down_esc = rev_down_out & (bo_escape_down >= BO_REVERSE_DISTANCE_ATR)
    adverse_long = c < close_prev
    adverse_short = c > close_prev
    renewed_count = s_touch.rolling(BO_RECOVERY_FRICTION_LOOKBACK, min_periods=1).sum().values
    renewed_friction = renewed_count >= BO_RECOVERY_MIN_FRICTION
    inside = (c <= upper) & (c >= lower)
    inside_recovery = np.zeros(n, dtype=bool)
    inside_recovery[2:] = inside[2:] & inside[1:-1] & inside[:-2]
    return dict(ready=ready, frictionTouch=friction_touch, frictionCount=friction_count,
                frictionHigh=friction_high, frictionLow=friction_low,
                frictionWidthATR=friction_width_atr, frictionQualified=friction_qualified,
                confirmationFriction=confirmation_friction, noRecentFriction=no_recent_friction,
                boUpConsecutive=bo_up_cons, boDownConsecutive=bo_down_cons,
                boEscapeUp=bo_escape_up, boEscapeDown=bo_escape_down,
                reverseUpOutside=rev_up_out, reverseDownOutside=rev_down_out,
                reverseUpEscape=rev_up_esc, reverseDownEscape=rev_down_esc,
                adverseForLong=adverse_long, adverseForShort=adverse_short,
                renewedFriction=renewed_friction, insideRecovery=inside_recovery)


def simulate(df, ind, tf_min, trend_entry=True, use_uturn=True, use_bo=True,
               control_pos=None, er_cfg=None):
    """1m 回测, Pine v6.7 语义 + 趋势接管 (Step 19)。返回 trades。
    control_pos: 影子基线 (use_bo=False 预跑) 每根 bar 进场决策前一刻的仓位方向 (-1/0/1,
      即止损/强制平之后、进场之前的位置; 决定基线在该 bar 是否开趋势单)。
      趋势信号当根, 只有基线会开趋势单时 (基线空仓或反向) 我方才开;
      基线已同向持仓时我方不开 (否则会多出基线没有的趋势单)。
      为 None 时退化为旧逻辑 (只按我方仓位模式转换)。
    统计见 V67_STATS (entry_modes: 'trend'/'edge'/'ut'/'bo'/'rev';
    V67_STATS['pos_before'] 为每根 bar 进场决策前一刻的仓位方向, 供影子预跑用)。"""
    V67_STATS.clear()
    V67_STATS.update(dict(trend_edges_long=0, trend_edges_short=0,
                          trend_only_entries=0,
                          ut_candidates_top=0, ut_candidates_bottom=0,
                          ut_confirms_s=0, ut_confirms_b=0,
                          ut_entries=0, ut_failures=0, ut_failed_exits=0,
                          bo_freezes=0, bo_signals_up=0, bo_signals_down=0,
                          bo_entries=0, rev_entries=0, rev_arms=0,
                          bo_ends_recovered=0, bo_ends_failed=0, bo_ends_timeout=0,
                          trend_protect_convert=0, trend_protect_flip_bo=0,
                          trend_protect_bo_blocked_sig=0, trend_protect_bo_blocked_pos=0,
                          trend_protect_skip_same=0, trend_protect_skip_trend=0,
                          entry_modes={}, entry_seg={}))
    ER_INFO.clear()
    occ, prior_occ, occ_top_valid, occ_bot_valid, _, _ = compute_occupancy(ind)
    bo = compute_bo_arrays(ind) if use_bo else None
    prev_strong_up = prev_strong_down = False
    cur_seg = 0
    n = len(df)
    times = df["t"]
    sm = times.dt.hour.values * 60 + times.dt.minute.values
    dates = times.dt.strftime("%Y-%m-%d").values
    # 早收日 EOD 补丁 (2026-11-08): EOD 按当日最后一根 bar 走。
    # 旧逻辑只认 15:57-16:00，早收日 (感恩节周五/7月3日/平安夜) 13:00 收盘永不触发，
    # 12:55 后开的单带过周末/假期。按日取最后一根 bar 的 close_min，EOD = 当日最后 4 根；
    # 正常日最后一根是 16:00，与旧逻辑一字不差。
    _day_last = {}
    for _d, _s in zip(dates, sm):
        _day_last[_d] = _s + tf_min
    o = ind["open"]; h = ind["high"]; l = ind["low"]; c = ind["close"]
    atr, upper, lower, chan_mid = ind["atr"], ind["upper"], ind["lower"], ind["chan_mid"]
    mid = ind["mid"]  # _slope: 高斯中线 (斜率第三路的斜率源)
    slope, er, adx = ind["slope"], ind["er"], ind["adx"]
    ready_arr = bo["ready"] if use_bo else None
    if ready_arr is None:
        # use_bo=False 时仍需 ready 数组 (与 v64 同款定义)
        upper_prev = np.full(n, np.nan); upper_prev[1:] = upper[:-1]
        lower_prev = np.full(n, np.nan); lower_prev[1:] = lower[:-1]
        ready_arr = np.zeros(n, dtype=bool)
        ready_arr[1:] = ((~np.isnan(upper_prev[1:])) & (~np.isnan(lower_prev[1:]))
                         & (~np.isnan(atr[1:])) & (~np.isnan(chan_mid[1:]))
                         & ((upper_prev[1:] - lower_prev[1:]) > 0))
    pos = 0
    pos_mode = None  # 当前仓位入场模式: 'trend'/'edge'/'bo'/'rev'/None (趋势接管用)
    entry_px = entry_atr = None
    entry_bar = -1
    entry_date = None
    entry_seg_cur = -1
    ut_trade_dir = 0
    # ---- ER 覆盖层状态 (er_cfg=None 时全关) ----
    er_on = er_cfg is not None
    er_n = int(er_cfg["n"]) if er_on else 0
    er_th = float(er_cfg["th"]) if er_on else 0.0
    er_b_mode = (er_cfg.get("mode", "A") == "B") if er_on else False
    er_arr = ER_ARRAYS.get(er_n) if er_on else None
    # sweep 扩展: branch=state(默认, 同原 fork) | surge; bw=突破回看; k/from=突变窗;
    # end=突变支段尾线 (state 支段尾线 = th 本身)
    er_bw = int(er_cfg.get("bw", 10)) if er_on else 10
    er_branch = er_cfg.get("branch", "state") if er_on else "state"
    er_k = int(er_cfg.get("k", 3)) if er_on else 3
    er_from = float(er_cfg.get("from", 0.40)) if er_on else 0.40
    er_end = float(er_cfg.get("end", 0.35)) if er_on else 0.35
    er_reentry = bool(er_cfg.get("reentry", False)) if er_on else False
    er_to = float(er_cfg.get("to", 0.60)) if er_on else 0.60  # 突变支上穿终点
    er_brk = int(er_cfg.get("brk", 0)) if er_on else 0  # 真突破门回看根数 (0=无门, 冠军原样)
    er_exit_new = (str(er_cfg.get("exit_caliber", "new")) == "new") if er_on else False  # _nb 出场口径: new=定版 (ER 出场根不屏蔽) / old=旧口径保真
    # ---- WER 深研扩展 (默认全关, 关时与 _nb 逐笔等价) ----
    er_exit_bars = int(er_cfg.get("exit_bars", 1)) if er_on else 1      # R3a 出段迟滞: 连续 N 根 <end 才出段
    er_dir_mode = str(er_cfg.get("dir_mode", "net")) if er_on else "net"  # R3b: net=近n根净位移 | arr=ER_DIR_ARRAY 逐根符号
    er_gate_min = er_cfg.get("gate_min", None) if er_on else None      # R3c: 触发根另须 ER_GATE_ARRAY[i] >= gate_min
    er_dead_thr = er_cfg.get("dead_thr", None) if er_on else None      # R2 质量格: 前 k 根窗内须 >=1 根 < dead_thr
    er_exit_deep = (float(er_cfg["exit_deep"]) if er_on
                    and er_cfg.get("exit_deep") is not None else None)  # R5 双层出段: <deep 任意 1 根即出
    er_dose_R = (float(er_cfg["dose_R"]) if er_on
                 and er_cfg.get("dose_R") is not None else None)  # _wdose 水位参照线 (None=关, 保真)
    er_dose_CAP = float(er_cfg.get("dose_CAP", 0.0)) if er_on else 0.0  # _wdose 降幅封顶
    er_dose_W = int(er_cfg.get("dose_W", 10)) if er_on else 10          # _wdose avg 回望窗 (不含当根)
    er_ce_method = str(er_cfg.get("ce_method", "")) if er_on else ""    # _wcent: A/B/C/E/F (""=关, 保真)
    er_ce_W = int(er_cfg.get("ce_W", 0)) if er_on else 0                # _wcent C: 对齐等待上限 (根)
    ce_st = CE_STATE   # _wcent: 自由运行 CE 状态 (runner 注入)
    ce_hh = CE_H       # _wcent: 自由运行 CE 健康度 (runner 注入; 现行方法未用, 保留注入点)
    er_ce_cf = int(er_cfg.get("ce_cf", 1)) if er_on else 1  # _wcent2: 翻转确认根数 (E/F/RF 用确认状态; 状态数组由 runner 按 cf 建)
    er_ce_f2 = bool(er_cfg.get("ce_f2", False)) if er_on else False  # _f2: 段内止损后 CE 变向重开 (默认关; 关时与 RF 逐笔等价)
    er_ce_f3 = bool(er_cfg.get("ce_f3", False)) if er_on else False
    er_f3_shield = bool(er_cfg.get("ce_f3_shield", False)) if er_on else False
    er_ramp_on = bool(er_cfg.get("ramp_on", False)) if er_on else False  # _ramp: 平滑爬升降门槛 (默认关; 关时与 F3S 逐笔等价)
    er_ramp_mode = str(er_cfg.get("ramp_mode", "max")) if er_on else "max"  # _ramp: max=降幅与 dose 取大 | pure=只用 ramp
    er_ramp_K = int(er_cfg.get("ramp_K", 8)) if er_on else 8              # _ramp: 爬升窗 (含当根)
    er_ramp_S0 = float(er_cfg.get("ramp_S0", 0.50)) if er_on else 0.50    # _ramp: 顺 S 生效线
    er_ramp_CAP = float(er_cfg.get("ramp_CAP", 0.0)) if er_on else 0.0    # _ramp: 降幅封顶
    er_ramp_BETA = float(er_cfg.get("ramp_BETA", 1.0)) if er_on else 1.0  # _rampβ: 渐进因子惩罚权重 (默认 1.0 = 现行定义, 与 shortk 逐笔等价)
    er_drop_mode = str(er_cfg.get("drop_mode", "max")) if er_on else "max"  # _sumdrop: max=现行取大 | sum=dose+ramp(+slope) 叠加
    er_drop_cap = (float(er_cfg["drop_cap"]) if er_on
                   and er_cfg.get("drop_cap") is not None else None)     # _sumdrop: 叠加总封顶 (None=不封顶)
    er_eff_floor = float(er_cfg.get("eff_floor", 0.40)) if er_on else 0.40  # _sumdrop: eff_to 硬下限 (仅 sum/slope 路径)
    er_slope_on = bool(er_cfg.get("slope_on", False)) if er_on else False  # _slope: 斜率第三路 (默认关; 关时逐笔等价)
    er_slope_W = int(er_cfg.get("slope_W", 5)) if er_on else 5            # _slope: 斜率均值窗 (含当根的 Δmid 根数)
    er_slope_s0 = float(er_cfg.get("slope_s0", 0.10)) if er_on else 0.10   # _slope: 生效线
    er_slope_s1 = float(er_cfg.get("slope_s1", 0.45)) if er_on else 0.45   # _slope: 降满线
    er_slope_CAP = float(er_cfg.get("slope_CAP", 0.0)) if er_on else 0.0   # _slope: 降幅封顶 CAP3
    drop_cnt = dict(eval_bars=0, floor_hit=0, floor_trig=0)               # _sumdrop 埋点计数
    slope_cnt = dict(bars=0, drop_pos=0, needed=0)                        # _slope 埋点计数
    ramp_cnt = dict(bars=0, drop_pos=0, drop_gt_dose=0, spike=0)          # _ramp 埋点计数  # _f3s: F3 活段出场根亦屏蔽本体 (默认关; 关时与 F3 逐笔等价)  # _f3: 段内状态跟随 (默认关; 关时与 F2 逐笔等价)
    ce_cst = CE_CST if CE_CST is not None else CE_STATE  # _wcent2: 确认后状态 (None 回退原始状态, 与 _wcent 等价)
    seg_below = 0  # R3a 迟滞计数
    seg_active = False; seg_dir = 0; seg_date = None
    seg_pending = False; seg_trigger_bar = -1  # 突破门: 已触发未入场 (等段内首个突破根)
    ce_pending = False; ce_pend_bar = -1  # _wcent C: 已触发未入场 (等 CE 状态转同向)
    ce_flip_bar = -1  # _wcent F: 最近一次翻仓根 (屏蔽延续判定用)
    f2_wait = False; f2_wait_dir = 0; f2_stop_bar = -1  # _f2: 段内止损等待 (被止损腿方向/止损根)
    ce_flip_map = {}  # _wcent F: {段起始根: 翻仓次数}
    seg_ended_bar = False; er_trigger = False
    er_ext = None  # B 止盈极值 (截至上一根, 先判后更)
    er_last_close = None  # (bar, reason): 最近一次 ER 仓平仓 (段内续开判定)
    er_cnt = dict(trigger=0, entries=0, swaps=0, blocked_trend_pos=0,
                  masked_sig=0, reentries=0,
                  gate_imm=0, gate_delay=0, gate_dead=0, gate_delay_bars=0,
                  seg_gauss_entries=0, deep_exit=0)
    ce_cnt = dict(redirect=0, blocked=0, pend=0, imm=0, delay_entries=0,
                  delay_bars=0, dead_seg=0, dead_timeout=0, ce_exit=0,
                  flip=0)  # _wcent 审计计数
    if er_on:
        ER_INFO["segments"] = []
        ER_INFO["counts"] = er_cnt
        ER_INFO["dose_drops"] = []
        ER_INFO["ramp_trig"] = []
        ER_INFO["ramp_counts"] = ramp_cnt
        ER_INFO["drop_trig"] = []
        ER_INFO["drop_counts"] = drop_cnt
        ER_INFO["slope_counts"] = slope_cnt
        ER_INFO["ce_counts"] = ce_cnt
        ER_INFO["ce_events"] = []
        ER_INFO["ce_flip_map"] = ce_flip_map
    # _slope: 逐根归一斜率 s[i] = |mean_{j in [i-W+1, i]} (mid[j]-mid[j-1])/atr[j]|
    # (窗内任一根非有限 -> NaN; slope_on 关时不算, slope_s 恒 None)
    slope_s = None
    if er_on and er_slope_on:
        _W = er_slope_W
        _dm = np.full(n, np.nan)
        if n > 1:
            _atr_ok = np.where(np.isfinite(atr) & (atr > 0), atr, np.nan)
            _dm[1:] = np.diff(mid) / _atr_ok[1:]
        slope_s = np.full(n, np.nan)
        if n >= _W:
            _fin = np.isfinite(_dm).astype(float)
            _dm0 = np.where(np.isfinite(_dm), _dm, 0.0)
            _ks = np.ones(_W)
            _sums = np.convolve(_dm0, _ks, mode="valid")
            _cnts = np.convolve(_fin, _ks, mode="valid")
            _tail = np.where(_cnts == _W, np.abs(_sums / _W), np.nan)
            slope_s[_W - 1:] = _tail
    # 止损变体状态 (每单重置; close_pos/open_pos 里维护)
    sl_level = None    # 当前止损价 (long: 下方; short: 上方)
    sl_extreme = None  # chand: 入场后最有利价 (long: 最高 high; short: 最低 low)
    sl_armed = False   # trail/be 已触发锁定
    trades = []
    pos_before_list = []  # 每根 bar 进场决策前一刻的仓位方向 (-1/0/1), 供影子预跑用

    # U-turn 状态 (Pine var)
    top_bar = -1; top_high = np.nan; top_low = np.nan
    bot_bar = -1; bot_high = np.nan; bot_low = np.nan
    last_ut_bar = -10 ** 9
    ut_dir = 0; ut_start_bar = -1
    ut_entry_price = np.nan; ut_atr = np.nan; ut_extreme = np.nan; ut_best = np.nan
    ut_progress = False
    # Friction Breakout 状态 (Pine var)
    frozen_high = np.nan; frozen_low = np.nan; frozen_bar = -1
    bo_dir = 0; bo_start_bar = -1
    reverse_armed_dir = 0; reverse_armed_bar = -1; reverse_source_dir = 0

    def close_pos(bar, px, reason):
        nonlocal pos, pos_mode, entry_px, entry_atr, entry_bar, entry_date, entry_seg_cur, ut_trade_dir
        nonlocal sl_level, sl_extreme, sl_armed
        nonlocal er_last_close
        if pos == 0:
            return
        if pos_mode == "er":
            er_last_close = (bar, reason)
        d = "long" if pos == 1 else "short"
        if entry_date is not None and START_DATE <= entry_date <= END_DATE:
            trades.append((entry_bar, bar, d, entry_px, px, reason))
        pos = 0; pos_mode = None; entry_px = entry_atr = None; entry_bar = -1; entry_date = None
        entry_seg_cur = -1; ut_trade_dir = 0
        sl_level = None; sl_extreme = None; sl_armed = False

    def open_pos(direction, mode):
        nonlocal pos, pos_mode, entry_px, entry_atr, entry_bar, entry_date, entry_seg_cur, ut_trade_dir
        nonlocal sl_level, sl_extreme, sl_armed
        if direction == 1:
            if pos == 1:
                return False
            if pos == -1:
                close_pos(i, c[i], "FLIP")
            pos = 1
        else:
            if pos == -1:
                return False
            if pos == 1:
                close_pos(i, c[i], "FLIP")
            pos = -1
        entry_px = c[i]; entry_atr = atr[i]; entry_bar = i; entry_date = dstr
        entry_seg_cur = cur_seg
        pos_mode = mode
        # 止损变体: 按模式设初始止损价
        sl_level = None; sl_extreme = None; sl_armed = False
        _k = SL_K
        if SL_MODE == "split":
            _k = SL_SPLIT_TREND if mode == "trend" else SL_SPLIT_EDGE
        if SL_MODE in ("atr", "split", "trail", "breakeven", "time", "chand"):
            sl_level = entry_px - _k * entry_atr if direction == 1 else entry_px + _k * entry_atr
        elif SL_MODE == "roi":
            _dd = _roi_stop_delta(direction, SL_ROI_T)
            sl_level = entry_px + _dd if _dd is not None else None
        if SL_MODE == "chand":
            sl_extreme = entry_px
        V67_STATS["entry_modes"][i] = mode
        V67_STATS["entry_seg"][i] = cur_seg
        if START_DATE <= dstr <= END_DATE:
            if mode == "trend":
                V67_STATS["trend_only_entries"] += 1
            elif mode == "ut":
                V67_STATS["ut_entries"] += 1
            elif mode == "bo":
                V67_STATS["bo_entries"] += 1
            elif mode == "rev":
                V67_STATS["rev_entries"] += 1
        ut_trade_dir = 1 if (mode == "ut" and direction == 1) else (-1 if mode == "ut" else 0)
        return True

    for i in range(n):
        dstr = dates[i]
        if dstr >= START_DATE and pos != 0 and (entry_date or "") < START_DATE:
            pos = 0; pos_mode = None; entry_px = entry_atr = None; entry_bar = -1; entry_date = None
            entry_seg_cur = -1; ut_trade_dir = 0
            sl_level = None; sl_extreme = None; sl_armed = False
        s = sm[i]
        close_min = s + tf_min
        eod = (close_min >= _day_last[dstr] - 3) and (close_min <= _day_last[dstr])
        # EOD bar 上不许开新仓 (正常日靠 close_min<945 挡，早收日 EOD bar 的 close_min 才 778-781，
        # 不加 not eod 会在最后一根 bar 上开仓、带过周末)。
        in_session = (570 <= s <= 955) and (close_min < 945) and (not eod)
        ready = bool(ready_arr[i])
        # ---- Gaussian wick-touch 信号 (与 v39/v64 一致) ----
        long_sig = short_sig = False
        if ready:
            tU, tL = upper[i - 1], lower[i - 1]
            width = tU - tL
            buy_top = tL + width * EDGE_PCT / 100.0
            sell_bot = tU - width * EDGE_PCT / 100.0
            low_touch = (l[i] <= buy_top) and (h[i] >= tL - MAX_OUTSIDE * atr[i])
            up_touch = (h[i] >= sell_bot) and (l[i] <= tU + MAX_OUTSIDE * atr[i])
            ls = low_touch and (c[i] <= chan_mid[i])
            ss = up_touch and (c[i] >= chan_mid[i])
            if not (ls and ss):
                long_sig = ls and in_session
                short_sig = ss and in_session
        # ---- 趋势状态 ----
        ind_ok = not (np.isnan(er[i]) or np.isnan(adx[i])
                      or np.isnan(slope[i]) or np.isnan(atr[i]))
        allow_long = allow_short = False
        strong_down = strong_up = False
        if ind_ok:
            trending = (er[i] >= ER_TH) and (adx[i] >= ADX_TH)
            strong_down = trending and (slope[i] < -TAKEOVER_SLOPE * atr[i])
            strong_up = trending and (slope[i] > TAKEOVER_SLOPE * atr[i])
            allow_long = not strong_down
            allow_short = not strong_up
        if (strong_up and not prev_strong_up) or (strong_down and not prev_strong_down):
            cur_seg += 1
        edge_up = strong_up and not prev_strong_up
        edge_down = strong_down and not prev_strong_down
        if in_session and START_DATE <= dstr <= END_DATE:
            if edge_up:
                V67_STATS["trend_edges_long"] += 1
            if edge_down:
                V67_STATS["trend_edges_short"] += 1
        # 趋势入场: plain 上升沿 (v6.7 无 pending, 与 v39 一致)
        trend_long_sig = trend_entry and edge_up and in_session
        trend_short_sig = trend_entry and edge_down and in_session
        # ---- U-turn 候选 (当根) ----
        top_cand = bot_cand = False
        if use_uturn and ready and atr[i] > 0 and i >= 10:
            cr = max(h[i] - l[i], MINTICK)
            up_wick = h[i] - max(o[i], c[i])
            lo_wick = min(o[i], c[i]) - l[i]
            prior_low = np.min(l[i - UT_EXC_LOOKBACK:i])
            prior_high = np.max(h[i - UT_EXC_LOOKBACK:i])
            recent_high = np.max(h[i - UT_EXTREME_LOOKBACK:i])
            recent_low = np.min(l[i - UT_EXTREME_LOOKBACK:i])
            if (not np.isnan(prior_low) and h[i] > recent_high
                    and (h[i] - prior_low) / atr[i] >= UT_MIN_EXC_ATR
                    and up_wick / cr >= UT_WICK_RATIO):
                top_cand = True
            if (not np.isnan(prior_high) and l[i] < recent_low
                    and (prior_high - l[i]) / atr[i] >= UT_MIN_EXC_ATR
                    and lo_wick / cr >= UT_WICK_RATIO):
                bot_cand = True
        if top_cand and START_DATE <= dstr <= END_DATE:
            V67_STATS["ut_candidates_top"] += 1
        if bot_cand and START_DATE <= dstr <= END_DATE:
            V67_STATS["ut_candidates_bottom"] += 1
        # 候选过期 (有效 8 根)
        if top_bar >= 0 and i - top_bar > UT_CONFIRM_BARS:
            top_bar = -1
        if bot_bar >= 0 and i - bot_bar > UT_CONFIRM_BARS:
            bot_bar = -1
        cooldown_ok = (i - last_ut_bar) > UT_COOLDOWN
        # ---- U-turn 确认 (用此前存的候选; 确认在候选更新之前) ----
        ut_s = ut_b = False
        if use_uturn and ready and cooldown_ok:
            if top_bar >= 0 and i > top_bar and c[i] < top_low and occ_top_valid[i]:
                ut_s = True
            if bot_bar >= 0 and i > bot_bar and c[i] > bot_high and occ_bot_valid[i]:
                ut_b = True
        if ut_s and ut_b:
            ut_s = ut_b = False
        if ut_s and START_DATE <= dstr <= END_DATE:
            V67_STATS["ut_confirms_s"] += 1
        if ut_b and START_DATE <= dstr <= END_DATE:
            V67_STATS["ut_confirms_b"] += 1
        # ---- U-turn 失败检测 ----
        ut_failed = False
        if not use_uturn:
            ut_dir = 0; ut_start_bar = -1
        elif ut_dir != 0 and ut_start_bar >= 0 and i > ut_start_bar:
            elapsed = i - ut_start_bar
            if ut_dir == -1:
                ut_best = min(ut_best, l[i])
            else:
                ut_best = max(ut_best, h[i])
            favorable = (ut_entry_price - ut_best) if ut_dir == -1 else (ut_best - ut_entry_price)
            if favorable >= UT_MIN_PROGRESS_ATR * ut_atr:
                ut_progress = True
            hard_fail = False
            if UT_USE_HARD_FAIL:
                if ut_dir == -1:
                    hard_fail = h[i] > ut_extreme + UT_FAILURE_BUFFER * ut_atr
                else:
                    hard_fail = l[i] < ut_extreme - UT_FAILURE_BUFFER * ut_atr
            progress_fail = UT_USE_PROGRESS_FAIL and elapsed >= UT_VALIDATION_BARS and not ut_progress
            if hard_fail or progress_fail:
                ut_failed = True
                ut_dir = 0
                if START_DATE <= dstr <= END_DATE:
                    V67_STATS["ut_failures"] += 1
            elif elapsed >= max(UT_BLOCK_BARS, UT_VALIDATION_BARS):
                ut_dir = 0
        # 反向 U-turn (默认允许, 不拦截)
        if ut_s and ut_dir == 1 and not UT_ALLOW_OPPOSITE_UT:
            ut_s = False
        if ut_b and ut_dir == -1 and not UT_ALLOW_OPPOSITE_UT:
            ut_b = False
        # 启动新 U-turn 校验
        if ut_s or ut_b:
            last_ut_bar = i
            ut_dir = 1 if ut_b else -1
            ut_start_bar = i
            ut_entry_price = c[i]
            ut_atr = atr[i]
            ut_extreme = bot_low if ut_b else top_high
            ut_best = c[i]
            ut_progress = False
            top_bar = -1; bot_bar = -1
        # 候选更新 (确认之后)
        if use_uturn and ready:
            if top_cand and (top_bar < 0 or h[i] >= top_high):
                top_bar = i; top_high = h[i]; top_low = l[i]
            if bot_cand and (bot_bar < 0 or l[i] <= bot_low):
                bot_bar = i; bot_high = h[i]; bot_low = l[i]
        # ---- 趋势接管: 趋势信号当根, 反手武装作废 (趋势单优先, 不被 REV 翻) ----
        # 分档保护 (Step 19): 趋势里 (持趋势仓) BO 挡, 趋势外 (持边缘仓/空仓) BO 不挡。
        trend_sig_now = bool(trend_long_sig or trend_short_sig)
        if trend_sig_now:
            reverse_armed_dir = 0; reverse_armed_bar = -1; reverse_source_dir = 0
        # ---- Friction Breakout 状态机 (Pine 第 11/12 组) ----
        bo_up = bo_down = rev_up = rev_down = exit_bo = False
        bo_ended = False
        if use_bo:
            # 冻结区间 (frozenValid 先算, 冻结后置 true)
            frozen_valid = (frozen_bar >= 0) and (i - frozen_bar <= BO_RANGE_MEMORY)
            if bo_dir == 0 and not frozen_valid and bool(bo["frictionQualified"][i]) and reverse_armed_dir == 0:
                frozen_high = bo["frictionHigh"][i]
                frozen_low = bo["frictionLow"][i]
                frozen_bar = i
                frozen_valid = True
                if START_DATE <= dstr <= END_DATE:
                    V67_STATS["bo_freezes"] += 1
            # 突破判定
            bo_up_break = (frozen_valid and not np.isnan(frozen_high)
                           and c[i] > frozen_high + BO_BUFFER_ATR * atr[i])
            bo_down_break = (frozen_valid and not np.isnan(frozen_low)
                             and c[i] < frozen_low - BO_BUFFER_ATR * atr[i])
            bo_up_escape = (bool(bo["boUpConsecutive"][i]) and bool(bo["noRecentFriction"][i])
                            and bo["boEscapeUp"][i] >= BO_MIN_ESCAPE_ATR)
            bo_down_escape = (bool(bo["boDownConsecutive"][i]) and bool(bo["noRecentFriction"][i])
                              and bo["boEscapeDown"][i] >= BO_MIN_ESCAPE_ATR)
            bo_up = bool(ready and in_session and bo_dir == 0 and reverse_armed_dir == 0
                         and bo_up_break and bo_up_escape)
            bo_down = bool(ready and in_session and bo_dir == 0 and reverse_armed_dir == 0
                           and bo_down_break and bo_down_escape)
            # 反手机会 (reverse armed)
            if reverse_armed_dir != 0:
                reverse_expired = (i - reverse_armed_bar) > BO_REVERSE_WINDOW
                if reverse_expired or eod:
                    reverse_armed_dir = 0; reverse_armed_bar = -1; reverse_source_dir = 0
                elif ready and in_session:
                    if reverse_armed_dir == 1 and bool(bo["reverseUpEscape"][i]):
                        rev_up = True
                    if reverse_armed_dir == -1 and bool(bo["reverseDownEscape"][i]):
                        rev_down = True
                    if rev_up or rev_down:
                        # 分档接管: 趋势信号当根或持趋势仓不启动接管; 持边缘仓/空仓照常启动
                        if not trend_sig_now and pos_mode != "trend":
                            bo_dir = 1 if rev_up else -1
                            bo_start_bar = i
                        reverse_armed_dir = 0; reverse_armed_bar = -1; reverse_source_dir = 0
            # 接管评估
            if bo_dir != 0 and bo_start_bar >= 0 and not rev_up and not rev_down:
                elapsed_bo = i - bo_start_bar
                momentum_failure = (elapsed_bo > 0 and (
                    (bo_dir == 1 and c[i] < chan_mid[i] and bool(bo["adverseForLong"][i]))
                    or (bo_dir == -1 and c[i] > chan_mid[i] and bool(bo["adverseForShort"][i]))))
                inside_failed = True
                for k in range(BO_FAILURE_BARS):
                    j = i - k
                    if j < 0 or np.isnan(frozen_high) or np.isnan(frozen_low) \
                            or not (frozen_low <= c[j] <= frozen_high):
                        inside_failed = False
                        break
                recovered = bool(bo["insideRecovery"][i]) and (
                    (not BO_REQUIRE_FRICTION_RECOVERY) or bool(bo["renewedFriction"][i]))
                failed = inside_failed
                timed_out = elapsed_bo >= BO_MAX_BARS
                if momentum_failure:
                    exit_bo = True
                    reverse_source_dir = bo_dir
                    reverse_armed_dir = -bo_dir
                    reverse_armed_bar = i
                    bo_dir = 0; bo_start_bar = -1
                    if START_DATE <= dstr <= END_DATE:
                        V67_STATS["rev_arms"] += 1
                elif recovered or failed or timed_out or eod:
                    bo_ended = True
                    bo_dir = 0; bo_start_bar = -1
                    if START_DATE <= dstr <= END_DATE:
                        if recovered:
                            V67_STATS["bo_ends_recovered"] += 1
                        elif failed:
                            V67_STATS["bo_ends_failed"] += 1
                        elif timed_out:
                            V67_STATS["bo_ends_timeout"] += 1
            # 新突破 -> 开始接管 (分档: 趋势信号当根或持趋势仓不启动; 持边缘仓/空仓照常启动)
            if bo_up and not trend_sig_now and pos_mode != "trend":
                bo_dir = 1; bo_start_bar = i; reverse_armed_dir = 0
                if START_DATE <= dstr <= END_DATE:
                    V67_STATS["bo_signals_up"] += 1
            if bo_down and not trend_sig_now and pos_mode != "trend":
                bo_dir = -1; bo_start_bar = i; reverse_armed_dir = 0
                if START_DATE <= dstr <= END_DATE:
                    V67_STATS["bo_signals_down"] += 1
            # boEnded 清冻结区间
            if bo_ended:
                frozen_high = np.nan; frozen_low = np.nan; frozen_bar = -1
            # 冻结区间过期 (无接管、无反手武装时)
            if bo_dir == 0 and reverse_armed_dir == 0 and frozen_bar >= 0 \
                    and (i - frozen_bar) > BO_RANGE_MEMORY:
                frozen_high = np.nan; frozen_low = np.nan; frozen_bar = -1
        # ---- 信号优先级 ----
        protected_ut = use_uturn and ut_dir != 0 and ut_start_bar >= 0 and (i - ut_start_bar) < UT_BLOCK_BARS
        allowed_gauss_b = long_sig and not (protected_ut and ut_dir == -1)
        allowed_gauss_s = short_sig and not (protected_ut and ut_dir == 1)
        if ut_s:
            allowed_gauss_b = False
        if ut_b:
            allowed_gauss_s = False
        final_b = (allowed_gauss_b and allow_long) or (ut_b and (not UT_RESPECT_REGIME or allow_long))
        final_s = (allowed_gauss_s and allow_short) or (ut_s and (not UT_RESPECT_REGIME or allow_short))
        if BO_BLOCK_OPPOSITE and bo_dir == 1:
            final_s = False
        if BO_BLOCK_OPPOSITE and bo_dir == -1:
            final_b = False
        if final_b and final_s:
            final_b = final_s = False
        final_b = bool(final_b and ready and in_session)
        final_s = bool(final_s and ready and in_session)
        # 特殊入场信号 (分档接管: 趋势绝对优先; REV/BO 在趋势信号当根或持趋势仓时挡,
        # 持边缘仓/空仓时照常开 -- BO 可翻边缘仓, 但铁定不碰趋势仓)
        enter_trend_long = bool(trend_long_sig)
        enter_trend_short = bool(trend_short_sig)
        trend_special = bool(enter_trend_long or enter_trend_short)
        allow_bo_entry = (not trend_special) and (pos_mode != "trend")
        enter_rev_long = bool(rev_up) and allow_bo_entry
        enter_rev_short = bool(rev_down) and allow_bo_entry
        enter_bo_long = bool(bo_up and BO_ENTER_DIRECTION) and allow_bo_entry
        enter_bo_short = bool(bo_down and BO_ENTER_DIRECTION) and allow_bo_entry
        if START_DATE <= dstr <= END_DATE and (bo_up or bo_down or rev_up or rev_down):
            if trend_special:
                V67_STATS["trend_protect_bo_blocked_sig"] += 1
            elif pos_mode == "trend":
                V67_STATS["trend_protect_bo_blocked_pos"] += 1
        special_entry = (enter_rev_long or enter_rev_short or enter_bo_long or enter_bo_short
                         or enter_trend_long or enter_trend_short)
        # ---- ER 覆盖层: 区域状态机 (触发根=入场信号根; ER<阈值根=段尾信号根) ----
        seg_ended_bar = False
        er_trigger = False
        if er_on:
            if seg_active and dstr != seg_date:
                seg_active = False  # 段不过夜 (正常情形日终 EOD 已平, 此处只清状态)
            if not seg_active:
                if er_branch == "surge":
                    # 突变支: ERn 新鲜上穿 >=0.60, 且前 k 根内曾 < from; 无突破门;
                    # 方向 = 近 n 根净位移符号
                    # _wdose: eff 门槛 (dose_R=None 时 _to_i == er_to, 与 _wdeep 等价)
                    _to_i = er_to
                    if er_dose_R is not None and i >= er_dose_W:
                        _dw = er_arr[i - er_dose_W:i]
                        if np.all(np.isfinite(_dw)):
                            _dr = er_dose_R - float(np.mean(_dw))
                            if _dr > 0.0:
                                _to_i = er_to - min(er_dose_CAP, _dr)
                    # _ramp: 平滑爬升降幅 (ramp_on 关时 _ramp_drop 恒 0, _to_i 原样)
                    _dose_drop = er_to - _to_i
                    _ramp_drop = 0.0
                    if er_ramp_on and i >= er_ramp_K - 1:
                        _rw = er_arr[i - er_ramp_K + 1:i + 1]
                        if np.all(np.isfinite(_rw)):
                            ramp_cnt["bars"] += 1
                            _rG = float(_rw[-1] - np.min(_rw))
                            if _rG > 0.0:
                                _rdl = np.diff(_rw)
                                _rsum = float(np.sum(np.abs(_rdl)))
                                if _rsum > 0.0:
                                    _rmax = float(np.max(_rdl))
                                    _rS = (_rG / _rsum) * (1.0 - er_ramp_BETA * _rmax / _rG)
                                    if _rS < 0.0:
                                        _rS = 0.0
                                    if _rG >= 0.30 and _rmax > 0.5 * _rG:
                                        ramp_cnt["spike"] += 1
                                    if _rS > er_ramp_S0 and er_ramp_CAP > 0.0:
                                        _ramp_drop = er_ramp_CAP * min(
                                            1.0, (_rS - er_ramp_S0) / (1.0 - er_ramp_S0))
                            if _ramp_drop > 0.0:
                                ramp_cnt["drop_pos"] += 1
                            if _ramp_drop > _dose_drop:
                                ramp_cnt["drop_gt_dose"] += 1
                    # _slope: 斜率第三路降幅 (slope_on 关时恒 0)
                    _slope_drop = 0.0
                    _sv = float(slope_s[i]) if er_slope_on else float("nan")
                    if er_slope_on and np.isfinite(_sv):
                        slope_cnt["bars"] += 1
                        if _sv > er_slope_s0 and er_slope_CAP > 0.0:
                            _slope_drop = er_slope_CAP * min(
                                1.0, (_sv - er_slope_s0)
                                / max(er_slope_s1 - er_slope_s0, 1e-9))
                        if _slope_drop > 0.0:
                            slope_cnt["drop_pos"] += 1
                    # 组合制式: max (现行, ramp_mode 分发) | sum/slope 路径
                    # (dose + ramp + slope 叠加 -> 可选总封顶 -> 硬下限截断)。
                    # 注: max 路径的分发从 ramp 有限窗块内提到此处, ramp_mode
                    # ="max" 时逐根等价 (窗外 _ramp_drop 恒 0, 比较不成立)。
                    _floor_hit = False
                    _to_noslope = None
                    if er_drop_mode == "sum" or er_slope_on:
                        _tot_ns = _dose_drop + _ramp_drop
                        if er_drop_cap is not None:
                            _tot_ns = min(_tot_ns, er_drop_cap)
                        _to_noslope = max(er_to - _tot_ns, er_eff_floor)
                        _tot = _tot_ns + _slope_drop
                        if er_drop_cap is not None:
                            _tot = min(_dose_drop + _ramp_drop + _slope_drop,
                                       er_drop_cap)
                        _to_i = er_to - _tot
                        if _to_i < er_eff_floor:
                            _to_i = er_eff_floor
                            _floor_hit = True
                        drop_cnt["eval_bars"] += 1
                        if _floor_hit:
                            drop_cnt["floor_hit"] += 1
                    elif er_ramp_mode == "pure":
                        _to_i = er_to - _ramp_drop
                    elif _ramp_drop > _dose_drop:
                        _to_i = er_to - _ramp_drop
                    if (in_session and s >= 580 and i >= er_k + 1
                            and not np.isnan(er_arr[i]) and er_arr[i] >= _to_i
                            and not np.isnan(er_arr[i - 1]) and er_arr[i - 1] < _to_i):
                        _win = er_arr[i - er_k:i]
                        if np.all(np.isfinite(_win)) and float(np.min(_win)) < er_from:
                            if er_dead_thr is not None and not np.any(_win < er_dead_thr):
                                pass  # R2 质量格: 前窗无死水根, 不触发
                            elif er_gate_min is not None and (ER_GATE_ARRAY is None
                                    or np.isnan(ER_GATE_ARRAY[i])
                                    or ER_GATE_ARRAY[i] < er_gate_min):
                                pass  # R3c 质量门: 触发根 ER12 未过地板, 不触发
                            else:
                                _dd = (int(ER_DIR_ARRAY[i]) if er_dir_mode == "arr"
                                       else int(np.sign(c[i] - c[i - er_n])))
                                if _dd != 0:
                                    # _wcent 入场侧 CE (ce_method 关时 _dd 与流程与 _wdose 一字不差)
                                    _ce_act = None
                                    if er_ce_method in ("A", "F"):
                                        _cd = int(ce_st[i])
                                        if _cd != 0 and _cd != _dd:
                                            ce_cnt["redirect"] += 1
                                            _ce_act = _dd
                                            _dd = _cd
                                    elif er_ce_method == "B":
                                        if int(ce_st[i]) != _dd:
                                            ce_cnt["blocked"] += 1
                                            ER_INFO["ce_events"].append(
                                                [i, dstr, "block", _dd, int(ce_st[i]), None])
                                            _dd = 0
                                if _dd != 0:
                                    seg_active = True; seg_dir = _dd; seg_date = dstr
                                    seg_below = 0
                                    if er_brk > 0:
                                        # 突破门版: 触发根只武装, 等段内首个突破根才置 er_trigger 入场
                                        seg_pending = True; seg_trigger_bar = i
                                    elif er_ce_method == "C":
                                        # _wcent C: 当根 CE 已同向直接入场; 否则挂起等对齐
                                        if int(ce_st[i]) == _dd:
                                            er_trigger = True
                                            ce_cnt["imm"] += 1
                                        else:
                                            ce_pending = True; ce_pend_bar = i
                                            ce_cnt["pend"] += 1
                                    else:
                                        er_trigger = True
                                    er_cnt["trigger"] += 1
                                    ER_INFO["dose_drops"].append(er_to - _to_i)
                                    if er_ramp_on:
                                        ER_INFO["ramp_trig"].append(
                                            [i, dstr, round(_dose_drop, 6),
                                             round(_ramp_drop, 6),
                                             round(float(er_arr[i]), 6),
                                             bool(float(er_arr[i]) < er_to - _dose_drop - 1e-12)])
                                    if er_drop_mode == "sum" or er_slope_on:
                                        _need_sl = bool(
                                            _to_noslope is not None
                                            and float(er_arr[i]) < _to_noslope - 1e-12)
                                        ER_INFO["drop_trig"].append(
                                            [i, dstr, round(_dose_drop, 6),
                                             round(_ramp_drop, 6),
                                             round(_slope_drop, 6),
                                             round(_to_i, 6), bool(_floor_hit),
                                             round(float(er_arr[i]), 6),
                                             _need_sl,
                                             (round(_sv, 6)
                                              if np.isfinite(_sv) else None)])
                                        if _floor_hit:
                                            drop_cnt["floor_trig"] += 1
                                        if _need_sl:
                                            slope_cnt["needed"] += 1
                                    ER_INFO["segments"].append([i, i, dstr])
                                    if _ce_act is not None:
                                        ER_INFO["ce_events"].append(
                                            [i, dstr, "redirect", _ce_act, _dd, None])
                else:
                    if (in_session and s >= 580 and i >= er_bw
                            and not np.isnan(er_arr[i]) and er_arr[i] >= er_th):
                        _dd = int(np.sign(c[i] - c[i - er_n]))
                        if _dd != 0:
                            _hh = float(np.max(h[i - er_bw:i])); _ll = float(np.min(l[i - er_bw:i]))
                            if (_dd > 0 and c[i] > _hh) or (_dd < 0 and c[i] < _ll):
                                seg_active = True; seg_dir = _dd; seg_date = dstr
                                er_trigger = True
                                er_cnt["trigger"] += 1
                                ER_INFO["segments"].append([i, i, dstr])
            else:
                ER_INFO["segments"][-1][1] = i
                _end_line = er_end if er_branch == "surge" else er_th
                if er_ce_method == "E" and int(ce_cst[i]) == -seg_dir:
                    # _wcent E: 自由运行 CE 状态翻到与段方向相反 -> 出段 (与 WER
                    # 出段先到先出; 同根两者同到仍只出一次)。注: 用的是自由 CE
                    # 状态, 与段锚 CE 线出段 (_wchand) 不是同一物。
                    seg_active = False; seg_ended_bar = True; seg_below = 0
                    ce_cnt["ce_exit"] += 1
                    ER_INFO["ce_events"].append(
                        [ER_INFO["segments"][-1][0], dstr, "ce_exit", seg_dir, i, None])
                elif er_exit_bars <= 1:
                    if np.isnan(er_arr[i]) or er_arr[i] < _end_line:
                        seg_active = False; seg_ended_bar = True
                else:
                    # R3a 出段迟滞: 连续 er_exit_bars 根低于出段线才结束
                    # R5 双层出段: 当根 < exit_deep 且纯迟滞本根尚不会出段 -> 1 根急出
                    if (er_exit_deep is not None and not np.isnan(er_arr[i])
                            and er_arr[i] < er_exit_deep
                            and seg_below + 1 < er_exit_bars):
                        seg_active = False; seg_ended_bar = True; seg_below = 0
                        er_cnt["deep_exit"] += 1
                    else:
                        if np.isnan(er_arr[i]) or er_arr[i] < _end_line:
                            seg_below += 1
                        else:
                            seg_below = 0
                        if seg_below >= er_exit_bars:
                            seg_active = False; seg_ended_bar = True; seg_below = 0
        # ---- _wcent C 对齐等待: pending 段等 CE 状态转同向才置 er_trigger 入场;
        # 段死 (WER 出段/跨日) 或等待 > ce_W 根则弃 (镜像突破门 pending 语义:
        # 对齐根若被趋势信号/趋势仓挡下, pending 同样消耗, 不再重试) ----
        if er_on and er_ce_method == "C" and ce_pending:
            if not seg_active:
                ce_pending = False
                ce_cnt["dead_seg"] += 1
                ER_INFO["ce_events"].append(
                    [ce_pend_bar, dstr, "dead_seg", seg_dir, i - ce_pend_bar, None])
            elif i - ce_pend_bar > er_ce_W:
                ce_pending = False
                ce_cnt["dead_timeout"] += 1
                ER_INFO["ce_events"].append(
                    [ce_pend_bar, dstr, "timeout", seg_dir, i - ce_pend_bar, None])
            elif in_session and int(ce_st[i]) == seg_dir:
                er_trigger = True
                ce_pending = False
                ce_cnt["delay_entries"] += 1
                ce_cnt["delay_bars"] += i - ce_pend_bar
                ER_INFO["ce_events"].append(
                    [ce_pend_bar, dstr, "entry", seg_dir, i - ce_pend_bar, None])
        # ---- ER 真突破门: 武装后等段内首个突破根入场; 段死 (含跨日) 仍未突破则作废 ----
        # 突破定义: 当根收盘严格破此前 er_brk 根最高 (多) / 最低 (空), 不含当根。
        # 置 er_trigger 后走原入场块 (执行口径/趋势让位/挡仓计数与无门版一字不差);
        # 首个突破根即入场根: 当根若被趋势信号/趋势仓挡下, pending 同样消耗、不再重试。
        if er_on and er_brk > 0 and seg_pending:
            if not seg_active:
                seg_pending = False
                er_cnt["gate_dead"] += 1
            elif in_session and i >= er_brk:
                _hh = float(np.max(h[i - er_brk:i])); _ll = float(np.min(l[i - er_brk:i]))
                if (seg_dir > 0 and c[i] > _hh) or (seg_dir < 0 and c[i] < _ll):
                    er_trigger = True
                    seg_pending = False
                    if i == seg_trigger_bar:
                        er_cnt["gate_imm"] += 1
                    else:
                        er_cnt["gate_delay"] += 1
                        er_cnt["gate_delay_bars"] += i - seg_trigger_bar
        _f2_pre = pos if (er_ce_f2 and pos_mode == "er") else 0  # _f2: 本根出场处理前 ER 仓方向快照
        # ---- 止损 (入场下一根起; 各模式见模块头 SL_MODE) ----
        if pos != 0 and i > entry_bar:
            if SL_MODE == "none":
                pass
            elif SL_MODE in ("atr", "split", "roi"):
                _rs = "SL_ROI" if SL_MODE == "roi" else "SL"
                if pos == 1 and sl_level is not None and l[i] <= sl_level:
                    close_pos(i, sl_level, _rs)
                elif pos == -1 and sl_level is not None and h[i] >= sl_level:
                    close_pos(i, sl_level, _rs)
            elif SL_MODE == "trail":
                if pos == 1:
                    if not sl_armed and h[i] - entry_px >= SL_TRAIL_X * entry_atr:
                        sl_level = entry_px + SL_TRAIL_LOCK * entry_atr
                        sl_armed = True
                    if l[i] <= sl_level:
                        close_pos(i, sl_level, "SL_TRAIL")
                else:
                    if not sl_armed and entry_px - l[i] >= SL_TRAIL_X * entry_atr:
                        sl_level = entry_px - SL_TRAIL_LOCK * entry_atr
                        sl_armed = True
                    if h[i] >= sl_level:
                        close_pos(i, sl_level, "SL_TRAIL")
            elif SL_MODE == "breakeven":
                if pos == 1:
                    if not sl_armed and _roi_pct(1, c[i] - entry_px) >= SL_BE_T:
                        sl_level = entry_px
                        sl_armed = True
                    if l[i] <= sl_level:
                        close_pos(i, sl_level, "SL_BE")
                else:
                    if not sl_armed and _roi_pct(-1, c[i] - entry_px) >= SL_BE_T:
                        sl_level = entry_px
                        sl_armed = True
                    if h[i] >= sl_level:
                        close_pos(i, sl_level, "SL_BE")
            elif SL_MODE == "time":
                if pos == 1 and l[i] <= sl_level:
                    close_pos(i, sl_level, "SL")
                elif pos == -1 and h[i] >= sl_level:
                    close_pos(i, sl_level, "SL")
                elif i - entry_bar >= SL_TIME_N:
                    if (pos == 1 and c[i] <= entry_px) or (pos == -1 and c[i] >= entry_px):
                        close_pos(i, c[i], "SL_TIME")
            elif SL_MODE == "chand":
                if pos == 1:
                    sl_extreme = max(sl_extreme, h[i])
                    sl_level = sl_extreme - SL_K * entry_atr
                    if l[i] <= sl_level:
                        close_pos(i, sl_level, "SL_CHAND")
                else:
                    sl_extreme = min(sl_extreme, l[i])
                    sl_level = sl_extreme + SL_K * entry_atr
                    if h[i] >= sl_level:
                        close_pos(i, sl_level, "SL_CHAND")
        # ---- 强制平 (EOD / EXIT BO / EXIT T; 趋势接管: EXIT T 只被趋势信号压住, 与基线一致) ----
        force_exit = False
        if pos != 0:
            exit_regime = (strong_down and pos == 1) or (strong_up and pos == -1)
            exit_eod = eod
            exit_bo_pos = exit_bo and (pos_mode in ("bo", "rev")) and ((reverse_source_dir == 1 and pos == 1)
                                       or (reverse_source_dir == -1 and pos == -1))
            exit_ut_fail = (ut_failed and UT_EXIT_ON_FAILURE
                            and ((ut_trade_dir == 1 and pos == 1)
                                 or (ut_trade_dir == -1 and pos == -1)))
            if exit_eod or exit_bo_pos or exit_ut_fail or (exit_regime and not trend_special):
                force_exit = True
                if exit_eod:
                    rs = "EOD"
                elif exit_bo_pos:
                    rs = "EXIT BO"
                elif exit_ut_fail:
                    rs = "UT FAIL"
                else:
                    rs = "EXIT T"
                if exit_ut_fail and START_DATE <= dstr <= END_DATE:
                    V67_STATS["ut_failed_exits"] += 1
                close_pos(i, c[i], rs)
        # ---- _f2 止损等待置位: 本根 ER 仓被止损 (SL) 平掉且段还活着 -> 进等待 ----
        if (er_ce_f2 and not er_ce_f3 and _f2_pre != 0 and pos == 0 and seg_active
                and er_last_close is not None and er_last_close[0] == i
                and er_last_close[1] == "SL"):
            f2_wait = True; f2_wait_dir = _f2_pre; f2_stop_bar = i
        # ---- _wcent F 翻仓跟随: 段内持 ER 仓且自由 CE 状态翻到与持仓反向 ->
        # 同根翻仓 (open_pos 平旧开反向, 止损按新入场价/ATR 重锚); 段与屏蔽
        # 延续。本根止损/强制平已先行 (pos 已空则不翻); 段尾根不翻 (ER_END 优先)。 ----
        if (er_on and not er_ce_f3 and er_ce_method == "F" and pos != 0 and pos_mode == "er"
                and seg_active and not seg_ended_bar and i > entry_bar
                and int(ce_cst[i]) == -pos):
            _s0 = ER_INFO["segments"][-1][0]
            ce_cnt["flip"] += 1
            ce_flip_map[_s0] = ce_flip_map.get(_s0, 0) + 1
            ER_INFO["ce_events"].append([_s0, dstr, "flip", pos, -pos, i])
            ce_flip_bar = i
            open_pos(-pos, "er")
        # ---- _wcent2 RF 实时翻仓 (快照入场版): 段方向仍为入场快照, 段内
        # 持 ER 仓且确认后 CE 状态翻到与持仓反向 -> 同根翻仓跟随 (机制同
        # F: open_pos 平旧开反向、止损重锚、段/屏蔽延续; 区别仅入场方向
        # 源)。本根止损/强制平已先行; 段尾根不翻 (ER_END 优先)。 ----
        if (er_on and not er_ce_f3 and er_ce_method == "RF" and pos != 0 and pos_mode == "er"
                and seg_active and not seg_ended_bar and i > entry_bar
                and int(ce_cst[i]) == -pos):
            _s0 = ER_INFO["segments"][-1][0]
            ce_cnt["flip"] += 1
            ce_flip_map[_s0] = ce_flip_map.get(_s0, 0) + 1
            ER_INFO["ce_events"].append([_s0, dstr, "flip", pos, -pos, i])
            ce_flip_bar = i
            open_pos(-pos, "er")
        # ---- _f2 段内止损后 CE 变向重开: 等待中 (止损根之后) 首个 CE 状态 ==
        # 与被止损腿相反方向的根按该方向重开 (止损根当根已完成变向 -> 次根开);
        # 止损由 open_pos 按新腿重锚; 重开后恢复 RF 跟随; 段结束不再开 ----
        if er_ce_f2 and not er_ce_f3 and f2_wait:
            if not seg_active or seg_ended_bar:
                f2_wait = False
            elif (pos == 0 and in_session and not trend_special
                    and i > f2_stop_bar and int(ce_cst[i]) == -f2_wait_dir):
                _s0 = ER_INFO["segments"][-1][0]
                ce_cnt["f2_reopen"] = ce_cnt.get("f2_reopen", 0) + 1
                ER_INFO["ce_events"].append(
                    [_s0, dstr, "f2_reopen", -f2_wait_dir, i, f2_stop_bar])
                f2_wait = False
                if open_pos(-f2_wait_dir, "er"):
                    er_ext = entry_px
                    er_ext = max(er_ext, float(h[i])) if -f2_wait_dir == 1 \
                        else min(er_ext, float(l[i]))
        # ---- _f3 状态跟随: 段在场期间每根对齐 CE 当根状态 (替代 RF 翻仓
        # 与 _f2 重开): 持 ER 仓方向不符 -> 同根翻仓; 空仓且槽位空闲
        # (pos==0) -> 按 CE 状态开 (不限入段/止损后/被接管后, 止损后同向
        # 也开); ER 仓平仓根本身不补 (次根起); 趋势信号根让位、EOD 根与
        # 段尾根不动作; 槽位被本体仓占用时 pos!=0 天然跳过, 空出后补齐 ----
        if (er_on and er_ce_f3 and seg_active and not seg_ended_bar
                and in_session and not trend_special and not eod):
            _cst3 = int(ce_cst[i])
            if (pos != 0 and pos_mode == "er" and i > entry_bar
                    and _cst3 != 0 and _cst3 == -pos):
                _s0 = ER_INFO["segments"][-1][0]
                ce_cnt["flip"] = ce_cnt.get("flip", 0) + 1
                ce_flip_map[_s0] = ce_flip_map.get(_s0, 0) + 1
                ER_INFO["ce_events"].append([_s0, dstr, "flip", pos, -pos, i])
                ce_flip_bar = i
                open_pos(-pos, "er")
            elif (pos == 0 and _cst3 != 0
                    and not (er_last_close is not None and er_last_close[0] == i)):
                _s0 = ER_INFO["segments"][-1][0]
                ce_cnt["f3_open"] = ce_cnt.get("f3_open", 0) + 1
                ER_INFO["ce_events"].append([_s0, dstr, "f3_open", _cst3, i, None])
                if open_pos(_cst3, "er"):
                    er_ext = entry_px
                    er_ext = max(er_ext, float(h[i])) if _cst3 == 1 \
                        else min(er_ext, float(l[i]))
        # ---- ER 覆盖层: ER 单出场 (B: 50% 回落止盈先判后更; 段尾根平) ----
        if er_on and pos != 0 and pos_mode == "er" and i > entry_bar:
            _er_closed = False
            if er_b_mode and er_ext is not None:
                _gain = (er_ext - entry_px) if pos == 1 else (entry_px - er_ext)
                if _gain > 0:
                    _line = er_ext - 0.5 * _gain if pos == 1 else er_ext + 0.5 * _gain
                    if (pos == 1 and c[i] <= _line) or (pos == -1 and c[i] >= _line):
                        close_pos(i, c[i], "ER_TP")
                        _er_closed = True
            if not _er_closed:
                if seg_ended_bar:
                    close_pos(i, c[i], "ER_END")
                elif er_b_mode and er_ext is not None:
                    if pos == 1:
                        er_ext = max(er_ext, float(h[i]))
                    else:
                        er_ext = min(er_ext, float(l[i]))
        # ---- 进场 (趋势接管: 趋势绝对优先 Trend > REV > BO > Gaussian/UT) ----
        # 影子仓位在此处记录: 止损/强制平之后、进场决策之前的位置 (决定基线是否开趋势单)
        pos_before_list.append(pos)
        can_long = bool(final_b) and (not special_entry) and (not force_exit) and (pos == 0 or pos == -1)
        can_short = bool(final_s) and (not special_entry) and (not force_exit) and (pos == 0 or pos == 1)
        # ---- ER 覆盖层: 区域内屏蔽本体普通 (高斯) 入场 ----
        # 旧口径 (exit_caliber="old"): 段内 + 段结束根全屏蔽, 次根起接管。
        # _nb 定版口径 ("new"): 段内屏蔽照旧, 但 ER 出场根不屏蔽 -- 本根有
        # ER 仓被平 (段尾/止盈/止损, er_last_close 记在本根) 则该根不屏蔽,
        # 高斯可同根开仓; 段结束根 seg_active 已 False, 天然不屏蔽。
        _er_exit_bar = er_last_close is not None and er_last_close[0] == i
        if er_exit_new:
            _mask_now = seg_active and not _er_exit_bar
        else:
            _mask_now = seg_active or seg_ended_bar
        if er_ce_method in ("F", "RF") and ce_flip_bar == i:
            _mask_now = seg_active  # _wcent F: 翻仓根 ER 仓延续, 不按 ER 出场根解屏蔽
        if er_ce_f3 and er_f3_shield:
            _mask_now = seg_active  # _f3s: F3 活段全程屏蔽本体, 出场根不留口子
        if er_on and _mask_now:
            if (final_b and (pos == 0 or pos == -1)) or (final_s and (pos == 0 or pos == 1)):
                er_cnt["masked_sig"] += 1
            can_long = False; can_short = False
        # ---- ER 覆盖层: ER 入场 (趋势信号当根让位; 持趋势仓挡; 持普通仓改开) ----
        if er_on and er_trigger and not trend_special:
            if pos == 0:
                if open_pos(seg_dir, "er"):
                    er_cnt["entries"] += 1
                    er_ext = entry_px
                    er_ext = max(er_ext, float(h[i])) if seg_dir == 1 else min(er_ext, float(l[i]))
                    can_long = False; can_short = False
            elif pos_mode == "trend":
                er_cnt["blocked_trend_pos"] += 1  # 该段 ER 作废 (入场只在触发根)
            elif pos_mode == "er":
                pass
            else:
                close_pos(i, c[i], "ER_SWAP")
                er_cnt["swaps"] += 1
                if open_pos(seg_dir, "er"):
                    er_cnt["entries"] += 1
                    er_ext = entry_px
                    er_ext = max(er_ext, float(h[i])) if seg_dir == 1 else min(er_ext, float(l[i]))
                can_long = False; can_short = False
        # ---- ER 覆盖层: 段内续开 (TP/SL 出场后紧接下一根, 段还活着才再开;
        # 段尾/趋势穿透出场不续; 方向锁定 seg_dir; 止盈跟踪自新入场价重置) ----
        if (er_on and er_reentry and not er_trigger and not trend_special
                and in_session and seg_active and pos == 0
                and er_last_close is not None and er_last_close[0] == i - 1
                and er_last_close[1] in ("ER_TP", "SL")):
            if open_pos(seg_dir, "er"):
                er_cnt["reentries"] += 1
                er_ext = entry_px
                er_ext = max(er_ext, float(h[i])) if seg_dir == 1 else min(er_ext, float(l[i]))
                can_long = False; can_short = False
        if enter_trend_long or enter_trend_short:
            tdir = 1 if enter_trend_long else -1
            if control_pos is None:
                # 无影子 (control 复现基线): 仅 BO/REV 仓转换, 其余走 open_pos 原语义
                # (control 下 pos_mode 永不为 bo/rev, 故等价于基线)
                bo_dir = 0; bo_start_bar = -1
                reverse_armed_dir = 0; reverse_armed_bar = -1; reverse_source_dir = 0
                if pos != 0 and pos_mode in ("bo", "rev"):
                    if (pos == 1 and tdir == 1) or (pos == -1 and tdir == -1):
                        if START_DATE <= dstr <= END_DATE:
                            V67_STATS["trend_protect_convert"] += 1
                        close_pos(i, c[i], "TREND")
                        open_pos(tdir, "trend")
                    else:
                        if START_DATE <= dstr <= END_DATE:
                            V67_STATS["trend_protect_flip_bo"] += 1
                        open_pos(tdir, "trend")
                else:
                    open_pos(tdir, "trend")
            else:
                # 有影子: 只有影子基线会开趋势单时 (基线进场决策前一刻空仓或反向) 我方才开;
                # 基线已同向持仓时基线不开, 我方也不开 (否则多出基线没有的趋势单)
                base_dir = int(control_pos[i])
                if base_dir == tdir:
                    # 基线已同向持仓: 不开趋势单, 我方仓位原样保留
                    if START_DATE <= dstr <= END_DATE:
                        V67_STATS["trend_protect_skip_same"] += 1
                else:
                    # 基线会开趋势单: 接管/反手状态清掉, 我方必须在该 bar 开出一致的趋势单
                    bo_dir = 0; bo_start_bar = -1
                    reverse_armed_dir = 0; reverse_armed_bar = -1; reverse_source_dir = 0
                    if pos != 0 and pos_mode in ("bo", "rev", "edge"):
                        if (pos == 1 and tdir == 1) or (pos == -1 and tdir == -1):
                            # 同向: 平 BO/REV/边缘仓, 在趋势信号 bar 重开趋势仓
                            if START_DATE <= dstr <= END_DATE:
                                V67_STATS["trend_protect_convert"] += 1
                            close_pos(i, c[i], "TREND")
                            open_pos(tdir, "trend")
                        else:
                            # 反向: 翻仓 (open_pos 内平仓 reason=FLIP, 与基线一致)
                            if START_DATE <= dstr <= END_DATE:
                                V67_STATS["trend_protect_flip_bo"] += 1
                            open_pos(tdir, "trend")
                    elif pos != 0 and pos_mode == "trend":
                        # 持趋势仓: 同向不动 (基线也不开, 属不变量违背, 记数); 反向翻仓
                        if (pos == 1 and tdir == 1) or (pos == -1 and tdir == -1):
                            if START_DATE <= dstr <= END_DATE:
                                V67_STATS["trend_protect_skip_trend"] += 1
                        else:
                            open_pos(tdir, "trend")
                    else:
                        # 空仓直接开
                        open_pos(tdir, "trend")
        elif enter_rev_long:
            open_pos(1, "rev")
        elif enter_rev_short:
            open_pos(-1, "rev")
        elif enter_bo_long:
            open_pos(1, "bo")
        elif enter_bo_short:
            open_pos(-1, "bo")
        elif can_long and not can_short:
            if open_pos(1, "ut" if ut_b else "edge") and er_on \
                    and (seg_active or seg_ended_bar):
                er_cnt["seg_gauss_entries"] += 1  # _nb: 段窗口内高斯实际开仓
        elif can_short and not can_long:
            if open_pos(-1, "ut" if ut_s else "edge") and er_on \
                    and (seg_active or seg_ended_bar):
                er_cnt["seg_gauss_entries"] += 1  # _nb: 段窗口内高斯实际开仓
        prev_strong_up = strong_up
        prev_strong_down = strong_down
    V67_STATS["pos_before"] = np.array(pos_before_list, dtype=int)
    return trades
