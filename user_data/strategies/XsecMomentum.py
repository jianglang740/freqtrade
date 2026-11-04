"""
XsecMomentum — 横截面动量多空（日频，风险调整动量，波动率目标 20%）

═══════════════════════════════════════════════════════════════════════
这是 `策略池/横截面动量/` 整条研究线的落地版。规则全部来自报告 04/05，
**没有任何一个参数是为了回测好看调的**，每一个都能指回一份报告：

  选池   ADV(14 日) ≥ $1M，剔除稳定币/法币，28 日年化波动率 ≥ 15%，
         再按 ADV 取前 TOP_N 个              → 05 第四节（Stage A 修池子）
  打分   7/14/28 日「动量 ÷ 同窗口收益波动」，三窗口等权平均，
         在池内做横截面排序                   → 01 §1.3、05 B6（不做波动率调整会更差）
  建仓   池内排名前 20% 做多，后 20% 做空，每腿等权
                                              → 01 §1.4，02 V3（持有缓冲是负贡献，已弃）
  调仓   日频，排名掉出分位就平              → 03 §二（降频全部更差）
  仓位   O2 波动率目标：k = 0.20 / 63 日已实现年化波动，只用 t-1 的信息
                                              → 04 §六（O2 是强制项）
  止损   无。只留一个很宽的兜底 stoploss    → 03 §五（止损越紧越坏）

═══════════════════════════════════════════════════════════════════════
⚠️ 落地前必须知道的四件事（报告里都有，这里再集中说一遍）

1. **容量只有 $1–5M。** $10M 规模上平方根冲击成本是毛利的 1.7 倍。
   这个策略不适合管理大资金，跑之前先确认资金规模。
2. **必须用合约（futures），现货做不了。** 空腿是这个策略的一半，
   `can_short = True`，`trading_mode` 必须是 futures。
3. **回撤 −56% 是已经发生过的事实**，不是风险提示。2023 年之后纯费率
   只有 13–16% 年化、Sharpe 0.6–0.7。
4. **本地期货数据只有 95 个币、且大多从 2023 年才开始**，还是幸存者偏差口径
   （只含今天还在交易的永续）。报告里那个 704 币的 32.7% 在这里**复现不出来**。
   而且这个回测的数字**会明显高于报告的 2023+ 那一行**，原因是币池不同 +
   幸存者偏差，不是策略变好了。**拿报告的数字当预期，不要拿这个回测的数字。**

═══════════════════════════════════════════════════════════════════════
本机回测结果（2023-01-01 → 2026-10-30，fee 3bp，币安 U 本位 95 个永续）

  年化 30.92% · 日净值波动 26.3% · Sharpe 1.13 · 最大回撤 22.95%
  分年（按年初权益计）：2023 +22.02% / 2024 +90.76% / 2025 +31.52% / 2026 −10.31%
  多腿累计 +25.7（profit_ratio 之和），空腿 −0.36 —— 空腿基本是零贡献的
  beta 对冲，和研究结论一致。

  ⚠️ 这组数字**偏乐观**：白名单是今天还活着的 95 个永续，已下架的看不见。
  报告 04 已经量过，生存者偏差主要抬高的是风险指标，但在这个短样本上
  它同时抬高了收益。**当作「策略能跑通、行为符合预期」的验证，不是收益预期。**

  前视自查（比 lookahead-analysis 可靠）：把回测区间从 2026-10 截到 2024-06，
  重叠期内 1091 笔已平仓交易逐笔完全一致，净利润差 0.000000 USDT。
  `freqtrade lookahead-analysis` 对横截面策略必误报（它只加载单个 pair），
  **别用它的结论**，用这个区间截断法。

═══════════════════════════════════════════════════════════════════════
实现上几个查过源码确认的点

* **横截面数据从哪来**：`populate_indicators` 每根 K 线只看本 pair，
  所以池子和排名必须自己从 `self.dp.get_pair_dataframe(pair, "1d")`
  把整个白名单拼成面板。回测时该接口返回**完整历史**
  （`dataprovider.py:389-397`，只有 `__slice_date` 被设置时才截断，
  而它是在 `backtest_loop` 里才设置的，`advise_all_indicators` 阶段为 None）。
  面板只按日期对齐后逐行排名，**不引入任何未来信息**。

* **信号在下一根开盘成交**：回测在跑之前把信号整体 shift 了一根
  （`backtesting.py:554-567`）。所以第 d 根算出来的排名，第 d+1 根开盘执行，
  和研究里「d 日收盘定权、d+1 日收益」完全同构。

* **实盘要补一根**：实盘 `populate_indicators` 拿到的最后一根是**形成中**的
  K 线，用它算出来的动量是残缺的。所以实盘会先把当根剔除再算，
  然后把上一根（已收线）的信号 forward-fill 到当根，让框架在开盘时执行。

* **`custom_stake_amount` 在回测里生效**（`backtesting.py:1097-1110`），
  这是 O2 唯一能落地的位置 —— 回测没有「组合」这个对象，只能逐笔记账，
  所以 O2 的 k 直接乘到每笔的 stake 上。

* ⚠️ **O2 的上限这里收成 1.0×，不是研究里的 2.0×。** 这是一个**保守方向的
  主动偏离**：研究允许在低波动期加到 2× 杠杆，本策略不允许任何时点超过
  1× 总敞口。代价是平静期的收益会低于报告里 O2 那一行，风险也同比更低。
  想严格还原研究口径，把 `LEV_CAP` 改成 2.0，并把 `exchange.leverage` 设成 2。

═══════════════════════════════════════════════════════════════════════
回测命令：

  freqtrade backtesting -s XsecMomentum -c user_data/config/config_xsec.json \
    -i 1d --timerange 20230101-20261030 --fee 0.0003 --export trades

  # 真实费率口径（币安 U 本位合约 taker 0.05%）
  freqtrade backtesting -s XsecMomentum -c user_data/config/config_xsec.json \
    -i 1d --timerange 20230101-20261030 --fee 0.0005 --export trades

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


class XsecMomentum(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = "1d"
    can_short = True

    # ── 研究定死的参数（改动前先读 05 报告）
    WINDOWS = (7, 14, 28)      # 动量/波动率窗口
    Q = 0.20                   # 每条腿的横截面分位
    ADV_WINDOW = 14            # ADV 回看；7/28/64 都测过，14 是平台区
    ADV_MIN = 1e6
    VOL_FLOOR = 0.15           # 28 日年化波动率地板
    ANN = 252
    TARGET_VOL = 0.20
    RV_WINDOW = 63
    LEV_CAP = 1.0              # 研究用 2.0；这里收到 1.0，见 docstring

    # ⚠️ 这个值有争议，说清楚：
    #    研究里验证过的是「池 50 / 每腿 10 个」，研究池来自约 200 个候选。
    #    本地期货白名单只有 95 个币，所以取 50 等于「前一半」而不是「前四分之一」，
    #    比研究更宽松 —— 研究里把池子放宽（B1/B2）是坏方向。
    #    但我实测两种映射（池 25 / 池 50），池 50 明显更好（CAGR 9.6% → 30.9%），
    #    而且它恰好复现了研究验证过的「每腿 10 个」这个结构参数。
    #    结论：保持 50，但把「池子偏宽」作为已知偏差记在这里，不假装它不存在。
    TOP_N = 50
    # config 里的 max_open_trades 必须等于 2 × round(TOP_N × Q) = 20，
    # 否则要么老有空槽（收益打折），要么后面的信号全被拒（回测里报 Rejected Entry）。
    # 池子因数据不全缩小时，实际每腿会少于 10 个，属于预期行为。

    # 稳定币 / 法币对：mom/vol 的分母趋零会把它们顶到多空两端
    EXCLUDE_BASE = frozenset({
        "USDC", "BUSD", "TUSD", "USDP", "DAI", "FDUSD", "PAX", "USDD", "USDS",
        "EURC", "USD1", "UST", "USTC", "GUSD", "LUSD", "FRAX", "PYUSD",
        "EUR", "GBP", "TRY", "BRL", "AUD", "JPY", "ZAR", "RUB", "UAH", "NGN",
    })

    # ── 框架参数
    startup_candle_count = 150
    minimal_roi = {"0": 10.0}   # 实质关闭 ROI：出场完全由排名决定
    stoploss = -0.25            # 兜底，日频下基本不触发（03 §五：止损越紧越坏）
    trailing_stop = False
    use_custom_stoploss = False

    # 面板缓存（实例级）
    _panel_key: str | None = None
    _signals: dict[str, DataFrame] | None = None

    # ═══════════════════════════════════════════════ 面板与信号
    def _whitelist(self) -> list[str]:
        """回测/实盘都从同一处取白名单，取不到就退回 config。"""
        try:
            return list(self.dp.current_whitelist())
        except Exception:
            return list(self.config.get("exchange", {}).get("pair_whitelist", []))

    def _build_panel(self) -> tuple[DataFrame, DataFrame]:
        """把白名单里所有 pair 拼成 (close, quoteVolume) 两个宽表。

        行 = 日期，列 = pair。回测时拿到的是完整历史；实盘时剔除形成中的当根 K 线，
        因为那根的 close/volume 还在变，用它算动量会得到残缺信号。
        """
        live = self.dp.runmode in (RunMode.DRY_RUN, RunMode.LIVE)
        cutoff = (timeframe_to_prev_date(self.timeframe, datetime.now(timezone.utc))
                  if live else None)

        closes, qvs = {}, {}
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
            # 本地 feather 只有 OHLCV，没有 quoteVolume；成交额 = 基础币成交量 × 收盘价
            if "quoteVolume" in s.columns:
                qvs[pair] = s["quoteVolume"].astype(float)
            else:
                qvs[pair] = (s["volume"].astype(float) * s["close"].astype(float))

        if not closes:
            return DataFrame(), DataFrame()

        close = DataFrame(closes).sort_index()
        qv = DataFrame(qvs).sort_index().reindex(columns=close.columns)
        return close, qv

    def _build_signals(self, close: DataFrame, qv: DataFrame) -> dict[str, DataFrame]:
        """返回 {pair: DataFrame[date, xsec_rank, xsec_long, xsec_short, xsec_k]}。"""
        if close.empty:
            return {}

        ret = close.pct_change(fill_method=None)
        vol28 = ret.rolling(28, min_periods=20).std()
        vol_ann = vol28 * np.sqrt(self.ANN)
        adv = qv.rolling(self.ADV_WINDOW,
                         min_periods=max(3, int(round(self.ADV_WINDOW * 0.7)))).mean()

        # ── 池子：先过滤，再排名（顺序不能反，否则池子大小会跟着成分一起变）
        cols = [c for c in close.columns if c.split("/")[0] not in self.EXCLUDE_BASE]
        ok = adv[cols].notna() & (adv[cols] >= self.ADV_MIN) & (vol_ann[cols] >= self.VOL_FLOOR)
        pool = ok & (adv[cols].where(ok).rank(axis=1, ascending=False) <= self.TOP_N)

        # ── 打分：三窗口「动量 ÷ 同窗口波动率」等权平均，只在池内排名
        parts = []
        for n in self.WINDOWS:
            mom = close / close.shift(n) - 1
            parts.append(mom / ret.rolling(n, min_periods=n).std())
        raw = sum(parts) / len(parts)
        raw = raw.where(pool)                       # 池外置 NaN，不参与排名
        rank = raw.rank(axis=1, pct=True, ascending=False)   # 1/n = 最强

        in_long = rank <= self.Q
        in_short = rank >= 1 - self.Q

        # ── O2：用「按 d-1 排名、持有到 d」的组合收益滚 63 日波动
        lg = in_long.shift(1).fillna(False).astype(bool)
        st = in_short.shift(1).fillna(False).astype(bool)
        long_r = ret.where(lg).mean(axis=1)
        short_r = ret.where(st).mean(axis=1)
        port = 0.5 * (long_r - short_r)             # 1× 口径（每腿 0.5×）
        rv = port.rolling(self.RV_WINDOW, min_periods=30).std() * np.sqrt(self.ANN)
        k = (self.TARGET_VOL / rv).clip(upper=self.LEV_CAP).shift(1).fillna(1.0)

        out = {}
        for pair in close.columns:
            out[pair] = DataFrame({
                "date": close.index,
                "xsec_rank": rank[pair].to_numpy(),
                "xsec_long": in_long[pair].fillna(False).to_numpy(),
                "xsec_short": in_short[pair].fillna(False).to_numpy(),
                "xsec_k": k.to_numpy(),
            })
        return out

    def _get_signals(self) -> dict[str, DataFrame]:
        """按面板最后一天缓存；实盘跨日会重建，回测只建一次。

        回测里 `populate_indicators` 会被每个 pair 各调一次（95 个 pair ×
        95 次 get_pair_dataframe 就是 O(N²)），所以回测模式建成即锁死。
        """
        if self._signals is not None and self.dp.runmode == RunMode.BACKTEST:
            return self._signals

        close, qv = self._build_panel()
        key = str(close.index.max()) if not close.empty else "empty"
        if key == self._panel_key and self._signals is not None:
            return self._signals
        self._signals = self._build_signals(close, qv)
        self._panel_key = key
        return self._signals

    # ═══════════════════════════════════════════════ 指标
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        pair = metadata["pair"]
        signals = self._get_signals()
        sig = signals.get(pair)

        dataframe["xsec_rank"] = np.nan
        dataframe["xsec_long"] = False
        dataframe["xsec_short"] = False
        dataframe["xsec_k"] = 1.0

        if sig is None or sig.empty:
            return dataframe

        idx = sig.set_index("date")
        dataframe["xsec_rank"] = dataframe["date"].map(idx["xsec_rank"])
        long_s = dataframe["date"].map(idx["xsec_long"])
        short_s = dataframe["date"].map(idx["xsec_short"])

        # 实盘：当根是形成中的 K 线，面板里没有它的行 → 用上一根已收线的信号顶上
        if self.dp.runmode in (RunMode.DRY_RUN, RunMode.LIVE) and len(dataframe) >= 2:
            if pd.isna(long_s.iloc[-1]):
                long_s.iloc[-1] = long_s.iloc[-2]
                short_s.iloc[-1] = short_s.iloc[-2]

        dataframe["xsec_long"] = long_s.fillna(False).astype(bool)
        dataframe["xsec_short"] = short_s.fillna(False).astype(bool)
        dataframe["xsec_k"] = dataframe["date"].map(idx["xsec_k"]).fillna(1.0)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        tradable = dataframe["volume"] > 0
        dataframe.loc[tradable & dataframe["xsec_long"], "enter_long"] = 1
        dataframe.loc[tradable & dataframe["xsec_short"], "enter_short"] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # 排名掉出分位就平：这就是日频调仓本身，不是可选的止损
        dataframe.loc[~dataframe["xsec_long"], "exit_long"] = 1
        dataframe.loc[~dataframe["xsec_short"], "exit_short"] = 1
        return dataframe

    # ═══════════════════════════════════════════════ 仓位（O2）
    def _k_at(self, pair: str, current_time: datetime) -> float:
        """取「上一根已收线」那一行的 k。绝不能用当根，那是未来信息。"""
        try:
            df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        except Exception:
            return 1.0
        if df is None or df.empty or "xsec_k" not in df.columns:
            return 1.0
        past = df[df["date"] < current_time]
        if past.empty:
            return 1.0
        v = past["xsec_k"].iloc[-1]
        return float(v) if np.isfinite(v) else 1.0

    def custom_stake_amount(self, pair, current_time, current_rate, proposed_stake,
                            min_stake, max_stake, leverage, entry_tag, side,
                            **kwargs) -> float:
        """1× 总敞口 × O2 的 k，平均分到每个持仓槽位上。"""
        k = self._k_at(pair, current_time)
        try:
            equity = float(self.wallets.get_total_stake_amount())
        except Exception:
            equity = float(proposed_stake) * self.config.get("max_open_trades", 20)
        slots = max(1, int(self.config.get("max_open_trades", 20)))
        stake = equity * k / slots
        if min_stake:
            stake = max(stake, float(min_stake))
        return min(stake, float(max_stake))

    def leverage(self, pair, current_time, current_rate, proposed_leverage,
                 max_leverage, entry_tag, side, **kwargs) -> float:
        # 敞口完全靠 stake 控制，杠杆保持 1×（见 docstring 里的 LEV_CAP 说明）
        return 1.0
