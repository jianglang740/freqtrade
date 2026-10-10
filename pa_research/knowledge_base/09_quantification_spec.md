# 09｜从价格行为概念到可量化算法的规范

## 1. 三层映射

每个价格行为概念都应拆成三层：

1. **领域定义**：交易者在图表上所说的概念是什么意思。
2. **操作定义**：在数据中怎样以明确条件识别它。
3. **交易假设**：识别后为什么可能具有交易价值，如何验证。

示例：失败突破
- 领域定义：价格尝试越过关键边界，但不能维持在边界外，随后回到原区域。
- 操作定义：先确认边界，再检测越界，之后检测在限定窗口内收盘回到边界内。
- 交易假设：错误方向的突破交易者可能退出，推动反向价格运动。
- 重要区别：操作定义可正确识别事件，不代表反向交易必然盈利。

## 2. 特征、状态、事件、信号

### 特征（Feature）
对当前及历史数据的数值描述，例如实体比例、收盘位置、相邻 K 线重叠率。

### 状态（State）
一段时间内的市场分类，例如趋势、震荡、宽通道或转换。状态可能在多根 K 线上持续。

### 事件（Event）
某个在时间上发生的行为，例如突破、失败突破、结构破坏。事件需要开始、确认和失效时点。

### 信号（Signal）
在当前可知信息下，满足某一策略入场或退出规则的决策输出。

不能把这四类字段混用。例如 `bull_trend` 是状态，不是买入信号；`breakout` 是事件，不自动等于入场。

## 3. 事件时间模型

所有结构和事件应至少保留：
- `occurred_at`：行为实际发生的时间；
- `confirmed_at`：算法拥有足够证据确认它的时间；
- `available_at`：策略允许读取的最早时间；
- `invalidated_at`：失效时间（若发生）；
- `status`：候选、确认、失效、过期。

可交易信息必须满足 `available_at <= decision_time`。

## 4. 置信度与证据

价格行为解释通常不是二元事实。建议将证据分项记录，而不是只输出一个黑箱总分：
- 结构证据；
- 位置证据；
- K 线证据；
- 跟随证据；
- 多周期背景；
- 反向证据；
- 风险空间。

初期评分只用于规则组合和排序。若要将评分解释为概率，需要在独立数据上做校准，并检查不同市场状态下的稳定性。

## 5. 状态机优先于单根触发

对于突破与失败突破，应使用事件状态机，例如：

```text
NO_BREAKOUT
    |
    v
BREAKOUT_CANDIDATE
    | \
    |  \---> BREAKOUT_INVALIDATED
    v
FOLLOW_THROUGH_CONFIRMED
    |
    +----> TREND_CONTINUATION
    |
    +----> RETURN_TO_RANGE
                 |
                 v
        FAILED_BREAKOUT_CANDIDATE
                 |
                 v
        FAILED_BREAKOUT_CONFIRMED
```

状态机可以减少重复触发，并明确每一步需要什么新证据。

## 6. 规则定义卡片模板

每个模块在编码前都必须填写：

```yaml
concept_name: "Failed Breakout"
domain_definition: ""
required_context: []
observable_inputs: []
operational_definition: ""
confirmation_rule: ""
invalidation_rule: ""
known_false_positives: []
lookahead_risk: ""
unit_test_cases: []
visual_review_cases: []
trading_hypothesis: ""
validation_plan: ""
```

如果 `operational_definition`、`confirmation_rule` 或 `lookahead_risk` 仍然空白，不能开始编写正式交易信号代码。

## 7. 第一批算法优先级

建议依次实现：
1. K 线基础特征；
2. 确认式摆动点；
3. HH / HL / LH / LL；
4. 市场状态候选分类；
5. 关键位置；
6. 突破事件；
7. 跟随确认；
8. 失败突破状态机；
9. 趋势回调；
10. H1 / H2 / L1 / L2 与二次入场。

原因：后续概念依赖前面的结构和时间语义。不要先做复杂设置，再反过来用临时条件解释它。

## 8. Freqtrade 接入边界

领域算法尽量保持为独立 Python 模块，Freqtrade 策略负责调用它们。建议接口职责：
- 特征函数：输入 OHLCV，返回新增特征列；
- 结构函数：输入已知特征，返回结构和确认时间；
- 事件函数：输入结构与历史数据，返回事件状态；
- 上下文函数：合并多周期背景；
- 策略函数：根据可用特征生成 entry / exit 信号；
- 风险层：独立处理止损、仓位和组合限制。

开发时必须核对所安装 Freqtrade 版本的接口和 informative timeframe 对齐规则。
