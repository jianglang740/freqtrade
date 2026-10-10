"""市场状态候选分类 —— 09 §7 步骤 4。

对应卡片：cards/state_and_levels_v1.yaml
────────────────────────────────────────────────────────────────────────
02_market_states.md §1 明文禁止用均线斜率、ADX 或固定涨跌幅定义 Brooks 式趋势。
因此这里只用三类可观测量：结构序列、趋势效率、重叠密度。
所有阈值用**滚动分位数**，不用绝对值（13 §8）。
────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import numpy as np
import pandas as pd

STATE_LABELS = ["trend_up", "trend_down", "range", "transition"]


def trend_efficiency(close: np.ndarray, n: int) -> np.ndarray:
    """pa_trend_efficiency = |C_t − C_{t−n}| / Σ|C_i − C_{i−1}|（13 §4 家族 B）。

    高值表示路径更有方向性，**不代表上涨或下跌**。分母为 0 时记 NaN。
    """
    c = np.asarray(close, dtype=float)
    net = np.abs(c - np.concatenate([np.full(n, np.nan), c[:-n]]))
    step = np.abs(np.diff(c, prepend=np.nan))
    path = pd.Series(step).rolling(n, min_periods=n).sum().to_numpy()
    with np.errstate(invalid="ignore", divide="ignore"):
        eff = net / path
    eff[path <= 0] = np.nan
    return eff


def overlap_density(overlap: np.ndarray, n: int) -> np.ndarray:
    """滚动窗口内相邻 K 线重叠程度的均值（13 §4 家族 E）。

    注意：20 §4.2 已证伪"用单对相邻 K 线重叠识别震荡"，
    必须用窗口统计；单对重叠中位数在 4h 上已达 0.83。
    """
    return pd.Series(overlap).rolling(n, min_periods=n).mean().to_numpy()


def _rolling_quantile(s: np.ndarray, n: int, q: float) -> np.ndarray:
    """仅使用当时及之前数据的滚动分位（13 §8 禁止全样本标准化）。"""
    return pd.Series(s).rolling(n, min_periods=max(20, n // 4)).quantile(q).to_numpy()


def classify(
    df: pd.DataFrame,
    structure_state: np.ndarray,
    efficiency_window: int = 20,
    overlap_window: int = 20,
    quantile_window: int = 200,
    hi_q: float = 0.75,
) -> pd.DataFrame:
    """生成状态候选（后视）。

    df 需含 close 与 feat_overlap；structure_state 为 structure.direction_series 的
    pa_structure_state 列。
    返回列：pa_trend_efficiency, pa_overlap_density, pa_state。
    """
    n = len(df)
    close = df["close"].to_numpy(dtype=float)
    overlap = df["feat_overlap"].to_numpy(dtype=float)

    eff = trend_efficiency(close, efficiency_window)
    ovl = overlap_density(overlap, overlap_window)

    eff_hi = _rolling_quantile(eff, quantile_window, hi_q)
    ovl_hi = _rolling_quantile(ovl, quantile_window, hi_q)

    out = pd.DataFrame(index=np.arange(n))
    out["pa_trend_efficiency"] = eff
    out["pa_overlap_density"] = ovl

    state = np.full(n, "unknown", dtype=object)
    for t in range(n):
        ss = structure_state[t]
        if np.isnan(ovl[t]):
            continue
        ovl_high = (not np.isnan(ovl_hi[t])) and ovl[t] >= ovl_hi[t]
        # 状态 = 结构方向 × 重叠密度。趋势效率作为**独立因子**输出，
        # 不作为状态门槛（否则趋势与"效率位于上四分位"几乎不同时成立，
        # 会把七成 bar 压进 transition —— 实测 BTC 4h 72.5%）。
        if ss == "trend_up" and not ovl_high:
            state[t] = "trend_up"
        elif ss == "trend_down" and not ovl_high:
            state[t] = "trend_down"
        elif ovl_high:
            state[t] = "range"
        else:
            state[t] = "transition"
    out["pa_state"] = state
    return out
