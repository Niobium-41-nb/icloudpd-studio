# -*- mode: python ; coding: utf-8 -*-
"""把 icloudpd 打成独立 exe，作为 iCloudPD Studio 的自带下载引擎。"""
import os

from PyInstaller.utils.hooks import collect_all

ROOT = os.path.abspath(os.getcwd())

datas, binaries, hiddenimports = collect_all("icloudpd")

a = Analysis(
    [os.path.join(ROOT, "icloudpd_entry.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "PyQt5", "PyQt6", "PySide2", "PySide6", "gi"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="icloudpd",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)
