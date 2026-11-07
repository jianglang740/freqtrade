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
                # 新版本存 date + funding_rate 两列；老版本存 6 列、费率放在 open
                ohlcv_cols = ["high", "low", "close", "volume"]
                if all(c in df.columns for c in ohlcv_cols):
                    if ((df[ohlcv_cols] != 0).any()).any():
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
    A("### 字段")
    A("")
    A("**行情 / mark：6 列**")
    A("")
    A("| 文件类型 | date | open | high | low | close | volume |")
    A("|---|---|---|---|---|---|---|")
    A("| 行情（现货 / 合约） | K 线起始时间（UTC） | 开盘价 | 最高价 | 最低价 | 收盘价 | 成交量（标的币数量） |")
    A("| mark | 同上 | 标记价开盘 | 标记价最高 | 标记价最低 | 标记价收盘 | **恒为 0**（标记价没有成交量） |")
    A("")
    A("**funding_rate：2 列**")
    A("")
    A("| 文件类型 | date | funding_rate |")
    A("|---|---|---|")
    A("| funding_rate | **结算时点** | **该次结算的资金费率**（小数，0.0001 = 0.01%） |")
    A("")
    A("实际数据长这样（各自文件的最后一行）：")
    A("")
    A("| 文件 | date | open | high | low | close | volume |")
    A("|---|---|---|---|---|---|---|")

    def sample_row_md(label: str, path: str) -> None:
        row = sample_row(path)
        if row is None:
            A(f"| {label} | — | — | — | — | — | — |")
            return
        A(
            f"| {label} | {fmt(row['date'])} | {fmt_num(float(row['open']))} "
            f"| {fmt_num(float(row['high']))} | {fmt_num(float(row['low']))} "
            f"| {fmt_num(float(row['close']))} | {fmt_num(float(row['volume']))} |"
        )

    sample_row_md(
        "行情（BTC 1d）",
        os.path.join(fdir, "BTC_USDT_USDT-1d-futures.feather"),
    )
    sample_row_md(
        "mark（BTC 1h）",
        os.path.join(fdir, "BTC_USDT_USDT-1h-mark.feather"),
    )
    A("")
    A("| 文件 | date | funding_rate |")
    A("|---|---|---|")
    fund_row = sample_row(os.path.join(fdir, "BTC_USDT_USDT-1h-funding_rate.feather"))
    if fund_row is None:
        A("| funding（BTC 1h） | — | — |")
    else:
        rate = fund_row["funding_rate"] if "funding_rate" in fund_row.index else fund_row["open"]
        A(f"| funding（BTC 1h） | {fmt(fund_row['date'])} | {fmt_rate(float(rate))} |")
    A("")
    A(
        "> freqtrade 2026.x 起把资金费存成 `date + funding_rate` 两列；更早的版本存成 6 列、"
        "把费率放在 `open` 列。读取时 `data/converter/converter.py` 的 `add_candle_aliases` "
        "会补一个 `open` 别名，所以两种格式回测都能读。本机现在统一为 2 列。"
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
    sp_tfs = sorted({tf for k in spot.values() for tf in k}, key=TF_ORDER.index)
    A(
        f"| 现货 spot | {len(spot)} | {'/'.join(f'`{t}`' for t in sp_tfs)} | "
        f"{fmt(sp_min)[:10]} → {fmt(sp_max)[:10]} "
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
        A(f"共 **{len(others)}** 个币对（以下数字由文件现场统计）：")
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
    A(
        f"共 **{len(spot)}** 个币对，周期 {'/'.join(f'`{t}`' for t in sp_tfs)}。"
        "这份数据只喂回测与 dry-run 实例。做因子研究请用 [因子研究/](因子研究/行情数据/README.md) 的数据湖"
        "（含已下架币，独立于本报告）。"
    )
    A("")
    A("| # | 币对 | 周期 | 起 | 止 | 根数 |")
    A("|---|---|---|---|---|---|")
    sp_spans = []
    for p, k in spot.items():
        tfs = sorted(k, key=TF_ORDER.index)
        s = min(m["start"] for m in k.values())
        e = max(m["end"] for m in k.values())
        sp_spans.append((p, "/".join(tfs), s, e, sum(m["rows"] for m in k.values()),
                         (e - s).days))
    sp_spans.sort(key=lambda r: r[5])
    for i, (pair, tfs, s, e, rows, _d) in enumerate(sp_spans, 1):
        A(f"| {i} | {pair} | {tfs} | {fmt(s)[:10]} | {fmt(e)[:10]} | {rows:,} |")
    A("")

    # ── 六、数据质量 ──────────────────────────────────────
    A("---")
    A("")
    A("## 六、数据质量：缺口与占位段")
    A("")
    A("### 自检")
    A("")
    def schema_ok_for(kind: str, cols: tuple) -> bool:
        if kind == "funding_rate":
            # 新格式 2 列；旧格式 6 列（费率在 open）。两种都算合法。
            return cols in (("date", "funding_rate"), ("date", "open", "high", "low", "close", "volume"))
        return cols == OHLCV_COLS

    schema_ok = all(schema_ok_for(kind, cols) for (kind, cols) in checks["schemas"])
    legacy_fund = sum(
        n for (kind, cols), n in checks["schemas"].items()
        if kind == "funding_rate" and cols != ("date", "funding_rate")
    )
    new_fund = sum(
        n for (kind, cols), n in checks["schemas"].items()
        if kind == "funding_rate" and cols == ("date", "funding_rate")
    )
    A(
        "- 列结构："
        + (
            "行情 / mark 全部 6 列；funding 全部 `date + funding_rate` 2 列"
            if schema_ok and legacy_fund == 0
            else "**发现异常列结构，见下方警告**"
        )
    )
    A(f"- funding 旧 6 列格式：{legacy_fund} 个文件；新 2 列格式：{new_fund} 个文件")
    A(f"- 重复时间戳：{checks['dupes']} 行")
    A(f"- mark 文件里 volume 非 0 的币对：{len(checks['mark_vol_nonzero'])} 个"
      + (f"（{'、'.join(sorted(set(checks['mark_vol_nonzero'])))}）" if checks["mark_vol_nonzero"] else ""))
    A(f"- funding 旧 6 列文件里 high/low/close/volume 非 0 的币对：{len(checks['fund_nonopen_nonzero'])} 个"
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
    A("### funding_rate 覆盖")
    A("")
    fut_pairs = sorted(fut)
    with_fr = [p for p in fut_pairs if "funding_rate" in fut[p]]
    without_fr = [p for p in fut_pairs if "funding_rate" not in fut[p]]
    A(f"合约 {len(fut_pairs)} 个币对中，**{len(with_fr)} 个有 funding 文件，{len(without_fr)} 个缺失**。")
    A("")
    if without_fr:
        A(f"- 缺失：{'、'.join(without_fr)}")
        A("")

    # 末次结算明显落后于其他币对的（通常是退市 / 结算中）
    fund_ends = []
    for p in with_fr:
        for tf, meta in fut[p]["funding_rate"].items():
            fund_ends.append((p, meta["end"]))
    if fund_ends:
        latest = max(e for _, e in fund_ends)
        lagging = sorted((p, e) for p, e in fund_ends if e < latest - pd.Timedelta(days=3))
        if lagging:
            A("末次结算明显早于其他币对（交易所侧已停止该标的结算，退市 / 结算中）：")
            A("")
            A("| 币对 | 末次结算 | 比全库最晚早 |")
            A("|---|---|---|")
            for p, e in lagging:
                days = (latest - e).total_seconds() / 86400
                A(f"| {p} | {fmt(e)[:16]} | {days:.0f} 天 |")
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
    A(
        "> 大批量补 funding 时，freqtrade 的并发请求可能被 Binance WAF 间歇返回 403。"
        "可靠做法是小批次跑（每批 3 个、批间等待），或直接分页调用 "
        "`/fapi/v1/fundingRate`（每页 1000 条，按 `date + funding_rate` 两列写 feather）。"
    )
    A("")

    A("### 其余组的日内周期")
    A("")
    if others:
        no_1h = [p for p in others if "1h" not in others[p].get("futures", {})]
        if no_1h:
            A(
                f"其余 {len(others)} 个币对都没有 `1m / 5m / 15m`；"
                f"{len(others) - len(no_1h)} 个有 `1h`，{len(no_1h)} 个连 `1h` 也没有（只有 `4h + 1d`）。"
            )
            A("")
            A(f"行情只有 `4h + 1d` 的：{'、'.join(sorted(no_1h))}")
        else:
            A(
                f"其余 {len(others)} 个币对均已有 `1h + 4h + 1d`；"
                "仍缺 `1m / 5m / 15m`（这三档目前只有 BTC / ETH / XAU 有）。"
            )
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
    sp_1d_end = max((m["end"] for p in spot.values() for tf, m in p.items() if tf == "1d"),
                    default=None)
    A(
        f"- 现货 `1h` 到 {fmt(spot_end)[:16]}，`1d` 到 "
        f"{fmt(sp_1d_end)[:10] if sp_1d_end is not None else '—'}"
        "（日线只到最后一根**走完**的日 K，所以永远比小时线早一天）；"
        f"合约行情到 {fmt(fut_end)[:16]}。跨市场研究要取交集。"
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

    # ── 八、交易对池的来历 ────────────────────────────────
    A("---")
    A("")
    A("## 八、交易对池是怎么选出来的")
    A("")
    A("### 先说结论：两边的池子不是同一条规则")
    A("")
    common = sorted(set(spot) & set(fut))
    only_spot = sorted(set(spot) - set(fut))
    only_fut = sorted(set(fut) - set(spot))
    A(
        f"现货 {len(spot)} 个、合约 {len(fut)} 个：**{len(common)} 个重合**，"
        f"只在现货 {len(only_spot)} 个，只在合约 {len(only_fut)} 个。"
    )
    A("")
    A("| 分组 | 数量 | 币种 |")
    A("|---|---|---|")
    A(f"| 现货、合约都有 | {len(common)} | — |")
    A(f"| 只在现货 | {len(only_spot)} | {'、'.join(only_spot) or '无'} |")
    A(f"| 只在合约 | {len(only_fut)} | {'、'.join(only_fut) or '无'} |")
    A("")

    A("### 现货这 100 个是怎么来的")
    A("")
    A(
        "2026-08-20 定下的口径：按 **24 小时成交额取 Top 100**，并剔除"
        "稳定币、黄金/包装资产（PAXG、XAUT 类）、Beta 美股代币、杠杆代币、"
        "非 ASCII 名称、以及当时已下架或停牌的币对。最初要的是「Top 100 全部历史」，"
        "随后简化成**只要 `1d`**；2026-10-31 为了做面板分析又补齐了 `1h`（100/100 对）。"
    )
    A("")
    A(
        "> 诚实交代：建池脚本当时放在 `/tmp`，没有入库，已经不存在了。"
        "上面的筛法是当时的记录，**无法按原样复现**；"
        "能复现的只有这份落盘结果（本报告扫描出来的名单）。"
    )
    A("")

    A("### 合约这 95 个是怎么来的")
    A("")
    A(
        "**不是**按合约成交额单独筛的 Top 95。可复现的证据是集合关系："
        "合约池 = 现货池里符号能 1:1 对上 USDT-M 永续的那些，再加 XAU。"
    )
    A("")
    A(
        f"- 现货池里有 {len(only_spot)} 个没有同符号 USDT 永续，因此没有进合约池；"
        f"最典型的是 memecoin —— Binance 只给了 1000 倍面值的永续"
        f"（`1000PEPE`、`1000SHIB`、`1000BONK`、`1000FLOKI`），符号对不上 `PEPE/USDT:USDT` 这种写法。"
    )
    A(
        "- XAU 是**合约独有**，为伦敦金研究单独加的（现货侧 XAU 被「剔除黄金」规则挡掉了）。"
    )
    A(
        f"- 数量刚好闭合：`{len(spot)} − {len(only_spot)} + {len(only_fut)} = {len(fut)}`。"
    )
    A("")
    A(
        "> 哪几个币只有 1000 倍面值永续、哪个根本没有 USDT 永续，是 **Binance 交易所侧的事实**"
        "（2026-10-31 核对 `exchangeInfo`），本地扫描看不到 —— 本地只能看到「没拉」。"
    )
    A("")

    A("### 对研究的实际影响")
    A("")
    if only_spot:
        A(
            f"- **合约截面缺了 {'、'.join(only_spot)}**。这些是现货高成交标的，"
            "做横截面动量/排序时属于**池子覆盖偏差**，不是样本随机缺失。"
        )
    if only_fut:
        A(
            f"- **{'、'.join(only_fut)} 不是加密资产**。做加密横截面时应剔除或单列，"
            "否则会把「币圈动量」和「贵金属趋势」混在同一个排序里。"
        )
    A(
        "- 即使重合的那 "
        f"{len(common)} 个，**口径也不同**：现货是 `1h + 1d`（无 mark、无 funding），"
        "合约是 `1h + 4h + 1d` 加 mark 和 funding；两边截止日期也差几天"
        "（见「其他注意事项」）。跨市场研究要取交集。"
    )
    A("")
    A("### 想把 memecoin 补进合约池")
    A("")
    A("```bash")
    A("freqtrade download-data --exchange binance --trading-mode futures \\")
    A('  -p "1000PEPE/USDT:USDT" "1000SHIB/USDT:USDT" "1000BONK/USDT:USDT" "1000FLOKI/USDT:USDT" \\')
    A("  -t 1h 4h 1d --candle-types futures mark --timerange 20221201-")
    A("```")
    A("")
    A(
        "注意两点：① 1000 倍面值永续的**价格是原币的 1000 倍**，"
        "做横截面时必须换算回同一尺度，别直接和 `PEPE` 的现货价比；"
        "② funding 下载大概率会撞 403，按第七节的办法小批次补。"
        "补完**必须重跑本脚本**，否则本节的分组表还是旧的。"
    )
    A("")

    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")

    print(f"已生成 {OUT}")
    print(f"  现货 {len(spot)} 对 / 合约 {len(fut)} 对 / {n_files} 个文件 / {total_bytes/1e6:.1f} MB")
    print(f"  funding_rate: {len(with_fr)}/{len(fut_pairs)} 有")


if __name__ == "__main__":
    main()
