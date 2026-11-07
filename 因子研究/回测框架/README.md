# 因子回测框架

跑在**两套面板**上的加密货币单因子回测引擎：

- **因子数据**：`因子研究/因子数据/panels/{market}_{tf}.parquet`（`date / symbol / <因子>` 长表）
- **行情数据**：`因子研究/行情数据/` 数据湖（K 线 + 资金费率 + 符号主表）

方法骨架来自 `../../fct_backtest/code/fct_backtest改进版.py`（全 A 股单因子框架）：
每日截面 MAD 去极值 + Z 分数、日历化调仓、Fama-MacBeth + Newey-West、风格中性化后看残差。
**执行层全部按币圈重写** —— 股票框架里「T+1 收盘成交、一年 252 天、不能做空、不算成本」
这四条，在 24/7、可做空、换手极高、还要付资金费的币圈全部不成立。

这份 README 不是参数手册，而是**逐环节拆解引擎**：每一步的输入输出、数学定义、
为什么这么写、哪一行代码在做什么、以及哪些地方是刻意选的、哪些地方是已知的妥协。
改代码之前先读对应的那一节。

---

## 一、目录与模块分层

```
回测框架/
├── README.md          # 本文
├── engine.py          # 引擎（约 800 行，全部逻辑在这一层）
├── stats.py           # 纯统计函数，不依赖引擎，可单独 import
├── plots.py           # 画图，只吃 engine.run() 的返回值
├── run_backtest.py    # CLI 入口：解析参数 → engine.run() → 写表写图
├── selftest.py        # 22 条断言，把每个开关都跑一遍
└── out/<tag>/         # 产物，gitignore
    ├── 因子回测统计结果.xlsx   （没装 openpyxl/xlsxwriter 时退化成同名 *.csv）
    ├── 组合明细_持有{h}.csv
    ├── config.json
    └── figs/*.png
```

依赖方向是单向的：

```
run_backtest.py / selftest.py
        ↓
     engine.py ──→ stats.py
        ↓
  lake_io.py（行情数据）    factor_io.py（因子数据）
```

`engine.py` 自己不做 I/O 以外的任何决策；所有口径都从 `Config` 里来。
`stats.py` 不认识 `Config`，只吃 `date × symbol` 宽表 —— 这样统计口径可以脱离回测单独复用
（比如在别的 notebook 里验证一个 IC 数）。

---

## 二、快速开始

```bash
# 最简：现货日线，30 日动量，每日调仓，持 1/5/10/20 天
python 因子研究/回测框架/run_backtest.py --factor mom_30d

# 永续合约 + 周频调仓 + 扣资金费（默认就扣）
python 因子研究/回测框架/run_backtest.py --market um --factor mom_30d --rebalance weekly

# 月频调仓、只看多头、成交额加权
python 因子研究/回测框架/run_backtest.py --market um --factor vol_30d \
    --rebalance monthly --mode long_only --weighting adv

# 小时线（一定带 --start，否则从 2017 年读起很慢）
python 因子研究/回测框架/run_backtest.py --tf 1h --factor mom_168h \
    --rebalance daily --start 2024-01-01 --hold 1,24

# 只跑统计不出图（批量扫因子时用）
python 因子研究/回测框架/run_backtest.py --factor mom_90d --no-plots --tag mom90_spot
```

产物默认写到 `out/<因子_市场_周期_调仓>/`，用 `--tag` 覆盖目录名。

**前置条件**：因子面板已生成（`python 因子研究/因子数据/build_factors.py`）。
面板里没有的因子会直接报 `KeyError` 并列出可用因子名。

### 自检：改完引擎先跑这个

```bash
python 因子研究/回测框架/selftest.py            # 全部 22 项，约 6 分钟
python 因子研究/回测框架/selftest.py --only 加权 资金费 --verbose
```

它和 `out/` 里那种冒烟目录的区别是：**断言数字说得通**，而不是只断言「没崩」。
例如：

| 断言 | 抓的是什么 bug |
|---|---|
| `long_only` 下 `n_short` 必须全为 0 | 空腿没有被 mode 关掉 |
| `zero` 成本 + `use_funding=False` 时 `net == gross` | 成本/资金费符号或重复扣 |
| `short_top` 与 `long_top` 的权重相关系数 < −0.5 | direction 只换了标签没换腿 |
| `adv` 加权的持仓 logADV 均值 > 等权 | style 因子没对齐到权重网格 |
| `exec_price=close` 与 `open` 的结果必须不同 | 参数被 `__post_init__` 吃掉了 |
| 周频 39 次 / 月频 9 次 / 每 3 根 91 次（±15%） | 调仓日历把 tz 搞丢、窗口数变 0 |
| 小时线日频调仓时 `days` 全部 ∈ (0.5, 1.5) | 1h 的 `days` 被当成自然日算 |
| 全历史 `delist_shock=0.5` 必须出现 `dead_exits > 0` | 退市分支从来没被走到 |

退出码非 0 = 有分支坏了，可以直接挂 CI。覆盖面：市场 ×2、频率 ×2、
四种调仓、三种 mode、两种 direction、三种加权、两种成交价、三种中性化、
分组数 3/5/10、退市冲击、资金费开关、零/高成本、外部因子面板。

> 小时线用例依赖 `因子数据/panels/um_1h.parquet`；没有就
> `python 因子研究/因子数据/build_factors.py --market um --tf 1h`。

---

## 三、端到端数据流

一次 `engine.run(cfg)` 的顺序是固定的，每一步都在同一个「调仓日网格」上叠加信息：

```
lake_io.load(exec_price)                        行情：date × symbol 的执行价
        │
        ├─ build_rebalances        bar 时间轴 → 调仓日（日/周/月/every）
        │
        ├─ align_entry_prices      调仓日 → 下一根 bar 的成交价（索引仍是信号日）
        │
        ├─ build_universe          点时股票池（有 bar / 上市满 N 天 / ADV 前 N）
        │       └─ 读日线，起点往前推 _warmup_start
        │
        ├─ load_factor_at          因子面板 → 调仓日截面（池外先不动）
        ├─ build_style             logADV / BTC Beta / 波动率（日线滚动 → ffill 到调仓日）
        ├─ preprocess_factor       池内 MAD 缩尾 + Z 分数
        ├─ neutralize_factor       截面回归取残差（raw 与 neut 两个变体）
        │
        ├─ assign_quantiles        截面等频分组 1..n
        ├─ build_weights           分组 → 带符号、腿内归一的权重
        ├─ funding_delta           每个窗口内按符号累计的资金费率
        │
        └─ simulate(P, W, h)       逐窗口 gross / cost / funding / net
                │
                ├─ forward_returns + ic_series + fama_macbeth   ← 统计（缩尾后）
                └─ portfolio_metrics                            ← 绩效（真实收益）
```

关键设计：**统计与绩效走两条不同的数据路径**。
统计用 `forward_returns`（同一天信号、持有 h 个窗口的累计收益，彼此重叠），
绩效用 `simulate`（h 个错位子组合的真实复利）。前者回答「信号有没有信息」，
后者回答「真金白银能不能赚到」。两者混用是因子研究里最常见的错误之一。

---

## 四、逐环节拆解

### 4.1 `load_bars` —— 只读一列价格

```python
df = lake_io.load(market, tf, columns=["symbol", "date", col])
px = df.pivot(index="date", columns="symbol", values=col)
```

- `col` 由 `cfg.exec_price` 决定，**整条回测只读 open 或只读 close**，
  不会出现「用 open 成交、用 close 估值」这种混用。
- 宽表里 NaN = 当天没有 bar（未上市 / 已下架 / 停牌）。**不填前值**，填值留到成交层再做。
- 时间轴 `bar_dates` 就是宽表的索引，后面所有日期对齐都以它为准。

### 4.2 `build_rebalances` —— 日历化调仓

| 模式 | 取法 |
|---|---|
| `daily` | 每个自然日（`floor("D")`）的**最后一根** bar |
| `weekly` | 每个 ISO 周（`year-week`）的最后一根 |
| `monthly` | 每个自然月（`%Y-%m`）的最后一根 |
| `every` | 每 N 根 bar（`bar_dates[::N]`），与日历无关 |

「每期最后一根」而不是第一根，是因为**信号必须用完整的当期数据算完**：
月频调仓用该月最后一根日线的收盘价定权重，下一根 bar 才建仓。

> 踩过的坑：`build_rebalances` 里如果写 `pd.DatetimeIndex(groupby.last().values)`，
> tz 会丢，后面 `get_indexer` 一个都匹配不上，调仓窗口变成 0 个、直接报「样本太少」。
> 代码里留着注释，别「优化」掉。

### 4.3 `align_entry_prices` —— 无前视的成交价

```python
pos = bar_dates.get_indexer(reb_dates)
entry_pos = pos[ok] + 1                 # 信号日的下一根 bar
P = px.iloc[entry_pos]
P.index = reb_dates[ok]                 # ← 索引仍然是信号日！
```

这是整个框架**最重要的一处约定**：

```
索引（下游看到的时间）  = 信号日 T
值（实际成交价）        = T+1 根 bar 的 open/close
```

为什么不能把索引改成 T+1？因为下游的因子、股票池、风格因子都锚在 T 上。
一旦索引变成 T+1，`F.loc[T+1]` 取到的因子已经是**用 T+1 收盘算出来的**，
却拿去解释 T+1 开盘的成交 —— 这就是前视。

> 这个 bug 真实发生过：早期版本索引错位一天，回测年化 2927%、夏普 74。
> 币圈日波动 10% 很常见，差一根 bar 的收益就能把结论彻底改写。

最后一个调仓日没有「下一根 bar」，会被 `ok = pos+1 < len(bar_dates)` 丢掉。

### 4.4 `build_universe` —— 点时股票池

三道闸门，全部只用当日及之前的数据：

1. **当天有 bar**：`rank` 只在有成交额的单元格上有值，NaN 自然出局。
2. **上市满 `min_history_days` 天**：首根日线 + N 天。避开上市初期的价格发现
   （新币前两周的走势基本由做市商和空投抛压决定，和因子无关）。
3. **流动性**：过去 `adv_lookback` 天日均成交额 ≥ `adv_min`，且**在满足前一条的符号里**
   按 ADV 降序排名 ≤ `top_n`。

```python
adv  = qv.rolling(lb, min_periods=max(3, lb // 2)).mean()
rank = adv.where(adv >= adv_min).rank(axis=1, ascending=False)
univ = rank <= top_n
```

两个容易忽略的点：

- **成交额一律按日线算，即使回测跑在 1h 上**。小时成交额会被单根巨量 bar 干扰
  （插针、上币公告），日频 ADV 更稳定，也和小币的实际容量更接近。
- **日线 → 调仓日走 `reindex(union).ffill().reindex(reb_dates)`**，用的是
  **上一根日线的状态**。盘中不因为「今天成交额突然放大」把币加进池子，
  否则会用当天尚未结束的信息决定当天的持仓。

#### `_warmup_start`：一个静默失真的修复

股票池和风格因子都读日线，但读的起点不是 `cfg.start`，而是：

```python
buf = max(adv_lookback, beta_window, vol_window, min_history_days) + 5
start = cfg.start - buf 天
```

不预热会怎样：`--start 2024-01-01` 时，每个符号的「首根日线」都恰好是 2024-01-01，
于是 `min_history_days=30` 把**前 30 个交易日的池子整个清空**，Beta / 波动率的
滚动窗口头两周全是 NaN。不报错、不留痕，只是样本开头悄悄少了一截 ——
实测基线 IC 从 −5.95% 变成 −6.77%，就是这个原因。

### 4.5 `load_factor_at` —— 因子读入

```python
long = factor_io.load_external(cfg.factor_panel, ...)   # 项目自建面板
    or factor_io.load_panel(cfg.market, cfg.tf, ...)    # 公共因子库
f = long[long["date"].isin(reb_dates)].pivot(...)
f = f.reindex(index=reb_dates, columns=columns)
```

- **因子面板必须和行情同频同时间戳**。`um/1d` 的因子只能配 `um/1d` 的行情。
- `reindex(columns=columns)` 把因子宽表强行对齐到行情宽表：行情里没有的符号，
  因子值丢弃；因子缺失的符号留 NaN，后面 `preprocess` / `assign_quantiles` 会剔除。
- 如果所有调仓日的因子都为空，`run()` 直接报错并提示检查时间戳 ——
  这种情况几乎都是「面板还是旧版、行情已经更新」造成的。

`--factor-panel` 是给**单个研究项目**用的：项目自己算的因子写到
`因子研究/<项目>/out/factor_*.parquet`，回测时直接读，不污染公共因子库。
缺省时读 `因子数据/panels/`。

### 4.6 `build_style` —— 币圈的「行业」

```python
logadv = log(rolling_mean(quote_volume, adv_lookback))
beta   = rolling_cov(ret_i, ret_BTC, w) / rolling_var(ret_BTC, w)
vol    = rolling_std(ret_i, vol_window)
```

股票框架用「市值 + 行业」中性化。币圈没有行业分类（板块标签既不稳定也没有点时数据），
真正的共同风险是 **BTC Beta**：牛市里所有币一起涨，横截面因子的多空组合
会不知不觉变成「高 Beta 币 vs 低 Beta 币」。

三个序列都在**日线**上滚动，然后 `ffill` 到调仓日 —— 和股票池同一套对齐规则。
`min_periods = max(5, window // 2)`：新币窗口攒一半就有值，不至于整段失效。

### 4.7 `preprocess_factor` —— 截面缩尾 + 标准化

```python
f   = f.where(univ)                       # 统计量只算池内
med = f.median(axis=1)
mad = (f - med).abs().median(axis=1)
f   = f.clip(med - mad_n*mad, med + mad_n*mad, axis=0)
f   = (f - f.mean(axis=1)) / f.std(axis=1)
```

- **逐日截面**，不是全样本。用全样本中位数/标准差 = 把未来分布漏进今天，是隐蔽的前视。
- **MAD 而不是标准差**：币圈 20 天收益动辄 ±300%，标准差本身就被极值绑架；
  MAD 的崩溃点是 50%，稳健得多。`mad_n=5.2` 对应正态下约 ±3.5σ。
- **池外和缺失保持 NaN，绝不填 0**。填 0 意味着「没有报价」被当成「截面均值」参与排序，
  小币缺 bar 很常见，那样会凭空制造信号。
- 若某日截面的 `std == 0`（全部同值，横盘到小数点后 8 位），整行变 NaN，
  下游自动跳过该日。

### 4.8 `neutralize_factor` —— 取残差

逐调仓日做一次截面 OLS：

```
factor_t = α + β1·logADV_t [+ β2·Beta_t] [+ β3·Vol_t] + ε_t
```

保留 `ε_t`（残差），再 `where(univ)` 掩掉池外。控制变量按 `neutralize` 档位：

| 档位 | 控制 | 剥掉什么 |
|---|---|---|
| `None` | — | 不中性化，原始因子 |
| `logadv` | log 成交额 | 规模/流动性 |
| `logadv_beta`（默认） | + BTC Beta | 规模 + 市场共同暴露 |
| `logadv_beta_vol` | + 波动率 | 再剥一层「低波异象」 |

实现细节：

- 用 `np.linalg.lstsq`（最小二乘），不用 `statsmodels` —— 截面几千次，快一个数量级。
- 控制变量里有 NaN 的符号**整行剔除**，剩下的样本必须仍 ≥ `fm_min_obs`（默认 10），
  否则该日整体置 NaN。
- **残差没有重新标准化**。回归后残差均值≈0，但方差≠1；下游 `assign_quantiles`
  只用排名，不受影响；`fama_macbeth` 的斜率单位是「每单位因子」，跨变体比较时
  看的是 t 值而不是斜率绝对值。

`run()` 会同时跑 **raw 和 neut 两个变体**，汇总表里并排列出
（`原始IC / 中性化IC / 原始年化 / 中性化年化`），这样能直接看出
「这个因子有多少是规模的影子」。`effective` 取 neut（当 `neutralize` 非空时），
所有分组、权重、绩效都用中性化后的因子。

### 4.9 `assign_quantiles` —— 等频分组

```python
v   = f.where(univ & f.notna())
cnt = v.notna().sum(axis=1)
q   = ceil(rank(v, method="first") / cnt * n)
q.loc[cnt < max(min_obs, n)] = np.nan
```

为什么不用 `pd.qcut`：币圈有大量价格**完全并列**的横盘币（尤其小币，
价格精度只到小数点后 8 位），qcut 遇到重复分位点会直接丢组或报
`Bin edges must be unique`。`ceil(rank/count*n)` 用一个确定的公式切分，
并列时按出现顺序打散，永远不会丢组。

截面有效符号数 < `max(min_obs, n)` 时整行置 NaN：分 5 组却只有 3 个币，
「最高组」和「最低组」都是同一个个币，算出来的价差没有意义。

### 4.10 `build_weights` —— 带符号的权重

```python
top, bot = (q == n), (q == 1)                 # direction=short_top 时互换
w_top = 腿内归一化(top)                        # 每行和 = 1
w_bot = 腿内归一化(bot)

long_only  : W = +w_top * gross
short_only : W = −w_top * gross
long_short : W = (w_top − w_bot) * gross / 2
```

三种加权（`_leg_weights`）：

| `--weighting` | 腿内权重 | 用途 |
|---|---|---|
| `equal` | 1 | 默认，最稳健 |
| `adv` | 成交额 | 资金按容量分配，小币自动降权 |
| `vol_inv` | 1/波动率 | 风险平价近似，低波币加杠杆 |

腿内归一化到 1，所以 `raw_exposure` 在多空下恒为 `gross_leverage`（多头 0.5 + 空头 0.5），
在单边下也等于 `gross_leverage`。自检里专门断言了这条。

> 现货 `--mode long_short` 会照常建多空组合，但**现货做不了空**，
> 那条曲线只是理论价差。真正能双边的只有 `um`。见 §十一.5。

---

## 五、组合模拟 `simulate` —— 真金白银的那一层

输入是同一网格上的三张表：`P`（建仓价）、`W_target`（每次调仓的目标权重）、
`funding_delta`（区间累计资金费率）。输出是逐窗口的收益与成本明细。

### 5.1 收益：ffill + 退市

```python
P_use   = P.ffill()                    # 中间缺 bar → 价格不变（0 收益）
R       = P_use[1:] / P_use[:-1] - 1   # 相邻两个建仓价之间的收益
died    = last_idx < (row + 1)         # 窗口结束前，该符号已经没有任何价格
R       = R − died * delist_shock
entry_ok = last_idx >= row             # 建仓时还有价格
```

- **ffill 只影响中间缺口**（停牌几天后恢复），不会把「已退市」变成一条水平线，
  因为 `last_idx` 记的是整列最后一个有效价。
- `died` 的符号在窗口结束前消失 → 用最后一个可成交价平仓，再额外扣
  `delist_shock`。默认 0 是**对策略有利**的口径，报告里必须写清楚用的是哪个。
- `entry_ok` 把「建仓时已经退市」的符号从收益和权重里同时清零，避免
  「买了一个不存在的币」。

### 5.2 重叠持有：`_smooth_weights`

`--hold 5` 不是「持有一个 5 日收益序列」。它等价于**5 个错位一天的子组合，
每个占 1/5 资金**：

```
W_t = (1/cnt) · Σ_{k=t-h+1}^{t} W_target_k        （cnt = 该区间内有持仓的行数）
```

- 满仓稳态下 `cnt = h`，即每期把资金等分到最近 h 次建仓的目标组合上，
  并在每次调仓时再平衡回 1/h。
- 区间内没有持仓的行（样本开头、全池为空的日子）不参与平均，
  避免用 0 权重的空行稀释真实仓位。
- `simulate` 最后 `iloc[h-1:]` 丢掉前 h−1 个「资金没铺满」的窗口。

**为什么不能直接把重叠的 h 期远期收益连乘**：那样同一个 5 日收益会被计入 5 次，
复利次数凭空多 5 倍。股票框架在 `_overlapping_strategy_returns` 里踩过这个坑，
这里的做法是逐窗口算真实组合收益，再让它们自然连乘 —— 每个窗口只贡献一次。

### 5.3 换手与成本

```python
turn[0] = Σ|W[0]|                 # 第一个窗口：全部新建
turn[i] = Σ|W[i] − W[i−1]|        # 之后：权重变化
cost    = turn * (fee_bps + slippage_bps) / 1e4
```

- 对 `|Δw|` 收费，等于「买入和卖出各收一次单边费」，和交易所实际收费一致。
- `fee_bps` 缺省按市场给：现货 10bp、永续 5bp；`slippage_bps` 默认 2bp。
- 多空组合的稳态换手大约是单边的 **2 倍**（两条腿都在动），
  所以 `年化成本` 列通常比直觉高，这是对的。
- 成本**逐窗口从收益里扣**，不是从累计净值末尾一次性扣 —— 后者会高估
  复利效果（早期亏掉的钱不会继续参与复利）。

### 5.4 资金费：`funding_delta`

数据湖 `um/funding/` 存的是**每次结算的费率**（`funding_rate`，多为 8 小时一次，
`funding_interval_hours` 记录间隔），不是年化费率。

```python
cum  = pivot(funding_rate).sort_index().cumsum()      # 每个符号的累计费率
cumZ = 前面垫一个全 0 行                                # 让 searchsorted 能取到「第一笔之前」
pos  = cumZ.index.searchsorted(entry_times, "left")
C    = cumZ.iloc[pos]                                  # 建仓时刻之前的累计
dF   = diff(C, axis=0)                                 # 窗口 i 内新增的累计费率
funding = −(W · dF).sum(axis=1)
```

- **符号**：多头付正费率、空头收正费率，所以 `funding = −W·dF`。
  汇总表里「年化资金费」**正数 = 收到**。
- 用 `searchsorted` 而不是 `merge_asof`：一次向量化取完所有调仓时刻，
  且不会因为索引类型不一致静默失败（`ignore_index=True` 会退化成 RangeIndex，
  所有取值都落在同一个位置 —— 这个坑代码里注释了）。
- 区间是 `(建仓 i, 建仓 i+1]` 的全部结算，和持仓窗口严格重合。
- `funding_delta` 只对 `um` 生效；`spot` 在 `__post_init__` 里被强制关掉。

> 踩过的坑：归档的 fundingRate CSV 是**三列** `calc_time, funding_interval_hours, last_funding_rate`。
> 早期解析只读两列，把 interval（1/4/8）当成了费率，回测里算出年化 22 万%。
> 现在 `01_fetch_bv.py` 按列数分支解析，`行情数据/README.md` 里有 schema 说明。

### 5.5 其他输出列

| 列 | 含义 | 用途 |
|---|---|---|
| `n_long` / `n_short` | 权重 > 1e-12 / < −1e-12 的符号数 | 判断腿是不是太窄 |
| `raw_exposure` | Σ\|W\| | 自检断言 ≤ `gross_leverage` |
| `dead_exits` | 窗口内撞退市的持仓数 | 退市口径的审计 |
| `days` | 相邻建仓时刻的自然日差 | 年化的分母，1h 时也是真实天数 |

---

## 六、统计层 `stats.py`

### 6.1 采样单位是「调仓日」，不是「交易日」

`IC`、`FM`、`NW` 的输入都是 `date × symbol` 宽表，日期轴 = 调仓日。
如果日频调仓却按每个交易日重复采样，相邻两个 IC 高度相关，t 值会被虚高；
按调仓日采样，每个观测对应一个独立决策，才是正确的统计单位。

### 6.2 `forward_returns` —— 重叠的远期收益

```python
fwd[:-h] = P_use[h:] / P_use[:-h] - 1      # 第 i 天建仓，持 h 个窗口
```

和 `simulate` 的收益算法同源（同样 ffill、同样处理退市），区别是
**它不做错位子组合**——每一行都是「如果只在第 i 天建一次仓会怎样」。
这些行彼此重叠（h>1 时），所以后面的 NW 必须把滞后阶数下限顶到 h−1。

### 6.3 `ic_series` —— 向量化 Spearman

Spearman IC = 秩的 Pearson 相关：

```python
rf, rr = F.rank(axis=1), R.rank(axis=1)
ic = Σ(dF·dR) / sqrt(ΣdF² · ΣdR²)
ic[n < min_obs] = NaN
```

一次算完所有截面，比逐日调用 `scipy.spearmanr` 快两个数量级（几千个截面时差别明显）。
和 `scipy.stats.spearmanr` 逐日比对过，结果完全一致。

**IC 的符号**：IC > 0 = 因子值越大、未来收益越高；IC < 0 = 反向。
币圈动量因子普遍是负 IC（反转），这时应该用 `--direction short_top`，
而不是宣布「因子没用」。

### 6.4 `fama_macbeth` + `newey_west_se`

逐调仓日跑截面回归 `ret ~ 1 + factor (+ controls)`，收集斜率序列，再做：

- **naive t**：`mean(slope) / (std(slope)/√n)`
- **NW t**：Bartlett 核 HAC 标准误，滞后阶数
  `lags = max(floor(4·(n/100)^(2/9)), h−1)`。

```python
var = mean(xc²)
for k in 1..lags:
    var += 2·(1 − k/(lags+1))·mean(xc[k:]·xc[:-k])
se  = sqrt(max(var, 0) / n)
```

`max(var, 0)` 是必须的：序列几乎为常数时 HAC 近似可能算出微负值，
直接开方会 `RuntimeWarning` + NaN（这个警告真实出现过）。

两类自相关都要覆盖：

1. **时间序列自相关**：因子值本身有持续性，相邻截面的斜率相关。
2. **重叠窗口的 MA(h−1) 结构**：`forward_returns` 重叠导致。

所以 `min_lags = max(0, h−1)` 是**下限**，不是全部。

`slope_bp` 只是为了好看（乘 1e4），比较因子强弱请用 t 值或 IC，
不要跨因子比较斜率绝对大小 —— 量纲不同。

### 6.5 `winsorize_rows` —— 只给统计用

```python
lo, hi = df.quantile(0.01, axis=1), df.quantile(0.99, axis=1)
df.clip(lower=lo, upper=hi, axis=0)
```

- **IC 不需要缩尾**：秩相关只看顺序，一两个百倍币不影响。
- **FM 和分位平均远期收益需要**：等权平均会被极端值绑架。
- **组合模拟绝不缩尾**：你真的会持有那只币，不能假装它只涨了 300%。

`run_variant` 里 `fwd_s = winsorize_rows(fwd)` 只喂给统计三件套，
`simulate` 永远用原始 `P`。

### 6.6 `quantile_forward_means`

各分位的平均远期收益。这是**信号强度的直观读法**，不是策略绩效：
它没扣成本、没考虑重叠、没做加权。看单调性即可，绝对值不要当收益。

---

## 七、绩效指标 `portfolio_metrics`

年化一律按 **365 天 / 真实日历天数**，不是 252 个交易日：

```python
total_days = sum(窗口 days)
ann  = (1 + 累计收益)^(365/total_days) − 1
per_year = 365 / mean(窗口 days)
vol  = std(窗口收益) × √per_year
sharpe = ann / vol
```

三个必须知道的口径：

1. **破产保护**：净值一旦 ≤ 0（空腿被极端逼空，亏损 > 100%），
   `ann = −1`、`mdd = −1`，汇总表标「破产」。
   否则 `(1+cum)^(365/days)` 会对负数开分数次幂，得到复数并抛 `TypeError`。
2. **最大回撤是逐窗口算的**：`eq` 的采样点是调仓日，不是每天。
   窗口内部的回撤看不见，所以 **`最大回撤` 是乐观的**；
   真实回撤要拿日频净值再算一遍。
3. **索提诺的分母**是「负收益窗口」的标准差，不是下行偏差的完整定义；
   窗口数少时（< 20）不要看这个数。

`benchmark_btc` 用同区间、同市场的 BTCUSDT 日线买入持有，
和策略并排给出累计/年化/波动/夏普/回撤。**任何结论都必须带这一行**，
否则读者无法判断「年化 20%」在同期 BTC 面前算不算好。

---

## 八、产物

### 8.1 汇总表（6 个 sheet）

| sheet | 内容 | 怎么用 |
|---|---|---|
| 汇总统计 | 每个持有窗口一行：IC / ICIR / IC_t_NW / FM 斜率与 t / 分位价差 / 年化 / 波动 / 夏普 / 回撤 / 年化成本 / 年化资金费 / 平均毛敞口 / 退市次数 | 一页看完，先看 IC_t_NW 和年化成本 |
| 分位数远期收益 | 各分位平均远期收益 | 看单调性，判断信号形状 |
| FM回归与NW检验 | FM 斜率（每单位 / BP）、naive t 与 NW t、IC 的 t、多空收益的 t | naive vs NW 差很多 = 自相关严重 |
| IC时间序列 | 逐调仓日 Spearman IC | 画累积 IC、看稳定性 |
| 换手与成本 | 平均/年化换手、累计成本、累计资金费、毛/净累计、平均腿数 | 判断容量和成本是否吃掉信号 |
| 分年度 | 每年净收益（每个持有窗口独立） | 看是不是靠某一年 |

`组合明细_持有{h}.csv` 是逐窗口明细（`gross / cost / funding / net / turnover /
n_long / n_short / raw_exposure / dead_exits / days`），做二次分析直接读它。

### 8.2 图（`figs/`）

| 文件 | 内容 |
|---|---|
| `dashboard.png` | 一页总览：净值 + 分层 + IC |
| `quantile_curves_{h}.png` | 各分位的累计净值曲线 |
| `ic_distribution.png` | IC 分布直方图 + 均值 |
| `fm_regression.png` | FM 斜率随时间的走势 |
| `t_value_compare.png` | naive t vs NW t（自相关有多严重） |
| `neutralization_compare.png` | 原始 vs 中性化的 IC / 净值 |
| `equity_drawdown.png` | 三面板：净值 vs BTC、回撤、累计成本与资金费 |
| `yearly_returns.png` | 分年度柱状图 |

`make_all` 对每张图单独 try/except：某张图画失败只打印 `⚠️ ... 画图失败`，
不会让整次回测前功尽弃。

---

## 九、参数详解

| 参数 | 默认 | 说明 |
|---|---|---|
| `--market` | `spot` | `spot` 现货 / `um` U 本位永续（只有 um 扣资金费） |
| `--tf` | `1d` | `1d` / `1h` |
| `--factor` | `mom_30d` | 因子面板里的列名 |
| `--factor-panel` | 空 | 读项目自建面板，不填则读公共因子库 |
| `--start` `--end` | 全历史 | UTC，**左闭右开** |
| `--rebalance` | `daily` | `daily` / `weekly` / `monthly` / `every` |
| `--every` | 1 | `rebalance=every` 时的 N |
| `--hold` | `1,5,10,20` | 持有几个**调仓窗口**，不是自然日 |
| `--quantiles` | 5 | 截面分几组 |
| `--direction` | `long_top` | 做多高值；`short_top` 反过来（做反转） |
| `--mode` | `long_short` | `long_short` / `long_only` / `short_only` |
| `--weighting` | `equal` | `equal` / `adv` / `vol_inv` |
| `--gross-leverage` | 1.0 | 多空合计毛敞口，多空下每腿 = 该值 / 2 |
| `--top-n` | 100 | 每日池子上限（按 14 日 ADV 排名） |
| `--adv-min` | 1e6 | 日均成交额下限（美元） |
| `--min-history` | 30 | 上市不足 N 天不进池 |
| `--adv-lookback` | 14 | ADV 与 logADV 的滚动窗口（天） |
| `--exec-price` | `open` | `open` 次日开盘 / `close` 次日收盘 |
| `--fee-bps` | 现货 10 / 永续 5 | 单边手续费 |
| `--slippage-bps` | 2 | 单边滑点 |
| `--delist-shock` | 0 | 撞退市时额外扣的亏损（0 = 最后可成交价平仓） |
| `--no-funding` | 关 | 永续不扣资金费 |
| `--neutralize` | `logadv_beta` | `None` / `logadv` / `logadv_beta` / `logadv_beta_vol` |
| `--mad-n` | 5.2 | MAD 去极值倍数 |
| `--beta-window` `--vol-window` | 30 | 风格因子的滚动窗口（天） |
| `--min-obs` | 20 | 单期截面最少符号数（分组 / IC） |
| `--fm-min-obs` | 10 | 单期 FM 回归 / 中性化回归的最少符号数 |

### 推荐组合

| 场景 | 参数 |
|---|---|
| **初筛**（扫几十个因子） | `--market um --rebalance weekly --hold 1,4 --top-n 80 --no-plots` |
| **细看**（候选因子出图） | `--rebalance weekly --hold 1,4 --quantiles 10` |
| **容量评估** | 加 `--weighting adv --slippage-bps 20` 做敏感性 |
| **中性化检验** | `--neutralize None` 与默认各跑一次，对比 `原始IC / 中性化IC` |
| **小时线** | 一定带 `--start`，`--hold` 用 bar 数（日频调仓时 `hold=24` ≈ 一天） |

---

## 十、和股票框架的差异（**重点**）

| 环节 | 股票框架 | 这里 | 为什么必须改 |
|---|---|---|---|
| 成交时点 | T+1 **收盘** | T+1 **开盘**（可切） | 币圈 24/7 连续，次日开盘就是第一可成交价 |
| 年化 | 252 个交易日 | **365** 天，按真实日历天数 | 没有休市、没有周末 |
| 方向 | 只能做多，算多空价差 | **真建多空组合** | 永续可直接做空，空头还能收资金费 |
| 成本 | 不考虑 | 换手 × (手续费+滑点)，逐窗口扣 | 币圈换手极高，这是最致命的偏差 |
| 资金费 | 无 | 逐窗口按实际结算累计扣/收 | 多空组合的空头每年能收 10~20% |
| 退市 | `is_st` / `delist_date` 缺失 | 持仓撞退市 → 最后价平仓 + `delist_shock` | 归档里有 235 个已下架币 |
| 中性化 | 市值 + 行业 | **流动性(size) + BTC Beta (+波动率)** | 币圈没有行业；共同风险是 Beta |
| 加权 | 等权 | 等权 / 成交额 / 逆波动 | 小币流动性差异比 A 股大一个数量级 |
| 分组 | `pd.qcut` | `ceil(rank/count*n)` | 大量价格并列，qcut 会丢组 |
| 极值 | 全样本 MAD | **逐日截面 MAD + 逐期缩尾** | 全样本统计量会把未来漏进来 |
| 回测起点 | 无预热 | `_warmup_start` 往前多读一段 | 否则池子和风格因子在开头被静默清空 |

保留不变的四项：**每日截面去极值/标准化**、**日历化调仓**、
**Fama-MacBeth + Newey-West**、**风格中性化后看残差还有没有 IC**。
这四项是因子研究的通用方法，和资产类别无关。

---

## 十一、几个必须知道的口径

### 11.1 重叠持有 ≠ 重复复利

见 §5.2。一句话：`--hold 5` 是 5 个错位子组合各占 1/5，不是把 5 日收益连乘 5 次。

### 11.2 资金费按「每次结算」累计，不是按年化费率

见 §5.4。汇总表的「年化资金费」是**算出来的结果**（`Σ funding / 总天数 × 365`），
不是输入假设。正数 = 收到。

### 11.3 破产保护

净值穿 0 时年化记 −1、回撤封顶 −1。不这么做会对负数开分数次幂，
得到复数并抛 `TypeError`。看到「破产 = True」就不要再看夏普了，没有意义。

### 11.4 统计用缩尾远期收益，组合用真实收益

见 §6.5。这是两条独立的数据路径，**不要为了「一致」把组合模拟也缩尾**。

### 11.5 现货的多空是「理论价差」

`--market spot --mode long_short` 会照常建多空组合，但现货做不了空。
那条曲线只能当多空价差读；现货可交易的只有 `--mode long_only`。
真正能双边的只有 `um`。写报告时必须标注市场。

### 11.6 退市口径

持仓期间该币不再有 bar：用最后一个可成交价平仓，`dead_exits` 计数。
`--delist-shock 0.3` 表示额外再扣 30%。默认 0 是保守（对策略有利）的口径。
真实退市往往更惨（流动性归零、只能场外折价出），**结论里必须写清用的是哪个值**。

### 11.7 最大回撤是逐调仓窗口的

`eq` 的采样点是调仓日。窗口内部的回撤不可见，所以 `最大回撤` 偏乐观。
日频调仓时误差不大；周频/月频调仓时，真实回撤会明显更深。

---

## 十二、已知限制

1. **只有 USDT 计价、只有日线与小时线**。5m/1m 没拉（体积从 1.4 GB 涨到 100 GB+）。
2. **没有行业中性化**，用 Beta 代替。币圈板块（DeFi / L2 / Meme）相关性很强，
   但目前没有可用的点时板块分类表，`logadv_beta` 只能剥离一部分共同风险。
3. **资金费只用于扣成本，没有做成因子**。
4. **滑点是固定 BPS**，没有按盘口深度建模。小币的实际冲击远大于 2bp，
   `--top-n` 较小时建议用 `--slippage-bps 20~50` 做敏感性。
5. **容量没算**。ADV 加权 + `gross_leverage` 起的组合规模要自己乘一遍，
   池子里排第 80 的小币可能只吃得住几万美元。
6. **幸存者偏差已处理**：股票池走数据湖的完整符号表（含已下架），
   但归档本身只从 2017/2019 年开始，更早上线的币的早期数据拿不到。
7. **因子面板不自动更新**。行情数据更新后必须重跑 `build_factors.py`，
   否则回测用的是旧面板（时间戳对不上时会直接报「调仓日上完全没有因子值」）。
8. **小时线的 ADV 仍按日线**。这是刻意的（更稳定），但意味着 1h 回测的池子
   最多一天变一次，不是每小时变。

---

## 十三、怎么扩展

### 加一个新因子

1. 在 `因子数据/build_factors.py` 的 `WINDOWS` / 计算段里加一列，重跑面板；
   或者在你的研究项目里单独算，走 `--factor-panel`。
2. 因子必须是**纯历史窗口**（`rolling` / `shift` 向前看），不能有任何
   全样本统计量（均值、分位、标准化）—— 那些在回测层按日做。
3. 跑 `run_backtest.py` 看 IC 的符号和 t 值，再决定 `--direction`。

### 加一个风格因子

1. `build_style` 里返回新的 `date × symbol` 宽表（日线滚动 → ffill 到调仓日）。
2. `neutralize_factor` 的 `controls` 里加名字，并扩一个 `neutralize` 档位。
3. 加一个 selftest 用例。

### 换成本模型

改 `simulate` 里的 `cost = turn * (fee_bps + slippage_bps) / 1e4`。
如果要做「按成交额比例的非线性冲击」，需要 `turn` 之外再拿到每只币的 ADV，
那就要把 `style["logadv"]` 传进 `simulate` —— 接口会变，改完记得跑自检。

### 加数据源

数据湖的读取统一走 `lake_io.load(market, tf, columns=..., start=..., end=...)`。
新数据（比如 mark price、未平仓量）建议在 `行情数据/` 下新开一个 `tf` 字面量
（如 `um/metrics`），不要塞进 K 线表。

---

## 十四、示例结果（2026-11-06 实测，用于确认口径，不是投资建议）

`mom_30d` / `um` / 周频 / top80 / 中性化 `logadv_beta` / 持有 1 和 4 周：

| 持有 | IC | IC_t_NW | 年化 | 波动 | 夏普 | 最大回撤 | 年化成本 | 年化资金费 |
|---|---|---|---|---|---|---|---|---|
| 1 周 | −5.19% | −4.95 | 16.91% | 40.72% | 0.42 | −40.21% | 3.14% | +14.37% |
| 4 周 | −5.41% | −3.50 | −0.31% | 33.85% | −0.01 | −47.08% | 1.47% | +10.05% |

同期 BTC 买入持有：累计 +889.9% / 年化 39.9% / 最大回撤 −76.7% / 夏普 0.66。

> **资金费那一列是「正 = 收到」**。这个组合的净收益里有一大块来自空头收到的
> 资金费，而不是因子本身。不扣资金费的同一组回测年化只有 3.91% / −7.70%。
> 看结论时这两列必须并排。

**读法**：IC 稳定为负且 t 值显著 —— 30 日动量在币圈横截面上是**反转**而不是延续，
`--direction short_top` 才对应「做多跌得多的」。但扣完成本后多空组合几乎不赚钱，
说明这个信号在 top80 这个流动性池子里已经被套利掉了。**这是一个诚实的否定结果**，
不要拿它当策略。

### 一个因子「算不算通过」的四道闸门

框架只负责把数字算对，判断标准要事先写死，不能看到结果再调。

| 闸门 | 标准 | 为什么 |
|---|---|---|
| 1. 信号存在 | IC 的 NW t 值 \|t\| > 2，且跨窗口同号 | 单窗口显著可能是偶然 |
| 2. 扣成本后为正 | 年化收益 > 年化成本 + 资金费拖累 | 币圈换手极高，毛收益没意义 |
| 3. 跨市场稳健 | `um` 与 `spot` 同号、量级相当 | 只在一边成立的是数据产物 |
| 4. 中性化后仍在 | `中性化IC` 与 `原始IC` 同号、量级不塌 | 否则因子只是规模的影子 |

任何一条不过就写否定结论，不再调参数救 —— 换参数不会改变符号。
`因子研究/风险调整动量因子/文档/研究计划.md` 是一个完整的例子（结论：否定）。

---

## 十五、术语速查

| 词 | 含义 |
|---|---|
| 调仓日 / 信号日 | 用当期最后一根 bar 的收盘算因子、定权重的日子 |
| 建仓价 | 信号日**下一根** bar 的 open（或 close） |
| 持有窗口 h | 持有 h 个调仓间隔，不是 h 个自然日 |
| 分位 Q1..Qn | 因子值从低到高，Qn 最高 |
| `long_top` | 做多 Qn、做空 Q1（默认） |
| 毛敞口 / `raw_exposure` | Σ\|w\|，多空各 0.5 时合计 1 |
| IC | 逐截面 Spearman 秩相关 |
| ICIR | mean(IC) / std(IC) |
| NW t | Newey-West HAC 修正后的 t 值 |
| FM 斜率 | 截面回归 `ret ~ factor` 的系数，乘 1e4 得 BP |
| 缩尾 | 把每期截面的 1%/99% 之外的值压到分位点上 |
| 中性化 | 回归掉 logADV / Beta / 波动率后取残差 |
| 破产 | 组合净值 ≤ 0，年化和回撤都记为 −100% |
