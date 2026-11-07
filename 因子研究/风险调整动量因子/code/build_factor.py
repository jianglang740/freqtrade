#!/usr/bin/env python
"""
风险调整动量因子：sharpe_mom_w = mom_w / vol_w

一句话：把「过去 w 期涨了多少」换成「过去 w 期每单位波动赚了多少」。
动机是原始的 mom 在币圈被高波动币绑架 —— 一个 30 天涨 300%、日波动 15% 的币，
和一个涨 40%、日波动 3% 的币，后者其实是更强的趋势，但原始动量排名把前者顶到第一。

输入是**公共因子库**，不重新读行情：`因子研究/因子数据/panels/{market}_{tf}.parquet`
里 mom_7d/30d/90d/180d 和 vol_7d/30d/90d/180d 已经算好，窗口一一对应。

产物：`out/factor_{market}_{tf}.parquet`
    列 = date / symbol / sharpe_mom_7d / sharpe_mom_30d / sharpe_mom_90d / sharpe_mom_180d
    回测：run_backtest.py --factor-panel 该文件 --factor sharpe_mom_30d

用法：
    python 因子研究/风险调整动量因子/code/build_factor.py --market um --tf 1d
    python 因子研究/风险调整动量因子/code/build_factor.py --market spot --tf 1d
"""

from __future__ import annotations

import argparse
import os
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))          # …/风险调整动量因子/code
PROJECT = os.path.dirname(HERE)                            # …/风险调整动量因子
FACTOR_ROOT = os.path.dirname(PROJECT)                     # 因子研究
for _p in (os.path.join(FACTOR_ROOT, "行情数据"),
           os.path.join(FACTOR_ROOT, "因子数据"),
           os.path.join(FACTOR_ROOT, "回测框架")):
    sys.path.insert(0, _p)

import factor_io  # noqa: E402

# 波动率的单位是「每个 bar 的收益率标准差」。低于这个值视为数据异常
# （连续多根一字板 / 停牌后复牌），除数接近 0 会把比值炸到天上。
VOL_FLOOR = 1e-4          # 日线：万分之一；小时线按 1/24 折算
TF_SCALE = {"1d": 1.0, "1h": 24.0 ** 0.5}


def build(market: str, tf: str) -> pd.DataFrame:
    windows = sorted(int(c[4:-1]) for c in factor_io.list_factors(market, tf)
                     if c.startswith("mom_"))
    cols, keep = [], []
    for w in windows:
        mc, vc = f"mom_{w}{tf[-1]}", f"vol_{w}{tf[-1]}"
        cols += [mc, vc]
        keep.append(w)
    p = factor_io.panel_path(market, tf)
    df = pd.read_parquet(p, columns=["date", "symbol"] + cols)
    if str(df["symbol"].dtype) == "category":
        df["symbol"] = df["symbol"].astype(str)

    floor = VOL_FLOOR * TF_SCALE[tf]
    out = df[["date", "symbol"]].copy()
    for w in keep:
        u = tf[-1]
        vol = df[f"vol_{w}{u}"].astype("float64")
        vol = vol.where(vol > floor)                       # 波动率过低 → 视为无效
        out[f"sharpe_mom_{w}{u}"] = (df[f"mom_{w}{u}"].astype("float64") / vol)
    out = out.replace([float("inf"), float("-inf")], pd.NA)
    return out.sort_values(["date", "symbol"], kind="stable").reset_index(drop=True)


def describe(df: pd.DataFrame) -> None:
    """比值和原始动量的相关性 —— 这是判断它到底是不是「一个新因子」的第一道关。"""
    for c in [c for c in df.columns if c.startswith("sharpe_mom_")]:
        w = c.split("_")[-1]
        raw = df[c].notna().sum()
        print(f"  {c:>18}: 覆盖 {raw:>8,} 行 / "
              f"中位 {df[c].median():>7.3f} / "
              f"|值|>10 的比例 {(df[c].abs() > 10).mean():.4%}")


def main() -> None:
    ap = argparse.ArgumentParser(description="构造风险调整动量因子面板")
    ap.add_argument("--market", default="um", choices=["spot", "um"])
    ap.add_argument("--tf", default="1d", choices=["1d", "1h"])
    args = ap.parse_args()

    df = build(args.market, args.tf)
    outdir = os.path.join(PROJECT, "out")
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, f"factor_{args.market}_{args.tf}.parquet")
    df.to_parquet(path, index=False, compression="zstd")

    print(f"[{args.market}/{args.tf}] 风险调整动量 → {os.path.relpath(path, FACTOR_ROOT)} "
          f"({len(df):,} 行 / {df['symbol'].nunique()} 个符号)")
    describe(df)


if __name__ == "__main__":
    main()
