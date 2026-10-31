#!/usr/bin/env python
"""
扫描 user_data/data/binance 下的全部 K 线, 生成/刷新根目录的 `K线数据报告.md`。

**数据一变就必须重跑本脚本** —— 每次 `download-data` 拉新数据、日常增量补最新、
用 `--prepend` 往前补历史、或用 `--erase` 重下之后, 都要重新生成一次,
否则报告里的覆盖范围、缺口和文件数会悄悄过期, 后续研究会引用错的数据。

    source /opt/anaconda3/etc/profile.d/conda.sh && conda activate freqtrade
    python gen_data_report.py

报告里的所有数字都是现场扫描出来的, 不要手改 —— 改了下次生成会被覆盖。
"""

from __future__ import annotations

import os
import re
import statistics
from collections import Counter
from datetime import datetime, timezone

import pandas as pd

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "user_data", "data", "binance")
OUT = os.path.join(ROOT, "K线数据报告.md")

# 一个周期一根 K 线应该是多少分钟 —— 用来算缺口
STEP_MIN = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440}
TF_ORDER = ["1m", "5m", "15m", "1h", "4h", "1d"]
OHLCV_COLS = ("date", "open", "high", "low", "close", "volume")

FUT_RE = re.compile(r"^(.+?)_USDT_USDT-(\w+)-(\w+)\.feather$")
SPOT_RE = re.compile(r"^(.+?)_USDT-(\w+)\.feather$")

KIND_CN = {"futures": "行情", "mark": "mark", "funding_rate": "funding_rate"}


def gap_detail(dates: pd.Series, tf: str) -> tuple[int, float, list[tuple]]:
    """返回 (缺口处数, 最大间隔分钟, [(前一根, 后一根, 间隔分钟), ...])。

    缺口 = 相邻两根的间隔不是该周期应有的步长。只对行情 / mark 使用；
    funding 的结算间隔由交易所决定（8h / 4h），不能按文件名里的 1h 去数。
    """
    if len(dates) < 2:
        return 0, 0.0, []
    step = STEP_MIN[tf]
    delta = dates.diff().dt.total_seconds().div(60)
    bad = []
    for i in range(1, len(dates)):
        if delta.iloc[i] != step:
            bad.append((dates.iloc[i - 1], dates.iloc[i], float(delta.iloc[i])))
    return len(bad), (max(x[2] for x in bad) if bad else 0.0), bad


def leading_flat(df: pd.DataFrame) -> dict | None:
    """开头连续 high == low 的 K 线（价格完全没有波动）。

    Binance 上线前的占位 K 线（BTC 的 OHLC 全等于 10000.0）和上市首日的
    无成交 / 单笔成交都会长这样。它们不是缺口，是真实存在的零振幅 K 线。
    """
    flat = df["high"].eq(df["low"]).to_numpy()
    n = 0
    for v in flat:
        if not v:
            break
        n += 1
    if not n:
        return None
    seg = df.iloc[:n]
    return {
        "n": n,
        "start": seg["date"].iloc[0],
        "end": seg["date"].iloc[-1],
        "price0": float(seg["close"].iloc[0]),
        "price1": float(seg["close"].iloc[-1]),
        "const": bool(seg["close"].nunique() == 1),
        "maxvol": float(seg["volume"].max()),
        "next": None if n >= len(df) else {
            "date": df["date"].iloc[n],
            "open": float(df["open"].iloc[n]),
            "close": float(df["close"].iloc[n]),
        },
    }


def sample_row(path: str) -> pd.Series | None:
    """取最后一根真实 K 线做示例（文件不存在时返回 None）。"""
    if not os.path.exists(path):
        return None
    return pd.read_feather(path).iloc[-1]


def scan():
    spot: dict[str, dict] = {}
    fut: dict[str, dict] = {}  # pair -> {kind: {tf: meta}}
    checks = {
        "schemas": Counter(),          # (kind, columns) -> 文件数
        "mark_vol_nonzero": [],        # mark 文件里 volume 不是 0 的
        "fund_nonopen_nonzero": [],    # funding 文件里 high/low/close/volume 不是 0 的
        "dupes": 0,
        "flat_runs": [],               # (pair, tf, leading_flat)
        "fund_cadence": Counter(),     # 结算间隔(小时) -> 币对数
        "fund_irregular": [],          # (pair, 行数, 标准间隔, 非标准间隔数, 最大间隔)
    }

    for f in sorted(os.listdir(DATA)):
        if not f.endswith(".feather"):
            continue
        m = SPOT_RE.match(f)
        if not m:
            continue
        pair, tf = m.group(1), m.group(2)
        p = os.path.join(DATA, f)
        df = pd.read_feather(p).sort_values("date")
        checks["schemas"][("spot", tuple(df.columns))] += 1
        checks["dupes"] += int(df["date"].duplicated().sum())
        spot.setdefault(pair, {})[tf] = {
            "rows": len(df),
            "start": df["date"].min(),
            "end": df["date"].max(),
            "size": os.path.getsize(p),
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
            df = pd.read_feather(p).sort_values("date")
            checks["schemas"][(kind, tuple(df.columns))] += 1
            checks["dupes"] += int(df["date"].duplicated().sum())

            meta: dict = {
                "rows": len(df),
                "start": df["date"].min(),
                "end": df["date"].max(),
                "size": os.path.getsize(p),
            }

            if kind == "funding_rate":
                # 资金费行距 = 交易所实际结算周期，不是文件名里的 1h
                delta = df["date"].diff().dt.total_seconds().div(3600).dropna()
                mode = float(delta.mode().iloc[0]) if len(delta) else 0.0
                irregular = int((delta != mode).sum())
                meta.update(cadence=mode, irregular=irregular,
                            max_interval=float(delta.max()) if len(delta) else 0.0)
                checks["fund_cadence"][mode] += 1
                if ((df[["high", "low", "close", "volume"]] != 0).any()).any():
                    checks["fund_nonopen_nonzero"].append(pair)
            else:
                g, mx, gl = gap_detail(df["date"], tf)
                meta.update(gaps=g, maxgap=mx, gaplist=gl)
                if kind == "futures":
                    lf = leading_flat(df)
                    meta["flat"] = lf
                    if lf and lf["n"] >= 2:
                        checks["flat_runs"].append((pair, tf, lf))
                if kind == "mark" and (df["volume"] != 0).any():
                    checks["mark_vol_nonzero"].append(pair)

            fut.setdefault(pair, {}).setdefault(kind, {})[tf] = meta

    checks["fund_irregular"] = sorted(
        (
            (fut[p]["funding_rate"][tf]["cadence"],
             fut[p]["funding_rate"][tf]["irregular"],
             fut[p]["funding_rate"][tf]["rows"],
             fut[p]["funding_rate"][tf]["max_interval"],
             p)
            for p in fut
            for tf in fut[p].get("funding_rate", {})
        ),
        reverse=True,
    )
    return spot, fut, checks


def fmt(ts) -> str:
    return ts.strftime("%Y-%m-%d %H:%M") if hasattr(ts, "strftime") else str(ts)


def fmt_num(x: float) -> str:
    return f"{x:.10g}"


def fmt_rate(x: float) -> str:
    """资金费率用小数显示，避免 5.232e-05 这种科学计数法。"""
    s = f"{x:.10f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


def tf_list(k: dict, kind: str) -> list[str]:
    return [tf for tf in TF_ORDER if tf in k.get(kind, {})]


def combo_desc(pairs: dict, kind: str) -> str:
    c: Counter = Counter()
    for p in pairs:
        tfs = tf_list(pairs[p], kind)
        if tfs:
            c["+".join(tfs)] += 1
    ordered = sorted(c.items(), key=lambda kv: (-kv[1], len(kv[0]), kv[0]))
    return "、".join(f"`{k}`×{v}" for k, v in ordered) if ordered else "—"


def cadence_cn(hours: float) -> str:
    return f"{hours:g} 小时"


def main() -> None:
    spot, fut, checks = scan()
    now = datetime.now(timezone.utc)
    fdir = os.path.join(DATA, "futures")

    total_bytes = sum(m["size"] for p in spot.values() for m in p.values())
    total_bytes += sum(
        m["size"] for p in fut.values() for k in p.values() for m in k.values()
    )
    n_files = sum(len(p) for p in spot.values()) + sum(
        len(k) for p in fut.values() for k in p.values()
    )

    majors = {p: k for p, k in fut.items() if "1m" in k.get("futures", {})}
    others = {p: k for p, k in fut.items() if p not in majors}
    total_fr = sum(1 for p in fut if "funding_rate" in fut[p])
    maj_bytes = sum(m["size"] for k in majors.values() for t in k.values() for m in t.values())
    oth_bytes = sum(m["size"] for k in others.values() for t in k.values() for m in t.values())

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

    # ── 维护规则 ──────────────────────────────────────────
    A("## 维护规则（硬性）")
    A("")
    A(
        "**每次拉取新数据、或对已有数据做任何增量更新，都必须重新生成 `K线数据报告.md`"
        "（重跑下面的生成器）。**包括但不限于："
    )
    A("")
    A("- `download-data` 新增币对 / 新增周期")
    A("- 日常增量补最新 K 线")
    A("- `--prepend` 往前补历史、`--erase` 整体重下")
    A("- 手工删改 `user_data/data` 下的任何 `.feather` 文件")
    A("")
    A(
        "不重跑的后果：本报告里的覆盖范围、缺口、文件数、资金费覆盖率都会悄悄过期，"
        "而回测和研究会把这些数字当成事实引用。**报告与磁盘数据必须始终一致。**"
    )
    A("")
    A("```bash")
    A("source /opt/anaconda3/etc/profile.d/conda.sh && conda activate freqtrade")
    A("python gen_data_report.py")
    A("```")
    A("")
    A("报告里所有数字都是现场扫描出来的，不要手改 —— 改了下次生成会被覆盖。")
    A("")
    A("---")
    A("")

    # ── 一、文件与字段 ────────────────────────────────────
    A("## 一、每份文件是什么：命名、字段、为什么有 mark 和 funding")
    A("")
    A("### 目录与命名")
    A("")
    A("```")
    A("user_data/data/binance/")
    A("├── BTC_USDT-1d.feather                        # 现货：<币对>-<周期>.feather")
    A("└── futures/")
    A("    ├── BTC_USDT_USDT-1m-futures.feather       # 合约行情：<币对>_USDT_USDT-<周期>-futures.feather")
    A("    ├── BTC_USDT_USDT-1m-mark.feather          # 标记价格：<币对>_USDT_USDT-<周期>-mark.feather")
    A("    └── BTC_USDT_USDT-1h-funding_rate.feather  # 资金费率：<币对>_USDT_USDT-1h-funding_rate.feather")
    A("```")
    A("")
    A(
        "- 现货文件名是 `BTC_USDT`（交易对）；合约文件名是 `BTC_USDT_USDT`（交易对 + 结算币），"
        "两者不是同一份数据。"
    )
    A("- 合约目录下每个币对最多三类文件：**行情**（`futures`）、**标记价格**（`mark`）、**资金费率**（`funding_rate`）。")
    A("")
    A("### 字段（三类文件都是 6 列，但只有部分列有值）")
    A("")
    A("| 文件类型 | date | open | high | low | close | volume |")
    A("|---|---|---|---|---|---|---|")
    A("| 行情（现货 / 合约） | K 线起始时间（UTC） | 开盘价 | 最高价 | 最低价 | 收盘价 | 成交量（标的币数量） |")
    A("| mark | 同上 | 标记价开盘 | 标记价最高 | 标记价最低 | 标记价收盘 | **恒为 0**（标记价没有成交量） |")
    A("| funding_rate | **结算时点** | **该次结算的资金费率**（小数，0.0001 = 0.01%） | 0 | 0 | 0 | 0 |")
    A("")
    A("实际数据长这样（各自文件的最后一行）：")
    A("")
    A("| 文件 | date | open | high | low | close | volume |")
    A("|---|---|---|---|---|---|---|")

    def sample_row_md(label: str, path: str, rate: bool = False) -> None:
        row = sample_row(path)
        if row is None:
            A(f"| {label} | — | — | — | — | — | — |")
            return
        val = fmt_rate if rate else fmt_num
        A(
            f"| {label} | {fmt(row['date'])} | {val(float(row['open']))} "
            f"| {val(float(row['high']))} | {val(float(row['low']))} "
            f"| {val(float(row['close']))} | {val(float(row['volume']))} |"
        )

    sample_row_md(
        "行情（BTC 1d）",
        os.path.join(fdir, "BTC_USDT_USDT-1d-futures.feather"),
    )
    sample_row_md(
        "mark（BTC 1h）",
        os.path.join(fdir, "BTC_USDT_USDT-1h-mark.feather"),
    )
    sample_row_md(
        "funding（BTC 1h）",
        os.path.join(fdir, "BTC_USDT_USDT-1h-funding_rate.feather"),
        rate=True,
    )
    A("")
    A(
        "> 注意 funding 文件除 `open` 外的列全是 0 —— 它本质是一张「时点 → 费率」表，"
        "只是沿用了 K 线的 6 列结构。"
    )
    A("")

    A("### 为什么要多拉 mark 和 funding，而不是只有量价")
    A("")
    A("永续合约持仓要按期结算**资金费**，它不是手续费，但同样是真实成本：")
    A("")
    A("```")
    A("资金费 = 资金费率 × 标记价格 × 持仓数量")
    A("```")
    A("")
    A(
        "- **mark（标记价格）**：资金费率是百分比，必须乘一个价格才能换成钱。"
        "freqtrade 用标记价的 `open` 作为计价基准（`exchange.py` 的 `_add_funding_columns`："
        "`fee_per_unit = open_fund × open_mark`）。"
    )
    A(
        "- **funding_rate（资金费率）**：每个结算时点的费率。"
        "回测把开仓到平仓之间所有结算点的 `费率 × 标记价 × 持仓量` 求和；"
        "多头付出、空头收取（`calculate_funding_fees`）。"
    )
    A(
        "- **现货不需要这两份**：现货没有资金费，所以现货目录只有一份量价数据。"
    )
    A(
        "- **本地文件只给回测用**：实盘 / dry-run 的资金费走交易所接口实时取；"
        "回测才读本地 `mark` + `funding_rate`。"
    )
    A(
        "- **回测清算价不读 mark**：清算价由开仓价、杠杆和维持保证金推出"
        "（`leverage/liquidation_price.py`），mark 在这里只负责资金费计价。"
    )
    A("")
    A("资金费数据缺失时**不一定报错**，这点容易踩坑：")
    A("")
    A(
        "- 回测按 `mark_ohlcv_timeframe`（Binance 默认 `1h`）加载 mark、按 "
        "`funding_fee_timeframe`（默认 `1h`）加载 funding，然后按时间戳内连接。"
    )
    A(
        "- 某个币对没有 mark 1h 文件 → 读出来是空表 → 内连接后为空 → "
        "**该币对的资金费按 0 计，且不报错**（`load_data` 的 `fail_without_data=True` "
        "只在**所有**币对都读不到时才抛异常）。"
    )
    A(
        "- 配置项 `futures_funding_rate` 只是「用常数费率填补缺失的 funding」；"
        "mark 行本身不存在时，它也无济于事，结果仍是 0。"
    )
    A("")
    A("所以「回测跑通了」不等于「资金费被算进去了」—— 先看同目录下有没有对应周期的 mark。")
    A("")

    A("### 为什么资金费文件叫 `1h`，行与行却隔 8 小时")
    A("")
    A(
        "`download-data` 拉资金费时永远使用 `funding_fee_timeframe`（Binance 默认 `1h`），"
        "文件名跟着这个标签走，**与你传的 `-t` 无关**。但每一行的时间戳是交易所的实际结算时点，"
        "所以行距是 8 小时（或 4 小时），**不是 1 小时**。"
    )
    A("")
    A(
        "换句话说：`-1h-funding_rate.feather` 里的 `1h` 只是 freqtrade 的存放周期标签，"
        "不代表数据密度。不要拿它去和 1h 的行情 K 线逐根对齐 —— 框架是按时间戳合并的。"
    )
    A("")
    A("本机 funding 文件的结算间隔：")
    A("")
    A("| 结算间隔 | 币对数 | 币对 |")
    A("|---|---|---|")
    by_cad: dict[float, list[str]] = {}
    for p in fut:
        for tf, meta in fut[p].get("funding_rate", {}).items():
            by_cad.setdefault(meta["cadence"], []).append(p)
    for cad in sorted(by_cad):
        names = sorted(by_cad[cad])
        shown = "、".join(names) if len(names) <= 8 else "、".join(names[:8]) + f"（共 {len(names)} 个）"
        A(f"| {cadence_cn(cad)} | {len(names)} | {shown} |")
    A("")
    A(
        "> mark 文件名里的周期则是**实际 K 线周期**。不传 `--candle-types` 时，"
        "freqtrade 只额外补一份 `mark_ohlcv_timeframe`（默认 `1h`）的 mark；"
        "显式传 `--candle-types futures mark` 时，才对 `-t` 的每个周期各拉一份 mark。"
        "只传 `--candle-types futures` 会跳过默认的 1h mark。"
    )
    A("")
    A("---")
    A("")

    # ── 二、总览 ──────────────────────────────────────────
    A("## 二、总览")
    A("")
    A("| 数据集 | 币对数 | 周期覆盖 | 时间跨度 | 体积 |")
    A("|---|---|---|---|---|")
    sp_min = min(m["start"] for p in spot.values() for m in p.values())
    sp_max = max(m["end"] for p in spot.values() for m in p.values())
    A(
        f"| 现货 spot | {len(spot)} | 仅 `1d` | {fmt(sp_min)[:10]} → {fmt(sp_max)[:10]} "
        f"| {sum(m['size'] for p in spot.values() for m in p.values())/1e6:.1f} MB |"
    )

    def span(pairs: dict) -> tuple:
        lo = min(m["start"] for k in pairs.values() for m in k.get("futures", {}).values())
        hi = max(m["end"] for k in pairs.values() for m in k.get("futures", {}).values())
        return lo, hi

    if majors:
        alltf = sorted(
            {tf for k in majors.values() for tf in k.get("futures", {})},
            key=TF_ORDER.index,
        )
        lo, hi = span(majors)
        A(
            f"| 合约 · 全周期组 | {len(majors)} | {'/'.join(alltf)} + mark + funding | "
            f"{fmt(lo)[:10]} → {fmt(hi)[:10]} | {maj_bytes/1e6:.1f} MB |"
        )
    if others:
        lo, hi = span(others)
        mark_n = sum(1 for p in others if "mark" in others[p])
        fr_n = sum(1 for p in others if "funding_rate" in others[p])
        A(
            f"| 合约 · 其余组 | {len(others)} | 行情 {combo_desc(others, 'futures')}；"
            f"mark {mark_n} 对；funding {fr_n} 对（全库 {total_fr} 对） | "
            f"{fmt(lo)[:10]} → {fmt(hi)[:10]} | {oth_bytes/1e6:.1f} MB |"
        )
    A(
        f"| **合计** | **{len(spot)} 现货 + {len(fut)} 合约** | | | "
        f"**{total_bytes/1e6:.1f} MB**（{n_files} 个文件） |"
    )
    A("")
    if majors:
        A("全周期组 = " + "、".join(sorted(majors)) + "。")
        A("")
    A("> 所有时间戳均为 **UTC**。")
    A("")
    A("---")
    A("")

    # ── 三、全周期组 ──────────────────────────────────────
    if majors:
        A("## 三、合约 · 全周期组（含 mark / funding）")
        A("")
        A("这组是用来做日内研究与择时的主力数据。")
        A("")
        A(
            "> funding 只显示在 `1h` 那一行：文件固定命名为 `-1h-funding_rate.feather`，"
            "但行距是交易所的实际结算间隔（见第一节），不是 1 小时。"
        )
        A("")
        for pair in sorted(majors):
            A(f"### {pair}")
            A("")
            A("| 周期 | 行情根数 | 起 | 止 | 行情缺口 | mark 根数 | funding（结算间隔） |")
            A("|---|---|---|---|---|---|---|")
            futd = fut[pair].get("futures", {})
            mk = fut[pair].get("mark", {})
            fr = fut[pair].get("funding_rate", {})
            for tf in sorted(futd, key=lambda t: TF_ORDER.index(t) if t in TF_ORDER else 99):
                m = futd[tf]
                mk_n = f"{mk[tf]['rows']:,}" if tf in mk else "—"
                if tf in fr:
                    fr_cell = f"{fr[tf]['rows']:,}（{cadence_cn(fr[tf]['cadence'])}）"
                else:
                    fr_cell = "—"
                A(
                    f"| {tf} | {m['rows']:,} | {fmt(m['start'])[:16]} | {fmt(m['end'])[:16]} "
                    f"| {m['gaps']} | {mk_n} | {fr_cell} |"
                )
            A("")
            if "1h" in fr:
                f1 = fr["1h"]
                extra = ""
                if f1["irregular"]:
                    extra = f"，其中 {f1['irregular']} 个非标准间隔（最大 {f1['max_interval']:g} 小时）"
                A(
                    f"funding 时间范围：{fmt(f1['start'])[:16]} → {fmt(f1['end'])[:16]}，"
                    f"共 {f1['rows']:,} 次结算，标准间隔 {cadence_cn(f1['cadence'])}{extra}。"
                )
                A("")
        A("---")
        A("")

    # ── 四、其余组 ────────────────────────────────────────
    if others:
        A("## 四、合约 · 其余组")
        A("")
        A(f"共 **{len(others)}** 个币对，周期覆盖**并不统一**（以下数字由文件现场统计）：")
        A("")
        A(f"- 行情 K 线：{combo_desc(others, 'futures')}")
        A(f"- mark：{combo_desc(others, 'mark')}")
        fr_others = [p for p in others if "funding_rate" in others[p]]
        A(f"- funding：{len(fr_others)} / {len(others)} 个币对已下载。")
        fr_no_1h = [p for p in fr_others if "1h" not in others[p].get("futures", {})]
        if fr_no_1h:
            A(
                f"  - 其中 **{'、'.join(sorted(fr_no_1h))}** 有 funding、但**没有 1h 行情**"
                "（资金费与行情不是同一次下载拉齐的）。"
            )
        A("")
        A("| # | 币对 | 行情周期 | mark 周期 | funding | 起 | 止 | 跨度(天) | 日线根数 |")
        A("|---|---|---|---|---|---|---|---|---|")
        rows = []
        for pair, k in others.items():
            m = k.get("futures", {}).get("1d")
            if not m:
                continue
            rows.append((pair, m["start"], m["end"], (m["end"] - m["start"]).days, m["rows"]))
        rows.sort(key=lambda r: r[3])
        for i, (pair, s, e, d, n) in enumerate(rows, 1):
            k = others[pair]
            tfs = "、".join(tf_list(k, "futures")) or "—"
            mks = "、".join(tf_list(k, "mark")) or "—"
            if "1h" in k.get("funding_rate", {}):
                fmeta = k["funding_rate"]["1h"]
                frcell = f"{fmeta['rows']:,}（{cadence_cn(fmeta['cadence'])}）"
            else:
                frcell = "—"
            A(
                f"| {i} | {pair} | {tfs} | {mks} | {frcell} "
                f"| {fmt(s)[:10]} | {fmt(e)[:10]} | {d} | {n:,} |"
            )
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
            A(
                f"中位跨度 **{statistics.median(spans):.0f}** 天，"
                f"最长 {max(spans)} 天，最短 {min(spans)} 天。"
            )
            A("")
        A("---")
        A("")

    # ── 五、现货 ──────────────────────────────────────────
    A("## 五、现货 spot")
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

    # ── 六、数据质量 ──────────────────────────────────────
    A("---")
    A("")
    A("## 六、数据质量：缺口与占位段")
    A("")
    A("### 自检")
    A("")
    schema_ok = all(
        cols == OHLCV_COLS
        for (kind, cols), _ in checks["schemas"].items()
    )
    A(
        f"- 列结构：{'全部文件均为 `date/open/high/low/close/volume` 6 列' if schema_ok else '**发现异常列结构，见下方警告**'}"
    )
    A(f"- 重复时间戳：{checks['dupes']} 行")
    A(f"- mark 文件里 volume 非 0 的币对：{len(checks['mark_vol_nonzero'])} 个"
      + (f"（{'、'.join(sorted(set(checks['mark_vol_nonzero'])))}）" if checks["mark_vol_nonzero"] else ""))
    A(f"- funding 文件里 high/low/close/volume 非 0 的币对：{len(checks['fund_nonopen_nonzero'])} 个"
      + (f"（{'、'.join(sorted(set(checks['fund_nonopen_nonzero'])))}）" if checks["fund_nonopen_nonzero"] else ""))
    A("")
    if not schema_ok:
        A("| 文件类型 | 列结构 | 文件数 |")
        A("|---|---|---|")
        for (kind, cols), n in checks["schemas"].items():
            A(f"| {kind} | {cols} | {n} |")
        A("")

    A("### 缺口（行情 / mark）")
    A("")
    A(
        "口径：相邻两根的间隔不是该周期应有的步长。**funding 不参与** —— 它的结算间隔本来就是 8h / 4h，"
        "按文件名里的 `1h` 去数会得到几千个假缺口（这反过来印证了「1h 只是标签」）。"
    )
    A("")
    all_gaps = []
    for pair in sorted(fut):
        for kind in ("futures", "mark"):
            for tf, meta in fut[pair].get(kind, {}).items():
                for prev, nxt, delta in meta.get("gaplist", []):
                    all_gaps.append((pair, kind, tf, prev, nxt, delta))
    all_gaps.sort(key=lambda x: (x[0], x[1], TF_ORDER.index(x[2]) if x[2] in TF_ORDER else 99, x[3]))
    if all_gaps:
        A("| 币对 | 文件类型 | 周期 | 前一根 | 后一根 | 间隔(分钟) |")
        A("|---|---|---|---|---|---|")
        for pair, kind, tf, prev, nxt, delta in all_gaps:
            A(f"| {pair} | {KIND_CN.get(kind, kind)} | {tf} | {fmt(prev)} | {fmt(nxt)} | {delta:g} |")
        A("")
        n_fut_gaps = sum(1 for g in all_gaps if g[1] == "futures")
        n_mark_gaps = sum(1 for g in all_gaps if g[1] == "mark")
        A(
            f"行情 K 线缺口共 **{n_fut_gaps}** 处；mark 缺口共 **{n_mark_gaps}** 处"
            + ("（都是 2–30 分钟的短时缺失，同一时段的行情 K 线没有缺口）" if n_mark_gaps else "")
            + "。"
        )
        A("")
    else:
        A("没有缺口。")
        A("")

    btc_gap = None
    for pair, kind, tf, prev, nxt, delta in all_gaps:
        if pair == "BTC" and kind == "futures" and tf == "1m":
            btc_gap = (prev, nxt, delta)
            break
    if btc_gap:
        prev, nxt, delta = btc_gap
        btc_meta = fut["BTC"]["futures"]["1m"]
        flat = btc_meta.get("flat")
        missing = prev + pd.Timedelta(minutes=1)
        lead = (
            "行情缺口只有这一个"
            if n_fut_gaps == 1
            else f"这是行情缺口中的一处（行情共 {n_fut_gaps} 处）"
        )
        A("### BTC 1m 的那个缺口")
        A("")
        A(
            f"{lead}，位于 **{fmt(missing)} UTC**：{fmt(prev)} 之后直接跳到 {fmt(nxt)}，"
            f"缺了中间这一分钟（相邻间隔 {delta:g} 分钟）。"
        )
        A("")
        if flat and flat["const"]:
            nxt_row = flat["next"]
            A(
                f"它落在 Binance 合约上线前的占位段里：BTC 1m 从 {fmt(flat['start'])} 起，"
                f"连续 {flat['n']} 根 K 线的 OHLC 全部等于 {fmt_num(flat['price0'])}、"
                f"成交量约等于 0（最大 {fmt_num(flat['maxvol'])}）。"
                f"第一根真实 K 线是 {fmt(nxt_row['date'])}，"
                f"开盘 {fmt_num(nxt_row['open'])}、收盘 {fmt_num(nxt_row['close'])}。"
            )
            A("")
            A(
                "所以这不是「行情中间丢了数据」，而是上线前的占位段里少了一根占位 K 线。"
                "对研究没有影响；freqtrade 读取时若 `fill_up_missing=True`，"
                "会用前一根收盘价补一根 `volume=0` 的 K 线（`data/converter/converter.py`）。"
            )
        else:
            A("该缺口附近没有检测到占位段，需要单独确认。")
        A("")

    A("### 开局无波动段（不是缺口）")
    A("")
    A(
        "下表是每个文件**开头**连续「最高价 = 最低价」的 K 线。"
        "Binance 上线前的占位 K 线（价格恒定、量≈0）和上市首日的无成交 / 单笔成交都会这样。"
        "它们是真实存在的零振幅 K 线，不是缺失行；指标会照常读到它们。"
    )
    A("")
    flat_runs = sorted(
        checks["flat_runs"],
        key=lambda x: (x[0], TF_ORDER.index(x[1]) if x[1] in TF_ORDER else 99),
    )
    if flat_runs:
        A("| 币对 | 周期 | 连续根数 | 起 | 止 | 价格 | 期间最大成交量 |")
        A("|---|---|---|---|---|---|---|")
        for pair, tf, lf in flat_runs:
            price = (
                f"{fmt_num(lf['price0'])}（恒定）"
                if lf["const"]
                else f"{fmt_num(lf['price0'])} → {fmt_num(lf['price1'])}"
            )
            A(
                f"| {pair} | {tf} | {lf['n']} | {fmt(lf['start'])[:16]} | {fmt(lf['end'])[:16]} "
                f"| {price} | {fmt_num(lf['maxvol'])} |"
            )
        A("")
    else:
        A("没有检测到。")
        A("")

    # ── 七、限制与补数 ────────────────────────────────────
    A("---")
    A("")
    A("## 七、已知限制与补数")
    A("")
    A("### funding_rate 覆盖不全")
    A("")
    fut_pairs = sorted(fut)
    with_fr = [p for p in fut_pairs if "funding_rate" in fut[p]]
    without_fr = [p for p in fut_pairs if "funding_rate" not in fut[p]]
    A(f"合约 {len(fut_pairs)} 个币对中，**{len(with_fr)} 个** 下载了资金费，**{len(without_fr)} 个缺失**。")
    A("")
    A(f"- 已有：{'、'.join(with_fr)}")
    if without_fr:
        A(f"- 缺失：{'、'.join(without_fr)}")
    A("")
    irregular = [r for r in checks["fund_irregular"] if r[1] > 10]
    if irregular:
        A("结算间隔不规整的币对（中途调整过资金费周期，或数据边缘有异常间隔）：")
        A("")
        A("| 币对 | 行数 | 标准间隔 | 非标准间隔行数 | 最大间隔 |")
        A("|---|---|---|---|---|")
        for cad, irr, rows_n, mx, pair in irregular:
            A(f"| {pair} | {rows_n:,} | {cadence_cn(cad)} | {irr:,} | {mx:g} 小时 |")
        A("")
    A("补齐资金费（文件固定落在 `-1h-funding_rate.feather`）：")
    A("")
    A("```bash")
    A("freqtrade download-data --exchange binance --trading-mode futures \\")
    A("  -p <pairs> -t 1h --candle-types funding_rate --timerange <start>-<end>")
    A("```")
    A("")

    A("### 其余组的日内周期")
    A("")
    if others:
        no_1h = [p for p in others if "1h" not in others[p].get("futures", {})]
        A(
            f"其余 {len(others)} 个币对都没有 `1m / 5m / 15m`；"
            f"{len(others) - len(no_1h)} 个有 `1h`，{len(no_1h)} 个连 `1h` 也没有（只有 `4h + 1d`）。"
        )
        if no_1h:
            A("")
            A(f"行情只有 `4h + 1d` 的：{'、'.join(sorted(no_1h))}")
        A("")
    A("要做日内择时必须先补（每对每周期走一次 REST，量不小）：")
    A("")
    A("```bash")
    A("freqtrade download-data --exchange binance --trading-mode futures \\")
    A("  -p <pairs> -t 1h 15m 5m 1m --candle-types futures --timerange <start>-")
    A("```")
    A("")
    A(
        "注意：显式传 `--candle-types futures` 会**跳过**默认附带的 1h mark。"
        "回测若要计资金费，mark 必须显式列出：`--candle-types futures mark`。"
    )
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
    spot_end = max(m["end"] for p in spot.values() for m in p.values())
    fut_end = max(m["end"] for p in fut.values() for m in p.get("futures", {}).values())
    lag_days = int((fut_end - spot_end).total_seconds() // 86400)
    A(
        f"- 现货数据只到 {fmt(spot_end)[:10]}，合约行情到 {fmt(fut_end)[:10]}"
        f"（相差约 {lag_days} 天）—— 跨现货/合约的研究要取交集。"
    )
    A("- **XAU 是合成 24/7 TradFi 永续，不是 COMEX 真黄金。**")
    A("- 其余组币池是「今天还活着」的币 → **幸存者偏差**，退市币不在池内。")
    A("- `mark` 价格普遍晚于行情 K 线（如 BTC/ETH 从 2019-12-23 起），交易所侧限制。")
    A(
        "- 新上市标的**首日那一段**普通下载拿不到（报 "
        "`Requested start date ... earlier than local data start date`），要补必须加 **`--prepend`**。"
    )
    A("- `download-data` 默认**只向后追加**，不会自动往前补；补更早历史要 `--prepend`，整体重下要 `--erase`。")
    A("- 回测前先确认：目标周期有 mark 文件、有 1h funding 文件，否则资金费会被静默按 0 计（见第一节）。")
    A("")

    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")

    print(f"已生成 {OUT}")
    print(f"  现货 {len(spot)} 对 / 合约 {len(fut)} 对 / {n_files} 个文件 / {total_bytes/1e6:.1f} MB")
    print(f"  funding_rate: {len(with_fr)}/{len(fut_pairs)} 有")


if __name__ == "__main__":
    main()
