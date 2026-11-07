"""数据湖构建的公共常量与工具。

三个脚本共用：01_fetch_bv.py（下载）、02_partition.py（分区）、03_verify.py（校验）。
把路径、schema、写入参数集中在这里，避免三处各写一份然后悄悄漂移。
"""

from __future__ import annotations

import os
import subprocess

# ── 路径 ──────────────────────────────────────────────
HERE = os.path.dirname(os.path.abspath(__file__))   # 因子研究/行情数据/_build
LAKE = os.path.dirname(HERE)                        # 因子研究/行情数据 ← 湖本体
PANEL = os.path.dirname(LAKE)                       # 因子研究（tradfi_contracts.csv 在这一层）
CACHE = os.path.join(LAKE, ".cache_bv")             # 单符号下载缓存
BASIC = os.path.join(LAKE, "basic")
MANIFEST = os.path.join(LAKE, "_manifest.json")
AUDIT = os.path.join(LAKE, "_audit.json")

# ── 数据源 ────────────────────────────────────────────
BASE = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
SOCKS = ("--socks5-hostname", "127.0.0.1:1080")
WORKERS = 16

# market → 归档里的路径段
MARKET_PATH = {
    "spot": "data/spot",
    "um": "data/futures/um",
}
MARKETS = tuple(MARKET_PATH)

# ── schema ────────────────────────────────────────────
KLINE_COLUMNS = [
    "symbol", "base", "quote", "date",
    "open", "high", "low", "close",
    "volume",            # 基础币成交量
    "quote_volume",      # 归档官方的计价币成交额，不是 close × volume
    "trade_count",       # 成交笔数
    "taker_buy_base_volume",
    "taker_buy_quote_volume",
]

# 归档的 fundingRate CSV 是**三列**：calc_time, funding_interval_hours, last_funding_rate。
# 早期只存了 funding_rate，把 interval 丢掉过 —— 结果 parse 读成了第 2 列（1/4/8 小时），
# 回测里年化资金费算出 22 万%。interval 必须和 rate 一起落盘，回测才能按「每次结算」折算。
FUNDING_COLUMNS = ["symbol", "base", "quote", "date", "funding_interval_hours", "funding_rate"]

# 显式 arrow schema：分区之间必须完全一致，否则 03_verify.py 的第 3 项校验没有意义
# （不依赖 from_pandas 的自动推断 —— 一个符号某个月 volume 全是整数就会推成 int64）
def _arrow_schema(cols: list[str]):
    import pyarrow as pa

    types = {
        "symbol": pa.string(), "base": pa.string(), "quote": pa.string(),
        "date": pa.timestamp("ms", tz="UTC"),
        "trade_count": pa.int64(),
        "funding_interval_hours": pa.int64(),
        "funding_rate": pa.float64(),
    }
    return pa.schema([(c, types.get(c, pa.float64())) for c in cols])


KLINE_SCHEMA = _arrow_schema(KLINE_COLUMNS)
FUNDING_SCHEMA = _arrow_schema(FUNDING_COLUMNS)

# 写 parquet 的统一参数（与参考仓库 01_partition.py 一致）
PA_KW = dict(
    compression="zstd",
    compression_level=3,
    use_dictionary=True,
    write_statistics=True,
    row_group_size=1_000_000,
)

# ── 符号过滤 ──────────────────────────────────────────
QUOTE = "USDT"

# 杠杆代币：BTCUPUSDT / BTCDOWNUSDT / ETHBULLUSDT / ETHBEARUSDT 之类。
# 这些是币安发的结构化产品，不是独立资产，做横截面会污染排名。
LEVERAGED_SUFFIX = ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")

# 稳定币 / 法币：作为 base 出现时 mom/vol 分母趋零，会把它们顶到多空两端
STABLECOIN_BASES = frozenset({
    "USDC", "BUSD", "TUSD", "USDP", "DAI", "FDUSD", "PAX", "USDD", "USDS",
    "EURC", "USD1", "UST", "USTC", "GUSD", "LUSD", "FRAX", "PYUSD", "AEUR",
    "USDE", "XUSD", "BKRW", "IDRT", "BVND", "USDSB", "SUSD",
})
FIAT_BASES = frozenset({
    "EUR", "GBP", "TRY", "BRL", "AUD", "JPY", "ZAR", "RUB", "UAH", "NGN",
    "PLN", "RON", "ARS", "MXN", "CZK", "DKK", "HUF", "ILS", "ISK", "NOK",
    "NZD", "SEK", "CHF", "CAD", "SGD", "HKD", "CNY",
})


def split_symbol(symbol: str) -> tuple[str, str]:
    """'BTCUSDT' → ('BTC', 'USDT')。归档里所有符号都以计价币结尾。"""
    if symbol.endswith(QUOTE):
        return symbol[: -len(QUOTE)], QUOTE
    return symbol, ""


def is_leveraged(symbol: str) -> bool:
    return symbol.endswith(LEVERAGED_SUFFIX)


# ── HTTP（走 curl + SOCKS，和现有 fetch_spot_all.py 同一套，已实测可用）──
def http_get(url: str, timeout: int = 90) -> bytes:
    p = subprocess.run(
        ["curl", "-s", "-f", *SOCKS, "--max-time", str(timeout), url],
        capture_output=True,
    )
    return p.stdout if p.returncode == 0 else b""


def ensure_dirs() -> None:
    for d in (LAKE, BASIC,
              os.path.join(CACHE, "spot"), os.path.join(CACHE, "um")):
        os.makedirs(d, exist_ok=True)


def cache_dir(market: str, tf: str) -> str:
    return os.path.join(CACHE, market, tf)
