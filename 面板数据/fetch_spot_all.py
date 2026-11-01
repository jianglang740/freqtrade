#!/usr/bin/env python
"""
抓取 data.binance.vision 上**全部曾经存在过**的 USDT 现货交易对的 1d K 线，
构建「全历史币池」面板 —— 这是消除存活者偏差的唯一办法。

为什么必须用归档而不是 ccxt：
  - 交易所 API 只返回**当前**上线的交易对，已下架的直接消失；
  - data.binance.vision 的归档保留了下架币的历史文件。

产物：
  - `spot_all_1d.parquet`：长表，列与 `build_panel.py` 完全一致
    （pair, date, open, high, low, close, volume, quote_volume），
    上市/下架天然由「有没有行」表达，不需要额外字段。
  - `spot_all_1d.manifest.json`：每个币的首末日期、行数、缺口，以及排除清单。

与已有 100 对面板的关系：
  这是**独立文件**，不写进 `user_data/data/binance/`，所以不会影响
  `K线数据报告.md`、也不会动到那两个正在跑的 dry-run 实例的数据。

用法：
    ssh -fN quant-proxy                       # 先起 socks 代理
    python 面板数据/fetch_spot_all.py --limit 5   # 冒烟测试
    python 面板数据/fetch_spot_all.py             # 全量（约 15 分钟）
"""

from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
SOCKS = "socks5h://127.0.0.1:1080"
CACHE = os.path.join(HERE, ".cache_bv")
OUT = os.path.join(HERE, "spot_all_1d.parquet")
MAN = os.path.join(HERE, "spot_all_1d.manifest.json")

LEV_RE = re.compile(r"(UP|DOWN|BULL|BEAR)USDT$")
WORKERS = 16

COLS = ["pair", "date", "open", "high", "low", "close", "volume", "quote_volume"]


# ────────────────────────────────────────────── HTTP
def http_get(url: str, timeout: int = 90) -> bytes:
    p = subprocess.run(
        ["curl", "-s", "-f", "--socks5-hostname", "127.0.0.1:1080",
         "--max-time", str(timeout), url],
        capture_output=True)
    return p.stdout if p.returncode == 0 else b""


def list_keys(prefix: str) -> list[str]:
    """列出某个前缀下的全部对象名（自动翻页）。"""
    keys, marker = [], None
    for _ in range(50):
        q = {"delimiter": "/", "prefix": prefix}
        if marker:
            q["marker"] = marker
        txt = http_get(BASE + "?" + urllib.parse.urlencode(q)).decode("utf-8", "ignore")
        page = re.findall(r"<Key>([^<]+)</Key>", txt)
        keys += page
        if "<IsTruncated>true</IsTruncated>" not in txt or not page:
            break
        marker = page[-1]
    return keys


def list_symbols() -> list[str]:
    syms, marker = [], None
    for _ in range(20):
        q = {"delimiter": "/", "prefix": "data/spot/monthly/klines/"}
        if marker:
            q["marker"] = marker
        txt = http_get(BASE + "?" + urllib.parse.urlencode(q)).decode("utf-8", "ignore")
        page = re.findall(r"<Prefix>data/spot/monthly/klines/([^/]+)/</Prefix>", txt)
        syms += page
        if "<IsTruncated>true</IsTruncated>" not in txt:
            break
        marker = "data/spot/monthly/klines/" + page[-1] + "/"
    return syms


# ────────────────────────────────────────────── 解析
def parse_zip(raw: bytes) -> pd.DataFrame | None:
    """一个月的 1d 归档 → DataFrame。兼容有/无表头、毫秒/微秒时间戳。"""
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            name = z.namelist()[0]
            txt = z.read(name).decode("utf-8", "ignore")
    except Exception:
        return None
    rows = []
    for line in txt.splitlines():
        p = line.split(",")
        if len(p) < 7 or not p[0].strip().isdigit():
            continue                      # 表头或坏行
        rows.append(p)
    if not rows:
        return None
    t0 = int(rows[0][0])
    unit = "us" if t0 > 10**15 else ("ms" if t0 > 10**11 else "s")
    out = pd.DataFrame({
        "date": pd.to_datetime([int(r[0]) for r in rows], unit=unit, utc=True),
        "open": [float(r[1]) for r in rows],
        "high": [float(r[2]) for r in rows],
        "low": [float(r[3]) for r in rows],
        "close": [float(r[4]) for r in rows],
        "volume": [float(r[5]) for r in rows],
        "quote_volume": [float(r[7]) for r in rows],
    })
    return out


def fetch_symbol(sym: str) -> tuple[str, pd.DataFrame | None, list[str]]:
    """抓一个币的全部月度归档；返回 (pair, df, 错误列表)。"""
    pair = f"{sym[:-4]}/USDT"
    cache_f = os.path.join(CACHE, f"{sym}.parquet")
    if os.path.exists(cache_f):
        try:
            df = pd.read_parquet(cache_f)
            df["date"] = pd.to_datetime(df["date"], utc=True)
            return pair, df, []
        except Exception:
            pass

    base = f"data/spot/monthly/klines/{sym}/1d/"
    keys = [k for k in list_keys(base) if k.endswith(".zip")]
    if not keys:
        return pair, None, [f"{sym}: 无归档"]
    frames, errs = [], []
    for k in keys:
        raw = http_get(BASE + "/" + k)
        if not raw:
            errs.append(k)
            continue
        d = parse_zip(raw)
        if d is not None:
            frames.append(d)
    if not frames:
        return pair, None, errs

    df = pd.concat(frames, ignore_index=True)
    df = df.drop_duplicates(subset=["date"], keep="last")
    df = df[df["volume"] > 0]
    df = df.sort_values("date").reset_index(drop=True)
    # 归档里的日期是毫秒精度的 UTC 零点；和面板保持同一 dtype
    df["date"] = df["date"].dt.tz_localize(None).astype("datetime64[ms]").dt.tz_localize("UTC")
    df.insert(0, "pair", pair)
    df = df[COLS]
    try:
        df.to_parquet(cache_f, index=False, compression="zstd")
    except Exception:
        pass
    return pair, df, errs


# ────────────────────────────────────────────── main
def main() -> None:
    limit = None
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])

    os.makedirs(CACHE, exist_ok=True)
    t_start = time.time()

    all_syms = list_symbols()
    usdt = sorted(s for s in all_syms if s.endswith("USDT"))
    lev = [s for s in usdt if LEV_RE.search(s)]
    keep = [s for s in usdt if s not in set(lev)]
    if limit:
        keep = keep[:limit]
    print(f"归档里共 {len(all_syms)} 个符号；USDT 计价 {len(usdt)} 个，"
          f"排除杠杆代币 {len(lev)} 个 → 抓取 {len(keep)} 个")
    print(f"缓存目录 {os.path.relpath(CACHE, HERE)}")

    frames, errors = [], {}
    done = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(fetch_symbol, s): s for s in keep}
        for f in as_completed(futs):
            pair, df, errs = f.result()
            done += 1
            if df is not None and len(df):
                frames.append(df)
            if errs:
                errors[pair] = errs[:5]
            if done % 25 == 0 or done == len(keep):
                print(f"  {done}/{len(keep)}  {time.time() - t_start:.0f}s  "
                      f"累计 {sum(len(x) for x in frames):,} 行", flush=True)

    if not frames:
        print("没有抓到任何数据，检查代理（ssh -fN quant-proxy）")
        return

    panel = pd.concat(frames, ignore_index=True)
    panel = panel.drop_duplicates(subset=["pair", "date"], keep="last")
    panel = panel.sort_values(["pair", "date"]).reset_index(drop=True)
    panel.to_parquet(OUT, index=False, compression="zstd", row_group_size=500_000)

    # ── 每个币的生死区间
    g = panel.groupby("pair")["date"]
    per = pd.DataFrame({"首个": g.min(), "末个": g.max(), "行数": g.size()})
    per["跨度天数"] = (per["末个"] - per["首个"]).dt.days + 1
    per["缺口"] = per["跨度天数"] - per["行数"]

    manifest = {
        "schema_version": 1,
        "source": "data.binance.vision 归档（含已下架交易对）",
        "built_at": pd.Timestamp.now(tz="UTC").isoformat(),
        "rows": int(len(panel)),
        "pairs": int(panel["pair"].nunique()),
        "date_from": str(panel["date"].min()),
        "date_to": str(panel["date"].max()),
        "excluded_leveraged": lev,
        "columns": COLS,
        "per_pair": {
            p: {"first": str(r["首个"]), "last": str(r["末个"]),
                "rows": int(r["行数"]), "gaps": int(r["缺口"])}
            for p, r in per.iterrows()
        },
        "errors": errors,
    }
    with open(MAN, "w") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=1)

    print("=" * 78)
    print(f"面板 {len(panel):,} 行 / {panel['pair'].nunique()} 个币 / "
          f"{panel['date'].min().date()} → {panel['date'].max().date()}")
    cutoff = per["末个"].max() - pd.Timedelta(days=30)   # 以面板自身最新一天为基准
    print(f"已下架（末个早于 {cutoff.date()}）：{int((per['末个'] < cutoff).sum())} 个")
    print(f"缺口 > 5 天的币：{int((per['缺口'] > 5).sum())} 个")
    print(f"写 {os.path.relpath(OUT, os.path.dirname(HERE))} "
          f"({os.path.getsize(OUT) / 1e6:.1f} MB) 与 manifest")
    print(f"耗时 {time.time() - t_start:.0f}s")


if __name__ == "__main__":
    main()
