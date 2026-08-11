"""
VolatilitySqueezeHARStrategy — 三层波动率建模短线策略

═══════════════════════════════════════════════════════════════════════════════
架构：L1 估计 → L2 预测 → L3 状态机 → 非线性入场 → 波动率曲面风控

L1 估计层：Yang-Zhang Estimator + Parkinson Range
  无偏估计，对开盘跳空和日内 range 同时敏感，效率比 ATR 高 5-8 倍。

L2 预测层：HAR-RV（Heterogeneous AutoRegressive Realized Volatility）
  融合 1m / 5m / 1h 三个时间尺度的已实现方差，预测下一根 K 线的波动率。

L3 状态层：滚动 Z-Score Regime Detection
  120 期滚动分布 → 实时判定波动率处于压抑/正常/释放/极端 四种状态。

入场逻辑：
  压抑期(Z<-1.2) + HAR 预测释放(>1.15x) + VWAP 方向过滤 + Range 突破确认

风控：
  波动率自适应止损（基于 HAR 预测值）
  动态追踪止盈（盈利后收紧）
  超时退出（避免在无效 setup 中耗死）
  极端波动过滤（Z>2 禁止新开仓）

═══════════════════════════════════════════════════════════════════════════════
"""

import numpy as np
from datetime import datetime
from typing import Optional

from freqtrade.persistence import Trade
from freqtrade.strategy import IStrategy, DecimalParameter, IntParameter
from pandas import DataFrame


class VolatilitySqueezeHARStrategy(IStrategy):

    # ═══════════════════════════════════════════════════════════════════════════
    # 基础设置
    # ═══════════════════════════════════════════════════════════════════════════
    timeframe = "1m"
    can_short = True
    startup_candle_count = 500  # HAR-RV 1h 分量需要约 300 根 1m K线预热

    # 止盈止损（会被 custom_stoploss / custom_exit 覆盖，这里设宽作为兜底）
    minimal_roi = {"0": 0.99}
    stoploss = -0.10
    use_exit_signal = True
    use_custom_stoploss = True

    # ═══════════════════════════════════════════════════════════════════════════
    # 超参（可在 hyperopt 中优化）
    # ═══════════════════════════════════════════════════════════════════════════
    # L1 估计层参数
    yz_window = IntParameter(10, 30, default=14, space="buy")
    parkinson_window = IntParameter(10, 30, default=14, space="buy")

    # L2 HAR-RV 参数
    har_w1m = DecimalParameter(0.1, 0.5, default=0.30, decimals=2, space="buy")
    har_w5m = DecimalParameter(0.2, 0.6, default=0.40, decimals=2, space="buy")
    har_w1h = DecimalParameter(0.1, 0.5, default=0.30, decimals=2, space="buy")
    har_release_threshold = DecimalParameter(
        1.05, 1.30, default=1.15, decimals=2, space="buy"
    )

    # L3 状态机参数
    regime_lookback = IntParameter(60, 240, default=120, space="buy")
    squeeze_zscore = DecimalParameter(
        -2.0, -0.5, default=-1.2, decimals=1, space="buy"
    )
    extreme_zscore = DecimalParameter(1.5, 3.0, default=2.0, decimals=1, space="buy")

    # 入场确认参数
    vwap_window = IntParameter(10, 50, default=20, space="buy")
    range_breakout_mult = DecimalParameter(
        1.0, 3.0, default=1.5, decimals=1, space="buy"
    )
    min_squeeze_duration = IntParameter(3, 10, default=5, space="buy")

    # 风控参数
    sl_atr_mult = DecimalParameter(0.8, 2.0, default=1.2, decimals=1, space="buy")
    tp_atr_mult = DecimalParameter(2.0, 5.0, default=3.0, decimals=1, space="buy")
    trailing_activate = DecimalParameter(
        0.5, 2.0, default=1.0, decimals=1, space="buy"
    )
    trailing_sl_mult = DecimalParameter(1.5, 3.0, default=2.0, decimals=1, space="buy")
    max_hold_minutes = IntParameter(15, 60, default=30, space="buy")
    timeout_profit_floor = DecimalParameter(
        0.0, 0.02, default=0.005, decimals=3, space="buy"
    )

    # ═══════════════════════════════════════════════════════════════════════════
    # L1 估计层：Yang-Zhang Estimator
    # ═══════════════════════════════════════════════════════════════════════════
    def _yang_zhang_vol(self, df: DataFrame, window: int) -> DataFrame:
        """
        Yang-Zhang (2000) 波动率估计器 —— 综合了 Rogers-Satchell + 隔夜跳空。
        对开盘跳空和日内范围同时敏感，近似无偏。
        """
        log_ho = np.log(df["high"] / df["open"])
        log_lo = np.log(df["low"] / df["open"])
        log_co = np.log(df["close"] / df["open"])
        log_oc = np.log(df["open"] / df["close"].shift(1))

        # Rogers-Satchell 方差（不需要漂移假设）
        rs = log_ho * (log_ho - log_co) + log_lo * (log_lo - log_co)

        # 隔夜跳空方差
        overnight = log_oc ** 2

        # 收盘-收盘方差
        close_to_close = np.log(df["close"] / df["close"].shift(1)) ** 2

        # Yang-Zhang 权重常数
        k = 0.34 / (1.34 + (window + 1) / (window - 1))

        yz_var = overnight + k * rs + (1 - k) * close_to_close
        yz_vol = np.sqrt(yz_var.rolling(window=window, min_periods=1).mean())

        return yz_vol

    def _parkinson_vol(self, df: DataFrame, window: int) -> DataFrame:
        """
        Parkinson (1980) 波动率估计器 —— 仅用 high/low，对日内波动敏感。
        作为 YZ 的补充验证。
        """
        log_hl = np.log(df["high"] / df["low"])
        # 理论上 Parkinson 是 1/(4N*ln2) * sum(log_hl^2)
        pk_var = (log_hl ** 2).rolling(window=window, min_periods=1).mean() / (
            4 * np.log(2)
        )
        pk_vol = np.sqrt(pk_var)
        return pk_vol

    # ═══════════════════════════════════════════════════════════════════════════
    # L2 预测层：HAR-RV
    # ═══════════════════════════════════════════════════════════════════════════
    def _har_rv_predict(self, df: DataFrame) -> DataFrame:
        """
        HAR-RV: 异质自回归已实现波动率模型。
        融合三个时间尺度：
          - RV_d: 高频（5 根 1m K线，约 5m 聚合）
          - RV_w: 中频（25 根 1m K线，约 25m 聚合）
          - RV_m: 低频（300 根 1m K线，约 5h 聚合）

        预测公式: RV_pred = w1*RV_d(t-1) + w2*RV_w(t-1) + w3*RV_m(t-1)
        """
        # 对数收益率
        log_ret = np.log(df["close"] / df["close"].shift(1))

        # 三个尺度的已实现方差（ squared log-return 的滚动平均）
        rv_d = (log_ret ** 2).rolling(window=5, min_periods=1).mean()    # ~5m
        rv_w = (log_ret ** 2).rolling(window=25, min_periods=1).mean()   # ~25m
        rv_m = (log_ret ** 2).rolling(window=300, min_periods=1).mean()  # ~5h

        # HAR-RV 预测（滞后一期）
        har_pred = (
            self.har_w1m.value * rv_d.shift(1)
            + self.har_w5m.value * rv_w.shift(1)
            + self.har_w1h.value * rv_m.shift(1)
        )

        # 转换为年化波动率（%），便于直观理解
        # 1m RV 的年化: sqrt(RV) * sqrt(365*24*60) ≈ sqrt(RV) * 2285
        har_pred_annual = np.sqrt(har_pred) * 2285.0
        rv_d_annual = np.sqrt(rv_d) * 2285.0

        return har_pred_annual, rv_d_annual

    # ═══════════════════════════════════════════════════════════════════════════
    # L3 状态层：Regime Detection
    # ═══════════════════════════════════════════════════════════════════════════
    def _regime_zscore(self, series: DataFrame, lookback: int) -> DataFrame:
        """
        滚动 Z-Score 状态识别。
        Z < -1.2 → 波动率压抑（Squeeze）
        -1.2 < Z < 1.0 → 正常
        1.0 < Z < 2.0 → 波动率释放
        Z > 2.0 → 极端波动（禁止新开仓）
        """
        rolling_mean = series.rolling(window=lookback, min_periods=1).mean()
        rolling_std = series.rolling(window=lookback, min_periods=1).std()
        # 避免除零
        rolling_std = rolling_std.replace(0, np.nan)
        zscore = (series - rolling_mean) / rolling_std
        return zscore.fillna(0)

    def _squeeze_duration(self, df: DataFrame, zscore_col: str, threshold: float) -> DataFrame:
        """
        统计当前处于 Squeeze 状态的连续 K 线数。
        用于确认 Squeeze 已经持续了一段时间（过滤瞬时噪音）。
        """
        is_squeeze = df[zscore_col] < threshold
        # 连续计数: 每次中断归零
        duration = is_squeeze.astype(int).groupby(
            (is_squeeze != is_squeeze.shift()).cumsum()
        ).cumsum() * is_squeeze.astype(int)
        return duration

    # ═══════════════════════════════════════════════════════════════════════════
    # 主指标计算
    # ═══════════════════════════════════════════════════════════════════════════
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:

        # ── L1: 波动率估计 ──
        dataframe["yz_vol"] = self._yang_zhang_vol(dataframe, self.yz_window.value)
        dataframe["pk_vol"] = self._parkinson_vol(dataframe, self.parkinson_window.value)

        # 综合波动率估计（YZ 为主，Parkinson 为交叉验证）
        dataframe["composite_vol"] = (dataframe["yz_vol"] + dataframe["pk_vol"]) / 2

        # ── L2: HAR-RV 预测 ──
        dataframe["har_pred"], dataframe["rv_current"] = self._har_rv_predict(dataframe)

        # 预测释放信号: HAR 预测 > 当前 RV × 阈值
        dataframe["har_release"] = (
            dataframe["har_pred"] > dataframe["rv_current"] * self.har_release_threshold.value
        )

        # ── L3: 状态识别 ──
        dataframe["vol_zscore"] = self._regime_zscore(
            dataframe["composite_vol"], self.regime_lookback.value
        )

        # Squeeze 持续时长计数
        dataframe["squeeze_duration"] = self._squeeze_duration(
            dataframe, "vol_zscore", self.squeeze_zscore.value
        )

        # 波动率状态标签（用于分析，非信号）
        conditions = [
            dataframe["vol_zscore"] < self.squeeze_zscore.value,
            (dataframe["vol_zscore"] >= self.squeeze_zscore.value)
            & (dataframe["vol_zscore"] < 1.0),
            (dataframe["vol_zscore"] >= 1.0) & (dataframe["vol_zscore"] < self.extreme_zscore.value),
            dataframe["vol_zscore"] >= self.extreme_zscore.value,
        ]
        choices = ["squeeze", "normal", "expanding", "extreme"]
        dataframe["vol_regime"] = np.select(conditions, choices, default="normal")

        # ── 微观结构指标 ──
        # 短期 VWAP
        typical_price = (dataframe["high"] + dataframe["low"] + dataframe["close"]) / 3
        vol_sum = dataframe["volume"].rolling(self.vwap_window.value, min_periods=1).sum()
        tp_vol = (typical_price * dataframe["volume"]).rolling(
            self.vwap_window.value, min_periods=1
        ).sum()
        dataframe["vwap_short"] = tp_vol / vol_sum.replace(0, np.nan)

        # 当前 K 线 Range 相对 HAR 预测的比率
        # 把 HAR 年化波动率转回 1m 价格百分比尺度
        har_1m_pct = dataframe["har_pred"] / 2285.0  # 反年化
        current_range = (dataframe["high"] - dataframe["low"]) / dataframe["close"].shift(1)
        dataframe["range_ratio"] = current_range / har_1m_pct.replace(0, np.nan)

        # 趋势方向（5m EMA 斜率）
        dataframe["ema5"] = dataframe["close"].ewm(span=5, adjust=False).mean()
        dataframe["ema20"] = dataframe["close"].ewm(span=20, adjust=False).mean()
        dataframe["trend_up"] = dataframe["ema5"] > dataframe["ema20"]

        # 成交量相对近期平均（突破需要放量）
        vol_sma = dataframe["volume"].rolling(20, min_periods=1).mean()
        dataframe["vol_ratio"] = dataframe["volume"] / vol_sma.replace(0, np.nan)

        # 波动率自适应止损宽度（HAR 预测值映射为价格百分比）
        # har_pred 是年化 %，转 1m: har_pred / 2285
        dataframe["sl_width"] = (
            dataframe["har_pred"] / 2285.0 * self.sl_atr_mult.value
        )
        dataframe["tp_width"] = (
            dataframe["har_pred"] / 2285.0 * self.tp_atr_mult.value
        )

        return dataframe

    # ═══════════════════════════════════════════════════════════════════════════
    # 入场信号
    # ═══════════════════════════════════════════════════════════════════════════
    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["enter_long"] = 0
        dataframe["enter_short"] = 0

        # ── 基础过滤 ──
        # 1. 不在极端波动状态（Z > extreme_zscore）
        not_extreme = dataframe["vol_zscore"] < self.extreme_zscore.value

        # 2. Squeeze 已持续足够时间
        squeeze_ok = dataframe["squeeze_duration"] >= self.min_squeeze_duration.value

        # 3. HAR 预测即将释放
        har_release = dataframe["har_release"]

        # 4. Range 突破确认（当前 K 线波动 > HAR 预测 × 倍数）
        range_breakout = dataframe["range_ratio"] > self.range_breakout_mult.value

        # 5. 成交量确认（突破要放量）
        volume_confirm = dataframe["vol_ratio"] > 1.2

        # ── Long Setup ──
        long_setup = (
            not_extreme
            & squeeze_ok
            & har_release
            & range_breakout
            & volume_confirm
            & dataframe["trend_up"]
            & (dataframe["close"] > dataframe["vwap_short"])
        )

        # ── Short Setup ──
        short_setup = (
            not_extreme
            & squeeze_ok
            & har_release
            & range_breakout
            & volume_confirm
            & (~dataframe["trend_up"])
            & (dataframe["close"] < dataframe["vwap_short"])
        )

        dataframe.loc[long_setup, ["enter_long", "enter_tag"]] = (1, "vol_squeeze_long")
        dataframe.loc[short_setup, ["enter_short", "enter_tag"]] = (1, "vol_squeeze_short")

        return dataframe

    # ═══════════════════════════════════════════════════════════════════════════
    # 基础出场信号（作为兜底，优先级低于 custom_exit / custom_stoploss）
    # ═══════════════════════════════════════════════════════════════════════════
    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["exit_long"] = 0
        dataframe["exit_short"] = 0

        # 如果波动率状态从 expanding 转为 normal/squeeze 且已有盈利，可考虑退出
        # 这里不主动给信号，交给 custom 回调处理
        return dataframe

    # ═══════════════════════════════════════════════════════════════════════════
    # 回调 1：波动率自适应动态止损
    # ═══════════════════════════════════════════════════════════════════════════
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
        动态止损：
          - 亏损阶段：止损宽度 = HAR 预测 × sl_atr_mult（波动率大则宽，波动率小则紧）
          - 盈利 > trailing_activate × tp_width：启动追踪止损
            追踪止损 = 最高盈利点 - trailing_sl_mult × sl_width
          - 盈利 > 2× tp_width：直接收紧到保本 + 0.5%
        """
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if dataframe.empty:
            return self.stoploss

        # 取最后一根 K 线的指标值
        last_candle = dataframe.iloc[-1]
        sl_width = last_candle["sl_width"]
        tp_width = last_candle["tp_width"]

        # 盈利阶段的分级保护
        if current_profit > 2 * tp_width:
            # 大幅盈利：保本 + 0.5%
            return -0.005

        if current_profit > self.trailing_activate.value * tp_width:
            # 启动追踪止损：从最高盈利点回撤 trailing_sl_mult × sl_width
            # freqtrade 的 trailing stop 机制会自动处理，这里给更紧的值
            return -max(sl_width * self.trailing_sl_mult.value, 0.01)

        # 默认：使用 HAR 预测的自适应止损宽度
        return -max(sl_width, 0.005)

    # ═══════════════════════════════════════════════════════════════════════════
    # 回调 2：超时退出 + 盈利保护
    # ═══════════════════════════════════════════════════════════════════════════
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
        1. 超时退出：持仓超过 max_hold_minutes 且盈利 < timeout_profit_floor → 不耗了
        2. 盈利目标：current_profit > tp_width → 目标止盈
        3. 波动率崩溃退出：vol_zscore 从 expanding 跌回 normal 且盈利为正
        """
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if dataframe.empty:
            return None

        # ── 超时退出 ──
        hold_minutes = (current_time - trade.open_date_utc).total_seconds() / 60
        if hold_minutes > self.max_hold_minutes.value and current_profit < self.timeout_profit_floor.value:
            return "hold_timeout"

        # ── 目标止盈 ──
        last_candle = dataframe.iloc[-1]
        tp_width = last_candle["tp_width"]
        if current_profit > tp_width:
            return "target_profit"

        # ── 波动率崩溃退出 ──
        # 如果波动率 Z-Score 从高位回落到正常区间且已有盈利，锁定利润
        if len(dataframe) >= 3:
            recent_z = dataframe["vol_zscore"].iloc[-3:]
            if (
                recent_z.iloc[0] > 1.0
                and recent_z.iloc[-1] < 0.5
                and current_profit > 0.01
            ):
                return "vol_collapse_exit"

        return None

    # ═══════════════════════════════════════════════════════════════════════════
    # 回调 3：入场前确认（波动率极端时拒绝交易）
    # ═══════════════════════════════════════════════════════════════════════════
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
        最后一道防线：
        - 如果当前 composite_vol Z-Score > extreme_zscore，拒绝入场
        - 避免在波动率已经爆炸的时候追单
        """
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if dataframe.empty:
            return True

        current_z = dataframe["vol_zscore"].iloc[-1]
        if current_z > self.extreme_zscore.value:
            return False

        return True
