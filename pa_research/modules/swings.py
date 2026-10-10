"""确认式摆动点（Confirmed Swing Point）——09 §7 步骤 2。

对应规则定义卡片：cards/swing_definition_v1.yaml
────────────────────────────────────────────────────────────────────────
核心约束（04 §2、17 §2）：
  pivot_idx     —— 极值实际发生的 K 线位置（图上画在哪）
  confirmed_idx —— 算法确认它的位置 = pivot_idx + right（交易逻辑最早可用）
两者必须分开保存。绝不允许用未来数据确认后，把标签回填到过去生成信号。
────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _window_stack(a: np.ndarray, left: int, right: int) -> np.ndarray:
    """构造形状 (n, left+right+1) 的窗口矩阵，M[i, j] = a[i + j - left]。

    使用 np.roll 会在边缘环绕，但边缘行的窗口不完整、会被调用方丢弃，
    因此环绕值不会进入结果。
    """
    return np.stack([np.roll(a, -(j - left)) for j in range(left + right + 1)], axis=1)


def find_swings(
    df: pd.DataFrame, left: int = 2, right: int = 2
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """返回 (摆动高点表, 摆动低点表)。

    每张表含列：pivot_idx, confirmed_idx, price, pivot_time, confirmed_time。
    摆动高点的判定见卡片 operational_definition；
    已确认时点 = pivot_idx + right，故 confirmed_idx 对应 K 线的收盘后才可用。

    df 必须含 high/low 列且按时间升序；时间列名为 "date" 时一并返回时间戳。
    """
    if left < 1 or right < 1:
        raise ValueError("left/right 必须 >= 1")
    n = len(df)
    if n <= left + right:
        empty = pd.DataFrame(
            columns=["pivot_idx", "confirmed_idx", "price", "pivot_time", "confirmed_time"]
        )
        return empty, empty

    hi = df["high"].to_numpy(dtype=float)
    lo = df["low"].to_numpy(dtype=float)

    # 完整窗口掩码：i 满足 i-left >= 0 且 i+right < n
    idx = np.arange(n)
    full = (idx >= left) & (idx < n - right)

    hi_win = _window_stack(hi, left, right)
    lo_win = _window_stack(lo, left, right)
    hi_prev = np.concatenate([[np.nan], hi[:-1]])
    lo_prev = np.concatenate([[np.nan], lo[:-1]])

    # 平台期只取第一次出现：要求严格高于/低于前一根
    is_high = full & (hi == hi_win.max(axis=1)) & (hi > hi_prev)
    is_low = full & (lo == lo_win.min(axis=1)) & (lo < lo_prev)

    return (
        _to_table(df, np.flatnonzero(is_high), hi, right),
        _to_table(df, np.flatnonzero(is_low), lo, right),
    )


def _to_table(
    df: pd.DataFrame, pivot_idx: np.ndarray, price: np.ndarray, right: int
) -> pd.DataFrame:
    confirmed_idx = pivot_idx + right
    t = df["date"].to_numpy() if "date" in df.columns else np.arange(len(df))
    return pd.DataFrame(
        {
            "pivot_idx": pivot_idx,
            "confirmed_idx": confirmed_idx,
            "price": price[pivot_idx],
            "pivot_time": t[pivot_idx],
            "confirmed_time": t[confirmed_idx],
        }
    )


def assert_confirmation_delay(table: pd.DataFrame, right: int) -> None:
    """卡片 unit_test_cases 的硬断言：confirmed_idx - pivot_idx 恒等于 right。"""
    if table.empty:
        return
    d = (table["confirmed_idx"] - table["pivot_idx"]).to_numpy()
    assert np.all(d == right), f"确认延迟不等于 {right}：{np.unique(d)}"
