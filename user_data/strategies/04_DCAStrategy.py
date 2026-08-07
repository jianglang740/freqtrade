"""
04_DCAStrategy — DCA 马丁策略

═══════════════════════════════════════════════════════
学习目标：掌握 position_adjustment_enable + adjust_trade_position
═══════════════════════════════════════════════════════

核心概念：
- 开首仓后，如果价格下跌，在预设的跌幅位置补仓
- 补仓金额逐级放大（马丁倍率），拉低持仓均价
- 当价格反弹到持仓均价盈利 target_profit 时，全部退出

不同于 01-03 的"一次买入一次卖出"，这个策略会多次买入。

═══════════════════════════════════════════════════════
运行方式：
  freqtrade backtesting -s 04_DCAStrategy -c user_data/config/config_all.json --timerange 20260701-20260806

注意：DCA 策略回测比较慢（每根K线都要检查加仓条件）
依赖数据：5m K线
═══════════════════════════════════════════════════════
"""

from datetime import datetime
from typing import Optional

from freqtrade.persistence import Trade
from freqtrade.strategy import IStrategy
from pandas import DataFrame
import talib.abstract as ta


class DCAStrategy(IStrategy):

    timeframe = "5m"
    can_short = False

    # ═══ 启用仓位调整 ═══
    position_adjustment_enable = True
    max_entry_position_adjustment = 4   # 首仓 + 最多 4 次加仓 = 共 5 次入场

    max_open_trades = 2   # 控制同时持仓币种数

    # ── 止损止盈 ──
    minimal_roi = {"0": 0.99}  # 不用固定ROI，由 custom_exit 接管
    stoploss = -0.30            # 硬止损 -30%（给 DCA 留空间）

    # ── DCA 参数 ──
    # 加仓触发：每跌 step_down% 补一次
    step_down = 0.03  # 3%

    # 马丁倍率：首仓 1x → 第1次加仓 1.5x → 第2次 2.5x → 第3次 4x → 第4次 6x
    martingale_mults = [1.0, 1.5, 2.5, 4.0, 6.0]

    # 整体盈利退出目标
    target_profit = 0.02  # 均价盈利 2% 清仓

    # RSI
    rsi_period = 14

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=self.rsi_period)
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=14)
        dataframe["ema50"] = ta.EMA(dataframe, timeperiod=50)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        首仓入场：RSI < 35 且价格在 EMA50 上方（处于上升趋势的回调中）
        """
        dataframe["enter_long"] = 0

        pullback_entry = (
            (dataframe["rsi"] < 35)
            & (dataframe["close"] > dataframe["ema50"])
            & (dataframe["volume"] > 0)
        )

        dataframe.loc[pullback_entry, ["enter_long", "enter_tag"]] = (
            1, "dca_first_entry"
        )

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["exit_long"] = 0
        return dataframe

    # ═══════════════════════════════════════
    # 核心：DCA 加仓逻辑
    # ═══════════════════════════════════════
    def adjust_trade_position(
        self,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        min_stake: float,
        max_stake: float,
        current_entry_rate: float,
        current_exit_rate: float,
        current_entry_profit: float,
        current_exit_profit: float,
        **kwargs,
    ) -> Optional[float]:
        """
        每次主循环迭代都会调用此方法（仅对有持仓的 trade）。

        逻辑：
        1. 已加仓次数达到上限 → 不操作
        2. 价格跌到下一个加仓位 → 补仓
        3. 否则 → 不操作

        current_profit 是负数（未实现亏损）时才考虑加仓。
        """

        filled_entries = trade.nr_of_successful_entries

        # 加仓次数用尽
        if filled_entries >= len(self.martingale_mults):
            return None

        # 距首仓跌幅不够深，不加仓
        # 首仓不亏=current_profit≈0，第1次加仓需要亏损 > step_down
        required_drop = self.step_down * filled_entries
        if current_profit > -required_drop:
            return None

        # RSI 极端（< 20）→ 极弱市场，暂不加仓，防止无限接飞刀
        # 通过 self.dp 获取当前数据
        try:
            dataframe, _ = self.dp.get_analyzed_dataframe(trade.pair, self.timeframe)
            last_rsi = dataframe["rsi"].iloc[-1]
            if last_rsi < 20:
                return None
        except Exception:
            pass

        # 按马丁倍率计算加仓金额
        mult = self.martingale_mults[filled_entries]
        additional_stake = trade.stake_amount * mult

        # 确保不小于交易所最小下单额
        if min_stake and additional_stake < min_stake:
            additional_stake = min_stake
        # 确保不超过可用余额
        if additional_stake > max_stake:
            additional_stake = max_stake

        return additional_stake

    # ═══════════════════════════════════════
    # DCA 整体退出
    # ═══════════════════════════════════════
    def custom_exit(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        **kwargs,
    ) -> Optional[str]:
        """
        持仓均价盈利达标 → 全部清仓。
        current_profit 本来就是基于持仓均价计算的。
        """
        if current_profit > self.target_profit:
            return "dca_target"

        return None
