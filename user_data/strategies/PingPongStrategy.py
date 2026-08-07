"""
PingPongStrategy — 全流程验收策略

每根K线交替买卖，用于验证：
- OKX 交易所连接（dry_run 模拟）
- 下单 / 平仓流程
- FreqUI 实时监控
"""

from freqtrade.strategy import IStrategy
from pandas import DataFrame


class PingPongStrategy(IStrategy):

    timeframe = "1m"
    can_short = False

    # 止损止盈给宽松点，让 ping-pong 自由发挥
    minimal_roi = {"0": 0.99}
    stoploss = -0.50

    # 只做单笔，避免混乱
    max_open_trades = 1

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # 用分钟数取模实现交替，不依赖 dataframe 长度
        dataframe["toggle"] = dataframe["date"].dt.minute % 2
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        偶数行买入。
        """
        dataframe["enter_long"] = 0
        dataframe.loc[dataframe["toggle"] == 0, "enter_long"] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        奇数行卖出。
        """
        dataframe["exit_long"] = 0
        dataframe.loc[dataframe["toggle"] == 1, "exit_long"] = 1
        return dataframe
