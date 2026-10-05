# -*- coding: utf-8 -*-
"""
资金管理 · 仓位管理（纯标准库）

策略只回答「做多还是做空」，这个模块回答**「做多少手、错了在哪退出、什么时候不许做」**。
回测与实盘策略交易共用同一套算法（与「目标仓位序列」同思路，避免两处漂移）。

包含四层：

  ① 手数模式（仓位规模）
     固定手数 / 保证金比例 / **风险固定** / 波动率目标 / 分数凯利
  ② 止损方式
     ATR / 百分比 / 固定跳数
  ③ 风险闸门（什么时候不许开新仓）
     最大回撤熔断 / 连亏减仓 / 连续亏损停手 / 日内亏损上限 /
     保证金占用上限 / 单笔手数上限 / 可用资金不足
  ④ 持仓管理
     金字塔加仓 / 移动止损（浮盈达标后止损上移至保本并跟随）

统一入口 MoneyManager.plan()，返回手数与首仓止损价；被拒单时给出可读原因。
"""
import math

from 行情K线 import ATR


# ----------------------------------------------------------------------
# 默认配置
# ----------------------------------------------------------------------
DEFAULTS = {
    "启用": True,
    "手数模式": "风险固定",
    "固定手数": 1,
    "资金比例%": 30.0,          # 按保证金占用占权益比例开仓
    "单笔风险%": 1.0,           # 风险固定 / 凯利 的风险预算
    "目标波动%": 2.0,           # 波动率目标：每根 K 线权益波动目标
    "凯利分数": 0.5,            # 分数凯利（1=全凯利，0.5=半凯利）
    "凯利下限%": 5.0,           # 凯利算出的比例下限
    "凯利上限%": 30.0,          # 凯利算出的比例上限
    "凯利最少样本": 10,         # 不足则退回风险固定
    "止损方式": "ATR",
    "ATR周期": 14,
    "ATR倍数": 2.0,
    "止损%": 2.0,
    "止损跳数": 20,
    "单笔最大手数": 20,
    "最小手数": 1,
    "保证金占用上限%": 50.0,     # 全部持仓保证金 / 权益 上限
    "最大回撤熔断%": 20.0,       # 权益自峰值回撤超过则停止开新仓（0=关闭）
    "熔断后清仓": True,
    "连亏减仓笔数": 3,           # 连亏达到 N 笔后手数打折（0=关闭）
    "连亏折扣": 0.5,
    "连亏停手笔数": 0,           # 连亏达到 N 笔后停止开仓（0=关闭）
    "日内亏损上限%": 0.0,        # 当日权益跌幅超过则当日不再开仓（0=关闭）
    "加仓模式": "不加仓",        # 不加仓 | 金字塔加仓
    "最大加仓次数": 2,
    "加仓触发%": 1.0,            # 浮盈达到该比例（每加一次门槛上移）
    "加仓手数比例": 0.5,         # 第 k 次加仓手数 = 首仓 × 比例^k
    "最大总手数": 0,             # 加仓后总手数上限（0=不限）
    "移动止损": "关闭",          # 关闭 | 保本 | 跟随ATR
    "保本触发%": 1.5,            # 浮盈达到该比例后止损移至保本
    "跟随ATR倍数": 2.0,          # 跟随ATR时的回撤距离倍数
}

LOT_MODES = [
    ("fixed", "固定手数", "每次固定 N 手，简单可控，适合小资金或验证逻辑"),
    ("ratio", "保证金比例", "按「保证金占权益 X%」倒算手数，资金越多手数越多"),
    ("risk", "风险固定", "先定单笔最大亏损（权益的 X%），再按止损距离倒算手数 —— 最科学，推荐"),
    ("atr", "波动率目标", "让持仓每根 K 线的波动约等于权益的 X%，波动大自动减仓、波动小自动加仓"),
    ("kelly", "分数凯利", "用历史胜率与盈亏比算最优下注比例，再乘「凯利分数」保守化"),
]

STOP_MODES = [
    ("atr", "ATR（波动止损）", "止损距离 = ATR(N) × 倍数，随市场波动自适应"),
    ("pct", "百分比", "止损距离 = 开仓价 × X%，固定比例"),
    ("ticks", "固定跳数", "止损距离 = N 跳 × 最小变动价位，最机械但最可预期"),
    ("none", "不设止损", "只靠信号反手退出，不加止损（此时「风险固定」会自动退回固定手数）"),
]

TRAIL_MODES = [
    ("off", "关闭", "只用首仓止损"),
    ("be", "保本", "浮盈达到触发比例后，把止损上移到开仓价，锁定不亏"),
    ("atr", "跟随ATR", "浮盈达到触发比例后，止损跟随最高价回撤 ATR×倍数"),
]

ADD_MODES = [
    ("off", "不加仓", "一次性建仓，不追加"),
    ("pyramid", "金字塔加仓", "浮盈达标后追加，且每追加一次手数递减（海龟式）"),
]

# 科学预设：四套典型风格
PRESETS = [
    {
        "key": "steady",
        "name": "稳健趋势（推荐）",
        "desc": "单笔风险 1%、ATR(14)×2 止损、保证金占用 ≤30%、回撤 20% 熔断、连亏 3 笔手数减半、浮盈后保本。适合日线级别的趋势跟踪。",
        "mm": {"手数模式": "风险固定", "单笔风险%": 1.0, "止损方式": "ATR",
               "ATR周期": 14, "ATR倍数": 2.0, "单笔最大手数": 10,
               "保证金占用上限%": 30.0, "最大回撤熔断%": 20.0, "熔断后清仓": True,
               "连亏减仓笔数": 3, "连亏折扣": 0.5, "连亏停手笔数": 0,
               "日内亏损上限%": 0.0, "加仓模式": "不加仓",
               "移动止损": "保本", "保本触发%": 2.0},
        "params": {"周期": 20},
    },
    {
        "key": "turtle",
        "name": "海龟式趋势（加仓）",
        "desc": "唐奇安突破 + 单笔风险 1.5% + ATR 止损 + 金字塔加仓 2 次 + 跟随 ATR 移动止损 + 回撤 25% 熔断。经典的「亏小赚大」结构。",
        "mm": {"手数模式": "风险固定", "单笔风险%": 1.5, "止损方式": "ATR",
               "ATR周期": 20, "ATR倍数": 2.0, "单笔最大手数": 12,
               "保证金占用上限%": 40.0, "最大回撤熔断%": 25.0, "熔断后清仓": True,
               "连亏减仓笔数": 3, "连亏折扣": 0.5,
               "加仓模式": "金字塔加仓", "最大加仓次数": 2, "加仓触发%": 1.5,
               "加仓手数比例": 0.5, "最大总手数": 0,
               "移动止损": "跟随ATR", "保本触发%": 1.5, "跟随ATR倍数": 2.0},
        "params": {"入场周期": 20, "出场周期": 10},
    },
    {
        "key": "conservative",
        "name": "保守低频（低回撤）",
        "desc": "单笔风险 0.5%、百分比止损 2%、保证金占用 ≤20%、回撤 15% 熔断、日内亏损 3% 停手、不加仓。追求平滑的资金曲线。",
        "mm": {"手数模式": "风险固定", "单笔风险%": 0.5, "止损方式": "pct",
               "止损%": 2.0, "单笔最大手数": 5,
               "保证金占用上限%": 20.0, "最大回撤熔断%": 15.0, "熔断后清仓": True,
               "连亏减仓笔数": 2, "连亏折扣": 0.5, "连亏停手笔数": 5,
               "日内亏损上限%": 3.0, "加仓模式": "不加仓",
               "移动止损": "保本", "保本触发%": 1.0},
        "params": {"快周期": 20, "慢周期": 60},
    },
    {
        "key": "balanced",
        "name": "波动率均衡（自适应）",
        "desc": "让持仓波动恒定在权益的 2%，波动大自动减仓。保证金占用 ≤40%、回撤 18% 熔断、连亏 3 笔减半。适合多品种组合。",
        "mm": {"手数模式": "atr", "目标波动%": 2.0, "止损方式": "ATR",
               "ATR周期": 14, "ATR倍数": 2.5, "单笔最大手数": 15,
               "保证金占用上限%": 40.0, "最大回撤熔断%": 18.0, "熔断后清仓": False,
               "连亏减仓笔数": 3, "连亏折扣": 0.5,
               "加仓模式": "不加仓", "移动止损": "保本", "保本触发%": 1.5},
        "params": {"周期": 10},
    },
]

# 参数分组（前端按此渲染）
FIELD_GROUPS = [
    {"title": "① 手数模式（做多少手）", "fields": [
        {"k": "手数模式", "type": "select", "label": "手数模式",
         "options": [m[1] for m in LOT_MODES], "keys": [m[0] for m in LOT_MODES],
         "hint": "改这里等于决定整套仓位逻辑，其余参数按模式生效"},
        {"k": "固定手数", "type": "num", "label": "固定手数（手）", "step": 1},
        {"k": "资金比例%", "type": "num", "label": "保证金占权益比例（%）"},
        {"k": "单笔风险%", "type": "num", "label": "单笔风险预算（%权益）"},
        {"k": "目标波动%", "type": "num", "label": "目标波动（%权益/根）"},
        {"k": "凯利分数", "type": "num", "label": "凯利分数（0.5=半凯利）"},
        {"k": "凯利上限%", "type": "num", "label": "凯利仓位上限（%）"},
        {"k": "单笔最大手数", "type": "num", "label": "单笔最大手数", "step": 1},
        {"k": "最小手数", "type": "num", "label": "最小手数", "step": 1},
    ]},
    {"title": "② 止损方式（错了在哪退出）", "fields": [
        {"k": "止损方式", "type": "select", "label": "止损方式",
         "options": [m[1] for m in STOP_MODES], "keys": [m[0] for m in STOP_MODES]},
        {"k": "ATR周期", "type": "num", "label": "ATR 周期", "step": 1},
        {"k": "ATR倍数", "type": "num", "label": "ATR 倍数"},
        {"k": "止损%", "type": "num", "label": "止损百分比（%）"},
        {"k": "止损跳数", "type": "num", "label": "止损跳数", "step": 1},
    ]},
    {"title": "③ 风险闸门（什么时候不许做）", "fields": [
        {"k": "保证金占用上限%", "type": "num", "label": "保证金占用上限（%权益）"},
        {"k": "最大回撤熔断%", "type": "num", "label": "最大回撤熔断（%，0=关）",
         "hint": "权益自峰值回撤超过该值即停止开新仓"},
        {"k": "熔断后清仓", "type": "check", "label": "熔断后强制清仓"},
        {"k": "连亏减仓笔数", "type": "num", "label": "连亏几笔后减仓（0=关）", "step": 1},
        {"k": "连亏折扣", "type": "num", "label": "减仓折扣（0.5=手数减半）"},
        {"k": "连亏停手笔数", "type": "num", "label": "连亏几笔后停手（0=关）", "step": 1},
        {"k": "日内亏损上限%", "type": "num", "label": "日内亏损上限（%，0=关）"},
    ]},
    {"title": "④ 持仓管理（加仓与移动止损）", "fields": [
        {"k": "加仓模式", "type": "select", "label": "加仓模式",
         "options": [m[1] for m in ADD_MODES], "keys": [m[0] for m in ADD_MODES]},
        {"k": "最大加仓次数", "type": "num", "label": "最大加仓次数", "step": 1},
        {"k": "加仓触发%", "type": "num", "label": "加仓触发浮盈（%）"},
        {"k": "加仓手数比例", "type": "num", "label": "加仓手数比例（0.5=每次减半）"},
        {"k": "最大总手数", "type": "num", "label": "加仓后总手数上限（0=不限）", "step": 1},
        {"k": "移动止损", "type": "select", "label": "移动止损",
         "options": [m[1] for m in TRAIL_MODES], "keys": [m[0] for m in TRAIL_MODES]},
        {"k": "保本触发%", "type": "num", "label": "移动止损触发浮盈（%）"},
        {"k": "跟随ATR倍数", "type": "num", "label": "跟随 ATR 倍数"},
    ]},
]


# ----------------------------------------------------------------------
# 清洗
# ----------------------------------------------------------------------
def _f(v, d):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return float(d)
    if x != x or x in (float("inf"), float("-inf")):
        return float(d)
    return x


def _i(v, d):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return int(d)


def norm(mm):
    """合并默认值并做类型/枚举清洗。"""
    m = dict(DEFAULTS)
    for k, v in (mm or {}).items():
        if k in DEFAULTS:
            m[k] = v
    m["启用"] = bool(m["启用"])
    for k in ("固定手数", "ATR周期", "止损跳数", "单笔最大手数", "最小手数",
              "连亏减仓笔数", "连亏停手笔数", "最大加仓次数", "最大总手数",
              "凯利最少样本"):
        m[k] = max(0, _i(m[k], DEFAULTS[k]))
    for k in ("资金比例%", "单笔风险%", "目标波动%", "凯利分数", "凯利下限%",
              "凯利上限%", "ATR倍数", "止损%", "保证金占用上限%",
              "最大回撤熔断%", "连亏折扣", "日内亏损上限%", "加仓触发%",
              "加仓手数比例", "保本触发%", "跟随ATR倍数"):
        m[k] = _f(m[k], DEFAULTS[k])
    m["固定手数"] = max(1, m["固定手数"])
    m["最小手数"] = max(1, m["最小手数"])
    m["单笔最大手数"] = max(m["最小手数"], m["单笔最大手数"])
    m["最大回撤熔断%"] = max(0.0, m["最大回撤熔断%"])
    m["保证金占用上限%"] = min(100.0, max(1.0, m["保证金占用上限%"]))
    m["连亏折扣"] = min(1.0, max(0.05, m["连亏折扣"]))
    m["加仓手数比例"] = min(1.0, max(0.05, m["加仓手数比例"]))
    m["凯利分数"] = min(1.0, max(0.05, m["凯利分数"]))

    # 枚举：外部一律按中文标签保存（与策略其它参数风格一致），内部映射回 key。
    # 入参允许写 key（fixed/risk/atr…）或中文标签，两种都认。
    m["_lot"], m["手数模式"] = _enum(m["手数模式"], LOT_MODES, "risk")
    m["_stop"], m["止损方式"] = _enum(m["止损方式"], STOP_MODES, "atr")
    m["_trail"], m["移动止损"] = _enum(m["移动止损"], TRAIL_MODES, "off")
    m["_add"], m["加仓模式"] = _enum(m["加仓模式"], ADD_MODES, "off")
    return m


def _pos_px(pos):
    """持仓成本价：回测/复盘用 avg，外部调用可能用 price，两种都认。"""
    v = pos.get("avg")
    if v is None:
        v = pos.get("price")
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _enum(v, modes, dflt):
    """返回 (key, label)。v 可以是 key、label 或任意别名。"""
    key2lab = dict((k, lab) for k, lab, _ in modes)
    lab2key = dict((lab, k) for k, lab, _ in modes)
    s = str(v if v is not None else "").strip()
    if s in key2lab:                       # 传入的是 key
        k = s
    elif s in lab2key:                     # 传入的是中文标签
        k = lab2key[s]
    elif s.lower() in key2lab:
        k = s.lower()
    else:
        k = dflt
    if k not in key2lab:
        k = dflt
    return k, key2lab[k]


def clean_for_save(mm):
    """去掉内部 _ 前缀键，便于持久化。"""
    return dict((k, v) for k, v in norm(mm).items() if not k.startswith("_"))


def preset(key):
    for p in PRESETS:
        if p["key"] == key:
            return p
    return None


def describe(mm):
    """一句话概括当前资金管理方案（用于 UI 与日志）。"""
    m = norm(mm)
    lot = {"fixed": "固定 %d 手" % m["固定手数"],
           "ratio": "保证金占权益 %.1f%%" % m["资金比例%"],
           "risk": "单笔风险 %.2f%%" % m["单笔风险%"],
           "atr": "波动率目标 %.2f%%" % m["目标波动%"],
           "kelly": "分数凯利 ×%.2f（上限 %.0f%%）" % (m["凯利分数"], m["凯利上限%"])}[m["_lot"]]
    stop = {"atr": "ATR(%d)×%.1f 止损" % (m["ATR周期"], m["ATR倍数"]),
            "pct": "%.1f%% 百分比止损" % m["止损%"],
            "ticks": "%d 跳止损" % m["止损跳数"],
            "none": "不设止损"}[m["_stop"]]
    parts = ["仓位：%s" % lot, "退出：%s" % stop,
             "仓位上限：保证金 ≤%.0f%%权益" % m["保证金占用上限%"]]
    if m["最大回撤熔断%"] > 0:
        parts.append("熔断：回撤 %.0f%%" % m["最大回撤熔断%"])
    if m["连亏减仓笔数"] > 0:
        parts.append("连亏 %d 笔手数 ×%.2f" % (m["连亏减仓笔数"], m["连亏折扣"]))
    if m["连亏停手笔数"] > 0:
        parts.append("连亏 %d 笔停手" % m["连亏停手笔数"])
    if m["日内亏损上限%"] > 0:
        parts.append("日内亏损 %.1f%% 停手" % m["日内亏损上限%"])
    if m["_add"] == "pyramid":
        parts.append("金字塔加仓 %d 次" % m["最大加仓次数"])
    if m["_trail"] != "off":
        parts.append("移动止损：%s" % m["移动止损"])
    return "；".join(parts)


# ----------------------------------------------------------------------
# 单笔下单计划（MoneyManager）
# ----------------------------------------------------------------------
class MoneyManager(object):
    """一次回测/一个策略会话内复用，预计算 ATR 序列避免重复计算。"""

    def __init__(self, mm, bars=None, spec=None, settings=None):
        self.mm = norm(mm)
        self.spec = spec or {}
        self.settings = settings or {}
        self.mult = _f(self.spec.get("合约乘数"), 1) or 1
        self.tick = _f(self.spec.get("最小变动价位"), 1) or 1
        self.margin_rate = _f(self.spec.get("保证金率"), 0)
        self.margin_k = _f(self.settings.get("保证金倍数", 1), 1) or 1
        self.atr = None
        if bars and (self.mm["_stop"] == "atr" or self.mm["_lot"] == "atr"
                     or self.mm["_trail"] == "atr"):
            try:
                self.atr = ATR(bars, self.mm["ATR周期"])
            except Exception:                                  # noqa: BLE001
                self.atr = None

    # ---------- 基础量 ----------
    def margin1(self, price):
        return round(price * self.mult * self.margin_rate * self.margin_k, 2)

    def atr_at(self, i):
        if not self.atr or i < 0 or i >= len(self.atr):
            return None
        v = self.atr[i]
        return None if v is None else float(v)

    def stop_dist(self, i, price):
        """返回 (距离, 说明)"""
        m = self.mm
        if m["_stop"] == "none":
            return 0.0, "不设止损"
        if m["_stop"] == "atr":
            a = self.atr_at(i)
            if a is None or a <= 0:
                return price * 0.02, "ATR 不可用，退化为 2%% 止损"
            return a * m["ATR倍数"], "ATR(%d)=%g × %.1f" % (m["ATR周期"], a, m["ATR倍数"])
        if m["_stop"] == "pct":
            return price * m["止损%"] / 100.0, "开仓价 × %.2f%%" % m["止损%"]
        return m["止损跳数"] * self.tick, "%d 跳 × %g" % (m["止损跳数"], self.tick)

    def stop_price(self, i, side, price):
        dist, txt = self.stop_dist(i, price)
        if self.mm["_stop"] == "none":
            return None, 0.0, txt
        if dist <= 0:
            dist = price * 0.02
            txt = "止损距离非法，退化为 2%"
        px = price - dist if side > 0 else price + dist
        return round(px, 6), dist, txt

    # ---------- 风险闸门 ----------
    def gate(self, state):
        """返回 (是否允许开新仓, 原因)。"""
        m = self.mm
        if not m["启用"]:
            return True, ""
        eq = _f(state.get("equity"), 0)
        peak = _f(state.get("peak"), eq)
        if m["最大回撤熔断%"] > 0 and peak > 0:
            dd = (peak - eq) / peak * 100.0
            if dd >= m["最大回撤熔断%"]:
                return False, "已触发最大回撤熔断（当前回撤 %.2f%% ≥ %.0f%%）" % (
                    dd, m["最大回撤熔断%"])
        if m["连亏停手笔数"] > 0 and _i(state.get("consecLoss"), 0) >= m["连亏停手笔数"]:
            return False, "连亏 %d 笔已停手" % state.get("consecLoss")
        if m["日内亏损上限%"] > 0 and state.get("dayEquity0"):
            e0 = _f(state.get("dayEquity0"), 0)
            if e0 > 0:
                d = (e0 - eq) / e0 * 100.0
                if d >= m["日内亏损上限%"]:
                    return False, "当日已亏 %.2f%%（上限 %.1f%%），今日不再开仓" % (
                        d, m["日内亏损上限%"])
        return True, ""

    # ---------- 手数 ----------
    def base_lots(self, i, price, state):
        """按手数模式算基础手数（未加闸门）。返回 (手数float, 说明)"""
        m = self.mm
        eq = max(0.0, _f(state.get("equity"), 0))
        unit = self.margin1(price)
        mode = m["_lot"]
        if mode == "fixed":
            return float(m["固定手数"]), "固定 %d 手" % m["固定手数"]
        if mode == "ratio":
            if unit <= 0:
                return float(m["固定手数"]), "保证金率缺失，用固定手数"
            return eq * m["资金比例%"] / 100.0 / unit, \
                "权益 %g × %.1f%% ÷ 单手保证金 %g" % (eq, m["资金比例%"], unit)
        if mode == "atr":
            a = self.atr_at(i)
            if a is None or a <= 0:
                return float(m["固定手数"]), "ATR 不可用，退化为固定手数"
            risk = eq * m["目标波动%"] / 100.0
            return risk / (a * self.mult), \
                "目标波动 %g ÷ (ATR %g × 乘数 %g)" % (risk, a, self.mult)
        if mode == "kelly":
            wins, total = _i(state.get("wins"), 0), _i(state.get("closed"), 0)
            gw, gl = _f(state.get("grossWin"), 0), _f(state.get("grossLoss"), 0)
            if total < m["凯利最少样本"] or wins <= 0 or gw <= 0 or gl <= 0:
                self.mm["_kellyFallback"] = True
                return self._risk_lots(i, price, state)
            w = wins / float(total)
            payoff = (gw / wins) / (gl / max(1, total - wins))   # 平均盈利/平均亏损
            if payoff <= 0:
                return float(m["固定手数"]), "盈亏比异常，用固定手数"
            f = w - (1 - w) / payoff
            f = min(m["凯利上限%"] / 100.0, max(m["凯利下限%"] / 100.0, f * m["凯利分数"]))
            if unit <= 0:
                return float(m["固定手数"]), "保证金率缺失，用固定手数"
            return eq * f / unit, \
                "胜率 %.1f%%、盈亏比 %.2f → 下注 %.1f%% 权益" % (w * 100, payoff, f * 100)
        return self._risk_lots(i, price, state)

    def _risk_lots(self, i, price, state):
        m = self.mm
        if m["_stop"] == "none":
            return float(m["固定手数"]), "不设止损无法按风险倒算手数，退回固定手数"
        eq = max(0.0, _f(state.get("equity"), 0))
        risk = eq * m["单笔风险%"] / 100.0
        _stop, dist, txt = self.stop_price(i, 1, price)
        per_lot = dist * self.mult
        if per_lot <= 0:
            return float(m["固定手数"]), "止损距离非法，用固定手数"
        return risk / per_lot, "风险预算 %g ÷ (止损 %g × 乘数 %g)" % (risk, dist, self.mult)

    def plan(self, i, price, side, state):
        """生成开仓计划。

        i     当前 K 线下标
        price 计划成交价
        side  +1 多 / -1 空
        state 见模块文档；至少需要 equity / peak / cash / marginUsed / consecLoss
        返回 {ok, lots, stop, stopDist, margin, risk, notes, reject}
        """
        m = self.mm
        out = {"ok": False, "lots": 0, "stop": None, "stopDist": 0.0,
               "margin": 0.0, "risk": 0.0, "notes": [], "reject": ""}
        if not m["启用"]:
            return out

        ok, why = self.gate(state)
        if not ok:
            out["reject"] = why
            return out

        lots_f, why_lot = self.base_lots(i, price, state)
        if m.get("_kellyFallback"):
            m.pop("_kellyFallback", None)
            why_lot += "（凯利样本不足 %d 笔，退回风险固定）" % m["凯利最少样本"]

        # 连亏减仓
        cl = _i(state.get("consecLoss"), 0)
        if m["连亏减仓笔数"] > 0 and cl >= m["连亏减仓笔数"]:
            lots_f *= m["连亏折扣"]
            why_lot += "；连亏 %d 笔 → 手数 ×%.2f" % (cl, m["连亏折扣"])
        # 回撤减仓（未到熔断线时按比例线性减仓）
        if m["最大回撤熔断%"] > 0:
            peak = _f(state.get("peak"), 0)
            eq = _f(state.get("equity"), 0)
            if peak > 0 and eq > 0:
                dd = (peak - eq) / peak * 100.0
                if 0 < dd < m["最大回撤熔断%"]:
                    k = 1.0 - dd / m["最大回撤熔断%"] * 0.5     # 最多减半
                    if k < 0.999:
                        lots_f *= k
                        why_lot += "；回撤 %.1f%% → 手数 ×%.2f" % (dd, k)

        lots = int(math.floor(max(0.0, lots_f) + 1e-9))
        lots = max(m["最小手数"], min(m["单笔最大手数"], lots))
        out["notes"].append(why_lot)
        out["stop"], out["stopDist"], stop_txt = self.stop_price(i, side, price)
        out["notes"].append(("止损位 %g（%s）" % (out["stop"], stop_txt))
                            if out["stop"] is not None else stop_txt)

        # 保证金占用上限
        eq = max(0.0, _f(state.get("equity"), 0))
        used = max(0.0, _f(state.get("marginUsed"), 0))
        unit = self.margin1(price)
        cap = eq * m["保证金占用上限%"] / 100.0
        if unit > 0 and eq > 0:
            room = cap - used
            if room <= 0:
                out["reject"] = "保证金占用已达上限 %.0f%%（已用 %g / 上限 %g）" % (
                    m["保证金占用上限%"], used, cap)
                return out
            allow = int(math.floor(room / unit + 1e-9))
            if allow < lots:
                lots = allow
                out["notes"].append("受保证金占用上限约束，手数降为 %d" % lots)
        if lots < m["最小手数"]:
            out["reject"] = "保证金占用上限下可开手数为 0（已用 %g / 上限 %g）" % (used, cap)
            return out
        if lots > m["单笔最大手数"]:
            lots = m["单笔最大手数"]

        # 可用资金（保证金 + 手续费近似）
        cost = self.margin1(price) * lots
        if cost > _f(state.get("cash"), 0):
            lots = int(math.floor(_f(state.get("cash"), 0) / unit)) if unit > 0 else 0
            if lots < m["最小手数"]:
                out["reject"] = "可用资金不足（需保证金约 %g，可用 %g）" % (
                    cost, _f(state.get("cash"), 0))
                return out
            out["notes"].append("受可用资金约束，手数降为 %d" % lots)

        out["ok"] = True
        out["lots"] = lots
        out["margin"] = self.margin1(price) * lots
        out["risk"] = round(out["stopDist"] * self.mult * lots, 2)
        return out

    # ---------- 加仓 ----------
    def add_lots(self, i, price, pos, state):
        """金字塔加仓判断。

        pos: {"dir","lots","price"(均价),"adds","first_lots"}
        返回 None（不加）或计划 dict（结构同 plan 的 lot/stop 部分）
        """
        m = self.mm
        if m["_add"] != "pyramid" or not m["启用"]:
            return None
        if pos["adds"] >= m["最大加仓次数"]:
            return None
        d = pos["dir"]
        cost = _pos_px(pos)
        if cost <= 0:
            return None
        gain = (price - cost) * d / cost * 100.0
        need = m["加仓触发%"] * (pos["adds"] + 1)
        if gain < need:
            return None
        base = pos["first_lots"] * (m["加仓手数比例"] ** (pos["adds"] + 1))
        lots = int(math.floor(base + 1e-9))
        if lots < 1:
            return None
        lots = min(lots, m["单笔最大手数"])
        if m["最大总手数"] > 0:
            lots = min(lots, max(0, m["最大总手数"] - pos["lots"]))
            if lots < 1:
                return None
        # 闸门与保证金上限
        ok, why = self.gate(state)
        if not ok:
            return None
        eq = max(0.0, _f(state.get("equity"), 0))
        used = max(0.0, _f(state.get("marginUsed"), 0))
        unit = self.margin1(price)
        cap = eq * m["保证金占用上限%"] / 100.0
        if unit > 0 and eq > 0:
            room = cap - used
            allow = int(math.floor(room / unit + 1e-9)) if room > 0 else 0
            lots = min(lots, allow)
        if lots < 1:
            return None
        return {"lots": lots,
                "note": "浮盈 %.2f%% ≥ %.2f%%，第 %d 次加仓 %d 手" % (
                    gain, need, pos["adds"] + 1, lots)}

    # ---------- 移动止损 ----------
    def trail_stop(self, pos, bar, i):
        """返回新的止损价（不移动则返回原值）。pos 需含 "stop" 与 "extreme"。"""
        m = self.mm
        if m["_trail"] == "off" or not m["启用"]:
            return pos.get("stop")
        c = bar.get("c")
        if c is None:
            return pos.get("stop")
        d = pos["dir"]
        cost = _pos_px(pos)
        if cost <= 0:
            return pos.get("stop")
        gain = (c - cost) * d / cost * 100.0
        if gain < m["保本触发%"]:
            return pos.get("stop")
        cur = pos.get("stop")
        if m["_trail"] == "be":
            be = cost
            if d > 0:
                return max(cur, be) if cur is not None else be
            return min(cur, be) if cur is not None else be
        # 跟随 ATR
        a = self.atr_at(i)
        if a is None or a <= 0:
            return cur
        dist = a * m["跟随ATR倍数"]
        if d > 0:
            cand = pos.get("extreme", c) - dist
            return max(cur, cand) if cur is not None else cand
        cand = pos.get("extreme", c) + dist
        return min(cur, cand) if cur is not None else cand
