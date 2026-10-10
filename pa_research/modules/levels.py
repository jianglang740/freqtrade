"""关键位置与距离因子 —— 09 §7 步骤 5。

对应卡片：cards/state_and_levels_v1.yaml
────────────────────────────────────────────────────────────────────────
04 §4：位置应表示为带容差的区间，不是精确单点。
04 §5：量化系统应计算到最近关键位置的距离，用波动尺度标准化，
       但**不能**把距离本身当作方向预测。
所有位置仅来自**已确认**摆动点。
────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def volatility_scale(df: pd.DataFrame, window: int = 20) -> np.ndarray:
    """滚动波动尺度 = 过去 window 根 true range 的均值（不是固定百分比）。

    02 §1 禁止固定涨跌幅；这里用滚动 TR 均值作为标准化尺度（13 §8）。
    """
    h = df["high"].to_numpy(dtype=float)
    l = df["low"].to_numpy(dtype=float)
    c = df["close"].to_numpy(dtype=float)
    prev_c = np.concatenate([[np.nan], c[:-1]])
    tr = np.maximum.reduce(
        [h - l, np.abs(h - prev_c), np.abs(l - prev_c)]
    )
    return pd.Series(tr).rolling(window, min_periods=window).mean().to_numpy()


def build_levels(
    df: pd.DataFrame,
    swing_highs: pd.DataFrame,
    swing_lows: pd.DataFrame,
    vol_window: int = 20,
    n_recent: int = 3,
    tol: float = 0.25,
) -> pd.DataFrame:
    """按 bar 生成位置与距离因子（严格后视）。

    返回列：
      pa_vol_scale            滚动波动尺度
      pa_range_upper/lower    最近 n_recent 个已确认摆动高点/低点构成的区间边界
      pa_range_position       (C − lower)/(upper − lower)，区间内 ∈[0,1]
      pa_distance_to_swing_high  最近已确认摆动高点相对价格的距离（vol 单位，正值=高点在上）
      pa_distance_to_swing_low   最近已确认摆动低点相对价格的距离（vol 单位，正值=低点在下）
      pa_near_level              价格是否落在任一关键位容差带内 [level ± tol·vol]
    """
    n = len(df)
    close = df["close"].to_numpy(dtype=float)
    vol = volatility_scale(df, vol_window)

    hs = swing_highs.sort_values("confirmed_idx").reset_index(drop=True) if swing_highs is not None else pd.DataFrame()
    ls = swing_lows.sort_values("confirmed_idx").reset_index(drop=True) if swing_lows is not None else pd.DataFrame()

    upper_arr = np.full(n, np.nan)
    lower_arr = np.full(n, np.nan)
    pos_arr = np.full(n, np.nan)
    d_hi = np.full(n, np.nan)
    d_lo = np.full(n, np.nan)
    near_arr = np.zeros(n, dtype=bool)

    for t in range(n):
        v = vol[t]
        if np.isnan(v) or v <= 0:
            continue
        kh = _confirmed_upto(hs, t)
        kl = _confirmed_upto(ls, t)
        ups = kh["price"].to_numpy()[-n_recent:] if len(kh) else np.array([])
        los = kl["price"].to_numpy()[-n_recent:] if len(kl) else np.array([])

        if len(ups):
            d_hi[t] = (ups[-1] - close[t]) / v
        if len(los):
            d_lo[t] = (close[t] - los[-1]) / v

        if len(ups) and len(los):
            upper, lower = float(ups.max()), float(los.min())
            if upper > lower:
                upper_arr[t] = upper
                lower_arr[t] = lower
                pos_arr[t] = (close[t] - lower) / (upper - lower)

        if len(ups) or len(los):
            levels = np.concatenate([ups, los])
            near_arr[t] = bool(np.any(np.abs(close[t] - levels) <= tol * v))

    out = pd.DataFrame(
        {
            "pa_vol_scale": vol,
            "pa_range_upper": upper_arr,
            "pa_range_lower": lower_arr,
            "pa_range_position": pos_arr,
            "pa_distance_to_swing_high": d_hi,
            "pa_distance_to_swing_low": d_lo,
            "pa_near_level": near_arr,
        },
        index=np.arange(n),
    )
    return out


def _confirmed_upto(table: pd.DataFrame, t: int) -> pd.DataFrame:
    """返回 confirmed_idx <= t 的行（t 时刻唯一可用的摆动点）。"""
    if table.empty:
        return table
    return table[table["confirmed_idx"] <= t]
