#!/usr/bin/env python3
"""官方结算脚本 (2026-10-22 定版，勿改逻辑)

口径 (MEMORY.md 2026-10-14 定死):
- 按 pickle actions 原始顺序，开→平配对（单仓，'持'跳过）
- Gamma公式: Call ROI% = 9.88×ΔSPY + 0.311×ΔSPY²
             Put  ROI% = -9.88×ΔSPY + 0.311×ΔSPY²
- 下限 -20%，逆向钳制 ≤0（亏方向不赚钱）
- 每周 $100k 起始，周内逐笔 sequential 复利 (capital *= 1+r)
- 跨周重置 $100k，全年 = 各周 P&L 加总（非复利）

用法:
    python3 settle_official.py "trades-week-2026-*-TAG-*.pkl"
    python3 settle_official.py file1.pkl file2.pkl ...
"""

import pickle
import glob
import sys


def gamma_roi(direction, d_spy):
    """direction: 'call' or 'put'; d_spy: close_px - open_px"""
    if direction == 'call':
        r = 9.88 * d_spy + 0.311 * d_spy * d_spy
    else:
        r = -9.88 * d_spy + 0.311 * d_spy * d_spy
    # 逆向钳制: 亏方向不赚钱
    if (direction == 'call' and d_spy < 0) or (direction == 'put' and d_spy > 0):
        r = min(r, 0)
    # 下限 -20%
    r = max(r, -20)
    return r


def settle_week(fn):
    """结算单周 pickle，返回 (week_pnl, n_trades)"""
    with open(fn, 'rb') as f:
        d = pickle.load(f)
    pos = None  # (direction, open_px)
    cap = 100000.0
    week_pnl = 0.0
    n = 0
    # 必须按 actions 原始顺序，不排序不过滤（'持'直接跳过）
    for bi, ts, act, dr, px, rs in d['actions']:
        if act == '开':
            pos = (dr, px)
        elif act == '平' and pos is not None:
            odr, opx = pos
            pos = None
            dd = px - opx
            r = gamma_roi(odr, dd)
            pnl = cap * r / 100.0
            cap *= (1 + r / 100.0)
            week_pnl += pnl
            n += 1
        # '持' 直接跳过
    Monday = d.get('week_monday', '?')
    return week_pnl, n, Monday


def main():
    patterns = sys.argv[1:]
    if not patterns:
        print(__doc__)
        sys.exit(1)
    files = []
    for p in patterns:
        files.extend(sorted(glob.glob(p)))
    if not files:
        print("no files matched")
        sys.exit(1)

    total = 0.0
    total_n = 0
    for fn in files:
        pnl, n, Monday = settle_week(fn)
        total += pnl
        total_n += n
        print(f"{Monday}: ${pnl:>12,.0f} ({n:3d}笔)  {fn.split('/')[-1][:60]}")
    print(f"\n合计: ${total:,.0f} ({total_n}笔, {len(files)}周)")


if __name__ == '__main__':
    main()
