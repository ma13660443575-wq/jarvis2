# -*- coding: utf-8 -*-
"""
desktop/package/backend_entry.py — JARVIS2 后端 sidecar 入口（PyInstaller）
==========================================================================
行为：
  1. 修复 --noconsole 下 sys.stdout=None，保证端口握手能被 Tauri 读到。
  2. 选端口：优先 config.PORT(8100)；被占则漂移到系统空闲端口。
  3. stdout 打印一行 `JARVIS_PORT=<n>` 作为唯一握手协议。
  4. 用已 import 的 app 对象跑 uvicorn（避免字符串导入在 frozen 态出问题）。

非 frozen 时也可直接跑：  .venv\\Scripts\\python desktop\\package\\backend_entry.py
"""
from __future__ import annotations

import os
import sys


def _ensure_import_root() -> None:
    """非 frozen 直接运行时把项目根加入 sys.path（frozen 由 PyInstaller --paths 处理）。"""
    if getattr(sys, "frozen", False):
        return
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))


def _bootstrap_console() -> None:
    """--noconsole 下 stdio 是 None，重挂到 Tauri 读取的 fd1/fd2。"""
    if not getattr(sys, "frozen", False):
        return
    if sys.stdout is None:
        try:
            sys.stdout = os.fdopen(1, "w", encoding="utf-8", errors="replace")
        except OSError:
            pass
    if sys.stderr is None:
        try:
            sys.stderr = os.fdopen(2, "w", encoding="utf-8", errors="replace")
        except OSError:
            pass


def _find_free_port(pref: int) -> int:
    """优先用 pref，被占则取系统空闲端口。"""
    import socket
    for port in (pref, 0):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", port))
            return s.getsockname()[1] or pref
        except OSError:
            continue
        finally:
            s.close()
    return pref


def _pid_alive(pid: int) -> bool:
    """判断 PID 是否仍在运行。

    注意：OpenProcess 只检查进程对象是否存在，不检查进程是否已终止。
    若其它进程（如 Tauri 持有的 CommandChild 句柄）仍打开着句柄，被强杀的
    进程对象会残留，OpenProcess 依旧返回句柄 —— 因此必须用
    WaitForSingleObject / GetExitCodeProcess 区分"僵尸对象"与"真正存活"。
    """
    import ctypes
    from ctypes import wintypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    SYNCHRONIZE = 0x00100000
    STILL_ACTIVE = 259
    WAIT_TIMEOUT = 258

    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    h = kernel32.OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if h:
        try:
            # WAIT_TIMEOUT=仍在运行；WAIT_OBJECT_0=已退出（含僵尸进程对象）
            return kernel32.WaitForSingleObject(h, 0) == WAIT_TIMEOUT
        finally:
            kernel32.CloseHandle(h)

    # 拿不到 SYNCHRONIZE（极罕见）：退化为退出码检测
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return False
    try:
        code = wintypes.DWORD(0)
        if not kernel32.GetExitCodeProcess(h, ctypes.byref(code)):
            return True  # 保守：无法确认已退出
        return code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(h)


def _parent_pid(pid: int):
    """用 Toolhelp 快照查给定进程的父 PID（Windows）。找不到返回 None。"""
    import ctypes
    from ctypes import wintypes

    TH32CS_SNAPPROCESS = 0x00000002

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_wchar * 260),
        ]

    kernel32 = ctypes.windll.kernel32
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == -1:
        return None
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if not kernel32.Process32FirstW(snap, ctypes.byref(entry)):
            return None
        while True:
            if entry.th32ProcessID == pid:
                return int(entry.th32ParentProcessID)
            if not kernel32.Process32NextW(snap, ctypes.byref(entry)):
                return None
    finally:
        kernel32.CloseHandle(snap)


def _start_watchdog() -> None:
    """退出守护：父进程（PyInstaller bootloader）或祖父进程（Tauri 主程序，仅 frozen）
    消失则自我退出——兜底防止 Tauri 侧 taskkill 漏杀 / 主程序崩溃后残留孤儿后端。

    frozen 链路：Tauri → bootloader → 本进程；非 frozen：终端 → 本进程。
    """
    import threading
    import time

    frozen = getattr(sys, "frozen", False)
    boot = os.getppid()
    app_pid = _parent_pid(boot) if frozen else None

    def _loop():
        while True:
            time.sleep(2.0)
            try:
                if not _pid_alive(boot) or (app_pid and not _pid_alive(app_pid)):
                    os._exit(0)
            except Exception:
                continue

    threading.Thread(target=_loop, daemon=True).start()


def _register_with_agent(port: int) -> None:
    """向常驻唤醒 agent（127.0.0.1:8123）上报本后端实际端口。

    agent 离线（未安装/未启动）时静默失败——等 agent 起来后由它主动拉起主应用，
    主应用启动时这里会再次注册。fire-and-forget：不阻塞 uvicorn 启动。
    """
    import json
    import urllib.request
    from backend import config  # noqa: F811

    if not config.WAKE_ENABLED:
        return
    url = f"http://127.0.0.1:{config.WAKE_AGENT_PORT}/register"
    data = json.dumps({"backend_port": port, "app_version": "0.1.0"}).encode("utf-8")
    try:
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            resp.read()
    except Exception:  # noqa: BLE001
        pass


def main() -> None:
    import threading  # noqa: F401  (供 _register_with_agent 后台线程)
    _bootstrap_console()
    _ensure_import_root()
    _start_watchdog()
    from backend import config  # noqa: E402  (触发 .env 加载 + 数据目录初始化)
    port = config.PORT
    if os.getenv("JARVIS_FIXED_PORT") != "1":
        port = _find_free_port(config.PORT)
    print(f"JARVIS_PORT={port}", flush=True)
    # 非阻塞向唤醒 agent 上报端口（后台线程，不拖慢启动）
    threading.Thread(target=_register_with_agent, args=(port,), daemon=True).start()
    from backend.main import app  # noqa: E402
    import uvicorn  # noqa: E402
    uvicorn.run(app, host=config.HOST, port=port, log_level="info")


if __name__ == "__main__":
    main()
