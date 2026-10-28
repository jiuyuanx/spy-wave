#!/usr/bin/env python3
"""
Alpaca SPY 期权交易员 — 3分钟 QQE 红/蓝衰减策略 (paper)。

策略:
- 红衰减 -> 买 call; 蓝衰减 -> 买 put (直接翻仓)
- Supertrend 定量: 顺势 ~$22k 权利金 / 逆势 ~$11k (按报价动态算张数)
- 期权: 2-3个交易日后到期, 平值 strike, 每天开盘选一次
- 止损: 连续2根同色柱变长 -> 平仓; 仓位亏10% -> 平仓; 15:55前全平, 不过夜

环境变量: ALPACA_KEY, ALPACA_SECRET, TRADING_ENABLED (true/false)
"""
import os, sys, json, time, urllib.request, urllib.parse
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
KEY = os.environ["ALPACA_KEY"]
SECRET = os.environ["ALPACA_SECRET"]
TRADING_ENABLED = os.environ.get("TRADING_ENABLED", "false").lower() == "true"
PAPER = "https://paper-api.alpaca.markets"
DATA = "https://data.alpaca.markets/v2"
WDIR = "/home/hatch/workspace/alpaca_watcher"
POS_F = os.path.join(WDIR, "positions.json")
TRADE_LOG = os.path.join(WDIR, "trades.log")

# 策略参数
# 2026-10-05 用户: 动态满仓 - 顺势用100%权益, 逆势用50%
STOP_PCT = 0.20          # 仓位止损 20%
EOD_FLAT_TIME = "15:55"  # 日终平仓时间


def log(msg):
    ts = datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(TRADE_LOG, "a") as f:
        f.write(line + "\n")


def api(path, params=None, method="GET", body=None, base=PAPER, retries=3):
    """带重试的 API 请求 (指数退避 1s/2s/4s)。"""
    q = urllib.parse.urlencode(params) if params else ""
    url = base + path + ("?" + q if q else "")
    data = json.dumps(body).encode() if body else None
    H = {"APCA-API-KEY-ID": KEY, "APCA-API-SECRET-KEY": SECRET,
         "Content-Type": "application/json"}
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=data, headers=H, method=method)
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.load(r) if r.status != 204 else {}
        except Exception as e:
            last_err = e
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    raise last_err


def trading_days_ahead(n):
    """n个交易日后的日期 (跳过周末)。"""
    d = datetime.now(ET).date()
    added = 0
    while added < n:
        d += timedelta(days=1)
        if d.weekday() < 5:
            added += 1
    return d.isoformat()


def get_expiration():
    """选2-3个交易日后到期的 SPY 期权到期日 (每天选一次, 缓存)。"""
    cache_f = os.path.join(WDIR, "expiration.json")
    today = datetime.now(ET).date().isoformat()
    if os.path.exists(cache_f):
        with open(cache_f) as f:
            c = json.load(f)
        if c.get("date") == today:
            return c["expiration"]
    # 找3个交易日后的到期日, 有chain才用
    for n in (3, 2, 4, 5):
        exp = trading_days_ahead(n)
        try:
            d = api("/v2/options/contracts", {"underlying_symbol": "SPY",
                    "expiration_date": exp, "limit": 1}, base=PAPER)
            if d.get("option_contracts"):
                with open(cache_f, "w") as f:
                    json.dump({"date": today, "expiration": exp}, f)
                log(f"selected expiration: {exp} ({n} trading days out)")
                return exp
        except Exception as e:
            log(f"expiration {exp} failed: {e}")
    raise RuntimeError("no valid expiration found")


def get_atm_contract(expiration, option_type, ref_price):
    """找最接近 ref_price 的 strike 的 call/put 合约。"""
    # 2026-10-06 用户: 加 strike 范围过滤, 否则 limit 100 可能不含 ATM
    d = api("/v2/options/contracts", {
        "underlying_symbol": "SPY", "expiration_date": expiration,
        "type": option_type, "strike_price_gte": ref_price - 10,
        "strike_price_lte": ref_price + 10, "limit": 50}, base=PAPER)
    contracts = d.get("option_contracts", [])
    if not contracts:
        raise RuntimeError(f"no {option_type} contracts for {expiration}")
    # 找 strike 最接近 ref_price 的
    best = min(contracts, key=lambda c: abs(float(c["strike_price"]) - ref_price))
    return best["symbol"], float(best["strike_price"])


OPDATA = "https://data.alpaca.markets/v1beta1"  # 期权行情只能走 v1beta1 (v2 无 options 路径)

def get_quote(symbol):
    """取期权买卖报价 (v1beta1 latest, indicative feed)。"""
    try:
        d = api("/options/quotes/latest",
                {"symbols": symbol, "feed": "indicative"}, base=OPDATA)
        q = d.get("quotes", {}).get(symbol, {})
        bid, ask = q.get("bp"), q.get("ap")
        if bid and ask:
            return float(bid), float(ask)
    except Exception as e:
        log(f"quote failed for {symbol}: {e}")
    return None, None


def calc_qty(mid_price, target_premium):
    """按目标权利金算张数。"""
    if not mid_price or mid_price <= 0:
        return 0
    qty = round(target_premium / (mid_price * 100))
    return max(qty, 1)


def get_account_equity():
    """获取账户权益(动态满仓用)。"""
    try:
        acct = api("/v2/account", {})
        return float(acct.get("equity", 0))
    except Exception as e:
        log(f"get_account_equity failed: {e}")
        return 0


def get_options_buying_power():
    """获取期权购买力 (2026-10-05: 下单张数要用这个, 不是 equity)。"""
    try:
        acct = api("/v2/account", {})
        return float(acct.get("options_buying_power", 0))
    except Exception as e:
        log(f"get_options_buying_power failed: {e}")
        return 0


def find_recent_order(symbol, qty, side, minutes=5):
    """查找最近 N 分钟内相同 symbol/side/qty 的订单 (下单重试去重用)。"""
    from datetime import datetime, timedelta, timezone
    try:
        orders = api("/v2/orders", params={"status": "all", "limit": 20, "direction": "desc"},
                     base=PAPER, retries=2)
    except Exception:
        return None
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    for o in orders:
        if o.get("symbol") != symbol: continue
        if o.get("side") != side: continue
        try:
            if int(float(o.get("qty", 0))) != qty: continue
        except Exception:
            continue
        try:
            created = datetime.fromisoformat(o["created_at"].replace("Z", "+00:00"))
        except Exception:
            continue
        if created < cutoff: continue
        if o.get("status") in ("canceled", "rejected", "expired"): continue
        return o
    return None


def place_order(symbol, qty, side, max_retries=10, limit_px=None):
    """下单 (paper)。2026-10-05 12:28 用户: 先读市价再挂单, 直接限价。
    limit_px 指定则直接限价单; 否则市价单 (无报价时转限价)。
    购买力不足时自动降张数重试。返回 order dict。"""
    if not TRADING_ENABLED:
        log(f"[DRY-RUN] would {side} {qty}x {symbol}")
        return {"id": "dry-run", "status": "dry-run"}
    last_err = None
    _limit_mode = False
    _limit_attempt = 0
    for attempt in range(max_retries):
        if attempt > 0:
            existing = find_recent_order(symbol, qty, side)
            if existing:
                log(f"order already exists, reuse (attempt {attempt+1}): "
                    f"{existing['id']} status={existing['status']}")
                return existing
        try:
            if limit_px is not None:
                # 2026-10-05 12:28 用户: 指定限价直接下, 不试市价
                log(f"limit order {side} {qty}x {symbol} @ {limit_px}")
                o = api("/v2/orders", method="POST", body={
                    "symbol": symbol, "qty": qty, "side": side,
                    "type": "limit", "limit_price": limit_px,
                    "time_in_force": "day"}, base=PAPER, retries=2)
            elif _limit_mode:
                # 限价模式: 每次重取报价, 价格越来越激进 (2026-10-05 11:12 用户)
                bid, ask = get_quote(symbol)
                _limit_attempt += 1
                aggress = 1 + 0.02 * _limit_attempt  # 每次多 2%
                if side == "buy" and ask:
                    limit_px = round(ask * aggress, 2)
                elif side == "sell" and bid:
                    limit_px = round(bid / aggress, 2)
                elif side == "sell":
                    limit_px = 0.01
                else:
                    raise Exception("no quote for limit order")
                log(f"limit retry {_limit_attempt}: {side} {qty}x {symbol} @ {limit_px}")
                o = api("/v2/orders", method="POST", body={
                    "symbol": symbol, "qty": qty, "side": side,
                    "type": "limit", "limit_price": limit_px,
                    "time_in_force": "day"}, base=PAPER, retries=2)
            else:
                o = api("/v2/orders", method="POST", body={
                    "symbol": symbol, "qty": qty, "side": side,
                    "type": "market", "time_in_force": "day"}, base=PAPER, retries=2)
            log(f"order {side} {qty}x {symbol}: {o['id']} status={o['status']}")
            return o
        except Exception as e:
            last_err = e
            err_str = str(e)
            # 2026-10-12 13:25 fix: HTTPError 正文里才有真正原因
            # (如 {"message":"insufficient options buying power"}), str(e) 只有 "HTTP Error 403"
            try:
                import urllib.error as _ue
                if isinstance(e, _ue.HTTPError):
                    _body = e.read().decode("utf-8", "ignore")
                    if _body:
                        err_str = err_str + " | " + _body[:300]
            except Exception:
                pass
            # 购买力不足 -> 降张数重试
            if "insufficient" in err_str and "buying power" in err_str:
                try:
                    import re
                    m = re.search(r'"options_buying_power"\s*:\s*"([\d.]+)"', err_str)
                    if m:
                        avail_bp = float(m.group(1))
                        bid, ask = get_quote(symbol)
                        px = ask if side == "buy" and ask else (bid if bid else ask)
                        if px and px > 0:
                            new_qty = int((avail_bp * 0.98) // (px * 100))
                            if new_qty > 0 and new_qty < qty:
                                log(f"insufficient buying power ({avail_bp}), reducing qty {qty} -> {new_qty}")
                                qty = new_qty
                                time.sleep(1)
                                continue
                except Exception as qe:
                    log(f"qty reduce failed: {qe}")
            # 无报价 -> 转限价模式
            if "no available quote" in err_str and not _limit_mode:
                _limit_mode = True
                _limit_attempt = 0
                log(f"no quote for market order, switching to limit mode")
                time.sleep(1)
                continue
            # 预设限价失败 -> 转刷新模式重取报价 (2026-10-05 用户: 价格有关)
            if limit_px is not None and "403" in err_str:
                log(f"preset limit_px {limit_px} failed, switching to refresh mode")
                limit_px = None
                _limit_mode = True
                _limit_attempt = 0
                time.sleep(1)
                continue
            log(f"place_order attempt {attempt+1}/{max_retries} failed: {e}")
            if attempt < max_retries - 1:
                time.sleep(min(2 ** attempt, 30))
    raise last_err


def get_positions():
    """取当前期权持仓。失败时直接抛异常, 由调用方跳过本轮, 绝不能返回[]误判为空仓。"""
    poss = api("/v2/positions", base=PAPER)
    # 只保留 SPY 期权 (symbol 长度>4 且非股票)
    return [p for p in poss if len(p["symbol"]) > 10 or p["asset_class"] == "us_option"]


def close_position(symbol, qty):
    """平仓 (卖出)。"""
    return place_order(symbol, qty, "sell")


def load_positions():
    if os.path.exists(POS_F):
        with open(POS_F) as f:
            return json.load(f)
    return {}


def save_positions(d):
    with open(POS_F, "w") as f:
        json.dump(d, f, indent=2)
