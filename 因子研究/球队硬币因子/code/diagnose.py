#!/usr/bin/env python
"""
球队硬币因子的诊断：为什么 IC 很强、组合却可能不赚钱。

回答三个问题：
  1. 分位收益在**真实收益**（不缩尾）下还单调吗？
     ｜ 回归框架的 `分组远期收益` 用 1%/99% 缩尾，币圈每月都有几只翻倍币，
     ｜ 缩尾后 Q5 的右尾被剪掉，均值会失真。
  2. 多空的钱是哪条腿赚的？逐分位单腿做多的年化是多少？
  3. 因子和已公共因子（vol_30d / mom_7d / rev_1d）的关系。

用法：
    python 因子研究/球队硬币因子/code/diagnose.py --market um --factor fct_team_coin
"""

from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
FACTOR_ROOT = os.path.dirname(PROJECT)
for _p in (os.path.join(FACTOR_ROOT, "行情数据"),
           os.path.join(FACTOR_ROOT, "因子数据"),
           os.path.join(FACTOR_ROOT, "回测框架")):
    sys.path.insert(0, _p)

import engine as E          # noqa: E402
import factor_io            # noqa: E402
from stats import forward_returns, winsorize_rows  # noqa: E402

OUT = os.path.join(PROJECT, "out")


def _ann(ret: pd.Series, days) -> float:
    """单腿年化：按持有天数折算的几何年化（days 与 ret 行对齐，是 numpy 数组）。"""
    r = np.asarray(ret, dtype="float64")
    d = np.asarray(days, dtype="float64")
    ok = np.isfinite(r) & np.isfinite(d)
    if ok.sum() < 3 or d[ok].sum() <= 0:
        return np.nan
    return float(np.prod(1.0 + r[ok]) ** (365.0 / d[ok].sum()) - 1.0)


def main() -> None:
    ap = argparse.ArgumentParser(description="球队硬币因子诊断")
    ap.add_argument("--market", default="um", choices=["um", "spot"])
    ap.add_argument("--factor", default="fct_team_coin")
    ap.add_argument("--rebalance", default="monthly", choices=["weekly", "monthly"])
    ap.add_argument("--top-n", type=int, default=80)
    ap.add_argument("--min-obs", type=int, default=15)
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--no-funding", action="store_true", help="um 也关掉资金费，隔离其影响")
    args = ap.parse_args()

    panel = os.path.join(OUT, f"factor_{args.market}_h8_adv.parquet")
    cfg = E.Config(market=args.market, tf="1d", factor=args.factor,
                   factor_panel=panel, direction="short_top",
                   rebalance=args.rebalance, hold_windows=(1,),
                   top_n=args.top_n, min_obs=args.min_obs,
                   start=args.start, end=args.end,
                   use_funding=not args.no_funding)
    res = E.run(cfg, verbose=False)

    h = 1
    eff = res["variants"][res["effective"]]
    F = eff["factor"]
    Q = eff["quantiles"]

    # 重算建仓价与远期收益（引擎不把 P 返回出来）
    px, bar_dates = E.load_bars(cfg)
    reb = E.build_rebalances(bar_dates, cfg.rebalance, cfg.every)
    P, _ = E.align_entry_prices(px, reb)
    fwd = forward_returns(P, h, cfg.delist_shock)
    fwd_w = winsorize_rows(fwd)

    print(f"\n===== {args.market} / {args.factor} / {args.rebalance} / 持有 {h} 窗口 =====")
    print(f"有效截面 {int(Q.notna().any(axis=1).sum())} 个")

    rows = []
    for q in range(1, cfg.quantiles + 1):
        m = Q.eq(q)
        raw = fwd.where(m).stack().dropna()
        win = fwd_w.where(m).stack().dropna()
        sim = eff["qsims"][h][q]
        rows.append({
            "分位": q,
            "样本": len(raw),
            "原始均值": raw.mean(),
            "缩尾均值": win.mean(),
            "中位数": raw.median(),
            "偏度": raw.skew(),
            "p95": raw.quantile(0.95),
            "单腿年化": _ann(sim["net"], sim["days"]),
            "单腿年化(毛)": _ann(sim["gross"], sim["days"]) if "gross" in sim else np.nan,
        })
    t = pd.DataFrame(rows).set_index("分位")
    print("\n=== 分位收益（真实 vs 缩尾）===")
    print(t.to_string(float_format=lambda v: f"{v:+.4f}" if abs(v) < 10 else f"{v:.3f}"))

    print("\n=== 原始收益的分位单调性（只看真实均值）===")
    print("  Q1→Q5 原始均值：", " / ".join(f"{v:+.4f}" for v in t["原始均值"]))
    print("  Q1→Q5 中位数  ：", " / ".join(f"{v:+.4f}" for v in t["中位数"]))

    print("\n=== 多空组合分年度（short_top = 多低因子、空高因子）===")
    print(res["tables"]["分年度"].to_string(index=False,
          float_format=lambda v: f"{v:+.2%}"))

    # 与公共因子的截面秩相关
    try:
        cols = ["mom_7d", "rev_1d", "vol_30d", "volume_z_30d"]
        pub = pd.read_parquet(factor_io.panel_path(args.market, "1d"),
                              columns=["date", "symbol"] + cols)
        if str(pub["symbol"].dtype) == "category":
            pub["symbol"] = pub["symbol"].astype(str)
        # 把 F（宽表）转成长表再 merge
        fl = F.stack().rename("f").reset_index()
        fl.columns = ["date", "symbol", "f"]
        m = fl.merge(pub, on=["date", "symbol"], how="inner")
        print("\n=== 与公共因子的截面秩相关（均值） ===")
        for c in cols:
            r = (m.dropna(subset=["f", c]).groupby("date")
                   .apply(lambda g: g["f"].rank().corr(g[c].rank())
                          if len(g) >= 20 else np.nan).mean())
            print(f"  {c:>16}: {r:+.3f}")
    except FileNotFoundError:
        pass


if __name__ == "__main__":
    main()
