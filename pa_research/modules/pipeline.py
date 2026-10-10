"""上下文层组装：features → swings → structure → state → levels。

09 §7 步骤 1–5 的组合入口，供事件层（步骤 6+）调用。
所有输出严格后视；本模块不产生任何交易信号（13 §1）。
"""

from __future__ import annotations

import pandas as pd

from . import features, levels, pullback, state, structure, swings


def build_context(
    df: pd.DataFrame,
    left: int = 2,
    right: int = 2,
    efficiency_window: int = 20,
    overlap_window: int = 20,
    quantile_window: int = 200,
    vol_window: int = 20,
    n_recent: int = 3,
    tol: float = 0.25,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """返回 (带上下文列的 df, 结构表, 摆动高点表, 摆动低点表)。

    df 必须已按时间升序、含 open/high/low/close（可选 date）。
    输出的 context 列前缀见各模块；index 与 df 对齐。
    """
    need = {"open", "high", "low", "close"}
    missing = need - set(df.columns)
    if missing:
        raise ValueError(f"df 缺少列：{sorted(missing)}")

    f = features.add_features(df)
    sh, sl = swings.find_swings(f, left=left, right=right)
    struct = structure.build_structure(sh, sl)
    dirser = structure.direction_series(struct, n=len(f))
    st = state.classify(
        f,
        dirser["pa_structure_state"].to_numpy(),
        efficiency_window=efficiency_window,
        overlap_window=overlap_window,
        quantile_window=quantile_window,
    )
    lv = levels.build_levels(f, sh, sl, vol_window=vol_window, n_recent=n_recent, tol=tol)
    pb = pullback.compute_pullback(
        f, struct, sh, sl, st["pa_state"].to_numpy(), lv["pa_vol_scale"].to_numpy()
    )

    ctx = pd.concat([f, dirser, st, lv, pb], axis=1)
    # 清理重复列（direction_series 与 classify 都返回 dataframe，列名不冲突）
    return ctx, struct, sh, sl


def assert_no_lookahead(struct: pd.DataFrame, n: int) -> None:
    """硬断言：任何结构标签的 available_idx 不得早于其 confirmed_idx。"""
    if struct.empty:
        return
    bad = struct["available_idx"] < struct["confirmed_idx"]
    assert not bad.any(), f"存在 available_idx < confirmed_idx 的行：{struct[bad]}"
    assert (struct["available_idx"] < n).all(), "available_idx 越界"
    assert (struct["pivot_idx"] < n).all(), "pivot_idx 越界"
