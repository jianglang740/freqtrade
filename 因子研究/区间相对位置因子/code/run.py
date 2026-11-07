#!/usr/bin/env python
"""
区间相对位置因子的批量回测：4 个窗口 × 现货/永续 × 两个方向。

为什么要同时跑 long_top 和 short_top：
    IC 的符号是因子属性，和方向无关；但「做多高 RelPos」和「做多低 RelPos」
    是两条完全不同的组合，成本、资金费、回撤都不一样。只跑一个方向，
    很容易在 IC<0 时把「方向选反了」误读成「因子不赚钱」。

用法：
    python 因子研究/区间相对位置因子/code/run.py
    python 因子研究/区间相对位置因子/code/run.py --markets um --windows 7,14
    python 因子研究/区间相对位置因子/code/run.py --hold 1,2,4 --rebalance weekly
"""

from __future__ import annotations

import argparse
import os
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
FACTOR_ROOT = os.path.dirname(PROJECT)
for _p in (os.path.join(FACTOR_ROOT, "行情数据"),
           os.path.join(FACTOR_ROOT, "因子数据"),
           os.path.join(FACTOR_ROOT, "回测框架")):
    sys.path.insert(0, _p)

import engine as E  # noqa: E402

OUT = os.path.join(PROJECT, "out")


def _row(cfg: E.Config, res: dict, window: int, direction: str) -> list[dict]:
    summ = res["tables"]["汇总统计"]
    cost = res["tables"]["换手与成本"].set_index("持有窗口")
    rows = []
    for _, r in summ.iterrows():
        h = int(r["持有窗口"])
        rows.append({
            "市场": cfg.market, "窗口": window, "方向": direction, "持有窗口": h,
            "IC": r.get("IC"), "ICIR": r.get("ICIR"), "IC_t_NW": r.get("IC_t_NW"),
            "FM斜率(BP)": r.get("FM斜率(BP)"), "FM_t_NW": r.get("FM_t_NW"),
            "分组远期价差": r.get("分组远期价差"),
            "原始IC": r.get("原始IC"), "中性化IC": r.get("中性化IC"),
            "年化收益": r.get("年化收益"), "年化波动": r.get("年化波动"),
            "夏普": r.get("夏普"), "最大回撤": r.get("最大回撤"),
            "年化成本": r.get("年化成本"), "资金费年化": r.get("年化资金费"),
            "年化换手": cost["年化换手"].get(h) if h in cost.index else None,
            "平均多腿": cost["平均多腿"].get(h) if h in cost.index else None,
            "平均空腿": cost["平均空腿"].get(h) if h in cost.index else None,
            "破产": r.get("破产"),
        })
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description="区间相对位置因子批量回测")
    ap.add_argument("--markets", default="um,spot")
    ap.add_argument("--windows", default="7,14,30,60")
    ap.add_argument("--directions", default="long_top,short_top")
    ap.add_argument("--rebalance", default="weekly", choices=["daily", "weekly", "monthly"])
    ap.add_argument("--hold", default="1,4")
    ap.add_argument("--top-n", type=int, default=80)
    ap.add_argument("--min-obs", type=int, default=15)
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--tag", default=None)
    args = ap.parse_args()

    holds = tuple(int(x) for x in args.hold.split(","))
    markets = [m.strip() for m in args.markets.split(",") if m.strip()]
    windows = [int(x) for x in args.windows.split(",") if x.strip()]
    directions = [d.strip() for d in args.directions.split(",") if d.strip()]

    rows: list[dict] = []
    for market in markets:
        panel = os.path.join(OUT, f"factor_{market}_1d.parquet")
        if not os.path.exists(panel):
            print(f"⚠️  缺 {os.path.relpath(panel, FACTOR_ROOT)}，"
                  f"先跑 code/build_factor.py --market {market}")
            continue
        for w in windows:
            for direction in directions:
                cfg = E.Config(market=market, tf="1d", factor=f"relpos_{w}d",
                               factor_panel=panel, direction=direction,
                               rebalance=args.rebalance, hold_windows=holds,
                               top_n=args.top_n, min_obs=args.min_obs,
                               start=args.start, end=args.end)
                print(f"\n>>> {market} / relpos_{w}d / {direction} / "
                      f"{args.rebalance} 持有 {holds}")
                try:
                    res = E.run(cfg, verbose=False)
                except Exception as e:                       # noqa: BLE001
                    print(f"    ❌ 失败：{type(e).__name__}: {e}")
                    continue
                rs = _row(cfg, res, w, direction)
                rows += rs
                for r in rs:
                    if r["持有窗口"] != holds[0]:
                        continue
                    print(f"    h={r['持有窗口']:<2} IC {r['IC']:>7.2%}  "
                          f"t {r['IC_t_NW']:>6.2f}  年化 {r['年化收益']:>8.2%}  "
                          f"夏普 {r['夏普']:>5.2f}  回撤 {r['最大回撤']:>7.2%}  "
                          f"成本 {r['年化成本']:>6.2%}  资金费 {r['资金费年化']:>7.2%}")

    if not rows:
        print("没有任何结果")
        return

    df = pd.DataFrame(rows)
    os.makedirs(OUT, exist_ok=True)
    tag = args.tag or f"{args.rebalance}_top{args.top_n}"
    path = os.path.join(OUT, f"汇总_{tag}.csv")
    df.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"\n汇总已写：{os.path.relpath(path, FACTOR_ROOT)}（{len(df)} 行）")

    # IC 符号一览：窗口 × 市场（持有 1 窗口）
    h0 = holds[0]
    ic = df[df["持有窗口"] == h0].pivot_table(
        index="窗口", columns=["市场", "方向"], values="IC")
    print(f"\n=== IC（持有 {h0} 窗口）===")
    print(ic.to_string(float_format=lambda v: f"{v:+.2%}"))


if __name__ == "__main__":
    main()
