#!/usr/bin/env python
"""
15 日区间突破因子（AlphaPilot 口径）：收盘价在过去 15 日**收盘价**区间里的位置。

    rp15d      = (close − min(close,15)) / (max(close,15) − min(close,15))
    rp15d_excl = 同式，但窗口为 [t−15, t−1]

- 端点用 close 的 min/max（不是 high/low）—— 对齐原文。
- rp15d **含当日**：创 15 日新高的当天，因子机械等于 1.0，这是「突破」信号。
- rp15d_excl **不含当日**：与既有 RelPos 项目的口径对齐，用来拆分「含当日」的贡献。
- 区间宽度 < 0.5% 置 NaN（稳定币 / 一字板保护）。

原文用 4h×90 根，数据湖没有 4h；这里用日线 close 的 15 天窗口近似
（15 天 = 90×4h，端点同为收盘价），差异写进 02 报告。

产物：out/factor_{market}_1d.parquet，列 = date / symbol / rp15d / rp15d_excl

用法：
    python 策略池/15日区间突破/scripts/build_factor.py --market um
    python 策略池/15日区间突破/scripts/build_factor.py --market spot
"""

from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))          # …/15日区间突破/scripts
PROJECT = os.path.dirname(HERE)
REPO = os.path.dirname(os.path.dirname(PROJECT))           # 仓库根
for _p in (os.path.join(REPO, "因子研究/行情数据"),
           os.path.join(REPO, "因子研究/因子数据")):
    sys.path.insert(0, _p)

import lake_io     # noqa: E402

WINDOW = 15
MIN_PERIODS = 10
MIN_RANGE = 0.005        # (max−min)/close 低于 0.5% 视为退化区间


def _pos(cl: pd.DataFrame, include_today: bool) -> pd.DataFrame:
    if include_today:
        hi = cl.rolling(WINDOW, min_periods=MIN_PERIODS).max()
        lo = cl.rolling(WINDOW, min_periods=MIN_PERIODS).min()
    else:
        hi = cl.shift(1).rolling(WINDOW, min_periods=MIN_PERIODS).max()
        lo = cl.shift(1).rolling(WINDOW, min_periods=MIN_PERIODS).min()
    width = hi - lo
    ok = (width > 0) & (width / cl > MIN_RANGE)
    return ((cl - lo) / width).where(ok)


def build(market: str) -> pd.DataFrame:
    df = lake_io.load(market, "1d", columns=["symbol", "date", "close"])
    if df.empty:
        raise RuntimeError(f"{market}/1d 没有数据")
    cl = lake_io.wide(df, "close").astype("float64")

    mats = {
        "rp15d": _pos(cl, include_today=True),
        "rp15d_excl": _pos(cl, include_today=False),
    }
    long = []
    for col, mat in mats.items():
        long.append(mat.stack().rename(col))
    panel = pd.concat(long, axis=1).reset_index()
    panel.columns = ["date", "symbol"] + list(mats.keys())
    panel = panel.replace([np.inf, -np.inf], np.nan)
    panel = panel.dropna(subset=list(mats.keys()), how="all")
    return panel.sort_values(["date", "symbol"], kind="stable").reset_index(drop=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="构建 15 日区间突破因子")
    ap.add_argument("--market", default="um", choices=["um", "spot"])
    args = ap.parse_args()

    out_dir = os.path.join(PROJECT, "out")
    os.makedirs(out_dir, exist_ok=True)
    panel = build(args.market)
    path = os.path.join(out_dir, f"factor_{args.market}_1d.parquet")
    panel.to_parquet(path, index=False)
    print(f"{args.market}: {len(panel):,} 行 / {panel['symbol'].nunique()} 个符号 → {path}")
    print(panel.groupby("date").size().describe()[["min", "50%", "max"]].to_string())


if __name__ == "__main__":
    main()
