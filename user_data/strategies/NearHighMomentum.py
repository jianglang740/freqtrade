"""
NearHighMomentum — 近高点动量多空（日线，动态流动性池，每 5 天调仓）

═══════════════════════════════════════════════════════════════════════
这是 `策略池/AlphaPilot复现/` 里 S3 的落地版。规则来自 01_原始设想.md 第四节、
02_复现结果.md 第四节：

    score = rank_pct(close / max(high, 30d))
          + rank_pct( (close/close[t-14] - 1) / std_14(日收益) )

    每 5 个自然日调仓一次：做多 score 前 5、做空后 5，
    每仓名义额 = 净值 20%（原文 10% × 2 倍杠杆），毛敞口 200%
    可打分币种 < 10 时跳过该次调仓
    收盘决策、次日开盘成交，单边 3bp，真实资金费

  ⚠️ 相位：研究从 anchor 起每 5 天一次，anchor = 回测起点。
     所以 `--timerange 20250101-...` 和 `20200101-...` 的调仓相位不同，
     结果不能互相比（研究里相位挪 0~4 天，收益在 131%~1141% 之间跳）。

  币池（点时，每个决策日重算）：
    14 日 ADV 前 50 / ADV ≥ 1 亿 / 上市满 365 天

═══════════════════════════════════════════════════════════════════════
研究口径下的结果（研究脚本，不是这个 freqtrade 版本）

  原文区间 2025-01-01 → 2026-10-20：+477.03% / 年化 160.94% / 夏普 1.54 / 回撤 −62.98%
  全历史   2020-01-01 → 2026-10-20：+12,203% / 年化 102.25% / 夏普 1.28 / 回撤 −58.59%

  ⚠️ 利润全在空腿：多头腿 −55.9%（夏普 −0.26），空头腿 +694.0%。
     同池等权多头对照 −45.0%。这不是 beta，是「做空最弱的流动性币」。
  ⚠️ 量级极不稳定：上市门槛 30 天时 +7,314%，365 天时 +477%（差 15 倍）；
     5 日相位换一天，收益在 131%~1141% 之间。不要拿任何一个数当预期。

freqtrade 回测结果（这个文件，--fee 0.0003，真实资金费，182 对白名单）

  原文区间 2025-01-01 → 2026-10-20：+147.56% / 夏普 1.02 / 回撤 −39.9%（闭合）−51.9%（含浮亏）
      613 笔，胜率 47.0%，拒单 0
  腿：多头 −61.33%、空头 +208.90%

⚠️ 为什么比研究低这么多（+148% vs +477%）

  · **腿的结构是对的**：多头亏、空头赚，量级差只出在空腿上
    （研究空腿 +694%，这里 +209%），多头腿 −61% 和研究 −56% 很接近。
  · 研究自己就说过这个策略「靠少数暴跌币赚钱」、量级对币池和相位极敏感（8 倍）。
    这里的币池是近似：用 close×volume 代替真实 quote_volume，且只覆盖下载过的
    182 对（不是全市场）。132 个调仓周期里，多头选股平均重合 92%、空头 88%——
    每期差的就是那 1-2 个名字，而利润恰恰集中在少数几个空头赢家上。
  · 2026-08 的 TUT 空头被 −99% 价格兜底止损打了两次（每次亏 396% 保证金），
    研究没止损、是硬扛过去的。
  · 结论：**这不是代码错了，是这个策略对「具体持了哪几个币」过于敏感。**
    拿 +148% 当这个版本的数字，不要拿研究的 +477% 当预期。

⚠️ 落地必须知道的几件事

1. **freqtrade 没有动态币池**，这里用「窗口内曾经进过池的币」做静态白名单，
   每天在策略里按同样规则重算池子。差异有两点：
   - 研究用真实 quote_volume，这里用 close × volume 近似（freqtrade 数据只有基础量）；
   - 白名单是下载过的币，不是全市场，所以「前 50」是在这个子集里取。
   已尽量把窗口内进过书里的币都下载齐（见 03_freqtrade落地.md）。
2. **200% 毛敞口**：每仓名义 = 净值 20%，多空各 5 仓。用 4× 杠杆只是为了让保证金
   占用降到 50%（10 仓 × 5%），名义敞口仍严格 = 净值 × 20% × 10；杠杆不改变盈亏。
3. **止损是价格 −99% 的兜底**，同 DownsideVol：`stoploss` 是保证金收益率、
   会除以杠杆换算成价格止损。
4. **回撤 −63% 是真的**。研究口径下这个策略靠少数暴跌币的空头赚钱，
   多头腿在 alt 熊市里被反复打穿。实盘心理门槛极高。
5. 实盘还要考虑借币成本、小币流动性、滑点——研究里 50bp 滑点就把收益从
   +477% 打到 +42%。

═══════════════════════════════════════════════════════════════════════
回测命令：

  freqtrade backtesting -s NearHighMomentum -c user_data/config/config_nearhigh.json \
    -i 1d --timerange 20250101-20261020 --fee 0.0003 --export trades

  # 全历史（anchor 自动变成 2020-01-01，相位与原文区间不同）
  freqtrade backtesting -s NearHighMomentum -c user_data/config/config_nearhigh.json \
    -i 1d --timerange 20200101-20261020 --fee 0.0003
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from pandas import DataFrame

from freqtrade.enums import RunMode
from freqtrade.exchange import timeframe_to_prev_date
from freqtrade.strategy import IStrategy


class NearHighMomentum(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = "1d"
    can_short = True

    # ── 研究定死的参数（改动前先读 02_复现结果.md 第四节）
    N_LEG = 5
    W_PER = 0.20
    STEP_DAYS = 5
    MIN_SCORED = 10

    TOP_N = 50
    ADV_MIN = 1e8
    MIN_HISTORY = 365
    ADV_LOOKBACK = 14

    # 见 docstring 第 2 条：只为留保证金，名义敞口由 stake×leverage 决定
    LEVERAGE = 4.0

    # 见 docstring 第 3 条：stoploss 会除以杠杆，换算成价格 −99% 的兜底
    BACKSTOP_PRICE = 0.99

    REBAL_MIN = 0.002

    # ── 框架参数
    startup_candle_count = 400      # 365 天上市门槛 + 30 天高点窗口 + 余量
    minimal_roi = {"0": 100.0}
    stoploss = -BACKSTOP_PRICE * LEVERAGE
    trailing_stop = False
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False
    position_adjustment_enable = True
    process_only_new_candles = True

    _panel_key: str | None = None
    _signals: dict[str, DataFrame] | None = None

    # ═══════════════════════════════════════════════ 面板
    def _whitelist(self) -> list[str]:
        try:
            return list(self.dp.current_whitelist())
        except Exception:
            return list(self.config.get("exchange", {}).get("pair_whitelist", []))

    def _build_panel(self):
        """拼出所有币的 (close, high, volume) 宽表。

        回测时 `get_pair_dataframe` 返回 [起点 − startup_candle_count, 结束] 的数据，
        足够覆盖 365 天的上市门槛判断。实盘时剔除形成中的当根 K 线。
        """
        live = self.dp.runmode in (RunMode.DRY_RUN, RunMode.LIVE)
        cutoff = (timeframe_to_prev_date(self.timeframe, datetime.now(timezone.utc))
                  if live else None)

        closes, highs, vols = {}, {}, {}
        for pair in self._whitelist():
            df = self.dp.get_pair_dataframe(pair=pair, timeframe=self.timeframe)
            if df is None or df.empty or len(df) < 40:
                continue
            if "date" not in df.columns:
                df = df.reset_index()
            if cutoff is not None:
                df = df[df["date"] < cutoff]
            if len(df) < 40:
                continue
            s = df.set_index("date")
            closes[pair] = s["close"].astype(float)
            highs[pair] = s["high"].astype(float)
            vols[pair] = s["volume"].astype(float)

        if not closes:
            return DataFrame(), DataFrame(), DataFrame()
        idx = pd.DataFrame(closes).sort_index().index
        return (DataFrame(closes).reindex(idx),
                DataFrame(highs).reindex(idx),
                DataFrame(vols).reindex(idx))

    # ═══════════════════════════════════════════════ 信号
    def _anchor(self) -> pd.Timestamp:
        """调仓相位锚点 = 回测起点（研究口径）。"""
        tr = str(self.config.get("timerange") or "")
        m = re.match(r"(\d{8})", tr)
        if m:
            return pd.Timestamp(m.group(1), tz="UTC")
        return pd.Timestamp("2025-01-01", tz="UTC")

    def _decision_bars(self, bars: pd.DatetimeIndex) -> list[pd.Timestamp]:
        """从 anchor 起每 STEP_DAYS 个自然日取一根 bar（允许顺延 1 天）。"""
        if len(bars) == 0:
            return []
        a = self._anchor()
        out, k, n = [], 0, len(bars)
        while True:
            want = (a + pd.Timedelta(days=self.STEP_DAYS * k)).normalize()
            if want > bars[-1] + pd.Timedelta(days=1):
                break
            pos = int(bars.searchsorted(want))
            if pos < n and bars[pos] <= want + pd.Timedelta(days=1):
                out.append(bars[pos])
            k += 1
        return out

    def _build_signals(self, close: DataFrame, high: DataFrame,
                       vol: DataFrame) -> dict[str, DataFrame]:
        if close.empty:
            return {}
        cols = list(close.columns)

        # ── 点时流动性池（用 close×volume 近似 quote_volume，见 docstring 第 1 条）
        qv = close * vol
        adv = qv.rolling(self.ADV_LOOKBACK,
                         min_periods=max(3, self.ADV_LOOKBACK // 2)).mean()
        enough = close.notna().cumsum() >= self.MIN_HISTORY
        rank = adv.where((adv >= self.ADV_MIN) & enough).rank(axis=1, ascending=False)
        univ = (rank <= self.TOP_N).fillna(False)

        # ── 打分
        near_high = close / high.rolling(30, min_periods=20).max()
        mom = close / close.shift(14) - 1
        sig = close.pct_change(fill_method=None).rolling(14, min_periods=10).std()
        smooth = mom / sig.replace(0, np.nan)
        score = (near_high.rank(axis=1, pct=True)
                 + smooth.rank(axis=1, pct=True))

        bars = close.index
        dec = self._decision_bars(bars)

        # 每个有效决策日的目标权重（带符号）
        wmat = DataFrame(index=dec, columns=cols, dtype=float)
        for t in dec:
            s = score.loc[t].where(univ.loc[t]).dropna()
            if len(s) < self.MIN_SCORED:
                continue
            s = s.sort_values(ascending=False)
            w = pd.Series(0.0, index=cols)
            w[s.index[:self.N_LEG]] = self.W_PER
            w[s.index[-self.N_LEG:]] = -self.W_PER
            wmat.loc[t] = w.to_numpy()

        out: dict[str, DataFrame] = {}
        for pair in cols:
            tgt_row = wmat[pair].reindex(bars)          # 只有决策 bar 有值
            is_dec = tgt_row.notna()
            out[pair] = DataFrame({
                "date": bars,
                "s3_enter_long": (is_dec & (tgt_row > 0)).to_numpy(),
                "s3_enter_short": (is_dec & (tgt_row < 0)).to_numpy(),
                "s3_exit_long": (is_dec & (tgt_row <= 0)).to_numpy(),
                "s3_exit_short": (is_dec & (tgt_row >= 0)).to_numpy(),
                "s3_weight": tgt_row.ffill().fillna(0.0).to_numpy(),
                "s3_rebal": is_dec.to_numpy(),
            })
        return out

    def _get_signals(self) -> dict[str, DataFrame]:
        if self._signals is not None and self.dp.runmode == RunMode.BACKTEST:
            return self._signals
        close, high, vol = self._build_panel()
        key = str(close.index.max()) if not close.empty else "empty"
        if key == self._panel_key and self._signals is not None:
            return self._signals
        self._signals = self._build_signals(close, high, vol)
        self._panel_key = key
        return self._signals

    # ═══════════════════════════════════════════════ 指标
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        pair = metadata["pair"]
        sig = self._get_signals().get(pair)

        dataframe["s3_enter_long"] = False
        dataframe["s3_enter_short"] = False
        dataframe["s3_exit_long"] = False
        dataframe["s3_exit_short"] = False
        dataframe["s3_weight"] = 0.0
        dataframe["s3_rebal"] = False
        if sig is None or sig.empty:
            return dataframe

        idx = sig.set_index("date")
        for col in ("s3_enter_long", "s3_enter_short",
                    "s3_exit_long", "s3_exit_short", "s3_rebal"):
            dataframe[col] = dataframe["date"].map(idx[col]).fillna(False).astype(bool)
        dataframe["s3_weight"] = dataframe["date"].map(idx["s3_weight"]).fillna(0.0)

        if self.dp.runmode in (RunMode.DRY_RUN, RunMode.LIVE) and len(dataframe) >= 2:
            if dataframe["date"].map(idx["s3_weight"]).isna().iloc[-1]:
                for col in ("s3_enter_long", "s3_enter_short",
                            "s3_exit_long", "s3_exit_short", "s3_rebal"):
                    dataframe.loc[dataframe.index[-1], col] = dataframe[col].iloc[-2]
                dataframe.loc[dataframe.index[-1], "s3_weight"] = \
                    dataframe["s3_weight"].iloc[-2]
        return dataframe

    # ═══════════════════════════════════════════════ 入场 / 出场列
    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        tradable = dataframe["volume"] > 0
        dataframe.loc[tradable & dataframe["s3_enter_long"], "enter_long"] = 1
        dataframe.loc[tradable & dataframe["s3_enter_short"], "enter_short"] = 1
        dataframe.loc[dataframe["enter_long"] == 1, "enter_tag"] = "nearhigh_long"
        dataframe.loc[dataframe["enter_short"] == 1, "enter_tag"] = "nearhigh_short"
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[dataframe["s3_exit_long"], "exit_long"] = 1
        dataframe.loc[dataframe["s3_exit_short"], "exit_short"] = 1
        return dataframe

    # ═══════════════════════════════════════════════ 仓位
    def _row_at(self, pair: str, current_time: datetime) -> pd.Series | None:
        try:
            df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        except Exception:
            return None
        if df is None or df.empty or "s3_weight" not in df.columns:
            return None
        past = df[df["date"] < current_time]
        if past.empty:
            return None
        return past.iloc[-1]

    def _weight_at(self, pair: str, current_time: datetime) -> float:
        row = self._row_at(pair, current_time)
        if row is None:
            return 0.0
        v = float(row["s3_weight"])
        return v if np.isfinite(v) else 0.0

    def _equity(self) -> float:
        try:
            return float(self.wallets.get_total_stake_amount())
        except Exception:
            return 0.0

    def custom_stake_amount(self, pair, current_time, current_rate, proposed_stake,
                            min_stake, max_stake, leverage, entry_tag, side,
                            **kwargs) -> float:
        w = abs(self._weight_at(pair, current_time))
        if w <= 0:
            return proposed_stake
        equity = self._equity() or float(proposed_stake) * 20
        # 名义敞口 = stake × LEVERAGE = equity × |w|（与研究一致）
        stake = equity * w / self.LEVERAGE
        if min_stake:
            stake = max(stake, float(min_stake))
        return min(stake, float(max_stake))

    def adjust_trade_position(self, trade, current_time, current_rate, current_profit,
                              min_stake, max_stake, current_entry_rate,
                              current_exit_rate, current_entry_profit,
                              current_exit_profit, **kwargs):
        """调仓日把仓位拉回目标名义额。

        `_check_trade_exit` 每根 K 线都会调用这里（`backtesting.py:986`），
        用 `s3_rebal` 限制在决策那一根，否则会变成日频调仓。
        """
        row = self._row_at(trade.pair, current_time)
        if row is None or not bool(row["s3_rebal"]):
            return None

        w = float(row["s3_weight"])
        if not np.isfinite(w) or w == 0:
            return None
        if (w > 0) != (not trade.is_short):
            return None                      # 方向要换：交给 exit/enter 走

        equity = self._equity()
        if equity <= 0:
            return None
        target_notional = equity * abs(w)
        current_notional = trade.amount * current_rate
        stake_delta = (target_notional - current_notional) / self.LEVERAGE

        if abs(stake_delta) < max(self.REBAL_MIN * equity,
                                  float(min_stake or 0.0)):
            return None
        if stake_delta < 0:
            stake_delta = max(stake_delta, -trade.stake_amount)
        elif max_stake:
            stake_delta = min(stake_delta, float(max_stake))
        return stake_delta if stake_delta != 0 else None

    def leverage(self, pair, current_time, current_rate, proposed_leverage,
                 max_leverage, entry_tag, side, **kwargs) -> float:
        return min(self.LEVERAGE, float(max_leverage) if max_leverage else self.LEVERAGE)
