# -*- coding: utf-8 -*-
"""
行情与 K 线引擎（纯标准库，零第三方依赖）

数据源：新浪财经期货接口
  · 日 K      https://stock2.finance.sina.com.cn/futures/api/jsonp.php/.../getDailyKLine?symbol=rb2610
  · 分钟 K    .../getFewMinLine?symbol=rb2610&type=5      （type: 1/3/5/15/30/60/120/240）
  · 分时      .../getMinLine?symbol=rb2610
  · 实时快照  https://hq.sinajs.cn/list=nf_RB2610          （主力看盘用；本终端主行情仍走东方财富）

说明：
  · 具体合约代码如 RB2610 用小写 rb2610；主力连续（长历史，回测用）用 VB0 形式，如 RB0 / M0 / CU0。
  · 具体合约当日无数据时自动回退「主力连续」，并在返回体里标记 fallback。
  · 内存 + 磁盘双层缓存，断网时回退磁盘缓存并标记 stale。

技术指标：纯 Python 实现，主图（MA/EMA/BOLL/SAR）与副图（VOL/MACD/KDJ/RSI/WR/BIAS/OBV/DMI/CCI/ATR）。
"""
import json
import math
import os
import re
import ssl
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime

ssl._create_default_https_context = ssl._create_unverified_context

import 路径工具 as PATHS

BASE_DIR = PATHS.APP_DIR                    # 可写数据基准目录（打包后 = exe 所在目录）
CACHE_DIR = PATHS.data("行情缓存")           # 行情缓存落在 exe 旁，退出不丢

SINA_REF = "https://finance.sina.com.cn"
SINA_JSONP = ("https://stock2.finance.sina.com.cn/futures/api/jsonp.php/"
              "var%20_x=/InnerFuturesNewService.")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# 周期表：key -> (显示名, 分钟数/None=日线, TTL 秒)
PERIODS = {
    "m1":   ("1分", 1, 60),
    "m3":   ("3分", 3, 60),
    "m5":   ("5分", 5, 60),
    "m15":  ("15分", 15, 90),
    "m30":  ("30分", 30, 90),
    "m60":  ("60分", 60, 120),
    "m120": ("2小时", 120, 180),
    "m240": ("4小时", 240, 300),
    "day":  ("日K", None, 300),
    "week": ("周K", None, 600),
    "month": ("月K", None, 900),
}
PERIOD_ORDER = ["m1", "m3", "m5", "m15", "m30", "m60", "m120", "m240",
                "day", "week", "month"]
DAY_LIKE = ("day", "week", "month")


# ----------------------------------------------------------------------
# HTTP
# ----------------------------------------------------------------------
def _get(url, referer=SINA_REF, timeout=20, retries=2):
    headers = {"User-Agent": UA, "Referer": referer, "Accept": "*/*"}
    last = None
    for i in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            raw = _OPENER.open(req, timeout=timeout).read()
            try:
                return raw.decode("gbk")
            except UnicodeDecodeError:
                return raw.decode("utf-8", "ignore")
        except Exception as e:                                # noqa: BLE001
            last = e
            time.sleep(0.35 * (i + 1))
    raise last


def _payload(text):
    """剥掉 jsonp 外壳： var _x=([...]);  ->  [...]"""
    t = text.strip()
    t = re.sub(r"^/\*.*?\*/", "", t, flags=re.S).strip()
    m = re.search(r"=\s*\((.*)\)\s*;?\s*$", t, re.S)
    return (m.group(1) if m else t).strip()


def _f(x, nd=4):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if v != v:
        return None
    return round(v, nd)


def _i(x):
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return None


def sina_codes(code, variety=""):
    """返回按优先级排列的新浪代码候选（具体合约 -> 大写 -> 主力连续）。"""
    c = (code or "").strip()
    v = (variety or "").strip().upper()
    cands = []
    if re.fullmatch(r"[A-Za-z]{1,3}\d{3,4}", c):              # 具体合约 RB2610 / MA610
        cands.append(c.lower())
        cands.append(c.upper())
        if v:
            cands.append(v + "0")
    elif re.fullmatch(r"[A-Za-z]{1,3}0", c):                  # 主力连续 RB0
        cands.append(c.upper())
        cands.append(c.lower())
    else:
        cands.append(c.lower())
        cands.append(c.upper())
    out = []
    for x in cands:
        if x and x not in out:
            out.append(x)
    return out


# ----------------------------------------------------------------------
# 原始数据抓取
# ----------------------------------------------------------------------
def fetch_daily(sina_code):
    """日 K： [{'d','o','h','l','c','v','p','s'}]"""
    body = _payload(_get(SINA_JSONP + "getDailyKLine?symbol=" + sina_code))
    try:
        arr = json.loads(body)
    except ValueError:
        return []
    out = []
    for it in arr if isinstance(arr, list) else []:
        c = _f(it.get("c"))
        if c is None:
            continue
        out.append({"d": str(it.get("d") or "")[:19],
                    "o": _f(it.get("o")), "h": _f(it.get("h")),
                    "l": _f(it.get("l")), "c": c,
                    "v": _i(it.get("v")), "p": _i(it.get("p")),
                    "s": _f(it.get("s"))})
    return out


def fetch_minutes(sina_code, mtype):
    """分钟 K： [{'d','o','h','l','c','v','p'}]"""
    url = "%sgetFewMinLine?symbol=%s&type=%d" % (SINA_JSONP, sina_code, mtype)
    body = _payload(_get(url))
    try:
        arr = json.loads(body)
    except ValueError:
        return []
    out = []
    for it in arr if isinstance(arr, list) else []:
        c = _f(it.get("c"))
        if c is None:
            continue
        out.append({"d": str(it.get("d") or "")[:19],
                    "o": _f(it.get("o")), "h": _f(it.get("h")),
                    "l": _f(it.get("l")), "c": c,
                    "v": _i(it.get("v")), "p": _i(it.get("p")), "s": None})
    return out


def fetch_timeshare(sina_code):
    """分时：[{'d','c','avg','v','oi'}]  + preClose / date"""
    body = _payload(_get(SINA_JSONP + "getMinLine?symbol=" + sina_code))
    try:
        arr = json.loads(body)
    except ValueError:
        return None
    if not isinstance(arr, list) or not arr:
        return None
    pts, pre, day = [], None, ""
    for row in arr:
        if not isinstance(row, list) or len(row) < 2:
            continue
        p = {"d": str(row[0]), "c": _f(row[1]),
             "avg": _f(row[2]) if len(row) > 2 else None,
             "v": _i(row[3]) if len(row) > 3 else None,
             "oi": _i(row[4]) if len(row) > 4 else None}
        if p["c"] is None:
            continue
        if pre is None and len(row) > 5:
            pre = _f(row[5])
        if not day and len(row) > 6:
            day = str(row[6])[:19]
        pts.append(p)
    if not pts:
        return None
    return {"date": day, "preClose": pre, "points": pts}


# ----------------------------------------------------------------------
# 周期重采样（周 / 月）
# ----------------------------------------------------------------------
def _dkey(d):
    return (d or "")[:10]


def resample(bars, mode):
    if mode not in ("week", "month") or not bars:
        return bars
    out, cur = [], None
    for b in bars:
        ds = _dkey(b["d"])
        try:
            t = datetime.strptime(ds, "%Y-%m-%d")
        except ValueError:
            continue
        if mode == "week":
            k = "%04d-W%02d" % t.isocalendar()[:2]
        else:
            k = "%04d-%02d" % (t.year, t.month)
        if cur is None or cur["k"] != k:
            if cur is not None:
                out.append(cur["b"])
            cur = {"k": k, "b": dict(b)}
        else:
            cb = cur["b"]
            cb["d"] = b["d"]
            cb["h"] = max(x for x in (cb["h"], b["h"]) if x is not None) \
                if (cb["h"] is not None or b["h"] is not None) else None
            cb["l"] = min(x for x in (cb["l"], b["l"]) if x is not None) \
                if (cb["l"] is not None or b["l"] is not None) else None
            cb["c"] = b["c"]
            cb["s"] = b["s"]
            cb["v"] = (cb["v"] or 0) + (b["v"] or 0)
            cb["p"] = b["p"] if b["p"] is not None else cb["p"]
    if cur is not None:
        out.append(cur["b"])
    return out


# ----------------------------------------------------------------------
# 技术指标（纯 Python）
# ----------------------------------------------------------------------
def _col(bars, k):
    return [b.get(k) for b in bars]


def MA(vals, n):
    n = max(1, int(n))
    out, s = [None] * len(vals), 0.0
    for i, v in enumerate(vals):
        if v is None:
            s = 0.0
            continue
        s += v
        if i >= n:
            s -= vals[i - n] or 0.0
        if i >= n - 1:
            out[i] = round(s / n, 4)
    return out


def EMA(vals, n):
    n = max(1, int(n))
    a = 2.0 / (n + 1.0)
    out, prev = [None] * len(vals), None
    for i, v in enumerate(vals):
        if v is None:
            continue
        prev = v if prev is None else prev + a * (v - prev)
        out[i] = round(prev, 4)
    return out


def SMA_CN(vals, n, m=1):
    """通达信 SMA(X,N,M): y = (M*x + (N-M)*y') / N"""
    n = max(1, int(n))
    m = max(1, int(m))
    out, prev = [None] * len(vals), None
    for i, v in enumerate(vals):
        if v is None:
            continue
        prev = v if prev is None else (m * v + (n - m) * prev) / float(n)
        out[i] = round(prev, 4)
    return out


def STD(vals, n):
    n = max(1, int(n))
    out = [None] * len(vals)
    for i in range(len(vals)):
        if i < n - 1:
            continue
        win = [v for v in vals[i - n + 1:i + 1] if v is not None]
        if len(win) < 2:
            continue
        mu = sum(win) / len(win)
        out[i] = round(math.sqrt(sum((x - mu) ** 2 for x in win) / len(win)), 4)
    return out


def HHV(vals, n):
    n = max(1, int(n))
    out = [None] * len(vals)
    for i in range(len(vals)):
        if i < n - 1:
            continue
        win = [v for v in vals[i - n + 1:i + 1] if v is not None]
        out[i] = max(win) if win else None
    return out


def LLV(vals, n):
    n = max(1, int(n))
    out = [None] * len(vals)
    for i in range(len(vals)):
        if i < n - 1:
            continue
        win = [v for v in vals[i - n + 1:i + 1] if v is not None]
        out[i] = min(win) if win else None
    return out


def BOLL(closes, n=20, k=2.0):
    mid = MA(closes, n)
    sd = STD(closes, n)
    up = [None if (m is None or s is None) else round(m + k * s, 4)
          for m, s in zip(mid, sd)]
    dn = [None if (m is None or s is None) else round(m - k * s, 4)
          for m, s in zip(mid, sd)]
    return mid, up, dn


def MACD(closes, fast=12, slow=26, sig=9):
    ef, es = EMA(closes, fast), EMA(closes, slow)
    dif = [None if (a is None or b is None) else round(a - b, 4)
           for a, b in zip(ef, es)]
    dea = EMA(dif, sig)
    bar = [None if (a is None or b is None) else round(2 * (a - b), 4)
           for a, b in zip(dif, dea)]
    return dif, dea, bar


def KDJ(bars, n=9, k=3, d=3):
    n, k, d = max(1, int(n)), max(1, int(k)), max(1, int(d))
    h, l, c = _col(bars, "h"), _col(bars, "l"), _col(bars, "c")
    hh, ll = HHV(h, n), LLV(l, n)
    rsv = [None] * len(bars)
    for i in range(len(bars)):
        if hh[i] is None or ll[i] is None or c[i] is None:
            continue
        rng = hh[i] - ll[i]
        rsv[i] = 50.0 if rng <= 0 else (c[i] - ll[i]) / rng * 100.0
    kv = SMA_CN(rsv, k, 1)
    dv = SMA_CN(kv, d, 1)
    jv = [None if (a is None or b is None) else round(3 * a - 2 * b, 4)
          for a, b in zip(kv, dv)]
    return kv, dv, jv


def RSI(closes, n=14):
    n = max(1, int(n))
    out = [None] * len(closes)
    au = ad = None
    for i in range(1, len(closes)):
        if closes[i] is None or closes[i - 1] is None:
            continue
        ch = closes[i] - closes[i - 1]
        up, dn = (ch, 0.0) if ch > 0 else (0.0, -ch)
        if au is None:
            au, ad = up, dn
        else:
            au = (au * (n - 1) + up) / n
            ad = (ad * (n - 1) + dn) / n
        tot = au + ad
        out[i] = round(100.0, 4) if tot <= 0 else round(au / tot * 100.0, 4)
    return out


def WR(bars, n=10):
    n = max(1, int(n))
    h, l, c = _col(bars, "h"), _col(bars, "l"), _col(bars, "c")
    hh, ll = HHV(h, n), LLV(l, n)
    out = [None] * len(bars)
    for i in range(len(bars)):
        if hh[i] is None or ll[i] is None or c[i] is None:
            continue
        rng = hh[i] - ll[i]
        out[i] = 50.0 if rng <= 0 else round((hh[i] - c[i]) / rng * 100.0, 4)
    return out


def BIAS(closes, n=6):
    m = MA(closes, max(1, int(n)))
    return [None if (a is None or not b) else round((a - b) / b * 100.0, 4)
            for a, b in zip(closes, m)]


def OBV(bars, n=30):
    c, v = _col(bars, "c"), _col(bars, "v")
    out = [None] * len(bars)
    cur = 0.0
    for i in range(len(bars)):
        if c[i] is None:
            continue
        vv = v[i] or 0
        if i == 0 or c[i - 1] is None:
            cur = float(vv)
        elif c[i] > c[i - 1]:
            cur += vv
        elif c[i] < c[i - 1]:
            cur -= vv
        out[i] = cur
    return out, MA(out, n)


def DMI(bars, n=14, m=6):
    n = max(1, int(n))
    m = max(1, int(m))
    h, l, c = _col(bars, "h"), _col(bars, "l"), _col(bars, "c")
    L = len(bars)
    tr, pdm, ndm = [None] * L, [None] * L, [None] * L
    for i in range(1, L):
        if None in (h[i], l[i], h[i - 1], l[i - 1]):
            continue
        tr[i] = max(h[i] - l[i], abs(h[i] - c[i - 1] or 0),
                    abs(l[i] - c[i - 1] or 0))
        up, dn = h[i] - h[i - 1], l[i - 1] - l[i]
        pdm[i] = up if (up > dn and up > 0) else 0.0
        ndm[i] = dn if (dn > up and dn > 0) else 0.0
    str_, sp, sn = [None] * L, [None] * L, [None] * L
    a, b, d = 0.0, 0.0, 0.0
    for i in range(L):
        if tr[i] is None:
            continue
        if str_[i - 1] is None and i > 0:
            a, b, d = tr[i] or 0, pdm[i] or 0, ndm[i] or 0
        else:
            a = (a * (n - 1) + (tr[i] or 0)) / n
            b = (b * (n - 1) + (pdm[i] or 0)) / n
            d = (d * (n - 1) + (ndm[i] or 0)) / n
        str_[i], sp[i], sn[i] = a, b, d
    pdi = [None if not str_[i] else round(sp[i] / str_[i] * 100, 4) for i in range(L)]
    ndi = [None if not str_[i] else round(sn[i] / str_[i] * 100, 4) for i in range(L)]
    dx = [None if (pdi[i] is None or ndi[i] is None or (pdi[i] + ndi[i]) == 0)
          else round(abs(pdi[i] - ndi[i]) / (pdi[i] + ndi[i]) * 100, 4)
          for i in range(L)]
    adx = SMA_CN([x if x is not None else 0.0 for x in dx], m, 1)
    adx = [None if dx[i] is None else adx[i] for i in range(L)]
    adxr = [None if (i < m or adx[i] is None or adx[i - m] is None)
            else round((adx[i] + adx[i - m]) / 2, 4) for i in range(L)]
    return pdi, ndi, adx, adxr


def CCI(bars, n=14):
    n = max(1, int(n))
    h, l, c = _col(bars, "h"), _col(bars, "l"), _col(bars, "c")
    L = len(bars)
    tp = [None if None in (h[i], l[i], c[i]) else (h[i] + l[i] + c[i]) / 3.0
          for i in range(L)]
    m = MA(tp, n)
    out = [None] * L
    for i in range(L):
        if i < n - 1 or m[i] is None:
            continue
        win = [x for x in tp[i - n + 1:i + 1] if x is not None]
        if not win:
            continue
        md = sum(abs(x - m[i]) for x in win) / len(win)
        out[i] = 0.0 if md == 0 else round((tp[i] - m[i]) / (0.015 * md), 4)
    return out


def ATR(bars, n=14):
    n = max(1, int(n))
    h, l, c = _col(bars, "h"), _col(bars, "l"), _col(bars, "c")
    L = len(bars)
    out = [None] * L
    prev = None
    for i in range(L):
        if i == 0 or None in (h[i], l[i], c[i - 1]):
            continue
        tr = max((h[i] or 0) - (l[i] or 0), abs((h[i] or 0) - c[i - 1]),
                 abs((l[i] or 0) - c[i - 1]))
        prev = tr if prev is None else (prev * (n - 1) + tr) / n
        out[i] = round(prev, 4)
    return out


def SAR(bars, af=0.02, maxaf=0.2):
    """抛物线 SAR（主图叠加）。"""
    h, l = _col(bars, "h"), _col(bars, "l")
    L = len(bars)
    out = [None] * L
    idx = [i for i in range(L) if h[i] is not None and l[i] is not None]
    if len(idx) < 3:
        return out
    i0 = idx[0]
    trend, ep = 1, h[i0]
    sar, acc = l[i0], af
    out[i0] = sar
    for k in range(1, len(idx)):
        i = idx[k]
        prev_i = idx[k - 1]
        sar = sar + acc * (ep - sar)
        if trend > 0:
            sar = min(sar, l[prev_i], l[i0] if k == 1 else l[idx[k - 2]])
            if l[i] < sar:
                trend, sar, ep, acc = -1, ep, l[i], af
            elif h[i] > ep:
                ep, acc = h[i], min(acc + af, maxaf)
        else:
            sar = max(sar, h[prev_i], h[i0] if k == 1 else h[idx[k - 2]])
            if h[i] > sar:
                trend, sar, ep, acc = 1, ep, h[i], af
            elif l[i] < ep:
                ep, acc = l[i], min(acc + af, maxaf)
        out[i] = round(sar, 4)
    return out


# ----------------------------------------------------------------------
# 指标注册表
# ----------------------------------------------------------------------
LINE_COLORS = ["#2563eb", "#e8a33d", "#7c3aed", "#0891b2", "#db2777",
               "#65a30d", "#b45309", "#0f766e"]

INDICATORS = {
    # ---- 主图 ----
    "MA":   {"kind": "main", "default": [5, 10, 20, 60], "args": "周期1,周期2,...",
             "desc": "简单移动平均"},
    "EMA":  {"kind": "main", "default": [12, 26], "args": "周期1,周期2,...",
             "desc": "指数移动平均"},
    "BOLL": {"kind": "main", "default": [20, 2], "args": "周期,倍数",
             "desc": "布林带（中轨/上轨/下轨）"},
    "SAR":  {"kind": "main", "default": [4, 2, 2], "args": "起始步长(%),极值(%),步长增量(%)（仅展示用）",
             "desc": "抛物线转向"},
    # ---- 副图 ----
    "VOL":  {"kind": "sub", "default": [5, 10, 20], "args": "均量周期1,周期2,...",
             "desc": "成交量与均量"},
    "MACD": {"kind": "sub", "default": [12, 26, 9], "args": "快线,慢线,信号",
             "desc": "指数平滑异同平均"},
    "KDJ":  {"kind": "sub", "default": [9, 3, 3], "args": "N,K,D",
             "desc": "随机指标"},
    "RSI":  {"kind": "sub", "default": [6, 12, 24], "args": "周期1,周期2,...",
             "desc": "相对强弱指标"},
    "WR":   {"kind": "sub", "default": [10, 6], "args": "周期1,周期2",
             "desc": "威廉指标"},
    "BIAS": {"kind": "sub", "default": [6, 12, 24], "args": "周期1,周期2,...",
             "desc": "乖离率"},
    "OBV":  {"kind": "sub", "default": [30], "args": "均线周期",
             "desc": "能量潮"},
    "DMI":  {"kind": "sub", "default": [14, 6], "args": "N,M",
             "desc": "动向指标（+DI/-DI/ADX/ADXR）"},
    "CCI":  {"kind": "sub", "default": [14], "args": "周期",
             "desc": "顺势指标"},
    "ATR":  {"kind": "sub", "default": [14], "args": "周期",
             "desc": "平均真实波幅"},
}
MAIN_LIST = [k for k, v in INDICATORS.items() if v["kind"] == "main"]
SUB_LIST = [k for k, v in INDICATORS.items() if v["kind"] == "sub"]


def parse_spec(spec):
    """'MACD(12,26,9)' -> ('MACD', [12,26,9])"""
    s = (spec or "").strip().upper().replace("（", "(").replace("）", ")")
    m = re.match(r"^([A-Z]+)\s*(?:\(([^)]*)\))?$", s)
    if not m:
        return None, []
    name = m.group(1)
    args = []
    if m.group(2):
        for part in m.group(2).split(","):
            part = part.strip()
            if not part:
                continue
            try:
                args.append(float(part))
            except ValueError:
                pass
    return name, args


def indicator_item(spec, bars):
    """计算单个指标，返回统一结构（供前端 ECharts 渲染）。"""
    name, args = parse_spec(spec)
    if name not in INDICATORS:
        return None
    meta = INDICATORS[name]
    if not args:
        args = list(meta["default"])
    closes = _col(bars, "c")
    item = {"id": name, "spec": "%s(%s)" % (name, ",".join(
        ("%g" % a) for a in args)), "name": name, "kind": meta["kind"],
        "desc": meta["desc"], "args": args, "lines": [], "bars": None,
        "marks": []}

    if name == "MA":
        for i, n in enumerate(args):
            item["lines"].append({"label": "MA%g" % n, "data": MA(closes, n),
                                  "color": LINE_COLORS[i % len(LINE_COLORS)]})
    elif name == "EMA":
        for i, n in enumerate(args):
            item["lines"].append({"label": "EMA%g" % n, "data": EMA(closes, n),
                                  "color": LINE_COLORS[i % len(LINE_COLORS)]})
    elif name == "BOLL":
        n, k = args[0], (args[1] if len(args) > 1 else 2)
        mid, up, dn = BOLL(closes, n, k)
        item["lines"] = [
            {"label": "BOLL中轨", "data": mid, "color": "#2563eb"},
            {"label": "BOLL上轨", "data": up, "color": "#db2777"},
            {"label": "BOLL下轨", "data": dn, "color": "#0891b2"}]
    elif name == "SAR":
        item["lines"] = [{"label": "SAR", "data": SAR(bars),
                          "color": "#e8a33d", "type": "scatter"}]
    elif name == "VOL":
        vols = _col(bars, "v")
        item["bars"] = {"label": "成交量", "data": vols, "volColor": True}
        for i, n in enumerate(args):
            item["lines"].append({"label": "MAVOL%g" % n, "data": MA(vols, n),
                                  "color": LINE_COLORS[i % len(LINE_COLORS)]})
    elif name == "MACD":
        f, s, g = args[0], args[1], (args[2] if len(args) > 2 else 9)
        dif, dea, bar = MACD(closes, f, s, g)
        item["bars"] = {"label": "MACD", "data": bar, "zeroColor": True}
        item["lines"] = [{"label": "DIF", "data": dif, "color": "#2563eb"},
                         {"label": "DEA", "data": dea, "color": "#e8a33d"}]
    elif name == "KDJ":
        n, k, d = args[0], (args[1] if len(args) > 1 else 3), \
            (args[2] if len(args) > 2 else 3)
        kv, dv, jv = KDJ(bars, n, k, d)
        item["lines"] = [{"label": "K", "data": kv, "color": "#2563eb"},
                         {"label": "D", "data": dv, "color": "#e8a33d"},
                         {"label": "J", "data": jv, "color": "#db2777"}]
        item["guides"] = [20, 80]
    elif name == "RSI":
        for i, n in enumerate(args):
            item["lines"].append({"label": "RSI%g" % n, "data": RSI(closes, n),
                                  "color": LINE_COLORS[i % len(LINE_COLORS)]})
        item["guides"] = [30, 70]
    elif name == "WR":
        for i, n in enumerate(args):
            item["lines"].append({"label": "WR%g" % n, "data": WR(bars, n),
                                  "color": LINE_COLORS[i % len(LINE_COLORS)]})
        item["guides"] = [20, 80]
    elif name == "BIAS":
        for i, n in enumerate(args):
            item["lines"].append({"label": "BIAS%g" % n, "data": BIAS(closes, n),
                                  "color": LINE_COLORS[i % len(LINE_COLORS)]})
        item["guides"] = [0]
    elif name == "OBV":
        obv, ma = OBV(bars, args[0] if args else 30)
        item["lines"] = [{"label": "OBV", "data": obv, "color": "#2563eb"},
                         {"label": "MAOBV", "data": ma, "color": "#e8a33d"}]
    elif name == "DMI":
        n, m = args[0], (args[1] if len(args) > 1 else 6)
        pdi, ndi, adx, adxr = DMI(bars, n, m)
        item["lines"] = [{"label": "PDI", "data": pdi, "color": "#d8342b"},
                         {"label": "MDI", "data": ndi, "color": "#0a9d5c"},
                         {"label": "ADX", "data": adx, "color": "#2563eb"},
                         {"label": "ADXR", "data": adxr, "color": "#e8a33d"}]
    elif name == "CCI":
        item["lines"] = [{"label": "CCI%g" % args[0], "data": CCI(bars, args[0]),
                          "color": "#7c3aed"}]
        item["guides"] = [-100, 100]
    elif name == "ATR":
        item["lines"] = [{"label": "ATR%g" % args[0], "data": ATR(bars, args[0]),
                          "color": "#0891b2"}]
    return item


def compute_main(specs, bars):
    out = []
    for s in specs:
        it = indicator_item(s, bars)
        if it and it["kind"] == "main":
            out.append(it)
    return out


def compute_sub(specs, bars):
    out = []
    for s in specs:
        it = indicator_item(s, bars)
        if it and it["kind"] == "sub":
            out.append(it)
    return out


# ----------------------------------------------------------------------
# K 线仓库（内存 + 磁盘缓存）
# ----------------------------------------------------------------------
class KlineStore(object):
    def __init__(self):
        self.lock = threading.RLock()
        self.mem = {}                 # key -> (ts, payload)
        self.fails = {}               # key -> (ts, err)
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
        except OSError:
            pass

    # ---------- 磁盘 ----------
    @staticmethod
    def _path(code, period):
        safe = re.sub(r"[^0-9A-Za-z_]+", "_", "%s_%s" % (code, period))
        return os.path.join(CACHE_DIR, "k_%s.json" % safe)

    def _disk_read(self, code, period):
        try:
            with open(self._path(code, period), "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def _disk_write(self, code, period, payload):
        try:
            tmp = self._path(code, period) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
            os.replace(tmp, self._path(code, period))
        except OSError:
            pass

    # ---------- 抓取 ----------
    def _load_remote(self, code, period, variety=""):
        """返回 (bars, symbol, fallback)；失败抛异常。"""
        if period in DAY_LIKE:
            for i, sym in enumerate(sina_codes(code, variety)):
                bars = fetch_daily(sym)
                if bars:
                    return (resample(bars, period), sym, i > 0)
            raise RuntimeError("新浪无该合约日K数据")
        mtype = PERIODS[period][1]
        for i, sym in enumerate(sina_codes(code, variety)):
            bars = fetch_minutes(sym, mtype)
            if bars:
                return (bars, sym, i > 0)
        raise RuntimeError("新浪无该合约分钟K数据")

    def get(self, code, period="day", limit=0, variety="", name="",
            force=False):
        """取 K 线。返回 dict（含 bars / 指标可另算）。"""
        code = (code or "").strip().upper()
        period = period if period in PERIODS else "day"
        key = "%s|%s" % (code, period)
        ttl = PERIODS[period][2]
        now = time.time()

        with self.lock:
            hit = self.mem.get(key)
            if hit and not force and now - hit[0] < ttl:
                return self._trim(hit[1], limit)

        err = None
        try:
            bars, sym, fb = self._load_remote(code, period, variety)
            payload = {"code": code, "period": period, "symbol": sym,
                       "fallback": fb, "stale": False, "source": "新浪财经",
                       "fetchedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                       "bars": bars}
            with self.lock:
                self.mem[key] = (now, payload)
            self._disk_write(code, period, payload)
            return self._trim(payload, limit)
        except Exception as e:                                # noqa: BLE001
            err = e

        with self.lock:
            self.fails[key] = (now, str(err))
        disk = self._disk_read(code, period)
        if disk and disk.get("bars"):
            disk = dict(disk)
            disk["stale"] = True
            disk["error"] = str(err)
            with self.lock:
                self.mem[key] = (now - ttl + 20, disk)        # 短 TTL 重试
            return self._trim(disk, limit)
        raise RuntimeError("行情获取失败：%s" % err)

    def timeshare(self, code, variety="", force=False):
        code = (code or "").strip().upper()
        key = "%s|fs" % code
        now = time.time()
        with self.lock:
            hit = self.mem.get(key)
            if hit and not force and now - hit[0] < 30:
                return hit[1]
        last = None
        for sym in sina_codes(code, variety):
            try:
                d = fetch_timeshare(sym)
            except Exception as e:                            # noqa: BLE001
                last = e
                continue
            if d and d.get("points"):
                d.update({"code": code, "symbol": sym, "source": "新浪财经",
                          "stale": False,
                          "fetchedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
                with self.lock:
                    self.mem[key] = (now, d)
                return d
        raise RuntimeError("分时数据获取失败：%s" % (last or "无数据"))

    @staticmethod
    def _trim(payload, limit):
        if not limit or limit <= 0:
            return payload
        lim = max(30, int(limit))
        if len(payload.get("bars") or []) > lim:
            p = dict(payload)
            p["bars"] = payload["bars"][-lim:]
            p["trimmed"] = len(payload["bars"]) - lim
            return p
        return payload

    def peek(self, code, period="day"):
        """只读缓存（不联网），有则返回。"""
        code = (code or "").strip().upper()
        with self.lock:
            hit = self.mem.get("%s|%s" % (code, period))
            if hit:
                return hit[1]
        return self._disk_read(code, period)


KLINE = KlineStore()
