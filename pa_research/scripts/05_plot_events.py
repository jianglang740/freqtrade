"""图形核验 —— 13 §10 要求"算法识别结果可在图表上检查"。

v2 重做（原版被判定为不可读）。逐条修掉的问题：
  1. 原版用 vlines 画影线 + bar 画实体，两者对不齐 → 改为真蜡烛（实体 + 同色影线）
  2. 图例压在 K 线上 → 移到画布右侧外
  3. 止损线会被参照边界线盖住 → 各条线用不同颜色/线型，并在**右端逐条标注价位与 R 倍数**
  4. 止损/目标线没有标注 → 现在标注 "止损 8231.0 (−1R)" 这种形式
  5. 不显示参照边界来自哪个摆动点 → 用空心圆环标出该摆动点
  6. "参照边界"文字与图例打架 → 移到右端标注
  7. 无成交量、无持仓区间 → 增加成交量副图 + 持仓区间底纹
  8. 出场 X 语义不一致 → 统一画在实际出场价，并注明原因与实现 R
  9. 固定 60/20 根窗口 → 改为按该笔的实际持仓长度自适应
 10. y 轴不留边距导致线被裁 → 统计算上止损/目标/失效位后留 4% 边距

数据来源：06_btc_exit_validation.py 写出的逐笔明细
  reports/exit_{version}_{tf}_{pair}.parquet —— 图上画的就是策略真实做过的那一笔。

运行：python pa_research/scripts/05_plot_events.py --version v1 --n 6
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

plt.rcParams["font.sans-serif"] = ["PingFang SC", "Heiti SC", "Arial Unicode MS", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "pa_research"))

from modules.bracket import load_price  # noqa: E402
from modules.swings import find_swings  # noqa: E402

REPORTS = REPO_ROOT / "pa_research/reports"
FIGS = REPORTS / "figs"
UP, DN = "#26a69a", "#ef5350"
LOOKBACK = 55
FORWARD_PAD = 6


def draw_candles(ax, x, o, h, l, c, min_body) -> None:
    """真蜡烛：影线 low→high，实体 open→close，同一根用同一种颜色。"""
    for xi, oi, hi, li, ci in zip(x, o, h, l, c):
        up = ci >= oi
        col = UP if up else DN
        ax.vlines(xi, li, hi, color=col, lw=1.0, zorder=3)
        lo, hi_body = (oi, ci) if up else (ci, oi)
        ax.add_patch(Rectangle((xi - 0.33, lo), 0.66, max(hi_body - lo, min_body),
                               facecolor=col, edgecolor=col, lw=0.3, zorder=3))


def place_labels(ax, labels, xmax, ymin, ymax, fontsize=7.5) -> None:
    """把价位标签画在右端**内侧**（避免被画布裁掉），并做纵向去重叠。

    止损与参照边界常常只差 tol×vol（本次即相差 61 点），两个标签会叠在一起，
    故先按 y 排序再逐个推开，被推开的用细引线连回真实价位。
    """
    if not labels:
        return
    labels = sorted(labels, key=lambda t: t[0])
    span = ymax - ymin
    min_gap = span * 0.05
    placed = [labels[0][0]]
    for y0, _, _ in labels[1:]:
        placed.append(max(y0, placed[-1] + min_gap))
    over = placed[-1] - (ymax - span * 0.02)
    if over > 0:
        placed = [p - over for p in placed]
    for (y0, text, color), y in zip(labels, placed):
        moved = abs(y - y0) > span * 0.004
        ax.annotate(
            text, xy=(xmax, y0), xytext=(xmax - 3, y),
            fontsize=fontsize, color=color, va="center", ha="right", zorder=10,
            bbox=dict(boxstyle="round,pad=0.18", fc="white", ec="none", alpha=0.88),
            arrowprops=(dict(arrowstyle="-", lw=0.5, color=color, alpha=0.55)
                        if moved else None),
        )


def plot_one(fig, axp, axv, sym, tf, row, df, sh_b, sl_b, version) -> str:
    e = int(row["entry_idx"])
    hold = int(row.get("hold_bars", 12))
    exit_i = int(row["exit_idx"]) if "exit_idx" in row and pd.notna(row.get("exit_idx")) else e + hold
    lo = max(0, e - LOOKBACK)
    hi = min(len(df), exit_i + FORWARD_PAD)
    w = df.iloc[lo:hi].reset_index(drop=True)
    x = np.arange(len(w))

    ymin = float(w["low"].min())
    ymax = float(w["high"].max())
    for k in ("stop_price", "target_price", "inval_price"):
        v = row.get(k, np.nan)
        if pd.notna(v):
            ymin, ymax = min(ymin, float(v)), max(ymax, float(v))
    pad = (ymax - ymin) * 0.04
    ymin, ymax = ymin - pad, ymax + pad
    xmax = len(w) - 1

    draw_candles(axp, x, w["open"], w["high"], w["low"], w["close"], (ymax - ymin) * 0.0015)

    # 摆动点：三角画在确认那一根，虚线连回枢轴那一根（17 §2）
    for tbl, col, mk in ((sh_b, "#b71c1c", "v"), (sl_b, "#1b5e20", "^")):
        sub = tbl[(tbl["confirmed_idx"] >= lo) & (tbl["confirmed_idx"] < hi)]
        if not len(sub):
            continue
        axp.scatter(sub["confirmed_idx"] - lo, sub["price"], marker=mk, s=26,
                    color=col, zorder=5, alpha=0.9)
        for a, b, y in zip(sub["pivot_idx"] - lo, sub["confirmed_idx"] - lo, sub["price"]):
            if a >= 0:
                axp.plot([a, b], [y, y], ls=":", lw=0.6, color=col, alpha=0.6, zorder=2)

    d = int(row["direction"])
    et = row["event_type"]
    entry = float(df["open"].iloc[e])
    labels: list[tuple[float, str, str]] = []

    # 参照边界：突破事件用 ref_level；回调事件的 ref_level 是触发根自身的高/低，
    # 真正的结构位是回调腿极值（失效位），故只对突破事件画"参照边界"。
    if et.startswith("breakout"):
        ref = float(row["ref_level"])
        axp.axhline(ref, ls="--", lw=1.1, color="black", zorder=4)
        labels.append((ref, f"参照边界 {ref:,.1f}", "black"))
        # 标出这条边界来自哪个已确认摆动点
        cand = (sh_b if d == 1 else sl_b)
        if len(cand):
            j = (cand["price"] - ref).abs().idxmin()
            ci = int(cand.loc[j, "confirmed_idx"])
            if lo <= ci < hi:
                axp.scatter([ci - lo], [cand.loc[j, "price"]], s=170, facecolors="none",
                            edgecolors="black", lw=1.2, zorder=6)

    # 止损 / 目标 / 失效位
    stop, tgt = row.get("stop_price", np.nan), row.get("target_price", np.nan)
    inval = row.get("inval_price", np.nan)
    if pd.notna(stop):
        axp.axhline(stop, ls="-", lw=1.2, color="#c62828", alpha=0.95, zorder=4)
        labels.append((stop, f"止损 {stop:,.1f}（−1R）", "#c62828"))
    if pd.notna(tgt):
        rr = row.get("rr", np.nan)
        axp.axhline(tgt, ls="-", lw=1.2, color="#2e7d32", alpha=0.95, zorder=4)
        labels.append((tgt, f"目标 {tgt:,.1f}" + (f"（+{rr:.1f}R）" if pd.notna(rr) else ""),
                       "#2e7d32"))
    # 失效位：突破事件的失效位就是参照边界本身，再画一条是重复，故跳过。
    drawn_ys = [y for y, _, _ in labels]
    if (pd.notna(inval) and pd.notna(stop)
            and abs(inval - stop) > (ymax - ymin) * 0.01
            and all(abs(inval - y) > (ymax - ymin) * 0.004 for y in drawn_ys)):
        axp.axhline(inval, ls="-.", lw=1.0, color="#ef6c00", alpha=0.9, zorder=4)
        labels.append((inval, f"失效位 {inval:,.1f}", "#ef6c00"))

    # 持仓区间底纹 + 入场 + 出场
    axp.axvspan(e - lo, exit_i - lo, color="#1565c0", alpha=0.055, zorder=1)
    axp.axvline(e - lo, color="#1565c0", lw=1.0, alpha=0.7, zorder=4)
    axp.scatter([e - lo], [entry], marker="*", s=190, color="#1565c0",
                zorder=7, edgecolors="white", linewidths=0.6)

    if version == "v1":
        exit_px = {"stop": stop, "target": tgt, "invalidation": float(df["close"].iloc[exit_i]),
                   "horizon": float(df["close"].iloc[exit_i])}.get(row["exit_reason"], np.nan)
    else:
        sd = float(row["pa_vol_scale"])
        exit_px = (entry - d * sd if row["exit_reason"] == "stop"
                   else entry + d * 2.0 * sd if row["exit_reason"] == "target"
                   else float(df["close"].iloc[exit_i]))
    if pd.notna(exit_px):
        axp.scatter([exit_i - lo], [exit_px], marker="X", s=95, color="black", zorder=8)
        # 右端一小半画布都留给价位标签（place_labels 的宽度会到 x≈0.62 起），
        # 故出场标注一律钉在**左缘**，用细引线指回真实出场点，避免压住标签列。
        yfrac = (float(exit_px) - ymin) / max(ymax - ymin, 1e-9)
        axp.annotate(
            f"出场 {row['exit_reason']}\n净 {row['net_R']:+.2f}R　持 {hold} 根",
            xy=(exit_i - lo, exit_px), xycoords="data",
            xytext=(0.012, min(max(yfrac, 0.08), 0.90)), textcoords="axes fraction",
            ha="left", va="center", fontsize=7.5, zorder=9,
            bbox=dict(boxstyle="round,pad=0.25", fc="white", ec="0.6", alpha=0.92),
            arrowprops=dict(arrowstyle="->", lw=0.7, color="0.3"),
        )

    place_labels(axp, labels, xmax, ymin, ymax)
    axp.set_xlim(-1, len(w) + 2)
    axp.set_ylim(ymin, ymax)
    axp.grid(alpha=0.14)
    axp.tick_params(labelsize=7)
    axp.set_ylabel("价格", fontsize=8)
    be = "　保本已移动" if row.get("breakeven_moved") else ""
    axp.set_title(
        f"{sym} {tf}　{et}　dir={d:+d}　状态={row['context_state']}　"
        f"MAE={row['mae_R']:.2f}R{be}" if pd.notna(row.get("mae_R")) else
        f"{sym} {tf} {et} dir={d:+d}",
        fontsize=9.5, pad=8,
    )

    # 成交量副图
    up = (w["close"] >= w["open"]).to_numpy()
    axv.bar(x[up], w["volume"][up], width=0.66, color=UP, alpha=0.75)
    axv.bar(x[~up], w["volume"][~up], width=0.66, color=DN, alpha=0.75)
    axv.axvspan(e - lo, exit_i - lo, color="#1565c0", alpha=0.055)
    axv.set_xlim(-1, len(w) + 2)
    axv.tick_params(labelsize=7)
    axv.set_ylabel("成交量", fontsize=8)
    axv.grid(alpha=0.14)
    return f"netR={row['net_R']:+.2f}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="v1", choices=["v0", "v1"])
    ap.add_argument("--pair", default="BTC_USDT_USDT")
    ap.add_argument("--tf", default="4h")
    ap.add_argument("--n", type=int, default=6, help="每个事件类型抽 n 张")
    ap.add_argument("--seed", type=int, default=3)
    args = ap.parse_args()

    FIGS.mkdir(parents=True, exist_ok=True)
    src = REPORTS / f"exit_{args.version}_{args.tf}_{args.pair}.parquet"
    if not src.exists():
        print(f"缺少 {src}，请先运行 06_btc_exit_validation.py")
        return
    ev = pd.read_parquet(src)
    df = load_price(args.pair, args.tf)
    sh_b, sl_b = find_swings(df, left=5, right=5)
    print(f"读入 {src.name}　{len(ev)} 笔")

    rng = np.random.default_rng(args.seed)
    made = 0
    for et, g in ev.groupby("event_type"):
        pick = g.iloc[rng.choice(len(g), size=min(args.n, len(g)), replace=False)]
        for k, (_, row) in enumerate(pick.iterrows()):
            fig, (axp, axv) = plt.subplots(
                2, 1, figsize=(13, 7), sharex=True,
                gridspec_kw={"height_ratios": [3.6, 1.0], "hspace": 0.06},
            )
            try:
                tag = plot_one(fig, axp, axv, args.pair, args.tf, row, df, sh_b, sl_b, args.version)
            except Exception as exc:      # noqa: BLE001 — 图形核验不应掩盖数据问题
                print(f"  跳过 {et}: {exc}", file=sys.stderr)
                plt.close(fig)
                continue
            fig.subplots_adjust(left=0.055, right=0.985, top=0.93, bottom=0.07)
            out = FIGS / f"{args.version}_{args.tf}_{et}_{k:02d}_{tag.replace('=','').replace('+','p').replace('-','m').replace('.','')}.png"
            fig.savefig(out, dpi=115)
            plt.close(fig)
            made += 1
    print(f"生成 {made} 张图 → {FIGS}")


if __name__ == "__main__":
    main()
