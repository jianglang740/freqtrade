from datetime import datetime
from typing import Optional
from freqtrade.persistence import Trade
from freqtrade.strategy import IStrategy
from pandas import DataFrame


class FibMartingaleStrategy(IStrategy):

    # ═══════════════════════════════════════
    # 锁定极值：确认入场即刻锁死，不容滑动窗口后续飘移
    # ═══════════════════════════════════════
    _locked_swings: dict = {}   # trade.id → {swing_high, swing_low}
    _pending_locks: dict = {}   # pair → {swing_high, swing_low}（confirm_trade_entry 阶段暂存）

    def _get_locked_fibs(self, trade_id: int):
        """获取入场时锁定的斐波那契线"""
        locked = self._locked_swings.get(trade_id)
        if locked is None:
            return None #如果尚未锁定极值，则返回 None
        swing_range = locked["swing_high"] - locked["swing_low"] # 计算波段范围
        levels = {}
        for level in self.fib_levels:
            tag = int(level * 1000)
            levels[f"fib_dn_{tag}"] = locked["swing_high"] - swing_range * level
            levels[f"fib_up_{tag}"] = locked["swing_low"] + swing_range * level
        return levels

    # ═══════════════════════════════════════
    # 基础设置
    # ═══════════════════════════════════════
    timeframe = "1m"
    startup_candle_count = 250  # swing_lookback 需要的预热K线，因为我们的波段识别窗口是 240 根 K 线，所以预热 250 根 K 线即可
    can_short = True  # 允许做空

    # ═══════════════════════════════════════
    # 仓位管理 — 马丁模式
    # ═══════════════════════════════════════
    position_adjustment_enable = True
    max_entry_position_adjustment = 7   # 首仓 + 最多 7 档加仓 = 共 8 次入场
    max_open_trades = 14                # 最多 14 个仓位（多+空合计）

    # ═══════════════════════════════════════
    # 止盈止损
    # ═══════════════════════════════════════
    minimal_roi = {"0": 0.15}         # 盈利 15%（价格 1.5%）固定止盈，短线快进快出，不做移动止盈逻辑
    stoploss = -0.25                 # 硬止损 -25%（10倍下价格 -2.5%），不做移动止损或追踪止损逻辑

    # ═══════════════════════════════════════
    # 策略参数（可调）
    # ═══════════════════════════════════════

    # 波段识别窗口（原始滚动窗口，备用）
    swing_lookback = 240  # 1m × 240 = 4小时波段

    # ZigZag 确认阈值：价格需从极点回撤/反弹 1.5% 才确认拐点，避免轮询模式下k线收长影线带来的噪音干扰（虚假极致）
    zigzag_threshold = 0.010  # 价格反向 1.0% 确认拐点（原 2.0% 太严，低波动不开单）

    # 斐波那契回撤位（8 档 = 首仓0.382 + 7 次加仓）
    fib_levels = [0.382, 0.5, 0.618, 0.786, 0.886, 1.0, 1.272, 1.618]

    # 马丁倍率（用于加仓时的手数放大，首仓固定仓位大小，不用复利效应放大仓位，损失潜在利润的同时也避免潜在风险）
    martingale_mults = [1.0, 1.5, 2.5, 4.0, 6.0, 8.0, 11.0, 13.0]

    # 首仓入场斐波那契阈值
    entry_fib_threshold = 0.382

    # ═══ 杠杆 ═══
    # 全局杠杆倍数
    leverage_value = 10  # 10 倍杠杆

    # ═══ 首仓手数（按币种分别设置，单位=张）═══

    # key = 交易对（BASE 货币），value = 首仓合约张数
    base_contracts: dict = {
        "BTC/USDT:USDT": 0.01,   # 首仓 0.01 BTC
        "ETH/USDT:USDT": 0.15,   # 首仓 0.15 ETH
    }

    # ═══════════════════════════════════════
    # 指标计算
    # ═══════════════════════════════════════
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:

        # ═══ ZigZag 确认型极值点 ═══
        # 极值需要反向回撤确认才锁定，避免长影线噪音
        zz_highs = []
        zz_lows = []
        # ⚠️ 用滚动窗口极值初始化，不能用 iloc[0] — 低波动市场 iloc[0] 会永久卡死
        init_lookback = min(self.swing_lookback, len(dataframe))
        confirmed_high = dataframe["high"].iloc[:init_lookback].max()
        confirmed_low = dataframe["low"].iloc[:init_lookback].min()

        potential_high = confirmed_high
        potential_low = confirmed_low

        for i in range(len(dataframe)):
            h = dataframe["high"].iloc[i]
            l = dataframe["low"].iloc[i]
            c = dataframe["close"].iloc[i]

            # 更新潜在极点
            if h > potential_high:
                potential_high = h
            if l < potential_low:
                potential_low = l

            # 从潜在高点回撤超阈值 → 确认这是真波段顶
            if c < potential_high * (1 - self.zigzag_threshold):
                confirmed_high = potential_high
                potential_high = c

            # 从潜在低点反弹超阈值 → 确认这是真波段底
            if c > potential_low * (1 + self.zigzag_threshold):
                confirmed_low = potential_low
                potential_low = c

            zz_highs.append(confirmed_high)
            zz_lows.append(confirmed_low)

        dataframe["zz_high"] = zz_highs
        dataframe["zz_low"] = zz_lows

        # ═══ 用 ZigZag 确认极值计算斐波那契线 ═══
        zz_range = dataframe["zz_high"] - dataframe["zz_low"]

        for level in self.fib_levels:
            tag = int(level * 1000)
            dataframe[f"fib_dn_{tag}"] = dataframe["zz_high"] - zz_range * level
            dataframe[f"fib_up_{tag}"] = dataframe["zz_low"] + zz_range * level

        return dataframe

    # ═══════════════════════════════════════
    # 首仓入场信号（做多 + 做空）
    # ═══════════════════════════════════════
    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["enter_long"] = 0 
        dataframe["enter_short"] = 0

        fib_tag = int(self.entry_fib_threshold * 1000)

        swing_range = dataframe["zz_high"] - dataframe["zz_low"]
        enough_range = swing_range > (dataframe["zz_high"] * self.zigzag_threshold)  # 波幅 > ZigZag阈值

        # ── 做多信号：价格跌破下跌斐波那契线 ──
        fib_dn = dataframe[f"fib_dn_{fib_tag}"]
        cross_dn = (
            (dataframe["close"] < fib_dn)
            & (dataframe["close"].shift(1) >= fib_dn.shift(1))
        )
        dataframe.loc[cross_dn & enough_range, "enter_long"] = 1

        # ── 做空信号：价格涨破上涨斐波那契线（从底部反弹到下压位）──
        fib_up = dataframe[f"fib_up_{fib_tag}"]
        cross_up = (
            (dataframe["close"] > fib_up)
            & (dataframe["close"].shift(1) <= fib_up.shift(1))
        )
        dataframe.loc[cross_up & enough_range, "enter_short"] = 1

        return dataframe

    # ═══════════════════════════════════════
    # 入场确认：即刻锁死当前 ZigZag 极值
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
        """入场前最后一刻：抓取当前 ZigZag 极值锁定。"""
        try:
            dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
            last_candle = dataframe.iloc[-1]
            self._pending_locks[pair] = {
                "swing_high": last_candle["zz_high"],
                "swing_low": last_candle["zz_low"],
            }
        except Exception:
            pass
        return True  # 永远允许入场，只负责记录

    # ═══════════════════════════════════════
    # 离场信号（交给 stoploss 和 ROI）
    # ═══════════════════════════════════════
    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["exit_long"] = 0
        dataframe["exit_short"] = 0
        return dataframe

    # ═══════════════════════════════════════
    # 自定义退出（极端情况保护）
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

        # 退出时清理锁定值（不论退出原因，下一次会重新锁）
        self._locked_swings.pop(trade.id, None)

        # 持仓超过 30 天不盈利 → 止损退出（考虑资金使用效率和合约交易的资金费率成本）
        hold_days = (current_time - trade.open_date_utc).days
        if hold_days > 30 and current_profit < 0.01:
            return "timeout"

        return None

    # ═══════════════════════════════════════
    # 动态追踪止损（替代固定止盈）
    # ═══════════════════════════════════════
    # ═══════════════════════════════════════
    # 杠杆回调：按币种返回杠杆倍数
    # ═══════════════════════════════════════
    def leverage(
        self,
        pair: str,
        current_time: datetime,
        current_rate: float,
        proposed_leverage: float,
        max_leverage: float,
        entry_tag: Optional[str],
        side: str,
        **kwargs,
    ) -> float:
        """
        固定使用 self.leverage_value 倍杠杆。
        回测中也要调用此方法来确定杠杆。
        """
        return self.leverage_value

    # ═══════════════════════════════════════
    # 手数 → 保证金换算
    # ═══════════════════════════════════════
    def custom_stake_amount(
        self,
        pair: str,
        current_time: datetime,
        current_rate: float,
        proposed_stake: float,
        min_stake: Optional[float],
        max_stake: float,
        leverage: float,
        entry_tag: Optional[str],
        side: str,
        **kwargs,
    ) -> float:
        """
        把合约手数换算成保证金（USDT）。
        公式：保证金 = (合约手数 × 当前价) ÷ 杠杆

        例如：0.01 BTC × $65,000 ÷ 5 倍 = $130 保证金
        """

        # 获取该交易对的首仓手数
        base_contract = self.base_contracts.get(pair)
        if base_contract is None:
            return proposed_stake  # 没有配置时回退到配置文件的值

        # 保证金计算
        required_stake = (base_contract * current_rate) / leverage

        return max(required_stake, min_stake or required_stake)

    # ═══════════════════════════════════════
    # 马丁加仓（多空通用）
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
        做多：价格继续下跌到更深 fib_dn_xxx 时加仓
        做空：价格继续上涨到更高 fib_up_xxx 时加仓
        """

        filled_entries = trade.nr_of_successful_entries
        if filled_entries >= len(self.martingale_mults):
            self._locked_swings.pop(trade.id, None)  # 加仓已满，清理
            return None

        # 将 confirm_trade_entry 阶段暂存的极值转移到正式锁定
        if trade.id not in self._locked_swings:
            pending = self._pending_locks.pop(trade.pair, None)
            if pending:
                self._locked_swings[trade.id] = pending
            else:
                # 兜底：未预锁时用当前值（极端情况）
                try:
                    dataframe, _ = self.dp.get_analyzed_dataframe(trade.pair, self.timeframe)
                    last_candle = dataframe.iloc[-1]
                    self._locked_swings[trade.id] = {
                        "swing_high": last_candle["zz_high"],
                        "swing_low": last_candle["zz_low"],
                    }
                except Exception:
                    return None

        # 用锁定的极值计算斐波那契线（不随滑动窗口飘移）
        locked_fibs = self._get_locked_fibs(trade.id)
        if locked_fibs is None:
            return None

        next_fib_idx = filled_entries
        if next_fib_idx >= len(self.fib_levels):
            return None

        next_fib_level = self.fib_levels[next_fib_idx]
        fib_tag = int(next_fib_level * 1000)
        is_short = trade.is_short

        if is_short:
            target_price = locked_fibs[f"fib_up_{fib_tag}"]
            if current_rate < target_price:
                return None
        else:
            target_price = locked_fibs[f"fib_dn_{fib_tag}"]
            if current_rate > target_price:
                return None

        mult = self.martingale_mults[filled_entries]
        base_contract = self.base_contracts.get(trade.pair, 0.01)
        # 加仓手数 = 首仓手数 × 马丁倍率
        additional_contracts = base_contract * mult
        # 保证金 = (手数 × 当前价) ÷ 杠杆
        raw_stake = (additional_contracts * current_rate) / trade.leverage
        additional_stake = max(raw_stake, min_stake) if min_stake else raw_stake
        if additional_stake > max_stake:
            additional_stake = max_stake

        return additional_stake
