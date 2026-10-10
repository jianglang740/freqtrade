"""摆动结构 HH / HL / LH / LL —— 09 §7 步骤 3。

对应卡片：cards/structure_definition_v1.yaml
────────────────────────────────────────────────────────────────────────
只用**已确认**摆动点。标签的可用时点 = max(自身 confirmed_idx, 参照点 confirmed_idx)。
────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import numpy as np
import pandas as pd

STRUCTURE_LABELS = ["HH", "LH", "HL", "LL", "UNKNOWN"]


def build_structure(
    swing_highs: pd.DataFrame, swing_lows: pd.DataFrame
) -> pd.DataFrame:
    """合并两类摆动点并标注 HH/LH/HL/LL。

    返回列：pivot_idx, confirmed_idx, kind('H'/'L'), price, label, ref_price,
             available_idx（= max(自身 confirmed_idx, 参照 confirmed_idx)）。
    按 pivot_idx 升序；同一 right 下确认顺序与 pivot 顺序一致。
    """
    frames = []
    for kind, tbl in (("H", swing_highs), ("L", swing_lows)):
        if tbl is None or tbl.empty:
            continue
        t = tbl[["pivot_idx", "confirmed_idx", "price"]].copy()
        t["kind"] = kind
        frames.append(t)
    if not frames:
        return pd.DataFrame(
            columns=[
                "pivot_idx", "confirmed_idx", "kind", "price",
                "label", "ref_price", "available_idx",
            ]
        )

    seq = pd.concat(frames, ignore_index=True).sort_values("pivot_idx").reset_index(drop=True)

    labels: list[str] = []
    ref_prices: list[float] = []
    avail: list[int] = []
    last_price = {"H": None, "L": None}
    last_conf = {"H": None, "L": None}

    for row in seq.itertuples(index=False):
        k = row.kind
        prev = last_price[k]
        if prev is None:
            labels.append("UNKNOWN")
            ref_prices.append(np.nan)
            avail.append(int(row.confirmed_idx))
        else:
            if k == "H":
                labels.append("HH" if row.price > prev else "LH")
            else:
                labels.append("HL" if row.price > prev else "LL")
            ref_prices.append(prev)
            # 参照点可能确认更晚 → 可用时点取两者较晚
            avail.append(int(max(row.confirmed_idx, last_conf[k])))
        last_price[k] = row.price
        last_conf[k] = row.confirmed_idx

    seq["label"] = labels
    seq["ref_price"] = ref_prices
    seq["available_idx"] = avail
    return seq


def direction_series(
    structure: pd.DataFrame, n: int, lookback_swings: int = 4
) -> pd.DataFrame:
    """按 bar 生成结构方向序列（严格后视）。

    在 bar t，只纳入 available_idx <= t 的摆动点，取最近 lookback_swings 个，
    统计其标签得到：
      pa_structure_direction  ∈ {-1, 0, +1}  最近一次同类高低点是否同时偏多/偏空
      pa_structure_persistence ∈ [0,1]       最近若干标签中同向占比
      pa_structure_state      ∈ {trend_up, trend_down, mixed, unknown}

    返回与 bar 对齐的 DataFrame（index 0..n-1）。
    """
    out = pd.DataFrame(index=np.arange(n))
    out["pa_structure_direction"] = 0
    out["pa_structure_persistence"] = np.nan
    out["pa_structure_state"] = "unknown"

    if structure.empty:
        return out

    struct = structure.sort_values("available_idx").reset_index(drop=True)
    avail = struct["available_idx"].to_numpy()
    labels = struct["label"].to_numpy()
    kinds = struct["kind"].to_numpy()

    for t in range(n):
        upto = np.searchsorted(avail, t, side="right")
        if upto < 2:
            continue
        lo = max(0, upto - lookback_swings)
        window = [
            (kinds[j], labels[j])
            for j in range(lo, upto)
            if labels[j] != "UNKNOWN"
        ]
        if not window:
            continue

        bull = sum(lbl in ("HH", "HL") for _, lbl in window)
        bear = sum(lbl in ("LH", "LL") for _, lbl in window)
        out.iat[t, 1] = max(bull, bear) / len(window)

        # 方向由"最近一次摆动高点标签 + 最近一次摆动低点标签"共同决定
        last_h = next((lbl for k, lbl in reversed(window) if k == "H"), None)
        last_l = next((lbl for k, lbl in reversed(window) if k == "L"), None)

        if last_h == "HH" and last_l == "HL":
            out.iat[t, 0] = 1
            out.iat[t, 2] = "trend_up"
        elif last_h == "LH" and last_l == "LL":
            out.iat[t, 0] = -1
            out.iat[t, 2] = "trend_down"
        else:
            out.iat[t, 0] = 0
            out.iat[t, 2] = "mixed"
    return out
