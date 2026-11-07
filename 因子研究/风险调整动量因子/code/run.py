#!/usr/bin/env python
"""
风险调整动量的批量回测：sharpe_mom_w 全窗口 × 现货/永续，并**同步跑原始动量做对照**。

为什么要并排跑原始动量：sharpe_mom 和 mom_30d 的截面秩相关有 0.90，
如果直接把 sharpe_mom 的回测结果拿出来说「这个因子有效」，很可能只是原始动量在起作用。
唯一有意义的问法是：**同样的持有期、同样的池子，风险调整后 IC 和净收益有没有变好**。

用法：
    python 因子研究/风险调整动量因子/code/run.py                     # 默认全跑
    python 因子研究/风险调整动量因子/code/run.py --markets um --windows 30
    python 因子研究/风险调整动量因子/code/run.py --rebalance monthly --hold 1,3
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


def _row(cfg: E.Config, res: dict, label: str) -> list[dict]:
    """把一次回测的汇总表拆成「每行一个持有窗口」的长表。"""
    summ = res["tables"]["汇总统计"]
    cost = res["tables"]["换手与成本"].set_index("持有窗口")
    rows = []
    for _, r in summ.iterrows():
        h = int(r["持有窗口"])
        rows.append({
            "市场": cfg.market, "因子": label, "持有窗口": h,
            "IC": r.get("IC"), "ICIR": r.get("ICIR"), "IC_t_NW": r.get("IC_t_NW"),
            "FM斜率(BP)": r.get("FM斜率(BP)"), "FM_t_NW": r.get("FM_t_NW"),
            "分组远期价差": r.get("分组远期价差"),
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
    ap = argparse.ArgumentParser(description="风险调整动量因子批量回测")
    ap.add_argument("--markets", default="um,spot")
    ap.add_argument("--windows", default="7,30,90,180")
    ap.add_argument("--rebalance", default="weekly", choices=["daily", "weekly", "monthly"])
    ap.add_argument("--hold", default="1,4")
    ap.add_argument("--top-n", type=int, default=80)
    ap.add_argument("--min-obs", type=int, default=15)
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--baseline", action="store_true", default=True,
                    help="同时跑原始动量做对照（默认开，--no-baseline 关掉）")
    ap.add_argument("--no-baseline", dest="baseline", action="store_false")
    ap.add_argument("--tag", default=None)
    args = ap.parse_args()

    holds = tuple(int(x) for x in args.hold.split(","))
    markets = [m.strip() for m in args.markets.split(",") if m.strip()]
    windows = [int(x) for x in args.windows.split(",") if x.strip()]

    base = dict(rebalance=args.rebalance, hold_windows=holds, top_n=args.top_n,
                min_obs=args.min_obs, start=args.start, end=args.end)

    rows: list[dict] = []
    for market in markets:
        panel = os.path.join(OUT, f"factor_{market}_1d.parquet")
        if not os.path.exists(panel):
            print(f"⚠️  缺 {os.path.relpath(panel, FACTOR_ROOT)}，先跑 code/build_factor.py --market {market}")
            continue
        for w in windows:
            jobs = [(f"sharpe_mom_{w}d", E.Config(market=market, tf="1d", factor=f"sharpe_mom_{w}d",
                                                  factor_panel=panel, **base))]
            if args.baseline:
                jobs.append((f"mom_{w}d", E.Config(market=market, tf="1d", factor=f"mom_{w}d", **base)))
            for label, cfg in jobs:
                print(f"\n>>> {market} / {label} / {args.rebalance} 持有 {holds}")
                try:
                    res = E.run(cfg, verbose=False)
                except Exception as e:                       # noqa: BLE001
                    print(f"    ❌ 失败：{type(e).__name__}: {e}")
                    continue
                rs = _row(cfg, res, label)
                rows += rs
                for r in rs:
                    print(f"    h={r['持有窗口']:<2} IC {r['IC']:>7.2%}  t {r['IC_t_NW']:>6.2f}  "
                          f"年化 {r['年化收益']:>7.2%}  夏普 {r['夏普']:>5.2f}  "
                          f"回撤 {r['最大回撤']:>7.2%}  资金费 {r['资金费年化']:>7.2%}")

    if not rows:
        print("没有任何结果")
        return

    df = pd.DataFrame(rows)
    os.makedirs(OUT, exist_ok=True)
    tag = args.tag or f"{args.rebalance}_top{args.top_n}"
    path = os.path.join(OUT, f"汇总_{tag}.csv")
    df.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"\n汇总已写：{os.path.relpath(path, FACTOR_ROOT)}（{len(df)} 行）")

    # 配对对照：同一 (市场, 窗口, 持有期) 下 sharpe_mom 减 mom
    pair = df.pivot_table(index=["市场", "持有窗口"], columns="因子", values="IC")
    cols = [c for c in pair.columns if c.startswith("sharpe_mom_")]
    diffs = []
    for c in cols:
        raw = c.replace("sharpe_mom_", "mom_")
        if raw in pair.columns:
            d = (pair[c] - pair[raw]).dropna()
            if len(d):
                diffs.append(d.rename(c))
    if diffs:
        print("\n=== 风险调整 vs 原始动量：IC 差（配对，同池同持有期）===")
        print(pd.concat(diffs, axis=1).to_string(float_format=lambda v: f"{v:+.2%}"))


if __name__ == "__main__":
    main()
