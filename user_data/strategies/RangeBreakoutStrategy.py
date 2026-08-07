"""
RangeBreakoutStrategy — 1m 区间突破策略

═══════════════════════════════════════════════════════
基于 1 分钟 BTC/ETH 日内交易区间突破研究：
  - 用最近 N 根K线的最高点和最低点定义 support/resistance
  - 价格突破 resistance → 做多
  - 价格跌破 support → 做空
  - 持有时间 < 区间窗口的一半时回报最显著
  - 超时未止盈则强制退出

逻辑极其简单——本质是 Donchian Channel 加超时过滤器。
═══════════════════════════════════════════════════════
"""

from datetime import datetime
from typing import Optional

from freqtrade.persistence import Trade
from freqtrade.strategy import IStrategy
from pandas import DataFrame


class RangeBreakoutStrategy(IStrategy):

    # ═══════════════════════════════════════
    # 基础设置
    # ═══════════════════════════════════════
    timeframe = "1m"
    can_short = True
    startup_candle_count = 120  # 2 小时预热

    # ═══════════════════════════════════════
    # 仓位管理
    # ═══════════════════════════════════════
    max_open_trades = 2
    position_adjustment_enable = False
    use_exit_signal = True

    # ═══════════════════════════════════════
    # 止盈止损
    # ═══════════════════════════════════════
    minimal_roi = {"0": 0.03}        # 盈利 3%（10倍下价格 0.3%）
    stoploss = -0.03                 # 亏损 3%（10倍下价格 0.3%）止损

    # ═══════════════════════════════════════
    # 策略参数
    # ═══════════════════════════════════════

    # 区间窗口（K线数）：120 = 2 小时
    range_window = 120

    # 突破确认缓冲：0.5%，大幅过滤 1m 噪音
    breakout_buffer = 0.005

    # 最大持有时间：区间窗口的 150%
    # 给真突破更多时间跑
    max_hold_ratio = 1.5

    # ═══════════════════════════════════════
    # 指标
    # ═══════════════════════════════════════
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # Donchian Channel：过去 N 根K线的最高/最低
        dataframe["resistance"] = (
            dataframe["high"].shift(1).rolling(window=self.range_window, min_periods=1).max()
        )
        dataframe["support"] = (
            dataframe["low"].shift(1).rolling(window=self.range_window, min_periods=1).min()
        )

        # 区间宽度百分比
        dataframe["range_pct"] = (
            (dataframe["resistance"] - dataframe["support"]) / dataframe["support"] * 100
        )

        return dataframe

    # ═══════════════════════════════════════
    # 入场：突破
    # ═══════════════════════════════════════
    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["enter_long"] = 0
        dataframe["enter_short"] = 0

        # 区间不能太窄（< 0.3%）也不能太宽（> 5%）——太窄=盘整无突破、太宽=波动异常
        range_ok = (dataframe["range_pct"] > 0.3) & (dataframe["range_pct"] < 5.0)

        # 向上突破：收盘价超过阻力线 + 缓冲
        breakout_up = dataframe["close"] > dataframe["resistance"] * (1 + self.breakout_buffer)
        # 向下突破：收盘价跌破支撑线 - 缓冲
        breakout_down = dataframe["close"] < dataframe["support"] * (1 - self.breakout_buffer)

        dataframe.loc[breakout_up & range_ok, "enter_long"] = 1
        dataframe.loc[breakout_down & range_ok, "enter_short"] = 1

        return dataframe

    # ═══════════════════════════════════════
    # 离场：假突破立即退出
    # ═══════════════════════════════════════
    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["exit_long"] = 0
        dataframe["exit_short"] = 0

        # 做多离场：收盘回到阻力线以下 = 假突破，不等跌回支撑位
        exit_long_signal = dataframe["close"] < dataframe["resistance"]

        # 做空离场：收盘回到支撑线以上 = 假突破
        exit_short_signal = dataframe["close"] > dataframe["support"]

        dataframe.loc[exit_long_signal, "exit_long"] = 1
        dataframe.loc[exit_short_signal, "exit_short"] = 1

        return dataframe

    # ═══════════════════════════════════════
    # 超时退出：持有时间超过窗口一半 → 强制平仓
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
        研究发现持有时间 < 区间窗口一半时回报最显著。
        超出则强制退出，避免在无效区间内耗死。
        """
        hold_minutes = (current_time - trade.open_date_utc).total_seconds() / 60
        max_hold = self.range_window * self.max_hold_ratio

        if hold_minutes > max_hold and current_profit < 0.01:
            # 超时且未达 3% 盈利 → 不耗了，离场
            return "hold_timeout"

        return None
