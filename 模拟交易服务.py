# -*- coding: utf-8 -*-
"""
国内期货模拟交易终端 · 后端服务（纯标准库，零第三方依赖）

能力：
  · 行情：东方财富期货行情接口实时抓取全市场真实合约行情，后台定时刷新 + 内存缓存；
          网络不可用时自动回退到「快照/contracts.json」离线快照，页面标注数据来源。
  · 资金：模拟入金 / 出金，逐笔记录流水；账户状态落盘，重启不丢。
  · 下单：买入(多) / 卖出(空) × 开仓 / 平仓 × 市价 / 限价；
          支持「平今 / 平昨 / 自动」三种平仓方式；限价单由撮合线程在行情触发时成交。
  · 风控：保证金占用、逐笔浮动盈亏、风险度（保证金/动态权益）、可开手数、
          自动强平（触及强平线按亏损从大到小强平）。
  · 结算：手续费按品种参数表计提（元/手 或 成交额比例），平今费率单独计算。

口径说明（模拟盘简化，页脚「口径说明」同步展示）：
  · 保证金按「最新价」实时盯市计算（真实交易所以结算价逐日盯市）。
  · 浮动盈亏在持仓期间累计展示，平仓时一次性转出为已实现盈亏。
  · 交易日按本机自然日切换，「今仓」在跨日后自动转为「昨仓」。

用法：
    python 模拟交易服务.py               # 默认端口 8908
    python 模拟交易服务.py --port 9000   # 指定端口
    python 模拟交易服务.py --no-browser  # 不自动打开浏览器
"""
import argparse
import atexit
import json
import os
import re
import socket
import ssl
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

# ----------------------------------------------------------------------
# 路径与常量（源码运行 / 打包 exe 双兼容，详见 路径工具.py）
# ----------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import 路径工具 as PATHS                                          # noqa: E402

BASE_DIR = PATHS.APP_DIR                    # 可写数据基准目录（打包后 = exe 所在目录）
PARAM_FILE = PATHS.res("品种参数.json")      # 只读资源（exe 旁放同名文件可覆盖）
SNAP_DIR = PATHS.res("快照")
SNAP_CONTRACTS = os.path.join(SNAP_DIR, "contracts.json")
STATE_DIR = PATHS.data("账户数据")           # 可写数据（不会随程序退出被删除）
STATE_FILE = os.path.join(STATE_DIR, "account.json")
PAGE = "交易面板.html"

PORT = 8908
PROBE_PORTS = 20
REFRESH_SEC = 30          # 行情后台刷新间隔（秒）
TICK_SEC = 2              # 撮合 / 风控巡检间隔（秒）

if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
import 行情K线 as KLINE_MOD                                       # noqa: E402
import 策略引擎 as STRAT_MOD                                      # noqa: E402
import 回测引擎 as BT_MOD                                         # noqa: E402
import 资金管理 as MM_MOD                                         # noqa: E402
import 账户引擎                                                   # noqa: E402

ssl._create_default_https_context = ssl._create_unverified_context
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

MARKETS = [
    ("113", "SHFE",  "上海期货交易所",     "上期所"),
    ("114", "DCE",   "大连商品交易所",     "大商所"),
    ("115", "CZCE",  "郑州商品交易所",     "郑商所"),
    ("225", "GFEX",  "广州期货交易所",     "广期所"),
    ("142", "INE",   "上海国际能源交易中心", "上期能源"),
    ("220", "CFFEX", "中国金融期货交易所",  "中金所"),
    ("8",   "CFFEX", "中国金融期货交易所",  "中金所"),
]
MARKET_NAME = {"113": "上期所", "114": "大商所", "115": "郑商所", "225": "广期所",
               "142": "上期能源", "220": "中金所", "8": "中金所"}

SECTOR_ORDER = ["黑色建材", "有色金属", "贵金属", "能源化工", "农产品", "金融期货", "航运指数", "其他"]

# 品种 -> 板块
SECTOR_MAP = {
    "RB": "黑色建材", "HC": "黑色建材", "I": "黑色建材", "J": "黑色建材",
    "JM": "黑色建材", "SF": "黑色建材", "SM": "黑色建材", "SS": "黑色建材",
    "WR": "黑色建材", "ZC": "黑色建材", "FG": "黑色建材", "SA": "黑色建材",
    "CU": "有色金属", "AL": "有色金属", "ZN": "有色金属", "PB": "有色金属",
    "NI": "有色金属", "SN": "有色金属", "AO": "有色金属", "BC": "有色金属",
    "SI": "有色金属", "LC": "有色金属", "PS": "有色金属", "AD": "有色金属",
    "AU": "贵金属", "AG": "贵金属", "PT": "贵金属", "PD": "贵金属",
    "SC": "能源化工", "FU": "能源化工", "LU": "能源化工", "BU": "能源化工",
    "RU": "能源化工", "NR": "能源化工", "BR": "能源化工", "L": "能源化工",
    "PP": "能源化工", "V": "能源化工", "EG": "能源化工", "EB": "能源化工",
    "PG": "能源化工", "MA": "能源化工", "TA": "能源化工", "PX": "能源化工",
    "PF": "能源化工", "PR": "能源化工", "UR": "能源化工", "SP": "能源化工",
    "SH": "能源化工", "WH": "能源化工", "PL": "能源化工", "BZ": "能源化工",
    "OP": "能源化工",
    "M": "农产品", "Y": "农产品", "A": "农产品", "B": "农产品", "P": "农产品",
    "C": "农产品", "CS": "农产品", "JD": "农产品", "LH": "农产品", "RR": "农产品",
    "SR": "农产品", "CF": "农产品", "CY": "农产品", "OI": "农产品", "RM": "农产品",
    "RS": "农产品", "AP": "农产品", "CJ": "农产品", "PK": "农产品", "JR": "农产品",
    "RI": "农产品", "LR": "农产品", "PM": "农产品", "BB": "农产品", "FB": "农产品",
    "LG": "农产品",
    "IF": "金融期货", "IH": "金融期货", "IC": "金融期货", "IM": "金融期货",
    "T": "金融期货", "TF": "金融期货", "TS": "金融期货", "TL": "金融期货",
    "EC": "航运指数",
}

# 夜盘收盘时间（相对当日 00:00 的分钟数；跨日则 > 1440）。
# 口径来源：东方财富期货「交易时间」 https://qhweb.eastmoney.com/tradinghours
# 未列出的品种即无夜盘（郑商所尿素/锰硅/硅铁/苹果/红枣/花生/稻麦菜籽类，大商所鸡蛋/生猪/原木/胶合板/纤维板，
# 上期所线材，上期能源欧线集运，广期所全部，中金所全部）。
NIGHT_END = {
    # 21:00 ~ 次日 01:00
    "CU": 1500, "AL": 1500, "ZN": 1500, "PB": 1500, "NI": 1500, "SN": 1500,
    "SS": 1500, "BC": 1500,
    # 21:00 ~ 次日 02:30
    "AU": 1590, "AG": 1590, "SC": 1590,
    # 21:00 ~ 23:00
    "RB": 1380, "HC": 1380, "RU": 1380, "BU": 1380, "FU": 1380, "SP": 1380,
    "BR": 1380, "AO": 1380, "AD": 1380, "OP": 1380, "NR": 1380, "LU": 1380,
    "A": 1380, "B": 1380, "M": 1380, "Y": 1380, "P": 1380, "C": 1380,
    "CS": 1380, "I": 1380, "J": 1380, "JM": 1380, "L": 1380, "V": 1380,
    "PP": 1380, "EG": 1380, "EB": 1380, "PG": 1380, "BZ": 1380, "RR": 1380,
    "L_F": 1380, "PP_F": 1380, "V_F": 1380,
    "SR": 1380, "CF": 1380, "CY": 1380, "OI": 1380, "RM": 1380, "MA": 1380,
    "TA": 1380, "FG": 1380, "SA": 1380, "PX": 1380, "PF": 1380, "PR": 1380,
    "SH": 1380, "ZC": 1380, "PL": 1380,
}
NIGHT_VARIETIES = set(NIGHT_END)
# 参数表「夜盘」字段取值 -> 收盘分钟数
NIGHT_CODE_MIN = {"2300": 1380, "0100": 1500, "0230": 1590}
# 中金所：股指与国债日盘时段不同
CFFEX_INDEX = {"IF", "IH", "IC", "IM"}


def night_end(variety):
    """夜盘收盘分钟数；无夜盘返回 None。优先取品种参数表，回退内置表。"""
    v = PARAMS.variety.get(variety)
    if v is not None and "夜盘" in v:
        return NIGHT_CODE_MIN.get(v.get("夜盘") or "")
    return NIGHT_END.get(variety)


DEFAULT_SETTINGS = {
    "保证金倍数": 1.0,      # 在东方财富公司保证金率基础上的整体倍数
    "手续费折扣": 1.0,      # 手续费整体折扣（1.0 = 东方财富公司手续费标准）
    "滑点跳数": 0,          # 市价单成交价滑点（以最小变动价位为单位）
    "自动强平": True,
    "强平线": 1.0,          # 风险度 = 占用保证金 / 动态权益，达到该值触发强平
    "仅交易时段下单": False,
    "涨跌停校验": True,      # 拒绝超出当日涨跌停板的委托价（东方财富「每日涨跌幅度」）
}


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today_str():
    return datetime.now().strftime("%Y-%m-%d")


def fnum(x, nd=2):
    """安全取数。"""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if v != v:                      # NaN
        return None
    return round(v, nd)


# ----------------------------------------------------------------------
# 品种参数
# ----------------------------------------------------------------------
class Params(object):
    def __init__(self):
        self.raw = {}
        self.variety = {}
        self.default_mult = 10.0
        self.default_rate = 0.10
        self.default_tick = 1.0
        self.load()

    def load(self):
        try:
            with open(PARAM_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
            self.raw = d
            self.variety = d.get("品种", {})
        except Exception as e:                              # noqa: BLE001
            print("[warn] 品种参数.json 读取失败，使用内置兜底参数：%s" % e)
            self.variety = {}

    def get(self, variety, fallback_name=""):
        v = self.variety.get(variety)
        if v:
            return {
                "variety": variety,
                "name": v.get("名称") or fallback_name or variety,
                "exchCN": v.get("交易所") or "",
                "mult": float(v.get("合约乘数") or self.default_mult),
                "tick": float(v.get("最小变动价位") or self.default_tick),
                "marginRate": float(v.get("保证金率") or self.default_rate),
                "limitPct": float(v.get("涨跌停板") or 0),
                "months": v.get("合约月份") or "",
                "lastDay": v.get("最后交易日") or "",
                "unit": v.get("单位") or "吨/手",
                "night": v.get("夜盘") or "",
                "fee": v.get("手续费") or {"模式": "按成交额", "开仓": 0.0001, "平仓": 0.0001, "平今": 0.0001},
                "known": True,
            }
        return {
            "variety": variety,
            "name": fallback_name or variety,
            "exchCN": "",
            "mult": self.default_mult,
            "tick": self.default_tick,
            "marginRate": self.default_rate,
            "limitPct": 0,
            "months": "",
            "lastDay": "",
            "unit": "吨/手（估算）",
            "night": "",
            "fee": {"模式": "按成交额", "开仓": 0.0001, "平仓": 0.0001, "平今": 0.0001},
            "known": False,
        }

    def fee_info(self, variety):
        """该品种东方财富公司手续费标准（开/平/今），用于前端展示。"""
        f = self.get(variety)["fee"]
        return {
            "mode": f.get("模式") or "按成交额",
            "开仓": float(f.get("开仓") or 0),
            "平仓": float(f.get("平仓") or 0),
            "平今": float(f.get("平今") or 0),
        }

    def margin_rate(self, variety, settings):
        p = self.get(variety)
        return p["marginRate"] * float(settings.get("保证金倍数", 1.0))

    def fee(self, variety, price, lots, action, settings):
        """action: open / close / closeToday"""
        p = self.get(variety)
        f = p["fee"]
        mode = f.get("模式") or "按成交额"
        if mode == "按手数":
            key = {"open": "开仓", "close": "平仓", "closeToday": "平今"}[action]
            amt = float(f.get(key, 0) or 0) * lots
        else:
            key = {"open": "开仓", "close": "平仓", "closeToday": "平今"}[action]
            rate = float(f.get(key, 0) or 0)
            amt = price * p["mult"] * lots * rate
        return round(amt * float(settings.get("手续费折扣", 1.0)), 2)


PARAMS = Params()

# ----------------------------------------------------------------------
# 行情 K 线 / 策略 / 回测 三大引擎
# ----------------------------------------------------------------------
KLINE = KLINE_MOD.KLINE
STRATS = STRAT_MOD.StrategyEngine(PARAMS)
REPLAYS = BT_MOD.ReplayManager()

DEFAULT_MAIN = ["MA(5,10,20,60)"]
DEFAULT_SUB = ["VOL(5,10,20)", "MACD(12,26,9)"]
BACKTEST_LOG = []            # 最近的回测记录（内存，最多 40 条）


def mm_state():
    """给「资金管理」模块用的实盘账户状态快照。

    权益峰值取权益曲线最大值；连亏笔数按最近已平仓笔数统计；
    日内基准取当前交易日第一笔权益采样。
    """
    try:
        snap = ACCOUNT.snapshot()
    except Exception:                                         # noqa: BLE001
        snap = {}
    s = ACCOUNT.state
    curve = s.get("equityCurve") or []
    eq = float(snap.get("equity") or 0)
    peak = eq
    for c in curve:
        try:
            v = float(c.get("equity") or 0)
        except (TypeError, ValueError):
            continue
        if v > peak:
            peak = v
    today = now_str()[:10]
    day0 = None
    for c in curve:
        if str(c.get("ts") or "")[:10] == today:
            day0 = float(c.get("equity") or 0)
            break
    closed = [t for t in (s.get("trades") or []) if t.get("pnl") is not None]
    wins = [t for t in closed if float(t.get("pnl") or 0) > 0]
    gw = sum(float(t.get("pnl") or 0) for t in wins)
    gl = abs(sum(float(t.get("pnl") or 0) for t in closed
                 if float(t.get("pnl") or 0) <= 0))
    consec = 0
    for t in closed:                       # trades 最新在前
        if float(t.get("pnl") or 0) <= 0:
            consec += 1
        else:
            break
    return {
        "equity": eq, "peak": max(peak, eq),
        "cash": float(snap.get("available") or snap.get("cash") or 0),
        "marginUsed": float(snap.get("margin") or 0),
        "consecLoss": consec, "wins": len(wins), "closed": len(closed),
        "grossWin": gw, "grossLoss": gl,
        "dayEquity0": day0 or eq,
        "settings": dict(s.get("settings") or {}),
        "positions": snap.get("positions") or [],
    }


def spec_of(variety):
    """回测/复盘用的品种规格（含手续费三档）。"""
    v = PARAMS.variety.get(variety) or {}
    fee = v.get("手续费") or {"模式": "按成交额", "开仓": 0.0001,
                              "平仓": 0.0001, "平今": 0.0001}
    return {"合约乘数": float(v.get("合约乘数") or PARAMS.default_mult),
            "最小变动价位": float(v.get("最小变动价位") or PARAMS.default_tick),
            "保证金率": float(v.get("保证金率") or PARAMS.default_rate),
            "手续费": dict(fee),
            "名称": v.get("名称") or variety}


def variety_of(code, fallback=""):
    m = re.match(r"^([A-Za-z]+)", (code or "").strip())
    return (m.group(1) if m else fallback).upper()


def main_contract(variety):
    """该品种主力合约代码（按持仓量、成交量取最大）。"""
    best, score = None, -1
    for c in MARKET.contracts:
        if c.get("variety") != variety or c.get("kind") != "real":
            continue
        q = MARKET.quotes.get(c["code"]) or {}
        s = float(q.get("ccl") or 0) * 1000 + float(q.get("vol") or 0)
        if s > score:
            best, score = c["code"], s
    return best


def split_specs(raw, defaults):
    """'MA(5,10),BOLL(20,2)' / ['MA(5)'] / '' -> 列表"""
    if raw is None or raw == "":
        return list(defaults)
    if isinstance(raw, (list, tuple)):
        return [str(x).strip() for x in raw if str(x).strip()]
    return [x.strip() for x in re.split(r"[;；,，]\s*(?=[A-Za-z])", str(raw))
            if x.strip()]


def slice_bars(bars, start="", end="", warmup=120):
    """按日期截取 K 线；start 之前额外保留 warmup 根用于指标预热。"""
    if not bars:
        return bars, 0
    s, e = (start or "").strip(), (end or "").strip()
    i0, i1 = 0, len(bars) - 1
    if s:
        for k, b in enumerate(bars):
            if (b["d"] or "")[:10] >= s:
                i0 = k
                break
    if e:
        for k in range(len(bars) - 1, -1, -1):
            if (bars[k]["d"] or "")[:10] <= e:
                i1 = k
                break
    if i1 < i0:
        i0, i1 = 0, len(bars) - 1
    cut = max(0, i0 - max(0, int(warmup)))
    return bars[cut:i1 + 1], i0 - cut


def kline_payload(code, period, limit=0, main_specs=None, sub_specs=None,
                  variety="", force=False):
    c = (code or "").strip().upper()
    var = (variety or variety_of(c)).upper()
    d = KLINE.get(c, period, limit=0, variety=var, force=force)
    bars = d["bars"]
    main = KLINE_MOD.compute_main(split_specs(main_specs, DEFAULT_MAIN), bars)
    subs = KLINE_MOD.compute_sub(split_specs(sub_specs, DEFAULT_SUB), bars)
    lim = int(limit or 0)
    if lim > 0 and len(bars) > lim:
        cut = len(bars) - lim
        bars = bars[cut:]
        for it in main + subs:
            for ln in it["lines"]:
                ln["data"] = ln["data"][cut:]
            if it.get("bars"):
                it["bars"]["data"] = it["bars"]["data"][cut:]
    info = PARAMS.variety.get(var) or {}
    return {
        "ok": True, "code": c, "variety": var,
        "name": info.get("名称") or var,
        "exchange": info.get("交易所") or "",
        "period": period, "symbol": d.get("symbol"),
        "source": d.get("source"), "fetchedAt": d.get("fetchedAt"),
        "fallback": d.get("fallback", False), "stale": d.get("stale", False),
        "error": d.get("error", ""),
        "count": len(bars), "total": len(d["bars"]),
        "bars": bars, "main": main, "sub": subs,
        "spec": {"合约乘数": info.get("合约乘数"),
                 "最小变动价位": info.get("最小变动价位"),
                 "保证金率": info.get("保证金率"),
                 "手续费": info.get("手续费"),
                 "单位": info.get("单位")},
    }


def contract_label(code):
    """给一个代码，返回更友好的名字。"""
    c = MARKET.by_code.get((code or "").upper())
    if c:
        return c.get("name") or code
    v = PARAMS.variety.get(variety_of(code)) or {}
    return v.get("名称") or code


# ----------------------------------------------------------------------
# 行情引擎
# ----------------------------------------------------------------------
def http_get(url, referer="", timeout=20, retries=2):
    headers = {"User-Agent": UA, "Accept": "*/*"}
    if referer:
        headers["Referer"] = referer
    last = None
    for i in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            return _OPENER.open(req, timeout=timeout).read().decode("utf-8", "ignore")
        except Exception as e:                              # noqa: BLE001
            last = e
            time.sleep(0.4 * (i + 1))
    raise last


def detect_kind(name):
    """区分真实月份合约与主连/连续/指数等衍生代码。"""
    n = (name or "").strip()
    if re.search(r"主连|连续|指数|加权|月均", n):
        return "link"
    return "real"


def fetch_market(market_id):
    out, page = [], 0
    while True:
        q = urllib.parse.urlencode({
            "orderBy": "dm", "sort": "asc", "pageIndex": page,
            "pageSize": 500, "_": int(time.time() * 1000),
        })
        url = "https://futsseapi.eastmoney.com/list/%s?%s" % (market_id, q)
        try:
            d = json.loads(http_get(url, "https://qhweb.eastmoney.com/"))
        except Exception:                                   # noqa: BLE001
            break
        lst = d.get("list") or []
        for it in lst:
            it["_market"] = market_id
        out.extend(lst)
        total = d.get("total") or 0
        page += 1
        if len(out) >= total or not lst or page > 8:
            break
    return out


class Market(object):
    """全市场行情缓存。"""

    def __init__(self):
        self.lock = threading.RLock()
        self.contracts = []          # 基础信息（含参数）
        self.by_code = {}            # code.upper() -> 基础信息
        self.quotes = {}             # code.upper() -> 行情 dict
        self.ts = ""                 # 行情时间
        self.source = "未加载"        # 实时 / 离线快照
        self.error = ""
        self._stop = False

    # ---------------- 基础信息 ----------------
    def _base_records(self, raw_list):
        """原始行情 -> 去重后的真实合约基础信息。"""
        rows, seen = [], set()
        for it in raw_list:
            dm = (it.get("dm") or "").strip()
            if not dm:
                continue
            name = (it.get("name") or "").strip()
            if detect_kind(name) != "real":
                continue
            p = re.match(r"^([A-Za-z]+)(\d+)$", dm)
            if not p:
                continue
            variety = p.group(1).upper()
            ym = p.group(2)
            mid = it.get("_market") or ""
            exch, exch_cn = "OTHER", "其他"
            for m in MARKETS:
                if m[0] == mid:
                    exch, exch_cn = m[1], m[2]
                    break
            key = dm.upper()
            if key in seen:
                continue
            seen.add(key)
            info = PARAMS.get(variety)
            rows.append({
                "code": dm.upper(),
                "codeRaw": dm,
                "name": name,
                "kind": "real",
                "kindLabel": "真实合约",
                "variety": variety,
                "varietyName": info["name"],
                "exch": exch,
                "exchName": exch_cn,
                "market": mid,
                "marketName": MARKET_NAME.get(mid, mid),
                "month": ym,
                "sector": SECTOR_MAP.get(variety, "其他"),
                "night": night_end(variety) is not None,
                "mult": info["mult"],
                "tick": info["tick"],
                "marginRate": info["marginRate"],
                "limitPct": info["limitPct"],
                "months": info["months"],
                "lastDay": info["lastDay"],
                "exchange": info["exchCN"] or exch_cn,
                "nightEnd": night_end(variety),
                "session": session_text(variety, exch),
                "unit": info["unit"],
                "fee": info["fee"],
                "paramKnown": info["known"],
            })
        rows.sort(key=lambda r: (r["variety"], r["month"]))
        return rows

    # ---------------- 行情字段 ----------------
    @staticmethod
    def _quote_of(it):
        p = fnum(it.get("p"), 4)
        zjsj = fnum(it.get("zjsj"), 4)
        if p in (None, 0):
            p = zjsj
        return {
            "p": p,
            "zde": fnum(it.get("zde"), 4),
            "zdf": fnum(it.get("zdf"), 4),
            "o": fnum(it.get("o"), 4),
            "h": fnum(it.get("h"), 4),
            "l": fnum(it.get("l"), 4),
            "zjsj": zjsj,
            "jjsj": fnum(it.get("jjsj"), 4),
            "zt": fnum(it.get("zt"), 4),        # 涨停价
            "dt": fnum(it.get("dt"), 4),        # 跌停价
            "vol": int(it.get("vol") or 0),
            "ccl": int(it.get("ccl") or 0),
            "rz": int(it.get("rz") or 0),
            "cje": fnum(it.get("cje"), 0),
            "wp": int(it.get("wp") or 0),
            "np": int(it.get("np") or 0),
        }

    def refresh(self, quiet=False):
        """拉取全市场行情。失败则保留旧数据并记录错误。"""
        try:
            with ThreadPoolExecutor(7) as ex:
                results = list(ex.map(fetch_market, [m[0] for m in MARKETS]))
            raw = []
            for r in results:
                raw.extend(r)
            if not raw:
                raise RuntimeError("接口未返回任何数据")
            rows = self._base_records(raw)
            if not rows:
                raise RuntimeError("未解析到真实合约")
            quotes = {}
            for it in raw:
                dm = (it.get("dm") or "").strip().upper()
                if dm:
                    quotes[dm] = self._quote_of(it)
            with self.lock:
                self.contracts = rows
                self.by_code = {r["code"]: r for r in rows}
                self.quotes = quotes
                self.ts = now_str()
                self.source = "东方财富实时行情"
                self.error = ""
            if not quiet:
                print("[行情] 已刷新 %d 个真实合约　%s" % (len(rows), self.ts))
            return True
        except Exception as e:                              # noqa: BLE001
            with self.lock:
                self.error = "%s" % e
            if not quiet:
                print("[行情] 刷新失败：%s" % e)
            return False

    def load_snapshot(self):
        """离线快照兜底。"""
        try:
            with open(SNAP_CONTRACTS, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:                              # noqa: BLE001
            print("[快照] 读取失败：%s" % e)
            return False
        rows, quotes = [], {}
        for it in data:
            if it.get("kind") != "real":
                continue
            variety = it.get("variety") or ""
            code = (it.get("code") or "").upper()
            if not code:
                continue
            info = PARAMS.get(variety, it.get("varietyName") or "")
            rows.append({
                "code": code,
                "codeRaw": it.get("code"),
                "name": it.get("name") or "",
                "variety": variety,
                "varietyName": it.get("varietyName") or info["name"],
                "exch": it.get("exch") or "",
                "exchName": it.get("exchName") or "",
                "market": it.get("market") or "",
                "marketName": it.get("marketName") or "",
                "month": it.get("month") or "",
                "sector": it.get("sector") or SECTOR_MAP.get(variety, "其他"),
                "night": night_end(variety) is not None,
                "mult": info["mult"],
                "tick": info["tick"],
                "marginRate": info["marginRate"],
                "limitPct": info["limitPct"],
                "months": info["months"],
                "lastDay": info["lastDay"],
                "exchange": info["exchCN"] or it.get("exchName") or "",
                "nightEnd": night_end(variety),
                "session": session_text(variety, it.get("exch") or ""),
                "unit": info["unit"],
                "fee": info["fee"],
                "paramKnown": info["known"],
            })
            quotes[code] = {
                "p": fnum(it.get("p"), 4) or fnum(it.get("zjsj"), 4),
                "zde": fnum(it.get("zde"), 4), "zdf": fnum(it.get("zdf"), 4),
                "o": fnum(it.get("o"), 4), "h": fnum(it.get("h"), 4), "l": fnum(it.get("l"), 4),
                "zjsj": fnum(it.get("zjsj"), 4), "jjsj": fnum(it.get("jjsj"), 4),
                "zt": fnum(it.get("zt"), 4), "dt": fnum(it.get("dt"), 4),
                "vol": int(it.get("vol") or 0), "ccl": int(it.get("ccl") or 0),
                "rz": int(it.get("rz") or 0), "cje": fnum(it.get("cje"), 0),
                "wp": int(it.get("wp") or 0), "np": int(it.get("np") or 0),
            }
        if not rows:
            return False
        rows.sort(key=lambda r: (r["variety"], r["month"]))
        try:
            meta = json.load(open(os.path.join(SNAP_DIR, "meta.json"), "r", encoding="utf-8"))
            snap_date = meta.get("generatedAt") or meta.get("tradeDate") or "未知"
        except Exception:                                   # noqa: BLE001
            snap_date = "未知"
        with self.lock:
            self.contracts = rows
            self.by_code = {r["code"]: r for r in rows}
            self.quotes = quotes
            self.ts = snap_date
            self.source = "离线快照（%s）" % snap_date
        print("[快照] 已载入离线行情 %d 个合约（快照时间 %s）" % (len(rows), snap_date))
        return True

    def get(self, code):
        return self.quotes.get((code or "").upper())

    def price(self, code):
        q = self.get(code)
        if not q:
            return None
        return q.get("p") or q.get("zjsj")

    def loop(self):
        """后台定时刷新。"""
        while not self._stop:
            for _ in range(REFRESH_SEC):
                if self._stop:
                    return
                time.sleep(1)
            self.refresh(quiet=True)


MARKET = Market()


# ----------------------------------------------------------------------
# 账户与撮合
# ----------------------------------------------------------------------
def pos_key(code, direction):
    return "%s|%s" % (code.upper(), direction)


def trade_side(direction, offset):
    """真实买卖方向：开多=买入、开空=卖出、平多=卖出、平空=买入。"""
    if offset == "open":
        return "buy" if direction == "long" else "sell"
    return "sell" if direction == "long" else "buy"


def clamp_limit(price, zt, dt):
    """成交价不允许突破当日涨跌停板（东方财富「每日涨跌幅度」口径）。"""
    if price is None:
        return price
    if zt and price > zt:
        return zt
    if dt and price < dt:
        return dt
    return price


class Account(object):
    def __init__(self):
        self.lock = threading.RLock()
        os.makedirs(STATE_DIR, exist_ok=True)
        self.file = STATE_FILE          # 当前账户的资金状态文件（多账户切换时改变）
        self._lastTxt = None            # 上次落盘内容，用于跳过无变化的重复写盘
        self._loaded = False            # 是否成功从磁盘载入过数据
        self.state = self._blank()
        self.load()

    # ---------------- 状态结构 ----------------
    @staticmethod
    def _blank():
        return {
            "version": 1,
            "createdAt": now_str(),
            "updatedAt": now_str(),
            "tradeDate": today_str(),
            "cash": 0.0,                     # 静态权益（结存）= 入金-出金+已实现盈亏-手续费
            "totalDeposit": 0.0,
            "totalWithdraw": 0.0,
            "realizedPnl": 0.0,
            "totalFee": 0.0,
            "positions": {},
            "orders": [],
            "trades": [],
            "cashFlow": [],
            "equityCurve": [],
            "logs": [],
            "settings": dict(DEFAULT_SETTINGS),
            "seq": 0,
        }

    def load(self):
        try:
            with open(self.file, "r", encoding="utf-8") as f:
                s = json.load(f)
            if isinstance(s, dict) and "cash" in s:
                base = self._blank()
                base.update(s)
                for k, v in DEFAULT_SETTINGS.items():
                    base["settings"].setdefault(k, v)
                base["positions"] = base.get("positions") or {}
                self.state = base
                self._loaded = True
                self._lastTxt = json.dumps(self.state, ensure_ascii=False, separators=(",", ":"))
                print("[账户] 已载入账户数据：%s" % self.file)
        except Exception:                                   # noqa: BLE001
            pass

    def bind(self, uid, state_file):
        """切换到指定账户的资金状态：先把当前账户落盘，再载入新文件。"""
        with self.lock:
            if os.path.abspath(self.file) == os.path.abspath(state_file):
                if not self._loaded:
                    self.load()         # 同一文件但尚未载入（如启动时重复绑定）
                return
            if self._loaded:
                try:
                    self.save()         # 内容没变时 save() 内部会自动跳过
                except Exception:       # noqa: BLE001
                    pass
            self.file = state_file
            self._loaded = False
            self._lastTxt = None
            self.state = self._blank()
            self.load()

    def save(self, force=False):
        """落盘。内容与上次完全一致时跳过写入，便于被定时兜底保存高频调用。"""
        with self.lock:
            try:
                txt = json.dumps(self.state, ensure_ascii=False, separators=(",", ":"))
                if not force and txt == self._lastTxt:
                    return
                tmp = self.file + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    f.write(txt)
                os.replace(tmp, self.file)
                self._lastTxt = txt
            except Exception as e:                          # noqa: BLE001
                print("[warn] 账户落盘失败：%s" % e)

    def next_id(self, prefix):
        self.state["seq"] = int(self.state.get("seq", 0)) + 1
        return "%s%05d" % (prefix, self.state["seq"])

    def log(self, kind, text):
        self.state["logs"].insert(0, {"ts": now_str(), "kind": kind, "text": text})
        del self.state["logs"][300:]

    # ---------------- 资金 ----------------
    def deposit(self, amount, note=""):
        amount = round(float(amount), 2)
        if amount <= 0:
            return False, "入金金额必须大于 0"
        s = self.state
        s["cash"] = round(s["cash"] + amount, 2)
        s["totalDeposit"] = round(s["totalDeposit"] + amount, 2)
        s["cashFlow"].insert(0, {"ts": now_str(), "type": "入金", "amount": amount,
                                 "note": note or "模拟资金注入", "cashAfter": s["cash"]})
        self.log("资金", "入金 ¥%.2f　结存 ¥%.2f" % (amount, s["cash"]))
        self.save()     # 立即落盘：与银行卡余额（users.json 实时保存）保持一致
        return True, "入金 ¥%.2f 成功" % amount

    def withdraw(self, amount, note=""):
        amount = round(float(amount), 2)
        if amount <= 0:
            return False, "出金金额必须大于 0"
        s = self.state
        snap = self.snapshot_locked()
        if amount > snap["available"] + 1e-6:
            return False, "可用资金不足：可用 ¥%.2f，申请出金 ¥%.2f" % (snap["available"], amount)
        s["cash"] = round(s["cash"] - amount, 2)
        s["totalWithdraw"] = round(s["totalWithdraw"] + amount, 2)
        s["cashFlow"].insert(0, {"ts": now_str(), "type": "出金", "amount": -amount,
                                 "note": note or "模拟资金提取", "cashAfter": s["cash"]})
        self.log("资金", "出金 ¥%.2f　结存 ¥%.2f" % (amount, s["cash"]))
        self.save()     # 立即落盘：与银行卡余额（users.json 实时保存）保持一致
        return True, "出金 ¥%.2f 成功" % amount

    # ---------------- 估值 ----------------
    def _last_price(self, code):
        p = MARKET.price(code)
        return p

    def snapshot_locked(self):
        """计算账户实时估值（调用方需持锁）。"""
        s = self.state
        settings = s["settings"]
        pos_out = []
        margin_total = 0.0
        float_total = 0.0
        for k, pos in s["positions"].items():
            if pos["lots"] <= 0:
                continue
            code = pos["code"]
            info = MARKET.by_code.get(code) or {}
            last = self._last_price(code)
            if last is None:
                last = pos.get("lastPrice") or pos["avgPrice"]
            pos["lastPrice"] = last
            mult = float(info.get("mult") or pos.get("mult") or PARAMS.default_mult)
            rate = PARAMS.margin_rate(pos["variety"], settings)
            sign = 1.0 if pos["dir"] == "long" else -1.0
            float_pnl = (last - pos["avgPrice"]) * mult * pos["lots"] * sign
            margin = last * mult * pos["lots"] * rate
            notional = last * mult * pos["lots"]
            pos["floatPnl"] = round(float_pnl, 2)
            pos["margin"] = round(margin, 2)
            margin_total += margin
            float_total += float_pnl
            pos_out.append({
                "key": k,
                "code": code,
                "name": pos.get("name") or info.get("name") or code,
                "variety": pos["variety"],
                "varietyName": pos.get("varietyName") or info.get("varietyName") or pos["variety"],
                "exch": pos.get("exch") or info.get("exch") or "",
                "exchName": pos.get("exchName") or info.get("exchName") or "",
                "dir": pos["dir"],
                "dirLabel": "多" if pos["dir"] == "long" else "空",
                "lots": pos["lots"],
                "todayLots": pos.get("todayLots", 0),
                "yesterdayLots": pos["lots"] - pos.get("todayLots", 0),
                "avgPrice": pos["avgPrice"],
                "lastPrice": last,
                "mult": mult,
                "unit": info.get("unit") or "",
                "marginRate": round(rate, 4),
                "margin": round(margin, 2),
                "notional": round(notional, 2),
                "floatPnl": round(float_pnl, 2),
                "floatPct": round(float_pnl / margin * 100, 2) if margin else 0.0,
                "openTs": pos.get("openTs") or "",
                "zdf": (MARKET.get(code) or {}).get("zdf"),
            })
        pos_out.sort(key=lambda x: (x["variety"], x["code"], x["dir"]))
        equity = s["cash"] + float_total
        available = equity - margin_total
        risk = (margin_total / equity) if equity > 0 else (999.0 if margin_total > 0 else 0.0)
        return {
            "cash": round(s["cash"], 2),
            "equity": round(equity, 2),
            "available": round(available, 2),
            "margin": round(margin_total, 2),
            "floatPnl": round(float_total, 2),
            "realizedPnl": round(s["realizedPnl"], 2),
            "totalFee": round(s["totalFee"], 2),
            "totalDeposit": round(s["totalDeposit"], 2),
            "totalWithdraw": round(s["totalWithdraw"], 2),
            "netDeposit": round(s["totalDeposit"] - s["totalWithdraw"], 2),
            "risk": round(min(risk, 9.9999), 4),
            "riskPct": round(min(risk, 9.9999) * 100, 2),
            "positions": pos_out,
            "positionCount": len(pos_out),
        }

    def snapshot(self):
        with self.lock:
            # 跨日：今仓转昨仓
            self.rollover_locked()
            return self.snapshot_locked()

    def rollover_locked(self):
        s = self.state
        if s.get("tradeDate") != today_str():
            for pos in s["positions"].values():
                pos["todayLots"] = 0
            s["tradeDate"] = today_str()
            self.log("结算", "交易日切换，全部今仓转为昨仓")
            self.save()

    # ---------------- 开平仓 ----------------
    def _price_for_fill(self, code, info, price_type, order_price, side):
        """返回 (成交价, 错误)。

        side 是真实的买卖方向：开多=买入、开空=卖出、平多=卖出、平空=买入。
        市价按最新价 + 滑点（滑点总是对成交方不利）；限价按「不劣于委托价」成交。
        """
        last = MARKET.price(code)
        if last is None:
            return None, "该合约暂无行情，无法成交"
        tick = float(info.get("tick") or 1.0)
        q = MARKET.get(code) or {}
        zt, dt = q.get("zt"), q.get("dt")
        if price_type == "market":
            slip = float(self.state["settings"].get("滑点跳数", 0) or 0) * tick
            fill = last + slip if side == "buy" else last - slip
            return round(clamp_limit(fill, zt, dt), 6), None
        if side == "buy":
            if last <= order_price:
                return round(clamp_limit(min(order_price, last), zt, dt), 6), None
            return None, "未触发（最新价 %.4f > 委托价 %.4f）" % (last, order_price)
        if last >= order_price:
            return round(clamp_limit(max(order_price, last), zt, dt), 6), None
        return None, "未触发（最新价 %.4f < 委托价 %.4f）" % (last, order_price)

    def place_order(self, req):
        """下单主流程。返回 (ok, msg, order_or_none)"""
        code = (req.get("code") or "").strip().upper()
        direction = req.get("dir")            # long / short
        offset = req.get("offset")            # open / close
        price_type = req.get("priceType") or "market"
        close_mode = req.get("closeMode") or "auto"   # auto / today / yesterday
        try:
            lots = int(req.get("lots") or 0)
        except (TypeError, ValueError):
            lots = 0

        if direction not in ("long", "short"):
            return False, "方向参数错误", None
        if offset not in ("open", "close"):
            return False, "开平参数错误", None
        if lots <= 0:
            return False, "手数必须为正整数", None
        if lots > 5000:
            return False, "单笔手数上限 5000 手", None

        with self.lock:
            s = self.state
            settings = s["settings"]
            info = MARKET.by_code.get(code)
            if not info:
                return False, "合约 %s 不存在或非真实月份合约" % code, None
            if settings.get("仅交易时段下单") and not session_open(info["variety"], info["exch"])[0]:
                return False, "当前非交易时段（可在「设置」中关闭该限制）", None

            variety = info["variety"]
            last = MARKET.price(code)
            if last is None:
                return False, "该合约暂无行情，无法下单", None

            # 限价单价格校验
            order_price = None
            if price_type == "limit":
                try:
                    order_price = float(req.get("price"))
                except (TypeError, ValueError):
                    return False, "限价单必须填写委托价", None
                if order_price <= 0:
                    return False, "委托价必须大于 0", None

            order = {
                "id": self.next_id("O"),
                "ts": now_str(),
                "code": code,
                "name": info["name"],
                "variety": variety,
                "varietyName": info["varietyName"],
                "exch": info["exchName"],
                "dir": direction,
                "dirLabel": "买多" if direction == "long" else "卖空",
                "offset": offset,
                "offsetLabel": "开仓" if offset == "open" else "平仓",
                "priceType": price_type,
                "priceTypeLabel": "市价" if price_type == "market" else "限价",
                "price": order_price,
                "lots": lots,
                "filledLots": 0,
                "status": "待成交" if price_type == "limit" else "已成交",
                "filledPrice": None,
                "fee": 0.0,
                "pnl": 0.0,
                "closeMode": close_mode,
                "msg": "",
            }

            # 涨跌停板校验（东方财富「每日涨跌幅度」）：越界委托直接拒绝并留痕
            if price_type == "limit" and settings.get("涨跌停校验", True):
                _q = MARKET.get(code) or {}
                _zt, _dt = _q.get("zt"), _q.get("dt")
                _pct = info.get("limitPct") or 0
                _tip = ("（东方财富公示每日涨跌幅度 ±%.1f%%）" % (_pct * 100)) if _pct else ""
                _bad = None
                if _zt and order_price > _zt + 1e-9:
                    _bad = "委托价 %.4f 高于涨停价 %.4f，已拒绝%s" % (order_price, _zt, _tip)
                elif _dt and order_price < _dt - 1e-9:
                    _bad = "委托价 %.4f 低于跌停价 %.4f，已拒绝%s" % (order_price, _dt, _tip)
                if _bad:
                    order["status"] = "已拒绝"
                    order["msg"] = _bad
                    s["orders"].insert(0, order)
                    del s["orders"][500:]
                    self.save()
                    return False, _bad, order

            if offset == "open":
                ok, msg = self._do_open(order, info, lots)
            else:
                ok, msg = self._do_close(order, info, lots, close_mode)

            if not ok:
                order["status"] = "已拒绝"
                order["msg"] = msg
                s["orders"].insert(0, order)
                del s["orders"][500:]
                self.save()
                return False, msg, order

            s["orders"].insert(0, order)
            del s["orders"][500:]
            self._sample_equity_locked()
            self.save()
            return True, msg, order

    def _do_open(self, order, info, lots):
        s = self.state
        settings = s["settings"]
        code = order["code"]
        direction = order["dir"]
        fill, err = self._price_for_fill(code, info, order["priceType"], order["price"],
                                         trade_side(direction, "open"))
        if fill is None:
            if order["priceType"] == "limit":
                # 挂单等待撮合
                return True, "已挂单，等待价格触发"
            return False, err

        mult = float(info["mult"])
        rate = PARAMS.margin_rate(info["variety"], settings)
        margin = fill * mult * lots * rate
        fee = PARAMS.fee(info["variety"], fill, lots, "open", settings)
        snap = self.snapshot_locked()
        if margin + fee > snap["available"] + 1e-6:
            can = max_lots_open(info, fill, snap["available"], settings)
            return False, ("可用资金不足：本单需保证金 ¥%.2f + 手续费 ¥%.2f，当前可用 ¥%.2f（该合约最多可开 %d 手）"
                           % (margin, fee, snap["available"], can))

        s["cash"] = round(s["cash"] - fee, 2)
        s["totalFee"] = round(s["totalFee"] + fee, 2)

        k = pos_key(code, direction)
        pos = s["positions"].get(k)
        if pos:
            total_cost = pos["avgPrice"] * pos["lots"] + fill * lots
            pos["lots"] += lots
            pos["avgPrice"] = round(total_cost / pos["lots"], 6)
            pos["todayLots"] = pos.get("todayLots", 0) + lots
            pos["lastPrice"] = fill
        else:
            s["positions"][k] = {
                "code": code, "name": info["name"], "variety": info["variety"],
                "varietyName": info["varietyName"], "exch": info["exch"],
                "exchName": info["exchName"], "dir": direction, "lots": lots,
                "todayLots": lots, "avgPrice": fill, "lastPrice": fill,
                "mult": mult, "openTs": now_str(),
            }

        order.update({"filledLots": lots, "status": "已成交", "filledPrice": fill, "fee": fee,
                      "msg": "成交价 %.4f" % fill})
        s["trades"].insert(0, {
            "id": order["id"], "ts": order["ts"], "code": code, "name": info["name"],
            "dir": direction, "dirLabel": order["dirLabel"], "offset": "open", "offsetLabel": "开仓",
            "price": fill, "lots": lots, "fee": fee, "pnl": None,
            "margin": round(margin, 2), "closeMode": "",
        })
        del s["trades"][1000:]
        self.log("成交", "开%s %s %d 手 @ %.4f　手续费 ¥%.2f"
                  % ("多" if direction == "long" else "空", info["name"], lots, fill, fee))
        return True, "开仓成交 %.4f × %d 手" % (fill, lots)

    def _do_close(self, order, info, lots, close_mode):
        s = self.state
        settings = s["settings"]
        code = order["code"]
        direction = order["dir"]
        k = pos_key(code, direction)
        pos = s["positions"].get(k)
        if not pos or pos["lots"] <= 0:
            return False, "无该方向持仓，无法平仓"

        today = pos.get("todayLots", 0)
        yest = pos["lots"] - today
        if close_mode == "today":
            if today <= 0:
                return False, "无今仓可平"
            if lots > today:
                return False, "今仓仅 %d 手，无法平 %d 手" % (today, lots)
            n_today, n_yest = lots, 0
        elif close_mode == "yesterday":
            if yest <= 0:
                return False, "无昨仓可平"
            if lots > yest:
                return False, "昨仓仅 %d 手，无法平 %d 手" % (yest, lots)
            n_today, n_yest = 0, lots
        else:                                   # auto：先平昨、再平今
            if lots > pos["lots"]:
                return False, "持仓仅 %d 手，无法平 %d 手" % (pos["lots"], lots)
            n_yest = min(lots, yest)
            n_today = lots - n_yest

        fill, err = self._price_for_fill(code, info, order["priceType"], order["price"],
                                         trade_side(direction, "close"))
        if fill is None:
            if order["priceType"] == "limit":
                return True, "已挂单，等待价格触发"
            return False, err

        sign = 1.0 if direction == "long" else -1.0
        mult = float(pos.get("mult") or info["mult"])
        pnl = (fill - pos["avgPrice"]) * mult * lots * sign

        fee = 0.0
        if n_yest:
            fee += PARAMS.fee(info["variety"], fill, n_yest, "close", settings)
        if n_today:
            fee += PARAMS.fee(info["variety"], fill, n_today, "closeToday", settings)

        s["cash"] = round(s["cash"] + pnl - fee, 2)
        s["realizedPnl"] = round(s["realizedPnl"] + pnl, 2)
        s["totalFee"] = round(s["totalFee"] + fee, 2)

        pos["lots"] -= lots
        pos["todayLots"] = max(0, today - n_today)
        if pos["lots"] <= 0:
            s["positions"].pop(k, None)

        mode_txt = "平昨%d手" % n_yest if n_today == 0 else ("平今%d手" % n_today if n_yest == 0
                                                            else "平昨%d手+平今%d手" % (n_yest, n_today))
        order.update({"filledLots": lots, "status": "已成交", "filledPrice": fill, "fee": fee,
                      "pnl": round(pnl, 2), "msg": "成交价 %.4f（%s）" % (fill, mode_txt)})
        s["trades"].insert(0, {
            "id": order["id"], "ts": order["ts"], "code": code, "name": info["name"],
            "dir": direction, "dirLabel": order["dirLabel"], "offset": "close", "offsetLabel": "平仓",
            "price": fill, "lots": lots, "fee": fee, "pnl": round(pnl, 2),
            "margin": 0.0, "closeMode": mode_txt,
        })
        del s["trades"][1000:]
        self.log("成交", "平%s %s %d 手 @ %.4f（%s）　盈亏 ¥%.2f　手续费 ¥%.2f"
                  % ("多" if direction == "long" else "空", info["name"], lots, fill, mode_txt, pnl, fee))
        return True, "平仓成交 %.4f × %d 手，盈亏 ¥%.2f" % (fill, lots, pnl)

    def match_pending(self):
        """对挂单中的限价单尝试撮合。"""
        with self.lock:
            s = self.state
            changed = False
            for order in s["orders"]:
                if order["status"] != "待成交":
                    continue
                code = order["code"]
                info = MARKET.by_code.get(code)
                if not info:
                    continue
                if order["offset"] == "open":
                    ok, msg = self._do_open(order, info, order["lots"])
                else:
                    ok, msg = self._do_close(order, info, order["lots"], order.get("closeMode") or "auto")
                if order["status"] == "已成交":
                    changed = True
                elif not ok and order["status"] != "待成交":
                    order["msg"] = msg
                    changed = True
            if changed:
                self._sample_equity_locked()
                self.save()

    def risk_check(self):
        """自动强平。"""
        with self.lock:
            s = self.state
            settings = s["settings"]
            if not settings.get("自动强平"):
                return
            snap = self.snapshot_locked()
            line = float(settings.get("强平线", 1.0))
            if not snap["positions"]:
                return
            if snap["risk"] < line:
                return
            self.log("风控", "风险度 %.2f%% 触及强平线 %.2f%%，启动自动强平"
                      % (snap["riskPct"], line * 100))
            guard = 0
            while guard < 40:
                guard += 1
                snap = self.snapshot_locked()
                if snap["risk"] < line or not snap["positions"]:
                    break
                worst = min(snap["positions"], key=lambda p: p["floatPnl"])
                info = MARKET.by_code.get(worst["code"])
                if not info:
                    s["positions"].pop(worst["key"], None)
                    continue
                order = {
                    "id": self.next_id("F"), "ts": now_str(), "code": worst["code"],
                    "name": worst["name"], "variety": worst["variety"],
                    "varietyName": worst["varietyName"], "exch": worst["exchName"],
                    "dir": worst["dir"], "dirLabel": "强平", "offset": "close",
                    "offsetLabel": "强平", "priceType": "market", "priceTypeLabel": "市价",
                    "price": None, "lots": worst["lots"], "filledLots": 0, "status": "已成交",
                    "filledPrice": None, "fee": 0.0, "pnl": 0.0, "closeMode": "auto", "msg": "",
                }
                ok, msg = self._do_close(order, info, worst["lots"], "auto")
                if not ok:
                    s["positions"].pop(worst["key"], None)
                    self.log("风控", "强平 %s 失败：%s（已移除该持仓）" % (worst["name"], msg))
                    continue
                s["orders"].insert(0, order)
                del s["orders"][500:]
                self.log("风控", "已强平 %s %s %d 手，盈亏 ¥%.2f"
                          % (worst["name"], worst["dirLabel"], worst["lots"], order["pnl"]))
            self._sample_equity_locked()
            self.save()

    def cancel(self, order_id):
        with self.lock:
            for order in self.state["orders"]:
                if order["id"] == order_id:
                    if order["status"] != "待成交":
                        return False, "该委托状态为「%s」，无法撤单" % order["status"]
                    order["status"] = "已撤单"
                    order["msg"] = "用户撤单 %s" % now_str()
                    self.save()
                    self.log("委托", "撤单 %s %s" % (order_id, order["name"]))
                    return True, "已撤单"
            return False, "未找到该委托"

    def cancel_all(self):
        n = 0
        with self.lock:
            for order in self.state["orders"]:
                if order["status"] == "待成交":
                    order["status"] = "已撤单"
                    order["msg"] = "批量撤单 %s" % now_str()
                    n += 1
            if n:
                self.save()
                self.log("委托", "批量撤单 %d 笔" % n)
        return True, "已撤销 %d 笔挂单" % n

    def _sample_equity_locked(self):
        s = self.state
        snap = self.snapshot_locked()
        curve = s.setdefault("equityCurve", [])
        curve.append({
            "ts": now_str(),
            "equity": snap["equity"], "cash": snap["cash"],
            "margin": snap["margin"], "floatPnl": snap["floatPnl"],
            "realizedPnl": snap["realizedPnl"],
        })
        if len(curve) > 4000:
            del curve[:len(curve) - 4000]

    def sample_equity(self):
        with self.lock:
            self._sample_equity_locked()

    def reset(self, keep_cash=False):
        with self.lock:
            old = self.state
            s = self._blank()
            _merged = dict(DEFAULT_SETTINGS)
            _merged.update(old.get("settings") or {})
            s["settings"] = _merged
            if keep_cash:
                s["cash"] = old.get("cash", 0.0)
                s["totalDeposit"] = old.get("totalDeposit", 0.0)
                s["totalWithdraw"] = old.get("totalWithdraw", 0.0)
                s["cashFlow"] = old.get("cashFlow") or []
            s["seq"] = old.get("seq", 0)
            self.state = s
            self.log("系统", "账户已重置（%s）" % ("保留资金" if keep_cash else "清空全部"))
            self._sample_equity_locked()
            self.save()
        return True, "账户已重置"

    def set_settings(self, patch):
        with self.lock:
            s = self.state["settings"]
            for k, v in (patch or {}).items():
                if k not in DEFAULT_SETTINGS:
                    continue
                if k in ("自动强平", "仅交易时段下单", "涨跌停校验"):
                    s[k] = bool(v)
                else:
                    try:
                        s[k] = float(v)
                    except (TypeError, ValueError):
                        continue
            self.log("系统", "交易设置已更新：%s" % json.dumps(s, ensure_ascii=False))
            self.save()
        return True, "设置已保存"


ACCOUNT = Account()


def _autosave_loop():
    """定时兜底保存：用户直接点窗口「×」时不会走正常退出流程，最多丢 5 秒数据。"""
    while True:
        time.sleep(5)
        try:
            ACCOUNT.save()
        except Exception:                                   # noqa: BLE001
            pass


threading.Thread(target=_autosave_loop, daemon=True).start()
atexit.register(lambda: ACCOUNT.save(force=True))


def _acc_bind(uid, state_file):
    """账户引擎回调：切换到指定账户的资金状态文件。"""
    ACCOUNT.bind(uid, state_file)


ACC = 账户引擎.AccountEngine(on_switch=_acc_bind)


def max_lots_open(info, price, available, settings):
    """按可用资金估算最多可开手数。"""
    if not price:
        return 0
    mult = float(info.get("mult") or 10)
    rate = PARAMS.margin_rate(info["variety"], settings)
    per_margin = price * mult * rate
    fee = PARAMS.fee(info["variety"], price, 1, "open", settings)
    per = per_margin + fee
    if per <= 0:
        return 0
    return max(0, int(available // per))


# ----------------------------------------------------------------------
# 交易时段
# ----------------------------------------------------------------------
def session_open(variety, exch):
    """返回 (是否可交易, 状态文本)。时段口径取自东方财富期货「交易时间」，按北京时间粗略判断（未模拟法定节假日）。"""
    now = datetime.now()
    if now.weekday() >= 5:
        return False, "周末休市"
    hm = now.hour * 60 + now.minute
    # 夜盘：20:55 开盘集合竞价起
    ne = night_end(variety)
    if ne is not None:
        if ne <= 24 * 60:
            if 20 * 60 + 55 <= hm <= ne:
                return True, "夜盘交易中"
        else:
            if hm >= 20 * 60 + 55 or hm <= ne - 24 * 60:
                return True, "夜盘交易中"
    # 日盘（含开市前 5 分钟集合竞价）
    if exch == "CFFEX":
        if variety in CFFEX_INDEX:
            if 9 * 60 + 25 <= hm <= 11 * 60 + 30 or 13 * 60 <= hm <= 15 * 60:
                return True, "日盘交易中"
        else:
            if 9 * 60 + 25 <= hm <= 11 * 60 + 30 or 13 * 60 <= hm <= 15 * 60 + 15:
                return True, "日盘交易中"
    else:
        if (8 * 60 + 55 <= hm <= 10 * 60 + 15) or (10 * 60 + 30 <= hm <= 11 * 60 + 30) \
                or (13 * 60 + 30 <= hm <= 15 * 60):
            return True, "日盘交易中"
    return False, "休市（非交易时段）"


def session_text(variety, exch):
    """该品种完整交易时段文本（东方财富「交易时间」口径）。"""
    cfg = PARAMS.raw.get("交易时段", {})
    if exch == "CFFEX":
        day = cfg.get("日盘_股指" if variety in CFFEX_INDEX else "日盘_国债", "")
    else:
        day = cfg.get("日盘_商品", "")
    parts = ["日盘 " + day] if day else []
    ne = night_end(variety)
    if ne is not None:
        key = {1380: "夜盘_2300", 1500: "夜盘_0100", 1590: "夜盘_0230"}.get(ne)
        if key and cfg.get(key):
            parts.append("夜盘 " + cfg[key])
    return "；".join(parts) if parts else "—"


# ----------------------------------------------------------------------
# HTTP 服务
# ----------------------------------------------------------------------
def compact_quotes():
    """紧凑行情数组：[code, p, zdf, zde, vol, ccl, o, h, l, zjsj, rz, wp, np, zt, dt]"""
    out = []
    for code, info in MARKET.by_code.items():
        q = MARKET.quotes.get(code)
        if not q:
            continue
        out.append([code, q["p"], q["zdf"], q["zde"], q["vol"], q["ccl"],
                    q["o"], q["h"], q["l"], q["zjsj"], q["rz"], q["wp"], q["np"],
                    q.get("zt"), q.get("dt")])
    return out


class Handler(BaseHTTPRequestHandler):
    server_version = "FuturesPaperTrade/1.0"

    def log_message(self, fmt, *args):
        pass

    # ---------- 工具 ----------
    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:                                   # noqa: BLE001
            pass

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False, separators=(",", ":")))

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0:
                return {}
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:                                   # noqa: BLE001
            return {}

    def _file(self, relpath):
        # 只读资源：打包后从解压目录取，也允许 exe 旁放同名文件覆盖内嵌资源
        parts = [p for p in str(relpath).replace("\\", "/").split("/") if p and p != "."]
        if not parts or ".." in parts:                       # 防目录穿越
            self._send(404, "404 Not Found", "text/plain; charset=utf-8")
            return
        path = PATHS.res(*parts)
        if not os.path.isfile(path):
            self._send(404, "404 Not Found", "text/plain; charset=utf-8")
            return
        ctype = "application/octet-stream"
        if relpath.endswith(".html"):
            ctype = "text/html; charset=utf-8"
        elif relpath.endswith(".js"):
            ctype = "application/javascript; charset=utf-8"
        elif relpath.endswith(".css"):
            ctype = "text/css; charset=utf-8"
        elif relpath.endswith(".json"):
            ctype = "application/json; charset=utf-8"
        elif relpath.endswith(".png"):
            ctype = "image/png"
        elif relpath.endswith(".ico"):
            ctype = "image/x-icon"
        with open(path, "rb") as f:
            self._send(200, f.read(), ctype)

    # ---------- GET ----------
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path)      # 中文文件名需先解码
        if path in ("/", "/index.html", "/" + PAGE):
            self._file(PAGE)
            return
        if path == "/api/config":
            reg = {}
            for nm, meta in KLINE_MOD.INDICATORS.items():
                reg[nm] = {"kind": meta["kind"], "desc": meta["desc"],
                           "default": meta["default"], "args": meta["args"]}
            self._json({
                "品种参数": PARAMS.variety,
                "设置": ACCOUNT.state["settings"],
                "默认设置": DEFAULT_SETTINGS,
                "板块顺序": SECTOR_ORDER,
                "交易时段": PARAMS.raw.get("交易时段", {}),
                "数据来源": PARAMS.raw.get("_数据来源", {}),
                "指标": {
                    "主图列表": KLINE_MOD.MAIN_LIST,
                    "副图列表": KLINE_MOD.SUB_LIST,
                    "注册表": reg,
                    "默认主图": DEFAULT_MAIN,
                    "默认副图": DEFAULT_SUB,
                },
                "周期列表": [{"key": k, "label": KLINE_MOD.PERIODS[k][0]}
                             for k in KLINE_MOD.PERIOD_ORDER],
                "策略模板": STRAT_MOD.StrategyEngine.templates(),
                "策略方向": STRAT_MOD.DIRECTIONS,
                "资金管理": {
                    "分组": MM_MOD.FIELD_GROUPS,
                    "默认": MM_MOD.clean_for_save({}),
                    "预设": [{"key": p["key"], "name": p["name"],
                              "desc": p["desc"],
                              "mm": MM_MOD.clean_for_save(p["mm"]),
                              "params": p.get("params") or {},
                              "摘要": MM_MOD.describe(p["mm"])}
                             for p in MM_MOD.PRESETS],
                    "手数模式": [{"key": k, "name": n, "desc": d}
                                 for k, n, d in MM_MOD.LOT_MODES],
                    "止损方式": [{"key": k, "name": n, "desc": d}
                                 for k, n, d in MM_MOD.STOP_MODES],
                    "移动止损": [{"key": k, "name": n, "desc": d}
                                 for k, n, d in MM_MOD.TRAIL_MODES],
                    "加仓模式": [{"key": k, "name": n, "desc": d}
                                 for k, n, d in MM_MOD.ADD_MODES],
                },
                "口径说明": [
                    "【行情数据】实时快照取自东方财富期货行情接口；K 线与分时取自新浪财经期货接口（东方财富 K 线域名在本机不可达）。具体合约无历史时自动回退「主力连续」代码（如 RB2610 → RB0），页面上会标注。",
                    "【保证金率】= 东方财富期货「公司保证金比例」；手续费 = 东方财富期货「公司手续费标准」（官网口径为「不高于以下标准」，多数品种约为交易所标准的 3 倍）。",
                    "【手续费】区分开仓 / 平昨 / 平今，平今按官网单列值计提（如铜、焦炭、生猪、苹果、股指、欧线集运、多晶硅）。",
                    "【保证金】按最新价实时盯市（真实交易所以结算价逐日盯市）；浮动盈亏持仓期间累计展示，平仓时一次性转为已实现盈亏。",
                    "【限价委托】超出当日涨跌停板（东方财富「每日涨跌幅度」）会被拒绝，成交价也被夹在涨跌停区间内；可在「设置」中关闭该校验。",
                    "【交易时段】按东方财富「交易时间」判断（含开市前 5 分钟集合竞价），仅交易时段下单开关默认关闭，便于非交易时段验证策略。",
                    "【回测】第 i 根 K 线收盘产生信号、第 i+1 根开盘成交（无未来函数）；成交价含滑点；手续费、保证金与实盘账户同一口径。",
                    "【资金管理】手数由「资金管理」模块统一决定，回测与实盘策略交易共用同一套代码：手数模式（固定 / 保证金比例 / 风险固定 / 波动率目标 / 分数凯利）、止损方式（ATR / 百分比 / 跳数），再叠加熔断、连亏减仓、日内亏损上限、保证金占用上限等风控闸门。手数计算基准是「上一根收盘时的权益」，同样不使用未来信息。",
                    "【风险固定】先定单笔最大亏损 = 权益 × 风险%，再按止损距离倒算手数：手数 = 风险预算 ÷ (止损距离 × 合约乘数)。这是最科学的仓位算法 —— 让每笔亏损金额可控，而不是让手数固定。",
                    "【复盘】指定区间逐根回放，可手动按当根收盘价记录交易，也可打开「自动跟随策略信号」由资金管理与策略共同驱动；每推进一根重算权益路径，因此资金变化曲线与记录永远自洽。记录可折叠、可复制、可导出 CSV / TSV / JSON。",
                    "交易日按本机自然日切换，今仓跨日后自动转为昨仓；不模拟交割、期权、套保与梯度申报费。",
                    "【账户与银期转账】支持多个模拟账户（登录账号 + 登录密码登录，交易账户 + 资金密码用于出金校验），每个账户有独立的持仓 / 资金 / 流水状态文件；模拟银行卡可添加 / 删除（解绑前须先转出余额）；银期转入需银行交易密码，银期转出需银行交易密码 + 资金密码且不超过可用资金。所有密码均加盐哈希落盘，接口不回传明文。默认账户 sim001（登录 / 资金 / 银行卡密码均为 123456）。",
                ],
            })
            return
        if path == "/api/contracts":
            self._json({
                "ts": MARKET.ts,
                "source": MARKET.source,
                "error": MARKET.error,
                "contracts": MARKET.contracts,
            })
            return
        if path == "/api/quotes":
            self._json({"ts": MARKET.ts, "source": MARKET.source, "q": compact_quotes()})
            return
        if path == "/api/state":
            snap = ACCOUNT.snapshot()
            st = ACCOUNT.state
            self._json({
                "ts": now_str(),
                "quoteTs": MARKET.ts,
                "source": MARKET.source,
                "quoteError": MARKET.error,
                "account": snap,
                "settings": st["settings"],
                "orders": st["orders"][:200],
                "trades": st["trades"][:200],
                "cashFlow": st["cashFlow"][:200],
                "logs": st["logs"][:80],
                "equityCurve": st["equityCurve"][-1200:],
                "marketOpen": session_open("RB", "SHFE")[0],
                "marketStatus": session_open("RB", "SHFE")[1],
                "createdAt": st.get("createdAt"),
                "accInfo": ACC.info(),
            })
            return
        if path == "/api/acc/info":
            self._json(ACC.info())
            return
        if path == "/api/status":
            self._json({
                "contracts": len(MARKET.contracts),
                "source": MARKET.source,
                "ts": MARKET.ts,
                "error": MARKET.error,
                "accountFile": ACCOUNT.file,
                "strategies": len(STRATS.list()),
                "replays": len(REPLAYS.list()),
            })
            return

        # ---------- 行情 K 线 / 分时 / 指标 ----------
        if path in ("/api/kline", "/api/timeshare", "/api/indicator"):
            q = urllib.parse.parse_qs(parsed.query)
            g = lambda k, d="": (q.get(k) or [d])[0]          # noqa: E731
            code = (g("code") or "RB0").strip().upper()
            period = (g("period") or "day").strip()
            if period not in KLINE_MOD.PERIODS:
                period = "day"
            try:
                limit = int(float(g("limit") or 0))
            except ValueError:
                limit = 0
            try:
                if path == "/api/timeshare":
                    var = (g("variety") or variety_of(code)).upper()
                    ts = KLINE.timeshare(code, var)
                    info = PARAMS.variety.get(var) or {}
                    ref = ts.get("preClose")
                    pts = ts["points"]
                    hi = max([p["c"] for p in pts if p["c"] is not None] or [0])
                    lo = min([p["c"] for p in pts if p["c"] is not None] or [0])
                    self._json({"ok": True, "code": code,
                                "name": info.get("名称") or var,
                                "variety": var, "date": ts.get("date"),
                                "preClose": ref, "high": hi, "low": lo,
                                "source": ts.get("source"),
                                "fetchedAt": ts.get("fetchedAt"),
                                "points": pts})
                else:
                    self._json(kline_payload(
                        code, period, limit,
                        main_specs=g("main") or None,
                        sub_specs=g("sub") or None,
                        variety=g("variety") or "",
                        force=(g("force") == "1")))
            except Exception as e:                            # noqa: BLE001
                self._json({"ok": False, "msg": str(e), "code": code,
                            "period": period, "bars": [], "main": [],
                            "sub": []}, 200)
            return

        # ---------- 策略 ----------
        if path == "/api/strategy/templates":
            self._json({"ok": True, "templates": STRAT_MOD.StrategyEngine.templates(),
                        "directions": STRAT_MOD.DIRECTIONS,
                        "sectors": SECTOR_ORDER})
            return
        if path == "/api/strategy/list":
            self._json({"ok": True, "strategies": STRATS.list()})
            return
        if path == "/api/strategy/signals":
            q = urllib.parse.parse_qs(parsed.query)
            g = lambda k, d="": (q.get(k) or [d])[0]          # noqa: E731
            code = (g("code") or "").strip().upper()
            period = g("period") or "day"
            sid = g("id") or ""
            st = STRATS.get(sid)
            if st:
                kind, params, period = (st["kind"], st.get("params"),
                                        g("period") or st.get("period"))
            else:
                kind = g("kind") or "ma_cross"
                params = {}
                for pair in (g("params") or "").split(";"):
                    if "=" in pair:
                        k, v = pair.split("=", 1)
                        try:
                            params[k.strip()] = float(v) if "." in v else int(v)
                        except ValueError:
                            params[k.strip()] = v.strip()
                if not params:
                    params = dict(STRAT_MOD.TEMPLATE_BY_KIND.get(
                        kind, {}).get("params") or {})
            try:
                var = variety_of(code)
                d = KLINE.get(code, period if period in KLINE_MOD.PERIODS
                              else "day", variety=var)
                _t, _w, acts = STRAT_MOD.run_strategy(d["bars"], kind, params)
                info = PARAMS.variety.get(var) or {}
                # 只回传最近 800 个信号，避免响应过大
                tail = acts[-800:]
                self._json({"ok": True, "code": code,
                            "name": info.get("名称") or var,
                            "period": period, "kind": kind, "params": params,
                            "bars": len(d["bars"]), "marks": tail,
                            "markCount": len(acts),
                            "recent": acts[-40:],
                            "last": (dict(acts[-1],
                                          barsAgo=len(d["bars"]) - 1 - acts[-1]["i"])
                                     if acts else None)})
            except Exception as e:                            # noqa: BLE001
                self._json({"ok": False, "msg": str(e), "marks": []})
            return

        # ---------- 回测记录 ----------
        if path == "/api/backtest/list":
            self._json({"ok": True, "items": BACKTEST_LOG[-40:][::-1]})
            return
        if path == "/api/backtest/detail":
            q = urllib.parse.parse_qs(parsed.query)
            bid = (q.get("id") or [""])[0]
            for it in BACKTEST_LOG:
                if it.get("id") == bid:
                    self._json({"ok": True, "item": it})
                    return
            self._json({"ok": False, "msg": "回测记录不存在"})
            return

        # ---------- 复盘 ----------
        if path == "/api/replay/list":
            self._json({"ok": True, "sessions": REPLAYS.list()})
            return
        if path == "/api/replay/view":
            q = urllib.parse.parse_qs(parsed.query)
            s = REPLAYS.get((q.get("id") or [""])[0])
            if not s:
                self._json({"ok": False, "msg": "复盘会话不存在或已过期"})
            else:
                try:
                    tail = int((q.get("tail") or ["260"])[0])
                except ValueError:
                    tail = 260
                self._json(s.view(tail))
            return

        # 静态资源
        rel = path.lstrip("/")
        if rel.startswith("vendor/") or rel.startswith("快照/"):
            self._file(rel)
            return
        self._send(404, "404 Not Found", "text/plain; charset=utf-8")

    # ---------- POST ----------
    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        try:
            if path.startswith("/api/strategy/") or \
               path.startswith("/api/backtest/") or \
               path.startswith("/api/replay/"):
                self._dispatch_quant(path)
                return
            self._dispatch_post(path)
        except Exception as e:                              # noqa: BLE001
            import traceback
            traceback.print_exc()
            try:
                self._json({"ok": False, "msg": "服务内部错误：%s" % e}, 500)
            except Exception:                               # noqa: BLE001
                pass

    # ---------- 策略 / 回测 / 复盘 接口 ----------
    def _dispatch_quant(self, path):
        req = self._body()

        # ===== 策略 =====
        if path == "/api/strategy/save":
            try:
                data = STRATS.save_one(req)
            except ValueError as e:
                self._json({"ok": False, "msg": str(e)})
                return
            self._json({"ok": True, "msg": "策略已保存：%s" % data["name"],
                        "strategy": data, "strategies": STRATS.list()})
            return

        if path == "/api/strategy/delete":
            ok = STRATS.delete(req.get("id") or "")
            self._json({"ok": ok, "msg": "已删除" if ok else "策略不存在",
                        "strategies": STRATS.list()})
            return

        if path == "/api/strategy/toggle":
            st = STRATS.toggle(req.get("id") or "",
                               req.get("enabled"), req.get("dryRun"))
            if not st:
                self._json({"ok": False, "msg": "策略不存在"})
                return
            if st["enabled"]:
                mode = "仅记录信号（不下单）" if st.get("dryRun") else "自动下单"
                msg = "策略「%s」已挂载：%s" % (st["name"], mode)
            else:
                msg = "策略「%s」已停止" % st["name"]
            self._json({"ok": True, "msg": msg, "strategy": st,
                        "strategies": STRATS.list()})
            return

        if path == "/api/strategy/scan":
            sid = req.get("id") or ""
            if not STRATS.get(sid):
                self._json({"ok": False, "msg": "策略不存在"})
                return
            res = STRATS.scan(sid, req)
            # 命中行补上「可交易合约」（具体月份）与现价
            for r in res.get("rows", []):
                mc = main_contract(r["variety"])
                r["tradeable"] = mc or ""
                q = MARKET.quotes.get(mc or "") or {}
                r["live"] = q.get("p")
                r["liveZdf"] = q.get("zdf")
            res["ts"] = now_str()
            self._json(res)
            return

        # ===== 回测 =====
        if path == "/api/backtest/run":
            r = self._run_backtest(req)
            self._json(r)
            return

        # ===== 复盘 =====
        if path == "/api/replay/open":
            self._json(self._replay_open(req))
            return
        if path in ("/api/replay/step", "/api/replay/seek", "/api/replay/view",
                    "/api/replay/auto", "/api/replay/clear"):
            s = REPLAYS.get(req.get("id") or "")
            if not s:
                self._json({"ok": False, "msg": "复盘会话不存在或已过期"})
                return
            if path == "/api/replay/step":
                self._json(s.step(req.get("n") or 1))
            elif path == "/api/replay/seek":
                self._json(s.seek(req.get("idx") or s.idx))
            elif path == "/api/replay/auto":
                v = s.toggle_auto(req.get("on"))
                v["msg"] = "已%s自动跟随策略信号" % ("打开" if s.auto else "关闭")
                self._json(v)
            elif path == "/api/replay/clear":
                v = s.clear_orders()
                v["msg"] = "已清空复盘操作记录"
                self._json(v)
            else:
                self._json(s.view(int(req.get("tail") or 260)))
            return
        if path == "/api/replay/export":
            s = REPLAYS.get(req.get("id") or "")
            if not s:
                self._json({"ok": False, "msg": "复盘会话不存在或已过期"})
                return
            name, txt, mime = s.export(req.get("fmt") or "csv")
            self._json({"ok": True, "filename": name, "text": txt,
                        "mime": mime, "rows": len(s.records)})
            return
        if path == "/api/replay/trade":
            s = REPLAYS.get(req.get("id") or "")
            if not s:
                self._json({"ok": False, "msg": "复盘会话不存在或已过期"})
                return
            r = s.trade(req.get("side") or "多", req.get("lots") or 1)
            r.update(s.view())
            r["msg"] = r.get("msg", "")
            self._json(r)
            return
        if path == "/api/replay/flat":
            s = REPLAYS.get(req.get("id") or "")
            if not s:
                self._json({"ok": False, "msg": "复盘会话不存在或已过期"})
                return
            r = s.flat()
            r["msg"] = "已平掉复盘持仓"
            self._json(r)
            return
        if path == "/api/replay/close":
            s = REPLAYS.close(req.get("id") or "")
            if s and s.pos:
                s.flat()
            self._json({"ok": True, "msg": "复盘已结束",
                        "stat": s.stat() if s else None,
                        "records": s.records if s else [],
                        "sessions": REPLAYS.list()})
            return

        self._json({"ok": False, "msg": "未知接口"}, 404)

    # ---------- 回测执行 ----------
    def _run_backtest(self, req):
        sid = req.get("strategyId") or ""
        st = STRATS.get(sid) if sid else None
        code = (req.get("code") or (st and st.get("scope", {})
                                    .get("value", [""])[0]) or "RB0")
        code = str(code).strip().upper()
        variety = (req.get("variety") or variety_of(code)).upper()
        period = req.get("period") or (st and st.get("period")) or "day"
        if period not in KLINE_MOD.PERIODS:
            period = "day"
        kind = req.get("kind") or (st and st.get("kind")) or "ma_cross"
        params = dict(STRAT_MOD.TEMPLATE_BY_KIND.get(kind, {}).get("params") or {})
        if st:
            params.update(st.get("params") or {})
        params.update(req.get("params") or {})
        if params.get("方向") not in STRAT_MOD.DIRECTIONS:
            params["方向"] = "双向"

        try:
            d = KLINE.get(code, period, variety=variety)
        except Exception as e:                                # noqa: BLE001
            return {"ok": False, "msg": "行情获取失败：%s" % e}
        bars_all = d["bars"]
        bars, warm = slice_bars(bars_all, req.get("start") or "",
                                req.get("end") or "", warmup=150)
        if len(bars) < 30:
            return {"ok": False, "msg": "所选区间 K 线不足 30 根，请放宽区间"}

        settings = dict(ACCOUNT.state["settings"])
        if req.get("feeDiscount") is not None:
            settings["手续费折扣"] = float(req["feeDiscount"] or 1)
        if req.get("marginMult") is not None:
            settings["保证金倍数"] = float(req["marginMult"] or 1)

        # 资金管理配置：优先用请求里的，其次用策略库里的，最后用默认
        mm_in = req.get("mm")
        if mm_in is None and st:
            mm_in = st.get("mm")
        mm_in = MM_MOD.clean_for_save(mm_in)

        cfg = {"cash": req.get("cash") or 100000,
               "lots": req.get("lots") or 1,
               "lotsMode": req.get("lotsMode") or "fixed",
               "lotsRatio": req.get("lotsRatio") or 0.3,
               "maxLots": req.get("maxLots") or 20,
               "slippageTicks": req.get("slippageTicks") or 0,
               "stopLossPct": req.get("stopLossPct") or 0,
               "takeProfitPct": req.get("takeProfitPct") or 0,
               "execMode": req.get("execMode") or "next_open",
               "mm": mm_in,
               "period": period, "settings": settings}

        t0 = time.time()
        res = BT_MOD.backtest(bars, kind, params, spec_of(variety), cfg)
        if not res.get("ok"):
            return res
        cost = round(time.time() - t0, 3)

        # 图表用：主图 / 副图指标
        main = KLINE_MOD.compute_main(
            split_specs(req.get("main"), ["MA(5,10,20,60)"]), bars)
        subs = KLINE_MOD.compute_sub(
            split_specs(req.get("sub"), ["VOL(5,10,20)", "MACD(12,26,9)"]), bars)

        bid = "bt_" + datetime.now().strftime("%H%M%S") + \
              ("%03d" % (len(BACKTEST_LOG) % 1000))
        item = {
            "id": bid,
            "ts": now_str(), "code": code, "name": contract_label(code),
            "variety": variety, "period": period, "kind": kind,
            "params": params, "warmup": warm, "cost": cost,
            "stats": res["stats"], "cfg": cfg,
            "mm": MM_MOD.clean_for_save(mm_in),
            "mmText": res["meta"].get("mmText", ""),
            "mainSpec": req.get("main") or "MA(5,10,20,60)",
            "subSpec": req.get("sub") or "VOL(5,10,20),MACD(12,26,9)",
        }
        BACKTEST_LOG.append(dict(item, trades=res["trades"][:400],
                                 equity=res["equity"]))
        del BACKTEST_LOG[:-40]

        return {"ok": True, "id": bid, "item": item,
                "stats": res["stats"], "trades": res["trades"],
                "equity": res["equity"], "marks": res["marks"],
                "bars": bars, "warmup": warm, "main": main, "sub": subs,
                "meta": res["meta"],
                "mm": MM_MOD.clean_for_save(mm_in),
                "spec": spec_of(variety),
                "settings": settings,
                "name": contract_label(code), "variety": variety}

    # ---------- 复盘 ----------
    def _replay_open(self, req):
        code = str(req.get("code") or "RB0").strip().upper()
        variety = (req.get("variety") or variety_of(code)).upper()
        period = req.get("period") or "day"
        if period not in KLINE_MOD.PERIODS:
            period = "day"
        try:
            d = KLINE.get(code, period, variety=variety)
        except Exception as e:                                # noqa: BLE001
            return {"ok": False, "msg": "行情获取失败：%s" % e}
        bars = d["bars"]
        if len(bars) < 60:
            return {"ok": False, "msg": "该合约 K 线不足 60 根，无法复盘"}

        start_date = (req.get("start") or "").strip()
        n = max(30, min(2000, int(req.get("bars") or 300)))
        if start_date:
            i0 = 0
            for k, b in enumerate(bars):
                if (b["d"] or "")[:10] >= start_date:
                    i0 = k
                    break
        else:
            i0 = len(bars) - n
        end = (req.get("end") or "").strip()
        if end:
            i1 = len(bars) - 1
            for k in range(len(bars) - 1, -1, -1):
                if (bars[k]["d"] or "")[:10] <= end:
                    i1 = k
                    break
        else:
            i1 = min(len(bars) - 1, i0 + n - 1)
        i0 = max(20, min(i0, len(bars) - 2))
        i1 = max(i0 + 5, min(i1, len(bars) - 1))

        kind = req.get("kind") or ""
        params = req.get("params") or {}
        if req.get("strategyId"):
            st = STRATS.get(req["strategyId"])
            if st:
                kind, params = st["kind"], st.get("params") or {}
        if kind and kind not in STRAT_MOD.TEMPLATE_BY_KIND:
            kind = ""

        # 资金管理：请求 > 策略库 > 默认
        st_obj = STRATS.get(req["strategyId"]) if req.get("strategyId") else None
        mm_in = req.get("mm")
        if mm_in is None and st_obj:
            mm_in = st_obj.get("mm")
        mm_in = MM_MOD.clean_for_save(mm_in)

        s = REPLAYS.open(code, contract_label(code), period, bars,
                         spec_of(variety), i0, i1, kind, params,
                         dict(ACCOUNT.state["settings"]),
                         mm=mm_in,
                         cash=req.get("cash") or 100000,
                         auto=bool(req.get("auto")),
                         tp_pct=req.get("takeProfitPct") or 0)
        v = s.view()
        v["msg"] = "复盘已开始：%s %s，共 %d 根%s" % (
            contract_label(code), KLINE_MOD.PERIODS[period][0], i1 - i0 + 1,
            "（自动跟随策略信号）" if s.auto else "")
        v["variety"] = variety
        return v

    def _dispatch_post(self, path):
        req = self._body()

        if path == "/api/deposit":
            ok, msg = ACCOUNT.deposit(req.get("amount"), req.get("note") or "")
        elif path == "/api/withdraw":
            ok, msg = ACCOUNT.withdraw(req.get("amount"), req.get("note") or "")
        elif path == "/api/order":
            ok, msg, order = ACCOUNT.place_order(req)
        elif path == "/api/cancel":
            ok, msg = ACCOUNT.cancel(req.get("id") or "")
        elif path == "/api/cancel_all":
            ok, msg = ACCOUNT.cancel_all()
        elif path == "/api/close":
            ok, msg, _ = ACCOUNT.place_order({
                "code": req.get("code"), "dir": req.get("dir"), "offset": "close",
                "priceType": "market", "lots": req.get("lots"),
                "closeMode": req.get("closeMode") or "auto",
            })
        elif path == "/api/settings":
            ok, msg = ACCOUNT.set_settings(req)
        elif path == "/api/reset":
            ok, msg = ACCOUNT.reset(bool(req.get("keepCash")))
        elif path == "/api/refresh":
            ok = MARKET.refresh()
            msg = "行情已刷新：%s" % MARKET.ts if ok else "行情刷新失败：%s" % MARKET.error
        elif path == "/api/acc/login":
            ok, msg, _u = ACC.login(req.get("loginName"), req.get("loginPwd"))
        elif path == "/api/acc/add":
            ok, msg, _u = ACC.add_account(
                req.get("loginName"), req.get("loginPwd"),
                trade_pwd=req.get("tradePwd"),
                first_bank=req.get("firstBank") if isinstance(req.get("firstBank"), dict) else None)
        elif path == "/api/acc/delete":
            ok, msg = ACC.delete_account(req.get("id") or ACC.data.get("current"),
                                         req.get("loginPwd"))
        elif path == "/api/bank/add":
            ok, msg = ACC.add_bank(req)
        elif path == "/api/bank/delete":
            ok, msg = ACC.delete_bank(req.get("id") or "", req.get("cardPwd"))
        elif path == "/api/acc/transfer":
            ok, msg = ACC.transfer(
                req.get("direction") or "转入", req.get("bankId") or "",
                req.get("amount"), req.get("bankPwd"),
                trade_pwd=req.get("tradePwd"),
                cash_cb=self._transfer_cash)
        else:
            self._json({"ok": False, "msg": "未知接口"}, 404)
            return

        self._json({"ok": bool(ok), "msg": msg, "ts": now_str()})

    # ---------- 银期转账的资金端 ----------
    def _transfer_cash(self, direction, amount, note):
        if direction == "转入":
            return ACCOUNT.deposit(amount, note)
        return ACCOUNT.withdraw(amount, note)


# ----------------------------------------------------------------------
# 后台线程
# ----------------------------------------------------------------------
def strategy_order(req):
    """把策略信号翻译成账户委托。

    策略在「主力连续」（如 RB0）上算信号，下单需换成可交易的具体月份合约。
    """
    code = (req.get("code") or "").strip().upper()
    var = variety_of(code)
    trade_code = code
    if main_contract(var):
        # 主力连续代码（VB0 形式）或非真实合约，一律换成当前主力月份合约
        info = MARKET.by_code.get(code)
        if not info or info.get("kind") != "real":
            trade_code = main_contract(var)
    if not trade_code or trade_code not in MARKET.by_code:
        return False, "找不到可交易的主力合约（%s）" % code, None
    pos = ACCOUNT.state.get("positions") or {}
    pos_list = list(pos.values()) if isinstance(pos, dict) else list(pos)
    if req.get("offset") == "平":
        want = "long" if req.get("direction") == "多" else "short"
        if not any(p.get("code") == trade_code and p.get("dir") == want
                   for p in pos_list):
            return False, "无对应持仓可平（%s）" % trade_code, None
    return ACCOUNT.place_order({
        "code": trade_code,
        "dir": "long" if req.get("direction") == "多" else "short",
        "offset": "open" if req.get("offset") == "开" else "close",
        "priceType": "market",
        "lots": req.get("lots") or 1,
        "closeMode": "auto",
    })


def tick_loop():
    n = 0
    while True:
        time.sleep(TICK_SEC)
        n += 1
        try:
            ACCOUNT.match_pending()
            ACCOUNT.risk_check()
        except Exception as e:                              # noqa: BLE001
            print("[warn] 撮合/风控异常：%s" % e)
        if n % 15 == 0:                                     # 约每 30 秒巡检策略
            try:
                STRATS.tick(order_fn=strategy_order, state_fn=mm_state)
            except Exception as e:                          # noqa: BLE001
                print("[warn] 策略巡检异常：%s" % e)


def sample_loop():
    n = 0
    while True:
        time.sleep(15)
        n += 1
        try:
            ACCOUNT.sample_equity()
        except Exception:                                   # noqa: BLE001
            pass


# ----------------------------------------------------------------------
# 启动
# ----------------------------------------------------------------------
def port_open(port, timeout=0.5):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout):
            return True
    except OSError:
        return False


def serves_our_page(port):
    url = "http://127.0.0.1:%d/api/status" % port
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=1.5) as r:
            return "accountFile" in r.read(4096).decode("utf-8", "ignore")
    except Exception:                                       # noqa: BLE001
        return False


def pick_port(start):
    for p in range(start, start + PROBE_PORTS):
        if not port_open(p):
            return p
    raise SystemExit("[!] %d~%d 端口均被占用，请用 --port 指定其它端口" % (start, start + PROBE_PORTS - 1))


def main():
    ap = argparse.ArgumentParser(description="国内期货模拟交易终端 · 本地服务")
    ap.add_argument("--port", type=int, default=PORT, help="监听端口（默认 8908）")
    ap.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    ap.add_argument("--offline", action="store_true", help="跳过联网，直接用离线快照")
    args, unknown = ap.parse_known_args()      # 忽略多余参数：双击快捷方式传参时不报错

    if PATHS.FROZEN:                            # 打包运行时给控制台窗口一个中文标题
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleTitleW("国内期货模拟交易终端 · 本地服务")
        except Exception:                       # noqa: BLE001
            pass

    print("=" * 64)
    print("  国内期货模拟交易终端")
    print("=" * 64)
    print("  运行方式：%s" % ("打包版 exe" if PATHS.FROZEN else "源码"))
    print("  数据目录：%s" % PATHS.APP_DIR)

    # 载入离线快照（先让页面有数据），再尝试联网刷新
    MARKET.load_snapshot()
    if not args.offline:
        print("[行情] 正在联网获取东方财富实时行情 ...")
        MARKET.refresh(quiet=True)
        if MARKET.error:
            print("[行情] 实时获取失败，继续使用离线快照：%s" % MARKET.error)
        else:
            print("[行情] 实时行情就绪：%d 个合约　%s" % (len(MARKET.contracts), MARKET.ts))

    threading.Thread(target=MARKET.loop, daemon=True).start()
    threading.Thread(target=tick_loop, daemon=True).start()
    threading.Thread(target=sample_loop, daemon=True).start()

    port = args.port
    if port_open(port):
        if serves_our_page(port):
            url = "http://127.0.0.1:%d/%s" % (port, urllib.parse.quote(PAGE))
            print("  服务已在运行，直接打开：%s" % url)
            if not args.no_browser:
                try:
                    webbrowser.open(url)
                except Exception:                           # noqa: BLE001
                    pass
            return
        port = pick_port(port + 1)
        print("[i] 端口 %d 被占用，改用 %d" % (args.port, port))

    url = "http://127.0.0.1:%d/%s" % (port, urllib.parse.quote(PAGE))
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError as e:
        print("[!] 无法监听端口 %d：%s" % (port, e))
        return

    with httpd:
        print("  面板地址：%s" % url)
        print("  账户数据：%s" % ACCOUNT.file)
        print("  行情来源：%s" % MARKET.source)
        print("-" * 64)
        if not args.no_browser:
            print("  已自动打开浏览器。")
        print("  关闭本窗口即停止服务（或按 Ctrl+C）")
        print("=" * 64)
        if not args.no_browser:
            try:
                webbrowser.open(url)
            except Exception:                               # noqa: BLE001
                pass
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            ACCOUNT.save()
            print("\n已停止，账户数据已保存。")


if __name__ == "__main__":
    main()
