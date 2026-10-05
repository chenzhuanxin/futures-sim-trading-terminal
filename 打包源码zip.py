# -*- coding: utf-8 -*-
"""打包源码 zip —— 生成发布用源码包，只收录 git 跟踪的文件（版控内容）。

为什么只收 git 跟踪文件：
  仓库里同时存在运行期数据（账户数据/、行情缓存/）、打包产物（*.exe）、
  截图原图等由 .gitignore 排除的内容。git ls-files 天然给出"该发布的源码清单"，
  用它打包可保证源码包与仓库内容严格一致，不会夹带本机私有数据。

用法：
  python 打包源码zip.py                       # 输出 futures-sim-trading-terminal-v1.0.0-source.zip
  python 打包源码zip.py --version v1.0.1      # 指定版本
  python 打包源码zip.py --out some/path.zip   # 指定输出路径
  python 打包源码zip.py --check               # 仅校验，不写盘

要点：
  - 中文文件名写入 zip 时置 UTF-8 标志位（bit 11），Windows 资源管理器 / 7-Zip /
    macOS 归档工具均可正确显示，不会乱码。
  - 校验三件事：条目数、关键文件存在、无私有数据夹带；并做 CRC 全量自检。
"""

import argparse
import hashlib
import os
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))

# 源码包解压后应存在的关键文件（缺失即视为打包失败）
MUST_HAVE = [
    "README.md",
    "LICENSE",
    "模拟交易服务.py",
    "行情K线.py",
    "账户引擎.py",
    "策略引擎.py",
    "回测引擎.py",
    "资金管理.py",
    "交易面板.html",
    "品种参数.json",
    "docs/使用说明.md",
    "docs/数据源说明.md",
    "docs/配置说明.md",
]

# 绝不允许出现在源码包里的路径片段（私有数据 / 构建产物）
FORBIDDEN = [
    "账户数据/",
    "行情缓存/",
    "__pycache__/",
    ".git/",
    ".exe",
    ".log",
    "_账户数据备份",
]


def tracked_files():
    """取 git 跟踪文件列表（相对路径，正斜杠）。"""
    try:
        r = subprocess.run(
            ["git", "-c", "core.quotepath=off", "ls-files", "-z"],
            cwd=ROOT, capture_output=True, check=True,
        )
    except (OSError, subprocess.CalledProcessError) as e:
        print("[!] 需要 git 且当前目录是仓库工作区：%s" % e)
        sys.exit(1)
    raw = r.stdout.decode("utf-8", "surrogateescape")
    return [p for p in raw.split("\0") if p]


def build_zip(files, out_path, prefix):
    """把 files 写进 zip，返回 (条目数, 解压后总字节)。"""
    total = 0
    out_path = os.path.abspath(out_path)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for rel in files:
            full = os.path.join(ROOT, rel.replace("/", os.sep))
            if not os.path.isfile(full):
                print("[!] 跟踪文件不存在，跳过：%s" % rel)
                continue
            arc = prefix + rel
            # zipfile 对非 ASCII 名会自动置 UTF-8 标志位（flag_bits |= 0x800）
            z.write(full, arc)
        # 顶层目录占位项，保证空目录结构也能体现
        z.writestr(prefix.rstrip("/") + "/", "")
    return len(files), total


def verify(zip_path, expect_count):
    """校验 zip：条目数、关键文件、无私有数据、CRC 全量自检。"""
    problems = []
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        inner = [n for n in names if not n.endswith("/")]
        if len(inner) != expect_count:
            problems.append("条目数 %d != 期望 %d" % (len(inner), expect_count))

        for must in MUST_HAVE:
            if not any(n.endswith("/" + must) for n in inner):
                problems.append("缺少关键文件：%s" % must)

        for n in inner:
            for bad in FORBIDDEN:
                if bad in n:
                    problems.append("夹带了不该有的内容：%s" % n)

        bad_crc = z.testzip()
        if bad_crc is not None:
            problems.append("CRC 校验失败：%s" % bad_crc)

        # 中文名 UTF-8 标志位抽查
        for info in z.infolist():
            if any(ord(c) > 127 for c in info.filename):
                if not (info.flag_bits & 0x800):
                    problems.append("中文名未置 UTF-8 标志：%s" % info.filename)
                break
    return problems


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser(description="打包发布用源码 zip（只收 git 跟踪文件）")
    ap.add_argument("--version", default="v1.0.0", help="版本号，默认 v1.0.0")
    ap.add_argument("--out", default=None, help="输出路径，默认项目根目录")
    ap.add_argument("--check", action="store_true", help="仅校验，不写盘")
    args = ap.parse_args()

    ver_num = args.version.lstrip("v")
    default_name = "futures-sim-trading-terminal-%s-source.zip" % args.version
    out_path = args.out or os.path.join(ROOT, default_name)
    prefix = "futures-sim-trading-terminal-%s/" % ver_num

    files = tracked_files()
    print("[*] 仓库跟踪文件：%d 个" % len(files))
    print("[*] 解压前缀：%s" % prefix)
    print("[*] 输出：%s" % out_path)

    if args.check:
        if not os.path.isfile(out_path):
            print("[!] 指定路径不存在，无法校验：%s" % out_path)
            sys.exit(1)
        problems = verify(out_path, len(files))
    else:
        n, _ = build_zip(files, out_path, prefix)
        print("[*] 已写入 %d 个条目" % n)
        problems = verify(out_path, len(files))

    size = os.path.getsize(out_path)
    digest = sha256_of(out_path)

    print("-" * 62)
    print("文件：%s" % os.path.basename(out_path))
    print("大小：%s 字节 (%.2f MB)" % (size, size / 1048576))
    print("SHA256：%s" % digest)

    if problems:
        print("[X] 校验未通过：")
        for p in problems:
            print("    - %s" % p)
        sys.exit(2)
    print("[OK] 校验通过：条目数、关键文件、无私有数据夹带、CRC 全量自检")


if __name__ == "__main__":
    main()
