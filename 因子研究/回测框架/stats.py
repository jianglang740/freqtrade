#!/usr/bin/env python
"""
因子统计检验：截面 IC、Fama-MacBeth 回归、Newey-West 自相关修正。

全部输入都是 date × symbol 宽表，日期轴 = 调仓日（不是每个交易日）——
和股票框架一致：统计量的采样单位是**调仓窗口**，否则日频重复计权。
"""

from __future__ import annotations

import numpy as np
import pandas as pd


# ══════════════════════════════════════════════ Newey-West
def newey_west_se(x, lags: int | None = None, min_lags: int = 0) -> float:
    """均值的 Newey-West HAC 标准误（Bartlett 权重）。

    IC / 斜率 / 多空收益序列都有自相关，普通 t 值会虚高；重叠持有窗口
    还存在 MA(h-1) 结构，用 `min_lags=h-1` 把滞后阶数下限顶上去。
    """
    x = np.asarray(x, dtype=float)
    x = x[~np.isnan(x)]
    n = len(x)
    if n < 3:
        return np.nan
    if lags is None:
        lags = int(np.floor(4 * (n / 100.0) ** (2.0 / 9.0)))
    lags = max(int(lags), int(min_lags))
    lags = max(0, min(lags, n - 2))
    xc = x - x.mean()
    var = np.mean(xc * xc)
    for k in range(1, lags + 1):
        var += 2 * (1 - k / (lags + 1)) * np.mean(xc[k:] * xc[:-k])
    # HAC 权重下 var 可能因为近似而微负（序列几乎全是常数时），
    # 直接开方会抛 RuntimeWarning 并返回 nan。
    return float(np.sqrt(max(var, 0.0) / n))


# ══════════════════════════════════════════════ 远期收益
def forward_returns(P: pd.DataFrame, h: int, delist_shock: float = 0.0) -> pd.DataFrame:
    """从第 i 个调仓窗口建仓，持有 h 个窗口的收益（i+h 的建仓价 / i 的建仓价 - 1）。

    退市/停牌：用最后一个可成交价平仓，并按 `delist_shock` 额外扣亏损。
    """
    arr = P.to_numpy(dtype="float64")
    n, ns = arr.shape
    P_use = pd.DataFrame(arr, index=P.index, columns=P.columns).ffill().to_numpy()
    rows = np.arange(n)

    mask = np.isfinite(arr)
    idx = np.where(mask, rows[:, None], -1)
    last_idx = idx.max(axis=0)

    fwd = np.full((n, ns), np.nan)
    if h < n:
        fwd[:-h] = P_use[h:] / P_use[:-h] - 1.0
        died = last_idx[None, :] < (rows + h)[:, None]
        fwd = fwd - died * delist_shock
    entry_ok = last_idx[None, :] >= rows[:, None]
    fwd = np.where(entry_ok, fwd, np.nan)
    return pd.DataFrame(fwd, index=P.index, columns=P.columns)


def winsorize_rows(df: pd.DataFrame, q: float = 0.01) -> pd.DataFrame:
    """逐期截面缩尾（默认 1%/99%）。

    币圈 20 天收益动辄 ±300%，等权平均会被一两个百倍币绑架；IC 是秩相关不怕，
    但 FM 回归和「分位平均远期收益」会被极端值带跑。统计口径统一先缩尾，
    组合模拟仍用真实收益（你真的会持有那只币）。
    """
    lo = df.quantile(q, axis=1)
    hi = df.quantile(1 - q, axis=1)
    return df.clip(lower=lo, upper=hi, axis=0)


# ══════════════════════════════════════════════ IC
def ic_series(F: pd.DataFrame, R: pd.DataFrame, min_obs: int = 10) -> pd.Series:
    """逐期截面 Spearman IC（向量化：秩相关 = 秩的 Pearson）。

    Spearman 对因子量纲不敏感，是因子研究的标准口径；这里用秩矩阵一次性算完，
    避免逐日调用 scipy.spearmanr（几千个截面会很慢）。
    """
    m = F.notna() & R.notna()
    n = m.sum(axis=1)
    Fm = F.where(m)
    Rm = R.where(m)
    rf = Fm.rank(axis=1)
    rr = Rm.rank(axis=1)
    mf = rf.mean(axis=1)
    mr = rr.mean(axis=1)
    dF = rf.sub(mf, axis=0)
    dR = rr.sub(mr, axis=0)
    cov = (dF * dR).sum(axis=1)
    den = np.sqrt((dF ** 2).sum(axis=1) * (dR ** 2).sum(axis=1))
    ic = cov / den.replace(0, np.nan)
    ic[n < min_obs] = np.nan
    ic.name = "IC"
    return ic.dropna()


# ══════════════════════════════════════════════ Fama-MacBeth
def fama_macbeth(F: pd.DataFrame, R: pd.DataFrame,
                 controls: dict[str, pd.DataFrame] | None = None,
                 min_obs: int = 10, min_lags: int = 0) -> dict:
    """逐调仓日截面 OLS：ret ~ 1 + factor (+ controls)，收集斜率做 NW t 检验。

    controls 用于控制共同风险（如 log 成交额、BTC Beta）——币圈没有行业
    中性化那套，控制共同风格就是这里的「行业中性」。
    """
    controls = controls or {}
    slopes, dates = [], []
    for t in F.index:
        m = F.loc[t].notna() & R.loc[t].notna()
        for c in controls.values():
            m &= c.loc[t].notna()
        if int(m.sum()) < min_obs:
            continue
        y = R.loc[t].to_numpy(dtype=float)[m.to_numpy(dtype=bool)]
        parts = [np.ones(len(y)), F.loc[t].to_numpy(dtype=float)[m.to_numpy(dtype=bool)]]
        for c in controls.values():
            parts.append(c.loc[t].to_numpy(dtype=float)[m.to_numpy(dtype=bool)])
        X = np.column_stack(parts)
        ok = np.isfinite(X).all(axis=1) & np.isfinite(y)
        if ok.sum() < min_obs:
            continue
        beta, *_ = np.linalg.lstsq(X[ok], y[ok], rcond=None)
        slopes.append(beta[1])
        dates.append(t)

    slopes = np.asarray(slopes, dtype=float)
    slope_series = pd.Series(slopes, index=pd.Index(dates, name="date")) if len(slopes) else pd.Series(dtype=float)
    if len(slopes) < 3:
        return {"mean_slope": np.nan, "slope_bp": np.nan, "t_naive": np.nan,
                "t_nw": np.nan, "n_dates": int(len(slopes)), "slope_series": slope_series}

    mean_slope = float(slopes.mean())
    se_naive = float(slopes.std(ddof=1) / np.sqrt(len(slopes)))
    se_nw = newey_west_se(slopes, min_lags=min_lags)
    return {
        "mean_slope": mean_slope,
        "slope_bp": mean_slope * 1e4,
        "t_naive": mean_slope / se_naive if se_naive > 0 else np.nan,
        "t_nw": mean_slope / se_nw if np.isfinite(se_nw) and se_nw > 0 else np.nan,
        "n_dates": int(len(slopes)),
        "slope_series": slope_series,
    }


# ══════════════════════════════════════════════ 分组远期收益
def quantile_forward_means(Q: pd.DataFrame, R: pd.DataFrame) -> pd.Series:
    """各分位在持有窗口内的平均远期收益（信号强度的直观读法，不是策略绩效）。"""
    out = {}
    for q in sorted(Q.stack().dropna().unique()):
        mask = Q.eq(q)
        v = R.where(mask)
        out[int(q)] = float(v.stack().mean()) if v.notna().any().any() else np.nan
    return pd.Series(out, name="fwd_return")
