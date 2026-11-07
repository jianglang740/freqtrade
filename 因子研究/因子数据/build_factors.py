#!/usr/bin/env python
"""
从行情数据湖批量计算因子，落成因子面板（供 因子研究/回测框架/ 回测）。

    python 因子研究/因子数据/build_factors.py --market spot --tf 1d
    python 因子研究/因子数据/build_factors.py --market spot --tf 1h --limit 30   # 冒烟

口径（和股票因子库一致，全部只看过去）：
  - 所有滚动窗口都是**尾部窗口**（rolling / shift 向前看），不含未来数据；
  - 收益用 close 的简单收益率；
  - 成交额一律用归档官方的 quote_volume（计价币成交额，不是 close×volume 估算）；
  - 因子在**全历史**上计算，回测时才按当日截面去极值/标准化 + 股票池过滤 ——
    这样同一个因子面板可以配不同的股票池复用，不用为每个池子重算。

因子命名带窗口单位：1d 用小写 `d`（mom_30d），1h 用 `h`（mom_168h）。
"""

from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))              # 因子研究/因子数据
ROOT = os.path.dirname(os.path.dirname(HERE))                  # freqtrade/
LAKE = os.path.join(os.path.dirname(HERE), "行情数据")          # 因子研究/行情数据
sys.path.insert(0, LAKE)
sys.path.insert(0, HERE)
import lake_io  # noqa: E402
import factor_io  # noqa: E402

# ── 窗口配置：1d 用自然日；1h 用 bar 数（24/72/168/720 = 1/3/7/30 天）
WINDOWS = {
    # 波动率的窗口刻意和动量一一对应：这样 `mom_w / vol_w` 随时可做，
    # 风险调整动量这类组合因子在项目里不用回头重算行情。
    "1d": dict(unit="d", mom=(7, 30, 90, 180), vol=(7, 30, 90, 180), other_short=30, other_mid=30),
    "1h": dict(unit="h", mom=(24, 72, 168, 720), vol=(24, 72, 168, 720), other_short=24, other_mid=168),
}
COLS = ["symbol", "date", "open", "high", "low", "close",
        "volume", "quote_volume", "taker_buy_quote_volume"]


def _rolling(sym: pd.Series, s: pd.Series, w: int, how: str) -> pd.Series:
    """按符号分组的尾部滚动统计。min_periods 取窗口的一半（至少 2）。"""
    mp = max(2, w // 2)
    return s.groupby(sym, observed=True, sort=False).transform(
        lambda x: getattr(x.rolling(w, min_periods=mp), how)())


def compute(df: pd.DataFrame, tf: str, keep: list[str] | None = None) -> pd.DataFrame:
    """长表 → 因子长表。df 必须已按 (symbol, date) 排序。"""
    cfg = WINDOWS[tf]
    u = cfg["unit"]
    df = df.sort_values(["symbol", "date"], kind="stable").reset_index(drop=True)
    sym = df["symbol"]
    g = df.groupby("symbol", observed=True, sort=False)

    close = df["close"].astype("float64")
    ret = close / g["close"].shift(1) - 1.0
    qv = df["quote_volume"].clip(lower=0).astype("float64").replace(0.0, np.nan)
    taker = df["taker_buy_quote_volume"].astype("float64")
    rng = (df["high"] - df["low"]).astype("float64") / close
    logv = np.log1p(qv)

    out = pd.DataFrame({"date": df["date"], "symbol": sym})
    ret_s = ret.rename("ret")
    rng_s = rng.rename("rng")
    taker_s = (taker / qv).rename("taker")
    illiq_s = (ret.abs() / qv * 1e9).rename("illiq")
    logv_s = logv.rename("logv")

    # ── 动量 / 反转：close_t / close_{t-w} - 1
    for w in cfg["mom"]:
        out[f"mom_{w}{u}"] = close / g["close"].shift(w) - 1.0
    out[f"rev_1{u}"] = -ret

    # ── 波动率 / 偏度 / 单日最大涨幅
    for w in cfg["vol"]:
        out[f"vol_{w}{u}"] = _rolling(sym, ret_s, w, "std")
    ws = cfg["other_short"]
    out[f"skew_{ws}{u}"] = _rolling(sym, ret_s, ws, "skew")
    out[f"maxret_{ws}{u}"] = _rolling(sym, ret_s, ws, "max")

    # ── 振幅 / 主动买盘占比 / 成交额放大倍数 / 非流动性
    out[f"range_{ws}{u}"] = _rolling(sym, rng_s, ws, "mean")
    out[f"takerratio_{ws}{u}"] = _rolling(sym, taker_s, ws, "mean")
    wm = cfg["other_mid"]
    mu = _rolling(sym, logv_s, wm, "mean")
    sd = _rolling(sym, logv_s, wm, "std")
    out[f"volume_z_{wm}{u}"] = (logv_s - mu) / sd.replace(0.0, np.nan)
    out[f"illiq_{ws}{u}"] = _rolling(sym, illiq_s, ws, "mean")

    for c in out.columns:
        if c not in ("date", "symbol"):
            out[c] = out[c].replace([np.inf, -np.inf], np.nan).astype("float32")

    if keep:
        missing = [c for c in keep if c not in out.columns]
        if missing:
            raise KeyError(f"未知因子 {missing}；可用：{[c for c in out.columns if c not in ('date', 'symbol')]}")
        out = out[["date", "symbol"] + keep]
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="从行情数据湖生成因子面板")
    ap.add_argument("--market", default="spot", choices=list(lake_io.MARKETS))
    ap.add_argument("--tf", default="1d", choices=["1d", "1h"])
    ap.add_argument("--factors", default="all", help="all 或逗号分隔的因子名")
    ap.add_argument("--limit", type=int, default=0, help="只算前 N 个符号（冒烟用）")
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    args = ap.parse_args()

    if not os.path.isdir(os.path.join(LAKE, args.market, args.tf)):
        print(f"数据湖里没有 {args.market}/{args.tf}，先跑 _build/02_partition.py")
        return

    print(f"[{args.market}/{args.tf}] 读取行情数据湖 ...")
    df = lake_io.load(args.market, args.tf, columns=COLS,
                      start=args.start, end=args.end)
    if args.limit:
        keep_syms = sorted(df["symbol"].unique())[: args.limit]
        df = df[df["symbol"].isin(keep_syms)]
    print(f"  {len(df):,} 行 / {df['symbol'].nunique()} 个符号")

    keep = None
    if args.factors != "all":
        keep = [s.strip() for s in args.factors.split(",") if s.strip()]

    print(f"  计算因子（窗口按 {args.tf}）...")
    fac = compute(df, args.tf, keep)
    names = [c for c in fac.columns if c not in ("date", "symbol")]
    print(f"  {len(names)} 个因子：" + ", ".join(names))

    # 只把**真的传了**的筛选条件写进 meta；None 会覆盖 write_panel 算出的实际日期范围
    meta = {"source": f"行情数据/{args.market}/{args.tf}",
            "limit_symbols": args.limit,
            "windows": WINDOWS[args.tf]}
    if args.start is not None:
        meta["filter_start"] = args.start
    if args.end is not None:
        meta["filter_end"] = args.end
    p = factor_io.write_panel(fac, args.market, args.tf, meta=meta)
    print(f"  已写入 {os.path.relpath(p, ROOT)}  "
          f"({os.path.getsize(p) / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
