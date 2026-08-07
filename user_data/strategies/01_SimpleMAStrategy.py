"""
01_SimpleMAStrategy — 双均线交叉（入门级）

═══════════════════════════════════════════════════════
学习目标：理解 freqtrade 策略的最基本结构
═══════════════════════════════════════════════════════

这是最简单的策略示例，只用了两根 EMA 均线：
- fast EMA 上穿 slow EMA → 买入（金叉）
- fast EMA 下穿 slow EMA → 卖出（死叉）

只覆盖 populate_indicators / populate_entry_trend / populate_exit_trend 三个核心方法。

═══════════════════════════════════════════════════════
运行方式：
  freqtrade backtesting -s 01_SimpleMAStrategy -c user_data/config/config_all.json --timerange 20260701-20260806
  freqtrade plot-dataframe -s 01_SimpleMAStrategy -p BTC/USDT:USDT -c user_data/config/config_all.json

依赖数据：5m K线，合约格式（如 BTC/USDT:USDT）
═══════════════════════════════════════════════════════
"""

from freqtrade.strategy import IStrategy
from pandas import DataFrame
import talib.abstract as ta


class SimpleMAStrategy(IStrategy):
    """
    双均线交叉策略 — 最简单的趋势跟踪策略。

    原理：
    - 短期均线上穿长期均线 → 买入
    - 短期均线下穿长期均线 → 卖出

    胜率不高但逻辑极其简单，适合作为第一个学习的策略。
    """

    # ── 基础设置 ──
    timeframe = "5m"
    can_short = False

    # ── 止盈止损 ──
    minimal_roi = {
        "120": 0,       # 2小时后不限盈利
        "60": 0.01,     # 1小时盈利1%止盈
        "30": 0.02,     # 30分钟盈利2%止盈
        "0": 0.05,      # 立即盈利5%止盈
    }
    stoploss = -0.10    # 亏10%止损

    # ── 只有 2 个可调参数 ──
    fast_ma = 12        # 快线周期
    slow_ma = 26        # 慢线周期

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        步骤 1：计算指标。

        输入 dataframe 有这些列：
          date, open, high, low, close, volume

        我们往上面加两列：
          ema_fast — 快线
          ema_slow — 慢线
        """

        # TA-Lib 的 EMA 函数：传入 dataframe 和周期即可
        dataframe["ema_fast"] = ta.EMA(dataframe, timeperiod=self.fast_ma)
        dataframe["ema_slow"] = ta.EMA(dataframe, timeperiod=self.slow_ma)

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        步骤 2：标记买入信号。

        必须设置的列：
          enter_long = 1  表示做多买入
          enter_tag  = "信号名称"  用于统计分类（可选但推荐）
        """

        dataframe["enter_long"] = 0

        # 金叉：快线上穿慢线
        # 判断方法：当前快线 > 慢线，但上一根K线快线 <= 慢线
        golden_cross = (
            (dataframe["ema_fast"] > dataframe["ema_slow"])
            & (dataframe["ema_fast"].shift(1) <= dataframe["ema_slow"].shift(1))
        )

        # 有成交量（排除停牌或死市）
        has_volume = dataframe["volume"] > 0

        dataframe.loc[golden_cross & has_volume, ["enter_long", "enter_tag"]] = (
            1,
            "golden_cross",
        )

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        步骤 3：标记卖出信号。

        必须设置的列：
          exit_long = 1  表示平掉多头仓位
        """

        dataframe["exit_long"] = 0

        # 死叉：快线下穿慢线
        dead_cross = (
            (dataframe["ema_fast"] < dataframe["ema_slow"])
            & (dataframe["ema_fast"].shift(1) >= dataframe["ema_slow"].shift(1))
        )

        dataframe.loc[dead_cross, "exit_long"] = 1

        return dataframe
