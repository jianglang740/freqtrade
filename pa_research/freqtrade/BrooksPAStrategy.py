"""
BrooksPAStrategy — 价格行为（Brooks）事件策略，freqtrade 实现

═══════════════════════════════════════════════════════════════════════
v2：出场改为**结构性**（cards/exit_and_risk_v1.yaml，先于代码冻结）
═══════════════════════════════════════════════════════════════════════
v1（波动率括号：止损 1×vol / 目标 2×vol / 12 根）违反 06 §3 的明文
"止损应尽量与交易假设失效相关，而不是仅使用固定百分比"。
本版按 06 §3/§4 重做：

  止损   失效点 + tol×vol（突破→参照边界；回调→回调腿极值），tol=0.25
  目标   最近的已确认结构位（多头=区间上沿，空头=区间下沿），
         缺失则退化为测量走势（区间高度投影）
  预筛   目标距离 ≥ 1R + 往返费用，否则不入场（06 §4 "目标太近"）
  失效   收盘回到结构内 → 市价出场（06 §3，接回 events.py 的状态机语义）
  保本   浮盈达 1R 后止损移到入场价（06 §5，显式声明为"开"）
  到期   24 根兜底

前提声明：pa_research 的统计闸门**未通过**（见 pa_research/结论报告.md）。
这是**探索性**的 BTC 单币回测，用途是复核研究管线，不是策略推荐。
参数全部取自事前登记的卡片，**未做任何挑选**。

═══ 与研究的已知差异（影响逐笔对照，不影响方向）═══
1. max_open_trades=1 → 同一时刻只持一仓，持仓期间的新信号被跳过；
   研究里每个事件独立模拟（允许重叠）。被跳过的信号在研究里**不必然更差**
   （本轮被跳过的 236 个均值 +0.2250，反而好于配上的 −0.0804），
   故逐笔均值差有一部分只是选样，不代表实现分歧。
2. **引擎限制（本版最大残差）**：backtesting.py 的 _check_trade_exit 把
   `row[OPEN_IDX]`（该根开盘价）作为 current_rate 传给 custom_exit，而
   custom_exit **拿不到该根的 high/low**。故"盘中触及目标"要到下一根开盘、
   且开盘价已越过目标时才被识别 → 会漏掉一部分盘中达标、随后回吐甚至反手
   打止损的赢单（本版 13 笔：研究均值 +2.99R vs freqtrade −1.09R）。
   研究管线按 low/high 逐根扫描，是精确实现。
   止损不受此影响：走 freqtrade 自身机制，按盘中 low/high 判定。
3. 失效出场用的是**上一根已收盘**的收盘价（asof 查表），与研究的
   "该根收盘即判定"在时序上等价。
4. 成交价与研究的 entry 开盘价一致（实测中位相对差 0.00e+00），
   止损距离也逐笔一致（476/477 完全相等）。旧版曾把信号**重复**下移一根，
   使成交比研究晚整整一根 —— 本版已修（信号根发出、freqtrade 下一根成交）。
"""

from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from pandas import DataFrame

from freqtrade.strategy import IStrategy

# 领域模块与 freqtrade 解耦（09 §8）：策略只调用，不复制算法
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT / "pa_research") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "pa_research"))

from modules.events import detect_breakouts  # noqa: E402
from modules.exits import StructuralExitPolicy, plan_exit  # noqa: E402
from modules.pipeline import build_context  # noqa: E402
from modules.swings import find_swings  # noqa: E402

#: 突破边界用的摆动窗口（与 01_build_events.py 的 --boundary-window 默认一致）。
BOUNDARY_WINDOW = 5


class BrooksPAStrategy(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = "4h"
    can_short = True
    process_only_new_candles = True
    startup_candle_count = 400

    # ── 事前登记的执行参数（不得事后调整）──
    H1H2H3 = (1.0, 2.0, 3.0)
    POLICY = StructuralExitPolicy()          # 卡片 exit_and_risk_v1.yaml
    HORIZON_BARS = POLICY.horizon

    stoploss = -0.99                          # 真实止损由 custom_stoploss 给出
    use_custom_stoploss = True
    minimal_roi = {"0": 100}                  # 目标由 custom_exit 给出
    trailing_stop = False                     # 06 §5：声明为不做追踪止损

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self._plan: dict[str, pd.DataFrame] = {}

    # ═══════════════════════════════════════════════════════════
    # 指标：上下文层 + 事件层 + 结构性出场计划
    # ═══════════════════════════════════════════════════════════
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        pair = metadata["pair"]
        work = dataframe.reset_index(drop=True)
        n = len(work)

        ctx, _struct, _sh, _sl = build_context(work)
        state = ctx["pa_state"].to_numpy()
        vol = ctx["pa_vol_scale"].to_numpy()

        # 信号根 j 上的失效位与方向。**不做 shift**：freqtrade 收到入场信号后
        # 自己会在下一根开盘成交，与 01_build_events.emit 的 entry_idx = idx+1 一致。
        # （上一版在策略里又 shift 了一次 → 双重延后，成交比研究晚一整根。）
        inval = np.full(n, np.nan)
        dirs = np.zeros(n, dtype=int)

        # 突破事件：失效位 = 参照边界
        sh_b, sl_b = find_swings(work, left=BOUNDARY_WINDOW, right=BOUNDARY_WINDOW)
        for r in detect_breakouts(work, sh_b, sl_b, state, vol).itertuples(index=False):
            i = int(r.candidate_idx)
            inval[i] = float(r.reference_level)
            dirs[i] = int(r.direction)

        # 回调事件：失效位 = 回调腿极值。同一根若同时有突破与回调信号，
        # 回调的定义更具体（05 §4–5），故覆盖之。
        h_count = ctx["pa_h_count"].to_numpy()
        l_count = ctx["pa_l_count"].to_numpy()
        anchor = ctx["pa_pullback_anchor"].to_numpy()
        for i in np.flatnonzero(np.isin(h_count, self.H1H2H3)):
            inval[i], dirs[i] = anchor[i], 1
        for i in np.flatnonzero(np.isin(l_count, self.H1H2H3)):
            inval[i], dirs[i] = anchor[i], -1

        # 出场计划挂在**成交根** f = 信号根 j + 1：入场价取 op[f]，
        # 而区间上下沿 / 波动率 / 失效位一律取自**信号根** j
        # ——与 01_build_events 用 ctx.iloc[idx] 取特征的做法一致。
        ru = ctx["pa_range_upper"].to_numpy()
        rl = ctx["pa_range_lower"].to_numpy()
        op = work["open"].to_numpy()
        stop_px = np.full(n, np.nan)
        tgt_px = np.full(n, np.nan)
        inv_px = np.full(n, np.nan)
        ok = np.zeros(n, dtype=bool)
        for j in np.flatnonzero(dirs != 0):
            f = j + 1
            if f >= n:
                continue
            p = plan_exit(
                "breakout_up" if dirs[j] == 1 else "breakout_down", int(dirs[j]),
                op[f], inval[j], np.nan, ru[j], rl[j], vol[j], self.POLICY,
            )
            if p is not None:
                stop_px[f], tgt_px[f], inv_px[f], ok[f] = (
                    p["stop"], p["target"], p["inval"], True)

        self._plan[pair] = pd.DataFrame(
            {"close": work["close"].to_numpy(), "stop": stop_px,
             "target": tgt_px, "inval": inv_px, "ok": ok},
            # freqtrade 的 feather 数据是 datetime64[ms]，索引会保持毫秒精度；
            # 而 asof 的键是纳秒，毫秒索引 + 纳秒键会抛 "Cannot losslessly
            # convert units"（被 strategy_safe_wrapper 静默吞掉 → custom_exit
            # 整段失效）。统一提到纳秒，任何精度的键都能无损比较。
            index=pd.DatetimeIndex(work["date"]).as_unit("ns"),
        )

        # 入场信号在**信号根** j 发出（freqtrade 收到后于 j+1 开盘成交）。
        # ok 挂在成交根，故回退一根才对应到发出信号的根。
        enter_ok = np.zeros(n, dtype=bool)
        enter_ok[:-1] = ok[1:]
        dataframe["pa_entry_ok"] = enter_ok & (dirs != 0)
        dataframe["pa_is_long"] = enter_ok & (dirs == 1)
        dataframe["pa_is_short"] = enter_ok & (dirs == -1)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[dataframe["pa_is_long"], ["enter_long", "enter_tag"]] = (1, "pa_long")
        dataframe.loc[dataframe["pa_is_short"], ["enter_short", "enter_tag"]] = (1, "pa_short")
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        return dataframe      # 出场由 custom_stoploss / custom_exit 接管

    # ═══════════════════════════════════════════════════════════
    # 执行
    # ═══════════════════════════════════════════════════════════
    def leverage(self, pair, current_time, current_rate, proposed_leverage,
                 max_leverage, entry_tag, side, **kwargs) -> float:
        return 1.0            # R 是"每单位风险"口径，杠杆会污染它

    def _row_at(self, pair: str, when) -> pd.Series | None:
        plan = self._plan.get(pair)
        if plan is None or plan.empty:
            return None
        try:
            return plan.asof(when)
        except Exception:     # noqa: BLE001
            return None

    def custom_stoploss(self, pair: str, trade, current_time, current_rate,
                        current_profit: float, after_fill: bool, **kwargs) -> float | None:
        """结构止损：突破→边界下方 tol×vol；回调→回调腿极值下方 tol×vol。

        走 freqtrade 自身的止损机制，按盘中 low/high 判定，故精度与研究一致。
        """
        row = self._row_at(pair, trade.open_date_utc)
        if row is None or not np.isfinite(row.get("stop", np.nan)):
            return None
        stop = float(row["stop"])
        if not np.isfinite(stop) or stop <= 0:
            return None
        return (stop - current_rate) / current_rate

    def custom_exit(self, pair: str, trade, current_time, current_rate,
                    current_profit: float, **kwargs) -> str | None:
        """目标 / 失效 / 到期。见文件头"已知差异 2"：目标在下一根开盘才被检测到。"""
        plan = self._plan.get(pair)
        row = self._row_at(pair, trade.open_date_utc)
        if plan is None or row is None:
            return None

        tgt = float(row.get("target", np.nan))
        inval = float(row.get("inval", np.nan))
        is_short = trade.is_short

        # 目标（在下一根开盘被检测到，故用当前价比较）
        if np.isfinite(tgt) and ((current_rate <= tgt) if is_short else (current_rate >= tgt)):
            return "target_structural"

        # 失效：用**上一根已收盘**的收盘价判定（该事实在收盘时即已知）
        key = pd.Timestamp(current_time).as_unit("ns") - pd.Timedelta(1, "ns")
        last_closed = plan["close"].asof(key)
        if np.isfinite(inval) and np.isfinite(last_closed):
            if (last_closed > inval) if is_short else (last_closed < inval):
                return "invalidation"

        # 到期兜底
        if pd.Timestamp(current_time) - pd.Timestamp(trade.open_date_utc) >= timedelta(
                hours=self.HORIZON_BARS * 4):
            return "time_horizon"
        return None
