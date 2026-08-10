"""
SMCTradingStrategy — 聪明钱概念策略（15m，完整版 SMC）

═══════════════════════════════════════════════════════════
SMC 六个核心组件：
  1. 市场结构 (Market Structure)：波段高低点 + 趋势方向
  2. 结构突破 (BoS) & 特征改变 (CHoCH)：
     BoS = 延续当前趋势，CHoCH = 趋势可能反转
  3. 订单块 (Order Block)：机构挂单的供需区
  4. 公允价值缺口 (FVG / Imbalance)：价格失衡留下的真空区
  5. 流动性猎杀 (Liquidity Grab)：扫止损后反向
  6. 溢价/折价 (Premium/Discount)：价格在交易区间的位置

入场信号优先级：Order Block 回踩 > FVG 回填 > 流动性猎杀
═══════════════════════════════════════════════════════════
"""

from freqtrade.strategy import IStrategy
from pandas import DataFrame
import numpy as np


class SMCTradingStrategy(IStrategy):

    timeframe = "15m"
    can_short = True

    # ── 仓位 ──
    max_open_trades = 2
    position_adjustment_enable = False
    startup_candle_count = 50

    # ── 止盈止损 ──
    minimal_roi = {"0": 0.10}     # 保证金 +10%（10倍下价格 +1%）
    stoploss = -0.10              # 保证金 -10%（10倍下价格 -1%）

    # ── SMC 参数 ──
    swing_lookback = 30           # 波段识别窗口
    ob_valid_bars = 50            # OB 有效K线数（向前填充）
    fvg_valid_bars = 30           # FVG 有效K线数
    liquidity_lookback = 20       # 流动性猎杀回看窗口

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        n = len(dataframe)
        lookback = min(self.swing_lookback, n // 2)
        close = dataframe["close"].values
        high = dataframe["high"].values
        low = dataframe["low"].values
        open_ = dataframe["open"].values

        # ══════════════════════════════════════════════════════════
        #  1. 市场结构：波段高低点 (Swing High / Swing Low)
        # ══════════════════════════════════════════════════════════
        is_sh = np.zeros(n, dtype=bool)  # Swing High
        is_sl = np.zeros(n, dtype=bool)  # Swing Low

        for i in range(lookback, n - lookback):
            h = high[i]
            l = low[i]
            if h == high[i - lookback : i + lookback + 1].max():
                is_sh[i] = True
            if l == low[i - lookback : i + lookback + 1].min():
                is_sl[i] = True

        dataframe["swing_high"] = is_sh
        dataframe["swing_low"] = is_sl

        # 维护 "最近确认的 N 个波段极值"
        sh_indices = np.where(is_sh)[0]
        sl_indices = np.where(is_sl)[0]

        # 最近的 Swing High 和 Swing Low
        last_sh = np.full(n, np.nan)
        last_sl = np.full(n, np.nan)
        prev_sh = np.full(n, np.nan)   # 上一个 SH
        prev_sl = np.full(n, np.nan)   # 上一个 SL

        sh_val = high[0]
        sl_val = low[0]
        sh_prev = high[0]
        sl_prev = low[0]

        for i in range(n):
            if is_sh[i]:
                sh_prev = sh_val
                sh_val = high[i]
            if is_sl[i]:
                sl_prev = sl_val
                sl_val = low[i]
            last_sh[i] = sh_val
            last_sl[i] = sl_val
            prev_sh[i] = sh_prev
            prev_sl[i] = sl_prev

        dataframe["last_sh"] = last_sh
        dataframe["last_sl"] = last_sl
        dataframe["prev_sh"] = prev_sh
        dataframe["prev_sl"] = prev_sl

        # ══════════════════════════════════════════════════════════
        #  2. BoS (结构突破) & CHoCH (特征改变)
        # ══════════════════════════════════════════════════════════
        # BoS: 延续趋势
        #   Bullish BoS: close 突破上一个 Swing High（上涨趋势中创新高）
        #   Bearish BoS: close 跌破上一个 Swing Low（下跌趋势中创新低）
        # CHoCH: 趋势改变
        #   Bullish CHoCH: 在跌势中，close 突破最新的 Swing High
        #   Bearish CHoCH: 在涨势中，close 跌破最新的 Swing Low

        bos_bull = np.zeros(n, dtype=bool)
        bos_bear = np.zeros(n, dtype=bool)
        choch_bull = np.zeros(n, dtype=bool)
        choch_bear = np.zeros(n, dtype=bool)

        # 跟踪当前趋势：1=上升, -1=下降, 0=不明
        trend = np.zeros(n, dtype=int)

        for i in range(2, n):
            c = close[i]
            sh = last_sh[i - 1]
            sl = last_sl[i - 1]
            psh = prev_sh[i - 1]
            psl = prev_sl[i - 1]

            # 检测 BoS
            if not np.isnan(psh) and c > psh:
                bos_bull[i] = True
                trend[i] = 1
            elif not np.isnan(psl) and c < psl:
                bos_bear[i] = True
                trend[i] = -1
            else:
                trend[i] = trend[i - 1]

            # 检测 CHoCH（趋势反转）
            if trend[i - 1] == -1 and c > sh and not np.isnan(sh):
                choch_bull[i] = True
                trend[i] = 1
            elif trend[i - 1] == 1 and c < sl and not np.isnan(sl):
                choch_bear[i] = True
                trend[i] = -1

        dataframe["bos_bull"] = bos_bull
        dataframe["bos_bear"] = bos_bear
        dataframe["choch_bull"] = choch_bull
        dataframe["choch_bear"] = choch_bear
        dataframe["trend"] = trend

        # ══════════════════════════════════════════════════════════
        #  3. 订单块 (Order Block)
        # ══════════════════════════════════════════════════════════
        # 做多 OB：BoS/CHoCH 前最后一段下跌中的连续阴线区域
        # 做空 OB：BoS/CHoCH 前最后一段上涨中的连续阳线区域

        ob_high = np.full(n, np.nan)
        ob_low = np.full(n, np.nan)
        ob_type = np.full(n, "", dtype=object)

        for i in range(3, n):
            # 做多 OB（BoS 或 CHoCH 看涨）
            if bos_bull[i] or choch_bull[i]:
                ob_h = 0.0
                ob_l = 1e18
                found = False
                # 向前收集连续阴线（最多 8 根）
                for j in range(i - 1, max(i - 9, 0), -1):
                    if close[j] <= open_[j]:  # 阴线
                        ob_h = max(ob_h, high[j])
                        ob_l = min(ob_l, low[j])
                        found = True
                    elif found:
                        break  # 遇到阳线，OB 收集结束
                if found:
                    ob_high[i] = ob_h
                    ob_low[i] = ob_l
                    ob_type[i] = "bull"

            # 做空 OB（BoS 或 CHoCH 看跌）
            if bos_bear[i] or choch_bear[i]:
                ob_h = 0.0
                ob_l = 1e18
                found = False
                for j in range(i - 1, max(i - 9, 0), -1):
                    if close[j] >= open_[j]:  # 阳线
                        ob_h = max(ob_h, high[j])
                        ob_l = min(ob_l, low[j])
                        found = True
                    elif found:
                        break
                if found:
                    ob_high[i] = ob_h
                    ob_low[i] = ob_l
                    ob_type[i] = "bear"

        dataframe["ob_high"] = ob_high
        dataframe["ob_low"] = ob_low
        dataframe["ob_type"] = ob_type

        # ══════════════════════════════════════════════════════════
        #  4. 公允价值缺口 (FVG / Imbalance)
        # ══════════════════════════════════════════════════════════
        # 3 根K线构成：中间K线跳空，第1根和第3根不重叠
        # Bullish FVG：low[i] > high[i+2] → 下跌缺口，价格回来填时做多
        # Bearish FVG：high[i] < low[i+2] → 上涨缺口，价格回来填时做空

        fvg_high = np.full(n, np.nan)
        fvg_low = np.full(n, np.nan)
        fvg_type = np.full(n, "", dtype=object)
        fvg_active = np.full(n, False)

        for i in range(n - 3):
            # Bearish FVG (上涨缺口 → 做空)：i 的高点低于 i+2 的低点
            if high[i] < low[i + 2]:
                fvg_low[i + 2] = high[i]
                fvg_high[i + 2] = low[i + 2]
                fvg_type[i + 2] = "bear"
            # Bullish FVG (下跌缺口 → 做多)：i 的低点高于 i+2 的高点
            if low[i] > high[i + 2]:
                fvg_high[i + 2] = low[i]
                fvg_low[i + 2] = high[i + 2]
                fvg_type[i + 2] = "bull"

        # FVG 有效期：价格回填后失效
        for i in range(n):
            if fvg_type[i] in ("bull", "bear"):
                # 检查后续价格是否已进入 FVG
                fvg_active[i] = True
                for k in range(i + 1, min(i + self.fvg_valid_bars, n)):
                    if fvg_low[i] <= low[k] <= fvg_high[i] or fvg_low[i] <= high[k] <= fvg_high[i]:
                        fvg_active[i] = True
                        break
                    # 如果价格直接穿过去了（超过 FVG 上沿 2 倍范围），FVG 失效
                    if (fvg_type[i] == "bull" and low[k] < fvg_low[i] - (fvg_high[i] - fvg_low[i])) or \
                       (fvg_type[i] == "bear" and high[k] > fvg_high[i] + (fvg_high[i] - fvg_low[i])):
                        fvg_active[i] = False
                        break

        dataframe["fvg_high"] = fvg_high
        dataframe["fvg_low"] = fvg_low
        dataframe["fvg_type"] = fvg_type
        dataframe["fvg_active"] = fvg_active

        # ══════════════════════════════════════════════════════════
        #  5. 流动性猎杀 (Liquidity Grab / Stop Hunt)
        # ══════════════════════════════════════════════════════════
        # 价格短暂刺破波段高低点后快速收回 → 市场在扫止损
        lg_bull = np.zeros(n, dtype=bool)  # 空头陷阱（扫了多头止损）
        lg_bear = np.zeros(n, dtype=bool)  # 多头陷阱（扫了空头止损）

        for i in range(lookback + 2, n):
            window_low = low[i - lookback : i].min()
            window_high = high[i - lookback : i].max()

            # 空头陷阱：价格跌破近期低点后立刻收回
            if low[i] < window_low and close[i] > window_low:
                lg_bull[i] = True
            # 多头陷阱：价格涨破近期高点后立刻收回
            if high[i] > window_high and close[i] < window_high:
                lg_bear[i] = True

        dataframe["lg_bull"] = lg_bull  # 扫多止损 → 做多信号
        dataframe["lg_bear"] = lg_bear  # 扫空止损 → 做空信号

        # ═══ 向前填充 OB 和 FVG，使其持续有效 ═══
        self._ffill_ob(dataframe)

        return dataframe

    def _ffill_ob(self, dataframe: DataFrame):
        """向前填充 Order Block：形成后持续有效"""
        n = len(dataframe)
        last_type = ""
        last_high = np.nan
        last_low = np.nan
        countdown = 0

        for i in range(n):
            cur = dataframe["ob_type"].iloc[i]
            if cur in ("bull", "bear"):
                last_type = cur
                last_high = dataframe["ob_high"].iloc[i]
                last_low = dataframe["ob_low"].iloc[i]
                countdown = self.ob_valid_bars
            elif countdown > 0:
                dataframe.loc[dataframe.index[i], "ob_type"] = last_type
                dataframe.loc[dataframe.index[i], "ob_high"] = last_high
                dataframe.loc[dataframe.index[i], "ob_low"] = last_low
                countdown -= 1

        # FVG 同样向前填充
        last_type = ""
        last_high = np.nan
        last_low = np.nan
        countdown = 0
        for i in range(n):
            cur = dataframe["fvg_type"].iloc[i]
            if cur in ("bull", "bear"):
                last_type = cur
                last_high = dataframe["fvg_high"].iloc[i]
                last_low = dataframe["fvg_low"].iloc[i]
                countdown = self.fvg_valid_bars
            elif countdown > 0:
                dataframe.loc[dataframe.index[i], "fvg_type"] = last_type
                dataframe.loc[dataframe.index[i], "fvg_high"] = last_high
                dataframe.loc[dataframe.index[i], "fvg_low"] = last_low
                countdown -= 1

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["enter_long"] = 0
        dataframe["enter_short"] = 0

        # ═══ 做多信号 ═══

        ob_bull = (dataframe["ob_type"] == "bull") & dataframe["ob_high"].notna()
        price_touch_ob = (
            (dataframe["low"] <= dataframe["ob_high"])
            & (dataframe["close"] >= dataframe["ob_low"])
        )
        enter_long_ob = ob_bull & price_touch_ob

        fvg_bull = (dataframe["fvg_type"] == "bull") & dataframe["fvg_high"].notna()
        price_touch_fvg = (
            (dataframe["close"] <= dataframe["fvg_high"])
            & (dataframe["close"] >= dataframe["fvg_low"])
        )
        enter_long_fvg = fvg_bull & price_touch_fvg

        enter_long_lg = dataframe["lg_bull"]

        dataframe.loc[enter_long_ob | enter_long_fvg | enter_long_lg, "enter_long"] = 1

        # ═══ 做空信号 ═══

        ob_bear = (dataframe["ob_type"] == "bear") & dataframe["ob_high"].notna()
        price_touch_ob_bear = (
            (dataframe["high"] >= dataframe["ob_low"])
            & (dataframe["close"] <= dataframe["ob_high"])
        )
        enter_short_ob = ob_bear & price_touch_ob_bear

        fvg_bear = (dataframe["fvg_type"] == "bear") & dataframe["fvg_high"].notna()
        price_touch_fvg_bear = (
            (dataframe["close"] <= dataframe["fvg_high"])
            & (dataframe["close"] >= dataframe["fvg_low"])
        )
        enter_short_fvg = fvg_bear & price_touch_fvg_bear

        enter_short_lg = dataframe["lg_bear"]

        dataframe.loc[enter_short_ob | enter_short_fvg | enter_short_lg, "enter_short"] = 1

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["exit_long"] = 0
        dataframe["exit_short"] = 0
        return dataframe
