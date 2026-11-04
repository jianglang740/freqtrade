#!/usr/bin/env python
"""
ADV 回看周期扫描：7 / 14 / 28 / 64 天。

这个维度和 `TOP_N` 不一样：池子始终是前 50 名，改回看周期只改变
「第 45–55 名那几只边缘币谁进来」，所以它首先是**稳定性检验**而不是优化项。

两个要盯的点：
  · 池子差异：不同周期下每天有几个币不同。差得多 → 结果依赖边缘币，是噪音；
  · 冲击成本：**固定用 14 日 ADV**，不跟着扫描走。否则池子口径和成本口径一起变，
    分不清是「选池变了」还是「成本估计变了」。

用法：
    source /opt/anaconda3/etc/profile.d/conda.sh && conda activate freqtrade
    python 策略池/横截面动量/scripts/xsec_adv_window_study.py
"""

from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), "out")

sys.path.insert(0, HERE)
from xsec_all_universe_study import ALL, load_wide  # noqa: E402
from xsec_momentum_study import build_scores, load_funding  # noqa: E402
from xsec_universe_study import (  # noqa: E402
    ANN, EXCLUDE_BASE, VOL_FLOOR, build_pool, metrics, net_with,
    run_portfolio, show, tail_stats, vol_target,
)

WINDOWS = (7, 14, 28, 64)
TOP_N = 50
COST_WINDOW = 14      # 冲击成本固定用 14 日 ADV


def minp(w: int) -> int:
    """滚动窗口至少要凑够七成，否则短周期会被早期稀疏数据放大。"""
    return max(3, int(round(w * 0.7)))


def main() -> None:
    close, qv, alive, last = load_wide(ALL)
    ret = close.pct_change(fill_method=None)
    vol28 = ret.rolling(28, min_periods=20).std()
    vol_ann = vol28 * np.sqrt(ANN)
    adv_cost = qv.rolling(COST_WINDOW, min_periods=10).mean()
    funding = load_funding(close.index, close.columns)

    print("=" * 96)
    print(f"诚实池 {close.shape[1]} 个币，{close.index.min().date()} → {close.index.max().date()}")
    print(f"池子：TOP_N={TOP_N}，已剔除稳定币/法币，波动率地板 {VOL_FLOOR:.0%}；"
          f"冲击成本固定用 {COST_WINDOW} 日 ADV")
    print("\n⚠️ 这是稳定性检验，不是优化。如果 7 天和 64 天差很多，")
    print("   说明结论依赖于边缘币，那本身就是个坏消息。")

    pools, detail = {}, {}
    for w in WINDOWS:
        adv = qv.rolling(w, min_periods=minp(w)).mean()
        pool = build_pool(adv, vol_ann, TOP_N, exclude=frozenset(EXCLUDE_BASE))
        score = build_scores(close, ret, pool)
        pools[w] = pool
        detail[w] = run_portfolio(score, ret, adv_cost, vol28, funding=funding)

    # ── 池子差异
    print("\n" + "=" * 96)
    print("【1】池子差异（相对 14 日口径）")
    print("=" * 96)
    print(f"  {'周期':>6}{'池中位':>8}{'每天换入换出(只)':>16}{'与14日重叠率':>14}")
    base = pools[COST_WINDOW]
    for w in WINDOWS:
        p = pools[w]
        diff = (p != base).sum(axis=1) / 2      # 换掉几个币
        inter = (p & base).sum(axis=1)
        union = (p | base).sum(axis=1).replace(0, np.nan)
        print(f"  {w:>5}d{int(p.sum(axis=1).median()):>8.0f}"
              f"{float(diff.mean()):>16.2f}{float((inter / union).mean()):>14.1%}")

    # ── 主表
    print("\n" + "=" * 96)
    print("【2】主表（纯费率，O2 归一到 20% 波动）")
    print("=" * 96)
    rows = []
    for w in WINDOWS:
        p = vol_target(net_with(detail[w]))
        cur = p.loc["2023-01-01":]
        # 注意顺序：metrics() 自带一个 "方案" 键（默认空串），后写会覆盖标签
        rows.append({
            **metrics(p),
            "方案": f"ADV {w}d",
            "2023+年化": (1 + cur.mean()) ** ANN - 1,
            "2023+Sharpe": cur.mean() / cur.std() * np.sqrt(ANN),
        })
    tab = pd.DataFrame(rows).set_index("方案")
    show(tab[["年化", "波动", "Sharpe", "最大回撤", "Calmar",
              "2023+年化", "2023+Sharpe", "月度胜率"]])

    # ── 尾部
    print("\n" + "=" * 96)
    print("【3】尾部指标（O2 归一后）")
    print("=" * 96)
    show(pd.DataFrame([{**tail_stats(vol_target(net_with(detail[w]))),
                        "方案": f"ADV {w}d"}
                       for w in WINDOWS]).set_index("方案"))

    # ── 容量
    print("\n" + "=" * 96)
    print("【4】容量（2023 年起，k=0.3，O2 归一后年化）")
    print("=" * 96)
    aums = [1e6, 3e6, 1e7, 3e7]
    cap = {}
    for w in WINDOWS:
        cap[f"ADV {w}d"] = [
            metrics(vol_target(net_with(detail[w], aum=a, k_imp=0.3)).loc["2023-01-01":])["年化"]
            for a in aums]
    capdf = pd.DataFrame(cap, index=[f"{a / 1e6:.0f}M" for a in aums])
    show(capdf, pct=capdf.columns)

    # ── 分年度
    print("\n" + "=" * 96)
    print("【5】分年度净年化（O2 归一后）")
    print("=" * 96)
    print(f"  {'年':>6}" + "".join(f"{w:>9}d" for w in WINDOWS))
    for y in sorted(set(detail[14].index.year)):
        line = f"  {y:>6}"
        for w in WINDOWS:
            p = vol_target(net_with(detail[w]))
            g = p[p.index.year == y]
            line += f"{(1 + g.mean()) ** ANN - 1:>10.1%}" if len(g) > 60 else f"{'—':>10}"
        print(line)

    # ── 与 14 日的相关性
    print("\n【6】O2 归一后，各周期日收益与 14 日口径的相关性")
    b = vol_target(net_with(detail[COST_WINDOW]))
    for w in WINDOWS:
        if w == COST_WINDOW:
            continue
        x = vol_target(net_with(detail[w]))
        j = pd.concat([b, x], axis=1, keys=["a", "b"]).dropna()
        print(f"  ADV {w:>2}d  相关 {j['a'].corr(j['b']):.3f}")

    os.makedirs(OUT, exist_ok=True)
    tab.to_csv(os.path.join(OUT, "study_adv_window.csv"))
    print(f"\n已写 {os.path.relpath(os.path.join(OUT, 'study_adv_window.csv'), os.path.dirname(os.path.dirname(HERE)))}")


if __name__ == "__main__":
    main()
