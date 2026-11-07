#!/usr/bin/env python
"""
加密货币单因子回测引擎。

设计上沿用 ../../fct_backtest 股票框架的四项核心方法（每日截面去极值/标准化、
日历化调仓、Fama-MacBeth + Newey-West、风格中性化），但针对币圈重写了**执行层**：

| 股票框架 | 这里 | 为什么 |
|---|---|---|
| T 日信号 → T+1 **收盘**买入 | T 日信号 → T+1 **开盘**买入（可切收盘） | 24/7 连续交易，次日开盘才是第一可成交价 |
| 一年 252 个交易日 | 一年 **365** 天，年化按真实日历天数 | 币圈没有休市 |
| 不能做空，只算多空价差 | 真正建多空组合（可切单边） | 永续合约可直接做空 |
| 不考虑成本 | 换手 × (手续费+滑点)，逐窗口扣 | 币圈换手高，这是最致命的偏差 |
| 不考虑资金费 | 永续持仓逐窗口扣/收资金费 | 多空组合空头每年能收 20% 资金费 |
| 不处理退市/停牌 | 持仓撞上退市 → 最后价平仓 + 可配 `delist_shock` | 归档含 235 个已下架币 |
| 市值 + 行业中性化 | 流动性(size) + BTC Beta (+波动率) 中性化 | 币圈没有行业，Beta 才是共同风险 |
| 固定 2×2 分组收益 | 等权/成交额/逆波动加权，逐日建权重 | 加密小币流动性差异极大 |

时间轴（无前视）：
    T 根 bar 收盘 → 因子已知
    T+1 根 bar 开盘（或收盘）→ 建仓价
    下一个调仓日 T'+1 开盘 → 平仓价
    窗口收益 = 平仓价 / 建仓价 - 1
持有 h 个调仓窗口时，用 h 个错位子组合各占 1/h 资金，得到**不重复复利**的
逐窗口真实收益（股票框架 `_overlapping_strategy_returns` 的同款思路）。
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field, asdict

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))              # 因子研究/回测框架
FACTOR_ROOT = os.path.dirname(HERE)                            # 因子研究
LAKE = os.path.join(FACTOR_ROOT, "行情数据")
FACTORS = os.path.join(FACTOR_ROOT, "因子数据")
for _p in (LAKE, FACTORS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import lake_io       # noqa: E402
import factor_io     # noqa: E402
from stats import (  # noqa: E402
    fama_macbeth, forward_returns, ic_series, newey_west_se,
    quantile_forward_means, winsorize_rows,
)

YEAR_DAYS = 365.0


# ══════════════════════════════════════════════ 配置
@dataclass
class Config:
    """一次回测的全部口径。CLI 参数一一对应。"""
    market: str = "spot"                 # spot | um
    tf: str = "1d"                       # 1d | 1h
    factor: str = "mom_30d"              # 因子面板里的列名
    # 项目自建的因子面板（date/symbol/<factor>）。留空 = 读公共因子库 panels/
    factor_panel: str | None = None

    start: str | None = None
    end: str | None = None

    # 调仓：daily/weekly/monthly 按自然日历取每期最后一根 bar；every = 每 N 根 bar
    rebalance: str = "daily"
    every: int = 1
    hold_windows: tuple = (1, 5, 10, 20)  # 持有几个调仓窗口（1 = 非重叠）

    quantiles: int = 5
    direction: str = "long_top"          # long_top | short_top（决定多腿做高值还是低值）
    mode: str = "long_short"             # long_short | long_only | short_only
    weighting: str = "equal"             # equal | adv | vol_inv
    gross_leverage: float = 1.0          # 多空合计毛敞口；单边时即多头敞口

    # 股票池
    top_n: int = 100
    adv_min: float = 1e6
    adv_lookback: int = 14               # 天
    min_history_days: int = 30           # 上市不足 N 天不进池

    # 执行与成本
    exec_price: str = "open"             # open（次日开盘）| close（次日收盘）
    fee_bps: float | None = None         # None = 现货 10bp / 永续 5bp
    slippage_bps: float = 2.0
    delist_shock: float = 0.0            # 持仓撞退市时额外扣的亏损（0 = 最后价平仓）
    use_funding: bool = True             # 仅 um 生效

    # 预处理与中性化
    mad_n: float = 5.2
    neutralize: str | None = "logadv_beta"   # None | logadv | logadv_beta | logadv_beta_vol
    beta_window: int = 30
    vol_window: int = 30
    min_obs: int = 20                    # 单期截面最少符号数（分组/IC）
    fm_min_obs: int = 10                 # 单期 FM 回归最少符号数

    def __post_init__(self):
        if self.fee_bps is None:
            self.fee_bps = 10.0 if self.market == "spot" else 5.0
        if isinstance(self.hold_windows, list):
            self.hold_windows = tuple(self.hold_windows)
        if self.market != "um":
            self.use_funding = False
        if self.neutralize in ("None", "none", ""):
            self.neutralize = None

    def as_dict(self) -> dict:
        return asdict(self)


# ══════════════════════════════════════════════ 读数据 / 调仓日历
def _pivot(df: pd.DataFrame, field: str) -> pd.DataFrame:
    return df.pivot(index="date", columns="symbol", values=field).sort_index()


def load_bars(cfg: Config) -> tuple[pd.DataFrame, pd.DatetimeIndex]:
    """读回测所需的价格列，返回 (date × symbol 的执行价宽表, bar 时间轴)。"""
    col = cfg.exec_price
    df = lake_io.load(cfg.market, cfg.tf, columns=["symbol", "date", col],
                      start=cfg.start, end=cfg.end)
    if df.empty:
        raise RuntimeError(f"{cfg.market}/{cfg.tf} 在 {cfg.start} ~ {cfg.end} 没有数据")
    px = _pivot(df, col).astype("float64")
    return px, pd.DatetimeIndex(px.index)


def build_rebalances(bar_dates: pd.DatetimeIndex, rebalance: str, every: int = 1) -> pd.DatetimeIndex:
    """把 bar 时间轴压成调仓日。daily/weekly/monthly = 每期**最后一根** bar。"""
    if rebalance == "every":
        if every < 1:
            raise ValueError("every 必须 ≥ 1")
        return bar_dates[::every]
    s = pd.Series(bar_dates)
    if rebalance == "daily":
        key = s.dt.floor("D")
    elif rebalance == "weekly":
        iso = s.dt.isocalendar()
        key = iso["year"].astype(str) + "-" + iso["week"].astype(str)
    elif rebalance == "monthly":
        key = s.dt.strftime("%Y-%m")
    else:
        raise ValueError(f"未知调仓方式：{rebalance}")
    # 必须保留 tz（bar_dates 是 UTC）；直接取 .values 会退化成 tz-naive，
    # 后面 get_indexer 全部匹配失败、调仓窗口变成 0 个。
    return pd.DatetimeIndex(s.groupby(key).last().sort_values())


def build_universe(cfg: Config, reb_dates: pd.DatetimeIndex) -> pd.DataFrame:
    """date × symbol 布尔表：点时股票池。

    三道闸门，全部只用**当日及之前**的数据：
      1. 当天有 bar（上市之后 / 下市之前）；
      2. 上市满 `min_history_days` 天（避开上市初期的价格发现）；
      3. 过去 `adv_lookback` 天日均成交额 ≥ `adv_min`，且排名前 `top_n`。
    成交额一律按**日线**算，即使回测跑在 1h 上 —— 日频流动性更稳定，
    也避免小时成交额被单根巨量 bar 干扰。
    """
    lb = cfg.adv_lookback
    daily = lake_io.load(cfg.market, "1d", columns=["symbol", "date", "quote_volume"],
                         start=cfg.start, end=cfg.end)
    if daily.empty:
        raise RuntimeError("日线为空，无法建股票池")
    qv = _pivot(daily, "quote_volume").astype("float64")
    adv = qv.rolling(lb, min_periods=max(3, lb // 2)).mean()
    rank = adv.where(adv >= cfg.adv_min).rank(axis=1, ascending=False)
    univ = rank <= cfg.top_n

    # 上市满 N 天：首根日线 + N 天
    first = qv.apply(lambda s: s.first_valid_index())
    for sym, t0 in first.items():
        if pd.isna(t0):
            univ[sym] = False
            continue
        univ.loc[univ.index < t0 + pd.Timedelta(days=cfg.min_history_days), sym] = False
    univ = univ.fillna(False)

    # 日线 → 调仓日：用上一根日线的状态（盘中不改池）
    univ = univ.reindex(univ.index.union(reb_dates)).ffill().reindex(reb_dates)
    return univ.fillna(False)


def align_entry_prices(px: pd.DataFrame, reb_dates: pd.DatetimeIndex) -> tuple[pd.DataFrame, pd.DatetimeIndex]:
    """调仓日 → 建仓 bar（下一根）。返回 (建仓价宽表, 建仓时间轴)。

    ⚠️ 返回表的**索引仍是信号日**（不是建仓日），值才是下一根 bar 的价格。
    这样因子、权重、收益全部锚在同一个「信号日」网格上，不会错位一天；
    上游如果把索引换成建仓日，因子就会用 T+1 收盘去解释 T+1 开盘的成交 —— 前视。
    """
    bar_dates = pd.DatetimeIndex(px.index)
    pos = bar_dates.get_indexer(reb_dates)
    ok = (pos >= 0) & (pos + 1 < len(bar_dates))
    entry_pos = pos[ok] + 1
    entry_times = bar_dates[entry_pos]
    P = px.iloc[entry_pos].copy()
    P.index = pd.DatetimeIndex(reb_dates[ok])
    return P, entry_times


# ══════════════════════════════════════════════ 因子 / 风格
def load_factor_at(cfg: Config, reb_dates: pd.DatetimeIndex,
                   columns: pd.Index) -> pd.DataFrame:
    """读因子面板，取调仓日截面，列对齐到行情宽表。

    `cfg.factor_panel` 非空时读项目自建面板，否则读公共因子库。
    """
    if cfg.factor_panel:
        long = factor_io.load_external(cfg.factor_panel, cfg.factor,
                                       start=cfg.start, end=cfg.end)
    else:
        long = factor_io.load_panel(cfg.market, cfg.tf, factors=[cfg.factor],
                                    start=cfg.start, end=cfg.end)
    long = long[long["date"].isin(reb_dates)]
    f = long.pivot(index="date", columns="symbol", values=cfg.factor)
    f = f.reindex(index=reb_dates, columns=columns)
    return f.astype("float64")


def preprocess_factor(f: pd.DataFrame, univ: pd.DataFrame, mad_n: float = 5.2) -> pd.DataFrame:
    """每日截面 MAD 缩尾 + Z-score，统计量只含当日**池内**符号（无全局前视）。

    与股票框架一致；池外 / 缺失值保持 NaN，不填 0 —— 填 0 会让「没有报价」
    被当成「截面均值」参与排序，币圈小币缺 bar 很常见，那会制造假信号。
    """
    f = f.where(univ)
    med = f.median(axis=1)
    mad = f.sub(med, axis=0).abs().median(axis=1)
    upper = med + mad_n * mad
    lower = med - mad_n * mad
    f = f.clip(lower=lower, upper=upper, axis=0)
    mu = f.mean(axis=1)
    sd = f.std(axis=1)
    f = f.sub(mu, axis=0).div(sd.where(sd > 0), axis=0)
    return f.where(univ)


def build_style(cfg: Config, reb_dates: pd.DatetimeIndex, columns: pd.Index) -> dict[str, pd.DataFrame]:
    """风格因子：log 成交额（size）、BTC 滚动 Beta、滚动波动率。

    币圈版本没有「行业」，共同风险主要是 Beta（所有币跟 BTC 同涨同跌）。
    """
    daily = lake_io.load(cfg.market, "1d", columns=["symbol", "date", "close", "quote_volume"],
                         start=cfg.start, end=cfg.end)
    close = _pivot(daily, "close").astype("float64")
    qv = _pivot(daily, "quote_volume").astype("float64")
    ret = close.pct_change(fill_method=None)

    adv = qv.rolling(cfg.adv_lookback, min_periods=max(3, cfg.adv_lookback // 2)).mean()
    logadv = np.log(adv.where(adv > 0))

    w = cfg.beta_window
    if "BTCUSDT" in ret.columns:
        btc = ret["BTCUSDT"]
        cov = ret.rolling(w, min_periods=max(5, w // 2)).cov(btc)
        var = btc.rolling(w, min_periods=max(5, w // 2)).var()
        beta = cov.div(var, axis=0)
    else:
        beta = pd.DataFrame(np.nan, index=ret.index, columns=ret.columns)

    vol = ret.rolling(cfg.vol_window, min_periods=max(5, cfg.vol_window // 2)).std()

    out = {}
    for name, frame in (("logadv", logadv), ("beta", beta), ("vol", vol)):
        x = frame.reindex(frame.index.union(reb_dates)).ffill().reindex(reb_dates)
        out[name] = x.reindex(columns=columns)
    return out


def neutralize_factor(f: pd.DataFrame, style: dict[str, pd.DataFrame],
                      univ: pd.DataFrame, mode: str | None,
                      min_obs: int = 10) -> pd.DataFrame:
    """逐调仓日截面回归取残差：factor ~ 1 + log(ADV) [+ Beta] [+ Vol]。"""
    if not mode:
        return f
    controls = ["logadv"]
    if mode in ("logadv_beta", "logadv_beta_vol"):
        controls.append("beta")
    if mode == "logadv_beta_vol":
        controls.append("vol")

    out = pd.DataFrame(np.nan, index=f.index, columns=f.columns)
    cols = f.columns
    for t in f.index:
        m = (univ.loc[t] & f.loc[t].notna()).to_numpy(dtype=bool)
        if m.sum() < min_obs:
            continue
        y = f.loc[t].to_numpy(dtype=float)[m]
        parts = [np.ones(m.sum())]
        ok = np.ones(m.sum(), dtype=bool)
        for c in controls:
            v = style[c].loc[t].to_numpy(dtype=float)[m]
            ok &= np.isfinite(v)
            parts.append(v)
        if ok.sum() < min_obs:
            continue
        X = np.column_stack([p[ok] for p in parts])
        y = y[ok]
        beta, *_ = np.linalg.lstsq(X, y, rcond=None)
        resid = y - X @ beta
        row = np.full(len(cols), np.nan)
        idx = np.flatnonzero(m)[ok]
        row[idx] = resid
        out.iloc[out.index.get_loc(t)] = row
    return out.where(univ)


# ══════════════════════════════════════════════ 分组 / 权重
def assign_quantiles(f: pd.DataFrame, univ: pd.DataFrame, n: int, min_obs: int) -> pd.DataFrame:
    """截面等频分组，返回 1..n。有效符号数不足的日期整行置 NaN。

    先按池内非缺失值排名，再按 `ceil(rank/count * n)` 分组 —— 比 pd.qcut 快，
    且不受重复值影响（qcut 遇大量并列会丢组）。
    """
    v = f.where(univ & f.notna())
    cnt = v.notna().sum(axis=1)
    r = v.rank(axis=1, method="first")
    q = np.ceil(r.div(cnt.replace(0, np.nan), axis=0) * n)
    q = q.where(v.notna())
    q.loc[cnt < max(min_obs, n)] = np.nan
    return q


def _leg_weights(mask: pd.DataFrame, cfg: Config, style: dict) -> pd.DataFrame:
    """一条腿内部的权重，行内归一化到 1。"""
    if cfg.weighting == "equal":
        w = mask.astype("float64")
    elif cfg.weighting == "adv":
        w = mask.astype("float64") * style["logadv"].pipe(np.exp)
    elif cfg.weighting == "vol_inv":
        w = mask.astype("float64") / style["vol"].replace(0, np.nan)
    else:
        raise ValueError(f"未知加权方式：{cfg.weighting}")
    w = w.where(mask).fillna(0.0)
    tot = w.sum(axis=1)
    return w.div(tot.replace(0, np.nan), axis=0).fillna(0.0)


def build_weights(q: pd.DataFrame, cfg: Config, style: dict) -> pd.DataFrame:
    """由分组结果建立带符号的目标权重。

    多空：多腿 +1、空腿 −1（各自腿内归一化），再整体缩放到 gross_leverage。
    单边：只保留一条腿，敞口 = gross_leverage。
    """
    n = cfg.quantiles
    top = q.eq(n)
    bot = q.eq(1)
    if cfg.direction == "short_top":
        top, bot = bot, top

    w_top = _leg_weights(top, cfg, style)
    w_bot = _leg_weights(bot, cfg, style)

    if cfg.mode == "long_only":
        W = w_top * cfg.gross_leverage
    elif cfg.mode == "short_only":
        W = -w_top * cfg.gross_leverage
    elif cfg.mode == "long_short":
        W = (w_top - w_bot) * (cfg.gross_leverage / 2.0)
    else:
        raise ValueError(f"未知模式：{cfg.mode}")
    return W.replace([np.inf, -np.inf], 0.0).fillna(0.0)


# ══════════════════════════════════════════════ 组合模拟
def _last_valid_idx(P: np.ndarray) -> np.ndarray:
    """每列最后一个有效价格所在的行号；全 NaN 列为 -1。"""
    n = P.shape[0]
    mask = np.isfinite(P)
    idx = np.where(mask, np.arange(n)[:, None], -1)
    return idx.max(axis=0)


def _smooth_weights(W_target: np.ndarray, h: int) -> np.ndarray:
    """h 个错位子组合：第 i 个窗口持有最近 h 次开仓的平均（每次 1/h 资金）。"""
    n = W_target.shape[0]
    valid = (np.abs(W_target).sum(axis=1) > 0).astype("float64")
    cumW = np.vstack([np.zeros((1, W_target.shape[1])), np.cumsum(W_target, axis=0)])
    cumV = np.concatenate([[0.0], np.cumsum(valid)])
    W = np.zeros_like(W_target, dtype="float64")
    rows = np.arange(n)
    lo = np.maximum(0, rows - h + 1)
    cnt = cumV[rows + 1] - cumV[lo]
    good = cnt > 0
    W[good] = (cumW[rows[good] + 1] - cumW[lo[good]]) / cnt[good, None]
    return W


def simulate(P: pd.DataFrame, W_target: pd.DataFrame, h: int, cfg: Config,
             entry_times: pd.DatetimeIndex,
             funding_delta: pd.DataFrame | None = None) -> pd.DataFrame:
    """把目标权重跑成逐窗口收益。

    P            : 调仓日 × 符号 的建仓价（下一根 bar 的 open/close）
    W_target     : 同一网格上的目标权重（每次调仓形成的组合）
    h            : 持有几个调仓窗口（>1 时错位建仓，资金均分）
    funding_delta: 同一网格上的区间累计资金费率（已按符号对齐）

    返回列：gross/cost/funding/net/turnover/n_long/n_short/raw_exposure/
            dead_exits/days，索引 = 窗口起始调仓日。
    """
    n, ns = P.shape
    if n < 2:
        return pd.DataFrame()
    arr = P.to_numpy(dtype="float64")
    P_use = pd.DataFrame(arr, index=P.index, columns=P.columns).ffill().to_numpy()
    last_idx = _last_valid_idx(arr)
    rows = np.arange(n)

    # 平仓价 = 该窗口结束时的建仓价；退市/停牌则用最后可成交价，并按需扣 shock
    R = P_use[1:] / P_use[:-1] - 1.0
    died = last_idx[None, :] < (rows[:-1] + 1)[:, None]   # 窗口结束前该符号已没有价格
    R = R - died * cfg.delist_shock
    entry_ok = last_idx[None, :] >= rows[:-1, None]
    R = np.where(entry_ok & np.isfinite(R), R, 0.0)

    Wt = W_target.to_numpy(dtype="float64")
    W = _smooth_weights(Wt, h)[:-1]           # 每个窗口实际持有的权重
    W = W * entry_ok
    gross = (W * R).sum(axis=1)

    # 换手：第一个窗口全部新建，之后按权重变化
    turn = np.empty(n - 1)
    turn[0] = np.abs(W[0]).sum()
    if n > 2:
        turn[1:] = np.abs(np.diff(W, axis=0)).sum(axis=1)
    cost = turn * (cfg.fee_bps + cfg.slippage_bps) / 1e4

    if funding_delta is not None:
        dF = funding_delta.reindex(index=pd.DatetimeIndex(entry_times[: n - 1]),
                                   columns=P.columns).fillna(0.0)
        funding = -(W * dF.to_numpy(dtype="float64")).sum(axis=1)
    else:
        funding = np.zeros(n - 1)

    out = pd.DataFrame({
        "gross": gross,
        "cost": cost,
        "funding": funding,
        "net": gross - cost + funding,
        "turnover": turn,
        "n_long": (W > 1e-12).sum(axis=1),
        "n_short": (W < -1e-12).sum(axis=1),
        "raw_exposure": np.abs(W).sum(axis=1),
        "dead_exits": (died & (np.abs(W) > 0)).sum(axis=1),
        "days": pd.Series(entry_times).diff().dt.total_seconds().div(86400).dropna().to_numpy(),
    }, index=P.index[: n - 1])
    out.index.name = "date"
    # 重叠模式下前 h-1 个窗口资金未完全铺开，从第一个满仓窗口开始统计
    return out.iloc[h - 1:].copy()


def funding_delta(cfg: Config, entry_times: pd.DatetimeIndex,
                  columns: pd.Index) -> pd.DataFrame | None:
    """每个窗口内（建仓到平仓）按符号累计的资金费率。"""
    if not (cfg.use_funding and cfg.market == "um"):
        return None
    try:
        f = lake_io.load("um", "funding", columns=["symbol", "date", "funding_rate"],
                         start=str(entry_times[0] - pd.Timedelta(days=1)),
                         end=str(entry_times[-1] + pd.Timedelta(days=1)))
    except FileNotFoundError:
        print("⚠️  数据湖没有 um/funding，本次不扣资金费")
        return None
    if f.empty:
        return None
    cum = (f.pivot(index="date", columns="symbol", values="funding_rate")
            .sort_index().astype("float64").cumsum())
    cum = cum.reindex(columns=columns).fillna(0.0)
    if cum.empty:
        return None
    # 前面垫一个全 0 行，让 searchsorted 在「第一笔资金费之前」也能取到值；
    # 索引必须是时间戳（不能用 ignore_index=True，否则会退化成 RangeIndex）
    zero = pd.DataFrame(np.zeros((1, cum.shape[1])), columns=cum.columns,
                        index=pd.DatetimeIndex([cum.index[0] - pd.Timedelta(seconds=1)]))
    cumZ = pd.concat([zero, cum])

    pos = cumZ.index.searchsorted(entry_times, side="left")
    pos = np.clip(pos, 0, len(cumZ) - 1)           # 晚于最后一笔结算时取最新累计值
    C = cumZ.iloc[pos].to_numpy(dtype="float64")   # (n_entry, n_sym) 建仓时刻之前累计
    # 窗口 i 的资金费 = C[i+1] - C[i]，覆盖 (建仓 i, 建仓 i+1] 的全部结算
    dF = pd.DataFrame(np.diff(C, axis=0),
                      index=pd.DatetimeIndex(entry_times[:-1]), columns=columns)
    return dF


# ══════════════════════════════════════════════ 指标
def portfolio_metrics(ret: pd.Series, days: pd.Series) -> dict:
    """逐窗口收益 → 绩效指标。年化一律按真实日历天数（365 天/年）。"""
    d = pd.DataFrame({"r": ret, "days": days}).dropna()
    if len(d) < 2:
        return {}
    eq = (1 + d["r"]).cumprod()
    cum = float(eq.iloc[-1] - 1)
    total_days = float(d["days"].sum())
    # 空腿被极端逼空时组合净值可能穿到 0 以下（亏损 > 100%），此时年化无意义，
    # 直接记 -100% 并在结果里保留累计收益，避免分数次幂算出复数。
    ruined = bool((eq <= 0).any())
    if ruined:
        ann = -1.0
    elif total_days > 0 and 1 + cum > 0:
        ann = (1 + cum) ** (YEAR_DAYS / total_days) - 1
    else:
        ann = np.nan
    per_year = YEAR_DAYS / float(d["days"].mean())
    vol = float(d["r"].std(ddof=1) * np.sqrt(per_year))
    sharpe = ann / vol if vol > 0 else np.nan
    running = np.maximum.accumulate(eq.to_numpy())
    mdd = float(np.min(eq.to_numpy() / running - 1))
    if ruined or mdd < -1:
        mdd = -1.0                                # 净值穿 0 就是爆仓，回撤封顶 -100%
    down = d["r"][d["r"] < 0]
    dvol = float(down.std(ddof=1) * np.sqrt(per_year)) if len(down) > 1 else np.nan
    sortino = ann / dvol if dvol and dvol > 0 else np.nan
    calmar = ann / abs(mdd) if mdd < 0 else np.nan
    return {
        "累计收益": cum, "年化收益": ann, "年化波动": vol, "夏普": sharpe,
        "索提诺": sortino, "卡玛": calmar, "最大回撤": mdd, "破产": ruined,
        "窗口胜率": float((d["r"] > 0).mean()), "窗口数": int(len(d)),
        "总天数": total_days, "年化换手": float(d["turnover"].mean() * per_year) if "turnover" in d else np.nan,
    }


def benchmark_curve(cfg: Config, start: pd.Timestamp, end: pd.Timestamp) -> pd.Series | None:
    """同期 BTC 日线收盘序列（绘图用，归一化在绘图端做）。"""
    daily = lake_io.load(cfg.market, "1d", columns=["symbol", "date", "close"],
                         start=str(pd.Timestamp(start).date()),
                         end=(pd.Timestamp(end) + pd.Timedelta(days=1)).strftime("%Y-%m-%d"))
    d = daily[daily["symbol"] == "BTCUSDT"].sort_values("date")
    if len(d) < 2:
        return None
    return d.set_index("date")["close"].astype(float)


def benchmark_btc(cfg: Config, start: pd.Timestamp, end: pd.Timestamp) -> dict:
    """同期 BTC 买入持有，作为必须并排看的对照。"""
    daily = lake_io.load(cfg.market, "1d", columns=["symbol", "date", "close"],
                         start=str(pd.Timestamp(start).date()),
                         end=(pd.Timestamp(end) + pd.Timedelta(days=1)).strftime("%Y-%m-%d"))
    d = daily[daily["symbol"] == "BTCUSDT"].sort_values("date")
    if len(d) < 2:
        return {}
    px = d.set_index("date")["close"].astype(float)
    r = px.pct_change().dropna()
    total_days = (px.index[-1] - px.index[0]).days
    cum = float(px.iloc[-1] / px.iloc[0] - 1)
    ann = (1 + cum) ** (YEAR_DAYS / total_days) - 1 if total_days > 0 else np.nan
    vol = float(r.std(ddof=1) * np.sqrt(YEAR_DAYS))
    eq = px / px.iloc[0]
    mdd = float((eq / eq.cummax() - 1).min())
    return {"累计收益": cum, "年化收益": ann, "年化波动": vol,
            "夏普": ann / vol if vol > 0 else np.nan, "最大回撤": mdd}


def yearly_table(sims: dict[int, pd.DataFrame], key: str = "net") -> pd.DataFrame:
    """按自然年拆分每个持有窗口的净收益。"""
    rows = []
    for h, sim in sims.items():
        if sim is None or sim.empty:
            continue
        for y, g in sim.groupby(sim.index.year):
            total_days = float(g["days"].sum())
            if total_days <= 0:
                continue
            cum = float((1 + g[key]).prod() - 1)
            ann = (1 + cum) ** (YEAR_DAYS / total_days) - 1 if 1 + cum > 0 else -1.0
            rows.append({"持有窗口": h, "年份": int(y),
                         "收益": cum, "年化": ann, "窗口数": int(len(g))})
    return pd.DataFrame(rows)


# ══════════════════════════════════════════════ 单变体（原始 / 中性化）
def run_variant(F: pd.DataFrame, Q: pd.DataFrame, W: pd.DataFrame, P: pd.DataFrame,
                entry_times: pd.DatetimeIndex, cfg: Config,
                dF: pd.DataFrame | None) -> dict:
    sims, qsims, signal = {}, {}, {}
    for h in cfg.hold_windows:
        sims[h] = simulate(P, W, h, cfg, entry_times, funding_delta=dF)

        # 每个分位单独跑一条只多组合，得到无重复复利的累计曲线
        qsims[h] = {}
        for qq in range(1, cfg.quantiles + 1):
            wq = (Q == qq).astype("float64")
            tot = wq.sum(axis=1)
            wq = wq.div(tot.replace(0, np.nan), axis=0).fillna(0.0) * cfg.gross_leverage
            qsims[h][qq] = simulate(P, wq, h, cfg, entry_times, funding_delta=dF)

        fwd = forward_returns(P, h, cfg.delist_shock)
        fwd_s = winsorize_rows(fwd)               # 统计口径缩尾；组合模拟仍用真实收益
        min_lags = max(0, h - 1)
        ic = ic_series(F, fwd_s, min_obs=cfg.fm_min_obs)
        signal[h] = {
            "ic": ic,
            "ic_t_naive": _tstat(ic, 0),
            "ic_t_nw": _tstat(ic, min_lags),
            "fm": fama_macbeth(F, fwd_s, min_obs=cfg.fm_min_obs, min_lags=min_lags),
            "fwd_q": quantile_forward_means(Q, fwd_s),
            "n_dates": int(F.notna().any(axis=1).sum()),
        }
    return {"factor": F, "quantiles": Q, "weights": W,
            "sims": sims, "qsims": qsims, "signal": signal}


def run(cfg: Config, verbose: bool = True) -> dict:
    """跑完一次单因子回测，返回结果字典。"""
    log = print if verbose else (lambda *a, **k: None)

    px_all, bar_dates = load_bars(cfg)
    reb_all = build_rebalances(bar_dates, cfg.rebalance, cfg.every)
    P_all, entry_times = align_entry_prices(px_all, reb_all)
    if len(P_all) < 3:
        raise RuntimeError(f"调仓窗口只有 {len(P_all)} 个，样本太少")
    reb_dates = pd.DatetimeIndex(P_all.index)
    columns = P_all.columns

    univ = build_universe(cfg, reb_dates).reindex(columns=columns).fillna(False)
    log(f"  调仓 {len(reb_dates)} 次 / 起 {reb_dates[0].date()} → {reb_dates[-1].date()} / "
        f"池子中位 {int(univ.sum(axis=1).median())} 个符号")

    F_raw = load_factor_at(cfg, reb_dates, columns)
    cover = F_raw.notna().sum(axis=1).median()
    log(f"  因子 {cfg.factor} 截面中位覆盖 {int(cover)} 个符号")
    if cover == 0:
        raise RuntimeError(f"调仓日上完全没有因子值，检查因子面板的时间戳是否和行情对齐")

    style = build_style(cfg, reb_dates, columns)
    F_pre = preprocess_factor(F_raw, univ, cfg.mad_n)

    variants = {"raw": None}
    if cfg.neutralize:
        F_neut = neutralize_factor(F_pre, style, univ, cfg.neutralize, cfg.fm_min_obs)
        variants["neut"] = F_neut
    else:
        F_neut = None

    dF = funding_delta(cfg, entry_times, columns)
    results = {"raw": F_pre} if F_neut is None else {"raw": F_pre, "neut": F_neut}

    out_variants = {}
    for name, F in results.items():
        Q = assign_quantiles(F, univ, cfg.quantiles, cfg.min_obs)
        W = build_weights(Q, cfg, style)
        out_variants[name] = run_variant(F, Q, W, P_all, entry_times, cfg, dF)
        log(f"  变体 {name}: 有效分组日 {int(Q.notna().any(axis=1).sum())}")

    effective = "neut" if (cfg.neutralize and "neut" in out_variants) else "raw"
    bm = benchmark_btc(cfg, entry_times[0], entry_times[-1])
    tables = _tables(cfg, out_variants, effective, univ)
    return {
        "cfg": cfg, "factor": cfg.factor, "reb_dates": reb_dates,
        "entry_times": entry_times, "universe": univ,
        "factor_raw": F_raw, "style": style,
        "variants": out_variants, "effective": effective,
        "benchmark": bm,
        "benchmark_curve": benchmark_curve(cfg, entry_times[0], entry_times[-1]),
        "tables": tables, "summary": tables["汇总统计"],
    }


# ══════════════════════════════════════════════ 汇总表
def _tables(cfg: Config, variants: dict, effective: str, univ: pd.DataFrame) -> dict:
    eff = variants[effective]
    rows = []
    for h in cfg.hold_windows:
        sim = eff["sims"].get(h)
        if sim is None or sim.empty:
            continue
        m = portfolio_metrics(sim["net"], sim["days"])
        sig = eff["signal"].get(h, {})
        fm = sig.get("fm", {})
        fq = sig.get("fwd_q")
        spread = float(fq.loc[cfg.quantiles] - fq.loc[1]) if isinstance(fq, pd.Series) and cfg.quantiles in fq.index and 1 in fq.index else np.nan
        row = {
            "持有窗口": h,
            "IC": sig.get("ic", pd.Series(dtype=float)).mean(),
            "ICIR": _icir(sig.get("ic")),
            "IC_t_NW": sig.get("ic_t_nw", np.nan),
            "FM斜率(BP)": fm.get("slope_bp", np.nan) if fm else np.nan,
            "FM_t_NW": fm.get("t_nw", np.nan) if fm else np.nan,
            "分组远期价差": spread,
            **m,
            "年化成本": _ann_drag(sim, "cost"),
            "年化资金费": _ann_drag(sim, "funding"),
            "平均毛敞口": float(sim["raw_exposure"].mean()),
            "退市平仓次数": int(sim["dead_exits"].sum()),
        }
        rows.append(row)
    summary = pd.DataFrame(rows)

    if cfg.neutralize and "neut" in variants:
        raw = variants["raw"]
        for i, h in enumerate(cfg.hold_windows):
            if h not in eff["sims"] or h not in raw["sims"]:
                continue
            summary.loc[summary["持有窗口"] == h, "原始IC"] = raw["signal"][h]["ic"].mean()
            summary.loc[summary["持有窗口"] == h, "中性化IC"] = eff["signal"][h]["ic"].mean()
            mr = portfolio_metrics(raw["sims"][h]["net"], raw["sims"][h]["days"])
            mn = portfolio_metrics(eff["sims"][h]["net"], eff["sims"][h]["days"])
            summary.loc[summary["持有窗口"] == h, "原始年化"] = mr.get("年化收益")
            summary.loc[summary["持有窗口"] == h, "中性化年化"] = mn.get("年化收益")

    # ── 其他 sheet
    quantile_rows = []
    for h in cfg.hold_windows:
        fq = eff["signal"][h].get("fwd_q")
        if isinstance(fq, pd.Series):
            quantile_rows.append({"持有窗口": h, **{f"Q{int(k)}": v for k, v in fq.items()}})
    fm_rows = []
    for h in cfg.hold_windows:
        fm = eff["signal"][h].get("fm", {})
        ic = eff["signal"][h].get("ic", pd.Series(dtype=float))
        sim = eff["sims"][h]
        fm_rows.append({
            "持有窗口": h,
            "FM斜率(每单位)": fm.get("mean_slope", np.nan),
            "FM斜率(BP)": fm.get("slope_bp", np.nan),
            "FM_t_naive": fm.get("t_naive", np.nan),
            "FM_t_NW": fm.get("t_nw", np.nan),
            "截面数": fm.get("n_dates", 0),
            "IC_t_naive": eff["signal"][h].get("ic_t_naive", np.nan),
            "IC_t_NW": eff["signal"][h].get("ic_t_nw", np.nan),
            "多空_t_naive": _tstat(sim["net"], 0),
            "多空_t_NW": _tstat(sim["net"], h - 1),
        })
    ic_rows = []
    for h in cfg.hold_windows:
        for t, v in eff["signal"][h]["ic"].items():
            ic_rows.append({"持有窗口": h, "日期": t, "IC": v})

    cost_rows = []
    for h in cfg.hold_windows:
        sim = eff["sims"][h]
        if sim.empty:
            continue
        cost_rows.append({
            "持有窗口": h,
            "平均换手": float(sim["turnover"].mean()),
            "年化换手": _ann_drag(sim, "turnover", absolute=True),
            "累计成本": float(sim["cost"].sum()),
            "累计资金费": float(sim["funding"].sum()),
            "毛收益累计": float((1 + sim["gross"]).prod() - 1),
            "净收益累计": float((1 + sim["net"]).prod() - 1),
            "平均多腿": float(sim["n_long"].mean()),
            "平均空腿": float(sim["n_short"].mean()),
        })

    return {
        "汇总统计": summary,
        "分位数远期收益": pd.DataFrame(quantile_rows),
        "FM回归与NW检验": pd.DataFrame(fm_rows),
        "IC时间序列": pd.DataFrame(ic_rows),
        "换手与成本": pd.DataFrame(cost_rows),
        "分年度": yearly_table(eff["sims"]),
    }


def _icir(ic: pd.Series | None) -> float:
    if ic is None or len(ic) < 2 or ic.std(ddof=1) == 0:
        return np.nan
    return float(ic.mean() / ic.std(ddof=1))


def _tstat(x: pd.Series, min_lags: int) -> float:
    x = pd.Series(x).dropna()
    if len(x) < 3:
        return np.nan
    se = newey_west_se(x.to_numpy(), min_lags=min_lags)
    return float(x.mean() / se) if se and se > 0 else np.nan


def _ann_drag(sim: pd.DataFrame, col: str, absolute: bool = False) -> float:
    if sim.empty:
        return np.nan
    total_days = float(sim["days"].sum())
    if total_days <= 0:
        return np.nan
    v = float(sim[col].sum())
    return (v if absolute else v) / total_days * YEAR_DAYS
