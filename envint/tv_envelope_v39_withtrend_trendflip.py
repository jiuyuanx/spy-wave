#!/usr/bin/env python3
"""TradingView Pine v3.9 变体 (趋势入场-翻仓版): 平移通道 + 20% 边缘区, 但趋势政策改回
"With trend only" (v3.7 政策: 强趋势时只禁逆势、顺势可入)。
用于隔离 Block-all 政策本身的效果 (其余与 tv_envelope_v39.py 完全一致)。
"""
import sys, os, pickle
import numpy as np
import pandas as pd

# ---------------- 参数 (Pine inputs 原值) ----------------
GAUSS_LEN = 30
GAUSS_BW = 3.5
ATR_LEN = 12
MULT = 1.05
SLOPE_LEN = 7
ASYM_SHIFT = 1.35
FAST_EMA_LEN = 5
MOM_LOOKBACK = 15
MOM_SENS = 0.55
MAX_SHIFT_ATR = 0.50
EDGE_PCT = 20.0
MAX_OUTSIDE = 0.55
ER_LEN = 17
ER_TH = 0.35
DI_LEN = 8
ADX_SMOOTH = 12
ADX_TH = 30  # envelope_adx30 正式版 (2026-11-07): 原 20 -> 30
TAKEOVER_SLOPE = 0.13
SL_ATR = float(os.environ.get("SL_ATR", "1.5"))  # Pine 原值 1.5; 环境变量可覆盖做单参数测试
TF_MIN = 3  # 3m; 1m 时改 1


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


def compute_indicators(df):
    """df: 3m/1m bars (t/open/high/low/close). 返回 dict of arrays."""
    o = df["open"].values.astype(float)
    h = df["high"].values.astype(float)
    l = df["low"].values.astype(float)
    c = df["close"].values.astype(float)
    n = len(df)
    # TR (正常, DMI 用)
    prev_c = np.empty(n); prev_c[0] = np.nan; prev_c[1:] = c[:-1]
    tr = np.maximum(h - l, np.maximum(np.abs(h - prev_c), np.abs(l - prev_c)))
    tr[0] = h[0] - l[0]
    # Gap-Adjusted ATR (envelope_adx30 正式版 2026-11-07): 每日首根 RTH bar (09:30)
    # 的 TR = high-low, 不含隔夜跳空; 其余 bar 用正常 TR。DMI (DI/ADX) 始终用正常 TR。
    times = df["t"]
    sm = times.dt.hour.values * 60 + times.dt.minute.values
    dates = times.dt.strftime("%Y-%m-%d").values
    in_rth = (sm >= 570) & (sm < 960)
    first_rth = np.zeros(n, dtype=bool)
    for i in range(n):
        if not in_rth[i]:
            continue
        if i == 0 or (not in_rth[i - 1]) or (dates[i] != dates[i - 1]):
            first_rth[i] = True
    tr_atr = np.where(first_rth, h - l, tr)
    atr = rma(tr_atr, ATR_LEN)
    # Gaussian mid (causal)
    w = np.exp(-0.5 * (np.arange(GAUSS_LEN) / GAUSS_BW) ** 2)
    mid = np.full(n, np.nan)
    for i in range(n):
        k = min(i + 1, GAUSS_LEN)
        # Pine close[i] = i根前, 最新根权重 w[0]=1 最大; 原代码把权重配反了
        # (最老根得最大权重, 有效滞后约20根), 在此纠正为最新根取 w[0]
        mid[i] = np.sum(c[i - k + 1:i + 1] * w[:k][::-1]) / np.sum(w[:k])
    slope = np.full(n, np.nan)
    slope[SLOPE_LEN:] = (mid[SLOPE_LEN:] - mid[:-SLOPE_LEN]) / SLOPE_LEN
    # v3.9 平移: EMA 动量 -> squash -> clamp 到 ±0.60*ATR, 通道对称于 mid+shift
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
    upper = mid + shift + width
    lower = mid + shift - width
    chan_mid = (upper + lower) / 2.0
    # ER(10)
    chg = np.abs(np.diff(c, prepend=np.nan))
    chg[0] = 0.0
    er = np.full(n, np.nan)
    for i in range(ER_LEN, n):
        noise = np.sum(chg[i - ER_LEN + 1:i + 1])
        er[i] = abs(c[i] - c[i - ER_LEN]) / noise if noise > 0 else 0.0
    # DMI(10,14)
    prev_h = np.empty(n); prev_h[0] = np.nan; prev_h[1:] = h[:-1]
    prev_l = np.empty(n); prev_l[0] = np.nan; prev_l[1:] = l[:-1]
    up = h - prev_h; dn = prev_l - l
    up[0] = 0.0; dn[0] = 0.0
    pdm = np.where((up > 0) & (up > dn), up, 0.0)
    mdm = np.where((dn > 0) & (dn > up), dn, 0.0)
    r_tr = rma(tr, DI_LEN)
    r_pdm = rma(pdm, DI_LEN)
    r_mdm = rma(mdm, DI_LEN)
    with np.errstate(invalid="ignore", divide="ignore"):
        dip = 100 * r_pdm / r_tr
        dim = 100 * r_mdm / r_tr
        dx = 100 * np.abs(dip - dim) / (dip + dim)
    adx = rma(np.where(np.isnan(dx), 0.0, dx), ADX_SMOOTH)
    # ADX 的 RMA 首值需 DX 全 window 非 na 才有效: 前 (DI_LEN+ADX_SMOOTH-1) 根置 na
    adx[:DI_LEN + ADX_SMOOTH - 1] = np.nan
    return dict(open=o, high=h, low=l, close=c, atr=atr, mid=mid, slope=slope,
                upper=upper, lower=lower, chan_mid=chan_mid, er=er, adx=adx)


def load_bars(tf="3m", start="2026-08-01"):
    pkl = "data_cache/spy_3m.pkl" if tf == "3m" else "data_cache/spy_1m.pkl"
    df = pickle.load(open(pkl, "rb"))
    if tf == "1m":
        # 1m 缓存 t 列是 object 混合: aware 行带时区偏移, naive 行就是美东墙钟
        # (10-22 实测: naive 09:30 close = 页内 1m曲线 09:30 close 766.61 分毫不差;
        # 旧代码 utc=True 把 naive 当 UTC 平移 -4h, 常规过滤后只剩 150 根下午 bar
        # 错标成上午, 10-22 envelope 全错位; 10-30 因是 aware 行未受影响)
        s = df["t"].astype(str)
        aware_mask = s.str.contains(r"[+-]\d{2}:?\d{2}$", regex=True)
        t_aware = pd.to_datetime(df.loc[aware_mask, "t"], utc=True
                                 ).dt.tz_convert("America/New_York").dt.tz_localize(None)
        t_naive = pd.to_datetime(df.loc[~aware_mask, "t"])
        df["t"] = pd.concat([t_aware, t_naive]).sort_index()
        df["t"] = pd.to_datetime(df["t"])
    else:
        df["t"] = pd.to_datetime(df["t"])
    df = df.sort_values("t").reset_index(drop=True)
    df = df[df["t"] >= pd.Timestamp(start)].reset_index(drop=True)
    if tf == "1m":
        # 只留常规时段 09:30-15:59
        mins = df["t"].dt.hour * 60 + df["t"].dt.minute
        df = df[(mins >= 570) & (mins <= 959)].reset_index(drop=True)
    return df

# ---------------- 模拟 (Pine 语义, bar 收盘执行) ----------------
START_DATE = os.environ.get("TV_START", "2026-09-08")
END_DATE = os.environ.get("TV_END", "2026-11-04")

# 变体统计 (simulate() 每次调用重置; trend_entry=False 时 trend_only_entries=0,
# trend_edges 仍计数, 作隔离对照: 两路跑的 edge 数必须一致)
SIM_STATS = dict(trend_edges_long=0, trend_edges_short=0,
                 trend_only_entries=0, trend_flip_entries=0, entry_modes={})


def simulate(df, ind, tf_min, trend_entry=False):
    # 变体统计重置 (每次调用); 上一根的强趋势状态 (上升沿检测用)
    SIM_STATS.clear()
    SIM_STATS.update(dict(trend_edges_long=0, trend_edges_short=0,
                          trend_only_entries=0, trend_flip_entries=0, entry_modes={}))
    prev_strong_up = prev_strong_down = False
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
    c, h, l = ind["close"], ind["high"], ind["low"]
    atr, upper, lower, chan_mid = ind["atr"], ind["upper"], ind["lower"], ind["chan_mid"]
    slope, er, adx = ind["slope"], ind["er"], ind["adx"]
    pos = 0  # 0 flat, +1 long, -1 short
    entry_px = entry_atr = None
    entry_bar = -1
    entry_date = None
    trades = []  # (entry_bar, exit_bar, dir, entry_px, exit_px, reason)

    def close_pos(bar, px, reason):
        nonlocal pos, entry_px, entry_atr, entry_bar, entry_date
        if pos == 0:
            return
        d = "long" if pos == 1 else "short"
        if entry_date is not None and START_DATE <= entry_date <= END_DATE:
            trades.append((entry_bar, bar, d, entry_px, px, reason))
        pos = 0; entry_px = entry_atr = None; entry_bar = -1; entry_date = None

    for i in range(n):
        dstr = dates[i]
        # 09-08 边界: 之前遗留仓位静默丢弃, 不计入
        if dstr >= START_DATE and pos != 0 and (entry_date or "") < START_DATE:
            pos = 0; entry_px = entry_atr = None; entry_bar = -1; entry_date = None
        s = sm[i]
        close_min = s + tf_min
        eod = (close_min >= _day_last[dstr] - 3) and (close_min <= _day_last[dstr])
        # EOD bar 上不许开新仓 (正常日靠 close_min<945 挡，早收日 EOD bar 的 close_min 才 778-781，
        # 不加 not eod 会在最后一根 bar 上开仓、带过周末)。
        in_session = (570 <= s <= 955) and (close_min < 945) and (not eod)
        # 信号
        long_sig = short_sig = False
        if i >= 1 and not (np.isnan(upper[i - 1]) or np.isnan(lower[i - 1])
                            or np.isnan(atr[i]) or np.isnan(chan_mid[i])):
            tU, tL = upper[i - 1], lower[i - 1]
            width = tU - tL
            if width > 0:
                buy_top = tL + width * EDGE_PCT / 100.0
                sell_bot = tU - width * EDGE_PCT / 100.0
                low_touch = (l[i] <= buy_top) and (h[i] >= tL - MAX_OUTSIDE * atr[i])
                up_touch = (h[i] >= sell_bot) and (l[i] <= tU + MAX_OUTSIDE * atr[i])
                ls = low_touch and (c[i] <= chan_mid[i])
                ss = up_touch and (c[i] >= chan_mid[i])
                if not (ls and ss):
                    long_sig = ls and in_session
                    short_sig = ss and in_session
        # 趋势状态 (na -> 封锁进场, 与 Pine na 语义一致)
        ind_ok = not (np.isnan(er[i]) or np.isnan(adx[i])
                      or np.isnan(slope[i]) or np.isnan(atr[i]))
        allow_long = allow_short = False
        strong_down = strong_up = False
        if ind_ok:
            trending = (er[i] >= ER_TH) and (adx[i] >= ADX_TH)
            strong_down = trending and (slope[i] < -TAKEOVER_SLOPE * atr[i])
            strong_up = trending and (slope[i] > TAKEOVER_SLOPE * atr[i])
            # 本变体 "With trend only": 强趋势时只禁逆势、顺势可入 (v3.7 政策)
            # 其余 v3.9 改动 (平移通道、20% 边缘区) 保留; EXIT T 不变
            allow_long = not strong_down
            allow_short = not strong_up
        # 趋势入场 (变体): strongUp/strongDown 上升沿 (edge-triggered)
        # 上升沿 = 本根强趋势为真且前一根为假; 计数只统计 in_session 且窗口内的沿
        # (edge 计数与 trend_entry 开关无关, 作隔离对照: 两路跑的 edge 数必须一致)
        edge_up = strong_up and not prev_strong_up
        edge_down = strong_down and not prev_strong_down
        if in_session and START_DATE <= dstr <= END_DATE:
            if edge_up:
                SIM_STATS["trend_edges_long"] += 1
            if edge_down:
                SIM_STATS["trend_edges_short"] += 1
        trend_long_sig = trend_entry and edge_up and in_session
        trend_short_sig = trend_entry and edge_down and in_session
        # 1) 止损 (入场下一根起)
        if pos != 0 and i > entry_bar:
            if pos == 1 and l[i] <= entry_px - SL_ATR * entry_atr:
                close_pos(i, entry_px - SL_ATR * entry_atr, "SL")
            elif pos == -1 and h[i] >= entry_px + SL_ATR * entry_atr:
                close_pos(i, entry_px + SL_ATR * entry_atr, "SL")
        # 2) 强制平 (regime / EOD)
        force_exit = False
        force_exit_regime = False
        if pos != 0:
            exit_regime = (strong_down and pos == 1) or (strong_up and pos == -1)
            exit_eod = eod
            if exit_regime or exit_eod:
                force_exit = True
                # 翻仓版用: 只有纯 EXIT T (非 EOD) 的 force_exit 才可被趋势信号绕过
                force_exit_regime = bool(exit_regime and not exit_eod)
                close_pos(i, c[i], "EOD" if exit_eod else "EXIT T")
        # 3) 进场 (reverse=true)
        # 翻仓版: 边缘信号与趋势信号 OR 合并 (同向同bar只开一单); 趋势信号须 trend_entry=True.
        # 与只从空仓开的版本唯一区别: 趋势信号可绕过第 2) 步 EXIT T 设的 force_exit,
        # 即上升沿当根若持反向仓, 先由 EXIT T 平掉、再顺势开 = 同bar翻仓; EOD 的 force_exit
        # 不可绕过, 边缘信号也不可绕过 (与基线一致).
        long_bypass = bool(trend_long_sig and force_exit_regime)
        short_bypass = bool(trend_short_sig and force_exit_regime)
        can_long = allow_long and ((not force_exit) or long_bypass) and (long_sig or trend_long_sig) and (pos == 0 or pos == -1)
        can_short = allow_short and ((not force_exit) or short_bypass) and (short_sig or trend_short_sig) and (pos == 0 or pos == 1)
        if can_long:
            if pos == -1:
                close_pos(i, c[i], "FLIP")
            pos = 1; entry_px = c[i]; entry_atr = atr[i]; entry_bar = i; entry_date = dstr
            _mode = "trend" if (trend_long_sig and not long_sig) else "edge"
            SIM_STATS["entry_modes"][i] = _mode
            if _mode == "trend":
                SIM_STATS["trend_only_entries"] += 1
            if long_bypass:
                SIM_STATS["trend_flip_entries"] += 1
        elif can_short:
            if pos == 1:
                close_pos(i, c[i], "FLIP")
            pos = -1; entry_px = c[i]; entry_atr = atr[i]; entry_bar = i; entry_date = dstr
            _mode = "trend" if (trend_short_sig and not short_sig) else "edge"
            SIM_STATS["entry_modes"][i] = _mode
            if _mode == "trend":
                SIM_STATS["trend_only_entries"] += 1
            if short_bypass:
                SIM_STATS["trend_flip_entries"] += 1
        prev_strong_up = strong_up
        prev_strong_down = strong_down
    return trades


# ---------------- 结算 ----------------
def stock_accounting(trades, equity0=100000.0, qty_pct=0.95):
    eq = equity0
    rows = []
    for eb, xb, d, epx, xpx, rs in trades:
        sh = qty_pct * eq / epx
        pnl = sh * (xpx - epx) * (1 if d == "long" else -1)
        eq += pnl
        rows.append(dict(dir=d, entry_px=epx, exit_px=xpx, reason=rs, pnl=pnl))
    wins = sum(1 for r in rows if r["pnl"] > 0)
    gp = sum(r["pnl"] for r in rows if r["pnl"] > 0)
    gl = -sum(r["pnl"] for r in rows if r["pnl"] < 0)
    return dict(n=len(rows), wins=wins, winrate=wins / len(rows) if rows else 0,
                pf=gp / gl if gl > 0 else float("inf"), total=eq - equity0, equity=eq,
                rows=rows)


def options_settlement(trades, dates_by_bar, gamma_roi):
    """标准口径: 每周$100k起, 周内逐笔复利, 跨周重置. 周 = 平仓日所在周一."""
    import datetime as _dt
    weeks = {}
    for eb, xb, d, epx, xpx, rs in trades:
        dstr = dates_by_bar[xb]
        dt = _dt.date.fromisoformat(dstr)
        mon = (dt - _dt.timedelta(days=dt.weekday())).isoformat()
        r = gamma_roi("call" if d == "long" else "put", xpx - epx) / 100.0
        weeks.setdefault(mon, []).append((xb, d, epx, xpx, rs, r))
    wk_rows, tot = [], 0.0
    all_r = []
    for mon in sorted(weeks):
        cap = 100000.0
        for xb, d, epx, xpx, rs, r in weeks[mon]:
            cap *= (1 + r)
            all_r.append((r, cap))
        pnl = cap - 100000.0
        tot += pnl
        wk_rows.append((mon, len(weeks[mon]), round(pnl, 2)))
    # PF 按周内复利后的每单金额算
    dollars = []
    for mon in sorted(weeks):
        cap = 100000.0
        for xb, d, epx, xpx, rs, r in weeks[mon]:
            dollars.append(cap * r)
            cap *= (1 + r)
    wins = sum(1 for x in dollars if x > 0)
    gp = sum(x for x in dollars if x > 0)
    gl = -sum(x for x in dollars if x < 0)
    return dict(n=len(dollars), wins=wins, winrate=wins / len(dollars) if dollars else 0,
                pf=gp / gl if gl > 0 else float("inf"), total=tot, weeks=wk_rows)


def main():
    import os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from settle_official import gamma_roi
    tf = sys.argv[1] if len(sys.argv) > 1 else "3m"
    tf_min = 3 if tf == "3m" else 1
    df = load_bars(tf, start=os.environ.get("TV_WARMUP", "2026-08-01"))
    ind = compute_indicators(df)
    trades = simulate(df, ind, tf_min)
    dates_by_bar = df["t"].dt.strftime("%Y-%m-%d").values
    st = stock_accounting(trades)
    op = options_settlement(trades, dates_by_bar, gamma_roi)
    print(f"=== TV v3.9 复现 ({tf}, {START_DATE}→{END_DATE}) ===")
    print(f"正股版: {st['n']}单 胜{st['wins']} "
          f"胜率{st['winrate'] * 100:.2f}% PF{st['pf']:.3f} 总盈亏${st['total']:,.2f}")
    print(f"期权版(同仓位, Gamma结算, 周$100k): {op['n']}单 胜{op['wins']} "
          f"胜率{op['winrate'] * 100:.2f}% PF{op['pf']:.3f} 总计${op['total']:,.2f}")
    print("周表 (周一, 单数, 周盈亏$):")
    if len(op["weeks"]) <= 12:
        for mon, cnt, pnl in op["weeks"]:
            print(f"  {mon} {cnt}单 ${pnl:,.2f}")
    else:
        from collections import defaultdict as _dd
        _m = _dd(lambda: [0, 0.0])
        for mon, cnt, pnl in op["weeks"]:
            _m[mon[:7]][0] += cnt
            _m[mon[:7]][1] += pnl
        print("月汇总 (按周一所在月, 单数, 月盈亏$):")
        for k in sorted(_m):
            print(f"  {k} {_m[k][0]}单 ${_m[k][1]:,.2f}")
    # 退出原因分布
    from collections import Counter
    print("退出原因:", dict(Counter(r[5] for r in trades)))


if __name__ == "__main__":
    main()
