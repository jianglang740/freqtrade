---
slug: taker-imbalance
name: 主动买入占比
latin: TAKER IMBALANCE
oneLine: 主动买入额占总成交额的比例，刻画瞬时买卖压力。
status: 研究中
published: true
order: 50
---

## 定义

币安归档的 K 线里自带 taker buy volume，用主动买入量除以总成交量就得到买卖压力比。它反映的是「谁在急着成交」，比单纯的价格更能提前捕捉资金流向。

## 公式

```text
imb = taker_buy_quote_volume / quote_volume
```

## 怎么用

- 与动量结合，过滤掉「靠挂单撑起来的假突破」
- 作为高频换手的辅助信号，判断突破是否有效
- 构建横截面因子，做多主动买入比例上升的币种

## 限制与坑

这个量只反映币安单一交易所、单一撮合引擎的情况，且在极短周期里噪声很大，需要做平滑。
