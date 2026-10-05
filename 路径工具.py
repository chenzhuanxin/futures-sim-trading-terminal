# -*- coding: utf-8 -*-
"""
路径工具 —— 让同一个程序既能「源码运行」又能「打包成 exe 运行」。

背景（打包后最容易踩的坑）：
    PyInstaller 单文件模式下，程序启动时会把内嵌资源解压到系统临时目录
    （形如 C:\\Users\\xxx\\AppData\\Local\\Temp\\_MEI123456），并把 sys._MEIPASS
    指向它；而 __file__ / os.path.dirname(__file__) 也落在那个临时目录里。
    该目录在程序退出时被整个删除 —— 如果账户数据、行情缓存还往里写，
    关掉程序数据就没了，用户会以为「账户被清空了」。

解决：
    把「只读资源」与「可写数据」彻底分开：
      · 只读资源（交易面板.html、vendor/、品种参数.json、快照/）：
            打包后走 sys._MEIPASS；源码运行走脚本目录。
      · 可写数据（账户数据/、行情缓存/）：
            一律落在 APP_DIR —— 打包后就是 exe 所在目录，
            所以「把 exe 拷到哪，账户数据跟到哪」，等于绿色便携版。
      · 同名资源若在 APP_DIR 下存在，优先用 APP_DIR 的那份 ——
            这样用户想改界面或参数，直接在 exe 旁边放一份同名文件即可覆盖，
            不用重新打包。
"""
import os
import sys

# 是否处于 PyInstaller 打包后的运行环境
FROZEN = bool(getattr(sys, "frozen", False))

# 只读资源目录：打包后 = 解压临时目录；源码运行 = 本文件所在目录
RES_DIR = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))

if FROZEN:
    # 可写数据目录：exe 所在目录（便携，拷到哪数据跟到哪）
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))


def res(*parts):
    """定位一个只读资源（文件或目录）。返回绝对路径。

    查找顺序：APP_DIR 下的同名文件 → 内嵌资源。
    允许用户用「exe 旁边放一份同名文件」的方式覆盖内嵌资源。
    """
    rel = os.path.join(*parts)
    if FROZEN:
        outer = os.path.join(APP_DIR, rel)
        if os.path.exists(outer):
            return outer
        return os.path.join(RES_DIR, rel)
    return os.path.join(APP_DIR, rel)


def data(*parts):
    """定位一个可写数据路径（文件或目录）。返回绝对路径，不负责创建。"""
    return os.path.join(APP_DIR, *parts)


def ensure_dir(path):
    """确保目录存在，返回该路径。"""
    try:
        os.makedirs(path, exist_ok=True)
    except Exception:                                       # noqa: BLE001
        pass
    return path


def describe():
    """一句话描述当前运行模式，启动时打印，便于排查。"""
    return "打包 exe" if FROZEN else "源码"
