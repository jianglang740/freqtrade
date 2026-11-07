#!/usr/bin/env python
"""
区间相对位置因子（RelPos）：close 在过去 n 日高低区间里的位置。

原始想法：close 与过去 7/14/30/60 日最高价、最低价做差，差值越大越超买/超卖。
直接做差有两个问题：绝对价差有量级差异（BTC 和 1000BONK 不可比），
而且高点和低点是两个指标、方向含义不一致。改成区间相对位置后一并解决：

    RelPos_n = (close_t − Low_{[t−n, t−1]}) / (High_{[t−n, t−1]} − Low_{[t−n, t−1]})
    relpos_n = 2 × RelPos_n − 1                      ∈ [−1, 1]

- +1 = 收盘价贴着过去 n 日区间上沿（超买）
- −1 = 贴着下沿（超卖）
- 可以略微超出 [−1, 1]：今天收盘创了 n 日新高/新低 —— 这是突破，不是错误

两个刻意的口径：

1. **区间不含今天**（`shift(1)` 后再 rolling）。含今天的话，收盘价必然落在
   当日 high/low 之间，RelPos 会被当天自身的高低点机械地拉向 0.5，
   同时「突破」这个信息也被吃掉了。
2. **用真实 high/low，不用 close 的 max/min**。插针、上下影线是真实成交过的价格，
   用 close 会低估区间宽度，把小币的极端波动洗掉。

除零保护：过去 n 日 (High−Low)/close < `MIN_RANGE`（默认 0.5%）时置 NaN ——
稳定币、连续一字板、长时间停牌的区间宽度接近 0，比值没有意义。

产物：out/factor_{market}_1d.parquet
    列 = date / symbol / relpos_7d / relpos_14d / relpos_30d / relpos_60d
                             / relpos01_7d ...（原始 [0,1] 版本，仅用于解读）

用法：
    python 因子研究/区间相对位置因子/code/build_factor.py --market um
    python 因子研究/区间相对位置因子/code/build_factor.py --market spot
"""

from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))          # …/区间相对位置因子/code
PROJECT = os.path.dirname(HERE)                            # …/区间相对位置因子
FACTOR_ROOT = os.path.dirname(PROJECT)                     # 因子研究
for _p in (os.path.join(FACTOR_ROOT, "行情数据"),
           os.path.join(FACTOR_ROOT, "因子数据")):
    sys.path.insert(0, _p)

import lake_io     # noqa: E402
import factor_io   # noqa: E402

WINDOWS = (7, 14, 30, 60)
MIN_RANGE = 0.005          # (High−Low)/close 低于 0.5% 视为退化区间
MIN_PERIODS = lambda n: max(3, n // 2)   # noqa: E731  窗口攒一半就有值


def build(market: str, tf: str = "1d") -> pd.DataFrame:
    if tf != "1d":
        raise ValueError("本轮只研究日线；小时线的窗口要另定")
    df = lake_io.load(market, tf, columns=["symbol", "date", "high", "low", "close"])
    if df.empty:
        raise RuntimeError(f"{market}/{tf} 没有数据")
    hi = lake_io.wide(df, "high")
    lo = lake_io.wide(df, "low")
    cl = lake_io.wide(df, "close")

    out = {}
    for n in WINDOWS:
        hh = hi.shift(1).rolling(n, min_periods=MIN_PERIODS(n)).max()
        ll = lo.shift(1).rolling(n, min_periods=MIN_PERIODS(n)).min()
        width = hh - ll
        # 区间退化的样本（稳定币 / 一字板 / 长期停牌）直接判无效
        ok = (width > 0) & (width / cl > MIN_RANGE)
        rel = ((cl - ll) / width).where(ok)
        out[f"relpos01_{n}d"] = rel          # 原始 [0,1]（少数突破会越界）
        out[f"relpos_{n}d"] = 2.0 * rel - 1.0  # 以 0 为中心，方便读符号

    long = []
    for col, mat in out.items():
        s = mat.stack().rename(col)
        long.append(s)
    panel = pd.concat(long, axis=1).reset_index()
    panel.columns = ["date", "symbol"] + list(out.keys())
    panel = panel.replace([np.inf, -np.inf], np.nan)
    # 没上市 / 已下架的日期不落行（全 NaN），和公共因子库的行数口径一致
    panel = panel.dropna(subset=list(out.keys()), how="all")
    return panel.sort_values(["date", "symbol"], kind="stable").reset_index(drop=True)


def _rank_corr(a: pd.Series, b: pd.Series, dates: pd.Series) -> float:
    """逐日截面秩相关的均值 —— 判断是不是「旧因子的换皮版」。"""
    t = pd.DataFrame({"d": dates, "a": a, "b": b}).dropna()
    if t.empty:
        return np.nan
    return float(t.groupby("d").apply(
        lambda g: g["a"].rank().corr(g["b"].rank()) if len(g) >= 20 else np.nan
    ).mean())


def describe(panel: pd.DataFrame, market: str) -> None:
    print(f"  覆盖 {len(panel):,} 行 / {panel['symbol'].nunique()} 个符号 / "
          f"{panel['date'].min().date()} → {panel['date'].max().date()}")
    for n in WINDOWS:
        c = f"relpos_{n}d"
        x = panel[c].dropna()
        print(f"  {c:>13}: 有效 {len(x):>8,} / 中位 {x.median():+.3f} / "
              f"|值|>1 占 {(x.abs() > 1).mean():.2%} / 缺失 {panel[c].isna().mean():.1%}")

    # 和公共因子库的截面秩相关：如果和 mom_7d 有 0.95，就只是动量的换皮
    try:
        pub = pd.read_parquet(factor_io.panel_path(market, "1d"),
                              columns=["date", "symbol", "mom_7d", "mom_30d",
                                       "vol_30d", "range_30d"])
        if str(pub["symbol"].dtype) == "category":
            pub["symbol"] = pub["symbol"].astype(str)
        m = panel[["date", "symbol", "relpos_7d", "relpos_30d"]].merge(
            pub, on=["date", "symbol"], how="inner")
        print("  与公共因子的截面秩相关（均值）：")
        for a in ("relpos_7d", "relpos_30d"):
            line = " / ".join(f"{b} {_rank_corr(m[a], m[b], m['date']):+.3f}"
                              for b in ("mom_7d", "mom_30d", "vol_30d", "range_30d")
                              if b in m)
            print(f"    {a}: {line}")
    except FileNotFoundError:
        print("  （公共因子库缺失，跳过相关性对照）")


def main() -> None:
    ap = argparse.ArgumentParser(description="构造区间相对位置因子面板")
    ap.add_argument("--market", default="um", choices=["spot", "um"])
    ap.add_argument("--tf", default="1d", choices=["1d"])
    args = ap.parse_args()

    df = build(args.market, args.tf)
    outdir = os.path.join(PROJECT, "out")
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, f"factor_{args.market}_{args.tf}.parquet")
    df.to_parquet(path, index=False, compression="zstd")

    print(f"[{args.market}/{args.tf}] 区间相对位置 → {os.path.relpath(path, FACTOR_ROOT)} "
          f"({len(df):,} 行)")
    describe(df, args.market)


if __name__ == "__main__":
    main()
