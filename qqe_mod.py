"""QQE MOD (Mihkel00) faithful Python port of the original Pine Script v4.

Original Pine logic (from https://github.com/ziminl/qqe-mod README):
  - Two QQE passes: primary (RSI 6, SF 5, factor 3.0) + Bollinger(50, 0.35)
    on (primary_trail - 50); secondary (RSI 6, SF 5, factor 1.61).
  - Histogram value = secondary RsiMa - 50
    - blue ("QQE Up"):   hist >  3 and (primary RsiMa - 50) > bb_upper
    - red  ("QQE Down"): hist < -3 and (primary RsiMa - 50) < bb_lower
    - silver (neutral):  |hist| > 3 but no primary confirmation
    - hidden:            |hist| <= 3
Pine semantics respected: rma-seeded Wilder RSI, ema seeded with first
non-na value, na comparisons -> False, population stdev (Pine default).
"""
import numpy as np
import pandas as pd


def rma(x, n):
    """Wilder's smoothing, Pine rma(): sum := (x + (n-1)*nz(sum[1])) / n ; sum[0]=0."""
    x = np.asarray(x, dtype=float)
    out = np.empty(len(x))
    prev = 0.0
    for i, v in enumerate(x):
        v = 0.0 if np.isnan(v) else v
        prev = (v + (n - 1) * prev) / n
        out[i] = prev
    return out


def ema(x, n):
    """Pine ema(): alpha*x + (1-alpha)*nz(ema[1], x) -> seeds with first non-na."""
    x = np.asarray(x, dtype=float)
    alpha = 2.0 / (n + 1)
    out = np.full(len(x), np.nan)
    prev = np.nan
    for i, v in enumerate(x):
        if np.isnan(v):
            continue
        prev = v if np.isnan(prev) else alpha * v + (1 - alpha) * prev
        out[i] = prev
    return out


def pine_rsi(src, length):
    src = np.asarray(src, dtype=float)
    n = len(src)
    chg = np.full(n, np.nan)
    chg[1:] = src[1:] - src[:-1]
    u = np.where(chg > 0, chg, 0.0)
    d = np.where(chg < 0, -chg, 0.0)
    u[0] = np.nan
    d[0] = np.nan
    avg_u = rma(u, length)
    avg_d = rma(d, length)
    rs = np.full(n, np.nan)
    nz = avg_d != 0
    rs[nz] = avg_u[nz] / avg_d[nz]
    rsi = 100.0 - 100.0 / (1.0 + rs)
    flat_up = (avg_d == 0) & (avg_u > 0)
    rsi[flat_up] = 100.0
    return rsi


def _qqe_pass(src, rsi_len, sf, qqe_factor):
    src = np.asarray(src, dtype=float)
    n = len(src)
    wilders = rsi_len * 2 - 1

    rsi = pine_rsi(src, rsi_len)
    rsi_ma = ema(rsi, sf)

    atr_rsi = np.full(n, np.nan)
    atr_rsi[1:] = np.abs(rsi_ma[1:] - rsi_ma[:-1])
    ma_atr_rsi = ema(atr_rsi, wilders)
    dar = ema(ma_atr_rsi, wilders) * qqe_factor

    rsidx = rsi_ma
    newlong = rsidx - dar
    newshort = rsidx + dar

    longband = np.full(n, np.nan)
    shortband = np.full(n, np.nan)
    trend = np.ones(n)  # nz(trend[1], 1)

    for i in range(n):
        nl, ns, ri = newlong[i], newshort[i], rsidx[i]
        if np.isnan(nl) or np.isnan(ns) or np.isnan(ri):
            if i > 0:
                longband[i] = longband[i - 1]
                shortband[i] = shortband[i - 1]
                trend[i] = trend[i - 1]
            continue
        if i == 0:
            longband[i] = nl
            shortband[i] = ns
        else:
            lb_prev, sb_prev = longband[i - 1], shortband[i - 1]
            ri_prev = rsidx[i - 1]
            if ri_prev > lb_prev and ri > lb_prev:
                longband[i] = lb_prev if lb_prev > nl else nl  # max, na-safe
            else:
                longband[i] = nl
            if ri_prev < sb_prev and ri < sb_prev:
                shortband[i] = sb_prev if sb_prev < ns else ns  # min, na-safe
            else:
                shortband[i] = ns
        if i >= 2:
            # cross(RSIndex, shortband[1]) -> trend up
            a0, a1 = rsidx[i - 1], rsidx[i]
            b0, b1 = shortband[i - 2], shortband[i - 1]
            cross_up = (a0 < b0 and a1 > b1) or (a0 > b0 and a1 < b1)
            # cross(longband[1], RSIndex) -> trend down
            c0, c1 = longband[i - 2], longband[i - 1]
            d0, d1 = rsidx[i - 1], rsidx[i]
            cross_dn = (c0 < d0 and c1 > d1) or (c0 > d0 and c1 < d1)
            if cross_up:
                trend[i] = 1
            elif cross_dn:
                trend[i] = -1
            else:
                trend[i] = trend[i - 1]
        elif i > 0:
            trend[i] = trend[i - 1]

    trail = np.where(trend == 1, longband, shortband)
    return {"rsi_ma": rsi_ma, "trail": trail, "trend": trend}


def qqe_mod(close, rsi_len=6, sf=5, qqe_factor=3.0,
            bb_len=50, bb_mult=0.35,
            rsi_len2=6, sf2=5, qqe_factor2=1.61, thresh=3.0):
    """Returns dict with hist, up/down/neutral states (Pine-faithful)."""
    close = np.asarray(close, dtype=float)
    n = len(close)
    primary = _qqe_pass(close, rsi_len, sf, qqe_factor)
    secondary = _qqe_pass(close, rsi_len2, sf2, qqe_factor2)

    trail_m50 = primary["trail"] - 50.0
    s = pd.Series(trail_m50)
    basis = s.rolling(bb_len, min_periods=bb_len).mean().values
    dev = bb_mult * s.rolling(bb_len, min_periods=bb_len).std(ddof=0).values
    upper = basis + dev
    lower = basis - dev

    hist = secondary["rsi_ma"] - 50.0
    prim_m50 = primary["rsi_ma"] - 50.0

    up_state = (hist > thresh) & (prim_m50 > upper)
    down_state = (hist < -thresh) & (prim_m50 < lower)
    neutral_state = (~up_state) & (~down_state) & (np.abs(hist) > thresh)

    return {
        "hist": hist,
        "up": up_state,
        "down": down_state,
        "neutral": neutral_state,
        "upper": upper,
        "lower": lower,
        "primary_trail": primary["trail"],
        "secondary_trail": secondary["trail"],
    }
