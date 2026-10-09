"""Vibe AStock 桌面启动器 —— 双击图标走这里。

职责（顺序固定，缺一步都打不开页面）：
  1. 探测 8910 是否已有服务在跑 → 有就直接开页面（幂等，不重复起进程）
  2. 没有就用 parked venv 的解释器拉起 uvicorn（后台独立进程，随窗口关闭不受影响）
  3. 轮询等待端口就绪（最多 WAIT_MAX 秒），就绪后用 Chrome --app 窗口打开
  4. 端口被别的程序占用 / venv 缺失 / 等待超时 → 明确报错，不静默失败

⛔ 为什么不用「先 kill 再重启」：服务是本地数据库的唯一持有者，
   粗暴 kill 可能打断正在写的复盘任务。占端口的若是别人程序，也不该乱杀。
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request

HOST = "127.0.0.1"
PORT = 8910
URL = f"http://{HOST}:{PORT}/"
WAIT_MAX = 90.0          # 冷启动要装依赖/读缓存，90s 足够
PROBE_TIMEOUT = 2.0

# 以本文件为锚推导项目根（本文件在 <root>/assets/ 下）
ASSETS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(ASSETS)

# venv 优先用项目内 .venv，没有则退回同级的 vibe-astock-venv-parked。
# 写成同级相对路径，换机器/挪目录不用改脚本。
VENV_CANDIDATES = [
    os.path.join(ROOT, ".venv", "Scripts", "python.exe"),
    os.path.join(os.path.dirname(ROOT), "vibe-astock-venv-parked", "Scripts", "python.exe"),
]

BROWSER_CANDIDATES = [
    r"C:\Orowser\Chrome\App\chrome.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


def log(msg: str) -> None:
    print(msg, flush=True)


def port_open(host: str = HOST, port: int = PORT) -> bool:
    """端口是否可连接（比 netstat 干净，且不依赖外部命令）。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(PROBE_TIMEOUT)
        return s.connect_ex((host, port)) == 0


def service_alive(host: str = HOST, port: int = PORT) -> bool:
    """端口开着 **且** 确实是本项目的服务（会返回我们的 schema）。

    只看端口会把「别的程序占了 8910」误判成服务已就绪，
    结果浏览器打开一个不相干的页面，还以为启动成功。
    """
    if not port_open(host, port):
        return False
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"http://{host}:{port}/", timeout=4) as r:
            body = r.read(4000).decode("utf-8", "ignore")
        # React SPA 根页面会引用自己的 bundle
        return "/assets/" in body or "<div id=" in body.lower()
    except Exception:
        # 端口通但 HTTP 不对路（多半是被别的程序占了）
        return False


def pick_venv() -> str | None:
    for p in VENV_CANDIDATES:
        if os.path.isfile(p):
            return p
    return None


def pick_browser() -> str | None:
    for p in BROWSER_CANDIDATES:
        if os.path.isfile(p):
            return p
    return None


def start_service(python_exe: str) -> subprocess.Popen:
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["ASTOCK_AGENT_HOME"] = os.path.expanduser(r"~\.duanxian-agents")
    log(f"[启动器] 拉起服务：{python_exe}")

    # ⛔⛔ 必须让服务**完全脱离**启动器的进程树，否则双击后启动器一退出，
    #    uvicorn 就被连带杀掉（实测踩过：启动器报"就绪"、页面能开，但启动器
    #    结束后 8910 立刻失联，图标等于白做）。
    #    DETACHED_PROCESS = 服务不依附父进程；CREATE_NEW_PROCESS_GROUP = 独立
    #    控制台组，不随父进程退出；CREATE_NO_WINDOW = 不弹黑框。
    flags = 0
    if os.name == "nt":
        flags = (getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
                 | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
                 | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
    return subprocess.Popen(
        [python_exe, "-m", "uvicorn", "server:app", "--host", HOST, "--port", str(PORT)],
        cwd=ROOT, env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=flags,
        close_fds=True,
    )


def open_browser(browser: str) -> None:
    log(f"[启动器] 打开页面：{URL}")
    # --app= 无地址栏，最接近原生 App 体验
    subprocess.Popen([browser, f"--app={URL}", f"--user-data-dir={ROOT}\\.browser-profile"])


def main() -> int:
    # 1) 幂等：服务已在跑就别重复起
    if service_alive():
        log("[启动器] 服务已在运行，直接打开页面")
    else:
        if port_open():
            log(f"[启动器] ⚠️ 端口 {PORT} 被其他程序占用，未强行终止")
            log("[启动器] 请关闭占用该端口的程序后重试")
            return 2

        python_exe = pick_venv()
        if not python_exe:
            log(f"[启动器] ❌ 找不到 Python 环境，已尝试：")
            for c in VENV_CANDIDATES:
                log(f"    - {c}")
            return 3

        proc = start_service(python_exe)

        # 2) 等就绪
        deadline = time.monotonic() + WAIT_MAX
        while time.monotonic() < deadline:
            if service_alive():
                log(f"[启动器] ✅ 服务就绪（{WAIT_MAX:.0f}s 内）")
                break
            if proc.poll() is not None:
                log(f"[启动器] ❌ 服务进程已退出，返回码 {proc.returncode}")
                log("[启动器] 常见原因：端口冲突、依赖缺失。手动运行以下命令看报错：")
                log(f'    cd "{ROOT}"')
                log(f'    "{python_exe}" -m uvicorn server:app --host {HOST} --port {PORT}')
                return 4
            time.sleep(1.5)
        else:
            log(f"[启动器] ❌ 等待 {WAIT_MAX:.0f}s 仍未就绪")
            return 5

    # 3) 打开页面
    browser = pick_browser()
    if not browser:
        log("[启动器] ❌ 未找到 Chrome/Edge，请手动访问：")
        log(f"    {URL}")
        return 6
    open_browser(browser)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 兜底：别让窗口一闪而过没提示
        log(f"[启动器] ❌ 异常：{type(exc).__name__}: {exc}")
        input("按回车键退出…")
        sys.exit(1)
