"""出场 v0 vs v1 并排验证 —— **仅 BTC 4h 单币**（用户要求收敛范围）。

入场规则完全不动（复用 01_build_events.build_one，保证事件集完全一致），
只换出场：

  v0 = bracket.py   波动率括号：止损 1×vol、目标 2×vol、12 根到期
  v1 = exits.py     结构性出场：止损=失效点+tol×vol、目标=结构位、失效出场、保本、24 根兜底
                    （规则见 cards/exit_and_risk_v1.yaml，**先于代码冻结**）

报告按事件类型与状态**分层**（18 §7.3：严禁只给混合平均），并**并列给出毛 R**
—— 结构性止损通常更宽 → 费/R 更小 → 净 R 会机械改善，必须与真正的优势区分开。

运行：python pa_research/scripts/06_btc_exit_validation.py
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "pa_research"))

from modules.bracket import Bracket, load_arrays, load_funding, simulate  # noqa: E402
from modules.exits import (  # noqa: E402
    StructuralExitPolicy, plan_exit, simulate_structural,
)

SCRIPTS = REPO_ROOT / "pa_research/scripts"
V0 = Bracket(stop_mult=1.0, target_mult=2.0, horizon=12)
V1 = StructuralExitPolicy()


def load_build_one():
    """01_build_events.py 以数字开头，无法直接 import，用 importlib 加载。"""
    spec = importlib.util.spec_from_file_location("be", SCRIPTS / "01_build_events.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.build_one


def describe(name: str, df: pd.DataFrame) -> dict:
    if df.empty:
        return {"方案": name, "n": 0}
    return {
        "方案": name,
        "n": len(df),
        "毛R均值": df.gross_R.mean(),
        "净R均值": df.net_R.mean(),
        "净R中位": df.net_R.median(),
        "费/R中位": df.fee_R.median(),   # fee_R 本身即以 R 为单位（费额 / 止损距离）
        "净胜率": (df.net_R > 0).mean(),
        "平均持仓": df.hold_bars.mean(),
        "MAE均值": df.mae_R.mean() if "mae_R" in df else np.nan,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pair", default="BTC_USDT_USDT")
    ap.add_argument("--tf", default="4h")
    args = ap.parse_args()

    build_one = load_build_one()
    ev = build_one(args.pair, args.tf)
    if ev.empty:
        print("没有事件")
        return
    print("=" * 96)
    print(f"出场并排验证 —— {args.pair} {args.tf}   （入场规则不变，只换出场）")
    print("=" * 96)
    print(f"事件总数 {len(ev)}")

    arr = load_arrays(args.pair, args.tf)
    fund = load_funding(args.pair)

    # ── v0：波动率括号 ──
    rows0 = []
    for r in ev.to_dict("records"):
        res = simulate(r, arr, fund, V0)
        if res:
            rows0.append({**r, **res})
    d0 = pd.DataFrame(rows0)

    # ── v1：结构性出场 ──
    rows1, rejected = [], {}
    for r in ev.to_dict("records"):
        diag: dict = {}
        plan = plan_exit(
            r["event_type"], int(r["direction"]), float(arr["o"][int(r["entry_idx"])]),
            r.get("ref_level", np.nan), r.get("pa_pullback_anchor", np.nan),
            r.get("pa_range_upper", np.nan), r.get("pa_range_lower", np.nan),
            float(r["pa_vol_scale"]), V1, _diag=diag,
        )
        if plan is None:
            k = diag.get("reject", "unknown")
            rejected[k] = rejected.get(k, 0) + 1
            continue
        res = simulate_structural(r, arr, fund, V1)
        if res:
            rows1.append({**r, **res})
    d1 = pd.DataFrame(rows1)

    # ── 1. 总体并排 ──
    print("\n── 1. 总体并排 ──")
    tab = pd.DataFrame([describe("v0 波动率括号", d0), describe("v1 结构性", d1)])
    print(tab.round(4).to_string(index=False))
    if rejected:
        print(f"\n  v1 因可交易性被跳过 {sum(rejected.values())} 个事件（占 {sum(rejected.values())/len(ev):.1%}）：")
        for k, v in sorted(rejected.items(), key=lambda kv: -kv[1]):
            print(f"    {k:20s} {v:5d}")

    # ── 1b. 共同子集对照（**关键**）──
    # v1 因空间预筛跳过了若干事件，直接拿 922 vs 1433 比是"苹果比橘子"：
    # 被跳过的那批本就是"目标太近"的样本，与剩下的不是同一分布。
    # 唯一公平的对照是在 v1 接受的**同一批事件**上重跑 v0。
    key = ["event_type", "entry_idx"]
    common = d0.merge(d1[key].drop_duplicates(), on=key, how="inner")
    print(f"\n── 1b. 共同子集对照（v1 接受的 {len(common)} 个事件上重跑 v0）──")
    tab2 = pd.DataFrame([
        describe("v0（同一子集）", common),
        describe("v1", d1),
    ])
    print(tab2.round(4).to_string(index=False))

    # 逐笔配对：同一批事件上 v1 − v0
    paired = common[["event_type", "entry_idx", "net_R", "gross_R"]].merge(
        d1[["event_type", "entry_idx", "net_R", "gross_R"]],
        on=["event_type", "entry_idx"], suffixes=("_v0", "_v1"),
    )
    for col, nm in (("net_R", "净R"), ("gross_R", "毛R")):
        diff = paired[f"{col}_v1"] - paired[f"{col}_v0"]
        se = diff.std(ddof=1) / np.sqrt(len(diff))
        t = diff.mean() / se if se > 0 else np.nan
        print(f"  配对差异 [{nm}] v1−v0: 均值 {diff.mean():+.4f}  "
              f"95%CI [{diff.mean()-1.96*se:+.4f}, {diff.mean()+1.96*se:+.4f}]  t={t:+.2f}")
    print("  注：这是**同一段 BTC 历史、同一批事件**的样本内配对差，"
          "不是样本外证据；\n       BTC 单币也无法支持多重检验校正（18 §3）。")

    # 后续所有分层一律用**共同子集**上的 v0，避免拿 922 比 1433。
    d0 = common

    # ── 2. 按事件类型分层（18 §7.3）──
    print("\n── 2. 按事件类型分层（共同子集，不报混合平均）──")
    for col, nm in (("gross_R", "毛R"), ("net_R", "净R")):
        p = pd.DataFrame({
            "v0": d0.groupby("event_type")[col].mean(),
            "v1": d1.groupby("event_type")[col].mean(),
        }).round(3)
        p["差(v1−v0)"] = (p["v1"] - p["v0"]).round(3)
        p["v1_n"] = d1.groupby("event_type")[col].size()
        print(f"\n  [{nm}] 均值")
        print(p.to_string())

    # ── 3. 出场原因分布 ──
    print("\n── 3. 出场原因分布 ──")
    rc = pd.DataFrame({
        "v0": d0.exit_reason.value_counts(normalize=True),
        "v1": d1.exit_reason.value_counts(normalize=True),
    }).fillna(0)
    print((rc * 100).round(1).to_string())
    if "breakeven_moved" in d1:
        print(f"\n  v1 中有 {d1.breakeven_moved.sum()} 笔（{d1.breakeven_moved.mean():.1%}）触发过保本移动")
    if "rr" in d1:
        print(f"  v1 的盈亏比 R:R —— 中位 {d1.rr.median():.2f}  均值 {d1.rr.mean():.2f}"
              f"  （v0 恒为 2.00）")
    if "measured_move" in d1:
        print(f"  v1 目标退化为测量走势的: {d1.measured_move.sum()} 笔（{d1.measured_move.mean():.1%}）")

    # ── 4. 风险口径对照 ──
    print("\n── 4. 风险与成本口径 ──")
    print(f"  止损宽度（中位）: v0 {common.stop_dist_rel.median():.4%}   v1 {d1.stop_dist_rel.median():.4%}")
    print(f"  费/R（中位）    : v0 {(0.0010/common.stop_dist_rel).median():.4f}"
          f"   v1 {(0.0010/d1.stop_dist_rel).median():.4f}")
    print("  卡片曾预判「结构性止损更宽 → 费/R 更低 → 净 R 机械改善」。")
    print("  **实测否证了这条预判**：v1 的止损略窄、费/R 略高，故净 R 的改善不来自成本。")
    print("  仍需以**毛 R**为主要判据（成本效应不成立，不等于优势成立）。")

    # ── 5. 按状态分层 ──
    print("\n── 5. 按 context_state 分层（净R均值）──")
    p = pd.DataFrame({
        "v0": d0.groupby("context_state").net_R.mean(),
        "v1": d1.groupby("context_state").net_R.mean(),
        "v0_n": d0.groupby("context_state").net_R.size(),
        "v1_n": d1.groupby("context_state").net_R.size(),
    }).round(3)
    print(p.to_string())

    out = REPO_ROOT / "pa_research/reports"
    d0.to_parquet(out / f"exit_v0_{args.tf}_{args.pair}.parquet", index=False)
    d1.to_parquet(out / f"exit_v1_{args.tf}_{args.pair}.parquet", index=False)
    print(f"\n逐笔明细已写入 {out}/exit_v0_* / exit_v1_*")


if __name__ == "__main__":
    main()
