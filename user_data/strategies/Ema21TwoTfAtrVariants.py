"""
Ema21TwoTfAtr 的对照组子类 —— 每组只改一个类常量，用来做单变量对照。

═══════════════════════════════════════════════════════
为什么是子类而不是每次手改主策略文件：主策略是可交付件（带完整 docstring），
不该为了跑对照被反复改写。本仓库已有这个模式：GridMartingale.py 里的
`class GridMartingaleNoFilter(GridMartingale)`。

**变体表事先定死，不做参数搜索**（开发执行记录.md 的诚实性约束）。
下面每一组对应方案里的编号，跑完不增删。要加新变体就新开一组，别改已有的。

  B0  Ema21TwoTfAtr       作者原意基准：cross 入场 / 2×ATR 止损 / 2×ATR 止盈 / 保本止损 / 固定手数 / 过滤全开
  V1  Ema21V1State        入场改状态式
  V2  Ema21V2Tp5          止盈 2×ATR → 5×ATR
  V3  Ema21V3NoProtect    关掉保护性止损
  V4  Ema21V4Risk         仓位改固定风险
  V5  Ema21V5NoFilter     关掉 ER + ATR 分位过滤
  V6  Ema21V6Stop5        止损 2×ATR → 5×ATR

⚠️ 跑之前就该知道的一条：B0 里 `arm_R=1.0` 与 `tp_mult=2.0` 都等于 2×ATR，
也就是说「抬高止损的触发距离」与「硬止盈的目标距离」**是同一个距离** ——
止盈必然先成交，保护性止损基本是死代码（B0 实测 715 笔里只有 1 笔）。
所以 V3（关保护性止损）与 B0 的差异天然会接近 0，**那是这个参数化的必然结果，
不是「保护性止损没用」的结论**。V2 / V6 把两者拉开后才真正测到它。

═══════════════════════════════════════════════════════
  freqtrade backtesting -s Ema21V2Tp5 -c user_data/config/config_xau_1m.json \
    -i 1m --timerange 20251212-20261011 --fee 0.00003
═══════════════════════════════════════════════════════
"""

from Ema21TwoTfAtr import Ema21TwoTfAtr


# V1 —— 入场改状态式（站上 且 偏离 > k_dev×ATR 且 斜率 > s_min）
class Ema21V1State(Ema21TwoTfAtr):
    entry_mode = "state"


# V2 —— 止盈 2×ATR → 5×ATR（顺带把保护性止损从死代码变成活代码）
class Ema21V2Tp5(Ema21TwoTfAtr):
    tp_mult = 5.0


# V3 —— 关掉保护性止损
class Ema21V3NoProtect(Ema21TwoTfAtr):
    use_breakeven = False


# V4 —— 仓位改固定风险（每笔风险 = 权益的 0.5%）
class Ema21V4Risk(Ema21TwoTfAtr):
    sizing_mode = "risk"


# V5 —— 关掉 ER + ATR 分位过滤，退回「只看 EMA21」的原始设想
class Ema21V5NoFilter(Ema21TwoTfAtr):
    use_filters = False


# V6 —— 止损 2×ATR → 5×ATR。基类兜底 stoploss = -0.25（即 −25%）对本样本最大的
# 5×ATR（5.66%）仍有 4.4 倍余量，不会卡住这个变体，不必单独调。
class Ema21V6Stop5(Ema21TwoTfAtr):
    stop_mult = 5.0
