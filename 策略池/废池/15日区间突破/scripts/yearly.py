#!/usr/bin/env python
"""
把关键配置的「分年度」和「分位数远期收益」单独落盘，写报告用。

run_study.py 只存了汇总统计；闸门 2 要求看「去掉最好一年后是否还为正」，
必须逐年拆开。这里复用 run_study.py 里写死的 CASES，不另设口径。

产物：out/yearly/<key>_分年度.csv、out/yearly/<key>_分位数.csv

用法：
    python 策略池/15日区间突破/scripts/yearly.py
    python 策略池/15日区间突破/scripts/yearly.py --keys um_full_ls,um_full_long,spot_full_long
"""

from __future__ import annotations

import argparse
import os
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import run_study as R      # noqa: E402  （复用 BASE / CASES / 路径）
import engine as E         # noqa: E402

OUT = os.path.join(R.PROJECT, "out", "yearly")

DEFAULT_KEYS = [
    "um_full_ls", "um_full_long", "um_full_short", "um_full_ls_neut",
    "um_full_ls_top20", "um_full_ls_excl", "um_full_bot_long", "um_src_bot_long",
    "um_src_ls", "um_src_long", "um_src_ls_top20",
    "um_full_relpos14", "um_src_relpos14",
    "um_full_mom7", "um_src_mom7", "um_full_mom30", "um_src_mom30",
    "spot_full_long", "spot_src_long",
]


def main() -> None:
    ap = argparse.ArgumentParser(description="关键配置的分年度 / 分位数明细")
    ap.add_argument("--keys", default=None, help="逗号分隔；默认全部")
    args = ap.parse_args()

    keys = DEFAULT_KEYS if not args.keys else [k.strip() for k in args.keys.split(",")]
    os.makedirs(OUT, exist_ok=True)
    by_key = {c[0]: c for c in R.CASES}
    for key in keys:
        if key not in by_key:
            print(f"跳过未知 key: {key}")
            continue
        _, market, factor, panel, start, end, over = by_key[key]
        cfg = E.Config(market=market, factor=factor,
                       factor_panel=R._panel_path(panel, market),
                       start=start, end=end, **{**R.BASE, **over})
        print(f"\n=== {key} ===")
        res = E.run(cfg)
        year = res["tables"]["分年度"]
        quant = res["tables"]["分位数远期收益"]
        year.to_csv(os.path.join(OUT, f"{key}_分年度.csv"), index=False)
        quant.to_csv(os.path.join(OUT, f"{key}_分位数.csv"), index=False)
        print(year.to_string(index=False, float_format=lambda x: f"{x:.4f}"))
    print(f"\n→ {OUT}")


if __name__ == "__main__":
    main()
