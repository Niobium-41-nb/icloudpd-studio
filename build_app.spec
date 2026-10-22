# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：把 iCloudPD Studio 打成单个可分发程序。

设计要点：
- 同一个入口承担三种身份（窗口壳 / 本地服务 / icloudpd 引擎），见 desktop.py:entry。
- icloudpd 与 pillow_heif 整包收集：前者是本项目唯一的实际下载引擎，后者决定
  HEIC 缩略图能不能显示，缺任何一个都会让「下载即用」的承诺落空。
- console=False：双击不弹黑框；子进程的 stdout 由 desktop.py 重定向到日志文件。
"""
import os

from PyInstaller.utils.hooks import collect_all

ROOT = os.path.abspath(os.getcwd())

# 调试开关：BUILD_CONSOLE=1 时打包成带控制台的版本，便于看到导入期异常。
CONSOLE = os.environ.get("BUILD_CONSOLE") == "1"


datas = [(os.path.join(ROOT, "static"), "static")]
binaries = []
hiddenimports = ["app"]

# 只收集 pillow_heif：它决定 HEIC 缩略图能否显示。icloudpd 由 build_engine.spec
# 单独打成 icloudpd.exe 与主程序并排放置，不重复塞进本包。
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
    [],
    exclude_binaries=True,
    name="iCloudPD Studio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=CONSOLE,
    disable_windowed_traceback=False,
    icon=os.path.join(ROOT, "static", "app.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="iCloudPD Studio",
)
