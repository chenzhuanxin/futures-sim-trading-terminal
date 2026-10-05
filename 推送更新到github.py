# -*- coding: utf-8 -*-
"""
通过 GitHub REST API 上传仓库（github.com 的 git 传输被断时的可靠通道）。

用法：C:/Python314/python.exe _上传到github.py

流程：
  1) Contents API 先传 README.md —— 激活空仓库的 main 分支（Git Data API 对空仓库会 409）
  2) 对其余文件：POST /git/blobs 建 blob → 建一次性 tree → 一次 commit → 更新 ref
     （避免 44 个文件产生 44 个 commit）
"""
import base64
import json
import subprocess
import sys
import time
import urllib.request

OWNER, REPO = "chenzhuanxin", "futures-sim-trading-terminal"
API = "https://api.github.com"

TOKEN = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True).stdout.strip()
if not TOKEN:
    sys.exit("取不到 gh token")


def api(method, path, payload=None, raw=False):
    """带重试的 API 调用（api.github.com 偶发抖动）。"""
    url = API + path if path.startswith("/") else path
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    last = None
    for i in range(4):
        try:
            req = urllib.request.Request(url, data=data, method=method, headers={
                "Authorization": "token " + TOKEN,
                "Accept": "application/vnd.github+json",
                "Content-Type": "application/json",
                "User-Agent": "upload-script",
            })
            with urllib.request.urlopen(req, timeout=60) as r:
                body = r.read()
            return (r.status, body if raw else (json.loads(body) if body else {}))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            # 422 = 已存在（可继续），其余 4xx 视为真错误直接抛
            if e.code < 500 and e.code not in (422,):
                raise RuntimeError("%s %s -> %d %s" % (method, path, e.code, body[:300]))
            last = RuntimeError("%d %s" % (e.code, body[:300]))
        except Exception as e:                                 # noqa: BLE001
            last = e
        time.sleep(1.5 * (i + 1))
    raise last


def git_files():
    out = subprocess.run(["git", "-c", "core.quotepath=off", "ls-files"],
                         capture_output=True, encoding="utf-8").stdout.splitlines()
    return [f.strip() for f in out if f.strip()]


# ---------- 1) 引导提交：README.md ----------
files = git_files()
files.remove("README.md")
print("共 %d 个待传文件（README.md 单独引导）" % len(files))

with open("README.md", "rb") as f:
    readme_b64 = base64.b64encode(f.read()).decode()
body = {"message": "init: 项目说明 README", "content": readme_b64}
try:
    st, r = api("PUT", "/repos/%s/%s/contents/README.md" % (OWNER, REPO), body)
    print("引导提交 README.md ->", st)
except RuntimeError as e:
    if "422" in str(e) and "sha" in str(e):
        # 文件已存在：取旧 sha 走更新
        st, old = api("GET", "/repos/%s/%s/contents/README.md" % (OWNER, REPO))
        body["sha"] = old["sha"]
        st, r = api("PUT", "/repos/%s/%s/contents/README.md" % (OWNER, REPO), body)
        print("引导提交 README.md（覆盖已有） ->", st)
    else:
        raise

st, ref = api("GET", "/repos/%s/%s/git/ref/heads/main" % (OWNER, REPO))
base_commit = ref["object"]["sha"]
st, base_c = api("GET", "/repos/%s/%s/git/commits/%s" % (OWNER, REPO, base_commit))
base_tree = base_c["tree"]["sha"]
print("基线 commit:", base_commit[:10], "tree:", base_tree[:10])

# ---------- 2) 批量建 blob ----------
tree_items, failed = [], []
for i, path in enumerate(files, 1):
    with open(path, "rb") as f:
        raw = f.read()
    # 文本文件走 utf-8（可读），二进制走 base64
    is_text = True
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError:
        is_text = False
    payload = {"content": raw.decode("utf-8") if is_text else base64.b64encode(raw).decode(),
               "encoding": "utf-8" if is_text else "base64"}
    try:
        st, b = api("POST", "/repos/%s/%s/git/blobs" % (OWNER, REPO), payload)
        tree_items.append({"path": path.replace("\\", "/"), "mode": "100644",
                           "type": "blob", "sha": b["sha"]})
        print("  [%2d/%d] blob %s (%.1f KB, %s)" %
              (i, len(files), path, len(raw) / 1024, "utf-8" if is_text else "base64"))
    except Exception as e:                                     # noqa: BLE001
        failed.append(path)
        print("  [%2d/%d] 失败 %s: %s" % (i, len(files), path, e))

if failed:
    sys.exit("有 %d 个文件建 blob 失败，中止：%s" % (len(failed), failed))

# ---------- 3) 一次 tree + 一次 commit + 更新 ref ----------
st, tree = api("POST", "/repos/%s/%s/git/trees" % (OWNER, REPO),
               {"base_tree": base_tree, "tree": tree_items})
st, new_c = api("POST", "/repos/%s/%s/git/commits" % (OWNER, REPO), {
    "message": "国内期货量化模拟交易终端 v1.0.0：行情+模拟交易+策略+回测+复盘+账户银期转账\n\n"
               "- 六大页签：行情·技术指标 / 模拟交易 / 策略选股·策略交易 / 策略回测 / 复盘 / 账户·银期转账\n"
               "- 纯 Python 标准库 + 单页 HTML，零第三方依赖\n"
               "- 交易规则/手续费/保证金/涨跌停/交易时段对齐东方财富期货官网公示口径（87 品种对账偏差 0.00%）\n"
               "- 资金管理与仓位管理（策略/回测/复盘共用）+ 4 套科学预设\n"
               "- 无未来函数回测；复盘 24 字段记录可折叠/复制/导出 + 资金变化曲线\n"
               "- 多模拟账户 + 银期转账（密码加盐哈希）\n"
               "- 自测 86+50+26 项全绿",
    "tree": tree["sha"],
    "parents": [base_commit],
})
st, _ = api("PATCH", "/repos/%s/%s/git/refs/heads/main" % (OWNER, REPO),
            {"sha": new_c["sha"], "force": False})
print("提交完成:", new_c["sha"][:10], "-> main")

# ---------- 4) 校验 ----------
st, remote_tree = api("GET", "/repos/%s/%s/git/trees/%s?recursive=1" % (OWNER, REPO, tree["sha"]))
blobs = [t for t in remote_tree["tree"] if t["type"] == "blob"]
print("远端文件数:", len(blobs) + 1, "（含引导的 README.md）")
missing = set(f.replace("\\\\", "/") for f in files) - set(t["path"] for t in blobs)
print("缺失:", missing if missing else "无")
