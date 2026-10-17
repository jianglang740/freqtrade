"""
Ema21TwoTfAtr — 1m 触发 / 5m EMA21 判据的双周期趋势策略（多空双向，ATR 止损止盈）

═══════════════════════════════════════════════════════
这是把作者「伦敦金 1m 站上 5m EMA21 做单」的设想落成可跑、可对照的版本。

  入场   1m 收盘价 上穿 5m EMA21 → 做多；下穿 → 做空
  过滤   效率比 ER（衡量震荡/重叠程度）+ ATR 分位（压缩区）+ EMA21 归一化斜率
  止损   stop_mult × ATR(5m)，入场时冻结；盈利走满 arm_R 个 R 后抬到保本位
  止盈   tp_mult × ATR(5m)，入场时冻结，**随持仓时间递减**
  仓位   固定手数 或 固定风险（每笔风险恒定），两种口径可切换

两处与作者原意不同的地方，都做成了开关，可以关掉退回原版对照：

  1. **入场逻辑做了两版**（`entry_mode`）。原设想的「站上 EMA21」与「偏离 > k×ATR」
     在逻辑上是互斥的 —— 穿越的定义就是 close − ema21 ≈ 0，穿越那一刻偏离必然很小。
     * "cross" = 原意：纯穿越事件，偏离/斜率只作为穿越后 confirm_bars 根的确认窗口
     * "state" = 状态式：站上 且 偏离 > k_dev×ATR 且 斜率 > s_min，状态由假转真即触发
  2. **震荡过滤器换成了 ER + ATR 分位**（`use_filters`）。作者原本想用「K 线重叠程度」
     和「涨跌幅」两个指标，但这两个正是 Kaufman 效率比的两半：
     ER = |close_t − close_{t−N}| / Σ|close_i − close_{i−1}| —— 分子是净位移（涨跌幅），
     分母是总路程，路程远大于净位移就是来回震荡（重叠程度）。一个无量纲的数同时回答
     两个问题，且与价格水平无关，比两个手调参数更难过拟合。关掉 `use_filters` 即退回
     「只看 EMA21」的原始设想。

═══════════════════════════════════════════════════════
参数全部**事先定死，不做任何参数搜索**（本仓库硬约定，见 开发执行记录.md 的诚实性约束）。
要对照就把类常量改掉重跑，不要用 hyperopt。

═══════════════════════════════════════════════════════
回测命令：

  # XAU 主口径（数据起点 2025-12-12，见 K线数据报告.md）
  freqtrade backtesting -s Ema21TwoTfAtr -c user_data/config/config_xau_1m.json \
    -i 1m --timerange 20251212-20261011 --fee 0.00003 --export trades

  # 毛值对照（fee=0，诚实性要求必跑）
  freqtrade backtesting -s Ema21TwoTfAtr -c user_data/config/config_xau_1m.json \
    -i 1m --timerange 20251212-20261011 --fee 0 --export trades

⚠️ **成本口径必须说清楚**：freqtrade 的 `--fee` 在**开仓和清仓各收一次**，而伦敦金
实盘成本是点差、**往返只过路一次**。所以点差要**折半**填进 `--fee`：
点差 $0.25/盎司 ÷ 金价约 4200 ≈ 往返 0.006% → `--fee 0.00003`。
这个 0.25 是假设值，**要用作者实际场地的点差替换**；方案里带 4 档敏感性
（0 / 0.00003 / 0.00006 / 0.00012）就是为了兜住这个假设。

═══════════════════════════════════════════════════════
几个查过源码确认的机制（都是会静默出错、不会报错的坑）：

* ⚠️ **custom_stoploss 返回的是「相对 current_rate 的比例」，不是止损价**
  （interface.py:457-475）。所以必须先算绝对止损价，再用 stoploss_from_absolute
  换算。语义写反不会报错，只会得到一个完全错误的止损距离。

* ⚠️ **回测里 custom_stoploss 收到的 current_rate 是本根 K 线的有利极值**
  （多 = high，空 = low；interface.py:1559/:1573），不是收盘价。这里只拿它做
  「绝对价 → 相对比率」的换算，框架再用 current_price*(1-ratio) 换回绝对价
  （trade_model.py:858-862），换算回去正好等于我们要的止损价，所以这个非直觉的
  入参**不影响**最终止损价。

* ⚠️ **self.stoploss 是硬上限，自定义止损不可能比它更宽**
  （trade_model.py:883-893 止损只朝有利方向单向移动）。这里它的**唯一作用**
  是 `_entry_atr` 取不到值时兜底，所以必须**可验证地**永不生效。

  **单位先记清楚：stoploss 是比率，`-0.25` 是 −25%，不是 −0.25%。** 这一条
  写在这里是因为我真的看错过一次 —— 一度把 `-0.25` 读成 0.25%，据此算出
  「约四分之一的入场止损被压扁」，还差点当成结论报出去。实际按 XAU 的 5m
  ATR14 实测分布（2025-12-12 起）核对：

  | 分位 | ATR5m | 2×ATR | 5×ATR |
  |---|---|---|---|
  | 中位 | 0.087% | 0.174% | 0.435% |
  | p90 | 0.180% | 0.360% | 0.899% |
  | max | 1.133% | 2.265% | 5.664% |

  本样本最大的 5×ATR 才 5.66%，对 `-0.25`（25%）有 4.4 倍余量、对最大的
  2×ATR（2.27%）有 11 倍余量 —— **它是真的不生效，不是「看起来够宽」**。
  换 `-0.10` 也一样不生效，实测两种取值回测输出逐字相同。结论：这类兜底
  常数要用数据核对余量，而不是凭感觉说一句「够宽了」。

* ⚠️ **内置 trailing_stop 与 custom_stoploss 是叠加关系，不是互斥，且无任何警告**
  （interface.py:1566-1603 两段依次执行）。本策略自己实现抬高止损，所以
  **必须显式 trailing_stop = False**，否则两套止损逻辑同时生效。

* ⚠️ **同根 K 线内出场优先级：exit-signal → stoploss → ROI → trailing**
  （interface.py:1504-1520）。止损优先于止盈，偏悲观。

* **止损与 ROI 都用 K 线 high/low 判定，且止损假设「正好成交在止损价」**
  （interface.py:1635-1636；backtesting.py:597-649）。作者的实盘引擎只收收盘价，
  这里存在系统性乐观。但主周期就是 1m（最细粒度），该偏差被**单根 1m K 线的振幅**
  限死 —— ATR(1m) 中位约 0.032% 价格，相对 2×ATR(5m) 的止损距离（约 0.174%）
  约 18%。**是有界偏差，不是无限乐观**，报告里要量化交代。

* **信号在「下一根开盘」成交**，框架回测前把信号整体 shift 了一根
  （backtesting.py:554-567）。所以 trade.open_date 是**入场那根**，而开仓决策用的是
  **信号那根**的信息 —— 冻结 ATR 时取的是信号那根的 ATR，因此 populate_indicators
  里多存一列 atr_5m_prev（见 _entry_atr）。

* **@informative 的 startup_candle_count 语义**：框架对**每个周期**都按这个数值
  回溯各自的根数（dataprovider.py:319-321）。所以它要满足 **5m 的最大窗口**
  （atr_rank 用 200 根 5m），设 300。别写成「300 根 1m」去理解 —— 那是单位混用。

* 回测模式**完全禁用数据库**且强制 dry_run=True（backtesting.py:152、:483-484）。
  custom_data（用来缓存入场 ATR）在回测下是**内存实现**（usedb_context.py:14），可用。

* **前视安全**：@informative 合并 5m 时的日期平移由框架保证
  （strategy_helper.py:34-47）：5m 那根的值只在它**收盘那一刻**对应的 1m 行上出现，
  窗口内其余 1m 行沿用上一根已完成的 5m。所以不需要自己做任何 shift(-) 之类的处理，
  但**也不能绕过 merge_informative_pair 自己合并**。
═══════════════════════════════════════════════════════
"""

from datetime import datetime

import pandas as pd
import talib.abstract as ta
from pandas import DataFrame

from freqtrade.persistence import Trade
from freqtrade.strategy import IStrategy, informative, stoploss_from_absolute


class Ema21TwoTfAtr(IStrategy):

    INTERFACE_VERSION = 3

    # ── 基础设置 ──
    timeframe = "1m"
    can_short = True
    # 单位是「根」，框架对 1m 和 5m 各自回溯这么多根。
    # 5m 侧最大的窗口是 atr_rank 的 200 根，加上 talib 的暖机，留到 300。
    startup_candle_count = 300

    # ── 出场开关 ──
    # 兜底硬止损。**注意单位是比率：-0.25 是 −25%**。这里只为 `_entry_atr`
    # 取不到值的情况兜底，必须可验证地永不生效 —— 本样本最大的 5×ATR 才 5.66%，
    # 25% 有 4.4 倍余量（见 docstring 的核对表）。
    stoploss = -0.25
    # ⚠️ 必须关：内置跟踪止损会与 custom_stoploss 叠加且无任何警告。
    trailing_stop = False
    use_custom_stoploss = True
    # ROI 走 custom_roi（需要 use_custom_roi）。minimal_roi 留一个永不触发的哨兵值，
    # 框架取「custom_roi 与 minimal_roi 中更低的那个」作为阈值（interface.py:1712-1717）。
    use_custom_roi = True
    minimal_roi = {"0": 10.0}

    # ═══════════════════════════════════════
    # 参数（事先定死，不做搜索）
    # ═══════════════════════════════════════

    # 入场版本："cross" = 作者原意（穿越事件）；"state" = 状态式
    entry_mode = "cross"
    confirm_bars = 3          # cross 版：穿越后允许入场的确认窗口（根 1m）

    # 状态式入场的门槛（entry_mode="state" 时才用）
    k_dev = 0.5               # 要求 close 偏离 ema21 超过 k_dev × ATR(5m)
    s_min = 0.05              # 要求归一化斜率超过 s_min（ema21.diff() / atr，单位：ATR/根）

    # 震荡过滤（use_filters=False 即退回「只看 EMA21」的原版设想）
    use_filters = True
    er_min = 0.30             # Kaufman 效率比下限（>0.3 视为有方向）
    atr_rank_min = 0.20       # ATR 在过去 200 根 5m 中的分位下限（排除极度压缩区）

    # 出场距离（单位：ATR(5m)，入场时冻结）
    stop_mult = 2.0
    tp_mult = 2.0

    # 止盈随时间递减：目标每 tp_half_life_h 小时减半，但不低于 tp_mult_floor
    tp_half_life_h = 4.0
    tp_mult_floor = 0.5

    # 保护性止损（抬高止损）：顺势走满 arm_R 个 R 后，把止损移到锁定 lock_R 个 R 的位置
    use_breakeven = True
    arm_R = 1.0
    lock_R = 0.0              # 0.0 = 保本；0.5 = 锁半个 R 的利润

    # 仓位口径："fixed" = 每次固定名义金额；"risk" = 每笔风险恒定
    sizing_mode = "fixed"
    fixed_stake = 1000.0      # sizing_mode="fixed" 时每笔的名义金额（USDT）
    risk_per_trade = 0.005    # sizing_mode="risk" 时，每笔风险 = 权益的 0.5%
    #
    # ⚠️ 两种口径都由 custom_stake_amount 接管，所以 config 里的 stake_amount
    # 必须设成 "unlimited"，否则 proposed_stake 会先被 config 定死再传进来。
    #
    # ⚠️ 作者说的「固定 0.3 手伦敦金」在这里表达为固定名义金额。原因：1 标准手 = 100 盎司，
    # 0.3 手 ≈ 30 盎司 ≈ 12.6 万 USDT 名义，而回测钱包只有 1 万 USDT、杠杆锁 1 倍，
    # 装不下。但**这个换算不影响任何结论** —— 胜率、R 倍数、盈亏比、cost_R
    # 全部与仓位大小无关，绝对收益只是线性缩放。所以用 1000 USDT 固定名义跑，
    # 报告里再把结论换算回 0.3 手的量级即可。

    # ═══════════════════════════════════════
    # 5m 判据（@informative 自动合并，列名带 _5m 后缀）
    # ═══════════════════════════════════════

    @informative("5m")
    def populate_indicators_5m(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema21"] = ta.EMA(dataframe, timeperiod=21)
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=14)

        # Kaufman 效率比：净位移 / 总路程。1 = 单边走，0 = 原地来回。
        n = 20
        change = dataframe["close"].diff(n).abs()
        volatility = dataframe["close"].diff().abs().rolling(n).sum()
        dataframe["er"] = (change / volatility).fillna(0.0)

        # ATR 在过去 200 根 5m 中的分位（0~1）。低分位 = 压缩区。
        # 用 Rolling.rank 而不是 rolling().apply(lambda ...)：后者在 8.7 万行上太慢。
        dataframe["atr_rank"] = (
            dataframe["atr"].rolling(200, min_periods=50).rank(pct=True).fillna(0.0)
        )

        # 归一化斜率：单位是「ATR/根」。用裸 diff() 的美元斜率会随金价水平漂移，
        # 参数不可移植（金价 2000 和 4200 时同一个「走平」对应的美元斜率差一倍）。
        dataframe["ema21_slope_norm"] = dataframe["ema21"].diff() / dataframe["atr"]

        return dataframe

    # ═══════════════════════════════════════
    # 1m 主周期
    # ═══════════════════════════════════════

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # 此时 dataframe 里已经有 _5m 后缀的列（@informative 自动合并）。
        #
        # 冻结「入场时的 ATR」要用信号那根的，而信号在下一根开盘成交
        # （backtesting.py:554-567），直接读入场那根会把入场 K 线自己的振幅
        # 算进止损宽度里。所以再 shift 一根，语义与 TurtleBreakout.atr_prev 一致。
        dataframe["atr_5m_prev"] = dataframe["atr_5m"].shift(1)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        close = dataframe["close"]
        ema = dataframe["ema21_5m"]
        atr = dataframe["atr_5m"]

        above = close > ema
        below = close < ema

        # ── 震荡过滤 ──
        if self.use_filters:
            not_chop = (dataframe["er_5m"] >= self.er_min) & (
                dataframe["atr_rank_5m"] >= self.atr_rank_min
            )
        else:
            not_chop = pd.Series(True, index=dataframe.index)

        # ── 触发 ──
        if self.entry_mode == "cross":
            # 作者原意：穿越事件。偏离与斜率不参与触发，只在穿越后的窗口内做确认，
            # 这样才不与「穿越那一刻偏离必然≈0」相矛盾。
            raw_up = above & (close.shift(1) <= ema.shift(1))
            raw_dn = below & (close.shift(1) >= ema.shift(1))
            trig_long = (
                raw_up.rolling(self.confirm_bars, min_periods=1).max().fillna(0).astype(bool)
                & above
            )
            trig_short = (
                raw_dn.rolling(self.confirm_bars, min_periods=1).max().fillna(0).astype(bool)
                & below
            )
        else:
            # 状态式：站上 + 偏离足够 + 斜率足够。状态由假转真即首次触发。
            dev = (close - ema) / atr
            slope = dataframe["ema21_slope_norm_5m"]
            long_state = above & (dev > self.k_dev) & (slope > self.s_min)
            short_state = below & (dev < -self.k_dev) & (slope < -self.s_min)
            trig_long = long_state & ~long_state.shift(1).fillna(False).astype(bool)
            trig_short = short_state & ~short_state.shift(1).fillna(False).astype(bool)

        valid = dataframe["atr_5m_prev"].notna() & (dataframe["volume"] > 0)

        dataframe["enter_long"] = 0
        dataframe["enter_short"] = 0
        dataframe.loc[(trig_long & not_chop & valid), "enter_long"] = 1
        dataframe.loc[(trig_short & not_chop & valid), "enter_short"] = 1

        # 两版都带上 enter_tag，方便按入场版本拆开看出场原因分布
        dataframe.loc[dataframe["enter_long"] == 1, "enter_tag"] = f"ema21_{self.entry_mode}_long"
        dataframe.loc[dataframe["enter_short"] == 1, "enter_tag"] = f"ema21_{self.entry_mode}_short"
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # 刻意**不做**信号出场：作者的设想里出场只有三种 ——
        # 硬止损、保护性止损、随时间递减的硬止盈。反转信号不做出场（也只做单向持仓）。
        # 这是刻意的范围取舍，不是遗漏。代价是：反向信号出现时若已有持仓，不会被采用。
        dataframe["exit_long"] = 0
        dataframe["exit_short"] = 0
        return dataframe

    # ═══════════════════════════════════════
    # 冻结「入场时的 ATR」
    # ═══════════════════════════════════════

    def _entry_atr(self, trade: Trade) -> float | None:
        """
        取出这笔交易开仓时冻结的 ATR(5m)（信号那根的）。

        照抄 TurtleBreakout._entry_n 的写法，理由也一样：信号在下一根开盘成交，
        trade.open_date 指向入场那根，所以按 open_date 定位后读 atr_5m_prev，
        拿到的正好是信号那根的 ATR。取不到就返回 None —— 调用方退回 stoploss
        兜底，而不是拿一个编出来的 ATR 去下止损单。
        """
        cached = trade.get_custom_data("ema21_entry_atr")
        if cached is not None:
            return float(cached)

        dataframe, _ = self.dp.get_analyzed_dataframe(trade.pair, self.timeframe)
        if dataframe is None or dataframe.empty:
            return None

        # side="right" 再减 1 = 「最后一根不晚于 open_date 的 K 线」，不会越界。
        pos = int(
            dataframe["date"].searchsorted(pd.Timestamp(trade.open_date_utc), side="right")
        ) - 1
        if pos < 0:
            return None

        atr = dataframe["atr_5m_prev"].iloc[pos]
        if pd.isna(atr) or float(atr) <= 0:
            return None

        trade.set_custom_data("ema21_entry_atr", float(atr))
        return float(atr)

    # ═══════════════════════════════════════
    # 止损：ATR 硬止损 + 保护性止损（抬高）
    # ═══════════════════════════════════════

    def custom_stoploss(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        after_fill: bool,
        **kwargs,
    ) -> float | None:
        atr = self._entry_atr(trade)
        if atr is None:
            return None   # 拿不到 ATR 就不设自定义止损，退回 stoploss 兜底

        entry = trade.open_rate
        stop_dist = self.stop_mult * atr
        if stop_dist <= 0:
            return None

        if trade.is_short:
            stop_price = entry + stop_dist
            # 保护性止损：顺势（下跌）走满 arm_R 个 R 后，把止损下移到锁定位
            r_now = (entry - current_rate) / stop_dist
            if self.use_breakeven and r_now >= self.arm_R:
                stop_price = entry - self.lock_R * stop_dist
        else:
            stop_price = entry - stop_dist
            r_now = (current_rate - entry) / stop_dist
            if self.use_breakeven and r_now >= self.arm_R:
                stop_price = entry + self.lock_R * stop_dist

        ratio = stoploss_from_absolute(
            stop_price, current_rate, is_short=trade.is_short, leverage=trade.leverage or 1.0
        )
        # stoploss_from_absolute 在「想要的止损价已越过现价」时会钳到 0，
        # 而 0 表示「止损就设在现价」= 立刻出场。那不是我们想要的，退回上一版止损。
        if ratio <= 0:
            return None
        return ratio

    # ═══════════════════════════════════════
    # 止盈：ATR 目标 + 随时间递减
    # ═══════════════════════════════════════

    def custom_roi(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        trade_duration: int,
        entry_tag: str | None,
        side: str,
        **kwargs,
    ) -> float | None:
        atr = self._entry_atr(trade)
        if atr is None or trade.open_rate <= 0:
            return None

        # 按入场 ATR 缩放的初始目标
        mult = self.tp_mult
        # 随时间递减：每 tp_half_life_h 小时减半，但不低于 tp_mult_floor。
        # 语义是「拿得越久，越不该继续等一个大目标」。
        hours = trade_duration / 60.0
        mult *= 0.5 ** (hours / self.tp_half_life_h)
        mult = max(mult, self.tp_mult_floor)

        return (mult * atr) / trade.open_rate

    # ═══════════════════════════════════════
    # 仓位：固定手数 或 固定风险
    # ═══════════════════════════════════════

    def custom_stake_amount(
        self,
        pair: str,
        current_time: datetime,
        current_rate: float,
        proposed_stake: float,
        min_stake: float | None,
        max_stake: float,
        leverage: float,
        entry_tag: str | None,
        side: str,
        **kwargs,
    ) -> float:
        if self.sizing_mode != "risk" or current_rate <= 0:
            # 固定名义金额。仍然夹在 min/max 之间，避免交易所下限把单子吃掉。
            stake = self.fixed_stake
            if min_stake is not None:
                stake = max(stake, min_stake)
            return min(stake, max_stake)

        # 用当前信号那根的 ATR 估止损距离（此时 analyzed dataframe 的最后一根
        # 就是信号那根，合法用法，参考 04_DCAStrategy.py:133 的同款写法）。
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if dataframe is None or dataframe.empty:
            return proposed_stake
        atr = dataframe["atr_5m_prev"].iloc[-1]
        if pd.isna(atr) or float(atr) <= 0:
            return proposed_stake

        stop_dist_pct = (self.stop_mult * float(atr)) / current_rate
        if stop_dist_pct <= 0:
            return proposed_stake

        risk_amount = self.wallets.get_total_stake_amount() * self.risk_per_trade
        stake = risk_amount / stop_dist_pct
        if min_stake is not None:
            stake = max(stake, min_stake)
        return min(stake, max_stake)

    def leverage(
        self,
        pair: str,
        current_time: datetime,
        current_rate: float,
        proposed_leverage: float,
        max_leverage: float,
        entry_tag: str | None,
        side: str,
        **kwargs,
    ) -> float:
        # 固定 1 倍。本项目所有策略都锁定 1 倍杠杆，不做杠杆优化 ——
        # 一放大杠杆回撤跟着放大，不同组的收益曲线就没有可比性。
        return 1.0
