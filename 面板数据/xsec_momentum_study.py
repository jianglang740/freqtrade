#!/usr/bin/env python
"""
复现并压力测试 `策略池/风险调整动量多空.md` 里的横截面动量多空。

变体编号沿用该文 §5.3 的事先声明（V0–V7），不做事后择优。
口径：
  - 信号用 t 日收盘；成交按 t 日收盘价近似（00:00 开盘价与前一根收盘价几乎相同，
    脚本会先验证这个假设）。
  - pnl 是「每边 1× 名义本金」的收益率；`--lev 1` 时每边 0.5×。
  - 成本：按换手算，费率作用在**实际成交名义本金**上。
  - 资金费：从合约 funding 文件按日汇总，多头付出、空头收取。

用法：
    source /opt/anaconda3/etc/profile.d/conda.sh && conda activate freqtrade
    python 面板数据/xsec_momentum_study.py
"""

from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from panel_io import load_panel  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "user_data", "data", "binance")
FDIR = os.path.join(DATA, "futures")

FEE = 0.0003          # 单边 0.03%（手续费 + 滑点）
ADV_MIN = 1e6
TOP_N = 50
Q = 0.20


# ────────────────────────────────────────────── 数据
def load_wide():
    df = load_panel("1d")
    close = df.pivot(index="date", columns="pair", values="close").sort_index()
    open_ = df.pivot(index="date", columns="pair", values="open").sort_index()
    qv = df.pivot(index="date", columns="pair", values="quote_volume").sort_index()
    return close, open_, qv


def load_funding(close_index, cols) -> pd.DataFrame:
    """把 funding_rate.feather 汇总成日频（每天多笔结算求和）。"""
    out = {}
    for c in cols:
        base = c.split("/")[0]
        for fname in (f"{base}_USDT_USDT-1h-funding_rate.feather",):
            p = os.path.join(FDIR, fname)
            if not os.path.exists(p):
                continue
            f = pd.read_feather(p)
            col = "funding_rate" if "funding_rate" in f.columns else "open"
            s = f.set_index("date")[col]
            s.index = s.index.floor("1D")
            out[c] = s.groupby(level=0).sum()
    if not out:
        return pd.DataFrame(index=close_index, columns=cols, dtype="float64")
    fr = pd.DataFrame(out).reindex(close_index)
    return fr.reindex(columns=cols)


# ────────────────────────────────────────────── 信号
def build_scores(close, ret, adv_ok, windows=(7, 14, 28), vol_adj=True,
                 top_n=TOP_N):
    univ = adv_ok
    parts = []
    for n in windows:
        mom = close / close.shift(n) - 1
        if vol_adj:
            sig = mom / ret.rolling(n, min_periods=n).std()
        else:
            sig = mom
        parts.append(sig)
    raw = sum(parts) / len(parts)
    z = raw.where(univ).sub(raw.where(univ).mean(axis=1), axis=0)
    z = z.div(raw.where(univ).std(axis=1), axis=0)
    return z


def rank_pct(score):
    return score.rank(axis=1, pct=True, ascending=False)


# ────────────────────────────────────────────── 组合
def run_portfolio(score, ret, rank=None, lev=1.0, fee=FEE, inv_vol=None,
                  buffer_exit=None, funding=None, weight_cap=None,
                  vol28=None, vol_cap_pct=None, short_market=False,
                  skip_squeeze=None):
    """逐日重建组合，返回日频 pnl 明细。

    rank 参数已废弃（现在在循环内按当日可选池重新排名，这样才能支持过滤）；
    保留形参只是为了不破坏旧调用。
    vol_cap_pct: 剔除 28 日波动率最高的这一比例的币（如 0.1 = 剔除前 10%）。
    short_market: True 时空腿直接空整个可选池（等权），不做截面选股。
    skip_squeeze: 空腿剔掉「当日涨幅 > k×28日波动率」的币（k 为该值）。
    """
    dates = score.index
    prev_l = pd.Series(dtype="float64")
    prev_s = pd.Series(dtype="float64")
    rows = []
    for i, d in enumerate(dates[:-1]):
        s = score.loc[d].dropna()
        d_next = ret.index[i + 1]
        r_next = ret.iloc[i + 1].replace([np.inf, -np.inf], np.nan)

        # 过滤：先砍掉波动率极端高的币，再排名
        if vol_cap_pct is not None and vol28 is not None:
            v = vol28.loc[d, s.index]
            thr = v.quantile(1 - vol_cap_pct)
            s = s[v <= thr]
        if len(s) < 10:
            continue
        rk = s.rank(pct=True, ascending=False)

        n = len(s)
        k = max(2, int(round(n * Q)))
        longs = set(s.nlargest(k).index)
        if short_market:
            shorts = set(s.index)
        else:
            shorts = set(s.nsmallest(k).index)
            if skip_squeeze is not None and vol28 is not None:
                today = ret.loc[d, list(shorts)]
                spr = today / vol28.loc[d, list(shorts)]
                shorts = {p for p in shorts if not (spr.get(p, 0) > skip_squeeze)}

        # V3 持有缓冲：昨日已在多头且排名仍在 exit 分位内，就继续持有
        if buffer_exit is not None:
            keep_l = {p for p in prev_l.index if rk.get(p, 9) <= buffer_exit}
            keep_s = {p for p in prev_s.index if rk.get(p, 9) >= 1 - buffer_exit}
            longs |= (keep_l & set(s.index))
            shorts |= (keep_s & set(s.index))

        # 剔除没有下一日收益的币（停牌/退市），并在腿内重新归一
        longs = [p for p in longs if pd.notna(r_next.get(p))]
        shorts = [p for p in shorts if pd.notna(r_next.get(p))]
        if not longs or not shorts:
            continue

        def weights(names):
            if inv_vol is not None:
                iv = 1.0 / inv_vol.loc[d, names].replace(0, np.nan)
                w = iv / iv.sum()
            else:
                w = pd.Series(1.0 / len(names), index=names)
            if weight_cap is not None:
                w = w.clip(upper=weight_cap)
            return w / w.sum()

        wl, ws = weights(longs), weights(shorts)
        rl = float((wl * r_next[wl.index]).sum())
        rs = float((ws * r_next[ws.index]).sum())

        # 换手（相对昨日权重，按并集对齐）
        tl = float(wl.sub(prev_l, fill_value=0).abs().sum())
        ts = float(ws.sub(prev_s, fill_value=0).abs().sum())
        turnover = (tl + ts) / 2          # 每边都是 1，取平均代表「单边换手」

        gross = rl - rs                     # 每边 1× 的收益率
        cost = fee * (tl + ts)              # 成交名义本金 × 费率（每边各 1×）
        f_cost = 0.0
        if funding is not None and d_next in funding.index:
            # 资金费按**持仓那一段**（d_next）的实际结算时点汇总
            fL = funding.loc[d_next, wl.index].fillna(0)
            fS = funding.loc[d_next, ws.index].fillna(0)
            f_cost = float((wl * fL).sum() - (ws * fS).sum())

        rows.append(dict(date=d, long=rl, short=rs, gross=gross, cost=cost,
                         funding=f_cost, net=gross - cost - f_cost,
                         n_long=len(longs), n_short=len(shorts),
                         turnover=turnover))
        prev_l, prev_s = wl, ws

    out = pd.DataFrame(rows).set_index("date")
    for c in ("gross", "net", "cost", "funding"):
        out[c] *= lev / 2.0
    return out


# ────────────────────────────────────────────── 指标
def metrics(p: pd.Series, label: str = "") -> dict:
    p = p.dropna()
    ann = (1 + p.mean()) ** 252 - 1
    vol = p.std() * np.sqrt(252)
    eq = (1 + p).cumprod()
    dd = (eq / eq.cummax() - 1).min()
    m = p.resample("ME").apply(lambda x: (1 + x).prod() - 1)
    return {
        "方案": label, "年化": ann, "波动": vol, "Sharpe": ann / vol if vol else np.nan,
        "最大回撤": dd, "Calmar": ann / abs(dd) if dd else np.nan,
        "月度胜率": (m > 0).mean(), "日偏度": p.skew(),
        "日均pnl": p.mean(), "样本天数": len(p),
    }


def show(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows).set_index("方案")
    fmt = df.copy()
    for c in ("年化", "波动", "最大回撤", "月度胜率", "日均pnl"):
        fmt[c] = fmt[c].map(lambda v: f"{v:.1%}")
    for c in ("Sharpe", "Calmar", "日偏度"):
        fmt[c] = fmt[c].map(lambda v: f"{v:.2f}")
    print(fmt.to_string())
    return df


def main() -> None:
    close, open_, qv = load_wide()
    ret = close.pct_change(fill_method=None)
    adv = qv.rolling(14, min_periods=10).mean()

    # 验证「00:00 开盘 ≈ 前一根收盘」，这样按收盘价成交才说得通
    gap = (open_ / close.shift(1) - 1).abs()
    print("=" * 78)
    print(f"样本 {close.index.min().date()} → {close.index.max().date()}，"
          f"{close.shape[1]} 个币")
    print(f"开盘价 / 前收盘 的偏离：中位 {gap.stack().median():.2%}，"
          f"95% 分位 {gap.stack().quantile(.95):.2%}")
    print(f"日频可用币对数的中位数：{int(ret.notna().sum(axis=1).median())}")

    def univ(top_n=TOP_N):
        return (adv.where(adv >= ADV_MIN).rank(axis=1, ascending=False) <= top_n)

    u50 = univ(50)
    score = build_scores(close, ret, u50)
    rank = rank_pct(score)
    vol28 = ret.rolling(28, min_periods=20).std()
    funding = load_funding(close.index, close.columns)
    print(f"资金费覆盖：{int(funding.notna().any().sum())} / {close.shape[1]} 个币")

    print("\n每日可选池实际大小：" + str(u50.sum(axis=1).describe()[["mean", "50%", "max"]].round(1).to_dict()))
    print(f"→ 名义上 50 个币，实际中位数 {int(u50.sum(axis=1).median())} 个"
          f"（通过 $1M 门槛的币不够 50 个），前/后 20% 即约 "
          f"{max(2,int(round(u50.sum(axis=1).median()*Q)))} 个。")

    res = []

    # ── V0 / V5：杠杆
    v0 = run_portfolio(score, ret, rank, lev=2.0, funding=funding)
    v5 = run_portfolio(score, ret, rank, lev=1.0, funding=funding)
    for p, lab in ((v0, "V0 原文规则 2×"), (v5, "V5 同规则 1×")):
        res.append(metrics(p["net"], lab))

    # ── V1 去掉波动率调整
    s1 = build_scores(close, ret, u50, vol_adj=False)
    v1 = run_portfolio(s1, ret, rank_pct(s1), lev=1.0, funding=funding)
    res.append(metrics(v1["net"], "V1 不做波动率调整 1×"))

    # ── V2 只用 28 日
    s2 = build_scores(close, ret, u50, windows=(28,))
    v2 = run_portfolio(s2, ret, rank_pct(s2), lev=1.0, funding=funding)
    res.append(metrics(v2["net"], "V2 单窗口 28d 1×"))

    # ── V3 持有缓冲：进 20%、出 40%
    v3 = run_portfolio(score, ret, rank, lev=1.0, buffer_exit=0.40, funding=funding)
    res.append(metrics(v3["net"], "V3 缓冲(出40%) 1×"))

    # ── V4 逆波动配权
    v4 = run_portfolio(score, ret, rank, lev=1.0, inv_vol=vol28, funding=funding)
    res.append(metrics(v4["net"], "V4 逆波动配权 1×"))

    # ── V6 池子大小
    for n, lab in ((20, "V6a 池 20 个 1×"), (30, "V6b 池 30 个 1×"),
                   (None, "V6c 池不限 1×")):
        u = univ(n if n else 999)
        s = build_scores(close, ret, u)
        p = run_portfolio(s, ret, rank_pct(s), lev=1.0, funding=funding)
        res.append(metrics(p["net"], lab))

    print("\n" + "=" * 78)
    print("【主结果】净收益（已扣手续费与资金费）")
    print("=" * 78)
    main_tbl = show(res)

    # ── 成本敏感性（V0 2× 与 V5 1×）
    print("\n" + "=" * 78)
    print("【V7 成本敏感性】年化 / 最大回撤")
    print("=" * 78)
    print(f"  {'单边费率':>10}{'1× 年化':>10}{'1× 回撤':>10}{'2× 年化':>10}{'2× 回撤':>10}")
    for fee in (0.0, 0.0003, 0.0006, 0.0012):
        a = run_portfolio(score, ret, rank, lev=1.0, fee=fee, funding=funding)
        b = run_portfolio(score, ret, rank, lev=2.0, fee=fee, funding=funding)
        ma, mb = metrics(a["net"], ""), metrics(b["net"], "")
        print(f"  {fee:>9.2%}{ma['年化']:>10.1%}{ma['最大回撤']:>10.1%}"
              f"{mb['年化']:>10.1%}{mb['最大回撤']:>10.1%}")

    # ── 分解
    print("\n" + "=" * 78)
    print("【分解】V5（1×）的毛/净、成本、资金费")
    print("=" * 78)
    gross_ann = (1 + v5["gross"].mean()) ** 252 - 1
    net_ann = (1 + v5["net"].mean()) ** 252 - 1
    cost_ann = v5["cost"].mean() * 252
    fund_ann = v5["funding"].mean() * 252
    print(f"  毛年化 {gross_ann:.1%}  手续费拖累 {cost_ann:.1%}  "
          f"资金费拖累 {fund_ann:.1%}  净年化 {net_ann:.1%}")
    print(f"  日均换手(单边) {v5['turnover'].mean():.1%}  "
          f"日均成本 {v5['cost'].mean()*1e4:.1f}bp  日均资金费 {v5['funding'].mean()*1e4:.2f}bp")
    print(f"  单笔优势/成本：日均毛 pnl {v5['gross'].mean()*1e4:.1f}bp ÷ "
          f"日均成本 {v5['cost'].mean()*1e4:.1f}bp = "
          f"{v5['gross'].mean()/max(v5['cost'].mean(),1e-12):.1f}")
    # 注意：long/short 两列是**整条腿自身**的收益率（未乘 lev/2），
    # 组合 pnl = (long - short) × lev/2，所以两条腿各贡献一半。
    print(f"  多腿自身年化 {(1+v5['long'].mean())**252-1:.1%}  "
          f"空腿标的自身年化 {(1+v5['short'].mean())**252-1:.1%}"
          f"（空头因此每年付出这个数）→ 组合拿两者之差的一半")

    # ── 年度
    print("\n" + "=" * 78)
    print("【分年度】V5 1× 净收益 / 最大回撤")
    print("=" * 78)
    for y, g in v5["net"].groupby(v5.index.year):
        eq = (1 + g).cumprod()
        print(f"  {y}  年化 {(1+g.mean())**252-1:>8.1%}  回撤 "
              f"{(eq/eq.cummax()-1).min():>7.1%}  天数 {len(g)}")

    # ── 集中度
    print("\n" + "=" * 78)
    print("【集中度】V5 1×")
    print("=" * 78)
    pnl = v5["net"].sort_values(ascending=False)
    tot = pnl.sum()
    for pct in (0.01, 0.05):
        k = max(1, int(len(pnl) * pct))
        print(f"  最好的 {pct:.0%} 交易日（{k} 天）贡献 {pnl.iloc[:k].sum()/tot:.1%} 的累计 pnl")
    print(f"  最好 5 天合计 {pnl.iloc[:5].sum()/tot:.1%}，最差 5 天合计 "
          f"{pnl.iloc[-5:].sum()/tot:.1%}")
    print(f"  日 pnl 偏度 {v5['net'].skew():.2f}")

    # ── 参照
    mkt = ret[u50].mean(axis=1)
    btc = ret.get("BTC/USDT")
    print("\n" + "=" * 78)
    print("【参照】等权池 / BTC 买入持有（1× 全额）")
    print("=" * 78)
    for name, s in (("等权池", mkt), ("BTC", btc)):
        s = s.dropna()
        eq = (1 + s).cumprod()
        print(f"  {name:<6} 年化 {(1+s.mean())**252-1:>8.1%}  回撤 "
              f"{(eq/eq.cummax()-1).min():>7.1%}  Sharpe "
              f"{((1+s.mean())**252-1)/(s.std()*np.sqrt(252)):.2f}")

    # ── 优化方向（探索性，与 V0–V7 分开看）
    print("\n" + "=" * 78)
    print("【优化方向】每次只改一处，对照 V5（1×，净）")
    print("=" * 78)

    def vol_target_scale(p, target=0.20, lookback=63, cap=2.0):
        rv = p.rolling(lookback, min_periods=30).std() * np.sqrt(252)
        k = (target / rv).clip(upper=cap).shift(1).fillna(1.0)
        return p * k                     # 所有项都对名义本金线性，所以整体缩放是精确的

    o = []
    om1 = run_portfolio(score, ret, lev=1.0, short_market=True, funding=funding)
    o.append(metrics(om1["net"], "O1 空腿改空整池 1×"))

    om2 = run_portfolio(score, ret, lev=1.0, vol28=vol28, vol_cap_pct=0.10, funding=funding)
    o.append(metrics(om2["net"], "O3 剔除波动最高 10% 1×"))

    om3 = run_portfolio(score, ret, lev=1.0, vol28=vol28, skip_squeeze=3.0, funding=funding)
    o.append(metrics(om3["net"], "O4 空腿挤空过滤(3σ) 1×"))

    vt = vol_target_scale(v5["net"], target=0.20)
    o.append(metrics(vt, "O2 波动率目标 20%"))

    show(o)
    print(f"  O1 换手 {om1['turnover'].mean():.1%}  O3 换手 {om2['turnover'].mean():.1%}  "
          f"O4 换手 {om3['turnover'].mean():.1%}")
    print(f"  O2 平均杠杆倍数 {float((vt / v5['net']).mean()):.2f}×")

    # 导出明细，供画图/复核
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "study_v5_daily.csv")
    v5.to_csv(out)
    print(f"\n明细已写 {os.path.relpath(out, ROOT)}")


if __name__ == "__main__":
    main()
