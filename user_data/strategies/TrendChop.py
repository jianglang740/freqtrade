"""
TrendChop — 震荡过滤的日线趋势跟踪（BTC / ETH / SOL 永续）

═══════════════════════════════════════════════════════════════════════
这是 `策略池/AlphaPilot复现/` 里 S5 的落地版。规则和参数全部来自那份复现报告
（01_原始设想.md 第六节、02_复现结果.md 第六节），没有为了回测好看改过任何一个：

    MA14、MA40、σ20(日收益)、ER30 = |P_t − P_{t−30}| / Σ|P_{t−i} − P_{t−i−1}|

  信号  MA14 > MA40 且 close > MA40 → 多
        MA14 < MA40 且 close < MA40 → 空
        其余 → 中性

  规则  信号转向新方向 且 ER30 ≥ 0.12   → 开仓 / 反手
        信号转向但 ER30 < 0.12          → 不开新仓；已有反向仓位平掉
        信号中性且收盘重新穿越 MA40      → 平仓
        信号与持仓同向                  → 持有，不调整仓位

  仓位  w = ±(1/3) × min(1, 0.035/σ20)，三币合计毛敞口 ≤ 100%

═══════════════════════════════════════════════════════════════════════
研究口径下的结果（研究脚本，不是这个 freqtrade 版本）

  原文区间 2023-06 → 2026-10-18：+465.6% / 夏普 1.36 / 回撤 −34.1%
  全历史 2022-01 → 2026-10：    +250.6% / 年化 29.7% / 夏普 0.82 / 回撤 −57.5%
  分年：2022 −26.8% / 2023 +102.9% / 2024 +16.8% / 2025 +64.5% / 2026 +22.9%
  资金费占区间起点净值 −38.6%，是这个策略最大的成本项。

⚠️ 落地必须知道的几件事

1. **SOL 的本地数据从 2022-12-01 才开始**，所以 2021–2022 的「三币」实际只有
   BTC/ETH 两币。研究里的全历史也有同样的限制，不是这里新引入的。
2. **2022 那种年份会亏 −26.8%**。趋势跟踪的假突破年，逻辑上说得通，
   不是参数没调好。不能拿 2023/2025 的收益当预期。
3. **资金费按时代价很大**：多头在牛市付、空头在熊市付，四年下来吃掉约
   38.6% 的初始资金。实盘资金费比这更不可控。
4. freqtrade 的仓位模型是「每笔固定 stake」，这里用 `custom_stake_amount`
   把研究里的净值权重乘上去；总敞口仍是 3 × 1/3 = 100% 上限，1× 杠杆。
5. 反手（多→空）用同一根 K 线同时发 `exit_long` 和 `enter_short` 实现，
   与研究的「转向即反手」一致。

═══════════════════════════════════════════════════════════════════════
回测命令：

  freqtrade backtesting -s TrendChop -c user_data/config/config_trendchop.json \
    -i 1d --timerange 20230601-20261018 --fee 0.0003 --export trades

  # 全历史（SOL 自动从 2022-12 开始）
  freqtrade backtesting -s TrendChop -c user_data/config/config_trendchop.json \
    -i 1d --timerange 20230101-20261030 --fee 0.0003
"""

from __future__ import annotations

import numpy as np
from pandas import DataFrame

from freqtrade.strategy import IStrategy


class TrendChop(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = "1d"
    can_short = True

    # ── 研究定死的参数（改动前先读 02_复现结果.md 第六节）
    FAST = 14
    SLOW = 40
    VOLW = 20
    ERW = 30
    ER_MIN = 0.12
    TARGET_VOL = 0.035
    BASE_W = 1.0 / 3.0

    # ── 框架参数
    startup_candle_count = 60
    minimal_roi = {"0": 100.0}      # 实质关闭 ROI：出场完全由信号决定
    stoploss = -0.99                # 研究没有止损；只留一个兜底
    trailing_stop = False
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False
    position_adjustment_enable = False
    process_only_new_candles = True

    # ═══════════════════════════════════════════════ 指标与状态机
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        c = dataframe["close"]

        ma_fast = c.rolling(self.FAST, min_periods=self.FAST).mean()
        ma_slow = c.rolling(self.SLOW, min_periods=self.SLOW).mean()
        sigma = c.pct_change().rolling(self.VOLW, min_periods=self.VOLW).std()
        move = (c - c.shift(self.ERW)).abs()
        path = c.diff().abs().rolling(self.ERW, min_periods=self.ERW).sum()
        er = move / path.replace(0, np.nan)

        sig = np.zeros(len(dataframe), dtype=int)
        sig[(ma_fast > ma_slow) & (c > ma_slow)] = 1
        sig[(ma_fast < ma_slow) & (c < ma_slow)] = -1

        n = len(dataframe)
        enter_long = np.zeros(n, dtype=bool)
        enter_short = np.zeros(n, dtype=bool)
        exit_long = np.zeros(n, dtype=bool)
        exit_short = np.zeros(n, dtype=bool)
        weight = np.full(n, np.nan)

        c_v = c.to_numpy(dtype=float)
        ma_v = ma_slow.to_numpy(dtype=float)
        er_v = er.to_numpy(dtype=float)
        sig_v = sig
        sigma_v = sigma.to_numpy(dtype=float)

        pos = 0
        for i in range(n):
            if not np.isfinite(ma_v[i]):
                continue
            s = int(sig_v[i])
            new = pos

            if s != 0 and s != pos:
                new = s if (np.isfinite(er_v[i]) and er_v[i] >= self.ER_MIN) else 0
            elif s == 0 and pos != 0:
                if (pos == 1 and c_v[i] < ma_v[i]) or (pos == -1 and c_v[i] > ma_v[i]):
                    new = 0

            if new == pos:
                continue

            # 入场那根是信号 bar；框架会在下一根开盘执行，与研究同构
            if new != 0:
                w = self._weight(sigma_v[i])
                if w <= 0:                      # 波动率不可用 / 为零：不开仓
                    new = 0
            if new == pos:
                continue

            if new == 1:
                enter_long[i] = True
            elif new == -1:
                enter_short[i] = True
            if pos == 1:
                exit_long[i] = True
            elif pos == -1:
                exit_short[i] = True
            if new != 0:
                weight[i] = self._weight(sigma_v[i])
            else:
                weight[i] = 0.0
            pos = new

        dataframe["tc_enter_long"] = enter_long
        dataframe["tc_enter_short"] = enter_short
        dataframe["tc_exit_long"] = exit_long
        dataframe["tc_exit_short"] = exit_short
        # 入场后不重算仓位；这里 ffill 只是为了 custom_stake_amount 能取到
        # 「最近一次入场时的权重」，而不是每根都改。
        dataframe["tc_weight"] = (
            DataFrame({"w": weight}).ffill()["w"].fillna(0.0).to_numpy()
        )
        return dataframe

    def _weight(self, sigma: float) -> float:
        if not np.isfinite(sigma) or sigma <= 0:
            return 0.0
        return self.BASE_W * min(1.0, self.TARGET_VOL / sigma)

    # ═══════════════════════════════════════════════ 入场 / 出场列
    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["enter_long"] = dataframe["tc_enter_long"].astype(int)
        dataframe["enter_short"] = dataframe["tc_enter_short"].astype(int)
        dataframe.loc[dataframe["enter_long"] == 1, "enter_tag"] = "trendchop_long"
        dataframe.loc[dataframe["enter_short"] == 1, "enter_tag"] = "trendchop_short"
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["exit_long"] = dataframe["tc_exit_long"].astype(int)
        dataframe["exit_short"] = dataframe["tc_exit_short"].astype(int)
        return dataframe

    # ═══════════════════════════════════════════════ 仓位
    def _weight_at(self, pair: str, current_time) -> float:
        """取「当前 K 线之前」最近一次入场的权重。绝不用当根（那是未来信息）。"""
        try:
            df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        except Exception:
            return 0.0
        if df is None or df.empty or "tc_weight" not in df.columns:
            return 0.0
        past = df[df["date"] < current_time]
        if past.empty:
            return 0.0
        v = float(past["tc_weight"].iloc[-1])
        return v if np.isfinite(v) and v > 0 else 0.0

    def custom_stake_amount(self, pair, current_time, current_rate, proposed_stake,
                            min_stake, max_stake, leverage, entry_tag, side,
                            **kwargs) -> float:
        w = self._weight_at(pair, current_time)
        if w <= 0:
            return proposed_stake
        try:
            equity = float(self.wallets.get_total_stake_amount())
        except Exception:
            equity = float(proposed_stake) * 3
        stake = equity * w
        if min_stake:
            stake = max(stake, float(min_stake))
        return min(stake, float(max_stake))

    def leverage(self, pair, current_time, current_rate, proposed_leverage,
                 max_leverage, entry_tag, side, **kwargs) -> float:
        # 敞口完全靠 stake 控制，杠杆保持 1×
        return 1.0
