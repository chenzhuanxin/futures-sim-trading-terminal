# -*- coding: utf-8 -*-
"""
行情 · 技术指标 · 策略 · 回测 · 复盘 自测

A 部分（无需服务）：指标数学正确性、策略方向过滤、回测合成序列手算比对
B 部分（需服务在 8908）：全部 HTTP 接口联调

用法：
    python 自测_行情策略回测.py            # A + B
    python 自测_行情策略回测.py --only-a   # 只跑 A
"""
import json
import os
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import 行情K线 as K                                              # noqa: E402
import 策略引擎 as SE                                            # noqa: E402
import 回测引擎 as BT                                            # noqa: E402

OK = [0, 0]


def chk(name, cond, extra=""):
    OK[0 if cond else 1] += 1
    print("  [%s] %s %s" % ("通过" if cond else "失败", name, extra))


# ======================================================================
# A. 模块级自检
# ======================================================================
def part_a():
    print("== A1. 技术指标数学正确性 ==")
    closes = [10.0, 11.0, 12.0, 11.5, 13.0, 14.0, 13.5, 15.0, 16.0, 15.5,
              17.0, 18.0, 17.5, 19.0, 20.0]
    chk("MA5 手工比对", abs(K.MA(closes, 5)[-1] -
                            sum(closes[-5:]) / 5) < 1e-9,
        "%s" % K.MA(closes, 5)[-1])
    chk("MA 长度与 None 填充",
        len(K.MA(closes, 5)) == 15 and K.MA(closes, 5)[:4] == [None] * 4 and
        K.MA(closes, 5)[4] is not None)
    ema = K.EMA(closes, 3)
    _a = 2.0 / (3 + 1)
    _man = closes[0]
    for _v in closes[1:]:
        _man = _man + _a * (_v - _man)
    chk("EMA 递推一致", abs(ema[-1] - round(_man, 4)) < 1e-6,
        "%s vs %s" % (ema[-1], round(_man, 4)))
    dif, dea, bar = K.MACD(closes)
    chk("MACD 柱 = 2×(DIF-DEA)", all(
        abs(b - 2 * (d - e)) < 1e-6
        for d, e, b in zip(dif, dea, bar) if b is not None))
    up = [{"d": "d%d" % i, "o": 100 + i, "h": 102 + i, "l": 99 + i,
           "c": 101 + i, "v": 1, "p": 1, "s": None} for i in range(40)]
    dn = [{"d": "d%d" % i, "o": 200 - i, "h": 201 - i, "l": 198 - i,
           "c": 199 - i, "v": 1, "p": 1, "s": None} for i in range(40)]
    chk("SAR 单调上涨在价格下方", K.SAR(up)[-1] < 141, K.SAR(up)[-1])
    chk("SAR 单调下跌在价格上方", K.SAR(dn)[-1] > 160, K.SAR(dn)[-1])
    bars = [{"d": "2026-01-%02d" % (i + 1), "o": 3000 + i * 3, "h": 3010 + i * 3,
             "l": 2990 + i * 3, "c": 3000 + i * 3, "v": 100 + i, "p": 500,
             "s": None} for i in range(60)]
    for spec in ["MA(5,10,20,60)", "EMA(12,26)", "BOLL(20,2)", "SAR(4,2,2)",
                 "VOL(5,10,20)", "MACD(12,26,9)", "KDJ(9,3,3)", "RSI(6,12,24)",
                 "WR(10,6)", "BIAS(6,12,24)", "OBV(30)", "DMI(14,6)",
                 "CCI(14)", "ATR(14)"]:
        it = K.indicator_item(spec, bars)
        chk("指标 %s" % spec, it is not None and
            (it["lines"] or it.get("bars")) and
            all(len(l["data"]) == len(bars) for l in it["lines"]))

    print()
    print("== A2. 周期重采样 ==")
    d = bars[:]
    wk = K.resample([dict(b, d="2026-01-%02d" % (i + 1)) for i, b in enumerate(d)], "week")
    chk("周K 合并根数减少", len(wk) < len(d), "%d -> %d" % (len(d), len(wk)))
    mo = K.resample([dict(b, d="2025-%02d-05" % (i + 1)) for i, b in enumerate(d)], "month")
    chk("月K 按月合并", len(mo) == 12, "%d 个月" % len(mo))

    print()
    print("== A3. 策略信号与方向过滤 ==")
    C = [10, 9, 10, 11, 12, 11, 10, 9]
    O = [10] + C[:-1]
    sbars = [{"d": "2026-01-%02d" % (i + 1), "o": o, "h": max(o, c) + 1,
              "l": min(o, c) - 1, "c": c, "v": 100, "p": 100, "s": None}
             for i, (o, c) in enumerate(zip(O, C))]
    _t, _w, acts = SE.run_strategy(sbars, "ma_cross", {"周期": 2})
    chk("MA2 交叉生成动作", len(acts) > 0 and
        all(x["action"] in ("开", "平") and x["side"] in ("多", "空") for x in acts),
        "%d 个动作" % len(acts))
    # 方向档位的语义：该档位下**只**允许出现对应方向的仓位
    for dirv, expect_sides in [("双向", {"多", "空"}),
                               ("仅做多", {"多"}),
                               ("仅做空", {"空"})]:
        _t, _w, a = SE.run_strategy(sbars, "ma_cross", {"周期": 2, "方向": dirv})
        got = set(x["side"] for x in a)
        chk("方向=%s 只产生 [%s] 方向" % (dirv, "/".join(sorted(expect_sides))),
            got == expect_sides, "实际 %s" % sorted(got))
    _t, _w, a1 = SE.run_strategy(bars, "ma2_cross", {"快周期": 5, "慢周期": 20})
    _t, _w, a2 = SE.run_strategy(bars, "custom",
                                 {"左值": "MA(5)", "算子": "上穿", "右值": "MA(20)"})
    chk("custom(MA5上穿MA20) 与 双均线 信号数一致",
        len(a1) == len(a2), "%d vs %d" % (len(a1), len(a2)))
    for t in SE.TEMPLATES:
        try:
            SE.run_strategy(bars, t["kind"], t["params"])
            chk("模板 %s 可运行" % t["kind"], True)
        except Exception as e:                               # noqa: BLE001
            chk("模板 %s 可运行" % t["kind"], False, str(e))

    print()
    print("== A4. 回测合成序列手算比对 ==")
    spec = {"合约乘数": 1, "最小变动价位": 1, "保证金率": 1.0,
            "手续费": {"模式": "按手数", "开仓": 0, "平仓": 0, "平今": 0}}
    r = BT.backtest(sbars, "ma_cross", {"周期": 2}, spec,
                    {"cash": 1000, "lots": 1, "period": "day"})
    chk("期末权益 = 1003（手算）", r["stats"]["期末权益"] == 1003.0,
        str(r["stats"]["期末权益"]))
    chk("交易次数 = 2", r["stats"]["交易次数"] == 2)
    chk("无未来函数（信号 i=2 成交于 i=3 开盘 10）",
        r["trades"][0]["开仓价"] == sbars[3]["o"], str(r["trades"][0]["开仓价"]))
    chk("权益曲线与K线等长", len(r["equity"]) == len(sbars))

    spec2 = {"合约乘数": 10, "最小变动价位": 1, "保证金率": 0.18,
             "手续费": {"模式": "按成交额", "开仓": 0.0003, "平仓": 0.0003,
                        "平今": 0.0003}}
    zbars = [{"d": "2026-02-%02d" % (i + 1),
              "o": 3000 + (15 if i % 4 in (0, 1) else -15), "h": 3030, "l": 2970,
              "c": 3000 + (15 if i % 4 in (0, 1) else -15),
              "v": 1, "p": 1, "s": None} for i in range(30)]
    r2 = BT.backtest(zbars, "ma_cross", {"周期": 2}, spec2,
                     {"cash": 100000, "lots": 2, "period": "day"})
    t = r2["trades"][0]
    dsign = 1 if t["方向"] == "多" else -1
    chk("手续费 = 万3×价×乘数×手数（开+平）",
        abs(t["手续费"] - round(0.0003 * t["开仓价"] * 10 * 2 +
                                0.0003 * t["平仓价"] * 10 * 2, 2)) < 0.02,
        str(t["手续费"]))
    chk("毛盈亏含方向与乘数",
        abs(t["毛盈亏"] - (t["平仓价"] - t["开仓价"]) * dsign * 10 * 2) < 0.01,
        str(t["毛盈亏"]))
    print()
    print("== A 部分小计：通过 %d / 失败 %d ==" % (OK[0], OK[1]))


# ======================================================================
# B. HTTP 接口联调
# ======================================================================
OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))
BASE = "http://127.0.0.1:8908"


def _get(path, timeout=90):
    with OP.open(BASE + path, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _post(path, body, timeout=180):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    try:
        with OP.open(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return json.loads(e.read().decode("utf-8"))


def part_b():
    print()
    print("== B1. K 线 / 分时 / 指标接口 ==")
    d = _get("/api/kline?code=RB0&period=day&limit=300"
             "&main=MA(5,10,20,60),BOLL(20,2)&sub=VOL(5,10,20),MACD(12,26,9),KDJ(9,3,3)")
    chk("日K limit=300", len(d.get("bars") or []) == 300,
        "count=%s total=%s" % (d.get("count"), d.get("total")))
    chk("主图 2 组", len(d.get("main") or []) == 2)
    chk("副图 3 组", len(d.get("sub") or []) == 3)
    ma = [x for x in d["main"] if x["name"] == "MA"][0]
    chk("MA 4 条且长度对齐", len(ma["lines"]) == 4 and
        all(len(l["data"]) == 300 for l in ma["lines"]))
    macd = [x for x in d["sub"] if x["name"] == "MACD"][0]
    chk("MACD 柱 + 双线", macd.get("bars") is not None and len(macd["lines"]) == 2)
    for p in ("m5", "m60", "week", "month"):
        dd = _get("/api/kline?code=RB0&period=%s&limit=200" % p)
        chk("周期 %s" % p, len(dd.get("bars") or []) > 0,
            "%d 根" % len(dd.get("bars") or []))
    ts = _get("/api/timeshare?code=RB0")
    chk("分时点数", len(ts.get("points") or []) > 100,
        "%d 点" % len(ts.get("points") or []))

    print()
    print("== B2. 策略接口 ==")
    lst = _get("/api/strategy/list")
    chk("内置示例策略存在", any(s["id"] == "st_ma5demo"
                                for s in lst["strategies"]))
    sg = _get("/api/strategy/signals?code=RB0&period=day&kind=ma_cross&params=%E5%91%A8%E6%9C%9F%3D5")
    chk("单标的信号", sg.get("ok") and len(sg.get("marks") or []) > 0,
        "%d 个（共 %s）" % (len(sg.get("marks") or []), sg.get("markCount")))
    sv = _post("/api/strategy/save", {
        "name": "自测双均线", "kind": "ma2_cross", "period": "day",
        "params": {"快周期": 5, "慢周期": 20, "方向": "双向"},
        "scope": {"mode": "sector", "value": ["黑色建材"]},
        "lots": 1, "enabled": False, "dryRun": True})
    chk("保存策略", sv.get("ok"), sv.get("msg"))
    nid = sv["strategy"]["id"]
    sc = _post("/api/strategy/scan", {"id": nid, "lookback": 5})
    chk("策略选股", sc.get("ok") and sc.get("poolSize") > 0,
        "池 %s 命中 %s" % (sc.get("poolSize"), sc.get("hitCount")))
    chk("扫描行带可交易合约", all(r.get("tradeable")
                                 for r in (sc.get("rows") or [])))
    tg = _post("/api/strategy/toggle", {"id": nid, "enabled": True, "dryRun": True})
    chk("挂载（仅信号）", tg.get("ok") and tg["strategy"]["enabled"], tg.get("msg"))
    _post("/api/strategy/toggle", {"id": nid, "enabled": False})

    print()
    print("== B3. 回测接口 ==")
    bt = _post("/api/backtest/run", {
        "code": "RB0", "period": "day", "kind": "ma_cross",
        "params": {"周期": 5}, "cash": 100000, "lots": 1,
        "slippageTicks": 1, "main": "MA(5)", "sub": "VOL(5,10,20)"}, 300)
    chk("回测执行", bt.get("ok"), bt.get("msg", ""))
    st = bt.get("stats") or {}
    chk("权益曲线 = K 线长度", len(bt.get("equity") or []) == len(bt.get("bars") or []))
    chk("有交易明细", len(bt.get("trades") or []) > 0,
        "%d 笔" % len(bt.get("trades") or []))
    chk("主副图指标返回", len(bt.get("main") or []) >= 1 and
        len(bt.get("sub") or []) >= 1)
    chk("绩效字段齐全", all(k in st for k in
                            ("总收益率", "年化收益率", "最大回撤", "胜率", "盈亏比",
                             "夏普比率", "总手续费", "最大连续亏损")))
    bt2 = _post("/api/backtest/run", {
        "code": "RB0", "period": "day", "kind": "ma_cross",
        "params": {"周期": 20}, "start": "2022-01-01", "cash": 200000,
        "lots": 2, "stopLossPct": 4, "slippageTicks": 1}, 300)
    chk("区间 + 止损回测", bt2.get("ok"),
        "收益 %s%% 回撤 %s%%" % ((bt2.get("stats") or {}).get("总收益率"),
                                (bt2.get("stats") or {}).get("最大回撤")))
    bl = _get("/api/backtest/list")
    chk("回测记录登记", len(bl.get("items") or []) >= 2)

    print()
    print("== B4. 复盘接口 ==")
    rp = _post("/api/replay/open", {"code": "RB0", "period": "day", "bars": 120,
                                    "kind": "ma_cross", "params": {"周期": 5}})
    chk("开启复盘", rp.get("ok"), rp.get("msg", ""))
    if rp.get("ok"):
        sid = rp["session"]
        s1 = _post("/api/replay/step", {"id": sid, "n": 20})
        chk("步进 20 根", s1["idx"] == rp["start"] + 20)
        tr = _post("/api/replay/trade", {"id": sid, "side": "多", "lots": 2})
        chk("手动开仓", "开仓" in (tr.get("msg") or ""), tr.get("msg"))
        _post("/api/replay/step", {"id": sid, "n": 15})
        tr2 = _post("/api/replay/trade", {"id": sid, "side": "空", "lots": 2})
        chk("手动平仓结算", tr2["stat"]["已平仓"] == 1, tr2.get("msg"))
        v = _get("/api/replay/view?id=%s" % sid)
        chk("复盘视图根数", len(v.get("bars") or []) == s1["idx"] + 15 - rp["start"] + 1)
        cl = _post("/api/replay/close", {"id": sid})
        chk("结束复盘", cl.get("ok"))

    print()
    print("== B5. 资金管理 + 复盘自动跟随 + 导出 ==")
    cfg = _get("/api/config")
    presets = {p["name"]: p for p in (cfg.get("资金管理") or {}).get("预设", [])}
    chk("内置 4 套科学预设", len(presets) == 4, " / ".join(presets))
    mm = presets["海龟式趋势（加仓）"]["mm"]
    svm = _post("/api/strategy/save", {
        "name": "自测-海龟加仓", "kind": "donchian", "period": "day",
        "params": {"入场周期": 20, "出场周期": 10, "方向": "双向"},
        "scope": {"mode": "codes", "value": ["RB0"]},
        "lots": 1, "mm": mm, "enabled": False, "dryRun": True})
    chk("策略保存带资金管理", svm.get("ok") and
        (svm["strategy"].get("mm") or {}).get("手数模式") == "风险固定",
        svm.get("msg", ""))
    mmid = svm["strategy"]["id"]
    btm = _post("/api/backtest/run", {"strategyId": mmid, "code": "RB0",
                                      "period": "day", "cash": 100000,
                                      "slippageTicks": 1}, 300)
    chk("资金管理回测执行", btm.get("ok"), btm.get("msg", ""))
    stm = btm.get("stats") or {}
    chk("回测含资金管理统计", all(k in stm for k in
                                  ("平均手数", "最大手数", "期望R", "累计R",
                                   "平均保证金占用%", "熔断次数", "风控拒单")),
        "期望R=%s 累计R=%s 最大手数=%s" % (stm.get("期望R"), stm.get("累计R"),
                                          stm.get("最大手数")))
    chk("回测明细含止损价/风险额/R", all(k in (btm.get("trades") or [{}])[0]
                                        for k in ("止损价", "风险额", "盈亏R",
                                                  "平仓类型")))
    rpx = _post("/api/replay/open", {"code": "RB0", "period": "day", "bars": 160,
                                     "cash": 100000, "strategyId": mmid,
                                     "auto": True, "takeProfitPct": 0})
    chk("复盘自动跟随开启", rpx.get("ok") and rpx.get("auto") is True,
        rpx.get("msg", ""))
    if rpx.get("ok"):
        rid = rpx["session"]
        stp = _post("/api/replay/step", {"id": rid, "n": 90})
        recs = stp.get("records") or []
        chk("自动跟随产生记录", len(recs) > 0, "%d 条" % len(recs))
        need = {"序号", "时间", "动作", "方向", "手数", "价格", "成交额", "手续费",
                "净盈亏", "持仓根数", "权益", "收益率%", "回撤%", "可用资金",
                "止损价", "风险额", "盈亏R", "来源", "说明"}
        missing = sorted(need - set(recs[0])) if recs else ["无记录"]
        chk("记录 24 字段完整", not missing, "缺 %s" % missing)
        eq = stp.get("equity") or []
        chk("资金曲线 12 列", len(eq) > 0 and len(eq[-1]) >= 12,
            "%d 点" % len(eq))
        for f in ("csv", "tsv", "json", "jsonl"):
            e = _post("/api/replay/export", {"id": rid, "fmt": f})
            chk("导出 %s" % f, e.get("ok") and len(e.get("text") or "") > 0,
                "%s / %d 字节" % (e.get("filename"), len(e.get("text") or "")))
        e = _post("/api/replay/export", {"id": rid, "fmt": "csv"})
        head = (e.get("text") or "").split("\n")
        chk("CSV 含元信息与表头", any(x.startswith("# 统计.")
                                      for x in head[:20]) and
            sum(1 for x in head if x.startswith("序号,")) == 1)
        chk("关闭复盘会话", _post("/api/replay/close",
                                  {"id": rid}).get("ok"))

    print()
    print("== B6. 清理 ==")
    dl = _post("/api/strategy/delete", {"id": nid})
    chk("删除测试策略", dl.get("ok"), dl.get("msg"))
    _post("/api/strategy/delete", {"id": mmid})


def main():
    part_a()
    if "--only-a" not in sys.argv:
        part_b()
    print()
    print("===== 结果: 通过 %d / 失败 %d =====" % (OK[0], OK[1]))
    sys.exit(1 if OK[1] else 0)


if __name__ == "__main__":
    main()
