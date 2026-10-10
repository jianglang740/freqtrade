"""稳健性与最终判定 —— 13 §9 步骤 4/7、18 §3/§7/§12。

回答两个问题：
  1) 无条件基线在**样本外**是否稳定？（按时间对半分割）
  2) 条件化的"显著"分组，是真实的增量还是多重检验的产物？
     （BH 校正 + 跨半样本的符号一致性）

本脚本不产生新参数，只对 02_stat_gate.py 的产物做检验。

运行：python pa_research/scripts/03_robustness.py --tf 4h
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

REPO_ROOT = Path(__file__).resolve().parents[2]
REPORTS = REPO_ROOT / "pa_research/reports"

VARIABLES = [
    ("feat_breakout_distance", "突破距离"),
    ("feat_breakout_close_quality", "收盘质量"),
    ("pa_pullback_depth", "回调深度"),
    ("pa_range_position", "区间位置"),
    ("pa_distance_to_swing_high", "距摆动高点"),
    ("pa_distance_to_swing_low", "距摆动低点"),
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tf", default="4h")
    ap.add_argument("--bins", type=int, default=3)
    args = ap.parse_args()

    r = pd.read_parquet(REPORTS / f"gate_{args.tf}.parquet")
    r["entry_time"] = pd.to_datetime(r["entry_time"])
    # 每个品种按自身时间中位切半 —— 避免早期只有 BTC/ETH 时全归入同一半
    r["half"] = r.groupby("symbol")["entry_time"].transform(
        lambda s: (s > s.median()).map({False: "H1", True: "H2"})
    )

    print("=" * 78)
    print("稳健性检验")
    print("=" * 78)

    # ── 1. 无条件基线的时间稳定性 ──
    print("\n── 1. 无条件基线：前一半 / 后一半 净 R ──")
    tab = r.pivot_table(index="event_type", columns="half", values="net_R", aggfunc="mean").round(4)
    tab["符号一致"] = np.sign(tab["H1"]) == np.sign(tab["H2"])
    tab["整体"] = r.groupby("event_type")["net_R"].mean().round(4)
    print(tab.to_string())
    n_consistent = int(tab["符号一致"].sum())
    print(f"\n  符号一致的基线: {n_consistent}/{len(tab)}"
          f"（其中净 R 两半都 > 0 的: "
          f"{int(((tab['H1'] > 0) & (tab['H2'] > 0)).sum())}）")

    # ── 2. 逐年的稳定性 ──
    r["year"] = r["entry_time"].dt.year
    print("\n── 2. 逐年净 R（主要年份）──")
    yr = r.pivot_table(index="year", columns="event_type", values="net_R", aggfunc="mean").round(4)
    cnt = r.pivot_table(index="year", columns="event_type", values="net_R", aggfunc="size")
    main_years = cnt.sum(axis=1).sort_values(ascending=False).head(4).index
    print(yr.loc[sorted(main_years)].to_string())
    print(f"\n  （事件数 <100 的年份波动大，不单独解读）")

    # ── 3. 条件化分组的跨半样本一致性 ──
    print("\n── 3. 条件化分组：跨半样本符号一致性 ──")
    rows = []
    for col, name in VARIABLES:
        for et in sorted(r["event_type"].unique()):
            sub = r[(r["event_type"] == et) & r[col].notna()].copy()
            if len(sub) < 300:
                continue
            sub["b"] = pd.qcut(sub[col], args.bins, labels=False, duplicates="drop")
            for b in sorted(sub["b"].dropna().unique()):
                g = sub[sub["b"] == b]
                a = g.loc[g.half == "H1", "net_R"].mean()
                c = g.loc[g.half == "H2", "net_R"].mean()
                if not (np.isfinite(a) and np.isfinite(c)):
                    continue
                rows.append({
                    "event_type": et, "variable": name, "bucket": int(b),
                    "n": len(g), "H1": a, "H2": c, "same_sign": (a > 0) == (c > 0),
                })
    cons = pd.DataFrame(rows)
    if cons.empty:
        print("  无可检验分组")
        return
    rate = cons["same_sign"].mean()
    se = np.sqrt(0.25 / len(cons))
    z = (rate - 0.5) / se
    print(f"  可检验分组 {len(cons)} 个；符号一致 {int(cons['same_sign'].sum())} "
          f"= {rate:.1%}（随机基准 50%，SE {se:.1%}，z={z:+.2f}）")
    print("  最稳定（两半同向且幅度大）的分组：")
    strong = cons[(cons.H1.abs() > 0.05) & (cons.H2.abs() > 0.05) & cons.same_sign]
    print(strong.sort_values("H2", key=abs, ascending=False).head(8).round(4).to_string(index=False)
          if len(strong) else "    （无）")

    # ── 4. 多重检验校正 ──
    print("\n── 4. 多重检验校正（BH，FDR 5%）──")
    led = pd.read_csv(REPO_ROOT / "pa_research/trial_ledger.csv").drop_duplicates()
    # 只校正**本周期**的试验：4h 与 1h 是两组独立的假设族，
    # 混在一起做 BH 会虚增 m 并扭曲阈值（曾在首轮发生，见结论报告 §3.5）。
    if "tf" in led.columns:
        led = led[led["tf"] == args.tf]
    else:
        print("  警告：trial_ledger 无 tf 列，无法按周期隔离；请重跑 02_stat_gate.py")
    led = led.dropna(subset=["ci_lo", "ci_hi"]).copy()
    led["se"] = (led.ci_hi - led.ci_lo) / 3.92
    led = led[led.se > 0]
    led["z"] = led.mean_net_R / led.se
    led["p"] = 2 * (1 - stats.norm.cdf(led.z.abs()))
    led = led.sort_values("p").reset_index(drop=True)
    m = len(led)
    led["bh"] = led["p"] <= (led.index + 1) / m * 0.05
    n_sig_raw = int(((led.ci_lo > 0) | (led.ci_hi < 0)).sum())
    print(f"  试验桶总数 {m}")
    print(f"  名义 95% 不跨 0 的桶: {n_sig_raw}（纯噪声期望约 {0.05*m:.1f}）")
    print(f"  BH 校正后仍显著: {int(led['bh'].sum())}")
    if led["bh"].any():
        print(led[led["bh"]][["event_type", "variable", "bucket", "n", "mean_net_R", "p"]]
              .round(4).to_string(index=False))
        print("\n  警告：这些 p 值来自**按品种聚类**的 bootstrap，未计入跨品种的**时间同步性**")
        print("  （37 个品种共享同一段 2023–2026 行情）。因此显著性是**高估**的；")
        print("  决定性证据以第 1、3 节的跨半样本结果为准。")

    # ── 5. 判定 ──
    print("\n" + "=" * 78)
    base_ok = ((tab["H1"] > 0) & (tab["H2"] > 0)).sum()
    print("判定（13 §9 / 18 §12）：")
    print(f"  · 无条件基线中，两半样本净 R 均为正的: {int(base_ok)}/{len(tab)}")
    print(f"  · 条件化分组跨半样本符号一致性: {rate:.1%}（随机基准 50%）")
    if base_ok == 0 and rate < 0.65:
        print("  → **闸门不通过。** 条件化未显示可靠的增量，且无条件基线在样本外不稳定。")
        print("    按 16 §14 / 18 §9，负结果是一等公民结论，不进入 Phase 5。")
    else:
        print("  → 存在值得进一步检验的分组，但仍需真正的样本外（walk-forward）验证。")
    print("=" * 78)


if __name__ == "__main__":
    main()
