#!/usr/bin/env python3
"""
iCloudPD Studio desktop shell.

Starts the local iCloudPD Studio server and opens it in a native WebView2
window.  Closing the window stops the server (and any running icloudpd task).
"""
from __future__ import annotations

import ctypes
import html
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))

# 打包版只有一个 exe，靠这个标记复用它当作「纯服务」进程。
SERVER_FLAG = "--server"

if FROZEN:
    # 打包版：exe 所在目录即应用目录（data/ 与其同级），静态资源在解压目录。
    APP_DIR = Path(sys.executable).resolve().parent
    APP_PY = None  # 源码不存在，服务由 exe 自身以 --server 身份拉起
    ICON_PATH = Path(getattr(sys, "_MEIPASS", APP_DIR)) / "static" / "app.ico"
else:
    APP_DIR = Path(__file__).resolve().parent
    APP_PY = APP_DIR / "app.py"
    ICON_PATH = APP_DIR / "static" / "app.ico"

DATA_DIR = APP_DIR / "data"
LOG_PATH = DATA_DIR / "desktop-server.log"

# 独立的 AppUserModelID：没有它，Windows 会把窗口算作 pythonw.exe 的应用，
# 任务栏按钮就显示 Python 图标，而不是我们自己的 app.ico。
APP_USER_MODEL_ID = "iCloudPD.Studio.Desktop"

WM_SETICON = 0x0080
ICON_SMALL = 0
ICON_BIG = 1
ICON_SMALL2 = 2
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010
SM_CXICON = 32
SM_CXSMICON = 49

SPLASH = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  html, body { margin: 0; height: 100%; }
  body {
    display: grid;
    place-items: center;
    background:
      radial-gradient(420px 300px at 78% 0%, rgba(221, 74, 39, .22), transparent 70%),
      #12110e;
    color: #f4eddf;
    font-family: "Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI", sans-serif;
    overflow: hidden;
  }
  .wrap { width: 100%; padding: 34px 38px; }
  .brand { display: flex; align-items: center; gap: 13px; margin-bottom: 30px; }
  .mark {
    width: 48px; height: 48px; border-radius: 14px; background: #f4eddf;
    display: grid; place-items: center; flex: none;
  }
  .mark svg { width: 30px; height: 30px; fill: none; stroke: #191814; stroke-width: 1.5; }
  .brand strong { display: block; font-size: 18px; letter-spacing: -.02em; }
  .brand span {
    display: block; margin-top: 2px; color: #8f8778;
    font-family: "Cascadia Code", Consolas, monospace; font-size: 8px; letter-spacing: .16em;
  }
  .line {
    height: 1px; background: linear-gradient(90deg, #dd4a27, rgba(244,237,223,.12));
    margin-bottom: 26px;
  }
  h1 { margin: 0 0 8px; font-family: "Songti SC", "Noto Serif SC", Georgia, serif; font-size: 34px; font-weight: 500; letter-spacing: -.04em; }
  h1 em { color: #f2643f; font-style: italic; }
  p { margin: 0; color: #a9a08f; font-size: 11px; line-height: 1.7; }
  .boot { display: flex; align-items: center; gap: 10px; margin-top: 26px; color: #867e70; font-family: Consolas, monospace; font-size: 9px; letter-spacing: .06em; }
  .spinner {
    width: 14px; height: 14px; border-radius: 50%;
    border: 1.5px solid rgba(244,237,223,.18); border-top-color: #dd4a27;
    animation: spin .8s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
  .dots { margin-left: auto; display: flex; gap: 4px; }
  .dots i { width: 5px; height: 5px; border-radius: 50%; background: rgba(244,237,223,.18); }
  .dots i:first-child { background: #dd4a27; }
</style>
</head>
<body>
  <div class="wrap">
    <div class="brand">
      <div class="mark">
        <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9.2"/><path d="M12 2.8 8.2 9.3M21.1 7.6h-7.6M18.2 19.9l-3.1-6.9M8.6 21.1l3.2-6.9M2.9 16.4h7.6M5.8 4.1l3.1 6.9"/></svg>
      </div>
      <div><strong>iCloudPD Studio</strong><span>LOCAL ARCHIVE DESKTOP</span></div>
    </div>
    <div class="line"></div>
    <h1>正在启动<em>本地引擎</em></h1>
    <p>首次启动需要检测 icloudpd，通常只需要几秒。<br>照片与密码都只留在本机。</p>
    <div class="boot"><span class="spinner"></span><span>STARTING LOCAL SERVICE</span><span class="dots"><i></i><i></i><i></i></span></div>
  </div>
</body>
</html>"""

ERROR = """<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><style>
  :root { color-scheme: dark; }
  body { margin: 0; min-height: 100vh; display: grid; place-items: center; background: #12110e; color: #f4eddf;
    font-family: "Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI", sans-serif; }
  .card { width: min(86vw, 580px); padding: 30px; border: 1px solid rgba(244,237,223,.12); border-radius: 20px; background: #1b1915; }
  h1 { margin: 0 0 8px; font-size: 24px; letter-spacing: -.03em; }
  p { color: #a9a08f; font-size: 12px; line-height: 1.7; }
  pre { max-height: 170px; overflow: auto; padding: 12px; border-radius: 9px; background: #0e0d0a; color: #c8bfae;
    font-family: Consolas, monospace; font-size: 9px; white-space: pre-wrap; }
  .row { display: flex; gap: 8px; margin-top: 18px; }
  button { height: 38px; padding: 0 14px; border-radius: 9px; border: 1px solid rgba(244,237,223,.16);
    background: transparent; color: #f4eddf; font-size: 11px; cursor: pointer; }
  button.primary { background: #dd4a27; border-color: #dd4a27; color: #fff; }
</style></head>
<body><div class="card">
  <h1>本地服务没有启动</h1>
  <p>请确认 Python 与 icloudpd 可用。下面是启动日志的最后部分：</p>
  <pre>{{LOG}}</pre>
  <div class="row">
    <button class="primary" onclick="pywebview.api.open_log()">打开完整日志</button>
    <button onclick="pywebview.api.close_app()">关闭</button>
  </div>
</div></body></html>"""


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def screen_size() -> tuple[int, int]:
    try:
        user32 = ctypes.windll.user32
        return int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1))
    except Exception:
        return 1440, 960


def set_app_user_model_id(app_id: str = APP_USER_MODEL_ID) -> None:
    """给当前进程一个独立的应用标识。

    必须在任何窗口创建之前调用，否则任务栏会把本窗口归到 pythonw.exe 名下，
    显示 Python 的默认图标（也无法单独固定到任务栏）。
    """
    if os.name != "nt":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(ctypes.c_wchar_p(app_id))
    except Exception:
        pass


def register_app_user_model_id(
    app_id: str = APP_USER_MODEL_ID,
    icon_path: Path = ICON_PATH,
    display_name: str = "iCloudPD Studio",
) -> None:
    """把 AUMID 登记到 HKCU，让任务栏/固定项用我们的图标而不是解释器图标。

    这是微软给「未打包桌面应用」的官方做法，只写当前用户，不需要管理员权限。
    """
    if os.name != "nt" or not icon_path.is_file():
        return
    try:
        import winreg

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\AppUserModelId\{app_id}") as key:
            winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, display_name)
            winreg.SetValueEx(key, "IconUri", 0, winreg.REG_SZ, str(icon_path))
    except Exception:
        pass


def _find_windows(pid: int, title_hint: str) -> list[int]:
    if os.name != "nt":
        return []
    import ctypes.wintypes as wt

    user32 = ctypes.windll.user32
    user32.EnumWindows.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    user32.EnumWindows.restype = ctypes.c_int
    user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
    user32.GetWindowThreadProcessId.restype = wt.DWORD
    user32.GetWindowTextLengthW.argtypes = [wt.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wt.HWND, ctypes.c_wchar_p, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.IsWindowVisible.argtypes = [wt.HWND]
    user32.IsWindowVisible.restype = wt.BOOL

    found: list[int] = []

    def callback(hwnd, _lparam):
        owner = wt.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value != pid or not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        if title_hint.lower() in buffer.value.lower():
            found.append(int(hwnd or 0))
        return True

    callback_ref = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)(callback)
    user32.EnumWindows(callback_ref, 0)
    return found


def apply_window_icon(icon_path: Path, title_hint: str = "iCloudPD", timeout: float = 15.0) -> None:
    """把 ico 同时挂到窗口的大图标与小图标上。

    WinForms 只设 Form.Icon，任务栏/Alt-Tab 在某些情况下仍沿用进程图标，
    这里直接对 HWND 发 WM_SETICON 兜底。
    """
    if os.name != "nt" or not icon_path.is_file():
        return
    user32 = ctypes.windll.user32
    user32.LoadImageW.restype = ctypes.c_void_p
    user32.LoadImageW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_wchar_p,
        ctypes.c_uint,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_uint,
    ]
    user32.SendMessageW.restype = ctypes.c_void_p
    user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_void_p]

    def load(size: int) -> int | None:
        handle = user32.LoadImageW(None, str(icon_path), IMAGE_ICON, size, size, LR_LOADFROMFILE)
        return int(handle) if handle else None

    big = load(int(user32.GetSystemMetrics(SM_CXICON) or 32))
    small = load(int(user32.GetSystemMetrics(SM_CXSMICON) or 16))
    if not big and not small:
        return

    pid = os.getpid()
    deadline = time.time() + timeout
    handles: list[int] = []
    while time.time() < deadline and not handles:
        handles = _find_windows(pid, title_hint)
        if not handles:
            time.sleep(0.2)
    for hwnd in handles:
        if big:
            user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, big)
        if small:
            user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, small)
            user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL2, small)


def tail(path: Path, limit: int = 5000) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        return text[-limit:]
    except Exception as exc:
        return f"(无法读取日志: {exc})"


def wait_health(url: str, proc: subprocess.Popen | None, timeout: float = 45.0) -> tuple[bool, str]:
    started = time.time()
    last_error = ""
    while time.time() - started < timeout:
        if proc is not None and proc.poll() is not None:
            return False, f"服务进程已退出（exit {proc.returncode}）"
        try:
            with urllib.request.urlopen(url + "/api/health", timeout=1.5) as response:
                if response.status == 200:
                    return True, ""
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last_error = str(exc)
        time.sleep(0.3)
    return False, last_error or "健康检查超时"


def find_existing_server() -> str | None:
    for port in range(8765, 8776):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.06):
                pass
        except OSError:
            continue
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=0.8) as response:
                data = json.loads(response.read().decode("utf-8"))
                if data.get("name") == "iCloudPD Studio":
                    return f"http://127.0.0.1:{port}"
        except Exception:
            continue
    return None


def request_shutdown(url: str) -> None:
    try:
        req = urllib.request.Request(
            url + "/api/shutdown",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=2).read()
    except Exception:
        pass


class Api:
    def __init__(self):
        self._window = None
        self._closed = threading.Event()

    def bind(self, window) -> None:
        self._window = window

    def close_app(self) -> None:
        if self._window is not None:
            self._window.destroy()

    def open_log(self) -> None:
        try:
            if LOG_PATH.exists():
                os.startfile(str(LOG_PATH))  # type: ignore[attr-defined]
        except Exception:
            pass


def main() -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    # 必须在创建任何窗口之前设置，否则任务栏会沿用 pythonw.exe 的图标
    set_app_user_model_id()
    register_app_user_model_id()
    try:
        import webview  # type: ignore
    except Exception as exc:
        message = (
            "缺少桌面窗口组件 pywebview。\n\n"
            "请在终端执行：python -m pip install pywebview\n\n"
            f"详细信息：{exc}"
        )
        try:
            import tkinter.messagebox as messagebox

            messagebox.showerror("iCloudPD Studio", message)
        except Exception:
            print(message)
        return 1

    if APP_PY is not None and not APP_PY.is_file():
        print(f"找不到 app.py: {APP_PY}")
        return 1

    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    existing = find_existing_server()
    server: subprocess.Popen | None = None
    log_file = None
    if existing:
        url = existing
    else:
        port = find_free_port()
        url = f"http://127.0.0.1:{port}"
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        log_file = open(LOG_PATH, "a", encoding="utf-8", errors="replace", buffering=1)
        log_file.write("\n\n===== " + datetime.now().isoformat(timespec="seconds") + " desktop start =====\n")
        log_file.flush()
        try:
            server_cmd = (
                [sys.executable, SERVER_FLAG, "--no-browser", "--port", str(port)]
                if FROZEN
                else [sys.executable, str(APP_PY), "--no-browser", "--port", str(port)]
            )
            server = subprocess.Popen(
                server_cmd,
                cwd=str(APP_DIR),
                stdout=log_file,
                stderr=subprocess.STDOUT,
                creationflags=creationflags,
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            )
        except Exception as exc:
            log_file.write(f"无法启动服务: {exc}\n")
            log_file.close()
            try:
                import tkinter.messagebox as messagebox

                messagebox.showerror("iCloudPD Studio", f"无法启动本地服务：\n{exc}")
            except Exception:
                print(exc)
            return 1

    api = Api()
    closed = api._closed

    def stop_server() -> None:
        if server is None:
            closed.set()
            if log_file is not None:
                try:
                    log_file.close()
                except Exception:
                    pass
            return
        if closed.is_set() and server.poll() is not None:
            return
        closed.set()
        if server.poll() is None:
            request_shutdown(url)
            deadline = time.time() + 7
            while server.poll() is None and time.time() < deadline:
                time.sleep(0.15)
        if server.poll() is None:
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(server.pid), "/T", "/F"],
                    capture_output=True,
                    timeout=12,
                    creationflags=creationflags,
                )
            except Exception:
                try:
                    server.terminate()
                except Exception:
                    pass
        if log_file is not None:
            try:
                log_file.close()
            except Exception:
                pass

    window = webview.create_window(
        "iCloudPD Studio",
        html=SPLASH,
        width=640,
        height=420,
        min_size=(640, 420),
        resizable=True,
        background_color="#12110e",
        text_select=True,
        js_api=api,
    )
    api.bind(window)

    if window is None:
        stop_server()
        return 1

    window.events.closed += lambda: stop_server()

    width, height = screen_size()
    win_w = max(1024, min(1440, width - 80))
    win_h = max(700, min(940, height - 100))

    def boot() -> None:
        ok, detail = wait_health(url, server)
        if closed.is_set():
            return
        if ok:
            try:
                window.load_url(url)
                time.sleep(0.35)
                window.resize(win_w, win_h)
                test_ms = int(os.environ.get("ICLOUDPD_DESKTOP_TEST_MS", "0") or 0)
                if test_ms > 0:
                    timer = threading.Timer(test_ms / 1000.0, window.destroy)
                    timer.daemon = True
                    timer.start()
            except Exception as exc:
                window.load_html(
                    ERROR.replace("{{LOG}}", html.escape(f"{detail}\n无法加载界面: {exc}\n\n{tail(LOG_PATH)}"))
                )
        else:
            window.load_html(
                ERROR.replace(
                    "{{LOG}}",
                    html.escape(f"{detail}\n\n{tail(LOG_PATH)}"),
                )
            )

    gui = "edgechromium" if os.name == "nt" else None

    def install_icon() -> None:
        # 窗口真正建出来之后才能拿到 HWND；加载完成后再补一次，避免被页面标题同步覆盖
        apply_window_icon(ICON_PATH, timeout=20.0)
        time.sleep(1.5)
        apply_window_icon(ICON_PATH, timeout=3.0)

    threading.Thread(target=install_icon, name="app-icon", daemon=True).start()

    try:
        webview.start(
            boot,
            gui=gui,
            private_mode=False,
            storage_path=str(DATA_DIR / "webview"),
            icon=str(ICON_PATH) if ICON_PATH.exists() else None,
            debug=False,
        )
    except Exception as exc:
        print(f"WebView 启动失败: {exc}")
        try:
            import tkinter.messagebox as messagebox

            answer = messagebox.askyesno(
                "iCloudPD Studio",
                f"桌面窗口启动失败：\n{exc}\n\n是否改用默认浏览器打开？",
            )
            if answer:
                ok, _ = wait_health(url, server)
                if ok:
                    webbrowser.open(url)
                    print("按 Enter 结束服务和窗口…")
                    input()
        except Exception:
            pass
    finally:
        stop_server()
    return 0


def log_crash() -> None:
    """窗口模式下没有控制台，异常会无声消失。

    把 traceback 落到 data/error.log，用户遇到问题至少能拿到一份可反馈的东西。
    """
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        import traceback

        with open(DATA_DIR / "error.log", "a", encoding="utf-8", errors="replace") as fh:
            fh.write("\n\n===== " + datetime.now().isoformat(timespec="seconds") + " =====\n")
            fh.write("argv: " + " ".join(sys.argv) + "\n")
            fh.write(traceback.format_exc())
    except Exception:
        pass


def entry() -> int:
    """开窗身份 / 服务身份，共用一个 exe。

    - 无参数：桌面窗口（webview 壳）
    - --server：纯 HTTP 服务，供上面那个壳在子进程里拉起
    """
    try:
        return dispatch()
    except SystemExit:
        raise
    except BaseException:
        log_crash()
        raise


def dispatch() -> int:
    argv = sys.argv[1:]
    if SERVER_FLAG in argv:
        sys.argv = [sys.argv[0]] + [item for item in argv if item != SERVER_FLAG]
        if str(APP_DIR) not in sys.path:
            sys.path.insert(0, str(APP_DIR))
        import app as studio  # noqa: PLC0415 - 延迟导入，窗口身份不必加载服务端

        return studio.main()
    return main()


if __name__ == "__main__":
    raise SystemExit(entry())