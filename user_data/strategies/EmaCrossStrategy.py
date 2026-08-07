"""
EMACrossStrategy - 双均线交叉 + RSI 过滤策略

买入条件：快线上穿慢线，且 RSI 不超买
卖出条件：快线下穿慢线，或 RSI 超买回落
"""

from freqtrade.strategy import IStrategy
from pandas import DataFrame
import talib.abstract as ta


class EmaCrossStrategy(IStrategy):

    # ── 基础设置 ──
    timeframe = "5m"
    can_short = True  # 允许做空

    # ── 止盈止损 ──
    minimal_roi = {
        "120": 0,       # 2小时后不限盈利
        "60": 0.01,     # 1小时盈利1%止盈
        "30": 0.02,     # 30分钟盈利2%止盈
        "0": 0.05,      # 立即盈利5%止盈
    }
    stoploss = -0.10    # 亏10%硬止损

    # ── 可调参数 ──
    # 快线周期
    fast_period = 12
    # 慢线周期
    slow_period = 26
    # RSI 周期
    rsi_period = 14
    # RSI 超卖线（低于此值考虑买入）
    rsi_oversold = 30
    # RSI 超买线（高于此值考虑卖出）
    rsi_overbought = 70

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        计算技术指标。所有指标作为新列添加到 dataframe 上。
        """

        # 双 EMA
        dataframe["ema_fast"] = ta.EMA(dataframe, timeperiod=self.fast_period)
        dataframe["ema_slow"] = ta.EMA(dataframe, timeperiod=self.slow_period)

        # RSI
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=self.rsi_period)

        # ATR（用于动态止损，这里只计算备用）
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=14)

        # 成交量均线
        dataframe["volume_sma"] = dataframe["volume"].rolling(window=20).mean()

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        买入信号。
        dataframe['enter_long'] = 1 表示买入。
        """
        dataframe["enter_long"] = 0

        # 条件1: 快线上穿慢线（金叉）
        golden_cross = (
            (dataframe["ema_fast"] > dataframe["ema_slow"])
            & (dataframe["ema_fast"].shift(1) <= dataframe["ema_slow"].shift(1))
        )

        # 条件2: RSI 不在超买区
        rsi_ok = dataframe["rsi"] < self.rsi_overbought

        # 条件3: 有成交量（避免死市）
        volume_ok = dataframe["volume"] > 0

        dataframe.loc[golden_cross & rsi_ok & volume_ok, "enter_long"] = 1

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        卖出信号。
        dataframe['exit_long'] = 1 表示卖出。
        """
        dataframe["exit_long"] = 0

        # 条件1: 快线下穿慢线（死叉）
        dead_cross = (
            (dataframe["ema_fast"] < dataframe["ema_slow"])
            & (dataframe["ema_fast"].shift(1) >= dataframe["ema_slow"].shift(1))
        )

        # 条件2: RSI 超买后回落
        rsi_overbought_exit = (
            (dataframe["rsi"] > self.rsi_overbought)
            & (dataframe["rsi"].shift(1) > self.rsi_overbought)
        )

        dataframe.loc[dead_cross | rsi_overbought_exit, "exit_long"] = 1

        return dataframe
