# -*- coding: utf-8 -*-
"""
一键打包：把「国内期货模拟交易终端」打成单文件 exe，双击即用。

用法：
    C:/Python314/python.exe 打包成exe.py

产物：
    期货模拟交易终端.exe        （就在本目录，双击即可运行）

打包后的行为：
    · exe 自带 Python 运行时，目标电脑无需装 Python、无需联网装依赖；
    · 只读资源（交易面板.html / vendor / 品种参数.json / 快照）已内嵌进 exe；
    · 可写数据（账户数据 / 行情缓存）落在 exe 所在目录，关掉程序不会丢；
    · 想改界面或品种参数又不想重新打包？在 exe 旁边放一份同名文件即可覆盖内嵌的那份。

依赖：PyInstaller（本机已装 6.22.3）、Pillow（用于现画图标）。
"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)

APP_NAME = "期货模拟交易终端"
ENTRY = "模拟交易服务.py"
ICON = "图标.ico"
ICON_PNG = "图标预览.png"
VERFILE = "_版本信息.txt"
SPEC = "_打包.spec"
EXE_NAME = APP_NAME + ".exe"

# 需要内嵌进 exe 的只读资源： (源路径, 打包后放在 exe 内的相对目录)
DATA_MAP = [
    ("交易面板.html", "."),
    ("品种参数.json", "."),
    ("vendor", "vendor"),
    ("快照", "快照"),
]

# 中文文件名的模块打包器容易漏收，显式声明
HIDDEN = ["路径工具", "行情K线", "策略引擎", "回测引擎", "资金管理", "账户引擎"]

# 明确用不到的大块头，排除掉给 exe 瘦身（注意：不能排 email / html，
# http.server 依赖它们）
EXCLUDES = ["tkinter", "unittest", "pydoc", "doctest", "lib2to3", "test", "distutils"]

VERSION = "1.0.0.0"


# ----------------------------------------------------------------------
# 一、现画一个图标（深蓝圆角底 + K 线柱 + 白色上升折线）
# ----------------------------------------------------------------------
def make_icon():
    from PIL import Image, ImageDraw

    S = 512
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))

    # 竖向渐变底（上深蓝 → 下墨黑）
    bg = Image.new("RGBA", (S, S))
    bd = ImageDraw.Draw(bg)
    top, bot = (37, 99, 235), (11, 18, 34)
    for y in range(S):
        t = y / (S - 1)
        bd.line([(0, y), (S, y)],
                fill=tuple(int(top[i] + (bot[i] - top[i]) * t) for i in range(3)) + (255,))

    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, S - 1, S - 1], radius=104, fill=255)
    img.paste(bg, (0, 0), mask)
    d = ImageDraw.Draw(img)

    # K 线柱：红涨绿跌（国内配色），底部对齐
    base_y = 452
    bars = [(150, 356, (240, 82, 88)), (256, 330, (22, 178, 158)), (362, 296, (240, 82, 88))]
    for cx, top_y, col in bars:
        d.rounded_rectangle([cx - 27, top_y, cx + 27, base_y], radius=11, fill=col + (255,))

    # 白色上升折线
    pts = [(78, 300), (176, 232), (252, 268), (346, 168), (446, 96)]
    d.line(pts, fill=(255, 255, 255, 255), width=19, joint="curve")
    for p in (pts[0], pts[-1]):
        d.ellipse([p[0] - 17, p[1] - 17, p[0] + 17, p[1] + 17], fill=(255, 255, 255, 255))

    img.save(ICON, format="ICO",
             sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    img.resize((256, 256), Image.LANCZOS).save(ICON_PNG)
    print("[1/5] 图标已生成：%s" % ICON)


# ----------------------------------------------------------------------
# 二、写 exe 的版本信息（文件属性里显示，纯 ASCII 内容最稳）
# ----------------------------------------------------------------------
def make_version():
    txt = """VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=(%s), prodvers=(%s),
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('080404b0', [
        StringStruct('CompanyName', '\\u672c\\u5730\\u6a21\\u62df\\u76d8'),
        StringStruct('FileDescription', '\\u56fd\\u5185\\u671f\\u8d27\\u6a21\\u62df\\u4ea4\\u6613\\u7ec8\\u7aef'),
        StringStruct('FileVersion', '%s'),
        StringStruct('InternalName', 'qhtrade'),
        StringStruct('LegalCopyright', '\\u4ec5\\u4f9b\\u5b66\\u4e60\\u7814\\u7a76\\uff0c\\u6a21\\u62df\\u76d8\\u4e0d\\u6d89\\u53ca\\u771f\\u5b9e\\u8d44\\u91d1'),
        StringStruct('OriginalFilename', '%s'),
        StringStruct('ProductName', '\\u56fd\\u5185\\u671f\\u8d27\\u6a21\\u62df\\u4ea4\\u6613\\u7ec8\\u7aef'),
        StringStruct('ProductVersion', '%s')
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [2052, 1200])])
  ]
)
""" % (", ".join(VERSION.split(".")), ", ".join(VERSION.split(".")), VERSION, EXE_NAME, VERSION)
    with open(VERFILE, "w", encoding="utf-8") as f:
        f.write(txt)
    print("[2/5] 版本信息已生成：%s" % VERFILE)


# ----------------------------------------------------------------------
# 三、生成 PyInstaller 的 spec（UTF-8，PyInstaller 二进制读取后按编码声明解析，
#     中文路径安全；路径一律用 SPECPATH 拼接，保证 spec 可移植）
# ----------------------------------------------------------------------
def write_spec():
    datas = ",\n        ".join(
        "(os.path.join(BASE, %r), %r)" % (s, d) for s, d in DATA_MAP
    )
    spec = '''# -*- mode: python ; coding: utf-8 -*-
"""由 打包成exe.py 自动生成 —— 改打包参数请改 打包成exe.py，勿直接改本文件。"""
import os

BASE = SPECPATH          # spec 所在目录 = 项目目录

a = Analysis(
    [os.path.join(BASE, %(entry)r)],
    pathex=[BASE],
    binaries=[],
    datas=[
        %(datas)s,
    ],
    hiddenimports=[%(hidden)s],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[%(excludes)s],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=%(name)r,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(BASE, %(icon)r),
    version=os.path.join(BASE, %(ver)r),
)
''' % {
        "entry": ENTRY,
        "datas": datas,
        "hidden": ", ".join(repr(h) for h in HIDDEN),
        "excludes": ", ".join(repr(e) for e in EXCLUDES),
        "name": APP_NAME,
        "icon": ICON,
        "ver": VERFILE,
    }
    with open(SPEC, "w", encoding="utf-8") as f:
        f.write(spec)
    print("[3/5] 打包配置已生成：%s" % SPEC)


# ----------------------------------------------------------------------
# 四、调用 PyInstaller
# ----------------------------------------------------------------------
def build():
    dist_dir = os.path.join(HERE, "dist")
    work_dir = os.path.join(HERE, "build")
    cmd = [sys.executable, "-m", "PyInstaller",
           "--noconfirm", "--clean", "--log-level", "WARN",
           "--distpath", dist_dir, "--workpath", work_dir,
           SPEC]
    print("[4/5] 正在构建单文件 exe（首次约 1~3 分钟，请稍候）...")
    r = subprocess.run(cmd, cwd=HERE)
    if r.returncode != 0:
        raise SystemExit("[!] 构建失败（返回码 %d），请查看上方输出" % r.returncode)

    src = os.path.join(dist_dir, EXE_NAME)
    if not os.path.isfile(src):
        raise SystemExit("[!] 构建完成但未找到产物：%s" % src)

    dst = os.path.join(HERE, EXE_NAME)
    if os.path.exists(dst):
        try:
            os.remove(dst)
        except OSError:
            raise SystemExit("[!] 旧的 %s 正在运行，请先关闭它再重新打包" % EXE_NAME)
    shutil.move(src, dst)
    shutil.rmtree(dist_dir, ignore_errors=True)
    shutil.rmtree(work_dir, ignore_errors=True)
    return dst


# ----------------------------------------------------------------------
# 五、自检：跑一下 exe 的 --help，确认打包后的解释器与模块都正常
# ----------------------------------------------------------------------
def selftest(exe_path):
    size = os.path.getsize(exe_path) / 1024.0 / 1024.0
    print("[5/5] 自检：启动 %s --help ..." % EXE_NAME)
    try:
        r = subprocess.run([exe_path, "--help"], capture_output=True, timeout=180)
        out = (r.stdout or b"").decode("utf-8", "replace") + \
              (r.stderr or b"").decode("utf-8", "replace")
        ok = (r.returncode == 0) and ("--port" in out)
        print("      返回码 %d，输出片段：%s" % (r.returncode, out.strip().splitlines()[:2]))
    except Exception as e:                                  # noqa: BLE001
        ok = False
        print("      自检异常：%s" % e)

    print("=" * 62)
    print("  打包%s" % ("成功 ✅" if ok else "完成，但自检未通过，请手动双击试试 ⚠"))
    print("  产物：%s  （%.1f MB）" % (exe_path, size))
    print("  双击它即可启动；数据会写在它旁边的「账户数据」「行情缓存」里。")
    print("=" * 62)
    return ok


if __name__ == "__main__":
    make_icon()
    make_version()
    write_spec()
    exe = build()
    selftest(exe)
