#!/usr/bin/env python
"""
用**全历史币池**（含已下架）重跑横截面动量，把生存者偏差量出来。

与 `xsec_momentum_study.py` 的差别只有一处：币池不再是我们事后挑的 100 个，
而是归档里全部曾经存在过的 USDT 现货对。上市/下架天然由「当天有没有行」表达，
所以选池**天然是时点的** —— 不需要额外的名单。

三个必须回答的问题：
  1. 全历史池里，这套策略还成立吗？
  2. 与 100 对面板的差额是多少（= 生存者偏差的量级）？
  3. 退市如果按「仓位打水漂」处理，结论会不会翻转？

用法：
    source /opt/anaconda3/etc/profile.d/conda.sh && conda activate freqtrade
    python 策略池/横截面动量/scripts/xsec_all_universe_study.py
"""

from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from xsec_momentum_study import (  # noqa: E402
    ADV_MIN, FEE, LAKE, Q, ROOT, TOP_N, build_scores, load_funding, metrics,
)

sys.path.insert(0, LAKE)          # lake_io.py 在数据湖根目录

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), "out")   # 策略池/横截面动量/out
# 2026-11 数据湖上线：原来的 panel 单文件已删除，改成从 lake_io 读。
# None = 从数据湖取；保留路径参数是为了兼容旧的调用方式。
ALL = None
OLD = None
COLS = ("方案", "年化", "波动", "Sharpe", "最大回撤", "Calmar", "月度胜率")


def show_(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame([{k: r[k] for k in COLS if k in r} for r in rows]).set_index("方案")
    fmt = df.copy()
    for c in ("年化", "波动", "最大回撤", "月度胜率"):
        fmt[c] = fmt[c].map(lambda v: f"{v:.1%}")
    for c in ("Sharpe", "Calmar"):
        fmt[c] = fmt[c].map(lambda v: f"{v:.2f}")
    print(fmt.to_string())
    return df


# ────────────────────────────────────────────── 数据
def legacy_pairs() -> set[str]:
    """原 100 对面板的符号，从仍留在 user_data/data/binance 的 feather 反查。"""
    d = os.path.join(ROOT, "user_data", "data", "binance")
    tag = "_USDT-1d.feather"
    return {f[: -len(tag)] + "/USDT" for f in os.listdir(d) if f.endswith(tag)}


def load_wide(path: str | None, symbols: set[str] | None = None):
    """长表 → 宽表，并把「上市到退市」这段之外的价格全部抹成 NaN。

    归档里 `volume == 0` 的日期已经被抓取脚本丢掉，所以会留下缺口；
    对缺口做前向填充（停牌期间按最后价计），但**退市之后不填**，
    否则一个死币会以最后价格永久留在截面里。
    """
    if path is None:                                   # 数据湖
        from lake_io import load_panel
        df = load_panel("1d", "spot")
        if symbols:
            df = df[df["pair"].isin(symbols)]
    else:
        df = pd.read_parquet(path)
    df["date"] = pd.to_datetime(df["date"], utc=True)
    close = df.pivot(index="date", columns="pair", values="close").sort_index()
    qv = df.pivot(index="date", columns="pair", values="quote_volume").sort_index()
    last = df.groupby("pair")["date"].max().reindex(close.columns)

    alive = close.notna().cummax() & pd.DataFrame(
        close.index.values[:, None] <= last.values[None, :],
        index=close.index, columns=close.columns)
    return close.ffill().where(alive), qv.where(alive), alive, last


# ────────────────────────────────────────────── 组合
def run(score, ret, funding=None, lev=1.0, fee=FEE, q=Q, dead_shock=0.0,
        vol28=None, skip_squeeze=None, short_market=False):
    """逐日重建等权多空组合。

    dead_shock: 持有一个「次日没有行情」的币（退市/停牌）时，给该仓位额外扣的亏损。
                0 = 按最后成交价平仓（乐观）；0.5 = 仓位打水漂一半（保守）。
    skip_squeeze: 空腿剔掉「当日涨幅 > k×28 日波动」的币。
    short_market: 空腿直接空整个可选池。
    """
    dates = score.index
    prev_l = prev_s = pd.Series(dtype="float64")
    rows = []
    for i, d in enumerate(dates[:-1]):
        s = score.loc[d].dropna()
        if len(s) < 10:
            continue
        d_next = ret.index[i + 1]
        r = ret.iloc[i + 1]
        k = max(2, int(round(len(s) * q)))
        longs = list(s.nlargest(k).index)
        if short_market:
            shorts = list(s.index)
        else:
            shorts = list(s.nsmallest(k).index)
            if skip_squeeze is not None and vol28 is not None:
                today = ret.loc[d].reindex(shorts)
                spr = today / vol28.loc[d].reindex(shorts)
                shorts = [p for p in shorts if not (spr.get(p, 0) > skip_squeeze)]
        if not shorts:
            continue
        wl = pd.Series(1.0 / len(longs), index=longs)
        ws = pd.Series(1.0 / len(shorts), index=shorts)

        tl = float(wl.sub(prev_l, fill_value=0).abs().sum())
        ts = float(ws.sub(prev_s, fill_value=0).abs().sum())

        rl, rs = r.reindex(wl.index), r.reindex(ws.index)
        lost_l, lost_s = rl.isna(), rs.isna()
        rl, rs = rl.fillna(0.0), rs.fillna(0.0)
        long_ret = float((wl * rl).sum()) - dead_shock * float(wl[lost_l].sum())
        short_ret = float((ws * rs).sum()) - dead_shock * float(ws[lost_s].sum())

        f_cost = 0.0
        if funding is not None and d_next in funding.index:
            f_cost = float((wl * funding.loc[d_next, wl.index].fillna(0)).sum()
                           - (ws * funding.loc[d_next, ws.index].fillna(0)).sum())

        rows.append(dict(date=d, long=long_ret, short=short_ret,
                         gross=long_ret - short_ret, cost=fee * (tl + ts),
                         funding=f_cost,
                         net=long_ret - short_ret - fee * (tl + ts) - f_cost,
                         n_long=len(longs), n_short=len(shorts),
                         dead_l=int(lost_l.sum()), dead_s=int(lost_s.sum())))
        prev_l, prev_s = wl, ws

    out = pd.DataFrame(rows).set_index("date")
    for c in ("gross", "net", "cost", "funding", "long", "short"):
        out[c] *= lev / 2.0
    return out


def main() -> None:
    try:
        from lake_io import available
        if not available("spot", "1d"):
            print("数据湖 spot/1d 还没建：先跑 因子研究/行情数据/_build/01_fetch_bv.py 与 02_partition.py")
            return
    except Exception as e:                              # noqa: BLE001
        print(f"读数据湖失败：{e}")
        return

    close, qv, alive, last = load_wide(ALL)
    ret = close.pct_change(fill_method=None)
    adv = qv.rolling(14, min_periods=10).mean()
    print("=" * 92)
    print(f"全历史池 {close.shape[1]} 个币，{close.index.min().date()} → {close.index.max().date()}")
    dead = last < close.index.max() - pd.Timedelta(days=30)
    print(f"其中已下架（末个行情早于最新日期 30 天以上）：{int(dead.sum())} 个")
    print(f"最终仍活着的：{int((~dead).sum())} 个；本面板的 100 对只占 "
          f"{100 / close.shape[1]:.0%}")

    univ = (adv.where(adv >= ADV_MIN).rank(axis=1, ascending=False) <= TOP_N)
    size = univ.sum(axis=1)
    print(f"\n可选池（ADV≥$1M 且前 {TOP_N}）大小："
          f"中位 {int(size.median())}，均值 {size.mean():.1f}，最大 {int(size.max())}")
    print("  分年份中位数：" + "  ".join(
        f"{y}:{int(g.median())}" for y, g in size.groupby(size.index.year) if len(g) > 50))

    funding = load_funding(close.index, close.columns)
    fund_cov = int(funding.notna().any().sum())
    print(f"资金费覆盖：{fund_cov} / {close.shape[1]} 个币（其余按 0 计，偏乐观）")

    # ── 三个嵌套的池子：把「事后选池」拆成两道筛选
    old_cols = legacy_pairs()
    alive_cols = list(last[last >= close.index.max() - pd.Timedelta(days=30)].index)
    pick100 = [c for c in close.columns if c in old_cols]
    print(f"\n三个嵌套池子：全历史 {close.shape[1]} ⊃ 仍活着 {len(alive_cols)} ⊃ "
          f"100 对面板 {len(pick100)}")

    def evaluate(cols, label, top_n=TOP_N, dead_shock=0.0, **kw):
        c, r, a = close[cols], ret[cols], adv[cols]
        u = a.where(a >= ADV_MIN).rank(axis=1, ascending=False) <= top_n
        s = build_scores(c, r, u)
        f = funding.reindex(columns=cols)
        p = run(s, r, funding=f, dead_shock=dead_shock,
                vol28=r.rolling(28, min_periods=20).std(), **kw)
        return metrics(p["net"], label), p, u

    print("\n" + "=" * 92)
    print("【拆解】同一规则、同一区间，只换币池")
    print("=" * 92)
    res, keep = [], {}
    for cols, lab in ((pick100, "① 100 对面板（活币 + 2026 成交额前 100）"),
                      (alive_cols, "② 全部仍活着的币"),
                      (list(close.columns), "③ 全历史（含已下架）")):
        m, p, u = evaluate(cols, lab)
        res.append(m)
        keep[lab] = p
        print(f"  {lab}：池中位数 {int(u.sum(axis=1).median())} 个，"
              f"每腿 {int(round(u.sum(axis=1).median() * Q))} 个")
    show_(res)
    a100 = res[0]["年化"]
    print(f"\n  第二道筛选（只留 2026 年成交额前 100）的贡献：{a100 - res[1]['年化']:+.1%}")
    print(f"  第一道筛选（已下架币不在样本里）的贡献：{res[1]['年化'] - res[2]['年化']:+.1%}")
    print(f"  合计虚高：{a100 - res[2]['年化']:+.1%}  "
          f"（Sharpe {res[0]['Sharpe'] - res[2]['Sharpe']:+.2f}）")

    # 每腿人数是个混杂因素：全历史池 50 个 → 每腿 10 个，100 对面板只有 35 个 → 7 个
    print("\n  ⚠️ 池子越大每腿人数越多（10 个 vs 7 个），这也会影响结果。")
    print("     控制变量：把全历史池也限制到 35 个（每腿 7 个）再跑一遍 ↓")
    m_ctl, _, _ = evaluate(list(close.columns), "③' 全历史但池上限 35", top_n=35)
    m_ctl100, _, _ = evaluate(pick100, "①' 100 对但池上限 35", top_n=35)
    show_([m_ctl100, m_ctl])
    print(f"     控制后差额：{m_ctl100['年化'] - m_ctl['年化']:+.1%}"
          f"（对比控制前的 {res[0]['年化'] - res[2]['年化']:+.1%}）")

    # ── 退市惩罚
    print("\n" + "=" * 92)
    print("【退市处理】持有一个「次日没有行情」的币时，按不同口径计亏")
    print("=" * 92)
    p_all = keep["③ 全历史（含已下架）"]
    shk = [metrics(p_all["net"], "按最后成交价平仓（乐观）")]
    for s in (0.5, 1.0):
        _, p, _ = evaluate(list(close.columns), "", dead_shock=s)
        shk.append(metrics(p["net"], f"仓位亏 {s:.0%}"))
    show_(shk)
    print(f"\n  9 年里持仓撞上退市/停牌：多腿 {int(p_all['dead_l'].sum())} 次，"
          f"空腿 {int(p_all['dead_s'].sum())} 次，"
          f"平均每次命中 {p_all['dead_l'].mean() + p_all['dead_s'].mean():.2f} 个仓位。")
    print("  → 很少。退市币在死之前早就被流动性门槛踢出可选池了，")
    print("    所以**直接持有到退市的损失不是偏差来源**，偏差来自「池子里有谁」。")

    # ── 优化项在诚实池子上还成不成立
    print("\n" + "=" * 92)
    print("【优化项复核】在主报告里选出的 O1/O2/O4，放到全历史池上还成立吗")
    print("=" * 92)

    def vol_target_scale(p, target=0.20, lookback=63, cap=2.0):
        rv = p.rolling(lookback, min_periods=30).std() * np.sqrt(252)
        kk = (target / rv).clip(upper=cap).shift(1).fillna(1.0)
        return p * kk

    allc = list(close.columns)
    opt = [metrics(p_all["net"], "③ 基准（全历史池）")]
    m_o1, p_o1, _ = evaluate(allc, "", short_market=True)
    opt.append(m_o1)
    m_o4, p_o4, _ = evaluate(allc, "", skip_squeeze=3.0)
    opt.append(m_o4)
    opt.append(metrics(vol_target_scale(p_all["net"]), "O2 波动率目标 20%"))
    show_([dict(r, 方案=lab) for r, lab in zip(
        opt, ["③ 基准（全历史池）", "O1 空腿改空整池", "O4 挤空过滤 3σ", "O2 波动率目标 20%"])])
    print(f"\n  O2 平均杠杆 {float((vol_target_scale(p_all['net']) / p_all['net']).mean()):.2f}×")

    # ── 分年度
    print("\n" + "=" * 92)
    print("【分年度】三个池子的净年化")
    print("=" * 92)
    print(f"  {'年份':>6}{'① 100 对':>12}{'② 仍活着':>12}{'③ 全历史':>12}{'①−③':>10}")
    for y in sorted(set(p_all.index.year)):
        vals = []
        for lab in ("① 100 对面板（活币 + 2026 成交额前 100）", "② 全部仍活着的币",
                    "③ 全历史（含已下架）"):
            p = keep[lab]
            g = p.loc[p.index.year == y, "net"]
            vals.append((1 + g.mean()) ** 252 - 1 if len(g) > 60 else np.nan)
        if np.isnan(vals).any():
            continue
        print(f"  {y:>6}{vals[0]:>12.1%}{vals[1]:>12.1%}{vals[2]:>12.1%}"
              f"{vals[0] - vals[2]:>+10.1%}")

    # ── 导出
    os.makedirs(OUT, exist_ok=True)
    p_all.to_csv(os.path.join(OUT, "study_all_daily.csv"))
    print(f"\n明细已写 {os.path.relpath(os.path.join(OUT, 'study_all_daily.csv'), ROOT)}")


if __name__ == "__main__":
    main()
