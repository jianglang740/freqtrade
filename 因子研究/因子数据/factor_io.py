#!/usr/bin/env python
"""
因子面板读写层。

因子面板是「日期 × 符号 × 因子」的长表，和行情数据湖的两张表**分开存**：

    因子研究/因子数据/panels/{market}_{tf}.parquet
        列：date(timestamp[ms, UTC]) / symbol(string) / <若干因子列 float32>
    因子研究/因子数据/panels/{market}_{tf}.json
        元信息：生成时间、来源、因子清单、行数、符号数、日期范围

一个 (market, tf) 一个文件，所有因子放在一起 —— 回测时只读需要的因子列，
pyarrow 的列裁剪会把其余列跳过，不必为每个因子单开文件。

用法：
    from factor_io import load_panel, list_factors, coverage

    f = load_panel("spot", "1d", factors=["mom_30d"], start="2020-01-01")
    print(list_factors("spot", "1d"))
"""

from __future__ import annotations

import json
import os

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))          # 因子研究/因子数据
PANELS = os.path.join(HERE, "panels")                      # 面板数据本体（gitignore）

MARKETS = ("spot", "um")
TIMEFRAMES = ("1d", "1h")


def panel_path(market: str, tf: str) -> str:
    return os.path.join(PANELS, f"{market}_{tf}.parquet")


def meta_path(market: str, tf: str) -> str:
    return os.path.join(PANELS, f"{market}_{tf}.json")


def available() -> list[tuple[str, str]]:
    """已经生成过的面板清单。"""
    if not os.path.isdir(PANELS):
        return []
    out = []
    for m in MARKETS:
        for t in TIMEFRAMES:
            if os.path.exists(panel_path(m, t)):
                out.append((m, t))
    return out


def read_meta(market: str, tf: str) -> dict:
    p = meta_path(market, tf)
    if not os.path.exists(p):
        return {}
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def list_factors(market: str, tf: str) -> list[str]:
    """该面板里有哪些因子列。"""
    meta = read_meta(market, tf)
    if meta.get("factors"):
        return list(meta["factors"])
    p = panel_path(market, tf)
    if not os.path.exists(p):
        raise FileNotFoundError(
            f"因子面板不存在：{p}\n先跑 因子研究/因子数据/build_factors.py --market {market} --tf {tf}")
    import pyarrow.parquet as pq
    cols = pq.read_schema(p).names
    return [c for c in cols if c not in ("date", "symbol")]


def load_panel(market: str = "spot", tf: str = "1d",
               factors: list[str] | None = None,
               start: str | None = None, end: str | None = None,
               symbols: list[str] | None = None) -> pd.DataFrame:
    """读因子长表。start / end 左闭右开，按 UTC。"""
    p = panel_path(market, tf)
    if not os.path.exists(p):
        raise FileNotFoundError(
            f"因子面板不存在：{p}\n先跑 因子研究/因子数据/build_factors.py --market {market} --tf {tf}")

    all_factors = list_factors(market, tf)
    if factors is None:
        factors = all_factors
    else:
        missing = [c for c in factors if c not in all_factors]
        if missing:
            raise KeyError(f"面板里没有这些因子：{missing}；可选：{all_factors}")

    df = pd.read_parquet(p, columns=["date", "symbol"] + list(factors))
    if "symbol" in df.columns and str(df["symbol"].dtype) == "category":
        df["symbol"] = df["symbol"].astype(str)
    if start is not None:
        df = df[df["date"] >= pd.Timestamp(start, tz="UTC")]
    if end is not None:
        df = df[df["date"] < pd.Timestamp(end, tz="UTC")]
    if symbols is not None:
        df = df[df["symbol"].isin(list(symbols))]
    return df.sort_values(["date", "symbol"], kind="stable").reset_index(drop=True)


def load_external(path: str, factor: str,
                  start: str | None = None, end: str | None = None,
                  symbols: list[str] | None = None) -> pd.DataFrame:
    """读**项目自建**的因子面板（不进公共因子库）。

    研究项目（如 因子研究/风险调整动量因子/）算出来的因子只对它自己有意义，
    没必要塞进 `panels/` 让所有项目共享 —— 但回测引擎得能读到它。
    格式与公共面板一致：`date / symbol / <factor>`，这里只做筛选。
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"外部因子面板不存在：{path}")
    df = pd.read_parquet(path, columns=["date", "symbol", factor])
    if str(df["symbol"].dtype) == "category":
        df["symbol"] = df["symbol"].astype(str)
    if start is not None:
        df = df[df["date"] >= pd.Timestamp(start, tz="UTC")]
    if end is not None:
        df = df[df["date"] < pd.Timestamp(end, tz="UTC")]
    if symbols is not None:
        df = df[df["symbol"].isin(list(symbols))]
    df[factor] = df[factor].astype("float64")
    return df.sort_values(["date", "symbol"], kind="stable").reset_index(drop=True)


def coverage(df: pd.DataFrame) -> pd.DataFrame:
    """每个符号的因子覆盖概况，排查缺口用。"""
    return (df.groupby("symbol", observed=True)
              .agg(rows=("date", "size"), start=("date", "min"), end=("date", "max"))
              .sort_values("rows", ascending=False))


def write_panel(df: pd.DataFrame, market: str, tf: str, meta: dict | None = None) -> str:
    """写面板 + 元信息。调用方负责列顺序和 dtype。"""
    os.makedirs(PANELS, exist_ok=True)
    p = panel_path(market, tf)
    tmp = p + ".tmp"
    df.to_parquet(tmp, index=False, compression="zstd")
    os.replace(tmp, p)

    factors = [c for c in df.columns if c not in ("date", "symbol")]
    info = {
        "market": market,
        "timeframe": tf,
        "factors": factors,
        "rows": int(len(df)),
        "symbols": int(df["symbol"].nunique()),
        "start": str(pd.Timestamp(df["date"].min()))[:19] if len(df) else None,
        "end": str(pd.Timestamp(df["date"].max()))[:19] if len(df) else None,
        "built_at": pd.Timestamp.now(tz="UTC").isoformat(),
    }
    if meta:
        info.update(meta)
    with open(meta_path(market, tf), "w", encoding="utf-8") as fh:
        json.dump(info, fh, ensure_ascii=False, indent=1)
    return p


if __name__ == "__main__":
    for m, t in available():
        info = read_meta(m, t)
        print(f"{m}/{t}: {info.get('rows', '?'):>10} 行 / "
              f"{len(info.get('factors', []))} 个因子 / {info.get('start')} → {info.get('end')}")
        print("   " + ", ".join(info.get("factors", [])))
