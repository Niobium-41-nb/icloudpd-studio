# -*- mode: python ; coding: utf-8 -*-
"""将桌面运行库内嵌到 exe，避免下载解压的 DLL 来源标记阻止 .NET 加载。"""
import os

from PyInstaller.utils.hooks import collect_all

ROOT = os.path.abspath(os.getcwd())

# 调试开关：BUILD_CONSOLE=1 时打包成带控制台的版本，便于看到导入期异常。
CONSOLE = os.environ.get("BUILD_CONSOLE") == "1"


ENGINE_PATH = os.environ.get("BUILD_ENGINE_PATH", os.path.join(ROOT, "dist_engine", "icloudpd.exe"))
if not os.path.isfile(ENGINE_PATH):
    raise FileNotFoundError("请先构建 build_engine.spec，或用 BUILD_ENGINE_PATH 指定 icloudpd.exe")

datas = [(os.path.join(ROOT, "static"), "static"), (os.path.join(ROOT, "LICENSE"), "."), (ENGINE_PATH, ".")]
binaries = []
hiddenimports = ["app"]

# 内嵌独立控制台引擎，保留下载任务的 stdin/stdout 管道。
for package in ("pillow_heif",):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(package)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

a = Analysis(
    [os.path.join(ROOT, "desktop.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # pywebview 启动时会探测所有可用 GUI 后端，PyInstaller 顺着 import 去加载
    # PyQt5/PySide 的钩子；本机并未安装它们（Windows 走 WebView2 + pythonnet），
    # 留着只会让打包在 Qt 插件目录这一步直接失败。
    excludes=[
        "tkinter.test",
        "test",
        "unittest",
        "pydoc_data",
        "PyQt5",
        "PyQt6",
        "PySide2",
        "PySide6",
        "gi",
        "cefpython3",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="iCloudPD Studio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=CONSOLE,
    disable_windowed_traceback=False,
    icon=os.path.join(ROOT, "static", "app.ico"),
)
