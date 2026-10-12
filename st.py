"""Supertrend (TradingView ta.supertrend-faithful) + sanity check."""
import pandas as pd
import numpy as np
from qqe_mod import rma


def supertrend(high, low, close, atr_period=10, multiplier=3.0):
    """Returns dict(line, direction, bull). bull=True -> uptrend ('buy' regime).
    Mirrors Pine v5 ta.supertrend: direction -1 = bull (line=lowerBand),
    +1 = bear (line=upperBand)."""
    high = np.asarray(high, dtype=float)
    low = np.asarray(low, dtype=float)
    close = np.asarray(close, dtype=float)
    n = len(close)
    tr = np.empty(n)
    tr[0] = high[0] - low[0]
    tr[1:] = np.maximum.reduce([high[1:] - low[1:],
                                np.abs(high[1:] - close[:-1]),
                                np.abs(low[1:] - close[:-1])])
    atr = rma(tr, atr_period)
    hl2 = (high + low) / 2.0
    upper = hl2 + multiplier * atr
    lower = hl2 - multiplier * atr

    f_upper = np.empty(n)
    f_lower = np.empty(n)
    direction = np.empty(n)
    st = np.empty(n)

    f_upper[0] = upper[0]
    f_lower[0] = lower[0]
    direction[0] = 1
    st[0] = f_upper[0]
    for i in range(1, n):
        f_lower[i] = lower[i] if (lower[i] > f_lower[i - 1]
                                 or close[i - 1] < f_lower[i - 1]) else f_lower[i - 1]
        f_upper[i] = upper[i] if (upper[i] < f_upper[i - 1]
                                 or close[i - 1] > f_upper[i - 1]) else f_upper[i - 1]
        if st[i - 1] == f_upper[i - 1]:
            direction[i] = -1 if close[i] > f_upper[i] else 1
        else:
            direction[i] = 1 if close[i] < f_lower[i] else -1
        st[i] = f_lower[i] if direction[i] == -1 else f_upper[i]

    return {"line": st, "direction": direction, "bull": direction == -1}


if __name__ == "__main__":
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    df = pd.read_csv("/home/hatch/workspace/qqe_backtest/spy_3m.csv")
    df["time"] = pd.to_datetime(df["time"], utc=True).dt.tz_convert("America/New_York")
    df["tod"] = df["time"].dt.strftime("%H:%M")
    df = df[(df["tod"] >= "09:30") & (df["tod"] < "16:00")].reset_index(drop=True)
    res = supertrend(df["high"].values, df["low"].values, df["close"].values)
    df["st"] = res["line"]
    df["bull"] = res["bull"]
    print("bull%% = %.1f%%" % (df["bull"].mean() * 100))
    # flips per day
    df["date"] = df["time"].dt.date
    flips = df.groupby("date")["bull"].apply(lambda s: (s != s.shift()).sum() - 1)
    print("flips/day: mean=%.1f median=%.1f" % (flips.mean(), flips.median()))
    # sanity: when bull, price should generally be above the line
    chk = df.iloc[500:]
    print("bull & close>line: %.1f%% | bear & close<line: %.1f%%" % (
        ((chk["bull"]) & (chk["close"] > chk["st"])).sum() / chk["bull"].sum() * 100,
        ((~chk["bull"]) & (chk["close"] < chk["st"])).sum() / (~chk["bull"]).sum() * 100))

    # plot one day
    d = df["date"].iloc[2000]
    w = df[df["date"] == d]
    fig, ax = plt.subplots(figsize=(14, 6))
    ax.plot(w["time"], w["close"], color="black", lw=1, label="SPY 3m")
    bull_line = np.where(w["bull"], w["st"], np.nan)
    bear_line = np.where(~w["bull"], w["st"], np.nan)
    ax.plot(w["time"], bull_line, color="green", lw=1.5, label="ST bull")
    ax.plot(w["time"], bear_line, color="red", lw=1.5, label="ST bear")
    ax.legend()
    ax.set_title(f"Supertrend check {d}")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig("/home/hatch/workspace/qqe_backtest/st_check.png", dpi=100)
    print("plot saved")
