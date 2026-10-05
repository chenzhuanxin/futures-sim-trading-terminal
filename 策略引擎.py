# -*- coding: utf-8 -*-
"""
策略引擎（纯标准库）

职责：
  · 策略模板库（8 类内置策略，含用户要求的「穿越 N 周期均线」示例）
  · 策略信号计算：统一用「目标仓位序列」模型 —— 每个策略输出 target[i] ∈ {+1, 0, -1}，
    再由统一转换器变成「开多 / 平多 / 开空 / 平空」动作，回测与实盘共用同一套信号。
  · 资金管理与仓位管理：每个策略带一份 mm 配置（手数模式 / 止损方式 / 风险闸门 / 加仓），
    由「资金管理」模块执行，回测与实盘同一口径 —— 策略只负责方向，不负责下多少手。
  · 策略选股：在品种池（全部主力连续 / 某板块 / 指定合约）上扫描，输出最新有效信号。
  · 策略交易：把策略挂载为自动交易，后台巡检，出现新信号即按资金管理算出的手数下单。
  · 持久化：账户数据/strategies.json
"""
import json
import os
import re
import threading
import time
import uuid
from datetime import datetime

import 行情K线 as K
import 资金管理 as MM
from 行情K线 import (MA, EMA, MACD, KDJ, RSI, WR, BIAS, BOLL, HHV, LLV,
                     DMI, CCI, ATR, SAR, OBV, SMA_CN)

import 路径工具 as PATHS

BASE_DIR = PATHS.APP_DIR                    # 可写数据基准目录（打包后 = exe 所在目录）
STATE_DIR = PATHS.data("账户数据")           # 策略库与账户同目录，退出不丢
STRAT_FILE = os.path.join(STATE_DIR, "strategies.json")


# ----------------------------------------------------------------------
# 工具
# ----------------------------------------------------------------------
def _iarg(p, key, dflt):
    try:
        return int(float(p.get(key, dflt)))
    except (TypeError, ValueError):
        return dflt


def _farg(p, key, dflt):
    try:
        return float(p.get(key, dflt))
    except (TypeError, ValueError):
        return dflt


def _g(v, i):
    if v is None or i < 0 or i >= len(v):
        return None
    return v[i]


def cross_up(f, s, i):
    """f 上穿 s（第 i 根）"""
    a0, b0, a1, b1 = _g(f, i - 1), _g(s, i - 1), _g(f, i), _g(s, i)
    if None in (a0, b0, a1, b1):
        return False
    return a0 <= b0 and a1 > b1


def cross_dn(f, s, i):
    a0, b0, a1, b1 = _g(f, i - 1), _g(s, i - 1), _g(f, i), _g(s, i)
    if None in (a0, b0, a1, b1):
        return False
    return a0 >= b0 and a1 < b1


def _sgn(v):
    return "+" if v >= 0 else ""


# ----------------------------------------------------------------------
# 策略模板：每个模板返回 (targets, reasons)
#   targets[i]  目标仓位：1=持多, 0=空仓, -1=持空；None=尚未开始
#   reasons[i]  第 i 根发生「目标变化」的原因文字
# ----------------------------------------------------------------------
def t_ma_cross(bars, p):
    """穿越 N 周期均线（用户示例：穿越 5 日日 K 均线）"""
    n = max(1, _iarg(p, "周期", 5))
    c = [b["c"] for b in bars]
    ma = MA(c, n)
    tgt, why, cur = [None] * len(bars), [None] * len(bars), 0
    for i in range(1, len(bars)):
        if ma[i] is None or ma[i - 1] is None:
            continue
        if cross_up(c, ma, i):
            cur = 1
            why[i] = "收盘 %g 上穿 MA%d %g" % (c[i], n, ma[i])
        elif cross_dn(c, ma, i):
            cur = -1
            why[i] = "收盘 %g 下穿 MA%d %g" % (c[i], n, ma[i])
        tgt[i] = cur
    return tgt, why


def t_ma2_cross(bars, p):
    """双均线金叉死叉"""
    f = max(1, _iarg(p, "快周期", 5))
    s = max(2, _iarg(p, "慢周期", 20))
    if s <= f:
        s = f + 1
    c = [b["c"] for b in bars]
    mf, ms = MA(c, f), MA(c, s)
    tgt, why, cur = [None] * len(bars), [None] * len(bars), 0
    for i in range(1, len(bars)):
        if cross_up(mf, ms, i):
            cur = 1
            why[i] = "MA%d %g 上穿 MA%d %g（金叉）" % (f, mf[i], s, ms[i])
        elif cross_dn(mf, ms, i):
            cur = -1
            why[i] = "MA%d %g 下穿 MA%d %g（死叉）" % (f, mf[i], s, ms[i])
        tgt[i] = cur
    return tgt, why


def t_macd_cross(bars, p):
    """MACD 金叉死叉"""
    fa, sl, sg = (_iarg(p, "快线", 12), _iarg(p, "慢线", 26), _iarg(p, "信号", 9))
    c = [b["c"] for b in bars]
    dif, dea, _ = MACD(c, fa, sl, sg)
    tgt, why, cur = [None] * len(bars), [None] * len(bars), 0
    for i in range(1, len(bars)):
        if cross_up(dif, dea, i):
            cur = 1
            why[i] = "DIF %g 上穿 DEA %g（金叉）" % (dif[i], dea[i])
        elif cross_dn(dif, dea, i):
            cur = -1
            why[i] = "DIF %g 下穿 DEA %g（死叉）" % (dif[i], dea[i])
        tgt[i] = cur
    return tgt, why


def t_kdj_cross(bars, p):
    """KDJ 金叉死叉（可限定低位/高位区）"""
    n, k, d = _iarg(p, "N", 9), _iarg(p, "K", 3), _iarg(p, "D", 3)
    low, high = _farg(p, "低位", 30), _farg(p, "高位", 70)
    kv, dv, _j = KDJ(bars, n, k, d)
    tgt, why, cur = [None] * len(bars), [None] * len(bars), 0
    for i in range(1, len(bars)):
        if None in (_g(kv, i), _g(dv, i), _g(kv, i - 1), _g(dv, i - 1)):
            continue
        if cross_up(kv, dv, i) and (low <= 0 or dv[i] <= low):
            cur = 1
            why[i] = "K %g 上穿 D %g（低位金叉，D≤%g）" % (kv[i], dv[i], low)
        elif cross_dn(kv, dv, i) and (high >= 100 or dv[i] >= high):
            cur = -1
            why[i] = "K %g 下穿 D %g（高位死叉，D≥%g）" % (kv[i], dv[i], high)
        elif cross_dn(kv, dv, i):
            cur = 0
            why[i] = "K %g 下穿 D %g（死叉离场）" % (kv[i], dv[i])
        tgt[i] = cur
    return tgt, why


def t_boll_break(bars, p):
    """布林带突破：上穿上轨开多，跌破中轨离场；下穿下轨开空，升破中轨离场"""
    n = max(2, _iarg(p, "周期", 20))
    k = _farg(p, "倍数", 2)
    c = [b["c"] for b in bars]
    mid, up, dn = BOLL(c, n, k)
    tgt, why, cur = [None] * len(bars), [None] * len(bars), 0
    for i in range(1, len(bars)):
        if None in (_g(mid, i), _g(up, i), _g(dn, i)):
            continue
        if cur >= 0 and c[i] > up[i]:
            cur = 1
            why[i] = "收盘 %g 上穿布林上轨 %g" % (c[i], up[i])
        elif cur <= 0 and c[i] < dn[i]:
            cur = -1
            why[i] = "收盘 %g 下穿布林下轨 %g" % (c[i], dn[i])
        elif cur == 1 and c[i] < mid[i]:
            cur = 0
            why[i] = "收盘 %g 跌破布林中轨 %g（离场）" % (c[i], mid[i])
        elif cur == -1 and c[i] > mid[i]:
            cur = 0
            why[i] = "收盘 %g 升破布林中轨 %g（离场）" % (c[i], mid[i])
        tgt[i] = cur
    return tgt, why


def t_donchian(bars, p):
    """唐奇安通道突破（海龟式）"""
    en = max(2, _iarg(p, "入场周期", 20))
    ex = max(1, _iarg(p, "出场周期", 10))
    c = [b["c"] for b in bars]
    hh = HHV([b["h"] for b in bars], en)
    ll = LLV([b["l"] for b in bars], en)
    xh = HHV([b["h"] for b in bars], ex)
    xl = LLV([b["l"] for b in bars], ex)
    tgt, why, cur = [None] * len(bars), [None] * len(bars), 0
    for i in range(1, len(bars)):
        # 用「上一根为止」的通道，避免含当根的未来信息
        up_ref, dn_ref = _g(hh, i - 1), _g(ll, i - 1)
        xu, xd = _g(xh, i - 1), _g(xl, i - 1)
        if up_ref is not None and c[i] > up_ref and cur <= 0:
            cur = 1
            why[i] = "收盘 %g 突破 %d 周期高点 %g" % (c[i], en, up_ref)
        elif dn_ref is not None and c[i] < dn_ref and cur >= 0:
            cur = -1
            why[i] = "收盘 %g 跌破 %d 周期低点 %g" % (c[i], en, dn_ref)
        elif cur == 1 and xd is not None and c[i] < xd:
            cur = 0
            why[i] = "收盘 %g 跌破 %d 周期出场低点 %g" % (c[i], ex, xd)
        elif cur == -1 and xu is not None and c[i] > xu:
            cur = 0
            why[i] = "收盘 %g 升破 %d 周期出场高点 %g" % (c[i], ex, xu)
        tgt[i] = cur
    return tgt, why


def t_rsi_reversal(bars, p):
    """RSI 超卖回升开多 / 超买回落开空"""
    n = max(2, _iarg(p, "周期", 14))
    low, high = _farg(p, "超卖", 30), _farg(p, "超买", 70)
    c = [b["c"] for b in bars]
    r = RSI(c, n)
    tgt, why, cur = [None] * len(bars), [None] * len(bars), 0
    for i in range(1, len(bars)):
        if None in (_g(r, i), _g(r, i - 1)):
            continue
        if r[i - 1] <= low < r[i]:
            cur = 1
            why[i] = "RSI%d 由 %g 上穿超卖线 %g" % (n, r[i], low)
        elif r[i - 1] >= high > r[i]:
            cur = -1
            why[i] = "RSI%d 由 %g 下穿超买线 %g" % (n, r[i], high)
        tgt[i] = cur
    return tgt, why


def _ref_series(expr, bars, closes):
    """把 'MA(5)' / 'CLOSE' / '30' 解析为序列或常数序列"""
    e = str(expr or "").strip().upper()
    if e in ("CLOSE", "C", "收盘价", "收盘"):
        return list(closes), "收盘价"
    if e in ("OPEN", "O", "开盘价", "开盘"):
        return [b["o"] for b in bars], "开盘价"
    if e in ("HIGH", "H", "最高价", "最高"):
        return [b["h"] for b in bars], "最高价"
    if e in ("LOW", "L", "最低价", "最低"):
        return [b["l"] for b in bars], "最低价"
    try:
        v = float(e)
        return [v] * len(bars), "%g" % v
    except ValueError:
        pass
    name, args = K.parse_spec(e)
    if name in K.INDICATORS:
        it = K.indicator_item(e, bars)
        if it and it["lines"]:
            return it["lines"][0]["data"], it["lines"][0]["label"]
    return [None] * len(bars), e


def t_custom(bars, p):
    """自定义条件组合：左值 算子 右值"""
    c = [b["c"] for b in bars]
    a, an = _ref_series(p.get("左值", "MA(5)"), bars, c)
    b, bn = _ref_series(p.get("右值", "MA(20)"), bars, c)
    op = str(p.get("算子", "上穿"))
    tgt, why, cur = [None] * len(bars), [None] * len(bars), 0
    for i in range(1, len(bars)):
        av, bv = _g(a, i), _g(b, i)
        if av is None or bv is None:
            continue
        hit_up = hit_dn = False
        if op == "上穿":
            hit_up, hit_dn = cross_up(a, b, i), cross_dn(a, b, i)
        elif op == "下穿":
            # 反向理解：下穿做多、上穿做空（均值回归用法）
            hit_up, hit_dn = cross_dn(a, b, i), cross_up(a, b, i)
        elif op == "大于":
            hit_up, hit_dn = av > bv, av < bv
        elif op == "小于":
            hit_up, hit_dn = av < bv, av > bv
        if hit_up:
            cur = 1
            why[i] = "%s %g %s %s %g → 做多" % (an, av, op, bn, bv)
        elif hit_dn:
            cur = -1
            why[i] = "%s %g %s %s %g → 做空" % (an, av, op, bn, bv)
        tgt[i] = cur
    return tgt, why


TEMPLATES = [
    {"kind": "ma_cross", "name": "穿越N周期均线", "fn": t_ma_cross,
     "period": "day", "minBars": 30, "star": True,
     "desc": "收盘价上穿均线做多、下穿均线做空（默认日K × MA5，即「穿越 5 日日 K 均线」）",
     "params": {"周期": 5, "方向": "双向"}},
    {"kind": "ma2_cross", "name": "双均线金叉死叉", "fn": t_ma2_cross,
     "period": "day", "minBars": 60,
     "desc": "快线上穿慢线（金叉）做多，下穿（死叉）做空",
     "params": {"快周期": 5, "慢周期": 20, "方向": "双向"}},
    {"kind": "macd_cross", "name": "MACD 金叉死叉", "fn": t_macd_cross,
     "period": "day", "minBars": 80,
     "desc": "DIF 上穿 DEA 做多，下穿做空",
     "params": {"快线": 12, "慢线": 26, "信号": 9, "方向": "双向"}},
    {"kind": "kdj_cross", "name": "KDJ 低位金叉 / 高位死叉", "fn": t_kdj_cross,
     "period": "day", "minBars": 40,
     "desc": "K 在低位上穿 D 做多，在高位下穿 D 做空（0 表示不做区域限制）",
     "params": {"N": 9, "K": 3, "D": 3, "低位": 30, "高位": 70, "方向": "双向"}},
    {"kind": "boll_break", "name": "布林带突破", "fn": t_boll_break,
     "period": "day", "minBars": 60,
     "desc": "上穿上轨做多、下穿下轨做空，回到中轨离场",
     "params": {"周期": 20, "倍数": 2, "方向": "双向"}},
    {"kind": "donchian", "name": "唐奇安通道突破（海龟）", "fn": t_donchian,
     "period": "day", "minBars": 80,
     "desc": "突破 N 周期新高做多、新低做空，反向 M 周期破位离场",
     "params": {"入场周期": 20, "出场周期": 10, "方向": "双向"}},
    {"kind": "rsi_reversal", "name": "RSI 超卖/超买反转", "fn": t_rsi_reversal,
     "period": "day", "minBars": 40,
     "desc": "RSI 从超卖区回升做多，从超买区回落做空",
     "params": {"周期": 14, "超卖": 30, "超买": 70, "方向": "双向"}},
    {"kind": "custom", "name": "自定义条件组合", "fn": t_custom,
     "period": "day", "minBars": 80,
     "desc": "左值 与 右值 按算子比较；值可写 MA(5)、MA(20)、BOLL(20,2)、CLOSE 或数字",
     "params": {"左值": "MA(5)", "算子": "上穿", "右值": "MA(20)", "方向": "双向"}},
]
TEMPLATE_BY_KIND = {t["kind"]: t for t in TEMPLATES}
DIRECTIONS = ["双向", "仅做多", "仅做空"]


def clamp_dir(targets, why, direction):
    """按方向偏好裁剪目标序列。"""
    if direction not in ("仅做多", "仅做空"):
        return targets, why
    out, ow = [], []
    cur = 0
    for i, t in enumerate(targets):
        if t is None:
            out.append(None)
            ow.append(None)
            continue
        v = max(t, 0) if direction == "仅做多" else min(t, 0)
        if v != cur:
            cur = v
            ow.append(why[i] or ("目标仓位变为 %d" % v))
        else:
            ow.append(None)
        out.append(v)
    return out, ow


def to_actions(bars, targets, why):
    """目标序列 -> 开平动作序列。"""
    acts, prev = [], 0
    for i, t in enumerate(targets):
        if t is None or t == prev:
            continue
        r = why[i] or ""
        if prev == 1 and t == 0:
            acts.append({"i": i, "d": bars[i]["d"], "side": "多", "action": "平",
                         "price": bars[i]["c"], "reason": r})
        elif prev == 1 and t == -1:
            acts.append({"i": i, "d": bars[i]["d"], "side": "多", "action": "平",
                         "price": bars[i]["c"], "reason": r + "（反手）"})
            acts.append({"i": i, "d": bars[i]["d"], "side": "空", "action": "开",
                         "price": bars[i]["c"], "reason": r})
        elif prev == -1 and t == 0:
            acts.append({"i": i, "d": bars[i]["d"], "side": "空", "action": "平",
                         "price": bars[i]["c"], "reason": r})
        elif prev == -1 and t == 1:
            acts.append({"i": i, "d": bars[i]["d"], "side": "空", "action": "平",
                         "price": bars[i]["c"], "reason": r + "（反手）"})
            acts.append({"i": i, "d": bars[i]["d"], "side": "多", "action": "开",
                         "price": bars[i]["c"], "reason": r})
        elif prev == 0 and t == 1:
            acts.append({"i": i, "d": bars[i]["d"], "side": "多", "action": "开",
                         "price": bars[i]["c"], "reason": r})
        elif prev == 0 and t == -1:
            acts.append({"i": i, "d": bars[i]["d"], "side": "空", "action": "开",
                         "price": bars[i]["c"], "reason": r})
        prev = t
    return acts


def run_strategy(bars, kind, params):
    """返回 (targets, why, actions)；未知策略抛 ValueError。"""
    tpl = TEMPLATE_BY_KIND.get(kind)
    if not tpl:
        raise ValueError("未知策略类型：%s" % kind)
    p = dict(tpl["params"])
    p.update(params or {})
    targets, why = tpl["fn"](bars, p)
    targets, why = clamp_dir(targets, why, p.get("方向", "双向"))
    return targets, why, to_actions(bars, targets, why)


def latest_signal(bars, kind, params):
    """最新一根上的信号（用于自动交易 / 选股）。"""
    _t, _w, acts = run_strategy(bars, kind, params)
    if not acts:
        return None
    last = acts[-1]
    last = dict(last)
    last["barsAgo"] = len(bars) - 1 - last["i"]
    return last


# ----------------------------------------------------------------------
# 策略引擎
# ----------------------------------------------------------------------
class StrategyEngine(object):
    def __init__(self, params, kline=None):
        self.lock = threading.RLock()
        self.params = params                 # 品种参数表对象（需有 .variety 字典）
        self.kline = kline or K.KLINE
        self.items = []
        self.runtime = {}                    # sid -> {"lastKey":..., "log":[...]}
        self.scan_cache = {}                 # (sid, 池签名) -> (ts, result)
        try:
            os.makedirs(STATE_DIR, exist_ok=True)
        except OSError:
            pass
        self.load()

    # ---------- 持久化 ----------
    def load(self):
        data = None
        try:
            with open(STRAT_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = None
        if isinstance(data, dict):
            self.items = data.get("策略") or []
            self.runtime = data.get("运行") or {}
        if not self.items:
            self.items = [self._seed()]
        # 补齐字段
        for it in self.items:
            it.setdefault("id", "st_" + uuid.uuid4().hex[:8])
            it.setdefault("enabled", False)
            it.setdefault("dryRun", True)
            it.setdefault("lots", 1)
            it.setdefault("period", TEMPLATE_BY_KIND.get(it.get("kind"), {}).get("period", "day"))
            it.setdefault("scope", {"mode": "all", "value": []})
            it.setdefault("notice", "")
            it.setdefault("createdAt", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            it["mm"] = MM.clean_for_save(it.get("mm"))
        self.save()

    def save(self):
        with self.lock:
            snap = {"策略": self.items, "运行": self.runtime,
                    "更新时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
        try:
            tmp = STRAT_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(snap, f, ensure_ascii=False, indent=1)
            os.replace(tmp, STRAT_FILE)
        except OSError:
            pass

    @staticmethod
    def _seed():
        """内置示例：穿越 5 日日 K 均线 + 稳健的资金管理方案。"""
        return {
            "id": "st_ma5demo",
            "name": "穿越5日日K均线",
            "kind": "ma_cross",
            "period": "day",
            "params": {"周期": 5, "方向": "双向"},
            "scope": {"mode": "all", "value": []},
            "lots": 1,
            "mm": MM.clean_for_save(MM.PRESETS[0]["mm"]),
            "stopLossPct": 0,
            "takeProfitPct": 0,
            "enabled": False,
            "dryRun": True,
            "notice": "内置示例：收盘价上穿 MA5 开多、下穿 MA5 开空；资金管理用「稳健趋势」预设（单笔风险 1%）。",
            "createdAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    # ---------- 模板 ----------
    @staticmethod
    def templates():
        return [{"kind": t["kind"], "name": t["name"], "desc": t["desc"],
                 "period": t["period"], "minBars": t["minBars"],
                 "star": t.get("star", False), "params": t["params"]}
                for t in TEMPLATES]

    # ---------- 增删改查 ----------
    def list(self):
        with self.lock:
            out = []
            for it in self.items:
                d = dict(it)
                tpl = TEMPLATE_BY_KIND.get(it.get("kind"))
                d["模板名"] = tpl["name"] if tpl else it.get("kind")
                d.update(self._stat(it))
                out.append(d)
            return out

    def get(self, sid):
        with self.lock:
            for it in self.items:
                if it.get("id") == sid:
                    return it
        return None

    def _stat(self, it):
        rt = self.runtime.get(it["id"]) or {}
        return {"最后信号": rt.get("lastSignalText", ""),
                "最后检查": rt.get("lastCheck", ""),
                "运行日志": (rt.get("log") or [])[-30:],
                "自动下单数": rt.get("orders", 0),
                "当前手数": rt.get("lastLots", 0),
                "资金管理": MM.describe(it.get("mm"))}

    def save_one(self, obj):
        """新增或更新。返回落库后的策略 dict。"""
        with self.lock:
            sid = (obj.get("id") or "").strip()
            tpl = TEMPLATE_BY_KIND.get(obj.get("kind"))
            if not tpl:
                raise ValueError("未知策略类型：%s" % obj.get("kind"))
            params = dict(tpl["params"])
            for k, v in (obj.get("params") or {}).items():
                params[k] = v
            if params.get("方向") not in DIRECTIONS:
                params["方向"] = "双向"
            scope = obj.get("scope") or {"mode": "all", "value": []}
            if scope.get("mode") not in ("all", "sector", "codes"):
                scope = {"mode": "all", "value": []}
            data = {
                "id": sid or ("st_" + uuid.uuid4().hex[:8]),
                "name": (obj.get("name") or tpl["name"]).strip()[:40],
                "kind": obj["kind"],
                "period": obj.get("period") or tpl["period"],
                "params": params,
                "scope": scope,
                "lots": max(1, _iarg(obj, "lots", 1)),
                "mm": MM.clean_for_save(obj.get("mm") if obj.get("mm") is not None
                                        else (self.get(sid) or {}).get("mm")),
                "stopLossPct": max(0.0, _farg(obj, "stopLossPct", 0)),
                "takeProfitPct": max(0.0, _farg(obj, "takeProfitPct", 0)),
                "enabled": bool(obj.get("enabled")),
                "dryRun": bool(obj.get("dryRun", True)),
                "notice": (obj.get("notice") or "").strip()[:200],
                "createdAt": obj.get("createdAt") or
                             datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            # 固定手数模式时，把「每次手数」同步进资金管理，避免两处口径打架
            if data["mm"].get("手数模式") in ("固定手数", "fixed") and obj.get("lots"):
                data["mm"] = MM.clean_for_save(
                    dict(data["mm"], 固定手数=max(1, _iarg(obj, "lots", 1))))
            if data["period"] not in K.PERIODS:
                data["period"] = tpl["period"]
            for idx, it in enumerate(self.items):
                if it.get("id") == data["id"]:
                    self.items[idx] = data
                    break
            else:
                self.items.append(data)
        self.save()
        return data

    def delete(self, sid):
        with self.lock:
            n = len(self.items)
            self.items = [x for x in self.items if x.get("id") != sid]
            self.runtime.pop(sid, None)
            hit = len(self.items) != n
        self.save()
        return hit

    def toggle(self, sid, enabled=None, dry=None):
        st = self.get(sid)
        if not st:
            return None
        if enabled is not None:
            st["enabled"] = bool(enabled)
        if dry is not None:
            st["dryRun"] = bool(dry)
        self.save()
        return st

    # ---------- 品种池 ----------
    def pool(self, scope):
        """返回 [(code, variety, 名称, 板块)]，code 为「主力连续」代码（回测/选股用）。"""
        scope = scope or {"mode": "all", "value": []}
        mode, val = scope.get("mode"), scope.get("value") or []
        out, seen = [], set()
        varieties = sorted((self.params.variety or {}).keys())
        for v in varieties:
            info = self.params.variety.get(v) or {}
            sec = K_SECTOR.get(v, "其他")
            if mode == "sector" and val and sec not in val:
                continue
            if mode == "codes":
                ups = [str(x).upper().strip() for x in val]
                if v in ups:
                    code = v + "0"
                else:
                    # 兼容 "RB0"（主力连续）与 "RB2610"（具体合约）两种写法
                    match = [u for u in ups if re.fullmatch(
                        re.escape(v) + r"(?:0|\d{3,4})", u)]
                    if not match:
                        continue
                    code = match[0]
            else:
                code = v + "0"
            if code in seen:
                continue
            seen.add(code)
            out.append((code, v, info.get("名称") or v, sec))
        return out

    # ---------- 信号 / 选股 ----------
    def signals_for(self, st, code, variety=None, period=None, limit=0,
                    with_bars=False):
        per = period or st.get("period") or "day"
        variety = variety or (re.match(r"^[A-Za-z]+", code or "") or [""])[0]
        d = self.kline.get(code, per, limit=limit, variety=variety)
        _t, _w, acts = run_strategy(d["bars"], st["kind"], st.get("params"))
        if with_bars:
            return d, acts
        return acts

    def scan(self, sid, override=None, lookback=3, max_pool=120,
             use_cache=True):
        """策略选股：在品种池上扫描，返回最新有效信号。"""
        st = self.get(sid)
        if not st:
            return {"ok": False, "msg": "策略不存在"}
        ov = override or {}
        scope = ov.get("scope") or st.get("scope")
        period = ov.get("period") or st.get("period")
        lookback = max(0, _iarg(ov, "lookback", lookback))
        pk = "%s|%s|%s" % (sid, period, json.dumps(scope, ensure_ascii=False,
                                                  sort_keys=True))
        now = time.time()
        if use_cache:
            with self.lock:
                hit = self.scan_cache.get(pk)
            if hit and now - hit[0] < 120:
                return hit[1]

        pool = self.pool(scope)[:max_pool]
        rows, errors = [], []
        lock = threading.Lock()

        def work(item):
            code, var, name, sec = item
            try:
                d = self.kline.get(code, period, variety=var)
            except Exception as e:                            # noqa: BLE001
                with lock:
                    errors.append("%s: %s" % (code, e))
                return
            bars = d["bars"]
            tpl = TEMPLATE_BY_KIND.get(st["kind"])
            if not bars or (tpl and len(bars) < tpl["minBars"]):
                return
            try:
                _t, _w, acts = run_strategy(bars, st["kind"], st.get("params"))
            except Exception as e:                            # noqa: BLE001
                with lock:
                    errors.append("%s: %s" % (code, e))
                return
            if not acts:
                return
            last = acts[-1]
            ago = len(bars) - 1 - last["i"]
            if ago > lookback:
                return
            tail = bars[-1]
            prev = bars[-2] if len(bars) > 1 else tail
            zdf = None
            if prev and prev.get("c"):
                zdf = round((tail["c"] - prev["c"]) / prev["c"] * 100, 2)
            row = {"code": code, "variety": var, "name": name, "sector": sec,
                   "date": last["d"], "side": last["side"], "action": last["action"],
                   "price": last["price"], "reason": last["reason"],
                   "barsAgo": ago, "last": tail["c"], "zdf": zdf,
                   "symbol": d.get("symbol"), "fallback": d.get("fallback", False)}
            with lock:
                rows.append(row)

        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=6) as ex:
            list(ex.map(work, pool))

        rows.sort(key=lambda r: (r["barsAgo"], -(r["zdf"] or 0)))
        res = {"ok": True, "strategy": {"id": st["id"], "name": st["name"],
                                        "kind": st["kind"], "period": period},
               "poolSize": len(pool), "hitCount": len(rows), "rows": rows,
               "lookback": lookback, "errors": errors[:8],
               "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
        with self.lock:
            self.scan_cache[pk] = (now, res)
        return res

    # ---------- 品种规格（资金管理算手数用） ----------
    def spec(self, variety):
        v = (self.params.variety or {}).get(variety) or {}
        fee = v.get("手续费") or {"模式": "按成交额", "开仓": 0.0001,
                                  "平仓": 0.0001, "平今": 0.0001}
        return {"合约乘数": float(v.get("合约乘数") or
                                  getattr(self.params, "default_mult", 10)),
                "最小变动价位": float(v.get("最小变动价位") or
                                      getattr(self.params, "default_tick", 1)),
                "保证金率": float(v.get("保证金率") or
                                  getattr(self.params, "default_rate", 0.1)),
                "手续费": dict(fee),
                "名称": v.get("名称") or variety}

    # ---------- 自动交易巡检 ----------
    def tick(self, order_fn=None, pos_fn=None, quote_fn=None, state_fn=None):
        """由服务端后台线程调用。返回本次产生的动作数。

        state_fn 提供账户快照（equity/cash/marginUsed/consecLoss/… 与 settings），
        资金管理模块据此算出手数；不提供时退回策略里的固定手数。
        """
        acct = {}
        if state_fn:
            try:
                acct = state_fn() or {}
            except Exception as e:                            # noqa: BLE001
                acct = {"_err": str(e)}
        fired = 0
        for st in self.list():
            if not st.get("enabled"):
                continue
            sid = st["id"]
            rt = self.runtime.setdefault(sid, {"log": [], "orders": 0})
            pool = self.pool(st.get("scope"))
            # 自动交易只盯「已在池内且被用户显式指定」的标的，避免误单；
            # 若为 all 模式，则限制前 8 个优先品种。
            if (st.get("scope") or {}).get("mode") == "all":
                pool = pool[:8]
            else:
                pool = pool[:20]
            mmc = MM.norm(st.get("mm"))
            msgs = []
            if acct.get("_err"):
                msgs.append("账户状态读取失败：%s" % acct["_err"])
            for code, var, name, _sec in pool:
                try:
                    d = self.kline.get(code, st.get("period") or "day",
                                       variety=var)
                except Exception as e:                        # noqa: BLE001
                    msgs.append("%s 行情失败 %s" % (code, e))
                    continue
                bars = d["bars"]
                if len(bars) < 30:
                    continue
                try:
                    _t, _w, acts = run_strategy(bars, st["kind"], st.get("params"))
                except Exception as e:                        # noqa: BLE001
                    msgs.append("%s 计算失败 %s" % (code, e))
                    continue
                if not acts:
                    continue
                last = acts[-1]
                key = "%s|%s|%s" % (code, last["d"], last["action"] + last["side"])
                if rt.get("lastKey_" + code) == key:
                    continue
                rt["lastKey_" + code] = key
                rt["lastSignalText"] = "%s %s %s @ %s（%s）" % (
                    code, last["action"], last["side"], last["price"], last["d"])

                # ---- 资金管理：算出本次下单手数 ----
                lots = max(1, int(st.get("lots") or 1))
                extra = ""
                side_dir = 1 if last["side"] == "多" else -1
                if last["action"] == "平":
                    held = 0
                    for p in (acct.get("positions") or []):
                        if p.get("code") == code and \
                                p.get("dir") == ("long" if side_dir > 0 else "short"):
                            held = max(held, int(p.get("lots") or 0))
                    lots = held or lots
                elif acct and mmc["启用"]:
                    try:
                        mgr = MM.MoneyManager(mmc, bars, self.spec(var),
                                              acct.get("settings") or {})
                        pl = mgr.plan(len(bars) - 1, last["price"], side_dir, acct)
                        if pl["ok"]:
                            lots = pl["lots"]
                            extra = "｜" + "；".join(pl["notes"])
                        else:
                            txt = "[%s] %s %s%s → 资金管理拒单：%s" % (
                                st["name"], code, last["action"], last["side"],
                                pl["reject"] or "风控不允许")
                            rt.setdefault("log", []).append(
                                {"ts": datetime.now().strftime("%H:%M:%S"),
                                 "text": txt})
                            fired += 1
                            continue
                    except Exception as e:                    # noqa: BLE001
                        msgs.append("%s 资金管理算手数失败 %s" % (code, e))
                rt["lastLots"] = lots

                text = "[%s] %s %s%s %d 手 @ %s ← %s%s" % (
                    st["name"], code, last["action"], last["side"], lots,
                    last["price"], last["reason"], extra)
                if st.get("dryRun", True) or not order_fn:
                    rt.setdefault("log", []).append(
                        {"ts": datetime.now().strftime("%H:%M:%S"),
                         "text": text + "（仅记录，未下单）"})
                    fired += 1
                else:
                    try:
                        req = {"code": code, "direction": last["side"],
                               "offset": "开" if last["action"] == "开" else "平",
                               "priceType": "市价", "lots": lots,
                               "closeMode": "自动",
                               "note": "策略:%s" % st["name"]}
                        ok, msg, _o = order_fn(req)
                        rt["orders"] = rt.get("orders", 0) + (1 if ok else 0)
                        rt.setdefault("log", []).append(
                            {"ts": datetime.now().strftime("%H:%M:%S"),
                             "text": text + ("　→ 下单成功" if ok else "　→ 下单失败：%s" % msg)})
                        fired += 1
                    except Exception as e:                    # noqa: BLE001
                        rt.setdefault("log", []).append(
                            {"ts": datetime.now().strftime("%H:%M:%S"),
                             "text": text + "　→ 下单异常 %s" % e})
            rt["log"] = (rt.get("log") or [])[-60:]
            rt["lastCheck"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            for m in msgs[:4]:
                rt.setdefault("log", []).append(
                    {"ts": datetime.now().strftime("%H:%M:%S"), "text": m})
        if fired:
            self.save()
        return fired


# 品种 -> 板块（与主服务保持一致，避免循环 import）
K_SECTOR = {
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
