"""统计闸门 —— 13 §9 链路第 2–4 步，本方案的判据。

对每个事件模拟一次括号交易（次日开盘入场，按波动尺度设止损/目标），
计算毛 R 与净 R（扣手续费与资金费），然后回答：

  在**条件化之后**（状态 / 位置 / 回调深度 / 突破质量），
  净期望是否显著优于**同事件的无条件基线**？

纪律（18 §7.3、§12）：
  · 严禁只报混合平均 —— 所有结论按状态与品种分层。
  · 所有试验写入 trial_ledger.csv，用于多重检验校正。
  · 负结果是合法结论，不粉饰。

运行：python pa_research/scripts/02_stat_gate.py --tf 4h
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "pa_research"))

REPORTS = REPO_ROOT / "pa_research/reports"
LEDGER = REPO_ROOT / "pa_research/trial_ledger.csv"

from modules.bracket import Bracket, run_backtest  # noqa: E402


def cluster_bootstrap_ci(
    df: pd.DataFrame, value_col: str, n_boot: int = 2000, seed: int = 7
) -> tuple[float, float, float]:
    """按**品种聚类**重采样的均值置信区间（18 §12：跨品种相关会高估样本量）。

    返回 (mean, lo95, hi95)。
    """
    if df.empty:
        return np.nan, np.nan, np.nan
    syms = df["symbol"].unique()
    groups = {s: df.loc[df["symbol"] == s, value_col].to_numpy() for s in syms}
    rng = np.random.default_rng(seed)
    means = np.empty(n_boot)
    k = len(syms)
    for b in range(n_boot):
        pick = rng.integers(0, k, k)
        vals = np.concatenate([groups[syms[j]] for j in pick])
        means[b] = vals.mean()
    return float(df[value_col].mean()), float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def t_stat_by_time(df: pd.DataFrame, value_col: str) -> float:
    """按时间的 t 统计量（18 §1：t ≈ SR·√years），用于横向比较。"""
    if df.empty or df[value_col].std() == 0:
        return np.nan
    return float(df[value_col].mean() / df[value_col].std() * np.sqrt(len(df)))


def add_trial(rows: list[dict], **kw) -> None:
    rows.append(kw)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tf", default="4h")
    args = ap.parse_args()

    ev = pd.read_parquet(REPORTS / f"events_{args.tf}.parquet")
    print(f"载入 {len(ev)} 事件，{ev['symbol'].nunique()} 品种，{args.tf}\n")

    trial_rows: list[dict] = []

    # ---- 试验 1：基准括号 (1R 止损 / 2R 目标 / 12 根) ----
    br = Bracket(stop_mult=1.0, target_mult=2.0, horizon=12)
    res = run_backtest(ev, args.tf, br)
    res.to_parquet(REPORTS / f"gate_{args.tf}.parquet", index=False)

    print("=" * 78)
    print(f"括号参数：止损 {br.stop_mult}R / 目标 {br.target_mult}R / 上限 {br.horizon} 根")
    print("=" * 78)
    print(f"中位费/R = {res['fee_R'].median():.4f}   中位资金费/R = {res['funding_R'].median():.4f}")
    print(f"中位止损宽度（相对价格）= {res['stop_dist_rel'].median():.4%}\n")

    print("── 1. 无条件基线（**注意：这不是结论，只是对照**）──")
    base = res.groupby("event_type").agg(
        n=("net_R", "size"),
        gross_R=("gross_R", "mean"),
        net_R=("net_R", "mean"),
        win=("net_R", lambda s: (s > 0).mean()),
        t=("net_R", lambda s: t_stat_by_time(pd.DataFrame({"net_R": s}), "net_R")),
    )
    print(base.round(4).to_string())
    print("  （t 为按事件的 t 统计量，仅供横向比较；跨品种相关会使其偏高，"
          "以第 3 节聚类 bootstrap 为准）")

    # ---- 2. 按状态分层（18 §7.3：严禁只给混合平均）----
    print("\n── 2. 按市场状态分层（净 R）──")
    for et in sorted(res["event_type"].unique()):
        sub = res[res["event_type"] == et]
        tab = sub.groupby("context_state").agg(n=("net_R", "size"), net_R=("net_R", "mean"))
        print(f"\n  {et}:")
        print(tab.round(4).to_string())

    # ---- 3. 条件化增量检验：分位分组 vs 无条件 ----
    print("\n── 3. 条件化增量检验（聚类 bootstrap，95% CI）──")
    buckets = {
        "price_action": [
            ("突破距离", "feat_breakout_distance", ["low", "mid", "high"]),
            ("收盘质量", "feat_breakout_close_quality", ["low", "mid", "high"]),
            ("回调深度", "pa_pullback_depth", ["shallow", "mid", "deep"]),
        ],
        "location": [
            ("区间位置", "pa_range_position", ["low", "mid", "high"]),
            ("到摆动高点距离", "pa_distance_to_swing_high", ["near", "mid", "far"]),
            ("到摆动低点距离", "pa_distance_to_swing_low", ["near", "mid", "far"]),
        ],
    }
    for dim, items in buckets.items():
        print(f"\n  【{dim}】")
        for name, col, labels in items:
            print(f"    {name} ({col})")
            for et in sorted(res["event_type"].unique()):
                sub = res[(res["event_type"] == et) & res[col].notna()].copy()
                if len(sub) < 200:
                    continue
                # 分位分组；数据有大量并列时可能塌缩为更少的桶，故按实际桶数取名
                codes = pd.qcut(sub[col], 3, labels=False, duplicates="drop")
                n_bins = int(codes.max()) + 1 if codes.notna().any() else 0
                if n_bins < 2:
                    continue
                names = {2: ["low", "high"], 3: labels}.get(n_bins, [f"q{i}" for i in range(n_bins)])
                sub["bucket"] = codes.map(lambda c: names[int(c)] if pd.notna(c) else None)

                m, lo, hi = cluster_bootstrap_ci(sub, "net_R")
                line = f"      {et:<14} 无条件 {m:+.4f} [{lo:+.4f},{hi:+.4f}] | "
                parts = []
                for lab in names:
                    g = sub[sub["bucket"] == lab]
                    if len(g) < 30:
                        continue
                    gm, glo, ghi = cluster_bootstrap_ci(g, "net_R")
                    sig = "*" if (glo > 0) != (ghi > 0) else " "
                    parts.append(f"{lab}={gm:+.4f}[{glo:+.4f},{ghi:+.4f}]{sig}")
                    add_trial(
                        trial_rows, tf=args.tf, dim=dim, variable=col, event_type=et,
                        bucket=str(lab), n=len(g), mean_net_R=gm, ci_lo=glo, ci_hi=ghi,
                    )
                print(line + " ".join(parts))
    print("\n  注：* 表示该分组 95% CI 不跨 0（聚类 bootstrap，按品种重采样）")

    # ---- 4. 逐品种（18 §7.3）----
    print("\n── 4. 逐品种净 R（看结论是否只由少数品种贡献）──")
    per_sym = res.groupby("symbol").agg(n=("net_R", "size"), net_R=("net_R", "mean"))
    per_sym = per_sym.sort_values("net_R", ascending=False)
    print(f"  品种数 {len(per_sym)}；净 R 为正的品种 {int((per_sym['net_R'] > 0).sum())}")
    print(per_sym.round(4).to_string())

    # ---- 5. trial ledger ----
    if trial_rows:
        led = pd.DataFrame(trial_rows)
        if LEDGER.exists():
            led = pd.concat([pd.read_csv(LEDGER), led], ignore_index=True)
        led.to_csv(LEDGER, index=False)
        print(f"\n试验记录写入 {LEDGER}（累计 {len(led)} 行）")


if __name__ == "__main__":
    main()
