# Freqtrade 全栈主力框架手册 2026.x

## 第四卷：数据库深入拆解——ORM、事务、状态持久化、迁移与数据库扩展

> 本卷目标：把 Freqtrade 的数据库从“配置里的一个 `db_url`”彻底拆成一个可以被理解、诊断、迁移、优化、扩展的系统。
>
> 阅读方式建议：先把“数据库到底保存什么”搞清楚，再看 SQLAlchemy/Session，再看 `Trade/Order/PairLock/CustomData/KeyValueStore/WalletHistory`，最后再研究 PostgreSQL、多 Bot、迁移和未来自定义数据库后端。

---

## 0. 先建立一个最重要的概念：Freqtrade 的“数据库”不是“市场数据仓库”

这是理解整个 Persistence 层最容易被搞混、也最重要的一件事。

Freqtrade 至少存在两类完全不同的数据：

```text
市场数据 / Market Data
    ├── OHLCV
    ├── Tick / Trade Data（视数据源而定）
    ├── Funding Rate
    ├── Mark Price / Index Price
    └── 其他历史行情

交易运行状态 / Trading State
    ├── Trade
    ├── Order
    ├── PairLock
    ├── CustomData
    ├── KeyValueStore
    └── WalletHistory
```

前者主要服务于 **策略计算、回测和行情分析**；后者主要服务于 **真实交易运行时的状态持久化和恢复**。

因此，不要一上来就认为：

> “既然我要做一个 PostgreSQL 数据库，那应该把所有 OHLCV 也塞进去。”

这不是 Freqtrade 默认的设计。

Freqtrade 的历史行情数据有独立的数据存储/转换机制，官方文档中的 `download-data`、`convert-data`、`convert-trade-data` 都是围绕 market data 文件体系展开的；而 `db_url` 针对的是 bot 的交易状态数据库。官方默认交易数据库通常是 `tradesv3.sqlite` 或 dry-run 对应数据库。([官方启动文档](https://docs.freqtrade.io/en/latest/bot-usage/))

这一点对后续架构设计非常关键：

```text
                         ┌───────────────────────┐
                         │      Freqtrade        │
                         └──────────┬────────────┘
                                    │
               ┌────────────────────┴────────────────────┐
               │                                         │
               ▼                                         ▼
     ┌─────────────────────┐                   ┌─────────────────────┐
     │ Market Data Layer   │                   │ Persistence Layer   │
     │                     │                   │                     │
     │ OHLCV / trades      │                   │ Trade               │
     │ funding             │                   │ Order               │
     │ mark / index        │                   │ PairLock            │
     │                     │                   │ CustomData          │
     │ 文件/数据格式        │                   │ KeyValueStore       │
     │ feather/parquet/... │                   │ WalletHistory       │
     └─────────────────────┘                   └──────────┬──────────┘
                                                          │
                                                          ▼
                                                  SQLAlchemy / DB
```

### 0.1 为什么这个边界特别重要？

因为如果以后你把 Freqtrade 做成一个真正的“量化平台”，通常应该采用：

```text
              Market Data Warehouse
                     │
            ┌────────┴─────────┐
            │                  │
       Research / ML       Backtesting
            │                  │
            └────────┬─────────┘
                     │
                     ▼
                 Freqtrade
                     │
                     ▼
           Trading State Database
```

而不是让一个 SQLite 文件同时承担所有工作。

对于你后续准备长期使用 Freqtrade 的主力框架路线，我建议从现在开始就把这两个领域严格分开。

---

# 1. Freqtrade Persistence 层到底在哪里？

当前源码的核心目录位于：

```text
freqtrade/
└── persistence/
    ├── base.py
    ├── models.py
    ├── trade_model.py
    ├── pairlock.py
    ├── pairlock_middleware.py
    ├── custom_data.py
    ├── key_value_store.py
    ├── wallet_history.py
    └── migrations.py
```

这是一个非常值得认真阅读的目录。

可以把它理解为：

```text
                 Persistence Package
                         │
        ┌────────────────┼────────────────┐
        │                │                │
        ▼                ▼                ▼
   ORM 基础层        数据模型层         迁移层
        │                │                │
        │        ┌───────┼────────┐       │
        │        │       │        │       │
        ▼        ▼       ▼        ▼       ▼
   SQLAlchemy   Trade  Order  PairLock  migrations
                         │
                         ├── CustomData
                         ├── KeyValueStore
                         └── WalletHistory
```

当前源码中的 `ModelBase` 基于 SQLAlchemy 的 `DeclarativeBase`，而 Session 类型使用 `scoped_session[Session]`；`models.py` 则负责根据 `db_url` 创建 SQLAlchemy Engine、创建 scoped session、注册模型并触发数据库迁移。([base.py](https://raw.githubusercontent.com/freqtrade/freqtrade/develop/freqtrade/persistence/base.py)、[models.py](https://raw.githubusercontent.com/freqtrade/freqtrade/develop/freqtrade/persistence/models.py))

---

# 2. 第一个核心：`db_url` 到底是什么？

Freqtrade 本身并没有把数据库类型硬编码成“只能 SQLite”。

它把数据库连接交给 SQLAlchemy 的 Database URL。

最常见的是：

```text
sqlite:///tradesv3.sqlite
```

PostgreSQL：

```text
postgresql+psycopg://username:password@hostname:5432/database_name
```

MariaDB：

```text
mysql+pymysql://username:password@hostname:3306/database_name
```

实际使用时，驱动必须根据你的环境安装。Freqtrade 官方文档明确说明：Freqtrade 使用 SQLAlchemy，因此可以支持多个数据库系统；官方测试并明确列出的系统包括 SQLite、PostgreSQL 和 MariaDB。Freqtrade 本身不会自动安装这些额外数据库驱动。([Advanced Post-installation Tasks](https://docs.freqtrade.io/en/latest/advanced-setup/))

这意味着架构上是：

```text
Freqtrade
   │
   │ db_url
   ▼
SQLAlchemy
   │
   ├── SQLite Dialect
   ├── PostgreSQL Dialect
   ├── MariaDB/MySQL Dialect
   └── 其他 SQLAlchemy dialect
          ↑
      理论上可用
      但未必被 Freqtrade 官方测试
```

这就是后面“怎样扩展数据库”的关键。

---

# 3. 从 `db_url` 到数据库连接：初始化过程

当前 `models.py` 的初始化逻辑可以抽象成下面这条链：

```text
config / CLI
     │
     ▼
config["db_url"]
     │
     ▼
init_db(db_url)
     │
     ▼
create_engine(db_url)
     │
     ├── dialect
     ├── driver
     ├── connection pool
     └── engine configuration
     │
     ▼
sessionmaker(bind=engine)
     │
     ▼
scoped_session(...)
     │
     ├── Trade.session
     ├── Order.session
     ├── PairLock.session
     ├── KeyValueStore.session
     └── WalletHistory.session
     │
     ▼
ModelBase.metadata.create_all(engine)
     │
     ▼
check_migrate(...)
     │
     ▼
数据库可用
```

当前源码的实现有几个非常重要的细节。

### 3.1 SQLite 有专门处理

当前 `models.py`：

```python
if db_url == "sqlite://":
    kwargs.update({
        "poolclass": StaticPool,
    })
```

同时对于 SQLite 会设置：

```python
connect_args={"check_same_thread": False}
```

也就是说 Freqtrade 并不是简单地：

```python
create_engine(db_url)
```

而是会根据数据库类型做必要的 Engine 配置。([models.py](https://raw.githubusercontent.com/freqtrade/freqtrade/develop/freqtrade/persistence/models.py))

### 3.2 Session 不是全局裸变量

当前代码使用：

```python
Trade.session = scoped_session(
    sessionmaker(bind=engine, autoflush=False),
    scopefunc=get_request_or_thread_id,
)
```

并且让：

```python
Order.session = Trade.session
PairLock.session = Trade.session
_KeyValueStoreModel.session = Trade.session
WalletHistory.session = Trade.session
```

这说明 Freqtrade Persistence 的关键不是“每次自己手写一个数据库连接”，而是：

```text
Engine
  ↓
Session Factory
  ↓
Scoped Session
  ↓
ORM Model
```

这是典型的 SQLAlchemy ORM 应用结构。

---

# 4. `scoped_session` 到底解决什么问题？

这个概念值得你单独理解。

如果没有 scoped session，假设运行过程中多个执行上下文同时访问数据库：

```text
Worker Thread A ─────┐
                     ├── DB
API Request B ───────┤
                     │
WebSocket C ─────────┘
```

如果所有地方共享同一个裸 Session，就容易出现：

```text
Session 被多个上下文意外共享
      ↓
状态污染
      ↓
未提交事务相互影响
      ↓
并发问题
      ↓
rollback 后其他逻辑也被影响
```

Freqtrade 当前实现通过 `scopefunc=get_request_or_thread_id` 把 Session 的生命周期与当前请求上下文/线程上下文关联起来。源码里明确说明：在 FastAPI 请求环境下考虑 request id；非请求环境则使用 thread id。([models.py](https://raw.githubusercontent.com/freqtrade/freqtrade/develop/freqtrade/persistence/models.py))

可以把它理解成：

```text
                Session Registry
                      │
      ┌───────────────┼────────────────┐
      │               │                │
      ▼               ▼                ▼
 Worker context   API request A   API request B
      │               │                │
   Session #1      Session #2       Session #3
```

注意：

**Scoped session 并不等于数据库连接数永远只有一个。**

底下依然由 SQLAlchemy Engine 的连接池负责连接管理。

---

# 5. Engine、Connection、Session 三者不要混淆

这是数据库架构里最容易混乱的三个概念。

## 5.1 Engine

Engine 是：

> SQLAlchemy 与底层数据库交互的入口和连接池管理器。

可以理解为“数据库基础设施层”。

```text
Engine
├── Dialect
├── DBAPI driver
├── Connection Pool
└── SQL execution
```

## 5.2 Connection

Connection 是具体数据库连接上下文。

```text
Engine
   │
   ├── Connection 1
   ├── Connection 2
   ├── Connection 3
   └── ...
```

## 5.3 Session

Session 是 ORM 的工作单元。

它负责：

```text
对象读取
对象修改
新增对象
删除对象
flush
commit
rollback
```

可以理解成：

```text
              Application
                   │
                   ▼
                Session
                   │
             ORM Identity Map
                   │
                   ▼
               Connection
                   │
                   ▼
                 Engine
                   │
                   ▼
               Database
```

因此当你在 Strategy 中看到：

```python
Trade.get_trades_proxy(...)
```

最终不是 Strategy 自己去连接 PostgreSQL，而是：

```text
Strategy
  ↓
Trade classmethod
  ↓
Trade.session
  ↓
SQLAlchemy Session
  ↓
Engine
  ↓
DB
```

---

# 6. Freqtrade 当前到底有哪些重要数据库模型？

当前 Persistence 层至少需要重点认识：

```text
trades
orders
pairlocks
trade_custom_data
KeyValueStore
wallet_history
```

不同版本的具体字段可能变化，因此不要死背列名，而应该理解模型语义。

总体 ER 图：

```text
                         ┌─────────────────────┐
                         │       trades        │
                         │─────────────────────│
                         │ id                  │
                         │ pair                │
                         │ exchange            │
                         │ is_open             │
                         │ open_rate           │
                         │ close_rate          │
                         │ amount              │
                         │ stake_amount        │
                         │ stop_loss           │
                         │ leverage            │
                         │ is_short            │
                         │ strategy            │
                         │ enter_tag           │
                         │ exit_reason         │
                         └─────────┬───────────┘
                                   │ 1
                    ┌──────────────┼──────────────┐
                    │ *           │ *             │ *
                    ▼             ▼               ▼
             ┌────────────┐ ┌─────────────┐ ┌──────────────┐
             │   orders   │ │custom_data  │ │ other state  │
             └────────────┘ └─────────────┘ └──────────────┘

    ┌────────────────┐     ┌────────────────────┐
    │   pairlocks    │     │   KeyValueStore    │
    │────────────────│     │────────────────────│
    │ pair           │     │ key                │
    │ side           │     │ value_type         │
    │ lock_time      │     │ value fields       │
    │ lock_end_time  │     └────────────────────┘
    │ active         │
    └────────────────┘

              ┌─────────────────────┐
              │   wallet_history    │
              │─────────────────────│
              │ wallet snapshots    │
              │ time series state   │
              └─────────────────────┘
```

---

# 7. `Trade`：Freqtrade 数据库中最重要的对象

官方文档把 Trade 定义为一个核心概念：Freqtrade 建立的一个 position 会以 Trade 对象存在，并持久化到数据库；策略的许多 callback 都会接收到 Trade 对象。([Trade Object](https://github.com/freqtrade/freqtrade/blob/develop/docs/trade-object.md))

当前源码中 `Trade` 继承自 `ModelBase` 和 `LocalTrade`：

```python
class Trade(ModelBase, LocalTrade):
    __tablename__ = "trades"
```

这一设计非常值得理解：

```text
               LocalTrade
                   ▲
                   │
                Trade
                   │
            SQLAlchemy ORM
```

`LocalTrade` 是回测环境使用的内存 Trade 表示；`Trade` 则是 live/dry-run 使用的数据库模型。

这也是为什么你不能简单地说：

> “回测和实盘完全复用同一个 Trade 数据库。”

不是这样。

源码当前明确维护了 `LocalTrade` 的内存状态，而 `Trade.use_db = True`；回测则使用 `LocalTrade` 的内存容器。Backtesting 模块也显式导入 `LocalTrade`、`Trade`、`disable_database_use`、`enable_database_use`。([trade_model.py](https://raw.githubusercontent.com/freqtrade/freqtrade/develop/freqtrade/persistence/trade_model.py)、[backtesting.py](https://github.com/freqtrade/freqtrade/blob/develop/freqtrade/optimize/backtesting.py))

---

# 8. Trade 中最值得理解的字段类别

不要把 Trade 看成一堆散乱字段。

最好按照语义分类。

## 8.1 身份信息

```text
id
exchange
pair
strategy
enter_tag
```

回答的是：

> “这是哪一笔交易？来自哪个策略？”

## 8.2 仓位信息

```text
amount
stake_amount
max_stake_amount
base_currency
stake_currency
```

回答的是：

> “当前持有多少？”

## 8.3 开仓状态

```text
open_rate
open_rate_requested
open_date
fee_open
fee_open_cost
fee_open_currency
open_trade_value
```

## 8.4 平仓状态

```text
close_rate
close_rate_requested
close_date
close_profit
close_profit_abs
exit_reason
exit_order_status
```

## 8.5 风控状态

```text
stop_loss
stop_loss_pct
initial_stop_loss
initial_stop_loss_pct
is_stop_loss_trailing
max_rate
min_rate
```

## 8.6 Futures / Leverage 状态

```text
leverage
is_short
liquidation_price
interest_rate
funding_fees
funding_fee_running
```

## 8.7 精度/合约状态

```text
amount_precision
price_precision
precision_mode
precision_mode_price
contract_size
```

这说明一个重要事实：

**Trade 并不仅仅是“买入价格 + 卖出价格”。**

它实际上是一个带有：

```text
执行状态
+ 仓位状态
+ 风控状态
+ 盈亏状态
+ Futures 状态
+ 精度状态
+ 策略身份
```

的交易状态机对象。

---

# 9. 为什么一个 Trade 对应多个 Order？

这是数据库设计最值得理解的一部分。

当前源码中：

```text
Trade 1 ────────< Order N
```

并且 `orders.ft_trade_id` 是 `ForeignKey("trades.id")`，同时 Order 在 `(ft_pair, order_id)` 上有唯一约束。([trade_model.py](https://raw.githubusercontent.com/freqtrade/freqtrade/develop/freqtrade/persistence/trade_model.py))

例如一笔交易：

```text
Trade #1001
│
├── Order #2001  Entry Limit
│
├── Order #2002  Entry Fill / Replace / Retry
│
├── Order #2003  DCA Add Position
│
├── Order #2004  DCA Add Position
│
├── Order #2005  Exit Limit
│
└── Order #2006  Stoploss
```

所以：

```text
Trade ≠ Order
```

Trade 是：

> 一段交易生命周期/position。

Order 是：

> 为了改变这段 position 状态而提交到交易所的订单记录。

这个抽象非常适合做：

```text
多次加仓
部分成交
部分退出
止损订单
挂单重试
订单重新同步
```

---

# 10. `Order` 表：为什么它长得很像 CCXT？

当前 Order 模型的设计目标非常明确：

> 记录交易所下达的订单，并尽量镜像 CCXT Order 结构。

源码注释直接说明：Order mirrors CCXT Order structure。([trade_model.py](https://raw.githubusercontent.com/freqtrade/freqtrade/develop/freqtrade/persistence/trade_model.py))

因此你会看到：

```text
order_id
status
symbol
order_type
side
price
average
amount
filled
remaining
cost
stop_price
order_date
order_filled_date
order_update_date
```

同时 Freqtrade 自己还有：

```text
ft_trade_id
ft_order_side
ft_pair
ft_is_open
ft_amount
ft_price
ft_cancel_reason
ft_order_tag
ft_fee_base
```

可以理解为：

```text
            CCXT Order
                │
                ▼
       Exchange / Freqtrade
                │
                ▼
             Order ORM
          ┌─────┴─────┐
          │           │
          ▼           ▼
   exchange fields  FT fields
```

所以数据库里的 Order 并不是原始交易所响应的“盲目复制”。

Freqtrade 会做一次领域模型转换。

---

# 11. 为什么必须有 `ft_` 字段？

这是很多第一次读源码的人容易忽略的地方。

例如：

```text
price
amount
side
status
```

这些更多属于交易所/CCXT 世界。

而：

```text
ft_pair
ft_trade_id
ft_order_side
ft_is_open
ft_amount
ft_price
```

则属于：

> Freqtrade 自己的交易领域模型。

因此这里其实有一个非常重要的边界：

```text
Exchange semantics
       │
       ▼
      CCXT
       │
       ▼
Freqtrade normalization
       │
       ▼
Persistence model
```

未来你接别的交易所、别的执行系统、甚至自己写 Execution Adapter 时，这个分层思想非常重要。

---

# 12. PairLock：数据库不仅存“成交”，还存“交易规则状态”

`PairLock` 是另一个经常被忽视的模型。

当前表名：

```text
pairlocks
```

关键字段包括：

```text
id
pair
side
reason
lock_time
lock_end_time
active
```

当前源码把 `pair`、`lock_end_time`、`active` 都设置了相应索引。([pairlock.py](https://raw.githubusercontent.com/freqtrade/freqtrade/develop/freqtrade/persistence/pairlock.py))

它表达的是：

```text
某交易对
    ↓
在某个时间段
    ↓
暂时禁止开仓/某方向交易
    ↓
原因 reason
```

这意味着数据库在这里已经不是“交易历史”，而是在存：

> **Trading Policy State**。

所以你以后设计自己的全局风控系统，也应该思考：

```text
Trade State
     +
Risk State
     +
Market State
     +
Policy State
```

哪些应该持久化？

---

# 13. PairLock 为什么需要 `side`？

当前实现支持：

```text
long
short
*
```

所以可以实现：

```text
BTC/USDT
只禁止 Long
```

或者：

```text
BTC/USDT
只禁止 Short
```

或者：

```text
BTC/USDT
Long + Short 都禁止
```

这对于 Futures 策略尤其重要。

---

# 14. CustomData：这是一个非常值得你未来利用的扩展点

Freqtrade 当前提供 trade 级别的持久化自定义数据：

```python
trade.set_custom_data(key="my_key", value=my_value)
```

再读取：

```python
trade.get_custom_data(key="my_key")
```

官方文档明确说明这些数据会和 Trade 关联，并且会序列化成 JSON 字符串存入数据库。([Advanced Strategies](https://docs.freqtrade.io/en/latest/strategy-advanced/))

这是个非常有价值的设计。

例如你可以存：

```python
trade.set_custom_data("entry_regime", "breakout")
trade.set_custom_data("entry_volatility", 0.032)
trade.set_custom_data("entry_score", 0.87)
trade.set_custom_data("dca_count", 2)
trade.set_custom_data("risk_bucket", "high")
```

那么你的 Strategy 在重启后仍然可以知道：

```text
这笔交易当初是什么状态进入的？
为什么进入？
进场时的模型评分是多少？
已经 DCA 几次？
属于哪个风险桶？
```

这对于实盘系统非常重要。

---

# 15. CustomData 的正确用途与错误用途

### 适合：

```text
交易级状态
少量模型结果
状态机变量
策略内部标志位
```

### 不适合：

```text
整个 DataFrame
几万行指标
整份 OHLCV
高频 Tick
巨型 JSON
大量历史日志
```

因为 CustomData 本质上仍然是数据库持久化。

如果你把：

```text
1000 行指标
×
10000 笔交易
```

塞进 CustomData，最终你会得到非常痛苦的：

```text
DB size 增长
JSON serialization
读取慢
索引困难
查询复杂
```

---

# 16. CustomData 在数据库层是怎么工作的？

当前源码中 `_CustomData` 是真正的持久化模型，而 `CustomDataWrapper` 是中间层。

源码对 Wrapper 的描述非常值得注意：

> 将数据库层抽象出去，使数据库可以成为可选项，以支持回测和 hyperopt。

([custom_data.py](https://raw.githubusercontent.com/freqtrade/freqtrade/develop/freqtrade/persistence/custom_data.py))

这其实暴露了 Freqtrade 一个非常重要的架构思想：

```text
Strategy
   │
   ▼
CustomDataWrapper
   │
   ├───────────────┐
   ▼               ▼
Live/Dry         Backtest
   │               │
   ▼               ▼
Database        In-memory
```

这也是未来你自己扩展 Persistence 抽象时非常值得借鉴的设计。

---

# 17. KeyValueStore：Bot 级别的持久化状态

当前还有：

```text
KeyValueStore
```

数据库表：

```text
KeyValueStore
```

当前支持的 value type：

```text
str
datetime
float
int
```

源码中也有明确的 key 类型设计，例如：

```text
bot_start_time
startup_time
binance_migration
wallet_history_migration
wallet_history_migration_date
```

它与 Trade CustomData 的区别非常重要：

```text
CustomData
    → Trade 级别

KeyValueStore
    → Bot 级别
```

举例：

```text
Trade 1234
    entry_score = 0.87
    regime = breakout
```

属于 CustomData。

而：

```text
bot_start_time = 2026-08-20T00:00:00Z
```

属于 KeyValueStore。

([key_value_store.py](https://raw.githubusercontent.com/freqtrade/freqtrade/develop/freqtrade/persistence/key_value_store.py))

---

# 18. WalletHistory：为什么钱包数据也需要落库？

实盘系统通常至少需要理解三类状态：

```text
交易状态
订单状态
账户状态
```

Trade 表负责第一类。

Order 表负责第二类。

WalletHistory 则服务于第三类中的历史快照。

如果以后你要做：

```text
账户权益曲线
风险暴露曲线
资金曲线
资金使用率
```

WalletHistory 一类的数据就很有价值。

但注意：

> 它并不等价于你自己的完整 Portfolio Ledger。

如果未来你要建设真正的资金账本，建议在 Freqtrade 之外建立更严谨的 ledger 层。

---

# 19. 数据库初始化：`create_all()` 与迁移是两个不同概念

Freqtrade 当前初始化过程里有：

```python
ModelBase.metadata.create_all(engine)
check_migrate(...)
```

这两个动作不要混淆。

## 19.1 create_all

它的思路更接近：

> 模型需要的表不存在时，把它创建出来。

## 19.2 migration

迁移解决的是：

> 已经存在一个旧版本数据库，如何安全地变成新版本数据库？

例如旧数据库可能没有：

```text
record_version
funding_fee
side
某个新字段
```

迁移层会检测旧结构并做兼容迁移。

当前 `persistence/migrations.py` 中存在大量针对旧结构的检查、备份表、字段迁移、序列重建以及修复逻辑。它还会针对 PostgreSQL 的 sequence 做专门处理。([migrations.py](https://raw.githubusercontent.com/freqtrade/freqtrade/develop/freqtrade/persistence/migrations.py))

---

# 20. Freqtrade 的数据库迁移思路非常值得学习

当前迁移并不是简单做：

```sql
ALTER TABLE trades ADD COLUMN xxx;
```

在一些复杂版本升级里，它会：

```text
旧表
  ↓
backup table
  ↓
检查列
  ↓
必要时删除旧结构
  ↓
重新按 ModelBase schema 建表
  ↓
从 backup table copy 数据
  ↓
恢复 sequence
  ↓
修正字段
```

这是一个非常实用的 schema evolution 思维。

尤其是 SQLite，因为 SQLite 对某些 ALTER TABLE 操作的能力有限，所以“重建表”在兼容性设计里并不罕见。

---

# 21. 为什么数据库升级绝对不能随意手工改？

假设你手工执行：

```sql
ALTER TABLE trades ADD COLUMN xxx TEXT;
```

但 Freqtrade 新版本模型没有这个字段。

结果可能是：

```text
数据库有字段
       ↓
ORM 不认识
       ↓
部分查询不受影响
       ↓
升级/迁移时出现未知行为
```

反过来，如果你手工删字段：

```text
Model 仍需要
       ↓
ORM 查询失败
```

因此原则是：

> **不要把 Freqtrade 官方 ORM Schema 当成你可以任意改造的业务数据库。**

你真正想扩展业务数据时，更推荐下面三种方式：

```text
A. Trade.set_custom_data()
B. 独立扩展数据库 / Schema
C. 你自己维护一层外部业务数据库
```

---

# 22. SQLite 为什么是默认数据库？

因为 Freqtrade 默认的交易数据库负载非常适合 SQLite 的简单部署模型：

```text
单 Bot
单进程主控
交易数据量通常不大
部署简单
无需数据库服务器
```

所以：

```text
SQLite
= 最低运维成本
```

而不是：

```text
SQLite
= 最适合所有生产环境
```

这是两个不同命题。

---

# 23. SQLite 的优势

```text
✓ 零数据库服务
✓ 一个文件
✓ 备份极其简单
✓ 本地调试方便
✓ 开发成本低
✓ 回测/本地实验很舒服
```

例如：

```bash
cp tradesv3.sqlite backups/tradesv3_$(date +%F).sqlite
```

非常简单。

---

# 24. SQLite 的缺点

最主要的问题不是“性能慢”，而是：

> **并发写入模型有限。**

当出现：

```text
Bot
API
FreqUI
多个 RPC 请求
多个辅助进程
```

同时访问并且涉及写操作时，你可能遇到：

```text
database is locked
```

历史 GitHub issue 中确实存在这一类 SQLite lock 问题。([GitHub issue #6353](https://github.com/freqtrade/freqtrade/issues/6353))

当前迁移代码也会对 SQLite 设置 WAL 模式，这有助于改善读写并发行为，但它不会把 SQLite 变成 PostgreSQL。([migrations.py](https://raw.githubusercontent.com/freqtrade/freqtrade/develop/freqtrade/persistence/migrations.py))

---

# 25. WAL 是什么？

SQLite 默认的传统 journal 思路更偏向：

```text
写入
 ↓
阻塞部分读写
```

WAL（Write-Ahead Logging）则允许更好的读写并发：

```text
Writer
  │
  ▼
WAL
  │
  ├──────────► Readers
  │
  ▼
Database
```

Freqtrade 当前 migration code 对 SQLite 数据库会尝试启用 WAL。([migrations.py](https://raw.githubusercontent.com/freqtrade/freqtrade/develop/freqtrade/persistence/migrations.py))

但要注意：

> WAL 解决的是 SQLite 内部并发模型的一部分问题，并不意味着你应该拿一个 SQLite 文件让几十个 bot 跨机器共享。

这是完全不同的问题。

---

# 26. 多 Bot 时 SQLite 应该怎么设计？

官方多实例文档明确建议：不同实例应该使用不同数据库；不同 bot 还应区分各自的 API 端口等运行资源。([Advanced Post-installation Tasks](https://docs.freqtrade.io/en/latest/advanced-setup/))

推荐：

```text
Bot A
  └── trades_a.sqlite

Bot B
  └── trades_b.sqlite

Bot C
  └── trades_c.sqlite
```

而不是：

```text
Bot A ──┐
Bot B ──┼──> shared.sqlite
Bot C ──┘
```

尤其不要让多个独立 Freqtrade 进程把同一个 SQLite 文件当成“共享数据库”。

---

# 27. PostgreSQL：什么时候应该迁移？

如果你以后走下面这种结构：

```text
VPS / Cloud
    │
    ├── Bot 1
    ├── Bot 2
    ├── Bot 3
    ├── Bot 4
    ├── FreqUI
    ├── monitoring
    └── analytics
```

我会建议尽早考虑 PostgreSQL。

尤其当你出现：

```text
多 Bot
多 API client
远程数据库
集中备份
数据库监控
数据分析
```

的时候。

官方已明确记录 PostgreSQL 为经过测试的数据库系统，并给出了 PostgreSQL `db_url` 示例。([Advanced Post-installation Tasks](https://docs.freqtrade.io/en/latest/advanced-setup/))

---

# 28. PostgreSQL 的基本部署结构

推荐：

```text
                    Internet
                       │
                       ▼
                  Reverse Proxy
                       │
              ┌────────┴────────┐
              ▼                 ▼
           FreqUI             API
              │                 │
              └────────┬────────┘
                       │
                 Freqtrade Bots
                       │
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
      Bot A           Bot B          Bot C
        │              │              │
        └──────────────┼──────────────┘
                       ▼
                 PostgreSQL
```

但这里还有一个关键选择：

```text
方案 A：一个数据库、多个 bot schema/table
方案 B：一个 PostgreSQL server、每个 bot 一个 database
方案 C：一个 PostgreSQL server、每个 bot 一个 schema
```

对于个人/小型实盘，我更推荐：

```text
1 个 PostgreSQL 实例
+
每个 Bot 一个 database
```

例如：

```text
freqtrade_bot_a
freqtrade_bot_b
freqtrade_bot_c
```

优点是：

```text
隔离简单
备份简单
权限简单
删除简单
迁移简单
```

---

# 29. 为什么不建议所有 Bot 共享一个 `trades` 表？

因为 Freqtrade 默认领域模型并不是为了做 multi-tenant trading database 而设计的。

如果你强行共享：

```text
trades
orders
pairlocks
custom_data
```

那么你必须保证所有对象都有：

```text
bot_id
instance_id
strategy_id
```

否则：

```text
Bot A Trade
Bot B Trade
```

就很难天然隔离。

所以当前框架下最自然的隔离方法是：

```text
Database isolation
```

而不是：

```text
Row-level tenant isolation
```

---

# 30. Freqtrade `convert-db`：官方提供了什么？

当前官方提供：

```bash
freqtrade convert-db
```

它支持指定：

```text
--db-url
--db-url-from
```

并可用于把数据库从一个系统转换到另一个系统，例如 SQLite → PostgreSQL。当前文档明确说明，转换会迁移 trades、orders、Pairlocks；目标数据库应该使用空库。([convert-db](https://raw.githubusercontent.com/freqtrade/freqtrade/develop/docs/commands/convert-db.md)、[utils](https://github.com/freqtrade/freqtrade/blob/develop/docs/utils.md))

基本思路：

```bash
freqtrade convert-db \
    --db-url-from sqlite:///tradesv3.sqlite \
    --db-url postgresql+psycopg://user:password@host:5432/freqtrade
```

执行之前应该：

```text
停止 Bot
↓
备份 SQLite
↓
创建空 PostgreSQL DB
↓
测试连接
↓
执行 convert-db
↓
检查数据
↓
再启动 Bot
```

不要在 Bot 运行过程中边交易边迁移数据库。

---

# 31. 为什么目标数据库必须尽量是“空库”？

因为 `convert-db` 会做数据库迁移和数据复制。

如果目标数据库已经有：

```text
trades
orders
pairlocks
```

可能出现：

```text
primary key collision
unique constraint collision
duplicate rows
sequence 不同步
```

官方文档明确警告目标数据库应为空，否则转换可能失败。([convert-db](https://github.com/freqtrade/freqtrade/blob/develop/docs/utils.md))

---

# 32. PostgreSQL 中最值得关注的是连接池

SQLite 常常让人关注：

```text
database is locked
```

PostgreSQL 则更容易遇到：

```text
connection pool exhausted
```

也就是：

```text
Bot 1
Bot 2
Bot 3
API
FreqUI
monitoring
        │
        ▼
PostgreSQL connection pool
        │
        ▼
连接数耗尽
```

当前 Freqtrade GitHub 也存在 PostgreSQL `QueuePool limit` 类问题案例。([Issue #13103](https://github.com/freqtrade/freqtrade/issues/13103))

因此如果你以后：

```text
5~20 bots
```

不要只想着：

> “Postgres 性能很强，所以肯定没问题。”

要开始真正关注：

```text
connection pool
session lifecycle
query frequency
API concurrency
DB max connections
```

---

# 33. `Session.commit()` 是整个持久化链的重要边界

理解 SQLAlchemy Session 最重要的一件事：

```text
Python object change
≠
Database durable state
```

例如：

```python
trade.stop_loss = 123.45
```

此时只是 ORM 对象发生变化。

真正持久化，需要：

```text
flush
+
transaction commit
```

所以逻辑上：

```text
Object state
   ↓
Session dirty state
   ↓
flush
   ↓
SQL
   ↓
commit
   ↓
Database durable state
```

---

# 34. Flush 与 Commit 的区别

### flush

把 ORM 的变化发送到数据库事务上下文。

### commit

提交事务。

可以简单记：

```text
flush = 把事情做到数据库事务里
commit = 把这次事务正式确认
```

如果：

```text
flush 成功
commit 失败
```

数据库不一定已经完成最终提交。

如果：

```text
commit 成功
```

才意味着当前事务真正完成。

---

# 35. Rollback 是另一个必须理解的概念

例如：

```text
开始事务
   │
   ├── update Trade
   ├── insert Order
   ├── update PairLock
   │
   ▼
其中一步失败
   │
   ▼
rollback
```

如果 Session 已经进入 failed transaction 状态，但你继续执行查询，可能收到类似：

```text
PendingRollbackError
```

历史 issue 中确实出现过数据库唯一约束失败后，又在 cleanup 中继续 commit，最终触发 `PendingRollbackError` 的案例。([Issue #12386](https://github.com/freqtrade/freqtrade/issues/12386))

这说明一个实盘级原则：

> **数据库异常不是普通异常；一旦事务失败，要正确 rollback/cleanup。**

---

# 36. 一次完整订单生命周期与数据库

现在把前面的东西串起来。

假设 Strategy 产生 Entry Signal：

```text
Signal
  ↓
risk checks
  ↓
stake sizing
  ↓
confirm_trade_entry
  ↓
Exchange.place_order
  ↓
CCXT response
  ↓
创建 / 更新 Order
  ↓
Trade / Order relation
  ↓
Session flush
  ↓
Session.commit
```

可以抽象为：

```text
                 Strategy
                    │
                    ▼
                 Signal
                    │
                    ▼
              Execution Layer
                    │
                    ▼
                Exchange
                    │
                    ▼
                  CCXT
                    │
                    ▼
               Raw Order
                    │
             normalization
                    │
                    ▼
                 Order ORM
                    │
                    ▼
               Trade ORM
                    │
                    ▼
                Session
                    │
                 commit
                    │
                    ▼
                Database
```

---

# 37. 为什么数据库不能只是“日志”

日志是：

```text
发生了什么？
```

数据库状态是：

```text
当前系统认为世界是什么状态？
```

例如：

```text
日志：
Order 123 closed
```

而数据库：

```text
orders.id=10
order_id=123
status=closed
ft_is_open=false
filled=1.25
average=59235.2
```

数据库是机器可以继续运行的“可恢复状态”。

因此：

```text
Log
= observation

DB
= state
```

这是实盘交易系统和普通脚本最大的区别之一。

---

# 38. Freqtrade 重启时为什么还能继续工作？

因为它不是：

```text
进程内存 = 唯一状态
```

而是：

```text
Process Memory
      +
Persistent State
      ↓
Restart Recovery
```

重启后可以重新读取：

```text
open trades
open orders
pairlocks
strategy-related persistent data
wallet-related state
```

所以数据库实际上充当了：

> **运行时恢复点。**

---

# 39. 为什么开仓后最关键的是 `Trade`，不是 signal？

Signal 本质上来自：

```text
当前 candle / 当前市场数据
```

它是瞬时计算结果。

Trade 则代表：

```text
这个 signal 已经变成现实世界的一段仓位生命周期
```

所以：

```text
Signal
= transient

Trade
= persistent
```

这是 Strategy 和 Persistence 的边界。

---

# 40. 回测为什么不应该依赖真实数据库？

因为回测需要：

```text
大量交易
大量参数组合
快速初始化
快速清空
```

如果每一个 hyperopt candidate 都写真实数据库：

```text
1000 parameters
×
10 years
×
100 pairs
```

数据库会成为巨大的瓶颈。

当前 Freqtrade 的 `LocalTrade` 就是解决这个问题的一部分。

Backtesting 代码也会显式使用：

```text
LocalTrade
Trade
disable_database_use
enable_database_use
```

来区分不同运行环境。([backtesting.py](https://github.com/freqtrade/freqtrade/blob/develop/freqtrade/optimize/backtesting.py))

---

# 41. 因此 Freqtrade Persistence 有两个世界

这是你以后读源码必须牢记的一张图：

```text
                    Trade abstraction
                          │
              ┌───────────┴───────────┐
              │                       │
              ▼                       ▼
         Live / Dry-run          Backtesting
              │                       │
              ▼                       ▼
            Trade                 LocalTrade
              │                       │
              ▼                       ▼
         SQLAlchemy DB            In-memory
```

所以当你写：

```python
trade.xxx
```

不能自动认为：

> “这个属性一定是从数据库来的。”

因为在回测世界，它可能是纯内存对象。

---

# 42. 如何在 Strategy 中查询数据库？

官方支持通过 `Trade` 获取历史交易数据，例如：

```python
from datetime import datetime, timedelta, timezone
from freqtrade.persistence import Trade

trades = Trade.get_trades_proxy(
    pair=metadata["pair"],
    open_date=datetime.now(timezone.utc) - timedelta(days=1),
    is_open=False,
).order_by(Trade.close_date).all()
```

然后可以：

```python
curdayprofit = sum(
    trade.close_profit
    for trade in trades
    if trade.close_profit is not None
)
```

但有一个非常重要的限制：官方文档明确提醒，回测/hyperopt 的 `populate_*` 方法中不能依赖 Trade history，因为这些方法执行时交易历史并不可用。([strategy-customization](https://github.com/freqtrade/freqtrade/blob/develop/docs/strategy-customization.md))

这实际上反映出：

```text
populate_*
= dataframe transformation

callbacks
= runtime decision
```

两者不能混为一谈。

---

# 43. 生产环境下不要在 Strategy 里疯狂 SQL 查询

这是很重要的性能原则。

不推荐：

```python
for pair in whitelist:
    trades = Trade.get_trades_proxy(...)
```

然后每根 candle 重复执行。

因为：

```text
每个 pair
 ×
每次 bot loop
 ×
每次 callback
```

最终就是：

```text
DB queries/sec 暴增
```

推荐：

```text
一次查询
↓
内存聚合
↓
复用结果
```

例如通过：

```text
bot_loop_start
```

统一加载状态，再让各 callback 使用内存结构。

---

# 44. 数据库查询的“热点”在哪里？

一般会集中在：

```text
Trade.get_open_trade...
Trade.get_trades...
Order.get_open_orders
PairLock queries
RPC status queries
FreqUI API queries
```

所以如果未来你遇到 PostgreSQL CPU 升高，不应该首先看：

```text
策略指标计算
```

而应该同时检查：

```text
Strategy CPU
+
DB query count
+
DB latency
+
Connection pool
+
API request rate
```

---

# 45. FreqUI 为什么会读取数据库？

FreqUI 本身是前端。

它通常通过 RPC / REST API 获取交易状态。

最终：

```text
FreqUI
  ↓
REST API
  ↓
RPC
  ↓
Trade / Order / PairLock
  ↓
SQLAlchemy Session
  ↓
Database
```

所以你打开一个交易页面时，背后实际上可能触发：

```text
API request
→ DB queries
→ JSON serialization
→ HTTP response
```

因此：

> FreqUI 不是“直接打开 SQLite 文件”。

它通过应用层读取交易状态。

---

# 46. 一个 API 请求为什么会产生多个数据库查询？

例如“显示一个 Trade 的完整详情”可能需要：

```text
Trade
   │
   ├── basic fields
   ├── orders
   ├── custom data
   └── calculated values
```

而 SQLAlchemy 的 relationship loading 策略会影响查询次数。

当前 Trade 的 orders relationship 使用 `selectin`，custom_data relationship 则是延迟更严格的策略。([trade_model.py](https://raw.githubusercontent.com/freqtrade/freqtrade/develop/freqtrade/persistence/trade_model.py))

这也是为什么读源码时要特别注意：

```text
relationship(... lazy=...)
```

因为它决定：

> “拿到 Trade 的时候，关联对象什么时候真正去查数据库？”

---

# 47. ORM 的 N+1 Query 问题

举例：

```text
查询 100 个 Trade
```

然后每个 Trade 再查询 Orders：

```text
1 个 Trade 查询
+
100 次 Order 查询
=
101 次 DB queries
```

这就是经典 N+1。

更好的方式可能是：

```text
1 个 Trade 查询
+
1 个批量 Orders 查询
```

或者由 SQLAlchemy 的 `selectin` 等加载方式帮助处理。

因此以后你自己扩展 Freqtrade 时，千万不要只看：

```python
for trade in trades:
    trade.orders
```

还要看：

```text
relationship lazy strategy
```

---

# 48. Index：数据库真正进入生产后必须理解

当前模型已经有一些明确的 index 设计，例如：

```text
Trade.pair
Order.ft_trade_id
Order.ft_is_open
Order.order_id
PairLock.pair
PairLock.lock_end_time
PairLock.active
KeyValueStore.key
```

这说明维护者已经针对常见查询路径考虑索引。([trade_model.py](https://raw.githubusercontent.com/freqtrade/freqtrade/develop/freqtrade/persistence/trade_model.py)、[pairlock.py](https://raw.githubusercontent.com/freqtrade/freqtrade/develop/freqtrade/persistence/pairlock.py)、[key_value_store.py](https://raw.githubusercontent.com/freqtrade/freqtrade/develop/freqtrade/persistence/key_value_store.py))

---

# 49. 为什么不是“所有字段都加 index”？

因为 Index 不是免费的。

每次写：

```text
INSERT
UPDATE
DELETE
```

都需要维护 index。

如果你把 50 个字段全部索引：

```text
查询可能快
但写入变慢
磁盘变大
缓存压力增大
```

所以生产数据库的核心原则是：

> **Index 应该围绕真实查询模式设计。**

---

# 50. Freqtrade 的高价值索引思路

如果你以后做自己的分析扩展，常见高价值维度通常是：

```text
pair
is_open
open_date
close_date
strategy
exit_reason
order_id
trade_id
```

例如：

```sql
CREATE INDEX idx_trades_open_date
ON trades(open_date);
```

但注意：

**不要在没有 EXPLAIN/真实 workload 的情况下，盲目给生产 Freqtrade 数据库加索引。**

---

# 51. 如果我要自己加一列，应该怎么办？

假设你想给 Trade 加：

```text
regime
```

第一反应不要：

```sql
ALTER TABLE trades ADD COLUMN regime TEXT;
```

因为你修改了 Freqtrade 官方 schema。

更好的方式是先判断：

### 方案 A：Trade-specific persistent data

```python
trade.set_custom_data("regime", "breakout")
```

适合：

```text
少量
策略内部
交易级
```

### 方案 B：外部扩展表

```text
trade_extensions
```

例如：

```text
trade_id
regime
model_score
feature_version
created_at
```

### 方案 C：独立 analytics DB

把分析数据写入你自己的 PostgreSQL schema。

---

# 52. 我最推荐的未来扩展：不要直接魔改 Freqtrade 的核心表

如果你准备把 Freqtrade 当主力框架，我建议采用：

```text
                         Freqtrade
                            │
             ┌──────────────┴──────────────┐
             │                             │
             ▼                             ▼
      Official Persistence          Your Extension Layer
             │                             │
             │                    ┌────────┼─────────┐
             │                    │        │         │
             ▼                    ▼        ▼         ▼
          trades             strategy  analytics  risk
          orders             state     event      state
          locks                         ledger
```

这样：

```text
Freqtrade 升级
↓
官方 schema 自己迁移

你的系统升级
↓
独立迁移
```

耦合最小。

---

# 53. 推荐的“扩展数据库”设计

如果未来你想建设自己的量化平台，我建议额外设计：

```text
analytics
├── strategy_runs
├── signal_events
├── trade_features
├── trade_attribution
├── risk_snapshots
├── equity_snapshots
├── execution_metrics
└── model_predictions
```

然后让 Freqtrade 作为 Execution Engine：

```text
Strategy
   │
   ├── Signal
   ├── Model Score
   ├── Regime
   └── Risk State
          │
          ├── Freqtrade official DB
          │
          └── Your Analytics DB
```

这比把所有东西塞进 `trades` 表强得多。

---

# 54. 进一步升级：Event 数据库

如果你以后真的想把这个框架做到“专业级”，我非常建议设计一个：

```text
Trading Event Store
```

例如：

```text
signal_generated
entry_rejected
entry_order_created
entry_order_filled
position_adjusted
exit_signal_generated
stoploss_triggered
exit_order_created
exit_order_filled
pair_locked
wallet_changed
```

然后存：

```text
event_id
bot_id
trade_id
pair
timestamp
event_type
payload
```

这样你就拥有：

```text
State DB
+
Event DB
```

可以实现非常强的可观测性。

---

# 55. State DB 与 Event DB 的区别

### State DB

回答：

> 当前是什么状态？

例如：

```text
Trade.is_open = true
```

### Event DB

回答：

> 它经历了什么？

例如：

```text
09:31:02 signal_generated
09:31:03 entry_order_created
09:31:05 partially_filled
09:31:12 fully_filled
09:45:21 stoploss_updated
10:13:52 exit_signal_generated
```

这两个模型未来可以一起使用：

```text
Event
  ↓
Current State
```

对于实盘 Debug 非常强。

---

# 56. 数据库扩展方向：PostgreSQL + TimescaleDB？

如果你未来想把 PostgreSQL 不只是用来保存 Trade，而是做行情/分析时序数据库，可以考虑 TimescaleDB 一类的 PostgreSQL 扩展。

但必须区分两件事：

```text
Freqtrade official persistence support
≠
TimescaleDB as arbitrary PostgreSQL extension
```

Freqtrade 官方测试并明确列出的数据库系统是 SQLite、PostgreSQL 和 MariaDB；TimescaleDB 是 PostgreSQL 生态扩展，因此不能自动理解为“Freqtrade 官方支持 TimescaleDB”。

如果使用，建议：

```text
Freqtrade Trade DB
    ↓
普通 PostgreSQL schema

Market Data Warehouse
    ↓
TimescaleDB / Parquet / Data Lake
```

而不是一开始就修改 Freqtrade 的 Trade schema。

---

# 57. 为什么我不建议你直接把 Freqtrade 改造成“任意数据库后端”？

因为真正的难点并不是：

```python
create_engine(...)
```

而是：

```text
SQL semantics
transaction semantics
locking
isolation
sequence
foreign key
JSON support
datetime
boolean
float behavior
index semantics
migration behavior
connection pooling
```

真正换数据库意味着：

```text
ORM compatibility
+
Dialect compatibility
+
Migration compatibility
+
Runtime concurrency
```

---

# 58. SQLAlchemy 已经帮你屏蔽了什么？

SQLAlchemy 已经抽象了：

```text
SELECT
INSERT
UPDATE
DELETE
JOIN
ORM mapping
transaction
connection pooling
```

例如：

```python
select(Trade).where(Trade.pair == "BTC/USDT")
```

可以由不同 dialect 编译成不同数据库的 SQL。

这就是：

```text
Python ORM expression
        ↓
SQLAlchemy compiler
        ↓
Dialect
        ↓
Database-specific SQL
```

---

# 59. 但是 SQLAlchemy 不会替你解决所有问题

例如：

```text
SQLite
PostgreSQL
MariaDB
```

对：

```text
Boolean
DateTime
Sequence
JSON
locking
transaction isolation
```

的实现并不完全一样。

所以：

> SQLAlchemy 是数据库抽象层，不是“所有数据库完全等价层”。

---

# 60. PostgreSQL 与 SQLite 最大的架构差异之一：Sequence

SQLite 的自增 id 与 PostgreSQL sequence 的行为不完全一样。

这也是为什么 Freqtrade migrations 当前代码里需要专门处理 PostgreSQL sequence：

```text
orders_id_seq
trades_id_seq
pairlocks_id_seq
KeyValueStore_id_seq
trade_custom_data_id_seq
wallet_history_id_seq
```

当前迁移代码会在 PostgreSQL 环境下显式读取/恢复 sequence。([migrations.py](https://raw.githubusercontent.com/freqtrade/freqtrade/develop/freqtrade/persistence/migrations.py))

这直接说明：

> 数据库迁移绝对不能只考虑“表字段”。

还必须考虑：

```text
index
foreign key
sequence
constraint
```

---

# 61. Foreign Key 为什么非常重要？

当前：

```text
orders.ft_trade_id
    ↓
ForeignKey("trades.id")
```

表示：

```text
Order 必须归属于某个 Trade
```

这使数据库成为：

> 数据完整性守门人。

如果没有 FK：

```text
Trade 被删除
Order 还在
```

数据库里就会出现孤儿数据。

当前模型还使用 relationship + cascade 来处理关联删除。Trade 的 orders relationship 明确配置了 `cascade="all, delete-orphan"`。([trade_model.py](https://raw.githubusercontent.com/freqtrade/freqtrade/develop/freqtrade/persistence/trade_model.py))

---

# 62. `delete-orphan` 意味着什么？

简单理解：

```text
Trade
  ├── Order A
  └── Order B
```

如果某个 Order 不再属于这个 Trade，它可能被视为 orphan，并由 ORM 的级联规则处理。

因此：

> 不要随便手工修改 ORM 关系对象。

因为 ORM relationship 不只是“方便访问”，它还携带持久化语义。

---

# 63. 数据库里的 `is_open` 为什么如此关键？

`Trade.is_open` 是运行态的重要状态字段。

逻辑上：

```text
is_open = true
    ↓
当前 position 尚未结束
```

而：

```text
is_open = false
    ↓
交易生命周期结束
```

这也是为什么大量查询都会围绕：

```text
Trade.is_open
```

展开。

例如：

```text
当前所有持仓
当前最大交易数
当前风险暴露
当前 FreqUI 页面
```

都可能依赖这一类状态。

---

# 64. 交易数据库应该如何做备份？

最简单版本：

## SQLite

```bash
cp tradesv3.sqlite backups/tradesv3_$(date +%F_%H%M%S).sqlite
```

但实盘最好：

```text
停止 Bot
↓
备份
↓
校验
↓
重启
```

或者使用 SQLite 自身的可靠备份机制，而不是在高写入期随便复制文件。

## PostgreSQL

使用：

```bash
pg_dump
```

例如：

```bash
pg_dump \
  -h 127.0.0.1 \
  -U freqtrade \
  -d freqtrade_bot_a \
  -Fc \
  -f freqtrade_bot_a.dump
```

---

# 65. 备份不等于恢复

真正生产级数据库策略必须包含：

```text
Backup
+
Restore Test
+
Recovery Procedure
```

至少要验证：

```text
新数据库是否能恢复？
Freqtrade 是否能连接？
Trade 是否完整？
Order 是否完整？
PairLock 是否完整？
Bot 是否能正常启动？
API/FreqUI 是否正常？
```

如果从来没做过 restore test：

> 不能认为自己有可靠备份。

---

# 66. PostgreSQL 备份策略建议

个人实盘可以考虑：

```text
每日完整 dump
+
每小时增量/快照（视需求）
+
异地存储
```

例如：

```text
VPS
 │
 ├── /backup/local
 │
 └── Object Storage
       ├── S3
       ├── OSS
       ├── COS
       └── MinIO
```

注意不要把数据库备份放在：

```text
只有同一块 VPS 的同一块磁盘
```

因为 VPS 整盘损坏时：

```text
DB
+
Backup
```

会一起消失。

---

# 67. 生产数据库权限应该怎么做？

不要让 Freqtrade 使用超级管理员：

```text
postgres
root
```

建议：

```text
PostgreSQL
 ├── freqtrade_admin
 ├── freqtrade_bot_a
 ├── freqtrade_bot_b
 └── analytics_reader
```

Freqtrade bot 用户只拥有自己的 database/schema 所需权限。

这样即使 bot 被入侵：

```text
Bot A
```

也不应该可以：

```text
DROP DATABASE Bot B
```

---

# 68. Freqtrade API 与数据库权限是两件不同的事

这是安全上经常被混淆的问题。

```text
API auth
```

控制的是：

```text
谁能调用 Freqtrade
```

而：

```text
DB credentials
```

控制的是：

```text
谁能读写数据库
```

不要因为 API 有密码，就认为数据库安全。

---

# 69. 数据库密码不要硬编码在代码仓库

推荐使用：

```text
Docker secrets
环境变量
Vault
密钥管理服务
```

例如不要把：

```python
DATABASE_URL = "postgresql://admin:SuperSecretPassword@..."
```

提交进 Git。

而应通过配置注入。

---

# 70. 多 Bot + 一个 PostgreSQL 的推荐架构

如果以后你有：

```text
Strategy A
Strategy B
Strategy C
Strategy D
```

我建议：

```text
                    PostgreSQL
                        │
        ┌───────────────┼────────────────┐
        │               │                │
        ▼               ▼                ▼
     bot_a            bot_b            bot_c
    database          database          database
        │               │                │
        ▼               ▼                ▼
   Freqtrade A      Freqtrade B      Freqtrade C
        │               │                │
        └───────────────┼────────────────┘
                        ▼
                    FreqUI / API
```

这里的“一个 FreqUI 管理多个 Bot”取决于你实际使用的当前 FreqUI 多 bot 能力和部署方式，但数据库层面的最佳实践仍然是：

> **实例隔离数据库。**

---

# 71. 一个更高级的方案：一个 PostgreSQL，多个 schema

例如：

```text
PostgreSQL cluster
│
├── bot_a schema
│   ├── trades
│   ├── orders
│   └── ...
│
├── bot_b schema
│   ├── trades
│   ├── orders
│   └── ...
│
└── analytics schema
    ├── trade_features
    ├── equity_curve
    └── events
```

优点：

```text
数据库实例统一
备份统一
资源统一
```

缺点：

```text
Freqtrade schema/search_path 处理更复杂
升级/迁移更复杂
权限设计更复杂
```

所以这属于：

> 你未来自己做平台化改造时再考虑的架构。

对于个人实盘，不必一开始就复杂化。

---

# 72. 不建议把 Redis 当 Trade 数据库

Redis 非常适合：

```text
cache
实时状态
队列
pub/sub
临时计算结果
```

但不适合作为 Freqtrade 官方 Trade persistence 的简单替换：

因为它缺少你这里真正依赖的很多关系模型语义：

```text
foreign key
SQL query
transaction semantics
relational constraints
ORM relationships
schema migration
```

更合理的是：

```text
PostgreSQL
   +
Redis
```

而不是：

```text
Redis
取代
PostgreSQL
```

---

# 73. 如果以后需要 ClickHouse 呢？

ClickHouse 非常适合：

```text
大量事件
大量 tick
大量交易日志
高并发分析
聚合查询
```

但它并不适合直接当 Freqtrade 官方 OLTP Trade persistence 的直接替代品。

正确架构更像：

```text
Freqtrade
   │
   ├── PostgreSQL
   │      └── authoritative state
   │
   └── Event Pipeline
          │
          ▼
      ClickHouse
          │
          ▼
       Analytics
```

即：

```text
OLTP
+
OLAP
```

---

# 74. 推荐你以后采用“数据库三层架构”

对于你计划长期使用 Freqtrade，我非常推荐最终演化到：

```text
┌──────────────────────────────────────────────┐
│                Trading Platform              │
├──────────────────────────────────────────────┤
│                                              │
│  Layer 1: Freqtrade Operational DB          │
│      PostgreSQL                              │
│      trades / orders / locks / state         │
│                                              │
│  Layer 2: Event / Analytics DB               │
│      ClickHouse / PostgreSQL / Parquet       │
│      events / signals / metrics / features   │
│                                              │
│  Layer 3: Market Data Warehouse              │
│      Parquet / S3 / Timescale / Data Lake    │
│      OHLCV / Tick / Funding / Orderbook      │
│                                              │
└──────────────────────────────────────────────┘
```

这是比：

```text
everything -> one sqlite
```

高一个数量级的架构。

---

# 75. 为什么你现在不用急着做这三层？

因为架构需要随着 workload 演化。

如果现在只有：

```text
2 个 Bot
10 个 Pair
```

SQLite 就足够。

如果变成：

```text
20 个 Bot
200 个 Pair
```

PostgreSQL 开始有意义。

如果变成：

```text
20 Bots
大量事件
大量 tick
模型训练
```

再引入：

```text
ClickHouse
Parquet
Object Storage
```

所以：

> 数据库架构不要超前到无法运维。

---

# 76. 怎样判断是否应该从 SQLite 迁移到 PostgreSQL？

可以看下面的信号。

### 第一类：实例数量

```text
1~2 bots
→ SQLite 很舒服

5~10 bots
→ PostgreSQL 开始值得考虑
```

这不是硬门槛，而是经验边界。

### 第二类：并发

```text
多个 bot
+
FreqUI
+
API
+
分析程序
```

并发读写越来越多，就值得考虑 PostgreSQL。

### 第三类：运维

如果你需要：

```text
远程 DB
集中备份
权限控制
监控
SQL 分析
```

PostgreSQL 更合适。

---

# 77. 数据库迁移前 checklist

```text
[ ] 停止全部 Freqtrade bots
[ ] 备份原 SQLite
[ ] 检查数据库完整性
[ ] 创建空 PostgreSQL database
[ ] 安装正确 DB driver
[ ] 测试 SQLAlchemy URL
[ ] 执行 convert-db
[ ] 检查 trades 数量
[ ] 检查 orders 数量
[ ] 检查 PairLocks
[ ] 检查 open trades
[ ] 检查最新交易状态
[ ] 检查 sequence
[ ] 启动 dry-run 验证
[ ] 再恢复 live
[ ] 保留旧 DB 一段时间
```

---

# 78. 怎么验证迁移前后数据一致？

不要只看：

```text
数据库能连接
```

至少比较：

```sql
SELECT COUNT(*) FROM trades;
SELECT COUNT(*) FROM orders;
SELECT COUNT(*) FROM pairlocks;
```

然后进一步：

```sql
SELECT COUNT(*)
FROM trades
WHERE is_open = true;
```

再比较：

```text
open trade pairs
latest trade
latest order
total realized profit
```

更进一步可以对关键字段做 checksum / hash。

---

# 79. SQLite → PostgreSQL 最容易漏掉什么？

常见风险点：

```text
timestamp
boolean
float
sequence
index
foreign key
null behavior
```

尤其：

```text
PostgreSQL boolean
SQLite integer-like boolean
```

以及：

```text
PostgreSQL sequence
```

Freqtrade 官方 migration code 已经专门处理 sequence，这就是一个很重要的提醒。([migrations.py](https://raw.githubusercontent.com/freqtrade/freqtrade/develop/freqtrade/persistence/migrations.py))

---

# 80. 数据库恢复后不要直接进入 Live

推荐：

```text
Restore
 ↓
Dry Run / read-only inspection
 ↓
API inspection
 ↓
FreqUI inspection
 ↓
Open trades check
 ↓
Exchange reconciliation
 ↓
Live
```

尤其是 open orders。

因为：

```text
DB Order State
```

和：

```text
Exchange Order State
```

可能短暂不一致。

---

# 81. 这是为什么 Order 状态需要周期性同步

真实世界：

```text
Freqtrade
     │
     │ place order
     ▼
Exchange
     │
     ▼
Order executes asynchronously
```

所以数据库写入的是：

```text
当前已知状态
```

然后后续继续从交易所获取 Order 状态：

```text
Exchange order
      ↓
CCXT normalized response
      ↓
Order.update_from_ccxt_object()
      ↓
Trade.commit()
```

当前源码确实包含这种 `Order.update_from_ccxt_object()` 更新逻辑。([trade_model.py](https://raw.githubusercontent.com/freqtrade/freqtrade/develop/freqtrade/persistence/trade_model.py))

---

# 82. 这也意味着数据库不是“绝对真相”，而是“持久化状态缓存”

非常重要。

对于交易执行领域，真正的世界是：

```text
Exchange
```

数据库是：

```text
Freqtrade's persistent representation of exchange state
```

因此如果出现：

```text
DB says open
Exchange says closed
```

必须 reconcile。

所以不要写：

> “数据库是交易所事实。”

更准确的是：

> “数据库是 Freqtrade 对交易状态的持久化表示，交易所是外部执行事实源之一。”

---

# 83. 生产级数据库监控应该看什么？

至少：

```text
Connections
Active sessions
Query latency
Transactions/sec
Locks
Deadlocks
Disk usage
WAL size
Checkpoint
CPU
Memory
IOPS
```

对于 PostgreSQL：

```sql
SELECT *
FROM pg_stat_activity;
```

可以帮助查看连接和活动情况。

---

# 84. 慢查询怎么办？

首先不要盲目改代码。

先：

```text
定位 endpoint
↓
定位 ORM method
↓
拿到 SQL
↓
EXPLAIN ANALYZE
↓
检查 index
↓
检查 row count
↓
再决定优化
```

这是你以后真正做源码级优化应该遵守的步骤。

---

# 85. 如果未来你想自己扩展 Freqtrade 的数据库层

这里就进入真正的“框架级改造”了。

我建议你不要先写：

```python
class MyDatabase:
    pass
```

而先定义：

```text
Persistence Port
```

也就是：

```text
Trading Core
      │
      ▼
Persistence Interface
      │
  ┌───┴────────┬────────────┐
  ▼            ▼            ▼
SQLAlchemy   Redis        EventStore
Adapter      Cache        Adapter
```

---

# 86. 为什么这是更好的扩展思路？

因为你的 Strategy/Trading Engine 不应该知道：

```text
PostgreSQL
SQLite
MariaDB
```

它应该知道：

```text
get_open_trades()
save_trade()
update_trade()
get_trade_orders()
store_trade_state()
```

也就是：

```text
Domain API
```

而不是：

```text
Database API
```

---

# 87. 如果你真的想改 Freqtrade，第一层应该抽象什么？

推荐顺序：

```text
1. TradeRepository
2. OrderRepository
3. PairLockRepository
4. CustomDataRepository
5. KeyValueRepository
6. WalletHistoryRepository
```

例如：

```python
class TradeRepository:
    def get_open_trades(self):
        raise NotImplementedError

    def get_by_id(self, trade_id: int):
        raise NotImplementedError

    def save(self, trade):
        raise NotImplementedError

    def commit(self):
        raise NotImplementedError
```

然后：

```text
SQLAlchemyTradeRepository
PostgresTradeRepository
EventSourcedTradeRepository
```

这才是真正意义上的“可替换数据库”。

---

# 88. 但为什么 Freqtrade 当前没有完全这么做？

因为项目本身主要面向：

```text
SQLAlchemy ORM
```

所以 Trade / Order / PairLock 等模型直接绑定 ORM，并共享 Session。

这种方式：

```text
开发快
成熟
稳定
```

但代价是：

```text
Persistence coupling 较高
```

如果你未来 fork Freqtrade 做自己的平台，这可能是一个值得演进的方向。

---

# 89. 我的建议：不要马上 fork Persistence

先用官方：

```text
SQLite
→
PostgreSQL
```

把：

```text
Trade
Order
PairLock
CustomData
```

用明白。

等你真正需要：

```text
Event Sourcing
multi-tenant
distributed execution
central risk
multi-account
cross-bot portfolio
```

再考虑深改。

---

# 90. 未来如果做“跨 Bot 账户级风控”，数据库架构会发生变化

这是与你后续量化框架目标特别相关的一个方向。

假设：

```text
Bot A → BTC breakout
Bot B → ETH mean reversion
Bot C → Gold style strategy
```

每个 Freqtrade 有自己的 Trade DB。

但账户级风控需要看到：

```text
Total Equity
Total Exposure
BTC Exposure
ETH Exposure
Long Exposure
Short Exposure
Strategy Correlation
Drawdown
```

这时不能让每个 Strategy 自己判断。

需要一个：

```text
Portfolio / Risk DB
```

架构变成：

```text
                 Portfolio Risk Engine
                         │
          ┌──────────────┼──────────────┐
          │              │              │
          ▼              ▼              ▼
        Bot A          Bot B          Bot C
          │              │              │
       Local DB       Local DB       Local DB
```

这个方向非常适合你后面做多策略组合系统。

---

# 91. 一个更专业的未来数据库架构

我建议最终目标可以是：

```text
                           ┌───────────────────┐
                           │ Portfolio / Risk  │
                           │ Database          │
                           └────────┬──────────┘
                                    │
                         exposures / equity / risk
                                    │
      ┌─────────────────────────────┼────────────────────────────┐
      │                             │                            │
      ▼                             ▼                            ▼
┌─────────────┐              ┌─────────────┐              ┌─────────────┐
│ Freqtrade A │              │ Freqtrade B │              │ Freqtrade C │
│ DB          │              │ DB          │              │ DB          │
└──────┬──────┘              └──────┬──────┘              └──────┬──────┘
       │                            │                            │
       └────────────────────────────┼────────────────────────────┘
                                    │
                                    ▼
                             Event / Analytics
                                    │
                    ┌───────────────┼────────────────┐
                    ▼               ▼                ▼
               PostgreSQL      ClickHouse        Parquet/S3
```

这已经不是普通的：

> “我写一个策略跑一下。”

而是一个真正的：

> **Multi-strategy algorithmic trading platform**。

---

# 92. 数据库性能优化的优先级

如果以后性能出问题，建议按照这个优先级排查：

```text
1. 是否查询过多
2. 是否存在 N+1
3. 是否连接池耗尽
4. 是否索引缺失
5. 是否单次查询返回过多行
6. 是否 Strategy 重复查询
7. 是否 API/FreqUI 高频访问
8. 是否 SQLite 并发瓶颈
9. 是否磁盘 IO 瓶颈
10. 最后才考虑更底层数据库调参
```

很多系统所谓的“数据库性能问题”，实际上是：

```text
应用层重复查询
```

而不是数据库本身慢。

---

# 93. 如果数据库 CPU 很高，怎么定位？

建议建立这条路径：

```text
CPU ↑
 ↓
DB process
 ↓
slow query
 ↓
SQL
 ↓
caller
 ↓
Freqtrade module
 ↓
Strategy / RPC / UI
```

例如：

```text
PostgreSQL CPU 90%
        ↓
pg_stat_statements
        ↓
发现某 query 高频执行
        ↓
追到 REST endpoint
        ↓
追到 RPC
        ↓
追到 Trade query
```

这就是你要的“不要黑盒”的调试方式。

---

# 94. 数据库异常分类

实盘时可以把数据库问题分成：

### 1. Connection error

```text
cannot connect
connection refused
```

### 2. Pool exhaustion

```text
QueuePool limit
```

### 3. Lock

```text
database is locked
```

### 4. Integrity

```text
UNIQUE violation
ForeignKey violation
NOT NULL violation
```

### 5. Transaction state

```text
PendingRollbackError
```

### 6. Schema

```text
missing column
wrong table
migration failure
```

### 7. Data corruption

```text
orphan row
inconsistent sequence
invalid values
```

---

# 95. 遇到数据库异常时不要马上删除数据库

这是实盘最忌讳的事情之一。

看到：

```text
SQLite error
```

不要：

```bash
rm tradesv3.sqlite
```

因为它可能包含：

```text
open positions
历史 trades
order records
pair locks
```

正确做法：

```text
停止 Bot
↓
复制数据库
↓
诊断
↓
验证交易所状态
↓
修复
```

---

# 96. 数据库损坏时最重要的不是 DB，而是 Exchange reconciliation

例如：

```text
DB 损坏
```

但交易所还有：

```text
BTC position
Open orders
```

这时真正的风险不是：

> “我的 SQLite 文件坏了。”

而是：

> “我不知道真实世界里到底持有什么。”

所以实盘系统恢复时必须：

```text
Exchange state
+
Database state
+
Logs
```

三者交叉验证。

---

# 97. 推荐的生产恢复流程

```text
          Incident
             │
             ▼
       Stop Freqtrade
             │
             ▼
       Freeze changes
             │
       ┌─────┴─────┐
       ▼           ▼
    DB state    Exchange state
       │           │
       └─────┬─────┘
             ▼
        Reconciliation
             │
       ┌─────┴─────┐
       ▼           ▼
    consistent   inconsistent
       │           │
       ▼           ▼
    restore      manual recovery
       │
       ▼
    Dry-run test
       │
       ▼
      Live
```

---

# 98. 最重要的数据库开发原则：不要直接让 Strategy 拥有数据库权力

不推荐：

```python
from sqlalchemy import create_engine

engine = create_engine("postgresql://...")
```

然后 Strategy 自己 query。

这会造成：

```text
Strategy
  ↓
直接控制数据库
```

最终导致：

```text
Strategy coupling to infrastructure
```

更好的方式：

```text
Strategy
   ↓
Domain / Repository API
   ↓
Persistence Layer
```

---

# 99. 但是 Freqtrade 当前允许直接使用 Trade API

这是一种实用主义设计：

```python
from freqtrade.persistence import Trade
```

可以查询 Trade。

它很方便，但你应该把它理解成：

> **受控的 Persistence API，而不是让 Strategy 获得任意 SQL 权限。**

不要进一步扩张成：

```text
Strategy → SQLAlchemy engine → raw SQL → 任意表
```

除非你是在自己维护 fork。

---

# 100. 如果未来我要扩展成 MongoDB 怎么办？

理论上可以，但是这不是“换一个 URL”那么简单。

MongoDB 是 document database，而 Freqtrade 当前 Persistence 核心是 relational ORM。

你需要重新映射：

```text
Trade
Order
PairLock
CustomData
WalletHistory
```

以及重新处理：

```text
transactions
foreign keys
relationships
indexes
queries
migration
```

所以 MongoDB 不属于：

> “轻量数据库替换”。

它属于：

> **Persistence architecture redesign。**

---

# 101. 如果未来我要用 MySQL 呢？

这个比 MongoDB 简单得多，因为它仍然是 SQL relational DB，并且 SQLAlchemy 有对应 dialect。

但要区分：

```text
SQLAlchemy 可以连接
```

和：

```text
Freqtrade 官方测试支持
```

当前官方文档明确列出 MariaDB，而不是简单地把“所有 MySQL-compatible 数据库”都当成官方支持。([Advanced Post-installation Tasks](https://docs.freqtrade.io/en/latest/advanced-setup/))

所以生产采用非官方明确测试系统时，要自己做：

```text
migration test
backtest test
live dry-run
API test
restart test
recovery test
```

---

# 102. 如果我要开发自己的数据库后端，建议先做“兼容层”而不是“替换层”

也就是：

```text
Freqtrade ORM
       │
       ▼
Compatibility Adapter
       │
       ├── PostgreSQL
       ├── MariaDB
       └── MyBackend
```

而不是：

```text
Freqtrade Core
       ↓
完全删掉 SQLAlchemy
       ↓
重新写一套 DB engine
```

前者风险低很多。

---

# 103. 自定义 Persistence 最小接口建议

如果以后你真的 fork，可以定义：

```python
class PersistenceBackend(ABC):

    @abstractmethod
    def get_open_trades(self):
        ...

    @abstractmethod
    def get_trade(self, trade_id: int):
        ...

    @abstractmethod
    def save_trade(self, trade):
        ...

    @abstractmethod
    def save_order(self, order):
        ...

    @abstractmethod
    def get_open_orders(self):
        ...

    @abstractmethod
    def set_pair_lock(self, lock):
        ...

    @abstractmethod
    def get_pair_locks(self):
        ...
```

然后由：

```text
SQLAlchemyBackend
```

实现。

---

# 104. 更进一步：Repository 与 Unit of Work

如果把 Persistence 做得更专业，可以采用：

```text
Repository
+
Unit of Work
```

例如：

```python
with unit_of_work() as uow:
    trade = uow.trades.get(trade_id)
    trade.stop_loss = new_stop
    uow.orders.add(order)
    uow.commit()
```

这样业务代码就不需要理解：

```text
session
flush
transaction
commit
```

但这已经属于你自己建设平台级框架的方向，不是当前官方 Freqtrade 的默认代码风格。

---

# 105. 为什么“状态机 + 数据库”特别适合你的策略开发？

因为你后续如果做多周期组合策略，很可能需要状态：

```text
market_regime
signal_state
entry_state
position_state
risk_state
```

而不是简单：

```text
if signal:
    return "enter_long"
```

你可能会需要：

```text
REGIME_UNKNOWN
REGIME_RANGE
REGIME_BREAKOUT
REGIME_TREND
REGIME_RISK_OFF
```

这些状态可以存在：

```text
内存
+
Trade CustomData
+
外部 Risk DB
```

形成完整状态机。

---

# 106. 你后续做组合策略时，我建议数据库记录“决策上下文”

例如 Trade 进入时：

```python
trade.set_custom_data("regime", "breakout")
trade.set_custom_data("entry_score", 0.83)
trade.set_custom_data("risk_score", 0.21)
trade.set_custom_data("htf_bias", "bullish")
trade.set_custom_data("volatility", 0.028)
```

这样未来你可以回答：

```text
为什么这笔交易进场？

当时高周期趋势是什么？

当时波动率是多少？

模型评分是多少？

风险是多少？
```

这会极大提升你的策略研究能力。

---

# 107. 数据库不应该保存所有研究数据

建议采用：

```text
Trade DB
    ↓
关键交易上下文

Research DB
    ↓
完整特征

Market Data
    ↓
完整行情
```

例如：

```text
Trade DB
entry_score = 0.82
```

而：

```text
Research DB
feature_001
feature_002
feature_003
...
feature_500
```

这种分工非常重要。

---

# 108. 数据库 Schema 版本必须纳入你的开发流程

如果以后你做自己的扩展：

```text
v1
v2
v3
```

每次 schema 修改都必须：

```text
migration
+
rollback plan
+
backup
+
compatibility test
```

不要：

```text
今天手改表
明天改 Python
后天忘了
```

---

# 109. 自定义表应该怎么命名？

不要轻易与 Freqtrade 官方表名冲突。

可以使用：

```text
ext_strategy_runs
ext_trade_features
ext_trade_events
ext_risk_snapshots
ext_portfolio_snapshots
```

这样一眼就知道：

```text
这是你自己的
```

---

# 110. 自定义表建议保留 `trade_id`，但不要假设它永远全局唯一

如果每个 Bot 有自己的数据库：

```text
trade_id = 123
```

在不同 bot 中完全可能同时存在。

所以外部数据最好：

```text
bot_id
trade_id
```

共同作为业务主键上下文。

例如：

```text
(bot_id, trade_id)
```

而不是只有：

```text
trade_id
```

这对于你未来做多 Bot 组合平台尤其重要。

---

# 111. 更推荐 `trade_uuid` 作为平台级关联 ID

如果未来你做平台化，可以在扩展数据库里给每笔交易：

```text
trade_uuid
```

例如：

```text
8af1c2d0-...
```

然后保存：

```text
platform_trade_id
bot_id
freqtrade_trade_id
```

形成：

```text
Platform Trade
      │
      ├── Bot ID
      └── Freqtrade Trade ID
```

这样未来迁移数据库、跨 bot 聚合、事件追踪都会更容易。

---

# 112. 如果未来运行多个交易所呢？

官方 Trade 中已经有：

```text
exchange
```

但平台级设计最好继续保留：

```text
account_id
exchange_id
bot_id
strategy_id
trade_id
```

这是因为：

```text
同一个 pair
```

在：

```text
Binance
Bybit
OKX
```

并不是同一个业务实体。

---

# 113. 如果未来做跨交易所套利呢？

这时单个 Trade 模型就不够。

你可能需要：

```text
ArbPosition
  ├── Leg A
  └── Leg B
```

例如：

```text
Binance BTC Long
Bybit BTC Short
```

两个 Freqtrade Trade 可以属于同一个：

```text
arbitrage_group_id
```

于是你的外部数据库就应该负责组合关系。

这就是为什么：

> Freqtrade 官方 Trade DB 不应该被迫承担整个世界。

---

# 114. 推荐的数据库边界

最终可以把系统分成：

```text
Freqtrade Core DB
│
├── Trade lifecycle
├── Order lifecycle
├── Pair lock
├── Strategy trade state
└── Bot state

Your Platform DB
│
├── Portfolio
├── Risk
├── Multi-bot relations
├── Cross-exchange relations
├── Analytics
├── Research metadata
└── Model metadata
```

这是一条非常稳健的演化路径。

---

# 115. 你以后升级 Freqtrade 时，为什么这种方式最安全？

因为官方数据库：

```text
Freqtrade owns it
```

你的扩展数据库：

```text
You own it
```

于是：

```text
Freqtrade upgrade
```

只影响：

```text
Freqtrade schema
```

而：

```text
Your extension schema
```

可以独立迁移。

这会显著降低升级风险。

---

# 116. 数据库 Debug 最终应该做到“源码 → SQL → DB”闭环

以后遇到问题：

```text
FreqUI 显示错误
```

你应该能追：

```text
FreqUI
↓
REST endpoint
↓
RPC
↓
Trade method
↓
SQLAlchemy query
↓
SQL
↓
PostgreSQL
```

如果：

```text
Trade 状态异常
```

则：

```text
freqtradebot
↓
Trade object mutation
↓
Session
↓
commit
↓
DB
```

这就是你要求的“不能有黑盒”。

---

# 117. 数据库部分最值得你精读的源码文件

按照优先级：

```text
1. freqtrade/persistence/models.py
2. freqtrade/persistence/base.py
3. freqtrade/persistence/trade_model.py
4. freqtrade/persistence/migrations.py
5. freqtrade/persistence/pairlock.py
6. freqtrade/persistence/pairlock_middleware.py
7. freqtrade/persistence/custom_data.py
8. freqtrade/persistence/key_value_store.py
9. freqtrade/persistence/wallet_history.py
10. freqtrade/optimize/backtesting.py
11. freqtrade/rpc/rpc.py
12. freqtrade/freqtradebot.py
```

建议不要从 `Trade` 一口气读完 2000 多行。

而是按：

```text
Model definition
→ relationship
→ query methods
→ update methods
→ commit
→ state calculation
```

拆开读。

---

# 118. 当前 `trade_model.py` 为什么这么大？

因为它不仅是 ORM schema。

还同时包含：

```text
Trade model
Order model
LocalTrade
Trade query helpers
profit calculations
order synchronization
trade reconstruction
backtesting alignment
```

所以它其实是：

> Model + Domain Logic + Persistence API 的混合体。

这是你以后如果 fork Freqtrade 最值得关注的地方之一。

---

# 119. 如果自己重构，我会怎么拆？

我会逐步向：

```text
persistence/
├── models/
│   ├── trade.py
│   ├── order.py
│   ├── pairlock.py
│   ├── custom_data.py
│   └── wallet_history.py
│
├── repositories/
│   ├── trade_repository.py
│   ├── order_repository.py
│   └── pairlock_repository.py
│
├── unit_of_work.py
├── migrations/
└── backends/
    ├── sqlalchemy.py
    └── in_memory.py
```

演化。

但这属于平台级重构，千万不要为了“代码看起来漂亮”就在你的第一个 Freqtrade fork 中直接大改。

---

# 120. 最后的结论：你应该怎样理解 Freqtrade 数据库？

我建议你把它记成下面这张图：

```text
                         Freqtrade
                            │
                            ▼
                     Domain State
                            │
          ┌─────────────────┼──────────────────┐
          │                 │                  │
          ▼                 ▼                  ▼
        Trade             Order            Policy
          │                 │                  │
          │                 │                  └── PairLock
          │                 │
          │                 └────────────────────┐
          │                                      │
          └───────────────┬──────────────────────┘
                          ▼
                     SQLAlchemy
                          │
                ┌─────────┴─────────┐
                │                   │
                ▼                   ▼
             Session              Engine
                │                   │
                └─────────┬─────────┘
                          ▼
                 SQLite / PostgreSQL /
                     MariaDB ...
```

而对于你未来的“主力量化框架”，最终建议演化为：

```text
                  Freqtrade
                     │
       ┌─────────────┴─────────────┐
       │                           │
       ▼                           ▼
Official Trading DB         External Platform DB
(PostgreSQL)                (your own schema)
       │                           │
       ├── trades                  ├── portfolio
       ├── orders                  ├── risk
       ├── pairlocks               ├── events
       ├── custom_data             ├── analytics
       └── bot state               ├── model metadata
                                   └── cross-bot state

                         +

                   Market Data Lake
                 Parquet / S3 / TSDB
```

这套边界既保留 Freqtrade 官方框架的稳定性，又给你未来增加：

```text
多策略
多 Bot
多交易所
组合风险
事件系统
模型
机器学习
研究平台
统一 Dashboard
```

留下非常大的空间。

---

# 121. 实战推荐路线

如果你准备真正把 Freqtrade 当主力框架，我建议数据库学习顺序严格按下面执行：

```text
阶段 1
SQLite
↓
用 sqlite3 / DB Browser 打开 tradesv3.sqlite
↓
观察 tables

阶段 2
读 models.py
↓
理解 Engine / Session / scoped_session

阶段 3
读 trade_model.py
↓
理解 Trade / Order relationship

阶段 4
实际跑一笔 Dry-run
↓
观察 Trade 和 Order 的变化

阶段 5
手动查询数据库
↓
把日志和数据库状态对应起来

阶段 6
研究 migration
↓
理解版本升级

阶段 7
迁移 PostgreSQL
↓
实战 convert-db

阶段 8
多个 Bot
↓
一个 PostgreSQL server
多个 DB

阶段 9
建立独立 Analytics DB

阶段 10
再考虑 Event Store / Portfolio DB
```

这比一开始就自己修改 Persistence 层要安全得多。

---

# 122. 数据库源码阅读任务清单

建议你真正打开源码后完成以下任务：

```text
[ ] 找到 init_db()
[ ] 找到 create_engine()
[ ] 找到 scoped_session()
[ ] 找到 Trade.session
[ ] 找到 Order.session
[ ] 找到 ModelBase.metadata.create_all()
[ ] 找到 check_migrate()
[ ] 找到 Trade class
[ ] 找到 Order class
[ ] 找到 PairLock class
[ ] 找到 CustomDataWrapper
[ ] 找到 KeyValueStore
[ ] 找到 WalletHistory
[ ] 找到 Trade.commit()
[ ] 找到 Order.update_from_ccxt_object()
[ ] 找到 Trade.get_open_trades()
[ ] 找到 get_trades_proxy()
[ ] 找到 Backtesting 的 LocalTrade
[ ] 找到 FreqUI 查询 Trade 的 RPC 路径
[ ] 找到 bot restart 时如何恢复 open trades
[ ] 找到订单同步后在哪里 commit
```

全部完成以后，Persistence 这一层基本就不会再是黑盒。

---

# 123. 官方源码与文档索引

以下链接建议长期保留：

1. [Freqtrade 官方文档](https://docs.freqtrade.io/en/latest/)
2. [Freqtrade GitHub](https://github.com/freqtrade/freqtrade)
3. [persistence/base.py](https://raw.githubusercontent.com/freqtrade/freqtrade/develop/freqtrade/persistence/base.py)
4. [persistence/models.py](https://raw.githubusercontent.com/freqtrade/freqtrade/develop/freqtrade/persistence/models.py)
5. [persistence/trade_model.py](https://raw.githubusercontent.com/freqtrade/freqtrade/develop/freqtrade/persistence/trade_model.py)
6. [persistence/pairlock.py](https://raw.githubusercontent.com/freqtrade/freqtrade/develop/freqtrade/persistence/pairlock.py)
7. [persistence/custom_data.py](https://raw.githubusercontent.com/freqtrade/freqtrade/develop/freqtrade/persistence/custom_data.py)
8. [persistence/key_value_store.py](https://raw.githubusercontent.com/freqtrade/freqtrade/develop/freqtrade/persistence/key_value_store.py)
9. [persistence/migrations.py](https://raw.githubusercontent.com/freqtrade/freqtrade/develop/freqtrade/persistence/migrations.py)
10. [Trade Object](https://github.com/freqtrade/freqtrade/blob/develop/docs/trade-object.md)
11. [Strategy Advanced](https://docs.freqtrade.io/en/latest/strategy-advanced/)
12. [Advanced Setup / Multiple Instances / Other DB](https://docs.freqtrade.io/en/latest/advanced-setup/)
13. [convert-db](https://github.com/freqtrade/freqtrade/blob/develop/docs/commands/convert-db.md)
14. [Backtesting source](https://github.com/freqtrade/freqtrade/blob/develop/freqtrade/optimize/backtesting.py)
15. [RPC source](https://github.com/freqtrade/freqtrade/blob/develop/freqtrade/rpc/rpc.py)

---

# 124. 本卷最终认知模型

最后把整个数据库系统压缩成一句话：

> **Freqtrade 的数据库不是“存交易记录的 SQLite 文件”，而是一套基于 SQLAlchemy ORM 的 Trading State Persistence Layer；它通过 Trade、Order、PairLock、CustomData、KeyValueStore、WalletHistory 等模型保存 bot 的可恢复运行状态，并通过 Session / Engine / Migration 把这些领域状态映射到具体关系数据库。**

理解这一点之后，你后面看到：

```text
SQLite
PostgreSQL
MariaDB
convert-db
Trade.set_custom_data()
PairLock
Order
Session
scoped_session
migration
```

就不会再把它们看成零散功能，而会看到一条完整的系统链：

```text
Trading Domain
      ↓
Persistence Model
      ↓
ORM
      ↓
Session
      ↓
Engine
      ↓
Dialect / Driver
      ↓
Database
      ↓
Durable State
      ↓
Restart / Recovery / API / FreqUI
```

这才是 Freqtrade 数据库真正的全貌。

---

## 附：重要版本说明

本卷基于 2026 年当前可见的 Freqtrade `develop` 源码与官方文档进行整理。Freqtrade 持续更新，因此具体字段、迁移逻辑、默认值和接口签名可能随版本变化。生产环境应以实际部署的 Freqtrade 版本、Docker image tag 和源码 commit 为准。
