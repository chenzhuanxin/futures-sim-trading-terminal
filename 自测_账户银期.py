# -*- coding: utf-8 -*-
"""
自测：模拟账户（登录/新建/删除）+ 模拟银行卡 + 银期转账
依赖服务已在 127.0.0.1:8908 运行（模拟交易服务.py）。
测试账户以 t_ 前缀命名并会在结束时删除；sim001 的测试资金在首尾各做一次归位，
因此本脚本可重复运行。
"""
import json
import re
import urllib.request

BASE = "http://127.0.0.1:8908"
DEF_PWD = "123456"

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
PASS, FAIL = 0, 0


def chk(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [通过] %s %s" % (name, extra))
    else:
        FAIL += 1
        print("  [失败] %s %s" % (name, extra))


def get(path):
    with OPENER.open(BASE + path, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


def post(path, body):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        with OPENER.open(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:                                  # noqa: BLE001
        return {"ok": False, "msg": str(e)}


def bank_of(info, name):
    return next((b for b in info.get("banks", []) if b["bank"] == name), None)


def sim_cash():
    return (get("/api/state").get("account") or {}).get("cash") or 0.0


def cleanup_sim001():
    """把 sim001 归位：可用资金全部转回工商卡，删掉非工商银行的测试卡。"""
    post("/api/acc/login", {"loginName": "sim001", "loginPwd": DEF_PWD})
    info = get("/api/acc/info")
    icbc = bank_of(info, "工商银行")
    if icbc:
        cash = sim_cash()
        if cash > 0.01:
            post("/api/acc/transfer", {"direction": "转出", "bankId": icbc["id"],
                                       "amount": round(cash, 2), "bankPwd": DEF_PWD,
                                       "tradePwd": DEF_PWD})
    info = get("/api/acc/info")
    for b in info.get("banks", []):
        if b["bank"] != "工商银行":
            post("/api/bank/delete", {"id": b["id"], "cardPwd": DEF_PWD})


def main():
    print("== 账户 / 银期转账 自测 ==")

    print("\n== 0. 预清理（保证可重复运行）==")
    cleanup_sim001()
    chk("sim001 已归位：结存 0、仅 1 张工商卡",
        sim_cash() == 0 and len(get("/api/acc/info").get("banks", [])) == 1)
    # 清掉上次运行可能残留的 t_ 测试账户
    info = get("/api/acc/info")
    for a in info.get("accounts", []):
        if a["loginName"].startswith("t_"):
            post("/api/acc/delete", {"id": a["id"], "loginPwd": "abc123"})
    post("/api/acc/login", {"loginName": "sim001", "loginPwd": DEF_PWD})

    print("\n== A. 初始状态 ==")
    info = get("/api/acc/info")
    chk("账户接口可用", info.get("ok") is True)
    chk("存在默认账户 sim001",
        any(a["loginName"] == "sim001" for a in info.get("accounts", [])))
    chk("默认账户已登录", (info.get("current") or {}).get("loginName") == "sim001")
    chk("默认账户有 1 张银行卡", (info.get("current") or {}).get("banks") == 1)
    chk("卡号已掩码", all("******" in b["cardNo"] for b in info.get("banks", [])))
    chk("接口不回传任何密码字段",
        not any(k for k in json.dumps(info) if "pwd" in k.lower() or "password" in k.lower()
                or "hash" in k.lower()))

    print("\n== B. 登录校验 ==")
    r = post("/api/acc/login", {"loginName": "sim001", "loginPwd": "wrong!"})
    chk("错误密码被拒绝", r.get("ok") is False, r.get("msg", ""))
    r = post("/api/acc/login", {"loginName": "no_such_user", "loginPwd": DEF_PWD})
    chk("不存在账号被拒绝", r.get("ok") is False, r.get("msg", ""))
    r = post("/api/acc/login", {"loginName": "sim001", "loginPwd": DEF_PWD})
    chk("正确密码登录成功", r.get("ok") is True, r.get("msg", ""))

    print("\n== C. 新建账户 ==")
    r = post("/api/acc/add", {"loginName": "t", "loginPwd": DEF_PWD})
    chk("过短账号被拒绝", r.get("ok") is False and "500" not in r.get("msg", ""),
        r.get("msg", ""))
    r = post("/api/acc/add", {"loginName": "t_acc1", "loginPwd": "12345"})
    chk("过短密码被拒绝", r.get("ok") is False and "500" not in r.get("msg", ""),
        r.get("msg", ""))
    r = post("/api/acc/add", {"loginName": "t_acc1", "loginPwd": "abc123",
                              "tradePwd": "xyz789",
                              "firstBank": {"bank": "招商银行", "cardNo": "62258801337744",
                                            "cardPwd": "bank66", "balance": 200000}})
    chk("新建账户成功并自动登录", r.get("ok") is True, r.get("msg", ""))
    info = get("/api/acc/info")
    cur = info.get("current") or {}
    chk("分配了 8 开头 10 位交易账户", bool(re.fullmatch(r"8\d{9}", cur.get("tradeAcc") or "")),
        cur.get("tradeAcc", ""))
    chk("当前账户已切到 t_acc1", cur.get("loginName") == "t_acc1")
    chk("新账户自动绑了首卡且余额 20 万",
        len(info.get("banks", [])) == 1 and info["banks"][0].get("balance") == 200000)
    r = post("/api/acc/add", {"loginName": "t_acc1", "loginPwd": "abc123"})
    chk("重名账户被拒绝", r.get("ok") is False and "500" not in r.get("msg", ""),
        r.get("msg", ""))

    print("\n== D. 账户状态隔离 ==")
    r = post("/api/acc/login", {"loginName": "sim001", "loginPwd": DEF_PWD})
    chk("切回 sim001", r.get("ok") is True)
    r = post("/api/acc/transfer", {"direction": "转入",
                                   "bankId": bank_of(get("/api/acc/info"), "工商银行")["id"],
                                   "amount": 100000, "bankPwd": DEF_PWD})
    chk("sim001 银期转入 10 万作隔离标记", r.get("ok") is True, r.get("msg", ""))
    sim_equity = sim_cash()
    chk("sim001 结存 = 10 万", sim_equity == 100000, "cash=%s" % sim_equity)
    r = post("/api/acc/login", {"loginName": "t_acc1", "loginPwd": "abc123"})
    chk("切到 t_acc1", r.get("ok") is True)
    chk("t_acc1 结存为 0（与 sim001 隔离）", sim_cash() == 0, "cash=%s" % sim_cash())
    r = post("/api/acc/login", {"loginName": "sim001", "loginPwd": DEF_PWD})
    chk("切回 sim001", r.get("ok") is True)
    chk("sim001 结存已载回（10 万）", sim_cash() == 100000, "cash=%s" % sim_cash())

    print("\n== E. 银行卡管理 ==")
    r = post("/api/bank/add", {"bank": "建设银行", "cardNo": "6227000000111222",
                               "cardPwd": DEF_PWD, "balance": 300000})
    chk("添加银行卡成功", r.get("ok") is True, r.get("msg", ""))
    info = get("/api/acc/info")
    ccb = bank_of(info, "建设银行")
    r = post("/api/bank/add", {"bank": "建设银行", "cardNo": "6227000000111222",
                               "cardPwd": DEF_PWD, "balance": 100000})
    chk("同账户重复卡号被拒绝", r.get("ok") is False, r.get("msg", ""))
    r = post("/api/bank/add", {"bank": "不存在的银行", "cardNo": "62220000001112",
                               "cardPwd": DEF_PWD, "balance": 100000})
    chk("非法银行被拒绝", r.get("ok") is False, r.get("msg", ""))
    r = post("/api/bank/add", {"bank": "建设银行", "cardNo": "622700123",
                               "cardPwd": DEF_PWD, "balance": 100000})
    chk("过短卡号被拒绝", r.get("ok") is False, r.get("msg", ""))
    r = post("/api/bank/delete", {"id": ccb["id"], "cardPwd": "999999"})
    chk("卡密码错误不能解绑", r.get("ok") is False, r.get("msg", ""))
    r = post("/api/bank/delete", {"id": ccb["id"], "cardPwd": DEF_PWD})
    chk("解绑成功（余额随卡移出，提示明确）", r.get("ok") is True and "300000" in r.get("msg", ""),
        r.get("msg", ""))
    r = post("/api/bank/add", {"bank": "建设银行", "cardNo": "6227000000111222",
                               "cardPwd": DEF_PWD, "balance": 300000})
    chk("重新加回建行卡供转账测试", r.get("ok") is True, r.get("msg", ""))

    print("\n== F. 银期转账（转入）==")
    info = get("/api/acc/info")
    icbc, ccb = bank_of(info, "工商银行"), bank_of(info, "建设银行")
    r = post("/api/acc/transfer", {"direction": "转入", "bankId": icbc["id"],
                                   "amount": 50000, "bankPwd": "wrong"})
    chk("银行密码错误拒绝转入", r.get("ok") is False, r.get("msg", ""))
    r = post("/api/acc/transfer", {"direction": "转入", "bankId": icbc["id"],
                                   "amount": 5000000, "bankPwd": DEF_PWD})
    chk("卡余额不足拒绝转入", r.get("ok") is False, r.get("msg", ""))
    r = post("/api/acc/transfer", {"direction": "转入", "bankId": icbc["id"],
                                   "amount": 50000, "bankPwd": DEF_PWD})
    chk("银期转入成功", r.get("ok") is True, r.get("msg", ""))
    chk("期货账户结存 10万+5万", sim_cash() == 150000, "cash=%s" % sim_cash())
    b = bank_of(get("/api/acc/info"), "工商银行")
    chk("工商卡余额 100万-15万", b.get("balance") == 850000, "余额=%s" % b.get("balance"))
    cf = (get("/api/state").get("cashFlow") or [])[0]
    chk("资金流水记了银期转入", "银期转入" in (cf.get("note") or ""),
        "%s %s" % (cf.get("type"), cf.get("note")))

    print("\n== G. 银期转账（转出）==")
    r = post("/api/acc/transfer", {"direction": "转出", "bankId": icbc["id"],
                                   "amount": 30000, "bankPwd": DEF_PWD})
    chk("缺资金密码拒绝转出", r.get("ok") is False, r.get("msg", ""))
    r = post("/api/acc/transfer", {"direction": "转出", "bankId": icbc["id"],
                                   "amount": 30000, "bankPwd": DEF_PWD, "tradePwd": "bad"})
    chk("资金密码错误拒绝转出", r.get("ok") is False, r.get("msg", ""))
    r = post("/api/acc/transfer", {"direction": "转出", "bankId": icbc["id"],
                                   "amount": 100000000, "bankPwd": DEF_PWD,
                                   "tradePwd": DEF_PWD})
    chk("超过可用资金拒绝转出", r.get("ok") is False, r.get("msg", ""))
    r = post("/api/acc/transfer", {"direction": "转出", "bankId": icbc["id"],
                                   "amount": 30000, "bankPwd": DEF_PWD,
                                   "tradePwd": DEF_PWD})
    chk("银期转出成功", r.get("ok") is True, r.get("msg", ""))
    chk("结存回到 12 万", sim_cash() == 120000, "cash=%s" % sim_cash())
    b = bank_of(get("/api/acc/info"), "工商银行")
    chk("工商卡余额 +3 万", b.get("balance") == 880000, "余额=%s" % b.get("balance"))
    info = get("/api/acc/info")
    flows = [f for x in info["banks"] for f in x.get("flow", [])]
    chk("银行卡留痕转账流水", any(f.get("type") == "银期转出" for f in flows)
        and any(f.get("type") == "银期转入" for f in flows), "共 %d 条" % len(flows))

    print("\n== H. 删除账户 ==")
    info = get("/api/acc/info")
    t_id = next((a["id"] for a in info["accounts"] if a["loginName"] == "t_acc1"), "")
    r = post("/api/acc/delete", {"id": t_id, "loginPwd": "wrong"})
    chk("密码错误不能删账户", r.get("ok") is False, r.get("msg", ""))
    r = post("/api/acc/delete", {"id": t_id, "loginPwd": "abc123"})
    chk("删除 t_acc1 成功", r.get("ok") is True, r.get("msg", ""))
    info = get("/api/acc/info")
    chk("删除后切回 sim001", (info.get("current") or {}).get("loginName") == "sim001")
    r = post("/api/acc/delete", {"id": info["current"]["id"], "loginPwd": DEF_PWD})
    chk("最后一个账户不可删除", r.get("ok") is False, r.get("msg", ""))

    print("\n== I. 收尾归位 ==")
    cleanup_sim001()
    info = get("/api/acc/info")
    chk("sim001 恢复：结存 0、仅 1 张工商卡（余额 100 万）",
        sim_cash() == 0 and len(info.get("banks", [])) == 1
        and info["banks"][0]["balance"] == 1000000)
    chk("t_ 测试账户已全部删除",
        not any(a["loginName"].startswith("t_") for a in info.get("accounts", [])))

    print()
    print("===== 结果: 通过 %d / 失败 %d =====" % (PASS, FAIL))
    raise SystemExit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
