#!/usr/bin/env python
"""
第一步：从 data.binance.vision 归档下载 K 线，落成**单符号缓存**。

为什么不用 `freqtrade download-data`：
  - 交易所 API 只返回**当前**上线的交易对，已下架的直接消失（这正是幸存者偏差的来源）；
  - 它写进 `user_data/data/`，会污染两个正在跑的 dry-run 实例的数据。

为什么月度归档 + 日度补尾：
  - 月度包体积小、请求少，覆盖到**上一个月**（实测 2026-11-06 时已有 2026-10）；
  - 日度包当天不开、昨天有，用来把尾巴补到「最多落后 1 天」。

产物（全部在 `因子研究/行情数据/.cache_bv/<market>/<tf>/`，已 gitignore）：
  <SYMBOL>.parquet   单符号全历史，列见 common.KLINE_COLUMNS / FUNDING_COLUMNS
  _fetch.json        本次抓取的符号清单与错误明细

用法：
    ssh -fN quant-proxy                                    # 先起 SOCKS
    python 因子研究/行情数据/_build/01_fetch_bv.py --market spot --tf 1d 1h --limit 20   # 冒烟
    python 因子研究/行情数据/_build/01_fetch_bv.py --market spot --tf 1d 1h
    python 因子研究/行情数据/_build/01_fetch_bv.py --market um   --tf 1d 1h funding
    python 因子研究/行情数据/_build/01_fetch_bv.py --market spot --tf 1h --refresh       # 增量：只补最近 2 个月 + 尾巴
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import time
import urllib.parse
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (  # noqa: E402
    BASE, CACHE, FUNDING_COLUMNS, KLINE_COLUMNS, MARKET_PATH, MARKETS,
    QUOTE, WORKERS, cache_dir, ensure_dirs, http_get, is_leveraged, split_symbol,
)

MONTH_RE = re.compile(r"-(\d{4})-(\d{2})\.zip$")
DAY_RE = re.compile(r"-(\d{4})-(\d{2})-(\d{2})\.zip$")
TAIL_LOOKBACK_DAYS = 60   # 最后一个完整月离今天超过这个天数，认为是已下架币，不补日包


# ══════════════════════════════════════════════ S3 列举
def list_prefixes(prefix: str) -> list[str]:
    """列出某个前缀下的**直接子目录**（S3 delimiter 分页）。"""
    out, marker = [], None
    for _ in range(80):
        q = {"delimiter": "/", "prefix": prefix}
        if marker:
            q["marker"] = marker
        txt = http_get(BASE + "?" + urllib.parse.urlencode(q)).decode("utf-8", "ignore")
        page = [p for p in re.findall(r"<Prefix>([^<]+)</Prefix>", txt) if p != prefix]
        out += page
        if "<IsTruncated>true</IsTruncated>" not in txt or not page:
            break
        marker = page[-1]
    return out


def list_keys(prefix: str) -> list[str]:
    """列出某个前缀下的**全部对象名**。"""
    out, marker = [], None
    for _ in range(80):
        q = {"prefix": prefix}
        if marker:
            q["marker"] = marker
        txt = http_get(BASE + "?" + urllib.parse.urlencode(q)).decode("utf-8", "ignore")
        page = re.findall(r"<Key>([^<]+)</Key>", txt)
        out += page
        if "<IsTruncated>true</IsTruncated>" not in txt or not page:
            break
        marker = page[-1]
    return out


def enumerate_symbols(market: str, tf: str) -> list[str]:
    """归档里该市场该周期出现过的全部 USDT 符号（含已下架）。"""
    prefix = f"{MARKET_PATH[market]}/monthly/" + ("fundingRate/" if tf == "funding" else f"klines/")
    subs = list_prefixes(prefix)
    syms = sorted(p.rstrip("/").rsplit("/", 1)[1] for p in subs)
    keep = [s for s in syms
            if s.endswith(QUOTE) and "_" not in s and not is_leveraged(s)]
    return keep


# ══════════════════════════════════════════════ 解析
def _unzip_csv(raw: bytes) -> str | None:
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            return z.read(z.namelist()[0]).decode("utf-8", "ignore")
    except Exception:
        return None


def _to_frame(txt: str, ncol: int) -> pd.DataFrame | None:
    """归档 CSV → DataFrame。兼容**有/无表头**、毫秒/微秒时间戳、列数不足。"""
    if not txt.strip():
        return None
    try:
        df = pd.read_csv(io.StringIO(txt), header=None)
    except Exception:
        return None
    if df.empty or 0 not in df.columns:
        return None
    ts = pd.to_numeric(df[0], errors="coerce")      # 表头行在这里变成 NaN，一并丢掉
    keep = ts.notna()
    if not keep.any():
        return None
    # 原始列数要在 reindex 之前记下：reindex 会把缺的列补成 NaN 列，
    # 之后就分不清「归档本来只有 2 列」和「第 3 列是空的」。
    ncol_raw = df.shape[1]
    # reset_index 必须做：丢掉表头行后索引会从 1 开始，后面按数组对齐会错位
    df = df.loc[keep].reindex(columns=range(ncol)).reset_index(drop=True)
    ts = ts.loc[keep].reset_index(drop=True)

    # 单位**逐行**判定，不能按首行一刀切：归档里存在同一个 CSV 内毫秒/微秒混用的情况。
    # 实测 KLAYUSDT-1d-2024-10.zip：前 28 行是毫秒，最后 2 行是微秒（该币下架后重发的包）。
    # 一刀切会把那 2 行放大 1000 倍，变成 year=56799 的幽灵 bar，且一路静默通过
    # 分区和校验（只在下架币上偶发），所以这里必须逐行归一。
    v = ts.to_numpy(dtype="int64")
    v = np.where(v >= 10**14, v // 1000, v)      # 微秒 → 毫秒
    v = np.where(v < 10**11, v * 1000, v)        # 秒   → 毫秒
    date = pd.Series(pd.to_datetime(v, unit="ms", utc=True))
    # 与 feather / 旧面板保持同一 dtype：datetime64[ms, UTC]
    date = date.dt.tz_localize(None).astype("datetime64[ms]").dt.tz_localize("UTC")

    def num(i: int):
        if i not in df.columns:
            return pd.Series(float("nan"), index=df.index).to_numpy()
        return pd.to_numeric(df[i], errors="coerce").to_numpy()

    out = pd.DataFrame({
        "date": date,                       # 直接传 Series，保住 datetime64[ms, UTC]
        "open": num(1), "high": num(2), "low": num(3), "close": num(4),
        "volume": num(5),
    })
    if ncol >= 11:                                   # K 线：12 列
        out["quote_volume"] = num(7)
        out["trade_count"] = num(8)
        out["taker_buy_base_volume"] = num(9)
        out["taker_buy_quote_volume"] = num(10)
    elif ncol_raw >= 3:                              # 资金费率：3 列
        # calc_time, funding_interval_hours, last_funding_rate
        out["funding_interval_hours"] = num(1)
        out["funding_rate"] = num(2)
    else:                                            # 老的两列包：只有算出来的费率
        out["funding_interval_hours"] = 8.0
        out["funding_rate"] = num(1)
    return out


def parse_kline(raw: bytes) -> pd.DataFrame | None:
    txt = _unzip_csv(raw)
    if txt is None:
        return None
    out = _to_frame(txt, 12)
    if out is None or out.empty:
        return None
    # 官方计价成交额缺失时退回 close × volume，并留痕在 manifest 里
    out["quote_volume"] = out["quote_volume"].fillna(out["close"] * out["volume"])
    out["trade_count"] = out["trade_count"].fillna(0).astype("int64")
    out["taker_buy_base_volume"] = out["taker_buy_base_volume"].fillna(0.0)
    out["taker_buy_quote_volume"] = out["taker_buy_quote_volume"].fillna(0.0)
    return out


def parse_funding(raw: bytes) -> pd.DataFrame | None:
    txt = _unzip_csv(raw)
    if txt is None:
        return None
    out = _to_frame(txt, 3)
    if out is None or out.empty:
        return None
    # interval 缺失（老格式前几行没写）时按 8 小时处理，这是币安 2023 年前的统一间隔
    out["funding_interval_hours"] = (
        pd.to_numeric(out["funding_interval_hours"], errors="coerce")
        .fillna(8.0).round().astype("int64")
    )
    return out[["date", "funding_interval_hours", "funding_rate"]]


# ══════════════════════════════════════════════ 抓一个符号
def _monthly_prefix(market: str, tf: str, symbol: str) -> str:
    if tf == "funding":
        return f"{MARKET_PATH[market]}/monthly/fundingRate/{symbol}/"
    return f"{MARKET_PATH[market]}/monthly/klines/{symbol}/{tf}/"


def _daily_prefix(market: str, tf: str, symbol: str) -> str:
    return f"{MARKET_PATH[market]}/daily/klines/{symbol}/{tf}/"


def _tail_days(last_month: str | None) -> list[str]:
    """月度归档之后还要补哪些日包。返回 'YYYY-MM-DD' 列表。"""
    today = pd.Timestamp.now(tz="UTC").normalize()
    end = today - pd.Timedelta(days=1)               # 当日日包当天不发
    if last_month:
        y, m = int(last_month[:4]), int(last_month[5:7])
        start = pd.Timestamp(year=y, month=m, day=1, tz="UTC") + pd.offsets.MonthBegin(1)
        if (today - start).days > TAIL_LOOKBACK_DAYS:
            return []                                # 早就没数据了，不追
    else:
        start = end - pd.Timedelta(days=30)          # 全新符号：只看最近一个月
    if start > end:
        return []
    return [d.strftime("%Y-%m-%d") for d in pd.date_range(start, end, freq="D")]


def fetch_symbol(market: str, tf: str, symbol: str, refresh: bool) -> dict:
    """抓一个符号的全历史，写缓存。返回一条审计记录。"""
    out_f = os.path.join(cache_dir(market, tf), f"{symbol}.parquet")
    cached = os.path.exists(out_f)
    if cached and not refresh:
        try:
            n = len(pd.read_parquet(out_f, columns=["date"]))
            return {"symbol": symbol, "status": "cached", "rows": n}
        except Exception:
            pass                                      # 缓存坏了，重下

    mkeys = sorted(k for k in list_keys(_monthly_prefix(market, tf, symbol))
                   if k.endswith(".zip"))             # 完整对象名，直接用
    if refresh and cached and mkeys:
        mkeys = mkeys[-2:]                            # 只重抓最近两个月

    errors: list[str] = []
    frames: list[pd.DataFrame] = []
    is_kline = tf != "funding"
    parse = parse_kline if is_kline else parse_funding

    for k in mkeys:
        raw = http_get(BASE + "/" + k)
        if not raw:
            errors.append(k.rsplit("/", 1)[-1])
            continue
        d = parse(raw)
        if d is not None and not d.empty:
            frames.append(d)

    if is_kline:
        last_month = None
        if mkeys:
            mm = MONTH_RE.search(mkeys[-1])
            if mm:
                last_month = f"{mm.group(1)}-{mm.group(2)}"
        for day in _tail_days(last_month):
            raw = http_get(BASE + "/" + _daily_prefix(market, tf, symbol) + f"{symbol}-{tf}-{day}.zip")
            if not raw:
                continue                              # 未发布 / 未上市，正常
            d = parse(raw)
            if d is not None and not d.empty:
                frames.append(d)

    if not frames:
        return {"symbol": symbol, "status": "empty", "rows": 0, "errors": errors[:5]}

    df = pd.concat(frames, ignore_index=True)
    df = df.drop_duplicates(subset=["date"], keep="last").sort_values("date")
    base, quote = split_symbol(symbol)
    df.insert(0, "quote", quote)
    df.insert(0, "base", base)
    df.insert(0, "symbol", symbol)
    df = df[FUNDING_COLUMNS if not is_kline else KLINE_COLUMNS].reset_index(drop=True)
    df.to_parquet(out_f, index=False, compression="zstd")
    return {"symbol": symbol, "status": "ok", "rows": int(len(df)),
            "start": str(df["date"].iloc[0])[:10], "end": str(df["date"].iloc[-1])[:10],
            "errors": errors[:5]}


# ══════════════════════════════════════════════ main
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", required=True, choices=list(MARKETS))
    ap.add_argument("--tf", nargs="+", required=True, choices=["1d", "1h", "funding"])
    ap.add_argument("--symbols", default="all", help="all 或逗号分隔的符号")
    ap.add_argument("--workers", type=int, default=WORKERS)
    ap.add_argument("--limit", type=int, default=0, help="只抓前 N 个符号，冒烟用")
    ap.add_argument("--refresh", action="store_true", help="已缓存的只补最近 2 个月 + 日包尾巴")
    args = ap.parse_args()

    ensure_dirs()
    for tf in args.tf:
        if tf == "funding" and args.market != "um":
            print(f"[{args.market}/{tf}] 资金费率只存在于 U 本位合约，跳过")
            continue
        os.makedirs(cache_dir(args.market, tf), exist_ok=True)

        all_syms = enumerate_symbols(args.market, tf)
        if args.symbols != "all":
            want = {s.strip().upper() for s in args.symbols.split(",") if s.strip()}
            syms = [s for s in all_syms if s in want]
            missing = want - set(syms)
            if missing:
                print(f"⚠️  这些符号不在归档里：{sorted(missing)}")
        else:
            syms = all_syms
        if args.limit:
            syms = syms[: args.limit]

        print(f"[{args.market}/{tf}] 归档共 {len(all_syms)} 个 USDT 符号 → 本次抓 {len(syms)} 个")
        t0 = time.time()
        recs: list[dict] = []
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = {ex.submit(fetch_symbol, args.market, tf, s, args.refresh): s for s in syms}
            for done, f in enumerate(as_completed(futs), 1):
                try:
                    recs.append(f.result())
                except Exception as e:                # 单个符号炸了不能拖垮全局
                    recs.append({"symbol": futs[f], "status": "error", "rows": 0, "errors": [str(e)]})
                if done % 50 == 0 or done == len(syms):
                    rows = sum(r.get("rows", 0) for r in recs)
                    print(f"  {done}/{len(syms)}  {time.time()-t0:.0f}s  累计 {rows:,} 行", flush=True)

        empty = [r["symbol"] for r in recs if r["status"] in ("empty", "error")]
        with_err = {r["symbol"]: r["errors"] for r in recs if r.get("errors")}
        log = {
            "market": args.market, "tf": tf,
            "built_at": pd.Timestamp.now(tz="UTC").isoformat(),
            "symbols_total": len(all_syms), "symbols_fetched": len(syms),
            "rows": int(sum(r.get("rows", 0) for r in recs)),
            "empty_or_error": empty,
            "errors": with_err,
        }
        with open(os.path.join(cache_dir(args.market, tf), "_fetch.json"), "w", encoding="utf-8") as fh:
            json.dump(log, fh, ensure_ascii=False, indent=1)
        print(f"[{args.market}/{tf}] 完成：{log['rows']:,} 行 / {len(syms)} 个符号 / "
              f"空或失败 {len(empty)} 个 / {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
