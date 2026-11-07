#!/usr/bin/env python
"""
第三步：数据湖完整性校验，七项，全绿才算建成。

  1. 文件清单与 _manifest.json 一致（多一个少一个都算失败）
  2. 月份连续（首月到尾月之间不允许缺文件）
  3. schema 一致（所有分区列名/类型完全相同）
  4. 行数守恒（分区行数之和 == 缓存行数之和）
  5. 主键唯一（(symbol, date) 无重复）
  6. 单日 bar 数合理（1h ≤ 24、1d ≤ 1；缺口只统计不判失败）
  7. 随机抽 20 个 (symbol, 月份) 回源比对 OHLC（--no-net 可跳过）

用法：
    python 因子研究/行情数据/_build/03_verify.py
    python 因子研究/行情数据/_build/03_verify.py --no-net      # 断网也能跑前 6 项
"""

from __future__ import annotations

import argparse
import glob
import importlib
import json
import os
import random
import sys

import pandas as pd
import pyarrow.parquet as pq

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import CACHE, LAKE, MANIFEST  # noqa: E402

MAX_BARS_PER_DAY = {"1h": 24, "1d": 1}
FAILS: list[str] = []


def fail(msg: str) -> None:
    FAILS.append(msg)
    print(f"  ❌ {msg}")


def ok(msg: str) -> None:
    print(f"  ✅ {msg}")


def partitions(market: str, tf: str) -> list[str]:
    return sorted(glob.glob(os.path.join(LAKE, market, tf, "**", "*.parquet"), recursive=True))


# ══════════════════════════════════════════════ 1. 文件清单
def check_manifest(man: dict) -> None:
    print("[1] 文件清单与 manifest 一致")
    for key, meta in man.get("datasets", {}).items():
        market, tf = key.split("/")
        on_disk = {os.path.relpath(p, LAKE) for p in partitions(market, tf)}
        in_man = {f["path"] for f in meta["files"]}
        if on_disk != in_man:
            fail(f"{key}: 磁盘 {len(on_disk)} 个 与 manifest {len(in_man)} 个不一致 "
                 f"（多 {sorted(on_disk-in_man)[:3]} / 少 {sorted(in_man-on_disk)[:3]}）")
        else:
            ok(f"{key}: {len(on_disk)} 个分区")
    b = man.get("basic")
    if b and not os.path.exists(os.path.join(LAKE, b["path"])):
        fail("basic/dim_instrument.parquet 缺失")
    elif b:
        ok(f"basic: {b['rows']} 个符号")


# ══════════════════════════════════════════════ 2. 月份连续
def check_continuity(man: dict) -> None:
    print("[2] 分区年份/月份连续")
    for key in man.get("datasets", {}):
        market, tf = key.split("/")
        root = os.path.join(LAKE, market, tf)
        if tf == "1h":
            # 1h：year=YYYY 是目录，month=MM 是文件名
            ys = sorted(int(os.path.basename(d)[5:]) for d in
                        glob.glob(os.path.join(root, "year=*")) if os.path.isdir(d))
        else:
            # 1d / funding：year=YYYY 直接是文件名，没有月份层
            ys = sorted(int(os.path.basename(p)[5:9]) for p in
                        glob.glob(os.path.join(root, "year=*.parquet")))
        if not ys:
            fail(f"{key}: 没有任何 year 分区")
            continue
        if ys != list(range(ys[0], ys[-1] + 1)):
            fail(f"{key}: 年份不连续 {ys[0]}..{ys[-1]}，缺 {sorted(set(range(ys[0], ys[-1]+1)) - set(ys))}")
            continue
        missing = []
        if tf == "1h":
            for y in ys:
                got = {int(os.path.basename(p)[6:8]) for p in
                       glob.glob(os.path.join(root, f"year={y}", "month=*.parquet"))}
                if not got:
                    missing.append((y, "全年缺失"))
                    continue
                need = set(range(min(got), max(got) + 1))
                if got != need:
                    missing.append((y, sorted(need - got)))
        if missing:
            fail(f"{key}: 月文件缺口 {missing[:5]}")
        else:
            ok(f"{key}: {ys[0]}..{ys[-1]} 连续")


# ══════════════════════════════════════════════ 3~6
def check_partitions(man: dict, no_net: bool) -> None:
    for key, meta in man.get("datasets", {}).items():
        market, tf = key.split("/")
        paths = partitions(market, tf)
        if not paths:
            continue

        print(f"[3] {key} schema 一致")
        schemas = {str(pq.ParquetFile(p).schema_arrow) for p in paths}
        if len(schemas) != 1:
            fail(f"{key}: 出现 {len(schemas)} 种 schema")
        else:
            ok(f"{key}: 单一 schema（{len(pq.ParquetFile(paths[0]).schema_arrow.names)} 列）")

        print(f"[4] {key} 行数守恒")
        on_disk = sum(pq.ParquetFile(p).metadata.num_rows for p in paths)
        cache = sum(pq.ParquetFile(p).metadata.num_rows
                    for p in glob.glob(os.path.join(CACHE, market, tf, "*.parquet")))
        if on_disk != cache:
            fail(f"{key}: 分区 {on_disk:,} 行 ≠ 缓存 {cache:,} 行")
        elif on_disk != meta["rows"]:
            fail(f"{key}: 分区 {on_disk:,} 行 ≠ manifest {meta['rows']:,} 行")
        else:
            ok(f"{key}: {on_disk:,} 行")

        print(f"[5] {key} 主键唯一" + (" / [6] 单日 bar 数" if tf in MAX_BARS_PER_DAY else ""))
        dup = over = 0
        for p in paths:
            cols = ["symbol", "date"]
            t = pq.read_table(p, columns=cols).to_pandas()
            dup += int(t.duplicated(cols).sum())
            if tf in MAX_BARS_PER_DAY:
                per_day = t.groupby([t["symbol"], t["date"].dt.date]).size()
                if not per_day.empty:
                    over += int((per_day > MAX_BARS_PER_DAY[tf]).sum())
        if dup:
            fail(f"{key}: {dup} 行主键重复")
        else:
            ok(f"{key}: 无重复主键")
        if tf in MAX_BARS_PER_DAY:
            if over:
                fail(f"{key}: {over} 个「符号×日」超过 {MAX_BARS_PER_DAY[tf]} 根 bar")
            else:
                ok(f"{key}: 所有「符号×日」bar 数 ≤ {MAX_BARS_PER_DAY[tf]}")

    if no_net:
        print("[7] 回源比对：--no-net 已跳过")
    else:
        check_source(man)


# ══════════════════════════════════════════════ 7. 回源比对
def check_source(man: dict) -> None:
    print("[7] 随机 20 个 (symbol, 月份) 回源比对 OHLC")
    fetch = importlib.import_module("01_fetch_bv")
    rng = random.Random(20261106)
    candidates = [(k, f) for k, m in man.get("datasets", {}).items()
                  if not k.endswith("funding") for f in m["files"]]
    rng.shuffle(candidates)
    checked = bad = 0
    for key, f in candidates:
        if checked >= 20:
            break
        market, tf = key.split("/")
        p = os.path.join(LAKE, f["path"])
        d = pq.read_table(p).to_pandas()
        if d.empty:
            continue
        ym = d["date"].dt.strftime("%Y-%m").mode().iloc[0]
        sym = d.loc[(d["date"].dt.strftime("%Y-%m") == ym), "symbol"].iloc[0]
        src = f"{fetch.MARKET_PATH[market]}/monthly/klines/{sym}/{tf}/{sym}-{tf}-{ym}.zip"
        raw = fetch.http_get(fetch.BASE + "/" + src)
        if not raw:
            print(f"  ⚠️  {sym} {ym} 归档取不到，跳过")
            continue
        ref = fetch.parse_kline(raw).set_index("date")
        got = d[(d["symbol"] == sym) & (d["date"].dt.strftime("%Y-%m") == ym)].set_index("date")
        both = got.index.intersection(ref.index)
        if both.empty:
            fail(f"{sym} {ym}: 与归档没有交集")
            bad += 1
            continue
        diff = (got.loc[both, ["open", "high", "low", "close"]]
                - ref.loc[both, ["open", "high", "low", "close"]]).abs().to_numpy().max()
        if diff > 1e-9:
            fail(f"{sym} {ym}: OHLC 最大差 {diff:.3e}")
            bad += 1
        checked += 1
    if bad == 0:
        ok(f"{checked} 个抽样全部与归档一致")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-net", action="store_true", help="跳过第 7 项回源比对")
    args = ap.parse_args()

    if not os.path.exists(MANIFEST):
        print(f"❌ 找不到 {MANIFEST}，先跑 02_partition.py")
        sys.exit(1)
    man = json.load(open(MANIFEST, encoding="utf-8"))

    check_manifest(man)
    check_continuity(man)
    check_partitions(man, args.no_net)

    print()
    if FAILS:
        print(f"❌ {len(FAILS)} 项失败")
        sys.exit(1)
    print("✅ 全部通过")


if __name__ == "__main__":
    main()
