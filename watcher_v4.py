#!/usr/bin/env python3
"""
Alpaca 3-min SPY watcher V4 — TRUE STATELESS.

Architecture (user-confirmed 2026-10-11):
  v3's problem: re-implemented backtest logic, fixed 20 bugs, still 94.3%. Shit-mountain.
  v4's approach: import signal logic DIRECTLY from backtest (signal_engine.py,
  extracted from gen_week_k3_clean.py). 100% identical signals. No re-implementation.

Every 30s cycle:
  1. Fetch bars (warmup + live 1m -> 3m)
  2. run_signals() on complete bars -> actions (same as backtest)
  3. Derive expected position from actions
  4. Read actual position from API
  5. If different -> place orders to sync
  6. Manual close detection: API flat but signals say holding -> already synced (do nothing,
     signals will re-enter on next signal)

No derive_entry_bar. No _MEM. No per-variable derivation. Just run the backtest.
"""
import os, sys, json, time, urllib.request, urllib.parse
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import pandas as pd
import numpy as np

sys.path.insert(0, "/home/hatch/workspace/qqe_backtest")
sys.path.insert(0, "/home/hatch/workspace/alpaca_watcher")
from signal_engine import run_signals
from trader import (get_expiration, get_atm_contract, get_quote, calc_qty,
                    place_order, get_positions, get_account_equity,
                    get_options_buying_power, api, PAPER,
                    STOP_PCT,
                    EOD_FLAT_TIME, TRADING_ENABLED)

ET = ZoneInfo("America/New_York")
KEY = os.environ["ALPACA_KEY"]
SECRET = os.environ["ALPACA_SECRET"]
DATA = "https://data.alpaca.markets/v2"
SYMBOL = "SPY"
WDIR = "/home/hatch/workspace/alpaca_watcher"
LOG_F = os.path.join(WDIR, "watcher_v4.log")
SIG_F = os.path.join(WDIR, "signals_v4.log")

# --- v4 轻量节流状态 (非交易状态，可丢；重启后按信号同步) ---
_last_pct_check_bar = None   # Bug 4: 上次做止损/止盈检查的 3m bar idx（只在新 bar 检查）
_last_open_acted_bar = None  # Bug 3: 上次执行开仓的信号 entry bar（防手动平仓后 30s 内重开）


def log(msg):
    ts = datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_F, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def api(path, params, retries=3):
    """带重试的 data API 请求 (指数退避 1s/2s/4s)。"""
    q = urllib.parse.urlencode(params)
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                DATA + path + "?" + q,
                headers={"APCA-API-KEY-ID": KEY, "APCA-API-SECRET-KEY": SECRET},
            )
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.load(r)
        except Exception as e:
            last_err = e
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    raise last_err


def fetch_1m():
    """从 Alpaca 拉今日 09:30 起全部 1 分钟线 (实时)。

    Bugfix 2026-10-12: 之前用 limit=100 滑动窗口，11:10 之后早盘 1m 缺失，
    to_3m 合成的早盘 3m bar n1m<3，经 drop_duplicates(keep=last) 覆盖掉
    warmup 的完整 bar，导致 n_complete 归零、信号冻结。现改按今日 09:30
    为起点拉全天 (start 参数)，参考 v3 load_history。
    """
    today = datetime.now(ET).date()
    start_et = datetime(today.year, today.month, today.day, 9, 30, tzinfo=ET)
    start_utc = start_et.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")
    params = {"symbols": SYMBOL, "timeframe": "1Min",
              "start": start_utc, "limit": 10000, "feed": "iex", "sort": "asc"}
    last_err = None
    for attempt in range(5):
        try:
            data = api("/stocks/bars", params, retries=2)
            bars = data.get("bars", {}).get(SYMBOL, [])
            df = pd.DataFrame(bars)
            if df.empty:
                return df
            df["t"] = pd.to_datetime(df["t"], utc=True).dt.tz_convert(ET)
            df = df[["t", "o", "h", "l", "c", "v"]].sort_values("t").reset_index(drop=True)
            # 2026-10-15: 用 Yahoo 数据补 Alpaca 缺失的 1m bar (12:56-14:11 第三方断层),
            # 与生产 watcher_v4.py 同逻辑 (gapfill_YYYY-MM-DD.csv 同目录, 只补当天)
            _gapf = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "gapfill_%s.csv" % today.strftime("%Y-%m-%d"))
            if os.path.exists(_gapf):
                try:
                    _gdf = pd.read_csv(_gapf, parse_dates=["t"])
                    _gdf["t"] = pd.to_datetime(_gdf["t"], utc=True).dt.tz_convert(ET)
                    _gdf = _gdf[["t", "o", "h", "l", "c", "v"]]
                    df = pd.concat([df, _gdf], ignore_index=True).sort_values("t") \
                           .drop_duplicates("t", keep="first").reset_index(drop=True)
                except Exception as e:
                    print(f"gapfill 失败: {e}")
            return df
        except Exception as e:
            last_err = e
            time.sleep(2 ** attempt)
    log(f"alpaca 1m failed 5x ({last_err}), skip this poll")
    return pd.DataFrame()


def to_3m(df1m):
    """1-min -> 9:30-aligned 3-min bars, regular session only."""
    if df1m.empty:
        return df1m
    df = df1m.copy()
    df = df[(df["t"].dt.strftime("%H:%M") >= "09:30") &
            (df["t"].dt.strftime("%H:%M") < "16:00")].copy()
    if df.empty:
        return df
    df["date"] = df["t"].dt.date
    mins = df["t"].dt.hour * 60 + df["t"].dt.minute
    df["bin"] = (mins - 570) // 3
    rows = []
    for (d, b), g in df.groupby(["date", "bin"]):
        t0 = datetime(d.year, d.month, d.day, 9, 30).replace(tzinfo=ET) + pd.Timedelta(minutes=int(b) * 3)
        rows.append({"t": t0, "o": g["o"].iloc[0], "h": g["h"].max(),
                     "l": g["l"].min(), "c": g["c"].iloc[-1], "v": g["v"].sum(),
                     "n1m": len(g)})
    out = pd.DataFrame(rows).sort_values("t").reset_index(drop=True)
    return out


def market_open(now):
    if now.weekday() >= 5:
        return False
    tod = now.strftime("%H:%M")
    return "09:30" <= tod < "16:00"


def load_history():
    """Warmup: 3-min bars from Alpaca IEX API, cached daily."""
    _today_str = datetime.now(ET).strftime("%Y-%m-%d")
    _cache_path = f"/home/hatch/workspace/alpaca_watcher/warmup_{_today_str}.csv"
    df = None
    try:
        start = (datetime.now(ET) - timedelta(days=5)).strftime("%Y-%m-%dT00:00:00Z")
        params = {"symbols": "SPY", "timeframe": "3Min", "limit": 500, "feed": "iex",
                  "sort": "desc", "start": start}
        data = api("/stocks/bars", params)
        bars = data.get("bars", {}).get("SPY", [])
        if not bars:
            raise ValueError("API returned no bars")
        df = pd.DataFrame(bars)
        df["t"] = pd.to_datetime(df["t"], utc=True).dt.tz_convert(ET)
        df = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
        try:
            df.to_csv(_cache_path, index=False)
            log(f"warmup cached to {_cache_path} ({len(df)} bars)")
        except Exception as _ce:
            log(f"warmup cache write failed: {_ce}")
    except Exception as e:
        log(f"load_history API failed ({e}), trying today's cache")
        if os.path.exists(_cache_path):
            try:
                df = pd.read_csv(_cache_path)
                df["t"] = pd.to_datetime(df["t"], utc=True).dt.tz_convert(ET)
                log(f"warmup loaded from cache {_cache_path} ({len(df)} bars)")
            except Exception as _ce2:
                log(f"cache read failed: {_ce2}")
                df = None
        if df is None:
            raise RuntimeError(f"warmup API failed and no cache for {_today_str}")
    df = df[(df["t"].dt.strftime("%H:%M") >= "09:30") &
            (df["t"].dt.strftime("%H:%M") < "16:00")].copy()
    out = pd.DataFrame({"t": df["t"], "o": df["open"], "h": df["high"],
                        "l": df["low"], "c": df["close"], "v": df["volume"]})
    out["n1m"] = 3
    return out.sort_values("t").reset_index(drop=True)


def get_api_pos_dir():
    """Read position direction from API. Returns 'call'/'put'/None. Raises on failure."""
    positions = get_positions()  # raises on query failure (fail-safe)
    _real = [p for p in positions if abs(float(p.get("qty", 0))) > 1]
    pos = max(_real, key=lambda p: abs(float(p.get("qty", 0)))) if _real else None
    if not pos:
        return None, None
    sym = pos.get("symbol", "")
    cp = sym[-9] if len(sym) >= 9 else ""
    if cp == "C":
        return "call", pos
    elif cp == "P":
        return "put", pos
    return None, None


def get_signal_state(actions):
    """Derive expected position from signal actions.

    Walk actions in order. "开" sets pos, "平" clears pos.
    Returns (pos_dir, entry_bar_idx, entry_time_str, last_action).
    """
    pos_dir = None
    entry_bar = None
    entry_tstr = None
    last_action = None
    for a in actions:
        bar_idx, tstr, act, direction = a[0], a[1], a[2], a[3]
        last_action = a
        if act == "开":
            pos_dir = direction
            entry_bar = bar_idx
            entry_tstr = tstr
        elif act == "平":
            pos_dir = None
            entry_bar = None
            entry_tstr = None
        # "持" doesn't change position
    return pos_dir, entry_bar, entry_tstr, last_action


def do_enter(direction, last_price, st_trend):
    """Open position. direction='call'/'put'.
    2026-10-12 用户: 市价单 (限价单挂单不成交的bug无法根治)。
    2026-10-12 13:18 fix: 张数按 obp*0.95 (Alpaca 按 obp 强检购买力, equity*0.95 会 403)。"""
    opt_type = direction
    with_trend = (st_trend == "buy" and direction == "call") or (st_trend == "sell" and direction == "put")
    equity = get_account_equity()
    if equity <= 0:
        equity = 100000
    obp = get_options_buying_power()
    base = obp * 0.95 if obp > 0 else equity * 0.95
    target = base if with_trend else base * 0.5
    exp = get_expiration()
    symbol, strike = get_atm_contract(exp, opt_type, last_price)
    bid, ask = get_quote(symbol)
    mid = (bid + ask) / 2 if bid and ask else None
    qty = calc_qty(mid, target)
    if qty <= 0:
        return f"ENTER skipped: qty=0"
    place_order(symbol, qty, "buy")  # 市价单: 无 limit_px -> 立即成交
    return f"ENTER {direction}: {qty}x {symbol} strike={strike} @~{mid:.2f} (market)"


_last_exit_id = None  # 最近一次 do_exit 的订单 id (翻仓时等成交用)


def _wait_fill(order_id, timeout=15):
    """等订单成交 (市价单一般1秒内成交)。超时返回 False, 由下一轮 cycle 重试。"""
    import time as _t
    t0 = _t.time()
    while _t.time() - t0 < timeout:
        try:
            o = api(f"/v2/orders/{order_id}", base=PAPER)
            if o.get("status") == "filled":
                return True
        except Exception:
            pass
        _t.sleep(1)
    return False


def do_exit(pos):
    """Close position. 2026-10-12 用户: 改市价单."""
    global _last_exit_id
    sym = pos["symbol"]
    qty = int(float(pos["qty"]))
    o = place_order(sym, qty, "sell")  # 市价单: 无 limit_px -> 立即成交
    _last_exit_id = o.get("id")
    return f"EXIT: {qty}x {sym} (market)"


def main():
    global _last_pct_check_bar, _last_open_acted_bar
    log("watcher_v4 starting — TRUE STATELESS, signals from backtest engine")
    hist3 = load_history()
    log(f"warmup: {len(hist3)} 3-min bars")

    while True:
        try:
            now = datetime.now(ET)
            if not market_open(now):
                log("market closed, sleeping 60s")
                time.sleep(60)
                continue

            df1m = fetch_1m()
            today3 = to_3m(df1m)
            bars3 = pd.concat([hist3, today3], ignore_index=True)
            bars3 = bars3.sort_values("t").drop_duplicates("t", keep="last").reset_index(drop=True)
            if len(bars3) < 100:
                log(f"only {len(bars3)} bars, need 100; sleeping")
                time.sleep(30)
                continue

            # complete bars only
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

            # Bug 5: n_complete=0 时跳过本轮（否则 iloc[n_complete-1] 取到最后一根，错位）
            if n_complete == 0:
                log("n_complete=0 (no complete 3m bar yet); sleeping 30s")
                time.sleep(30)
                continue

            # === V4 CORE: run backtest signal engine on complete bars ===
            # bars for signal_engine need t/open/high/low/close
            sig_bars = pd.DataFrame({
                "t": bars3["t"].iloc[:n_complete],
                "open": bars3["o"].iloc[:n_complete],
                "high": bars3["h"].iloc[:n_complete],
                "low": bars3["l"].iloc[:n_complete],
                "close": bars3["c"].iloc[:n_complete],
            })
            actions = run_signals(sig_bars)
            sig_pos, sig_entry_bar, sig_entry_tstr, last_action = get_signal_state(actions)

            # === Compare with API position ===
            try:
                api_pos, api_pos_obj = get_api_pos_dir()
            except Exception as e:
                log(f"get_positions failed, skipping cycle: {e}")
                time.sleep(30)
                continue

            last_px = float(bars3["c"].iloc[n_complete - 1])
            bar_t = bars3["t"].iloc[n_complete - 1].isoformat()
            _now_t = now.strftime("%H:%M")

            # Bug 2: 15:55 全平，不隔夜 (抄 v3 1999-2004)；之后不开新仓
            if _now_t >= EOD_FLAT_TIME and api_pos:
                r = do_exit(api_pos_obj)
                log(f"FLAT 15:55 close (no overnight): {r}")
                time.sleep(30)
                continue
            _no_new_after_1555 = (_now_t >= EOD_FLAT_TIME)

            # Bug 4: 20% 硬止损 (2026-10-12 user's: 不止盈，以后都不止盈)
            # 原30%止盈已删除
            _cur_bar = n_complete - 1
            if api_pos and _last_pct_check_bar != _cur_bar:
                _last_pct_check_bar = _cur_bar
                try:
                    uplpc = float(api_pos_obj.get("unrealized_plpc", 0) or 0)
                except Exception:
                    uplpc = 0.0
                if uplpc <= -STOP_PCT:
                    r = do_exit(api_pos_obj)
                    log(f"STOP 20% (uplpc={uplpc:.1%}): {r}")
                    time.sleep(30)
                    continue

            if sig_pos != api_pos:
                if not TRADING_ENABLED:
                    log(f"SYNC NEEDED (paper disabled): signals={sig_pos} api={api_pos}")
                elif api_pos and not sig_pos:
                    # signals say flat, API has position -> close
                    reason = last_action[5] if last_action else "signal flat"
                    r = do_exit(api_pos_obj)
                    log(f"SYNC CLOSE ({reason}): {r}")
                elif sig_pos and not api_pos:
                    # Bug 3: api-flat 但信号 holding 时，只在有新"开"action 时重开；
                    # 否则视为手动平仓，仅 log 不下单
                    if _no_new_after_1555:
                        log(f"SYNC OPEN skipped (after 15:55): sig={sig_pos}")
                    elif sig_entry_bar is not None and sig_entry_bar == _last_open_acted_bar:
                        log(f"MANUAL CLOSE detected: api flat but signals holding "
                            f"(entry_bar={sig_entry_bar}); not reopening")
                    else:
                        r = do_enter(sig_pos, last_px, "buy" if sig_pos == "call" else "sell")
                        log(f"SYNC OPEN (entry_bar={sig_entry_bar} {sig_entry_tstr}): {r}")
                        _last_open_acted_bar = sig_entry_bar
                else:
                    # both have positions but different directions -> flip
                    if _no_new_after_1555:
                        # 15:55 后只平不翻
                        r = do_exit(api_pos_obj)
                        log(f"SYNC FLAT (after 15:55, no flip): {r}")
                    else:
                        r = do_exit(api_pos_obj)
                        log(f"SYNC FLIP close {api_pos}: {r}")
                        # 2026-10-12 13:18 fix: 等 exit 成交再进, 否则购买力被占用导致张数算小
                        # (市价单一般1秒内成交; 超时则跳过本轮, 下一轮 cycle 重试)
                        if _last_exit_id and _wait_fill(_last_exit_id, timeout=15):
                            r = do_enter(sig_pos, last_px, "buy" if sig_pos == "call" else "sell")
                            log(f"SYNC FLIP open {sig_pos}: {r}")
                            _last_open_acted_bar = sig_entry_bar
                        else:
                            log(f"SYNC FLIP exit not filled in 15s, retry next cycle")
            else:
                log(f"ok px={last_px:.2f} sig_pos={sig_pos} api_pos={api_pos} "
                    f"actions={len(actions)} bar={bar_t}")

        except Exception as e:
            log(f"ERROR: {type(e).__name__}: {e}")
        time.sleep(30)


if __name__ == "__main__":
    main()
