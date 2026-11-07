#!/usr/bin/env python
"""
球队硬币因子的批量回测。

研报口径是**月度**因子 + 月度调仓，所以主口径是 `--rebalance monthly --hold 1`。
另外跑两种稳健性口径：
    - 持有 3 个月（`--hold 1,3`）：信号衰减速度
    - 周频调仓（`--rebalance weekly`）：月底信号在月内的有效性

方向：研报的因子是**负向**的（买因子值最低的一组），所以主结果看 `short_top`
（做多因子值低的一侧）。同时跑 `long_top`，避免把「方向选反」误读成「因子无效」——
IC 与方向无关，组合收益才有关。

用法：
    python 因子研究/球队硬币因子/code/run.py                  # 全量主口径
    python 因子研究/球队硬币因子/code/run.py --factors fct_team_coin
    python 因子研究/球队硬币因子/code/run.py --rebalance weekly --tag weekly
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

# 主口径：合成因子 + 三个维度的修正因子；raw 作为「翻转到底有没有用」的对照
DEFAULT_FACTORS = ("fct_team_coin", "fct_interday_modified", "fct_intraday_modified",
                   "fct_overnight_modified", "fct_interday_raw")


def _rows(cfg: E.Config, res: dict, factor: str, direction: str) -> list[dict]:
    summ = res["tables"]["汇总统计"]
    cost = res["tables"]["换手与成本"].set_index("持有窗口")
    rows = []
    for _, r in summ.iterrows():
        h = int(r["持有窗口"])
        rows.append({
            "市场": cfg.market, "因子": factor, "方向": direction, "持有窗口": h,
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
    ap = argparse.ArgumentParser(description="球队硬币因子批量回测")
    ap.add_argument("--markets", default="um,spot")
    ap.add_argument("--factors", default=",".join(DEFAULT_FACTORS))
    ap.add_argument("--directions", default="short_top,long_top",
                    help="研报的因子是负向的，主方向 short_top（做多因子值低的一侧）")
    ap.add_argument("--rebalance", default="monthly", choices=["weekly", "monthly"])
    ap.add_argument("--hold", default="1,3")
    ap.add_argument("--top-n", type=int, default=80)
    ap.add_argument("--min-obs", type=int, default=15)
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--tag", default=None)
    args = ap.parse_args()

    holds = tuple(int(x) for x in args.hold.split(","))
    markets = [m.strip() for m in args.markets.split(",") if m.strip()]
    factors = [f.strip() for f in args.factors.split(",") if f.strip()]
    directions = [d.strip() for d in args.directions.split(",") if d.strip()]

    rows: list[dict] = []
    for market in markets:
        panel = os.path.join(OUT, f"factor_{market}_h8_adv.parquet")
        if not os.path.exists(panel):
            print(f"⚠️  缺 {os.path.relpath(panel, FACTOR_ROOT)}，"
                  f"先跑 code/build_factor.py --market {market}")
            continue
        for factor in factors:
            for direction in directions:
                cfg = E.Config(market=market, tf="1d", factor=factor,
                               factor_panel=panel, direction=direction,
                               rebalance=args.rebalance, hold_windows=holds,
                               top_n=args.top_n, min_obs=args.min_obs,
                               start=args.start, end=args.end)
                print(f"\n>>> {market} / {factor} / {direction} / "
                      f"{args.rebalance} 持有 {holds}")
                try:
                    res = E.run(cfg, verbose=False)
                except Exception as e:                       # noqa: BLE001
                    print(f"    ❌ 失败：{type(e).__name__}: {e}")
                    continue
                rs = _rows(cfg, res, factor, direction)
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

    h0 = holds[0]
    print(f"\n=== IC（持有 {h0} 窗口）===")
    ic = df[df["持有窗口"] == h0].pivot_table(
        index=["市场", "因子"], columns="方向", values="IC")
    print(ic.to_string(float_format=lambda v: f"{v:+.2%}"))


if __name__ == "__main__":
    main()
