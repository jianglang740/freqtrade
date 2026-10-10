"""结构性出场 —— 对应卡片 cards/exit_and_risk_v1.yaml（**先于本文件冻结**）。

与 bracket.py 的关系：
  bracket.py   = v0 基线，纯波动率括号（止损 1×vol / 目标 2×vol / 12 根到期）。
                 保留不动，作为并排对照。
  exits.py     = v1，按 06 §3（止损=失效点）与 §4（目标=结构位）重做。

两者的 simulate 返回**同一组键**，故可直接并排比较。

严禁在此模块里做参数搜索：卡片已冻结，参数改动必须另起卡片版本。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .bracket import FEE_ROUNDTRIP, load_arrays, load_funding

EVENT_IS_BREAKOUT = ("breakout_up", "breakout_down")


@dataclass(frozen=True)
class StructuralExitPolicy:
    """卡片 exit_and_risk_v1.yaml 冻结的参数。"""

    tol: float = 0.25            # 04 §4：位置容差，vol 单位
    breakeven_at_R: float | None = 1.0   # 06 §5：显式声明为"开"
    horizon: int = 24            # 兜底，不再作为主要出场


def plan_exit(
    event_type: str,
    direction: int,
    entry: float,
    ref_level: float,
    anchor: float,
    range_upper: float,
    range_lower: float,
    vol: float,
    policy: StructuralExitPolicy,
    _diag: dict | None = None,
) -> dict | None:
    """算出该事件的结构止损/目标/失效位。不可交易时返回 None。

    只用到信号根 t 及之前的信息（全部来自 confirmed_idx <= t 的摆动点）。

    _diag：可选。传一个 dict 进来时，被拒的原因会写进 `_diag["reject"]`
    （纯诊断用，不影响任何交易判定）。
    """
    def _reject(why: str) -> None:
        if _diag is not None:
            _diag["reject"] = why
        return None

    if not np.isfinite(vol) or vol <= 0 or not np.isfinite(entry) or entry <= 0:
        return _reject("no_vol")

    # ── 失效位（06 §3）──
    is_bo = event_type in EVENT_IS_BREAKOUT
    inval = float(ref_level) if is_bo else float(anchor)
    if not np.isfinite(inval):
        return _reject("no_inval_level")

    # ── 止损 = 失效位 + 噪声缓冲 ──
    buf = policy.tol * vol
    stop = inval - buf if direction == 1 else inval + buf
    if direction == 1 and stop >= entry:
        return _reject("stop_wrong_side")   # 止损落在入场价的错误一侧 → 不可交易
    if direction == -1 and stop <= entry:
        return _reject("stop_wrong_side")
    stop_dist = abs(entry - stop)
    if not np.isfinite(stop_dist) or stop_dist <= 0:
        return _reject("zero_stop_dist")

    # ── 目标 = 结构位（06 §4），缺失则退化为测量走势 ──
    lvl = float(range_upper) if direction == 1 else float(range_lower)
    ok = np.isfinite(lvl) and ((lvl > entry) if direction == 1 else (lvl < entry))
    used_measured_move = False
    if not ok:
        height = float(range_upper) - float(range_lower)
        if np.isfinite(height) and height > 0:
            lvl = entry + direction * height
            ok = (lvl > entry) if direction == 1 else (lvl < entry)
            used_measured_move = ok
    if not ok:
        return _reject("no_target_level")
    target_dist = abs(lvl - entry)
    if not np.isfinite(target_dist) or target_dist <= 0:
        return _reject("zero_target_dist")

    # ── 空间预筛（06 §4 + 19 §2.1）：至少覆盖成本并剩到 1R ──
    fee_amt = FEE_ROUNDTRIP * entry
    if target_dist < stop_dist + fee_amt:
        return _reject("insufficient_room")

    return {
        "stop": float(stop),
        "target": float(lvl),
        "inval": inval,
        "stop_dist": float(stop_dist),
        "target_dist": float(target_dist),
        "rr": float(target_dist / stop_dist),
        "measured_move": used_measured_move,
    }


def simulate_structural(
    row: dict,
    prices,
    funding,
    policy: StructuralExitPolicy,
) -> dict:
    """模拟一笔结构性交易。返回键与 bracket.simulate 对齐，另加 plan/breakeven 字段。

    盘中优先级（卡片 operational_definition，保守优先）：
        1 止损 → 2 目标 → 3 收盘回到结构内（失效） → 4 到期
    同一根内止损与目标同时触及 → 取止损。
    失效判定**只看收盘**（bar t 收盘即可知），不看该根的 high/low，故非前视。
    """
    arr = prices if isinstance(prices, dict) else None
    if arr is None:
        return {}
    i = int(row["entry_idx"])
    direction = int(row["direction"])
    n = arr["n"]
    if i >= n:
        return {}

    entry = arr["o"][i]
    plan = plan_exit(
        row["event_type"], direction, entry,
        row.get("ref_level", np.nan), row.get("pa_pullback_anchor", np.nan),
        row.get("pa_range_upper", np.nan), row.get("pa_range_lower", np.nan),
        float(row["pa_vol_scale"]), policy,
    )
    if plan is None:
        return {}

    stop, target, inval = plan["stop"], plan["target"], plan["inval"]
    stop_dist = plan["stop_dist"]
    o, h, l, c, t_ns = arr["o"], arr["h"], arr["l"], arr["c"], arr["t"]

    end = min(i + policy.horizon, n - 1)
    exit_price, reason, exit_i = float(c[end]), "horizon", end
    breakeven_moved = False

    for j in range(i, end + 1):
        if direction == 1:
            if l[j] <= stop:
                exit_price, reason, exit_i = float(stop), "stop", j
                break
            if h[j] >= target:
                exit_price, reason, exit_i = float(target), "target", j
                break
            if c[j] < inval:
                exit_price, reason, exit_i = float(c[j]), "invalidation", j
                break
        else:
            if h[j] >= stop:
                exit_price, reason, exit_i = float(stop), "stop", j
                break
            if l[j] <= target:
                exit_price, reason, exit_i = float(target), "target", j
                break
            if c[j] > inval:
                exit_price, reason, exit_i = float(c[j]), "invalidation", j
                break

        # 保本移动（06 §5）：本根触发，**下一根**才生效，避免同根内循环定义
        if not breakeven_moved and policy.breakeven_at_R is not None:
            be = policy.breakeven_at_R * stop_dist
            hit = (h[j] >= entry + be) if direction == 1 else (l[j] <= entry - be)
            if hit:
                stop = max(stop, entry) if direction == 1 else min(stop, entry)
                breakeven_moved = True

    gross_R = direction * (exit_price - entry) / stop_dist
    fee_R = FEE_ROUNDTRIP * entry / stop_dist

    # 最大不利偏移（描述性统计，不作为任何决策输入）
    if direction == 1:
        mae_R = (entry - float(l[i : exit_i + 1].min())) / stop_dist
    else:
        mae_R = (float(h[i : exit_i + 1].max()) - entry) / stop_dist

    fund_R = 0.0
    if funding is not None and exit_i > i:
        fdates, frates = funding
        a = int(np.searchsorted(fdates, t_ns[i], side="right"))
        b = int(np.searchsorted(fdates, t_ns[exit_i], side="right"))
        fund_R = direction * (float(frates[a:b].sum()) if b > a else 0.0) * entry / stop_dist

    return {
        "gross_R": gross_R,
        "fee_R": fee_R,
        "funding_R": fund_R,
        "net_R": gross_R - fee_R - fund_R,
        "exit_reason": reason,
        "hold_bars": exit_i - i,
        "stop_dist_rel": stop_dist / entry,
        "target_dist_rel": plan["target_dist"] / entry,
        "rr": plan["rr"],
        "breakeven_moved": breakeven_moved,
        "mae_R": mae_R,
        "exit_idx": exit_i,
        "stop_price": plan["stop"],
        "target_price": plan["target"],
        "inval_price": plan["inval"],
    }


def run_structural(events: pd.DataFrame, tf: str, policy: StructuralExitPolicy) -> pd.DataFrame:
    """对事件表跑结构性出场。被预筛跳过的事件不会出现在输出里。"""
    out = []
    for row in events.to_dict("records"):
        arr = load_arrays(row["symbol"], tf)
        if arr is None:
            continue
        res = simulate_structural(row, arr, load_funding(row["symbol"]), policy)
        if res:
            out.append({**row, **res})
    return pd.DataFrame(out)
