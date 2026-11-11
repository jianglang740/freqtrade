import logging
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
from pandas import DataFrame

from freqtrade.persistence import Trade
from freqtrade.strategy import IStrategy

logger = logging.getLogger(__name__)


class FibMartingaleStrategy(IStrategy):

    # ═══════════════════════════════════════
    # 基础设置
    # ═══════════════════════════════════════
    timeframe = "1m"
    startup_candle_count = 250  # 预热 250 根 1m K 线
    can_short = True  # 允许做空

    # ═══════════════════════════════════════
    # 信号重算
    # ═══════════════════════════════════════
    # 默认 True 时，每根 K 线只在出现瞬间算一次信号，之后 59 秒内信号被清空，
    # 导致 K 线中途价格穿越斐波那契线时，下单读到的缓存信号已是 0。
    # 设为 False 让每 5 秒循环都重算信号，K 线中途的穿越也能被捕捉到。
    process_only_new_candles = False

    # custom_exit 依赖 use_exit_signal，显式打开，避免被配置或版本默认值关掉
    use_exit_signal = True

    # ═══════════════════════════════════════
    # 仓位管理 — 马丁模式
    # ═══════════════════════════════════════
    position_adjustment_enable = True
    max_entry_position_adjustment = 7   # 首仓 + 最多 7 档加仓 = 共 8 次入场
    max_open_trades = 14                # 最多 14 个仓位（多+空合计）

    # ═══════════════════════════════════════
    # 止盈止损
    # ═══════════════════════════════════════
    minimal_roi = {"0": 0.15}         # 保证金盈利 15%（10x 下价格 +1.5%）固定止盈

    # 框架止损只作为灾难兜底（10x 下价格反向 8%，接近爆仓价）。
    # 真正的「总保证金 -25%」止损放在 custom_exit 里按当前均价实时判断，
    # 原因见下方 custom_exit 注释：freqtrade 的原生/custom 止损都不允许向下调，
    # 加仓后没法把止损搬到新均价的 -25% 位置。
    stoploss = -0.8

    # 总保证金硬止损比例：0.25 = -25% 保证金（10x 下约价格反向 2.5%）
    hard_stop_margin = 0.25
    use_custom_stoploss = False

    # ═══════════════════════════════════════
    # 策略参数（可调）
    # ═══════════════════════════════════════

    # 历史参数：保留用于说明预热长度；ZigZag 本身没有固定窗口
    swing_lookback = 240  # 1m × 240 ≈ 4 小时

    # ZigZag 确认阈值：价格需从极点回撤/反弹 2.0% 才确认拐点
    zigzag_threshold = 0.020

    # 斐波那契回撤位（8 档 = 首仓 0.382 + 7 次加仓）
    fib_levels = [0.382, 0.5, 0.618, 0.786, 0.886, 1.0, 1.272, 1.618]

    # 马丁倍率（相对首仓实际成交手数）
    martingale_mults = [1.0, 1.5, 2.5, 4.0, 6.0, 8.0, 11.0, 13.0]

    # 首仓入场斐波那契阈值
    entry_fib_threshold = 0.382

    # ═══ 杠杆 ═══
    leverage_value = 10  # 10 倍杠杆

    # ═══ 首仓手数（按币种分别设置，单位 = 基础币，不是张）═══
    base_contracts: dict = {
        "BTC/USDT:USDT": 0.01,   # 首仓 0.01 BTC
        "ETH/USDT:USDT": 0.15,   # 首仓 0.15 ETH
    }

    # ═══════════════════════════════════════
    # 实例状态（不能放类属性，否则多个策略/回测之间会串状态）
    # ═══════════════════════════════════════
    def _reset_state(self) -> None:
        self._locked_swings: dict = {}       # trade.id → {swing_high, swing_low}
        self._pending_locks: dict = {}       # (pair, side) → {swing_high, swing_low, time}
        self._last_rates: dict = {}          # trade.id → 上一次 adjust 看到的价格
        self._last_dca_candle: dict = {}     # trade.id → 已加仓的 1m K 线
        self._base_amounts: dict = {}        # trade.id → 首仓实际成交数量

    def _ensure_state(self) -> None:
        if not hasattr(self, "_locked_swings"):
            self._reset_state()

    def bot_start(self, **kwargs) -> None:
        self._reset_state()

    def _cleanup_trade_state(self, trade: Trade) -> None:
        self._locked_swings.pop(trade.id, None)
        self._pending_locks.pop((trade.pair, trade.trade_direction), None)
        self._last_rates.pop(trade.id, None)
        self._last_dca_candle.pop(trade.id, None)
        self._base_amounts.pop(trade.id, None)

    # ═══════════════════════════════════════
    # ZigZag
    # ═══════════════════════════════════════
    @staticmethod
    def _compute_zigzag(
        highs: np.ndarray,
        lows: np.ndarray,
        closes: np.ndarray,
        threshold: float,
    ) -> tuple[np.ndarray, np.ndarray]:
        """
        交替确认的 ZigZag。

        - trend =  1：当前在上升段，zz_high 取「上一确认高点」与「本段最高点」的较大者，
          zz_low 取上一个确认低点
        - trend = -1：当前在下降段，zz_low 取「上一确认低点」与「本段最低点」的较小者，
          zz_high 取上一个确认高点
        - trend =  0：尚未确认第一段方向，用当前已见区间的 max/min

        这样既不会出现 confirmed_low > confirmed_high 的反向区间，也不会在
        单边趋势里因为「迟迟不确认新拐点」而把区间卡死在 0。

        关键点：只用当前及过去的 K 线，不用未来数据；任何时刻 zz_high >= zz_low。
        """
        n = len(highs)
        zz_highs = np.empty(n, dtype=float)
        zz_lows = np.empty(n, dtype=float)
        if n == 0:
            return zz_highs, zz_lows

        confirmed_high = float(highs[0])
        confirmed_low = float(lows[0])
        potential_high = confirmed_high
        potential_low = confirmed_low
        trend = 0

        for i in range(n):
            h = float(highs[i])
            l = float(lows[i])
            c = float(closes[i])

            if not (np.isfinite(h) and np.isfinite(l) and np.isfinite(c)):
                zz_highs[i] = confirmed_high
                zz_lows[i] = confirmed_low
                continue

            if trend == 0:
                if h > potential_high:
                    potential_high = h
                if l < potential_low:
                    potential_low = l

                # 先向下确认高点，或先向上确认低点；同一根 K 线只确认一个方向
                if c <= potential_high * (1.0 - threshold):
                    confirmed_high = potential_high
                    trend = -1
                    potential_low = l
                elif c >= potential_low * (1.0 + threshold):
                    confirmed_low = potential_low
                    trend = 1
                    potential_high = h

            elif trend == 1:
                # 上升段：高点可以继续抬高；zz_high 不因新一段起点较低而回落
                if h > potential_high:
                    potential_high = h

                # 从高点回撤超过阈值 → 确认波段顶，转入下降段
                if c <= potential_high * (1.0 - threshold):
                    confirmed_high = potential_high
                    trend = -1
                    potential_low = l

            else:  # trend == -1
                # 下降段：低点可以继续降低；zz_low 不因新一段起点较高而抬升
                if l < potential_low:
                    potential_low = l

                # 从低点反弹超过阈值 → 确认波段底，转入上升段
                if c >= potential_low * (1.0 + threshold):
                    confirmed_low = potential_low
                    trend = 1
                    potential_high = h

            zz_highs[i] = max(confirmed_high, potential_high)
            zz_lows[i] = min(confirmed_low, potential_low)

        return zz_highs, zz_lows

    # ═══════════════════════════════════════
    # 指标计算
    # ═══════════════════════════════════════
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        self._ensure_state()

        zz_highs, zz_lows = self._compute_zigzag(
            dataframe["high"].to_numpy(dtype=float),
            dataframe["low"].to_numpy(dtype=float),
            dataframe["close"].to_numpy(dtype=float),
            self.zigzag_threshold,
        )
        dataframe["zz_high"] = zz_highs
        dataframe["zz_low"] = zz_lows

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
        enough_range = (
            (swing_range > 0)
            & (swing_range >= dataframe["zz_low"] * self.zigzag_threshold)
        )

        # ── 做多信号：收盘价从上方跌破下跌斐波那契线 ──
        fib_dn = dataframe[f"fib_dn_{fib_tag}"]
        cross_dn = (
            (dataframe["close"] < fib_dn)
            & (dataframe["close"].shift(1) >= fib_dn.shift(1))
        )
        dataframe.loc[cross_dn & enough_range, "enter_long"] = 1

        # ── 做空信号：收盘价从下方涨破上涨斐波那契线 ──
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
        self._ensure_state()
        try:
            dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
            if dataframe is None or dataframe.empty:
                logger.warning("%s: 入场锁定时没有可用的分析数据", pair)
                return True

            last_candle = dataframe.iloc[-1]
            swing_high = float(last_candle["zz_high"])
            swing_low = float(last_candle["zz_low"])
            if not (np.isfinite(swing_high) and np.isfinite(swing_low)):
                logger.warning("%s: 入场锁定的 ZigZag 极值不是有限数", pair)
                return True
            if swing_high <= swing_low:
                logger.warning(
                    "%s: 入场锁定的 ZigZag 区间无效 high=%s low=%s",
                    pair,
                    swing_high,
                    swing_low,
                )
                return True

            self._pending_locks[(pair, side)] = {
                "swing_high": swing_high,
                "swing_low": swing_low,
                "time": current_time,
            }
        except Exception as exc:  # noqa: BLE001 - 入场不能因为锁定失败而中断
            logger.warning("%s: 入场锁定 ZigZag 极值失败：%s", pair, exc)
        return True

    # ═══════════════════════════════════════
    # 离场信号（交给 stoploss 和 ROI）
    # ═══════════════════════════════════════
    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["exit_long"] = 0
        dataframe["exit_short"] = 0
        return dataframe

    # ═══════════════════════════════════════
    # 自定义退出（总保证金硬止损 + 超时）
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
        为什么硬止损放在这里而不是 custom_stoploss：

        freqtrade 的止损「只上不下」——对多单只接受向上调整，对空单只接受向下调整，
        只有 after_fill=True 时允许 refresh。但每次加仓成交后，框架都会调用
        recalc_trade_from_orders()，用上一档 stop_loss_pct 把止损重新锚到新均价上，
        这一步不带 allow_refresh，方向又恰好是「不利方向」，于是把 after_fill 刚刷新到
        新均价 -25% 的止损又顶回去。实测 10x 下 7 次加仓后，止损被卡在约 -14% 保证金，
        既不是用户要的 -25%，也无法预测。

        current_profit 由 trade.calc_profit_ratio 计算，分母是当前总保证金（含所有加仓），
        已经包含杠杆和手续费，所以 `current_profit <= -0.25` 就是字面意义上的
        「总保证金 -25%」。多空对称，且不受 DCA 改均价影响。

        代价：这是按每次 should_exit 的当前价判断（回测里是每根 1m K 线的开盘价），
        不是盘中逐笔触发，极端插针时可能多亏一点；框架 stoploss=-0.8 只做灾难兜底。
        """
        self._ensure_state()

        # 总保证金 -25% 硬止损（跟随 DCA 后的新均价）
        if current_profit <= -self.hard_stop_margin:
            return f"hard_stop_margin_{int(self.hard_stop_margin * 100)}pct"

        # 持仓超过 30 天仍不盈利 → 退出，考虑资金使用效率和资金费率成本
        if trade.open_date_utc is not None:
            hold_days = (current_time - trade.open_date_utc).total_seconds() / 86400.0
            if hold_days > 30 and current_profit <= 0:
                return "timeout_30d"

        return None

    # ═══════════════════════════════════════
    # 退出成交后清理状态（不能在 custom_exit 里提前 pop）
    # ═══════════════════════════════════════
    def order_filled(
        self,
        pair: str,
        trade: Trade,
        order,
        current_time: datetime,
        **kwargs,
    ) -> None:
        self._ensure_state()
        is_full_exit = (
            order.ft_order_side in (trade.exit_side, "stoploss", "liquidation")
            and (order.safe_amount or 0.0) >= trade.amount - 1e-12
        )
        if is_full_exit:
            self._cleanup_trade_state(trade)

    # ═══════════════════════════════════════
    # 杠杆回调
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
        """固定使用 self.leverage_value 倍杠杆（回测中也会调用）。"""
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
        把首仓数量换算成保证金（USDT）：
        保证金 = (基础币数量 × 当前价) ÷ 杠杆
        """
        base_contract = self.base_contracts.get(pair)
        if base_contract is None:
            return proposed_stake  # 未配置的币种回退到配置文件的值

        lev = leverage or self.leverage_value
        required_stake = (base_contract * current_rate) / lev
        return max(required_stake, min_stake or required_stake)

    # ═══════════════════════════════════════
    # 锁定极值 → 斐波那契线
    # ═══════════════════════════════════════
    def _get_locked_fibs(self, trade_id: int):
        locked = self._locked_swings.get(trade_id)
        if locked is None:
            return None

        swing_high = locked["swing_high"]
        swing_low = locked["swing_low"]
        swing_range = swing_high - swing_low
        if swing_range <= 0:
            return None

        levels = {}
        for level in self.fib_levels:
            tag = int(level * 1000)
            levels[f"fib_dn_{tag}"] = swing_high - swing_range * level
            levels[f"fib_up_{tag}"] = swing_low + swing_range * level
        return levels

    def _get_or_create_lock(self, trade: Trade, current_time: datetime):
        locked = self._locked_swings.get(trade.id)
        if locked is not None:
            return locked

        side = trade.trade_direction
        pending = self._pending_locks.pop((trade.pair, side), None)
        if pending is not None:
            age = current_time - pending["time"]
            if age <= timedelta(minutes=10):
                if pending["swing_high"] > pending["swing_low"]:
                    self._locked_swings[trade.id] = {
                        "swing_high": pending["swing_high"],
                        "swing_low": pending["swing_low"],
                    }
                    return self._locked_swings[trade.id]
                logger.warning(
                    "%s: 入场锁定的区间无效 high=%s low=%s，改用当前值",
                    trade.pair,
                    pending["swing_high"],
                    pending["swing_low"],
                )
            else:
                logger.warning(
                    "%s: 丢弃过期的入场锁定（%s 秒前），改用当前值",
                    trade.pair,
                    int(age.total_seconds()),
                )

        # 兜底：没有预锁或预锁过期时，用当前 ZigZag 极值
        try:
            dataframe, _ = self.dp.get_analyzed_dataframe(trade.pair, self.timeframe)
            if dataframe is None or dataframe.empty:
                return None
            last_candle = dataframe.iloc[-1]
            swing_high = float(last_candle["zz_high"])
            swing_low = float(last_candle["zz_low"])
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s: 读取 ZigZag 极值失败：%s", trade.pair, exc)
            return None

        if not (np.isfinite(swing_high) and np.isfinite(swing_low)):
            return None
        if swing_high <= swing_low:
            logger.warning(
                "%s: 当前 ZigZag 区间无效 high=%s low=%s",
                trade.pair,
                swing_high,
                swing_low,
            )
            return None

        self._locked_swings[trade.id] = {
            "swing_high": swing_high,
            "swing_low": swing_low,
        }
        return self._locked_swings[trade.id]

    # ═══════════════════════════════════════
    # 马丁加仓（多空通用）
    # ═══════════════════════════════════════
    def adjust_trade_position(
        self,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        min_stake: Optional[float],
        max_stake: float,
        current_entry_rate: float,
        current_exit_rate: float,
        current_entry_profit: float,
        current_exit_profit: float,
        **kwargs,
    ) -> Optional[float]:
        """
        做多：价格继续下跌并「向下穿越」更深 fib_dn 时加仓
        做空：价格继续上涨并「向上穿越」更高 fib_up 时加仓

        只认一次穿越，且每根 1m K 线最多加一档，避免价格在档位之间时
        连续调用 adjust_trade_position 把多档一次性补完。
        """
        self._ensure_state()

        # 记录本次看到的价格，供下一次判断是否发生穿越。
        # 必须在所有 return 之前更新，否则挂单期间的价格变化会造成假穿越。
        last_rate = self._last_rates.get(trade.id, trade.open_rate)
        self._last_rates[trade.id] = current_rate

        filled_entries = trade.nr_of_successful_entries
        if filled_entries >= len(self.martingale_mults):
            return None

        # 已经打到总保证金 -25% 止损线时不再加仓，避免“加仓后立刻止损”
        if current_profit <= -self.hard_stop_margin:
            return None

        # 已有挂单时不再重复提交，等它成交或取消
        if trade.has_open_orders:
            return None

        if self._get_or_create_lock(trade, current_time) is None:
            return None

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
            crossed = last_rate < target_price <= current_rate
        else:
            target_price = locked_fibs[f"fib_dn_{fib_tag}"]
            crossed = last_rate > target_price >= current_rate

        if not crossed:
            return None

        # 每根 1m K 线最多加一档（回测里 current_time 是 K 线时间；
        # 实盘里是循环时间，按分钟取整也能限制同一分钟内的反复加仓）
        candle_key = current_time.replace(second=0, microsecond=0)
        if self._last_dca_candle.get(trade.id) == candle_key:
            return None

        # 首仓实际成交数量：优先用第一笔入场订单的成交数量，
        # 这样即使首仓被最小/最大仓位限制，后续倍率仍相对真实首仓，不会错位。
        base_amount = self._base_amounts.get(trade.id)
        if base_amount is None:
            entry_orders = [
                o
                for o in trade.orders
                if o.ft_order_side == trade.entry_side and o.safe_filled > 0
            ]
            if entry_orders:
                base_amount = (
                    entry_orders[0].safe_amount_after_fee
                    or entry_orders[0].safe_filled
                )
            else:
                base_amount = trade.amount / max(1, filled_entries)

            if base_amount <= 0:
                return None
            self._base_amounts[trade.id] = base_amount

        mult = self.martingale_mults[filled_entries]
        additional_contracts = base_amount * mult

        lev = trade.leverage or self.leverage_value

        # 加仓后的新均价如果已经让总保证金亏损达到 -25%，
        # 这一档加进去也会立刻触发止损，没有意义，直接放弃。
        new_amount = trade.amount + additional_contracts
        if new_amount <= 0:
            return None
        new_avg = (
            trade.amount * trade.open_rate + additional_contracts * current_rate
        ) / new_amount
        if new_avg <= 0:
            return None
        if is_short:
            post_add_profit = (1.0 - current_rate / new_avg) * lev
        else:
            post_add_profit = (current_rate / new_avg - 1.0) * lev
        if post_add_profit <= -self.hard_stop_margin:
            return None

        raw_stake = (additional_contracts * current_rate) / lev

        if min_stake and raw_stake < min_stake:
            raw_stake = min_stake
        if max_stake and raw_stake > max_stake:
            raw_stake = max_stake
        if raw_stake <= 0:
            return None
        if min_stake and raw_stake < min_stake:
            return None  # 资金/交易所上限已经不够下最小一档

        self._last_dca_candle[trade.id] = candle_key
        return raw_stake
