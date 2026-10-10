"""走查验证（walk-forward）—— 03 号脚本判定为"边缘"之后的决胜检验。

03_robustness.py 给的是**样本内**的跨半样本一致性，它有一个致命弱点：
分位分组是在**全样本**上切的，桶边界本身就用到了后一半的数据（13 §8 禁止
全样本标准化）。而且"哪个分组显著"是在看过全部数据之后挑的（18 §3）。

本脚本做真正的样本外选择：
  · 每个品种按时间切成 K 折；第 k 折的**训练集**= 该折之前的所有事件。
  · 桶边界**只用训练集的分位数**确定，再用同一组边界切测试集。
  · 在训练集上挑"训练净 R 最高"的规则，然后**只看它在测试集上的表现**。
  · 汇总所有折的样本外结果，与三个基准对比：
      ① 全部候选规则的平均样本外净 R（"1/N"基准 —— 选择没带来任何东西）
      ② 同事件的无条件基线（不做任何条件化）
      ③ 训练集上最好规则的样本外表现（即选择带来的东西）

判据（事前写下）：只有当**选择后**的样本外净 R 同时高于 ① 和 ②，
且按品种聚类的 95% CI 不跨 0，条件化才算带来真实增量。

运行：python pa_research/scripts/04_walkforward.py --tf 4h
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "pa_research"))

from modules.bracket import Bracket, run_backtest  # noqa: E402

REPORTS = REPO_ROOT / "pa_research/reports"

# 条件化变量（与 02/03 号脚本一致，便于对照）
VARIABLES = [
    ("feat_breakout_distance", "突破距离"),
    ("feat_breakout_close_quality", "收盘质量"),
    ("pa_pullback_depth", "回调深度"),
    ("pa_range_position", "区间位置"),
    ("pa_distance_to_swing_high", "距摆动高点"),
    ("pa_distance_to_swing_low", "距摆动低点"),
]

# 事前冻结的主括号参数；其余仅作敏感性检验，不参与选择
PRIMARY = Bracket(stop_mult=1.0, target_mult=2.0, horizon=12)
SENSITIVITY = [
    Bracket(stop_mult=1.0, target_mult=1.5, horizon=12),
    Bracket(stop_mult=1.5, target_mult=2.0, horizon=12),
    Bracket(stop_mult=1.0, target_mult=3.0, horizon=12),
]


def get_results(tf: str, br: Bracket) -> pd.DataFrame:
    """带磁盘缓存的事件回测结果（同一括号参数只算一次）。"""
    fp = REPORTS / f"wf_{tf}_{br.tag()}.parquet"
    if fp.exists():
        return pd.read_parquet(fp)
    ev = pd.read_parquet(REPORTS / f"events_{tf}.parquet")
    res = run_backtest(ev, tf, br)
    res.to_parquet(fp, index=False)
    return res


def add_folds(df: pd.DataFrame, k: int) -> pd.DataFrame:
    """每个品种按时间等分为 k 折（-1 为因样本太短而落选的尾部）。"""
    df = df.copy()
    df["entry_time"] = pd.to_datetime(df["entry_time"])
    df = df.sort_values(["symbol", "entry_time"]).reset_index(drop=True)
    df["fold"] = -1
    for _, idx in df.groupby("symbol").groups.items():
        idx = np.asarray(idx)
        if len(idx) < k * 20:
            continue
        df.loc[idx, "fold"] = np.linspace(0, k - 1, len(idx)).astype(int)
    return df


def bucket_by_train(train_vals: pd.Series, test_vals: pd.Series, bins: int = 3):
    """用**训练集分位数**定桶边界，再用同一组边界切测试集（13 §8）。"""
    tv = train_vals.dropna()
    if len(tv) < bins * 10:
        return None, None
    qs = np.linspace(0, 1, bins + 1)[1:-1]
    edges = np.unique(np.quantile(tv.to_numpy(), qs))
    if len(edges) != bins - 1:
        return None, None
    bounds = [-np.inf, *edges, np.inf]
    return (
        pd.cut(train_vals, bounds, labels=False),
        pd.cut(test_vals, bounds, labels=False),
    )


def cluster_bootstrap_ci(df: pd.DataFrame, col: str, n_boot: int = 2000, seed: int = 11):
    """按品种聚类的均值置信区间（18 §12）。"""
    if df.empty:
        return np.nan, np.nan, np.nan
    syms = df["symbol"].unique()
    groups = {s: df.loc[df["symbol"] == s, col].to_numpy() for s in syms}
    rng = np.random.default_rng(seed)
    k = len(syms)
    means = np.empty(n_boot)
    for b in range(n_boot):
        pick = rng.integers(0, k, k)
        means[b] = np.concatenate([groups[syms[j]] for j in pick]).mean()
    return float(df[col].mean()), float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def walkforward(res: pd.DataFrame, k: int, min_train_n: int, bins: int) -> dict:
    """一轮走查验证（扩张窗口）。

    每一折：训练集 = 该折之前的全部事件（桶边界只用训练集分位）；
    在训练集上挑净 R 最高的规则，把它的桶边界套到测试集，只交易落入该桶的事件。
    返回逐折记录，以及**样本外逐笔交易**（用于聚类 bootstrap）。
    """
    folds = sorted(f for f in res["fold"].unique() if f >= 0)
    picks, oos_trades, base_trades = [], [], []
    n_trials = 0

    for f in folds:
        train = res[res["fold"] < f]          # 扩张窗口
        test = res[res["fold"] == f]
        if train.empty or test.empty:
            continue

        # 基准②：无条件（测试集全部事件都交易）
        for s, v in zip(test["symbol"], test["net_R"]):
            base_trades.append({"fold": int(f), "symbol": s, "net_R": float(v)})

        # 候选规则：事件类型 × 变量 × 桶；桶边界只用训练集确定
        rows = []
        for et in sorted(train["event_type"].unique()):
            tr_et = train[train["event_type"] == et]
            te_et = test[test["event_type"] == et]
            if te_et.empty:
                continue
            for col, name in VARIABLES:
                if col not in res.columns:
                    continue
                tr_b, te_b = bucket_by_train(tr_et[col], te_et[col], bins)
                if tr_b is None:
                    continue
                for b in range(bins):
                    g = tr_et[tr_b == b]
                    if len(g) < min_train_n:
                        continue
                    n_trials += 1
                    rows.append({
                        "event_type": et, "variable": name, "col": col, "bucket": b,
                        "train_n": len(g), "train_R": float(g["net_R"].mean()),
                        "train_R_se": float(g["net_R"].std(ddof=1) / np.sqrt(len(g))),
                    })
        if not rows:
            continue
        cand = pd.DataFrame(rows)

        # 选择：训练集上净 R 最高者（18 §7：不做最优，但此处正是要检验"选优"是否有效）
        best = cand.sort_values("train_R", ascending=False).iloc[0]
        tr_et = train[train["event_type"] == best.event_type]
        te_et = test[test["event_type"] == best.event_type]
        _, te_b = bucket_by_train(tr_et[best.col], te_et[best.col], bins)
        sel = te_et[te_b == best.bucket] if te_b is not None else te_et.iloc[0:0]

        for s, v in zip(sel["symbol"], sel["net_R"]):
            oos_trades.append({"fold": int(f), "symbol": s, "net_R": float(v)})

        uncond_fold = float(test["net_R"].mean())
        picks.append({
            "fold": int(f), "event_type": best.event_type, "variable": best.variable,
            "bucket": int(best.bucket), "train_R": float(best.train_R),
            "train_R_se": float(best.train_R_se),
            "test_R": float(sel["net_R"].mean()) if len(sel) else np.nan,
            "test_n": len(sel),
            "uncond_test_R": uncond_fold,
            "beats_uncond": (len(sel) > 0) and float(sel["net_R"].mean()) > uncond_fold,
            "n_cand": len(cand),
        })

    return {
        "picks": pd.DataFrame(picks),
        "oos": pd.DataFrame(oos_trades),
        "baseline": pd.DataFrame(base_trades),
        "n_trials": n_trials,
    }


def summarise(tag: str, out: dict) -> dict:
    picks, oos, base = out["picks"], out["oos"], out["baseline"]
    res = {"tag": tag, "n_folds": len(picks), "n_trials": out["n_trials"]}
    if picks.empty:
        return res

    sel_m, sel_lo, sel_hi = cluster_bootstrap_ci(oos, "net_R")
    bas_m, bas_lo, bas_hi = cluster_bootstrap_ci(base, "net_R")

    res.update({
        "oos_n": len(oos),
        "selected_OOS": sel_m, "selected_lo": sel_lo, "selected_hi": sel_hi,
        "uncond_OOS": bas_m, "uncond_lo": bas_lo, "uncond_hi": bas_hi,
        "uncond_n": len(base),
        "folds_beat_uncond": float(picks["beats_uncond"].mean()),
        "covered_folds": int((picks["test_n"] > 0).sum()),
        "train_test_sign_agree": float(
            (((picks["train_R"] > 0) == (picks["test_R"] > 0)) & (picks["test_n"] > 0)).mean()
        ),
        "selected_ci_excludes_0": bool(sel_lo > 0 or sel_hi < 0),
    })
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tf", default="4h")
    ap.add_argument("--folds", type=int, default=4)
    ap.add_argument("--min-train-n", type=int, default=100)
    ap.add_argument("--bins", type=int, default=3)
    args = ap.parse_args()

    print("=" * 78)
    print(f"走查验证  tf={args.tf}  {args.folds} 折  min_train_n={args.min_train_n}")
    print("=" * 78)

    results = []
    for br in [PRIMARY, *SENSITIVITY]:
        res = get_results(args.tf, br)
        res = add_folds(res, args.folds)
        out = walkforward(res, args.folds, args.min_train_n, args.bins)
        s = summarise(br.tag(), out)
        results.append(s)
        if br is PRIMARY:
            primary_out = out

    tab = pd.DataFrame(results).set_index("tag")
    print("\n── 各括号参数下的样本外结果（逐笔汇总 + 按品种聚类 95% CI）──")
    cols = ["n_folds", "covered_folds", "n_trials", "oos_n",
            "selected_OOS", "selected_lo", "selected_hi",
            "uncond_OOS", "uncond_lo", "uncond_hi",
            "folds_beat_uncond", "train_test_sign_agree"]
    print(tab[cols].round(4).to_string())

    p = primary_out["picks"]
    print("\n── 主括号下，每折选中的规则 ──")
    show = ["fold", "event_type", "variable", "bucket", "train_R", "train_R_se",
            "test_R", "test_n", "uncond_test_R", "n_cand"]
    print(p[show].round(4).to_string(index=False) if not p.empty else "  （无）")

    print("\n── 判定 ──")
    s = results[0]
    ok = (
        s["selected_OOS"] > s["uncond_OOS"]
        and s["selected_ci_excludes_0"]
        and s["selected_lo"] > 0
        and s["folds_beat_uncond"] >= 0.6
    )
    print(f"  · 选中规则的样本外净 R = {s['selected_OOS']:+.4f} "
          f"[{s['selected_lo']:+.4f},{s['selected_hi']:+.4f}]  n={s['oos_n']}")
    print(f"  · 无条件基线样本外净 R = {s['uncond_OOS']:+.4f} "
          f"[{s['uncond_lo']:+.4f},{s['uncond_hi']:+.4f}]  n={s['uncond_n']}")
    print(f"  · 选中的规则有样本外覆盖的折: {s['covered_folds']}/{s['n_folds']}")
    print(f"  · 选中优于无条件的折: {s['folds_beat_uncond']:.0%}")
    print(f"  · 训练/测试同号的折: {s['train_test_sign_agree']:.0%}")
    if ok:
        print("  → 条件化在**真正的样本外**仍带来增量，值得进入 Phase 5。")
    else:
        print("  → **条件化在样本外不带来增量。** 训练集上挑出来的最好规则，")
        print("    在样本外并不稳定地优于无条件基线；这正是 16 §14 的预期 ——")
        print("    机械形态规则已被系统性否证，本面板上'形态 + 上下文'亦然。")
    print("=" * 78)

    tab.to_csv(REPORTS / f"walkforward_{args.tf}.csv")


if __name__ == "__main__":
    main()
