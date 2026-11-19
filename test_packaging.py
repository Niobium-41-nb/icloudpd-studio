from __future__ import annotations

import json
import os
from pathlib import Path
import re
import runpy
import shutil
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from types import ModuleType, SimpleNamespace
import unittest
from unittest import mock
from urllib.error import URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent


class PackagingSpecTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.engine = Path(self.temp.name) / "icloudpd.exe"
        self.engine.write_bytes(b"MZ")
        self.modules = {
            name: ModuleType(name)
            for name in ("PyInstaller", "PyInstaller.utils", "PyInstaller.utils.hooks")
        }
        self.modules["PyInstaller.utils.hooks"].collect_all = mock.Mock(return_value=([], [], []))
        self.analysis = SimpleNamespace(pure=["app"], scripts=["desktop"], binaries=["runtime.dll"], datas=["static"])
        self.analyze = mock.Mock(return_value=self.analysis)
        self.exe = mock.Mock()
        self.collect = mock.Mock()
        self.run_spec()

    def run_spec(self):
        with mock.patch.dict(sys.modules, self.modules), mock.patch.dict(os.environ, {"BUILD_ENGINE_PATH": str(self.engine)}):
            runpy.run_path(
                str(ROOT / "build_app.spec"),
                init_globals={
                    "Analysis": self.analyze,
                    "PYZ": mock.Mock(return_value="pyz"),
                    "EXE": self.exe,
                    "COLLECT": self.collect,
                },
            )

    def test_runtime_and_resources_are_embedded_in_executable(self):
        args, kwargs = self.exe.call_args
        self.assertIs(args[2], self.analysis.binaries)
        self.assertIs(args[3], self.analysis.datas)
        self.assertFalse(kwargs.get("exclude_binaries", False))

    def test_build_does_not_distribute_sidecar_runtime(self):
        self.collect.assert_not_called()

    def test_engine_is_embedded_in_resources(self):
        self.assertIn((str(self.engine), "."), self.analyze.call_args.kwargs["datas"])

    def test_missing_engine_stops_build(self):
        self.engine = Path(self.temp.name) / "missing.exe"
        with self.assertRaises(FileNotFoundError):
            self.run_spec()

    def test_embedded_engine_takes_priority_over_sidecar(self):
        import app

        with mock.patch.object(app, "RESOURCE_DIR", Path(self.temp.name)), mock.patch.object(app, "APP_DIR", ROOT):
            with mock.patch.object(Path, "is_file", return_value=True):
                name = "icloudpd.exe" if os.name == "nt" else "icloudpd"
                self.assertEqual(app.bundled_engine_path(), Path(self.temp.name) / name)


@unittest.skipUnless(os.name == "nt", "Windows desktop packaging test")
@unittest.skipUnless(os.environ.get("ICLOUDPD_TEST_EXE"), "Set ICLOUDPD_TEST_EXE to a standalone executable")
class DownloadedExecutableTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix="icloudpd-single-exe-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.package = self.root / "中文 下载目录"
        self.package.mkdir()
        self.exe = self.package / "iCloudPD Studio.exe"
        shutil.copy2(Path(os.environ["ICLOUDPD_TEST_EXE"]).resolve(), self.exe)
        Path(str(self.exe) + ":Zone.Identifier").write_text("[ZoneTransfer]\r\nZoneId=3\r\n", encoding="utf-8")
        self.assertEqual(list(self.package.iterdir()), [self.exe])
        self.data = self.package / "data"
        self.library = self.root / "empty-library"
        self.cookies = self.root / "empty-cookies"
        self.library.mkdir()
        self.cookies.mkdir()
        self.env = {**os.environ, "ICLOUDPD_UI_VERBOSE": "1", "PYTHONIOENCODING": "utf-8"}
        for key in ("ICLOUDPD_BIN", "ICLOUDPD_DATA_DIR", "ICLOUDPD_DESKTOP_TEST_MS", "PYTHONNET_RUNTIME"):
            self.env.pop(key, None)

    def configure(self, data):
        data.mkdir(parents=True, exist_ok=True)
        (data / "config.json").write_text(
            json.dumps({"download_dir": str(self.library), "cookie_dir": str(self.cookies), "username": ""}),
            encoding="utf-8",
        )

    def start(self, args=()):
        self.log = open(self.root / "process.log", "wb")
        self.addCleanup(self.log.close)
        self.proc = subprocess.Popen(
            [str(self.exe), *args], cwd=self.package, env=self.env, stdout=self.log, stderr=subprocess.STDOUT,
        )
        self.addCleanup(self.stop)
        return self.proc

    def stop(self):
        if self.proc.poll() is None:
            subprocess.run(
                ["taskkill", "/PID", str(self.proc.pid), "/T", "/F"],
                capture_output=True, timeout=15, check=True,
            )
            self.proc.wait(timeout=15)

    def wait_ready(self, url=None):
        deadline = time.monotonic() + 100
        while time.monotonic() < deadline:
            self.assertIsNone(self.proc.poll(), (self.root / "process.log").read_text(errors="replace"))
            if url is None:
                path = self.data / "desktop-server.log"
                if path.exists():
                    match = re.search(r"Web UI\s+(http://127\.0\.0\.1:\d+)", path.read_text(encoding="utf-8", errors="replace"))
                    if match:
                        url = match[1]
            if url:
                try:
                    with urlopen(url + "/api/health", timeout=1) as response:
                        self.assertEqual(json.load(response)["version"], "1.0.2")
                    return url
                except (URLError, TimeoutError, ConnectionError):
                    pass
            time.sleep(0.2)
        self.fail("Standalone service did not start within 100 seconds")

    def assert_engine(self, url):
        with urlopen(url + "/api/bootstrap", timeout=30) as response:
            boot = json.load(response)
        self.assertTrue(boot["engine"]["available"], boot["engine"])
        self.assertEqual(boot["engine"]["version"], "1.32.3")
        self.assertEqual(Path(boot["app"]["data_dir"]), self.data)
        self.assertEqual(boot["accounts"], [])
        self.assertEqual(boot["jobs"], [])
        self.assertFalse((self.package / "icloudpd.exe").exists())
        self.assertIn("ZoneId=3", Path(str(self.exe) + ":Zone.Identifier").read_text())

    def check_no_existing_desktop(self):
        for port in range(8765, 8776):
            with socket.socket() as sock:
                sock.settimeout(0.1)
                if sock.connect_ex(("127.0.0.1", port)) == 0:
                    self.skipTest("An existing desktop service must not be reused by the test")

    def test_downloaded_executable_loads_native_window(self):
        self.check_no_existing_desktop()
        self.configure(self.data)
        self.env["ICLOUDPD_DESKTOP_TEST_MS"] = "8000"
        self.start()
        url = self.wait_ready()
        self.assert_engine(url)
        self.assertEqual(self.proc.wait(timeout=100), 0)
        log = (self.data / "desktop-server.log").read_text(encoding="utf-8", errors="replace")
        for request in ("/", "/static/app.js", "/api/bootstrap"):
            self.assertIn(f'GET {request} HTTP/1.1" 200', log)
        self.assertNotIn("Traceback", log)
        self.assertFalse((self.data / "error.log").exists())
        self.assertTrue((self.data / "webview").is_dir())

    def test_missing_clr_uses_browser_mode_and_keeps_service_alive(self):
        self.check_no_existing_desktop()
        self.configure(self.data)
        self.env.update({"PYTHONNET_RUNTIME": "unavailable-runtime", "ICLOUDPD_DESKTOP_TEST_MS": "6000"})
        self.start()
        url = self.wait_ready()
        self.assert_engine(url)
        self.assertEqual(self.proc.wait(timeout=100), 0)
        log = (self.data / "desktop-server.log").read_text(encoding="utf-8", errors="replace")
        self.assertIn("切换浏览器模式", log)
        self.assertFalse((self.data / "webview").exists())
        self.assertFalse((self.data / "error.log").exists())

    def test_unusable_portable_directory_uses_local_app_data(self):
        (self.package / "data").write_text("blocked by a regular file", encoding="utf-8")
        self.env["LOCALAPPDATA"] = str(self.root / "user-data")
        self.data = self.root / "user-data" / "iCloudPD Studio" / "data"
        self.configure(self.data)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        url = f"http://127.0.0.1:{port}"
        self.start(("--server", "--no-browser", "--port", str(port)))
        self.wait_ready(url)
        self.assert_engine(url)
        with urlopen(Request(url + "/api/shutdown", data=b"{}", headers={"Content-Type": "application/json"}), timeout=10):
            pass
        self.assertEqual(self.proc.wait(timeout=20), 0)
        self.assertEqual((self.package / "data").read_text(), "blocked by a regular file")


if __name__ == "__main__":
    unittest.main()
