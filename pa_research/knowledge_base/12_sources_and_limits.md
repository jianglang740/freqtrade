# 12｜参考来源、版权与知识边界

## 1. 主要参考框架

本知识库的概念组织参考了 Al Brooks 的公开书目与术语体系，特别是其价格行为系列中关于以下主题的章节组织：
- 趋势、震荡区间与状态转换；
- 趋势 K 线、十字星与高潮；
- 突破、回调、测试与反转；
- 信号 K 线、入场 K 线与二次入场；
- 趋势线、通道与水平关键位置；
- Spike and Channel、宽通道、趋势回调；
- 突破失败、支撑阻力、测量走势与交易管理。

公开书目页面显示，Al Brooks 的价格行为系列分别讨论趋势、震荡区间和反转等主题；其术语表也包含 H1/H2、趋势 K 线、突破、回调等常用术语。

## 2. 公开参考链接

- Wiley / Al Brooks, *Trading Price Action Trends*：
  https://onlinelibrary.wiley.com/doi/book/10.1002/9781119202592
- Wiley / Al Brooks, *Trading Price Action Trading Ranges*：
  https://onlinelibrary.wiley.com/doi/book/10.1002/9781119202608
- Brooks Price Action 官方网站术语缩写页：
  https://www.brookspriceaction.com/acronyms.php
- Brooks Trading Course 官方课程介绍：
  https://www.brookstradingcourse.com/buy-the-brooks-price-action-trading-course/

## 3. 本知识库的边界

本知识库是独立编写的中文概念归纳与工程化设计文档：
- 不复制书籍或课程中的大段文字；
- 不替代原著、课程、作者的实时图表讲解；
- 不声称完整复现作者所有细节、例外和交易判断；
- 部分概念的操作化定义是为了量化研发而提出的工程建议，不应误认为作者唯一或官方定义；
- 所有算法化规则都需要在目标市场和时间周期上验证。

## 4. 术语与定义的版本控制

价格行为概念可能存在不同解释方式。后续研发应给每个操作定义建立版本号，例如：
- `swing_definition_v1`
- `breakout_definition_v1`
- `failed_breakout_definition_v1`
- `h2_counting_definition_v1`

修改定义时应记录：
- 修改原因；
- 旧定义和新定义；
- 受影响的事件标签；
- 受影响的回测；
- 是否改变历史信号。

## 5. 如何继续扩充知识库

建议未来按以下优先级扩充：
1. 以真实图表构建带有上下文的案例集；
2. 每个案例同时标注多头情景、空头情景和失效条件；
3. 为模糊案例保留“存在争议”的标签；
4. 将人工标签与算法标签并排比较；
5. 记录误判类型，而不只记录策略盈亏；
6. 将经过验证的操作定义转化为单元测试；
7. 每次改动都保留数据与规则版本。

真正有价值的 AI 知识库不是词条越多越好，而是概念之间的关系清晰、边界明确、案例可核验、操作定义可测试。

**V1.1 进展**：第 1、3、5 项已部分由 `11_case_studies.md`（抽象案例）与 `15_quantification_case_library.md`（已公开量化案例）覆盖；第 6 项的方法在 `18_validation_playbook.md`。**第 1 项（真实图表的带上下文案例集）仍未做**——这是本库最大的空缺。

## 6. V1.1 新增来源（14–20 篇）

V1.1 扩充了外部证据层。新增来源按类别列出；**核实状态见各篇文末**。

### 6.1 官方术语来源
- Brooks Price Action 官方缩写/术语页：https://www.brookspriceaction.com/acronyms.php
- Brooks Trading Course 公开论坛与博文（交易者方程、80/20 时间分配）

### 6.2 学术文献（同行评议，14–16 篇）
- Brock, Lakonishok & LeBaron, JF 47(5), 1992 — DOI 10.1111/j.1540-6261.1992.tb04681.x
- Sullivan, Timmermann & White, JF 54(5), 1999 — DOI 10.1111/0022-1082.00163
- White, Econometrica 68(5), 2000
- Bajgrowicz & Scaillet, JFE 106(3), 2012 — DOI 10.1016/j.jfineco.2012.06.001
- Lo, Mamaysky & Wang, JF 55(4), 2000 — NBER WP 7613
- Marshall, Young & Rose, JBF 30(8), 2006 — DOI 10.1016/j.jbankfin.2005.10.001
- Horton, QREF 49(2), 2009
- Fock, Klein & Zwergel, Journal of Derivatives 13(1), 2005 — DOI 10.3905/jod.2005.580514
- Osler, JF 58(5), 2003 — DOI 10.1111/1540-6261.00588；NY Fed SR-125
- George & Hwang, JF 59(5), 2004 — DOI 10.1111/j.1540-6261.2004.00695.x
- Korajczyk & Sadka, JF 59(3), 2004 — DOI 10.1111/j.1540-6261.2004.00656.x
- Gao, Han, Li & Zhou, JFE 129(2), 2018 — DOI 10.1016/j.jfineco.2018.05.002
- Harvey, Liu & Zhu, RFS 29(1), 2016 — DOI 10.1093/rfs/hhv059
- Moskowitz, Ooi & Pedersen, JFE 104(2), 2012
- Park & Irwin, Journal of Economic Surveys 21(4), 2007
- Bulkowski, *Encyclopedia of Chart Patterns*, 3rd ed., 2021

### 6.3 验证方法学（18 篇）
- Bailey, Borwein, López de Prado & Zhu, JCF 20(4), 2017 — SSRN 2326253；Notices of the AMS 61(5), 2014
- Bailey & López de Prado, JPM 40(5), 2014 — SSRN 2460551
- López de Prado, *Advances in Financial Machine Learning*, Wiley 2018
- Hansen, JBES 23(4), 2005；Romano & Wolf, Econometrica 73(4), 2005
- Benjamini & Hochberg, JRSS-B 57(1), 1995
- Carver, *Systematic Trading*, Harriman House 2015

### 6.4 工程与实现（17 篇）
- 各开源项目仓库与 PyPI 元数据（采集于 2026-10-10），详见 `17_implementation_landscape.md` 第 1 节

### 6.5 数据供应商技术文章（19 篇）
- CoinAPI，*How to Backtest Crypto Data (Spot, Options, Futures) Properly*
- CoinAPI，*Why Most Backtests Fail Without Historical Bid & Ask Data*

**注意**：6.5 类为**行业技术文章，非同行评议**。本库未逐条核实其引用的第三方数字。

### 6.6 标注为预印本/待核实/厂商倾向的条目
各篇已内联标注。**引用时须连同标注一起引用。**

## 7. 数据与脚本

V1.1 附带了两个可复现本项目实测数据的脚本（位于本目录 `scripts/`）：
- `scripts/pa_feature_calibration.py` → `scripts/pa_feature_calibration.csv`（对应 `20_bar_feature_calibration.md`）
- `19_cost_data_and_execution.md` 第 2、3 节的数字可用其给出的 feather 文件路径直接复现（无独立脚本，为一次性 pandas 读取）

这两个脚本只做**描述性统计**，不生成任何交易信号。
