#!/usr/bin/env python
"""
面板数据的读写与常用变换。

面板是**长表**：一行 = 一个币对一个时刻，(pair, date) 是联合主键。
做单因子检验时通常要转成宽表（date × pair），用 `wide()`。

    from panel_io import load_panel, wide
    df = load_panel("1h")
    close = wide(df, "close")          # index=date, columns=pair
    ret = close.pct_change(fill_method=None)
"""

from __future__ import annotations

import json
import os

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))


def panel_path(timeframe: str = "1h", market: str = "spot") -> str:
    return os.path.join(HERE, f"{market}_{timeframe}.parquet")


def load_panel(timeframe: str = "1h", market: str = "spot",
               columns: list[str] | None = None) -> pd.DataFrame:
    """读面板长表。默认把 pair 转成 str（parquet 里存的是字典编码）。"""
    p = panel_path(timeframe, market)
    if not os.path.exists(p):
        raise FileNotFoundError(f"面板不存在：{p}，先跑 build_panel.py")
    df = pd.read_parquet(p, columns=columns)
    df["pair"] = df["pair"].astype(str)
    return df


def load_manifest(timeframe: str = "1h", market: str = "spot") -> dict:
    with open(os.path.join(HERE, f"{market}_{timeframe}.manifest.json"),
              encoding="utf-8") as fh:
        return json.load(fh)


def wide(df: pd.DataFrame, field: str = "close") -> pd.DataFrame:
    """长表 → 宽表（index=交易时刻, columns=币对）。"""
    return df.pivot(index="date", columns="pair", values=field).sort_index()


def to_daily(df: pd.DataFrame, tz_day: str = "UTC") -> pd.DataFrame:
    """把 1h 面板聚合到日线，日界线固定为 UTC 00:00。

    **自己聚合而不是直接读 `-1d.feather`**：两边口径一致，避免日线与小时线
    在缺口/起点上各说各话。
    """
    need = {"pair", "date", "open", "high", "low", "close", "volume"}
    missing = need - set(df.columns)
    if missing:
        # 常见原因：load_panel(columns=[...]) 只取了 close，却仍然调用 to_daily
        raise ValueError(f"to_daily 需要完整 OHLCV 列，缺少：{sorted(missing)}")
    g = df.set_index("date").groupby("pair", observed=True)
    daily = g.resample("1D").agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
    ).reset_index()
    daily = daily.dropna(subset=["open", "close"])
    # 一整根都缺的日子会被 resample 造出全 NaN 行，上面 dropna 已清掉
    return daily


def liquid_universe(df: pd.DataFrame, top_n: int = 50,
                    lookback_h: int = 24 * 14,
                    min_quote_volume: float = 1e6) -> pd.DataFrame:
    """按滚动成交额给每日一个可选池（返回 date × pair 的布尔表）。

    和研报口径一致：先要求「过去 14 天日均成交额 ≥ 100 万美元」，再取成交额最大的 N 个。
    """
    qv = wide(df, "quote_volume")
    adv = qv.rolling(lookback_h, min_periods=lookback_h // 2).mean()
    ok = adv >= min_quote_volume
    rank = adv.where(ok).rank(axis=1, ascending=False)
    return rank <= top_n


def coverage(df: pd.DataFrame) -> pd.DataFrame:
    """每个币对的覆盖概况，写报告/排查缺口时用。"""
    return (df.groupby("pair", observed=True)
              .agg(rows=("date", "size"),
                   start=("date", "min"),
                   end=("date", "max"),
                   zero_vol=("volume", lambda s: int((s <= 0).sum())))
              .sort_values("rows", ascending=False))
