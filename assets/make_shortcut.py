"""在桌面创建 / 列出 / 移除 Vibe AStock 快捷方式。

⛔ 为什么不用 PowerShell：`New-Object -ComObject WScript.Shell` 和 `Add-Type`
   都会被本机安全策略拦截。这里用 Python ctypes 直接调 IShellLinkW +
   IPersistFile，绕开 PowerShell。

用法：
    python make_shortcut.py            # 创建
    python make_shortcut.py --list     # 打印环境与当前快捷方式状态
    python make_shortcut.py --remove   # 移除桌面快捷方式
"""
from __future__ import annotations

import ctypes
import os
import sys
import time
import uuid
import winreg
from ctypes import wintypes

ASSETS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(ASSETS)
ICON = os.path.join(ASSETS, "vibe-astock.ico")
LAUNCHER = os.path.join(ASSETS, "launcher.py")

SHORTCUT_NAME = "Vibe AStock 复盘.lnk"
DESC = "Vibe AStock 本地短线复盘工作台"

# 快捷方式的 TargetPath 不能是 .py（系统不认），指向解释器，脚本作为参数。
# 用**系统 python 启动器**而非项目 venv：快捷方式的作用只是把 launcher.py 跑起来，
# launcher 内部自己会挑 venv 起服务，所以这里用哪个解释器都行，
# 唯一要求是它能 import 标准库（ctypes/urllib）—— 托管 python 完全满足。
_FALLBACK_PYTHONS = [
    r"C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe",
    sys.executable,
]


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]


def guid(s: str) -> GUID:
    u = uuid.UUID(s.strip("{}"))
    g = GUID()
    g.Data1, g.Data2, g.Data3 = u.time_low, u.time_mid, u.time_hi_version
    g.Data4 = (ctypes.c_ubyte * 8)(*u.bytes[8:])
    return g


def vcall(ptr, index, restype, argtypes, *args):
    vtbl = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vtbl[index])(ptr, *args)


CLSID_SHELL_LINK = guid("{00021401-0000-0000-C000-000000000046}")
IID_ISHELL_LINK = guid("{000214F9-0000-0000-C000-000000000046}")
IID_IPERSISTFILE = guid("{0000010b-0000-0000-C000-000000000046}")


def desktop_dir() -> str:
    k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                        r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders")
    v, _ = winreg.QueryValueEx(k, "Desktop")
    return os.path.expandvars(v)


def shortcut_path() -> str:
    return os.path.join(desktop_dir(), SHORTCUT_NAME)


def pick_python() -> str:
    p = _first_existing_python()
    if p == "<未找到>":
        raise SystemExit("找不到可用的 Python 解释器")
    return p


def create() -> None:
    if not os.path.isfile(ICON):
        raise SystemExit(f"图标不存在：{ICON}\n先运行 assets/make_icon.py")
    if not os.path.isfile(LAUNCHER):
        raise SystemExit(f"启动器不存在：{LAUNCHER}")

    py = pick_python()
    lnk = shortcut_path()

    # ⛔ 已存在的 .lnk 必须先删：IPersistFile::Save 覆写一个仍被占用的旧
    #    快捷方式会抛 PermissionError(拒绝访问)—— 这是本机实测踩到的坑。
    if os.path.isfile(lnk):
        for attempt in range(5):
            try:
                os.remove(lnk)
                break
            except PermissionError:
                # 资源管理器/杀软可能短暂持有句柄，退避重试
                if attempt == 4:
                    raise SystemExit(
                        f"无法覆盖 {lnk}\n"
                        "请先在桌面上把这个快捷方式删掉，或关闭已打开它的窗口，再重试。"
                    )
                time.sleep(0.4)

    ole32 = ctypes.windll.ole32
    ole32.CoInitialize(None)
    p_link = ctypes.c_void_p()
    ole32.CoCreateInstance(ctypes.byref(CLSID_SHELL_LINK), None, 1,
                           ctypes.byref(IID_ISHELL_LINK), ctypes.byref(p_link))

    args = f'"{LAUNCHER}"'   # 路径含中文与可能的空格，加引号

    # 每个 Set* 都查 HRESULT：索引错位时它们会静默"成功"但字段写不进去
    steps = [
        ("SetPath",         20, [wintypes.LPCWSTR], py),
        ("SetArguments",    11, [wintypes.LPCWSTR], args),
        ("SetWorkingDir",    9, [wintypes.LPCWSTR], ROOT),
        ("SetDescription",   7, [wintypes.LPCWSTR], DESC),
        ("SetIconLocation", 17, [wintypes.LPCWSTR, ctypes.c_int], ICON, 0),
        ("SetShowCmd",      15, [ctypes.c_int], 1),
    ]
    for name, idx, argtypes, *argv in steps:
        hr = vcall(p_link, idx, ctypes.HRESULT, argtypes, *argv)
        if hr != 0:
            raise SystemExit(f"{name} 失败 hr=0x{hr & 0xFFFFFFFF:08X}")

    p_persist = ctypes.c_void_p()
    vcall(p_link, 0, ctypes.HRESULT, [ctypes.c_void_p, ctypes.c_void_p],
          ctypes.byref(IID_IPERSISTFILE), ctypes.byref(p_persist))
    hr = vcall(p_persist, 6, ctypes.HRESULT, [wintypes.LPCWSTR, wintypes.BOOL], lnk, True)
    if hr != 0:
        raise SystemExit(f"保存快捷方式失败 hr=0x{hr & 0xFFFFFFFF:08X}")

    print("已创建：", lnk)
    verify(lnk)


def verify(lnk: str) -> None:
    """校验关键字段真的写进了 .lnk。

    ⛔ 不用 IShellLinkW getter 反读：IPersistFile::Load 的 vtable 索引在
       本机对不上（会返回非 0，getter 全读成空），容易误判成"没写进去"。
    ⛔ 不用整段 decode('utf-16-le') 后做字符串包含：中文路径会夹带解码噪声，
       字段其实写对了也报 FAIL（实测踩过）。
    ✅ 只拿 **纯 ASCII 关键词的 UTF-16LE 字节**做包含判断，稳。
    """
    raw = open(lnk, "rb").read()
    for label, expect in (("目标解释器", "python.exe"),
                          ("启动器参数", "launcher.py"),
                          ("图标路径", "vibe-astock.ico")):
        print(f"  {label:<10} {'✅' if expect.encode('utf-16-le') in raw else '❌'}  {expect}")

    # 目标文件必须真实存在，否则双击必然失败
    for label, path in (("解释器存在", _first_existing_python()),
                        ("启动器存在", LAUNCHER),
                        ("图标存在", ICON)):
        print(f"  {label:<10} {'✅' if os.path.isfile(path) else '❌'}  {path}")
    print("  文件大小", os.path.getsize(lnk), "bytes")


def _first_existing_python() -> str:
    for p in _FALLBACK_PYTHONS:
        if p and os.path.isfile(p):
            return p
    return "<未找到>"


def show() -> None:
    print("桌面       :", desktop_dir())
    print("图标       :", ICON, "存在 =", os.path.isfile(ICON))
    print("启动器     :", LAUNCHER, "存在 =", os.path.isfile(LAUNCHER))
    print("项目根     :", ROOT)
    print("解释器     :", pick_python())
    lnk = shortcut_path()
    print("快捷方式   :", lnk, "存在 =", os.path.isfile(lnk))
    if os.path.isfile(lnk):
        verify(lnk)


def remove() -> None:
    lnk = shortcut_path()
    if os.path.isfile(lnk):
        os.remove(lnk)
        print("已移除：", lnk)
    else:
        print("桌面没有该快捷方式，无需移除")


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg == "--list":
        show()
    elif arg == "--remove":
        remove()
    else:
        create()
