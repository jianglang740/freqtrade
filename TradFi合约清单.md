# Binance TradFi 合约清单

> 探测日期 2026-11-04。由 `面板数据/tradfi_probe.py --markdown` 生成，可重跑。来源：`fapi.binance.com/fapi/v1/exchangeInfo` + `data.binance.vision` 归档目录扫描。**只读元数据，未下载任何 K 线。**

---

## 结论速览

1. **交易所公开 API 里有独立的 `contractType = "TRADIFI_PERPETUAL"`，共 235 个**，全部 `status = TRADING`。和网页「8 页 × 30 个」对得上（7 页满 + 第 8 页 25 个）。
2. **分类是字段给的，不用猜**：`underlyingType` 直接区分类别，`underlyingSubType` 统一带 `"TradFi"`。
3. **数据能拿全**：实时 REST 的 K 线、标记价、指数价、资金费全部可用；归档里每个币都有日线+月线 K 线。
4. **freqtrade 认这些合约**：ccxt 解析成普通 `swap`（`NVDA/USDT:USDT`），`freqtrade list-markets` 显示 `Future`，可 `download-data` / `backtesting`。
5. ⚠️ **但全都 2025-12 之后才上线**，最长 11 个月、最短 1 个月（见第四节）。这是硬约束。

---

## 一、能拿到哪些数据（逐项实测）

| 来源 / 端点 | 内容 | 结果 |
|---|---|---|
| `/fapi/v1/exchangeInfo` | 合约元数据：`contractType`、`underlyingType`、`onboardDate`、精度、保证金率、最小下单额 | ✅ |
| `/fapi/v1/klines` | 实时 K 线，周期 `1m 3m 5m 15m 30m 1h 2h 4h 6h 8h 12h 1d` | ✅ |
| `/fapi/v1/markPriceKlines` | 标记价 K 线 | ✅ |
| `/fapi/v1/indexPriceKlines` | 指数（现货参照）价 K 线 | ✅ 但 **Pre-IPO 4 个没有**，返回 `-4144 Invalid pair` |
| `/fapi/v1/premiumIndex` | 标记价 / 指数价 / 预计结算价 / 资金费率 / 下次结算时间 | ✅ |
| `/fapi/v1/fundingRate` | 历史资金费率，带 `rateType` | ✅ |
| `/fapi/v1/ticker/24hr` | 24h 价格、成交额、涨跌 | ✅ |
| 归档 `{daily,monthly}/klines` | 日线 / 月线 K 线，12 个周期齐全 | ✅ 全覆盖 |
| 归档 `daily/{markPrice,indexPrice,premiumIndex}Klines` | 标记 / 指数 / 溢价 K 线 | ✅ |
| 归档 `monthly/fundingRate` | 资金费率 | ✅ |
| 归档 `daily/{trades,aggTrades,metrics}` | 逐笔、聚合逐笔、指标 | ✅ |
| 归档 `daily/bookTicker` | 最优挂单快照 | ❌ 没有 |

归档路径与加密合约完全一致（`um` = U 本位）：
`data/futures/um/{daily|monthly}/klines/<SYMBOL>/<周期>/<SYMBOL>-<周期>-<日期>.zip`

---

## 二、分类与规模

| 类别 | `underlyingType` | 个数 | 24h 合计成交额 | 中位成交额 |
|---|---|---:|---:|---:|
| 美股 | `EQUITY` | 197 | 18,802M | 2M |
| 港股 | `HK_EQUITY` | 15 | 254M | 3M |
| 韩股 | `KR_EQUITY` | 8 | 1,971M | 948K |
| 中国股 | `CN_EQUITY` | 2 | 81M | 41M |
| 大宗商品 | `COMMODITY` | 8 | 6,704M | 518M |
| Pre-IPO | `PREMARKET` | 4 | 113M | 3M |
| 外汇 | `FX` | 1 | 851K | 851K |
| **合计** | | **235** | **27,926M** | |

**流动性极度不均**：单笔中位成交额最高的 **大宗商品** 是 518M，最低的 **外汇** 只有 851K；全部 235 个里有 **82 个日成交额不到 1M**。做横截面研究时这条比什么都重要 —— 成交额门槛会直接决定可选池大小。

---

## 三、完整清单（按类别、类内按 24h 成交额降序）

「对应公司 / 标的说明」一列是人工整理的，来自上市公司公告 / 基金发行方说明 —— **交易所 API 只给代码，不给公司身份**。凡是指数 ETF、杠杆 ETF、封闭式基金，一律标注出来，不要当成个股。⚠️ `最新价` 是合约报价，部分标的带合约乘数，**不能直接和股票行情软件的价格对比**。


### 美股（197 个）

| 交易对 | 标的 | 对应公司 / 标的说明 | 上市 | 归档起 | 最新价 | 24h 成交额 | 24h 涨跌 |
|---|---|---|---|---|---:|---:|---:|
| `SOXLUSDT` | SOXL | Direxion 每日 3× 做多半导体指数 ETF | 2026-05-15 | 2026-05 | 77.72 | 5,550M | +6.96% |
| `SNDKUSDT` | SNDK | Sandisk 闪迪 · NAND 闪存（2025 年从西部数据分拆） | 2026-04-07 | 2026-04 | 1,136.76 | 2,559M | +2.07% |
| `KORUUSDT` | KORU | Direxion 每日 3× 做多 MSCI 韩国 ETF | 2026-06-22 | 2026-06 | 11.45 | 1,248M | +5.34% |
| `SPCXUSDT` | SPCX | SpaceX 太空探索技术 · 猎鹰 / 星舰 + Starlink（2026-06 IPO，史上最大） | 2026-05-21 | 2026-05 | 135.41 | 948M | -1.96% |
| `MUUSDT` | MU | Micron 美光科技 · DRAM / HBM 内存 | 2026-04-07 | 2026-04 | 867.44 | 839M | +1.07% |
| `SOXSUSDT` | SOXS | Direxion 每日 3× 做空半导体指数 ETF | 2026-07-16 | 2026-07 | 50.58 | 782M | -7.06% |
| `SKHYUSDT` | SKHY | SK 海力士美股 ADR | 2026-07-10 | 2026-07 | 152.71 | 712M | +1.40% |
| `INTCUSDT` | INTC | Intel 英特尔 · CPU 与晶圆代工 | 2026-02-02 | 2026-02 | 60.56 | 462M | -2.05% |
| `QQQUSDT` | QQQ | Invesco 纳斯达克 100 指数 ETF | 2026-04-06 | 2026-04 | 693.17 | 448M | +1.16% |
| `SNXXUSDT` | SNXX | Tradr 2× 做多 Sandisk（SNDK）的杠杆 ETF | 2026-07-09 | 2026-07 | 6.54 | 371M | +3.81% |
| `GOOGLUSDT` | GOOGL | Alphabet 谷歌 · 搜索 / 广告 / 云 | 2026-03-26 | 2026-03 | 284.29 | 351M | +1.54% |
| `NVDAUSDT` | NVDA | NVIDIA 英伟达 · GPU 与 AI 加速卡 | 2026-03-26 | 2026-03 | 209.94 | 336M | +2.11% |
| `AMDUSDT` | AMD | AMD 超威半导体 · CPU / GPU | 2026-05-06 | 2026-05 | 514.27 | 311M | +7.24% |
| `TSLAUSDT` | TSLA | Tesla 特斯拉 · 电动车 / 储能 / 自动驾驶 | 2026-01-28 | 2026-01 | 382.92 | 254M | -4.88% |
| `CRCLUSDT` | CRCL | Circle Internet Group · USDC 稳定币发行方 | 2026-02-09 | 2026-02 | 76.23 | 244M | +5.19% |
| `MSTRUSDT` | MSTR | Strategy（原 MicroStrategy）· 比特币储备公司 | 2026-02-09 | 2026-02 | 138.68 | 239M | +5.77% |
| `SPYUSDT` | SPY | SPDR 标普 500 指数 ETF | 2026-04-06 | 2026-04 | 763.34 | 196M | +1.32% |
| `EWYUSDT` | EWY | iShares MSCI 韩国 ETF | 2026-03-16 | 2026-03 | 157.19 | 185M | +1.78% |
| `TQQQUSDT` | TQQQ | ProShares 3× 做多纳指 100 ETF | 2026-06-29 | 2026-06 | 62.38 | 169M | +3.62% |
| `LITEUSDT` | LITE | Lumentum · 光通信器件 | 2026-05-15 | 2026-05 | 691.02 | 169M | -1.23% |
| `AAPLUSDT` | AAPL | Apple 苹果 | 2026-04-06 | 2026-04 | 333.28 | 159M | +2.08% |
| `MRVLUSDT` | MRVL | Marvell 美满电子 · 数据中心定制芯片 | 2026-05-15 | 2026-05 | 174.72 | 159M | -0.52% |
| `DRAMUSDT` | DRAM | Roundhill Memory 存储芯片主题 ETF | 2026-05-18 | 2026-05 | 48.08 | 155M | +1.35% |
| `NBISUSDT` | NBIS | Nebius Group · AI 云算力（原 Yandex 海外业务） | 2026-05-26 | 2026-05 | 154.78 | 127M | +7.02% |
| `MUUUSDT` | MUU | Direxion 每日 2× 做多 Micron ETF | 2026-07-16 | 2026-07 | 22.96 | 123M | +2.04% |
| `INTWUSDT` | INTW | GraniteShares 2× 做多 Intel ETF | 2026-07-09 | 2026-07 | 6.34 | 118M | -4.08% |
| `COINUSDT` | COIN | Coinbase · 美国最大加密资产交易所 | 2026-02-09 | 2026-02 | 147.55 | 99M | +6.66% |
| `MVLLUSDT` | MVLL | GraniteShares 2× 做多 Marvell ETF | 2026-06-29 | 2026-06 | 13.98 | 96M | -1.06% |
| `PLTRUSDT` | PLTR | Palantir · 政府与企业数据分析平台 | 2026-02-09 | 2026-02 | 170.98 | 89M | +3.98% |
| `METAUSDT` | META | Meta · 社交平台与广告 | 2026-03-26 | 2026-03 | 634.68 | 82M | +3.95% |
| `AAOIUSDT` | AAOI | Applied Optoelectronics · 光模块 | 2026-06-08 | 2026-06 | 66.44 | 78M | +2.90% |
| `ORCLUSDT` | ORCL | Oracle 甲骨文 · 数据库与企业云 | 2026-05-15 | 2026-05 | 124.87 | 72M | +5.65% |
| `BEUSDT` | BE | Bloom Energy · 固体氧化物燃料电池 | 2026-05-26 | 2026-05 | 229.11 | 70M | +11.27% |
| `CRWVUSDT` | CRWV | CoreWeave · GPU 云算力租赁 | 2026-05-18 | 2026-05 | 53.47 | 69M | +3.81% |
| `AXTIUSDT` | AXTI | AXT · 化合物半导体衬底（砷化镓 / 磷化铟） | 2026-06-08 | 2026-06 | 53.61 | 56M | +9.81% |
| `SQQQUSDT` | SQQQ | ProShares 3× 做空纳指 100 ETF | 2026-06-29 | 2026-06 | 41.60 | 48M | -3.61% |
| `HOODUSDT` | HOOD | Robinhood · 互联网券商 | 2026-02-02 | 2026-02 | 86.31 | 47M | +2.27% |
| `CBRSUSDT` | CBRS | Cerebras Systems · 晶圆级 AI 芯片（2026-05 IPO） | 2026-05-19 | 2026-05 | 99.62 | 43M | +2.35% |
| `AMZNUSDT` | AMZN | Amazon 亚马逊 · 电商与 AWS | 2026-02-09 | 2026-02 | 259.37 | 41M | +5.04% |
| `IRENUSDT` | IREN | IREN · 比特币矿场转型 AI 算力 | 2026-06-03 | 2026-06 | 24.15 | 39M | +7.05% |
| `MSFTUSDT` | MSFT | Microsoft 微软 · 云与软件 | 2026-04-20 | 2026-04 | 529.02 | 33M | +4.46% |
| `RKLBUSDT` | RKLB | Rocket Lab · 小型运载火箭与航天 | 2026-05-18 | 2026-05 | 55.16 | 30M | +3.67% |
| `BMNRUSDT` | BMNR | Bitmine Immersion · 以太坊（ETH）储备公司 | 2026-06-11 | 2026-06 | 20.91 | 28M | +5.13% |
| `AVGOUSDT` | AVGO | Broadcom 博通 · 定制 ASIC / 网络芯片 | 2026-04-20 | 2026-04 | 336.75 | 27M | +2.69% |
| `WDCUSDT` | WDC | Western Digital 西部数据 · 机械硬盘 | 2026-05-26 | 2026-05 | 350.10 | 27M | +1.13% |
| `ARMUSDT` | ARM | Arm Holdings · 芯片指令集 IP 授权 | 2026-05-26 | 2026-05 | 209.91 | 27M | +3.40% |
| `COHRUSDT` | COHR | Coherent 相干公司 · 光通信与激光器件 | 2026-05-26 | 2026-05 | 214.55 | 25M | +4.30% |
| `TSMUSDT` | TSM | 台积电 ADR · 晶圆代工 | 2026-04-06 | 2026-04 | 411.35 | 24M | +2.13% |
| `MRNAUSDT` | MRNA | Moderna · mRNA 疫苗与疗法 | 2026-08-25 | 2026-08 | 232.69 | 24M | +4.91% |
| `KSTRUSDT` | KSTR | KraneShares 上证科创板 50 指数 ETF | 2026-07-02 | 2026-07 | 18.11 | 24M | +7.86% |
| `DELLUSDT` | DELL | Dell 戴尔 · 服务器与 PC | 2026-06-03 | 2026-06 | 419.87 | 23M | +4.37% |
| `BABAUSDT` | BABA | 阿里巴巴 ADR · 电商与云 | 2026-04-20 | 2026-04 | 103.89 | 21M | +1.50% |
| `SMCIUSDT` | SMCI | Super Micro 超微电脑 · AI 服务器整机 | 2026-06-22 | 2026-06 | 34.20 | 20M | +9.30% |
| `QCOMUSDT` | QCOM | Qualcomm 高通 · 手机 SoC / 基带 | 2026-05-06 | 2026-05 | 165.88 | 19M | +0.83% |
| `SKUUUSDT` | SKUU | GraniteShares 2× 做多 SK 海力士 ETF | 2026-08-25 | 2026-08 | 18.04 | 16M | +2.91% |
| `ALABUSDT` | ALAB | Astera Labs · AI 服务器互联芯片 | 2026-06-22 | 2026-06 | 241.38 | 15M | +1.80% |
| `GLWUSDT` | GLW | Corning 康宁 · 玻璃基板 / 光纤 | 2026-06-11 | 2026-06 | 128.20 | 14M | +2.31% |
| `SKDDUSDT` | SKDD | GraniteShares 2× 做空 SK 海力士 ETF | 2026-08-25 | 2026-08 | 8.60 | 14M | -2.99% |
| `UVXYUSDT` | UVXY | ProShares 1.5× 做多 VIX 短期期货 ETF | 2026-06-11 | 2026-06 | 17.71 | 9M | -9.64% |
| `CRDOUSDT` | CRDO | Credo Technology · 高速互联（AEC 线缆 / SerDes） | 2026-06-08 | 2026-06 | 154.58 | 8M | +7.36% |
| `STXXUSDT` | STXX | Tradr 2× 做多 Seagate（STX）的杠杆 ETF | 2026-06-11 | 2026-06 | 612.27 | 8M | +0.98% |
| `USARUSDT` | USAR | USA Rare Earth · 美国稀土开采与永磁材料 | 2026-05-06 | 2026-05 | 8.98 | 8M | +0.11% |
| `AMATUSDT` | AMAT | Applied Materials 应用材料 · 半导体设备 | 2026-06-08 | 2026-06 | 387.92 | 7M | +0.64% |
| `SHAZUSDT` | SHAZ | SharonAI Holdings · 澳洲 AI 云算力（Neocloud） | 2026-07-21 | 2026-07 | 24.53 | 7M | +14.79% |
| `BNCUSDT` | BNC | CEA Industries · BNB 储备公司 | 2026-07-09 | 2026-07 | 5.01 | 7M | +3.94% |
| `FLNCUSDT` | FLNC | Fluence Energy · 电网侧储能系统 | 2026-05-18 | 2026-05 | 5.93 | 6M | +11.41% |
| `LLYUSDT` | LLY | Eli Lilly 礼来 · 创新药与减重 | 2026-06-01 | 2026-06 | 1,423.59 | 6M | +1.54% |
| `SHOPUSDT` | SHOP | Shopify · 电商 SaaS | 2026-08-17 | 2026-08 | 144.32 | 6M | -8.77% |
| `STRCUSDT` | STRC | Strategy 可变利率 A 系列永续优先股（"Stretch"，非普通股） | 2026-07-02 | 2026-07 | 99.99 | 6M | +0.39% |
| `RAMUSDT` | RAM | Roundhill T-REX 2× 做多 DRAM 主题的杠杆 ETF | 2026-08-25 | 2026-08 | 8.18 | 6M | +2.76% |
| `PENGUSDT` | PENG | Penguin Solutions · 企业级内存与 AI 基础设施 | 2026-07-21 | 2026-07 | 55.23 | 5M | +12.42% |
| `MSTUUSDT` | MSTU | 每日 2× 做多 Strategy（MSTR）的杠杆 ETF | 2026-10-12 | 2026-10 | 30.33 | 5M | +11.67% |
| `DJTUSDT` | DJT | Trump Media & Technology Group · Truth Social | 2026-08-25 | 2026-08 | 5.54 | 5M | -2.66% |
| `ASTSUSDT` | ASTS | AST SpaceMobile · 卫星直连手机 | 2026-06-01 | 2026-06 | 38.42 | 5M | +3.50% |
| `SPCXUSD1` | SPCX | SpaceX 太空探索技术 · 猎鹰 / 星舰 + Starlink（2026-06 IPO，史上最大） | 2026-07-20 | 2026-07 | 135.56 | 5M | -1.95% |
| `TEMUSDT` | TEM | Tempus AI · 医疗 AI 与基因检测 | 2026-08-28 | 2026-08 | 79.57 | 4M | +1.10% |
| `ASMLUSDT` | ASML | ASML 阿斯麦 ADR · EUV 光刻机 | 2026-06-11 | 2026-06 | 1,558.53 | 4M | -0.55% |
| `NFLXUSDT` | NFLX | Netflix 奈飞 · 流媒体 | 2026-06-09 | 2026-06 | 63.80 | 4M | +2.06% |
| `LRCXUSDT` | LRCX | Lam Research 泛林 · 刻蚀设备 | 2026-06-22 | 2026-06 | 235.15 | 4M | +0.17% |
| `ANETUSDT` | ANET | Arista Networks · 数据中心交换机 | 2026-09-18 | 2026-09 | 188.16 | 4M | +12.99% |
| `NOKUSDT` | NOK | Nokia 诺基亚 ADR · 通信设备 | 2026-06-01 | 2026-06 | 8.74 | 3M | +3.07% |
| `NETUSDT` | NET | Cloudflare · CDN / 边缘计算 | 2026-08-17 | 2026-08 | 279.50 | 3M | +5.41% |
| `CRWDUSDT` | CRWD | CrowdStrike · 终端安全 | 2026-06-08 | 2026-06 | 249.02 | 3M | +1.52% |
| `NVOUSDT` | NVO | Novo Nordisk 诺和诺德 ADR · 减重与糖尿病 | 2026-06-01 | 2026-06 | 41.43 | 3M | +2.88% |
| `IBMUSDT` | IBM | IBM · 企业 IT 与大型机 | 2026-06-03 | 2026-06 | 252.32 | 3M | +1.09% |
| `ONDSUSDT` | ONDS | Ondas Holdings · 工业无人机与专网通信 | 2026-06-03 | 2026-06 | 5.91 | 3M | +1.09% |
| `EWJUSDT` | EWJ | iShares MSCI 日本 ETF | 2026-03-19 | 2026-03 | 93.18 | 3M | +0.47% |
| `NOWUSDT` | NOW | ServiceNow · 企业工作流 SaaS | 2026-06-03 | 2026-06 | 110.38 | 3M | +5.18% |
| `APPUSDT` | APP | AppLovin · 移动广告与营销平台 | 2026-07-10 | 2026-07 | 251.72 | 3M | +8.33% |
| `GPROUSDT` | GPRO | GoPro · 运动相机 | 2026-09-03 | 2026-09 | 0.98 | 2M | -3.06% |
| `CYPHUSDT` | CYPH | Cypherpunk Technologies · Zcash 储备公司（原 Leap Therapeutics） | 2026-09-18 | 2026-09 | 2.80 | 2M | +14.89% |
| `TTWOUSDT` | TTWO | Take-Two Interactive · 游戏发行（GTA 系列） | 2026-07-02 | 2026-07 | 197.47 | 2M | +2.05% |
| `HUTUSDT` | HUT | Hut 8 · 比特币矿业与算力 | 2026-09-18 | 2026-09 | 61.72 | 2M | +9.76% |
| `RDDTUSDT` | RDDT | Reddit · 社交论坛 | 2026-08-06 | 2026-08 | 97.83 | 2M | +6.86% |
| `AGPUUSDT` | AGPU | Axe Compute · 算力租赁（原 Predictive Oncology 更名） | 2026-09-18 | 2026-09 | 7.67 | 2M | -2.29% |
| `COSTUSDT` | COST | Costco 好市多 | 2026-06-09 | 2026-06 | 1,020.12 | 2M | -1.67% |
| `GEVUSDT` | GEV | GE Vernova · 燃气轮机与电网设备 | 2026-07-10 | 2026-07 | 906.73 | 2M | +3.32% |
| `KLACUSDT` | KLAC | KLA · 半导体量测 / 检测设备 | 2026-06-22 | 2026-06 | 159.35 | 2M | +0.85% |
| `HIMSUSDT` | HIMS | Hims & Hers Health · 远程医疗与减重 | 2026-06-09 | 2026-06 | 26.25 | 2M | +3.51% |
| `QNTXUSDT` | QNTX | Quantinuum · 离子阱量子计算（2026-06 IPO） | 2026-05-29 | 2026-05 | 33.48 | 2M | +5.88% |
| `TSLLUSDT` | TSLL | Direxion 每日 2× 做多 Tesla ETF | 2026-09-02 | 2026-09 | 10.21 | 2M | -9.65% |
| `SMHUSDT` | SMH | VanEck 半导体行业 ETF | 2026-07-29 | 2026-07 | 527.50 | 2M | +1.72% |
| `TERUSDT` | TER | Teradyne 泰瑞达 · 半导体测试设备 | 2026-07-02 | 2026-07 | 275.36 | 2M | +2.40% |
| `VRTUSDT` | VRT | Vertiv 维谛 · 数据中心散热与供电 | 2026-07-10 | 2026-07 | 238.41 | 1M | +4.56% |
| `EWZUSDT` | EWZ | iShares MSCI 巴西 ETF | 2026-06-10 | 2026-06 | 32.36 | 1M | -0.06% |
| `MCDUSDT` | MCD | 麦当劳 | 2026-10-06 | 2026-10 | 262.22 | 1M | -0.49% |
| `GMEUSDT` | GME | GameStop · 游戏零售（meme 股） | 2026-06-10 | 2026-06 | 24.21 | 1M | +0.00% |
| `RIVNUSDT` | RIVN | Rivian · 电动皮卡与 SUV | 2026-06-10 | 2026-06 | 13.53 | 1M | +2.11% |
| `BXUSDT` | BX | Blackstone 黑石 · 另类资产管理 | 2026-06-08 | 2026-06 | 120.37 | 1M | +0.52% |
| `CRMLUSDT` | CRML | Critical Metals Corp · 格陵兰稀土与锂 | 2026-09-29 | 2026-09 | 6.58 | 1M | -3.05% |
| `ADBEUSDT` | ADBE | Adobe · 创意与营销软件 | 2026-06-11 | 2026-06 | 239.61 | 1M | -3.88% |
| `NVDLUSDT` | NVDL | GraniteShares 2× 做多 NVIDIA ETF | 2026-09-02 | 2026-09 | 30.35 | 1M | +4.33% |
| `IONQUSDT` | IONQ | IonQ · 离子阱量子计算 | 2026-08-28 | 2026-08 | 30.67 | 1M | +6.94% |
| `WMTUSDT` | WMT | Walmart 沃尔玛 | 2026-05-18 | 2026-05 | 125.01 | 1M | -1.89% |
| `TMFUSDT` | TMF | Direxion 每日 3× 做多 20 年期以上美债 ETF | 2026-07-27 | 2026-07 | 29.71 | 1M | -0.47% |
| `BBXUSDT` | BBX | BlackBerry 黑莓 · 车载软件与安全 | 2026-06-01 | 2026-06 | 8.40 | 1M | +4.12% |
| `TXNUSDT` | TXN | Texas Instruments 德州仪器 · 模拟芯片 | 2026-07-02 | 2026-07 | 316.68 | 1M | +2.75% |
| `HPEUSDT` | HPE | Hewlett Packard Enterprise · 企业服务器 / 网络 | 2026-06-08 | 2026-06 | 54.40 | 1M | +4.88% |
| `CIENUSDT` | CIEN | Ciena · 光传输设备 | 2026-06-22 | 2026-06 | 307.75 | 1M | +5.87% |
| `VUSDT` | V | Visa · 支付网络 | 2026-05-18 | 2026-05 | 423.44 | 1M | +0.05% |
| `SNOWUSDT` | SNOW | Snowflake · 云数据仓库 | 2026-07-10 | 2026-07 | 294.94 | 1M | +1.52% |
| `UBERUSDT` | UBER | Uber 优步 · 网约车与配送 | 2026-05-15 | 2026-05 | 68.34 | 1M | +2.15% |
| `AMCUSDT` | AMC | AMC Entertainment · 连锁院线 | 2026-09-18 | 2026-09 | 3.29 | 1M | +2.68% |
| `VSTUSDT` | VST | Vistra · 独立发电（核电 / 天然气） | 2026-08-17 | 2026-08 | 137.72 | 1M | +1.11% |
| `EWTUSDT` | EWT | iShares MSCI 台湾 ETF | 2026-06-01 | 2026-06 | 99.10 | 1M | +1.34% |
| `KOUSDT` | KO | Coca-Cola 可口可乐 | 2026-08-06 | 2026-08 | 95.14 | 1M | -0.25% |
| `BOTUSDT` | BOT | RoboStrategy · 机器人 / 具身智能私募股权的封闭式基金（非运营公司） | 2026-07-09 | 2026-07 | 24.05 | 1M | +9.47% |
| `BWETUSDT` | BWET | Breakwave Tanker Shipping ETF · 油轮运价期货 ETF（非个股） | 2026-09-29 | 2026-09 | 841.28 | 976K | -6.74% |
| `FWDIUSDT` | FWDI | Forward Industries · Solana（SOL）储备公司 | 2026-07-09 | 2026-07 | 6.29 | 968K | +3.61% |
| `USDEXUSDT` | USDEX | StablecoinX · Ethena（ENA）生态储备公司（真实股票代码 USDE，币安改名避免与稳定币 USDe 冲突） | 2026-10-06 | 2026-10 | 8.40 | 954K | +9.38% |
| `XOMUSDT` | XOM | ExxonMobil 埃克森美孚 · 综合油气 | 2026-09-28 | 2026-09 | 175.29 | 953K | -3.30% |
| `IWMUSDT` | IWM | iShares 罗素 2000 小盘股 ETF | 2026-06-08 | 2026-06 | 284.44 | 901K | +1.72% |
| `SOFIUSDT` | SOFI | SoFi · 数字银行 | 2026-07-21 | 2026-07 | 17.15 | 897K | +2.57% |
| `PDDUSDT` | PDD | 拼多多 ADR · 电商 | 2026-08-28 | 2026-08 | 81.68 | 870K | -0.91% |
| `CSCOUSDT` | CSCO | Cisco 思科 · 网络设备 | 2026-05-15 | 2026-05 | 104.11 | 868K | +1.23% |
| `OKLOUSDT` | OKLO | Oklo · 小型模块化核反应堆（SMR） | 2026-09-28 | 2026-09 | 23.98 | 840K | +0.29% |
| `SECZUSDT` | SECZ | Securitize · 现实资产代币化平台（2026 SPAC 上市） | 2026-09-29 | 2026-09 | 8.24 | 826K | +13.19% |
| `BSPUSDT` | BSP | Bending Spoons · 意大利互联网软件集团（2026-07 IPO） | 2026-07-02 | 2026-07 | 44.04 | 826K | -3.86% |
| `FLEXUSDT` | FLEX | Flex 伟创力 · 电子代工（EMS） | 2026-07-02 | 2026-07 | 89.88 | 822K | +4.60% |
| `BRKBUSDT` | BRKB | Berkshire Hathaway 伯克希尔 B 类股 | 2026-05-18 | 2026-05 | 558.12 | 793K | +0.59% |
| `DDOGUSDT` | DDOG | Datadog · 云监控与可观测性 | 2026-09-02 | 2026-09 | 227.32 | 779K | +4.23% |
| `CRMUSDT` | CRM | Salesforce · 企业 CRM SaaS | 2026-06-03 | 2026-06 | 225.62 | 769K | +1.40% |
| `DISUSDT` | DIS | Disney 迪士尼 · 影视与主题公园 | 2026-05-15 | 2026-05 | 115.15 | 767K | +2.03% |
| `ZCSHUSDT` | ZCSH | Grayscale Zcash 产品 · 灰度 Zcash 信托 / ETF | 2026-10-12 | 2026-10 | 34.90 | 762K | +13.24% |
| `TWSTUSDT` | TWST | Twist Bioscience · DNA 合成 | 2026-09-28 | 2026-09 | 144.14 | 737K | +10.73% |
| `TBTUSDT` | TBT | ProShares 每日 2× 做空 20 年期以上美债 ETF | 2026-07-27 | 2026-07 | 38.44 | 733K | +0.66% |
| `APLDUSDT` | APLD | Applied Digital · 数据中心与算力托管 | 2026-09-18 | 2026-09 | 17.58 | 731K | +6.29% |
| `PFEUSDT` | PFE | Pfizer 辉瑞 · 创新药与疫苗 | 2026-10-29 | 2026-10 | 30.28 | 707K | -2.57% |
| `MARAUSDT` | MARA | MARA Holdings · 比特币矿业 | 2026-08-28 | 2026-08 | 7.13 | 701K | +9.36% |
| `CATUSDT` | CAT | Caterpillar 卡特彼勒 · 工程机械 | 2026-07-02 | 2026-07 | 635.76 | 676K | +2.72% |
| `CVNAUSDT` | CVNA | Carvana · 二手车线上零售 | 2026-09-28 | 2026-09 | 72.67 | 639K | +1.40% |
| `GDXUSDT` | GDX | VanEck 金矿股 ETF | 2026-08-17 | 2026-08 | 82.70 | 629K | +6.17% |
| `PANWUSDT` | PANW | Palo Alto Networks · 网络安全 | 2026-07-21 | 2026-07 | 354.90 | 624K | -0.48% |
| `PAYPUSDT` | PAYP | PayPay · 日本软银系移动支付（2026-03 纳斯达克 IPO） | 2026-03-23 | 2026-03 | 14.95 | 620K | +0.07% |
| `UNHUSDT` | UNH | UnitedHealth 联合健康 · 健康保险 | 2026-09-29 | 2026-09 | 398.86 | 620K | +0.38% |
| `NKEUSDT` | NKE | Nike 耐克 | 2026-09-29 | 2026-09 | 38.96 | 612K | -0.03% |
| `XLEUSDT` | XLE | 能源精选行业 SPDR ETF | 2026-06-10 | 2026-06 | 66.33 | 608K | -2.56% |
| `JPMUSDT` | JPM | JPMorgan 摩根大通 · 综合银行 | 2026-05-18 | 2026-05 | 342.46 | 589K | +0.47% |
| `MELIUSDT` | MELI | MercadoLibre · 拉美电商与支付 | 2026-10-29 | 2026-10 | 1,689.14 | 547K | +0.04% |
| `XBIUSDT` | XBI | SPDR 生物科技 ETF（等权） | 2026-07-09 | 2026-07 | 156.37 | 522K | +2.48% |
| `DKNGUSDT` | DKNG | DraftKings · 在线体育博彩 | 2026-06-10 | 2026-06 | 22.00 | 494K | +1.62% |
| `URNMUSDT` | URNM | Sprott 铀矿股 ETF | 2026-06-09 | 2026-06 | 42.51 | 470K | +2.31% |
| `GSUSDT` | GS | Goldman Sachs 高盛 · 投行 | 2026-07-29 | 2026-07 | 921.62 | 470K | +2.67% |
| `RDWUSDT` | RDW | Redwire · 太空基础设施与载荷 | 2026-10-12 | 2026-10 | 8.32 | 470K | +4.26% |
| `ACNUSDT` | ACN | Accenture 埃森哲 · IT 咨询 | 2026-09-29 | 2026-09 | 254.70 | 469K | -1.35% |
| `ZMUSDT` | ZM | Zoom · 视频会议 | 2026-06-10 | 2026-06 | 92.22 | 462K | +0.67% |
| `AKAMUSDT` | AKAM | Akamai · CDN 与边缘加速 | 2026-10-06 | 2026-10 | 87.64 | 454K | +0.54% |
| `SMRUSDT` | SMR | NuScale Power · 小型模块化核反应堆（SMR） | 2026-10-29 | 2026-10 | 5.50 | 451K | +2.21% |
| `MPUSDT` | MP | MP Materials · 美国稀土（Mountain Pass 矿） | 2026-09-29 | 2026-09 | 38.79 | 441K | +0.36% |
| `LYTEUSDT` | LYTE | Roundhill Photonics & Optics · 光子与光学主题 ETF | 2026-08-17 | 2026-08 | 16.67 | 431K | +5.91% |
| `HDUSDT` | HD | Home Depot 家得宝 | 2026-05-15 | 2026-05 | 339.78 | 406K | +0.15% |
| `AMGNUSDT` | AMGN | Amgen 安进 · 生物药 | 2026-10-29 | 2026-10 | 464.65 | 381K | +0.03% |
| `MDBUSDT` | MDB | MongoDB · 文档型数据库 | 2026-09-02 | 2026-09 | 342.70 | 346K | +3.58% |
| `ZSUSDT` | ZS | Zscaler · 零信任网络安全 | 2026-09-02 | 2026-09 | 228.94 | 343K | +2.18% |
| `SNPSUSDT` | SNPS | Synopsys 新思科技 · EDA 工具 | 2026-10-12 | 2026-10 | 465.64 | 331K | -0.03% |
| `WENUSDT` | WEN | Wendy's · 快餐连锁 | 2026-07-09 | 2026-07 | 7.22 | 309K | +0.18% |
| `RUMUSDT` | RUM | Rumble · 视频平台 | 2026-09-28 | 2026-09 | 5.20 | 277K | +1.96% |
| `EBAYUSDT` | EBAY | eBay · 电商平台 | 2026-06-09 | 2026-06 | 118.87 | 253K | -1.53% |
| `CEGUSDT` | CEG | Constellation Energy · 核电运营商 | 2026-10-21 | 2026-10 | 263.43 | 231K | +1.53% |
| `BACUSDT` | BAC | Bank of America 美国银行 · 综合银行 | 2026-10-21 | 2026-10 | 50.51 | 230K | +1.69% |
| `STMUSDT` | STM | STMicroelectronics 意法半导体 ADR · MCU / 功率器件 | 2026-10-26 | 2026-10 | 48.77 | 217K | +3.00% |
| `SONYUSDT` | SONY | Sony 索尼 ADR · 游戏 / 影像 / 音乐 | 2026-06-22 | 2026-06 | 25.26 | 213K | -0.86% |
| `TEAMUSDT` | TEAM | Atlassian · 团队协作 SaaS（Jira） | 2026-09-02 | 2026-09 | 191.91 | 210K | +3.61% |
| `TZAUSDT` | TZA | Direxion 每日 3× 做空小盘股 ETF | 2026-07-16 | 2026-07 | 44.70 | 210K | -4.95% |
| `PYPLUSDT` | PYPL | PayPal · 在线支付 | 2026-07-29 | 2026-07 | 58.44 | 189K | -0.75% |
| `MRKUSDT` | MRK | Merck 默沙东 · 创新药与疫苗 | 2026-08-28 | 2026-08 | 146.21 | 185K | -1.14% |
| `EIDOUSDT` | EIDO | iShares MSCI 印尼 ETF | 2026-10-26 | 2026-10 | 9.77 | 184K | +0.72% |
| `VKTXUSDT` | VKTX | Viking Therapeutics · 减重与代谢病新药 | 2026-10-06 | 2026-10 | 26.42 | 177K | -1.38% |
| `EWZSUSDT` | EWZS | iShares MSCI 巴西小盘股 ETF | 2026-10-29 | 2026-10 | 10.68 | 158K | +0.94% |
| `GTLBUSDT` | GTLB | GitLab · DevOps 平台 | 2026-09-02 | 2026-09 | 52.90 | 140K | +2.72% |
| `BITOUSDT` | BITO | ProShares 比特币策略 ETF（持比特币期货） | 2026-07-27 | 2026-07 | 10.47 | 139K | +2.05% |
| `MDTUSDT` | MDT | Medtronic 美敦力 · 医疗器械 | 2026-10-21 | 2023-06 | 99.11 | 120K | +0.36% |
| `OXYUSDT` | OXY | Occidental Petroleum 西方石油 · 油气 | 2026-10-29 | 2026-10 | 60.00 | 108K | -2.49% |
| `IBKRUSDT` | IBKR | Interactive Brokers 盈透证券 · 互联网券商 | 2026-10-21 | 2026-10 | 78.69 | 106K | +4.46% |
| `PATHUSDT` | PATH | UiPath · RPA 流程自动化 | 2026-09-18 | 2026-09 | 13.17 | 52K | -0.08% |
| `EQTUSDT` | EQT | EQT Corporation · 美国天然气生产商 | 2026-10-26 | 2026-10 | 53.82 | 48K | -2.75% |
| `ABBVUSDT` | ABBV | AbbVie 艾伯维 · 免疫与医美 | 2026-10-26 | 2026-10 | 287.72 | 45K | +0.06% |

### 港股（15 个）

| 交易对 | 标的 | 对应公司 / 标的说明 | 上市 | 归档起 | 最新价 | 24h 成交额 | 24h 涨跌 |
|---|---|---|---|---|---:|---:|---:|
| `ZHONGJIUSDT` | ZHONGJI | 中际旭创（3308.HK）· 800G / 1.6T 光模块龙头 | 2026-08-14 | 2026-08 | 84.47 | 92M | +6.11% |
| `CSOPSKHYNIX2LUSDT` | CSOPSKHYNIX2L | CSOP 发行的 SK 海力士 2× 每日杠杆产品 | 2026-08-11 | 2026-08 | 2.86 | 57M | -0.04% |
| `ZHIPUUSDT` | ZHIPU | 智谱 AI（Zhipu）· 国产大模型 | 2026-07-17 | 2026-07 | 62.35 | 49M | +9.04% |
| `MINIMAXUSDT` | MINIMAX | MiniMax 稀宇科技 · 国产大模型（海螺 AI） | 2026-07-17 | 2026-07 | 21.32 | 30M | +6.76% |
| `GIGADEVUSDT` | GIGADEV | 兆易创新（3986.HK）· NOR Flash / MCU | 2026-08-03 | 2026-08 | 43.80 | 9M | +8.42% |
| `HK1810USDT` | HK1810 | 小米集团（1810.HK）· 手机 / IoT / 电动车 | 2026-07-22 | 2026-07 | 28.56 | 6M | -0.21% |
| `CSOPSAMSUNG2LUSDT` | CSOPSAMSUNG2L | CSOP 发行的 三星电子 2× 每日杠杆产品 | 2026-08-11 | 2026-08 | 6.16 | 3M | -0.69% |
| `HK0700USDT` | HK0700 | 腾讯控股（0700.HK）· 社交 / 游戏 / 广告 | 2026-07-22 | 2026-07 | 401.44 | 3M | +0.96% |
| `TENCENTUSDT` | TENCENT | 腾讯控股（与 HK0700 同标的，币安另开的一套合约） | 2026-07-22 | 2026-07 | 51.22 | 2M | +1.03% |
| `HK0992USDT` | HK0992 | 联想集团（0992.HK）· PC 与服务器 | 2026-09-07 | 2026-09 | 20.90 | 1M | +5.56% |
| `BYDUSDT` | BYD | 比亚迪（1211.HK）· 电动车与电池 | 2026-09-07 | 2026-09 | 9.25 | 525K | +0.22% |
| `POPMARTUSDT` | POPMART | 泡泡玛特（9992.HK）· 潮流玩具 IP | 2026-07-23 | 2026-07 | 17.55 | 497K | -1.13% |
| `KUAISHOUUSDT` | KUAISHOU | 快手（1024.HK）· 短视频与直播电商 | 2026-08-11 | 2026-08 | 3.58 | 427K | +2.11% |
| `MEITUANUSDT` | MEITUAN | 美团（3690.HK）· 本地生活服务 | 2026-08-11 | 2026-08 | 9.59 | 324K | +1.27% |
| `HK0625USDT` | HK0625 | 希音 SHEIN（0625.HK）· 跨境电商（2026-09 上市） | 2026-09-01 | 2026-09 | 38.61 | 289K | +5.81% |

### 韩股（8 个）

| 交易对 | 标的 | 对应公司 / 标的说明 | 上市 | 归档起 | 最新价 | 24h 成交额 | 24h 涨跌 |
|---|---|---|---|---|---:|---:|---:|
| `SKHYNIXUSDT` | SKHYNIX | SK 海力士 · HBM / DRAM 内存 | 2026-06-02 | 2026-06 | 1,061.36 | 1,757M | +0.42% |
| `SAMSUNGUSDT` | SAMSUNG | 三星电子 · 存储 / 手机 / 晶圆代工 | 2026-06-02 | 2026-06 | 165.18 | 210M | -0.11% |
| `HYUNDAIUSDT` | HYUNDAI | 现代汽车 · 整车 | 2026-06-02 | 2026-06 | 212.80 | 2M | +2.15% |
| `SAMSUNGEMUSDT` | SAMSUNGEM | 三星电机（Samsung Electro-Mechanics）· 被动元件与封装基板 | 2026-08-14 | 2026-08 | 844.26 | 1M | +0.15% |
| `HANMIUSDT` | HANMI | 韩美药品（Hanmi Pharm）· 创新药 | 2026-08-14 | 2026-08 | 136.62 | 722K | -0.12% |
| `LGELECTRONICSUSDT` | LGELECTRONICS | LG 电子 · 家电与电子 | 2026-08-14 | 2026-08 | 110.83 | 544K | +2.10% |
| `NAVERUSDT` | NAVER | NAVER · 韩国最大搜索引擎与门户 | 2026-08-14 | 2026-08 | 128.05 | 295K | +1.13% |
| `KODEX200USDT` | KODEX200 | KODEX 200 ETF · 跟踪 KOSPI 200 指数（非个股） | 2026-08-14 | 2026-08 | 68.19 | 264K | +0.65% |

### 中国股（2 个）

| 交易对 | 标的 | 对应公司 / 标的说明 | 上市 | 归档起 | 最新价 | 24h 成交额 | 24h 涨跌 |
|---|---|---|---|---|---:|---:|---:|
| `UNITREEUSDT` | UNITREE | 宇树科技 · 人形机器人与四足机器人 | 2026-08-19 | 2026-08 | 96.67 | 51M | +2.77% |
| `CXMTUSDT` | CXMT | 长鑫存储（ChangXin Memory）· 国产 DRAM | 2026-08-18 | 2026-08 | 6.59 | 30M | +12.27% |

### 大宗商品（8 个）

| 交易对 | 标的 | 对应公司 / 标的说明 | 上市 | 归档起 | 最新价 | 24h 成交额 | 24h 涨跌 |
|---|---|---|---|---|---:|---:|---:|
| `XAUUSDT` | XAU | 黄金 · 现货盎司报价 | 2025-12-11 | 2025-12 | 4,050.22 | 2,964M | +3.49% |
| `CLUSDT` | CL | WTI 原油 · 近月期货 | 2026-04-01 | 2026-04 | 95.37 | 1,521M | -2.53% |
| `XAGUSDT` | XAG | 白银 · 现货盎司报价 | 2026-01-07 | 2026-01 | 52.15 | 1,126M | +6.25% |
| `BZUSDT` | BZ | 布伦特原油 · 近月期货 | 2026-04-01 | 2026-04 | 102.33 | 997M | -3.42% |
| `NATGASUSDT` | NATGAS | 亨利港天然气 · 近月期货 | 2026-04-01 | 2026-04 | 2.90 | 39M | -0.17% |
| `COPPERUSDT` | COPPER | 铜 · 现货报价 | 2026-03-06 | 2026-03 | 6.47 | 20M | +1.36% |
| `XPDUSDT` | XPD | 钯金 · 现货盎司报价 | 2026-01-30 | 2026-01 | 998.95 | 19M | +5.62% |
| `XPTUSDT` | XPT | 铂金 · 现货盎司报价 | 2026-01-30 | 2026-01 | 1,467.39 | 18M | +2.76% |

### Pre-IPO（4 个）

| 交易对 | 标的 | 对应公司 / 标的说明 | 上市 | 归档起 | 最新价 | 24h 成交额 | 24h 涨跌 |
|---|---|---|---|---|---:|---:|---:|
| `ANTHROPICUSDT` | ANTHROPIC | Anthropic · Claude 模型开发商（Pre-IPO 价格合约，非真实股权） | 2026-06-02 | 2026-06 | 1,620.50 | 107M | -2.77% |
| `OPENAIUSDT` | OPENAI | OpenAI · ChatGPT 开发商（Pre-IPO 价格合约，非真实股权） | 2026-05-26 | 2026-05 | 1,389.36 | 5M | +0.48% |
| `MOONSHOTUSDT` | MOONSHOT | 月之暗面 · Kimi 大模型（Pre-IPO 价格合约，非真实股权） | 2026-09-22 | 2026-09 | 63.00 | 363K | -0.33% |
| `OURAUSDT` | OURA | Ōura · 智能戒指（Pre-IPO 价格合约，非真实股权） | 2026-09-23 | 2026-09 | 48.42 | 153K | -0.23% |

### 外汇（1 个）

| 交易对 | 标的 | 对应公司 / 标的说明 | 上市 | 归档起 | 最新价 | 24h 成交额 | 24h 涨跌 |
|---|---|---|---|---|---:|---:|---:|
| `USDBRLUSDT` | USDBRL | 美元 / 巴西雷亚尔汇率 | 2026-09-21 | 2026-09 | 5.48 | 851K | +0.08% |

---

## 四、时间跨度：这是最大的限制

| 类别 | 最早上线 | 最晚上线 | 归档已有月数（中位） |
|---|---|---|---:|
| 美股 | 2026-01-28 | 2026-10-29 | 4 |
| 港股 | 2026-07-17 | 2026-09-07 | 3 |
| 韩股 | 2026-06-02 | 2026-08-14 | 3 |
| 中国股 | 2026-08-18 | 2026-08-19 | 3 |
| 大宗商品 | 2025-12-11 | 2026-04-01 | 9 |
| Pre-IPO | 2026-05-26 | 2026-09-23 | 4 |
| 外汇 | 2026-09-21 | 2026-09-21 | 2 |

上线是渐进铺开的：2025-12 只有 1 个，**2026-06 单月上新 51 个**为峰值，最近 60 天还新上了 46 个 —— 说明这个板块仍在高速扩张。**任何跨品种横截面策略在这个样本上都只有不到一年，而且样本本身还在变。**

---

## 五、合约价与真实标的差多少

用交易所自己给的 `indexPrice`（现货参照价）和 `markPrice` 算：`|标记价 − 指数价| / 指数价`，取当前时刻快照。

| 类别 | 可算个数 | 偏离中位 | 偏离最大 |
|---|---:|---:|---:|
| 美股 | 197 | 0.099% | 1.210% |
| 港股 | 15 | 0.096% | 0.281% |
| 韩股 | 8 | 0.100% | 0.493% |
| 中国股 | 2 | 0.077% | 0.090% |
| 大宗商品 | 8 | 0.053% | 0.149% |
| Pre-IPO | 4 | 0.000% | 0.000% |
| 外汇 | 1 | 0.296% | 0.296% |

**结论：偏离确实很小。** 全部 TradFi 合约的 |标记价 − 指数价| 中位数只有 **0.095%**（不到 9 个基点），但最大的一只（`BWETUSDT`）到了 1.21%。做价差交易或跨市场对冲时要**按单币检查**，不能只看中位数。

⚠️ Pre-IPO 那 4 个显示 0.000% 不是「完美贴合」，而是**它们没有独立指数价** —— `indexPrice` 直接等于 `markPrice`。这 4 个没有现货参照，别拿去做价差。

---

## 六、freqtrade 怎么用

```bash
freqtrade download-data -c user_data/config/config_backtest.json \
  --trading-mode futures -t 1h -t 1d \
  --pairs NVDA/USDT:USDT XAU/USDT:USDT \
  --timerange 20251201-
```

1. **符号要用统一格式** `NVDA/USDT:USDT`，不是 `NVDAUSDT`。
2. **归档的月度文件晚一个月才发布**，最近行情靠 REST 补，和 `spot_all_1d` 的情形一样。
3. **样本期只有几个月**，别指望用它做长期回测；要做横截面研究得先想清楚样本外怎么验证。
