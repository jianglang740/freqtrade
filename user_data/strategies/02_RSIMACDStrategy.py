"""
02_RSIMACDStrategy — 多指标组合策略

═══════════════════════════════════════════════════════
学习目标：掌握多指标组合 + enter_tag 分类 + 超参预留
═══════════════════════════════════════════════════════

比 01 更进一步：
- 用 RSI + MACD + 成交量 三个指标组合判断
- 用 enter_tag 标记不同买入原因，回测报告可分类统计
- 参数用 IntParameter 定义，为后续超参优化做准备

═══════════════════════════════════════════════════════
运行方式：
  freqtrade backtesting -s 02_RSIMACDStrategy -c user_data/config/config_all.json --timerange 20260701-20260806

依赖数据：5m K线
═══════════════════════════════════════════════════════
"""

from freqtrade.strategy import IStrategy, IntParameter
from pandas import DataFrame
import talib.abstract as ta


class RSIMACDStrategy(IStrategy):

    # ── 基础设置 ──
    timeframe = "5m"
    can_short = False
    startup_candle_count = 50  # EMA(50) 需要 50 根历史K线

    # ── 止盈止损 ──
    minimal_roi = {"120": 0, "60": 0.01, "30": 0.02, "0": 0.05}
    stoploss = -0.10

    # ── 超参优化预留参数（目前用默认值，后续可用 hyperopt 搜索最优值）──
    rsi_period = IntParameter(7, 21, default=14, space="buy")
    rsi_buy_threshold = IntParameter(20, 40, default=30, space="buy")
    rsi_sell_threshold = IntParameter(60, 80, default=70, space="sell")
    macd_fast = IntParameter(8, 16, default=12, space="buy")
    macd_slow = IntParameter(20, 30, default=26, space="buy")
    macd_signal = IntParameter(5, 12, default=9, space="buy")

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        计算三个指标体系：RSI、MACD、成交量。
        """

        # ═══ RSI ═══
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=self.rsi_period.value)

        # ═══ MACD ═══
        macd = ta.MACD(
            dataframe,
            fastperiod=self.macd_fast.value,
            slowperiod=self.macd_slow.value,
            signalperiod=self.macd_signal.value,
        )
        dataframe["macd"] = macd["macd"]
        dataframe["macdsignal"] = macd["macdsignal"]
        dataframe["macdhist"] = macd["macdhist"]  # MACD 柱（正值=多头，负值=空头）

        # ═══ 成交量均线 ═══
        dataframe["volume_sma"] = dataframe["volume"].rolling(window=20).mean()

        # ═══ 趋势 EMA（辅助判断方向）═══
        dataframe["ema50"] = ta.EMA(dataframe, timeperiod=50)

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        三种入场场景，每种有不同的 enter_tag：
        """

        dataframe["enter_long"] = 0

        # ── 场景 1：RSI 超卖反弹 ──
        rsi_oversold = (
            (dataframe["rsi"] < self.rsi_buy_threshold.value)
            & (dataframe["volume"] > dataframe["volume_sma"])  # 放量
        )

        # ── 场景 2：MACD 金叉 ──
        macd_golden_cross = (
            (dataframe["macd"] > dataframe["macdsignal"])
            & (dataframe["macd"].shift(1) <= dataframe["macdsignal"].shift(1))
        )

        # ── 场景 3：MACD 柱转正 + RSI 不超买 ──
        macd_hist_positive = (
            (dataframe["macdhist"] > 0)
            & (dataframe["macdhist"].shift(1) <= 0)
            & (dataframe["rsi"] < 60)
        )

        # ── 分别标记不同的 enter_tag ──
        dataframe.loc[rsi_oversold, ["enter_long", "enter_tag"]] = (1, "rsi_oversold")
        dataframe.loc[macd_golden_cross, ["enter_long", "enter_tag"]] = (1, "macd_golden_cross")
        dataframe.loc[macd_hist_positive, ["enter_long", "enter_tag"]] = (1, "macd_hist_positive")

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        两种出场场景：
        """

        dataframe["exit_long"] = 0

        # ── RSI 超买 ──
        rsi_overbought = dataframe["rsi"] > self.rsi_sell_threshold.value

        # ── MACD 死叉 ──
        macd_dead_cross = (
            (dataframe["macd"] < dataframe["macdsignal"])
            & (dataframe["macd"].shift(1) >= dataframe["macdsignal"].shift(1))
        )

        dataframe.loc[rsi_overbought | macd_dead_cross, "exit_long"] = 1

        return dataframe
