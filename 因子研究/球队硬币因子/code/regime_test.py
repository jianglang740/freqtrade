#!/usr/bin/env python
"""
直接检验研报的核心机制：「硬币态才反转，球队态不反转」。

研报把「球队/硬币」拆成两个可观测代理：
    波动状态：20 日波动率 < 当日截面均值 → 硬币态
    换手状态：ΔTO < 当日截面均值          → 硬币态
理论上，**原始的 20 日收益因子**在硬币态里应该显著反转（IC 为负），
在球队态里应该不反转（IC 接近 0 或为正）。这正是 flips 想利用的东西。

这个脚本不再造因子，只按状态把截面切成两半，分别算：
    IC(20 日平均收益, 下月收益) 与 Q1−Q5 的平均远期收益差
如果两半没有差别，说明「状态划分」这件事在币圈不成立，
球队硬币因子就只是「20 日反转 × 波动率」的另一种写法。

用法：
    python 因子研究/球队硬币因子/code/regime_test.py --market um
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

import engine as E                       # noqa: E402
from stats import forward_returns, winsorize_rows  # noqa: E402

OUT = os.path.join(PROJECT, "out")


def _ic(f: pd.Series, r: pd.Series) -> float:
    m = f.notna() & r.notna()
    if int(m.sum()) < 10:
        return np.nan
    return float(f[m].rank().corr(r[m].rank()))


def _spread(f: pd.Series, r: pd.Series, q: int = 5) -> tuple[float, float]:
    """截面分 q 组，返回 (Q1 平均, Q5 平均) 的原始远期收益。"""
    m = f.notna() & r.notna()
    if int(m.sum()) < q:
        return np.nan, np.nan
    rank = f[m].rank(method="first")
    grp = np.ceil(rank / len(rank) * q)
    return float(r[m][grp == 1].mean()), float(r[m][grp == q].mean())


def main() -> None:
    ap = argparse.ArgumentParser(description="球队/硬币状态机制检验")
    ap.add_argument("--market", default="um", choices=["um", "spot"])
    ap.add_argument("--rebalance", default="monthly")
    ap.add_argument("--top-n", type=int, default=80)
    ap.add_argument("--min-obs", type=int, default=15)
    args = ap.parse_args()

    panel_path = os.path.join(OUT, f"factor_{args.market}_h8_adv.parquet")
    panel = pd.read_parquet(panel_path, columns=["date", "symbol", "fct_interday_raw",
                                                 "_sigma_inter", "_dturn"])
    if str(panel["symbol"].dtype) == "category":
        panel["symbol"] = panel["symbol"].astype(str)

    cfg = E.Config(market=args.market, tf="1d", factor="fct_interday_raw",
                   factor_panel=panel_path, rebalance=args.rebalance,
                   hold_windows=(1,), top_n=args.top_n, min_obs=args.min_obs)
    px, bar_dates = E.load_bars(cfg)
    reb = E.build_rebalances(bar_dates, cfg.rebalance, cfg.every)
    P, _ = E.align_entry_prices(px, reb)
    univ = E.build_universe(cfg, pd.DatetimeIndex(P.index)).reindex(columns=P.columns).fillna(False)
    fwd = winsorize_rows(forward_returns(P, 1), 0.01)

    def wide(col: str) -> pd.DataFrame:
        return (panel[panel["date"].isin(reb)]
                .pivot(index="date", columns="symbol", values=col)
                .reindex(index=P.index, columns=P.columns))

    raw = wide("fct_interday_raw")
    sig = wide("_sigma_inter")
    dto = wide("_dturn")

    rows = []
    for name, state, lower_is_coin in (("波动率", sig, True), ("换手变化", dto, True)):
        cs = state.mean(axis=1)                      # 全截面（非池内）均值，与造因子一致
        coin = state.lt(cs, axis=0) if lower_is_coin else state.gt(cs, axis=0)
        for regime, mask in (("硬币态", coin), ("球队态", ~coin)):
            ics, q1s, q5s, ns = [], [], [], []
            for t in P.index:
                m = (mask.loc[t] & univ.loc[t] & raw.loc[t].notna()).to_numpy(dtype=bool)
                if m.sum() < 10:
                    continue
                f = raw.loc[t].to_numpy(dtype=float)[m]
                r = fwd.loc[t].to_numpy(dtype=float)[m]
                ok = np.isfinite(f) & np.isfinite(r)
                if ok.sum() < 10:
                    continue
                ics.append(_ic(pd.Series(f[ok]), pd.Series(r[ok])))
                a, b = _spread(pd.Series(f[ok]), pd.Series(r[ok]))
                q1s.append(a)
                q5s.append(b)
                ns.append(int(ok.sum()))
            rows.append({
                "状态变量": name, "状态": regime,
                "截面数": len(ics), "平均覆盖": np.mean(ns) if ns else np.nan,
                "IC均值": np.nanmean(ics) if ics else np.nan,
                "Q1均值": np.nanmean(q1s) if q1s else np.nan,
                "Q5均值": np.nanmean(q5s) if q5s else np.nan,
                "Q1−Q5": (np.nanmean(q1s) - np.nanmean(q5s)) if q1s else np.nan,
            })

    t = pd.DataFrame(rows)
    print(f"\n===== {args.market} / {args.rebalance} / 20 日平均收益因子的条件 IC =====")
    print("（Q1 = 过去 20 日平均收益最低，Q5 = 最高；Q1−Q5 为缩尾后的平均远期收益差）")
    print(t.to_string(index=False, float_format=lambda v: f"{v:+.4f}"))

    path = os.path.join(OUT, f"机制检验_{args.market}.csv")
    t.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"\n已写：{os.path.relpath(path, FACTOR_ROOT)}")


if __name__ == "__main__":
    main()
