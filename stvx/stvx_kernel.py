#!/usr/bin/env python3
"""AMGATE 变体 (2026-11-16, user 点名): = st_kernel_gf.py + 上午收益门控 (机制写进内核本体,
不拼凑)。与 st_kernel_gf.py 的差异只有 AMGATE 扩展 (默认关, 关时逐字同 gf):
  pi[58]=AMG_ON (env DG_AMG="1"), pf[66]=AMG_PCT (阈值%, env DG_AMG_PCT),
  pi[59]=AMG_START (分钟, env DG_AMG_START, 默认 810=13:30),
  pi[60]=AMG_END (分钟, env DG_AMG_END, 默认 900=15:00)。
  低侧扩展 (2026-11-17): pi[61]=AMG_MODE (env DG_AMG_MODE: hi=默认高侧 val>=PCT /
  lo=低侧 val<=PCT, PCT 传负数 / dual=双侧), pf[67]=AMG_LO (env DG_AMG_LO, 默认 -30);
  触发判定统一走 _amg_hit, mode 0 与原定版逐字同。
  上午收益 = 内核自己的单按 settle_unified walk 口径逐 bar 维护 (刷单腿 gamma>=+10%
  按 bar 收盘切腿连乘; 窗口内整笔平仓的单末腿记 v2 罚 (仅 put, 按整单 d 一次);
  13:00 未平的腿按 13:00 bar 开盘价标记, 不计罚), 13:00 bar 首根封存判定 (因果)。
  达阈值 -> 当天 [AMG_START, AMG_END) 只平不开 (翻仓降只平, 新开全挡; 止损/日终平照常);
  AMG_END 首根 bar 若空仓且 ST 趋势有定义 -> 按当时趋势直接开仓 (rsn 13「窗口恢复开X」,
  状态触发)。GF 规则保持原样、互不干扰。交易内核签名末尾比 gf 多 am_j13/am_ret/am_trig/diag。
  diag[4]=被挡开仓次数, diag[5]=翻仓降级只平次数, diag[6]=恢复开仓次数。

GF 变体 (2026-11-14): = st_kernel_fs.py + 大跳空日上午空仓门 (DG_GF)。
GF 语义: 当日 |今开-昨收| >= DG_GF_G 点 -> 当天 DG_GF_TMIN (ET 分钟数) 之前封锁一切开仓
(翻仓/开盘开仓/兜底全挡; 数组内核不动, 带/趋势照常更新), T 首根空仓时按当时 trend 入场
(执行价口径与开盘开仓同: 信号根收盘判定、次根开盘结算)。GF 关时与 fs 逐字同路径。

FS 变体 (2026-11-14, user 点名): 只在翻仓信号根查止损。
与 st_kernel_es.py 的唯一数值差异: 止损窗口触发条件加 trend[i] != trend[i-1] 门
——非翻仓信号 bar 一律不查止损 (浮亏穿 -20% 也不动); 翻仓信号根上先查止损,
已穿 -20% 则按现行 machinery 进窗口 (同根翻仓不执行, 窗口优先, 与正本同根
触发口径一致), 没穿则翻仓照常。其余逐字同 st_kernel_es.py。

以下为 ES 原说明:
ST-only vp 引擎的 numba 编译内核 (2026-11-13 试点) — ES 分支副本 (st_kernel_nc.py)。

与 st_kernel_nc.py 的差异: 新增两个开盘 ATR 处置分支 (默认关, 关时与 nc 逐字同路径)。
OPX 乘法压带 (DG_OPX=1, pi[53], pf[59..61]): 开盘 OPX_WIN 分钟内,
压幅 drop = clamp(K*(ATR当根/ATR昨收 - 1), 0, CAP), 因子 (1-drop) 与当日滑梯/PB
因子相乘叠加, 只压趋势侧带; ATR 不爆的日子 drop 自动 = 0。
OPR 比值压带式 (DG_OPR=1, pi[54], pf[62..63]; user 2026-11-13 21:15 定版):
走现有压带乘法通道 (与滑梯/PB/OPX 同一合成点, 因子相乘, 只压趋势侧带), 不动
ATR 本身。ratio = ATR当根 / REFN, REFN = 过去 N 个交易日「开盘首根后 ATR」的
均值 (DG_OPR_NREF, pi[55], 默认 5; 同时刻基准, 开盘前已知、无未来函数);
drop = max(0, 1 - C/ratio) (ratio <= C 时不压; 数学上等价于把带宽 ATR 封顶在
C*REFN); 消退 (DG_OPR_FADE, pi[56], 默认开): drop_t = drop * (1 - 已开盘分钟/
窗口分钟), 窗口末归零; FADE=0 为不消退对照 (等价硬切封顶)。

以下为 NC 分支原说明:
ST-only vp 引擎的 numba 编译内核 (2026-11-13 试点) — NC 分支副本 (st_kernel_s5x.py)。

与 st_kernel_s5x.py 的唯一差异: 新增 NC 开盘跟正常信号分支 (DG_NC=1, pi[52];
默认关, 关时与 s5x 逐字同路径)。NC 语义: 日首先用今开价对带入带子做一次虚拟
翻转判定 (隔夜跳先行消化, 模拟连续 tape 的 9:30 信号状态), 翻了则当日自首根起
按新方向走; 开仓仍走原开盘入场 (含 delay/PB/滑梯等全部原有机制, 只换信号状态)。

与 st_kernel_numba.py 的唯一差异: 新增 S5 slope5 连续剂量压带分支
(与慢版 stonly_gen_s5.py 逐项对齐; 原文件不动)。S5 语义:
  s = 同向 slope5 分量 (多头趋势 max(0,slp5); 空头趋势 max(0,-slp5))
  机制: 趋势中动能冲到极端 (高潮) 大概率反转, 趁冲高预先压紧趋势侧带, 反转来时出得快
  起压点: s < START (0.8) 不压; s >= START 起压; drop = min(CAP, K*s) (用 s 本身乘 K);
  乘数 qm = 1-drop (压趋势侧最终带, 棘轮同 QD)
  距离门: 剂量 x clamp((影子带距/ATR-1)/1.5, 0, 1)  [带距>=2.5 全剂量, <=1 零剂量]
  年龄门: 当前信号年龄 (trend_run) >= 17 剂量归零
参数: pf[40]=K pf[41]=CAP pf[42]=AGE_TH; pi[43]=S5_ON pi[44]=GDIST pi[45]=GAGE。

把 stonly_gen_vp.py 的两段逐 bar 数值状态机移植为 numba 内核:
  A) Supertrend 数组计算 (TR/ATR/基础带/最终带/趋势 + 全部 STDIGEST 组件钩子
     + vp 的 EE/SKIP 日标志), 即慢版 584-955 行;
  B) ST-only 交易循环 (日切/开盘ignore/日终平/止损窗口/Gamma 20%止损/EE 提前认错/
     ST 翻转与开盘开仓), 即慢版主循环在 ST_ONLY=1 下实际走到的路径
     (大K/3K 等待与止盈在 ST-only 下永不触发, 状态恒 None, 不移植)。
I/O、env 解析、数据装载留在本文件的 Python 外壳; 输出与慢版 pkl 同构
{actions, week_monday, warm_t, warm_o, warm_h, warm_l, warm_c}。

参数化: 全部钩子参数打包为 pf (float64) / pi (int64) 两个向量作内核入参,
换参数格只换向量值, 不重编译。衰减剂量压紧臂未定型, 不在本内核内
(扩展位: pf/pi 尾部预留空槽, 内核 A 的带计算出口 _bl_e/_bu_e 处可插入)。

验证纪律: 与慢版逐笔全等 (bar/时刻/动作/方向/价格/原因串) 才算可用;
浮点运算逐字照抄慢版表达式与运算顺序, min/max 用 Python 语义助手
(NaN 行为与 np.minimum 不同, 慢版用的是 builtins min/max)。
"""
import os
import sys

import numpy as np
import pandas as pd
from numba import njit

# ---------------- 参数向量布局 ----------------
# pf (float64):
#  0 C4DISC      1 C4D0(c4s)   2 C4T(c4s)    3 C4A_D0      4 C4A_T
#  5 AGE_FULL    6 AGE_CUT     7 V4F         8 V4WIN       9 V3RHO
# 10 C1THR      11 C3THR      12 C1P_SCALE  13 C1PHI_LO   14 C3P_SCALE
# 15 V2GK       16 GAPH_C     17 GAPH_MIN   18 YGAP_C     19 YGAP_MIN
# 20 QF_THR     21 QF_RATIO   22 YGREV_PTS  23 YD0BIG     24 YD0
# 25 YD0REV     26 EE_PTS     27 SKIP_PTS   28 OLD_PTS    29 OLD0
# 30 OLD_T      31 OLD_AGE    32 STOP_WIN_RECOV          33 EE_ATR
# 34 V1_ALPHA
PF_N = 80
# pi (int64):
#  0 V1_ON       1 V1_BARS     2 V2_ON       3 V4_ON       4 V3_ON
#  5 C1_ON       6 C3_ON       7 C4_ON       8 C4WBARS     9 C4S_ON
# 10 C4EXP      11 C4COND    12 C4AGE_ON   13 AGEG_MODE(0 step/1 lin/2 exp)
# 14 C1P_ON     15 C3P_ON     16 V2G_ON     17 GAPH_ON    18 YGAP_ON
# 19 QF_ON      20 QF_HALF    21 YGREV_ON   22 YD0_ON     23 YD0REV_ON
# 24 EE_ON      25 EE_BARS    26 SKIP_ON    27 OLD_ON     28 OLD_MODE(0 both/1 same/2 rev/3 never)
# 29 C2_ON      30 DWELL_BARS 31 V5_ON      32 STOP_WIN_BARS
PI_N = 80

_SKIP_DATES = {"2026-06-17"}


def _dgf(env, name, default):
    v = env.get(name, "")
    return float(v) if v else default


def make_params(env):
    """与 stonly_gen_vp.py 顶层 env 解析逐项对齐 (含各钩子的门控默认)。"""
    pf = np.zeros(PF_N, dtype=np.float64)
    pi = np.zeros(PI_N, dtype=np.int64)
    stv = set(t for t in env.get("STDIGEST", "").split("+") if t)

    def has(tok):
        return tok in stv

    # --- 一轮组件 ---
    if has("v1a"):
        pi[0] = 1; pi[1] = 6; pf[34] = 1.0 / 3.0
    elif has("v1b"):
        pi[0] = 1; pi[1] = 12; pf[34] = 0.25
    pi[2] = 1 if has("v2") else 0
    if has("v4a"):
        pi[3] = 1; pf[7] = _dgf(env, "DG_V4F", 1.5)
    elif has("v4b"):
        pi[3] = 1; pf[7] = _dgf(env, "DG_V4F", 1.3)
    pf[8] = float(env.get("DG_V4WIN", "10"))
    if has("v3a"):
        pi[4] = 1; pf[9] = _dgf(env, "DG_RHO", 0.5)
    elif has("v3b"):
        pi[4] = 1; pf[9] = _dgf(env, "DG_RHO", 0.0)
    if has("c1a"):
        pi[5] = 1; pf[10] = _dgf(env, "DG_C1THR", 0.7)
    elif has("c1b"):
        pi[5] = 1; pf[10] = _dgf(env, "DG_C1THR", 1.2)
    if has("c3a"):
        pi[6] = 1; pf[11] = _dgf(env, "DG_C3THR", 0.7)
    elif has("c3b"):
        pi[6] = 1; pf[11] = _dgf(env, "DG_C3THR", 1.2)
    # --- c4 (旧快速认错) ---
    c4_on = has("c4") or has("c4a") or has("c4b")
    pi[7] = 1 if c4_on else 0
    if c4_on:
        pf[0] = _dgf(env, "DG_C4DISC", 0.7 if has("c4b") else 0.5)
        pi[8] = max(1, int(round(float(env.get("DG_C4WIN_MIN", "30")) / 3.0)))
    # --- c4s (平滑版) ---
    pi[9] = 1 if has("c4s") else 0
    if pi[9]:
        pf[1] = _dgf(env, "DG_C4D0", 0.5)
        pf[2] = _dgf(env, "DG_C4T", 90.0)
    pi[10] = 1 if env.get("DG_C4SHAPE", "") == "exp" else 0
    pi[11] = 1 if env.get("DG_C4COND", "") == "1" else 0
    # --- c4age (年龄门) ---
    c4age_on = has("c4age")
    pi[12] = 1 if c4age_on else 0
    if c4age_on:
        pf[3] = _dgf(env, "DG_C4D0", 0.5)
        pf[4] = _dgf(env, "DG_C4T", 90.0)
    ageg = env.get("DG_AGEG", "step") if c4age_on else "step"
    pi[13] = 1 if ageg == "lin" else (2 if ageg == "exp" else 0)
    pf[5] = _dgf(env, "DG_AGE_FULL", 7.0) if c4age_on else 7.0
    pf[6] = _dgf(env, "DG_AGE_CUT", 20.0) if c4age_on else 20.0
    # --- c1p / c3p ---
    pi[14] = 1 if has("c1p") else 0
    pf[12] = _dgf(env, "DG_C1P", 1.0) if pi[14] else 0.0
    pf[13] = _dgf(env, "DG_C1PHI_LO", 0.0)
    pi[15] = 1 if has("c3p") else 0
    pf[14] = _dgf(env, "DG_C3P", 1.0) if pi[15] else 0.0
    # --- v2g ---
    pi[16] = 1 if has("v2g") else 0
    if pi[16]:
        pf[15] = _dgf(env, "DG_V2GK", 1.0)
    # --- gaph / ygap ---
    pi[17] = 1 if (has("gaph") and c4age_on) else 0
    pf[16] = _dgf(env, "DG_GAPH_C", 0.0) if pi[17] else 0.0
    pf[17] = _dgf(env, "DG_GAPH_MIN", 1.0) if pi[17] else 1.0
    pi[18] = 1 if (has("ygap") and c4age_on) else 0
    pf[18] = _dgf(env, "DG_YGAP_C", 0.0) if pi[18] else 0.0
    pf[19] = _dgf(env, "DG_YGAP_MIN", 1.0) if pi[18] else 1.0
    # --- yd0 (无组件门控, env 在即生效) ---
    if env.get("DG_YD0"):
        pi[22] = 1; pf[24] = float(env["DG_YD0"])
    # --- qf ---
    qf_on = has("qf") and c4age_on
    pi[19] = 1 if qf_on else 0
    if qf_on:
        pf[20] = _dgf(env, "DG_QF_THR", 1.0)
        pf[21] = _dgf(env, "DG_QF_RATIO", 0.5)
    pi[20] = 1 if (qf_on and env.get("DG_QF", "off") == "half") else 0
    # --- ygrev ---
    ygrev_on = has("ygrev") and c4age_on
    pi[21] = 1 if ygrev_on else 0
    if ygrev_on:
        pf[22] = _dgf(env, "DG_YGREV_PTS", 5.0)
        pf[23] = _dgf(env, "DG_YD0BIG", 0.8)
    if env.get("DG_YD0REV"):
        pi[23] = 1; pf[25] = float(env["DG_YD0REV"])
    # --- vp: EE / SKIP (慢版: 关时参数取固定默认, 与 env 无关) ---
    ee_on = env.get("DG_EE", "") == "1"
    pi[24] = 1 if ee_on else 0
    pf[26] = _dgf(env, "DG_EE_PTS", 2.0) if ee_on else 2.0
    pi[25] = int(_dgf(env, "DG_EE_BARS", 10.0)) if ee_on else 10
    pf[33] = _dgf(env, "DG_EE_ATR", 0.75) if ee_on else 0.75
    skip_on = env.get("DG_YRSKIP", "") == "1"
    pi[26] = 1 if skip_on else 0
    pf[27] = _dgf(env, "DG_SKIP_PTS", 5.0) if skip_on else 5.0
    # --- vp: OLD ---
    old_on = env.get("DG_OLD", "") == "1"
    pi[27] = 1 if old_on else 0
    if old_on:
        pf[28] = _dgf(env, "DG_OLD_PTS", 2.0)
        pf[29] = _dgf(env, "DG_OLD0", 0.75)
        pf[30] = _dgf(env, "DG_OLD_T", 60.0)
        pf[31] = _dgf(env, "DG_OLD_AGE", 17.0)
    old_mode_s = env.get("DG_OLD_MODE", "both") if old_on else "both"
    pi[28] = 0 if old_mode_s == "both" else (1 if old_mode_s == "same" else (2 if old_mode_s == "rev" else 3))
    # --- c2 / v5 ---
    pi[29] = 1 if has("c2") else 0
    pi[30] = max(1, int(round(float(env.get("DG_DWELL_MIN", "30")) / 3.0)))
    pi[31] = 1 if has("v5") else 0
    # --- 止损窗口 (交易循环) ---
    pf[32] = float(env.get("STOP_WIN_RECOV", "0.50"))
    pi[32] = int(env.get("STOP_WIN_BARS", "5"))
    # --- nb 扩展 (2026-11-13): PF 沿用臂, 与慢版 stonly_gen_pf.py 逐项对齐 ---
    # pf[35]=PF_D0  pf[36]=PF_T ; pi[33]=PF_ON  pi[34]=PF_CLK(0=a 沿原窗口/1=b 自翻仓重滑)
    pf_on = env.get("DG_PF", "") == "1"
    pi[33] = 1 if pf_on else 0
    pi[34] = 0 if env.get("DG_PF_CLK", "b") == "a" else 1
    if pf_on:
        pf[35] = _dgf(env, "DG_PF_D0", 1.0)
        pf[36] = _dgf(env, "DG_PF_T", 60.0)
    # --- nb 扩展 (2026-11-13): QD 衰减剂量臂, 与慢版 stonly_gen_qd.py 逐项对齐 ---
    # pf[37]=QD_START pf[38]=QD_FLOOR pf[39]=QD_GFLOOR
    # pi[35]=QD_ON pi[36]=QD_MODE(1 decay/2 slope) pi[37]=QD_GATE(0 none/1 scale/2 floor) pi[38]=QD_AGE05
    qd_on = env.get("DG_QD", "") == "1"
    pi[35] = 1 if qd_on else 0
    pi[36] = 2 if env.get("DG_QD_MODE", "decay") == "slope" else 1
    pi[37] = {"none": 0, "scale": 1, "floor": 2}[env.get("DG_QD_GATE", "none")] if qd_on else 0
    pi[38] = 1 if env.get("DG_QD_AGE05", "") == "1" else 0
    if qd_on:
        pf[37] = _dgf(env, "DG_QD_START", 0.25)
        pf[38] = _dgf(env, "DG_QD_FLOOR", 0.6)
        pf[39] = _dgf(env, "DG_QD_GFLOOR", 1.0)
    # --- op 扩展 (2026-11-13): 开盘入场政策, 与慢版 stonly_gen_op.py / _op2.py 逐项对齐 ---
    # pi[39]=OP_POLICY(0 A 现行/1 B1 延迟K根/2 B2 只等翻转/3 S 按今天 ST 信号开) pi[40]=OP_K
    # pi[41]=OP_IGN_OFF(1=gap-ignore 完全不加) pi[42]=OP_KRAW(1=K 从日首原始根数计, 含 ignore 窗;
    #   0=K 计在 ignore 窗之后的可交易根数 [主口径])
    # S 族 (pi[39]=3): 开盘空仓待命, 今天 ST 翻转当根开; 前 3 根可交易 bar 未翻,
    #   第 3 根按当时 ST 状态方向兜底开仓。
    pi[39] = {"a": 0, "b1": 1, "b2": 2, "s": 3, "skip": 4, "delay": 5}[env.get("DG_OP_POLICY", "a")]
    pi[40] = int(_dgf(env, "DG_OP_K", 0.0))
    pi[41] = 1 if env.get("DG_OP_IGN", "on") == "off" else 0
    pi[42] = 1 if env.get("DG_OP_KMODE", "post") == "raw" else 0
    # --- s5 扩展 (2026-11-13): slope5 连续剂量压带, 与慢版 stonly_gen_s5.py 逐项对齐 ---
    # pf[40]=S5_K pf[41]=S5_CAP pf[42]=S5_AGE_TH pf[43]=S5_START ; pi[43]=S5_ON pi[44]=S5_GDIST pi[45]=S5_GAGE
    s5_on = env.get("DG_S5", "") == "1"
    pi[43] = 1 if s5_on else 0
    pi[44] = 0 if env.get("DG_S5_GDIST", "1") == "0" else 1
    pi[45] = 0 if env.get("DG_S5_GAGE", "1") == "0" else 1
    if s5_on:
        pf[40] = _dgf(env, "DG_S5_K", 0.3)
        pf[41] = _dgf(env, "DG_S5_CAP", 0.4)
        pf[42] = _dgf(env, "DG_S5_AGE_TH", 17.0)
        pf[43] = _dgf(env, "DG_S5_START", 0.8)
    # --- opd 扩展 (2026-11-13 组合表轮): 条件跳过/延迟, 移植自 st_kernel_numba 同名逻辑,
    #   槽位避开 S5 (pf[40..43]/pi[43..47]): pf[48..49]=年龄带 pf[50..51]=幅度带
    #   pi[48]=SK_LIST(按日集合) pi[49]=SK_DIR(0任意/1同向/2反向)。命中定义与
    #   st_kernel_numba 逐字同: 年龄=昨收 trend 尾部等值游程, signed=(今开-昨收)x昨收 trend,
    #   幅度带 pf[50]<amp<=pf[51] (pf[50]<0 无约束), 年龄带含端点 (pf[48]<0 无约束)。
    if pi[39] == 4 or pi[39] == 5:
        _sa = env.get("DG_OP_SK_AGE", "any")
        if _sa != "any":
            _lo, _hi = _sa.split(",")
            pf[48] = float(_lo); pf[49] = float(_hi)
        else:
            pf[48] = -1.0; pf[49] = 0.0
        _sm = env.get("DG_OP_SK_AMP", "any")
        if _sm != "any":
            _lo, _hi = _sm.split(",")
            pf[50] = float(_lo); pf[51] = float(_hi)
        else:
            pf[50] = -1.0; pf[51] = 0.0
        pi[49] = {"any": 0, "same": 1, "rev": 2}[env.get("DG_OP_SK_DIR", "any")]
        pi[48] = 1 if env.get("DG_OP_SK_LISTFILE", "") else 0
    # --- s5x 扩展 (2026-11-13 组合细扫轮): 距离门满量点 + 压带认错移植 ---
    # pf[52]=S5_DFULL 距离门满量点 (默认 2.5, 斜坡 = (带距/ATR-1)/(DFULL-1), 起始点 1.0 不变);
    # pi[50]=PB_ON pi[51]=PB_DIR; pf[53]=PB_P pf[54]=PB_T pf[55..56]=PB 年龄带
    # pf[57..58]=PB 幅度带。PB 命中判定与 st_kernel_numba 逐字同 (年龄=pd_age 昨收游程,
    # signed=(今开-昨收)x昨收 trend), 但用 PB 独立条件参数 (本轮 OP 走清单、PB 走条件)。
    pf[52] = _dgf(env, "DG_S5_DFULL", 2.5)
    _pb_on = env.get("DG_PB", "") == "1"
    pi[50] = 1 if _pb_on else 0
    pi[51] = {"any": 0, "same": 1, "rev": 2}[env.get("DG_PB_DIR", "any")]
    if _pb_on:
        pf[53] = _dgf(env, "DG_PB_P", 0.3)
        pf[54] = _dgf(env, "DG_PB_T", 60.0)
        _pa = env.get("DG_PB_AGE", "any")
        if _pa != "any":
            _lo, _hi = _pa.split(","); pf[55] = float(_lo); pf[56] = float(_hi)
        else:
            pf[55] = -1.0; pf[56] = 0.0
        _pm = env.get("DG_PB_AMP", "any")
        if _pm != "any":
            _lo, _hi = _pm.split(","); pf[57] = float(_lo); pf[58] = float(_hi)
        else:
            pf[57] = -1.0; pf[58] = 0.0
    # --- nc 扩展 (2026-11-13): 开盘跟正常信号 (隔夜跳先行消化); pi[52]=NC_ON, 默认关 ---
    pi[52] = 1 if env.get("DG_NC", "") == "1" else 0
    # --- es 扩展 (2026-11-13): 开盘 ATR 比值压带式; pi[54]=OPR_ON, pf[62]=C, pf[63]=WIN(分),
    #     pi[55]=NREF(参照天数, 默认5), pi[56]=FADE(0=不消退对照, 默认1), 默认关 ---
    pi[54] = 1 if env.get("DG_OPR", "") == "1" else 0
    pi[55] = max(1, min(10, int(_dgf(env, "DG_OPR_NREF", 5.0))))  # 基准环与开关无关, 须有默认值
    pi[56] = 0 if env.get("DG_OPR_FADE", "") == "0" else 1
    if pi[54] == 1:
        pf[62] = _dgf(env, "DG_OPR_C", 1.25)
        pf[63] = _dgf(env, "DG_OPR_WIN", 60.0)
    # --- es 扩展 (2026-11-13): 开盘 ATR 爆炸压带; pi[53]=OPX_ON, pf[59]=K, pf[60]=CAP, pf[61]=WIN(分), 默认关 ---
    pi[53] = 1 if env.get("DG_OPX", "") == "1" else 0
    if pi[53] == 1:
        pf[59] = _dgf(env, "DG_OPX_K", 1.0)
        pf[60] = _dgf(env, "DG_OPX_CAP", 0.4)
        pf[61] = _dgf(env, "DG_OPX_WIN", 60.0)
    # 诊断专用 (默认关, 不影响数值): pi[46]=NOPRESS_PUT_DAY 有标记; pi[47]=DEBUG 捕获逐根分解
    pi[46] = 1 if env.get("DG_S5_NOPRESS_PUT_DAY", "") else 0
    pi[47] = 1 if env.get("DG_S5_DEBUG", "") == "1" else 0
    # --- gf 扩展 (2026-11-14): 大跳空日上午空仓; pi[57]=GF_ON, pf[64]=GF_G(点), pf[65]=GF_TMIN(自0:00起分钟数, ET) ---
    pi[57] = 1 if env.get("DG_GF", "") == "1" else 0
    if pi[57] == 1:
        pf[64] = _dgf(env, "DG_GF_G", 10.0)
        pf[65] = _dgf(env, "DG_GF_TMIN", 600.0)
    # --- amgate 扩展 (2026-11-16): 上午收益门控; pi[58]=AMG_ON, pf[66]=AMG_PCT(阈值%),
    #     pi[59]=AMG_START(分钟), pi[60]=AMG_END(分钟), 默认关 ---
    pi[58] = 1 if env.get("DG_AMG", "") == "1" else 0
    pi[59] = 810; pi[60] = 900
    if pi[58] == 1:
        pf[66] = _dgf(env, "DG_AMG_PCT", 50.0)
        # 低侧扩展 (2026-11-17): pi[61]=AMG_MODE (env DG_AMG_MODE: hi/lo/dual, 默认 hi),
        # pf[67]=AMG_LO (双侧模式低阈值, env DG_AMG_LO, 默认 -30)
        _m = env.get("DG_AMG_MODE", "hi")
        pi[61] = 1 if _m == "lo" else (2 if _m == "dual" else 0)
        pf[67] = _dgf(env, "DG_AMG_LO", -30.0)
        _s = env.get("DG_AMG_START", "13:30"); _hh, _mm = _s.split(":")
        pi[59] = int(_hh) * 60 + int(_mm)
        _e = env.get("DG_AMG_END", "15:00"); _hh, _mm = _e.split(":")
        pi[60] = int(_hh) * 60 + int(_mm)
    return pf, pi


# ---------------- 内核 A: Supertrend 数组 ----------------

@njit
def _pymin(a, b):
    # Python builtins min 语义 (与 np.minimum 的 NaN 传播不同)
    return b if b < a else a


@njit
def _pymax(a, b):
    return b if b > a else a


@njit
def _gam(dirc, d):
    # settle_unified.gamma_pct 逐字移植 (dirc: 1=call / -1=put; 不含 floor, 逆向钳制 <=0)
    if dirc == 1:
        r = 9.88 * d + 0.311 * d * d
        if d < 0.0:
            r = _pymin(r, 0.0)
    else:
        r = -9.88 * d + 0.311 * d * d
        if d > 0.0:
            r = _pymin(r, 0.0)
    return r


@njit
def _pen(d):
    # settle_unified.penalty_pp 逐字移植: clamp(2.3*max(0, d-1.0), 0, 20)
    v = 2.3 * (d - 1.0) if d > 1.0 else 0.0
    return _pymin(v, 20.0)


@njit
def _amg_hit(val, thr, lo, mode):
    # amgate 触发判定 (2026-11-17 低侧扩展): mode 0=高侧 val>=thr (原定版, 逐字同);
    # mode 1=低侧 val<=thr (thr 为负值, env DG_AMG_PCT 传负数);
    # mode 2=双侧 val>=thr 或 val<=lo (lo=pf[67], env DG_AMG_LO)。
    if mode == 1:
        return val <= thr
    if mode == 2:
        return val >= thr or val <= lo
    return val >= thr


@njit
def _dwell(arr, ds, si, want_min, dwell_bars):
    # 慢版 _dg_dwell: 同值连续 >= dwell_bars 的最紧水平; 无则返回 (False, 0.0)
    best = 0.0
    has_best = False
    run_v = 0.0
    has_run = False
    run_n = 0
    for j in range(ds, si + 1):
        v = arr[j]
        if has_run and v == run_v:
            run_n += 1
        else:
            run_v = v
            has_run = True
            run_n = 1
        if run_n >= dwell_bars and not np.isnan(v):
            if not has_best or (v < best if want_min else v > best):
                best = v
                has_best = True
    return has_best, best


@njit
def st_arrays_kernel(h, l, c, o, hl2, tr, atr_init,
                     day_start, bar_in_day, is_1500, cur_day_start0,
                     pf, pi,
                     atr, mult, bu, bl, fu, fl, trend,
                     ee_flag_bar, skip_flag_bar,
                     dec_p, dec_m, slp5, dbg, s5_npput):
    n = len(c)
    if n < 10:
        return
    # 取参 (局部化, 与慢版全局名一一对应)
    c4disc = pf[0]; c4s_d0 = pf[1]; c4s_t = pf[2]; c4a_d0 = pf[3]; c4a_t = pf[4]
    age_full = pf[5]; age_cut = pf[6]; v4f = pf[7]; v4win = pf[8]; v3rho = pf[9]
    c1thr = pf[10]; c3thr = pf[11]; c1p_scale = pf[12]; c1phi_lo = pf[13]; c3p_scale = pf[14]
    v2gk = pf[15]; gaph_c = pf[16]; gaph_min = pf[17]; ygap_c = pf[18]; ygap_min = pf[19]
    qf_thr = pf[20]; qf_ratio = pf[21]; ygrev_pts = pf[22]; yd0big = pf[23]; yd0_val = pf[24]
    yd0rev_val = pf[25]; ee_pts = pf[26]; skip_pts = pf[27]
    old_pts = pf[28]; old0 = pf[29]; old_t = pf[30]; old_age = pf[31]
    v1_alpha = pf[34]
    v1_on = pi[0] == 1; v1_bars = pi[1]; v2_on = pi[2] == 1; v4_on = pi[3] == 1; v3_on = pi[4] == 1
    c1_on = pi[5] == 1; c3_on = pi[6] == 1; c4_on = pi[7] == 1; c4wbars = pi[8]
    c4s_on = pi[9] == 1; c4exp = pi[10] == 1; c4cond = pi[11] == 1; c4age_on = pi[12] == 1
    ageg_mode = pi[13]; c1p_on = pi[14] == 1; c3p_on = pi[15] == 1; v2g_on = pi[16] == 1
    gaph_on = pi[17] == 1; ygap_on = pi[18] == 1; qf_on = pi[19] == 1; qf_half = pi[20] == 1
    ygrev_on = pi[21] == 1; yd0_on = pi[22] == 1; yd0rev_on = pi[23] == 1
    old_on = pi[27] == 1; old_mode = pi[28]; c2_on = pi[29] == 1; dwell_bars = pi[30]; v5_on = pi[31] == 1
    pf_on = pi[33] == 1; pf_clk = pi[34]; pf_d0 = pf[35]; pf_t = pf[36]
    qd_on = pi[35] == 1; qd_mode = pi[36]; qd_gate = pi[37]; qd_age05 = pi[38] == 1
    qd_start = pf[37]; qd_floor = pf[38]; qd_gfloor = pf[39]
    s5_on = pi[43] == 1; s5_gdist = pi[44] == 1; s5_gage = pi[45] == 1
    s5_k = pf[40]; s5_cap = pf[41]; s5_age_th = pf[42]; s5_start = pf[43]
    s5_dfull = pf[52]
    pb_on = pi[50] == 1; pb_dir = pi[51]
    nc_on = pi[52] == 1
    opx_on = pi[53] == 1
    opx_k = pf[59]; opx_cap = pf[60]; opx_win = pf[61]
    opr_on = pi[54] == 1
    opr_c = pf[62]; opr_win = pf[63]
    opr_nref = pi[55]; opr_fade = pi[56] == 1
    pb_p = pf[53]; pb_t = pf[54]
    pb_age_lo = pf[55]; pb_age_hi = pf[56]; pb_amp_lo = pf[57]; pb_amp_hi = pf[58]
    s5_npput_on = pi[46] == 1; s5_dbg = pi[47] == 1
    pf_flip_el = -1.0
    qd_fu = np.full(n, np.nan); qd_fl = np.full(n, np.nan)
    s5_fu = np.full(n, np.nan); s5_fl = np.full(n, np.nan)

    atr[9] = atr_init
    if v4_on:
        mult[9] = 3.0 * (v4f + (1.0 - v4f) * _pymin(_pymax(bar_in_day[9] / v4win, 0.0), 1.0))
    bu[9] = hl2[9] + mult[9] * atr[9]
    bl[9] = hl2[9] - mult[9] * atr[9]
    fu[9] = bu[9]; fl[9] = bl[9]; trend[9] = 1
    qd_fu[9] = bu[9]; qd_fl[9] = bl[9]
    s5_fu[9] = bu[9]; s5_fl[9] = bl[9]
    pullin_until = -1; pullin_fired = False
    c4_e = -1; c4_dir = 0; c4_to = -1; c4_armed = False
    pd_c1_fire = False; pd_c3_fire = False
    pd_cond_fire = False; c4s_today = True
    pd_age = 10 ** 9; pd_tight_atr = 999.0; trend_run = 1; c4age_today = False
    pb_today = False
    atr_ref = np.nan
    ref5_hist = np.zeros(10); ref5_n = 0; atr_ref5 = np.nan; push5 = False
    c4a_d0_today = 1.0; c4a_T_today = 90.0
    pd_c1p_phi = 0.0; pd_trend = 0; pd_phi_raw = 0.0
    pd_fu15 = np.nan; pd_fl15 = np.nan; pd_atr15 = np.nan
    pd_anc_fu = np.nan; pd_anc_fl = np.nan; has_anc = False
    cur_day_start = cur_day_start0
    for si in range(10, n):
        if day_start[si]:
            cur_day_start = si
            atr_ref = atr[si - 1]
            if ref5_n > 0:
                _take = opr_nref if opr_nref < ref5_n else ref5_n
                _s5 = 0.0
                for _j in range(ref5_n - _take, ref5_n):
                    _s5 += ref5_hist[_j]
                atr_ref5 = _s5 / _take
            else:
                atr_ref5 = np.nan
            push5 = True
            pf_flip_el = -1.0
            if pb_on:
                signed_pb = (o[si] - c[si - 1]) * trend[si - 1]
                amp_pb = signed_pb if signed_pb >= 0.0 else -signed_pb
                pb_today = True
                if pb_age_lo >= 0.0 and not (pb_age_lo <= pd_age <= pb_age_hi):
                    pb_today = False
                if pb_dir == 1 and signed_pb < 0.0:
                    pb_today = False
                if pb_dir == 2 and signed_pb >= 0.0:
                    pb_today = False
                if pb_amp_lo >= 0.0 and not (pb_amp_lo < amp_pb <= pb_amp_hi):
                    pb_today = False
            if c4s_on:
                c4s_today = pd_cond_fire if c4cond else True
            if c4age_on:
                if ageg_mode == 1:
                    g_age = _pymin(_pymax(1.0 - pd_age / 20.0, 0.0), 1.0)
                elif ageg_mode == 2:
                    g_age = np.exp(-pd_age / 10.0)
                else:
                    if pd_age <= age_full:
                        g_age = 1.0
                    elif pd_age >= age_cut:
                        g_age = 0.0
                    else:
                        g_age = (age_cut - pd_age) / (age_cut - age_full)
                c4a_d0_today = c4a_d0 * g_age
                c4age_today = g_age > 0.0
                if yd0_on and pd_age <= age_full:
                    c4a_d0_today = yd0_val
                if gaph_on and c4age_today and gaph_c > 0.0:
                    pa = atr[si - 1]
                    gp8 = abs(o[si] - c[si - 1])
                    if not np.isnan(pa) and pa > 0:
                        h8 = _pymin(_pymax(gp8 / (gaph_c * pa), gaph_min), 1.0)
                        c4a_d0_today = 1.0 - (1.0 - c4a_d0_today) * h8
                if ygap_on and c4age_today and pd_age <= age_full and ygap_c > 0.0:
                    pa9 = atr[si - 1]
                    gp9 = abs(o[si] - c[si - 1])
                    if not np.isnan(pa9) and pa9 > 0:
                        h9 = _pymin(_pymax(gp9 / (ygap_c * pa9), ygap_min), 1.0)
                        c4a_d0_today = 1.0 - (1.0 - c4a_d0_today) * h9
                if qf_on and c4age_today and age_full < pd_age < age_cut and pd_tight_atr < qf_thr:
                    if qf_half:
                        c4a_d0_today = 1.0 - (1.0 - c4a_d0_today) * qf_ratio
                    else:
                        c4a_d0_today = 1.0
                if ygrev_on and c4age_today and pd_age <= age_full:
                    gp_sgn = o[si] - c[si - 1]
                    gp_r = abs(gp_sgn)
                    if gp_r >= ygrev_pts:
                        is_rev = (gp_sgn > 0) != (trend[si - 1] > 0)
                        c4a_d0_today = (yd0rev_val if (is_rev and yd0rev_on) else yd0big)
                c4a_T_today = c4a_t
                if old_on and pd_age >= old_age:
                    gp_o = o[si] - c[si - 1]
                    if abs(gp_o) >= old_pts:
                        rev_o = (gp_o > 0) != (trend[si - 1] > 0)
                        if old_mode == 0 or (old_mode == 2 and rev_o) or (old_mode == 1 and not rev_o):
                            c4age_today = True
                            c4a_d0_today = old0
                            c4a_T_today = old_t
                if (pi[24] == 1 or pi[26] == 1) and pd_age <= age_full:
                    gp_f = o[si] - c[si - 1]
                    rev_f = (gp_f > 0) != (trend[si - 1] > 0)
                    if rev_f and abs(gp_f) >= ee_pts:
                        ee_flag_bar[si] = True
                    if rev_f and abs(gp_f) >= skip_pts:
                        skip_flag_bar[si] = True
            if c4_on:
                gp = abs(o[si] - c[si - 1]) if si > 0 else 0.0
                c4_e = si + (2 if gp > 2.0 else 0)
                c4_dir = 0; c4_armed = False; c4_to = c4_e + c4wbars
        atr_base = atr[si - 1]
        if c3_on and day_start[si] and pd_c3_fire and not np.isnan(pd_atr15):
            atr_base = pd_atr15
        if c3p_on and day_start[si] and not np.isnan(pd_atr15) and pd_phi_raw > 0.0:
            phi3 = pd_phi_raw * c3p_scale
            atr_base = (1.0 - phi3) * atr_base + phi3 * pd_atr15
        tr_use = tr[si]
        if v2g_on and day_start[si] and not np.isnan(atr[si - 1]) and atr[si - 1] > 0:
            hl = h[si] - l[si]
            exc = tr[si] - hl
            if exc > 0.0:
                tr_use = hl + _pymin(exc, v2gk * atr[si - 1])
        if v1_on and bar_in_day[si] < v1_bars:
            a = v1_alpha
            atr[si] = atr_base * (1.0 - a) + tr_use * a
        else:
            atr[si] = (atr_base * 9 + tr_use) / 10
        if np.isnan(atr[si]):
            trend[si] = trend[si - 1]; trend_run += 1; continue
        if push5:
            # 本日首根 ATR 入同时刻基准环 (留最近 10 日; 本日 REFN 已在日首算定)
            if ref5_n < 10:
                ref5_hist[ref5_n] = atr[si]; ref5_n += 1
            else:
                for _j in range(9):
                    ref5_hist[_j] = ref5_hist[_j + 1]
                ref5_hist[9] = atr[si]
            push5 = False
        if v4_on:
            ff = _pymin(_pymax(bar_in_day[si] / v4win, 0.0), 1.0)
            mult[si] = 3.0 * (v4f + (1.0 - v4f) * ff)
        bu[si] = hl2[si] + mult[si] * atr[si]
        bl[si] = hl2[si] - mult[si] * atr[si]
        pfu = fu[si - 1]; pfl = fl[si - 1]
        if day_start[si]:
            if c2_on and has_anc:
                pfu = pd_anc_fu; pfl = pd_anc_fl
            if c1_on and pd_c1_fire and not np.isnan(pd_fu15):
                pfu = pd_fu15; pfl = pd_fl15
            if c1p_on and pd_c1p_phi > 0.0:
                if pd_trend == -1 and not np.isnan(pd_fu15):
                    pfu = pfu - pd_c1p_phi * (pfu - pd_fu15)
                elif pd_trend == 1 and not np.isnan(pd_fl15):
                    pfl = pfl - pd_c1p_phi * (pfl - pd_fl15)
            if v3_on:
                k = 1.0 - v3rho
                pfu = pfu + k * (bu[si] - pfu)
                pfl = pfl + k * (bl[si] - pfl)
        bu_e = bu[si]; bl_e = bl[si]
        f_slide = 1.0
        if c4_on and c4_armed and si <= c4_to:
            if trend[si - 1] != c4_dir:
                c4_armed = False
            elif c4_dir == 1:
                bl_e = hl2[si] - mult[si] * c4disc * atr[si]
            elif c4_dir == -1:
                bu_e = hl2[si] + mult[si] * c4disc * atr[si]
        if c4s_on and c4s_today:
            el = bar_in_day[si] * 3.0
            if c4exp:
                f4 = 1.0 - (1.0 - c4s_d0) * (0.5 ** (el / 20.0))
            elif c4s_t > 0:
                f4 = c4s_d0 + (1.0 - c4s_d0) * _pymin(el / c4s_t, 1.0)
            else:
                f4 = 1.0
            if f4 < 1.0:
                f_slide = f4
                if trend[si - 1] == 1:
                    bl_e = hl2[si] - mult[si] * f4 * atr[si]
                elif trend[si - 1] == -1:
                    bu_e = hl2[si] + mult[si] * f4 * atr[si]
        if c4age_on and c4age_today:
            el = bar_in_day[si] * 3.0
            f4a = c4a_d0_today + (1.0 - c4a_d0_today) * _pymin(el / c4a_T_today, 1.0) if c4a_T_today > 0 else 1.0
            if pf_on and pf_flip_el >= 0.0 and pf_d0 < 1.0:
                if pf_clk == 0:
                    span = pf_t - pf_flip_el
                    fpf = (pf_d0 + (1.0 - pf_d0) * _pymin((el - pf_flip_el) / span, 1.0)) if span > 0 else 1.0
                else:
                    fpf = (pf_d0 + (1.0 - pf_d0) * _pymin((el - pf_flip_el) / pf_t, 1.0)) if pf_t > 0 else 1.0
                if fpf < f4a:
                    f4a = fpf
            if f4a < 1.0:
                f_slide = f4a
                if trend[si - 1] == 1:
                    bl_e = hl2[si] - mult[si] * f4a * atr[si]
                elif trend[si - 1] == -1:
                    bu_e = hl2[si] + mult[si] * f4a * atr[si]
        f_eff = f_slide
        if pb_on and pb_today:
            # 压带认错 (移植 st_kernel_numba): fpb(el) = 1 - P*(1 - el/T) 线性松回,
            # 在当日滑梯因子之上因子相乘叠加 (f_use = f_slide * fpb), 不用 min 取深者。
            el = bar_in_day[si] * 3.0
            fpb = 1.0 - pb_p * (1.0 - _pymin(el / pb_t, 1.0)) if pb_t > 0 else 1.0
            f_use = f_slide * fpb
            if f_use < f_slide:
                f_eff = f_use
                if trend[si - 1] == 1:
                    bl_e = hl2[si] - mult[si] * f_use * atr[si]
                elif trend[si - 1] == -1:
                    bu_e = hl2[si] + mult[si] * f_use * atr[si]
        if opx_on and bar_in_day[si] * 3.0 < opx_win and atr_ref > 0.0 and not np.isnan(atr[si]):
            # 开盘 ATR 爆炸压带 (es 新增): drop = clamp(K*(ATR当根/ATR昨收 - 1), 0, CAP);
            # 因子 (1-drop) 与滑梯/PB 的有效因子 f_eff 相乘叠加, 只压趋势侧带。
            opx_r = atr[si] / atr_ref - 1.0
            opx_drop = _pymin(_pymax(opx_k * opx_r, 0.0), opx_cap)
            if opx_drop > 0.0:
                f_opx = f_eff * (1.0 - opx_drop)
                if f_opx < f_eff:
                    f_eff = f_opx
                    if trend[si - 1] == 1:
                        bl_e = hl2[si] - mult[si] * f_opx * atr[si]
                    elif trend[si - 1] == -1:
                        bu_e = hl2[si] + mult[si] * f_opx * atr[si]
        if opr_on and bar_in_day[si] * 3.0 < opr_win and atr_ref5 > 0.0 and not np.isnan(atr[si]):
            # 比值压带式 (es 定版): drop = max(0, 1 - C/ratio), ratio = ATR当根/REFN;
            # 消退时 drop 再乘 (1 - 已开盘分钟/窗口分钟); 因子与 f_eff 相乘, 只压趋势侧带。
            opr_ratio = atr[si] / atr_ref5
            if opr_ratio > opr_c:
                opr_drop = 1.0 - opr_c / opr_ratio
                if opr_fade:
                    opr_drop = opr_drop * (1.0 - bar_in_day[si] * 3.0 / opr_win)
                if opr_drop > 0.0:
                    f_opr = f_eff * (1.0 - opr_drop)
                    if f_opr < f_eff:
                        if trend[si - 1] == 1:
                            bl_e = hl2[si] - mult[si] * f_opr * atr[si]
                        elif trend[si - 1] == -1:
                            bu_e = hl2[si] + mult[si] * f_opr * atr[si]
        fu[si] = bu_e if (bu_e < pfu or c[si - 1] > pfu) else pfu
        fl[si] = bl_e if (bl_e > pfl or c[si - 1] < pfl) else pfl
        if qd_on:
            qd_fu[si] = bu_e if (bu_e < qd_fu[si - 1] or c[si - 1] > qd_fu[si - 1]) else qd_fu[si - 1]
            qd_fl[si] = bl_e if (bl_e > qd_fl[si - 1] or c[si - 1] < qd_fl[si - 1]) else qd_fl[si - 1]
            qs = trend[si - 1]
            qm = 1.0
            if qd_mode == 2:
                if not np.isnan(slp5[si]) and abs(slp5[si]) > 0.5:
                    qm = 0.6
            elif qs == 1 or qs == -1:
                qdec = dec_p[si] if qs == 1 else dec_m[si]
                if not np.isnan(qdec) and qdec >= qd_start:
                    qfrac = _pymin((qdec - qd_start) / (1.0 - qd_start), 1.0) if qd_start < 1.0 else 1.0
                    qm = 1.0 - (1.0 - qd_floor) * qfrac
                    if qd_age05 and pd_age >= 17:
                        qm = 1.0 - 0.5 * (1.0 - qm)
                    if qd_gate == 1 and not np.isnan(atr[si]) and atr[si] > 0:
                        qdist = (c[si] - qd_fl[si]) / atr[si] if qs > 0 else (qd_fu[si] - c[si]) / atr[si]
                        qg = _pymin(_pymax((qdist - 1.0) / 1.5, 0.0), 1.0)
                        qm = 1.0 - (1.0 - qm) * qg
            if qm < 1.0 and qs == 1:
                qt = hl2[si] - qm * (hl2[si] - fl[si])
                if qt > fl[si]:
                    fl[si] = qt
                if qd_gate == 2 and not np.isnan(atr[si]):
                    qcap = c[si] - qd_gfloor * atr[si]
                    if fl[si] > qcap:
                        fl[si] = qcap
            elif qm < 1.0 and qs == -1:
                qt = hl2[si] + qm * (fu[si] - hl2[si])
                if qt < fu[si]:
                    fu[si] = qt
                if qd_gate == 2 and not np.isnan(atr[si]):
                    qcap = c[si] + qd_gfloor * atr[si]
                    if fu[si] < qcap:
                        fu[si] = qcap
        if s5_on:
            s5_fu[si] = bu_e if (bu_e < s5_fu[si - 1] or c[si - 1] > s5_fu[si - 1]) else s5_fu[si - 1]
            s5_fl[si] = bl_e if (bl_e > s5_fl[si - 1] or c[si - 1] < s5_fl[si - 1]) else s5_fl[si - 1]
            qs5 = trend[si - 1]
            if s5_dbg:
                dbg[si, 5] = fu[si]; dbg[si, 6] = fl[si]; dbg[si, 7] = trend_run
            if (qs5 == 1 or qs5 == -1) and not np.isnan(slp5[si]):
                sv = slp5[si] if qs5 == 1 else -slp5[si]
                if s5_dbg:
                    dbg[si, 0] = sv; dbg[si, 2] = 1.0
                if sv >= s5_start:
                    sdrop = s5_k * sv
                    if sdrop > s5_cap:
                        sdrop = s5_cap
                    if s5_dbg:
                        dbg[si, 1] = sdrop
                    if s5_gdist and not np.isnan(atr[si]) and atr[si] > 0:
                        sdist = (c[si] - s5_fl[si]) / atr[si] if qs5 > 0 else (s5_fu[si] - c[si]) / atr[si]
                        sg = _pymin(_pymax((sdist - 1.0) / (s5_dfull - 1.0), 0.0), 1.0)
                        if s5_dbg:
                            dbg[si, 2] = sg
                        sdrop = sdrop * sg
                    if s5_gage and trend_run >= s5_age_th:
                        sdrop = 0.0
                        if s5_dbg:
                            dbg[si, 4] = 1.0
                    if s5_npput_on and s5_npput[si] > 0.5 and qs5 == -1:
                        sdrop = 0.0
                    if s5_dbg:
                        dbg[si, 3] = sdrop
                    sm = 1.0 - sdrop
                    if sm < 1.0 and qs5 == 1:
                        stx = hl2[si] - sm * (hl2[si] - fl[si])
                        if stx > fl[si]:
                            fl[si] = stx
                    elif sm < 1.0 and qs5 == -1:
                        stx = hl2[si] + sm * (fu[si] - hl2[si])
                        if stx < fu[si]:
                            fu[si] = stx
        # --- nc 扩展: 日首虚拟 bar — 先用今开价 o[si] 对带入本日的带子 (pfu/pfl, 即
        # 隔夜全程 "在场" 的带子) 做一次翻转判定 (模拟连续 tape 下 9:30 的信号状态);
        # 翻了则本根起基准方向换新方向, 本根收盘的翻转判定带改用本根棘轮带 (fu/fl,
        # 等价于真实翻转 bar 的带在一根之后接管); 没翻则与原路径逐字同。
        vflip = False
        base_tr = trend[si - 1]
        cmp_fu = pfu; cmp_fl = pfl
        if nc_on and day_start[si]:
            vtr = trend[si - 1]
            if o[si] > pfu:
                vtr = 1
            elif o[si] < pfl:
                vtr = -1
            if vtr != trend[si - 1]:
                vflip = True
                base_tr = vtr
                cmp_fu = fu[si]; cmp_fl = fl[si]
        new_tr = base_tr
        if c[si] > cmp_fu:
            new_tr = 1
        elif c[si] < cmp_fl:
            new_tr = -1
        pullin_fired = False
        if v5_on and new_tr == trend[si - 1] and si <= pullin_until:
            if trend[si - 1] == 1:
                trig = _pymax(pfl, hl2[si] - 1.8 * atr[si])
                if c[si] < trig:
                    new_tr = -1; pullin_fired = True
            else:
                trig = _pymin(pfu, hl2[si] + 1.8 * atr[si])
                if c[si] > trig:
                    new_tr = 1; pullin_fired = True
        if v5_on and new_tr != trend[si - 1] and not pullin_fired and bar_in_day[si] < 10:
            pullin_until = si + 10
        trend[si] = new_tr
        if vflip:
            trend_run = 1
        elif new_tr == trend[si - 1]:
            trend_run = trend_run + 1
        else:
            trend_run = 1
        if pf_on and new_tr != trend[si - 1]:
            pf_flip_el = bar_in_day[si] * 3.0
        if c4_on and si == c4_e:
            c4_dir = new_tr; c4_armed = True
        if si == n - 1 or day_start[si + 1]:
            ds = cur_day_start
            ref = -1
            for j in range(si, ds - 1, -1):
                if is_1500[j]:
                    ref = j; break
            if ref < 0:
                ref = ds if ds > si - 19 else si - 19
            atr_e = atr[si]
            if trend[si] == -1:
                tight = fu[ref] - fu[si]
            elif trend[si] == 1:
                tight = fl[si] - fl[ref]
            else:
                tight = 0.0
            pd_c1_fire = (c1_on and not np.isnan(atr_e) and atr_e > 0 and tight > c1thr * atr_e)
            pd_c3_fire = (c3_on and not np.isnan(atr_e) and atr_e > 0 and tight > c3thr * atr_e)
            pd_cond_fire = (c4cond and not np.isnan(atr_e) and atr_e > 0 and tight > 0.7 * atr_e)
            pd_age = trend_run
            pd_tight_atr = (tight / atr_e) if (not np.isnan(atr_e) and atr_e > 0) else 999.0
            if c1p_on or c3p_on:
                pd_phi_raw = 0.0
                if not np.isnan(atr_e) and atr_e > 0 and tight > 0.0:
                    pd_phi_raw = _pymin(_pymax((tight / atr_e - c1phi_lo) / (3.0 - c1phi_lo), 0.0), 1.0)
                if c1p_on:
                    pd_trend = trend[si]
                    pd_c1p_phi = pd_phi_raw * c1p_scale
            pd_fu15 = fu[ref]; pd_fl15 = fl[ref]; pd_atr15 = atr[ref]
            if c2_on:
                has_fu, afu = _dwell(fu, ds, si, True, dwell_bars)
                has_fl, afl = _dwell(fl, ds, si, False, dwell_bars)
                pd_anc_fu = afu if has_fu else fu[ref]
                pd_anc_fl = afl if has_fl else fl[ref]
                has_anc = True


# ---------------- 内核 B: ST-only 交易循环 ----------------
# 动作编码: act 0=开 1=平 2=持; dir 1=call -1=put
# reason 码: 0 日终平仓(1m15:55) / 1 日终平仓(最后一根) / 2 止损窗口回RECOV平 /
#   3 止损窗口(续) / 4 止损窗口6根平 / 5 止损窗口6根平(补) / 6 止损窗口开始(p1=gamma,p2=thr) /
#   7 VP提前认错(p1=adv,p2=atr) / 8 ST翻转平 / 9 ST翻转开 / 10 ST开盘平 / 11 ST开盘开 /
#   12 ST兜底开 (S 政策: 前3根未翻, 第3根按当时 ST 状态开) / 13 窗口恢复开 (AMGATE)

@njit
def st_trade_kernel(c, o, day_ord, hm_min, eod_px, skip_bar,
                    trend, atr, bar_in_day, ee_day, skip_day, opskip_day,
                    start_idx, end_idx, pf, pi,
                    a_bar, a_act, a_dir, a_px, a_rsn, a_p1, a_p2,
                    am_j13, am_ret, am_trig, diag):
    recov = pf[32]; ee_atr = pf[33]
    ee_on = pi[24] == 1; ee_bars = pi[25]; skip_on = pi[26] == 1
    sw_bars = pi[32]
    op_pol = pi[39]; op_k = pi[40]; op_ign_off = pi[41] == 1; op_kraw = pi[42] == 1
    sk_age_lo = pf[48]; sk_age_hi = pf[49]; sk_amp_lo = pf[50]; sk_amp_hi = pf[51]
    sk_dir = pi[49]; sk_list = pi[48] == 1
    gf_on = pi[57] == 1; gf_g = pf[64]; gf_tmin = int(pf[65])
    gf_today = False; gf_open_idx = -1
    # --- amgate 状态 (pi[58]/pf[66]/pi[59]/pi[60]); 关时全部惰性, 路径与 gf 逐字同 ---
    am_on = pi[58] == 1; am_thr = pf[66]; am_start = pi[59]; am_end = pi[60]
    am_mode = pi[61]; am_lo = pf[67]
    am_factor = 1.0; am_sealed = True; am_triggered = False; am_resume_done = False
    am_resume_now = False
    am_trk = False; am_leg_px = 0.0; am_open_px = 0.0; am_ob = -1; am_pdir = 0
    am_day_ord = -1; na_mark = 0; prev_bar = -1
    op_pending = False; op_entry = -1
    s_wait = False; s_cnt = 0
    op_skip_today = False
    na = 0
    pos = 0  # 0 无仓 / 1 call / -1 put
    stop_dir = 0; stop_px = 0.0
    sw_on = False; sw_trig = -1; sw_thr = 0.0; sw_dr = 0
    ee_active = False; ee_day_ord = -1; ee_px = 0.0; ee_atr_v = 0.0; ee_bid = 0
    prev_ord = -1; day_start_idx = start_idx
    ign_n = 1; ign_day_idx = -1; ign_wk = 1
    n = len(c)
    for i in range(start_idx, end_idx):
        # --- amgate 跟踪维护: 处理上一根 bar (prev_bar) 已记录的动作 (驱动源 = 内核自己的
        # fills): 平仓 -> 末腿落账 (窗口内整笔平仓: 末腿 ROI 经 floor 后, put 再扣整单 v2 罚);
        # 开仓 -> 起跟踪 (leg_px = 成交价); 跟踪单在上一根未平且开仓根早于上一根 ->
        # 按上一根收盘查刷单腿 (gamma>=+10% 切腿连乘)。窗口外/封存后全部跳过。 ---
        if am_on and prev_bar >= 0:
            closed_prev = False
            for k in range(na_mark, na):
                if a_act[k] == 1:
                    if am_trk and not am_sealed:
                        bk = a_bar[k]
                        if am_j13[am_day_ord] < 0 or bk < am_j13[am_day_ord]:
                            d = a_px[k] - am_leg_px
                            r = _gam(am_pdir, d)
                            if r < -100.0:
                                r = -100.0
                            if am_pdir == -1:
                                r -= _pen(a_px[k] - am_open_px)
                            am_factor *= (1.0 + r / 100.0)
                    am_trk = False
                    closed_prev = True
                elif a_act[k] == 0:
                    am_trk = True; am_open_px = a_px[k]; am_leg_px = a_px[k]
                    am_ob = a_bar[k]; am_pdir = a_dir[k]
            na_mark = na
            if am_trk and not am_sealed and not closed_prev and am_ob < prev_bar:
                if am_j13[am_day_ord] < 0 or prev_bar < am_j13[am_day_ord]:
                    g = _gam(am_pdir, c[prev_bar] - am_leg_px)
                    if g >= 10.0:
                        am_factor *= (1.0 + g / 100.0)
                        am_leg_px = c[prev_bar]
        prev_bar = i
        if skip_bar[i]:
            continue
        oi = day_ord[i]
        if oi != prev_ord:
            day_start_idx = i; prev_ord = oi
            sw_on = False
            ee_active = False
            if am_on:
                if am_day_ord >= 0 and not am_sealed:
                    # 前一日无 13:00 bar (早收盘日): 日终封存 (因子已含全日腿; 日终恒空仓)
                    am_ret[am_day_ord] = (am_factor - 1.0) * 100.0
                    am_trig[am_day_ord] = 1 if _amg_hit((am_factor - 1.0) * 100.0, am_thr, am_lo, am_mode) else 0
                am_factor = 1.0; am_sealed = False; am_triggered = False
                am_resume_done = False; am_trk = False; am_day_ord = oi
            if i == day_start_idx and i > 0:
                gap = abs(o[i] - c[i - 1])
                ign_n = 2 if gap > 2.0 else 0
                ign_day_idx = day_start_idx
            elif ign_day_idx != day_start_idx:
                ign_n = 1
            if op_ign_off:
                ign_n = 0
            ign_wk = ign_n
            if op_pol == 1:
                if op_kraw:
                    op_entry = day_start_idx + (ign_wk if ign_wk > op_k else op_k)
                else:
                    op_entry = day_start_idx + ign_wk + op_k
                op_pending = True
            if op_pol == 3:
                s_wait = True; s_cnt = 0
            if op_pol == 4 or op_pol == 5:
                # 命中判定 (日首一次): 与 st_kernel_numba 逐字同
                op_skip_today = False
                op_pending = False
                hit = False
                if sk_list:
                    hit = opskip_day[i]
                elif i > 0:
                    td = trend[i - 1]
                    age = 1; j = i - 1
                    while j - 1 >= 0 and trend[j - 1] == td:
                        age += 1; j -= 1
                    signed = (o[i] - c[i - 1]) * td
                    amp = signed if signed >= 0.0 else -signed
                    hit = True
                    if sk_age_lo >= 0.0 and not (sk_age_lo <= age <= sk_age_hi):
                        hit = False
                    if sk_dir == 1 and signed < 0.0:
                        hit = False
                    if sk_dir == 2 and signed >= 0.0:
                        hit = False
                    if sk_amp_lo >= 0.0 and not (sk_amp_lo < amp <= sk_amp_hi):
                        hit = False
                if op_pol == 4:
                    op_skip_today = hit
                elif hit:
                    op_entry = day_start_idx + ign_wk + op_k
                    op_pending = True
            gf_today = False; gf_open_idx = -1
            if gf_on and i > 0:
                gfv = o[i] - c[i - 1]
                if gfv < 0.0:
                    gfv = -gfv
                if gfv >= gf_g:
                    gf_today = True
                    j = i
                    while j < n and day_ord[j] == oi and hm_min[j] < gf_tmin:
                        j += 1
                    if j < n and day_ord[j] == oi:
                        gf_open_idx = j
        if i - day_start_idx < ign_wk:
            continue
        if op_pol == 3 and s_wait:
            s_cnt += 1
        px = c[i]
        next_same = (i + 1 < n) and (day_ord[i + 1] == oi)
        px_exec = o[i + 1] if next_same else px
        # --- amgate 封存: 13:00 bar 本根, 先于本根一切交易逻辑。因果: 只用已维护的
        # 上午因子 + 本根开盘价标记未平腿 (执行开仓根 >= 本根的单不计, 与 walk 口径同)。 ---
        if am_on and not am_sealed and am_j13[oi] >= 0 and i == am_j13[oi]:
            if pos != 0 and am_trk and (am_ob + 1) < i:
                r = _gam(am_pdir, o[i] - am_leg_px)
                if r < -100.0:
                    r = -100.0
                am_factor *= (1.0 + r / 100.0)
            am_sealed = True
            am_triggered = _amg_hit((am_factor - 1.0) * 100.0, am_thr, am_lo, am_mode)
            am_ret[oi] = (am_factor - 1.0) * 100.0
            am_trig[oi] = 1 if am_triggered else 0
        # 恢复判定只在窗口结束首根 bar 做一次 (在任何 continue 路径之前定格):
        # 该根不空仓 (含止损窗口/日终平路径) 即不再恢复。
        am_resume_now = False
        if am_on and am_triggered and not am_resume_done and hm_min[i] >= am_end:
            am_resume_done = True
            am_resume_now = True
        # 日终平仓 (15:55, 1m 价)
        if pos != 0 and hm_min[i] >= 955:
            ep = eod_px[i]
            if np.isnan(ep):
                ep = px
            a_bar[na] = i; a_act[na] = 1; a_dir[na] = pos; a_px[na] = ep; a_rsn[na] = 0; a_p1[na] = 0.0; a_p2[na] = 0.0; na += 1
            pos = 0; sw_on = False
            continue
        # 当日最后一根强制平
        if pos != 0 and not next_same:
            a_bar[na] = i; a_act[na] = 1; a_dir[na] = pos; a_px[na] = px_exec; a_rsn[na] = 1; a_p1[na] = 0.0; a_p2[na] = 0.0; na += 1
            pos = 0; sw_on = False
            continue
        # 止损窗口
        if sw_on and pos == 0:
            sw_on = False
        if sw_on and pos != 0:
            age = i - sw_trig
            if age >= 1:
                if (sw_dr == 1 and px >= sw_thr) or (sw_dr == -1 and px <= sw_thr):
                    a_bar[na] = i; a_act[na] = 1; a_dir[na] = pos; a_px[na] = px_exec; a_rsn[na] = 2; a_p1[na] = 0.0; a_p2[na] = 0.0; na += 1
                    pos = 0; stop_dir = 0; sw_on = False
                    continue
                if age <= sw_bars:
                    a_bar[na] = i; a_act[na] = 2; a_dir[na] = pos; a_px[na] = px_exec; a_rsn[na] = 3; a_p1[na] = 0.0; a_p2[na] = 0.0; na += 1
                    if age == sw_bars:
                        a_bar[na] = i; a_act[na] = 1; a_dir[na] = pos; a_px[na] = px_exec; a_rsn[na] = 4; a_p1[na] = 0.0; a_p2[na] = 0.0; na += 1
                        pos = 0; stop_dir = 0; sw_on = False
                    continue
                if age >= sw_bars + 1:
                    a_bar[na] = i; a_act[na] = 1; a_dir[na] = pos; a_px[na] = px_exec; a_rsn[na] = 5; a_p1[na] = 0.0; a_p2[na] = 0.0; na += 1
                    pos = 0; stop_dir = 0; sw_on = False
                    continue
        # 20% Gamma 止损检查
        if pos != 0:
            if stop_dir != pos:
                stop_dir = pos; stop_px = px
            else:
                d = px - stop_px
                if pos == 1:
                    g = 9.88 * d + 0.311 * d * d
                    if d < 0:
                        g = _pymin(g, 0.0)
                else:
                    g = -9.88 * d + 0.311 * d * d
                    if d > 0:
                        g = _pymin(g, 0.0)
                if not sw_on and g <= -20.0 and trend[i] != trend[i - 1]:
                    thr = px + recov * (stop_px - px)
                    sw_on = True; sw_trig = i; sw_thr = thr; sw_dr = pos
                    a_bar[na] = i; a_act[na] = 2; a_dir[na] = pos; a_px[na] = px_exec; a_rsn[na] = 6; a_p1[na] = g; a_p2[na] = thr; na += 1
                    continue
        else:
            stop_dir = 0
        # EE 提前认错
        if ee_on and ee_active and pos != 0 and not sw_on:
            nb = bar_in_day[i] - ee_bid
            if oi == ee_day_ord and 1 <= nb <= ee_bars:
                adv = (ee_px - px) if pos == 1 else (px - ee_px)
                if ee_atr_v > 0 and adv >= ee_atr * ee_atr_v:
                    a_bar[na] = i; a_act[na] = 1; a_dir[na] = pos; a_px[na] = px_exec; a_rsn[na] = 7; a_p1[na] = adv; a_p2[na] = ee_atr_v; na += 1
                    pos = 0; stop_dir = 0; ee_active = False
                    continue
        # ST 翻转 / 开盘开仓
        is_flip = trend[i] != trend[i - 1]
        if op_pol == 2 or op_pol == 3 or (op_pol == 4 and op_skip_today):
            is_dayopen = False
        elif op_pol == 1 or (op_pol == 5 and op_pending):
            is_dayopen = (pos == 0 and op_pending and i >= op_entry and trend[i] != 0)
        else:
            is_dayopen = (pos == 0 and i == day_start_idx + ign_wk and trend[i] != 0)
        if gf_today and gf_open_idx >= 0 and i == gf_open_idx and pos == 0 and trend[i] != 0:
            is_dayopen = True
        gf_block = gf_today and (gf_open_idx < 0 or i < gf_open_idx)
        op_block = ((op_pol == 1 or op_pol == 5) and op_pending and i < op_entry) or \
                   (op_pol == 3 and s_wait and not is_flip)
        s_fallback = (op_pol == 3 and s_wait and pos == 0 and s_cnt >= 3
                      and not is_flip and trend[i] != 0)
        in_win = am_on and am_triggered and hm_min[i] >= am_start and hm_min[i] < am_end
        if s_fallback and in_win:
            diag[4] += 1.0
            s_wait = False
        elif s_fallback and not gf_block:
            new = trend[i]
            a_bar[na] = i; a_act[na] = 0; a_dir[na] = new; a_px[na] = px_exec; a_rsn[na] = 12; a_p1[na] = 0.0; a_p2[na] = 0.0; na += 1
            pos = new; stop_dir = new; stop_px = px
            s_wait = False
            if ee_on and ee_day[i]:
                ee_active = True; ee_day_ord = oi; ee_px = px; ee_atr_v = atr[i]; ee_bid = bar_in_day[i]
        elif is_dayopen and not is_flip and skip_on and skip_day[i]:
            pass
        elif (is_flip or is_dayopen) and not op_block and not gf_block:
            new = trend[i]
            if in_win and pos != 0 and pos != new:
                # AMGATE 窗口内: 翻仓降级为只平 (平仓侧价格/原因与翻仓平一致), 不开反向
                rc = 8 if is_flip else 10
                a_bar[na] = i; a_act[na] = 1; a_dir[na] = pos; a_px[na] = px_exec; a_rsn[na] = rc; a_p1[na] = 0.0; a_p2[na] = 0.0; na += 1
                pos = 0; stop_dir = 0
                ee_active = False
                diag[5] += 1.0
            elif in_win and pos == 0:
                # AMGATE 窗口内: 新开全挡
                diag[4] += 1.0
                op_pending = False
                s_wait = False
            elif pos != 0 and pos != new:
                rc = 8 if is_flip else 10
                a_bar[na] = i; a_act[na] = 1; a_dir[na] = pos; a_px[na] = px_exec; a_rsn[na] = rc; a_p1[na] = 0.0; a_p2[na] = 0.0; na += 1
                a_bar[na] = i; a_act[na] = 0; a_dir[na] = new; a_px[na] = px_exec; a_rsn[na] = rc + 1; a_p1[na] = 0.0; a_p2[na] = 0.0; na += 1
                pos = new; stop_dir = new; stop_px = px
                ee_active = False
            elif pos == 0:
                rc = 9 if is_flip else 11
                a_bar[na] = i; a_act[na] = 0; a_dir[na] = new; a_px[na] = px_exec; a_rsn[na] = rc; a_p1[na] = 0.0; a_p2[na] = 0.0; na += 1
                pos = new; stop_dir = new; stop_px = px
                op_pending = False
                s_wait = False
                if is_dayopen and ee_on and ee_day[i]:
                    ee_active = True; ee_day_ord = oi; ee_px = px; ee_atr_v = atr[i]; ee_bid = bar_in_day[i]
        # --- amgate 窗口恢复: 窗口结束首根 bar (am_resume_now 已在 bar 顶定格),
        # 空仓且 ST 趋势有定义 -> 按当时趋势直接开仓 (状态触发, 同开盘开执行价口径);
        # 持仓未平不动; 本根若已由翻仓/开盘开仓进场 (pos != 0) 则不重复开。 ---
        if am_resume_now and pos == 0 and trend[i] != 0:
            new = trend[i]
            a_bar[na] = i; a_act[na] = 0; a_dir[na] = new; a_px[na] = px_exec; a_rsn[na] = 13; a_p1[na] = 0.0; a_p2[na] = 0.0; na += 1
            pos = new; stop_dir = new; stop_px = px
            diag[6] += 1.0
            if ee_on and ee_day[i]:
                ee_active = True; ee_day_ord = oi; ee_px = px; ee_atr_v = atr[i]; ee_bid = bar_in_day[i]
    if am_on and am_day_ord >= 0 and not am_sealed:
        # 帧尾最后一日无 13:00 bar 时的封存兜底 (正常日已在 13:00 封存, 不走这里)
        am_ret[am_day_ord] = (am_factor - 1.0) * 100.0
        am_trig[am_day_ord] = 1 if _amg_hit((am_factor - 1.0) * 100.0, am_thr, am_lo, am_mode) else 0
    return na


# ---------------- 外壳: 数据装载 + 动作格式化 ----------------

def _fmt_reason(rsn, dirc, p1, p2, recov, ee_atr_v):
    dn = "call" if dirc == 1 else "put"
    if rsn == 0:
        return "日终平仓(1m15:55)"
    if rsn == 1:
        return "日终平仓(最后一根)"
    if rsn == 2:
        return f"止损窗口回{int(recov * 100)}%平"
    if rsn == 3:
        return "止损窗口(续)"
    if rsn == 4:
        return "止损窗口6根平"
    if rsn == 5:
        return "止损窗口6根平(补)"
    if rsn == 6:
        return f"止损窗口开始(Gamma{p1:.1f}%,回{int(recov * 100)}%价{p2:.2f})"
    if rsn == 7:
        return f"VP提前认错(逆行{p1:.2f}>={ee_atr_v}xATR{p2:.2f})"
    if rsn == 8:
        return f"ST翻转平{dn}"
    if rsn == 9:
        return f"ST翻转开{dn}"
    if rsn == 10:
        return f"ST开盘平{dn}"
    if rsn == 11:
        return f"ST开盘开{dn}"
    if rsn == 12:
        return f"ST兜底开{dn}"
    if rsn == 13:
        return f"窗口恢复开{dn}"
    return f"?rsn{rsn}"


_YEAR_CACHE = {}


def load_week_frame(monday, data_year):
    """与慢版逐项一致的数据装载; 返回 (warm, eod_1m, test_days) 或 None。
    年度 DataFrame 只读, 进程内缓存 (纯 I/O 优化, 不改变任何数值结果)。"""
    if data_year in _YEAR_CACHE:
        df, eod_1m = _YEAR_CACHE[data_year]
    else:
        _dc = "/home/hatch/workspace/research/data_cache"
        df_pkl = f"{_dc}/spy_3m_{data_year}.pkl" if data_year != "2026" else f"{_dc}/spy_3m.pkl"
        df1_pkl = f"{_dc}/spy_1m_{data_year}.pkl" if data_year != "2026" else f"{_dc}/spy_1m.pkl"
        df = pd.read_pickle(df_pkl)
        df1m = pd.read_pickle(df1_pkl)
        eod_1m = {}
        tmp = df1m[df1m["hm"] == "15:55"]
        for _, r in tmp.iterrows():
            eod_1m[r["dstr"]] = float(r["close"])
        _YEAR_CACHE[data_year] = (df, eod_1m)
    week_start = pd.to_datetime(monday).date()
    week_days = [(pd.to_datetime(monday) + pd.Timedelta(days=i)).strftime("%Y-%m-%d") for i in range(5)]
    warm_start = (pd.to_datetime(monday) - pd.Timedelta(days=45)).strftime("%Y-%m-%d")
    warm = df[df["t"].dt.date.astype(str) >= warm_start].reset_index(drop=True)
    avail = sorted(set(warm["t"].dt.date.astype(str).values))
    test_days = [d for d in week_days if d in avail and d not in _SKIP_DATES]
    if not test_days:
        return None
    return warm, eod_1m, test_days


def run_week_fast(monday, data_year, env):
    """跑一周, 返回 (actions, warm) ; actions 与慢版同构 tuple 列表。无数据返回 None。"""
    got = load_week_frame(monday, data_year)
    if got is None:
        return None
    warm, eod_1m, test_days = got
    pf, pi = make_params(env)
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
    # TR (慢版逐 bar 循环的向量化等价: max 为精确运算; v2 日首根只用 H-L)
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
        # QD/S5 输入数组: 与慢版 stonly_gen_qd.py / stonly_gen_vp.py 同源同式
        sys.path.insert(0, "/home/hatch/workspace/qqe_backtest")
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
        with open(env.get("DG_OP_SK_LISTFILE", "")) as _f:
            _sk_dates = set(_json.load(_f))
        opskip_day = np.isin(dates, list(_sk_dates))
    dbg = np.zeros((n, 8), dtype=float)
    s5_npput = np.zeros(n, dtype=float)
    _npd = env.get("DG_S5_NOPRESS_PUT_DAY", "")
    if _npd:
        s5_npput[dates == _npd] = 1.0
    st_arrays_kernel(h, l, c, o, hl2, tr, atr_init, day_start, bar_in_day, is_1500,
                     cur_ds0, pf, pi, atr, mult, bu, bl, fu, fl, trend, ee_flag_bar, skip_flag_bar,
                     dec_p, dec_m, slp5, dbg, s5_npput)
    if pi[47] == 1:
        globals()["S5_LAST_DEBUG"] = {
            "dbg": dbg, "fu": fu.copy(), "fl": fl.copy(), "trend": trend.copy(),
            "atr": atr.copy(), "c": c.copy(), "o": o.copy(), "dates": dates.copy(),
            "hm": warm["t"].dt.strftime("%H:%M").values.copy(), "slp5": slp5.copy(),
        }
    # 日标志 (flag 只在日首 bar 置位) -> 逐 bar 广播
    ee_day_bar = np.zeros(n, dtype=bool); skip_day_bar = np.zeros(n, dtype=bool)
    if ee_flag_bar.any():
        ords = np.unique(day_ord[ee_flag_bar])
        ee_day_bar = np.isin(day_ord, ords)
    if skip_flag_bar.any():
        ords = np.unique(day_ord[skip_flag_bar])
        skip_day_bar = np.isin(day_ord, ords)
    eod_px = np.array([eod_1m.get(d, np.nan) for d in dates], dtype=float)
    skip_bar = np.isin(dates, list(_SKIP_DATES))
    start_idx = int(np.where(dates == test_days[0])[0][0])
    end_idx = int(np.where(dates == test_days[-1])[0][-1]) + 1
    # amgate: 每日 13:00 bar 全局下标 (无则 -1) + 上午收益/触发输出数组
    ndays = int(day_ord.max()) + 1 if n else 0
    am_j13 = np.full(ndays, -1, dtype=np.int64)
    for _d in range(ndays):
        _idx = np.where((day_ord == _d) & (hm_min == 780))[0]
        if len(_idx):
            am_j13[_d] = int(_idx[0])
    am_ret = np.full(ndays, np.nan, dtype=float)
    am_trig = np.zeros(ndays, dtype=np.int64)
    diag = np.zeros(8, dtype=float)
    maxa = 4 * (end_idx - start_idx) + 16
    a_bar = np.zeros(maxa, dtype=np.int64); a_act = np.zeros(maxa, dtype=np.int8)
    a_dir = np.zeros(maxa, dtype=np.int8); a_px = np.zeros(maxa, dtype=float)
    a_rsn = np.zeros(maxa, dtype=np.int8); a_p1 = np.zeros(maxa, dtype=float); a_p2 = np.zeros(maxa, dtype=float)
    na = st_trade_kernel(c, o, day_ord, hm_min, eod_px, skip_bar, trend, atr, bar_in_day,
                         ee_day_bar, skip_day_bar, opskip_day, start_idx, end_idx, pf, pi,
                         a_bar, a_act, a_dir, a_px, a_rsn, a_p1, a_p2,
                         am_j13, am_ret, am_trig, diag)
    recov = pf[32]; ee_atr_v = pf[33]
    hm_str = np.array([f"{int(m // 60):02d}:{int(m % 60):02d}" for m in hm_min])
    act_str = {0: "开", 1: "平", 2: "持"}
    actions = []
    for k in range(na):
        bi = int(a_bar[k]); d = int(a_dir[k])
        tstr = f"{dates[bi][5:]}T{hm_str[bi]}"
        actions.append((bi, tstr, act_str[int(a_act[k])], "call" if d == 1 else "put",
                        float(a_px[k]),
                        _fmt_reason(int(a_rsn[k]), d, float(a_p1[k]), float(a_p2[k]), recov, ee_atr_v)))
    am_info = {}
    for _d in range(ndays):
        am_info[str(uniq[_d])] = (float(am_ret[_d]), int(am_trig[_d]))
    return actions, warm, am_info


def main(argv):
    import pickle as pk
    monday = argv[1]
    data_year = os.environ.get("DATA_YEAR", "2026")
    outdir = os.environ.get("NBX_OUTDIR", "/tmp/nbx_out")
    os.makedirs(outdir, exist_ok=True)
    got = run_week_fast(monday, data_year, os.environ)
    if got is None:
        print(f"no data for week of {monday}", flush=True)
        return 1
    actions, warm, am_info = got
    reb = "rebound-" + ("redonly" if os.environ.get("REBOUND_RED", "0") == "1" and os.environ.get("REBOUND_BLUE", "0") != "1"
                        else "both" if os.environ.get("REBOUND_RED", "0") == "1" else "noreb")
    if os.environ.get("ST_TAG"):
        reb += "-" + os.environ["ST_TAG"]
    reb += f"-cumslope{float(os.environ.get('CUM_SLOPE_THRESHOLD', '0.2')):g}"
    if os.environ.get("BIGK_BLOCK_ALL", "0") == "1":
        reb += "-bigkblockall"
    if os.environ.get("STRONG_TREND", "0") == "1":
        reb += "-strongtrend"
    path = os.path.join(outdir, f"trades-week-{monday}-k3clean-lb10-blue11-{reb}-stonly.pkl")
    with open(path, "wb") as f:
        pk.dump({"actions": actions, "week_monday": monday,
                 "warm_t": warm["t"].astype(str).tolist(),
                 "warm_o": warm["open"].tolist(), "warm_h": warm["high"].tolist(),
                 "warm_l": warm["low"].tolist(), "warm_c": warm["close"].tolist()}, f)
    print(f"saved trades {path} actions={len(actions)}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
