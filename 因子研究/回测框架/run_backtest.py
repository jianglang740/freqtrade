#!/usr/bin/env python
"""
单因子回测 CLI。所有口径都能从命令行覆盖，方便批量试参数。

    # 最简：现货日线，30 日动量，每日调仓，持 1/5/10/20 天
    python 因子研究/回测框架/run_backtest.py --factor mom_30d

    # 永续合约 + 资金费 + 周频调仓
    python 因子研究/回测框架/run_backtest.py --market um --factor mom_30d --rebalance weekly

    # 小时线
    python 因子研究/回测框架/run_backtest.py --tf 1h --factor mom_168h --rebalance daily

约定：
  - 因子面板由 因子研究/因子数据/build_factors.py 生成；
  - 行情数据来自 因子研究/行情数据/ 数据湖；
  - 结果默认写到 因子研究/回测框架/out/<因子_市场_周期_调仓>/。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))            # freqtrade/
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "行情数据"))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "因子数据"))

from engine import Config, run          # noqa: E402
import plots as P                       # noqa: E402


def _parse_holds(s: str) -> tuple[int, ...]:
    return tuple(int(x) for x in s.replace("，", ",").split(",") if x.strip())


def build_cfg(args) -> Config:
    return Config(
        market=args.market, tf=args.tf, factor=args.factor,
        factor_panel=args.factor_panel,
        start=args.start, end=args.end,
        rebalance=args.rebalance, every=args.every,
        hold_windows=_parse_holds(args.hold),
        quantiles=args.quantiles, direction=args.direction, mode=args.mode,
        weighting=args.weighting, gross_leverage=args.gross_leverage,
        top_n=args.top_n, adv_min=args.adv_min, adv_lookback=args.adv_lookback,
        min_history_days=args.min_history,
        exec_price=args.exec_price, fee_bps=args.fee_bps,
        slippage_bps=args.slippage_bps, delist_shock=args.delist_shock,
        use_funding=not args.no_funding,
        mad_n=args.mad_n, neutralize=args.neutralize,
        beta_window=args.beta_window, vol_window=args.vol_window,
        min_obs=args.min_obs,
    )


def _fmt_summary(df: pd.DataFrame) -> str:
    if df.empty:
        return "（无结果）"
    fmt = df.copy()
    pct = ["IC", "原始IC", "中性化IC", "分组远期价差", "累计收益", "年化收益", "年化波动",
           "最大回撤", "年化成本", "年化资金费", "原始年化", "中性化年化"]
    for c in pct:
        if c in fmt.columns:
            fmt[c] = fmt[c].map(lambda v: f"{v:.2%}" if pd.notna(v) else "—")
    for c in ["ICIR", "IC_t_NW", "FM_t_NW", "夏普", "索提诺", "卡玛",
              "年化换手", "平均毛敞口", "FM斜率(BP)"]:
        if c in fmt.columns:
            fmt[c] = fmt[c].map(lambda v: f"{v:.2f}" if pd.notna(v) else "—")
    cols = [c for c in ["持有窗口", "IC", "ICIR", "IC_t_NW", "FM斜率(BP)", "FM_t_NW",
                        "分组远期价差", "年化收益", "年化波动", "夏普", "最大回撤",
                        "年化成本", "年化资金费", "平均毛敞口", "退市平仓次数"] if c in fmt.columns]
    return fmt[cols].to_string(index=False)


def write_tables(tables: dict, outdir: str) -> str | None:
    """优先写一个 Excel；没有 openpyxl/xlsxwriter 时逐表写 CSV。"""
    os.makedirs(outdir, exist_ok=True)
    xlsx = os.path.join(outdir, "因子回测统计结果.xlsx")
    for engine in ("openpyxl", "xlsxwriter"):
        try:
            __import__(engine)
        except ImportError:
            continue
        try:
            with pd.ExcelWriter(xlsx, engine=engine) as w:
                for name, df in tables.items():
                    if isinstance(df, pd.DataFrame) and not df.empty:
                        df.to_excel(w, sheet_name=name[:31], index=False)
            return xlsx
        except Exception as e:                       # noqa: BLE001
            print(f"⚠️  写 Excel 失败（{e}），改存 CSV")
            break
    for name, df in tables.items():
        if isinstance(df, pd.DataFrame) and not df.empty:
            df.to_csv(os.path.join(outdir, f"{name}.csv"), index=False, encoding="utf-8-sig")
    return None


def main() -> None:
    ap = argparse.ArgumentParser(description="加密货币单因子回测（随机池 / 多空 / 扣成本）")
    ap.add_argument("--market", default="spot", choices=["spot", "um"])
    ap.add_argument("--tf", default="1d", choices=["1d", "1h"])
    ap.add_argument("--factor", default="mom_30d", help="因子面板里的列名")
    ap.add_argument("--factor-panel", default=None,
                    help="项目自建因子面板的 parquet 路径（date/symbol/<factor>）；不填则读公共因子库")
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)

    ap.add_argument("--rebalance", default="daily", choices=["daily", "weekly", "monthly", "every"])
    ap.add_argument("--every", type=int, default=1, help="rebalance=every 时每 N 根 bar")
    ap.add_argument("--hold", default="1,5,10,20", help="持有几个调仓窗口，逗号分隔")

    ap.add_argument("--quantiles", type=int, default=5)
    ap.add_argument("--top-n", type=int, default=100, help="每日可选池上限（按成交额）")
    ap.add_argument("--adv-min", type=float, default=1e6, help="日均成交额下限（美元）")
    ap.add_argument("--adv-lookback", type=int, default=14, help="成交额均线天数")
    ap.add_argument("--min-history", type=int, default=30, help="上市最少天数")

    ap.add_argument("--exec-price", default="open", choices=["open", "close"])
    ap.add_argument("--mode", default="long_short", choices=["long_short", "long_only", "short_only"])
    ap.add_argument("--direction", default="long_top", choices=["long_top", "short_top"],
                    help="long_top=做多因子高值；short_top=做空因子高值（反转）")
    ap.add_argument("--weighting", default="equal", choices=["equal", "adv", "vol_inv"])
    ap.add_argument("--gross-leverage", type=float, default=1.0,
                    help="组合毛敞口；多空模式下每腿 = 该值/2")

    ap.add_argument("--fee-bps", type=float, default=None, help="单边手续费，默认现货 10 / 永续 5")
    ap.add_argument("--slippage-bps", type=float, default=2.0)
    ap.add_argument("--delist-shock", type=float, default=0.0,
                    help="持仓撞退市时额外扣的亏损（0=按最后价平仓）")
    ap.add_argument("--no-funding", action="store_true", help="永续不扣资金费")

    ap.add_argument("--neutralize", default="logadv_beta",
                    choices=["logadv", "logadv_beta", "logadv_beta_vol", "None"])
    ap.add_argument("--mad-n", type=float, default=5.2)
    ap.add_argument("--beta-window", type=int, default=30)
    ap.add_argument("--vol-window", type=int, default=30)
    ap.add_argument("--min-obs", type=int, default=20)

    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--tag", default=None, help="结果子目录名后缀")
    ap.add_argument("--no-plots", action="store_true")
    args = ap.parse_args()

    cfg = build_cfg(args)
    name = args.tag or f"{cfg.factor}_{cfg.market}_{cfg.tf}_{cfg.rebalance}"
    outdir = args.out_dir or os.path.join(HERE, "out", name)
    os.makedirs(outdir, exist_ok=True)

    print("=" * 88)
    print(f"单因子回测 | {cfg.factor} | {cfg.market}/{cfg.tf} | {cfg.rebalance} 调仓 | "
          f"持有 {cfg.hold_windows} | {cfg.mode} | 中性化={cfg.neutralize}")
    print(f"成本：手续费 {cfg.fee_bps:.1f}bp + 滑点 {cfg.slippage_bps:.1f}bp（单边）/ 资金费="
          f"{'扣' if cfg.use_funding else '不扣'}")
    print("=" * 88)

    res = run(cfg)
    tables = res["tables"]

    print("\n=== 汇总统计 ===")
    print(_fmt_summary(tables["汇总统计"]))

    bm = res.get("benchmark") or {}
    if bm:
        print(f"\n=== 同期对照 === BTC 买入持有：累计 {bm['累计收益']:.1%} / "
              f"年化 {bm['年化收益']:.1%} / 最大回撤 {bm['最大回撤']:.1%} / 夏普 {bm['夏普']:.2f}")

    xlsx = write_tables(tables, outdir)
    # 逐窗口明细单独存，便于二次分析
    var = res["variants"][res["effective"]]
    for h, sim in var["sims"].items():
        if sim is not None and not sim.empty:
            sim.to_csv(os.path.join(outdir, f"组合明细_持有{h}.csv"), encoding="utf-8-sig")

    with open(os.path.join(outdir, "config.json"), "w", encoding="utf-8") as fh:
        json.dump(cfg.as_dict(), fh, ensure_ascii=False, indent=1, default=str)

    print(f"\n统计表已写：{xlsx or outdir + '/*.csv'}")
    if not args.no_plots:
        paths = P.make_all(res, os.path.join(outdir, "figs"))
        print(f"图表 {len(paths)} 张已写：{os.path.join(outdir, 'figs')}/")
    print(f"全部产物：{os.path.relpath(outdir, ROOT)}/")


if __name__ == "__main__":
    main()
