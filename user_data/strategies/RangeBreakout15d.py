"""
RangeBreakout15d — AlphaPilot「15 日区间突破」的 freqtrade 落地版

═══════════════════════════════════════════════════════════════════════
规则直接照抄 AlphaPilot 策略页（2026-11-09 抓取），研究结论见
`策略池/废池/15日区间突破/`（02 判定：不通过；03 freqtrade 回测：4h 5bp −5.65%，
日线对照年化 10.4% 且利润集中在 2025 一年。不建议实盘）。

  因子   rp = (close − min(close,90)) / (max(close,90) − min(close,90))
         4h K 线 90 根 = 15 天；端点用 close；**含当前 bar**
  池子   本地 95 个 U 本位永续，剔除稳定币/法币；按 30 日成交额取前 50
         （每腿 10 个，正好填满 20 个持仓槽位）
  调仓   每周一 00:00 UTC。信号在周日 20:00 那根 4h（本周最后一根）上算，
         freqtrade 在下一根开盘（周一 00:00）成交 —— 与原文同时点
  组合   前 20% 做多、后 20% 做空，腿内等权，两侧各 50% 资金，1× 杠杆
  出场   只在周度调仓时按排名平仓/换仓；没有止损（兜底 stoploss 收到 −99%）
  成本   回测用 --fee 指定；原文口径 3bp 单边，币安实际 taker 5bp

═══════════════════════════════════════════════════════════════════════
⚠️ 先把丑话说在前面（报告 02 第六节）：

1. **这条策略在诚实池上不通过**：全历史 Sharpe 0.95 / 回撤 −46%，
   多头腿 Sharpe 0.16 / 回撤 −97.5%，现货多头 −8%/年。
   收益本质是「做空正在崩盘的币」，不是「区间突破」。
2. **本地 95 个永续是幸存者偏差口径**（只含今天还在交易的币），
   回测数字会明显好于报告 02 里的诚实池，**只能当行为验证，不是收益预期**。
3. 容量：和横截面动量同源，$1–5M 量级；不要拿它管大钱。
4. `freqtrade lookahead-analysis` 对横截面策略必误报（只加载单个 pair），
   前视自查请用「截断区间重跑、重叠交易逐笔比对」的方法（见 XsecMomentum）。

═══════════════════════════════════════════════════════════════════════
回测命令：

  freqtrade backtesting -s RangeBreakout15d -c user_data/config/config_xsec.json \
    -i 4h --timerange 20230101-20261030 --fee 0.0003 --export trades

  # 真实费率
  freqtrade backtesting -s RangeBreakout15d -c user_data/config/config_xsec.json \
    -i 4h --timerange 20230101-20261030 --fee 0.0005 --export trades

═══════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from pandas import DataFrame

from freqtrade.enums import RunMode
from freqtrade.exchange import timeframe_to_prev_date
from freqtrade.strategy import IStrategy

logger = logging.getLogger(__name__)


class RangeBreakout15d(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = "4h"
    can_short = True

    # ── 原文定死的参数
    WINDOW = 90               # 4h × 90 = 15 天
    Q = 0.20                  # 每条腿的横截面分位
    TOP_N = 50                # 池子（按 30 日成交额），每腿 10 个 = 20 个槽位
    ADV_WINDOW = 180          # 180 根 4h = 30 天
    ADV_MIN = 1e6
    MIN_RANGE = 0.005         # 区间宽度 / close < 0.5% 视为退化（稳定币/一字板）
    REB_HOUR: int | None = 20  # 周日 20:00 这根 4h 是本周最后一根

    # 稳定币 / 法币：区间宽度趋零，排名没有意义
    EXCLUDE_BASE = frozenset({
        "USDC", "BUSD", "TUSD", "USDP", "DAI", "FDUSD", "PAX", "USDD", "USDS",
        "EURC", "USD1", "UST", "USTC", "GUSD", "LUSD", "FRAX", "PYUSD",
        "EUR", "GBP", "TRY", "BRL", "AUD", "JPY", "ZAR", "RUB", "UAH", "NGN",
    })

    # ── 框架参数
    startup_candle_count = 200
    minimal_roi = {"0": 100.0}   # 实质关闭 ROI：出场完全由周度排名决定
    stoploss = -0.99             # 原文没有止损；−99% 只是满足框架的兜底
    trailing_stop = False
    use_custom_stoploss = False

    # 面板缓存（实例级）
    _panel_key: str | None = None
    _signals: dict[str, DataFrame] | None = None

    # ═══════════════════════════════════════════════ 面板与信号
    def _whitelist(self) -> list[str]:
        try:
            return list(self.dp.current_whitelist())
        except Exception:
            return list(self.config.get("exchange", {}).get("pair_whitelist", []))

    def _build_panel(self) -> tuple[DataFrame, DataFrame]:
        """白名单所有 pair 拼成 (close, 30 日成交额) 两个宽表。

        回测时 get_pair_dataframe 返回完整历史；实盘剔除形成中的当根 K 线，
        避免用残缺的 close 算区间位置。
        """
        live = self.dp.runmode in (RunMode.DRY_RUN, RunMode.LIVE)
        cutoff = (timeframe_to_prev_date(self.timeframe, datetime.now(timezone.utc))
                  if live else None)

        closes, advs = {}, {}
        for pair in self._whitelist():
            df = self.dp.get_pair_dataframe(pair=pair, timeframe=self.timeframe)
            if df is None or df.empty or len(df) < self.WINDOW:
                continue
            if "date" not in df.columns:
                df = df.reset_index()
            if cutoff is not None:
                df = df[df["date"] < cutoff]
            if len(df) < self.WINDOW:
                continue
            s = df.set_index("date")
            closes[pair] = s["close"].astype(float)
            # 本地 feather 没有 quoteVolume；成交额 = 基础币成交量 × 收盘价
            qv = (s["quoteVolume"].astype(float) if "quoteVolume" in s.columns
                  else s["volume"].astype(float) * s["close"].astype(float))
            advs[pair] = qv.rolling(self.ADV_WINDOW,
                                    min_periods=max(10, self.ADV_WINDOW // 2)).mean()

        if not closes:
            return DataFrame(), DataFrame()

        close = DataFrame(closes).sort_index()
        adv = DataFrame(advs).sort_index().reindex(columns=close.columns)
        return close, adv

    def _build_signals(self, close: DataFrame, adv: DataFrame) -> dict[str, DataFrame]:
        """返回 {pair: DataFrame[date, xsec_score, xsec_long, xsec_short]}。"""
        if close.empty:
            return {}

        hi = close.rolling(self.WINDOW, min_periods=self.WINDOW).max()
        lo = close.rolling(self.WINDOW, min_periods=self.WINDOW).min()
        width = hi - lo
        rp = (close - lo) / width.where(width > 0)

        cols = [c for c in close.columns if c.split("/")[0] not in self.EXCLUDE_BASE]
        ok = (rp[cols].notna() & (width[cols] / close[cols] > self.MIN_RANGE)
              & (adv[cols] >= self.ADV_MIN))
        pool = ok & (adv[cols].where(ok).rank(axis=1, ascending=False) <= self.TOP_N)

        score = rp[cols].where(pool)
        rank = score.rank(axis=1, pct=True, ascending=False)   # 1/n = 区间顶

        in_long = (rank <= self.Q).fillna(False)
        in_short = (rank >= 1 - self.Q).fillna(False)

        # 只在「本周最后一根」发信号：下一根开盘 = 周一 00:00，与原文同时点
        reb = (close.index.dayofweek == 6)
        if self.REB_HOUR is not None:
            reb = reb & (close.index.hour == self.REB_HOUR)
        reb_s = pd.Series(reb, index=close.index)
        in_long = in_long.mul(reb_s, axis=0).astype(bool)
        in_short = in_short.mul(reb_s, axis=0).astype(bool)

        out = {}
        for pair in cols:
            out[pair] = DataFrame({
                "date": close.index,
                "xsec_score": score[pair].to_numpy(),
                "xsec_long": in_long[pair].to_numpy(),
                "xsec_short": in_short[pair].to_numpy(),
            })
        return out

    def _get_signals(self) -> dict[str, DataFrame]:
        """回测只建一次（95 个 pair × 95 次 get_pair_dataframe 是 O(N²)）。"""
        if self._signals is not None and self.dp.runmode == RunMode.BACKTEST:
            return self._signals

        close, adv = self._build_panel()
        key = str(close.index.max()) if not close.empty else "empty"
        if key == self._panel_key and self._signals is not None:
            return self._signals
        self._signals = self._build_signals(close, adv)
        self._panel_key = key
        return self._signals

    # ═══════════════════════════════════════════════ 指标
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        pair = metadata["pair"]
        signals = self._get_signals()
        sig = signals.get(pair)

        dataframe["xsec_score"] = np.nan
        dataframe["xsec_long"] = False
        dataframe["xsec_short"] = False

        if sig is None or sig.empty:
            return dataframe

        idx = sig.set_index("date")
        dataframe["xsec_score"] = dataframe["date"].map(idx["xsec_score"])
        long_s = dataframe["date"].map(idx["xsec_long"])
        short_s = dataframe["date"].map(idx["xsec_short"])

        # 实盘：当根是形成中的 K 线，面板里没有它的行 → 用上一根已收线的信号顶上
        if self.dp.runmode in (RunMode.DRY_RUN, RunMode.LIVE) and len(dataframe) >= 2:
            if pd.isna(long_s.iloc[-1]):
                long_s.iloc[-1] = long_s.iloc[-2]
                short_s.iloc[-1] = short_s.iloc[-2]

        dataframe["xsec_long"] = long_s.fillna(False).astype(bool)
        dataframe["xsec_short"] = short_s.fillna(False).astype(bool)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        tradable = dataframe["volume"] > 0
        dataframe.loc[tradable & dataframe["xsec_long"], "enter_long"] = 1
        dataframe.loc[tradable & dataframe["xsec_short"], "enter_short"] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # 只在周度调仓那根 K 线按新排名换仓；周中不因为排名变化平仓
        reb = (dataframe["date"].dt.dayofweek == 6)
        if self.REB_HOUR is not None:
            reb = reb & (dataframe["date"].dt.hour == self.REB_HOUR)
        dataframe.loc[reb & ~dataframe["xsec_long"], "exit_long"] = 1
        dataframe.loc[reb & ~dataframe["xsec_short"], "exit_short"] = 1
        return dataframe

    # ═══════════════════════════════════════════════ 仓位
    def custom_stake_amount(self, pair, current_time, current_rate, proposed_stake,
                            min_stake, max_stake, leverage, entry_tag, side,
                            **kwargs) -> float:
        """1× 总敞口，平均分到每个持仓槽位（20 个槽 = 每笔 5%，每腿 50%）。"""
        try:
            equity = float(self.wallets.get_total_stake_amount())
        except Exception:
            equity = float(proposed_stake) * self.config.get("max_open_trades", 20)
        slots = max(1, int(self.config.get("max_open_trades", 20)))
        stake = equity / slots
        if min_stake:
            stake = max(stake, float(min_stake))
        return min(stake, float(max_stake))

    def leverage(self, pair, current_time, current_rate, proposed_leverage,
                 max_leverage, entry_tag, side, **kwargs) -> float:
        return 1.0


class RangeBreakout15dDaily(RangeBreakout15d):
    """A/B 对照：4h×90 → 日线×15，池子、分位、成本、调仓时点完全不变。

    用途：把「4h 采样」和「95 个幸存者币池」这两个差异拆开。
    如果两者回测接近，说明结果差异主要来自币池（幸存者偏差），
    而不是 4h 与日线的口径 —— 这正是报告 02 里日线近似成立的前提。
    """

    timeframe = "1d"
    WINDOW = 15               # 日线 × 15 = 15 天
    ADV_WINDOW = 30           # 30 天
    REB_HOUR = None           # 日线只有 00:00 一根，周日那根即本周最后一根
