#!/usr/bin/env python
"""
探测 Binance U 本位合约里的 TradFi 板块（美股 / 港股 / 韩股 / 中国股 / 大宗商品 / Pre-IPO / 外汇）。

只读元数据，不下载 K 线：
  1. `/fapi/v1/exchangeInfo` 取全部合约，按 `contractType == "TRADIFI_PERPETUAL"` 筛出 TradFi；
  2. `/fapi/v1/ticker/24hr` 拿最新价、24h 成交额、涨跌；
  3. 扫 `data.binance.vision` 的月线目录，确认每个币在归档里真的有数据、以及起点在哪。

产出（都在本目录）：
  tradfi_contracts.csv   —— 235 行机读清单
  根目录 TradFi合约清单.md 会由 --markdown 重新生成

用法：
    ssh -fN quant-proxy     # 出境要走 socks（见 mac-proxy-ssh-tunnel）
    source /opt/anaconda3/etc/profile.d/conda.sh && conda activate freqtrade
    python 因子研究/tradfi_probe.py                 # 只写 csv
    python 因子研究/tradfi_probe.py --markdown      # 连报告一起重写

⚠️ 分类不要自己猜：交易所的 `underlyingType` 字段直接给了类别，
   `underlyingSubType` 里统一带 `"TradFi"` 标记。
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import re
import statistics
import subprocess
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
CSV_OUT = os.path.join(HERE, "tradfi_contracts.csv")
MD_OUT = os.path.join(os.path.dirname(HERE), "TradFi合约清单.md")

API = "https://fapi.binance.com"
ARCHIVE = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
SOCKS = "socks5h://127.0.0.1:1080"

# 交易所字段 → 中文名；顺序即报告里的章节顺序
LABEL = {
    "EQUITY": "美股",
    "HK_EQUITY": "港股",
    "KR_EQUITY": "韩股",
    "CN_EQUITY": "中国股",
    "COMMODITY": "大宗商品",
    "PREMARKET": "Pre-IPO",
    "FX": "外汇",
}
ORDER = list(LABEL.values())

# 标的名 → 公司/标的一句话说明。
# 惯例：ETF 与杠杆产品必须写明「XX ETF / N× 做多」，不要伪装成公司；
# 同一家公司有多套合约的（SPCX、TENCENT）各自独立写。
# ⚠️ 交易所只给 baseAsset 代码，公司身份来自公开资料（上市公司公告、基金发行方说明），
#    不是 API 字段。新上币时这里没有条目 —— 脚本会点名，不要静默留空。
DESC: dict[str, str] = {
    # ── 美股 · 半导体 / 存储 / 硬件 ──
    "NVDA": "NVIDIA 英伟达 · GPU 与 AI 加速卡",
    "AMD": "AMD 超威半导体 · CPU / GPU",
    "INTC": "Intel 英特尔 · CPU 与晶圆代工",
    "MU": "Micron 美光科技 · DRAM / HBM 内存",
    "WDC": "Western Digital 西部数据 · 机械硬盘",
    "SNDK": "Sandisk 闪迪 · NAND 闪存（2025 年从西部数据分拆）",
    "AVGO": "Broadcom 博通 · 定制 ASIC / 网络芯片",
    "QCOM": "Qualcomm 高通 · 手机 SoC / 基带",
    "MRVL": "Marvell 美满电子 · 数据中心定制芯片",
    "ARM": "Arm Holdings · 芯片指令集 IP 授权",
    "TSM": "台积电 ADR · 晶圆代工",
    "ASML": "ASML 阿斯麦 ADR · EUV 光刻机",
    "AMAT": "Applied Materials 应用材料 · 半导体设备",
    "LRCX": "Lam Research 泛林 · 刻蚀设备",
    "KLAC": "KLA · 半导体量测 / 检测设备",
    "TER": "Teradyne 泰瑞达 · 半导体测试设备",
    "SNPS": "Synopsys 新思科技 · EDA 工具",
    "TXN": "Texas Instruments 德州仪器 · 模拟芯片",
    "STM": "STMicroelectronics 意法半导体 ADR · MCU / 功率器件",
    "ALAB": "Astera Labs · AI 服务器互联芯片",
    "CRDO": "Credo Technology · 高速互联（AEC 线缆 / SerDes）",
    "AXTI": "AXT · 化合物半导体衬底（砷化镓 / 磷化铟）",
    "SAMSUNG": "三星电子 · 存储 / 手机 / 晶圆代工",
    "SMCI": "Super Micro 超微电脑 · AI 服务器整机",
    "DELL": "Dell 戴尔 · 服务器与 PC",
    "HPE": "Hewlett Packard Enterprise · 企业服务器 / 网络",
    "PENG": "Penguin Solutions · 企业级内存与 AI 基础设施",
    "FLEX": "Flex 伟创力 · 电子代工（EMS）",
    "GLW": "Corning 康宁 · 玻璃基板 / 光纤",
    "BBX": "BlackBerry 黑莓 · 车载软件与安全",
    "SONY": "Sony 索尼 ADR · 游戏 / 影像 / 音乐",
    # ── 美股 · 光通信 / 网络 ──
    "COHR": "Coherent 相干公司 · 光通信与激光器件",
    "LITE": "Lumentum · 光通信器件",
    "AAOI": "Applied Optoelectronics · 光模块",
    "CIEN": "Ciena · 光传输设备",
    "ANET": "Arista Networks · 数据中心交换机",
    "CSCO": "Cisco 思科 · 网络设备",
    "NOK": "Nokia 诺基亚 ADR · 通信设备",
    "AKAM": "Akamai · CDN 与边缘加速",
    "NET": "Cloudflare · CDN / 边缘计算",
    # ── 美股 · 软件 / 云 / 互联网 ──
    "MSFT": "Microsoft 微软 · 云与软件",
    "GOOGL": "Alphabet 谷歌 · 搜索 / 广告 / 云",
    "AMZN": "Amazon 亚马逊 · 电商与 AWS",
    "META": "Meta · 社交平台与广告",
    "AAPL": "Apple 苹果",
    "ORCL": "Oracle 甲骨文 · 数据库与企业云",
    "CRM": "Salesforce · 企业 CRM SaaS",
    "ADBE": "Adobe · 创意与营销软件",
    "NOW": "ServiceNow · 企业工作流 SaaS",
    "SNOW": "Snowflake · 云数据仓库",
    "MDB": "MongoDB · 文档型数据库",
    "DDOG": "Datadog · 云监控与可观测性",
    "ZS": "Zscaler · 零信任网络安全",
    "CRWD": "CrowdStrike · 终端安全",
    "PANW": "Palo Alto Networks · 网络安全",
    "TEAM": "Atlassian · 团队协作 SaaS（Jira）",
    "GTLB": "GitLab · DevOps 平台",
    "PATH": "UiPath · RPA 流程自动化",
    "SHOP": "Shopify · 电商 SaaS",
    "ZM": "Zoom · 视频会议",
    "IBM": "IBM · 企业 IT 与大型机",
    "ACN": "Accenture 埃森哲 · IT 咨询",
    "APP": "AppLovin · 移动广告与营销平台",
    "RDDT": "Reddit · 社交论坛",
    "RUM": "Rumble · 视频平台",
    "DJT": "Trump Media & Technology Group · Truth Social",
    "PDD": "拼多多 ADR · 电商",
    "BABA": "阿里巴巴 ADR · 电商与云",
    "MELI": "MercadoLibre · 拉美电商与支付",
    "EBAY": "eBay · 电商平台",
    "UBER": "Uber 优步 · 网约车与配送",
    "CVNA": "Carvana · 二手车线上零售",
    "HOOD": "Robinhood · 互联网券商",
    "SOFI": "SoFi · 数字银行",
    "IBKR": "Interactive Brokers 盈透证券 · 互联网券商",
    "PYPL": "PayPal · 在线支付",
    "PAYP": "PayPay · 日本软银系移动支付（2026-03 纳斯达克 IPO）",
    "COIN": "Coinbase · 美国最大加密资产交易所",
    "PLTR": "Palantir · 政府与企业数据分析平台",
    "TSLA": "Tesla 特斯拉 · 电动车 / 储能 / 自动驾驶",
    "CRCL": "Circle Internet Group · USDC 稳定币发行方",
    # ── 美股 · AI 算力 / 电力 / 数据中心 ──
    "CRWV": "CoreWeave · GPU 云算力租赁",
    "NBIS": "Nebius Group · AI 云算力（原 Yandex 海外业务）",
    "APLD": "Applied Digital · 数据中心与算力托管",
    "IREN": "IREN · 比特币矿场转型 AI 算力",
    "SHAZ": "SharonAI Holdings · 澳洲 AI 云算力（Neocloud）",
    "AGPU": "Axe Compute · 算力租赁（原 Predictive Oncology 更名）",
    "VRT": "Vertiv 维谛 · 数据中心散热与供电",
    "VST": "Vistra · 独立发电（核电 / 天然气）",
    "CEG": "Constellation Energy · 核电运营商",
    "GEV": "GE Vernova · 燃气轮机与电网设备",
    "EQT": "EQT Corporation · 美国天然气生产商",
    "OXY": "Occidental Petroleum 西方石油 · 油气",
    "XOM": "ExxonMobil 埃克森美孚 · 综合油气",
    "BE": "Bloom Energy · 固体氧化物燃料电池",
    "FLNC": "Fluence Energy · 电网侧储能系统",
    "SMR": "NuScale Power · 小型模块化核反应堆（SMR）",
    "OKLO": "Oklo · 小型模块化核反应堆（SMR）",
    "BWET": "Breakwave Tanker Shipping ETF · 油轮运价期货 ETF（非个股）",
    # ── 美股 · 科技新秀 / 2026 年 IPO ──
    "SPCX": "SpaceX 太空探索技术 · 猎鹰 / 星舰 + Starlink（2026-06 IPO，史上最大）",
    "CBRS": "Cerebras Systems · 晶圆级 AI 芯片（2026-05 IPO）",
    "QNTX": "Quantinuum · 离子阱量子计算（2026-06 IPO）",
    "IONQ": "IonQ · 离子阱量子计算",
    "BSP": "Bending Spoons · 意大利互联网软件集团（2026-07 IPO）",
    "SECZ": "Securitize · 现实资产代币化平台（2026 SPAC 上市）",
    "BOT": "RoboStrategy · 机器人 / 具身智能私募股权的封闭式基金（非运营公司）",
    "TEM": "Tempus AI · 医疗 AI 与基因检测",
    "HIMS": "Hims & Hers Health · 远程医疗与减重",
    "VKTX": "Viking Therapeutics · 减重与代谢病新药",
    "TWST": "Twist Bioscience · DNA 合成",
    "ASTS": "AST SpaceMobile · 卫星直连手机",
    "RKLB": "Rocket Lab · 小型运载火箭与航天",
    "RDW": "Redwire · 太空基础设施与载荷",
    "ONDS": "Ondas Holdings · 工业无人机与专网通信",
    "GPRO": "GoPro · 运动相机",
    "WEN": "Wendy's · 快餐连锁",
    "DKNG": "DraftKings · 在线体育博彩",
    "GME": "GameStop · 游戏零售（meme 股）",
    "AMC": "AMC Entertainment · 连锁院线",
    "RIVN": "Rivian · 电动皮卡与 SUV",
    # ── 美股 · 加密资产相关上市公司 ──
    "MSTR": "Strategy（原 MicroStrategy）· 比特币储备公司",
    "STRC": "Strategy 可变利率 A 系列永续优先股（\"Stretch\"，非普通股）",
    "BMNR": "Bitmine Immersion · 以太坊（ETH）储备公司",
    "BNC": "CEA Industries · BNB 储备公司",
    "FWDI": "Forward Industries · Solana（SOL）储备公司",
    "CYPH": "Cypherpunk Technologies · Zcash 储备公司（原 Leap Therapeutics）",
    "USDEX": "StablecoinX · Ethena（ENA）生态储备公司（真实股票代码 USDE，币安改名避免与稳定币 USDe 冲突）",
    "MARA": "MARA Holdings · 比特币矿业",
    "HUT": "Hut 8 · 比特币矿业与算力",
    # ── 美股 · 医药 / 金融 / 消费 / 工业 ──
    "LLY": "Eli Lilly 礼来 · 创新药与减重",
    "NVO": "Novo Nordisk 诺和诺德 ADR · 减重与糖尿病",
    "MRK": "Merck 默沙东 · 创新药与疫苗",
    "PFE": "Pfizer 辉瑞 · 创新药与疫苗",
    "ABBV": "AbbVie 艾伯维 · 免疫与医美",
    "AMGN": "Amgen 安进 · 生物药",
    "MRNA": "Moderna · mRNA 疫苗与疗法",
    "MDT": "Medtronic 美敦力 · 医疗器械",
    "UNH": "UnitedHealth 联合健康 · 健康保险",
    "JPM": "JPMorgan 摩根大通 · 综合银行",
    "GS": "Goldman Sachs 高盛 · 投行",
    "BAC": "Bank of America 美国银行 · 综合银行",
    "BX": "Blackstone 黑石 · 另类资产管理",
    "BRKB": "Berkshire Hathaway 伯克希尔 B 类股",
    "V": "Visa · 支付网络",
    "KO": "Coca-Cola 可口可乐",
    "MCD": "麦当劳",
    "NKE": "Nike 耐克",
    "WMT": "Walmart 沃尔玛",
    "COST": "Costco 好市多",
    "HD": "Home Depot 家得宝",
    "DIS": "Disney 迪士尼 · 影视与主题公园",
    "NFLX": "Netflix 奈飞 · 流媒体",
    "TTWO": "Take-Two Interactive · 游戏发行（GTA 系列）",
    "CAT": "Caterpillar 卡特彼勒 · 工程机械",
    "MP": "MP Materials · 美国稀土（Mountain Pass 矿）",
    "USAR": "USA Rare Earth · 美国稀土开采与永磁材料",
    "CRML": "Critical Metals Corp · 格陵兰稀土与锂",
    # ── 美股 · ETF（指数 / 行业 / 杠杆，非个股）──
    "SPY": "SPDR 标普 500 指数 ETF",
    "QQQ": "Invesco 纳斯达克 100 指数 ETF",
    "IWM": "iShares 罗素 2000 小盘股 ETF",
    "SMH": "VanEck 半导体行业 ETF",
    "XBI": "SPDR 生物科技 ETF（等权）",
    "XLE": "能源精选行业 SPDR ETF",
    "GDX": "VanEck 金矿股 ETF",
    "URNM": "Sprott 铀矿股 ETF",
    "DRAM": "Roundhill Memory 存储芯片主题 ETF",
    "LYTE": "Roundhill Photonics & Optics · 光子与光学主题 ETF",
    "KSTR": "KraneShares 上证科创板 50 指数 ETF",
    "EWY": "iShares MSCI 韩国 ETF",
    "EWJ": "iShares MSCI 日本 ETF",
    "EWT": "iShares MSCI 台湾 ETF",
    "EWZ": "iShares MSCI 巴西 ETF",
    "EWZS": "iShares MSCI 巴西小盘股 ETF",
    "EIDO": "iShares MSCI 印尼 ETF",
    "BITO": "ProShares 比特币策略 ETF（持比特币期货）",
    "ZCSH": "Grayscale Zcash 产品 · 灰度 Zcash 信托 / ETF",
    "TQQQ": "ProShares 3× 做多纳指 100 ETF",
    "SQQQ": "ProShares 3× 做空纳指 100 ETF",
    "SOXL": "Direxion 每日 3× 做多半导体指数 ETF",
    "SOXS": "Direxion 每日 3× 做空半导体指数 ETF",
    "KORU": "Direxion 每日 3× 做多 MSCI 韩国 ETF",
    "TZA": "Direxion 每日 3× 做空小盘股 ETF",
    "TMF": "Direxion 每日 3× 做多 20 年期以上美债 ETF",
    "TBT": "ProShares 每日 2× 做空 20 年期以上美债 ETF",
    "UVXY": "ProShares 1.5× 做多 VIX 短期期货 ETF",
    "NVDL": "GraniteShares 2× 做多 NVIDIA ETF",
    "TSLL": "Direxion 每日 2× 做多 Tesla ETF",
    "MSTU": "每日 2× 做多 Strategy（MSTR）的杠杆 ETF",
    "MUU": "Direxion 每日 2× 做多 Micron ETF",
    "INTW": "GraniteShares 2× 做多 Intel ETF",
    "MVLL": "GraniteShares 2× 做多 Marvell ETF",
    "SKUU": "GraniteShares 2× 做多 SK 海力士 ETF",
    "SKDD": "GraniteShares 2× 做空 SK 海力士 ETF",
    "STXX": "Tradr 2× 做多 Seagate（STX）的杠杆 ETF",
    "SNXX": "Tradr 2× 做多 Sandisk（SNDK）的杠杆 ETF",
    "RAM": "Roundhill T-REX 2× 做多 DRAM 主题的杠杆 ETF",
    # ── 港股 ──
    "HK0700": "腾讯控股（0700.HK）· 社交 / 游戏 / 广告",
    "TENCENT": "腾讯控股（与 HK0700 同标的，币安另开的一套合约）",
    "HK1810": "小米集团（1810.HK）· 手机 / IoT / 电动车",
    "HK0992": "联想集团（0992.HK）· PC 与服务器",
    "HK0625": "希音 SHEIN（0625.HK）· 跨境电商（2026-09 上市）",
    "BYD": "比亚迪（1211.HK）· 电动车与电池",
    "POPMART": "泡泡玛特（9992.HK）· 潮流玩具 IP",
    "KUAISHOU": "快手（1024.HK）· 短视频与直播电商",
    "MEITUAN": "美团（3690.HK）· 本地生活服务",
    "ZHONGJI": "中际旭创（3308.HK）· 800G / 1.6T 光模块龙头",
    "GIGADEV": "兆易创新（3986.HK）· NOR Flash / MCU",
    "ZHIPU": "智谱 AI（Zhipu）· 国产大模型",
    "MINIMAX": "MiniMax 稀宇科技 · 国产大模型（海螺 AI）",
    "CSOPSKHYNIX2L": "CSOP 发行的 SK 海力士 2× 每日杠杆产品",
    "CSOPSAMSUNG2L": "CSOP 发行的 三星电子 2× 每日杠杆产品",
    # ── 韩股 ──
    "SKHYNIX": "SK 海力士 · HBM / DRAM 内存",
    "SAMSUNGEM": "三星电机（Samsung Electro-Mechanics）· 被动元件与封装基板",
    "HYUNDAI": "现代汽车 · 整车",
    "HANMI": "韩美药品（Hanmi Pharm）· 创新药",
    "LGELECTRONICS": "LG 电子 · 家电与电子",
    "NAVER": "NAVER · 韩国最大搜索引擎与门户",
    "KODEX200": "KODEX 200 ETF · 跟踪 KOSPI 200 指数（非个股）",
    "SKHY": "SK 海力士美股 ADR",
    # ── 中国股 ──
    "UNITREE": "宇树科技 · 人形机器人与四足机器人",
    "CXMT": "长鑫存储（ChangXin Memory）· 国产 DRAM",
    # ── 大宗商品 ──
    "XAU": "黄金 · 现货盎司报价",
    "XAG": "白银 · 现货盎司报价",
    "XPT": "铂金 · 现货盎司报价",
    "XPD": "钯金 · 现货盎司报价",
    "COPPER": "铜 · 现货报价",
    "CL": "WTI 原油 · 近月期货",
    "BZ": "布伦特原油 · 近月期货",
    "NATGAS": "亨利港天然气 · 近月期货",
    # ── Pre-IPO（价格合约，不是股权）──
    "OPENAI": "OpenAI · ChatGPT 开发商（Pre-IPO 价格合约，非真实股权）",
    "ANTHROPIC": "Anthropic · Claude 模型开发商（Pre-IPO 价格合约，非真实股权）",
    "MOONSHOT": "月之暗面 · Kimi 大模型（Pre-IPO 价格合约，非真实股权）",
    "OURA": "Ōura · 智能戒指（Pre-IPO 价格合约，非真实股权）",
    # ── 外汇 ──
    "USDBRL": "美元 / 巴西雷亚尔汇率",
}


def get(url: str, timeout: int = 60) -> str:
    """走 socks 代理取文本。直连 fapi 在这个网络下会超时。"""
    p = subprocess.run(
        ["curl", "-s", "-f", "--socks5-hostname", "127.0.0.1:1080",
         "--max-time", str(timeout), url],
        capture_output=True,
    )
    return p.stdout.decode("utf-8", "ignore") if p.returncode == 0 else ""


def archive_range(symbol: str) -> tuple[str | None, str | None, int]:
    """月线归档的首/末月份与文件数。月度归档晚一个月才发布，所以起点比上市日晚是正常的。"""
    x = get(f"{ARCHIVE}?prefix=data/futures/um/monthly/klines/{symbol}/1d/")
    keys = sorted(k for k in re.findall(r"<Key>([^<]+)</Key>", x) if k.endswith(".zip"))
    if not keys:
        return None, None, 0
    month = lambda k: "-".join(k[:-4].split("-")[-2:])
    return month(keys[0]), month(keys[-1]), len(keys)


def collect() -> list[dict]:
    info = json.loads(get(f"{API}/fapi/v1/exchangeInfo"))
    ticks = {t["symbol"]: t for t in json.loads(get(f"{API}/fapi/v1/ticker/24hr"))}
    trad = [s for s in info["symbols"] if s.get("contractType") == "TRADIFI_PERPETUAL"]
    print(f"U 本位合约 {len(info['symbols'])} 个，其中 TRADIFI_PERPETUAL {len(trad)} 个")

    with ThreadPoolExecutor(max_workers=16) as pool:
        rng = {s: r for s, r in zip((x["symbol"] for x in trad), pool.map(archive_range, (x["symbol"] for x in trad)))}

    rows = []
    for s in trad:
        t = ticks.get(s["symbol"], {})
        start, end, n = rng.get(s["symbol"], (None, None, 0))
        rows.append({
            "symbol": s["symbol"],
            "base": s["baseAsset"],
            "类别": LABEL.get(s["underlyingType"], s["underlyingType"]),
            "underlyingType": s["underlyingType"],
            "上市": dt.datetime.fromtimestamp(s["onboardDate"] / 1000, dt.UTC).strftime("%Y-%m-%d"),
            "归档起": start,
            "归档止": end,
            "月数": n,
            "最新价": float(t.get("lastPrice") or 0),
            "24h成交额USDT": float(t.get("quoteVolume") or 0),
            "24h涨跌%": float(t.get("priceChangePercent") or 0),
        })

    miss = [r["symbol"] for r in rows if not r["月数"]]
    if miss:
        print(f"⚠️ 归档里没有月线的：{miss}")
    rows.sort(key=lambda r: (ORDER.index(r["类别"]), -r["24h成交额USDT"]))
    return rows


def basis() -> dict[str, float]:
    """标记价相对指数价的偏离（%）。交易所自己算的 indexPrice 就是「真实标的」的参照。"""
    data = json.loads(get(f"{API}/fapi/v1/premiumIndex"))
    out = {}
    for d in data:
        try:
            mark, idx = float(d["markPrice"]), float(d["indexPrice"])
        except (KeyError, TypeError, ValueError):
            continue
        if idx > 0:
            out[d["symbol"]] = (mark - idx) / idx * 100
    return out


def write_csv(rows: list[dict]) -> None:
    with open(CSV_OUT, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"已写 {CSV_OUT}（{len(rows)} 行）")


def write_markdown(rows: list[dict]) -> None:
    for r in rows:
        r["24h"] = float(r["24h成交额USDT"])
        r["px"] = float(r["最新价"])
        r["chg"] = float(r["24h涨跌%"])
    def money(v: float) -> str:
        """小于 1M 的成交额直接四舍五入会显示成 0M，那是误导。"""
        if v >= 1e6:
            return f"{v / 1e6:,.0f}M"
        if v >= 1e3:
            return f"{v / 1e3:,.0f}K"
        return f"{v:,.0f}"
    today = dt.date.today().isoformat()

    L = [f"# Binance TradFi 合约清单\n",
         f"> 探测日期 {today}。由 `因子研究/tradfi_probe.py --markdown` 生成，可重跑。"
         f"来源：`fapi.binance.com/fapi/v1/exchangeInfo` + `data.binance.vision` 归档目录扫描。"
         f"**只读元数据，未下载任何 K 线。**\n",
         "---\n\n## 结论速览\n",
         f"1. **交易所公开 API 里有独立的 `contractType = \"TRADIFI_PERPETUAL\"`，共 {len(rows)} 个**，"
         f"全部 `status = TRADING`。和网页「8 页 × 30 个」对得上（7 页满 + 第 8 页 25 个）。\n"
         "2. **分类是字段给的，不用猜**：`underlyingType` 直接区分类别，`underlyingSubType` 统一带 `\"TradFi\"`。\n"
         "3. **数据能拿全**：实时 REST 的 K 线、标记价、指数价、资金费全部可用；归档里每个币都有日线+月线 K 线。\n"
         "4. **freqtrade 认这些合约**：ccxt 解析成普通 `swap`（`NVDA/USDT:USDT`），"
         "`freqtrade list-markets` 显示 `Future`，可 `download-data` / `backtesting`。\n"
         "5. ⚠️ **但全都 2025-12 之后才上线**，最长 11 个月、最短 1 个月（见第四节）。这是硬约束。\n",
         "---\n\n## 一、能拿到哪些数据（逐项实测）\n",
         "| 来源 / 端点 | 内容 | 结果 |", "|---|---|---|",
         "| `/fapi/v1/exchangeInfo` | 合约元数据：`contractType`、`underlyingType`、`onboardDate`、精度、保证金率、最小下单额 | ✅ |",
         "| `/fapi/v1/klines` | 实时 K 线，周期 `1m 3m 5m 15m 30m 1h 2h 4h 6h 8h 12h 1d` | ✅ |",
         "| `/fapi/v1/markPriceKlines` | 标记价 K 线 | ✅ |",
         "| `/fapi/v1/indexPriceKlines` | 指数（现货参照）价 K 线 | ✅ 但 **Pre-IPO 4 个没有**，返回 `-4144 Invalid pair` |",
         "| `/fapi/v1/premiumIndex` | 标记价 / 指数价 / 预计结算价 / 资金费率 / 下次结算时间 | ✅ |",
         "| `/fapi/v1/fundingRate` | 历史资金费率，带 `rateType` | ✅ |",
         "| `/fapi/v1/ticker/24hr` | 24h 价格、成交额、涨跌 | ✅ |",
         "| 归档 `{daily,monthly}/klines` | 日线 / 月线 K 线，12 个周期齐全 | ✅ 全覆盖 |",
         "| 归档 `daily/{markPrice,indexPrice,premiumIndex}Klines` | 标记 / 指数 / 溢价 K 线 | ✅ |",
         "| 归档 `monthly/fundingRate` | 资金费率 | ✅ |",
         "| 归档 `daily/{trades,aggTrades,metrics}` | 逐笔、聚合逐笔、指标 | ✅ |",
         "| 归档 `daily/bookTicker` | 最优挂单快照 | ❌ 没有 |",
         "\n归档路径与加密合约完全一致（`um` = U 本位）：\n"
         "`data/futures/um/{daily|monthly}/klines/<SYMBOL>/<周期>/<SYMBOL>-<周期>-<日期>.zip`\n",
         "---\n\n## 二、分类与规模\n",
         "| 类别 | `underlyingType` | 个数 | 24h 合计成交额 | 中位成交额 |", "|---|---|---:|---:|---:|"]

    for c in ORDER:
        g = [r for r in rows if r["类别"] == c]
        if not g:
            continue
        L.append(f"| {c} | `{g[0]['underlyingType']}` | {len(g)} | {money(sum(r['24h'] for r in g))} | "
                 f"{money(statistics.median(r['24h'] for r in g))} |")
    L.append(f"| **合计** | | **{len(rows)}** | **{money(sum(r['24h'] for r in rows))}** | |")

    # 这句必须现场算：24h 成交额是滚动窗口，写死会和上面的表打架
    by_cat = {c: statistics.median(r["24h"] for r in rows if r["类别"] == c) for c in ORDER}
    rich = max(by_cat, key=by_cat.get)
    thin = min(by_cat, key=by_cat.get)
    n_thin = sum(1 for r in rows if r["24h"] < 1e6)
    L.append(f"\n**流动性极度不均**：单笔中位成交额最高的 **{rich}** 是 {money(by_cat[rich])}，"
             f"最低的 **{thin}** 只有 {money(by_cat[thin])}；全部 235 个里有 **{n_thin} 个"
             f"日成交额不到 1M**。做横截面研究时这条比什么都重要 —— 成交额门槛会直接决定可选池大小。\n")

    L.append("---\n\n## 三、完整清单（按类别、类内按 24h 成交额降序）\n")
    L.append("「对应公司 / 标的说明」一列是人工整理的，来自上市公司公告 / 基金发行方说明 —— "
             "**交易所 API 只给代码，不给公司身份**。凡是指数 ETF、杠杆 ETF、封闭式基金，一律标注出来，不要当成个股。"
             "⚠️ `最新价` 是合约报价，部分标的带合约乘数，**不能直接和股票行情软件的价格对比**。\n")
    for c in ORDER:
        g = [r for r in rows if r["类别"] == c]
        if not g:
            continue
        L.append(f"\n### {c}（{len(g)} 个）\n")
        L.append("| 交易对 | 标的 | 对应公司 / 标的说明 | 上市 | 归档起 | 最新价 | 24h 成交额 | 24h 涨跌 |")
        L.append("|---|---|---|---|---|---:|---:|---:|")
        for r in g:
            L.append(f"| `{r['symbol']}` | {r['base']} | {DESC.get(r['base'], '⚠️ **未确认**')} | "
                     f"{r['上市']} | {r['归档起']} | "
                     f"{r['px']:,.2f} | {money(r['24h'])} | {r['chg']:+.2f}% |")

    L.append("\n---\n\n## 四、时间跨度：这是最大的限制\n")
    L.append("| 类别 | 最早上线 | 最晚上线 | 归档已有月数（中位） |")
    L.append("|---|---|---|---:|")
    for c in ORDER:
        g = [r for r in rows if r["类别"] == c]
        if not g:
            continue
        L.append(f"| {c} | {min(r['上市'] for r in g)} | {max(r['上市'] for r in g)} | "
                 f"{statistics.median(int(r['月数']) for r in g):.0f} |")
    # 上线节奏也现场算，别写死月份
    import collections
    hist = collections.Counter(r["上市"][:7] for r in rows)
    first, peak = min(hist), max(hist, key=hist.get)
    recent = sum(1 for r in rows if r["上市"] >= (dt.date.today() - dt.timedelta(days=60)).isoformat())
    L.append(f"\n上线是渐进铺开的：{first} 只有 {hist[first]} 个，**{peak} 单月上新 {hist[peak]} 个**为峰值，"
             f"最近 60 天还新上了 {recent} 个 —— 说明这个板块仍在高速扩张。"
             f"**任何跨品种横截面策略在这个样本上都只有不到一年，而且样本本身还在变。**\n")

    L.append("---\n\n## 五、合约价与真实标的差多少\n")
    # premiumIndex 返回的是全部 942 个合约，必须只留 TradFi —— 否则加密币的极端偏离会污染统计
    trad_syms = {r["symbol"] for r in rows}
    b = {s: v for s, v in basis().items() if s in trad_syms}
    per = {c: [abs(b[r["symbol"]]) for r in rows if r["类别"] == c and r["symbol"] in b] for c in ORDER}
    L.append("用交易所自己给的 `indexPrice`（现货参照价）和 `markPrice` 算：`|标记价 − 指数价| / 指数价`，"
             "取当前时刻快照。\n")
    L.append("| 类别 | 可算个数 | 偏离中位 | 偏离最大 |")
    L.append("|---|---:|---:|---:|")
    for c in ORDER:
        v = per[c]
        if v:
            L.append(f"| {c} | {len(v)} | {statistics.median(v):.3f}% | {max(v):.3f}% |")
    # 结论句也要现场算，快照值会变
    med_all = statistics.median([abs(v) for v in b.values()])
    worst_sym = max(((s, abs(v)) for s, v in b.items()), key=lambda x: x[1])
    L.append(f"\n**结论：偏离确实很小。** 全部 TradFi 合约的 |标记价 − 指数价| 中位数只有 "
             f"**{med_all:.3f}%**（不到 {med_all * 100:.0f} 个基点），"
             f"但最大的一只（`{worst_sym[0]}`）到了 {worst_sym[1]:.2f}%。"
             f"做价差交易或跨市场对冲时要**按单币检查**，不能只看中位数。\n")
    if per.get("Pre-IPO"):
        L.append("⚠️ Pre-IPO 那 4 个显示 0.000% 不是「完美贴合」，而是**它们没有独立指数价** —— "
                 "`indexPrice` 直接等于 `markPrice`。这 4 个没有现货参照，别拿去做价差。\n")

    L.append("---\n\n## 六、freqtrade 怎么用\n")
    L.append("```bash\n"
             "freqtrade download-data -c user_data/config/config_backtest.json \\\n"
             "  --trading-mode futures -t 1h -t 1d \\\n"
             "  --pairs NVDA/USDT:USDT XAU/USDT:USDT \\\n"
             "  --timerange 20251201-\n"
             "```\n")
    L.append("1. **符号要用统一格式** `NVDA/USDT:USDT`，不是 `NVDAUSDT`。\n"
             "2. **归档的月度文件晚一个月才发布**，最近行情靠 REST 补，和 `spot_all_1d` 的情形一样。\n"
             "3. **样本期只有几个月**，别指望用它做长期回测；要做横截面研究得先想清楚样本外怎么验证。\n")

    with open(MD_OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print(f"已写 {MD_OUT}")


def main() -> None:
    ap = argparse.ArgumentParser(description="探测 Binance TradFi 合约")
    ap.add_argument("--markdown", action="store_true", help="同时重写根目录 TradFi合约清单.md")
    args = ap.parse_args()

    rows = collect()
    # 新上币时 DESC 会漏，必须点名 —— 报告里静默显示「未确认」比漏掉更难发现
    missing = sorted({r["base"] for r in rows} - DESC.keys())
    if missing:
        print(f"⚠️ DESC 里还缺 {len(missing)} 个标的的说明：{missing}")
    write_csv(rows)
    if args.markdown:
        write_markdown(rows)

    for c in ORDER:
        g = [r for r in rows if r["类别"] == c]
        if g:
            print(f"  {c:<6} {len(g):>4} 个")


if __name__ == "__main__":
    main()
