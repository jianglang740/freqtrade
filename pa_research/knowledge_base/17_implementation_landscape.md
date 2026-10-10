# 17｜开源实现与工程现状

- 版本：V1.0
- 定位：回答"这个概念有没有现成代码可用，能不能直接依赖"
- 数据来源：GitHub REST API 与 PyPI JSON API 实时元数据，采集于 **2026-10-10**
- **重要**：本篇描述的是 API 与行为，未逐行审计源码。许可证与活跃度取自上述 API。任何用作依赖前都应自行复核

## 1. 结论速查

| 名称 | 类别 | 许可证 | 成熟度 | 结论 |
|---|---|---|---|---|
| **jbn/ZigZag** | 摆动点 | BSD-3 | 成熟、低活跃 | **可用**（仅离线标注） |
| **joshyattridge/smart-money-concepts** | SMC 结构 | MIT | 活跃，2.1k★ | **可借鉴，但会重绘** |
| **Khaymat/pyvsmc** | SMC 结构 | MIT | 新，极小 | 有潜力，未验证 |
| **Louisjzhao/smc-toolkit** | SMC 结构 | MIT | 新，8★ | 玩具 |
| **Prasad1612/smart-money-concept** | SMC 结构 | **无许可证** | 新 | 避免（法律+质量） |
| **market-structure** (pymarket-structure) | 结构+freqtrade | 声称 MIT | **仓库 404** | 验证前避免 |
| **TA-Lib CDL\*** | K 线形态 | BSD-2 | 成熟，12.3k★ | 可用，但有坑 |
| **pandas-ta-classic** | 指标+K 线 | MIT | 活跃 fork，452★ | 可用（替代 pandas-ta） |
| **tsfresh** | ML 特征 | MIT | 活跃，9.5k★ | **可用** |
| **bukosabino/ta** | ML 特征 | MIT | 活跃，5.2k★ | 可用 |
| **mlfinlab (hudson-and-thames)** | 标注/结构 | **专有** | 已停（2023） | **避免** |
| **mlfinpy** | 标注/结构 | MIT | 重写版，85★，停更 | 最好的开源 mlfinlab 替代 |
| **equinor/mlfinlab** | 标注 | BSD-3 | 冻结于 2020 | 只读存档 |
| **skfolio** | 组合优化 | BSD-3 | 活跃，2.5k★ | 可用（非 PA） |
| **vectorbt** | 回测 | **Apache-2.0 + Commons Clause** | 活跃，9.3k★ | 源码可见，非 OSI |
| **backtesting.py** | 回测 | **AGPL-3.0** | 活跃，9k★ | 注意 copyleft |
| **freqtrade** | 全栈机器人 | GPL-3.0 | 极活跃，55k★ | 本项目平台 |

**许可证图例**：`MIT`/`BSD` = 宽松，可安全依赖；`GPL`/`AGPL` = 强 copyleft（**AGPL 是陷阱**——网络服务即触发源码披露义务）；`Commons Clause`/`NOASSERTION`/专有/无许可证 = **不是 OSI 开源**。

## 2. 最关键的一节：摆动点库全都会重绘

这是本篇对知识库**最重要**的发现，它从工程侧独立验证了 `09_quantification_spec.md` 第 3 节的事件时间模型。

### 2.1 现象

所有绘制摆动高/低点的库——`smart-money-concepts`、`stock_indicators`、`jbn/ZigZag`——**都会重绘最后一段**。

`smart-money-concepts` 的 README 自己写明：摆动高点是"`swing_length` 根 K 线**之前与之后**的最高点"。也就是说它**向未来看 50 根 K 线**。每个摆动点因此都是一个**重绘的（repainting）** pivot，只有在 50 根之后才可知。

`stock_indicators` 的 `get_zig_zag` 文档自带明确的 **"repaint" 警告**，诚实地说明了最后一段的问题。

### 2.2 这为什么是致命的

对于 `09_quantification_spec.md` 第 3 节要求的 `available_at <= decision_time`：

> 摆动点**存在于**第 N 根 K 线，但只在第 N+L 根**可知**。

如果用这些库的输出直接做实时信号，就等于在决策时使用了未来 L 根 K 线的信息——正是 `10_failure_modes.md` 第 7 节的未来函数。

### 2.3 正确的工程模式

**存储两个索引，按确认时间排序，用确认时间过滤：**

- `pivot_idx`：极值所在的 K 线序号；
- `confirmed_idx`：该 pivot 最早可知的 K 线序号（例如 `i + L`）；
- 按 `confirmed_idx` 而非 `pivot_idx` 排序输出；
- 对外暴露 `pivots_known_at(pivots, t)`，过滤 `confirmed_idx <= t`。

有参考实现采用分形 `find_pivots()` 并显式标注 `confirmed_idx` 为"唯一因果的历史视图"；另有实现采用**反转阈值 ZigZag**（单次因果遍历：只有回撤达到阈值后才确认极值，阈值按 ATR 自适应），摆动点按构造交替，尾部暴露为"正在形成的腿"而不是假装已确认。

> **这正是 `04_structure_and_levels.md` 第 2 节的要求**（必须分开 `pivot_time` / `confirmed_at` / `available_from`）与本库 `swing_definition_v1` 这类版本化定义的工程落地形式。本库的设计不是多余的谨慎——**开源生态的系统性缺陷恰好是它要防的那件事。**

### 2.4 相关的前视陷阱清单

同类问题还包括（`09` §3 与 `18` §4 的工程侧面）：

- `shift(-N)`、`populate_*` 中的 `.iloc[-1]`；
- 裸用的全局 `.max()/.min()/.mean()`——全局窗口意味着信号 K 线"看得见"未来；
- **窗口启动长度本身是泄漏源**：有分析显示 EMA/TEMA/RSI 随预热窗口长度不同而有差异（某例达约 2.5%），因此"预热窗口选多长"也是一个（常被忽略的）泄漏源；
- **截断差分检验**：把历史截断到第 i 根，重算指标，若第 i 根的值发生变化，则该指标不是时点稳定的。pandas-ta 曾有此类问题记录（`lookahead=False` 下 Skew/Kurt/SSF/TOS_STDEVALL 仍随截断改变）。

## 3. K 线形态库

### 3.1 TA-Lib

- **许可证**：BSD-2（Python 包装器；C 库同为 BSD）→ 宽松。12,276★，2026-10-09 仍在推送。
- **实现**：**61 个 `CDL*` 形态函数**，每个返回 `+100`（看涨）/`-100`（看跌）/`0`（无），长度与输入相同。涵盖单根（`CDLDOJI`、`CDLHAMMER`、`CDLMARUBOZU`）、两根（`CDLENGULFING`、`CDLDARKCLOUDCOVER`）、三根（`CDLMORNINGSTAR`、`CDL3BLACKCROWS`）、多根（`CDLBREAKAWAY`、`CDLRISEFALL3METHODS`）。
- **前视**：每个函数逐根因果，**不看向未来窗口**。**但经典形态的上下文未被验证**——TA-Lib 自己的函数页说明，例如 `CDLUNIQUE3RIVER` "理想上出现在下跌趋势中（**代码不检查趋势**）"，`CDLGAPSIDESIDEWHITE` "不验证此前的趋势"。输出只是**几何形状**；同一个"已确认"的反转仍需你自行加上趋势过滤。
- **已知缺陷**：Issue #87（多个 `CDL*` 返回全零的初始化 bug，据报已修）、Issue #119（用户观察到锤子/射击之星的检测看起来**颠倒**，`CDLSHOOTINGSTAR` 漏检有效形态）。普遍反映信号"过于机械"、在横盘中频繁触发，且部分形态极罕见（有研究在 470 万根 K 线中只找到约 9 次 `CDL3STARSINSOUTH`）。
- **结论**：把 `CDL*` 输出当作**特征**而非信号，始终用趋势/结构上下文门控。

> 这与 `16_academic_evidence.md` 第 5 节完全一致：独立的 K 线形态在严格检验中无一存活。

### 3.2 pandas-ta-classic 与供应链警告

- **pandas-ta-classic**（MIT，452★，2026-10-10 推送）：原版 pandas-ta 闭源后继续维护的社区 fork，284 个指标 + 62 个原生 K 线形态，无需 TA-Lib。
- **必须知道的供应链事件**：2025 年原版 `twopirllc/pandas-ta` 宣布从 GitHub 移除并转为付费模式（pandas-ta.dev），**PyPI 历史被清空，归属权变更**——这是一个严重的供应链红旗。`twopirllc/pandas-ta` 仓库现已 404。
- **操作建议**：使用 `pandas-ta-classic`，并**显式替换**掉被污染的原版。

## 4. 价格行为规则引擎：没有可用的 Brooks 库

**诚实的结论：不存在可信、在维护、生产级的 Al Brooks 库。**

存在的只有"Agent Skills"和个人项目：

- **AxisJu/price-action-skills**：一个 Al Brooks "skill"（SKILL.md 包），含 `scripts/pa_analyzer.py`（20-EMA、趋势 K 线 vs 十字星、摆动高低点、**H1/H2/L1/L2 回调计数与楔形**）和画图脚本。确实把概念编了码，但以 agent skill 形式发布，不是库。**玩具/实验性。**
- 另有若干 `mrhustlex/Trading-Masters-Strategies`（skill `brooks-priceaction`）、`realsnake/AL_price_action`、`Robert-ZLF/trading-assistant`、`CheshireMew/Price-action-analysis` 等，均为个人项目或标注工具。

**SMC/ICT 结构引擎**才是真正有代码的（见第 2 节）。

**对本库的结论**：**H1/H2/L1/L2 计数、突破回调、失败突破的逻辑，只能自己写。** 这些仓库仅可作为**边界情形定义的参考读物**（H1 与 H2 的分界、突破回调与失败突破的分野），**不是依赖项**。

## 5. ML 特征工程库

- **tsfresh**（MIT，9,471★，活跃）：自动提取数百个时序特征，`extract_features()` / `select_features()` / `extract_relevant_features()`，用多重假设检验做特征选择（FRESH 算法）。
  **前视注意**：特征提取按窗口因果，但**选择步骤是在数据上拟合的**，必须放在 walk-forward 划分**内部**。它不是金融专用——需要喂滚动窗口的价格/收益序列，不是原始 OHLC。
- **bukosabino/ta**（MIT，5,235★）：纯 pandas 指标库（`add_all_ta_features`），常用于 ML 特征块，比 TA-Lib 更轻更友好。
- **TA-Lib 包装器**：注意 TA-Lib 与 NumPy 的 NaN/预热约定不同（早期 K 线上 RSI 可差约 2.8 点）——**不要 `fillna(0)`**。

## 6. 标注 / 验证 / 回测库

### 6.1 德普拉多（de Prado）生态

**这是本库 `18_validation_playbook.md` 第 4、5 节所需工具的来源。**

- **mlfinlab (hudson-and-thames)**：**专有许可证。** 其 `LICENSE.txt` 是"版权保护通知与许可协议"（Hudson & Thames，2021-11 更新），即**保留所有权利**——不是开源。分发已迁移到 **£100+VAT/月** 的付费产品。最后推送 2023-10-02。**避免使用。**
- **equinor/mlfinlab**：BSD-3——是**限制许可证之前的快照（2020-04）**，明确保留"在他们限制许可证之前"。0★，2020 冻结。**仅作只读存档**，在现代 pandas/NumPy 上不能干净运行。
- **mlfinpy**（MIT，85★，2025-01 后停更约 9 个月，**有测试**）：从零重写的 MIT 版，覆盖 `filters/`（CUSUM）、`labeling/`（三重障碍、趋势扫描、固定视野、原始收益）、`structural_breaks/`（Chow、CUSUM、SADF）、`util/frac_diff`、`sample_weights/`、`sampling/`、`data_structure/`（美元/不平衡/运行棒）、`bet_sizing/`、`cross_validation/`。
  **这是最可用的开源 mlfinlab 替代**，研究级，非生产加固。
- **Open-Quant/openquant**（MIT，52★，活跃）：含清理过的三重障碍标注模块，可作第二参考。

**这些模块正好解决本库的两类需求**：
1. **事件采样**（CUSUM 过滤器）——只在累计收益超过阈值时采样，而非每根 K 线；
2. **非重叠标签**——三重障碍 + 样本唯一性权重，直接对抗摆动点标签造成的重叠泄漏（见 `18` §5）。

### 6.2 组合与回测

- **skfolio**（BSD-3，2,474★，非常活跃）：sklearn 风格 API，组合优化/配置（均值方差、HRP、风险平价、时序交叉验证切分）。**不是价格行为工具**——只与"把多个单资产 PA 信号组合成组合"有关。
- **vectorbt**：**Apache-2.0 + Commons Clause** → 源码可见但**非 OSI 批准**（Commons Clause 禁止**出售**该软件）。免费库只收 bug 修复，新功能进**完全专有的付费 vectorbt PRO**。适合极快的向量化参数扫描。
- **backtesting.py**：**AGPL-3.0**——强 copyleft；若把修改版作为网络服务暴露，必须开源。若打算产品化需注意。

## 7. Freqtrade 相关

- **多周期 / informative pairs**：内建且设计安全。`informative_pairs()` 返回 `(pair, timeframe)` 元组；`merge_informative_pair()` 合并高周期数据**不引入前视**（列名加 `_1h`/`_1d` 后缀）；`@informative` 装饰器是现代写法。文档警告不要用该助手合并**小于**基准对周期的 informative TF。（与 `13` §7 一致。）
- **`freqtrade lookahead-analysis`（官方）**：正确的验证工具。它在单 pair 切片上重跑回测，与全历史基线对比列值，报告 `has_bias`、`biased_entry_signals`、`biased_exit_signals`、`biased_indicators`。它会强制覆盖 cache/limits 以避免假阴性，**但无法验证从未触发的信号**（已知假阴性模式）。
  **对本领域的意义**：它猎捕的经典偏差来源正是摆动点陷阱——`shift(-N)`、`populate_*` 中的 `.iloc[-1]`、裸 `max()/min()/mean()`、`ta.MACD(..., signalperiod=1)`。**确认一个摆动点需要未来的 K 线 → 必须显式建模确认延迟，而不是用负向 shift。**
- **freqtrade/freqtrade-strategies**（GPL-3.0，5,537★）：官方策略集，含 `lookahead_bias/` 下**故意带前视**的示例，供学习识别。
- **zmomz/freq_strategies**：**无许可证**，含 `MarketStructureCHOCH.py` 与 `MarketStructureSFP.py`，**仅作参考代码**，无许可证是再分发问题。

## 8. 公开的"价格行为因子"数据集

**不存在规范的、整理好的 PA 因子数据集。** 最接近的公开制品多为 ML/视觉，不是干净的因子表：

- HuggingFace 上的 OHLC 文本配 K 线标签数据集；
- `jerrychi/FinErva`：2,056 条人工核验的 K 线图技术分析样本 + 思维链理由；
- **FinChart-Multimodal**（CVPR 2026 论文）：10 万条 OHLCV→图表样本，可复现流水线；
- 学术：Stanford CS231n 与一篇 PeerJ 研究各手工标注几百个样本。

**评估：这些是图像/分类数据集，不是无前视的因子矩阵。不要把它们当作已验证的 alpha 来源。**

## 9. 最终建议

**可以依赖**（宽松许可证、在维护、真实代码）
- **tsfresh**（MIT）、**bukosabino/ta**（MIT）——ML 特征块；
- **TA-Lib**（BSD）——K 线/指标，但作为**特征**并用趋势/结构门控；预期会遇到检测颠倒类 bug（#119）；
- **pandas-ta-classic**（MIT）——指标层，并**显式替换**被污染的 `pandas-ta`；
- **jbn/ZigZag**（BSD-3）——**仅离线标注**；
- **mlfinpy**（MIT）——三重障碍 / CUSUM / 趋势扫描标注；**Open-Quant/openquant**（MIT）作后备。

**可借鉴但须大量改造**
- **smart-money-concepts**（MIT）：维护最好的 SMC 代码且有测试，**但其摆动定义向未来看 50 根 → 重绘**。借鉴其几何计算，确认式摆动点自己实现（见第 2.3 节）。

**避免**
- **mlfinlab**（专有，"保留所有权利"）——法律陷阱；
- **Prasad1612/smart-money-concept**——无许可证；
- **`market-structure` / pymarket-structure**——GitHub 仓库 404，虽有吸引人的 Freqtrade 集成宣称但**无法审计，不可信任**；
- **所有 "Al Brooks Python" 仓库**——agent skills 与个人项目，无测试，**不存在在维护的库**。H1/H2/突破回调逻辑**自己写**。

**产品化时的许可证陷阱**：backtesting.py **AGPL-3.0**；vectorbt **Apache-2.0 + Commons Clause**（禁止转售）；freqtrade 及其策略集 **GPL-3.0**；mlfinlab **专有**。

**针对本库的前视问题**：不要依赖任何库的摆动输出做实时信号。采用**确认式摆动点模式**（`pivot_idx` + `confirmed_idx`，按确认时间过滤）或带显式 `confirmation_candle_index` 的反转阈值 ZigZag；用 `freqtrade lookahead-analysis` 验证（记住它抓不到从未触发的信号）；优先用**事件式标注**（三重障碍/CUSUM）以避免重叠的摆动标签跨样本泄漏。

## 10. 与知识库其他部分的关系

| 本篇发现 | 对应/佐证的条目 |
|---|---|
| §2 所有摆动库都重绘 | **`04_structure_and_levels.md` §2（`pivot_time`/`confirmed_at`/`available_from`）；`09` §3 事件时间模型** |
| §2.4 截断差分检验 | `10_failure_modes.md` §7 未来函数；`18` §4 净化 CV |
| §3 K 线形态只是几何 | `03_bars_and_signals.md`（信号 K 线需上下文）；`16` §5 |
| §4 无 Brooks 库 | `13` 因子工程必须自建；`09` §7 构建顺序 |
| §6.1 三重障碍 / CUSUM | `18` §5 标签方法 |
| §7 lookahead-analysis 假阴性 | 本项目已有记忆条目：该工具对横截面策略必误报 |
| §9 许可证 | 若产品化须先过法务 |

## 11. 核实状态

本篇全部许可证、星数、最后推送时间取自 **2026-10-10** 的 GitHub REST API 与 PyPI JSON API。行为描述来自各项目 README 与元数据，**未逐行审计源码**。

标为"玩具""实验性""未验证"的条目是基于活跃度与测试覆盖的判断，不是基于代码审计。

`12_sources_and_limits.md` 第 3 节的边界声明适用于本篇。
