#!/usr/bin/env python
"""
扫描 user_data/data/binance 下的全部 K 线, 生成/刷新根目录的 `K线数据报告.md`。

每次下载或补齐数据后跑一次:
    source /opt/anaconda3/etc/profile.d/conda.sh && conda activate freqtrade
    python gen_data_report.py

报告里的所有数字都是现场扫描出来的, 不要手改 —— 改了下次生成会被覆盖。
"""

from __future__ import annotations

import os
import re
import statistics
from datetime import datetime, timezone

import pandas as pd

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "user_data", "data", "binance")
OUT = os.path.join(ROOT, "K线数据报告.md")

# 一个周期一根 K 线应该是多少分钟 —— 用来算缺口
STEP_MIN = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440}
TF_ORDER = ["1m", "5m", "15m", "1h", "4h", "1d"]

FUT_RE = re.compile(r"^(.+?)_USDT_USDT-(\w+)-(\w+)\.feather$")
SPOT_RE = re.compile(r"^(.+?)_USDT-(\w+)\.feather$")


def read_dates(path: str) -> pd.Series:
    return pd.to_datetime(pd.read_feather(path, columns=["date"])["date"]).sort_values()


def gaps(dates: pd.Series, tf: str) -> tuple[int, float]:
    """返回 (缺口处数, 最大间隔分钟)。缺口 = 相邻两根间隔不是该周期应有的步长。"""
    if len(dates) < 2:
        return 0, 0.0
    d = dates.diff().dt.total_seconds().div(60).dropna()
    step = STEP_MIN[tf]
    return int((d != step).sum()), float(d.max())


def scan():
    spot: dict[str, dict] = {}
    fut: dict[str, dict] = {}  # pair -> {kind: {tf: path}}

    for f in sorted(os.listdir(DATA)):
        if not f.endswith(".feather"):
            continue
        m = SPOT_RE.match(f)
        if not m:
            continue
        pair, tf = m.group(1), m.group(2)
        d = read_dates(os.path.join(DATA, f))
        spot.setdefault(pair, {})[tf] = {
            "rows": len(d),
            "start": d.min(),
            "end": d.max(),
            "size": os.path.getsize(os.path.join(DATA, f)),
        }

    fdir = os.path.join(DATA, "futures")
    if os.path.isdir(fdir):
        for f in sorted(os.listdir(fdir)):
            if not f.endswith(".feather"):
                continue
            m = FUT_RE.match(f)
            if not m:
                continue
            pair, tf, kind = m.group(1), m.group(2), m.group(3)
            p = os.path.join(fdir, f)
            d = read_dates(p)
            g, mx = gaps(d, tf) if tf in STEP_MIN else (0, 0)
            fut.setdefault(pair, {}).setdefault(kind, {})[tf] = {
                "rows": len(d),
                "start": d.min(),
                "end": d.max(),
                "size": os.path.getsize(p),
                "gaps": g,
                "maxgap": mx,
            }

    return spot, fut


def fmt(ts) -> str:
    return ts.strftime("%Y-%m-%d %H:%M") if hasattr(ts, "strftime") else str(ts)


def main() -> None:
    spot, fut = scan()
    now = datetime.now(timezone.utc)

    total_bytes = sum(m["size"] for p in spot.values() for m in p.values())
    total_bytes += sum(
        m["size"] for p in fut.values() for k in p.values() for m in k.values()
    )
    n_files = sum(len(p) for p in spot.values()) + sum(
        len(k) for p in fut.values() for k in p.values()
    )

    L: list[str] = []
    A = L.append
    A("# K 线数据报告")
    A("")
    A(
        f"> 本文件由 [`gen_data_report.py`](gen_data_report.py) 自动生成，**请勿手工编辑**"
        f"（会被覆盖）。\n> 生成时间：**{now.strftime('%Y-%m-%d %H:%M')} UTC**　"
        f"｜　数据根目录：`user_data/data/binance`"
    )
    A("")
    A("更新数据后重跑一次即可刷新：")
    A("")
    A("```bash")
    A("source /opt/anaconda3/etc/profile.d/conda.sh && conda activate freqtrade")
    A("python gen_data_report.py")
    A("```")
    A("")
    A("---")
    A("")

    # ── 总览 ──────────────────────────────────────────────
    A("## 一、总览")
    A("")
    A("| 数据集 | 币对数 | 周期覆盖 | 时间跨度 | 体积 |")
    A("|---|---|---|---|---|")
    sp_min = min(m["start"] for p in spot.values() for m in p.values())
    sp_max = max(m["end"] for p in spot.values() for m in p.values())
    A(
        f"| 现货 spot | {len(spot)} | 仅 1d | {fmt(sp_min)[:10]} → {fmt(sp_max)[:10]} "
        f"| {sum(m['size'] for p in spot.values() for m in p.values())/1e6:.1f} MB |"
    )

    majors = {p: k for p, k in fut.items() if "1m" in k.get("futures", {})}
    others = {p: k for p, k in fut.items() if p not in majors}
    maj_bytes = sum(m["size"] for k in majors.values() for t in k.values() for m in t.values())
    oth_bytes = sum(m["size"] for k in others.values() for t in k.values() for m in t.values())

    if majors:
        alltf = sorted({tf for k in majors.values() for tf in k.get("futures", {})}, key=TF_ORDER.index)
        lo = min(m["start"] for k in majors.values() for m in k.get("futures", {}).values())
        hi = max(m["end"] for k in majors.values() for m in k.get("futures", {}).values())
        A(
            f"| 合约 · 全周期组 | {len(majors)} | {'/'.join(alltf)} + mark | "
            f"{fmt(lo)[:10]} → {fmt(hi)[:10]} | {maj_bytes/1e6:.1f} MB |"
        )
    if others:
        alltf = sorted({tf for k in others.values() for tf in k.get("futures", {})}, key=TF_ORDER.index)
        lo = min(m["start"] for k in others.values() for m in k.get("futures", {}).values())
        hi = max(m["end"] for k in others.values() for m in k.get("futures", {}).values())
        A(
            f"| 合约 · 其余组 | {len(others)} | {'/'.join(alltf)} + mark | "
            f"{fmt(lo)[:10]} → {fmt(hi)[:10]} | {oth_bytes/1e6:.1f} MB |"
        )
    A(f"| **合计** | **{len(spot)} 现货 + {len(fut)} 合约** | | | **{total_bytes/1e6:.1f} MB**（{n_files} 个文件） |")
    A("")
    A("全周期组 = " + "、".join(sorted(majors)) if majors else "")
    A("")
    A("> 所有时间戳均为 **UTC**。")
    A("")
    A("---")
    A("")

    # ── 全周期组明细 ───────────────────────────────────────
    if majors:
        A("## 二、合约 · 全周期组（含 mark / funding）")
        A("")
        A("这组是用来做日内研究与择时的主力数据。")
        A("")
        for pair in sorted(majors):
            A(f"### {pair}")
            A("")
            A("| 周期 | 行情 K 线 | 起 | 止 | 缺口 | mark | funding_rate |")
            A("|---|---|---|---|---|---|---|")
            futd = fut[pair].get("futures", {})
            mk = fut[pair].get("mark", {})
            fr = fut[pair].get("funding_rate", {})
            for tf in sorted(futd, key=lambda t: TF_ORDER.index(t) if t in TF_ORDER else 99):
                m = futd[tf]
                mk_n = f"{mk[tf]['rows']:,}" if tf in mk else "—"
                fr_n = f"{fr[tf]['rows']:,}" if tf in fr else "—"
                A(
                    f"| {tf} | {m['rows']:,} | {fmt(m['start'])[:16]} | {fmt(m['end'])[:16]} "
                    f"| {m['gaps']} | {mk_n} | {fr_n} |"
                )
            A("")

    # ── 其余组 ────────────────────────────────────────────
    if others:
        A("## 三、合约 · 其余组")
        A("")
        A(f"共 **{len(others)}** 个币对，只有 `1d` + `4h`（含 mark）。")
        A("")
        rows = []
        for pair, k in others.items():
            m = k.get("futures", {}).get("1d")
            if not m:
                continue
            rows.append((pair, m["start"], m["end"], (m["end"] - m["start"]).days, m["rows"]))
        rows.sort(key=lambda r: r[3])
        A("| # | 币对 | 起 | 止 | 跨度(天) | 日线根数 |")
        A("|---|---|---|---|---|---|")
        for i, (pair, s, e, d, n) in enumerate(rows, 1):
            A(f"| {i} | {pair} | {fmt(s)[:10]} | {fmt(e)[:10]} | {d} | {n:,} |")
        A("")

        spans = [r[3] for r in rows]
        if spans:
            A("**跨度分布**")
            A("")
            A("| 区间 | 币对数 |")
            A("|---|---|")
            for label, lo, hi in [
                ("≥ 3 年", 1095, 10**9),
                ("2–3 年", 730, 1095),
                ("1–2 年", 365, 730),
                ("< 1 年", 0, 365),
            ]:
                c = sum(1 for s in spans if lo <= s < hi)
                A(f"| {label} | {c} |")
            A("")
            A(f"中位跨度 **{statistics.median(spans):.0f}** 天，最长 {max(spans)} 天，最短 {min(spans)} 天。")
            A("")

    # ── 现货 ──────────────────────────────────────────────
    A("## 四、现货 spot")
    A("")
    A(f"共 **{len(spot)}** 个币对，只有 `1d`。主要用于多因子截面研究。")
    A("")
    sp_spans = sorted(
        ((p, m["start"], m["end"], (m["end"] - m["start"]).days)
         for p, k in spot.items() for tf, m in k.items()),
        key=lambda r: r[3],
    )
    A("| # | 币对 | 起 | 止 | 跨度(天) |")
    A("|---|---|---|---|---|")
    for i, (pair, s, e, d) in enumerate(sp_spans, 1):
        A(f"| {i} | {pair} | {fmt(s)[:10]} | {fmt(e)[:10]} | {d} |")
    A("")

    # ── 缺口与限制 ────────────────────────────────────────
    A("---")
    A("")
    A("## 五、已知缺口与限制")
    A("")
    A("### funding_rate 覆盖不全")
    A("")
    fut_pairs = sorted(fut)
    with_fr = [p for p in fut_pairs if "funding_rate" in fut[p]]
    without_fr = [p for p in fut_pairs if "funding_rate" not in fut[p]]
    A(f"合约 {len(fut_pairs)} 个币对中，**只有 {len(with_fr)} 个** 下载了资金费，**{len(without_fr)} 个缺失**。")
    A("")
    A(f"- 已有：{'、'.join(with_fr)}")
    A("")
    A(
        "- 影响：回测若用 `futures_funding_rate: 0` 兜底，引擎不读资金费文件也能跑，"
        "但**资金费成本被按 0 处理**（永续多空都要付/收，是真实成本）。"
    )
    A("- 补齐：")
    A("")
    A("```bash")
    A("freqtrade download-data -c <config> --trading-mode futures \\")
    A("  -p <pairs> -t 1h --candle-types funding_rate --timerange <start>-<end>")
    A("```")
    A("")
    A("### 其余组没有日内周期")
    A("")
    A(f"除全周期组外，{len(others)} 个币对只有 `1d` + `4h`，**没有 1h / 15m / 5m / 1m**。")
    A("要做日内择时必须先补（每对每周期走一次 REST，量不小）。")
    A("")
    A("### 交易标的的时间上限")
    A("")
    A("| 标的 | 交易所侧最早 | 说明 |")
    A("|---|---|---|")
    A("| BTC 合约 | 2019-09-08 | Binance 合约上线日 |")
    A("| ETH 合约 | 2019-11-27 | 晚于 BTC |")
    A("| XAU 合约 | 2025-12-11 | `onboardDate`，`contractType=TRADIFI_PERPETUAL`，**不可再往前拉** |")
    A("")
    A("### 其他注意事项")
    A("")
    A("- **XAU 是合成 24/7 TradFi 永续，不是 COMEX 真黄金。**")
    A("- 其余组币池是「今天还活着」的币 → **幸存者偏差**，退市币不在池内。")
    A("- `mark` 价格普遍晚于行情 K 线（如 BTC/ETH 从 2019-12-23 起），交易所侧限制。")
    A("- 新上市标的**首日那一段**普通下载拿不到（报 `Requested start date ... earlier than local data start date`），"
      "要补必须加 **`--prepend`**。")
    A("- `download-data` 默认**只向后追加**，不会自动往前补；补更早历史要 `--prepend`，整体重下要 `--erase`。")
    A("")

    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")

    print(f"已生成 {OUT}")
    print(f"  现货 {len(spot)} 对 / 合约 {len(fut)} 对 / {n_files} 个文件 / {total_bytes/1e6:.1f} MB")
    print(f"  funding_rate: {len(with_fr)}/{len(fut_pairs)} 有")


if __name__ == "__main__":
    main()
