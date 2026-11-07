#!/usr/bin/env python
"""
第二步：把单符号缓存重排成 Hive 分区，并生成符号主表。

分区风格**刻意对齐** `量化研报复现/曹春晓量化因子复现/行情数据/`：

    行情数据/spot/1h/year=2024/month=06.parquet      ← 小时线：年目录 + 月文件
    行情数据/spot/1d/year=2024.parquet                ← 日线：只有年文件
    行情数据/um/funding/year=2024.parquet             ← 资金费率：只有年文件
    行情数据/basic/dim_instrument.parquet             ← 符号主表（上市/下架标签）

⚠️ `month` 是**文件名**不是目录，所以 `pyarrow.dataset(partitioning="hive")` 只会把
`year` 识别成分区列。这是照抄参考仓库的既定风格，不是笔误。

内存处理：16 GB 机器装不下 3700 万行一次性 concat，所以按**年**分两阶段走
（先写年度临时文件，再逐年排序切月），峰值内存约等于一年的数据。

用法：
    python 因子研究/行情数据/_build/02_partition.py
    python 因子研究/行情数据/_build/02_partition.py --only spot/1h um/1d
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import shutil
import sys
import time

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (  # noqa: E402
    AUDIT, BASIC, FIAT_BASES, FUNDING_SCHEMA, KLINE_SCHEMA, LAKE, MANIFEST,
    MARKET_PATH, MARKETS, PA_KW, PANEL, STABLECOIN_BASES, cache_dir,
    is_leveraged, split_symbol,
)

DATASETS = [("spot", "1d"), ("spot", "1h"), ("um", "1d"), ("um", "1h"), ("um", "funding")]
TMP = os.path.join(LAKE, ".tmp")

STATUS_TRADING_DAYS = 3      # 末根日线在最新日 3 天内 → 仍在交易
STATUS_SUSPEND_DAYS = 30     # 3~30 天 → 疑似停牌；超过 30 天 → 已下架

# date 合法区间（毫秒）。币安现货最早 2017-07-14，留点余量。
DATE_MIN_MS = 1_480_000_000_000                                  # 2016-11-20
DATE_MAX_MS = int(time.time() * 1000) + 2 * 86_400_000            # 今天 +2 天


def schema_of(tf: str):
    return FUNDING_SCHEMA if tf == "funding" else KLINE_SCHEMA


def columns_of(schema) -> list[str]:
    return list(schema.names)


def write_partition(df: pd.DataFrame, schema, path: str) -> int:
    """按固定 schema 原子写一个分区。返回字节数。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tbl = pa.Table.from_pandas(df[columns_of(schema)], schema=schema, preserve_index=False)
    tmp = path + ".tmp"
    pq.write_table(tbl, tmp, **PA_KW)
    os.replace(tmp, path)
    return os.path.getsize(path)


def md5(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ══════════════════════════════════════════════ 分年处理
def build_dataset(market: str, tf: str) -> dict:
    src = cache_dir(market, tf)
    files = sorted(glob.glob(os.path.join(src, "*.parquet")))
    if not files:
        print(f"  [{market}/{tf}] 缓存为空，跳过（先跑 01_fetch_bv.py）")
        return {}

    schema = schema_of(tf)
    cols = columns_of(schema)
    t0 = time.time()

    # ── 阶段 1：每个年一个临时文件，边读边 append，避免一次性 concat
    os.makedirs(TMP, exist_ok=True)
    tmp_path: dict[int, str] = {}
    writers: dict[int, pq.ParquetWriter] = {}
    rows_in = 0
    date_min = date_max = None
    try:
        for f in files:
            d = pd.read_parquet(f)
            if d.empty:
                continue
            rows_in += len(d)
            d = d[cols].copy()
            # 硬闸：时间戳越界一律报错并点名符号，绝不静默落盘。
            # 背景：归档里存在同一 CSV 内毫秒/微秒混用，一旦单位判错会造出 year=5xxxx 的幽灵 bar。
            dv = d["date"].astype("int64")
            if int(dv.min()) < DATE_MIN_MS or int(dv.max()) > DATE_MAX_MS:
                raise ValueError(
                    f"{os.path.basename(f)} 的 date 越界："
                    f"{d.loc[dv.idxmin(), 'date']} ~ {d.loc[dv.idxmax(), 'date']}"
                    f"（合法区间 2017-01-01 ~ 今天+2天）。删掉该缓存文件重抓。")
            lo, hi = d["date"].min(), d["date"].max()
            date_min = lo if date_min is None else min(date_min, lo)
            date_max = hi if date_max is None else max(date_max, hi)
            d["_y"] = d["date"].dt.year.to_numpy()
            for y, g in d.groupby("_y", sort=False):
                y = int(y)
                if y not in writers:
                    p = os.path.join(TMP, f"{market}_{tf}_y{y}.parquet")
                    tmp_path[y] = p
                    writers[y] = pq.ParquetWriter(
                        p, schema, compression=PA_KW["compression"],
                        compression_level=PA_KW["compression_level"],
                        use_dictionary=PA_KW["use_dictionary"])
                writers[y].write_table(
                    pa.Table.from_pandas(g[cols], schema=schema, preserve_index=False))
    finally:
        for w in writers.values():
            w.close()

    # ── 阶段 2：逐年读回来，按 (date, symbol) 排序后切分区
    out_root = os.path.join(LAKE, market, tf)
    written: list[str] = []
    rows_out = 0
    for y in sorted(tmp_path):
        d = pd.read_parquet(tmp_path[y])
        d = d.sort_values(["date", "symbol"], kind="stable").reset_index(drop=True)
        if tf == "1h":
            m = d["date"].dt.month.to_numpy()
            for mm in range(1, 13):
                g = d.loc[m == mm]
                if g.empty:
                    continue
                p = os.path.join(out_root, f"year={y}", f"month={mm:02d}.parquet")
                write_partition(g, schema, p)
                written.append(p)
                rows_out += len(g)
        else:
            p = os.path.join(out_root, f"year={y}.parquet")
            write_partition(d, schema, p)
            written.append(p)
            rows_out += len(d)
        os.remove(tmp_path[y])

    # 清掉本数据集不再需要的旧分区
    keep = {os.path.abspath(p) for p in written}
    for p in glob.glob(os.path.join(out_root, "**", "*.parquet"), recursive=True):
        if os.path.abspath(p) not in keep:
            os.remove(p)
    for root, dirs, fs in os.walk(out_root, topdown=False):
        if not dirs and not fs and root != out_root:
            os.rmdir(root)

    assert rows_in == rows_out, f"{market}/{tf} 行数不守恒：缓存 {rows_in} → 分区 {rows_out}"
    files_meta = [{"path": os.path.relpath(p, LAKE), "rows": int(pq.ParquetFile(p).metadata.num_rows),
                   "bytes": os.path.getsize(p), "md5": md5(p)} for p in written]
    total = sum(x["rows"] for x in files_meta)
    print(f"  [{market}/{tf}] {len(files)} 个符号 → {len(written)} 个分区 / {total:,} 行 / "
          f"{sum(x['bytes'] for x in files_meta)/1e6:.0f} MB / {time.time()-t0:.0f}s")
    return {
        "files": files_meta,
        "rows": int(total),
        "symbols": len(files),
        "date_min": str(date_min),
        "date_max": str(date_max),
    }


# ══════════════════════════════════════════════ 符号主表
def load_tradfi() -> set[str]:
    p = os.path.join(PANEL, "tradfi_contracts.csv")
    if not os.path.exists(p):
        return set()
    try:
        return set(pd.read_csv(p, encoding="utf-8-sig")["symbol"].astype(str))
    except Exception:
        return set()


def build_dim() -> dict:
    """从两个市场的日线分区统计出符号主表 —— 这就是「ST / 退市」标签。"""
    per_market: dict[str, pd.DataFrame] = {}
    for market in MARKETS:
        paths = sorted(glob.glob(os.path.join(LAKE, market, "1d", "year=*.parquet")))
        if not paths:
            continue
        t = pa.concat_tables([pq.read_table(p, columns=["symbol", "date"]) for p in paths])
        d = t.to_pandas()
        g = d.groupby("symbol", observed=True)["date"]
        per_market[market] = pd.DataFrame({
            "first_bar": g.min(), "last_bar": g.max(), "bars": g.size(),
        })

    if not per_market:
        print("  [basic] 没有 1d 分区，跳过主表")
        return {}

    latest = max(df["last_bar"].max() for df in per_market.values())
    tradfi = load_tradfi()

    frames = []
    for market, per in per_market.items():
        per = per.reset_index()
        per.insert(1, "market", market)
        frames.append(per)
    dim = pd.concat(frames, ignore_index=True)

    dim["base"], dim["quote"] = zip(*dim["symbol"].map(split_symbol))
    dim["list_date"] = dim["first_bar"].dt.date
    dim["last_bar_1d"] = dim["last_bar"].dt.date
    dim["first_bar_1d"] = dim["list_date"]

    age = (latest - dim["last_bar"]).dt.days
    dim["status"] = "delisted"
    dim.loc[age < STATUS_SUSPEND_DAYS, "status"] = "suspended"
    dim.loc[age < STATUS_TRADING_DAYS, "status"] = "trading"
    dim["delist_date"] = [
        None if s == "trading" else d for s, d in zip(dim["status"], dim["last_bar_1d"])
    ]

    dim["bars_1d"] = dim["bars"].astype("int64")
    dim["is_leveraged"] = dim["symbol"].map(is_leveraged)
    dim["is_stablecoin"] = dim["base"].isin(STABLECOIN_BASES)
    dim["is_fiat"] = dim["base"].isin(FIAT_BASES)
    # TradFi 永续只在 U 本位合约里有。必须带上 market 条件：同一个代码可能是两个标的
    # （实测 MDTUSDT 在现货是老币、2026-04 已下架；在合约是 2026-10 上市的美股永续）。
    dim["is_tradfi"] = dim["symbol"].isin(tradfi) & (dim["market"] == "um")

    out = dim[[
        "symbol", "market", "base", "quote", "list_date", "delist_date", "status",
        "bars_1d", "first_bar_1d", "last_bar_1d",
        "is_leveraged", "is_stablecoin", "is_fiat", "is_tradfi",
    ]].sort_values(["market", "symbol"]).reset_index(drop=True)

    os.makedirs(BASIC, exist_ok=True)
    p = os.path.join(BASIC, "dim_instrument.parquet")
    tmp = p + ".tmp"
    pq.write_table(pa.Table.from_pandas(out, preserve_index=False), tmp, **PA_KW)
    os.replace(tmp, p)

    cnt = out["status"].value_counts().to_dict()
    print(f"  [basic] {len(out)} 个符号  " + "  ".join(f"{k}={v}" for k, v in sorted(cnt.items())))
    return {
        "path": os.path.relpath(p, LAKE), "rows": int(len(out)),
        "bytes": os.path.getsize(p), "md5": md5(p),
        "status_counts": {k: int(v) for k, v in cnt.items()},
        "latest_bar": str(latest),
        "note": "status 由「末根日线距今多久」推导，是阈值代理，不是交易所公告",
    }


# ══════════════════════════════════════════════ main
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", default=None,
                    help="只重建指定数据集，如 spot/1h um/1d；缺省全量")
    args = ap.parse_args()

    targets = DATASETS
    if args.only:
        want = {tuple(x.split("/")) for x in args.only}
        targets = [d for d in DATASETS if d in want]

    prev_man = {}
    if os.path.exists(MANIFEST):
        try:
            prev_man = json.load(open(MANIFEST, encoding="utf-8"))
        except Exception:
            prev_man = {}
    prev = prev_man.get("datasets", {})

    datasets = dict(prev)
    for market, tf in targets:
        key = f"{market}/{tf}"
        print(f"[{key}]")
        meta = build_dataset(market, tf)
        if meta:
            old = prev.get(key, {})
            meta["diff"] = ({
                "rows_before": old.get("rows"),
                "rows_after": meta["rows"],
                "symbols_before": old.get("symbols"),
                "symbols_after": meta["symbols"],
            } if old else {"note": "首次生成"})
            datasets[key] = meta

    basic_meta = (build_dim() if any(d[1] == "1d" for d in targets) else None) \
        or prev_man.get("basic")
    man = {
        "schema_version": 2,
        "built_at": pd.Timestamp.now(tz="UTC").isoformat(),
        "source": "data.binance.vision 归档（含已下架交易对）",
        "layout": "行情数据/<market>/<tf>/year=YYYY[/month=MM].parquet",
        "primary_key": ["symbol", "date"],
        "note": "month 是文件名不是目录，只有 year 会被 hive 分区解析成列",
        "datasets": datasets,
        "basic": basic_meta,
    }
    with open(MANIFEST, "w", encoding="utf-8") as fh:
        json.dump(man, fh, ensure_ascii=False, indent=1)

    if os.path.isdir(TMP) and not os.listdir(TMP):
        shutil.rmtree(TMP, ignore_errors=True)
    print(f"写 {os.path.relpath(MANIFEST, PANEL)}")


if __name__ == "__main__":
    main()
