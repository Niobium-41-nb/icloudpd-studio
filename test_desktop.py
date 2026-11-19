"""Desktop regressions: no real service, sockets, GUI, registry, or user data."""
from __future__ import annotations

import builtins
import heapq
import importlib.util
import itertools
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from unittest import mock


PROJECT_DIR = Path(__file__).resolve().parent


def load_isolated_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, PROJECT_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    # Do not create/update production __pycache__ files during these tests.
    with mock.patch.object(sys, "dont_write_bytecode", True):
        spec.loader.exec_module(module)
    return module


class SandboxCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="icloudpd-desktop-test-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.app_dir = self.base / "portable app"
        self.app_dir.mkdir()
        self.data_dir = self.app_dir / "data"
        self.local_appdata = self.base / "Local AppData"
        self.local_data = self.local_appdata / "iCloudPD Studio" / "data"
        self.enterContext(mock.patch.dict(os.environ, {
            "ICLOUDPD_DATA_DIR": str(self.base / "import data"),
            "LOCALAPPDATA": str(self.local_appdata),
            "ICLOUDPD_DESKTOP_TEST_MS": "0",
        }))
        # select_data_dir executes on import, so sandbox its environment first.
        self.runtime = load_isolated_module("_test_runtime_paths", "runtime_paths.py")
        self.runtime.APP_DIR = self.app_dir
        self.runtime.RESOURCE_DIR = self.app_dir / "resources"
        self.runtime.DATA_DIR = self.data_dir
        self.runtime.FROZEN = False
        os.environ.pop("ICLOUDPD_DATA_DIR")

    def make_config(self, directory: Path, content: bytes) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        config = directory / "config.json"
        config.write_bytes(content)
        return config


class RuntimePathsTests(SandboxCase):
    def test_writable_portable_wins_and_preserves_existing_config(self):
        portable_config = self.make_config(self.data_dir, b'{"source":"portable"}\n')
        local_config = self.make_config(self.local_data, b'{"source":"local"}\n')
        with mock.patch.object(
            self.runtime, "writable_directory", wraps=self.runtime.writable_directory
        ) as probe:
            selected = self.runtime.select_data_dir(self.app_dir, frozen=True)
        self.assertEqual(selected, self.data_dir)
        probe.assert_called_once_with(self.data_dir)
        self.assertEqual(portable_config.read_bytes(), b'{"source":"portable"}\n')
        self.assertEqual(local_config.read_bytes(), b'{"source":"local"}\n')
        self.assertEqual(list(self.data_dir.iterdir()), [portable_config])

    def test_oserror_falls_back_without_removing_either_existing_config(self):
        portable_config = self.make_config(self.data_dir, b"portable configuration")
        local_config = self.make_config(self.local_data, b"local configuration")
        real_temporary_file = self.runtime.TemporaryFile

        def probe_file(*args, **kwargs):
            if Path(kwargs["dir"]) == self.data_dir:
                raise OSError("simulated read-only portable directory")
            return real_temporary_file(*args, **kwargs)

        with mock.patch.object(self.runtime, "TemporaryFile", side_effect=probe_file) as probe:
            selected = self.runtime.select_data_dir(self.app_dir, frozen=True)
        self.assertEqual(selected, self.local_data)
        self.assertEqual(probe.call_args_list, [
            mock.call(dir=self.data_dir), mock.call(dir=self.local_data),
        ])
        self.assertEqual(portable_config.read_bytes(), b"portable configuration")
        self.assertEqual(local_config.read_bytes(), b"local configuration")
        self.assertEqual(list(self.data_dir.iterdir()), [portable_config])
        self.assertEqual(list(self.local_data.iterdir()), [local_config])

    def test_local_fallback_failure_is_propagated(self):
        failure = OSError("simulated disk failure")
        with mock.patch.object(self.runtime, "writable_directory", side_effect=failure) as probe:
            with self.assertRaises(OSError) as caught:
                self.runtime.select_data_dir(self.app_dir, frozen=True)
        self.assertIs(caught.exception, failure)
        self.assertEqual(probe.call_args_list, [
            mock.call(self.data_dir), mock.call(self.local_data),
        ])

    def test_localappdata_missing_uses_sandbox_home(self):
        os.environ.pop("LOCALAPPDATA")
        home = self.base / "home"
        expected = home / "AppData" / "Local" / "iCloudPD Studio" / "data"
        with mock.patch.object(self.runtime.Path, "home", return_value=home):
            with mock.patch.object(
                self.runtime, "writable_directory", side_effect=[OSError("blocked"), expected]
            ) as probe:
                self.assertEqual(self.runtime.select_data_dir(self.app_dir, True), expected)
        self.assertEqual(probe.call_args_list, [mock.call(self.data_dir), mock.call(expected)])

    def test_explicit_absolute_directory_wins_in_source_and_frozen_modes(self):
        explicit = self.base / "explicit data"
        config = self.make_config(explicit, b"explicit configuration")
        os.environ["ICLOUDPD_DATA_DIR"] = f"  {explicit}  "
        for frozen in (False, True):
            with self.subTest(frozen=frozen):
                with mock.patch.object(
                    self.runtime, "writable_directory", wraps=self.runtime.writable_directory
                ) as probe:
                    self.assertEqual(self.runtime.select_data_dir(self.app_dir, frozen), explicit)
                probe.assert_called_once_with(explicit)
                self.assertEqual(config.read_bytes(), b"explicit configuration")
        self.assertFalse(self.data_dir.exists())
        self.assertFalse(self.local_data.exists())

    def test_explicit_directory_failure_never_falls_back(self):
        explicit = self.base / "blocked override"
        os.environ["ICLOUDPD_DATA_DIR"] = str(explicit)
        for frozen in (False, True):
            with self.subTest(frozen=frozen):
                failure = PermissionError("simulated override denial")
                with mock.patch.object(
                    self.runtime, "writable_directory", side_effect=failure
                ) as probe:
                    with self.assertRaises(PermissionError) as caught:
                        self.runtime.select_data_dir(self.app_dir, frozen)
                self.assertIs(caught.exception, failure)
                probe.assert_called_once_with(explicit)
        self.assertFalse(self.data_dir.exists())
        self.assertFalse(self.local_data.exists())

    def test_relative_override_is_rejected_before_any_probe(self):
        for override in ("data", "../data"):
            for frozen in (False, True):
                with self.subTest(override=override, frozen=frozen):
                    os.environ["ICLOUDPD_DATA_DIR"] = override
                    with mock.patch.object(self.runtime, "writable_directory") as probe:
                        with self.assertRaisesRegex(ValueError, "绝对路径"):
                            self.runtime.select_data_dir(self.app_dir, frozen)
                    probe.assert_not_called()

    def test_source_mode_does_not_probe_or_create_data(self):
        with mock.patch.object(self.runtime, "writable_directory") as probe:
            with mock.patch.object(self.runtime, "TemporaryFile") as temporary_file:
                self.assertEqual(self.runtime.select_data_dir(self.app_dir, False), self.data_dir)
        probe.assert_not_called()
        temporary_file.assert_not_called()
        self.assertFalse(self.data_dir.exists())
        self.assertFalse(self.local_data.exists())


class DesktopSandboxCase(SandboxCase):
    def setUp(self):
        super().setUp()
        with mock.patch.dict(sys.modules, {"runtime_paths": self.runtime}):
            self.desktop = load_isolated_module("_test_desktop", "desktop.py")
        d = self.desktop
        d.FROZEN = True
        d.APP_PY = None
        d.LOG_PATH = self.data_dir / "desktop-server.log"
        self.process = mock.Mock(name="owned_server", spec=["poll", "terminate", "pid", "returncode"])
        self.process.pid = 12345
        self.process.returncode = None
        self.process.poll.return_value = None
        self.events = []

        def shutdown(_url):
            self.events.append("shutdown")
            self.process.returncode = 0
            self.process.poll.return_value = 0

        self.subprocess = mock.Mock(spec=subprocess)
        self.subprocess.STDOUT = subprocess.STDOUT
        self.subprocess.CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.subprocess.Popen.return_value = self.process
        self.enterContext(mock.patch.object(d, "subprocess", self.subprocess))
        self.enterContext(mock.patch.object(d, "socket", mock.Mock(spec=d.socket)))
        self.urlopen = self.enterContext(mock.patch.object(
            d.urllib.request, "urlopen", side_effect=AssertionError("unexpected network request")
        ))
        self.browser = self.enterContext(mock.patch.object(d, "webbrowser", mock.Mock(spec=d.webbrowser)))
        self.browser.open.return_value = True
        self.enterContext(mock.patch.object(d, "threading", types.SimpleNamespace(
            Event=threading.Event, Thread=mock.Mock(), Timer=mock.Mock(),
        )))
        # Bound accidental shutdown waits as well as preventing real sleeps.
        self.enterContext(mock.patch.object(d, "time", types.SimpleNamespace(
            time=mock.Mock(side_effect=itertools.count(0, 10).__next__), sleep=mock.Mock(),
        )))
        for name in ("set_app_user_model_id", "register_app_user_model_id", "apply_window_icon"):
            self.enterContext(mock.patch.object(d, name))
        self.enterContext(mock.patch.object(d, "find_existing_server", return_value=None))
        self.enterContext(mock.patch.object(d, "find_free_port", return_value=18901))
        self.enterContext(mock.patch.object(d, "wait_health", return_value=(True, "")))
        self.enterContext(mock.patch.object(d, "request_shutdown", side_effect=shutdown))
        self.enterContext(mock.patch.object(sys, "stdin", None))
        self.input_mock = self.enterContext(mock.patch.object(
            builtins, "input", side_effect=AssertionError("input is unavailable in a windowed EXE")
        ))
        self.addCleanup(self.input_mock.assert_not_called)
        self.window = mock.MagicMock(name="webview_window")
        self.webview = types.ModuleType("webview")
        self.webview.create_window = mock.Mock(return_value=self.window)
        self.webview.start = mock.Mock(side_effect=lambda boot, **kwargs: boot())
        self.webview.__path__ = []
        self.guilib = types.ModuleType("webview.guilib")
        self.guilib.initialize = mock.Mock(return_value=types.SimpleNamespace(renderer="edgechromium"))
        self.webview.guilib = self.guilib
        # Fail closed if production adds an unexpected GUI path.
        tk = types.ModuleType("tkinter")
        ttk = types.ModuleType("tkinter.ttk")
        messagebox = types.ModuleType("tkinter.messagebox")
        tk.Tk = mock.Mock(side_effect=AssertionError("unexpected real Tk request"))
        tk.ttk = ttk
        tk.messagebox = messagebox
        messagebox.showerror = mock.Mock()
        self.enterContext(mock.patch.dict(sys.modules, {
            "webview": self.webview,
            "webview.guilib": self.guilib,
            "tkinter": tk,
            "tkinter.ttk": ttk,
            "tkinter.messagebox": messagebox,
        }))
        self.url = "http://127.0.0.1:18901"

    def assert_external_server_untouched(self):
        self.desktop.request_shutdown.assert_not_called()
        self.subprocess.Popen.assert_not_called()
        self.subprocess.run.assert_not_called()
        self.process.terminate.assert_not_called()
        self.desktop.find_free_port.assert_not_called()
        self.assertFalse(self.desktop.LOG_PATH.exists())


class DesktopMainTests(DesktopSandboxCase):
    def setUp(self):
        super().setUp()
        self.enterContext(mock.patch.object(
            self.desktop, "run_browser_mode", side_effect=AssertionError("unexpected browser fallback")
        ))

    def assert_fallback_lifetime(self, stage):
        d = self.desktop
        if stage == "import":
            sys.modules["webview"] = None
            expected_reason = "webview"
        elif stage == "initialize":
            self.guilib.initialize.side_effect = RuntimeError("renderer initialization failed")
            expected_reason = "renderer initialization failed"
        elif stage == "wrong_renderer":
            self.guilib.initialize.return_value.renderer = "mshtml"
            expected_reason = "WebView2"
        elif stage == "create_window":
            self.webview.create_window.side_effect = RuntimeError("create_window failed")
            expected_reason = "create_window failed"
        elif stage == "missing_window":
            self.webview.create_window.return_value = None
            expected_reason = "无法创建桌面窗口"
        elif stage == "start":
            self.webview.start.side_effect = RuntimeError("WebView2 unavailable")
            expected_reason = "WebView2 unavailable"

        def fallback(url, server, reason=""):
            self.events.append("fallback entered")
            self.assertEqual(url, self.url)
            self.assertIs(server, self.process)
            self.assertIn(expected_reason, reason)
            self.assertIsNone(server.poll())
            d.request_shutdown.assert_not_called()
            self.subprocess.run.assert_not_called()
            server.terminate.assert_not_called()
            self.assertFalse(self.subprocess.Popen.call_args.kwargs["stdout"].closed)
            self.events.append("fallback returning")
            return 17

        with mock.patch.object(d, "run_browser_mode", side_effect=fallback) as browser_mode:
            self.assertEqual(d.main(), 17)
        browser_mode.assert_called_once()
        self.assertEqual(self.events, ["fallback entered", "fallback returning", "shutdown"])
        d.request_shutdown.assert_called_once_with(self.url)
        self.assertTrue(self.subprocess.Popen.call_args.kwargs["stdout"].closed)
        self.subprocess.run.assert_not_called()
        self.process.terminate.assert_not_called()

    def test_webview_import_failure_keeps_server_alive_until_fallback_returns(self):
        self.assert_fallback_lifetime("import")

    @unittest.skipUnless(os.name == "nt", "Windows WebView2 preflight")
    def test_webview_initialize_failure_keeps_server_alive_until_fallback_returns(self):
        self.assert_fallback_lifetime("initialize")
        self.guilib.initialize.assert_called_once_with("edgechromium")
        self.webview.create_window.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Windows WebView2 preflight")
    def test_legacy_renderer_uses_browser_fallback_without_creating_window(self):
        self.assert_fallback_lifetime("wrong_renderer")
        self.guilib.initialize.assert_called_once_with("edgechromium")
        self.webview.create_window.assert_not_called()

    def test_webview_create_failure_keeps_server_alive_until_fallback_returns(self):
        self.assert_fallback_lifetime("create_window")
        self.webview.create_window.assert_called_once()
        self.webview.start.assert_not_called()

    def test_webview_missing_window_keeps_server_alive_until_fallback_returns(self):
        self.assert_fallback_lifetime("missing_window")
        self.webview.start.assert_not_called()

    def test_webview_start_failure_keeps_server_alive_until_fallback_returns(self):
        self.assert_fallback_lifetime("start")

    def test_browser_mode_skips_webview_import_and_never_reads_missing_stdin(self):
        real_import = builtins.__import__
        webview_imports = []

        def guarded_import(name, *args, **kwargs):
            if name == "webview":
                webview_imports.append(name)
                raise ImportError("webview must not be imported in browser mode")
            return real_import(name, *args, **kwargs)

        def browser_mode(url, server):
            self.assertEqual(url, self.url)
            self.assertIs(server, self.process)
            self.assertIsNone(server.poll())
            self.desktop.request_shutdown.assert_not_called()
            self.events.append("browser returning")
            return 7

        with mock.patch.object(builtins, "__import__", side_effect=guarded_import):
            with mock.patch.object(self.desktop, "run_browser_mode", side_effect=browser_mode):
                self.assertEqual(self.desktop.main(browser_mode=True), 7)
        self.assertEqual(webview_imports, [])
        self.webview.create_window.assert_not_called()
        self.webview.start.assert_not_called()
        self.guilib.initialize.assert_not_called()
        self.input_mock.assert_not_called()
        self.assertEqual(self.events, ["browser returning", "shutdown"])

    def assert_server_environment(self, frozen):
        d = self.desktop
        d.FROZEN = frozen
        if not frozen:
            d.APP_PY = self.app_dir / "app.py"
            d.APP_PY.write_text("# Test fixture; never executed.\n", encoding="utf-8")
        os.environ["ICLOUDPD_DATA_DIR"] = str(self.base / "stale inherited path")
        os.environ["ICLOUDPD_TEST_SENTINEL"] = "inherited"
        with mock.patch.object(d, "run_browser_mode", return_value=0):
            self.assertEqual(d.main(browser_mode=True), 0)
        self.subprocess.Popen.assert_called_once()
        args, kwargs = self.subprocess.Popen.call_args
        target = d.SERVER_FLAG if frozen else str(d.APP_PY)
        self.assertEqual(args[0], [sys.executable, target, "--no-browser", "--port", "18901"])
        self.assertEqual(kwargs["cwd"], str(self.app_dir))
        self.assertEqual(kwargs["env"]["ICLOUDPD_DATA_DIR"], str(self.data_dir))
        self.assertEqual(kwargs["env"]["PYTHONIOENCODING"], "utf-8")
        self.assertEqual(kwargs["env"]["ICLOUDPD_TEST_SENTINEL"], "inherited")
        self.assertEqual(kwargs["stdout"].name, str(d.LOG_PATH))
        self.assertTrue(kwargs["stdout"].closed)
        self.assertEqual(os.environ["ICLOUDPD_DATA_DIR"], str(self.base / "stale inherited path"))

    def test_frozen_server_gets_selected_data_directory_in_environment(self):
        self.assert_server_environment(frozen=True)

    def test_source_server_gets_selected_data_directory_in_environment(self):
        self.assert_server_environment(frozen=False)

    def test_reused_server_is_not_shutdown_in_browser_mode(self):
        self.desktop.find_existing_server.return_value = self.url
        with mock.patch.object(self.desktop, "run_browser_mode", return_value=0) as fallback:
            self.assertEqual(self.desktop.main(browser_mode=True), 0)
        fallback.assert_called_once_with(self.url, None)
        self.assert_external_server_untouched()

    def test_reused_server_is_not_shutdown_in_native_mode(self):
        self.desktop.find_existing_server.return_value = self.url
        self.enterContext(mock.patch.object(self.desktop, "screen_size", return_value=(800, 600)))
        self.assertEqual(self.desktop.main(), 0)
        self.desktop.wait_health.assert_called_once_with(self.url, None)
        self.assert_external_server_untouched()

    def test_reused_server_is_not_shutdown_after_webview_fallback(self):
        self.desktop.find_existing_server.return_value = self.url
        self.webview.create_window.side_effect = RuntimeError("no native window")
        with mock.patch.object(self.desktop, "run_browser_mode", return_value=1) as fallback:
            self.assertEqual(self.desktop.main(), 1)
        fallback.assert_called_once_with(self.url, None, "no native window")
        self.assert_external_server_untouched()

    def test_owned_server_is_cleaned_up_if_browser_fallback_raises(self):
        with mock.patch.object(self.desktop, "run_browser_mode", side_effect=RuntimeError("Tk failed")):
            with self.assertRaisesRegex(RuntimeError, "Tk failed"):
                self.desktop.main(browser_mode=True)
        self.desktop.request_shutdown.assert_called_once_with(self.url)
        self.assertTrue(self.subprocess.Popen.call_args.kwargs["stdout"].closed)

    def test_initial_and_loaded_windows_fit_small_screens(self):
        self.desktop.find_existing_server.return_value = self.url
        for screen in ((640, 480), (800, 600), (1024, 600), (480, 320)):
            with self.subTest(screen=screen):
                self.webview.create_window.reset_mock()
                self.window.resize.reset_mock()
                with mock.patch.object(self.desktop, "screen_size", return_value=screen):
                    self.assertEqual(self.desktop.main(), 0)
                options = self.webview.create_window.call_args.kwargs
                sizes = [(options["width"], options["height"]), options["min_size"]]
                self.window.resize.assert_called_once()
                sizes.append(self.window.resize.call_args.args)
                for width, height in sizes:
                    self.assertGreater(width, 0)
                    self.assertGreater(height, 0)
                    self.assertLessEqual(width, screen[0])
                    self.assertLessEqual(height, screen[1])
        self.assert_external_server_untouched()


class FakeTclError(Exception):
    pass


class FakeRoot:
    """Virtual-time Tk after queue; it never sleeps or creates a window."""
    def __init__(self):
        self.now = 0
        self.sequence = itertools.count()
        self.queue = []
        self.after_calls = []
        self.widgets = []
        self.variables = []
        self.destroyed = False
        self.destroy_calls = 0

    def title(self, text):
        self.title_text = text

    def resizable(self, *args):
        self.resizable_args = args

    def iconbitmap(self, path):
        self.icon_path = path

    def winfo_screenwidth(self):
        return 800

    def after(self, delay, callback, *args):
        token = next(self.sequence)
        self.after_calls.append((delay, callback))
        heapq.heappush(self.queue, (self.now + delay, token, callback, args))
        return str(token)

    def mainloop(self):
        for _ in range(100):
            if self.destroyed:
                return
            if not self.queue:
                raise AssertionError("fake Tk exhausted its queue before close")
            self.now, _, callback, args = heapq.heappop(self.queue)
            callback(*args)
        raise AssertionError("fake Tk exceeded its bounded callback budget")

    def destroy(self):
        self.destroy_calls += 1
        if self.destroyed:
            raise FakeTclError("already destroyed")
        self.destroyed = True
        self.queue.clear()

    def widget(self, text):
        return next(widget for widget in self.widgets if widget.options.get("text") == text)


class FakeWidget:
    def __init__(self, parent, kind, **options):
        self.root = parent if isinstance(parent, FakeRoot) else parent.root
        self.kind = kind
        self.options = dict(options)
        self.value = ""
        self.root.widgets.append(self)

    def pack(self, **kwargs):
        pass

    def configure(self, **kwargs):
        self.options.update(kwargs)

    def insert(self, index, value):
        self.value = value

    def invoke(self):
        if self.options.get("state") != "disabled":
            return self.options["command"]()


class FakeStringVar:
    def __init__(self, value):
        self.history = [value]

    def set(self, value):
        self.history.append(value)

    def get(self):
        return self.history[-1]


class BrowserModeTests(DesktopSandboxCase):
    def setUp(self):
        super().setUp()
        self.root = FakeRoot()
        self.health_delay = 250
        tk = types.ModuleType("tkinter")
        ttk = types.ModuleType("tkinter.ttk")
        tk.Tk = mock.Mock(return_value=self.root)
        tk.TclError = FakeTclError

        def string_var(value):
            variable = FakeStringVar(value)
            self.root.variables.append(variable)
            return variable

        tk.StringVar = string_var
        for kind in ("Frame", "Label", "Entry", "Button"):
            setattr(ttk, kind, lambda parent, _kind=kind, **kwargs: FakeWidget(parent, _kind, **kwargs))
        tk.ttk = ttk
        self.enterContext(mock.patch.dict(sys.modules, {"tkinter": tk, "tkinter.ttk": ttk}))

        def deferred_thread(*, target, **kwargs):
            thread = mock.Mock()
            thread.start.side_effect = lambda: self.root.after(self.health_delay, target)
            return thread

        self.desktop.threading.Thread.side_effect = deferred_thread

    @property
    def status(self):
        return self.root.variables[0]

    @property
    def open_button(self):
        return self.root.widget("打开浏览器")

    def close_window(self):
        self.root.widget("退出并停止服务").invoke()

    def run_mode(self, server="owned", reason=""):
        process = self.process if server == "owned" else None
        result = self.desktop.run_browser_mode(self.url, process, reason)
        self.assertTrue(self.root.destroyed)
        self.assertEqual(self.root.destroy_calls, 2)  # finally tolerates Tk's second destroy.
        self.desktop.request_shutdown.assert_not_called()  # main owns process cleanup.
        self.subprocess.Popen.assert_not_called()
        self.subprocess.run.assert_not_called()
        self.process.terminate.assert_not_called()
        self.urlopen.assert_not_called()
        self.webview.create_window.assert_not_called()
        return result

    def test_pending_health_reschedules_then_ready_opens_browser_and_can_close(self):
        def inspect_pending():
            self.assertIn("正在启动", self.status.get())
            self.assertEqual(self.open_button.options["state"], "disabled")
            self.open_button.invoke()
            self.browser.open.assert_not_called()

        def inspect_ready():
            self.assertIn("服务已就绪", self.status.get())
            self.assertEqual(self.open_button.options["state"], "normal")
            self.browser.open.assert_called_once_with(self.url)
            entry = next(widget for widget in self.root.widgets if widget.kind == "Entry")
            self.assertEqual(entry.value, self.url)
            self.assertEqual(entry.options["state"], "readonly")
            self.root.widget(f"数据目录：{self.data_dir}")
            self.root.widget("桌面组件不可用，已切换兼容模式。")
            self.close_window()

        self.root.after(150, inspect_pending)
        self.root.after(450, inspect_ready)
        self.assertEqual(self.run_mode(reason="WebView2 unavailable"), 0)
        self.desktop.wait_health.assert_called_once_with(self.url, self.process)
        health_callbacks = [delay for delay, callback in self.root.after_calls if callback.__name__ == "check_health"]
        self.assertGreaterEqual(len(health_callbacks), 3)
        self.assertTrue(all(delay == 100 for delay in health_callbacks))

    def assert_browser_failure_is_visible(self, exception=None):
        self.browser.open.return_value = False
        self.browser.open.side_effect = exception

        def inspect_failure():
            self.assertIn("无法自动打开浏览器", self.status.get())
            self.assertFalse(any("服务已就绪" in value for value in self.status.history))
            self.assertEqual(self.open_button.options["state"], "normal")
            self.browser.open.assert_called_once_with(self.url)
            self.assertIsNone(self.process.poll())
            self.close_window()

        self.root.after(450, inspect_failure)
        # Service readiness is distinct from opening a browser: manual URL use remains possible.
        self.assertEqual(self.run_mode(), 0)

    def test_no_browser_shows_error_instead_of_success_and_can_close(self):
        self.assert_browser_failure_is_visible()

    def test_browser_exception_shows_error_instead_of_success_and_can_close(self):
        self.assert_browser_failure_is_visible(RuntimeError("no browser installed"))

    def test_open_button_can_retry_after_browser_failure(self):
        self.browser.open.side_effect = [False, True]

        def retry_and_close():
            self.assertIn("无法自动打开浏览器", self.status.get())
            self.open_button.invoke()
            self.assertIn("服务已就绪", self.status.get())
            self.assertEqual(self.browser.open.call_args_list, [mock.call(self.url), mock.call(self.url)])
            self.close_window()

        self.root.after(450, retry_and_close)
        self.assertEqual(self.run_mode(), 0)

    def test_health_failure_displays_detail_and_log_and_returns_failure(self):
        self.desktop.wait_health.return_value = (False, "服务进程已退出（exit 7）")

        def inspect_failure():
            self.assertIn("本地服务启动失败", self.status.get())
            self.assertIn("exit 7", self.status.get())
            self.assertIn(str(self.desktop.LOG_PATH), self.status.get())
            self.assertEqual(self.open_button.options["state"], "disabled")
            self.browser.open.assert_not_called()
            self.close_window()

        self.root.after(450, inspect_failure)
        self.assertEqual(self.run_mode(), 1)

    def test_server_exit_after_ready_disables_open_and_returns_failure(self):
        def exit_server():
            self.assertIn("服务已就绪", self.status.get())
            self.process.returncode = 9
            self.process.poll.return_value = 9

        def inspect_exit():
            self.assertIn("本地服务已退出", self.status.get())
            self.assertIn(str(self.desktop.LOG_PATH), self.status.get())
            self.assertEqual(self.open_button.options["state"], "disabled")
            self.browser.open.assert_called_once_with(self.url)
            self.close_window()

        self.root.after(450, exit_server)
        self.root.after(1450, inspect_exit)
        self.assertEqual(self.run_mode(), 1)

    def test_external_server_has_no_owned_process_to_poll_or_stop(self):
        self.root.after(450, self.close_window)
        self.assertEqual(self.run_mode(server=None), 0)
        self.desktop.wait_health.assert_called_once_with(self.url, None)
        self.process.poll.assert_not_called()
        self.browser.open.assert_called_once_with(self.url)

    def test_window_can_close_before_health_finishes(self):
        self.health_delay = 2500
        self.root.after(50, self.close_window)
        self.assertEqual(self.run_mode(), 0)
        self.desktop.wait_health.assert_not_called()
        self.browser.open.assert_not_called()
        self.assertEqual(self.root.queue, [])


if __name__ == "__main__":
    unittest.main()
