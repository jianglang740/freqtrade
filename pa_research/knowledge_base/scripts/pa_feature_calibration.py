"""基础特征经验分布标定。

用途：为 Brooks 价格行为知识库（13_price_action_factors.md 第 3 节 Step 3）
提供各周期上基础特征的真实分布，用来判断某个阈值在这份数据上到底是
"常见" 还是 "极罕见"。

本脚本只做描述性统计，不做任何预测性主张，不生成任何交易信号。
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

"""路径说明：本脚本位于 <repo>/pa_research/knowledge_base/scripts/，
数据在 <repo>/user_data/data/binance/futures/，因此上溯四级到仓库根目录。"""

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA = REPO_ROOT / "user_data/data/binance/futures"
SYMBOLS = ["BTC_USDT_USDT", "ETH_USDT_USDT", "XAU_USDT_USDT"]
TIMEFRAMES = ["5m", "15m", "1h", "4h"]
EPS = 1e-12


def load(sym: str, tf: str) -> pd.DataFrame:
    fp = DATA / f"{sym}-{tf}-futures.feather"
    df = pd.read_feather(fp)
    df = df[["date", "open", "high", "low", "close", "volume"]].copy()
    df = df.dropna(subset=["open", "high", "low", "close"])
    # 卫生检查：high 必须 >= max(o,c), low <= min(o,c)
    bad = (
        (df["high"] < df[["open", "close"]].max(axis=1) - EPS)
        | (df["low"] > df[["open", "close"]].min(axis=1) + EPS)
        | (df["high"] < df["low"] - EPS)
        | (df["close"] <= 0)
    )
    n_bad = int(bad.sum())
    if n_bad:
        df = df[~bad].reset_index(drop=True)
    return df, n_bad


def features(df: pd.DataFrame) -> pd.DataFrame:
    o, h, l, c = (df[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
    r = h - l
    r_safe = np.maximum(r, EPS)
    prev_r = np.concatenate([[np.nan], r[:-1]])
    prev_h = np.concatenate([[np.nan], h[:-1]])
    prev_l = np.concatenate([[np.nan], l[:-1]])

    out = pd.DataFrame(
        {
            "body_ratio": np.abs(c - o) / r_safe,
            "close_location": (c - l) / r_safe,
            "directional_body": (c - o) / r_safe,
            "upper_wick": (h - np.maximum(o, c)) / r_safe,
            "lower_wick": (np.minimum(o, c) - l) / r_safe,
            "norm_range": r / np.maximum(c, EPS),
            "overlap": np.maximum(
                0.0, np.minimum(h, prev_h) - np.maximum(l, prev_l)
            ) / np.maximum(np.minimum(r, prev_r), EPS),
        }
    )
    return out


def pct(s: pd.Series, q: float) -> float:
    return float(np.nanpercentile(s.to_numpy(dtype=float), q))


def main() -> None:
    rows = []
    for sym in SYMBOLS:
        for tf in TIMEFRAMES:
            fp = DATA / f"{sym}-{tf}-futures.feather"
            if not fp.exists():
                continue
            df, n_bad = load(sym, tf)
            f = features(df)
            f = f.replace([np.inf, -np.inf], np.nan)

            br = f["body_ratio"]
            clv = f["close_location"]
            rows.append(
                {
                    "symbol": sym.split("_")[0],
                    "tf": tf,
                    "n": len(f),
                    "dropped": n_bad,
                    "br_p10": pct(br, 10),
                    "br_med": pct(br, 50),
                    "br_p90": pct(br, 90),
                    "doji_<0.10": float((br < 0.10).mean()),
                    "trendbar_>0.70": float((br > 0.70).mean()),
                    "clv_p10": pct(clv, 10),
                    "clv_med": pct(clv, 50),
                    "clv_p90": pct(clv, 90),
                    "close_top_>0.75": float((clv > 0.75).mean()),
                    "close_bot_<0.25": float((clv < 0.25).mean()),
                    "ovl_med": pct(f["overlap"], 50),
                    "ovl_p90": pct(f["overlap"], 90),
                    "zero_overlap": float((f["overlap"] <= 0).mean()),
                    "rng_med_pct": pct(f["norm_range"], 50) * 100,
                }
            )
            print(f"done {sym} {tf} n={len(f)}", file=sys.stderr)

    out = pd.DataFrame(rows)
    pd.set_option("display.width", 240)
    pd.set_option("display.max_columns", 40)
    pd.set_option("display.float_format", lambda v: f"{v:.4f}")
    print(out.to_string(index=False))
    out.to_csv(Path(__file__).resolve().parent / "pa_feature_calibration.csv", index=False)
    # 注：CSV 输出到脚本同目录；它只是缓存，删掉可随时重跑


if __name__ == "__main__":
    main()
