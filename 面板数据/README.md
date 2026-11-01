# 面板数据

把散在 `user_data/data/binance/*.feather` 里的 K 线，压成**一张长表面板**，专门给单因子检验
和横截面研究用。不要把这里当数据备份 —— 它是从 feather 现场生成的派生品。

## 目录内容

| 文件 | 是什么 |
|---|---|
| `spot_1h.parquet` | 现货小时线面板，联合主键 `(pair, date)` |
| `spot_1d.parquet` | 现货日线面板，同结构 |
| `*.manifest.json` | 生成快照：行数、起止、每个币对缺口、与上一版的差异 |
| `build_panel.py` | 现场扫描 feather → 重建面板（唯一入口） |
| `panel_io.py` | 读取与常用变换（转宽表、聚合日线、流动性选池） |

## 面板结构

```
pair            分类编码，如 "BTC/USDT"
date            UTC，毫秒精度（与 feather 一致）
open high low close volume   交易所原始六列
quote_volume    估算值 = close × volume，仅用于流动性过滤
```

- **联合主键 `(pair, date)`**，已排序、已去重（重复时保留后读到的）。
- `volume` 是**基础货币数量**（BTC、ETH 个数），不是 USDT 金额。要 USDT 金额请用
  `quote_volume`，但它是 `close × volume` 的估算，和交易所官方计价成交额有微小差异。
- 没有 `trade_count`：feather 里本来就没有这一列。

## 更新流程（三步，缺一不可）

```bash
source /opt/anaconda3/etc/profile.d/conda.sh && conda activate freqtrade

# 1. 先补数据（1h 全历史；新增币对改 --pairs-file 即可）
freqtrade download-data -c user_data/config/config_spot_panel.json --trading-mode spot \
  --pairs-file <pairs.json> -t 1h --timerange 20170101-

# 2. 重建面板（全量，几秒）
python 面板数据/build_panel.py --timeframes 1h 1d

# 3. 刷新 K 线数据报告（工程纪律：数据一变就重跑）
python gen_data_report.py
```

第 2 步会打印与上一版的差异：新增了哪些币对、多了几行、哪些币对延长了几天。
**这就是「兼容后期更新」的实现方式** —— 不是增量合并，而是每次全量重建 + 差异审计。

## 兼容后期更新的四条设计

1. **扫描目录，不读白名单**。新拉了 `XYZ_USDT-1h.feather`，重跑一次就自动出现在面板里；
   删掉 feather 的币对也会自动消失（并在差异里报出来）。
2. **全量重建，不做增量 upsert**。增量合并要处理「同一根 K 线补了更晚的数据」「币对改名」
   「时区偏移」这些坑，一旦出错是静默的。全量只要几秒，出错不可能。
3. **字段集合固定 + `schema_version`**。加字段必须同时升版本号并写进 manifest；
   因子代码可以据此判断面板是否可用，而不是撞一个 KeyError。
4. **manifest 记录每个币对的覆盖与缺口**。因子检验前先看 `pairs_detail`，
   别让「某个币只有 3 个月数据」悄悄混进截面排名。

## 已知偏差（做因子结论前必须过一遍）

- **幸存者偏差**：池子只有「今天还活着」的 100 个币，退市/暴跌归零的币不在里面。
  用这套面板做动量/反转，会系统性偏乐观。
- **现货不能做空**：面板能做「收益数学」，但不能直接验证多空组合的实盘可行性。
- **起点不齐**：2017 年只有少数几个币，截面宽度随年份变化；早期截面（<10 个币）
  的排序结果不可靠，`liquid_universe()` 的 `top_n` 会因此选不满。
- **1h 现货的成交额起点**：老币的早期小时线成交额极小，`min_quote_volume` 过滤会
  在早期把大部分币筛掉，这是特性不是 bug。
- 面板是**派生品**：feather 变了它不会自己变，必须重跑 `build_panel.py`。
- **EPIC/USDT 是手工补的**：它在 Binance 现货已下架，`freqtrade download-data` 直接跳过
  （不走交易所元数据校验就查不到这个 pair）。1h 是用 `data.binance.vision` 的月度/日度
  归档拼的，1d 的尾部（2026-08-21 之后）是由 1h 聚合追加的。其余 99 对都来自 freqtrade。
- `1h` 面板有 40 个币对存在零星缺口（合计约 1.7k 根，占总行数 0.05%），
  基本都是 2017–2019 年交易所自身的停机，不是下载漏了。

## 当前快照（2026-10-31）

| 面板 | 行数 | 币对 | 跨度 | 体积 |
|---|---|---|---|---|
| `spot_1h` | 3,202,163 | 100 | 2017-08-17 04:00 → 2026-10-31 13:00 | 94 MB |
| `spot_1d` | 133,476 | 100 | 2017-08-17 → 2026-10-30 | 4.9 MB |

数据源清单以根目录 [K线数据报告.md](../K线数据报告.md) 为准，那边由 `gen_data_report.py` 现场扫描生成。

## 常用片段

```python
from panel_io import load_panel, wide, to_daily, liquid_universe

df = load_panel("1h")
close = wide(df, "close")
ret = close.pct_change(fill_method=None)

daily = to_daily(df)                  # UTC 00:00 为界聚合
adv_ok = liquid_universe(daily, top_n=50)
```
