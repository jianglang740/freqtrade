"""
03_AdvancedStrategy — 自定义回调策略

═══════════════════════════════════════════════════════
学习目标：掌握 IStrategy 的高级回调

演示了 5 个自定义回调：
1. confirm_trade_entry() — 入场前置检查（限制每天最多交易次数）
2. custom_exit() — 自定义退出（目标盈利 + 持仓超时）
3. custom_stoploss() — 动态止损（盈利越多止损越紧）
4. custom_entry_price() — 自定义入场价格
5. bot_start() / bot_loop_start() — 生命周期钩子

═══════════════════════════════════════════════════════
运行方式：
  freqtrade backtesting -s 03_AdvancedStrategy -c user_data/config/config_all.json --timerange 20260701-20260806
  freqtrade trade -s 03_AdvancedStrategy -c user_data/config/config_all.json  (模拟盘)

关键区别：confirm_trade_entry 只在实盘/模拟盘调用，回测也会调
═══════════════════════════════════════════════════════
"""

from datetime import datetime, time
from typing import Optional

from freqtrade.persistence import Trade
from freqtrade.strategy import IStrategy
from pandas import DataFrame
import talib.abstract as ta


class AdvancedStrategy(IStrategy):

    # ── 基础设置 ──
    timeframe = "5m"
    can_short = False

    # ── 止盈止损 ──
    minimal_roi = {"0": 0.99}    # 不用固定ROI，交给 custom_exit 处理
    stoploss = -0.15              # 硬止损 -15%（custom_stoploss 不能比这更宽）
    use_exit_signal = True

    # ── 启用自定义止损 ──
    use_custom_stoploss = True

    # ── 策略参数 ──
    atr_period = 14
    rsi_period = 14

    # ═══════════════════════════════════════
    # 生命周期：启动时一次
    # ═══════════════════════════════════════
    def bot_start(self, **kwargs) -> None:
        """
        在机器人启动时调用一次。
        可以在这里初始化外部连接、加载缓存数据等。
        """
        import logging
        logger = logging.getLogger(__name__)
        logger.info("AdvancedStrategy 已启动！")

    def bot_loop_start(self, current_time: datetime, **kwargs) -> None:
        """
        每个主循环迭代开始时调用。
        可以在这里拉取外部数据（新闻情绪、链上数据等）。
        """
        pass

    # ═══════════════════════════════════════
    # 指标计算
    # ═══════════════════════════════════════
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=self.rsi_period)
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=self.atr_period)
        dataframe["ema20"] = ta.EMA(dataframe, timeperiod=20)
        dataframe["ema50"] = ta.EMA(dataframe, timeperiod=50)
        return dataframe

    # ═══════════════════════════════════════
    # 入场信号
    # ═══════════════════════════════════════
    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["enter_long"] = 0

        trend_up = dataframe["ema20"] > dataframe["ema50"]
        rsi_ok = (dataframe["rsi"] > 25) & (dataframe["rsi"] < 45)
        volume_ok = dataframe["volume"] > 0

        dataframe.loc[trend_up & rsi_ok & volume_ok, ["enter_long", "enter_tag"]] = (
            1, "trend_rsi_entry"
        )

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["exit_long"] = 0
        return dataframe

    # ═══════════════════════════════════════
    # 回调 1：入场前置检查
    # ═══════════════════════════════════════
    def confirm_trade_entry(
        self,
        pair: str,
        order_type: str,
        amount: float,
        rate: float,
        time_in_force: str,
        current_time: datetime,
        entry_tag: Optional[str],
        side: str,
        **kwargs,
    ) -> bool:
        """
        在订单即将发出前调用。
        返回 False 可阻止此笔交易。

        这里演示：限制每天最多 3 笔交易。
        """

        # 统计今天已开仓数
        today = current_time.date()
        open_trades = Trade.get_open_trades()
        today_trades = [t for t in open_trades if t.open_date_utc.date() == today]

        if len(today_trades) >= 3:
            return False  # 今天已经开了 3 笔，拒绝新开仓

        return True

    # ═══════════════════════════════════════
    # 回调 2：自定义退出
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
        优先级高于 populate_exit_trend 的信号。

        返回 None → 不退出
        返回 "target_profit" → 触发退出（exit_reason = "target_profit"）
        """

        # 条件 1：盈利达到 3%，退出
        if current_profit > 0.03:
            return "target_profit"

        # 条件 2：持仓超过 8 小时且亏损超过 3%
        hold_hours = (current_time - trade.open_date_utc).total_seconds() / 3600
        if hold_hours > 8 and current_profit < -0.03:
            return "timeout_loss"

        return None

    # ═══════════════════════════════════════
    # 回调 3：动态止损
    # ═══════════════════════════════════════
    def custom_stoploss(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        after_fill: bool,
        **kwargs,
    ) -> Optional[float]:
        """
        盈利越多，止损越紧。

        返回的止损值不能比 self.stoploss (-0.15) 更宽松。

        示例：
        - 亏损阶段  → 止损 -15%（等于 stoploss）
        - 盈利 > 3%  → 止损收紧到 -1%（保本）
        - 盈利 > 5%  → 止损收紧到 +1%（锁定收益）
        - 盈利 > 10% → 止损收紧到 +5%
        """

        if current_profit > 0.10:
            return -0.05  # 盈利 > 10%：止损设在当前价 -5%
        if current_profit > 0.05:
            return -0.01  # 盈利 > 5%：止损 -1%
        if current_profit > 0.03:
            return -0.03  # 盈利 > 3%：止损 -3%

        return self.stoploss  # 默认值

    # ═══════════════════════════════════════
    # 回调 4：自定义入场价格
    # ═══════════════════════════════════════
    def custom_entry_price(
        self,
        pair: str,
        trade: Optional[Trade],
        current_time: datetime,
        proposed_rate: float,
        entry_tag: Optional[str],
        side: str,
        **kwargs,
    ) -> float:
        """
        可以在框架给出的价格基础上做微调。
        这里不做调整，原样返回。
        """
        return proposed_rate
