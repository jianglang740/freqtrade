"""freqtrade 策略 ↔ 研究管线 逐笔交叉验证 —— **仅 BTC 4h 单币**。

目的（计划 Phase E 的验证步骤 4）：把结构性出场写进 freqtrade 策略后，
确认两边给出的是同一件事，而不是各跑各的。

配对口径
  freqtrade 的成交根 = 研究 entry_idx（01_build_events.emit 的 idx+1）。
  研究同一成交根上可能有多个事件（如同根既突破又回调），而策略按
  05 §4–5 的优先级只取一个 → 研究侧先去重，再做 1:1 配对。
  net R 换算：freqtrade profit_ratio 已扣手续费与资金费，除以该笔止损
  距离即得净 R（与研究的 net_R 同口径）。

已知的**引擎限制**（本次量化的主要残差，非 bug）
  backtesting.py 的 `_check_trade_exit` 把 `row[OPEN_IDX]` 作为 current_rate
  传给 `should_exit`，而 `custom_exit` 只能拿到这个开盘价、拿不到该根的
  high/low。故"盘中触及目标"在 freqtrade 里要到**下一根开盘**才可能被识别，
  且只在该开盘价已越过目标时才成立 → 会漏掉一部分盘中达标、随后回吐甚至
  反手打止损的赢单。研究管线（06）按 low/high 逐根扫描，是精确实现。
  止损不受影响：走 freqtrade 自身机制，按盘中 low/high 判定。

运行：python pa_research/scripts/07_btc_freqtrade_crosscheck.py
"""

from __future__ import annotations

import glob
import json
import os
import sys
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "pa_research"))

from modules.bracket import load_arrays  # noqa: E402

PAIR = "BTC_USDT_USDT"
TF = "4h"


def load_ft_trades() -> pd.DataFrame:
    pats = str(REPO_ROOT / "user_data/backtest_results/backtest-result-*.zip")
    z = sorted(glob.glob(pats), key=os.path.getmtime)[-1]
    with zipfile.ZipFile(z) as zf:
        nm = [n for n in zf.namelist() if n.endswith(".json")][0]
        data = json.load(zf.open(nm))
    ft = pd.DataFrame(data["strategy"][list(data["strategy"])[0]]["trades"])
    ft["open_dt"] = pd.to_datetime(ft["open_date"], utc=True)
    ft["side"] = np.where(ft["is_short"], "short", "long")
    print(f"freqtrade 结果：{Path(z).name}  {len(ft)} 笔")
    return ft


def load_research() -> pd.DataFrame:
    res = pd.read_parquet(REPO_ROOT / f"pa_research/reports/exit_v1_{TF}_{PAIR}.parquet")
    arr = load_arrays(PAIR, TF)           # 注意：t 的单位是毫秒
    res["open_dt"] = pd.to_datetime(arr["t"][res["entry_idx"].to_numpy()], unit="ms", utc=True)
    res["open_px"] = arr["o"][res["entry_idx"].to_numpy()]
    res["side"] = np.where(res["direction"] == 1, "long", "short")
    return res


def dedup_precedence(res: pd.DataFrame) -> pd.DataFrame:
    """策略里回调覆盖突破 → 研究侧同一成交根也按此取唯一行。"""
    res = res.assign(_pb=res["event_type"].str[0].isin(["h", "l"]))
    return (res.sort_values(["open_dt", "side", "_pb"])
            .drop_duplicates(["open_dt", "side"], keep="last").drop(columns="_pb"))


def main() -> None:
    ft = load_ft_trades()
    res_all = load_research()
    res = dedup_precedence(res_all)
    print(f"研究事件 {len(res_all)} → 去重 {len(res)}（回调解消同根突破）")

    m = ft.merge(
        res[["open_dt", "side", "net_R", "gross_R", "stop_dist_rel", "exit_reason",
             "event_type", "open_px"]],
        on=["open_dt", "side"], how="inner", suffixes=("_ft", "_res"),
    )
    print(f"配对 {len(m)} / {len(ft)} 笔（ft 未配对 {len(ft) - len(m)}）")
    print("=" * 90)

    # ── 0. 止损口径是否一致（最关键的一致性证据）──
    # ft 的 |stop_loss_ratio| 就是该笔止损距离/入场价，应与研究 stop_dist_rel 相等。
    m["sd_ft"] = m["stop_loss_ratio"].abs()
    sd_gap = (m["sd_ft"] - m["stop_dist_rel"]).abs() / m["stop_dist_rel"]
    exact = np.isclose(m["sd_ft"], m["stop_dist_rel"], rtol=1e-6)
    print(f"\n── 0. 止损距离口径 ──")
    print(f"  |stop_loss_ratio| vs 研究 stop_dist_rel：完全相等 {exact.sum()}/{len(m)}"
          f"  （不等的是初始止损尚未被 custom_stoploss 覆盖的少数根，如 max_open_trades 收尾的 force_exit）")
    print(f"  入场价一致：中位相对差 "
          f"{((m.open_rate - m.open_px).abs() / m.open_px).median():.2e}")

    # ── 1. 总体 ──
    m["net_R_ft"] = m["profit_ratio"] / m["stop_dist_rel"]
    d = m["net_R_ft"] - m["net_R"]
    se = d.std(ddof=1) / np.sqrt(len(d))
    print(f"\n── 1. 逐笔配对（净R）──")
    print(f"  freqtrade 均值 {m.net_R_ft.mean():+.4f}   研究均值 {m.net_R.mean():+.4f}")
    print(f"  差 v(ft−研究) 均值 {d.mean():+.4f}  95%CI "
          f"[{d.mean() - 1.96 * se:+.4f}, {d.mean() + 1.96 * se:+.4f}]  t={d.mean() / se:+.2f}")
    print(f"  符号一致 {np.mean(np.sign(m.net_R_ft) == np.sign(m.net_R)):.1%}"
          f"  相关 {m.net_R_ft.corr(m.net_R):.3f}"
          f"  完全一致 {np.mean(np.isclose(m.net_R_ft, m.net_R, atol=1e-6)):.1%}")

    # ── 2. 出场原因 ──
    print(f"\n── 2. 出场原因交叉表（行=freqtrade，列=研究）──")
    ct = pd.crosstab(m.exit_reason_ft, m.exit_reason_res)
    print(ct.to_string())
    same = np.trace(ct.reindex(index=ct.index, columns=ct.columns).fillna(0).to_numpy())
    # 归一名：ft 的 target_structural ↔ 研究的 target；stop_loss ↔ stop
    alias = {"target_structural": "target", "stop_loss": "stop", "force_exit": "horizon"}
    rs = m.exit_reason_ft.map(lambda x: alias.get(x, x))
    print(f"  原因一致率（含别名归一）: {np.mean(rs == m.exit_reason_res):.1%}")

    # ── 3. 选样效应：被 max_open_trades=1 跳过的信号在研究中表现如何 ──
    skipped = res.merge(m[["open_dt", "side"]], on=["open_dt", "side"], how="left", indicator=True)
    skipped = skipped[skipped["_merge"] == "left_only"]
    print(f"\n── 3. 选样效应（max_open_trades=1 持仓期间跳过的 {len(skipped)} 个信号）──")
    print(f"  被跳过信号的研究净R均值 {skipped.net_R.mean():+.4f}"
          f"（配对上的 {m.net_R.mean():+.4f}）→ 方向"
          f"{'不同' if (skipped.net_R.mean() > 0) != (m.net_R.mean() > 0) else '相同'}")

    # ── 4. 引擎限制的量化：研究全场内达标、ft 未按目标出场的笔数 ──
    missed = m[(m.exit_reason_res == "target") & (m.exit_reason_ft != "target_structural")]
    print(f"\n── 4. 引擎限制：研究盘中达标、freqtrade 未按目标出场 {len(missed)} 笔 ──")
    if len(missed):
        print(f"  这批的研究净R均值 {missed.net_R.mean():+.4f}"
              f"  freqtrade 净R均值 {missed.net_R_ft.mean():+.4f}")
        print(f"  其 stop_dist_rel 中位 {missed.stop_dist_rel.median():.4f}"
              f"（越小越易被单根噪音扫掉 → 残差集中在这里）")
        w = missed.assign(gap=d.loc[missed.index].abs()).nlargest(3, "gap")
        print("  最大 3 例：")
        for _, r in w.iterrows():
            print(f"    {r.open_dt}  {r.event_type}  研究 {r.net_R:+.2f}R "
                  f"vs ft {r.net_R_ft:+.2f}R（止损距 {r.stop_dist_rel:.4f}）")

    print("\n判据：止损口径若逐笔一致、原因分布与净R高度相关、配对差不显著，"
          "\n      则两边实现的是同一策略；残差主要来自引擎的盘中目标粒度与选样。")


if __name__ == "__main__":
    main()
