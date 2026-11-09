#!/usr/bin/env python
"""
15 日区间突破：按 01_原始设想.md 写死的口径批量回测，并做因子相关性对照。

跑的东西（全部事先在 01 里声明，不构成参数搜索）：
  um 全历史      : rp15d 多空 / 多腿 / 空腿 / 中性化多空 / top20 敏感性
  um 原文区间    : 2025-04-01 → 2026-10-31，同上
  um 归因        : rp15d_excl（不含当日）
  um 对照        : relpos_14d / mom_7d / mom_30d，同配置
  spot 全历史    : rp15d 多腿（可执行性）
  spot 原文区间  : rp15d 多腿
外加周频截面秩相关：rp15d vs rp15d_excl / relpos_14d / mom_7d / mom_30d。

产物：
  out/study_runs.csv        每次回测一行
  out/study_corr.csv        因子两两截面秩相关
  out/runs/<key>.csv        每次回测的完整汇总表（分年度等在报告里引用）

用法：
    python 策略池/15日区间突破/scripts/run_study.py
    python 策略池/15日区间突破/scripts/run_study.py --only um
"""

from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
REPO = os.path.dirname(os.path.dirname(PROJECT))
for _p in (os.path.join(REPO, "因子研究/行情数据"),
           os.path.join(REPO, "因子研究/因子数据"),
           os.path.join(REPO, "因子研究/回测框架")):
    sys.path.insert(0, _p)

import engine as E          # noqa: E402
import lake_io              # noqa: E402
import factor_io            # noqa: E402

OUT = os.path.join(PROJECT, "out")
RUNS = os.path.join(OUT, "runs")
PANEL = os.path.join(OUT, "factor_{}_1d.parquet")
RELPOS_PANEL = os.path.join(REPO, "因子研究/区间相对位置因子/out/factor_{}_1d.parquet")
SOURCE_WINDOW = ("2025-04-01", "2026-11-01")      # 原文 2025-04-01 → 2026-10-31

BASE = dict(
    tf="1d", rebalance="weekly", hold_windows=(1, 4), quantiles=5,
    direction="long_top", mode="long_short", weighting="equal",
    gross_leverage=1.0, top_n=50, adv_min=1e6, min_history_days=30,
    exec_price="open", fee_bps=3.0, slippage_bps=0.0,
    delist_shock=0.0, use_funding=True, neutralize=None, min_obs=20,
)

CASES = [
    # key, market, factor, panel, start, end, overrides
    ("um_full_ls",       "um", "rp15d", "self", None, None, {}),
    ("um_full_long",     "um", "rp15d", "self", None, None, {"mode": "long_only"}),
    ("um_full_short",    "um", "rp15d", "self", None, None, {"mode": "short_only"}),
    ("um_full_ls_neut",  "um", "rp15d", "self", None, None, {"neutralize": "logadv_beta"}),
    ("um_full_ls_top20", "um", "rp15d", "self", None, None, {"top_n": 20}),
    ("um_src_ls",        "um", "rp15d", "self", *SOURCE_WINDOW, {}),
    ("um_src_long",      "um", "rp15d", "self", *SOURCE_WINDOW, {"mode": "long_only"}),
    ("um_src_ls_top20",  "um", "rp15d", "self", *SOURCE_WINDOW, {"top_n": 20}),
    ("um_full_ls_excl",  "um", "rp15d_excl", "self", None, None, {}),
    # 注：引擎的 short_only 是「空 top」，不是多空组合里的空腿。
    # 空腿（空 bottom）= 下面这组「多 bottom」的相反数。
    ("um_full_bot_long", "um", "rp15d", "self", None, None,
     {"direction": "short_top", "mode": "long_only"}),
    ("um_src_bot_long",  "um", "rp15d", "self", *SOURCE_WINDOW,
     {"direction": "short_top", "mode": "long_only"}),
    ("um_full_relpos14", "um", "relpos_14d", "relpos", None, None, {}),
    ("um_src_relpos14",  "um", "relpos_14d", "relpos", *SOURCE_WINDOW, {}),
    ("um_full_mom7",     "um", "mom_7d", "public", None, None, {}),
    ("um_src_mom7",      "um", "mom_7d", "public", *SOURCE_WINDOW, {}),
    ("um_full_mom30",    "um", "mom_30d", "public", None, None, {}),
    ("um_src_mom30",     "um", "mom_30d", "public", *SOURCE_WINDOW, {}),
    ("spot_full_long",   "spot", "rp15d", "self", None, None,
     {"mode": "long_only", "fee_bps": None, "slippage_bps": 2.0}),
    ("spot_src_long",    "spot", "rp15d", "self", *SOURCE_WINDOW,
     {"mode": "long_only", "fee_bps": None, "slippage_bps": 2.0}),
]


def _rows(key: str, cfg: E.Config, res: dict) -> list[dict]:
    summ = res["tables"]["汇总统计"]
    cost = res["tables"]["换手与成本"].set_index("持有窗口")
    rows = []
    for _, r in summ.iterrows():
        h = int(r["持有窗口"])
        rows.append({
            "key": key, "市场": cfg.market, "因子": cfg.factor,
            "方向": cfg.direction, "模式": cfg.mode, "中性化": str(cfg.neutralize),
            "top_n": cfg.top_n, "开始": str(cfg.start), "结束": str(cfg.end),
            "持有窗口": h,
            "IC": r.get("IC"), "ICIR": r.get("ICIR"), "IC_t_NW": r.get("IC_t_NW"),
            "原始IC": r.get("原始IC"), "中性化IC": r.get("中性化IC"),
            "FM斜率(BP)": r.get("FM斜率(BP)"), "FM_t_NW": r.get("FM_t_NW"),
            "分组远期价差": r.get("分组远期价差"),
            "年化收益": r.get("年化收益"), "年化波动": r.get("年化波动"),
            "夏普": r.get("夏普"), "最大回撤": r.get("最大回撤"),
            "年化成本": r.get("年化成本"), "资金费年化": r.get("年化资金费"),
            "年化换手": cost["年化换手"].get(h) if h in cost.index else None,
            "平均多腿": cost["平均多腿"].get(h) if h in cost.index else None,
            "平均空腿": cost["平均空腿"].get(h) if h in cost.index else None,
            "平均窗口数": r.get("窗口数"), "破产": r.get("破产"),
        })
    return rows


def _panel_path(panel: str, market: str) -> str | None:
    if panel == "self":
        return PANEL.format(market)
    if panel == "relpos":
        return RELPOS_PANEL.format(market)
    return None      # public 因子库


def _weekly_dates(market: str) -> pd.DatetimeIndex:
    df = lake_io.load(market, "1d", columns=["date"])
    s = pd.Series(pd.DatetimeIndex(df["date"].unique())).sort_values()
    iso = s.dt.isocalendar()
    key = iso["year"].astype(str) + "-" + iso["week"].astype(str)
    # 保留 tz（.values 会剥掉 UTC，和因子面板 reindex 时全部对不上）
    return pd.DatetimeIndex(s.groupby(key).max().array)


def _factor_wide(market: str, factor: str, panel: str) -> pd.DataFrame:
    path = _panel_path(panel, market)
    if path:
        long = factor_io.load_external(path, factor)
    else:
        long = factor_io.load_panel(market, "1d", factors=[factor])
    return long.pivot(index="date", columns="symbol", values=factor)


def correlations(market: str = "um") -> pd.DataFrame:
    dates = _weekly_dates(market)
    series = {
        "rp15d": _factor_wide(market, "rp15d", "self"),
        "rp15d_excl": _factor_wide(market, "rp15d_excl", "self"),
        "relpos_14d": _factor_wide(market, "relpos_14d", "relpos"),
        "mom_7d": _factor_wide(market, "mom_7d", "public"),
        "mom_30d": _factor_wide(market, "mom_30d", "public"),
    }
    names = list(series)
    rows = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            common = series[a].columns.intersection(series[b].columns)
            va = series[a][common].reindex(dates)
            vb = series[b][common].reindex(dates)
            both = va.notna() & vb.notna()
            rs = []
            for d in dates:
                m = both.loc[d].fillna(False).astype(bool)
                if m.sum() >= 20:
                    rs.append(va.loc[d][m].rank().corr(vb.loc[d][m].rank()))
            rows.append({"市场": market, "A": a, "B": b,
                         "秩相关均值": float(np.mean(rs)) if rs else np.nan,
                         "最小": float(np.min(rs)) if rs else np.nan,
                         "最大": float(np.max(rs)) if rs else np.nan,
                         "期数": len(rs)})
    return pd.DataFrame(rows)


def main() -> None:
    ap = argparse.ArgumentParser(description="15 日区间突破批量回测")
    ap.add_argument("--only", default=None, help="只跑某个 key 前缀，如 um / spot")
    ap.add_argument("--corr-only", action="store_true", help="只算因子相关性")
    args = ap.parse_args()

    os.makedirs(RUNS, exist_ok=True)
    rows: list[dict] = []
    for key, market, factor, panel, start, end, over in CASES:
        if args.corr_only:
            break
        prefixes = [p.strip() for p in args.only.split(",")] if args.only else None
        if prefixes and not any(key.startswith(p) for p in prefixes):
            continue
        cfg = E.Config(market=market, factor=factor,
                       factor_panel=_panel_path(panel, market),
                       start=start, end=end, **{**BASE, **over})
        print(f"\n=== {key} ===")
        res = E.run(cfg)
        rows += _rows(key, cfg, res)
        res["tables"]["汇总统计"].to_csv(os.path.join(RUNS, f"{key}.csv"), index=False)

    if rows:
        df = pd.DataFrame(rows)
        path = os.path.join(OUT, "study_runs.csv")
        if args.only and os.path.exists(path):
            # 只补跑了几组时不要覆盖整张表
            old = pd.read_csv(path)
            df = (pd.concat([old, df], ignore_index=True)
                    .drop_duplicates(subset=["key", "持有窗口"], keep="last"))
        df.to_csv(path, index=False)
        cols = ["key", "持有窗口", "IC", "IC_t_NW", "年化收益", "夏普",
                "最大回撤", "年化成本", "资金费年化"]
        print("\n" + df[cols].to_string(index=False, float_format=lambda x: f"{x:.4f}"))
        print(f"\n→ {path}")

    corr = correlations("um")
    cpath = os.path.join(OUT, "study_corr.csv")
    corr.to_csv(cpath, index=False)
    print("\n" + corr.to_string(index=False, float_format=lambda x: f"{x:.3f}"))
    print(f"→ {cpath}")


if __name__ == "__main__":
    main()
