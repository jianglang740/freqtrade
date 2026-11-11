"""
CryptoBreakout — 多空加密货币突破（1h，BTC / ETH 永续）

═══════════════════════════════════════════════════════════════════════
这是 `策略池/AlphaPilot复现/` 里 S1 的落地版。规则来自 01_原始设想.md 第二节：

  做多（全部满足）
    1. 24h 均量 > 7 日均量            vol.rolling(24).mean() > vol.rolling(168).mean()
    2. 未平仓量高于 24h 前           ⚠️ 数据湖没有 OI，按原文允许「无数据则跳过」
    3. ER48 ≥ 0.10
    4. MA72 > MA240
    5. close > MA240
    6. close > 前 36 小时最高价（不含当前 bar）
    7. close ≥ 0.96 × 240h 最高价

  做空为镜像；量能与 ER 条件相同。

  仓位  w = 0.5 × min(1, 0.006/σ24)，σ24 = 过去 24 根 1h 收益的标准差
  出场  多头：close < max(high,24) − 3×ATR24，或 MA72 下穿 MA240
        空头镜像；出场后必须重新满足完整清单才能再入场

═══════════════════════════════════════════════════════════════════════
研究口径下的结果（研究脚本，不是这个 freqtrade 版本）

  原文区间 2025-04 → 2026-10-18：+34.4% / 夏普 1.02 / 回撤 −12.5%
  全历史 2020-01 → 2026-10：    +122.4% / 年化 12.4% / 夏普 0.70 / 回撤 −16.5%
  7 个自然年全部为正；平均毛敞口只有 14%。

⚠️ 落地必须知道的几件事

1. **这不是高收益策略。** 它的价值是「低仓位、低回撤、和趋势策略低相关」，
   7 年年化 12.4%，不要拿原文的 +101% 当预期。
2. **OI 过滤缺失**是已知偏差，原文允许无数据时跳过；入场质量可能因此变差。
3. 原文区间只有 1.5 年，全历史才是更应该看的样本。
4. freqtrade 的仓位用 `custom_stake_amount` 按净值权重给；研究里 w 最大 0.5，
   两个币合计毛敞口 ≤ 100%，这里保持 1× 杠杆。
5. 出场与入场在同一根 bar 同时成立时，这里**抑制入场信号**，
   避免「平仓后同一开盘立刻反手」，与研究的状态机不一致。

═══════════════════════════════════════════════════════════════════════
回测命令：

  freqtrade backtesting -s CryptoBreakout -c user_data/config/config_breakout.json \
    -i 1h --timerange 20250401-20261018 --fee 0.0003 --export trades

  freqtrade backtesting -s CryptoBreakout -c user_data/config/config_breakout.json \
    -i 1h --timerange 20200101-20261018 --fee 0.0003
"""

from __future__ import annotations

import numpy as np
from pandas import DataFrame

from freqtrade.strategy import IStrategy


class CryptoBreakout(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = "1h"
    can_short = True

    # ── 研究定死的参数（改动前先读 02_复现结果.md 第二节）
    ER_MIN = 0.10
    W_MAX = 0.5
    VOL_TARGET = 0.006

    # ── 框架参数
    startup_candle_count = 300
    minimal_roi = {"0": 100.0}      # 出场完全由突破/均线规则决定
    stoploss = -0.99                # 研究没有止损；只留一个兜底
    trailing_stop = False
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False
    position_adjustment_enable = False
    process_only_new_candles = True

    # ═══════════════════════════════════════════════ 指标与信号
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        c, h, l, v = (dataframe["close"], dataframe["high"],
                      dataframe["low"], dataframe["volume"])

        ma72 = c.rolling(72, min_periods=72).mean()
        ma240 = c.rolling(240, min_periods=240).mean()
        vol_ok = (v.rolling(24, min_periods=24).mean()
                  > v.rolling(168, min_periods=168).mean())
        er48 = ((c - c.shift(48)).abs()
                / c.diff().abs().rolling(48, min_periods=48).sum().replace(0, np.nan))
        hi36 = h.rolling(36, min_periods=36).max().shift(1)
        lo36 = l.rolling(36, min_periods=36).min().shift(1)
        hi240 = h.rolling(240, min_periods=240).max()
        lo240 = l.rolling(240, min_periods=240).min()
        hi24 = h.rolling(24, min_periods=24).max()
        lo24 = l.rolling(24, min_periods=24).min()

        tr = DataFrame({"a": h - l, "b": (h - c.shift()).abs(),
                        "c": (l - c.shift()).abs()}).max(axis=1)
        atr24 = tr.rolling(24, min_periods=24).mean()
        sigma24 = c.pct_change().rolling(24, min_periods=24).std()

        common = vol_ok & er48.ge(self.ER_MIN)
        long_ok = (common & (ma72 > ma240) & (c > ma240)
                   & (c > hi36) & (c >= 0.96 * hi240))
        short_ok = (common & (ma72 < ma240) & (c < ma240)
                    & (c < lo36) & (c <= 1.04 * lo240))

        exit_long = (c < hi24 - 3 * atr24) | (ma72 < ma240)
        exit_short = (c > lo24 + 3 * atr24) | (ma72 > ma240)

        # 同一根 bar 上出场与入场同时成立时，抑制入场（见 docstring 第 5 条）
        dataframe["s1_enter_long"] = (long_ok & ~exit_long).fillna(False).astype(bool)
        dataframe["s1_enter_short"] = (short_ok & ~exit_short).fillna(False).astype(bool)
        dataframe["s1_exit_long"] = exit_long.fillna(False).astype(bool)
        dataframe["s1_exit_short"] = exit_short.fillna(False).astype(bool)

        w = self.W_MAX * np.minimum(1.0, self.VOL_TARGET / sigma24)
        dataframe["s1_weight"] = w.where(np.isfinite(w) & (w > 0), 0.0).fillna(0.0)
        return dataframe

    # ═══════════════════════════════════════════════ 入场 / 出场列
    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["enter_long"] = dataframe["s1_enter_long"].astype(int)
        dataframe["enter_short"] = dataframe["s1_enter_short"].astype(int)
        dataframe.loc[dataframe["enter_long"] == 1, "enter_tag"] = "breakout_long"
        dataframe.loc[dataframe["enter_short"] == 1, "enter_tag"] = "breakout_short"
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["exit_long"] = dataframe["s1_exit_long"].astype(int)
        dataframe["exit_short"] = dataframe["s1_exit_short"].astype(int)
        return dataframe

    # ═══════════════════════════════════════════════ 仓位
    def _weight_at(self, pair: str, current_time) -> float:
        """取「当前 K 线之前」那一根的权重。绝不用当根，那是未来信息。"""
        try:
            df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        except Exception:
            return 0.0
        if df is None or df.empty or "s1_weight" not in df.columns:
            return 0.0
        past = df[df["date"] < current_time]
        if past.empty:
            return 0.0
        v = float(past["s1_weight"].iloc[-1])
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
            equity = float(proposed_stake) * 2
        stake = equity * w
        if min_stake:
            stake = max(stake, float(min_stake))
        return min(stake, float(max_stake))

    def leverage(self, pair, current_time, current_rate, proposed_leverage,
                 max_leverage, entry_tag, side, **kwargs) -> float:
        return 1.0
