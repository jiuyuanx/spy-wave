#!/usr/bin/env python3
"""vendored 自 research/er_leg_backtest_20261110/wer_deep_common.py (2026-11-15)。

gen_wave_today 的整合版 Envelope 配方只需要 to_recs 一个函数, 逐字收录;
原模块的 ctx()/base_body() 依赖研究目录数据缓存与 run_sl_sweep 全树,
不进部署包。函数逻辑一字未改。"""
from settle_official import gamma_roi


def to_recs(tr, modes, dates):
    recs = []
    for (eb, xb, d, epx, xpx, rsn) in tr:
        recs.append(dict(eb=int(eb), xb=int(xb), ed=dates[eb], xd=dates[xb],
                         dir=1 if d == "long" else -1, epx=epx, xpx=xpx, rsn=rsn,
                         mode=modes.get(int(eb), "?"),
                         r=gamma_roi("call" if d == "long" else "put", xpx - epx),
                         stop=(rsn == "SL")))
    return recs
