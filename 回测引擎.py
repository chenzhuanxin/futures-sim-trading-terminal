# -*- coding: utf-8 -*-
"""
回测与复盘引擎（纯标准库）

回测口径（无未来函数）：
  · 第 i 根 K 线「收盘」产生信号，第 i+1 根「开盘」成交（execMode=next_open）。
  · 手数由「资金管理」模块决定（风险固定 / 保证金比例 / 波动率目标 / 分数凯利 / 固定手数），
    计算基准是**上一根收盘时的权益**，不使用当根信息。
  · 成交价含滑点：买入 = 基准 + 滑点跳数 × 最小变动价位；卖出 = 基准 − 滑点跳数 × 最小变动价位。
  · 手续费按品种参数表计提，开仓/平仓分列；同一交易日内开平按「平今」费率，隔日按「平昨」。
  · 保证金占用 = 成交价 × 合约乘数 × 保证金率 × 保证金倍数 × 手数；可用资金不足则放弃该笔开仓。
  · 逐笔盯市：权益 = 现金 + Σ持仓浮动盈亏。
  · 止损位由资金管理模块给出（ATR / 百分比 / 跳数）；止盈按开仓价百分比。
    止损止盈都在当根内用「开盘价 + 最高/最低价」判定，反映跳空，且不使用未来信息。
  · 金字塔加仓按触发当根收盘价成交（浮盈在收盘时已知，不构成未来函数）。
  · 熔断 / 连亏减仓 / 日内亏损上限 / 保证金占用上限由资金管理模块的闸门统一拦截。

复盘口径：指定区间与起始根，逐根揭示。可以：
  · 手动在任意位置按当根收盘价记录一笔交易（训练判断力）；
  · 或打开「自动跟随策略信号」，由资金管理模块按同样口径自动开平，
    从而得到一条可对照的资金变化曲线。
每条记录都带权益 / 收益率 / 回撤 / 手续费 / 成交额 / R 倍数，可折叠、可复制、可导出。
"""
import math
import threading
import uuid
from datetime import datetime

import 策略引擎 as SE
import 资金管理 as MM

MULT_KEYS = ("合约乘数", "最小变动价位", "保证金率")


# ----------------------------------------------------------------------
# 周期 -> 每年 K 线根数（近似，用于年化与夏普）
# ----------------------------------------------------------------------
BARS_PER_YEAR = {
    "day": 252, "week": 52, "month": 12,
    "m240": int(252 * 1.4), "m120": int(252 * 2.9), "m60": int(252 * 5.75),
    "m30": int(252 * 11.5), "m15": int(252 * 23), "m5": int(252 * 69),
    "m3": int(252 * 115), "m1": int(252 * 345),
}


def fnum(x, nd=4):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if v != v or v in (float("inf"), float("-inf")):
        return None
    return round(v, nd)


def _fee_of(spec, price, lots, is_open, same_day, settings):
    """按品种参数表计算手续费。"""
    f = (spec or {}).get("手续费") or {}
    mode = f.get("模式") or "按手数"
    disc = float((settings or {}).get("手续费折扣", 1) or 1)
    if is_open:
        rate = f.get("开仓", 0)
    else:
        rate = f.get("平今", f.get("平仓", 0)) if same_day else f.get("平仓", 0)
    mult = float((spec or {}).get("合约乘数") or 1)
    if mode == "按手数":
        v = float(rate or 0) * lots * disc
    else:
        v = float(rate or 0) * price * mult * lots * disc
    return round(v, 2)


def _margin_of(spec, price, lots, settings):
    rate = float((spec or {}).get("保证金率") or 0)
    mult = float((spec or {}).get("合约乘数") or 1)
    k = float((settings or {}).get("保证金倍数", 1) or 1)
    return round(price * mult * rate * k * lots, 2)


def mm_from_cfg(cfg):
    """把回测配置整理成资金管理配置。

    显式传了 mm 就用 mm；否则用旧字段（lots/lotsMode/lotsRatio/maxLots/stopLossPct）
    合成一个等效配置，保证老调用方的行为不变。
    """
    if cfg.get("mm"):
        return MM.norm(cfg["mm"])
    lots = max(1, int(cfg.get("lots") or 1))
    sl = max(0.0, float(cfg.get("stopLossPct") or 0))
    mm = {"启用": True, "固定手数": lots, "最小手数": 1,
          "单笔最大手数": max(1, int(cfg.get("maxLots") or 20)),
          "保证金占用上限%": 100.0, "最大回撤熔断%": 0.0,
          "连亏减仓笔数": 0, "连亏停手笔数": 0, "日内亏损上限%": 0.0,
          "加仓模式": "不加仓", "移动止损": "关闭"}
    if (cfg.get("lotsMode") or "fixed") == "ratio":
        mm["手数模式"] = "保证金比例"
        mm["资金比例%"] = float(cfg.get("lotsRatio") or 0.3) * 100.0
    else:
        mm["手数模式"] = "固定手数"
    if sl > 0:
        mm["止损方式"] = "百分比"
        mm["止损%"] = sl
    else:
        mm["止损方式"] = "不设止损"
    return MM.norm(mm)


# ----------------------------------------------------------------------
# 回测
# ----------------------------------------------------------------------
def backtest(bars, kind, params, spec, cfg=None):
    """执行回测。bars 为 K 线数组，spec 为品种参数条目，cfg 为回测配置。"""
    cfg = dict(cfg or {})
    cash0 = float(cfg.get("cash") or 100000)
    exec_mode = cfg.get("execMode") or "next_open"
    slip = max(0.0, float(cfg.get("slippageTicks") or 0))
    spec = spec or {}
    tick = float(spec.get("最小变动价位") or 1) or 1
    mult = float(spec.get("合约乘数") or 1) or 1
    tp = max(0.0, float(cfg.get("takeProfitPct") or 0)) / 100.0
    settings = cfg.get("settings") or {}
    mm = mm_from_cfg(cfg)
    MGR = MM.MoneyManager(mm, bars, spec, settings)

    L = len(bars)
    if L < 5:
        return {"ok": False, "msg": "K 线不足，无法回测"}

    targets, why, acts = SE.run_strategy(bars, kind, params)

    # 信号在第 i 根收盘产生 -> 第 i+1 根开盘成交
    plan = {}
    for a in acts:
        j = a["i"] + 1 if exec_mode == "next_open" else a["i"]
        if 0 <= j < L:
            plan.setdefault(j, []).append(a)

    cash = cash0
    pos = None
    trades, equity = [], []
    fee_total = 0.0
    peak_eq = cash0
    skipped_fund = skipped_risk = 0
    halt_count = 0
    halted_until = None              # 熔断后不再开仓的时间点（None=未熔断）
    day_key, day_eq0 = None, cash0
    day_halted = False
    consec_loss = 0
    wins = hits = 0
    gross_win = gross_loss = 0.0
    max_lots_used = 0
    max_margin_pct = 0.0
    eq_prev = cash0
    margin_sum = 0.0
    margin_n = 0
    pos_lots_sum = 0
    pos_n = 0
    stop_events = 0

    def slip_px(base, side_sign):
        return round(base + side_sign * slip * tick, 6)

    def margin_used_of(p, price):
        if not p:
            return 0.0
        return _margin_of(spec, price, p["lots"], settings)

    def float_of(p, price):
        if not p:
            return 0.0
        return (price - p["avg"]) * p["dir"] * mult * p["lots"]

    def state_now(price_mark):
        return {"equity": eq_prev if eq_prev > 0 else cash0,
                "peak": peak_eq if peak_eq > 0 else cash0,
                "cash": cash, "marginUsed": margin_used_of(pos, price_mark),
                "consecLoss": consec_loss, "wins": wins, "closed": wins + hits_of(),
                "grossWin": gross_win, "grossLoss": gross_loss,
                "dayEquity0": day_eq0}

    def hits_of():
        return len(trades) - wins

    # ---------- 开仓 ----------
    def do_open(i, o, side_dir, price, reason, forced_lots=None):
        """返回 True 表示开仓成功。"""
        nonlocal cash, pos, fee_total, skipped_fund, skipped_risk
        nonlocal max_lots_used, pos_lots_sum, pos_n
        st = state_now(price)
        if forced_lots:
            unit = _margin_of(spec, price, 1, settings)
            lots = max(1, int(forced_lots))
            if mm["保证金占用上限%"] < 100:
                cap = max(0.0, st["equity"]) * mm["保证金占用上限%"] / 100.0
                room = cap - st["marginUsed"]
                if unit > 0:
                    lots = min(lots, max(0, int(math.floor(room / unit + 1e-9))))
            if lots < 1:
                skipped_risk += 1
                return False
            stop, sdist, stxt = MGR.stop_price(i, side_dir, price)
            pl = {"ok": True, "lots": lots, "stop": stop, "stopDist": sdist,
                  "margin": _margin_of(spec, price, lots, settings),
                  "risk": round(sdist * mult * lots, 2),
                  "notes": ["手动手数 %d" % lots, stxt], "reject": ""}
        else:
            pl = MGR.plan(i, price, side_dir, st)
            if not pl["ok"]:
                if pl["reject"]:
                    skipped_risk += 1
                else:
                    skipped_fund += 1
                return False
            lots = pl["lots"]

        o_side = 1 if side_dir > 0 else -1
        fill = slip_px(price, o_side)
        need_f = _fee_of(spec, fill, lots, True, True, settings)
        need_m = _margin_of(spec, fill, lots, settings)
        if cash < need_f:
            skipped_fund += 1
            return False
        cash -= need_f
        fee_total += need_f
        pos = {"dir": side_dir, "lots": lots, "avg": fill, "idx": i,
               "date": bars[i]["d"], "fee_open": need_f, "reason": reason,
               "stop": pl.get("stop"), "extreme": fill, "adds": 0,
               "first_lots": lots, "risk": pl.get("risk") or 0.0,
               "planNotes": pl.get("notes") or [],
               "legs": [{"lots": lots, "price": fill, "idx": i,
                         "date": bars[i]["d"], "fee": need_f}]}
        max_lots_used = max(max_lots_used, lots)
        pos_lots_sum += lots
        pos_n += 1
        del need_m
        return True

    # ---------- 平仓 ----------
    def do_close(i, px, reason, kindtxt="信号"):
        nonlocal cash, pos, fee_total, consec_loss, wins, hits
        nonlocal gross_win, gross_loss
        same_day = (pos["date"] or "")[:10] == (bars[i]["d"] or "")[:10]
        fee = _fee_of(spec, px, pos["lots"], False, same_day, settings)
        pnl = (px - pos["avg"]) * pos["dir"] * mult * pos["lots"]
        fee_open = pos.get("fee_open") or 0.0
        net = round(pnl - fee - fee_open, 2)
        cash += pnl - fee
        fee_total += fee
        eq_after = cash
        dd_after = ((peak_eq - eq_after) / peak_eq * 100.0) if peak_eq > 0 else 0.0
        risk = pos.get("risk") or 0.0
        rec = {
            "序号": len(trades) + 1,
            "方向": "多" if pos["dir"] > 0 else "空",
            "手数": pos["lots"],
            "开仓时间": pos["date"], "开仓价": round(pos["avg"], 4),
            "平仓时间": bars[i]["d"], "平仓价": round(px, 4),
            "持仓根数": i - pos["idx"],
            "毛盈亏": round(pnl, 2),
            "手续费": round(fee + fee_open, 2),
            "净盈亏": net,
            "开仓理由": pos.get("reason") or "",
            "平仓理由": reason or "",
            "平仓类型": kindtxt,
            "止损价": pos.get("stop"),
            "风险额": round(risk, 2),
            "盈亏R": round(net / risk, 2) if risk > 0 else None,
            "加仓次数": pos.get("adds", 0),
            "开仓明细": " / ".join("%g×%d" % (l["price"], l["lots"])
                                   for l in pos.get("legs") or []),
            "权益": round(eq_after, 2),
            "收益率%": round((eq_after - cash0) / cash0 * 100.0, 2) if cash0 else 0.0,
            "回撤%": round(dd_after, 2),
        }
        trades.append(rec)
        if net > 0:
            wins += 1
            gross_win += net
            consec_loss = 0
        else:
            hits += 1
            gross_loss += abs(net)
            consec_loss += 1
        pos = None
        return net

    # ---------- 主循环 ----------
    for i in range(L):
        b = bars[i]
        o, h, l, c = b["o"], b["h"], b["l"], b["c"]
        if o is None or c is None:
            continue
        d10 = (b["d"] or "")[:10]
        if d10 != day_key:
            day_key, day_eq0, day_halted = d10, eq_prev or cash0, False

        # --- 1) 止损 / 止盈（当根内，用开盘价与极值） ---
        if pos:
            d = pos["dir"]
            stop_px = pos.get("stop")
            take_px = None
            if tp > 0:
                take_px = pos["avg"] * (1 + tp) if d > 0 else pos["avg"] * (1 - tp)
            hit = None
            if d > 0:
                if stop_px is not None and l is not None and l <= stop_px:
                    hit = ("stop", min(o, stop_px), "止损")
                elif take_px is not None and h is not None and h >= take_px:
                    hit = ("take", max(o, take_px), "止盈")
            else:
                if stop_px is not None and h is not None and h >= stop_px:
                    hit = ("stop", max(o, stop_px), "止损")
                elif take_px is not None and l is not None and l <= take_px:
                    hit = ("take", min(o, take_px), "止盈")
            if hit:
                px = slip_px(hit[1], -1 if d > 0 else 1)
                txt = hit[2]
                if txt == "止损" and pos.get("moved"):
                    txt = "移动止损"
                do_close(i, px, "%s @%g" % (txt, px), txt)
                if txt == "止损":
                    stop_events += 1

        # --- 2) 熔断 / 日内亏损闸门（用上一根权益判断） ---
        if mm["最大回撤熔断%"] > 0 and peak_eq > 0:
            dd = (peak_eq - (eq_prev or cash0)) / peak_eq * 100.0
            if dd >= mm["最大回撤熔断%"]:
                if halted_until is None:
                    halted_until = i
                    halt_count += 1
                    if mm["熔断后清仓"] and pos:
                        px = slip_px(o, -1 if pos["dir"] > 0 else 1)
                        do_close(i, px, "回撤熔断强制清仓 @%g" % px, "熔断")
        if mm["日内亏损上限%"] > 0 and day_eq0 > 0 and not day_halted:
            if (day_eq0 - (eq_prev or cash0)) / day_eq0 * 100.0 >= mm["日内亏损上限%"]:
                day_halted = True

        # --- 3) 执行本根开盘的计划信号 ---
        for a in plan.get(i, []):
            want_open = a["action"] == "开"
            side_dir = 1 if a["side"] == "多" else -1
            if not want_open:
                if pos and pos["dir"] == side_dir:
                    px = slip_px(o, -1 if side_dir > 0 else 1)
                    do_close(i, px, a["reason"], "信号")
            else:
                if pos and pos["dir"] == side_dir:
                    continue
                if pos:
                    px = slip_px(o, -1 if pos["dir"] > 0 else 1)
                    do_close(i, px, "信号反手", "信号")
                if halted_until is not None or day_halted:
                    skipped_risk += 1
                    continue
                do_open(i, o, side_dir, o, a["reason"])

        # --- 4) 金字塔加仓 / 移动止损（收盘价） ---
        if pos:
            d = pos["dir"]
            if c > pos["extreme"] if d > 0 else c < pos["extreme"]:
                pos["extreme"] = c
            # 移动止损
            newstop = MGR.trail_stop(pos, b, i)
            if newstop is not None and pos.get("stop") is not None:
                if (d > 0 and newstop > pos["stop"]) or (d < 0 and newstop < pos["stop"]):
                    pos["stop"] = round(newstop, 6)
                    pos["moved"] = True
            # 加仓
            if not (halted_until is not None or day_halted):
                st = state_now(c)
                add = MGR.add_lots(i, c, pos, st)
                if add:
                    fill = slip_px(c, 1 if d > 0 else -1)
                    fee = _fee_of(spec, fill, add["lots"], True, True, settings)
                    if cash >= fee:
                        cash -= fee
                        fee_total += fee
                        leg = {"lots": add["lots"], "price": fill, "idx": i,
                               "date": b["d"], "fee": fee}
                        tot = pos["lots"] + add["lots"]
                        pos["avg"] = round(
                            (pos["avg"] * pos["lots"] + fill * add["lots"]) / tot, 6)
                        pos["lots"] = tot
                        pos["fee_open"] = (pos.get("fee_open") or 0) + fee
                        pos["adds"] = pos.get("adds", 0) + 1
                        pos["legs"].append(leg)
                        pos["planNotes"].append(add["note"])
                        max_lots_used = max(max_lots_used, tot)
                        if mm["_stop"] != "none":
                            s2, _dd, _t = MGR.stop_price(i, d, pos["avg"])
                            if s2 is not None and pos.get("stop") is not None:
                                pos["stop"] = (max(pos["stop"], s2) if d > 0
                                               else min(pos["stop"], s2))

        # --- 5) 盯市 ---
        fp = float_of(pos, c)
        mu = margin_used_of(pos, c)
        eq = cash + fp
        if eq > peak_eq:
            peak_eq = eq
            halted_until = None                     # 创新高解除熔断
        dd = (peak_eq - eq) / peak_eq * 100.0 if peak_eq > 0 else 0.0
        if mm["保证金占用上限%"] < 100:
            base = eq if eq > 0 else 1.0
            pct = mu / base * 100.0
            max_margin_pct = max(max_margin_pct, pct)
            if mu > 0:
                margin_sum += pct
                margin_n += 1
        equity.append([i, b["d"], round(eq, 2), round(cash, 2), c,
                       round(dd, 4), round(mu, 2),
                       pos["dir"] if pos else 0,
                       pos["lots"] if pos else 0,
                       round(pos["avg"], 4) if pos else None])
        eq_prev = eq
        if cash + fp <= 0:
            break

    # ---- 收尾：强制平掉未平仓 ----
    if pos and equity:
        i = len(equity) - 1
        px = slip_px(bars[min(i, L - 1)]["c"], -1 if pos["dir"] > 0 else 1)
        do_close(min(i, L - 1), px, "回测结束强制平仓", "强制平仓")
        if equity:
            equity[-1][2] = round(cash, 2)
            equity[-1][4] = bars[min(i, L - 1)]["c"]
            equity[-1][7] = 0
            equity[-1][8] = 0
            equity[-1][9] = None
            equity[-1][6] = 0.0

    stats = _stats(equity, trades, cash, cash0, cfg.get("period") or "day",
                   fee_total, skipped_fund + skipped_risk, {
                       "skipped_fund": skipped_fund, "skipped_risk": skipped_risk,
                       "halt": halt_count, "stop_events": stop_events,
                       "max_lots": max_lots_used,
                       "avg_lots": (pos_lots_sum / pos_n) if pos_n else 0,
                       "max_margin_pct": max_margin_pct,
                       "avg_margin_pct": (margin_sum / margin_n) if margin_n else 0,
                       "mm": mm,
                   })
    return {"ok": True, "bars": bars,
            "equity": equity, "trades": trades,
            "marks": [{"i": a["i"], "d": a["d"], "side": a["side"],
                       "action": a["action"], "price": a["price"],
                       "reason": a["reason"]} for a in acts],
            "stats": stats,
            "meta": {"kind": kind, "params": params, "execMode": exec_mode,
                     "cash": cash0, "slippageTicks": slip,
                     "takeProfitPct": tp * 100,
                     "mm": MM.clean_for_save(mm),
                     "mmText": MM.describe(mm),
                     "lots": mm["固定手数"],
                     "lotsMode": mm["_lot"],
                     "stopLossPct": mm["止损%"]}}


def _stats(equity, trades, final_cash, cash0, period, fee_total, skipped, extra=None):
    extra = extra or {}
    n = len(trades)
    wins = [t for t in trades if t["净盈亏"] > 0]
    loss = [t for t in trades if t["净盈亏"] <= 0]
    gross_win = sum(t["净盈亏"] for t in wins)
    gross_loss = abs(sum(t["净盈亏"] for t in loss))
    bpy = BARS_PER_YEAR.get(period, 252)
    eq = [e[2] for e in equity]
    final_eq = eq[-1] if eq else cash0
    total_ret = (final_eq - cash0) / cash0 * 100.0 if cash0 else 0.0

    bars_n = max(1, len(eq))
    years = bars_n / float(bpy)
    if years > 0 and final_eq > 0 and cash0 > 0:
        annual = ((final_eq / cash0) ** (1.0 / years) - 1.0) * 100.0
    else:
        annual = 0.0

    peak, mdd, s_i, e_i = -1e18, 0.0, 0, 0
    s0 = 0
    for k, v in enumerate(eq):
        if v > peak:
            peak, s0 = v, k
        if peak > 0:
            d = (peak - v) / peak * 100.0
            if d > mdd:
                mdd, s_i, e_i = d, s0, k

    rets = []
    for k in range(1, len(eq)):
        if eq[k - 1] > 0:
            rets.append(eq[k] / eq[k - 1] - 1.0)
    sharpe = 0.0
    if len(rets) > 2:
        mu = sum(rets) / len(rets)
        var = sum((r - mu) ** 2 for r in rets) / (len(rets) - 1)
        sd = math.sqrt(var)
        if sd > 1e-12:
            sharpe = mu / sd * math.sqrt(bpy)

    cur = best = 0
    for t in trades:
        if t["净盈亏"] <= 0:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0

    # R 倍数统计（有止损的风险额才有意义）
    rs = [t["盈亏R"] for t in trades if t.get("盈亏R") is not None]
    win_r = [r for r in rs if r > 0]
    loss_r = [r for r in rs if r <= 0]
    avg_win_r = sum(win_r) / len(win_r) if win_r else 0.0
    avg_loss_r = abs(sum(loss_r) / len(loss_r)) if loss_r else 0.0
    wr = len(wins) / n if n else 0.0
    expect_r = wr * avg_win_r - (1 - wr) * avg_loss_r if rs else 0.0

    longs = [t for t in trades if t["方向"] == "多"]
    shorts = [t for t in trades if t["方向"] == "空"]
    hold = [t["持仓根数"] for t in trades]
    mm = extra.get("mm") or {}
    return {
        "初始资金": round(cash0, 2),
        "期末权益": round(final_eq, 2),
        "总收益率": round(total_ret, 2),
        "年化收益率": round(annual, 2),
        "最大回撤": round(mdd, 2),
        "最大回撤起点": equity[s_i][1] if equity and s_i < len(equity) else "",
        "最大回撤终点": equity[e_i][1] if equity and e_i < len(equity) else "",
        "交易次数": n,
        "盈利次数": len(wins), "亏损次数": len(loss),
        "胜率": round(len(wins) / n * 100.0, 2) if n else 0.0,
        "盈亏比": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
        "总盈亏": round(sum(t["净盈亏"] for t in trades), 2),
        "总手续费": round(fee_total, 2),
        "平均每笔盈亏": round(sum(t["净盈亏"] for t in trades) / n, 2) if n else 0.0,
        "最大单笔盈利": round(max([t["净盈亏"] for t in trades] or [0]), 2),
        "最大单笔亏损": round(min([t["净盈亏"] for t in trades] or [0]), 2),
        "最大连续亏损": best,
        "夏普比率": round(sharpe, 2),
        "平均持仓根数": round(sum(hold) / len(hold), 1) if hold else 0,
        "多头交易": len(longs), "空头交易": len(shorts),
        "多头胜率": round(len([t for t in longs if t["净盈亏"] > 0]) / len(longs) * 100, 2) if longs else 0.0,
        "空头胜率": round(len([t for t in shorts if t["净盈亏"] > 0]) / len(shorts) * 100, 2) if shorts else 0.0,
        "资金不足跳过": extra.get("skipped_fund", skipped),
        "风控拒单": extra.get("skipped_risk", 0),
        "回测根数": len(eq),
        # ---- 资金管理相关 ----
        "平均手数": round(extra.get("avg_lots", 0), 2),
        "最大手数": extra.get("max_lots", 0),
        "平均盈利R": round(avg_win_r, 2),
        "平均亏损R": round(avg_loss_r, 2),
        "期望R": round(expect_r, 3),
        "累计R": round(sum(rs), 2) if rs else 0.0,
        "平均保证金占用%": round(extra.get("avg_margin_pct", 0), 1),
        "最大保证金占用%": round(extra.get("max_margin_pct", 0), 1),
        "熔断次数": extra.get("halt", 0),
        "止损离场次数": extra.get("stop_events", 0),
        "资金管理方案": MM.describe(mm) if mm else "",
        "手数模式": mm.get("手数模式", ""),
        "止损方式": mm.get("止损方式", ""),
    }


# ----------------------------------------------------------------------
# 复盘会话
# ----------------------------------------------------------------------
REC_KEYS = ["序号", "时间", "动作", "方向", "手数", "价格", "成交额", "手续费",
            "净盈亏", "持仓根数", "权益", "收益率%", "回撤%", "可用资金",
            "保证金", "持仓方向", "持仓手数", "持仓均价", "止损价", "风险额",
            "盈亏R", "加仓", "来源", "说明"]


class ReplaySession(object):
    """逐根揭示的复盘会话。

    两个模式可混用：
      · 手动：用户按当根收盘价记一笔开/平；
      · 自动：打开 auto 后由策略信号 + 资金管理自动开平（第 i 根信号 → 第 i+1 根开盘成交）。
    每推进一根都会重算权益路径，因此资金变化曲线与记录永远自洽。
    """

    def __init__(self, sid, code, name, period, bars, spec, start, end,
                 kind="", params=None, settings=None, mm=None, cash=100000.0,
                 auto=False, tp_pct=0.0):
        self.sid = sid
        self.code = code
        self.name = name
        self.period = period
        self.bars = bars
        self.spec = spec or {}
        self.kind = kind
        self.params = params or {}
        self.settings = settings or {}
        self.cash0 = float(cash or 100000)
        self.tp = max(0.0, float(tp_pct or 0)) / 100.0
        self.auto = bool(auto)
        self.mm = MM.norm(mm)
        self.start = max(0, min(int(start), max(0, len(bars) - 1)))
        self.end = max(self.start, min(int(end), max(0, len(bars) - 1)))
        self.idx = self.start
        self.createdAt = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.mult = float(self.spec.get("合约乘数") or 1) or 1
        self.orders = []             # 用户下单 [{"i","op","side","lots","ts"}]
        self.records = []
        self.equity = []
        self.pos = None
        self.signals = []
        self.mmText = MM.describe(self.mm)
        self.HIST = MM.MoneyManager(self.mm, bars, self.spec, self.settings)
        if kind:
            try:
                _t, _w, self.signals = SE.run_strategy(bars, kind, params)
            except Exception:                                  # noqa: BLE001
                self.signals = []
        self._rebuild()

    # ---------- 核心：重算权益路径 ----------
    def _rebuild(self):
        bars, mult = self.bars, self.mult
        spec, settings = self.spec, self.settings
        mm = self.mm
        cash = self.cash0
        pos = None
        peak = self.cash0
        recs, curve = [], []
        fpnl = 0.0
        seq = 0
        closes = 0
        wins = 0
        gross_win = gross_loss = 0.0
        consec = 0
        fee_total = 0.0
        # 自动信号：第 i 根收盘出信号 -> 第 i+1 根开盘成交
        auto_plan = {}
        if self.auto and self.signals:
            for a in self.signals:
                j = a["i"] + 1
                if 0 <= j < len(bars):
                    auto_plan.setdefault(j, []).append(a)
        user_plan = {}
        for o in self.orders:
            user_plan.setdefault(o["i"], []).append(o)

        def do_open(i, price, side, lots, reason, source, forced=True):
            nonlocal cash, pos, seq, fee_total
            if pos is not None:
                return False
            fill = price
            fee = _fee_of(spec, fill, lots, True, True, settings)
            st = {"equity": (cash + fpnl), "peak": peak, "cash": cash,
                  "marginUsed": 0.0, "consecLoss": consec, "wins": wins,
                  "closed": closes, "grossWin": gross_win,
                  "grossLoss": gross_loss, "dayEquity0": cash + fpnl}
            stop = None
            risk = 0.0
            if source == "自动信号":
                pl = self.HIST.plan(i, fill, side, st)
                if not pl["ok"]:
                    self.lastReject = pl["reject"] or "资金管理拒绝开仓"
                    return False
                lots = pl["lots"]
                stop = pl["stop"]
                risk = pl.get("risk") or 0.0
                reason = reason + "｜" + "；".join(pl["notes"])
            else:
                s2, _d, _t = self.HIST.stop_price(i, side, fill)
                stop = s2
                if stop is not None:
                    risk = round(abs(fill - stop) * mult * lots, 2)
            if cash < fee:
                self.lastReject = "可用资金不足"
                return False
            cash -= fee
            fee_total += fee
            pos = {"dir": side, "lots": lots, "avg": fill, "idx": i,
                   "date": bars[i]["d"], "fee_open": fee, "reason": reason,
                   "stop": stop, "risk": risk, "extreme": fill,
                   "first_lots": lots, "adds": 0, "legs": [lots], "source": source}
            seq += 1
            recs.append(_rec(seq, i, bars[i], "开", "多" if side > 0 else "空",
                             lots, fill, mult, fee, None, None, reason, source,
                             cash + 0.0, peak, cash0=self.cash0, stop=stop,
                             risk=risk, pos=pos))
            return True

        def do_close(i, price, reason, source):
            nonlocal cash, pos, seq, closes, wins, gross_win, gross_loss
            nonlocal consec, fee_total
            if pos is None:
                return False
            same_day = (pos["date"] or "")[:10] == (bars[i]["d"] or "")[:10]
            fee = _fee_of(spec, price, pos["lots"], False, same_day, settings)
            pnl = (price - pos["avg"]) * pos["dir"] * mult * pos["lots"]
            net = round(pnl - fee - (pos.get("fee_open") or 0), 2)
            cash += pnl - fee
            fee_total += fee
            seq += 1
            recs.append(_rec(seq, i, bars[i], "平", "多" if pos["dir"] > 0 else "空",
                             pos["lots"], price, mult, fee, net,
                             i - pos["idx"], reason, source, cash,
                             peak, cash0=self.cash0, stop=pos.get("stop"),
                             risk=pos.get("risk") or 0.0, pos=pos))
            closes += 1
            if net > 0:
                wins += 1
                gross_win += net
                consec = 0
            else:
                gross_loss += abs(net)
                consec += 1
            pos = None
            return True

        for i in range(self.start, self.idx + 1):
            b = bars[i]
            o, h, l, c = b["o"], b["h"], b["l"], b["c"]
            if o is None or c is None:
                curve.append([i, b["d"], round(cash, 2), round(cash, 2), c, 0.0,
                              0.0, 0, 0, None])
                continue
            # 1) 止损 / 止盈
            if pos:
                d = pos["dir"]
                sp = pos.get("stop")
                tpx = None
                if self.tp > 0:
                    tpx = pos["avg"] * (1 + self.tp) if d > 0 else pos["avg"] * (1 - self.tp)
                hit = None
                if d > 0:
                    if sp is not None and l is not None and l <= sp:
                        hit = (min(o, sp), "止损")
                    elif tpx is not None and h is not None and h >= tpx:
                        hit = (max(o, tpx), "止盈")
                else:
                    if sp is not None and h is not None and h >= sp:
                        hit = (max(o, sp), "止损")
                    elif tpx is not None and l is not None and l <= tpx:
                        hit = (min(o, tpx), "止盈")
                if hit:
                    do_close(i, hit[0], "%s @%g" % (hit[1], hit[0]), hit[1])
            # 2) 自动信号（开盘成交）
            for a in auto_plan.get(i, []):
                side = 1 if a["side"] == "多" else -1
                if a["action"] == "开":
                    if pos and pos["dir"] != side:
                        do_close(i, o, "信号反手：" + a["reason"], "信号")
                    if pos is None:
                        do_open(i, o, side, 1, a["reason"], "自动信号")
                else:
                    if pos and pos["dir"] == side:
                        do_close(i, o, a["reason"], "信号")
            # 3) 用户手动动作（收盘价成交）
            for od in user_plan.get(i, []):
                if od["op"] == "开":
                    if pos is not None and pos["dir"] == od["side"]:
                        pass                       # 同向不重复开（手动模式一次一仓）
                    else:
                        if pos is not None:
                            do_close(i, c, "手动反手", "手动")
                        do_open(i, c, od["side"], max(1, int(od["lots"] or 1)),
                                "手动复盘开仓", "手动")
                else:
                    if pos is not None:
                        do_close(i, c, "手动平仓", "手动")
            # 4) 加仓与移动止损（收盘）
            if pos:
                d = pos["dir"]
                if (c > pos["extreme"]) if d > 0 else (c < pos["extreme"]):
                    pos["extreme"] = c
                ns = self.HIST.trail_stop(pos, b, i)
                if ns is not None and pos.get("stop") is not None:
                    if (d > 0 and ns > pos["stop"]) or (d < 0 and ns < pos["stop"]):
                        pos["stop"] = round(ns, 6)
            # 5) 盯市
            fpnl = (c - pos["avg"]) * pos["dir"] * mult * pos["lots"] if pos else 0.0
            eq = cash + fpnl
            if eq > peak:
                peak = eq
            dd = (peak - eq) / peak * 100.0 if peak > 0 else 0.0
            mu = 0.0
            if pos:
                mu = _margin_of(spec, c, pos["lots"], settings)
            curve.append([i, b["d"], round(eq, 2), round(cash, 2), c,
                          round(dd, 4), round(mu, 2),
                          pos["dir"] if pos else 0, pos["lots"] if pos else 0,
                          round(pos["avg"], 4) if pos else None,
                          round(fpnl, 2),
                          round((eq - self.cash0) / self.cash0 * 100.0, 2) if self.cash0 else 0.0])

        self.records = recs
        self.equity = curve
        self.pos = pos
        self._peak = peak
        self._fpnl = fpnl
        self._fee = fee_total
        self._closes = closes
        self._wins = wins
        self._gross_win = gross_win
        self._gross_loss = gross_loss

    # ---------- 视图 ----------
    def window(self):
        return self.bars[self.start:self.idx + 1]

    def view(self, tail=260):
        w = self.window()
        show = w[-tail:] if len(w) > tail else w
        off = self.start + max(0, len(w) - len(show))
        sigs = [s for s in self.signals
                if self.start <= s["i"] <= self.idx and s["i"] >= off]
        marks = [{"i": s["i"] - off, "d": s["d"], "side": s["side"],
                  "action": s["action"], "price": s["price"],
                  "reason": s["reason"]} for s in sigs]
        recs = [dict(r, i=r["i"] - off) for r in self.records if r["i"] >= off]
        eqc = [list(e) for e in self.equity if e[0] >= off]
        for e in eqc:
            e[0] = e[0] - off
        return {
            "ok": True, "session": self.sid, "code": self.code,
            "name": self.name, "period": self.period,
            "idx": self.idx, "start": self.start, "end": self.end,
            "offset": off, "bars": show, "marks": marks, "records": recs,
            "cur": self.bars[self.idx],
            "pos": self.pos, "stat": self.stat(), "equity": eqc,
            "auto": self.auto, "cash0": self.cash0,
            "mmText": self.mmText, "mm": MM.clean_for_save(self.mm),
            "progress": round((self.idx - self.start) /
                              max(1, self.end - self.start) * 100, 1),
        }

    # ---------- 控制 ----------
    def step(self, n=1):
        self.idx = max(self.start, min(self.end, self.idx + int(n)))
        self._rebuild()
        return self.view()

    def seek(self, i):
        self.idx = max(self.start, min(self.end, int(i)))
        self._rebuild()
        return self.view()

    def toggle_auto(self, on=None):
        self.auto = (not self.auto) if on is None else bool(on)
        self._rebuild()
        return self.view()

    def trade(self, side, lots=1):
        """在当前根收盘价记录一笔动作（开或平）。"""
        b = self.bars[self.idx]
        lots = max(1, int(lots or 1))
        d = 1 if side == "多" else -1
        if self.pos is None:
            self.orders.append({"i": self.idx, "op": "开", "side": d,
                                "lots": lots,
                                "ts": datetime.now().strftime("%H:%M:%S")})
            self._rebuild()
            if self.pos is None:
                self.orders.pop()
                self._rebuild()
                return {"ok": False, "msg": getattr(self, "lastReject", "") or
                        "开仓被资金管理拒绝"}
            return {"ok": True, "msg": "%s %d 手开仓 @ %g" % (side, lots, b["c"])}
        self.orders.append({"i": self.idx, "op": "平",
                            "ts": datetime.now().strftime("%H:%M:%S")})
        self._rebuild()
        last = self.records[-1] if self.records else {}
        self.pos = None
        return {"ok": True, "msg": "平仓 @ %g，净盈亏 %s" % (
            b["c"], last.get("净盈亏"))}

    def flat(self):
        if self.pos is not None:
            self.trade("多" if self.pos["dir"] < 0 else "空", self.pos["lots"])
        return self.view()

    def clear_orders(self):
        self.orders = []
        self._rebuild()
        return self.view()

    def stat(self):
        done = [r for r in self.records if r.get("净盈亏") is not None]
        wins = [d for d in done if d["净盈亏"] > 0]
        gw = sum(d["净盈亏"] for d in done if d["净盈亏"] > 0)
        gl = abs(sum(d["净盈亏"] for d in done if d["净盈亏"] <= 0))
        rs = [d["盈亏R"] for d in done if d.get("盈亏R") is not None]
        eq = self.equity[-1][2] if self.equity else self.cash0
        peak = max([e[2] for e in self.equity] or [self.cash0])
        dd = (peak - eq) / peak * 100.0 if peak > 0 else 0.0
        return {
            "记录笔数": len(self.records),
            "已平仓": len(done), "盈利": len(wins),
            "胜率": round(len(wins) / len(done) * 100, 2) if done else 0.0,
            "净盈亏": round(sum(d["净盈亏"] for d in done), 2),
            "总手续费": round(self._fee, 2),
            "盈亏比": round(gw / gl, 2) if gl > 0 else None,
            "累计R": round(sum(rs), 2) if rs else 0.0,
            "浮动盈亏": round(self._fpnl, 2),
            "初始资金": round(self.cash0, 2),
            "当前权益": round(eq, 2),
            "收益率": round((eq - self.cash0) / self.cash0 * 100, 2) if self.cash0 else 0.0,
            "最大回撤": round(max([e[5] for e in self.equity] or [0]), 2),
            "当前回撤": round(dd, 2),
            "平均持仓根数": round(
                sum(d["持仓根数"] for d in done if d.get("持仓根数")) /
                max(1, len([d for d in done if d.get("持仓根数")])), 1),
            "持仓": self.pos,
        }

    def brief(self):
        return {"id": self.sid, "code": self.code, "name": self.name,
                "period": self.period, "createdAt": self.createdAt,
                "start": self.start, "end": self.end, "idx": self.idx,
                "auto": self.auto, "cash0": self.cash0,
                "progress": round((self.idx - self.start) /
                                  max(1, self.end - self.start) * 100, 1),
                "stat": self.stat()}

    # ---------- 导出 ----------
    def export(self, fmt="csv"):
        """返回 (文件名, 文本, mime)。fmt: csv | tsv | json | jsonl"""
        rows = [dict(r) for r in self.records]
        fmt = (fmt or "csv").lower()
        base = "复盘_%s_%s_%s" % (self.code, self.period,
                                 datetime.now().strftime("%Y%m%d_%H%M%S"))
        meta = {
            "合约": self.code, "名称": self.name, "周期": self.period,
            "区间": "%s ~ %s" % (self.bars[self.start]["d"],
                                 self.bars[self.idx]["d"]),
            "初始资金": self.cash0, "资金管理": self.mmText,
            "自动跟随策略": "是" if self.auto else "否",
            "策略": self.kind or "无", "统计": self.stat(),
        }
        if fmt == "json":
            import json as _json
            return (base + ".json",
                    _json.dumps({"meta": meta, "records": rows,
                                 "equity": self.equity},
                                ensure_ascii=False, indent=1),
                    "application/json;charset=utf-8")
        if fmt == "jsonl":
            import json as _json
            lines = [_json.dumps(meta, ensure_ascii=False)]
            lines += [_json.dumps(r, ensure_ascii=False) for r in rows]
            return (base + ".jsonl", "\n".join(lines),
                    "application/x-ndjson;charset=utf-8")
        sep = "\t" if fmt == "tsv" else ","
        keys = [k for k in REC_KEYS if k in (rows[0] if rows else {})] or REC_KEYS

        def cell(v):
            if v is None:
                return ""
            s = str(v)
            if fmt == "csv" and (any(ch in s for ch in ',"\n')):
                s = '"' + s.replace('"', '""') + '"'
            if fmt == "tsv":
                s = s.replace("\t", " ").replace("\n", " ")
            return s

        head = ["# " + k + "=" + str(v) for k, v in meta.items()
                if k != "统计"]
        for k, v in (meta.get("统计") or {}).items():
            if k == "持仓":
                continue
            head.append("# 统计.%s=%s" % (k, v))
        lines = head + [sep.join(keys)]
        lines += [sep.join(cell(r.get(k)) for k in keys) for r in rows]
        return (base + (".tsv" if fmt == "tsv" else ".csv"),
                "\n".join(lines),
                "text/tab-separated-values;charset=utf-8" if fmt == "tsv"
                else "text/csv;charset=utf-8")


def _rec(seq, i, bar, action, side, lots, price, mult, fee, pnl, hold,
         reason, source, cash, peak, cash0=100000.0, stop=None, risk=0.0,
         pos=None):
    eq = cash + 0.0
    return {
        "序号": seq, "i": i, "d": bar["d"], "时间": bar["d"],
        "动作": action, "方向": side, "手数": lots, "价格": round(price, 4),
        "成交额": round(price * mult * lots, 2), "手续费": round(fee, 2),
        "净盈亏": pnl, "持仓根数": hold,
        "权益": round(eq, 2),
        "收益率%": round((eq - cash0) / cash0 * 100.0, 2) if cash0 else 0.0,
        "回撤%": round((peak - eq) / peak * 100.0, 2) if peak > 0 else 0.0,
        "可用资金": round(cash, 2),
        "保证金": 0.0, "持仓方向": (side if action == "开" else ""),
        "持仓手数": lots, "持仓均价": round(price, 4),
        "止损价": stop, "风险额": round(risk, 2),
        "盈亏R": (round(pnl / risk, 2) if (pnl is not None and risk > 0) else None),
        "加仓": pos.get("adds", 0) if pos else 0,
        "来源": source, "说明": reason,
    }


class ReplayManager(object):
    def __init__(self, max_keep=8):
        self.lock = threading.RLock()
        self.sessions = {}
        self.order = []
        self.max_keep = max_keep

    def open(self, code, name, period, bars, spec, start, end, kind="",
             params=None, settings=None, mm=None, cash=100000.0, auto=False,
             tp_pct=0.0):
        sid = "rp_" + uuid.uuid4().hex[:8]
        s = ReplaySession(sid, code, name, period, bars, spec, start, end,
                          kind, params, settings, mm, cash, auto, tp_pct)
        with self.lock:
            self.sessions[sid] = s
            self.order.append(sid)
            while len(self.order) > self.max_keep:
                old = self.order.pop(0)
                self.sessions.pop(old, None)
        return s

    def get(self, sid):
        with self.lock:
            return self.sessions.get(sid)

    def close(self, sid):
        with self.lock:
            s = self.sessions.pop(sid, None)
            if sid in self.order:
                self.order.remove(sid)
        return s

    def list(self):
        with self.lock:
            return [self.sessions[k].brief() for k in self.order
                    if k in self.sessions]
