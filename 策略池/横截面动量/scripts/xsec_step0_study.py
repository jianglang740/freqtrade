#!/usr/bin/env python
"""
第 0 步探索（不改信号，只问三个问题）：

  A. 伪时点币池 —— 用「当年就能知道的成交额排名」选池再回测，结果掉多少？
     这是对生存者偏差能给出的**最便宜的近似**（不能覆盖已下架币的缺失，见文末说明）。
  B. 调仓频率 —— 日频调仓是不是过度交易？持有 1/2/3/5/7/10/14/21/28 日各跑一遍。
  C. 信号衰减 —— 入选后第 1~28 日的多空超额收益曲线，决定 B 的答案该落在哪。
  D. 单币止损（用户最关心的问题）—— 持有期内单币逆向波动超过 kσ 就提前剔除。

与 V0–V7 分开：那批是事先声明的主结果，这批是探索，编号 B/A 前缀。
复用 `xsec_momentum_study.py` 的数据加载与信号构造，保证口径完全一致。

用法：
    source /opt/anaconda3/etc/profile.d/conda.sh && conda activate freqtrade
    python 策略池/横截面动量/scripts/xsec_step0_study.py
"""

from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from xsec_momentum_study import (  # noqa: E402
    ADV_MIN, FEE, Q, TOP_N,
    build_scores, load_funding, load_wide, metrics,
)

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), "out")   # 策略池/横截面动量/out

COLS = ("方案", "年化", "波动", "Sharpe", "最大回撤", "Calmar", "月度胜率")


def show_(rows: list[dict]) -> pd.DataFrame:
    """只印需要的列；`show` 在 xsec_momentum_study 里绑定了固定列，这里不复用。"""
    df = pd.DataFrame([{k: r[k] for k in COLS if k in r} for r in rows]).set_index("方案")
    fmt = df.copy()
    for c in ("年化", "波动", "最大回撤", "月度胜率"):
        fmt[c] = fmt[c].map(lambda v: f"{v:.1%}")
    for c in ("Sharpe", "Calmar"):
        fmt[c] = fmt[c].map(lambda v: f"{v:.2f}")
    print(fmt.to_string())
    return df


# ────────────────────────────────────────────── 可持有 k 日的回测器
def run_hold(score, ret, close, funding=None, lev=1.0, fee=FEE, every=1, q=Q,
             stop_sigma=None, vol=None, phase=0):
    """每 `every` 个交易日重建一次组合，持有期内不动（除强制剔除）。

    与 `run_portfolio` 的差别只有一个：中间那些日子不重新排名，所以不产生换手。
    持有期内权重**固定**（不随价格漂移），这一点会让成本略被低估 ——
    维护性漂移换手在下面单独估计并打印，不藏在结果里。

    stop_sigma: 持有期内，多头累计跌幅 / 空头累计涨幅 超过 k×入场时 28 日波动
                就把该币剔除（按当日收盘），换手照收费率。
    """
    dates = score.index
    wl = ws = None
    hold = {}                                   # name -> dict(side, entry, vol)
    rows = []
    for i, d in enumerate(dates[:-1]):
        d_next = ret.index[i + 1]
        r = ret.iloc[i + 1].replace([np.inf, -np.inf], np.nan)
        cost = 0.0

        if wl is None or (i - phase) % every == 0:
            s = score.loc[d].dropna()
            if len(s) < 10:
                continue
            k = max(2, int(round(len(s) * q)))
            longs = list(s.nlargest(k).index)
            shorts = list(s.nsmallest(k).index)
            tgt_l = pd.Series(1.0 / len(longs), index=longs)
            tgt_s = pd.Series(1.0 / len(shorts), index=shorts)
            if wl is not None:
                cost += fee * (tgt_l.sub(wl, fill_value=0).abs().sum()
                               + tgt_s.sub(ws, fill_value=0).abs().sum())
            wl, ws = tgt_l, tgt_s
            hold = {}
            for p in longs:
                hold[p] = dict(side=1, entry=close.at[d, p],
                               vol=vol.at[d, p] if vol is not None else np.nan)
            for p in shorts:
                hold[p] = dict(side=-1, entry=close.at[d, p],
                               vol=vol.at[d, p] if vol is not None else np.nan)

        # ── 剔除：停牌（没有下一日收益）或触及单币止损
        drop = set()
        for p, m in hold.items():
            if pd.isna(r.get(p)):
                drop.add(p)
                continue
            if stop_sigma is not None and np.isfinite(m["vol"]) and m["vol"] > 0:
                px = close.at[d, p]
                move = m["side"] * (px / m["entry"] - 1)     # 多头看跌、空头看涨
                if move < -stop_sigma * m["vol"]:
                    drop.add(p)
        if drop:
            for leg, w in (("l", wl), ("s", ws)):
                keep = w[[p for p in w.index if p not in drop]]
                if keep.empty:
                    wl = ws = None
                    break
                new = keep / keep.sum()
                names = new.index.union(w.index)
                cost += fee * float(
                    (new.reindex(names, fill_value=0)
                     - w.reindex(names, fill_value=0)).abs().sum())
                if leg == "l":
                    wl = new
                else:
                    ws = new
            for p in drop:
                hold.pop(p, None)
            if wl is None or ws is None:
                hold = {}
                continue

        rl = float((wl * r[wl.index]).sum())
        rs = float((ws * r[ws.index]).sum())
        f_cost = 0.0
        if funding is not None and d_next in funding.index:
            f_cost = float((wl * funding.loc[d_next, wl.index].fillna(0)).sum()
                           - (ws * funding.loc[d_next, ws.index].fillna(0)).sum())

        rows.append(dict(date=d, long=rl, short=rs, gross=rl - rs, cost=cost,
                         funding=f_cost, net=rl - rs - cost - f_cost,
                         n_long=len(wl), n_short=len(ws)))

    out = pd.DataFrame(rows).set_index("date")
    # cost 已按 lev/2 缩放；单边换手 = (tl+ts)/2 = cost/fee（lev=1 时）
    for c in ("gross", "net", "cost", "funding"):
        out[c] *= lev / 2.0
    out["turnover"] = out["cost"] / (fee * lev) if fee else np.nan
    return out


def maintenance_turnover(score, ret, every, q=Q):
    """持有期内「等权漂移」每天需要多少维护性换手（本回测未计入）。

    返回的是单边换手（与 B 表同口径）。
    """
    dates = score.index
    drift = []
    wl = ws = None
    for i, d in enumerate(dates[:-1]):
        r = ret.iloc[i + 1].replace([np.inf, -np.inf], np.nan)
        if wl is None or i % every == 0:
            s = score.loc[d].dropna()
            if len(s) < 10:
                continue
            k = max(2, int(round(len(s) * q)))
            wl = pd.Series(1.0 / k, index=s.nlargest(k).index)
            ws = pd.Series(1.0 / k, index=s.nsmallest(k).index)
            continue
        for leg in ("l", "s"):
            w = wl if leg == "l" else ws
            v = w * (1 + r.reindex(w.index).fillna(0))
            nw = v / v.sum()
            drift.append(float((nw - w).abs().sum()) / 2)
            if leg == "l":
                wl = nw
            else:
                ws = nw
    return float(np.mean(drift)) if drift else 0.0


# ────────────────────────────────────────────── A. 伪时点币池
def pit_universe(adv, cutoff, top_n=TOP_N, min_adv=ADV_MIN):
    """截至 cutoff 当天为止「看得见」的名单：当时已有成交额、且排名前列。"""
    snap = adv.loc[:cutoff].iloc[-1]
    names = snap[snap >= min_adv].nlargest(top_n).index
    m = pd.DataFrame(False, index=adv.index, columns=adv.columns)
    m.loc[cutoff:, names] = True
    return m, list(names)


# ────────────────────────────────────────────── C. 信号衰减
def decay_study(close, score, horizons=(1, 2, 3, 5, 7, 10, 14, 20, 28),
                q=Q, n_boot=400, block=28, seed=0):
    fwd = {h: close.shift(-h) / close - 1 for h in horizons}
    recs = []
    for d in score.index[:-max(horizons)]:
        s = score.loc[d].dropna()
        if len(s) < 10:
            continue
        k = max(2, int(round(len(s) * q)))
        L = list(s.nlargest(k).index)
        S = list(s.nsmallest(k).index)
        rec = {"date": d}
        for h in horizons:
            f = fwd[h].loc[d]
            rec[f"L{h}"] = f.reindex(L).mean()
            rec[f"S{h}"] = f.reindex(S).mean()
        recs.append(rec)
    df = pd.DataFrame(recs).set_index("date")

    rng = np.random.default_rng(seed)
    n = len(df)
    nb = int(np.ceil(n / block))
    out = []
    for h in horizons:
        exc = (df[f"L{h}"] - df[f"S{h}"]).dropna().values
        boots = []
        for _ in range(n_boot):
            starts = rng.integers(0, n, nb)
            idx = np.concatenate([(np.arange(s, s + block) % n) for s in starts])
            boots.append(exc[idx].mean())
        lo, hi = np.percentile(boots, [5, 95])
        out.append({"持有天数": h, "多头": df[f"L{h}"].mean(),
                    "空头": df[f"S{h}"].mean(), "多空差": exc.mean(),
                    "日均": exc.mean() / h, "5%": lo, "95%": hi})
    return df, pd.DataFrame(out)


# ────────────────────────────────────────────── main
def main() -> None:
    close, open_, qv = load_wide()
    ret = close.pct_change(fill_method=None)
    adv = qv.rolling(14, min_periods=10).mean()
    vol28 = ret.rolling(28, min_periods=20).std()
    funding = load_funding(close.index, close.columns)

    def univ(top_n=TOP_N):
        return (adv.where(adv >= ADV_MIN).rank(axis=1, ascending=False) <= top_n)

    u50 = univ(50)
    score = build_scores(close, ret, u50)

    print("=" * 92)
    print(f"样本 {close.index.min().date()} → {close.index.max().date()}，{close.shape[1]} 个币")
    print("=" * 92)

    # 自检：run_hold(every=1) 必须复现 V5 的 41.0% / −34.4%
    chk = run_hold(score, ret, close, funding=funding, every=1)
    mc = metrics(chk["net"], "自检 run_hold(every=1)")
    print(f"\n【自检】run_hold(every=1) 年化 {mc['年化']:.1%}  回撤 {mc['最大回撤']:.1%} "
          f"Sharpe {mc['Sharpe']:.2f}   （V5 报告值 41.0% / −34.4% / 1.49）")
    assert abs(mc["年化"] - 0.410) < 0.05, "自检失败：与 V5 口径不一致，先查代码"

    # ── B. 调仓频率
    print("\n" + "=" * 92)
    print("【B】调仓频率（其余规则 = V5，净收益，已扣手续费与资金费）")
    print("=" * 92)
    rows, btab = [], []
    for every in (1, 2, 3, 5, 7, 10, 14, 21, 28):
        p = run_hold(score, ret, close, funding=funding, every=every)
        m = metrics(p["net"], f"B{every:>2d} 每 {every:>2d} 日调仓")
        m["单边换手/日"] = p["cost"].mean() / FEE      # cost 已含 lev/2=0.5
        m["维护换手/日"] = maintenance_turnover(score, ret, every)
        rows.append(m)
        btab.append(m)
    bt = show_(btab)
    print("\n  换手与成本（单边，占名义本金）：")
    print(f"  {'调仓间隔':>8}{'重建换手/日':>14}{'维护换手/日':>14}{'合计换手/日':>14}{'年化成本':>12}")
    for r in btab:
        reb = r["单边换手/日"]
        mnt = r["维护换手/日"]
        print(f"  {r['方案'][1:3].strip():>8}{reb:>13.1%}{mnt:>13.1%}"
              f"{reb + mnt:>13.1%}{(reb + mnt) * FEE * 252:>11.1%}")
    print("  ⚠️ 维护换手是回测**未计入**的部分（本回测持有期内权重固定），列出来是为了不低估成本。")

    os.makedirs(OUT, exist_ok=True)
    pd.DataFrame(btab).to_csv(os.path.join(OUT, "study_step0_hold.csv"))

    # ── B' 相位稳健性：间隔固定但起始相位不同，看「更好」是不是只是对齐运气
    print("\n" + "=" * 92)
    print("【B'】相位稳健性：同一个调仓间隔，把所有起始相位都跑一遍")
    print("=" * 92)
    print("  如果一个间隔真的更好，不同相位应该都接近；散得开就说明是运气。")
    print(f"  {'间隔':>6}{'运行数':>8}{'年化中位':>12}{'年化最低':>12}{'年化最高':>12}"
          f"{'Sharpe中位':>13}{'回撤中位':>12}{'回撤最好':>12}")
    ph_rows = []
    for every in (2, 3, 5, 7):
        runs = []
        for ph in range(every):
            p = run_hold(score, ret, close, funding=funding, every=every, phase=ph)
            runs.append(metrics(p["net"], f"B{every} 相位{ph}"))
        a = [r["年化"] for r in runs]
        sh = [r["Sharpe"] for r in runs]
        dd = [r["最大回撤"] for r in runs]
        ph_rows += runs
        print(f"  {every:>6}{len(runs):>8}{np.median(a):>12.1%}{min(a):>12.1%}{max(a):>12.1%}"
              f"{np.median(sh):>13.2f}{np.median(dd):>12.1%}{max(dd):>12.1%}")
    pd.DataFrame(ph_rows).to_csv(os.path.join(OUT, "study_step0_phase.csv"), index=False)

    # ── C. 信号衰减
    print("\n" + "=" * 92)
    print("【C】信号衰减：入选后持有 h 日的收益（已实现口径，重叠窗口，块自助 90% 区间）")
    print("=" * 92)
    decay_df, dtab = decay_study(close, score)
    show_d = dtab.copy()
    for c in ("多头", "空头", "多空差", "日均", "5%", "95%"):
        show_d[c] = show_d[c].map(lambda v: f"{v:.2%}")
    print(show_d.to_string(index=False))
    print("  读法：「日均」= 多空差 / h，是这一持有时长的日化超额。"
          "它与 B 表的年化应当同向变化。")
    decay_df.to_csv(os.path.join(OUT, "study_step0_decay_daily.csv"))
    dtab.to_csv(os.path.join(OUT, "study_step0_decay.csv"), index=False)

    # ── A. 伪时点币池
    print("\n" + "=" * 92)
    print("【A】伪时点币池：用 cutoff 当天看得见的成交额排名选池，之后不再换人")
    print("=" * 92)
    print("  对照 = 同一天起、仍用「每日动态排行榜」的 V5。两者之差 = 币池组成变化带来的收益。")
    ar = []
    for cutoff in ("2019-12-31", "2020-12-31", "2021-12-31", "2022-12-31"):
        c = pd.Timestamp(cutoff, tz="UTC")
        mask, names = pit_universe(adv, c)
        # 当时点名单与今天动态榜的重合度 —— 用来判断「换人」到底多不多
        end_row = adv.loc[:].ffill().iloc[-1]
        end_top = set(end_row[end_row >= ADV_MIN].nlargest(TOP_N).index)
        overlap = len(set(names) & end_top)
        u_pit = mask & (adv >= ADV_MIN)
        s_pit = build_scores(close, ret, u_pit)
        p_pit = run_hold(s_pit, ret, close, funding=funding, every=1)
        p_ref = run_hold(score, ret, close, funding=funding, every=1)

        win_pit = p_pit.loc[c:]
        win_ref = p_ref.loc[c:]
        m_pit = metrics(win_pit["net"], f"A {cutoff[:7]} 选池 → 回测至今")
        m_ref = metrics(win_ref["net"], f"  对照：动态榜 → 同窗口")
        m_pit["币池大小"] = len(names)
        m_ref["币池大小"] = np.nan
        ar += [m_pit, m_ref]
        print(f"  · {cutoff[:7]} 时点池 {len(names)} 个币（与今天动态榜重合 {overlap} 个），"
              f"窗口 {c.date()} → 至今（{len(win_pit)} 天）")
        print(f"      时点池 年化 {m_pit['年化']:>7.1%}  Sharpe {m_pit['Sharpe']:>5.2f}  "
              f"回撤 {m_pit['最大回撤']:>7.1%}   ├ 动态榜 年化 {m_ref['年化']:>7.1%}  "
              f"Sharpe {m_ref['Sharpe']:>5.2f}  回撤 {m_ref['最大回撤']:>7.1%}")
        print(f"      差额   {m_pit['年化'] - m_ref['年化']:>+7.1%}                      "
              f"      {m_pit['Sharpe'] - m_ref['Sharpe']:>+5.2f}")

    # metrics() 里「年化/波动/回撤」都是小数，差额要用小数算，不能拿格式化后的值算
    show_(ar)
    pd.DataFrame(ar).to_csv(os.path.join(OUT, "study_step0_pit.csv"), index=False)

    # ── D. 单币止损
    print("\n" + "=" * 92)
    print("【D】持有期内单币止损：逆向波动超过 k×入场时 28 日波动 就剔除")
    print("=" * 92)
    dres = []
    # every=1 不测：当日重建组合，入场价就是当日收盘，止损条件恒为假
    for every in (3, 7):
        for k in (None, 3.0, 4.0, 5.0):
            p = run_hold(score, ret, close, funding=funding, every=every,
                         stop_sigma=k, vol=vol28)
            lab = f"D every={every:>2d} " + ("无止损" if k is None else f"k={k:.0f}σ")
            dres.append(metrics(p["net"], lab))
    show_(dres)
    pd.DataFrame(dres).to_csv(os.path.join(OUT, "study_step0_stop.csv"), index=False)

    print("\n" + "=" * 92)
    print("说明：A 只能覆盖「币池组成随时间变化」这一块偏差；**已下架币的缺失仍然看不到**，")
    print("所以 A 的差额是**下界**，真实折价只会更大。要真正回答这个问题，必须补")
    print("全历史符号（含已下架）的 1d 面板 —— 那是第 1 步。")
    print("=" * 92)


if __name__ == "__main__":
    main()
