#!/usr/bin/env python
"""
数据湖读取层。

数据湖布局（和 `量化研报复现/曹春晓量化因子复现/行情数据/` 同构）：
本文件就放在湖根目录，所以下面的路径都是相对 `因子研究/行情数据/`。

    spot/1h/year=2024/month=06.parquet
    spot/1d/year=2024.parquet
    um/1h/year=2024/month=06.parquet
    um/1d/year=2024.parquet
    um/funding/year=2024.parquet
    basic/dim_instrument.parquet

⚠️ `month` 是**文件名**不是目录，所以 hive 分区只解析出 `year` 一列。
   要按月份过滤请直接用 `start` / `end` 参数走 `date` 列。

常用：

    from lake_io import load, wide, symbols, active_universe

    df = load("spot", "1h", start="2024-01-01", end="2025-01-01",
              symbols=["BTCUSDT", "ETHUSDT"])
    close = wide(df, "close")             # index=date, columns=symbol
    dim = symbols("spot")                 # 符号主表，含 status / delist_date
    alive = active_universe("spot")       # date × symbol 布尔表（点时股票池）

兼容旧脚本（`策略池/横截面动量/scripts/` 用的是 `pair` 列和 "BTC/USDT" 命名）：
    from lake_io import load_panel
    df = load_panel("1d")                 # 多一列 pair
"""

from __future__ import annotations

import glob
import os

import pandas as pd
import pyarrow.dataset as ds

HERE = os.path.dirname(os.path.abspath(__file__))
LAKE = HERE                       # 本文件就在湖根目录：因子研究/行情数据/

MARKETS = ("spot", "um")
TIMEFRAMES = ("1d", "1h", "funding")


# ══════════════════════════════════════════════ 基础读取
def available(market: str = "spot", tf: str = "1h") -> list[str]:
    """该数据集下所有分区文件（升序）。"""
    return sorted(glob.glob(os.path.join(LAKE, market, tf, "**", "*.parquet"), recursive=True))


def dataset(market: str = "spot", tf: str = "1h") -> ds.Dataset:
    p = os.path.join(LAKE, market, tf)
    if not os.path.isdir(p):
        raise FileNotFoundError(f"数据集不存在：{p}，先跑 因子研究/行情数据/_build/02_partition.py")
    return ds.dataset(p, format="parquet", partitioning="hive")


def load(market: str = "spot", tf: str = "1h", *,
         columns: list[str] | None = None,
         start: str | None = None, end: str | None = None,
         symbols: list[str] | None = None,
         years: list[int] | None = None) -> pd.DataFrame:
    """读长表。`year` 走 hive 分区剪枝，`start`/`end`/`symbols` 走谓词下推。

    start / end 是**左闭右开**，ETC 表示成 '2024-01-01' 这样的字符串即可。
    """
    filter_ = None
    if start is not None:
        filter_ = ds.field("date") >= pd.Timestamp(start, tz="UTC")
    if end is not None:
        f = ds.field("date") < pd.Timestamp(end, tz="UTC")
        filter_ = f if filter_ is None else (filter_ & f)
    if symbols is not None:
        f = ds.field("symbol").isin(list(symbols))
        filter_ = f if filter_ is None else (filter_ & f)
    if years is not None:
        f = ds.field("year").isin(list(years))
        filter_ = f if filter_ is None else (filter_ & f)

    tbl = dataset(market, tf).to_table(columns=columns, filter=filter_)
    df = tbl.to_pandas()
    if "year" in df.columns:
        df = df.drop(columns="year")          # 分区列，不是数据
    return df.sort_values(["date", "symbol"] if "symbol" in df.columns else ["date"],
                          kind="stable").reset_index(drop=True)


def _key(df: pd.DataFrame, col: str = "symbol") -> str:
    """数据湖用 `symbol`，旧面板用 `pair`。有哪个用哪个，让旧脚本不用改。"""
    if col in df.columns:
        return col
    return "pair" if "pair" in df.columns else col


def wide(df: pd.DataFrame, field: str = "close", col: str = "symbol") -> pd.DataFrame:
    """长表 → 宽表（index=时间, columns=符号）。"""
    return df.pivot(index="date", columns=_key(df, col), values=field).sort_index()


def to_daily(df: pd.DataFrame, col: str = "symbol") -> pd.DataFrame:
    """把 1h 长表聚合到日线，日界线固定 UTC 00:00。

    自己聚合而不是直接读 1d 分区：两边口径一致，避免日线与小时线各说各话。
    """
    need = {"date", "open", "high", "low", "close", "volume"}
    missing = need - set(df.columns)
    if missing:
        raise ValueError(f"to_daily 需要完整 OHLCV 列，缺少：{sorted(missing)}")
    g = df.set_index("date").groupby(_key(df, col), observed=True)
    out = g.resample("1D").agg(
        open=("open", "first"), high=("high", "max"), low=("low", "min"),
        close=("close", "last"), volume=("volume", "sum"),
        quote_volume=("quote_volume", "sum"), trade_count=("trade_count", "sum"),
    ).reset_index()
    return out.dropna(subset=["open", "close"])


# ══════════════════════════════════════════════ 符号主表
def symbols(market: str | None = None) -> pd.DataFrame:
    """符号主表。`status` ∈ {trading, suspended, delisted}，`delist_date` 为末根日线日期。"""
    p = os.path.join(LAKE, "basic", "dim_instrument.parquet")
    if not os.path.exists(p):
        raise FileNotFoundError(f"符号主表不存在：{p}，先跑 02_partition.py")
    dim = pd.read_parquet(p)
    dim["symbol"] = dim["symbol"].astype(str)
    if market is not None:
        dim = dim[dim["market"] == market].reset_index(drop=True)
    return dim


def active_universe(market: str = "spot", *, start: str | None = None,
                    end: str | None = None) -> pd.DataFrame:
    """date × symbol 的布尔表：该日有 bar 即为 True。

    做点时（point-in-time）股票池请用这个，**不要**用 `symbols().status` ——
    那是个全局快照，会把「2026 年才下架」的币在 2020 年的截面里也标成已下架。
    """
    d = load(market, "1d", columns=["symbol", "date"], start=start, end=end)
    if d.empty:
        return pd.DataFrame()
    d["_alive"] = True
    return d.pivot(index="date", columns="symbol", values="_alive").fillna(False).astype(bool)


# ══════════════════════════════════════════════ 常用变换
def liquid_universe(df: pd.DataFrame, top_n: int = 50, lookback: int = 14,
                    min_quote_volume: float = 1e6,
                    col: str = "symbol") -> pd.DataFrame:
    """按滚动成交额给每日一个可选池（date × symbol 布尔表）。

    口径与研究报告一致：先要求「过去 14 天日均成交额 ≥ 100 万美元」，再取成交额最大的 N 个。
    `df` 应当是**日线**长表；成交额用归档官方的 `quote_volume`。
    """
    qv = wide(df, "quote_volume", _key(df, col))
    adv = qv.rolling(lookback, min_periods=max(3, lookback // 2)).mean()
    ok = adv >= min_quote_volume
    return (adv.where(ok).rank(axis=1, ascending=False) <= top_n)


def coverage(df: pd.DataFrame, col: str = "symbol") -> pd.DataFrame:
    """每个符号的覆盖概况，写报告/排查缺口时用。"""
    return (df.groupby(_key(df, col), observed=True)
              .agg(rows=("date", "size"), start=("date", "min"), end=("date", "max"),
                   zero_volume=("volume", lambda s: int((s <= 0).sum())))
              .sort_values("rows", ascending=False))


# ══════════════════════════════════════════════ 旧接口兼容
def load_panel(timeframe: str = "1h", market: str = "spot",
               columns: list[str] | None = None) -> pd.DataFrame:
    """兼容 `panel_io.load_panel`：返回长表，并补一列 `pair`（'BTC/USDT' 形式）。

    `策略池/横截面动量/scripts/` 里的脚本按 `pair` 分组，给它们留一条活路。
    """
    df = load(market, timeframe, columns=columns)
    if "symbol" in df.columns:
        df["pair"] = (df["symbol"].str.replace(r"^(.+)USDT$", r"\1/USDT", regex=True)
                      if "base" not in df.columns else df["base"] + "/" + df["quote"])
        df["pair"] = df["pair"].astype("category")
    return df


if __name__ == "__main__":
    for m in MARKETS:
        for t in TIMEFRAMES:
            try:
                ds_ = dataset(m, t)
            except FileNotFoundError:
                continue
            print(f"{m}/{t}: {len(available(m, t))} 个分区 / {ds_.count_rows():,} 行")
    try:
        d = symbols()
        print(f"\n符号主表 {len(d)} 行")
        print(d.groupby(["market", "status"]).size().to_string())
    except FileNotFoundError:
        pass
