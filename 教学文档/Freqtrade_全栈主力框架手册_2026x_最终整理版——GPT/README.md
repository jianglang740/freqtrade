# Freqtrade 全栈主力框架手册 2026.x

这套资料按 **“能运行 → 看懂运行时 → 看懂源码 → 看懂持久化 → 能部署 → 能扩展”** 的路线整理，目标是把 Freqtrade 从策略框架提升为长期可维护的主力交易基础设施。

> **版本基线（2026-08-20）：** 以当前官方 `develop` 源码和官方最新文档为校验基线。当前官方发布线可见 `2026.7`。生产环境请以实际 Docker tag、`freqtrade -V`、Git commit 与对应版本文档为最终依据。

## 文档目录

1. [第一卷：全栈主手册](./Freqtrade_全栈主力框架手册_2026x.md)
   - 总体架构、数据流、运行模式
   - Strategy V3、DataFrame、DataProvider
   - 多周期、Pairlist、Protection
   - 回测、Hyperopt、lookahead、recursive analysis
   - FreqUI、REST API、Telegram、Webhook
   - Docker、VPS、多 bot、统一 FreqUI
   - Producer/Consumer、监控、升级、恢复

2. [第二卷：源码级深入教程](./02_源码级深入教程_第二卷.md)
   - Worker / FreqtradeBot
   - StrategyResolver / DataProvider / Exchange
   - callbacks、Trade / Order 状态链
   - RPC / FreqUI / Producer-Consumer

3. [第三卷：调用链与状态机](./03_源码级深入教程_第三卷_调用链与状态机.md)
   - 文件 → 类 → 方法 → caller/callee
   - 启动生命周期、交易生命周期
   - 入场、退出、订单、异常、恢复
   - 源码 Debug 路线

4. [第四卷：数据库深入拆解](./04_数据库深入拆解_ORM_事务_迁移与数据库扩展.md)
   - SQLAlchemy Engine / Session / scoped_session
   - Trade / Order / PairLock / CustomData / KeyValueStore / WalletHistory
   - 事务、索引、并发、迁移、备份恢复
   - SQLite / PostgreSQL / MariaDB
   - 自定义数据库后端的可行架构与扩展路线

5. [总体架构图](./freqtrade_architecture.png)

## Markdown 代码块规范

所有代码示例统一使用带语言标识的 fenced code block。

```python
class Example:
    pass
```

```bash
freqtrade trade -c user_data/config.json
```

```json
{
  "dry_run": true
}
```

```sql
SELECT * FROM trades;
```

## 官方校验入口

- [官方文档](https://docs.freqtrade.io/en/latest/)
- [Start the bot](https://docs.freqtrade.io/en/latest/bot-usage/)
- [Strategy callbacks](https://docs.freqtrade.io/en/latest/strategy-callbacks/)
- [Advanced Strategy](https://docs.freqtrade.io/en/latest/strategy-advanced/)
- [FreqUI](https://docs.freqtrade.io/en/latest/freq-ui/)
- [REST API](https://docs.freqtrade.io/en/latest/rest-api/)
- [Advanced setup / databases / multiple instances](https://docs.freqtrade.io/en/latest/advanced-setup/)
- [Producer / Consumer](https://docs.freqtrade.io/en/latest/producer-consumer/)
- [Lookahead analysis](https://docs.freqtrade.io/en/latest/lookahead-analysis/)
- [Recursive analysis](https://docs.freqtrade.io/en/latest/recursive-analysis/)
- [GitHub](https://github.com/freqtrade/freqtrade)

## 校对原则

本次最终整理主要修正：版本/源码基线、过时文档链接、PostgreSQL 驱动 URL、dry-run 数据库说明、webserver 能力表述、callback 列表遗漏，以及旧版 Markdown 中未加语言标识的代码示例和转义符问题。
