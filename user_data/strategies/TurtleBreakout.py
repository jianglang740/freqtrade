"""
TurtleBreakout — BTC/ETH 4h 唐奇安通道突破（经典原版海龟，多空双向）

═══════════════════════════════════════════════════════
这个策略的目的**不是超越网格马丁，是当它的镜像对照**：

  网格马丁 = 逆势加仓 → 胜率高、单笔小赚，少数大亏吃光全部利润
  海龟突破 = 顺势加仓 → 胜率低、单笔小亏，少数大赚覆盖全部成本

两个策略放在同一张对比表里，是教科书式的对照 —— 一边是「胜率陷阱」，
一边是「盈亏比」，比单独摆一个策略有说服力得多。

═══════════════════════════════════════════════════════
规则（全部取自海龟常规约定，**事先定死，不做任何参数搜索**）：

  入场   收盘价 > 前 20 根最高价  → 做多   (enter_tag = turtle_break_long)
         收盘价 < 前 20 根最低价  → 做空   (enter_tag = turtle_break_short)

  加仓   每顺势再走 0.5N 加 1 个单位，最多 4 个单位（首单算第 1 个）
         单位大小**相等**（原版海龟如此，不是马丁的倍数放大）

  止损   最后一个单位的入场价 ∓ 2N        N = ATR(20)，入场时冻结
         随着加仓止损价一路上移（只上移不下移，框架保证）

  出场   收盘价 < 前 10 根最低价（多）／ 收盘价 > 前 10 根最高价（空）
         刻意**不做**分批止盈，也不加 ADX / 波动率过滤 —— 原版没有

═══════════════════════════════════════════════════════
回测命令：

  # 主结果（带明细周期，为了加仓判得准）
  freqtrade backtesting -s TurtleBreakout -c user_data/config/config_turtle_4h.json \
    -i 4h --timerange 20190901-20261010 --timeframe-detail 5m --export trades

  # 对照组（不带明细周期，数字不可与上面直接比较）
  freqtrade backtesting -s TurtleBreakout -c user_data/config/config_turtle_4h.json \
    -i 4h --timerange 20190901-20261010 --export trades

为什么主结果要带 --timeframe-detail：**只为了加仓**。不带时
adjust_trade_position 每根 4h K 线只在**开盘价**判一次
（backtesting.py:718-724），会系统性漏掉盘中走到的加仓点。
**止损不需要它** —— 2N 止损走 custom_stoploss，框架已经拿 K 线的 low/high
判盘中触及了（interface.py:1635-1636）。

═══════════════════════════════════════════════════════
几个查过源码确认的机制：

* ⚠️ **ta.MAX / ta.MIN 默认用 close 列，不是 high/low。** 不显式传 price=，
  算出来的是「收盘价通道」—— 通道更窄、信号更多，而且根本不是唐奇安通道。
  本机实测：同一段数据不传 price 得 8611.27，传 price="high" 得 8788.0。

* ⚠️ **唐奇安通道必须 .shift(1)**。不 shift 的话「当根最高价」里含当根，
  `close > 当根最高价` 恒为假，永远开不出仓。

* ⚠️ **信号在「下一根开盘」成交**，框架在回测前把信号整体 shift 了一根
  （backtesting.py:554-567）。所以 trade.open_date 是**入场那根**，
  而开仓决策用的是**信号那根**的信息 —— 冻结 N 时取的是信号那根的 ATR，
  因此 populate_indicators 里多存一列 atr_prev（见下面 _entry_n）。

* 回测里 custom_stoploss 收到的 current_rate 是本根 K 线的**有利极值**
  （多 = high，interface.py:1559/:1573），不是收盘价。我们是拿它去换算
  「绝对止损价 → 相对比率」，换算回来的比率乘上 current_rate 正好等于
  设定的绝对止损价，所以这个非直觉的入参**不影响**结果。

* 止损**只会朝有利方向走，不会变宽**（trade_model.py:886 "stop losses only
  walk up, never down"）。所以：
    - 我们给的 2N 止损（约 3~7%）永远比 stoploss = -0.25 的兜底紧 → 生效；
    - 加仓后止损价上移到「新单位入场价 ∓ 2N」→ 自动生效，不用特殊处理。

* 加仓后 trade.open_rate 会变成**加权均价**、trade.stake_amount 会变成总和，
  所以「上一个单位的价格」必须自己缓存，否则加仓阈值会随均价漂移。
  本策略用 trade.set_custom_data 缓存，并在 order_filled 里推进它。

* 回测模式**完全禁用数据库**，且强制 dry_run=True、stoploss_on_exchange=False
  （backtesting.py:152、:483-484）。所以配置里的 db_url 只影响 dry-run 实例。
═══════════════════════════════════════════════════════
"""

from datetime import datetime

import pandas as pd
import talib.abstract as ta
from pandas import DataFrame

from freqtrade.persistence import Order, Trade
from freqtrade.strategy import IStrategy, stoploss_from_absolute


class TurtleBreakout(IStrategy):

    INTERFACE_VERSION = 3

    # ── 基础设置 ──
    timeframe = "4h"
    can_short = True
    # 20 根通道 + 20 根 ATR 就够，但 talib 的 ATR 前 19 根是 NaN，
    # atr_prev 还要再 shift 一根，40 刚好卡在边界上，所以留到 60。
    startup_candle_count = 60

    # ── 唐奇安参数（原版海龟的常规值，不做搜索）──
    entry_channel = 20      # 突破前 20 根的高低点开仓
    exit_channel = 10       # 跌破前 10 根的反向通道离场
    atr_period = 20         # N = ATR(20)

    # ── 仓位与风险 ──
    stop_atr_mult = 2.0     # 止损 = 入场价 ∓ 2N
    pyramid_step_n = 0.5    # 每顺势走 0.5N 加一个单位
    max_units = 4           # 最多 4 个单位（首单算 1 个）

    # ── 出场 ──
    # minimal_roi 设成一个到不了的值 = 关掉固定止盈，出场只走 10 通道信号。
    # 原版海龟不设固定止盈 —— 趋势跟踪的全部收益来自少数几笔跑很远的单子，
    # 固定止盈会把它们砍在趋势中段。
    minimal_roi = {"0": 0.99}
    # 这个 -25% **只是灾难兜底**（框架要求 stoploss 必须存在），
    # 真正的止损由 custom_stoploss 给的 2N（约 3~7%）承担。
    stoploss = -0.25
    use_custom_stoploss = True
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    # ── 加仓开关 ──
    position_adjustment_enable = True
    max_entry_position_adjustment = max_units - 1   # 首单算 1 个，所以这里是 3

    # ─────────────────────────────────────────────
    # 指标
    # ─────────────────────────────────────────────

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        p = self.entry_channel
        q = self.exit_channel

        # 显式传 price=high/low，见模块 docstring 的第一个 ⚠️
        # .shift(1) 让通道**不含当根**，见第二个 ⚠️
        dataframe["dc_up20"] = ta.MAX(dataframe, timeperiod=p, price="high").shift(1)
        dataframe["dc_dn20"] = ta.MIN(dataframe, timeperiod=p, price="low").shift(1)
        dataframe["dc_up10"] = ta.MAX(dataframe, timeperiod=q, price="high").shift(1)
        dataframe["dc_dn10"] = ta.MIN(dataframe, timeperiod=q, price="low").shift(1)

        dataframe["atr"] = ta.ATR(dataframe, timeperiod=self.atr_period)
        # 冻结 N 时用的是**信号那根**的 ATR，见 _entry_n 的说明
        dataframe["atr_prev"] = dataframe["atr"].shift(1)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        ready = (
            dataframe["dc_up20"].notna()
            & dataframe["dc_dn20"].notna()
            & dataframe["atr_prev"].notna()
        )

        enter_long = ready & (dataframe["close"] > dataframe["dc_up20"])
        dataframe.loc[enter_long, "enter_long"] = 1
        dataframe.loc[enter_long, "enter_tag"] = "turtle_break_long"

        enter_short = ready & (dataframe["close"] < dataframe["dc_dn20"])
        dataframe.loc[enter_short, "enter_short"] = 1
        dataframe.loc[enter_short, "enter_tag"] = "turtle_break_short"

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        ready = dataframe["dc_up10"].notna() & dataframe["dc_dn10"].notna()

        # 多头跌破 10 根低点离场；空头涨破 10 根高点离场
        exit_long = ready & (dataframe["close"] < dataframe["dc_dn10"])
        dataframe.loc[exit_long, "exit_long"] = 1

        exit_short = ready & (dataframe["close"] > dataframe["dc_up10"])
        dataframe.loc[exit_short, "exit_short"] = 1

        return dataframe

    # ─────────────────────────────────────────────
    # 缓存工具（照抄 GridMartingale._cached）
    # ─────────────────────────────────────────────

    @staticmethod
    def _cached(trade: Trade, key: str, value):
        """
        惰性缓存：第一次调用时把 value 存进这笔交易的 custom_data。

        必须缓存的原因：加仓之后 trade.open_rate 会变成加权均价、
        trade.stake_amount 会变成累计值。不缓存的话「上一个单位的价格」
        会随均价漂移，金字塔就不是金字塔了。
        """
        cached = trade.get_custom_data(key)
        if cached is None:
            trade.set_custom_data(key, value)
            return value
        return cached

    def _entry_n(self, trade: Trade) -> float | None:
        """
        取出这笔交易开仓时冻结的 N（= 当时那根信号 K 线的 ATR(20)）。

        为什么不能直接用「入场那根的 ATR」：信号在**下一根开盘**成交
        （backtesting.py:554-567），trade.open_date 指向的是入场那根。
        直接读它会把入场 K 线自己的振幅算进止损宽度里，属于轻微前视。
        所以 populate_indicators 里存了 atr_prev，这里按 open_date 定位到
        入场那根，读 atr_prev 拿到的就正好是信号那根的 ATR。

        取不到就返回 None —— 调用方会退回到 stoploss = -0.25 的兜底止损，
        而不是拿一个编出来的 N 去下止损单。
        """
        cached = trade.get_custom_data("turtle_entry_n")
        if cached is not None:
            return float(cached)

        dataframe, _ = self.dp.get_analyzed_dataframe(trade.pair, self.timeframe)
        if dataframe is None or dataframe.empty:
            return None

        # side="right" 再减 1 = 「最后一根不晚于 open_date 的 K 线」，
        # 精确命中时就是那一根，不精确时退到它前面最近的一根，都不会越界。
        pos = int(dataframe["date"].searchsorted(pd.Timestamp(trade.open_date_utc), side="right")) - 1
        if pos < 0:
            return None

        n = dataframe["atr_prev"].iloc[pos]
        if pd.isna(n) or float(n) <= 0:
            return None

        trade.set_custom_data("turtle_entry_n", float(n))
        return float(n)

    # ─────────────────────────────────────────────
    # 加仓：每顺势走 0.5N 加一个单位
    # ─────────────────────────────────────────────

    def adjust_trade_position(
        self,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        min_stake: float | None,
        max_stake: float,
        current_entry_rate: float,
        current_exit_rate: float,
        current_entry_profit: float,
        current_exit_profit: float,
        **kwargs,
    ) -> tuple[float, str] | None:
        n = self._entry_n(trade)
        if n is None:
            return None

        filled = trade.nr_of_successful_entries   # 首单成交后 = 1
        if filled >= self.max_units:
            return None

        # 基准仓位：首单的 stake，加仓后 trade.stake_amount 会变成累计值，所以缓存
        base_stake = float(self._cached(trade, "turtle_base_stake", trade.stake_amount))
        # 上一个单位的成交价，由 order_filled 在每次入场成交后推进
        last_px = float(self._cached(trade, "turtle_last_px", trade.open_rate))

        step = self.pyramid_step_n * n
        if trade.is_short:
            # 空头要价格**下跌**才算顺势；没走到目标价就不加
            if current_rate > last_px - step:
                return None
        else:
            if current_rate < last_px + step:
                return None

        if min_stake is not None and base_stake < min_stake:
            return None
        if base_stake > max_stake:
            # 余额不够就不加，避免回测里出现超出钱包的仓位
            return None

        # 返回 (金额, 标签)：标签会写进订单的 ft_order_tag，
        # APP 的订单列表里就能看出这是加仓单而不是首单。
        return base_stake, "turtle_add"

    def order_filled(
        self, pair: str, trade: Trade, order: Order, current_time: datetime, **kwargs
    ) -> None:
        """
        每次入场单成交后，把「上一个单位的价格」推进到实际成交价。

        为什么不在 adjust_trade_position 里直接推进：那里只是**下单**，
        订单未必成交（回测里还要靠 _get_order_filled 判价格是否在 K 线区间内）。
        放在 order_filled 里推进，记的一定是真实成交价。
        """
        if order.ft_order_side != trade.entry_side:
            return   # 出场单不动它
        trade.set_custom_data("turtle_last_px", float(order.safe_price))

    # ─────────────────────────────────────────────
    # 止损：最后一个单位入场价 ∓ 2N
    # ─────────────────────────────────────────────

    def custom_stoploss(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        after_fill: bool,
        **kwargs,
    ) -> float | None:
        n = self._entry_n(trade)
        if n is None:
            return None   # 拿不到 N 就不设自定义止损，退回 stoploss 兜底

        last_px = float(self._cached(trade, "turtle_last_px", trade.open_rate))
        offset = self.stop_atr_mult * n
        stop_rate = (
            last_px + offset if trade.is_short
            else last_px - offset
        )

        # 说明：回测里 current_rate 传进来的是本根 K 线的有利极值（多 = high），
        # 但这里只是拿它做「绝对价 → 相对比率」的换算，换算回去正好等于
        # stop_rate，所以最终止损价不受这个非直觉入参影响。
        return stoploss_from_absolute(
            stop_rate, current_rate, is_short=trade.is_short, leverage=trade.leverage or 1.0
        )

    def leverage(
        self,
        pair: str,
        current_time: datetime,
        current_rate: float,
        proposed_leverage: float,
        max_leverage: float,
        entry_tag: str | None,
        side: str,
        **kwargs,
    ) -> float:
        # 固定 1 倍。本项目所有演示策略都锁定 1 倍杠杆，不做杠杆优化 ——
        # 一放大杠杆，回撤也跟着放大，拿高杠杆跑出来的收益曲线没有可比性。
        return 1.0
