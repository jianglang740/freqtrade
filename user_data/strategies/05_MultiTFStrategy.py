"""
05_MultiTFStrategy — 多时间框架策略

═══════════════════════════════════════════════════════
学习目标：掌握 @informative 装饰器的用法
═══════════════════════════════════════════════════════

核心概念：
- 主时间框架 5m 做具体的买卖信号
- 大时间框架 1h 判断主趋势方向
- 只在"小周期回撤、大周期上涨"时买入

@informative 装饰器自动：
1. 拉取指定时间框架的数据
2. 在你写的 populate_indicators_* 方法里计算大周期指标
3. 把大周期指标列合并到小周期 DataFrame（自动对齐时间、防 lookahead）

═══════════════════════════════════════════════════════
运行方式：
  freqtrade backtesting -s 05_MultiTFStrategy -c user_data/config/config_all.json --timerange 20260701-20260806

注意：需要 1h 数据（已下载的话会自动关联）
依赖数据：5m K线（主框架）+ 1h K线（@informative 自动加载）
═══════════════════════════════════════════════════════
"""

from freqtrade.strategy import IStrategy, informative
from pandas import DataFrame
import talib.abstract as ta


class MultiTFStrategy(IStrategy):

    timeframe = "5m"
    can_short = False

    # startup_candle_count 要足够覆盖 1h EMA20 的计算
    # 1h * 20 = 20小时 = 240 根 5m K线
    startup_candle_count = 300

    minimal_roi = {"120": 0, "60": 0.01, "0": 0.03}
    stoploss = -0.10

    # ═══════════════════════════════════════
    # @informative 装饰器 — 大周期指标
    # ═══════════════════════════════════════

    @informative("1h")
    def populate_indicators_1h(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        在 1h K线上计算大周期指标。
        这些指标会自动合并到 5m DataFrame 中，列名自动加上 _1h 后缀。

        ⚠️ 方法名可以是任意的，只要被 @informative 装饰过就会被自动调用。
        """
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)
        dataframe["ema20"] = ta.EMA(dataframe, timeperiod=20)
        dataframe["ema50"] = ta.EMA(dataframe, timeperiod=50)
        return dataframe

    # ═══════════════════════════════════════
    # 主时间框架指标
    # ═══════════════════════════════════════
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        在 5m K线上计算小周期指标。

        注意：此时 dataframe 中已经包含了 _1h 后缀的大周期列！
        大周期列已经被 @informative 装饰器自动合并进来了。
        """
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)
        dataframe["ema12"] = ta.EMA(dataframe, timeperiod=12)
        dataframe["ema26"] = ta.EMA(dataframe, timeperiod=26)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        只在两个条件同时满足时买入：
        1. 大趋势向上（1h EMA20 > EMA50）
        2. 小周期回调（5m RSI < 35）
        3. 小周期反弹确认（5m EMA12 刚上穿 EMA26）
        """

        dataframe["enter_long"] = 0

        # 大趋势向上（来自 @informative 指标，列名有 _1h 后缀）
        trend_up = dataframe["ema20_1h"] > dataframe["ema50_1h"]

        # 1h RSI 不超买
        htf_not_overbought = dataframe["rsi_1h"] < 65

        # 小周期回调到超卖区
        ltf_oversold = dataframe["rsi"] < 35

        # 小周期反弹
        ltf_cross_up = (
            (dataframe["ema12"] > dataframe["ema26"])
            & (dataframe["ema12"].shift(1) <= dataframe["ema26"].shift(1))
        )

        dataframe.loc[
            trend_up & htf_not_overbought & ltf_oversold & ltf_cross_up,
            ["enter_long", "enter_tag"],
        ] = (1, "multi_tf_entry")

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        出场信号：
        1. 大趋势转弱（1h EMA20 < EMA50）
        2. 或 小周期 RSI > 70
        """

        dataframe["exit_long"] = 0

        trend_down = dataframe["ema20_1h"] < dataframe["ema50_1h"]
        ltf_overbought = dataframe["rsi"] > 70

        dataframe.loc[trend_down | ltf_overbought, "exit_long"] = 1

        return dataframe
