"""在面板上构建事件表 → pa_research/reports/events_{tf}.parquet

事件类型：
  breakout_up / breakout_down   突破候选（events.detect_breakouts）
  h1 / h2 / h3                  上升趋势中的第 k 次向上触发
  l1 / l2 / l3                  下降趋势中的第 k 次向下触发

每行 = 一个事件，含
  symbol, tf, event_type, direction, entry_idx（= candidate_idx + 1，次日开盘入场）
  ref_level, context_state
  feat_* 第 entry_idx-1 根可知的输入特征
  （结果不在此处计算；由 02_stat_gate.py 用价格数据模拟，避免把未来数据混入事件表）

运行：python pa_research/scripts/01_build_events.py [--tf 4h] [--limit N]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "pa_research"))

from modules.events import detect_breakouts  # noqa: E402
from modules.pipeline import assert_no_lookahead, build_context  # noqa: E402
from modules.swings import find_swings  # noqa: E402

DATA = REPO_ROOT / "user_data/data/binance/futures"
CONFIG = REPO_ROOT / "pa_research/freqtrade/config_brooks_pa.json"
OUT = REPO_ROOT / "pa_research/reports"

FEAT_COLS = [
    "pa_state", "pa_range_position", "pa_distance_to_swing_high",
    "pa_distance_to_swing_low", "pa_near_level", "pa_vol_scale",
    "pa_pullback_depth", "pa_pullback_duration",
    # 结构性出场（cards/exit_and_risk_v1.yaml）所需的输入：
    # 区间上下沿用作目标位，回调腿极值用作止损失效位。
    "pa_range_upper", "pa_range_lower", "pa_pullback_anchor",
]


def load_panel() -> list[str]:
    cfg = json.loads(CONFIG.read_text())
    return [p.replace("/", "_").replace(":", "_") for p in cfg["exchange"]["pair_whitelist"]]


def build_one(sym: str, tf: str, boundary_window: int = 5) -> pd.DataFrame:
    fp = DATA / f"{sym}-{tf}-futures.feather"
    if not fp.exists():
        return pd.DataFrame()
    df = pd.read_feather(fp)
    df = df.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)
    if len(df) < 500:
        return pd.DataFrame()

    ctx, struct, sh, sl = build_context(df)
    assert_no_lookahead(struct, len(df))

    # 突破边界用**更大窗口**的摆动点：结构腿用 left=right=2，
    # 但若边界也用 2 根，则"突破"退化为几乎每 3 根 K 线一次的价格抖动，
    # 不构成 05 §1 所说的"有意义的结构边界"。故边界单独用 boundary_window。
    sh_b, sl_b = find_swings(df, left=boundary_window, right=boundary_window)

    rows: list[dict] = []
    vol = ctx["pa_vol_scale"].to_numpy()
    state = ctx["pa_state"].to_numpy()
    t_arr = df["date"].to_numpy()

    def emit(event_type, direction, idx, ref, extra):
        if idx + 1 >= len(df):
            return
        e = idx + 1  # 次日开盘入场
        row = {
            "symbol": sym, "tf": tf, "event_type": event_type,
            "direction": direction, "entry_idx": e, "entry_time": t_arr[e],
            "ref_level": ref, "context_state": state[idx],
        }
        for c in FEAT_COLS:
            row[c] = ctx[c].iloc[idx]
        row.update(extra)
        rows.append(row)

    ev = detect_breakouts(df, sh_b, sl_b, state, vol)
    for r in ev.itertuples(index=False):
        emit(
            "breakout_up" if r.direction == 1 else "breakout_down",
            int(r.direction), int(r.candidate_idx), float(r.reference_level),
            {
                "feat_breakout_distance": r.feat_breakout_distance,
                "feat_breakout_close_quality": r.feat_breakout_close_quality,
                "feat_breakout_bar_body": r.feat_breakout_bar_body,
            },
        )

    for k in (1, 2, 3):
        for idx in np.flatnonzero(ctx["pa_h_count"].to_numpy() == k):
            emit(f"h{k}", 1, int(idx), float(df["high"].iloc[idx]), _pb_extra(ctx, idx, 1))
        for idx in np.flatnonzero(ctx["pa_l_count"].to_numpy() == k):
            emit(f"l{k}", -1, int(idx), float(df["low"].iloc[idx]), _pb_extra(ctx, idx, -1))

    return pd.DataFrame(rows)


def _pb_extra(ctx: pd.DataFrame, idx: int, direction: int) -> dict:
    """回调触发事件的输入特征。close_quality 用方向调整后的收盘位置。"""
    clv = float(ctx["feat_close_location"].iloc[idx])
    return {
        "feat_breakout_distance": np.nan,
        "feat_breakout_close_quality": clv if direction == 1 else 1.0 - clv,
        "feat_breakout_bar_body": ctx["feat_body_ratio"].iloc[idx],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tf", default="4h")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--boundary-window", type=int, default=5)
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    panel = load_panel()
    if args.limit:
        panel = panel[: args.limit]

    frames = []
    t0 = time.time()
    for i, sym in enumerate(panel, 1):
        f = build_one(sym, args.tf, boundary_window=args.boundary_window)
        if not f.empty:
            frames.append(f)
        print(f"[{i}/{len(panel)}] {sym} {args.tf}: {len(f)} 事件  ({time.time()-t0:.0f}s)",
              file=sys.stderr)

    if not frames:
        print("没有事件产出")
        return
    all_ev = pd.concat(frames, ignore_index=True)
    out = OUT / f"events_{args.tf}.parquet"
    all_ev.to_parquet(out, index=False)
    print(f"\n写入 {out}  共 {len(all_ev)} 事件，{all_ev['symbol'].nunique()} 个品种")
    print(all_ev["event_type"].value_counts().to_string())


if __name__ == "__main__":
    main()
