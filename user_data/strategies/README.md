# Freqtrade 示例策略集

> 5 个渐进式策略，从入门到进阶，涵盖 freqtrade 策略开发的核心知识点。

---

## 策略列表

| 编号 | 策略名           | 知识点                          |   难度   |
| :--: | ---------------- | ------------------------------- | :------: |
|  01  | SimpleMAStrategy | 三核心方法、均线交叉            |    ⭐    |
|  02  | RSIMACDStrategy  | 多指标组合、enter_tag、超参预留 |   ⭐⭐   |
|  03  | AdvancedStrategy | 5个自定义回调、生命周期钩子     |  ⭐⭐⭐  |
|  04  | DCAStrategy      | DCA/马丁加仓、仓位调整          | ⭐⭐⭐⭐ |
|  05  | MultiTFStrategy  | @informative 多时间框架         |  ⭐⭐⭐  |

---

## 前置准备

### 1. 数据下载

所有策略都需要 5m K线数据。05_MultiTFStrategy 额外需要 1h 数据。

```bash
# 激活环境
conda activate freqtrade

# 下载 Binance 合约数据（与 config_all.json 中的白名单匹配）
# 5m K线（所有策略都需要）
freqtrade download-data -c user_data/config/config_all.json -t 5m -p BTC/USDT:USDT ETH/USDT:USDT --timerange 20260601-20260806

# 1h K线（05_MultiTFStrategy 需要）
freqtrade download-data -c user_data/config/config_all.json -t 1h -p BTC/USDT:USDT ETH/USDT:USDT --timerange 20260601-20260806
```

> `config_all.json` 已经配置好了白名单和代理，直接用即可。

### 2. 确认数据已下载

```bash
freqtrade list-data -c user_data/config/config_all.json
```

应该能看到 BTC/USDT:USDT 和 ETH/USDT:USDT 的 5m 和 1h 数据。

---

## 运行方式

### 回测（快速验证策略效果）

```bash
# 01 双均线
freqtrade backtesting -s 01_SimpleMAStrategy -c user_data/config/config_all.json --timerange 20260701-20260806

# 02 RSI+MACD
freqtrade backtesting -s 02_RSIMACDStrategy -c user_data/config/config_all.json --timerange 20260701-20260806

# 03 高级回调
freqtrade backtesting -s 03_AdvancedStrategy -c user_data/config/config_all.json --timerange 20260701-20260806

# 04 DCA马丁
freqtrade backtesting -s 04_DCAStrategy -c user_data/config/config_all.json --timerange 20260701-20260806

# 05 多时间框架
freqtrade backtesting -s 05_MultiTFStrategy -c user_data/config/config_all.json --timerange 20260701-20260806
```

### 画图（可视化买卖信号）

```bash
# 指定某个策略 + 某个交易对
freqtrade plot-dataframe -s 01_SimpleMAStrategy -p BTC/USDT:USDT -c user_data/config/config_all.json

# --trade-source 可以指定用哪个回测结果的买卖记录
freqtrade plot-dataframe -s 01_SimpleMAStrategy -p BTC/USDT:USDT -c user_data/config/config_all.json --trade-source file
```

### 模拟盘（实盘验证）

```bash
freqtrade trade -s 03_AdvancedStrategy -c user_data/config/config_all.json --dry-run
```

---

## 各策略详解

### 01 — SimpleMAStrategy（双均线交叉）

**学什么：**

- freqtrade 策略的最小结构
- `populate_indicators` — 计算指标
- `populate_entry_trend` — 设置买入信号
- `populate_exit_trend` — 设置卖出信号
- `.shift(1)` 的用法（金叉/死叉判断）

**策略逻辑：**

- fast EMA(12) 上穿 slow EMA(26) → 做多
- fast EMA(12) 下穿 slow EMA(26) → 平多

**只有 2 个参数**：`fast_ma=12`, `slow_ma=26`

---

### 02 — RSIMACDStrategy（多指标组合）

**学什么：**

- 多个指标的组合判断
- `enter_tag` 的用法（回测报告中按标签分类统计）
- `IntParameter` 的定义（为超参优化做准备，当前用默认值）
- `startup_candle_count` 的作用

**策略逻辑：**

- 三种入场场景（各自有不同的 enter_tag）：
  1. `rsi_oversold` — RSI 超卖 + 放量
  2. `macd_golden_cross` — MACD 金叉
  3. `macd_hist_positive` — MACD 柱转正 + RSI 不超买
- RSI 超买或 MACD 死叉则卖出

**回测后关注**：ENTER TAG STATS 表 — 看哪种入场信号胜率更高。

---

### 03 — AdvancedStrategy（自定义回调）

**学什么：**

- `bot_start()` / `bot_loop_start()` — 生命周期钩子
- `confirm_trade_entry()` — 入场准入控制（限制每天交易次数）
- `custom_exit()` — 自定义退出条件（目标盈利+超时）
- `custom_stoploss()` — 动态止损（盈利越多止损越紧）
- `custom_entry_price()` — 自定义入场价格

**策略逻辑：**

- 入场：EMA20 > EMA50 趋势向上 + RSI 30~45 回调区
- 退出：custom_exit 在盈利 3% 或持仓 8 小时亏损时退出
- 止损：动态调整 — 盈利 > 10% 时止损收紧到 -5%

**运行后观察**：

- EXIT REASON STATS 表 — 看 `target_profit` vs `timeout_loss` 的分布
- 注意确认 custom_stoploss 是否比 stoploss 更紧

---

### 04 — DCAStrategy（DCA 马丁）

**学什么：**

- `position_adjustment_enable = True` 和 `max_entry_position_adjustment`
- `adjust_trade_position()` — 每根K线的加仓决策
- `custom_exit()` 用于整体盈利退出
- 通过 `self.dp.get_analyzed_dataframe()` 在回调中获取当前数据
- `trade.nr_of_successful_entries` 检查已加仓次数

**策略逻辑：**

- 首仓：RSI < 35 超卖时买入
- 加仓：每跌 3% 补一次，金额按马丁倍率递增（1x → 1.5x → 2.5x → 4x → 6x）
- 退出：持仓均价盈利 2% 全部清仓
- 保护：RSI < 20 停止加仓（极端行情不接飞刀）

**重要提示：**

- DCA 策略在回测时需要更多资金，配置里的 `stake_amount` 和 `dry_run_wallet` 要足够
- 马丁策略在持续单边行情中回撤很大，实盘需谨慎

---

### 05 — MultiTFStrategy（多时间框架）

**学什么：**

- `@informative("1h")` 装饰器的完整用法
- 大周期判断方向，小周期精确入场
- 信息型列名的自动规则（`_1h` 后缀）
- `startup_candle_count` 的计算（1h EMA20 需要 20×12=240 根 5m K线）

**策略逻辑：**

- 大趋势：1h EMA20 > EMA50（只在上升趋势的回调中做多）
- 入场：1h 趋势向上 + 5m RSI 超卖 + 5m EMA12 上穿 EMA26
- 出场：1h 趋势转弱 或 5m RSI 超买

**回测后观察**：

- 对比 01_SimpleMAStrategy（没有大趋势过滤）的结果
- 应该看到交易笔数减少但胜率提高

---

## 学习路径建议

```
Day 1-2   01_SimpleMAStrategy   搞懂三个核心方法的基本结构
          改参数（fast_ma/slow_ma）反复回测，看结果怎么变

Day 3     02_RSIMACDStrategy    学多指标组合+enter_tag
          回测后看 ENTER TAG STATS，理解不同信号的胜率差异

Day 4-5   03_AdvancedStrategy   学 5 个自定义回调
          重点理解 custom_exit 和 custom_stoploss 的调用时机

Day 6     05_MultiTFStrategy    学 @informative 装饰器
          理解大周期数据如何自动合并到小周期

Day 7+    04_DCAStrategy        学仓位调整
          理解 adjust_trade_position 的调用频率和加仓逻辑
```

---

## 常见问题

**Q: 回测完 0 笔交易怎么办？**
A: 检查三点：① 数据是否已下载 ② 时间范围是否正确 ③ 策略条件是否太严格（放宽 RSI 阈值、去掉 volume 条件等）

**Q: 回测很慢怎么办？**
A: 04_DCAStrategy 因为每根K线都要检查加仓，会比其他策略慢。减少 `--timerange` 范围、减少白名单交易对数量可以加速。

**Q: 策略怎么改才能盈利？**
A: 先看 EXIT REASON STATS 表 — 哪种退出方式亏损最多，针对性优化。比如 stop_loss 亏损太多 → 调整入场时机 + 收紧止损。

**Q: 能用 OKX 配置跑这些策略吗？**
A: 可以，把 `-c user_data/config/config_okx.json` 替换掉 `config_all.json` 即可。但 OKX 配置只有 BTC/USDT 数据，且是 1m K线，需要先下载 5m 数据。
