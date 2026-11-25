#!/usr/bin/env python3
"""hama 节载荷配方 (SSL Channel x NSDT HAMA x QQE MOD, user 2026-11-23
令同步 gen 日页, 节名就叫 hama)。

引擎 = 研究定版 ~/workspace/research/ssl_hama_qqe_20261122/sim.py 的
vendored 移植 (复制隔离: 不 import research 目录; QQE 走 gen 仓根
qqe_mod.py 正本, 与研究 sim 同一文件)。定版口径逐项同 sim.py 默认 +
REV_ON=1 (sim_out.json 2026-11-22 15:49 生成口径, src 含 REV/REVF):
- 背景: SSL 1m (SMA200 通道状态) + HAMA 1m + HAMA 3m (WMA55(hl2),
  EMA20/25 平滑 HA 定色, NSDT 渐变染色); 进场侧 HAMA3 黄色不卡
  (BG3_SOFT), 免费首次进场须严格背景三件全同色; 背景与 QQE 同色
  那根即进场 (无等待期), 段内再进走 W5 前窗最长门。段首单另有
  峰值衰减过滤 (定版 FIRST_PR=0.5, user 2026-11-23 采纳): 当根
  |hist| / 同向 LB3 峰值 < 0.5 不开 (只挡段首路, W5 再进不受影响)。
- 衰减反转进场 REV: 对方波衰减 trigger + QQE 确认 (过零/再衰减
  >=10%, 反长作废) + 背景确认双齐才进; 深度衰减 (<=50% 峰 + 三根
  递减) 免 QQE 确认 (REVF)。
- 回补进场 HB (定版, user 2026-11-23 采纳): 任一方向波衰减登记
  (峰>=12) 后, 同款 _hb_trigger 以进场门槛 (1.4, 1.0) 重涨触发 +
  触发根 |hist|>=15 + 背景同向 + 空仓 -> 开仓 (src=HB, 身份按
  顺势单不带 wave); 进场清 rev_pend/dec_pend; STD/REV 之后只补
  空位, 持仓时不新增动作。门槛 (1.4, 1.0) = 原门槛 (2.0, 1.2)
  x0.7 并钳下限 1.0 (剂量直接写进门槛数字, 不留 dose 参数);
  衰减单 HB 出场门槛仍 (2.0, 1.2) 不变。
- 出场: 自己波衰减 (次根确认 DECAY / 加速 DECAYF) / D4 连减 4 根
  且 |hist|<16 / 衰减单波浪管理 (回补门槛 HB + 新波反向 FLIP) /
  顺势单背景破 BG / 反色 FLIP / 日终 EOD; 转灰 exit 已删 (定版)。
  价格止损 99xATR 占位 (STOP_K=99)。
- 波浪管理 WM-FLIP (定版, user 2026-11-24 落地): fav 口径 WAVE_ID
  波账, 衰减单开仓记来源波 (REV=登记根/REVF=信号根所属波), FLIP
  须当前根波 id != 来源波才平; 同波反号根不触发 (原波反长由 HB
  门槛管); 身份转换后 ecol 支 FLIP 不查波账。
- 确认根新波已蔫门 WILT (定版 R=0.7 写死, user 2026-11-24 落地):
  REV/REVF 背景凑齐、执行开仓那根, 确认根 hist 已过零到新波侧时,
  |hist| <= 同侧连续段紧邻前 2 根同侧柱较大者 x0.7 -> 不开、
  pending 作废; 不足 2 根不查、未过零不查; 双侧对称。
- 逆势单 CTR (定版, user 2026-11-24 晚定版; 研究
  research/ssl_hama_qqe_20261122/ctr_report.md 追加13): 标准 QQE
  信号 (F 标准跟随 = 两根真色 >=8 递增 / D 衰减翻仓确认 / DF 深度
  衰减快速支) 出现但背景不允许顺势开时照开、打逆势身份 (ctr);
  衰减路峰值门 11 (CTR_PEAK_MIN); F 支带同波递进门 (同波上一个
  F 开仓触发根 |hist| 为锚、须严格大于); 退场只吃特殊衰减
  CTRX (自己波同向柱 <=前根 90% 登记、按 dec_pend 同款确认) +
  HB 回补平 + 新波反向 FLIP (带 WM 同波门) + SL/EOD; 背景转
  同边 -> 转普通顺势单 (conv); 普通单 (STD/HB) 被 DECAY/DECAYF
  平掉的决策根上背景没齐反方向 -> 同根翻逆势 (CTR_FLIP);
  逆势单平仓决策根整根按空仓根处理、全进场路放行
  (CTR_REOPEN_ALL)。渲染: 逆势单亮蓝边圈 (#3b82f6)。
- 午间斜率门: 12:00 起每半小时检查点, |近30根位移| >= 0.75xATR
  才开下半小时, 14:30 解除 (MID_ON, 全因果)。
- 开仓窗口: 09:31–15:54 (起点 09:31 = 提前进场, user 2026-11-24 采纳:
  扫 09:31-09:34 中 09:31 Δ+777.69pp 三年全正胜出; 尾限去除为
  2026-11-23 定版):
  全部进场路尾限由 15:30 放开到 15:54 (相对 Δ+951.20pp 三年
  全正, 15:30 后新开 994 笔/+813.71pp); 换身份与挡单标注仍
  15:30, 与 sim.py ENTRY_END 定版口径一致。
- FOMC 决议日全天不做 (日期表与 sim.py 同)。
- 结算: 30% 杠杆真 0DTE 公式 (Call ROI% = 30*dSPY + 2.4*dSPY^2,
  put 镜像, 下限 -100)。

数据生产形态: 一遍全帧跑完 (缓存 2023-11-01 起暖机, spy_1m_2023.pkl
+ spy_1m.pkl, 与研究 sim 的 SIM_PRE2023 + SIM_LOAD_FROM=2023-11-01
同帧), 按日切片; 绝不逐日重跑。目标日不在缓存时 (实盘当日) 用
gen 传入的当日 1m bars 补帧尾。
载荷 schema 与研究 sim_out.json 的 day dict 同形 (ssl20 三键渲染
不用, 不发), 渲染走 render_section (研究 build_html.py 单日版逐式
移植, 模板逐字复用)。
"""
import json
import os
import sys

import numpy as np
import pandas as pd

_GEN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _GEN_ROOT not in sys.path:
    sys.path.insert(0, _GEN_ROOT)
from qqe_mod import qqe_mod  # noqa: E402

_CACHE_DIR = os.path.join(_GEN_ROOT, "..", "research", "data_cache")
WARM_FROM = "2023-11-01"
OUT_FROM = "2024-01-02"

# ---- 定版参数 (sim.py 默认 + REV_ON=1) ----
STOP_K = 99.0
FIRST_PR = 0.5
HB_ENTRY_MULT = (1.4, 1.0)  # 回补进场门槛 (小档/大档), 定版常数
HB_MIN = 15.0               # 回补进场触发根 |hist| 下限, 定版常数
WILT_R = 0.7                # 确认根「新波已蔫」门定版常数 (user 2026-11-24 落地)
CTR_PEAK_MIN = 11.0         # 逆势衰减路 (D/DF) 峰值门定版常数 (user 2026-11-24 晚定版)
REV_FAST = True
HY_W = 0
HY_ALL = False
HS_MIN = 0
BG_H3 = False
BG3_SOFT = True
SLP_N = 30
SLP_T = 0.75
CTR_SLP_T = 0.30  # 逆势路专用斜率门槛 (定版, user 2026-11-24 采纳; 顺势仍 0.75)
FOMC = {"2024-01-31", "2024-03-20", "2024-05-01", "2024-06-12",
        "2024-07-31", "2024-09-18", "2024-11-07", "2024-12-18",
        "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18",
        "2025-07-30", "2025-09-17", "2025-10-29", "2025-12-10",
        "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17",
        "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09"}

_MEMO = {}


def _load_frame(upto_day, today_bars=None):
    """缓存全帧 (2023-11-01 起) 截到 upto_day; upto_day 不在缓存且
    给了 today_bars (gen 当日帧, 列 t/open/high/low/close) 时用它补
    当日 (同日以 today_bars 为准)。返回 sim.py 同schema 的 DataFrame
    (dstr/hm/open/high/low/close, 已按时序排好、只留 09:30-15:59)。"""
    if today_bars is not None and len(today_bars):
        # gen 实盘/工作流路径: 直接用 gen 自家暖帧 (warmup+当日, API 组帧),
        # 不读本机缓存 (GitHub Actions 环境无缓存文件, 2026-11-23 实测
        # FileNotFoundError)。暖帧约 10 自然日, 高于 sslhq 等价闸的
        # K=3 交易日收敛线, 当日逐笔与全帧口径一致。
        t0 = pd.to_datetime(today_bars["t"])
        c = pd.DataFrame({
            "dstr": t0.dt.strftime("%Y-%m-%d").to_numpy(),
            "hm": t0.dt.strftime("%H:%M").to_numpy(),
            "open": today_bars["open"].to_numpy(float),
            "high": today_bars["high"].to_numpy(float),
            "low": today_bars["low"].to_numpy(float),
            "close": today_bars["close"].to_numpy(float)})
        c = c[(c["hm"] >= "09:30") & (c["hm"] <= "15:59")].copy()
        c = c.sort_values(["dstr", "hm"]).reset_index(drop=True)
        return c
    parts = []
    p23 = os.path.join(_CACHE_DIR, "spy_1m_2023.pkl")
    if os.path.exists(p23):
        d23 = pd.read_pickle(p23)
        parts.append(d23[d23["dstr"] >= WARM_FROM])
    pm = os.path.join(_CACHE_DIR, "spy_1m.pkl")
    dm = pd.read_pickle(pm)
    parts.append(dm)
    c = pd.concat(parts, ignore_index=True)
    c = c[(c["dstr"] >= WARM_FROM) & (c["dstr"] <= upto_day)]
    if today_bars is not None and len(today_bars):
        tb = today_bars.copy()
        t = pd.to_datetime(tb["t"])
        tb = pd.DataFrame({
            "dstr": t.dt.strftime("%Y-%m-%d").to_numpy(),
            "hm": t.dt.strftime("%H:%M").to_numpy(),
            "open": tb["open"].to_numpy(float),
            "high": tb["high"].to_numpy(float),
            "low": tb["low"].to_numpy(float),
            "close": tb["close"].to_numpy(float)})
        tb = tb[tb["dstr"] == upto_day]
        if len(tb):
            c = c[c["dstr"] != upto_day]
            c = pd.concat([c, tb], ignore_index=True)
    c = c[(c["hm"] >= "09:30") & (c["hm"] <= "15:59")].copy()
    c = c.sort_values(["dstr", "hm"]).reset_index(drop=True)
    return c


def _ema(x, n):
    a = 2.0 / (n + 1)
    out = np.empty_like(x)
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = a * x[i] + (1 - a) * out[i - 1]
    return out


def _wma(x, n):
    w = np.arange(1, n + 1, dtype=float)
    out = np.full(len(x), np.nan)
    for i in range(n - 1, len(x)):
        out[i] = float(np.dot(x[i - n + 1:i + 1], w) / w.sum())
    out[:n - 1] = x[:n - 1]
    return out


def _sma(x, n):
    out = np.full(len(x), np.nan)
    cs = np.cumsum(np.insert(x, 0, 0.0))
    out[n - 1:] = (cs[n:] - cs[:-n]) / n
    return out


def _ssl_state(h, l, cl, n=200):
    mah = _sma(h, n)
    mal = _sma(l, n)
    st = np.zeros(len(cl), dtype=int)
    cur = 0
    for i in range(len(cl)):
        if not np.isnan(mah[i]):
            if cl[i] > mah[i]:
                cur = 1
            elif cl[i] < mal[i]:
                cur = -1
        st[i] = cur
    return mah, mal, st


def _hama(o, h, l, cl):
    hac = (o + h + l + cl) / 4.0
    hao = np.empty(len(cl))
    hao[0] = (o[0] + cl[0]) / 2.0
    for i in range(1, len(cl)):
        hao[i] = (hao[i - 1] + hac[i - 1]) / 2.0
    sc = _ema(hac, 20)
    so = _ema(hao, 25)
    col = np.zeros(len(cl), dtype=int)
    shade = np.zeros(len(cl), dtype=int)
    prev = 0
    runlen = 0
    for i in range(len(cl)):
        s = 1 if sc[i] > so[i] else (-1 if sc[i] < so[i] else 0)
        if s == 0 or (prev != 0 and s != prev):
            col[i] = 0
            shade[i] = 0
            runlen = 0
        else:
            col[i] = s
            runlen += 1
            shade[i] = s * min(runlen, 3)
        if s != 0:
            prev = s
    line = _wma((h + l) / 2.0, 55)
    return line, col, shade


def _run(c):
    """sim.py 主体逐字移植: 全帧指标 + 逐日交易循环, 返回
    {day: payload} (OUT_FROM 起, FOMC 日 payload 无单)。"""
    O = c["open"].to_numpy(float)
    H = c["high"].to_numpy(float)
    L = c["low"].to_numpy(float)
    C = c["close"].to_numpy(float)
    DSTR = c["dstr"].to_numpy()
    HM = c["hm"].to_numpy()
    N = len(c)
    all_days = sorted(set(DSTR.tolist()))
    DAYS = [d for d in all_days if d >= OUT_FROM]

    # ---------- 3m bars ----------
    rows = []
    for d, g in c.groupby("dstr", sort=False):
        idx = g.index.to_numpy()
        for k in range(0, len(idx) - 2, 3):
            ii = idx[k:k + 3]
            rows.append((ii[-1], O[ii[0]], H[ii].max(), L[ii].min(),
                         C[ii[-1]]))
    last1, O3, H3, L3, C3 = (np.array([r[j] for r in rows])
                             for j in range(5))
    last1 = last1.astype(int)

    ssl1_mah, ssl1_mal, ssl1_st = _ssl_state(H, L, C)
    ssl3_mah, ssl3_mal, ssl3_st = _ssl_state(H3, L3, C3)
    hama1_line, hama1_col, hama1_shade = _hama(O, H, L, C)
    hama3_line, hama3_col, hama3_shade = _hama(O3, H3, L3, C3)

    ssl3_at = np.zeros(N, dtype=int)
    hama3_at = np.zeros(N, dtype=int)
    hama3_line_at = np.full(N, np.nan)
    hama3_shade_at = np.zeros(N, dtype=int)
    pos3 = np.searchsorted(last1, np.arange(N), side="right") - 1
    for i in range(N):
        j = pos3[i]
        if j >= 0:
            ssl3_at[i] = ssl3_st[j]
            hama3_at[i] = hama3_col[j]
            hama3_line_at[i] = hama3_line[j]
            hama3_shade_at[i] = hama3_shade[j]

    # ---------- QQE 1m ----------
    q = qqe_mod(C)
    hist = np.asarray(q["hist"], dtype=float)
    qup = np.asarray(q["up"]).astype(bool)
    qdn = np.asarray(q["down"]).astype(bool)
    qcol = np.where(qup, 1, np.where(qdn, -1, 0))
    ecol = qcol.copy()
    _gm = (qcol == 0) & (np.abs(hist) >= 10)
    ecol[_gm] = np.sign(hist[_gm]).astype(int)

    TR = np.maximum(H - L, np.maximum(np.abs(H - np.roll(C, 1)),
                                      np.abs(L - np.roll(C, 1))))
    TR[0] = H[0] - L[0]
    atr1m = np.zeros(N)
    _a = float(TR[:14].mean())
    for i in range(N):
        _a = TR[i] if i == 0 else (_a * 13 + TR[i]) / 14
        atr1m[i] = _a

    def _trig_line(peak):
        return peak * 0.80 ** (1.0 + max(0.0, float(peak) - 12.0) / 16.0)

    def _decay_signals(mask, opp):
        out = {}
        for i in range(N):
            ca = abs(hist[i])
            lo = max(0, i - 10)
            seg_lo = lo
            for j in range(i, lo - 1, -1):
                if j < i and DSTR[j] != DSTR[i]:
                    seg_lo = j + 1
                    break
                if opp[j] or (j < i and abs(hist[j]) <= 3):
                    seg_lo = j + 1
                    break
            peaks = [abs(hist[j]) for j in range(seg_lo, i + 1)
                     if mask[j] and abs(hist[j]) > 3]
            if not peaks:
                continue
            if mask[i] and ca < 4:
                continue
            peak = max(peaks)
            if ca >= peak:
                continue
            prev_ca = abs(hist[i - 1]) if i - 1 >= seg_lo else 999.0
            if peak >= 6 and ca <= _trig_line(peak) and ca <= prev_ca * 0.9:
                out[i] = peak
        return out

    dec_up = _decay_signals(ecol == 1, ecol == -1)
    dec_dn = _decay_signals(ecol == -1, ecol == 1)

    # ---- 波账 WAVE_ID (WM-FLIP 定版, user 2026-11-24 落地; 与 sim.py
    # 同文): fav 口径波段划分: |hist|<3 忽略 (归属当前波), 反向 >=3
    # 断波开新波, 跨日重置。衰减单开仓记来源波 id (pos["src_wave"],
    # 仅内部字段, 输出时剥离), FLIP 支须当前根波 id != 来源波才平。
    WAVE_ID = np.full(N, -1, dtype=int)
    _wid = 0
    _wsgn = 0
    _wcur = -1
    for _i in range(N):
        if _i > 0 and DSTR[_i] != DSTR[_i - 1]:
            _wsgn = 0
            _wcur = -1
        _h = hist[_i]
        if abs(_h) >= 3:
            _s = 1 if _h > 0 else -1
            if _s != _wsgn:
                _wid += 1
                _wsgn = _s
                _wcur = _wid
        WAVE_ID[_i] = _wcur

    def _wilt_check(gi, ed, i0):
        """确认根「新波已蔫」门 (定版 R=0.7, 与 sim.py 同文): hist[gi]
        未过零到新波一侧 -> 不查; gi 前同侧连续段 (当日内) 紧邻不足
        2 根 -> 不查; 否则 |hist[gi]| <= max(前两根) x WILT_R -> 挡。"""
        hv = hist[gi]
        if hv * ed <= 0:
            return False, 0.0, []
        prev = []
        j = gi - 1
        while j >= i0 and hist[j] * ed > 0 and len(prev) < 2:
            prev.append(abs(float(hist[j])))
            j -= 1
        if len(prev) < 2:
            return False, 0.0, prev
        base = max(prev)
        return abs(float(hv)) <= base * WILT_R, base, prev

    _FW_ANCHOR = {}  # 逆势跟随递进门锚: WAVE_ID -> 同波最近一次 CTR F
    # 开仓触发根 |hist| (全程一本账, 与 sim.py 同口径)

    def _fw_ok(gi):
        """递进门 (定版): 同波无 F 开仓锚时恒过; 有锚时触发根
        |hist| 须严格大于锚。"""
        _a = _FW_ANCHOR.get(int(WAVE_ID[gi]))
        return _a is None or abs(hist[gi]) > _a

    def _hb_true(j, sgn):
        return bool(qup[j]) and abs(hist[j]) >= 4 if sgn > 0 else \
            bool(qdn[j]) and abs(hist[j]) >= 4

    def _hb_gray(j):
        if abs(hist[j]) <= 3:
            return False
        return not _hb_true(j, 1) and not _hb_true(j, -1)

    def _hb_true_w(j, sgn):
        if _hb_true(j, sgn):
            return True
        return _hb_gray(j) and hist[j] * sgn > 0 and abs(hist[j]) >= 10

    def _hb_trigger(i, tdir, marker, mult=(2.0, 1.2)):
        # mult = "比前根"两档倍数 (小档 |前|<=5 / 大档): 衰减单出场
        # 调用用默认 (2.0, 1.2); 回补进场路传 HB_ENTRY_MULT (1.4, 1.0)
        if not _hb_true_w(i, tdir):
            return False
        hc = abs(hist[i])
        if hc > marker * 1.2:
            return True
        if i >= 1:
            hp = hist[i - 1]
            if hp * tdir > 0 and abs(hp) > 3:
                nd = mult[0] if abs(hp) <= 5 else mult[1]
                if hc > abs(hp) * nd:
                    return True
        if i >= 2:
            h1, h2 = hist[i - 2], hist[i - 1]
            if (h1 * tdir > 0 and h2 * tdir > 0 and abs(h1) > 3
                    and abs(h2) > 3 and abs(h2) > abs(h1) and hc > abs(h2)):
                nd1 = mult[0] if abs(h1) <= 5 else mult[1]
                if hc > abs(h1) * nd1:
                    return True
        if i >= 1:
            hp = hist[i - 1]
            if hp * tdir > 0 and abs(hp) > 3 and abs(hp) > marker \
                    and hc > marker:
                return True
        return False

    bg = np.zeros(N, dtype=int)
    bg_e = np.zeros(N, dtype=int)
    bg_r = np.zeros(N, dtype=int)
    for i in range(N):
        if ssl1_st[i] == 1 and hama3_at[i] == 1 and \
                (BG_H3 or hama1_col[i] == 1):
            bg[i] = 1
        elif ssl1_st[i] == -1 and hama3_at[i] == -1 and \
                (BG_H3 or hama1_col[i] == -1):
            bg[i] = -1
        if ssl1_st[i] == 1 and hama1_col[i] == 1 and hama3_at[i] != -1:
            bg_e[i] = 1
        elif ssl1_st[i] == -1 and hama1_col[i] == -1 and hama3_at[i] != 1:
            bg_e[i] = -1
        if ssl1_st[i] == 1 and hama1_col[i] == 1:
            bg_r[i] = 1
        elif ssl1_st[i] == -1 and hama1_col[i] == -1:
            bg_r[i] = -1

    def roi(dirn, ep, xp):
        d = xp - ep
        r = 30.0 * d + 2.4 * d * d if dirn == 1 else -30.0 * d + 2.4 * d * d
        return max(r, -100.0)

    # ---- 午间斜率门 (全因果) ----
    MID_ON = np.ones(N, dtype=bool)
    if SLP_N > 0:
        _CP = {"12:00", "12:30", "13:00", "13:30", "14:00"}
        _state = True
        for _i in range(N):
            _h = HM[_i]
            if _h in _CP:
                _state = (_i - SLP_N >= 0) and \
                    abs(C[_i] - C[_i - SLP_N]) >= SLP_T * atr1m[_i]
            if "12:00" <= _h < "14:30":
                MID_ON[_i] = _state

    # ---- 逆势路专用门 (定版 0.30, 形态与 MID_ON 同, 只换门槛) ----
    MID_CTR = np.ones(N, dtype=bool)
    if SLP_N > 0:
        _CP = {"12:00", "12:30", "13:00", "13:30", "14:00"}
        _state = True
        for _i in range(N):
            _h = HM[_i]
            if _h in _CP:
                _state = (_i - SLP_N >= 0) and \
                    abs(C[_i] - C[_i - SLP_N]) >= CTR_SLP_T * atr1m[_i]
            if "12:00" <= _h < "14:30":
                MID_CTR[_i] = _state

    day_idx = {d: np.where(DSTR == d)[0] for d in DAYS}
    out_days = {}
    for d in DAYS:
        ii = day_idx[d]
        i0, i1 = int(ii[0]), int(ii[-1])
        trades = []
        events = []  # 仪表化: 非成交事件 (pending 设置/确认/取消, 午间门挡单)
        if d not in FOMC:
            pos = None
            prev_conj = False
            dec_pend = None
            rev_pend = None
            ctr_pend = None   # 逆势路衰减翻仓待确认: (登记根, |hist|, 衰减波方向)
            ctr_xpend = None  # 逆势单特殊退场待确认: (触发根, 触发根 |hist|)
            hb_arm = {1: None, -1: None}   # 回补进场: 各方向已登记 marker
            hb_next = {1: False, -1: False}  # 上一根有衰减登记 -> 本根落 marker
            for gi in range(i0, i1 + 1):
                hm = HM[gi]
                _bge = bg_e[gi] if BG3_SOFT else bg[gi]
                conj = _bge != 0 and ecol[gi] == _bge
                conj_s = bg[gi] != 0 and ecol[gi] == bg[gi]
                edge = conj_s and not prev_conj
                prev_conj = conj_s
                # 回补全局登记 (与持仓无关的波状态, 同 sim): 方向波
                # 衰减登记 (dec_up/dec_dn, 峰>=12) -> 次根落 marker =
                # 次根 |hist|; 新登记覆盖旧登记; 登记按日重置。
                for _s, _decd in ((1, dec_up), (-1, dec_dn)):
                    if hb_next[_s]:
                        hb_arm[_s] = abs(hist[gi])
                        hb_next[_s] = False
                    if _decd.get(gi) is not None and _decd[gi] >= 12:
                        hb_next[_s] = True
                # ---- 持仓管理 ----
                if pos is not None:
                    dr = pos["dir"]
                    exited = False
                    _pos_src = pos.get("src")
                    _dp_t0 = dec_pend[0] if dec_pend is not None else None
                    if dr == 1 and L[gi] <= pos["stop"]:
                        xp, rsn, xb = pos["stop"], "SL", gi
                        trades.append({**pos, "xb": xb, "xp": xp,
                                       "rsn": rsn,
                                       "r": roi(dr, pos["ep"], xp)})
                        pos = None
                        exited = True
                    elif dr == -1 and H[gi] >= pos["stop"]:
                        xp, rsn, xb = pos["stop"], "SL", gi
                        trades.append({**pos, "xb": xb, "xp": xp,
                                       "rsn": rsn,
                                       "r": roi(dr, pos["ep"], xp)})
                        pos = None
                        exited = True
                    if pos is not None:
                        rsn = None
                        if pos.get("ctr"):
                            # 逆势单特殊退场 (定版, 同 sim): 只吃本支
                            # —— 特殊衰减 CTRX (确认规则同 dec_pend) +
                            # HB 回补平 + 新波反向 FLIP (带 WM 同波门)
                            # + EOD; DECAY/D4/BG/转色 FLIP 不管逆势单
                            # (下方各支已加 not ctr 守卫), SL 在本支
                            # 之前与全策略同款。
                            if ctr_xpend is not None:
                                _th = ctr_xpend[1]
                                _hv = hist[gi]
                                if _hv * dr < 0 or abs(_hv) <= 0.9 * _th:
                                    rsn = "CTRX"
                                    ctr_xpend = None
                                elif _hv * dr > 0 and abs(_hv) > _th:
                                    ctr_xpend = None
                            if rsn is None and _hb_trigger(
                                    gi, -dr, pos.get("marker", 0.0)):
                                rsn = "HB"
                            if rsn is None and hist[gi] * dr < 0 \
                                    and abs(hist[gi]) > 3:
                                if WAVE_ID[gi] != pos.get("src_wave", -1):
                                    rsn = "FLIP"
                            if rsn is None and hm == "15:55":
                                rsn = "EOD"
                            if rsn is None and ctr_xpend is None \
                                    and gi - 1 >= i0 \
                                    and hist[gi] * dr > 0 \
                                    and hist[gi - 1] * dr > 0 \
                                    and abs(hist[gi]) <= 0.9 \
                                    * abs(hist[gi - 1]):
                                ctr_xpend = (gi, abs(hist[gi]))
                        if dec_pend is not None:
                            _th = dec_pend[1]
                            _hv = hist[gi]
                            if _hv * dr < 0 or abs(_hv) <= 0.9 * _th:
                                rsn = "DECAY"
                                dec_pend = None
                            elif _hv * dr > 0 and abs(_hv) > _th:
                                events.append((gi, "衰减确认失败·取消"))
                                dec_pend = None
                        if rsn is None and not pos.get("ctr") \
                                and abs(hist[gi]) < 16 \
                                and gi - 3 >= i0:
                            _d4 = all(hist[_j] * dr > 0
                                      for _j in range(gi - 3, gi + 1)) and \
                                all(abs(hist[_j]) < abs(hist[_j - 1])
                                    for _j in range(gi - 2, gi + 1))
                            if _d4:
                                rsn = "D4"
                        if rsn is None and pos.get("wave"):
                            if _hb_trigger(gi, -dr, pos.get("marker", 0.0)):
                                rsn = "HB"
                            elif hist[gi] * dr < 0 and abs(hist[gi]) > 3:
                                # WM-FLIP 定版: 与开仓来源同一根波不认
                                # 「新波反向」 (原波反长由 HB 门槛管)
                                if WAVE_ID[gi] != pos.get("src_wave", -1):
                                    rsn = "FLIP"
                        elif rsn is None and not pos.get("ctr") \
                                and ecol[gi] == -dr \
                                and ecol[gi] != 0:
                            rsn = "FLIP"
                        if rsn is None and not pos.get("ctr") \
                                and bg[gi] != dr \
                                and not pos.get("wave"):
                            rsn = "BG"
                        if rsn is None and hm == "15:55":
                            rsn = "EOD"
                        if rsn is None and not pos.get("ctr"):
                            _sig = (dec_up if dr == 1 else dec_dn).get(gi)
                            if _sig is not None and _sig >= 12 \
                                    and dec_pend is None:
                                if abs(hist[gi]) <= 0.5 * _sig and \
                                        abs(hist[gi]) < abs(hist[gi - 1]) \
                                        < abs(hist[gi - 2]):
                                    rsn = "DECAYF"
                                else:
                                    dec_pend = (gi, abs(hist[gi]))
                                    events.append(
                                        (gi, "衰减信号·待次根确认"))
                        if rsn is not None:
                            xb = gi + 1 if gi + 1 <= i1 else gi
                            xp = O[xb] if gi + 1 <= i1 else C[gi]
                            if rsn == "EOD":
                                xb, xp = gi, O[gi]
                            trades.append({**pos, "xb": xb, "xp": xp,
                                           "rsn": rsn,
                                           "r": roi(dr, pos["ep"], xp)})
                            pos = None
                            exited = True
                    if pos is not None and pos.get("wave"):
                        _hy2 = True
                        if HY_W > 0:
                            _hy2 = all(hama1_col[_j] != 0 for _j in
                                       range(max(0, gi - HY_W + 1), gi + 1))
                        if (_bge == dr and ecol[gi] == dr and _hy2
                                and abs(hama1_shade[gi]) >= HS_MIN
                                and "09:31" <= hm <= "15:30"):
                            _w5d = 0.0
                            for _j in range(max(i0, gi - 5), gi):
                                if ecol[_j] == dr:
                                    _w5d = max(_w5d, abs(hist[_j]))
                            if _w5d > 0 and abs(hist[gi]) >= _w5d:
                                pos["wave"] = False
                                pos["conv"] = True
                    if pos is not None and pos.get("ctr"):
                        # 逆势单身份转换 (定版, 同 sim): 背景三件套
                        # 转到持仓同边 -> 转普通顺势单, 自次根起按
                        # 标准退场管理 (DECAY/D4/BG/FLIP 恢复)
                        if bg[gi] == dr:
                            pos["ctr"] = False
                            pos["conv"] = True
                            ctr_xpend = None
                    if exited and pos is None \
                            and rsn in ("DECAY", "DECAYF") \
                            and _pos_src in ("STD", "HB"):
                        # 衰减平仓同根翻逆势 (定版, 同 sim): 决策根
                        # 上背景 (bg_r) 没齐到反方向才翻; 执行与
                        # 平仓同为次根开盘 (真翻仓)
                        _ed = -dr
                        if bg_r[gi] != _ed \
                                and "09:31" <= hm <= "15:54" \
                                and MID_CTR[gi]:
                            eb = gi + 1
                            if eb <= i1:
                                stop = float(
                                    O[eb] - STOP_K * atr1m[gi]) \
                                    if _ed == 1 else float(
                                        O[eb] + STOP_K * atr1m[gi])
                                if rsn == "DECAYF":
                                    _csub, _sw = "DF", int(WAVE_ID[gi])
                                else:
                                    _csub, _sw = "D", int(
                                        WAVE_ID[_dp_t0]) \
                                        if _dp_t0 is not None else int(
                                            WAVE_ID[gi])
                                pos = {"dir": _ed, "trig": gi, "eb": eb,
                                       "ep": float(O[eb]), "stop": stop,
                                       "grey": bool(qcol[gi] == 0),
                                       "src": "CTR", "ctr": True,
                                       "ctr0": True, "csub": _csub,
                                       "marker": float(abs(hist[eb])),
                                       "src_wave": _sw, "flipborn": True}
                                rev_pend = None
                    if exited:
                        dec_pend = None
                        ctr_xpend = None
                        if _pos_src == "CTR":
                            # 逆势单平仓根全放行 (定版, 同 sim):
                            # 本根整根按空仓根处理, 不跳过进场段,
                            # ctr_pend 保留
                            pass
                        else:
                            ctr_pend = None
                            continue
                if pos is not None:
                    continue
                # ---- 触发 ----
                _w5 = 0.0
                for _j in range(max(i0, gi - 5), gi):
                    if ecol[_j] == _bge:
                        _w5 = max(_w5, abs(hist[_j]))
                incr = ecol[gi] == _bge and _w5 > 0 and abs(hist[gi]) >= _w5
                _hy = True
                if HY_W > 0:
                    _hy = all(hama1_col[_j] != 0 for _j in
                              range(max(0, gi - HY_W + 1), gi + 1))
                _first = edge or (hm == "09:31" and conj_s)
                if HY_ALL:
                    _first = _first and _hy
                # 段首单峰值衰减过滤 (定版 0.5, 同 sim.py FIRST_PR): 当根
                # |hist| 占同向 LB3 峰值 (当根及前 2 根、只算同日、
                # sign(hist)==进场方向、灰柱按符号算) 的比值 < 0.5 ->
                # 段首路不开; 窗口内除当根外无同号 bar 时比值=1.0 自然
                # 通过。只挡 _first 路, W5 再进 (conj and incr) 不受影响。
                _pr_ok = True
                if FIRST_PR > 0 and _first:
                    _pk = abs(hist[gi])
                    for _j in range(max(i0, gi - 2), gi):
                        if hist[_j] * _bge > 0:
                            _pk = max(_pk, abs(hist[_j]))
                    _pr_ok = _pk <= 0 or (abs(hist[gi]) / _pk) >= FIRST_PR
                if not MID_ON[gi] \
                        and ((_first and _pr_ok) or (conj and incr and _hy)) \
                        and abs(hama1_shade[gi]) >= HS_MIN \
                        and "09:31" <= hm <= "15:30":
                    events.append((gi, "午间斜率门关·信号被挡"))
                if _first and not _pr_ok and not (conj and incr and _hy) \
                        and abs(hama1_shade[gi]) >= HS_MIN \
                        and "09:31" <= hm <= "15:30" and MID_ON[gi]:
                    events.append((gi, "段首峰值比<0.5·信号被挡"))
                if ((_first and _pr_ok) or (conj and incr and _hy)) \
                        and abs(hama1_shade[gi]) >= HS_MIN \
                        and "09:31" <= hm <= "15:54" and MID_ON[gi]:
                    dr = _bge
                    eb = gi + 1
                    if eb <= i1:
                        stop = float(O[eb] - STOP_K * atr1m[gi]) \
                            if dr == 1 else float(O[eb] + STOP_K * atr1m[gi])
                        pos = {"dir": dr, "trig": gi, "eb": eb,
                               "ep": float(O[eb]), "stop": stop,
                               "grey": bool(qcol[gi] == 0), "src": "STD"}
                        dec_pend = None
                        if rev_pend is not None:
                            events.append((gi, "标准进场·翻仓等待取消"))
                        rev_pend = None
                # ---- REV 反转进场通道 ----
                if pos is None:
                    if rev_pend is not None:
                        _t0, _th, _wv, _qc, _hd = rev_pend
                        _hv = hist[gi]
                        if _hv * _wv > 0 and abs(_hv) > _th:
                            events.append((gi, "翻仓确认失败·取消"))
                            rev_pend = None
                        else:
                            if not _qc and (_hv * _wv < 0
                                            or abs(_hv) <= 0.9 * _th):
                                _qc = True
                                events.append((gi, "衰减已确认"))
                            if _qc and bg_r[gi] == -_wv \
                                    and "09:31" <= hm <= "15:54" \
                                    and MID_ON[gi]:
                                _ed = -_wv
                                if _wilt_check(gi, _ed, i0)[0]:
                                    # 新波已蔫门挡下: 不开, pending 作废
                                    events.append((gi, "新波已蔫·不开"))
                                else:
                                    eb = gi + 1
                                    if eb <= i1:
                                        stop = float(
                                            O[eb] - STOP_K * atr1m[gi]) \
                                            if _ed == 1 else \
                                            float(O[eb] + STOP_K * atr1m[gi])
                                        pos = {"dir": _ed, "trig": _t0,
                                               "eb": eb, "trig0": _hd,
                                               "ep": float(O[eb]),
                                               "stop": stop,
                                               "grey": bool(qcol[_t0] == 0),
                                               "src": "REV", "wave": True,
                                               "marker": float(
                                                   abs(hist[eb])),
                                               "src_wave": int(
                                                   WAVE_ID[_t0])}
                                        dec_pend = None
                                rev_pend = None
                            else:
                                if _qc and bg_r[gi] == -_wv \
                                        and "09:31" <= hm <= "15:30" \
                                        and not MID_ON[gi]:
                                    events.append(
                                        (gi, "午间斜率门关·翻仓信号被挡"))
                                rev_pend = (_t0, _th, _wv, _qc, _hd)
                    if pos is None:
                        for _wv, _sg in ((1, dec_up.get(gi)),
                                         (-1, dec_dn.get(gi))):
                            if _sg is not None and _sg >= 12:
                                _ed = -_wv
                                if (abs(hist[gi]) <= 0.5 * _sg
                                        and abs(hist[gi]) < abs(hist[gi - 1])
                                        < abs(hist[gi - 2])
                                        and bg_r[gi] == _ed
                                        and "09:31" <= hm <= "15:54"
                                        and MID_ON[gi]):
                                    if _wilt_check(gi, _ed, i0)[0]:
                                        # 快速支信号根 hist 恒在旧波一侧,
                                        # 构造上不触发; 同 REV 口径不开
                                        events.append((gi, "新波已蔫·不开"))
                                    else:
                                        eb = gi + 1
                                        if eb <= i1:
                                            stop = float(
                                                O[eb] - STOP_K * atr1m[gi]) \
                                                if _ed == 1 else float(
                                                    O[eb] + STOP_K * atr1m[gi])
                                            pos = {"dir": _ed, "trig": gi,
                                                   "eb": eb,
                                                   "ep": float(O[eb]),
                                                   "stop": stop,
                                                   "grey": bool(
                                                       qcol[gi] == 0),
                                                   "src": "REVF",
                                                   "wave": True,
                                                   "marker": float(
                                                       abs(hist[eb])),
                                                   "src_wave": int(
                                                       WAVE_ID[gi])}
                                            dec_pend = None
                                else:
                                    if abs(hist[gi]) <= 0.5 * _sg \
                                            and abs(hist[gi]) < \
                                            abs(hist[gi - 1]) \
                                            < abs(hist[gi - 2]) \
                                            and bg_r[gi] == _ed \
                                            and "09:31" <= hm <= "15:30" \
                                            and not MID_ON[gi]:
                                        events.append(
                                            (gi, "午间斜率门关·信号被挡"))
                                    # 链头 _hd2: 覆盖同向在挂 pending 时继承
                                    # 其链头 (展示用: 等待圈画在链头第一根,
                                    # user 2026-11-23 令; 机制字段不变)
                                    _hd2 = gi
                                    if rev_pend is not None \
                                            and rev_pend[2] == _wv:
                                        _hd2 = rev_pend[4]
                                    rev_pend = (gi, abs(hist[gi]), _wv,
                                                False, _hd2)
                                    events.append(
                                        (gi, "衰减翻仓信号·待次根确认"))
                                break
                # ---- 回补进场路 (定版, 同 sim): 任一方向波衰减登记
                # 后, 同款 _hb_trigger 以进场门槛 (1.4, 1.0) 触发 +
                # 触发根 |hist|>=HB_MIN + 背景 bg 同向 + 空仓 -> 按该
                # 方向开仓。身份按 STD 顺势单 (不带 wave): 吃
                # DECAY/DECAYF/D4/BG/FLIP/EOD, 不吃波浪管理。
                # STD/REV 定版通道在本路之前已处理, 本路只补空位;
                # 持仓时不新增任何动作。
                if pos is None:
                    for _s in (1, -1):
                        if (hb_arm[_s] is not None and bg[gi] == _s
                                and abs(hist[gi]) >= HB_MIN
                                and _hb_trigger(gi, _s, hb_arm[_s],
                                                HB_ENTRY_MULT)
                                and abs(hama1_shade[gi]) >= HS_MIN
                                and "09:31" <= hm <= "15:54"
                                and MID_ON[gi]):
                            eb = gi + 1
                            if eb <= i1:
                                stop = float(O[eb] - STOP_K * atr1m[gi]) \
                                    if _s == 1 else \
                                    float(O[eb] + STOP_K * atr1m[gi])
                                pos = {"dir": _s, "trig": gi, "eb": eb,
                                       "ep": float(O[eb]), "stop": stop,
                                       "grey": bool(qcol[gi] == 0),
                                       "src": "HB"}
                                hb_arm[_s] = None
                                dec_pend = None
                                if rev_pend is not None:
                                    events.append(
                                        (gi, "回补进场·翻仓等待取消"))
                                rev_pend = None
                            break
                # ---- 逆势进场路 CTR (定版, 同 sim): 标准 QQE 信号
                # 出现但背景不允许顺势开时照开、打逆势身份。
                # STD/REV/HB 已在本路之前处理, 本路只补空位; 开仓
                # 窗/午间斜率门与各路同款, FIRST_PR/WILT/HS_MIN
                # 不套本路。
                if pos is None:
                    # (a) 衰减翻仓支: ctr_pend 与 rev_pend 同款状态机
                    # (登记峰 >= CTR_PEAK_MIN、反长超登记根作废、过零
                    # 或再衰减 >=10% 确认), 差别只在背景条件取反 ——
                    # 确认时背景已齐到进场方向 -> 归 REV 路 (清 pend
                    # 不抢); 没齐 -> 逆势开。
                    if ctr_pend is not None:
                        _t0, _th, _wv = ctr_pend
                        _hv = hist[gi]
                        if _hv * _wv > 0 and abs(_hv) > _th:
                            ctr_pend = None
                        elif _hv * _wv < 0 or abs(_hv) <= 0.9 * _th:
                            _ed = -_wv
                            if bg_r[gi] == _ed:
                                ctr_pend = None
                            elif "09:31" <= hm <= "15:54" and MID_CTR[gi]:
                                eb = gi + 1
                                if eb <= i1:
                                    stop = float(
                                        O[eb] - STOP_K * atr1m[gi]) \
                                        if _ed == 1 else float(
                                            O[eb] + STOP_K * atr1m[gi])
                                    pos = {"dir": _ed, "trig": gi,
                                           "eb": eb,
                                           "ep": float(O[eb]),
                                           "stop": stop,
                                           "grey": bool(qcol[gi] == 0),
                                           "src": "CTR", "ctr": True,
                                           "ctr0": True, "csub": "D",
                                           "marker": float(
                                               abs(hist[eb])),
                                           "src_wave": int(
                                               WAVE_ID[_t0])}
                                    dec_pend = None
                                    rev_pend = None
                                ctr_pend = None
                    if pos is None:
                        # (b) 登记/快速支: 与 REV 同点 (衰减信号峰
                        # >= CTR_PEAK_MIN); 深度衰减快速支 (<=50% 峰
                        # + 三根递减) 在背景不允许时当根直接开
                        # (允许归 REVF), 否则登记 ctr_pend。
                        for _wv, _sg in ((1, dec_up.get(gi)),
                                         (-1, dec_dn.get(gi))):
                            if _sg is not None and _sg >= CTR_PEAK_MIN:
                                _ed = -_wv
                                if (abs(hist[gi]) <= 0.5 * _sg
                                        and abs(hist[gi])
                                        < abs(hist[gi - 1])
                                        < abs(hist[gi - 2])
                                        and bg_r[gi] != _ed
                                        and "09:31" <= hm <= "15:54"
                                        and MID_CTR[gi]):
                                    eb = gi + 1
                                    if eb <= i1:
                                        stop = float(
                                            O[eb] - STOP_K * atr1m[gi]) \
                                            if _ed == 1 else float(
                                                O[eb] + STOP_K * atr1m[gi])
                                        pos = {"dir": _ed, "trig": gi,
                                               "eb": eb,
                                               "ep": float(O[eb]),
                                               "stop": stop,
                                               "grey": bool(
                                                   qcol[gi] == 0),
                                               "src": "CTR", "ctr": True,
                                               "ctr0": True, "csub": "DF",
                                               "marker": float(
                                                   abs(hist[eb])),
                                               "src_wave": int(
                                                   WAVE_ID[gi])}
                                        dec_pend = None
                                        rev_pend = None
                                else:
                                    ctr_pend = (gi, abs(hist[gi]), _wv)
                                break
                    if pos is None:
                        # (c) 标准跟随支: 连续两根真色柱 |hist|>=8、
                        # 第二根更长; 严格背景没站到波方向 -> 逆势
                        # 开 (站到了归 STD 路); 带同波递进门。
                        _s = qcol[gi]
                        _f_ev = (_s != 0 and gi - 1 >= i0
                                 and qcol[gi - 1] == _s
                                 and abs(hist[gi - 1]) >= 8
                                 and abs(hist[gi]) > abs(hist[gi - 1])
                                 and bg[gi] != _s
                                 and "09:31" <= hm <= "15:54"
                                 and MID_CTR[gi])
                        if _f_ev and _fw_ok(gi):
                            eb = gi + 1
                            if eb <= i1:
                                stop = float(
                                    O[eb] - STOP_K * atr1m[gi]) \
                                    if _s == 1 else float(
                                        O[eb] + STOP_K * atr1m[gi])
                                pos = {"dir": _s, "trig": gi, "eb": eb,
                                       "ep": float(O[eb]), "stop": stop,
                                       "grey": False, "src": "CTR",
                                       "ctr": True, "ctr0": True,
                                       "csub": "F",
                                       "marker": float(abs(hist[eb])),
                                       "src_wave": int(WAVE_ID[gi])}
                                _FW_ANCHOR[int(WAVE_ID[gi])] = \
                                    abs(hist[gi])
                                dec_pend = None
                                rev_pend = None
                                ctr_pend = None
            if pos is not None:  # 日终兜底
                gi = i1
                trades.append({**pos, "xb": gi, "xp": float(C[gi]),
                               "rsn": "EOD",
                               "r": roi(pos["dir"], pos["ep"], float(C[gi]))})
        comp = 1.0
        for t in trades:
            comp *= (1 + t["r"] / 100.0)
        for t in trades:
            if t["rsn"] in ("SL", "EOD"):
                t["xs"] = t["xb"]
            elif abs(t["xp"] - float(O[t["xb"]])) < 1e-9:
                t["xs"] = t["xb"] - 1
            else:
                t["xs"] = t["xb"]

        def _arr(a, rnd=True):
            out = []
            for i in ii:
                v = a[i]
                if v is None or (isinstance(v, float) and np.isnan(v)):
                    out.append(None)
                elif rnd:
                    out.append(round(float(v), 2))
                else:
                    out.append(int(v))
            return out

        out_days[d] = {
            "bars": [[HM[i], float(O[i]), float(H[i]), float(L[i]),
                      float(C[i])] for i in ii],
            "ssl_mah": _arr(ssl1_mah),
            "ssl_mal": _arr(ssl1_mal),
            "ssl_st": _arr(ssl1_st, rnd=False),
            "hama": _arr(hama1_line),
            "hama_col": _arr(hama1_col, rnd=False),
            "hama3": _arr(hama3_at, rnd=False),
            "hama3_line": _arr(hama3_line_at),
            "hama3_shade": _arr(hama3_shade_at, rnd=False),
            "hama_shade": _arr(hama1_shade, rnd=False),
            "bg": _arr((bg_e if BG3_SOFT else bg), rnd=False),
            "hist": _arr(hist),
            "qcol": _arr(qcol, rnd=False),
            "trades": [{k: (int(v) - int(i0)
                            if k in ("trig", "trig0", "eb", "xb", "xs")
                            else v)
                        for k, v in t.items() if k != "src_wave"}
                       for t in trades],
            "events": [[int(_g) - int(i0), _x] for _g, _x in events],
            "comp": round((comp - 1) * 100, 2),
            "sum": round(sum(t["r"] for t in trades), 2),
            "n": len(trades),
        }
    return out_days


def day_payload(day_str, today_bars=None):
    """当日 hama 载荷 (返回 (payload, err))。全帧一遍跑、按日切片,
    备忘在进程内 (同帧同日不重跑)。"""
    try:
        key = (day_str, today_bars is None)
        if key not in _MEMO:
            frame = _load_frame(day_str, today_bars=today_bars)
            _MEMO.clear()
            _MEMO[key] = _run(frame)
        days = _MEMO[key]
        if day_str not in days:
            return None, f"hama 无该日数据: {day_str}"
        return days[day_str], None
    except Exception as e:  # noqa: BLE001 - gen 侧据此标注该节失败
        import traceback
        traceback.print_exc()
        return None, f"{type(e).__name__}: {e}"


# ================= 节渲染 (研究 build_html.py 单日版) =================

_SECTION_CSS = """
#hamaSec{max-width:1150px;margin:14px auto;padding:0 8px;font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;color:#111}
#hamaSec .hamaTitle{font-size:15px;font-weight:bold;margin:2px 0 4px}
#hamaInfo{font-size:13px;margin:6px 0;line-height:1.5}
#hamaTrades{font-size:12.5px;margin-top:6px;line-height:1.65;color:#222}
#hamaCh{width:100%;height:640px}
#hamaSec .leg{font-size:12px;color:#555}
"""

_RENDER_JS = """
(function(){
  var dd = HAMA_DAY;
  var chart = echarts.init(document.getElementById('hamaCh'));
  var n = dd.bars.length;
  var xs = dd.bars.map(function(b){ return b[0]; });
  var close = dd.bars.map(function(b){ return b[4]; });
  var areas = [];
  dd.trades.forEach(function(t){ var al = Math.min(0.40, 0.05 + Math.abs(t.r) * 0.0075).toFixed(2);
    areas.push([{xAxis: t.eb, itemStyle: {color: t.r >= 0 ? 'rgba(22,163,74,' + al + ')' : 'rgba(220,38,38,' + al + ')'}}, {xAxis: t.xb}]); });
  var marks = [], xmarks = [];
  dd.trades.forEach(function(t){
    var isRev = t.src === 'REV' || t.src === 'REVF';
    var isCtr = !!t.ctr0;
    marks.push({coord: [t.trig, close[t.trig]], symbol: 'circle', symbolSize: 17,
      itemStyle: {color: t.dir === 1 ? '#16a34a' : '#dc2626', borderColor: isCtr ? '#3b82f6' : (isRev ? '#111' : '#fff'), borderWidth: isCtr ? 3 : (isRev ? 2.6 : 1.2)},
      label: {show: true, formatter: t.dir === 1 ? 'C' : 'P', color: '#fff', fontSize: 10, fontWeight: 700}});
    xmarks.push({coord: [t.xs, close[t.xs]], symbol: 'circle', symbolSize: 14,
      itemStyle: {color: '#6b7280', borderColor: '#fff', borderWidth: 1.2},
      label: {show: true, formatter: '平', color: '#fff', fontSize: 9}});
  });
  var qmarks = [], qxmarks = [];
  dd.trades.forEach(function(t){
    var isRevQ = t.src === 'REV' || t.src === 'REVF';
    var isCtrQ = !!t.ctr0;
    qmarks.push({coord: [t.trig, dd.hist[t.trig]], symbol: 'circle', symbolSize: 15,
      itemStyle: {color: t.dir === 1 ? '#16a34a' : '#dc2626', borderColor: isCtrQ ? '#3b82f6' : (isRevQ ? '#111' : '#fff'), borderWidth: isCtrQ ? 3 : (isRevQ ? 2.4 : 1.2)},
      label: {show: true, formatter: t.dir === 1 ? 'C' : 'P', color: '#fff', fontSize: 9, fontWeight: 700}});
    qxmarks.push({coord: [t.xs, dd.hist[t.xs]], symbol: 'circle', symbolSize: 12,
      itemStyle: {color: '#6b7280', borderColor: '#fff', borderWidth: 1.2},
      label: {show: true, formatter: '平', color: '#fff', fontSize: 8}});
  });
  function chanSeries(mahK, malK, stK, width, fillA) {
    var out = [];
    var cols = {1: '#089981', '-1': '#f23645', 0: '#9ca3af'};
    var s0 = 0;
    var rns = [];
    for (var i = 1; i <= n; i++) {
      if (i === n || dd[stK][i] !== dd[stK][s0]) { rns.push([s0, i - 1, dd[stK][s0]]); s0 = i; }
    }
    rns.forEach(function(run){ var a = run[0], b = run[1], sv = run[2];
      var ext = Math.min(b + 1, n - 1);
      var dH = dd.bars.map(function(b2, i){ return (i >= a && i <= ext) ? dd[mahK][i] : null; });
      var dL = dd.bars.map(function(b2, i){ return (i >= a && i <= ext) ? dd[malK][i] : null; });
      out.push({type: 'line', data: dH, symbol: 'none', lineStyle: {width: width, color: cols[sv]}, itemStyle: {color: cols[sv]}, xAxisIndex: 0, yAxisIndex: 0, silent: true});
      out.push({type: 'line', data: dL, symbol: 'none', lineStyle: {width: width, color: cols[sv]}, itemStyle: {color: cols[sv]}, xAxisIndex: 0, yAxisIndex: 0, silent: true});
      if (fillA > 0 && sv !== 0) {
        var base = dd.bars.map(function(b2, i){ return (i >= a && i <= b) ? dd[malK][i] : null; });
        var delt = dd.bars.map(function(b2, i){ return (i >= a && i <= b && dd[mahK][i] !== null) ? +(dd[mahK][i] - dd[malK][i]).toFixed(2) : null; });
        var stk = 'f' + mahK + a;
        out.push({type: 'line', data: base, stack: stk, symbol: 'none', lineStyle: {width: 0}, xAxisIndex: 0, yAxisIndex: 0, silent: true});
        out.push({type: 'line', data: delt, stack: stk, symbol: 'none', lineStyle: {width: 0}, areaStyle: {color: sv === 1 ? 'rgba(8,153,129,0.10)' : 'rgba(242,54,69,0.10)'}, xAxisIndex: 0, yAxisIndex: 0, silent: true});
      }
    });
    return out;
  }
  var sslSeries = chanSeries('ssl_mah', 'ssl_mal', 'ssl_st', 1.6, 0.10);
  var SHADE_C = {0: '#facc15', 1: '#66d95e', 2: '#2fbf2f', 3: '#00b300', '-1': '#ff8c42', '-2': '#ff4020', '-3': '#ff0000'};
  var hamaSeries = [];
  function pushHama(lineK, shadeK, width, nm) {
    var s = 0;
    var shadeRuns = [];
    for (var i = 1; i <= n; i++) {
      if (i === n || dd[shadeK][i] !== dd[shadeK][s]) { shadeRuns.push([s, i - 1, dd[shadeK][s]]); s = i; }
    }
    shadeRuns.forEach(function(run){ var a = run[0], b = run[1], sv = run[2];
      var data = dd.bars.map(function(b2, i){ return (i >= a && i <= Math.min(b + 1, n - 1)) ? dd[lineK][i] : null; });
      hamaSeries.push({name: nm, type: 'line', data: data, symbol: 'none',
        lineStyle: {width: width, color: SHADE_C[sv]}, itemStyle: {color: SHADE_C[sv]},
        xAxisIndex: 0, yAxisIndex: 0, silent: true});
    });
  }
  pushHama('hama', 'hama_shade', 2.6, 'HAMA');
  pushHama('hama3_line', 'hama3_shade', 1.6, 'HAMA3');
  var qqe = dd.bars.map(function(b, i){ return {value: dd.hist[i], itemStyle: {color: dd.qcol[i] === 1 ? '#2563eb' : (dd.qcol[i] === -1 ? '#dc2626' : '#9ca3af')}}; });
  var RSN_CN = {DECAY: '衰减出场', DECAYF: '衰减加速出场', CTRX: '逆势衰减出场', D4: '连减4根出场', GREY: 'QQE转灰', FLIP: '新波反向', HB: '回补门槛平仓', BG: '背景破', SL: '止损', EOD: '日终平仓'};
  var ev = {};
  dd.trades.forEach(function(t){
    var srcTxt = t.src === 'REV' ? '衰减trigger·等确认' : (t.src === 'REVF' ? '衰减trigger·加速' : (t.src === 'HB' ? '回补进场' : (t.src === 'CTR' ? '逆势·' + (t.csub === 'F' ? '标准跟随' : (t.csub === 'DF' ? '衰减快速' : '衰减翻仓')) : '顺势凑齐')));
    (ev[t.trig] = ev[t.trig] || []).push('开' + (t.dir === 1 ? ' Call' : ' Put') + ' · ' + srcTxt);
    (ev[t.xs] = ev[t.xs] || []).push('平仓 · ' + (RSN_CN[t.rsn] || t.rsn) + ' · 本笔 ' + (t.r >= 0 ? '+' : '') + t.r.toFixed(2) + '%' + (t.conv ? ' · 中途转顺势单' : ''));
  });
  chart.setOption({
    animation: false,
    axisPointer: {link: [{xAxisIndex: 'all'}]},
    tooltip: {trigger: 'axis', axisPointer: {type: 'cross'},
      formatter: function(ps){
        var i = ps[0].dataIndex;
        var col = dd.qcol[i] === 1 ? '蓝' : (dd.qcol[i] === -1 ? '红' : '灰');
        var bg = dd.bg[i] === 1 ? '多头背景' : (dd.bg[i] === -1 ? '空头背景' : '无背景');
        return xs[i] + ' 收 ' + close[i].toFixed(2) + '<br>SSL ' + (dd.ssl_st[i] === 1 ? '绿' : (dd.ssl_st[i] === -1 ? '红' : '-')) +
          ' · HAMA ' + (dd.hama_col[i] === 1 ? '绿' : (dd.hama_col[i] === -1 ? '红' : '黄')) +
          ' · HAMA3 ' + (dd.hama3[i] === 1 ? '绿' : (dd.hama3[i] === -1 ? '红' : '黄')) +
          '<br>QQE ' + dd.hist[i].toFixed(1) + ' ' + col + '<br>' + bg +
          (ev[i] ? '<br><b>' + ev[i].join('</b><br><b>') + '</b>' : '');
      }},
    grid: [{left: 52, right: 14, top: 26, height: '62%'}, {left: 52, right: 14, top: '74%', height: '18%'}],
    xAxis: [{type: 'category', data: xs, gridIndex: 0, axisLabel: {interval: 29}},
            {type: 'category', data: xs, gridIndex: 1, axisLabel: {show: false}, tooltip: {show: true}}],
    yAxis: [{scale: true, gridIndex: 0}, {gridIndex: 1, splitLine: {show: false}}],
    dataZoom: [{type: 'inside', xAxisIndex: [0, 1]}],
    series: [
      {name: 'SPY', type: 'line', data: close, symbol: 'none', lineStyle: {width: 1.4, color: '#111'}, xAxisIndex: 0, yAxisIndex: 0,
       markArea: {silent: true, data: areas}, markPoint: {data: marks.concat(xmarks), tooltip: {show: false}}}
    ].concat(sslSeries).concat(hamaSeries).concat([
      {name: 'QQE', type: 'bar', data: qqe, xAxisIndex: 1, yAxisIndex: 1,
       markPoint: {data: qmarks.concat(qxmarks), tooltip: {show: false}}}
    ])
  }, true);
  var win = dd.trades.filter(function(t){ return t.r > 0; }).length;
  document.getElementById('hamaInfo').innerHTML =
    '<b>' + HAMA_DATE + '</b> | ' + dd.n + ' 单 · 胜 ' + win + ' 负 ' + (dd.n - win) +
    ' · 当日收益率 <b>' + (dd.sum >= 0 ? '+' : '') + dd.sum.toFixed(2) + '%</b> (逐笔相加, 非复利)' +
    ' <span class="leg">| 底纹: 每笔单开仓→平仓段, 绿=赚 红=亏, 深浅随盈亏幅度 · SSL: SMA200通道, 两线同为状态色 (绿=多头/红=空头) + 中间淡填充 · 粗线=HAMA 1m均线, 细线=HAMA 3m均线 (均 WMA55(hl2), NSDT渐变染色: 翻转根黄, 之后由浅到深) · 圈: 绿C/红P=触发根, 灰平=出场根 (价格图与QQE图同步画) · 黑边圈=QQE衰减trigger反转进场 · 蓝边圈=逆势单 (背景不允许顺势时按QQE信号开) · 进场背景: SSL+HAMA1m同向且HAMA3m不反向 (HAMA3黄色不卡进场) · 结算: 30%杠杆真0DTE口径</span>';
  document.getElementById('hamaTrades').innerHTML = dd.trades.map(function(t){
    return xs[t.trig] + ' 触发 → ' + xs[t.eb] + ' 开' + (t.dir === 1 ? ' Call' : ' Put') + ' @' + t.ep.toFixed(2) +
    ' → ' + xs[t.xb] + ' 平 @' + t.xp.toFixed(2) + ' <b>' + (t.r >= 0 ? '+' : '') + t.r.toFixed(2) + '%</b> (' + t.rsn + (t.src === 'HB' ? ', 回补进场' : (t.src === 'CTR' ? ', 逆势' + (t.csub === 'F' ? '·跟随' : (t.csub === 'DF' ? '·衰减快速' : '·衰减翻仓')) + (t.flipborn ? '·同根翻' : '') : (t.src && t.src !== 'STD' ? ', 衰减trigger' + (t.src === 'REVF' ? '·加速' : '') : ''))) + (t.conv ? ', 中途转顺势单' : '') + ', 止损位 ' + t.stop.toFixed(2) + ')';
  }).join('<br>');
})();
"""


def render_section(day_str, payload, err=None):
    """hama 节整块 HTML (自包含 div + script, 插在 </body> 前)。
    payload 为 None 时按 gen 惯例给可见错误标注 (不静默回退)。"""
    if payload is None:
        return ('<div id="hamaSec"><style>' + _SECTION_CSS + '</style>'
                '<div class="hamaTitle">hama</div>'
                '<div style="color:#dc2626;font-size:13px">hama 计算失败: '
                + str(err) + '</div></div>')
    data = json.dumps(payload, ensure_ascii=False,
                      default=lambda x: float(x)
                      if isinstance(x, np.floating) else int(x))
    return ('<div id="hamaSec"><style>' + _SECTION_CSS + '</style>'
            '<div class="hamaTitle">hama</div>'
            '<div id="hamaInfo"></div>'
            '<div id="hamaCh"></div>'
            '<div id="hamaTrades"></div></div>'
            '<script>const HAMA_DATE = ' + json.dumps(day_str) + ';\n'
            'const HAMA_DAY = ' + data + ';\n'
            + _RENDER_JS + '</script>')
