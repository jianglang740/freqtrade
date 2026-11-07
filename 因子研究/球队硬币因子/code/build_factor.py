#!/usr/bin/env python
"""
「球队/硬币」因子的币圈复现 —— 因子构建。

来源：方正证券《个股动量效应的识别及"球队硬币"因子构建》（曹春晓，2022-06）。
原文逻辑（A 股月度因子）：

    同一只股票的收益率有两种状态：
      硬币型 —— 换手率变化低于截面均值（低关注、信息少），当日收益应当**反转**
      球队型 —— 换手率变化高于截面均值（高关注、有共同信息），收益应当**延续**

    同一只股票的波动率也有两种状态：
      20 日波动率低于截面均值 → 硬币型 → 翻转它的收益
      高于截面均值           → 球队型 → 保持原样

    三个价格维度（日间 / 日内 / 隔夜）各自做「波动翻转」和「换手翻转」，
    两者等权得到修正因子，三个维度再等权得到最终的「球队硬币」因子。
    因子方向为负（研报做法：买因子值最低的一组）。

──────────────────────────────────────────────────────────────
币圈适配（两处，都是结构差异决定的，不是调参）
──────────────────────────────────────────────────────────────

1. 换手率代理
   A 股的 turnover_rate = 成交量 / 流通股本。加密货币没有流通股本（至少本数据湖没有），
   但「成交额相对自身常态的倍数」是同构的量：流通股本在日频上近似不变，
   所以 turnover_rate ∝ 成交量。取

       TO_t   = quote_volume_t / mean(quote_volume_{t-20..t-1})     （ADV 严格不含今天）
       ΔTO_t  = TO_t − TO_{t-1}

   分母用滚动 20 日均值而不是固定常数，等价于假设「常态成交额」随时间缓慢漂移，
   避免新币早期成交额量级差异把 ΔTO 的截面分布拉歪。
   稳健性口径（`--turnover-proxy logchange`）：ΔTO_t = log(qv_t / qv_{t-1})。

2. 隔夜窗口
   A 股隔夜收益 = 昨收 → 今开（约 18 小时非交易时段，含集合竞价）。
   币圈 24/7 连续交易，1d bar 的 open 就是上一根 1h bar 的 close，
   直接套公式的话 D_overnight 只剩一个 tick 的噪声。所以用 1h 线把 UTC 一天切成

       隔夜（off-hours）：00:00–08:00 UTC    →  R_overnight = P(08:00) / close_{t-1} − 1
       日内（active）   ：08:00–24:00 UTC    →  R_intraday  = close_t   / P(08:00)   − 1
       日间（全天）     ：                   →  R_interday  = close_t   / close_{t-1} − 1

   三个维度保持原文关系 R_interday ≈ (1+R_on)(1+R_intra) − 1，且「隔夜在前」的顺序不变。
   08:00 这个切点来自全市场小时成交额分布：03–06 点最低（3.1%/小时），
   00–08 点合计只占全天 28.6%，是全样本的结构性事实，与因子表现无关。
   `--off-hour` 可换成 12 做稳健性检查。

3. 无前视
   - 换手翻转里，隔夜维度用的是 **t−1 日** 的 ΔTO（`dto.shift(1)`），
     因为 00:00–08:00 这段收益发生在 t 日 08:00 之前，而 t 日的全天成交额要到 24:00 才知道。
     原文同样用 t−1 的换手信息翻转 t 日的隔夜收益。
   - 因子在月底取值后按符号前向填充（ffill），T 月底的值从 T+1 起才可见。

产物：out/factor_{market}_1d.parquet
    列 = date / symbol / 13 个因子列（与研报一一对应）

用法：
    python 因子研究/球队硬币因子/code/build_factor.py --market um
    python 因子研究/球队硬币因子/code/build_factor.py --market spot --off-hour 12
"""

from __future__ import annotations

import argparse
import os
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))          # …/球队硬币因子/code
PROJECT = os.path.dirname(HERE)                            # …/球队硬币因子
FACTOR_ROOT = os.path.dirname(PROJECT)                     # 因子研究
for _p in (os.path.join(FACTOR_ROOT, "行情数据"),
           os.path.join(FACTOR_ROOT, "因子数据")):
    sys.path.insert(0, _p)

import lake_io       # noqa: E402
import factor_io     # noqa: E402

WINDOW = 20                # 滚动窗口：过去 20 个交易日
ADV_MIN_PERIODS = 10       # 换手率代理的 ADV 至少要有 10 天
OFF_HOUR = 8               # 隔夜窗口结束时刻（UTC 小时）

# 与研报 4.1 节完全一致的 13 个因子列
FCT_COLS = [
    "fct_interday_raw", "fct_interday_volflip", "fct_interday_turnflip", "fct_interday_modified",
    "fct_intraday_raw", "fct_intraday_volflip", "fct_intraday_turnflip", "fct_intraday_modified",
    "fct_overnight_raw", "fct_overnight_volflip", "fct_overnight_turnflip", "fct_overnight_modified",
    "fct_team_coin",
]

_t0 = time.time()


def log(msg: str) -> None:
    print(f"[{time.time() - _t0:6.1f}s] {msg}", flush=True)


# ══════════════════════════════════════════════ 数据
def load_daily(market: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """日线：收盘价 + 成交额（宽表，index=UTC 日, columns=symbol）。"""
    df = lake_io.load(market, "1d", columns=["symbol", "date", "close", "quote_volume"])
    if df.empty:
        raise RuntimeError(f"{market}/1d 没有数据")
    cl = lake_io.wide(df, "close")
    qv = lake_io.wide(df, "quote_volume")
    return cl, qv


def load_session_price(market: str, off_hour: int) -> pd.DataFrame:
    """每个 (symbol, UTC 日) 在 off_hour 时刻的价格 = 最后一根 hour < off_hour 的 1h 收盘。

    用「08:00 前最后一根」而不是死取 07:00：新上市/停牌的币可能缺个别小时线，
    取最后一根可成交价更稳，且不影响正常日子（正常日子就是 07:00 那根）。
    """
    df = lake_io.load(market, "1h", columns=["symbol", "date", "close"])
    if df.empty:
        raise RuntimeError(f"{market}/1h 没有数据")
    df = df[df["date"].dt.hour < off_hour]
    df = df.assign(day=df["date"].dt.floor("D"))
    s = (df.sort_values("date", kind="stable")
           .groupby(["symbol", "day"], observed=True)["close"].last())
    return s.unstack("symbol")


# ══════════════════════════════════════════════ 因子
def build(market: str, off_hour: int = OFF_HOUR,
          turnover_proxy: str = "adv") -> pd.DataFrame:
    cl, qv = load_daily(market)
    log(f"{market}: 日线 {cl.shape[0]} 天 × {cl.shape[1]} 符号")

    sess = load_session_price(market, off_hour).reindex(index=cl.index, columns=cl.columns)
    # 覆盖率要相对「当天有日线的格子」算：宽表是整块矩形，大量空格是还没上市
    cover = float((sess.notna() & cl.notna()).sum().sum() / cl.notna().sum().sum())
    log(f"{market}: 隔夜窗口结束于 {off_hour:02d}:00 UTC，"
        f"有日线的格子中 {cover:.2%} 能取到隔夜分界价")

    # ── Step 1: 三个价格维度 + 换手率变化量 ────────────────────────────
    prev_cl = cl.shift(1)
    r_inter = cl / prev_cl - 1.0                 # 日间：昨收 → 今收
    r_intra = cl / sess - 1.0                    # 日内：今 08:00 → 今收
    r_on = sess / prev_cl - 1.0                  # 隔夜：昨收 → 今 08:00

    qv_pos = qv.where(qv > 0)                    # 0 成交额没有意义
    if turnover_proxy == "adv":
        adv = qv_pos.shift(1).rolling(WINDOW, min_periods=ADV_MIN_PERIODS).mean()
        to = qv_pos / adv
    elif turnover_proxy == "logchange":
        to = np.log(qv_pos)                      # Δlog(qv) 直接当 ΔTO
    else:
        raise ValueError(f"未知换手代理：{turnover_proxy}")
    dto = to - to.shift(1)                       # ΔTO_t = TO_t − TO_{t−1}

    # ── Step 1.5: 截面均值与「距离」 ────────────────────────────────────
    mkt_dto = dto.mean(axis=1)                   # ΔTO_t 的截面均值
    mkt_dto_lag = dto.shift(1).mean(axis=1)      # ΔTO_{t−1} 的截面均值
    mkt_ron = r_on.mean(axis=1)                  # 隔夜收益截面均值

    d_on = r_on.sub(mkt_ron, axis=0).abs()       # 隔夜距离：|R_on − 截面均值|
    d_turn_lag = dto.shift(1).sub(mkt_dto_lag, axis=0).abs()   # 换手距离（t−1）
    mkt_d_turn_lag = d_turn_lag.mean(axis=1)

    # ── Step 2: 换手翻转（硬币型 → 反转当日收益） ───────────────────────
    coin_inter = dto.lt(mkt_dto, axis=0)
    coin_intra = coin_inter.copy()
    coin_on = d_turn_lag.lt(mkt_d_turn_lag, axis=0)
    r_inter_flip = r_inter.where(~coin_inter, -r_inter)
    r_intra_flip = r_intra.where(~coin_intra, -r_intra)
    d_on_flip = d_on.where(~coin_on, -d_on)

    # ── Step 3: 滚动 20 日 → 均值/标准差，做波动翻转 ────────────────────
    def roll(df: pd.DataFrame, how: str) -> pd.DataFrame:
        return getattr(df.rolling(WINDOW, min_periods=WINDOW), how)()

    m_inter, m_intra, m_on = roll(r_inter, "mean"), roll(r_intra, "mean"), roll(d_on, "mean")
    m_inter_f, m_intra_f, m_on_f = (roll(r_inter_flip, "mean"), roll(r_intra_flip, "mean"),
                                    roll(d_on_flip, "mean"))
    sd_inter, sd_intra, sd_on = (roll(r_inter, "std"), roll(r_intra, "std"), roll(d_on, "std"))

    # 波动率低于当日截面均值 → 硬币型 → 翻转
    def vol_mask(sd: pd.DataFrame) -> pd.DataFrame:
        return sd.lt(sd.mean(axis=1), axis=0)

    vm_inter, vm_intra, vm_on = vol_mask(sd_inter), vol_mask(sd_intra), vol_mask(sd_on)

    # 每个因子都是一张 date × symbol 的宽表，先放在 dict 里，最后再统一压成长表
    fct: dict[str, pd.DataFrame] = {
        "fct_interday_raw": m_inter,
        "fct_intraday_raw": m_intra,
        "fct_overnight_raw": m_on,                     # 隔夜维度用「距离」本身做基准
        "fct_interday_volflip": m_inter.where(~vm_inter, -m_inter),
        "fct_intraday_volflip": m_intra.where(~vm_intra, -m_intra),
        "fct_overnight_volflip": m_on.where(~vm_on, -m_on),
        "fct_interday_turnflip": m_inter_f,
        "fct_intraday_turnflip": m_intra_f,
        "fct_overnight_turnflip": m_on_f,
    }
    fct["fct_interday_modified"] = (fct["fct_interday_volflip"]
                                    + fct["fct_interday_turnflip"]) / 2.0
    fct["fct_intraday_modified"] = (fct["fct_intraday_volflip"]
                                    + fct["fct_intraday_turnflip"]) / 2.0
    fct["fct_overnight_modified"] = (fct["fct_overnight_volflip"]
                                     + fct["fct_overnight_turnflip"]) / 2.0
    fct["fct_team_coin"] = (fct["fct_interday_modified"]
                            + fct["fct_intraday_modified"]
                            + fct["fct_overnight_modified"]) / 3.0

    # ── Step 4: 月底取值 → 按符号前向填充（无前视） ─────────────────────
    # 引擎在「T 日收盘出信号、T+1 开盘建仓」，所以 T 月底的值在 T 当天就可以用；
    # ffill 保证周频/日频回测时取到的是最近一个已过去的月底值。
    month_end = pd.DatetimeIndex(pd.Series(cl.index).groupby(
        cl.index.strftime("%Y-%m")).last().sort_values())
    log(f"{market}: 月底时点 {len(month_end)} 个 → 前向填充到日频")

    # 两个**诊断用**状态变量（不是因子）：20 日波动率与换手率变化量。
    # 用来在报告里直接检验「硬币态才反转」这个机制，回测不会用到。
    fct["_sigma_inter"] = sd_inter
    fct["_dturn"] = dto
    out_cols = FCT_COLS + ["_sigma_inter", "_dturn"]

    series = []
    for name in out_cols:
        w = fct[name].replace([np.inf, -np.inf], np.nan)
        w = w.reindex(month_end).reindex(cl.index).ffill()
        # reshape 成 (date, symbol) 的 MultiIndex Series；stack 默认保留 NaN，
        # 后面统一 dropna(how="all") 清掉上市前/全空的行
        series.append(w.rename_axis(index="date", columns="symbol").stack().rename(name))

    long = pd.concat(series, axis=1).reset_index()
    long = long.dropna(subset=out_cols, how="all")
    return long.sort_values(["date", "symbol"], kind="stable").reset_index(drop=True)


# ══════════════════════════════════════════════ 诊断
def _rank_corr(a: pd.Series, b: pd.Series, dates: pd.Series) -> float:
    t = pd.DataFrame({"d": dates, "a": a, "b": b}).dropna()
    if t.empty:
        return np.nan
    return float(t.groupby("d").apply(
        lambda g: g["a"].rank().corr(g["b"].rank()) if len(g) >= 20 else np.nan
    ).mean())


def describe(panel: pd.DataFrame, market: str) -> None:
    print(f"\n  覆盖 {len(panel):,} 行 / {panel['symbol'].nunique()} 个符号 / "
          f"{panel['date'].min().date()} → {panel['date'].max().date()}")
    print("  各列有效占比 / 中位数 / 截面标准差：")
    for c in FCT_COLS:
        x = panel[c]
        print(f"    {c:<26} {x.notna().mean():6.1%}  "
              f"中位 {x.median():+.4f}  全样本std {x.std():.4f}")

    try:
        pub = pd.read_parquet(factor_io.panel_path(market, "1d"),
                              columns=["date", "symbol", "mom_7d", "rev_1d",
                                       "vol_30d", "volume_z_30d", "takerratio_30d"])
        if str(pub["symbol"].dtype) == "category":
            pub["symbol"] = pub["symbol"].astype(str)
        m = panel[["date", "symbol", "fct_team_coin", "fct_interday_modified"]].merge(
            pub, on=["date", "symbol"], how="inner")
        print("\n  与公共因子的截面秩相关（均值）—— 判断是不是旧因子的换皮：")
        for a in ("fct_team_coin", "fct_interday_modified"):
            line = " / ".join(f"{b} {_rank_corr(m[a], m[b], m['date']):+.3f}"
                              for b in ("mom_7d", "rev_1d", "vol_30d",
                                        "volume_z_30d", "takerratio_30d") if b in m)
            print(f"    {a}: {line}")
    except FileNotFoundError:
        print("  （公共因子库缺失，跳过相关性对照）")


def main() -> None:
    ap = argparse.ArgumentParser(description="构造球队硬币因子面板（币圈版）")
    ap.add_argument("--market", default="um", choices=["spot", "um"])
    ap.add_argument("--off-hour", type=int, default=OFF_HOUR,
                    help="隔夜窗口结束时刻（UTC 小时），默认 8")
    ap.add_argument("--turnover-proxy", default="adv", choices=["adv", "logchange"],
                    help="换手率代理：adv=成交额/近20日均额（默认）；logchange=对数成交额变化")
    ap.add_argument("--tag", default=None, help="输出文件名后缀，稳健性口径用")
    args = ap.parse_args()

    panel = build(args.market, args.off_hour, args.turnover_proxy)
    outdir = os.path.join(PROJECT, "out")
    os.makedirs(outdir, exist_ok=True)
    suffix = args.tag or f"h{args.off_hour}_{args.turnover_proxy}"
    path = os.path.join(outdir, f"factor_{args.market}_{suffix}.parquet")
    panel.to_parquet(path, index=False, compression="zstd")

    print(f"\n[{args.market}] 球队硬币因子 → {os.path.relpath(path, FACTOR_ROOT)} "
          f"({len(panel):,} 行)")
    describe(panel, args.market)


if __name__ == "__main__":
    main()
