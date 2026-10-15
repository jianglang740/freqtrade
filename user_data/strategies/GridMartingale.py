"""
GridMartingale — BTC/ETH 1h 网格 + 马丁加仓（多空双向）

═══════════════════════════════════════════════════════
这个策略的目的**不是找 alpha，是产出订单流**：
APP 的交易列表、持仓页、收益曲线、订单详情都需要真实数据才能调试，
而趋势跟踪策略 7 年才 191 笔、每笔 1 个入场 + 1 个出场。
网格马丁每笔交易会有 1~4 个入场单，交易频次也高得多。

═══════════════════════════════════════════════════════
怎么运作：

  首单：价格偏离 1 天均线 1% 才开
        做多 = 跌破均线 1%（接下跌）
        做空 = 涨过均线 1%（接上涨）

  加仓：价格每逆向走 1.5%，加一层，仓位放大 1.5 倍
        第 1 层 1.0×   第 2 层 1.5×   第 3 层 2.25×   第 4 层 3.375×
        全部打满 = 8.125 倍基准仓
        网格层价：做多 = 首单价 ×(1 - 1.5%×层号)，做空对称

  止盈：整个持仓的**均价**赚到 1.2% 就全平
        —— 用的是 minimal_roi，而 ROI 是回测里唯一会看 K 线 high/low 的出场路径，
           所以盘中触及就能精确成交，不用等到收盘。

  止损：均价亏 25% 硬砍。这是尾部保护 —— 马丁最怕单边行情一路不回头。

  大势过滤：价格在 200 根（约 8 天）均线上方才允许开多头网格，下方才允许开空头网格。
        这条不是可选项 —— 去掉它，空头网格会在 2019–2026 的 BTC 牛市里被反复扫穿，
        亏掉多头网格赚的全部利润（见文件末尾的对照子类）。

═══════════════════════════════════════════════════════
回测命令：

  freqtrade backtesting -s GridMartingale -c user_data/config/config_grid_1h.json \
    -i 1h --timerange 20190901-20261010 --export trades

═══════════════════════════════════════════════════════
几个查过源码确认的机制：

* adjust_trade_position 返回**正数 = 加仓**，返回负数 = 减仓（永远只是减仓，不会反向开仓）。
* 加仓次数由 max_entry_position_adjustment 限制；它数的是「已成交的入场单数」，
  首单也算一个，所以 4 层要设成 3，不是 4。
* trade.open_rate 在加仓后会变成**加权均价**，所以首单价格必须自己缓存，
  否则第 2 层算出来的网格价会跟着均价漂移。本策略用 trade.set_custom_data 缓存。
* 回测里 adjust_trade_position 每根 K 线只调一次、只看开盘价 —— 对网格无所谓，
  因为网格本来就是按价格档位触发的。
═══════════════════════════════════════════════════════
"""

from datetime import datetime

import talib.abstract as ta
from pandas import DataFrame

from freqtrade.persistence import Trade
from freqtrade.strategy import IStrategy


class GridMartingale(IStrategy):

    INTERFACE_VERSION = 3

    # ── 基础设置 ──
    timeframe = "1h"
    can_short = True
    startup_candle_count = 250   # 覆盖 regime SMA(200)

    # ── 网格参数 ──
    sma_period = 24           # 1 天（1h × 24），作为网格的参考中枢
    entry_deviation = 0.01    # 价格偏离中枢 1% 才开首单
    grid_step = 0.015         # 网格层间距 1.5%
    martingale_mult = 1.5     # 每层仓位倍率
    max_layers = 4            # 最多 4 层（首单 + 3 次加仓）= 8.125 倍基准仓

    # ── 大势过滤 ──
    # 网格是「区间里来回吃价差」的策略，最怕单边行情：价格一路不回头时，
    # 逆势那一侧会一路加满仓然后被止损打掉。全周期回测里空头网格在
    # 2019–2026 的 BTC 牛市里亏掉了全部利润，就是这个问题。
    # 所以只在「大势方向」上铺网：价格在长期均线上方才做多网格，下方才做空网格。
    use_regime_filter = True
    regime_sma_period = 200   # 约 8 天

    # ── 止盈止损 ──
    minimal_roi = {"0": 0.012}   # 均价 +1.2% 全平（走 ROI 路径，盘中精确触发）
    stoploss = -0.25             # 均价 -25% 硬砍，尾部保护
    use_custom_stoploss = False
    use_exit_signal = False      # 不用信号出场，出场全靠 ROI / 止损

    # ── 加仓开关 ──
    position_adjustment_enable = True
    max_entry_position_adjustment = max_layers - 1   # 首单算 1 个，所以这里是 3

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["sma"] = ta.SMA(dataframe, timeperiod=self.sma_period)
        dataframe["regime_sma"] = ta.SMA(dataframe, timeperiod=self.regime_sma_period)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        ready = dataframe["sma"].notna()

        # 大势方向：多头网格只在长期均线上方铺，空头网格只在下方铺
        if self.use_regime_filter:
            regime = dataframe["regime_sma"]
            above = regime.notna() & (dataframe["close"] > regime)
            below = regime.notna() & (dataframe["close"] < regime)
        else:
            above = below = ready

        enter_long = ready & above & (dataframe["close"] < dataframe["sma"] * (1 - self.entry_deviation))
        dataframe.loc[enter_long, "enter_long"] = 1
        dataframe.loc[enter_long, "enter_tag"] = "grid_long"

        enter_short = ready & below & (dataframe["close"] > dataframe["sma"] * (1 + self.entry_deviation))
        dataframe.loc[enter_short, "enter_short"] = 1
        dataframe.loc[enter_short, "enter_tag"] = "grid_short"

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # 出场交给 minimal_roi（止盈）和 stoploss（止损），不放信号出场
        return dataframe

    # ─────────────────────────────────────────────
    # 网格加仓
    # ─────────────────────────────────────────────

    @staticmethod
    def _cached(trade: Trade, key: str, value):
        """
        首单价格 / 基准仓位的惰性缓存。

        必须缓存的原因：加仓之后 trade.open_rate 会变成加权均价，
        trade.stake_amount 也会变大。如果不缓存，第 N 层的网格价会随着
        均价漂移，网格就不是网格了。
        """
        cached = trade.get_custom_data(key)
        if cached is None:
            trade.set_custom_data(key, value)
            return value
        return cached

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
    ) -> float | None:
        # 第一次调用时这笔交易只有首单，此时记下的就是首单价和基准仓位
        base_rate = float(self._cached(trade, "grid_base_rate", trade.open_rate))
        base_stake = float(self._cached(trade, "grid_base_stake", trade.stake_amount))

        filled = trade.nr_of_successful_entries   # 首单成交后 = 1
        if filled >= self.max_layers:
            return None

        # 下一层就是第 filled 层（1-based）：首单之后 filled=1 → 加第 1 层
        layer = filled
        if trade.is_short:
            target = base_rate * (1 + self.grid_step * layer)
            if current_rate < target:
                return None
        else:
            target = base_rate * (1 - self.grid_step * layer)
            if current_rate > target:
                return None

        stake = base_stake * (self.martingale_mult ** layer)
        if min_stake is not None and stake < min_stake:
            return None
        if stake > max_stake:
            # 余额不够就不加，避免回测里出现超出钱包的仓位
            return None
        return stake

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
        # 固定 1 倍。网格马丁本来就在放大仓位，杠杆再叠加会直接把账户打穿。
        return 1.0


class GridMartingaleNoFilter(GridMartingale):
    """
    对照版本：关掉大势过滤，多空两侧任何时候都铺网。

    用途：单独跑它就能复现「空头网格在牛市里亏掉全部利润」的原始结果，
    和上面带过滤的版本做差，才能说清过滤到底值多少 —— 而不是只拿好数字说事。

      freqtrade backtesting -s GridMartingaleNoFilter -c user_data/config/config_grid_1h.json \
        -i 1h --timerange 20190901-20261010 --export trades
    """

    use_regime_filter = False
