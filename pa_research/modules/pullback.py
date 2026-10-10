"""趋势回调与 H1/H2/L1/L2 二次入场 —— 09 §7 步骤 9–10。

对应卡片：cards/pullback_and_h2_definition_v1.yaml
────────────────────────────────────────────────────────────────────────
关键纠正（14 §3）：H2 是**腿的计数**，不是"最近第二根阳线"。
回调腿只在摆动低点**已确认**后计入，故 k 值严格后视。
────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def compute_pullback(
    df: pd.DataFrame,
    structure: pd.DataFrame,
    swing_highs: pd.DataFrame,
    swing_lows: pd.DataFrame,
    trend_state: np.ndarray,
    vol: np.ndarray,
) -> pd.DataFrame:
    """按 bar 生成回调与二次入场因子（后视）。

    trend_state 为 per-bar 的 pa_state（'trend_up'/'trend_down'/其它）。
    返回列：
      pa_pullback_legs      自最近 HH（或 LL）以来已确认的回调腿数 k
      pa_h_count            trend_up 中向上触发时的 k（H1→1, H2→2…），否则 NaN
      pa_l_count            trend_down 中向下触发时的 k
      pa_trigger_up/down    该 bar 是否构成同向触发
      pa_pullback_depth     回调深度（前段推进腿幅度为分母）
      pa_pullback_duration  距最近 HH/LL 的 pivot 根数
      pa_pullback_anchor    回调腿极值：多头=回调区间最低点，空头=最高点。
                            这是"回调假设"的失效位（cards/exit_and_risk_v1.yaml §止损），
                            在 t 时刻已可知（只用 [锚点 pivot, t] 的已发生极值）。
    """
    n = len(df)
    h = df["high"].to_numpy(dtype=float)
    l = df["low"].to_numpy(dtype=float)
    c = df["close"].to_numpy(dtype=float)

    legs = np.full(n, np.nan)
    depth = np.full(n, np.nan)
    duration = np.full(n, np.nan)
    anchor = np.full(n, np.nan)
    trig_up = np.zeros(n, dtype=bool)
    trig_dn = np.zeros(n, dtype=bool)
    h_count = np.full(n, np.nan)
    l_count = np.full(n, np.nan)

    sh_c, sh_p, sh_x = _prep(swing_highs)
    sl_c, sl_p, sl_x = _prep(swing_lows)
    if structure is not None and not structure.empty:
        hh_c, hh_p, hh_x = _prep(structure[(structure["kind"] == "H")
                                           & (structure["label"] == "HH")])
        ll_c, ll_p, ll_x = _prep(structure[(structure["kind"] == "L")
                                           & (structure["label"] == "LL")])
    else:
        _e = np.empty(0, dtype=np.int64)
        hh_c = hh_p = ll_c = ll_p = _e
        hh_x = ll_x = np.empty(0, dtype=float)

    for t in range(1, n):
        st = trend_state[t]
        k_up = _count_legs(hh_c, hh_p, sl_c, sl_p, t)   # HH 之后已确认的摆动低点数
        k_dn = _count_legs(ll_c, ll_p, sh_c, sh_p, t)

        if st == "trend_up":
            trig_up[t] = c[t] > h[t - 1]
            if not np.isnan(k_up):
                legs[t] = k_up
                if trig_up[t] and k_up >= 1:
                    h_count[t] = k_up
            d, dur, anc = _pullback_geometry(hh_c, hh_p, hh_x, sl_p, sl_x, t, l, vol[t], "up")
            depth[t], duration[t], anchor[t] = d, dur, anc
        elif st == "trend_down":
            trig_dn[t] = c[t] < l[t - 1]
            if not np.isnan(k_dn):
                legs[t] = k_dn
                if trig_dn[t] and k_dn >= 1:
                    l_count[t] = k_dn
            d, dur, anc = _pullback_geometry(ll_c, ll_p, ll_x, sh_p, sh_x, t, h, vol[t], "down")
            depth[t], duration[t], anchor[t] = d, dur, anc

    return pd.DataFrame(
        {
            "pa_pullback_legs": legs,
            "pa_h_count": h_count,
            "pa_l_count": l_count,
            "pa_trigger_up": trig_up,
            "pa_trigger_down": trig_dn,
            "pa_pullback_depth": depth,
            "pa_pullback_duration": duration,
            "pa_pullback_anchor": anchor,
        },
        index=np.arange(n),
    )


def _prep(tbl: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """按 pivot_idx 排序，返回 (confirmed_idx, pivot_idx, price) 三个并行数组。

    swings.py 保证 confirmed_idx = pivot_idx + right，故按 pivot_idx 排序后
    confirmed_idx 亦严格递增 —— 这是下面全部二分查找成立的前提。
    """
    if tbl is None or tbl.empty:
        e = np.empty(0, dtype=np.int64)
        return e, e, np.empty(0, dtype=float)
    t = tbl.sort_values("pivot_idx")
    return (
        t["confirmed_idx"].to_numpy(dtype=np.int64),
        t["pivot_idx"].to_numpy(dtype=np.int64),
        t["price"].to_numpy(dtype=float),
    )


def _count_legs(
    a_conf: np.ndarray, a_piv: np.ndarray,
    o_conf: np.ndarray, o_piv: np.ndarray, t: int,
) -> float:
    """最近一个已确认的锚点之后、已确认的反向摆动点数。

    这是 H(k)/L(k) 的 k 值来源：k 条已完成的回调腿 → 下一次同向触发是第 k 次。
    计数 = #{反向点: confirmed <= t} − #{反向点: pivot <= 锚点 pivot}，
    二者之差恰为 #{pivot > 锚点 pivot 且 confirmed <= t}（由上面的单调性保证）。
    """
    i = int(np.searchsorted(a_conf, t, side="right")) - 1
    if i < 0:
        return np.nan
    j_conf = int(np.searchsorted(o_conf, t, side="right"))
    j_piv = int(np.searchsorted(o_piv, a_piv[i], side="right"))
    return float(j_conf - j_piv)


def _pullback_geometry(
    a_conf: np.ndarray, a_piv: np.ndarray, a_px: np.ndarray,
    o_piv: np.ndarray, o_px: np.ndarray,
    t: int, extreme: np.ndarray, vol: float, side: str,
) -> tuple[float, float, float]:
    """返回 (回调深度, 距锚点的根数, 回调腿极值)。深度分母为前一段推进腿幅度。

    side='up'：锚点=HH，extreme=low；side='down'：锚点=LL，extreme=high。

    第三项 pa_pullback_anchor 是回调假设的失效位（exit_and_risk_v1.yaml）：
    多头=回调区间最低点，空头=最高点。只用到 [锚点 pivot, t] 已发生的极值，
    故在 t 时刻可知，非前视。
    """
    if len(a_conf) == 0 or not np.isfinite(vol) or vol <= 0:
        return np.nan, np.nan, np.nan
    i = int(np.searchsorted(a_conf, t, side="right")) - 1
    if i < 0:
        return np.nan, np.nan, np.nan
    hh_idx, hh_price = int(a_piv[i]), float(a_px[i])
    if hh_idx >= t:
        return np.nan, np.nan, np.nan
    if len(o_piv) == 0:
        return np.nan, np.nan, np.nan

    # 前一段推进腿的起点：锚点之前的最后一个反向摆动点
    pi = int(np.searchsorted(o_piv, hh_idx, side="left")) - 1

    if side == "up":
        ext = float(extreme[hh_idx : t + 1].min())
        if pi < 0:
            return np.nan, float(t - hh_idx), ext
        leg = hh_price - float(o_px[pi])
        dep = (hh_price - ext) / leg if leg > 0 else np.nan
    else:
        ext = float(extreme[hh_idx : t + 1].max())
        if pi < 0:
            return np.nan, float(t - hh_idx), ext
        leg = float(o_px[pi]) - hh_price
        dep = (ext - hh_price) / leg if leg > 0 else np.nan
    return float(dep), float(t - hh_idx), ext
