"""括号交易模拟（研究与回测共用的执行模型）。

成本假设见 19_cost_data_and_execution.md §2、§3。
入场时点遵循 freqtrade 回测语义：信号在 bar t 收盘产生，成交在 bar t+1 开盘。
（对应 freqtrade/optimize/backtesting.py:554-567 的 HEADERS 位移）
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA = REPO_ROOT / "user_data/data/binance/futures"

# taker 单边 0.05% → 往返 0.10%（19 §2）
FEE_ROUNDTRIP = 0.0010

_PRICE_CACHE: dict[str, pd.DataFrame] = {}
_ARR_CACHE: dict[str, dict | None] = {}
_FUND_CACHE: dict[str, tuple[np.ndarray, np.ndarray]] = {}


@dataclass(frozen=True)
class Bracket:
    stop_mult: float = 1.0
    target_mult: float = 2.0
    horizon: int = 12

    def tag(self) -> str:
        return f"s{self.stop_mult}_t{self.target_mult}_h{self.horizon}"


def load_price(sym: str, tf: str) -> pd.DataFrame | None:
    key = f"{sym}|{tf}"
    if key not in _PRICE_CACHE:
        fp = DATA / f"{sym}-{tf}-futures.feather"
        _PRICE_CACHE[key] = pd.read_feather(fp) if fp.exists() else None
    return _PRICE_CACHE[key]


def to_arrays(df: pd.DataFrame) -> dict:
    """OHLCV → 并行 numpy 数组。**每个品种只做一次**。

    原先把这一步放在 simulate 里按事件重复做：31.5k 事件 × 每次 5 列 × 8000 行，
    等于十亿级元素的重复拷贝，是整条链路最大的热点。
    """
    return {
        "o": df["open"].to_numpy(dtype=float),
        "h": df["high"].to_numpy(dtype=float),
        "l": df["low"].to_numpy(dtype=float),
        "c": df["close"].to_numpy(dtype=float),
        "t": df["date"].astype("int64").to_numpy(),
        "n": len(df),
    }


def load_arrays(sym: str, tf: str) -> dict | None:
    key = f"{sym}|{tf}"
    if key not in _ARR_CACHE:
        df = load_price(sym, tf)
        _ARR_CACHE[key] = to_arrays(df) if df is not None else None
    return _ARR_CACHE[key]


def load_funding(sym: str) -> tuple[np.ndarray, np.ndarray] | None:
    """返回 (时间纳秒 int64, 费率) 两个并行数组，按时间升序（供二分查找）。

    原先每次模拟都对整张资金费表做布尔切片，是 O(事件数 × 费率表长) 的热点。
    """
    if sym not in _FUND_CACHE:
        fp = DATA / f"{sym}-1h-funding_rate.feather"
        if not fp.exists():
            _FUND_CACHE[sym] = None  # type: ignore[assignment]
            return None
        df = pd.read_feather(fp).sort_values("date")
        dates = df["date"].astype("int64").to_numpy()
        _FUND_CACHE[sym] = (dates, df["open"].to_numpy(dtype=float))
    return _FUND_CACHE[sym]


def simulate(row: dict, prices, funding, br: Bracket) -> dict:
    """模拟单笔括号交易。

    entry_idx 为成交根（= 信号根 + 1）。止损/目标以波动尺度 vol 表示。
    同一根同时触及止损与目标时，**保守地取止损**。
    prices 可为 `to_arrays` 的字典，或 OHLCV DataFrame（为绘图等临时调用保留）。
    """
    arr = prices if isinstance(prices, dict) else to_arrays(prices)
    i = int(row["entry_idx"])
    direction = int(row["direction"])
    vol = float(row["pa_vol_scale"])
    n = arr["n"]
    if not np.isfinite(vol) or vol <= 0 or i >= n:
        return {}

    o, h, l, c, t_ns = arr["o"], arr["h"], arr["l"], arr["c"], arr["t"]

    entry = o[i]
    stop_dist = br.stop_mult * vol
    if direction == 1:
        stop, target = entry - stop_dist, entry + br.target_mult * vol
    else:
        stop, target = entry + stop_dist, entry - br.target_mult * vol

    end = min(i + br.horizon, n - 1)
    exit_price, reason, exit_i = c[end], "horizon", end
    for j in range(i, end + 1):
        if direction == 1:
            if l[j] <= stop:
                exit_price, reason, exit_i = stop, "stop", j
                break
            if h[j] >= target:
                exit_price, reason, exit_i = target, "target", j
                break
        else:
            if h[j] >= stop:
                exit_price, reason, exit_i = stop, "stop", j
                break
            if l[j] <= target:
                exit_price, reason, exit_i = target, "target", j
                break

    gross_R = direction * (exit_price - entry) / stop_dist
    fee_R = FEE_ROUNDTRIP * entry / stop_dist

    # 资金费（19 §3）：多头在正费率时支付。累计 (t0, t1] 区间内的实际费率。
    fund_R = 0.0
    if funding is not None and exit_i > i:
        fdates, frates = funding
        a = int(np.searchsorted(fdates, t_ns[i], side="right"))     # 严格大于 t0
        b = int(np.searchsorted(fdates, t_ns[exit_i], side="right"))  # ≤ t1
        rate_sum = float(frates[a:b].sum()) if b > a else 0.0
        fund_R = direction * rate_sum * entry / stop_dist

    return {
        "gross_R": gross_R,
        "fee_R": fee_R,
        "funding_R": fund_R,
        "net_R": gross_R - fee_R - fund_R,
        "exit_reason": reason,
        "hold_bars": exit_i - i,
        "stop_dist_rel": stop_dist / entry,
    }


def run_backtest(events: pd.DataFrame, tf: str, br: Bracket) -> pd.DataFrame:
    out = []
    for row in events.to_dict("records"):
        arr = load_arrays(row["symbol"], tf)
        if arr is None:
            continue
        res = simulate(row, arr, load_funding(row["symbol"]), br)
        if res:
            out.append({**row, **res})
    return pd.DataFrame(out)
