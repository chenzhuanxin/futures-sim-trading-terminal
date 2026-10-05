# -*- coding: utf-8 -*-
"""
账户引擎 —— 模拟账户（登录账号 / 登录密码 / 交易账户 / 交易密码）与银期转账。

功能：
- 多账户管理：新建 / 登录切换 / 删除（最后一个账户不可删）；
- 每个账户有独立的资金状态文件（持仓、委托、成交、权益曲线互不干扰）；
- 模拟银行卡：可添加 / 删除（删除需银行交易密码），卡内余额独立；
- 银期转账：转入（验银行密码）、转出（验银行密码 + 资金密码，且校验可用资金）；
- 密码一律 sha256(salt + pwd) 落盘，任何接口不回传明文。

文件布局（账户数据/）：
- users.json                 账户注册表（含银行卡，current = 当前登录账户 id）
- account.json               第一个账户（默认账户）的资金状态（兼容旧版数据）
- acc_<uid>.json             其余账户的资金状态
"""
import json
import os
import random
import re
import hashlib
import shutil

import 路径工具 as PATHS

BASE_DIR = PATHS.APP_DIR                    # 可写数据基准目录（打包后 = exe 所在目录）
STATE_DIR = PATHS.data("账户数据")           # 账户数据落在 exe 旁，退出不丢
USERS_FILE = os.path.join(STATE_DIR, "users.json")

BANKS = [
    "工商银行", "农业银行", "中国银行", "建设银行", "交通银行", "招商银行",
    "邮储银行", "中信银行", "兴业银行", "民生银行", "光大银行", "浦发银行",
]


def _now():
    import time
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _hash(pwd, salt):
    return hashlib.sha256((salt + str(pwd)).encode("utf-8")).hexdigest()


def _rand_digits(n):
    return "".join(str(random.randint(0, 9)) for _ in range(n))


def _mask_card(no):
    s = str(no or "")
    if len(s) <= 10:
        return s[:2] + "****" + s[-2:] if len(s) > 4 else s
    return s[:6] + "******" + s[-4:]


def _mask_acc(no):
    s = str(no or "")
    return s[:3] + "****" + s[-3:] if len(s) > 6 else s


class AccountEngine(object):
    """模拟账户注册表。on_switch(uid, state_file) 由服务端注入，用于热切换资金状态。"""

    def __init__(self, on_switch=None):
        os.makedirs(STATE_DIR, exist_ok=True)
        self.lock = __import__("threading").RLock()
        self.on_switch = on_switch or (lambda uid, f: None)
        self.data = {"version": 1, "current": "", "seq": 0, "users": {}}
        self._load()
        if not self.data["users"]:
            self._bootstrap()
        self._migrate_legacy()
        cur = self.data.get("current") or ""
        f = self.state_file(cur)
        if cur and f:
            self.on_switch(cur, f)          # 启动即载入当前登录账户的资金状态

    def _migrate_legacy(self):
        """兼容更早版本：旧版把第一位账户的资金存在 account.json，搬到固定映射位置。"""
        legacy = os.path.join(STATE_DIR, "account.json")
        if not os.path.exists(legacy):
            return
        fid = self._first_id()
        f = self.state_file(fid) if fid else ""
        if f and not os.path.exists(f):
            try:
                os.replace(legacy, f)       # 整体沿用，账户与资金都不丢
                print("[账户] 已沿用旧版账户数据：account.json → %s" % os.path.basename(f))
            except OSError:
                try:
                    shutil.copyfile(legacy, f)
                except OSError:
                    pass

    # ---------------- 落盘 ----------------
    def _load(self):
        try:
            with open(USERS_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict) and isinstance(d.get("users"), dict):
                self.data = d
        except Exception:                                   # noqa: BLE001
            pass

    def _save(self):
        try:
            tmp = USERS_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, separators=(",", ":"))
            os.replace(tmp, USERS_FILE)
        except Exception as e:                              # noqa: BLE001
            print("[warn] 账户注册表落盘失败：%s" % e)

    def _next_id(self):
        self.data["seq"] = int(self.data.get("seq", 0)) + 1
        return "U%04d" % self.data["seq"]

    def _bootstrap(self):
        """首次运行：建默认账户 sim001（登录/资金密码 123456，银行卡密码 123456）。"""
        uid = self._next_id()
        state_file = os.path.join(STATE_DIR, "acc_%s.json" % uid)
        salt_l, salt_t, salt_b = _rand_digits(16), _rand_digits(16), _rand_digits(16)
        self.data["users"][uid] = {
            "id": uid, "loginName": "sim001",
            "loginSalt": salt_l, "loginHash": _hash("123456", salt_l),
            "tradeAcc": "8" + _rand_digits(9),
            "tradeSalt": salt_t, "tradeHash": _hash("123456", salt_t),
            "createdAt": _now(), "banks": [],
        }
        self.data["users"][uid]["banks"].append({
            "id": "B%04d" % (int(self.data["seq"]) * 100 + 1),
            "bank": "工商银行", "cardNo": "6222" + _rand_digits(12),
            "cardSalt": salt_b, "cardHash": _hash("123456", salt_b),
            "balance": 1000000.0, "createdAt": _now(), "flow": [],
        })
        self.data["current"] = uid
        self._save()
        self.on_switch(uid, state_file)
        print("[账户] 已初始化默认账户 sim001（登录/资金密码 123456，银行卡密码 123456）")

    # ---------------- 查询 ----------------
    def get(self, uid=None):
        uid = uid or self.data.get("current") or ""
        return self.data["users"].get(uid)

    def state_file(self, uid):
        """账户资金状态文件：一律 acc_<uid>.json（固定映射，与账户顺序无关）。"""
        u = self.data["users"].get(uid)
        if not u:
            return ""
        return os.path.join(STATE_DIR, "acc_%s.json" % uid)

    def _first_id(self):
        ids = list(self.data["users"].keys())
        return ids[0] if ids else ""

    def user_public(self, u):
        return {
            "id": u["id"], "loginName": u["loginName"],
            "tradeAcc": u["tradeAcc"], "createdAt": u["createdAt"],
            "banks": len(u.get("banks") or []),
            "tradeAccMasked": _mask_acc(u["tradeAcc"]),
        }

    def info(self):
        """当前账户 + 全部账户列表（掩码）+ 当前账户银行卡列表。"""
        with self.lock:
            cur = self.get()
            accs = [self.user_public(u) for u in self.data["users"].values()]
            accs.sort(key=lambda a: a["id"])
            banks = self.banks_public(cur) if cur else []
            return {
                "ok": True,
                "current": self.user_public(cur) if cur else None,
                "accounts": accs,
                "banks": banks,
            }

    def banks_public(self, u):
        out = []
        for b in u.get("banks") or []:
            out.append({
                "id": b["id"], "bank": b["bank"], "cardNo": _mask_card(b["cardNo"]),
                "balance": round(float(b.get("balance") or 0), 2),
                "createdAt": b.get("createdAt") or "",
                "flow": (b.get("flow") or [])[:60],
            })
        return out

    # ---------------- 登录 / 账户 CRUD ----------------
    def login(self, login_name, pwd):
        with self.lock:
            for uid, u in self.data["users"].items():
                if u["loginName"] == str(login_name or "").strip():
                    if _hash(pwd or "", u["loginSalt"]) != u["loginHash"]:
                        return False, "登录密码错误", None
                    prev = self.data.get("current") or ""
                    self.data["current"] = uid
                    self._save()
                    if prev != uid:
                        self.on_switch(uid, self.state_file(uid))
                    return True, "登录成功：%s" % u["loginName"], self.user_public(u)
            return False, "登录账号不存在", None

    def add_account(self, login_name, login_pwd, trade_pwd=None, trade_acc=None,
                    first_bank=None):
        """新建账户。first_bank = {bank, cardNo, cardPwd, balance} 可选。"""
        login_name = str(login_name or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_]{3,20}", login_name or ""):
            return False, "登录账号须为 3-20 位字母 / 数字 / 下划线", None
        if len(str(login_pwd or "")) < 6:
            return False, "登录密码至少 6 位", None
        trade_pwd = trade_pwd if trade_pwd else login_pwd
        if len(str(trade_pwd or "")) < 6:
            return False, "资金密码（交易密码）至少 6 位", None
        with self.lock:
            if any(u["loginName"] == login_name for u in self.data["users"].values()):
                return False, "登录账号已存在：%s" % login_name, None
            uid = self._next_id()
            salt_l, salt_t = _rand_digits(16), _rand_digits(16)
            u = {
                "id": uid, "loginName": login_name,
                "loginSalt": salt_l, "loginHash": _hash(login_pwd, salt_l),
                "tradeAcc": str(trade_acc or "").strip() or ("8" + _rand_digits(9)),
                "tradeSalt": salt_t, "tradeHash": _hash(trade_pwd, salt_t),
                "createdAt": _now(), "banks": [],
            }
            if first_bank:
                ok, msg, bank = self._make_bank(first_bank)
                if not ok:
                    return False, msg, None
                u["banks"].append(bank)
            self.data["users"][uid] = u
            prev = self.data.get("current") or ""
            self.data["current"] = uid
            self._save()
            self.on_switch(uid, self.state_file(uid))
            msg = "账户已创建并登录：%s（交易账户 %s）" % (login_name, u["tradeAcc"])
            if first_bank:
                msg += "，已绑定 %s %s" % (first_bank["bank"], _mask_card(first_bank["cardNo"]))
            return True, msg, self.user_public(u)

    def delete_account(self, uid, login_pwd):
        with self.lock:
            u = self.data["users"].get(uid or "")
            if not u:
                return False, "账户不存在"
            if _hash(login_pwd or "", u["loginSalt"]) != u["loginHash"]:
                return False, "登录密码错误，无法删除账户"
            if len(self.data["users"]) <= 1:
                return False, "至少保留一个账户，不能删除"
            state_file = self.state_file(uid)
            del self.data["users"][uid]
            msg = "账户已删除：%s" % u["loginName"]
            if self.data.get("current") == uid:
                nxt = self._first_id()
                self.data["current"] = nxt
                self.on_switch(nxt, self.state_file(nxt))
                msg += "，已切换到 %s" % self.data["users"][nxt]["loginName"]
            self._save()
            try:
                if os.path.exists(state_file):
                    os.remove(state_file)
                    msg += "（资金状态文件已清除）"
            except Exception as e:                          # noqa: BLE001
                msg += "（资金状态文件删除失败：%s）" % e
            return True, msg

    # ---------------- 银行卡 ----------------
    @staticmethod
    def _make_bank(fb):
        bank = str(fb.get("bank") or "").strip()
        card = re.sub(r"\s+", "", str(fb.get("cardNo") or ""))
        pwd = str(fb.get("cardPwd") or "")
        try:
            balance = round(float(fb.get("balance") or 0), 2)
        except (TypeError, ValueError):
            return False, "银行卡余额格式错误", None
        if bank not in BANKS:
            return False, "请选择支持的银行（%s 等）" % "/".join(BANKS[:6]), None
        if not re.fullmatch(r"\d{12,19}", card or ""):
            return False, "银行卡号须为 12-19 位数字", None
        if len(pwd) < 6:
            return False, "银行交易密码至少 6 位", None
        salt = _rand_digits(16)
        return True, "", {
            "id": "B" + _rand_digits(8),
            "bank": bank, "cardNo": card,
            "cardSalt": salt, "cardHash": _hash(pwd, salt),
            "balance": balance, "createdAt": _now(), "flow": [],
        }

    def add_bank(self, fb):
        with self.lock:
            u = self.get()
            if not u:
                return False, "未登录"
            ok, msg, bank = self._make_bank(fb)
            if not ok:
                return False, msg
            if any(b["cardNo"] == bank["cardNo"] for b in u["banks"]):
                return False, "该卡号已绑定"
            u["banks"].append(bank)
            self._save()
            return True, "银行卡已绑定：%s %s（余额 ¥%.2f）" % (
                bank["bank"], _mask_card(bank["cardNo"]), bank["balance"])

    def delete_bank(self, bank_id, card_pwd):
        with self.lock:
            u = self.get()
            if not u:
                return False, "未登录"
            for i, b in enumerate(u["banks"]):
                if b["id"] == bank_id:
                    if _hash(card_pwd or "", b["cardSalt"]) != b["cardHash"]:
                        return False, "银行交易密码错误"
                    bal = float(b.get("balance") or 0)
                    del u["banks"][i]
                    self._save()
                    if bal > 0.005:
                        return True, ("银行卡已解绑：%s %s。卡内余额 ¥%.2f 随卡移出模拟系统"
                                      "（真实银期中该余额仍在您的银行账户里）；"
                                      "期货账户资金不受影响。"
                                      % (b["bank"], _mask_card(b["cardNo"]), bal))
                    return True, "银行卡已解绑：%s %s" % (b["bank"], _mask_card(b["cardNo"]))
            return False, "银行卡不存在"

    # ---------------- 银期转账 ----------------
    def transfer(self, direction, bank_id, amount, bank_pwd, trade_pwd=None,
                 cash_cb=None):
        """direction: 转入 / 转出。cash_cb(方向, 金额, 备注) -> (ok, msg) 由资金状态执行。"""
        try:
            amount = round(float(amount), 2)
        except (TypeError, ValueError):
            return False, "金额格式错误"
        if amount <= 0:
            return False, "转账金额必须大于 0"
        if amount > 100000000:
            return False, "单笔转账不能超过 1 亿"
        with self.lock:
            u = self.get()
            if not u:
                return False, "未登录"
            bank = next((b for b in u["banks"] if b["id"] == bank_id), None)
            if not bank:
                return False, "请选择银行卡"
            if _hash(bank_pwd or "", bank["cardSalt"]) != bank["cardHash"]:
                return False, "银行交易密码错误"
            if direction == "转入":
                if amount > float(bank.get("balance") or 0) + 1e-6:
                    return False, "卡内余额不足：卡余额 ¥%.2f，申请转入 ¥%.2f" % (
                        float(bank.get("balance") or 0), amount)
                if cash_cb:
                    ok, msg = cash_cb("转入", amount, "银期转入 %s %s" % (
                        bank["bank"], _mask_card(bank["cardNo"])))
                    if not ok:
                        return False, "期货账户入金失败：%s" % msg
                bank["balance"] = round(float(bank["balance"]) - amount, 2)
                bank["flow"].insert(0, {"ts": _now(), "type": "银期转入", "amount": -amount,
                                        "balanceAfter": bank["balance"]})
                del bank["flow"][60:]
                self._save()
                return True, "银期转入成功：¥%.2f 已入金，卡内余额 ¥%.2f" % (
                    amount, bank["balance"])
            if direction == "转出":
                if _hash(trade_pwd or "", u["tradeSalt"]) != u["tradeHash"]:
                    return False, "资金密码（交易密码）错误"
                if cash_cb:
                    ok, msg = cash_cb("转出", amount, "银期转出 %s %s" % (
                        bank["bank"], _mask_card(bank["cardNo"])))
                    if not ok:
                        return False, msg          # 通常是可用资金不足
                bank["balance"] = round(float(bank.get("balance") or 0) + amount, 2)
                bank["flow"].insert(0, {"ts": _now(), "type": "银期转出", "amount": amount,
                                        "balanceAfter": bank["balance"]})
                del bank["flow"][60:]
                self._save()
                return True, "银期转出成功：¥%.2f 已到卡，卡内余额 ¥%.2f" % (
                    amount, bank["balance"])
            return False, "方向须为 转入 / 转出"
