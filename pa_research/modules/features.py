"""基础特征（Primitive Feature）——09_quantification_spec.md §7 步骤 1。

────────────────────────────────────────────────────────────────────────
本模块只做测量，不产生任何交易信号（13_price_action_factors.md §1）。

定义与 20_bar_feature_calibration.md 的实测分布一一对应，公式取自
13_price_action_factors.md §3 Step 3，不重新发明。
────────────────────────────────────────────────────────────────────────

未来函数边界：每根 K 线的特征只使用该根及更早的数据（相邻重叠用到 t-1），
在时点 t 的收盘后即可用，无前视。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

EPS = 1e-12

# 本模块新增的列名，供下游按名引用，避免拼写漂移
FEATURE_COLUMNS = [
    "feat_body_ratio",
    "feat_close_location",
    "feat_directional_body",
    "feat_upper_wick",
    "feat_lower_wick",
    "feat_norm_range",
    "feat_overlap",
]


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    """在 OHLCV 上追加基础特征列，返回新 DataFrame（不修改入参）。

    列语义见 13 §3 Step 3：
      body_ratio       |C-O| / R           ∈ [0,1]
      close_location   (C-L) / R           ∈ [0,1]
      directional_body (C-O) / R           ∈ [-1,1]  正=偏多
      upper_wick       (H-max(O,C)) / R    ∈ [0,1]
      lower_wick       (min(O,C)-L) / R    ∈ [0,1]
      norm_range       R / C               价格标准化幅度
      overlap          相邻 K 线范围重叠率   ∈ [0,1]，首行为 NaN

    缺失语义：R=0 时以 EPS 兜底，不产生 inf；overlap 首根为 NaN（无前一根），
    不得填 0（13 §6：0 应表示真实测量接近零）。
    调用方须保证 df 已按时间升序、且 high/low 与开收盘自洽。
    """
    out = df.copy()
    o = out["open"].to_numpy(dtype=float)
    h = out["high"].to_numpy(dtype=float)
    l = out["low"].to_numpy(dtype=float)
    c = out["close"].to_numpy(dtype=float)

    r = h - l
    r_safe = np.maximum(r, EPS)

    prev_h = np.concatenate([[np.nan], h[:-1]])
    prev_l = np.concatenate([[np.nan], l[:-1]])
    prev_r = np.concatenate([[np.nan], r[:-1]])

    out["feat_body_ratio"] = np.abs(c - o) / r_safe
    out["feat_close_location"] = (c - l) / r_safe
    out["feat_directional_body"] = (c - o) / r_safe
    out["feat_upper_wick"] = (h - np.maximum(o, c)) / r_safe
    out["feat_lower_wick"] = (np.minimum(o, c) - l) / r_safe
    out["feat_norm_range"] = r / np.maximum(c, EPS)
    out["feat_overlap"] = np.maximum(
        0.0, np.minimum(h, prev_h) - np.maximum(l, prev_l)
    ) / np.maximum(np.minimum(r, prev_r), EPS)

    return out


def check_ohlc_integrity(df: pd.DataFrame) -> int:
    """数据卫生检查：返回 high/low 与开收盘矛盾、close<=0 的行数。

    与 scripts/pa_feature_calibration.py 的检查一致（本项目 95 对 4h 上为 0 行）。
    调用方应据此决定是否剔除，而不是静默忽略。
    """
    h, l = df["high"].to_numpy(dtype=float), df["low"].to_numpy(dtype=float)
    o, c = df["open"].to_numpy(dtype=float), df["close"].to_numpy(dtype=float)
    bad = (
        (h < np.maximum(o, c) - EPS)
        | (l > np.minimum(o, c) + EPS)
        | (h < l - EPS)
        | (c <= 0)
    )
    return int(bad.sum())
