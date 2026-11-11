"""
DownsideVol — 低下行波动率多空（日线，固定 18 个 U 本位永续，周频调仓）

═══════════════════════════════════════════════════════════════════════
这是 `策略池/AlphaPilot复现/` 里 S4 的落地版。规则来自 01_原始设想.md 第五节、
02_复现结果.md 第五节，一个参数都没有为了回测好看调过：

  标的   AAVE ADA APT ARB AVAX BNB BTC DOGE DOT ETH LINK LTC NEAR OP SOL SUI UNI XRP
  信号   σ⁻_i = std_30( min(r_i, 0) )       下行波动率
         σ_i  = std_30( r_i )                腿内逆波动加权用
         fund7_i = 过去 7 天日度资金费之和的均值

  每周一收盘决策、周二开盘成交：
    多头：σ⁻ 最低 6 个里，取 fund7 最低的 4 个
    空头：σ⁻ 最高的 4 个
    腿内按 1/σ 归一化，腿波动率 = 组内 σ 的调和平均
    σ_L = 4/Σ(1/σ_i)   G_L = σ_S/(σ_L+σ_S)   G_S = σ_L/(σ_L+σ_S)
    两腿毛敞口合计 = 100% 净值

═══════════════════════════════════════════════════════════════════════
研究口径下的结果（研究脚本，不是这个 freqtrade 版本）

  原文区间 2024-01 → 2026-09：+220.21% / 年化 52.67% / 夏普 2.01 / 回撤 −15.02%
  全历史   2023-01 → 2026-09：+211.73% / 年化 35.41% / 夏普 1.50 / 回撤 −26.84%
  分年：2023 −3.2% / 2024 +112.6% / 2025 +41.7% / 2026 +6.9%

freqtrade 回测结果（这个文件，--fee 0.0003，真实资金费）

  原文区间 2024-01-01 → 2026-09-30：+208.9% / 夏普 1.54 / 回撤 −17.8%（含浮亏）
      323 笔，胜率 52.6%，拒单 0
  全历史   2023-01-01 → 2026-09-30：+252.1% / 夏普 1.31 / 回撤 −17.6%（含浮亏）
      415 笔，胜率 50.8%

⚠️ 两个数为什么不一样（已逐周对账，不是随便写的）

  · 原文区间差 −11pp 属正常：窗口起点的资金费预热让第一个决策周晚几天、
    freqtrade 的净值是已实现口径、费用开平各收一次。
  · **全历史 +252% 比研究高 40pp，是回测起点的边界运气，不是策略更强。**
    2023-01 起跑时资金费面板不够长，头三周只进了 BTC/ETH 两笔
    （毛敞口 0.18–0.43），而 2023 年这个策略本来是小亏的，
    少持仓反而躲过亏损，白捡约 +6pp 后被复利放大。
  · 对账方法：用回测成交反推逐日持仓，周二毛敞口/净值 0.96–1.00，
    与研究目标的逐周 L1 距离中位数 0.024；把两边权重分别乘同一组周收益
    得 245% vs 210%，说明差异只来自「持了什么」，不来自杠杆、费用、资金费记账。
  · **对外只引用原文区间那个数（+208.9%）。** 全历史那个不要用。

⚠️ 落地必须知道的几件事

1. **绝对收益好看，但大头是 beta。** 同区间 18 币等权买入持有 +188.6%
   （夏普 0.90、回撤 −60.2%）。策略的 alpha 是「用三分之一的市场风险拿到
   接近市场的收益」，不是「跑赢市场很多」。拿夏普和回撤当卖点，别拿收益。
2. **必须每周把仓位拉回目标权重，不能放着漂。** 研究里量过：
   每周全额再平衡 +220.21%，只在名单变化时调权 +219.52%，
   **完全不调权、纯漂移只有 +184.92%**（差 35pp）。所以这里开了
   `position_adjustment_enable`，用 `adjust_trade_position` 做周度再平衡。
3. **2× 杠杆是记账需要，不是放大收益。** 目标毛敞口 100%，1× 时保证金就占满
   净值，同一根 K 线上先处理新开仓、后处理旧平仓会因现金不足被拒单。这里
   杠杆 2×、stake 只给净值权重的一半，**名义敞口仍严格 = 研究里的 100%**，
   保证金占用降到 50%，给轮动留出现金。费用/资金费都按名义额计，不受影响。
4. **止损是价格 −99% 的兜底。** freqtrade 的 `stoploss` 是保证金收益率，会除以
   杠杆换算成价格止损（`trade_model.py:858-862`）。研究里没有止损；2× 下写
   −0.99 等于价格 −49.5% 就砍仓，实测会误杀 16 笔（SUI 2024-01 空头等），
   所以这里按杠杆放大成价格 −99%。
5. **freqtrade 的净值是「已实现」口径**：钱包余额不含未实现盈亏，周中按净值
   定权会略微低估仓位。这个策略周频换手、持仓期短，影响较小。
6. **不从窗口第一天建仓会漏行情。** 入场信号在每个决策周都发（已持仓的会被
   框架忽略），这样从任意回测起点开始都会先按当时的目标组合建仓，而不是等
   某只币下次换手才买——研究口径下后者会少掉一大截收益。

═══════════════════════════════════════════════════════════════════════
回测命令：

  freqtrade backtesting -s DownsideVol -c user_data/config/config_downvol.json \
    -i 1d --timerange 20240101-20260930 --fee 0.0003 --export trades

  freqtrade backtesting -s DownsideVol -c user_data/config/config_downvol.json \
    -i 1d --timerange 20230101-20260930 --fee 0.0003
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


class DownsideVol(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = "1d"
    can_short = True

    # ── 研究定死的参数（改动前先读 02_复现结果.md 第五节）
    LOOKBACK = 30
    N_PICK = 4
    N_SMOOTH = 6
    MIN_OBS = 20
    FUND_SMOOTH = 7
    FUND_MIN_OBS = 5

    # 见 docstring 第 3 条：2× 只为留保证金，名义敞口由 stake×leverage 决定
    LEVERAGE = 2.0

    # 见 docstring 第 4 条：stoploss 会除以杠杆，换算成价格 −99% 的兜底
    BACKSTOP_PRICE = 0.99

    # 低于这个比例的再平衡不做（省手续费），按净值算
    REBAL_MIN = 0.002

    # ── 框架参数
    startup_candle_count = 60
    minimal_roi = {"0": 100.0}      # 出场完全由周度名单决定
    stoploss = -BACKSTOP_PRICE * LEVERAGE
    trailing_stop = False
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False
    position_adjustment_enable = True   # 周度再平衡，见 docstring 第 2 条
    process_only_new_candles = True

    # 面板缓存（实例级）
    _panel_key: str | None = None
    _signals: dict[str, DataFrame] | None = None

    # ═══════════════════════════════════════════════ 面板
    def _whitelist(self) -> list[str]:
        try:
            return list(self.dp.current_whitelist())
        except Exception:
            return list(self.config.get("exchange", {}).get("pair_whitelist", []))

    def _build_panel(self) -> tuple[DataFrame, DataFrame]:
        """拼出 18 个币的 (close, 资金费) 宽表。

        回测时 `get_pair_dataframe` 返回完整历史（`dataprovider.py:371-400`），
        信号只按日期逐行计算，不引入未来信息。实盘时剔除形成中的当根 K 线。
        """
        live = self.dp.runmode in (RunMode.DRY_RUN, RunMode.LIVE)
        cutoff = (timeframe_to_prev_date(self.timeframe, datetime.now(timezone.utc))
                  if live else None)

        closes, funds = {}, {}
        for pair in self._whitelist():
            df = self.dp.get_pair_dataframe(pair=pair, timeframe=self.timeframe)
            if df is None or df.empty or len(df) < 60:
                continue
            if "date" not in df.columns:
                df = df.reset_index()
            if cutoff is not None:
                df = df[df["date"] < cutoff]
            if len(df) < 60:
                continue
            s = df.set_index("date")
            closes[pair] = s["close"].astype(float)

            try:
                f = self.dp.get_pair_dataframe(
                    pair=pair, timeframe="1h", candle_type="funding_rate"
                )
            except Exception:
                f = None
            if f is not None and not f.empty:
                if "date" not in f.columns:
                    f = f.reset_index()
                fs = f.set_index("date")
                col = "funding_rate" if "funding_rate" in fs.columns else "open"
                funds[pair] = fs[col].astype(float)

        if not closes:
            return DataFrame(), DataFrame()
        return DataFrame(closes).sort_index(), DataFrame(funds).sort_index()

    # ═══════════════════════════════════════════════ 信号
    def _build_signals(self, close: DataFrame, fund: DataFrame) -> dict[str, DataFrame]:
        if close.empty:
            return {}
        cols = list(close.columns)

        r = close.pct_change(fill_method=None)
        sig_low = np.minimum(r, 0.0).rolling(
            self.LOOKBACK, min_periods=self.MIN_OBS).std()
        sig_all = r.rolling(self.LOOKBACK, min_periods=self.MIN_OBS).std()

        if fund.empty:
            fund7 = DataFrame(np.nan, index=close.index, columns=cols)
        else:
            f7 = fund.reindex(columns=cols)
            fund7 = (f7.resample("1D").sum(min_count=1)
                       .rolling(self.FUND_SMOOTH, min_periods=self.FUND_MIN_OBS)
                       .mean()
                       .reindex(close.index)
                       .ffill())

        bars = close.index
        mondays = bars[bars.weekday == 0]

        # 每个周一的目标权重（带符号）；没有有效决策的周留 NaN = 维持不动
        wmat = DataFrame(index=mondays, columns=cols, dtype=float)
        for t in mondays:
            sl = sig_low.loc[t].dropna()
            sa = sig_all.loc[t].dropna()
            f7 = fund7.loc[t].dropna()
            common = sl.index.intersection(sa.index).intersection(f7.index)
            if len(common) < 12:
                continue
            sl, sa, f7 = sl[common], sa[common], f7[common]

            long_pool = sl.nsmallest(self.N_SMOOTH).index
            longs = f7[long_pool].nsmallest(self.N_PICK).index
            shorts = sl.nlargest(self.N_PICK).index
            if len(longs) == 0 or len(shorts) == 0:
                continue

            def leg(idx):
                s = sa[idx]
                iw = 1.0 / s
                return iw / iw.sum(), len(idx) / iw.sum()

            wl, sig_l = leg(longs)
            ws, sig_s = leg(shorts)
            g_l = sig_s / (sig_l + sig_s)
            g_s = sig_l / (sig_l + sig_s)

            w = pd.Series(0.0, index=cols)
            w[longs] = wl * g_l
            w[shorts] = -ws * g_s
            wmat.loc[t] = w.to_numpy()

        out: dict[str, DataFrame] = {}
        for pair in cols:
            w_m = wmat[pair]
            tgt = np.sign(w_m)                          # NaN = 非决策周
            tgt_m = tgt.reindex(bars)                   # 只有周一有值
            decision = tgt_m.notna()                    # ← 决策日 = 周一
            tgt_daily = tgt_m.ffill()                   # 其余日子沿用上周目标
            prev = tgt_daily.shift(1).fillna(0.0)

            # 新开：决策周发信号；已持仓的会被框架忽略，不影响
            # 明细见 docstring 第 6 条
            out[pair] = DataFrame({
                "date": bars,
                "dv_enter_long": (decision & (tgt_daily > 0)).to_numpy(),
                "dv_enter_short": (decision & (tgt_daily < 0)).to_numpy(),
                "dv_exit_long": (decision & (tgt_daily != 1) & (prev == 1)).to_numpy(),
                "dv_exit_short": (decision & (tgt_daily != -1) & (prev == -1)).to_numpy(),
                "dv_weight": w_m.reindex(bars).ffill().fillna(0.0).to_numpy(),
                "dv_rebal": decision.to_numpy(),
            })
        return out

    def _get_signals(self) -> dict[str, DataFrame]:
        """回测里每个 pair 都会调一次 populate_indicators，面板只建一次。"""
        if self._signals is not None and self.dp.runmode == RunMode.BACKTEST:
            return self._signals

        close, fund = self._build_panel()
        key = str(close.index.max()) if not close.empty else "empty"
        if key == self._panel_key and self._signals is not None:
            return self._signals
        self._signals = self._build_signals(close, fund)
        self._panel_key = key
        return self._signals

    # ═══════════════════════════════════════════════ 指标
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        pair = metadata["pair"]
        sig = self._get_signals().get(pair)

        dataframe["dv_enter_long"] = False
        dataframe["dv_enter_short"] = False
        dataframe["dv_exit_long"] = False
        dataframe["dv_exit_short"] = False
        dataframe["dv_weight"] = 0.0
        dataframe["dv_rebal"] = False

        if sig is None or sig.empty:
            return dataframe

        idx = sig.set_index("date")
        for col in ("dv_enter_long", "dv_enter_short",
                    "dv_exit_long", "dv_exit_short", "dv_rebal"):
            dataframe[col] = dataframe["date"].map(idx[col]).fillna(False).astype(bool)
        dataframe["dv_weight"] = dataframe["date"].map(idx["dv_weight"]).fillna(0.0)

        # 实盘：当根是形成中的 K 线，用上一根已收线的信号顶上
        if self.dp.runmode in (RunMode.DRY_RUN, RunMode.LIVE) and len(dataframe) >= 2:
            if dataframe["date"].map(idx["dv_weight"]).isna().iloc[-1]:
                for col in ("dv_enter_long", "dv_enter_short",
                            "dv_exit_long", "dv_exit_short", "dv_rebal"):
                    dataframe.loc[dataframe.index[-1], col] = dataframe[col].iloc[-2]
                dataframe.loc[dataframe.index[-1], "dv_weight"] = \
                    dataframe["dv_weight"].iloc[-2]
        return dataframe

    # ═══════════════════════════════════════════════ 入场 / 出场列
    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        tradable = dataframe["volume"] > 0
        dataframe.loc[tradable & dataframe["dv_enter_long"], "enter_long"] = 1
        dataframe.loc[tradable & dataframe["dv_enter_short"], "enter_short"] = 1
        dataframe.loc[dataframe["enter_long"] == 1, "enter_tag"] = "downvol_long"
        dataframe.loc[dataframe["enter_short"] == 1, "enter_tag"] = "downvol_short"
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[dataframe["dv_exit_long"], "exit_long"] = 1
        dataframe.loc[dataframe["dv_exit_short"], "exit_short"] = 1
        return dataframe

    # ═══════════════════════════════════════════════ 仓位
    def _row_at(self, pair: str, current_time: datetime) -> pd.Series | None:
        """取「当前 K 线之前」那一行。绝不用当根，那是未来信息。"""
        try:
            df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        except Exception:
            return None
        if df is None or df.empty or "dv_weight" not in df.columns:
            return None
        past = df[df["date"] < current_time]
        if past.empty:
            return None
        return past.iloc[-1]

    def _weight_at(self, pair: str, current_time: datetime) -> float:
        row = self._row_at(pair, current_time)
        if row is None:
            return 0.0
        v = float(row["dv_weight"])
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
        equity = self._equity() or float(proposed_stake) * 8
        # 名义敞口 = stake × LEVERAGE = equity × |w|（与研究一致）
        stake = equity * w / self.LEVERAGE
        if min_stake:
            stake = max(stake, float(min_stake))
        return min(stake, float(max_stake))

    def adjust_trade_position(self, trade, current_time, current_rate, current_profit,
                              min_stake, max_stake, current_entry_rate,
                              current_exit_rate, current_entry_profit,
                              current_exit_profit, **kwargs):
        """周度再平衡：决策周之后的那个开盘，把仓位拉回目标名义额。

        回测里 `_check_trade_exit` 每根 K 线都会调用这里（`backtesting.py:986`），
        所以必须用 `dv_rebal` 把它限制在决策周的那一根，否则会变成日频调仓。
        """
        row = self._row_at(trade.pair, current_time)
        if row is None or not bool(row["dv_rebal"]):
            return None

        w = float(row["dv_weight"])
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
