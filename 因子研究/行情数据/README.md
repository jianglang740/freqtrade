# 因子研究 · 币安加密货币因子数据湖

币安 **现货（spot）** + **U 本位永续（um）** 的 Hive 分区量价数据湖，专门给因子研究用。
目录风格对齐 [`量化研报复现/曹春晓量化因子复现/行情数据/`](../../../../../Desktop/量化研报复现/曹春晓量化因子复现/)。

**整个湖是自包含的**：数据、构建脚本、读取层、缓存、本文档都在 `因子研究/行情数据/` 这一层里，
拷走这个目录就能独立运行。

**这里只放数据和数据工具，不放任何策略的研究脚本。** 研究脚本、报告和产物跟着策略走，
在 [`策略池/<策略名>/`](../../策略池/)。

## 和 `user_data/data/binance/` 是两条管子，不合并

|  | `user_data/data/binance/` | 本目录 `行情数据/` |
|---|---|---|
| 用途 | 喂两个 dry-run 实盘实例 + `XsecMomentum` 策略回测 | 因子研究 |
| 来源 | `freqtrade download-data`（交易所 API） | `data.binance.vision` 归档 |
| 看得到已下架币 | ❌ 看不到 | ✅ 看得到 |
| 币种 | 100 个现货对 + 部分合约 | 全部 USDT 现货 + 全部 USDT 永续 |
| 格式 | `.feather`，freqtrade 原生 | Hive 分区 parquet |
| 文档 | 根目录 [K线数据报告.md](../../K线数据报告.md) | 本文 |

`gen_data_report.py` / `K线数据报告.md` **只管** `user_data/data/`，**不覆盖**本目录。

---

## 一、目录结构

```
因子研究/
├── tradfi_probe.py / tradfi_contracts.csv   ← TradFi 合约标记（湖外，不属于行情数据）
└── 行情数据/                                ← 数据湖本体，本 README 就放在这里
    ├── README.md                            ← 本文
    ├── lake_io.py                           ← 读取层（新代码用这个）
    ├── _manifest.json                       ← 文件级指纹 + 逐数据集行数
    ├── _audit.json                          ← 逐年统计体检表
    ├── _build/                              ← 构建管线
    │   ├── common.py           常量、schema、HTTP、符号解析
    │   ├── 01_fetch_bv.py      下载归档 → 单符号缓存
    │   ├── 02_partition.py     缓存 → Hive 分区 + 主表 + manifest
    │   ├── 03_verify.py        7 项完整性校验（不通过就退出非零）
    │   └── 04_audit.py         生成 _audit.json
    ├── spot/                                ← 以下四个目录是数据本体，gitignore
    │   ├── 1h/year=2017/month=08.parquet … year=2026/month=10.parquet
    │   └── 1d/year=2017.parquet     … year=2026.parquet
    ├── um/
    │   ├── 1h/year=2019/month=09.parquet … year=2026/month=10.parquet
    │   ├── 1d/year=2019.parquet     … year=2026.parquet
    │   └── funding/year=2019.parquet … year=2026.parquet
    ├── basic/dim_instrument.parquet         ← 符号主表（上市/下架标签）
    └── .cache_bv/{market}/{tf}/<SYMBOL>.parquet  ← 下载缓存，删了只是要重下
```

进仓库的只有 `README.md` / `lake_io.py` / `_build/` / 两份 json；`spot/`、`um/`、`basic/`、
`.cache_bv/` 都在 `.gitignore` 里（体积上 GB，重建要几十分钟）。

用字面量 `spot` / `um` 和 `1h` / `1d` / `funding` 当路径，不搞 `market=spot/tf=1h` 的嵌套 —— 这样
`dataset("spot/1h", partitioning="hive")` 只多出 `year` 一列，路径一眼能读。

### ⚠️ 两个必须知道的坑

1. **`month=MM` 是文件名，不是目录。**（和股票仓库同样的设计，刻意对齐，不是笔误。）
   所以 hive 分区**只解析出 `year` 一列**，没有 `month` 列。
   要按月份过滤请走 `date` 列：

   ```python
   load("spot", "1h", start="2024-06-01", end="2024-07-01")
   ```

2. **分区剪枝只认 `year`。** `filter=ds.field("year") == 2024` 会只打开那 12 个文件；
   `start`/`end` 走的是 parquet 内部统计量的行组跳读，便宜但不是零成本。

---

## 二、表结构

### 2.1 K 线表 —— `spot/{1h,1d}`、`um/{1h,1d}`

归档 CSV 有 12 列，这里保留其中 11 列 + 2 列派生，落成 **13 列**：

| 列 | 类型 | 说明 |
|---|---|---|
| `symbol` | string | 交易所原生符号，如 `BTCUSDT`（**不是** `BTC/USDT`） |
| `base` / `quote` | string | `BTC` / `USDT` |
| `date` | timestamp[ms, UTC] | K 线**开始**时刻 |
| `open` `high` `low` `close` | double | |
| `volume` | double | 基础币数量（BTC 个数） |
| `quote_volume` | double | **计价币成交额，归档官方字段** |
| `trade_count` | int64 | 成交笔数 |
| `taker_buy_base_volume` | double | 主动买入量（基础币） |
| `taker_buy_quote_volume` | double | 主动买入额（计价币） |

- 主键 `(symbol, date)`，按 `(date, symbol)` 排序，分区内已去重。
- **`quote_volume` 是官方值**，不再是旧面板那种 `close × volume` 估算 —— 所以「估算偏差」这条坑不存在了。
  极少数早期行官方字段为空，此时才回退成 `close × volume`（可用 `quote_volume / (close*volume)` 自查，
  正常应在 0.999 附近）。
- `taker_buy_*` 是白送的微观结构因子原料（主动买入占比、买卖压力），本轮只存不用。
- 归档 zip 内的 CSV 在 2025 年后**带表头行**；解析器按「首列能否转成数字」丢掉表头，不用管。
- 时间戳单位按数值量级自动判断：**2025-01-01 之前是毫秒，之后是微秒**。

### 2.2 资金费率表 —— `um/funding`

```
symbol                 string                BTCUSDT
base/quote             string                BTC / USDT
date                   timestamp[ms, UTC]    结算时刻
funding_interval_hours int64                 本次结算的间隔（8 小时为主，历史上的 1/4 小时）
funding_rate           double                当期费率（正 = 多头付给空头）
```

规模很小（约 280 万行、30 MB），但没有它就没有永续因子的地基。

> ⚠️ **归档的 fundingRate CSV 是三列**：`calc_time, funding_interval_hours, last_funding_rate`，
> 不是常被以为的两列。早期解析只读了两列，把 interval（1/4/8）当成了费率 ——
> 回测里年化资金费算出 22 万%。`01_fetch_bv.py` 现在按原始列数判断：
> 三列取 `num(1)`/`num(2)`，两列的老包按 8 小时间隔处理。
>
> 回测把窗口内所有结算的 `funding_rate` **累加**，所以 `funding_interval_hours`
> 只用于审计和「折算成日费率」，不参与计算。

### 2.3 符号主表 —— `basic/dim_instrument.parquet`

**这张表就是「ST / 退市」标签**，对应股票仓库的 `is_st` / `delist_date`。

| 列 | 说明 |
|---|---|
| `symbol` / `market` / `base` / `quote` | 标识 |
| `list_date` | 首根日线日期（上市代理） |
| `delist_date` | 末根日线日期；`status=trading` 时为 `null` |
| `status` | `trading` / `suspended` / `delisted` |
| `bars_1d` / `first_bar_1d` / `last_bar_1d` | 覆盖统计，审计用 |
| `is_leveraged` | `*UP` / `*DOWN` / `*BULL` / `*BEAR`（本轮已剔除，但留标记） |
| `is_stablecoin` | 稳定币计价的交易对 |
| `is_fiat` | **基础币**是法币（`AUDUSDT` / `EURUSDT` / `GBPUSDT` / `NOKUSDT`） |
| `is_tradfi` | TradFi 永续（`contractType == TRADIFI_PERPETUAL`，见 `tradfi_probe.py`） |

`status` 判定（写死在 [`_build/02_partition.py`](_build/02_partition.py)，不人工维护）：

| 状态 | 条件（相对全库最新交易日 `L`） |
|---|---|
| `trading` | `last_bar_1d >= L − 3 天` |
| `suspended` | `L − 30 天 <= last_bar_1d < L − 3 天` |
| `delisted` | `last_bar_1d < L − 30 天` |

#### 下架标签的两条限制（做结论前必读）

1. **这是启发式代理，不是事实。** 币安下架后不再发布 K 线，所以「没有行」≈「不在市」；
   但**中途停牌**（临时暂停交易后又恢复）看起来完全一样，只能靠 30 天阈值区分。
   阈值是工程判断，不是交易所公告。
2. **做点时（point-in-time）股票池时，不要用 `status` 字段。**
   `status` 是**今天**的全局快照，会把「2026 年才下架」的币在 2020 年的截面里也当成已下架。
   正确做法是用 `active_universe()`（该日有没有 bar）：

   ```python
   from lake_io import active_universe
   alive = active_universe("spot", start="2020-01-01")   # date × symbol 的布尔表
   ```

   `status` 只适合快速筛样本（比如「只看今天还在交易的」）。

---

## 三、读取：`lake_io.py`

```python
from lake_io import load, wide, to_daily, symbols, active_universe, liquid_universe

# 长表（谓词下推：只读命中的行组）
df = load("spot", "1h", start="2024-01-01", end="2025-01-01",
          symbols=["BTCUSDT", "ETHUSDT"])
df = load("um", "1d", columns=["symbol", "date", "close", "quote_volume"], years=[2023, 2024])

close = wide(df, "close")          # index=date, columns=symbol
daily = to_daily(df)               # 1h 长表 → 日线，UTC 00:00 为界

dim  = symbols("spot")             # 符号主表，含 status / delist_date
alive = active_universe("spot")    # 点时股票池（date × symbol 布尔）
pool  = liquid_universe(daily, top_n=50, min_quote_volume=1e6)   # 每日可交易池
```

- `start` / `end` 是**左闭右开**。
- 返回的长表**不含 `year` 列**（分区列被自动丢掉）。
- `wide()` / `to_daily()` / `liquid_universe()` 的 key 列写 `symbol`，但会自动兼容旧的 `pair` 列。

### 旧脚本兼容

`策略池/横截面动量/scripts/` 里的脚本按 `pair` 列（`BTC/USDT`）分组。`lake_io.load_panel()`
已经会多补一列 `pair`，那些脚本只要把 import 路径换成 `lake_io` 即可：

```python
from lake_io import load_panel         # 兼容旧脚本
df = load_panel("1d")                  # 含 pair 列的长表
```

原来的 `panel_io.py` 只是个转发 `lake_io` 的空壳，已于 2026-11-06 删除。

---

## 四、更新流程

**数据源只有一个：`data.binance.vision` 归档。** 不用 `freqtrade download-data` ——
交易所 API 只返回**当前**上线的交易对，下架币直接查不到（这正是幸存者偏差的来源），
而且它会写进 `user_data/data/`，污染两个正在跑的 dry-run 实例。

```bash
source /opt/anaconda3/etc/profile.d/conda.sh && conda activate freqtrade
ssh -fN quant-proxy            # 先起 SOCKS（data.binance.vision 的 S3 端点要代理）

# 首次全量（约 1-2 小时，1.6 GB 左右）
python 因子研究/行情数据/_build/01_fetch_bv.py --market spot --tf 1d 1h
python 因子研究/行情数据/_build/01_fetch_bv.py --market um   --tf 1d 1h funding
python 因子研究/行情数据/_build/02_partition.py
python 因子研究/行情数据/_build/03_verify.py
python 因子研究/行情数据/_build/04_audit.py
```

**日常增量**（每天/每周跑一次即可，几分钟）：

```bash
python 因子研究/行情数据/_build/01_fetch_bv.py --market spot --tf 1d 1h --refresh
python 因子研究/行情数据/_build/01_fetch_bv.py --market um   --tf 1d 1h funding --refresh
python 因子研究/行情数据/_build/02_partition.py
python 因子研究/行情数据/_build/03_verify.py --no-net
python 因子研究/行情数据/_build/04_audit.py
```

`--refresh` 只重抓**已缓存符号的最后 2 个月 + 日包尾巴**，不会重下全历史。
新上线的币（缓存里没有）无论加不加 `--refresh` 都会被抓。

### 为什么「缓存」和「分区」分成两个脚本

重分区不用重下；改 schema / 改排序 / 改拆分粒度只跑 `02`，几秒钟的事。
缓存是全量重下的唯一代价，所以它单独存在 `.cache_bv/`。

### 归档的发布时间规律（决定尾巴能补到哪天）

| 归档 | 覆盖到 |
|---|---|
| 月度包 | **上一个月**（当月要等下月初才发布） |
| 日度包 | **昨天**（当天的要等第二天） |

所以数据湖最多落后市场 **1 天** —— 这就是 `01_fetch_bv.py` 抓完月度包之后还要补日包的原因。

### `03_verify.py` 的 7 项校验

1. 文件清单与 `_manifest.json` 完全一致（多一个少一个都失败）
2. 月份连续（首月到尾月之间不许缺文件）
3. schema 一致（所有分区列名/类型完全相同）
4. 行数守恒（分区行数之和 == 缓存行数之和 == manifest）
5. 主键 `(symbol, date)` 唯一
6. 单日 bar 数合理（1h ≤ 24，1d ≤ 1）
7. 随机抽 20 个 `(symbol, 月份)` 回源比对 OHLC（`--no-net` 跳过）

---

## 五、已知偏差与设计取舍

- **幸存者偏差已消除** —— 归档里含已下架币，`dim_instrument.delisted_share` 会在 `_audit.json` 里报出来。
- **现货不能做空**：要做多空组合的实盘可行性验证，得用 `um`（永续）。
  永续有资金费率（`um/funding`），持有成本不能忽略。
- **截面宽度随年份剧增**：2017 年只有个位数符号，2024 年后上千个。
  早期截面（<10 个币）的排序结果不可靠；`liquid_universe(top_n=...)` 在早期会选不满。
- **`data.binance.vision` 自身也会缺数据**：交易所停机日归档里就是没有那根 bar
  （实测 BTCUSDT 2021-01-11 / 2021-01-21）。这不是下载漏了，`04_audit.py` 的缺口分布会体现。
- **1m / 5m 没拉**：体积会从 ~1.4 GB 涨到 100 GB+，本轮用不上。
  `markPriceKlines` / `aggTrades` / 深度快照同样没拉，目录结构留得下，将来需要再加。
- **只有 USDT 计价**：BTC/ETH/USDC 等计价对没拉，`is_fiat` / `is_stablecoin` 两列是为将来扩留的。
- **`行情数据/` 是派生品**：`.cache_bv/` 变了它不会自己变，必须重跑 `02_partition.py`。
- **旧研究文档里的命令已失效**：数据湖取代了 `spot_1h.parquet` / `spot_1d.parquet` /
  `spot_all_1d.parquet` 和 `build_panel.py` / `fetch_spot_all.py`（均已删除）。
  [`策略池/横截面动量/`](../../策略池/横截面动量/) 下几份报告还写着跑 `fetch_spot_all.py`、
  读 `spot_all_1d.parquet` —— 那些是**历史记录**，照抄会报错。
  重跑研究请改用 [`lake_io.load_panel()`](lake_io.py) 或直接 `lake_io.load()`。
  唯一改好的是 [`xsec_all_universe_study.py`](../../策略池/横截面动量/scripts/xsec_all_universe_study.py)，
  它现在从数据湖读数（723 个现货符号，2017-08-17 起）。

---

## 六、当前快照（2026-11-06）

| 数据集 | 行数 | 符号 | 分区 | 跨度（UTC） | 体积 |
|---|---|---|---|---|---|
| `spot/1h` | 19,991,647 | 723 | 112 | 2017-08-17 04:00 → 2026-11-05 23:00 | 860 MB |
| `spot/1d` | 833,773 | 723 | 10 | 2017-08-17 → 2026-11-05 | 44 MB |
| `um/1h` | 16,608,938 | 921 | 83 | 2020-01-01 00:00 → 2026-11-05 23:00 | 718 MB |
| `um/1d` | 692,415 | 921 | 7 | 2020-01-01 → 2026-11-05 | 36 MB |
| `um/funding` | 2,844,831 | 922 | 7 | 2020-01-01 → 2026-10-31 | 2 MB |
| `basic/dim_instrument` | 1,644 | 现货 723 + 合约 921 | 1 | — | 35 KB |

符号主表的状态分布：`trading` 1407、`delisted` **235**（现货 204 + 合约 31）、`suspended` 2。
标记计数：`is_tradfi` 234（全是合约）、`is_stablecoin` 21、`is_fiat` 4、`is_leveraged` 0（已从抓取端剔除）。

> **为什么是 1644 而不是归档里列出的 1645**：`USDSOLDUSDT` 在归档里只有符号前缀、没有任何 K 线，
> 所以不在主表里。主表只收录**至少有一根 bar** 的符号。
>
> **`is_tradfi` 只标合约**：同一个代码可能是两个不同标的。实测 `MDTUSDT` 在现货是老币
> （2026-04 已下架），在合约是 2026-10 上市的美股永续 —— 只在 `market == "um"` 上生效。
>
> `is_fiat` 指的是**基础币是法币**（`AUDUSDT` / `EURUSDT` / `GBPUSDT` / `NOKUSDT`），
> 计价币仍然全是 USDT。

数据源清单以根目录 [K线数据报告.md](../../K线数据报告.md) 为准 —— 但那份报告**只覆盖
`user_data/data/`**，不包含本数据湖。

### 与旧数据的对齐验证（建成时跑过）

- `BTCUSDT` 现货 1h 首根 = `2017-08-17 04:00 UTC`，与旧 feather 一致。
- 与 `user_data/data/binance/BTC_USDT-1h.feather` 的 80,527 根重叠 K 线逐一比对：
  `close` 相对差**中位数与 p99 都是 0**，只有 2 根超过 0.01%
  （`2020-12-21 13:00` 差 0.153%、`2021-04-23 01:00` 差 0.063%），
  属于归档与交易所 API 对个别历史 bar 的修订，不是系统性问题。
- `dataset("spot","1h").get_fragments(filter=year==2024)` 返回 **12** 个，分区剪枝生效。

## 六、常见陷阱速查

| 症状 | 原因 |
|---|---|
| 读出来没有 `month` 列 | `month` 是文件名，hive 只解析 `year`。按 `date` 过滤 |
| 4 位年份以前的数据时间戳不对 | 2025 年前的归档是毫秒，之后是微秒；解析器**逐行**判单位 |
| 出现 `year=56799` 之类的幽灵分区 | 同一个归档 CSV 内毫秒/微秒混用（下架币重发的包会这样）；解析器已逐行归一，`02_partition.py` 也有越界硬闸 |
| `symbol` 和 `pair` 混着用 | 数据湖是 `BTCUSDT`，旧面板是 `BTC/USDT`；`lake_io._key()` 自动兼容 |
| 下载报 S3 超时 | 没起 SOCKS 代理：`ssh -fN quant-proxy` |
| `dim_instrument` 里某币 status 是 delisted 但你认为它在交易 | 停牌超 30 天就会被判 delisted；这是阈值不是事实 |
