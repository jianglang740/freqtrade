# VolatilitySqueezeHARStrategy — 三层波动率建模策略

## 策略概述

基于 **Yang-Zhang Estimator + HAR-RV + 滚动 Z-Score Regime Detection** 的三层波动率建模架构，专门用于加密货币 1m 时间周期的短线交易。

| 层级 | 模型 | 作用 |
|------|------|------|
| **L1 估计层** | Yang-Zhang + Parkinson | 无偏波动率估计，效率比 ATR 高 5-8 倍 |
| **L2 预测层** | HAR-RV | 融合 1m/5m/1h 三个尺度的波动率预测 |
| **L3 状态层** | 滚动 Z-Score Regime | 实时识别 squeeze/normal/expanding/extreme 四种状态 |

## 入场逻辑

1. **压抑期确认**：`vol_zscore < -1.2` 持续 ≥ 5 根 K 线
2. **HAR 预测释放**：预测波动率 > 当前波动率 × 1.15
3. **Range 突破确认**：当前 K 线 range / HAR 预测 > 1.5
4. **成交量确认**：vol_ratio > 1.2
5. **方向过滤**：价格在短期 VWAP 上方 + EMA5 > EMA20 → 做多；反向 → 做空

## 风控架构

| 机制 | 规则 |
|------|------|
| 硬止损（兜底）| -10% |
| 动态止损 | HAR 预测 × 1.2（波动率大则宽，波动率小则紧）|
| 追踪止损 | 盈利 > 1× tp_width 后启动 |
| 目标止盈 | HAR 预测 × 3.0 |
| 超时退出 | 持仓 > 30 分钟且盈利 < 0.5% |
| 波动率崩溃退出 | vol_zscore 从 expanding 跌回 normal 且盈利 > 1% |

## 文件清单

```
user_data/strategies/VolatilitySqueezeHARStrategy.py   # 策略文件
user_data/config/config_volatility_squeeze.json        # 配置文件（合约模式）
```

## 回测

### 1. 下载数据

```bash
# 下载 BTC/ETH 合约 1m 数据（推荐至少 2 个月）
freqtrade download-data -c user_data/config/config_volatility_squeeze.json \
    -t 1m -p BTC/USDT:USDT ETH/USDT:USDT \
    --timerange 20260601-20260810
```

### 2. 运行回测

```bash
# 基础回测
freqtrade backtesting -s VolatilitySqueezeHARStrategy \
    -c user_data/config/config_volatility_squeeze.json \
    --timerange 20260701-20260810

# 详细回测（导出交易明细）
freqtrade backtesting -s VolatilitySqueezeHARStrategy \
    -c user_data/config/config_volatility_squeeze.json \
    --timerange 20260701-20260810 \
    --export trades --breakdown day

# 画图
freqtrade plot-dataframe -s VolatilitySqueezeHARStrategy -p BTC/USDT:USDT \
    -c user_data/config/config_volatility_squeeze.json
```

### 3. 超参优化

```bash
freqtrade hyperopt -s VolatilitySqueezeHARStrategy \
    -c user_data/config/config_volatility_squeeze.json \
    --hyperopt-loss SharpeHyperOptLossDaily \
    --timerange 20260701-20260810 \
    -e 200
```

可优化的参数：
- `yz_window` / `parkinson_window` — 波动率估计窗口
- `har_w1m` / `har_w5m` / `har_w1h` — HAR-RV 权重
- `squeeze_zscore` / `extreme_zscore` — 状态机阈值
- `sl_atr_mult` / `tp_atr_mult` — 风控倍数

## 模拟盘

```bash
freqtrade trade -s VolatilitySqueezeHARStrategy \
    -c user_data/config/config_volatility_squeeze.json \
    --dry-run
```

## 关键指标解读

回测完成后重点关注以下输出：

| 指标 | 健康范围 | 说明 |
|------|----------|------|
| 盈利因子 (Profit Factor) | > 1.3 | 毛利润/毛亏损 |
| 夏普比率 (Sharpe) | > 1.5 | 风险调整后收益 |
| 最大回撤 (Max Drawdown) | < 15% | 资金曲线最大回撤 |
| 胜率 | 40%-55% | 1m 策略不需要高胜率，靠盈亏比 |
| 平均盈亏比 | > 2.0 | 平均盈利/平均亏损 |
| ENTER TAG STATS | — | 看 `vol_squeeze_long` vs `vol_squeeze_short` 哪个表现更好 |
| EXIT REASON STATS | — | 看哪种退出方式贡献最多利润/亏损 |

## 调试建议

### 回测交易数为 0？

1. 确认数据已下载：`freqtrade list-data -c user_data/config/config_volatility_squeeze.json`
2. 降低 `squeeze_zscore` 阈值（如从 -1.2 调到 -0.8）
3. 降低 `min_squeeze_duration`（如从 5 调到 3）
4. 降低 `range_breakout_mult`（如从 1.5 调到 1.2）
5. 去掉 `volume_confirm` 条件（临时测试）

### 交易太频繁/太少？

- 太频繁：收紧 `squeeze_zscore`（更负）、提高 `range_breakout_mult`
- 太少：放宽 `squeeze_zscore`、降低 `har_release_threshold`、缩短 `min_squeeze_duration`

### 止损被频繁扫掉？

- 增大 `sl_atr_mult`（如从 1.2 调到 1.5）
- 检查 `har_pred` 是否低估了真实波动率（可能需要调整 HAR 权重）

## 进阶：自定义数据源增强

策略在 `bot_loop_start` 中预留了外部数据接口。如果你想增强：

- **链上数据**：在 `bot_loop_start` 中拉取链上大额转账/交易所流入流出
- **资金费率**：OKX 合约资金费率可以作为情绪指标
- **订单簿失衡**：微观结构信号可以叠加到入场条件中

在 `populate_indicators` 末尾添加自定义列即可。

## 已知限制

1. **1m 数据量大**：回测时需要足够的内存，建议先只跑 BTC/USDT:USDT 一个交易对
2. **HAR-RV 需要预热**：`startup_candle_count = 500`，前 500 根 K 线不会生成信号
3. **合约模式**：当前配置为 `trading_mode: futures`，如需现货请改为 `spot` 并将 `can_short` 设为 `False`
4. **dry_run 资金**：配置为 500 USDT / 80% 可用比例，回测时可根据实际资金调整

---

> ⚠️ **风险提示**：本策略基于历史波动率统计规律，加密货币市场可能出现黑天鹅事件导致波动率模型失效。实盘前务必经过充分的回测和模拟盘验证，且不要投入超过可承受损失范围的资金。
