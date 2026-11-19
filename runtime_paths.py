from __future__ import annotations

import os
from pathlib import Path
import sys
from tempfile import TemporaryFile

FROZEN = bool(getattr(sys, "frozen", False))
APP_DIR = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR))


def writable_directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    with TemporaryFile(dir=path) as probe:
        probe.write(b"1")
        probe.flush()
    return path


def select_data_dir(app_dir: Path, frozen: bool) -> Path:
    override = os.environ.get("ICLOUDPD_DATA_DIR", "").strip()
    if override:
        path = Path(override).expanduser()
        if not path.is_absolute():
            raise ValueError("ICLOUDPD_DATA_DIR 必须是绝对路径")
        return writable_directory(path)
    portable = app_dir / "data"
    if not frozen:
        return portable
    try:
        return writable_directory(portable)
    except OSError:
        local = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        return writable_directory(local / "iCloudPD Studio" / "data")


DATA_DIR = select_data_dir(APP_DIR, FROZEN)
