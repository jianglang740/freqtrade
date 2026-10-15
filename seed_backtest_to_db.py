#!/usr/bin/env python
"""
把回测导出的逐笔交易灌进 dry-run 数据库，让 APP 有历史数据可以看。

为什么需要这个：回测结果只存在 zip 里，而 freqtrade 的 REST API（APP 的数据源）
读的是 SQLite 里的 trades / orders 两张表。两者格式不一样，所以要转一次。

用法：

    # 先看清楚会发生什么（不写库）
    python seed_backtest_to_db.py --config user_data/config/config_grid_1h.json --dry-run

    # 真灌
    python seed_backtest_to_db.py --config user_data/config/config_grid_1h.json

    # 指定 zip（默认取 backtest_results/ 里最新的那个）
    python seed_backtest_to_db.py --config ... --zip user_data/backtest_results/xxx.zip

    # 每个策略一份配置、一个库 —— 换策略就换 --config，别手改脚本
    python seed_backtest_to_db.py --config user_data/config/config_turtle_4h.json

交易周期（Trade.timeframe，单位分钟）按 `--config` 里的 `timeframe` 推导，
需要时用 `--timeframe 4h` 覆盖。**回测包本身不记录周期**，所以只能从配置取。

⚠️ 灌库前必须停掉 freqtrade 进程，SQLite 同时读写会锁表。
   注意是停**对应那个库**的进程；网格实例跑在 tradesv3.dryrun.sqlite 上，
   灌 tradesv3.turtle.sqlite 不影响它，不用停。

═══════════════════════════════════════════════════════
几个查过源码确认的点：

* Order 表有唯一约束 (ft_pair, order_id)，所以 order_id 必须自己造且每对唯一。
* Order.ft_trade_id 外键指向 Trade.id（Trade 没有单独的 ft_trade_id 字段）。
* 时间在库里存的是「naive UTC」—— open_date_utc 属性只是补个 tzinfo，
  所以写库前要把时区摘掉，否则读出来会差 8 小时。
* 交易的开仓均价/总量/仓位由订单反推（recalc_trade_from_orders），
  所以先挂订单再让 freqtrade 自己算，保证「交易明细」和「订单列表」对得上。
* 回测导出里没有 order_id / status / order_type，这三个是补的常量。

另外还要灌 wallet_history（钱包曲线），否则 APP 的「收益曲线」「钱包历史」两个页面
是空的 —— 只有逐笔交易的话，那两个图没有点可画。曲线来源不是 trades 表，
而是回测包里的 *_wallet.feather（回测自己逐小时记的资金曲线）。
═══════════════════════════════════════════════════════
"""

import argparse
import io
import json
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from freqtrade import constants
from freqtrade.configuration import Configuration
from freqtrade.data.btanalysis import load_backtest_data
from freqtrade.enums import TradingMode
from freqtrade.exchange.exchange_utils_timeframe import timeframe_to_minutes
from freqtrade.persistence import KeyValueStore, Order, Trade, WalletHistory
from freqtrade.persistence.models import init_db

REPO = Path(__file__).resolve().parent


def 找最新回测包() -> Path:
    """取 backtest_results/ 里最新的 zip，和 .last_result.json 保持一致。"""
    last = REPO / "user_data" / "backtest_results" / ".last_result.json"
    if last.exists():
        try:
            名字 = json.loads(last.read_text())["latest_backtest"]
            路径 = last.parent / 名字
            if 路径.exists():
                return 路径
        except (KeyError, json.JSONDecodeError):
            pass

    候选 = sorted((REPO / "user_data" / "backtest_results").glob("*.zip"), key=lambda p: p.stat().st_mtime)
    if not 候选:
        sys.exit("❌ backtest_results/ 里没有 zip，先跑一次回测（要带 --export trades）")
    return 候选[-1]


def 读包里的策略名(zip路径: Path) -> str:
    """zip 里就是 JSON，直接从里面读策略名，免得手填。"""
    with zipfile.ZipFile(zip路径) as z:
        主文件 = [n for n in z.namelist() if n.endswith(".json") and "config" not in n][0]
        with z.open(主文件) as f:
            策略名列表 = list(json.load(f)["strategy"].keys())
    if len(策略名列表) != 1:
        sys.exit(f"❌ 包里不止一个策略：{策略名列表}，请手动指定")
    return 策略名列表[0]


def 读钱包曲线(zip路径: Path) -> pd.Series:
    """
    从回测包里读逐小时资金曲线，压成日线。

    为什么压成日线：实盘那边 record_wallet_state 是每天 00:07 记一条
    （freqtradebot.py:197），表上还有 (timestamp, currency) 唯一约束。
    保持同样的粒度，APP 那边不用区分两种数据。

    column 名来自回测导出的 *_{策略名}_wallet.feather：
    date / currency / rate / balance / total_quote
    """
    with zipfile.ZipFile(zip路径) as z:
        候补 = [n for n in z.namelist() if n.endswith("_wallet.feather")]
        if not 候补:
            sys.exit(f"❌ {zip路径.name} 里没有 *_wallet.feather，回测导出时是不是没记钱包曲线？")
        with z.open(候补[0]) as f:
            df = pd.read_feather(io.BytesIO(f.read()))

    曲线 = df.set_index("date")["total_quote"].sort_index()
    曲线 = 曲线.resample("1D").last().dropna()
    # feather 里是带时区的，库里存的是 naive UTC，统一摘掉时区再往外交
    if 曲线.index.tz is not None:
        曲线.index = 曲线.index.tz_convert("UTC").tz_localize(None)
    return 曲线


def 转naive_utc(值) -> datetime:
    """pandas 的时间戳 → 库里存的 naive UTC。"""
    if 值 is None or (isinstance(值, float) and pd.isna(值)):
        return None
    ts = pd.Timestamp(值)
    if ts.tzinfo is not None:
        ts = ts.tz_convert("UTC").tz_localize(None)
    return ts.to_pydatetime()


def 毫秒转naive_utc(毫秒) -> datetime:
    return datetime.fromtimestamp(毫秒 / 1000, tz=timezone.utc).replace(tzinfo=None)


def main() -> None:
    p = argparse.ArgumentParser(description="把回测逐笔交易灌进 freqtrade 的 dry-run 数据库")
    p.add_argument("--config", default="user_data/config/config_grid_1h.json", help="配置文件（用来拿 db_url）")
    p.add_argument("--zip", default=None, help="回测 zip，默认取最新的")
    p.add_argument("--db-url", default=None, help="覆盖数据库地址，默认按配置里的 dry_run 推导")
    p.add_argument("--timeframe", default=None, help="覆盖交易周期，默认按配置里的 timeframe 推导")
    p.add_argument("--dry-run", action="store_true", help="只显示要写什么，不真写")
    args = p.parse_args()

    配置路径 = Path(args.config)
    if not 配置路径.is_absolute():
        配置路径 = REPO / 配置路径
    config = Configuration.from_files([str(配置路径)])

    # Configuration.from_files 不会设 db_url（那步 _process_trading_options 只在交易 runmode 跑），
    # 所以这里按 freqtrade 自己的规则补上，见 configuration.py:149-157
    db_url = args.db_url or config.get("db_url")
    if not db_url or db_url == constants.DEFAULT_DB_PROD_URL:
        db_url = (
            constants.DEFAULT_DB_DRYRUN_URL if config.get("dry_run", False)
            else constants.DEFAULT_DB_PROD_URL
        )
    # 相对路径锚定到仓库根目录 —— 机器人是从仓库根跑的，脚本从哪儿跑都得指向同一个库
    if db_url.startswith("sqlite:///") and not db_url.startswith("sqlite:////"):
        库里路径 = Path(db_url[len("sqlite:///"):])
        if not 库里路径.is_absolute():
            db_url = "sqlite:///" + str(REPO / 库里路径)

    zip路径 = Path(args.zip) if args.zip else 找最新回测包()
    if not zip路径.is_absolute():
        zip路径 = REPO / zip路径

    策略名 = 读包里的策略名(zip路径)
    df = load_backtest_data(str(zip路径), strategy=策略名)

    # 起始资金 = 配置里的 dry_run_wallet。机器人启动时也是拿这个数当本金，
    # 所以用它推出来的结束余额会和 /balance 接口给的数字一致。
    起始资金 = float(config["dry_run_wallet"])

    # Trade.timeframe 存的是**分钟数**（APP 拿它显示「1h」「4h」）。
    # 从配置的 timeframe 推导，别再写死 —— 我们已经有 1h 和 4h 两个实例了。
    # --timeframe 收的是周期字符串（"1h"/"4h"），不是分钟数，免得两种口径混用。
    周期分钟 = timeframe_to_minutes(args.timeframe or config["timeframe"])

    print(f"配置文件 : {配置路径.relative_to(REPO)}")
    print(f"数据库   : {db_url}")
    print(f"回测包   : {zip路径.name}")
    print(f"策略     : {策略名}")
    周期来源 = "命令行覆盖" if args.timeframe else f"配置 {config['timeframe']}"
    print(f"周期     : {周期分钟} 分钟（{周期来源}）")
    print(f"交易数   : {len(df)}")
    print(f"合计盈亏 : {df['profit_abs'].sum():+.2f} {config['stake_currency']}")
    print(f"账户余额 : {起始资金:.2f} → {起始资金 + df['profit_abs'].sum():.2f} {config['stake_currency']}")

    # 时间排序，让 trade id 和开仓时间一致
    df = df.sort_values("open_date").reset_index(drop=True)

    时间字段 = ["open_date", "close_date"]
    for 列 in 时间字段:
        df[列] = df[列].apply(转naive_utc)

    if args.dry_run:
        print("\n--- --dry-run，以下只是预览，没有写库 ---")
        r = df.iloc[0]
        print(f"首笔：{r['pair']} 开 {r['open_date']} @ {r['open_rate']} "
              f"平 {r['close_date']} @ {r['close_rate']} 盈亏 {r['profit_abs']:+.2f}")
        print(f"      订单 {len(r['orders'])} 个，enter_tag={r['enter_tag']!r} exit_reason={r['exit_reason']!r}")
        for i, o in enumerate(r["orders"]):
            print(f"      [{i}] {o['ft_order_side']:4s} is_entry={o['ft_is_entry']} "
                  f"price={o['safe_price']:.2f} amount={o['amount']} tag={o['ft_order_tag']!r}")

        订单总数 = sum(len(r["orders"]) for r in df.to_dict("records"))
        print(f"\n将要写入：{len(df)} 笔交易 + {订单总数} 个订单")

        曲线 = 读钱包曲线(zip路径)
        print(f"将要写入：{len(曲线)} 天钱包曲线 "
              f"{曲线.index.min().date()} → {曲线.index.max().date()} "
              f"({曲线.iloc[0]:.2f} → {曲线.iloc[-1]:.2f})")
        return

    init_db(db_url)

    已存在 = Trade.session.query(Trade).count()
    if 已存在:
        sys.exit(f"❌ 库里已经有 {已存在} 笔交易了。先备份并清空再灌，别直接覆盖：\n"
                 f"   sqlite3 {db_url.replace('sqlite:///', '')} 'DELETE FROM orders; DELETE FROM trades;'")

    订单号 = 1
    写入 = []
    for 序号, r in enumerate(df.to_dict("records"), start=1):
        交易 = Trade(
            id=序号,
            pair=r["pair"],
            stake_amount=float(r["stake_amount"]),
            amount=float(r["amount"]),
            open_date=r["open_date"],
            open_rate=float(r["open_rate"]),
            open_rate_requested=float(r["open_rate"]),
            close_date=r["close_date"],
            close_rate=float(r["close_rate"]),
            close_rate_requested=float(r["close_rate"]),
            fee_open=float(r["fee_open"]),
            fee_close=float(r["fee_close"]),
            is_open=False,
            enter_tag=r["enter_tag"] or None,
            exit_reason=r["exit_reason"],
            leverage=float(r["leverage"]),
            is_short=bool(r["is_short"]),
            exchange="binance",
            strategy=策略名,
            timeframe=周期分钟,   # 单位分钟，由配置的 timeframe 推导（1h→60、4h→240）
            trading_mode=TradingMode.FUTURES,
            max_rate=float(r["max_rate"]),
            min_rate=float(r["min_rate"]),
            close_profit=float(r["profit_ratio"]),
            close_profit_abs=float(r["profit_abs"]),
            amount_requested=float(r["amount"]),
            stop_loss=float(r["stop_loss_abs"]),
            initial_stop_loss=float(r["initial_stop_loss_abs"]),
            funding_fees=float(r["funding_fees"]) if r.get("funding_fees") is not None else None,
        )

        for i, o in enumerate(r["orders"]):
            成交时间 = 毫秒转naive_utc(o["order_filled_timestamp"])
            单价 = float(o["safe_price"])
            数量 = float(o["amount"])
            # 回测导出里没有 order_id / status / order_type，这三个是补的常量
            订单 = Order(
                id=订单号,
                ft_trade_id=序号,
                ft_pair=r["pair"],
                ft_is_open=False,
                order_id=f"bt{序号}-{i}",
                symbol=r["pair"],
                ft_order_side=o["ft_order_side"],
                side=o["ft_order_side"],
                order_type="market",
                status="closed",
                order_date=成交时间,
                order_filled_date=成交时间,
                order_update_date=成交时间,
                ft_price=单价,
                ft_amount=数量,
                price=单价,
                average=单价,
                amount=数量,
                filled=数量,
                remaining=0.0,
                cost=float(o["cost"]),
                ft_order_tag=o["ft_order_tag"] or None,
            )
            交易.orders.append(订单)
            订单号 += 1

        # 先让 freqtrade 从订单反推开仓均价/总量，保证「交易明细」和「订单列表」对得上
        交易.recalc_trade_from_orders()
        # 再用回测的权威数字覆盖平仓侧。
        # 为什么必须覆盖：recalc_trade_from_orders 会用订单逐笔重算平仓盈亏，
        # 口径和回测记录的汇总值有手续费差异（实测全样本差 223.81 USDT）。
        # 订单是权威的（明细页要看），盈亏也是权威的（回测就是这么记的）——
        # 让 recalc 管开仓侧、回测管平仓侧，两边各司其职。
        交易.is_open = False
        交易.close_date = r["close_date"]
        交易.close_rate = float(r["close_rate"])
        交易.close_rate_requested = float(r["close_rate"])
        交易.close_profit = float(r["profit_ratio"])
        交易.close_profit_abs = float(r["profit_abs"])
        # realized_profit 也是 recalc 派生的（trade_model.py:1322），必须一起覆盖，
        # 否则 API 里 realized_profit 和 profit_abs 会显示两个不同的数，
        # 钱包历史也会按 realized_profit 算出一个对不上的余额。
        交易.realized_profit = float(r["profit_abs"])
        交易.exit_reason = r["exit_reason"]

        # 手续费字段。真实平仓交易是成交时由 update_fee 写的（trade_model.py:993-1002），
        # 而 recalc_trade_from_orders 只在「持仓还没平」时才补 fee_open_cost（:1338），
        # 所以走回测路子的平仓交易这两个字段会留空 —— APP 交易详情页会显示成「—」。
        # 口径照 update_fee：手续费成本 = 该方向所有订单成交额 × 费率，合约的手续费以计价币结算。
        交易.fee_open_cost = (
            sum(float(o["cost"]) for o in r["orders"] if o["ft_is_entry"]) * float(r["fee_open"])
        )
        交易.fee_open_currency = config["stake_currency"]
        交易.fee_close_cost = (
            sum(float(o["cost"]) for o in r["orders"] if not o["ft_is_entry"]) * float(r["fee_close"])
        )
        交易.fee_close_currency = config["stake_currency"]
        写入.append(交易)

    for 交易 in 写入:
        Trade.session.add(交易)
    Trade.commit()

    # ── 写完自查 ──
    库里笔数 = Trade.session.query(Trade).count()
    库里订单 = Order.session.query(Order).count()
    库里盈亏 = sum(t.close_profit_abs or 0 for t in Trade.session.query(Trade).all())

    print("\n--- 写入完成 ---")
    print(f"交易 {库里笔数} 笔（回测 {len(df)} 笔）")
    print(f"订单 {库里订单} 个（回测 {sum(len(r['orders']) for r in df.to_dict('records'))} 个）")
    print(f"合计盈亏 {库里盈亏:+.2f}（回测 {df['profit_abs'].sum():+.2f}）")

    对不上 = []
    if 库里笔数 != len(df):
        对不上.append("交易数")
    if abs(库里盈亏 - df["profit_abs"].sum()) > 0.01:
        对不上.append("盈亏合计")
    if 对不上:
        print(f"❌ 对不上：{'、'.join(对不上)}")
        sys.exit(1)
    print("✅ 核对一致")

    # ── 钱包曲线 ──
    曲线 = 读钱包曲线(zip路径)
    # 曲线最后一点用「交易反推出的余额」，这样和 /balance 接口给的数字完全一致。
    # （回测 feather 的收盘值是 6239.92，交易反推是 6241.01，差 1.09 —— 手续费口径的
    #   老问题，前面注释里说过。让曲线和 /balance 对齐比让曲线内部自洽更重要，
    #   否则 APP 上「当前余额」和曲线终点会对不上，看起来像 bug。）
    结束余额 = 起始资金 + float(df["profit_abs"].sum())
    结束日 = pd.Timestamp.now(tz="UTC").tz_localize(None).normalize()   # 今天 00:00（naive UTC）
    if 结束日 <= 曲线.index.max():
        结束日 = 曲线.index.max()
    曲线 = pd.concat([曲线[曲线.index < 结束日], pd.Series([结束余额], index=[结束日])])

    # 和 trades 一样，重灌前先确认是空的，不覆盖
    已存曲线 = WalletHistory.session.query(WalletHistory).count()
    if 已存曲线:
        sys.exit(f"❌ 库里已经有 {已存曲线} 条钱包记录了。先清空：\n"
                 f"   sqlite3 {db_url.replace('sqlite:///', '')} 'DELETE FROM wallet_history;'")

    for 日, 余额 in 曲线.items():
        WalletHistory.session.add(
            WalletHistory(
                timestamp=转naive_utc(日),
                currency=config["stake_currency"],
                quote_currency=config["stake_currency"],
                rate=1.0,          # 计价币对自己永远是 1
                balance=float(余额),
                total_quote=float(余额),
                leverage=1.0,
                bot_managed=True,
            )
        )

    # 告诉机器人「钱包迁移已经做过了，别再跑一遍」。
    # 跑的话它会按自己的口径在「今天」插一条，和上面最后一条撞唯一约束
    # (timestamp, currency)，虽然只是记条错误日志，但余额也会对不上，所以直接挡住。
    # wallet_history_migration_date 是 APP 用来在曲线上画「采集起点」竖线的
    # （rpc.py:813 → capture_start_ts），这里设成现在 = 人工灌的历史到此为止。
    KeyValueStore.store_value("wallet_history_migration", 1)
    KeyValueStore.store_value("wallet_history_migration_date", datetime.now(timezone.utc).replace(tzinfo=None))
    WalletHistory.session.commit()

    print(f"\n钱包曲线 {len(曲线)} 天：{曲线.index.min().date()} → {曲线.index.max().date()}")
    print(f"起始 {曲线.iloc[0]:.2f} → 结束 {曲线.iloc[-1]:.2f} {config['stake_currency']}")
    if abs(曲线.iloc[-1] - 结束余额) > 0.01 or abs(结束余额 - (起始资金 + df["profit_abs"].sum())) > 0.01:
        print("❌ 曲线终点和交易合计对不上")
        sys.exit(1)
    print("✅ 核对一致")


if __name__ == "__main__":
    main()
