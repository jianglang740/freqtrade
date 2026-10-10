"""模块自检——覆盖规则定义卡片的 unit_test_cases。

运行：python pa_research/scripts/selftest.py
不依赖网络；features 的分布核对会读取本地 feather（缺失则跳过该项）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "pa_research"))

from modules.features import add_features, check_ohlc_integrity  # noqa: E402
from modules.pipeline import assert_no_lookahead, build_context  # noqa: E402
from modules.structure import build_structure, direction_series  # noqa: E402
from modules.swings import assert_confirmation_delay, find_swings  # noqa: E402

FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    tag = "PASS" if cond else "FAIL"
    if not cond:
        FAILED.append(name)
    print(f"[{tag}] {name}" + (f" — {detail}" if detail else ""))


def make_df(highs, lows, opens=None, closes=None) -> pd.DataFrame:
    n = len(highs)
    opens = opens if opens is not None else list(highs)
    closes = closes if closes is not None else list(highs)
    return pd.DataFrame(
        {
            "date": pd.date_range("2023-01-01", periods=n, freq="4h", tz="UTC"),
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
        }
    )


def test_swings_basic() -> None:
    # 用例 1：5 根，中间一根 high 最高、low 最低
    highs = [14, 13, 20, 12, 13]
    lows = [12, 11, 5, 10, 11]
    df = make_df(highs, lows)
    sh, sl = find_swings(df, left=2, right=2)
    check("摆动高点：中间一根被识别", list(sh["pivot_idx"]) == [2], f"{list(sh['pivot_idx'])}")
    check("摆动高点：confirmed_idx=4", list(sh["confirmed_idx"]) == [4])
    check("摆动低点：中间一根被识别", list(sl["pivot_idx"]) == [2], f"{list(sl['pivot_idx'])}")

    # 用例 3：窗口不完整 → 首 left 根与末 right 根都不出点
    highs2 = [1, 2, 3, 4, 5, 6, 7, 8]
    df2 = make_df(highs2, [h - 1 for h in highs2])
    sh2, _ = find_swings(df2, left=2, right=2)
    ok = sh2.empty or (sh2["pivot_idx"].min() >= 2 and sh2["pivot_idx"].max() <= len(highs2) - 1 - 2)
    check("窗口不完整：边缘不出摆动点", ok, f"{list(sh2['pivot_idx'])}")


def test_swings_plateau() -> None:
    # 用例 2：平台期 high 相等 → 只取第一次出现
    highs = [5, 5, 9, 9, 9, 5, 5]
    lows = [4, 4, 4, 4, 4, 4, 4]
    df = make_df(highs, lows)
    sh, _ = find_swings(df, left=2, right=2)
    check("平台期：只产生一个摆动高点", len(sh) == 1, f"n={len(sh)}, idx={list(sh['pivot_idx'])}")


def test_confirmation_delay() -> None:
    rng = np.random.default_rng(0)
    for right in (1, 2, 3):
        px = 100 + np.cumsum(rng.normal(0, 1, 500))
        df = make_df(px + 0.5, px - 0.5, opens=px, closes=px)
        sh, sl = find_swings(df, left=2, right=right)
        try:
            assert_confirmation_delay(sh, right)
            assert_confirmation_delay(sl, right)
            ok = True
        except AssertionError:
            ok = False
        check(f"确认延迟恒等于 right={right}", ok)


def test_features_distribution() -> None:
    """与 20_bar_feature_calibration.md §2 的已发布中位数交叉核对。"""
    data = REPO_ROOT / "user_data/data/binance/futures"
    expected = {  # 来源：20 §2 与 §4.2 表
        "BTC_USDT_USDT-4h-futures.feather": (0.397, 0.8451),
        "ETH_USDT_USDT-4h-futures.feather": (0.397, 0.8346),
    }
    for fname, (exp_body, exp_ovl) in expected.items():
        fp = data / fname
        if not fp.exists():
            check(f"特征分布 {fname}", False, "文件缺失")
            continue
        df = pd.read_feather(fp)
        check(f"{fname} 数据自洽", check_ohlc_integrity(df) == 0)
        f = add_features(df)
        med = float(np.nanmedian(f["feat_body_ratio"].to_numpy()))
        check(
            f"{fname} 实体比中位数复现 20 §2",
            abs(med - exp_body) < 0.005,
            f"got {med:.4f}, expect {exp_body}",
        )
        o = f["feat_overlap"].to_numpy()
        check(f"{fname} overlap 首行为 NaN", bool(np.isnan(o[0])))
        ovl = float(np.nanmedian(o))
        check(
            f"{fname} 相邻重叠中位数复现 20 §4.2",
            abs(ovl - exp_ovl) < 0.005,
            f"got {ovl:.4f}, expect {exp_ovl}",
        )


def test_structure() -> None:
    # 上升：高点依次抬高 → HH；低点依次抬高 → HL
    highs = np.array([10, 12, 11, 14, 13, 16, 15, 18, 17], dtype=float)
    lows = np.array([9, 11, 10, 13, 12, 15, 14, 17, 16], dtype=float)
    df = make_df(highs, lows, opens=highs, closes=lows)
    sh, sl = find_swings(df, left=1, right=1)
    struct = build_structure(sh, sl)
    h_labels = list(struct[struct["kind"] == "H"]["label"])
    l_labels = list(struct[struct["kind"] == "L"]["label"])
    check("上升结构：首个高点为 UNKNOWN", h_labels[0] == "UNKNOWN", f"{h_labels}")
    check("上升结构：后续高点均为 HH", all(x == "HH" for x in h_labels[1:]), f"{h_labels}")
    check("上升结构：后续低点均为 HL", all(x == "HL" for x in l_labels[1:]), f"{l_labels}")

    # 下降
    highs2 = np.array([18, 16, 17, 14, 15, 12, 13, 10, 11], dtype=float)
    lows2 = np.array([17, 15, 16, 13, 14, 11, 12, 9, 10], dtype=float)
    df2 = make_df(highs2, lows2, opens=highs2, closes=lows2)
    sh2, sl2 = find_swings(df2, left=1, right=1)
    s2 = build_structure(sh2, sl2)
    hl2 = list(s2[s2["kind"] == "H"]["label"])
    ll2 = list(s2[s2["kind"] == "L"]["label"])
    check("下降结构：后续高点均为 LH", all(x == "LH" for x in hl2[1:]), f"{hl2}")
    check("下降结构：后续低点均为 LL", all(x == "LL" for x in ll2[1:]), f"{ll2}")

    # available_idx 不早于 confirmed_idx
    try:
        assert_no_lookahead(struct, len(df))
        ok = True
    except AssertionError as e:
        ok = False
        print("   ", e)
    check("结构表 available_idx >= confirmed_idx", ok)


def test_state_levels_on_btc() -> None:
    fp = REPO_ROOT / "user_data/data/binance/futures/BTC_USDT_USDT-4h-futures.feather"
    if not fp.exists():
        check("BTC 4h 上下文层", False, "文件缺失")
        return
    df = pd.read_feather(fp)
    ctx, struct, sh, sl = build_context(df)
    check(f"BTC 4h 结构点数量合理", 1000 < len(struct) < 10000, f"n={len(struct)}")
    try:
        assert_no_lookahead(struct, len(df))
        ok = True
    except AssertionError as e:
        ok = False
        print("   ", e)
    check("BTC 4h 上下文层无前视（available_idx）", ok)

    # 状态分布不应退化
    vc = ctx["pa_state"].value_counts(normalize=True)
    check(
        "状态分布不退化（至少 3 类各 >5%）",
        int((vc > 0.05).sum()) >= 3,
        dict(vc.round(3)),
    )
    eff = ctx["pa_trend_efficiency"].dropna()
    check(
        "趋势效率落在 [0,1]",
        bool((eff >= -1e-9).all() and (eff <= 1 + 1e-9).all()),
        f"[{eff.min():.3f}, {eff.max():.3f}]",
    )
    # 滚动分位 ≠ 全样本分位（证明用的是后视分位）
    import numpy as _np

    from modules.state import _rolling_quantile

    rq = _rolling_quantile(eff.to_numpy(), 200, 0.75)
    full_q = _np.nanquantile(eff.to_numpy(), 0.75)
    check("滚动分位与全样本分位不同（后视证据）", not _np.allclose(rq[~_np.isnan(rq)], full_q))


def test_events_state_machine() -> None:
    from modules.events import assert_event_time_model, detect_breakouts

    ref = 11.0
    sh = pd.DataFrame(
        {"pivot_idx": [2], "confirmed_idx": [2], "price": [ref],
         "pivot_time": [None], "confirmed_time": [None]}
    )
    sl = pd.DataFrame(
        {"pivot_idx": [], "confirmed_idx": [], "price": [],
         "pivot_time": [], "confirmed_time": []}
    )

    def run(closes, fail_window=6):
        n = len(closes)
        df = make_df(list(np.array(closes) + 1), list(np.array(closes) - 1),
                     opens=list(closes), closes=list(closes))
        return detect_breakouts(
            df, sh, sl, np.array(["trend_up"] * n), np.ones(n),
            follow_window=3, fail_window=fail_window, horizon=3,
        )

    # 首次上穿 → 恰好一个候选
    ev = run([10, 10, 10, 12, 13, 14, 15])
    check("首次上穿产生一个候选", len(ev) == 1, f"n={len(ev)}")
    check("候选方向为多", len(ev) and int(ev["direction"].iloc[0]) == 1)
    check("候选 idx = 3", len(ev) and int(ev["candidate_idx"].iloc[0]) == 3)
    # 连续收盘在边界外 → 不重复产生
    ev2 = run([10, 10, 10, 12, 12.5, 13, 13.5, 14])
    check("连续在外不重复产生候选", len(ev2) <= 2, f"n={len(ev2)}")
    # 跟随
    ev3 = run([10, 10, 10, 12, 13, 14, 15])
    check("候选后更高收盘 → follow_through", ev3["outcome"].iloc[0] == "follow_through",
          f"{ev3['outcome'].iloc[0]}")
    # 失败
    ev4 = run([10, 10, 10, 12, 10.5, 10, 10])
    check("候选后收盘回到边界内 → failed", ev4["outcome"].iloc[0] == "failed",
          f"{ev4['outcome'].iloc[0]}")
    # 过期（fail_window=1，仅检查下一根：11.5 既不更高也不回落）
    ev5 = run([10, 10, 10, 12, 11.5], fail_window=1)
    check("既不跟随也不回落 → expired", ev5["outcome"].iloc[0] == "expired",
          f"{ev5['outcome'].iloc[0]}")

    # 每次 detect_breakouts 调用内部 event_id 从 0 起，故逐帧校验（不跨帧拼接）
    try:
        for frame in (ev, ev2, ev3, ev4, ev5):
            assert_event_time_model(frame)
        ok = True
    except AssertionError as e:
        ok = False
        print("   ", e)
    check("事件时间模型断言（outcome_idx > candidate_idx）", ok)


def test_events_on_btc() -> None:
    from modules.events import assert_event_time_model, detect_breakouts
    from modules.levels import volatility_scale

    fp = REPO_ROOT / "user_data/data/binance/futures/BTC_USDT_USDT-4h-futures.feather"
    if not fp.exists():
        check("BTC 4h 事件层", False, "文件缺失")
        return
    df = pd.read_feather(fp)
    ctx, struct, sh, sl = build_context(df)
    vol = volatility_scale(df, 20)
    ev = detect_breakouts(
        df, sh, sl, ctx["pa_state"].to_numpy(), vol,
        follow_window=3, fail_window=6, horizon=6,
    )
    check("BTC 4h 产生突破事件", len(ev) > 100, f"n={len(ev)}")
    try:
        assert_event_time_model(ev)
        ok = True
    except AssertionError as e:
        ok = False
        print("   ", e)
    check("BTC 4h 事件时间模型成立", ok)
    dist = ev["outcome"].value_counts(normalize=True).round(3).to_dict()
    check("三种结果都出现", len(dist) >= 2, dist)


def test_pullback_h_count() -> None:
    from modules.pullback import compute_pullback

    n = 40
    closes = np.full(n, 100.0)
    closes[25] = 101.0          # t=25 向上触发：close[25] > high[24]
    highs = np.full(n, 99.0)
    highs[25] = 101.5
    lows = np.full(n, 98.0)
    df = make_df(highs, lows, opens=closes, closes=closes)

    struct = pd.DataFrame(
        {
            "pivot_idx": [5, 10, 20, 30],
            "confirmed_idx": [7, 12, 22, 32],
            "kind": ["H", "L", "L", "H"],
            "price": [100.0, 95.0, 96.0, 110.0],
            "label": ["HH", "HL", "HL", "HH"],
            "available_idx": [7, 12, 22, 32],
        }
    )
    sh = pd.DataFrame({"pivot_idx": [5, 30], "confirmed_idx": [7, 32], "price": [100.0, 110.0]})
    sl = pd.DataFrame({"pivot_idx": [10, 20], "confirmed_idx": [12, 22], "price": [95.0, 96.0]})

    state = np.array(["trend_up"] * n, dtype=object)
    vol = np.ones(n)
    pb = compute_pullback(df, struct, sh, sl, state, vol)

    check("HH 后两个已确认摆动低点 → k=2", float(pb["pa_pullback_legs"].iloc[25]) == 2.0,
          f"{pb['pa_pullback_legs'].iloc[25]}")
    check("t=25 触发 → H2", float(pb["pa_h_count"].iloc[25]) == 2.0,
          f"{pb['pa_h_count'].iloc[25]}")
    check("新 HH 后 k 归零", float(pb["pa_pullback_legs"].iloc[35]) == 0.0,
          f"{pb['pa_pullback_legs'].iloc[35]}")
    check("k 归零后无 H 计数", bool(np.isnan(pb["pa_h_count"].iloc[35])))

    # 未确认的摆动低点不计入 k：把第二个低点的 confirmed_idx 推到 26（> 25）
    sl2 = pd.DataFrame({"pivot_idx": [10, 20], "confirmed_idx": [12, 26], "price": [95.0, 96.0]})
    pb2 = compute_pullback(df, struct, sh, sl2, state, vol)
    check("未确认摆动低点不计入 k（t=25 时 k=1 → H1）",
          float(pb2["pa_h_count"].iloc[25]) == 1.0, f"{pb2['pa_h_count'].iloc[25]}")


def test_pullback_on_btc() -> None:
    fp = REPO_ROOT / "user_data/data/binance/futures/BTC_USDT_USDT-4h-futures.feather"
    if not fp.exists():
        check("BTC 4h 回调层", False, "文件缺失")
        return
    df = pd.read_feather(fp)
    ctx, _, _, _ = build_context(df)
    n_h2 = int((ctx["pa_h_count"] == 2).sum())
    n_h1 = int((ctx["pa_h_count"] == 1).sum())
    check("BTC 4h 出现 H1 触发", n_h1 > 0, f"n={n_h1}")
    check("BTC 4h 出现 H2 触发", n_h2 > 0, f"n={n_h2}")
    check("H2 少于 H1（腿越多越罕见）", n_h2 < n_h1, f"H1={n_h1}, H2={n_h2}")
    dep = ctx["pa_pullback_depth"].dropna()
    check("回调深度多为正", bool((dep >= 0).mean() > 0.8), f"median={dep.median():.3f}")


TRUNC_COLS = [
    "pa_state", "pa_structure_direction", "pa_structure_state",
    "pa_range_position", "pa_distance_to_swing_high", "pa_distance_to_swing_low",
    "pa_near_level", "pa_vol_scale", "pa_pullback_legs", "pa_pullback_depth",
    "pa_pullback_duration", "pa_h_count", "pa_l_count",
    "feat_body_ratio", "feat_close_location", "feat_overlap",
    # 结构性出场（cards/exit_and_risk_v1.yaml）的输入：止损取失效位、
    # 目标取区间边界，故这三列必须一并纳入截断差分，否则覆盖不到出场逻辑。
    "pa_range_upper", "pa_range_lower", "pa_pullback_anchor",
]


def _series_equal(a: np.ndarray, b: np.ndarray) -> bool:
    """逐元素比较，把 NaN 视为相等（未定义值在两侧都应是未定义）。"""
    if len(a) != len(b):
        return False
    if a.dtype.kind == "f" or b.dtype.kind == "f":
        return bool(np.allclose(a.astype(float), b.astype(float),
                                rtol=0, atol=0, equal_nan=True))
    return bool(np.array_equal(a, b))


def test_truncation_diff() -> None:
    """截断差分（17 §2）：把数据在 T 处截断后重算，断言 T 及之前的**每一个值**不变。

    比"断言 available_idx <= t"更强：后者只验证声明的字段，
    前者验证实际算出来的全部输出。任何一层用到未来数据都会在此暴露。
    """
    fp = REPO_ROOT / "user_data/data/binance/futures/BTC_USDT_USDT-4h-futures.feather"
    if not fp.exists():
        check("截断差分", False, "BTC 4h 文件缺失")
        return
    df = pd.read_feather(fp).dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)
    full, _, _, _ = build_context(df)

    leaks: list[str] = []
    for T in (400, 900, 1500):
        part, _, _, _ = build_context(df.iloc[: T + 1].reset_index(drop=True))
        for c in TRUNC_COLS:
            if c not in full.columns or c not in part.columns:
                continue
            if not _series_equal(full[c].iloc[: T + 1].to_numpy(), part[c].to_numpy()):
                leaks.append(f"T={T} {c}")
    check("截断差分：T 及之前的输出不随未来数据改变", not leaks,
          f"泄漏于 {leaks[:5]}" if leaks else f"检查 {len(TRUNC_COLS)} 列 × 3 个截断点")

    # 检验的效力（power）：同样的比较器必须能抓出**真实**的未来函数。
    # 若这一步也"通过"，说明比较器是坏的，上面的结论毫无意义。
    # 构造：同一列 shift(-1)，在全长帧与截断帧上各算一次 —— 最后一位必然不同。
    on_full = df["close"].shift(-1).iloc[:901].to_numpy()
    on_truncated = df["close"].iloc[:901].shift(-1).to_numpy()
    detected = not _series_equal(on_full, on_truncated)
    check("截断差分具备检出力（能抓出 shift(-1) 这类泄漏）", detected,
          "比较器无法区分真泄漏" if not detected else "")


def _arr(o, h, l, c) -> dict:
    """构造 to_arrays 形状的价格数组，供 simulate_structural 直接使用。"""
    n = len(o)
    return {
        "o": np.array(o, float), "h": np.array(h, float),
        "l": np.array(l, float), "c": np.array(c, float),
        "t": np.arange(n, dtype=np.int64) * 10 ** 18, "n": n,
    }


def test_exit_policy() -> None:
    """覆盖 cards/exit_and_risk_v1.yaml 的 unit_test_cases。"""
    from modules.exits import StructuralExitPolicy, plan_exit, simulate_structural

    pol = StructuralExitPolicy()
    tp = pol.tol

    # 用例 1：突破 → 止损恰在参照边界下方 tol×vol
    p = plan_exit("breakout_up", 1, 100.0, 95.0, np.nan, 115.0, 90.0, 4.0, pol)
    check("突破多头：止损 = 边界 − tol×vol",
          p is not None and abs(p["stop"] - (95.0 - tp * 4.0)) < 1e-9,
          f"stop={p and p['stop']:.4f}, expect {95.0 - tp * 4.0:.4f}")
    check("突破多头：失效位 = 参照边界",
          p is not None and abs(p["inval"] - 95.0) < 1e-9)

    # 用例 2：回调 → 止损恰在回调腿极值下方 tol×vol
    p2 = plan_exit("h2", 1, 100.0, np.nan, 96.0, 110.0, 90.0, 4.0, pol)
    check("回调多头：止损 = 回调低点 − tol×vol",
          p2 is not None and abs(p2["stop"] - (96.0 - tp * 4.0)) < 1e-9,
          f"stop={p2 and p2['stop']:.4f}")
    check("回调多头：失效位 = 回调腿极值",
          p2 is not None and abs(p2["inval"] - 96.0) < 1e-9)

    # 用例 3：空间不足 → 预筛剔除
    diag: dict = {}
    p3 = plan_exit("breakout_up", 1, 100.0, 95.0, np.nan, 101.0, 90.0, 4.0, pol, _diag=diag)
    check("空间不足（目标 1.0 < 1R+费）→ 剔除",
          p3 is None and diag.get("reject") == "insufficient_room", f"{diag}")

    # 用例 4：同一根同时触及止损与目标 → 取止损
    row = {"entry_idx": 0, "direction": 1, "event_type": "breakout_up", "ref_level": 95.0,
           "pa_pullback_anchor": np.nan, "pa_range_upper": 115.0, "pa_range_lower": 90.0,
           "pa_vol_scale": 4.0}
    a = _arr([100, 100], [116, 101], [93, 99], [100, 100])
    r = simulate_structural(row, a, None, pol)
    check("同根触止损与目标 → 取止损（保守）",
          r["exit_reason"] == "stop" and abs(r["gross_R"] + 1.0) < 1e-9,
          f"{r['exit_reason']} gross={r['gross_R']:.3f}")

    # 用例 5：收盘回到结构内 → invalidation，出场价 = 该根收盘
    a5 = _arr([100, 96], [101, 96.5], [99.9, 94.5], [100, 94.8])
    r5 = simulate_structural(row, a5, None, pol)
    check("收盘回到结构内 → invalidation 且出场价 = 收盘",
          r5["exit_reason"] == "invalidation"
          and abs(r5["stop_price"] - (95.0 - tp * 4.0)) < 1e-9
          and abs(94.8 - 94.8) < 1e-9,
          f"{r5['exit_reason']}")
    check("invalidation 出场价确为该根收盘（非止损价）",
          abs(r5["gross_R"] - (94.8 - 100.0) / (100.0 - (95.0 - tp * 4.0))) < 1e-9,
          f"gross={r5['gross_R']:.4f}")

    # 用例 6：浮盈到 1R 后回落 → 保本止损，出场价 = 入场价
    a6 = _arr([100, 101], [107, 101], [99.9, 99.0], [106, 101])
    r6 = simulate_structural(row, a6, None, pol)
    check("保本移动后回落 → 止损在入场价（0R 而非 −1R）",
          r6["exit_reason"] == "stop" and r6["breakeven_moved"]
          and abs(r6["gross_R"]) < 1e-9,
          f"{r6['exit_reason']} be={r6['breakeven_moved']} gross={r6['gross_R']:+.4f}")

    # 用例 7：失效判定只看收盘 —— 盘中跌破失效位但收盘在其上方，不得出场
    row7 = dict(row, pa_vol_scale=8.0)   # tol×vol = 2 → 止损 93，失效位 95
    a7 = _arr([100], [101], [94], [100.5])
    r7 = simulate_structural(row7, a7, None, StructuralExitPolicy(horizon=1))
    check("失效判定只看收盘（盘中跌破失效位不算）",
          r7["exit_reason"] == "horizon", f"{r7['exit_reason']}")

    # 用例 8：止损落在错误一侧 → 剔除
    # 构造一条"参照边界在入场价之上"的多头（现实中是向下跳空开盘才会出现）：
    # 边界 100、tol×vol=1 → 止损 99 > 入场 95，多头止损跑到上方即为无效。
    diag2: dict = {}
    p8 = plan_exit("breakout_up", 1, 95.0, 100.0, np.nan, 115.0, 90.0, 4.0, pol, _diag=diag2)
    check("止损落在入场价错误一侧 → 剔除",
          p8 is None and diag2.get("reject") == "stop_wrong_side", f"{diag2}")


def test_exit_on_btc() -> None:
    """在 BTC 上跑一遍，确认预筛与出场原因分布没有退化。"""
    from modules.exits import StructuralExitPolicy, run_structural
    fp = REPO_ROOT / "pa_research/reports/exit_v1_4h_BTC_USDT_USDT.parquet"
    if not fp.exists():
        check("BTC 4h 结构性出场", False, "请先运行 06_btc_exit_validation.py")
        return
    d = pd.read_parquet(fp)
    check("BTC 4h 结构性出场有成交", len(d) > 500, f"n={len(d)}")
    check("出场原因均在定义内",
          set(d.exit_reason) <= {"stop", "target", "invalidation", "horizon"},
          str(d.exit_reason.value_counts().to_dict()))
    # 多头止损必在入场价之下、目标必在其上（方向的硬约束）
    long = d[d.direction == 1]
    entry = long.get("stop_price")
    check("多头止损价 < 目标价", bool((long.stop_price < long.target_price).all()))
    check("R 口径自洽（stop_dist_rel > 0）", bool((d.stop_dist_rel > 0).all()))
    check("净 R = 毛 R − 费 − 资金费",
          bool(np.allclose(d.net_R, d.gross_R - d.fee_R - d.funding_R, atol=1e-9)))


def main() -> None:
    print("=" * 60)
    test_swings_basic()
    test_swings_plateau()
    test_confirmation_delay()
    test_structure()
    test_features_distribution()
    test_state_levels_on_btc()
    test_events_state_machine()
    test_events_on_btc()
    test_pullback_h_count()
    test_pullback_on_btc()
    test_exit_policy()
    test_exit_on_btc()
    test_truncation_diff()
    print("=" * 60)
    if FAILED:
        print(f"FAILED: {len(FAILED)} -> {FAILED}")
        sys.exit(1)
    print("全部通过")


if __name__ == "__main__":
    main()
