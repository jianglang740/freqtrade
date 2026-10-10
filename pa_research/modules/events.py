"""突破 / 跟随 / 失败突破状态机 —— 09 §7 步骤 6–8。

对应卡片：cards/failed_breakout_definition_v1.yaml
────────────────────────────────────────────────────────────────────────
状态机（09 §5）：
  NO_BREAKOUT → BREAKOUT_CANDIDATE
                   ├─→ FOLLOW_THROUGH_CONFIRMED
                   ├─→ RETURN_TO_RANGE → FAILED_BREAKOUT_CONFIRMED
                   └─→ EXPIRED

列命名约定（防止未来函数）：
  feat_*   第 t 根可知的**输入**特征
  outcome_* / label_*  **结果**，用到未来数据，只可用于评估
────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import numpy as np
import pandas as pd

OUTCOME_LABELS = ["follow_through", "failed", "expired"]

EVENT_COLUMNS = [
    "event_id", "direction", "candidate_idx", "candidate_time",
    "reference_level", "context_state",
    "feat_breakout_distance", "feat_breakout_close_quality", "feat_breakout_bar_body",
    "outcome", "outcome_idx", "outcome_time", "status",
    "label_forward_return", "label_max_favorable", "label_max_adverse",
]


def detect_breakouts(
    df: pd.DataFrame,
    swing_highs: pd.DataFrame,
    swing_lows: pd.DataFrame,
    context_state: np.ndarray,
    vol: np.ndarray,
    follow_window: int = 3,
    fail_window: int = 6,
    horizon: int = 6,
) -> pd.DataFrame:
    """扫描全序列，返回突破事件表（每次突破尝试一行，唯一 event_id）。

    参数
      follow_window  候选后多少根内出现同向收盘算跟随
      fail_window    候选后多少根内回到边界内算失败
      horizon        评估用的前向收益窗口（vol 单位）

    context_state / vol 为按 bar 的数组，长度须等于 len(df)。
    """
    n = len(df)
    o = df["open"].to_numpy(dtype=float)
    h = df["high"].to_numpy(dtype=float)
    l = df["low"].to_numpy(dtype=float)
    c = df["close"].to_numpy(dtype=float)
    t_arr = df["date"].to_numpy() if "date" in df.columns else np.arange(n)

    hs = _sorted_arrays(swing_highs)
    ls = _sorted_arrays(swing_lows)

    events: list[dict] = []
    last_broken_ref: dict[int, float | None] = {1: None, -1: None}

    for t in range(1, n):
        if np.isnan(vol[t]) or vol[t] <= 0:
            continue
        # 每根 K 线最多产生一个候选：只在"首次穿越"时触发，两个方向取先满足者
        for direction, tbl in ((1, hs), (-1, ls)):
            ref = _last_confirmed_price(tbl, t)
            if ref is None:
                continue
            # 同一条边界被反复穿越（价格在边界附近来回）不算多个突破事件：
            # 只有边界本身更新后才允许产生新的候选。
            if last_broken_ref[direction] is not None and ref == last_broken_ref[direction]:
                continue
            crossed_now = (c[t] > ref) if direction == 1 else (c[t] < ref)
            crossed_prev = (c[t - 1] > ref) if direction == 1 else (c[t - 1] < ref)
            if not (crossed_now and not crossed_prev):
                continue

            ev = _classify_candidate(
                t, direction, ref, o, h, l, c, t_arr, context_state, vol,
                follow_window, fail_window, horizon,
            )
            ev["event_id"] = len(events)
            events.append(ev)
            last_broken_ref[direction] = ref
            break  # 本根已产生候选，不再检查另一方向

    if not events:
        return pd.DataFrame(columns=EVENT_COLUMNS)
    return pd.DataFrame(events)[EVENT_COLUMNS]


def _sorted_arrays(tbl: pd.DataFrame | None) -> tuple[np.ndarray, np.ndarray]:
    """按 confirmed_idx 排序，返回 (confirmed_idx, price) 并行数组（供二分查找）。"""
    if tbl is None or tbl.empty:
        e = np.empty(0, dtype=np.int64)
        return e, np.empty(0, dtype=float)
    t = tbl.sort_values("confirmed_idx")
    return (t["confirmed_idx"].to_numpy(dtype=np.int64),
            t["price"].to_numpy(dtype=float))


def _last_confirmed_price(arrs: tuple[np.ndarray, np.ndarray], t: int) -> float | None:
    """最近一个 confirmed_idx <= t 的摆动点价格（t 时刻可知）。"""
    conf, px = arrs
    i = int(np.searchsorted(conf, t, side="right")) - 1
    return None if i < 0 else float(px[i])


def _classify_candidate(
    t, direction, ref, o, h, l, c, t_arr, context_state, vol,
    follow_window, fail_window, horizon,
) -> dict:
    n = len(c)
    rng = h[t] - l[t]
    rng_safe = max(rng, 1e-12)

    # --- 输入特征（只用 t 及之前）---
    dist = direction * (c[t] - ref) / vol[t]
    # 收盘质量：突破方向的收盘位置（多头看向上收盘，空头看向下收盘）
    clv = (c[t] - l[t]) / rng_safe
    close_quality = clv if direction == 1 else (1.0 - clv)
    body = abs(c[t] - o[t]) / rng_safe

    # --- 结果（用到未来数据，仅用于评估）---
    outcome, outcome_idx = "expired", min(t + fail_window, n - 1)
    hi_end = min(t + fail_window, n - 1)
    for j in range(t + 1, hi_end + 1):
        if direction == 1:
            if c[j] > ref and c[j] > c[t]:
                outcome, outcome_idx = "follow_through", j
                break
            if c[j] < ref:
                outcome, outcome_idx = "failed", j
                break
        else:
            if c[j] < ref and c[j] < c[t]:
                outcome, outcome_idx = "follow_through", j
                break
            if c[j] > ref:
                outcome, outcome_idx = "failed", j
                break

    jend = min(t + horizon, n - 1)
    fwd = direction * (c[jend] - c[t]) / vol[t]
    if direction == 1:
        mfe = (h[t + 1 : jend + 1].max() - c[t]) / vol[t] if jend > t else np.nan
        mae = (c[t] - l[t + 1 : jend + 1].min()) / vol[t] if jend > t else np.nan
    else:
        mfe = (c[t] - l[t + 1 : jend + 1].min()) / vol[t] if jend > t else np.nan
        mae = (h[t + 1 : jend + 1].max() - c[t]) / vol[t] if jend > t else np.nan

    return {
        "direction": direction,
        "candidate_idx": t,
        "candidate_time": t_arr[t],
        "reference_level": ref,
        "context_state": context_state[t],
        "feat_breakout_distance": dist,
        "feat_breakout_close_quality": close_quality,
        "feat_breakout_bar_body": body,
        "outcome": outcome,
        "outcome_idx": outcome_idx,
        "outcome_time": t_arr[outcome_idx],
        "status": "confirmed" if outcome == "follow_through" else outcome,
        "label_forward_return": fwd,
        "label_max_favorable": mfe,
        "label_max_adverse": mae,
    }


def assert_event_time_model(events: pd.DataFrame) -> None:
    """卡片硬断言：outcome_idx 严格大于 candidate_idx；event_id 唯一。"""
    if events.empty:
        return
    assert (events["outcome_idx"] > events["candidate_idx"]).all(), "存在 outcome_idx <= candidate_idx"
    assert events["event_id"].is_unique, "event_id 不唯一"
    assert events["outcome"].isin(OUTCOME_LABELS).all(), "出现未定义的 outcome"
