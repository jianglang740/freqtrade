#!/usr/bin/env python
"""
回测结果可视化。所有图直接存 PNG，不弹窗（服务器/终端里跑也不会卡住）。

图的口径和 engine 的统计表一致：分层曲线来自**逐窗口真实组合收益**，
不是把重叠窗口的远期收益直接连乘（那会重复复利，见股票框架的血泪教训）。
"""

from __future__ import annotations

import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

# PingFang SC 放第一位：Arial Unicode MS 没有 bold 字重，标题加粗时会刷一屏
# "Failed to find font weight bold" 的 findfont 警告。
plt.rcParams["font.sans-serif"] = ["PingFang SC", "Arial Unicode MS", "Heiti SC", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False


def _save(fig, outdir: str, name: str) -> str:
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, name)
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return path


def _equity(sim: pd.DataFrame, col: str = "net") -> pd.Series:
    return (1 + sim[col]).cumprod()


def _colors(n: int) -> dict[int, str]:
    base = ["#2E5AAC", "#4C9F70", "#D9A441", "#C75B39", "#7B5AA6",
            "#3E8E9C", "#B5486B", "#6F8F3A", "#8A6E4B", "#5A5A5A"]
    c = {i + 1: base[i % len(base)] for i in range(n)}
    c[1] = "#2E5AAC"
    c[n] = "#C0392B"
    return c


def _fmt_date(ax, index, n_ticks: int = 8):
    """给日期轴打「YYYY-MM」刻度。

    注意：`index` 为空时必须直接返回。空刻度会让坐标轴退化成没有刻度定位器的
    奇异变换，后面 `set_xticklabels` 会抛 `LinAlgError: Singular matrix` ——
    这正是 um/weekly 那种「某持有窗口没有有效调仓日」时炸掉的原因。
    """
    if index is None or len(index) == 0:
        ax.set_xlim(0, 1)          # 显式给个非退化范围，避免 tight_layout 报错
        return
    idx = pd.DatetimeIndex(index)
    if len(idx) <= n_ticks:
        pos = list(idx)
    else:
        sel = np.linspace(0, len(idx) - 1, n_ticks, dtype=int)
        pos = [idx[i] for i in sel]
    ax.set_xticks(pos)
    ax.set_xticklabels([pd.Timestamp(t).strftime("%Y-%m") for t in pos],
                       rotation=45, fontsize=9)


# ══════════════════════════════════════════════ 分层累计净值
def plot_quantile_curves(res: dict, outdir: str) -> list[str]:
    cfg = res["cfg"]
    var = res["variants"][res["effective"]]
    colors = _colors(cfg.quantiles)
    paths = []
    for h, sims in var["qsims"].items():
        if not sims:
            continue
        fig, ax = plt.subplots(figsize=(13, 7))
        for q, sim in sims.items():
            if sim is None or sim.empty:
                continue
            eq = _equity(sim)
            lw = 2.8 if q in (1, cfg.quantiles) else 1.3
            ax.plot(eq.index, eq.values, color=colors.get(q, "gray"), linewidth=lw,
                    label=f"Q{q}" + ("（空腿）" if q == 1 else "（多腿）" if q == cfg.quantiles else ""))
        ax.axhline(1, color="gray", linewidth=0.8)
        ax.set_title(f"分层累计净值 · 持有 {h} 个调仓窗口（{cfg.rebalance} 调仓，"
                     f"{cfg.weighting} 加权，T+1 {cfg.exec_price} 成交）", fontsize=13, fontweight="bold")
        ax.set_ylabel("净值（1 = 起点）")
        ax.grid(True, alpha=0.3)
        ax.legend(ncol=2, fontsize=9)
        _fmt_date(ax, eq.index)
        paths.append(_save(fig, outdir, f"quantile_curves_{h}.png"))
    return paths


# ══════════════════════════════════════════════ 绩效仪表盘
def plot_dashboard(res: dict, outdir: str) -> str:
    cfg = res["cfg"]
    var = res["variants"][res["effective"]]
    hs = list(cfg.hold_windows)
    fig, axes = plt.subplots(2, 2, figsize=(15, 10))

    # 1) 多空净净值
    ax = axes[0, 0]
    for h in hs:
        sim = var["sims"].get(h)
        if sim is not None and not sim.empty:
            eq = _equity(sim)
            ax.plot(eq.index, eq.values, label=f"持有 {h} 窗口")
    ax.axhline(1, color="gray", linewidth=0.8)
    ax.set_title("多空组合净净值（含成本与资金费）", fontweight="bold")
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3)

    # 2) IC 均值
    ax = axes[0, 1]
    ic = [var["signal"][h]["ic"].mean() for h in hs]
    ax.bar(range(len(hs)), ic, color="#4C72B0", alpha=0.85)
    ax.axhline(0, color="gray", linewidth=0.8)
    ax.set_xticks(range(len(hs)))
    ax.set_xticklabels([f"{h} 窗口" for h in hs])
    ax.set_title("Spearman IC 均值", fontweight="bold")
    for i, v in enumerate(ic):
        if np.isfinite(v):
            ax.text(i, v, f"{v:.3f}", ha="center", va="bottom" if v >= 0 else "top", fontsize=9)
    ax.grid(True, alpha=0.3, axis="y")

    # 3) 分位平均远期收益（第一个持有窗口）
    ax = axes[1, 0]
    h0 = hs[0]
    fq = var["signal"][h0].get("fwd_q")
    if isinstance(fq, pd.Series) and len(fq):
        ax.bar(fq.index.astype(str), fq.values, color="#DD8452", alpha=0.9)
        ax.axhline(0, color="gray", linewidth=0.8)
        for i, v in enumerate(fq.values):
            ax.text(i, v, f"{v:.3f}", ha="center", va="bottom" if v >= 0 else "top", fontsize=8)
    ax.set_title(f"分位平均远期收益（持有 {h0} 窗口）", fontweight="bold")
    ax.set_xlabel("分位（Q1 最低 → Qn 最高）")
    ax.grid(True, alpha=0.3, axis="y")

    # 4) IC 时序（第一个持有窗口）
    ax = axes[1, 1]
    s = var["signal"][h0]["ic"]
    if len(s):
        ax.plot(s.index, s.values, color="#6A4C93", linewidth=1, alpha=0.8)
        ax.axhline(0, color="gray", linewidth=0.8)
        ax.axhline(s.mean(), color="#C0392B", linestyle="--", linewidth=1,
                   label=f"均值 {s.mean():.3f}")
        ax.legend(fontsize=9)
    ax.set_title(f"IC 时间序列（持有 {h0} 窗口）", fontweight="bold")
    ax.grid(True, alpha=0.3)
    _fmt_date(ax, s.index)

    fig.suptitle(f"{res['factor']} · {cfg.market}/{cfg.tf} · {cfg.rebalance} 调仓", fontsize=15, fontweight="bold")
    fig.tight_layout()
    return _save(fig, outdir, "dashboard.png")


# ══════════════════════════════════════════════ IC 分布
def plot_ic(res: dict, outdir: str) -> str:
    cfg = res["cfg"]
    h0 = cfg.hold_windows[0]
    var = res["variants"][res["effective"]]
    ic = var["signal"][h0]["ic"]
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    ax = axes[0]
    if len(ic):
        ax.hist(ic.values, bins=40, color="#4C72B0", alpha=0.75)
        ax.axvline(0, color="gray", linestyle="--", linewidth=1)
        ax.axvline(ic.mean(), color="#C0392B", linestyle="--", linewidth=1.2,
                   label=f"均值 {ic.mean():.4f} / ICIR {ic.mean() / ic.std(ddof=1):.3f}"
                   if ic.std(ddof=1) > 0 else f"均值 {ic.mean():.4f}")
        ax.legend(fontsize=10)
    ax.set_title(f"IC 分布（持有 {h0} 窗口）", fontweight="bold")
    ax.grid(True, alpha=0.3)

    ax = axes[1]
    if len(ic):
        ax.plot(ic.index, ic.cumsum().values, color="#4C72B0", linewidth=1.5)
        ax.axhline(0, color="gray", linewidth=0.8)
    ax.set_title("累计 IC（检验信号是否持续为正）", fontweight="bold")
    ax.grid(True, alpha=0.3)
    _fmt_date(ax, ic.index)
    fig.tight_layout()
    return _save(fig, outdir, "ic_distribution.png")


# ══════════════════════════════════════════════ FM 回归
def plot_fm(res: dict, outdir: str) -> str:
    cfg = res["cfg"]
    h0 = cfg.hold_windows[0]
    fm = res["variants"][res["effective"]]["signal"][h0]["fm"]
    s = fm.get("slope_series", pd.Series(dtype=float))
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    ax = axes[0]
    if len(s):
        ax.plot(s.index, s.values, color="#4C72B0", linewidth=1.1)
        ax.axhline(0, color="gray", linewidth=0.8)
        ax.axhline(fm["mean_slope"], color="#C0392B", linestyle="--", linewidth=1.1,
                   label=f"均值 {fm['slope_bp']:.1f} BP / t_NW={fm['t_nw']:.2f}")
        ax.legend(fontsize=9)
    ax.set_title(f"Fama-MacBeth 逐期截面斜率（持有 {h0} 窗口）", fontweight="bold")
    ax.grid(True, alpha=0.3)
    _fmt_date(ax, s.index)

    ax = axes[1]
    if len(s):
        ax.hist(s.values, bins=40, color="#DD8452", alpha=0.8)
        ax.axvline(0, color="gray", linestyle="--", linewidth=1)
    ax.set_title("FM 斜率分布", fontweight="bold")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    return _save(fig, outdir, "fm_regression.png")


# ══════════════════════════════════════════════ naive vs NW t
def plot_t_compare(res: dict, outdir: str) -> str:
    cfg = res["cfg"]
    var = res["variants"][res["effective"]]
    hs = list(cfg.hold_windows)
    from engine import _tstat

    triplets = [
        ("IC 的 t 值",
         [var["signal"][h].get("ic_t_naive", np.nan) for h in hs],
         [var["signal"][h].get("ic_t_nw", np.nan) for h in hs]),
        ("FM 斜率的 t 值",
         [var["signal"][h]["fm"].get("t_naive", np.nan) for h in hs],
         [var["signal"][h]["fm"].get("t_nw", np.nan) for h in hs]),
        ("多空组合的 t 值",
         [_tstat(var["sims"][h]["net"], 0) if h in var["sims"] else np.nan for h in hs],
         [_tstat(var["sims"][h]["net"], h - 1) if h in var["sims"] else np.nan for h in hs]),
    ]
    x = np.arange(len(hs))
    width = 0.38
    fig, axes = plt.subplots(1, 3, figsize=(17, 5))
    for ax, (title, naive, nw) in zip(axes, triplets):
        ax.bar(x - width / 2, naive, width, color="#4C72B0", label="naive t")
        ax.bar(x + width / 2, nw, width, color="#DD8452", label="Newey-West t")
        ax.axhline(1.96, color="#2E7D32", linestyle="--", linewidth=1, alpha=0.8, label="|t|=1.96")
        ax.axhline(-1.96, color="#2E7D32", linestyle="--", linewidth=1, alpha=0.8)
        ax.set_xticks(x)
        ax.set_xticklabels([f"{h} 窗口" for h in hs])
        ax.set_title(title, fontweight="bold")
        ax.legend(fontsize=8)
        ax.grid(True, alpha=0.3, axis="y")
    fig.suptitle("naive vs Newey-West t 值（修正自相关后的显著性）", fontsize=14, fontweight="bold")
    fig.tight_layout()
    return _save(fig, outdir, "t_value_compare.png")


# ══════════════════════════════════════════════ 中性化对比
def plot_neutralization(res: dict, outdir: str) -> str | None:
    if not res["cfg"].neutralize or "neut" not in res["variants"]:
        return None
    cfg = res["cfg"]
    h0 = cfg.hold_windows[0]
    raw = res["variants"]["raw"]
    neut = res["variants"]["neut"]
    fig, axes = plt.subplots(1, 3, figsize=(18, 5.2))

    ax = axes[0]
    for name, var, ls in (("原始", raw, "-"), ("中性化", neut, "--")):
        sim = var["sims"].get(h0)
        if sim is not None and not sim.empty:
            eq = _equity(sim)
            ax.plot(eq.index, eq.values, linestyle=ls, linewidth=1.6, label=name)
    ax.axhline(1, color="gray", linewidth=0.8)
    ax.set_title(f"多空净净值：原始 vs 中性化（持有 {h0} 窗口）", fontweight="bold")
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3)

    ax = axes[1]
    raw_ic = [raw["signal"][h]["ic"].mean() for h in cfg.hold_windows]
    neu_ic = [neut["signal"][h]["ic"].mean() for h in cfg.hold_windows]
    x = np.arange(len(cfg.hold_windows))
    w = 0.38
    ax.bar(x - w / 2, raw_ic, w, color="#4C72B0", label="原始 IC")
    ax.bar(x + w / 2, neu_ic, w, color="#DD8452", label="中性化 IC")
    ax.axhline(0, color="gray", linewidth=0.8)
    ax.set_xticks(x)
    ax.set_xticklabels([f"{h} 窗口" for h in cfg.hold_windows])
    ax.set_title("各持有窗口 IC：原始 vs 中性化", fontweight="bold")
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3, axis="y")

    ax = axes[2]
    raw_r = [raw["sims"][h]["net"].mean() if h in raw["sims"] else np.nan for h in cfg.hold_windows]
    neu_r = [neut["sims"][h]["net"].mean() if h in neut["sims"] else np.nan for h in cfg.hold_windows]
    ax.bar(x - w / 2, raw_r, w, color="#4C72B0", label="原始")
    ax.bar(x + w / 2, neu_r, w, color="#DD8452", label="中性化")
    ax.axhline(0, color="gray", linewidth=0.8)
    ax.set_xticks(x)
    ax.set_xticklabels([f"{h} 窗口" for h in cfg.hold_windows])
    ax.set_title("平均窗口净收益：原始 vs 中性化", fontweight="bold")
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3, axis="y")

    fig.suptitle(f"风格中性化（{cfg.neutralize}）：剥离流动性与 BTC Beta 后还剩多少",
                 fontsize=14, fontweight="bold")
    fig.tight_layout()
    return _save(fig, outdir, "neutralization_compare.png")


# ══════════════════════════════════════════════ 净值 / 回撤 / 成本
def plot_equity_drawdown(res: dict, outdir: str) -> str:
    cfg = res["cfg"]
    h0 = cfg.hold_windows[0]
    sim = res["variants"][res["effective"]]["sims"].get(h0)
    fig, axes = plt.subplots(3, 1, figsize=(13, 10), sharex=True,
                             gridspec_kw={"height_ratios": [2.2, 1, 1]})
    if sim is None or sim.empty:
        return _save(fig, outdir, "equity_drawdown.png")

    eq = _equity(sim)
    ax = axes[0]
    ax.plot(eq.index, eq.values, color="#2E5AAC", linewidth=1.7, label="策略净净值")
    btc = res.get("benchmark_curve")
    if btc is not None and len(btc):
        b = btc.reindex(eq.index, method="ffill").dropna()
        if len(b) > 1:
            b = b / b.iloc[0]
            ax.plot(b.index, b.values, color="#F2A900", linewidth=1.4, linestyle="--",
                    label="BTC 买入持有（同期，归一）")
    ax.axhline(1, color="gray", linewidth=0.8)
    ax.set_title(f"多空组合净值 vs BTC（持有 {h0} 窗口；已扣手续费/滑点/资金费）",
                 fontweight="bold")
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3)

    ax = axes[1]
    dd = eq / np.maximum.accumulate(eq.values) - 1
    ax.fill_between(dd.index, dd.values, 0, color="#C0392B", alpha=0.45)
    ax.set_title("回撤", fontweight="bold")
    ax.grid(True, alpha=0.3)

    ax = axes[2]
    ax.plot(sim.index, sim["cost"].cumsum().values, color="#C0392B", label="累计手续费+滑点")
    if sim["funding"].abs().sum() > 0:
        ax.plot(sim.index, sim["funding"].cumsum().values, color="#6A4C93", label="累计资金费（正=收到）")
    ax.axhline(0, color="gray", linewidth=0.8)
    ax.set_title("成本累计（占本金比例）", fontweight="bold")
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3)
    _fmt_date(ax, sim.index)
    fig.tight_layout()
    return _save(fig, outdir, "equity_drawdown.png")


# ══════════════════════════════════════════════ 分年度
def plot_yearly(res: dict, outdir: str) -> str:
    cfg = res["cfg"]
    yt = res["tables"].get("分年度", pd.DataFrame())
    fig, ax = plt.subplots(figsize=(13, 5.5))
    if isinstance(yt, pd.DataFrame) and not yt.empty:
        years = sorted(yt["年份"].unique())
        hs = sorted(yt["持有窗口"].unique())
        w = 0.8 / max(1, len(hs))
        x = np.arange(len(years))
        for i, h in enumerate(hs):
            v = (yt[yt["持有窗口"] == h].set_index("年份")["收益"]
                 .reindex(years).to_numpy(dtype=float))
            ax.bar(x + i * w - 0.4 + w / 2, v, w, label=f"持有 {h} 窗口")
        ax.set_xticks(x)
        ax.set_xticklabels([str(y) for y in years])
        ax.axhline(0, color="gray", linewidth=0.8)
        ax.legend(fontsize=9)
        ax.grid(True, alpha=0.3, axis="y")
    ax.set_title("分年度净收益（每个持有窗口独立统计）", fontweight="bold")
    ax.set_ylabel("年度收益")
    fig.tight_layout()
    return _save(fig, outdir, "yearly_returns.png")


def make_all(res: dict, outdir: str) -> list[str]:
    """生成全部图，返回文件清单。

    单张图失败不影响其他图 —— 统计表已经落盘，图形只是辅助阅读，
    没必要因为某个持有窗口没有数据就整轮白跑。
    """
    paths: list[str] = []
    for fn in (plot_dashboard, plot_ic, plot_fm, plot_t_compare,
               plot_neutralization, plot_equity_drawdown, plot_yearly):
        try:
            p = fn(res, outdir)
            if p:
                paths.append(p)
        except Exception as e:                       # noqa: BLE001
            print(f"⚠️  {fn.__name__} 画图失败：{type(e).__name__}: {e}")
    try:
        paths += plot_quantile_curves(res, outdir)
    except Exception as e:                           # noqa: BLE001
        print(f"⚠️  plot_quantile_curves 画图失败：{type(e).__name__}: {e}")
    return paths
