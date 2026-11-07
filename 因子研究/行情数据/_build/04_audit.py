#!/usr/bin/env python
"""
第四步：生成 `行情数据/_audit.json` —— 数据湖的体检表。

和 03_verify.py 的分工：
  - 03 是**判定**：数据对不对，通不过就退出非零；
  - 04 是**描述**：数据长什么样，给做因子的人看（逐年行数、截面宽度、缺口分布、下架比例）。

用法：
    python 因子研究/行情数据/_build/04_audit.py
"""

from __future__ import annotations

import glob
import json
import os
import sys

import pandas as pd
import pyarrow.compute as pc
import pyarrow.parquet as pq

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import AUDIT, LAKE, MANIFEST  # noqa: E402

TOP_N_FOR_AUDIT = 200_000   # 1h 分区最多取这么多行来估截面宽度，够用且快


def partitions(market: str, tf: str) -> list[str]:
    return sorted(glob.glob(os.path.join(LAKE, market, tf, "**", "*.parquet"), recursive=True))


def audit_dataset(market: str, tf: str) -> dict:
    paths = partitions(market, tf)
    if not paths:
        return {}

    by_year: dict[int, dict] = {}
    for p in paths:
        pf = pq.ParquetFile(p)
        rows = pf.metadata.num_rows
        y = int(os.path.basename(os.path.dirname(p))[5:]) if tf == "1h" else \
            int(os.path.basename(p)[5:9])
        t = pq.read_table(p, columns=["symbol"])
        syms = len(pc.unique(t["symbol"]))
        rec = by_year.setdefault(y, {"rows": 0, "symbols": 0})
        rec["rows"] += rows
        rec["symbols"] = max(rec["symbols"], syms)

    # 日线额外统计：交易日数、截面宽度、缺口
    daily_span = ""
    if tf == "1d":
        t = pq.read_table(paths, columns=["symbol", "date"]).to_pandas()
        by_year_dates = t.groupby(t["date"].dt.year)["date"].nunique().to_dict()
        for y, n in by_year_dates.items():
            if y in by_year:
                by_year[y]["trade_days"] = int(n)
        g = t.groupby("symbol")["date"]
        per = pd.DataFrame({"first": g.min(), "last": g.max(), "rows": g.size()})
        per["span"] = (per["last"] - per["first"]).dt.days + 1
        per["gap_days"] = per["span"] - per["rows"]
        q = per["gap_days"].quantile([0.5, 0.9, 0.99]).to_dict()
        daily_span = {
            "span_days_median": float(per["span"].median()),
            "span_days_max": int(per["span"].max()),
            "gap_days_median": float(q.get(0.5, 0)),
            "gap_days_p90": float(q.get(0.9, 0)),
            "gap_days_p99": float(q.get(0.99, 0)),
            "symbols_with_gap_gt_30d": int((per["gap_days"] > 30).sum()),
        }

    return {
        "partitions": len(paths),
        "rows": int(sum(v["rows"] for v in by_year.values())),
        "by_year": {str(k): v for k, v in sorted(by_year.items())},
        **({"daily": daily_span} if daily_span else {}),
    }


def main() -> None:
    if not os.path.exists(MANIFEST):
        print(f"❌ 找不到 {MANIFEST}，先跑 02_partition.py")
        sys.exit(1)
    man = json.load(open(MANIFEST, encoding="utf-8"))

    out = {
        "audited_at": pd.Timestamp.now(tz="UTC").isoformat(),
        "source_manifest_built_at": man.get("built_at"),
        "datasets": {},
        "basic": None,
    }
    for key in man.get("datasets", {}):
        market, tf = key.split("/")
        print(f"[{key}] 统计中 …", end="", flush=True)
        out["datasets"][key] = audit_dataset(market, tf)
        print(f" {out['datasets'][key].get('rows', 0):,} 行")

    dim_p = os.path.join(LAKE, "basic", "dim_instrument.parquet")
    if os.path.exists(dim_p):
        dim = pq.read_table(dim_p).to_pandas()
        out["basic"] = {
            "rows": int(len(dim)),
            "by_market": {k: int(v) for k, v in dim["market"].value_counts().items()},
            "by_status": {f"{m}/{s}": int(n) for (m, s), n in
                          dim.groupby(["market", "status"]).size().items()},
            "flags": {
                "is_leveraged": int(dim["is_leveraged"].sum()),
                "is_stablecoin": int(dim["is_stablecoin"].sum()),
                "is_fiat": int(dim["is_fiat"].sum()),
                "is_tradfi": int(dim["is_tradfi"].sum()),
            },
            "delisted_share": round(float((dim["status"] == "delisted").mean()), 4),
        }

    with open(AUDIT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    print(f"写 {os.path.relpath(AUDIT, os.path.dirname(LAKE))}")


if __name__ == "__main__":
    main()
