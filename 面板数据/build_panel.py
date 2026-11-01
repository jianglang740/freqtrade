#!/usr/bin/env python
"""
从 `user_data/data/binance` 现场扫描 feather, 生成面板数据 parquet。

设计原则(为了兼容后期数据更新):
  1. **每次都全量重建**, 不做增量合并。全量只要几秒, 增量合并才是数据错乱的来源。
  2. **扫描目录而不是读白名单** —— 新拉了币对、新拉了周期, 重跑一次就自动进面板。
  3. 重建后**打印与上一版面板的差异**(新增币对 / 新增行 / 起止日期), 更新可审计。
  4. 字段集合固定并写进 manifest 的 `schema_version`; 破坏性改动必须升版本号。

联合主键 = (pair, date)。换行 `pair` 用字典编码存储, 文件比字符串直存小很多。

用法:
    source /opt/anaconda3/etc/profile.d/conda.sh && conda activate freqtrade
    python 面板数据/build_panel.py                # 重建现货 1h + 1d
    python 面板数据/build_panel.py --timeframes 1h
    python 面板数据/build_panel.py --market futures --timeframes 1h
"""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timezone

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "user_data", "data", "binance")
OUT_DIR = os.path.dirname(os.path.abspath(__file__))

SCHEMA_VERSION = 1
OHLCV = ["date", "open", "high", "low", "close", "volume"]
COLUMNS = ["pair", "date", "open", "high", "low", "close", "volume", "quote_volume"]
STEP_MIN = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440}


def scan(market: str, tf: str) -> tuple[pd.DataFrame, list[str]]:
    """把该周期下所有 feather 读成一张长表。"""
    out = []
    skipped = []
    for f in sorted(os.listdir(DATA)):
        m = re.match(rf"^(.+?)_USDT-{tf}\.feather$", f)
        if not m:
            continue
        pair = m.group(1) + "/USDT"
        df = pd.read_feather(os.path.join(DATA, f))
        missing = [c for c in OHLCV if c not in df.columns]
        if missing:
            skipped.append(f"{pair} 缺列 {missing}")
            continue
        df = df[OHLCV].copy()
        df["pair"] = pair
        out.append(df)
    if not out:
        return pd.DataFrame(columns=COLUMNS), skipped
    df = pd.concat(out, ignore_index=True)
    return df, skipped


def clean(df: pd.DataFrame) -> pd.DataFrame:
    """去重、排序、类型归一。日期统一成 UTC 毫秒，和 feather 一致。"""
    df["date"] = pd.to_datetime(df["date"], utc=True).astype("datetime64[ms, UTC]")
    df = df.drop_duplicates(subset=["pair", "date"], keep="last")
    df = df.sort_values(["pair", "date"]).reset_index(drop=True)
    # 现货 volume 是**基础货币**数量；quote_volume 是估算的计价货币成交额
    df["quote_volume"] = df["close"].astype("float64") * df["volume"].astype("float64")
    for c in ["open", "high", "low", "close", "volume", "quote_volume"]:
        df[c] = df[c].astype("float64")
    df["pair"] = df["pair"].astype("category")
    return df[COLUMNS]


def silhouette(df: pd.DataFrame, tf: str) -> dict:
    """每个币对的覆盖情况 + 缺口普查，写进 manifest 供因子检验前自查。"""
    step = pd.Timedelta(minutes=STEP_MIN[tf])
    per = {}
    for pair, g in df.groupby("pair", observed=True):
        d = g["date"]
        delta = d.diff().dropna()
        gaps = int((delta > step * 1.5).sum())
        per[str(pair)] = {
            "rows": int(len(g)),
            "start": d.min().strftime("%Y-%m-%d %H:%M"),
            "end": d.max().strftime("%Y-%m-%d %H:%M"),
            "gaps": gaps,
            "missing_bars_est": int((delta / step).sub(1).clip(lower=0).sum()),
            "zero_volume_bars": int((g["volume"] <= 0).sum()),
            "nulls": int(g[["open", "high", "low", "close"]].isna().sum().sum()),
        }
    return per


def diff_against(old_path: str, new: pd.DataFrame) -> dict:
    """与上一版面板对比，让每次更新可审计。"""
    if not os.path.exists(old_path):
        return {"note": "上一版不存在（首次生成）"}
    old = pd.read_parquet(old_path, columns=["pair", "date"])
    old_pairs = set(old["pair"].astype(str).unique())
    new_pairs = set(new["pair"].astype(str).unique())
    old_last = old.groupby("pair", observed=True)["date"].max()
    new_last = new.groupby("pair", observed=True)["date"].max()
    extended = {}
    for p in sorted(new_pairs & old_pairs):
        delta = new_last[p] - old_last.get(p, new_last[p])
        if pd.isna(delta):
            continue
        days = int(delta / pd.Timedelta(days=1))
        if days > 0:
            extended[p] = f"+{days} 天"
    return {
        "pairs_added": sorted(new_pairs - old_pairs),
        "pairs_removed": sorted(old_pairs - new_pairs),
        "rows_before": int(len(old)),
        "rows_after": int(len(new)),
        "rows_added": int(len(new) - len(old)),
        "pairs_extended": extended,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="spot", choices=["spot", "futures"])
    ap.add_argument("--timeframes", nargs="+", default=["1h", "1d"])
    args = ap.parse_args()

    for tf in args.timeframes:
        src = DATA if args.market == "spot" else os.path.join(DATA, "futures")
        if args.market == "futures":
            print(f"⚠️  合约面板尚未接通（feather 在 {src}/ 下、文件名多一段），先只做现货。")
            continue

        raw, skipped = scan(args.market, tf)
        if raw.empty:
            print(f"[{tf}] 没有找到任何 feather，跳过")
            continue
        df = clean(raw)

        name = f"{args.market}_{tf}.parquet"
        out = os.path.join(OUT_DIR, name)
        manifest_path = os.path.join(OUT_DIR, f"{args.market}_{tf}.manifest.json")
        diff = diff_against(out, df)

        df.to_parquet(out, compression="zstd", index=False,
                      row_group_size=500_000)

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        per = silhouette(df, tf)
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "built_at_utc": now,
            "market": args.market,
            "timeframe": tf,
            "source_dir": os.path.relpath(DATA, ROOT),
            "primary_key": ["pair", "date"],
            "columns": COLUMNS,
            "units": {
                "volume": "基础货币数量（交易所原始口径）",
                "quote_volume": "估算值 = close × volume，用于流动性过滤，非交易所官方成交额",
                "date": "UTC，毫秒精度，与 feather 一致",
            },
            "rows": int(len(df)),
            "pairs": int(df["pair"].nunique()),
            "date_min": df["date"].min().strftime("%Y-%m-%d %H:%M"),
            "date_max": df["date"].max().strftime("%Y-%m-%d %H:%M"),
            "file_bytes": os.path.getsize(out),
            "skipped_files": skipped,
            "build_diff": diff,
            "pairs_detail": per,
        }
        with open(manifest_path, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, ensure_ascii=False, indent=2)

        print(f"[{tf}] {name}: {len(df):,} 行 / {df['pair'].nunique()} 对 "
              f"/ {manifest['date_min']} → {manifest['date_max']} "
              f"/ {os.path.getsize(out)/1e6:.1f} MB")
        if diff.get("pairs_added"):
            print(f"     新增币对：{diff['pairs_added']}")
        if diff.get("pairs_removed"):
            print(f"     ⚠️ 消失的币对：{diff['pairs_removed']}")
        if "rows_added" in diff:
            print(f"     行数 {diff['rows_before']:,} → {diff['rows_after']:,}"
                  f"（+{diff['rows_added']:,}）")
        if diff.get("pairs_extended"):
            print(f"     有新增数据的币对 {len(diff['pairs_extended'])} 个")


if __name__ == "__main__":
    main()
