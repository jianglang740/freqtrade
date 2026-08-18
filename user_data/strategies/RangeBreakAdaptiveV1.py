from __future__ import annotations

from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd
from pandas import DataFrame

from freqtrade.persistence import Trade
from freqtrade.strategy import (
    IStrategy,
    IntParameter,
    DecimalParameter,
    informative,
    stoploss_from_absolute,
)


class RangeBreakAdaptiveV1(IStrategy):
    """
    RangeBreakAdaptiveV1
    --------------------
    Price-structure driven multi-timeframe strategy.

    Main timeframe: 15m
    Informative timeframes: 1h, 4h

    Design goals:
      - No RSI / MACD / EMA / Bollinger / ATR dependency.
      - Range mean-reversion + breakout in one state-aware framework.
      - Soft scoring rather than a long AND-chain of entry conditions.
      - 4h = macro structure, 1h = regime/range, 15m = entry trigger.
      - Structure-based exits/stops rather than fixed take-profit targets.

    This is a research baseline, not a production-ready strategy.
    """

    INTERFACE_VERSION = 3

    timeframe = "15m"
    startup_candle_count = 800

    can_short = True

    process_only_new_candles = True
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    # Hard safety stop. Dynamic custom_stoploss may tighten this,
    # but can never make the stop wider.
    stoploss = -0.08
    use_custom_stoploss = True
    trailing_stop = False

    # Exits are primarily structural, but pocket gains at +10% margin
    # (~2% price at 5x) instead of holding for the full structure exit.
    minimal_roi = {"0": 0.10}

    # ---- Research parameters ----
    # Informative timeframe lookbacks are fixed in v1.
    # Freqtrade's informative-decorator docs caution against using
    # hyperoptable Parameter.value inside informative methods during hyperopt.
    RANGE_LOOKBACK_1H = 48
    RANGE_LOOKBACK_4H = 24

    edge_zone = DecimalParameter(
        0.10, 0.30, default=0.20, decimals=2, space="buy"
    )
    breakout_buffer = DecimalParameter(
        0.001, 0.010, default=0.003, decimals=3, space="buy"
    )
    expansion_ratio = DecimalParameter(
        1.20, 2.50, default=1.50, decimals=2, space="buy"
    )

    entry_score_threshold = IntParameter(
        2, 6, default=3, space="buy"
    )
    structure_lookback = IntParameter(
        4, 16, default=8, space="sell"
    )

    # Fraction of the proposed stake used as the sizing risk budget.
    # This is deliberately conservative for v1.
    risk_per_trade = DecimalParameter(
        0.002, 0.010, default=0.010, decimals=3, space="buy"
    )

    # Fixed structural buffers, intentionally not hyperoptimized in v1.
    range_stop_buffer = 0.003
    breakout_stop_buffer = 0.002

    @informative("1h")
    def populate_indicators_1h(
        self, dataframe: DataFrame, metadata: dict
    ) -> DataFrame:
        """
        1h market regime / range engine.

        Only OHLCV-derived structure is used.
        """
        lb = self.RANGE_LOOKBACK_1H

        # Current range for location.
        dataframe["range_high"] = dataframe["high"].rolling(
            lb, min_periods=lb
        ).max()
        dataframe["range_low"] = dataframe["low"].rolling(
            lb, min_periods=lb
        ).min()
        dataframe["range_mid"] = (
            dataframe["range_high"] + dataframe["range_low"]
        ) / 2.0

        width = dataframe["range_high"] - dataframe["range_low"]
        dataframe["range_width"] = (
            width / dataframe["close"].replace(0, np.nan)
        )

        dataframe["range_pos"] = (
            (dataframe["close"] - dataframe["range_low"])
            / width.replace(0, np.nan)
        ).clip(0, 1)

        # Previous completed range for breakout detection.
        dataframe["prev_range_high"] = (
            dataframe["high"]
            .rolling(lb, min_periods=lb)
            .max()
            .shift(1)
        )
        dataframe["prev_range_low"] = (
            dataframe["low"]
            .rolling(lb, min_periods=lb)
            .min()
            .shift(1)
        )

        candle_range = dataframe["high"] - dataframe["low"]

        median_range = (
            candle_range.rolling(12, min_periods=12)
            .median()
            .shift(1)
        )
        dataframe["expansion_ratio"] = (
            candle_range / median_range.replace(0, np.nan)
        )

        dataframe["close_location"] = (
            2.0
            * (dataframe["close"] - dataframe["low"])
            / candle_range.replace(0, np.nan)
            - 1.0
        )

        median_volume = (
            dataframe["volume"]
            .rolling(12, min_periods=12)
            .median()
            .shift(1)
        )
        dataframe["volume_ratio"] = (
            dataframe["volume"] / median_volume.replace(0, np.nan)
        )

        # Short-term structure relative to the previous completed candles.
        prev_high = (
            dataframe["high"]
            .rolling(8, min_periods=8)
            .max()
            .shift(1)
        )
        prev_low = (
            dataframe["low"]
            .rolling(8, min_periods=8)
            .min()
            .shift(1)
        )

        dataframe["structure_up"] = (
            dataframe["close"] > prev_high
        ).astype(int)
        dataframe["structure_down"] = (
            dataframe["close"] < prev_low
        ).astype(int)

        # Soft directional bias.
        close_diff = dataframe["close"].diff()
        dataframe["macro_direction"] = (
            close_diff.rolling(6, min_periods=6)
            .sum()
            .apply(
                lambda x: 1 if x > 0 else (-1 if x < 0 else 0),
            )
        )

        return dataframe

    @informative("4h")
    def populate_indicators_4h(
        self, dataframe: DataFrame, metadata: dict
    ) -> DataFrame:
        """
        4h macro structure.

        It is deliberately a soft bias rather than a hard filter.
        """
        lb = self.RANGE_LOOKBACK_4H

        swing_high = (
            dataframe["high"]
            .rolling(lb, min_periods=lb)
            .max()
            .shift(1)
        )
        swing_low = (
            dataframe["low"]
            .rolling(lb, min_periods=lb)
            .min()
            .shift(1)
        )

        dataframe["macro_break_up"] = (
            dataframe["close"] > swing_high
        ).astype(int)
        dataframe["macro_break_down"] = (
            dataframe["close"] < swing_low
        ).astype(int)

        macro_high = dataframe["high"].rolling(
            lb, min_periods=lb
        ).max()
        macro_low = dataframe["low"].rolling(
            lb, min_periods=lb
        ).min()
        macro_width = macro_high - macro_low

        dataframe["macro_pos"] = (
            (dataframe["close"] - macro_low)
            / macro_width.replace(0, np.nan)
        ).clip(0, 1)

        close_diff = dataframe["close"].diff()
        dataframe["macro_direction"] = (
            close_diff.rolling(6, min_periods=6)
            .sum()
            .apply(
                lambda x: 1 if x > 0 else (-1 if x < 0 else 0),
            )
        )

        return dataframe

    def populate_indicators(
        self, dataframe: DataFrame, metadata: dict
    ) -> DataFrame:
        """
        15m trigger engine.

        @informative() safely merges completed 1h/4h data.
        """
        local_range = dataframe["high"] - dataframe["low"]
        dataframe["candle_range"] = local_range
        dataframe["body"] = (
            dataframe["close"] - dataframe["open"]
        ).abs()

        prev_median_range = (
            dataframe["candle_range"]
            .rolling(16, min_periods=16)
            .median()
            .shift(1)
        )
        dataframe["local_expansion"] = (
            dataframe["candle_range"]
            / prev_median_range.replace(0, np.nan)
        )

        dataframe["close_location"] = (
            2.0
            * (dataframe["close"] - dataframe["low"])
            / local_range.replace(0, np.nan)
            - 1.0
        )

        slb = int(self.structure_lookback.value)

        dataframe["local_high"] = (
            dataframe["high"]
            .rolling(slb, min_periods=slb)
            .max()
            .shift(1)
        )
        dataframe["local_low"] = (
            dataframe["low"]
            .rolling(slb, min_periods=slb)
            .min()
            .shift(1)
        )

        dataframe["near_range_low"] = (
            dataframe["range_pos_1h"] <= self.edge_zone.value
        )
        dataframe["near_range_high"] = (
            dataframe["range_pos_1h"]
            >= 1.0 - self.edge_zone.value
        )

        # Use the previous completed 1h range for breakout detection.
        dataframe["breakout_up_1h"] = (
            dataframe["close"]
            > dataframe["prev_range_high_1h"]
            * (1.0 + self.breakout_buffer.value)
        )
        dataframe["breakout_down_1h"] = (
            dataframe["close"]
            < dataframe["prev_range_low_1h"]
            * (1.0 - self.breakout_buffer.value)
        )

        # Price-action rejection.
        dataframe["bull_rejection"] = (
            (dataframe["close_location"] > 0.20)
            & (dataframe["low"] < dataframe["low"].shift(1))
            & (dataframe["close"] > dataframe["open"])
        )
        dataframe["bear_rejection"] = (
            (dataframe["close_location"] < -0.20)
            & (dataframe["high"] > dataframe["high"].shift(1))
            & (dataframe["close"] < dataframe["open"])
        )

        dataframe["local_break_up"] = (
            dataframe["close"] > dataframe["local_high"]
        )
        dataframe["local_break_down"] = (
            dataframe["close"] < dataframe["local_low"]
        )

        # Participation is intentionally soft, not a mandatory gate.
        dataframe["volume_participation"] = (
            dataframe["volume_ratio_1h"].fillna(1.0) > 1.0
        )

        dataframe["macro_bull"] = (
            (dataframe["macro_break_up_4h"] > 0)
            | (dataframe["macro_direction_4h"] > 0)
        )
        dataframe["macro_bear"] = (
            (dataframe["macro_break_down_4h"] > 0)
            | (dataframe["macro_direction_4h"] < 0)
        )

        # Simple transition state:
        # 0 = ordinary range / neutral
        # 1 = expansion / transition
        # 2 = breakout
        dataframe["market_state_1h"] = 0
        dataframe.loc[
            dataframe["expansion_ratio_1h"]
            >= self.expansion_ratio.value,
            "market_state_1h",
        ] = 1
        dataframe.loc[
            dataframe["breakout_up_1h"]
            | dataframe["breakout_down_1h"],
            "market_state_1h",
        ] = 2

        return dataframe

    def populate_entry_trend(
        self, dataframe: DataFrame, metadata: dict
    ) -> DataFrame:
        """
        Score-based entry engine.

        Range Long:
          +2 range-edge location
          +1 bullish rejection
          +1 local recovery
          +1 macro support
          +1 participation

        Breakout Long:
          +2 1h structure break
          +1 local break
          +1 expansion
          +1 participation
          +1 macro support

        Short is symmetric.
        """
        range_long_score = (
            2 * dataframe["near_range_low"].astype(int)
            + dataframe["bull_rejection"].astype(int)
            + (
                (dataframe["close"] > dataframe["open"].shift(1))
                & (dataframe["close"] > dataframe["close"].shift(1))
            ).astype(int)
            + dataframe["macro_bull"].astype(int)
            + dataframe["volume_participation"].astype(int)
        )

        range_short_score = (
            2 * dataframe["near_range_high"].astype(int)
            + dataframe["bear_rejection"].astype(int)
            + (
                (dataframe["close"] < dataframe["open"].shift(1))
                & (dataframe["close"] < dataframe["close"].shift(1))
            ).astype(int)
            + dataframe["macro_bear"].astype(int)
            + dataframe["volume_participation"].astype(int)
        )

        breakout_long_score = (
            2 * dataframe["breakout_up_1h"].astype(int)
            + dataframe["local_break_up"].astype(int)
            + (
                dataframe["local_expansion"]
                >= self.expansion_ratio.value
            ).astype(int)
            + dataframe["volume_participation"].astype(int)
            + dataframe["macro_bull"].astype(int)
        )

        breakout_short_score = (
            2 * dataframe["breakout_down_1h"].astype(int)
            + dataframe["local_break_down"].astype(int)
            + (
                dataframe["local_expansion"]
                >= self.expansion_ratio.value
            ).astype(int)
            + dataframe["volume_participation"].astype(int)
            + dataframe["macro_bear"].astype(int)
        )

        dataframe["range_long_score"] = range_long_score
        dataframe["range_short_score"] = range_short_score
        dataframe["breakout_long_score"] = breakout_long_score
        dataframe["breakout_short_score"] = breakout_short_score

        threshold = int(self.entry_score_threshold.value)

        breakout_long = breakout_long_score >= threshold
        breakout_short = breakout_short_score >= threshold

        # Range trades are disabled once a same-direction breakout is active.
        range_long = (
            (range_long_score >= threshold)
            & ~breakout_long
            & (dataframe["range_pos_1h"] < 0.50)
            & ~dataframe["breakout_down_1h"]
        )
        range_short = (
            (range_short_score >= threshold)
            & ~breakout_short
            & (dataframe["range_pos_1h"] > 0.50)
            & ~dataframe["breakout_up_1h"]
        )

        valid = dataframe["volume"] > 0

        dataframe.loc[
            breakout_long & valid,
            ["enter_long", "enter_tag"],
        ] = (1, "breakout_long")

        dataframe.loc[
            range_long & valid,
            ["enter_long", "enter_tag"],
        ] = (1, "range_long")

        dataframe.loc[
            breakout_short & valid,
            ["enter_short", "enter_tag"],
        ] = (1, "breakout_short")

        dataframe.loc[
            range_short & valid,
            ["enter_short", "enter_tag"],
        ] = (1, "range_short")

        return dataframe

    def populate_exit_trend(
        self, dataframe: DataFrame, metadata: dict
    ) -> DataFrame:
        """
        Vectorized structure exits.

        Trade-specific dynamic trailing is handled by custom_stoploss().
        """
        range_exit_long = (
            (dataframe["range_pos_1h"] > 0.70)
            & dataframe["bear_rejection"]
        )
        range_exit_short = (
            (dataframe["range_pos_1h"] < 0.30)
            & dataframe["bull_rejection"]
        )

        # Breakout failure means price re-enters the old completed range.
        breakout_failure_long = (
            dataframe["breakout_up_1h"].shift(1).fillna(False)
            & (dataframe["close"] < dataframe["prev_range_high_1h"])
        )
        breakout_failure_short = (
            dataframe["breakout_down_1h"].shift(1).fillna(False)
            & (dataframe["close"] > dataframe["prev_range_low_1h"])
        )

        long_exit = range_exit_long | breakout_failure_long
        short_exit = range_exit_short | breakout_failure_short

        dataframe.loc[long_exit, ["exit_long", "exit_tag"]] = (
            1,
            "structure_exit_long",
        )
        dataframe.loc[short_exit, ["exit_short", "exit_tag"]] = (
            1,
            "structure_exit_short",
        )

        return dataframe

    def _get_last_candle(
        self, pair: str
    ) -> Optional[pd.Series]:
        if not self.dp:
            return None

        dataframe, _ = self.dp.get_analyzed_dataframe(
            pair, self.timeframe
        )

        if dataframe is None or dataframe.empty:
            return None

        return dataframe.iloc[-1]

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
        Structure-based stop.

        Range:
            stop beyond the opposite range edge.

        Breakout:
            stop behind recent local structure.

        Freqtrade only accepts stoploss updates that move in the
        favorable direction.
        """
        candle = self._get_last_candle(pair)

        if (
            candle is None
            or not np.isfinite(current_rate)
            or current_rate <= 0
        ):
            return None

        tag = (trade.enter_tag or "").lower()
        is_breakout = "breakout" in tag

        if trade.is_short:
            if is_breakout:
                stop_price = (
                    candle["local_high"]
                    * (1.0 + self.breakout_stop_buffer)
                )
            else:
                stop_price = (
                    candle["range_high_1h"]
                    * (1.0 + self.range_stop_buffer)
                )

            if (
                not np.isfinite(stop_price)
                or stop_price <= current_rate
            ):
                return None

        else:
            if is_breakout:
                stop_price = (
                    candle["local_low"]
                    * (1.0 - self.breakout_stop_buffer)
                )
            else:
                stop_price = (
                    candle["range_low_1h"]
                    * (1.0 - self.range_stop_buffer)
                )

            if (
                not np.isfinite(stop_price)
                or stop_price >= current_rate
            ):
                return None

        return stoploss_from_absolute(
            stop_price,
            current_rate=current_rate,
            is_short=trade.is_short,
            leverage=trade.leverage,
        )

    def custom_exit(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        **kwargs,
    ):
        """
        Additional trade-specific breakout failure exit.

        Breakout trades are allowed to run while structure remains intact.
        """
        candle = self._get_last_candle(pair)

        if candle is None:
            return None

        tag = (trade.enter_tag or "").lower()

        if "breakout" in tag:
            if not trade.is_short:
                if (
                    current_profit > 0.01
                    and np.isfinite(candle["local_low"])
                    and current_rate < candle["local_low"]
                ):
                    return "breakout_structure_failure"

            else:
                if (
                    current_profit > 0.01
                    and np.isfinite(candle["local_high"])
                    and current_rate > candle["local_high"]
                ):
                    return "breakout_structure_failure"

        return None

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
        Approximate risk-aware sizing.

        v1 intentionally uses the bot's proposed stake as the capital ceiling.
        This avoids depending on wallet internals while keeping the position
        smaller when the structural stop is farther away.
        """
        candle = self._get_last_candle(pair)

        if (
            candle is None
            or current_rate <= 0
            or not np.isfinite(current_rate)
        ):
            return proposed_stake

        tag = (entry_tag or "").lower()
        is_breakout = "breakout" in tag

        if side == "short":
            if is_breakout:
                stop_price = (
                    candle["local_high"]
                    * (1.0 + self.breakout_stop_buffer)
                )
            else:
                stop_price = (
                    candle["range_high_1h"]
                    * (1.0 + self.range_stop_buffer)
                )
        else:
            if is_breakout:
                stop_price = (
                    candle["local_low"]
                    * (1.0 - self.breakout_stop_buffer)
                )
            else:
                stop_price = (
                    candle["range_low_1h"]
                    * (1.0 - self.range_stop_buffer)
                )

        if (
            not np.isfinite(stop_price)
            or stop_price <= 0
        ):
            return proposed_stake

        stop_distance = (
            abs(current_rate - stop_price) / current_rate
        )

        # Avoid pathological position sizes.
        stop_distance = float(
            np.clip(
                stop_distance,
                0.005,
                abs(self.stoploss),
            )
        )

        risk_budget = (
            proposed_stake
            * float(self.risk_per_trade.value)
        )
        # Account for leverage so the risk-per-trade budget stays meaningful
        # while leverage multiplies the notional position.
        stake = risk_budget / (stop_distance * max(float(leverage), 1.0))

        if min_stake is not None:
            stake = max(stake, min_stake)

        return float(
            np.clip(
                stake,
                min_stake or 0.0,
                max_stake,
            )
        )

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
        v2 uses 1x leverage by default; leverage is a config/override decision.
        """
        return min(1.0, max_leverage)
