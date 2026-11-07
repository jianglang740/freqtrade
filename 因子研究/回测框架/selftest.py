#!/usr/bin/env python
"""
回测框架自检：把**每一个 CLI 开关**都跑一遍，并断言结果合理。

和 `out/` 里那些冒烟目录的区别：
  - 冒烟只证明「这一条路能跑」，自检证明「每个分支都跑过，而且数字说得通」；
  - 不留大文件（不写表、不画图），只打印 PASS/FAIL 和关键指标；
  - 退出码非 0 表示有分支坏了，可以直接挂到 CI/脚本里。

用的是短窗口（2024-01 → 2024-10）+ top60，单次 10~30 秒。
只覆盖日线；小时线需要先跑 build_factors.py --tf 1h 建面板。

    python 因子研究/回测框架/selftest.py
    python 因子研究/回测框架/selftest.py --verbose     # 打印每个用例的指标
"""

from __future__ import annotations

import argparse
import os
import sys
import traceback

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
FACTOR_ROOT = os.path.dirname(HERE)
for _p in (os.path.join(FACTOR_ROOT, "行情数据"),
           os.path.join(FACTOR_ROOT, "因子数据")):
    sys.path.insert(0, _p)

import engine as E  # noqa: E402

BASE = dict(tf="1d", factor="mom_30d", start="2024-01-01", end="2024-10-01",
            hold_windows=(1, 3), top_n=60, min_obs=15)


# ══════════════════════════════════════════════ 断言工具
def _summary(res: dict) -> pd.DataFrame:
    return res["tables"]["汇总统计"]


def _sim(res: dict, h: int | None = None) -> pd.DataFrame:
    var = res["variants"][res["effective"]]
    h = h if h is not None else list(res["cfg"].hold_windows)[0]
    return var["sims"][h]


def check_basic(res: dict) -> list[str]:
    """所有分支都该满足的底线：有结果、指标有限、换手非负、净值不炸。"""
    bad = []
    s = _summary(res)
    if s.empty:
        return ["汇总统计为空"]
    for col in ("IC", "年化收益", "年化波动", "最大回撤", "年化成本"):
        if col not in s.columns:
            bad.append(f"缺列 {col}")
        elif s[col].isna().all():
            bad.append(f"{col} 全是 NaN")
    sim = _sim(res)
    if (sim["turnover"] < -1e-12).any():
        bad.append("换手出现负值")
    if (sim["raw_exposure"] > res["cfg"].gross_leverage + 1e-6).any():
        bad.append("毛敞口超过 gross_leverage")
    if not np.isfinite(sim["net"]).all():
        bad.append("净收益出现 NaN/inf")
    if (sim["days"] <= 0).any():
        bad.append("窗口天数出现非正值")
    return bad


# ══════════════════════════════════════════════ 用例
def case_baseline():
    cfg = E.Config(**BASE)
    res = E.run(cfg, verbose=False)
    bad = check_basic(res)
    sim = _sim(res)
    bad += [] if (sim["n_long"] > 0).any() and (sim["n_short"] > 0).any() else ["多空双腿为空"]
    return res, bad


def case_long_only():
    cfg = E.Config(**BASE, market="um", mode="long_only")
    res = E.run(cfg, verbose=False)
    bad = check_basic(res)
    sim = _sim(res)
    bad += [] if (sim["n_short"] == 0).all() else ["long_only 出现空头"]
    bad += [] if (sim["gross"] != 0).any() else ["long_only 毛收益全 0"]
    bad += [] if abs(sim["raw_exposure"].mean() - 1.0) < 0.2 else \
        [f"long_only 敞口应≈1，实际 {sim['raw_exposure'].mean():.2f}"]
    return res, bad


def case_short_only():
    cfg = E.Config(**BASE, market="um", mode="short_only")
    res = E.run(cfg, verbose=False)
    bad = check_basic(res)
    sim = _sim(res)
    bad += [] if (sim["n_long"] == 0).all() else ["short_only 出现多头"]
    return res, bad


def case_short_top():
    """direction=short_top 必须和 long_top 得到相反方向的多空组合。"""
    a = E.run(E.Config(**BASE, market="um"), verbose=False)
    b = E.run(E.Config(**BASE, market="um", direction="short_top"), verbose=False)
    bad = check_basic(b)
    wa = a["variants"]["neut"]["weights"].iloc[-1]
    wb = b["variants"]["neut"]["weights"].iloc[-1]
    corr = float(pd.Series(wa).corr(pd.Series(wb)))
    bad += [] if corr < -0.5 else [f"short_top 权重与 long_top 相关 {corr:+.2f}，应为负"]
    return b, bad


def case_weighting(kind: str):
    cfg = E.Config(**BASE, market="um", weighting=kind)
    res = E.run(cfg, verbose=False)
    bad = check_basic(res)
    if kind == "adv":
        # 成交额加权下，持仓的 ADV 分位应该整体高于等权
        eq = E.run(E.Config(**BASE, market="um", weighting="equal"), verbose=False)
        adv = res["style"]["logadv"]
        wa = res["variants"]["neut"]["weights"].abs()
        we = eq["variants"]["neut"]["weights"].abs()
        pa = float((wa * adv.reindex_like(wa)).sum(axis=1).sum() / wa.sum().sum())
        pe = float((we * adv.reindex_like(we)).sum(axis=1).sum() / we.sum().sum())
        bad += [] if pa > pe else [f"adv 加权的 logADV 均值 {pa:.2f} 未高于等权 {pe:.2f}"]
    return res, bad


def case_exec_close():
    cfg = E.Config(**BASE, market="um", exec_price="close")
    res = E.run(cfg, verbose=False)
    bad = check_basic(res)
    # 收盘价成交必须和开盘价成交给出不同结果，否则说明 exec_price 没生效
    o = E.run(E.Config(**BASE, market="um", exec_price="open"), verbose=False)
    d = abs(_summary(res)["年化收益"].iloc[0] - _summary(o)["年化收益"].iloc[0])
    bad += [] if d > 1e-6 else ["exec_price=close 与 open 结果完全相同，参数没生效"]
    return res, bad


def case_rebalance(kind: str):
    cfg = E.Config(**BASE, market="um", rebalance=kind, every=3)
    res = E.run(cfg, verbose=False)
    bad = check_basic(res)
    n = len(res["reb_dates"])
    # 2024-01-01 → 2024-10-01 共 273 根日线
    want = {"daily": 273, "weekly": 39, "monthly": 9, "every": 91}.get(kind)
    if want and not (want * 0.85 <= n <= want * 1.15):
        bad.append(f"{kind} 调仓 {n} 次，预期约 {want} 次")
    return res, bad


def case_quantiles(n: int):
    cfg = E.Config(**BASE, market="um", quantiles=n)
    res = E.run(cfg, verbose=False)
    bad = check_basic(res)
    fq = res["variants"][res["effective"]]["signal"][1].get("fwd_q")
    if not isinstance(fq, pd.Series) or len(fq) < max(1, n - 1):
        bad.append(f"quantiles={n} 只得到 {0 if not isinstance(fq, pd.Series) else len(fq)} 个分组")
    return res, bad


def case_delist_shock():
    """退市只在下架币上发生，短窗口大概率碰不到 —— 这个用例走全历史。"""
    cfg = E.Config(**{**BASE, "start": None, "end": None}, market="um",
                   rebalance="weekly", delist_shock=0.5)
    res = E.run(cfg, verbose=False)
    bad = check_basic(res)
    sim = _sim(res)
    if int(sim["dead_exits"].sum()) == 0:
        bad.append("全历史窗口也没有退市平仓，delist_shock 无法验证")
    return res, bad


def case_neutralize(kind: str | None):
    cfg = E.Config(**BASE, market="um", neutralize=kind)
    res = E.run(cfg, verbose=False)
    bad = check_basic(res)
    if kind and res["effective"] != "neut":
        bad.append(f"neutralize={kind} 却没走中性化变体")
    return res, bad


def case_no_funding():
    cfg = E.Config(**BASE, market="um", use_funding=False)
    res = E.run(cfg, verbose=False)
    bad = check_basic(res)
    bad += [] if (_sim(res)["funding"] == 0).all() else ["--no-funding 下资金费不为 0"]
    return res, bad


def case_funding_on():
    cfg = E.Config(**BASE, market="um", use_funding=True)
    res = E.run(cfg, verbose=False)
    bad = check_basic(res)
    f = _sim(res)["funding"]
    bad += [] if f.abs().sum() > 0 else ["use_funding=True 但资金费全 0（数据湖缺 um/funding？）"]
    return res, bad


def case_costs():
    # 必须同时关掉资金费，否则 net = gross - cost + funding，零成本时仍不等于 gross
    zero = E.run(E.Config(**BASE, market="um", fee_bps=0, slippage_bps=0,
                          use_funding=False), verbose=False)
    full = E.run(E.Config(**BASE, market="um", fee_bps=50, slippage_bps=50,
                          use_funding=False), verbose=False)
    bad = check_basic(full)
    zg = _sim(zero)["gross"].sum()
    zn = _sim(zero)["net"].sum()
    bad += [] if abs(zg - zn) < 1e-12 else ["零成本时 net 应等于 gross"]
    if _sim(full)["cost"].sum() <= _sim(zero)["cost"].sum():
        bad.append("高费率没有提高成本")
    return full, bad


def case_external_panel():
    """--factor-panel：外部因子面板必须能被读到并跑通。"""
    proj = os.path.join(FACTOR_ROOT, "风险调整动量因子", "out", "factor_um_1d.parquet")
    if not os.path.exists(proj):
        return None, ["跳过：没有 风险调整动量因子/out/factor_um_1d.parquet"]
    cfg = E.Config(**{**BASE, "factor": "sharpe_mom_30d"}, market="um", factor_panel=proj)
    res = E.run(cfg, verbose=False)
    return res, check_basic(res)


def case_hourly():
    """小时线：bar 网格、调仓日历、ADV 仍按日线，整条链路都要能跑。"""
    cfg = E.Config(**{**BASE, "tf": "1h", "factor": "mom_168h",
                      "start": "2024-08-01", "end": "2024-11-01",
                      "hold_windows": (1, 3)},
                   market="um", rebalance="daily")
    res = E.run(cfg, verbose=False)
    bad = check_basic(res)
    n = len(res["reb_dates"])
    if not (80 <= n <= 100):                       # 2024-08-01 → 2024-11-01 约 92 天
        bad.append(f"1h 日频调仓 {n} 次，预期约 92 次")
    sim = _sim(res)
    # 每个窗口应当恰好持有一天（1h bar 的日频调仓）
    if not ((sim["days"] > 0.5) & (sim["days"] < 1.5)).all():
        bad.append(f"1h 日频窗口天数异常：中位 {sim['days'].median():.2f} 天")
    return res, bad


CASES = [
    ("基线 spot 多空", case_baseline),
    ("um 纯多头", case_long_only),
    ("um 纯空头", case_short_only),
    ("short_top 反向", case_short_top),
    ("成交额加权", lambda: case_weighting("adv")),
    ("逆波动加权", lambda: case_weighting("vol_inv")),
    ("收盘价成交", case_exec_close),
    ("日频调仓", lambda: case_rebalance("daily")),
    ("周频调仓", lambda: case_rebalance("weekly")),
    ("月频调仓", lambda: case_rebalance("monthly")),
    ("每 3 根 bar", lambda: case_rebalance("every")),
    ("分 3 组", lambda: case_quantiles(3)),
    ("分 10 组", lambda: case_quantiles(10)),
    ("退市冲击 0.5", case_delist_shock),
    ("中性化 logadv", lambda: case_neutralize("logadv")),
    ("中性化 logadv+beta+vol", lambda: case_neutralize("logadv_beta_vol")),
    ("不中性化", lambda: case_neutralize(None)),
    ("扣资金费", case_funding_on),
    ("不扣资金费", case_no_funding),
    ("零成本/高成本", case_costs),
    ("外部因子面板", case_external_panel),
    ("小时线 um/1h", case_hourly),
]


def main() -> None:
    ap = argparse.ArgumentParser(description="回测框架自检")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--only", nargs="*", default=None, help="只跑名字里含这些关键词的用例")
    args = ap.parse_args()

    cases = CASES
    if args.only:
        cases = [c for c in CASES if any(k in c[0] for k in args.only)]

    failed = []
    for name, fn in cases:
        try:
            res, bad = fn()
        except Exception as e:                       # noqa: BLE001
            failed.append(name)
            print(f"❌ {name}：{type(e).__name__}: {e}")
            traceback.print_exc(limit=3)
            continue
        if bad:
            failed.append(name)
            print(f"❌ {name}：" + "；".join(bad))
            continue
        s = _summary(res) if res is not None else None
        line = ""
        if args.verbose and s is not None and not s.empty:
            r = s.iloc[0]
            line = (f"   h={int(r['持有窗口'])} IC {r['IC']:+.2%} t {r['IC_t_NW']:+.2f} "
                    f"年化 {r['年化收益']:+.1%} 回撤 {r['最大回撤']:+.1%}")
        print(f"✅ {name}{line}")

    print()
    if failed:
        print(f"共 {len(cases)} 项，失败 {len(failed)} 项：{failed}")
        sys.exit(1)
    print(f"全部 {len(cases)} 项通过")


if __name__ == "__main__":
    main()
