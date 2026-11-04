#!/usr/bin/env python
"""
池子形状与波动率：主流币到底该不该剔除？

背景：现口径的可选池是「ADV≥$1M **且** 按 14 日 ADV 排名前 50」。
用户提出两个怀疑：
  1. BTC/ETH/BNB/SOL 这类大币永远在池子里，但它们波动小，占着名额不干活；
  2. 既然策略赚的是波动的钱，就该把低波动的剔除、把池子放宽（按 $1M 等距）。

先做两项诊断再改口径，诊断结论决定了变体表怎么设计：
  · ADV_MIN 在 2021 年后从未生效（每天 ADV≥$1M 的币中位 220 个），
    真正的旋钮是 TOP_N —— 所以「放宽 $1M 门槛」改成「扩 TOP_N」。
  · 白占名额的不是主流币，是稳定币/法币对：9 个交易对吃掉 8.7% 的持仓名额、
    pnl 合计为负。原因是信号 `动量/波动率`：稳定币波动率趋近 0，信号被放大成极端值。
    所以 Stage A 先修池子，这是 bug 修复，不是变体。

变体表**在跑之前就写死**（见 VARIANTS），跑完不按结果增删。
⚠️ B7 原本设计成「腿内权重上限 10%」，但在每腿 10 个币的等权组合里，
   10% 上限在数学上恒等于无操作。跑之前改成「波动率倒数加权」，理由记在这里。

用法：
    source /opt/anaconda3/etc/profile.d/conda.sh && conda activate freqtrade
    python 策略池/横截面动量/scripts/xsec_universe_study.py

产物：out/study_universe.csv（各变体指标）、out/universe_capacity.csv（容量曲线）
"""

from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), "out")

sys.path.insert(0, HERE)
from xsec_all_universe_study import ALL, load_wide  # noqa: E402
from xsec_momentum_study import (  # noqa: E402
    ADV_MIN, FEE, Q, build_scores, load_funding, metrics,
)

# 年化基准沿用仓库既有口径（252），不改成 365 —— 改了就没法和 32.7%/1.06 对比。
# ⚠️ 加密是 7×24 交易，252 会低估年化波动约 17%；这是全仓库一致的约定，不是这里引入的。
ANN = 252

# 稳定币 / 法币：波动率地板本来也能滤掉，但脱锚那一刻波动率会蹿上去，
# 必须**按名字**再排一道，否则 USDC 在 SVB 那种日子里会重新混进池子。
EXCLUDE_BASE = frozenset({
    "USDC", "BUSD", "TUSD", "USDP", "DAI", "FDUSD", "PAX", "USDD", "USDS",
    "EURC", "USD1", "UST", "USTC", "GUSD", "LUSD", "FRAX", "PYUSD",
    "EUR", "GBP", "TRY", "BRL", "AUD", "JPY", "ZAR", "RUB", "UAH", "NGN",
})

# 主流币名单：**现在写死，跑完不许改**（否则就是事后挑名单）
MAJORS = ("BTC", "ETH", "BNB", "SOL", "XRP", "DOGE", "ADA", "TRX",
          "LTC", "BCH", "LINK", "AVAX", "DOT", "TON")

VOL_FLOOR = 0.15       # 28 日年化波动率地板（252 基准）；稳定币≈1%，远在下面
TARGET_VOL = 0.20      # O2 波动率目标
LEG = 0.5              # 每条腿的名义本金 = 0.5 × AUM（1× 杠杆多空）
SIGMA_CAP = 0.25       # 冲击模型里日波动率的截断上限，见下方说明


# ────────────────────────────────────────────── 池子
def build_pool(adv, vol_ann, top_n, exclude=frozenset(), vol_floor=VOL_FLOOR,
               vol_drop_lo=None, vol_drop_hi=None):
    """合格集合 → 按 ADV 取前 top_n。

    关键设计：**过滤发生在排名之前**，所以池子大小恒为 top_n。
    这样 B1/B2 只改「大小」，B3/B4/B5 只改「成分」，两个维度不互相污染。
    （另一种做法是先取前 50 再过滤，池子会缩到 ~36 —— 那是把两件事搅在一起。）

    vol_floor:   绝对波动率地板（年化）。A0 传 None 以复现旧池。
    vol_drop_lo / vol_drop_hi: 剔除当日合格集合内波动率最低/最高的这一比例。
    """
    cols = [c for c in adv.columns if c.split("/")[0] not in exclude]
    ok = adv[cols].notna() & (adv[cols] >= ADV_MIN)
    if vol_floor is not None:
        ok &= (vol_ann[cols] >= vol_floor)
    if vol_drop_lo is not None or vol_drop_hi is not None:
        rk = vol_ann[cols].where(ok).rank(axis=1, pct=True)
        if vol_drop_lo is not None:
            ok &= rk > vol_drop_lo
        if vol_drop_hi is not None:
            ok &= rk < 1 - vol_drop_hi
    return ok & (adv[cols].where(ok).rank(axis=1, ascending=False) <= top_n)


# ────────────────────────────────────────────── 组合
def run_portfolio(score, ret, adv, vol28, funding=None, q=Q, fee=FEE,
                  inv_vol=None, dead_shock=0.0):
    """逐日重建等权多空组合，返回日频明细。

    与 `xsec_all_universe_study.run` 的差别：把冲击成本的**无量纲核**单独存下来，
    见 `imp_S`。平方根冲击模型里
        impact(日, 按 AUM 计的收益率) = 0.5 · k · sqrt(0.5·AUM) · imp_S
    与 AUM、k 都解耦，所以容量曲线不用重跑回测。
    退市惩罚也是线性的（dead_shock × lost_w），同样存下来后算。
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
        shorts = list(s.nsmallest(k).index)
        if not longs or not shorts:
            continue

        def weights(names):
            if inv_vol is not None:
                iv = 1.0 / inv_vol.loc[d, names].replace(0, np.nan)
                if not iv.notna().any():
                    return pd.Series(1.0 / len(names), index=names)
                iv = iv.fillna(iv.mean())
                return iv / iv.sum()
            return pd.Series(1.0 / len(names), index=names)

        wl, ws = weights(longs), weights(shorts)
        dl = wl.sub(prev_l, fill_value=0)
        ds = ws.sub(prev_s, fill_value=0)
        tl, ts = float(dl.abs().sum()), float(ds.abs().sum())

        rl, rs = r.reindex(wl.index), r.reindex(ws.index)
        lost_l, lost_s = rl.isna(), rs.isna()
        long_ret = float((wl * rl.fillna(0)).sum())
        short_ret = float((ws * rs.fillna(0)).sum())
        lost_w = float(wl[lost_l].sum()) + float(ws[lost_s].sum())

        f_cost = 0.0
        if funding is not None and d_next in funding.index:
            f_cost = float((wl * funding.loc[d_next, wl.index].fillna(0)).sum()
                           - (ws * funding.loc[d_next, ws.index].fillna(0)).sum())

        def imp_core(dw):
            """{σ·|Δw|^1.5 / √ADV} 的无量纲核。

            ⚠️ σ 必须截断：面板里有 3 个币日的收益超过 100 倍（LUNA 2022-05-31
            换合约，单日 177,399x），28 日波动率被算成 33,525，
            不截断会把这一天的冲击成本放大 5 个数量级、污染整列统计。
            25%/日 的上限远高于正常加密币（池内中位约 6%）。
            """
            names = dw.index[dw.abs() > 1e-12]
            if not len(names):
                return 0.0
            x = dw[names].abs()
            sig = vol28.loc[d, names].clip(upper=SIGMA_CAP)
            v = sig * x ** 1.5 / np.sqrt(adv.loc[d, names])
            return float(v.replace([np.inf, -np.inf], np.nan).fillna(0).sum())

        rows.append(dict(
            date=d, long=long_ret, short=short_ret,
            gross=long_ret - short_ret,
            cost_fee=fee * (tl + ts),
            funding=f_cost,
            imp_S=imp_core(dl) + imp_core(ds),
            lost_w=lost_w,
            ext_w=float(wl[rl.abs() > 1].sum()) + float(ws[rs.abs() > 1].sum()),
            n_long=len(longs), n_short=len(shorts),
            turnover=(tl + ts) / 2,
        ))
        prev_l, prev_s = wl, ws

    out = pd.DataFrame(rows).set_index("date")
    for c in ("long", "short", "gross", "cost_fee", "funding"):
        out[c] *= LEG
    out["net"] = out["gross"] - out["cost_fee"] - out["funding"]
    return out


def impact_series(detail: pd.DataFrame, aum: float, k_imp: float) -> pd.Series:
    """平方根冲击模型的成本序列（按 AUM 计的收益率）。推导见 run_portfolio。"""
    return 0.5 * k_imp * np.sqrt(LEG * aum) * detail["imp_S"]


def net_with(detail: pd.DataFrame, aum: float | None = None, k_imp: float = 0.3,
             dead_shock: float = 0.0) -> pd.Series:
    p = detail["net"] - dead_shock * LEG * detail["lost_w"]
    if aum:
        p = p - impact_series(detail, aum, k_imp)
    return p


def vol_target(p: pd.Series, target=TARGET_VOL, lookback=63, cap=2.0) -> pd.Series:
    """O2：按 63 日已实现波动把仓位缩放到目标波动，只用 t-1 的信息。"""
    rv = p.rolling(lookback, min_periods=30).std() * np.sqrt(ANN)
    kk = (target / rv).clip(upper=cap).shift(1).fillna(1.0)
    return p * kk


# ────────────────────────────────────────────── 指标
def tail_stats(p: pd.Series) -> dict:
    """回测里不显眼、实盘里最要命的那几个数。"""
    p = p.dropna()
    eq = (1 + p).cumprod()
    peak = eq.cummax()
    underwater = (eq < peak)
    # 最长水下天数（峰值到回本，未回本则算到样本末）
    longest, cur = 0, 0
    for u in underwater:
        cur = cur + 1 if u else 0
        longest = max(longest, cur)
    m = p.resample("ME").apply(lambda x: (1 + x).prod() - 1)
    ep = p.loc["2025-11-06":"2026-09-14"]
    return {
        "最差单日": p.min(),
        "最差5日合计": (1 + p.nsmallest(5)).prod() - 1,
        "最差单月": m.min(),
        "最长水下(交易日)": int(longest),
        "2025-11起那一段": (1 + ep).prod() - 1 if len(ep) > 20 else np.nan,
    }


def show(df: pd.DataFrame, cols=None, pct=("年化", "波动", "最大回撤", "月度胜率",
                                           "最差单日", "最差5日合计", "最差单月",
                                           "2025-11起那一段", "2023+年化")) -> None:
    fmt = df[cols] if cols else df.copy()
    for c in fmt.columns:
        if c in pct:
            fmt[c] = fmt[c].map(lambda v: "" if pd.isna(v) else f"{v:.1%}")
        elif c in ("Sharpe", "Calmar", "日偏度", "2023+Sharpe"):
            fmt[c] = fmt[c].map(lambda v: "" if pd.isna(v) else f"{v:.2f}")
        elif c in ("最长水下(交易日)", "样本天数", "每腿个数", "池大小"):
            fmt[c] = fmt[c].map(lambda v: "" if pd.isna(v) else f"{v:.0f}")
    print(fmt.to_string())


# 变体表：跑之前写死，跑完不增删
VARIANTS = [
    dict(id="A0", 名="旧池（对照，未修）", top_n=50),
    dict(id="B0", 名="修池后基准", top_n=50, fix_pool=True),
    dict(id="B1", 名="TOP_N 50→100", top_n=100, fix_pool=True),
    dict(id="B2", 名="TOP_N 50→150", top_n=150, fix_pool=True),
    dict(id="B3", 名="剔除 14 个主流币", top_n=50, fix_pool=True, exclude_majors=True),
    dict(id="B4", 名="剔除当日最低波动 20%", top_n=50, fix_pool=True, vol_drop_lo=0.20),
    dict(id="B5", 名="剔除当日最高波动 20%", top_n=50, fix_pool=True, vol_drop_hi=0.20),
    dict(id="B6", 名="原始动量（不做波动率调整）", top_n=50, fix_pool=True, vol_adj=False),
    dict(id="B7", 名="腿内波动率倒数加权", top_n=50, fix_pool=True, inv_vol=True),
]


def main() -> None:
    close, qv, alive, last = load_wide(ALL)
    ret = close.pct_change(fill_method=None)
    adv = qv.rolling(14, min_periods=10).mean()
    vol28 = ret.rolling(28, min_periods=20).std()
    vol_ann = vol28 * np.sqrt(ANN)

    print("=" * 96)
    print(f"诚实池 {close.shape[1]} 个币，{close.index.min().date()} → {close.index.max().date()}")
    stable_cols = [c for c in close.columns if c.split("/")[0] in EXCLUDE_BASE]
    print(f"按名字剔除的稳定币/法币对：{len(stable_cols)} 个"
          f"（面板里实际存在的：{' '.join(sorted(stable_cols))}）")

    funding = load_funding(close.index, close.columns)
    print(f"资金费覆盖 {int(funding.notna().any().sum())}/{close.shape[1]} 个币（缺的按 0 计，偏乐观）")

    # ── 跑变体
    detail, pools = {}, {}
    print("\n" + "=" * 96)
    print("【0】池子诊断：修池前后每天都在交易什么")
    print("=" * 96)
    for v in VARIANTS:
        excl = frozenset(EXCLUDE_BASE) if v.get("fix_pool") else frozenset()
        if v.get("exclude_majors"):
            excl = excl | frozenset(MAJORS)
        pool = build_pool(adv, vol_ann, v["top_n"], exclude=excl,
                          vol_floor=VOL_FLOOR if v.get("fix_pool") else None,
                          vol_drop_lo=v.get("vol_drop_lo"),
                          vol_drop_hi=v.get("vol_drop_hi"))
        score = build_scores(close, ret, pool, vol_adj=v.get("vol_adj", True))
        detail[v["id"]] = run_portfolio(
            score, ret, adv, vol28, funding=funding,
            inv_vol=vol28 if v.get("inv_vol") else None)
        pools[v["id"]] = pool
        size = pool.sum(axis=1)
        print(f"  {v['id']} {v['名']:<24} 池中位 {size.median():3.0f}  "
              f"每腿 {int(round(size.median() * Q)):2d}  "
              f"2026 年池中位 {size[size.index.year == 2026].median():3.0f}")

    # 旧池里到底有多少稳定币
    old_pool = pools["A0"]
    st = [c for c in old_pool.columns if c.split("/")[0] in EXCLUDE_BASE]
    st_share = float(old_pool[st].sum().sum() / old_pool.sum().sum())
    print(f"\n  旧池里稳定币/法币占据的持仓名额比例：{st_share:.1%}")
    print(f"  修池后池内波动率中位（年化）："
          f"{float(vol_ann.where(pools['B0']).median().median()):.1%}")

    flat = ret.stack()
    ext = flat[flat.abs() > 1]
    print(f"\n  ⚠️ 面板里 |日收益| > 100% 的币日 {len(ext)} 个"
          f"（最大 {flat.max():,.0f}× @ {flat.idxmax()[1]} {flat.idxmax()[0].date()}，"
          f"换合约造成的价格断裂）")
    for v in VARIANTS:
        print(f"     {v['id']} 持仓命中极端收益的仓位权重合计 "
              f"{float(detail[v['id']]['ext_w'].sum()):.3f}")

    # ── 主表：不归 vs O2 归
    print("\n" + "=" * 96)
    print("【1】变体主表（纯费率成本，无冲击成本）")
    print("=" * 96)
    rows = []
    for v in VARIANTS:
        d = detail[v["id"]]
        p = net_with(d)
        cur_p = vol_target(p).loc["2023-01-01":]
        rows.append({**metrics(p), "方案": f"{v['id']} {v['名']}",
                     "2023+年化": (1 + cur_p.mean()) ** ANN - 1,
                     "2023+Sharpe": cur_p.mean() / cur_p.std() * np.sqrt(ANN)})
        rows.append({**metrics(vol_target(p)), "方案": f"{v['id']} +O2",
                     "2023+年化": (1 + cur_p.mean()) ** ANN - 1,
                     "2023+Sharpe": cur_p.mean() / cur_p.std() * np.sqrt(ANN)})
    tab = pd.DataFrame(rows).set_index("方案")
    show(tab[["年化", "波动", "Sharpe", "最大回撤", "Calmar",
              "2023+年化", "2023+Sharpe", "月度胜率"]])
    print("\n  ⚠️ 2023 年起这一列才是「现在还能不能跑」的答案：")
    print("     2018–2021 的 ADV 只有 $5–15M、日波动 10–15%，那几年贡献了全样本的大部分收益。")
    print("\n  ⚠️ 不归一时「波动」列必须一起看：去主流币/扩池会整体抬高波动，")
    print("     不归一的话「年化更高」可能只是「杠杆更高」。")

    # ── 尾部（只在 O2 归一后看，否则波动不同没法比）
    print("\n" + "=" * 96)
    print("【2】尾部指标（全部 O2 归一后）")
    print("=" * 96)
    tail = [{"方案": f"{v['id']} {v['名']}", **tail_stats(vol_target(net_with(detail[v["id"]])))}
            for v in VARIANTS]
    tail = pd.DataFrame(tail).set_index("方案")
    show(tail)

    # ── 冲击成本
    print("\n" + "=" * 96)
    print("【3】平方根冲击成本：cost = 费率 + k·σ·√(下单额/ADV)")
    print("=" * 96)
    print(f"  {'变体':<26}{'2023+年化':>10}{'Sharpe':>8}{'最大回撤':>10}"
          f"{'费率占毛利':>11}{'冲击占毛利':>11}")
    for v in VARIANTS:
        d = detail[v["id"]]
        cur = d[d.index >= "2023-01-01"]
        p = vol_target(net_with(d, aum=1e7, k_imp=0.3))
        m = metrics(p[p.index >= "2023-01-01"])
        g = float(cur["gross"].sum())
        def share(x: float) -> str:
            # 毛利接近 0 或为负时百分比没有意义，直接标 —
            return f"{x / g:.1%}" if g > 0.2 else "—"
        print(f"  {v['id'] + ' ' + v['名']:<26}{m['年化']:>10.1%}{m['Sharpe']:>8.2f}"
              f"{m['最大回撤']:>10.1%}{share(float(cur['cost_fee'].sum())):>11}"
              f"{share(float(impact_series(cur, 1e7, 0.3).sum())):>11}")
    print("\n  ⚠️ 只报 2023 年起：2018–2020 的 ADV 只有 $5–15M、日波动 10–15%，")
    print("     冲击成本比现在高一个量级，全样本平均会把容量问题夸大到失真。")

    # ── 容量曲线
    print("\n" + "=" * 96)
    print("【4】容量曲线（2023 年起，k=0.3，O2 归一后年化）")
    print("=" * 96)
    aums = [1e6, 5e6, 1e7, 3e7, 5e7, 1e8]
    cap = {}
    for v in VARIANTS:
        d = detail[v["id"]]
        cap[v["id"]] = []
        for a in aums:
            p = vol_target(net_with(d, aum=a, k_imp=0.3))
            cap[v["id"]].append(metrics(p[p.index >= "2023-01-01"])["年化"])
    capdf = pd.DataFrame(cap, index=[f"{a / 1e6:.0f}M" for a in aums]).T
    show(capdf, pct=capdf.columns)

    # ── k 敏感性
    print("\n  k 敏感性（2023 年起，AUM=$10M）：")
    ks = [0.1, 0.3, 0.5]
    kdf = pd.DataFrame(
        {f"k={k}": [metrics(vol_target(net_with(detail[v["id"]], aum=1e7, k_imp=k))
                           .loc["2023-01-01":])["年化"] for v in VARIANTS] for k in ks},
        index=[v["id"] for v in VARIANTS])
    show(kdf, pct=kdf.columns)

    # ── 退市惩罚
    print("\n" + "=" * 96)
    print("【5】退市/停牌惩罚：持仓撞上「次日无行情」按半仓亏损计（O2 归一后年化）")
    print("=" * 96)
    print(f"  {'变体':<6}{'正常':>10}{'半仓亏损':>10}{'命中仓位·次':>12}")
    for v in VARIANTS:
        d = detail[v["id"]]
        a = metrics(vol_target(net_with(d)))["年化"]
        b = metrics(vol_target(net_with(d, dead_shock=0.5)))["年化"]
        print(f"  {v['id']:<6}{a:>10.1%}{b:>10.1%}{float(d['lost_w'].sum()):>12.1f}")

    # ── 分年度
    print("\n" + "=" * 96)
    print("【6】分年度净年化（O2 归一后）")
    print("=" * 96)
    yrs = sorted({d for d in detail["A0"].index.year})
    hdr = f"  {'年':>6}" + "".join(f"{v['id']:>9}" for v in VARIANTS)
    print(hdr)
    for y in yrs:
        line = f"  {y:>6}"
        for v in VARIANTS:
            p = vol_target(net_with(detail[v["id"]]))
            g = p[p.index.year == y]
            line += f"{(1 + g.mean()) ** ANN - 1:>9.1%}" if len(g) > 60 else f"{'—':>9}"
        print(line)

    # ── 相关性：变体之间是不是同一个东西
    print("\n【7】O2 归一后，各变体日收益与 B0 的相关性")
    b0 = vol_target(net_with(detail["B0"]))
    for v in VARIANTS:
        if v["id"] == "B0":
            continue
        x = vol_target(net_with(detail[v["id"]]))
        j = pd.concat([b0, x], axis=1, keys=["b0", "x"]).dropna()
        print(f"  {v['id']:<4} {v['名']:<24} 相关 {j['b0'].corr(j['x']):.2f}  "
              f"（B0 换手 {b0.index.size} 天，该变体 {x.index.size} 天）")

    # ── 落盘
    os.makedirs(OUT, exist_ok=True)
    tab.to_csv(os.path.join(OUT, "study_universe.csv"))
    capdf.to_csv(os.path.join(OUT, "universe_capacity.csv"))
    print(f"\n已写 {os.path.relpath(os.path.join(OUT, 'study_universe.csv'), os.path.dirname(os.path.dirname(HERE)))}")


if __name__ == "__main__":
    main()
