"""
CrossSectionalMomentum — 横截面动量因子多空策略

思路：
    每天对票池里所有币算「后视 N 日收益率」，再按当日截面做分位排名（0~1）。
    排名靠前的一档做多、靠后的一档做空，等权持有，直到排名跌出对应区间。

为什么这样算没有前视：
    回测里 populate_indicators 对整个历史一次性向量化执行，get_pair_dataframe
    返回的是全时段数据（官方文档 docs/strategy-customization.md 明确警告）。
    因此每个时点的取值必须只依赖「≤ 该时点」的数据。
    本策略只用 shift / 按日截面排名这类后视运算，不使用任何全样本统计量
    （全样本均值、全样本标准差、全样本排名都会引入前视）。

    日期 t 的排名 = 用 t 日各币的后视动量做截面排序，t 日动量只用到 ≤t 的收盘价
    → 无前视。

参数对比：回看天数 × 分位，见文件末尾的 9 个子类。
"""

import pandas as pd
from pandas import DataFrame

from freqtrade.strategy import IStrategy


# 进程级缓存：避免每个 pair 都重建一次截面排名表（94 对 × 94 对）
# key = (回看天数, 票池规模, 数据最新日期)  →  只保留最新一份
_RANK_CACHE: dict = {}


class CrossSectionalMomentum(IStrategy):
    INTERFACE_VERSION = 3

    # ═══════════════════════════════════════
    # 基础设置
    # ═══════════════════════════════════════
    timeframe = "1d"
    can_short = True                 # 合约模式下允许做空
    process_only_new_candles = True
    startup_candle_count = 100       # ≥ 最大回看天数(90) + 缓冲

    # ═══════════════════════════════════════
    # 止盈止损：关闭，纯靠排名变化进出
    #   固定 ROI 设成天文数字使永不触发；stoploss 设成 -0.99 等于不设
    #   5 倍杠杆下爆仓由 freqtrade 的强平逻辑处理
    # ═══════════════════════════════════════
    minimal_roi = {"0": 100.0}
    stoploss = -0.99
    use_custom_stoploss = False

    # ═══════════════════════════════════════
    # 因子参数（子类覆盖）
    # ═══════════════════════════════════════
    mom_lookback = 30        # 后视天数
    quantile = 0.20          # 多/空各取的分位（0.2 = 前后各 20%）
    leverage_value = 5.0     # 固定杠杆倍数

    def leverage(
        self,
        pair: str,
        current_time,
        current_rate: float,
        proposed_leverage: float,
        max_leverage: float,
        entry_tag,
        side: str,
        **kwargs,
    ) -> float:
        """固定 5 倍杠杆，但不超过交易所对该 pair 的上限。"""
        return min(self.leverage_value, max_leverage)

    # ═══════════════════════════════════════
    # 截面排名表（核心）
    # ═══════════════════════════════════════
    def _build_rank_table(self) -> pd.DataFrame | None:
        """
        构建「日期 × 币」的截面分位排名表。
        - 动量 = close / close.shift(N) - 1        （后视）
        - 排名 = 按日对动量做 pct 排名（0~1）       （只用当日截面）
        结果缓存，键含回看天数与数据最新日期；实盘下新 K 线到达时自动失效重建。
        """
        if self.dp is None:
            return None

        pairs = self.dp.current_whitelist()
        closes: dict = {}
        last_date = None
        for p in pairs:
            df = self.dp.get_pair_dataframe(p, self.timeframe)
            if df is None or df.empty:
                continue
            s = df.set_index("date")["close"]
            closes[p] = s
            d = s.index.max()
            last_date = d if last_date is None else max(last_date, d)

        if not closes:
            return None

        key = (self.mom_lookback, len(pairs), last_date)
        if key in _RANK_CACHE:
            return _RANK_CACHE[key]

        wide = pd.DataFrame(closes).sort_index()
        mom = wide / wide.shift(self.mom_lookback) - 1.0   # 后视动量
        rank = mom.rank(axis=1, pct=True)                  # 按日截面分位排名

        _RANK_CACHE.clear()          # 只保留最新一份，防内存膨胀
        _RANK_CACHE[key] = rank
        return rank

    # ═══════════════════════════════════════
    # 三个核心方法
    # ═══════════════════════════════════════
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        rank = self._build_rank_table()
        pair = metadata["pair"]
        if rank is not None and pair in rank.columns:
            # 按日期对齐取本对的排名；新币样本不足时该列为 NaN
            dataframe["mom_rank"] = rank[pair].reindex(dataframe["date"]).to_numpy()
        else:
            dataframe["mom_rank"] = float("nan")
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["enter_long"] = 0
        dataframe["enter_short"] = 0

        top = dataframe["mom_rank"] >= (1.0 - self.quantile)   # 动量最强档 → 做多
        bot = dataframe["mom_rank"] <= self.quantile           # 动量最弱档 → 做空
        valid = dataframe["volume"] > 0

        dataframe.loc[top & valid, ["enter_long", "enter_tag"]] = (1, "mom_top")
        dataframe.loc[bot & valid, ["enter_short", "enter_tag"]] = (1, "mom_bottom")
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["exit_long"] = 0
        dataframe["exit_short"] = 0

        # 排名跌出多头区 / 升出空头区 → 平仓
        dataframe.loc[dataframe["mom_rank"] < (1.0 - self.quantile), "exit_long"] = 1
        dataframe.loc[dataframe["mom_rank"] > self.quantile, "exit_short"] = 1
        return dataframe


# ═══════════════════════════════════════
# 参数对比子类：回看 {7, 30, 90} × 分位 {10%, 20%, 30%}
# ═══════════════════════════════════════
class Mom_L7_Q10(CrossSectionalMomentum):
    mom_lookback = 7
    quantile = 0.10


class Mom_L7_Q20(CrossSectionalMomentum):
    mom_lookback = 7
    quantile = 0.20


class Mom_L7_Q30(CrossSectionalMomentum):
    mom_lookback = 7
    quantile = 0.30


class Mom_L30_Q10(CrossSectionalMomentum):
    mom_lookback = 30
    quantile = 0.10


class Mom_L30_Q20(CrossSectionalMomentum):
    mom_lookback = 30
    quantile = 0.20


class Mom_L30_Q30(CrossSectionalMomentum):
    mom_lookback = 30
    quantile = 0.30


class Mom_L90_Q10(CrossSectionalMomentum):
    mom_lookback = 90
    quantile = 0.10


class Mom_L90_Q20(CrossSectionalMomentum):
    mom_lookback = 90
    quantile = 0.20


class Mom_L90_Q30(CrossSectionalMomentum):
    mom_lookback = 90
    quantile = 0.30


# ═══════════════════════════════════════
# 止损扫描：在最优组 (L30/Q20) 上收紧 stoploss
#   基类 stoploss = -0.99，5 倍杠杆下 = 价格逆向 19.8% 才平仓 → 单笔亏光
#   这些子类把它压到 价格逆向 1% / 2% / 4% / 7% / 10%
# ═══════════════════════════════════════
class Mom_L30_Q20_SL05(Mom_L30_Q20):
    stoploss = -0.05          # 5x → 价格 -1%


class Mom_L30_Q20_SL10(Mom_L30_Q20):
    stoploss = -0.10          # 5x → 价格 -2%


class Mom_L30_Q20_SL20(Mom_L30_Q20):
    stoploss = -0.20          # 5x → 价格 -4%


class Mom_L30_Q20_SL35(Mom_L30_Q20):
    stoploss = -0.35          # 5x → 价格 -7%


class Mom_L30_Q20_SL50(Mom_L30_Q20):
    stoploss = -0.50          # 5x → 价格 -10%


# ═══════════════════════════════════════
# 杠杆扫描：组合总名义敞口 = 杠杆 × 本金
#   基类 5 倍 → 40 笔满仓时组合敞口 5 倍本金，这是 78% 回撤的真正来源
# ═══════════════════════════════════════
class Mom_L30_Q20_L1(Mom_L30_Q20):
    leverage_value = 1.0


class Mom_L30_Q20_L2(Mom_L30_Q20):
    leverage_value = 2.0


class Mom_L30_Q20_L3(Mom_L30_Q20):
    leverage_value = 3.0


# 低杠杆 + 4% 价格止损 的组合
class Mom_L30_Q20_L1_SL20(Mom_L30_Q20):
    leverage_value = 1.0
    stoploss = -0.20


class Mom_L30_Q20_L2_SL20(Mom_L30_Q20):
    leverage_value = 2.0
    stoploss = -0.20


class Mom_L30_Q20_L3_SL20(Mom_L30_Q20):
    leverage_value = 3.0
    stoploss = -0.20


# ═══════════════════════════════════════
# 4h 周期实验
#   mom_lookback 是「K 线根数」，4h 下 1 天 = 6 根
#   对照组：1d / L1 / max_open_trades=40  →  +51.90%, DD 21.03%, Sharpe 1.22
# ═══════════════════════════════════════
class Mom_4h_Base(CrossSectionalMomentum):
    timeframe = "4h"
    leverage_value = 1.0
    quantile = 0.20
    startup_candle_count = 400       # ≥ 最大回看 120 根 + 缓冲


class Mom_4h_L3d_Q20(Mom_4h_Base):
    mom_lookback = 18                # 3 天


class Mom_4h_L5d_Q20(Mom_4h_Base):
    mom_lookback = 30                # 5 天


class Mom_4h_L10d_Q20(Mom_4h_Base):
    mom_lookback = 60                # 10 天


class Mom_4h_L20d_Q20(Mom_4h_Base):
    mom_lookback = 120               # 20 天
