#!/usr/bin/env python3
"""
iCloudPD Studio
A local, dependency-free web UI for icloudpd.

Run:
    python app.py
Then open http://127.0.0.1:8765

The server binds to loopback only. Passwords are kept in memory for the
duration of the icloudpd process and are never written to disk or logs.
"""
from __future__ import annotations

import argparse
import codecs
import hashlib
import json
import locale
import mimetypes
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
import webbrowser
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    from PIL import Image, ImageOps
except Exception:  # pragma: no cover - Pillow is optional
    Image = None
    ImageOps = None

try:
    import pillow_heif  # type: ignore

    pillow_heif.register_heif_opener()
except Exception:  # pragma: no cover - optional HEIF support
    pillow_heif = None


APP_NAME = "iCloudPD Studio"
APP_VERSION = "1.0.0"
APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
DATA_DIR = APP_DIR / "data"
JOBS_DIR = DATA_DIR / "jobs"
THUMB_DIR = DATA_DIR / "thumbs"
CONFIG_PATH = DATA_DIR / "config.json"

IMAGE_EXTS = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff",
    ".heic", ".heif", ".avif", ".jxl",
}
VIDEO_EXTS = {
    ".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm", ".3gp", ".mpg", ".mpeg",
}
MEDIA_EXTS = IMAGE_EXTS | VIDEO_EXTS
BROWSER_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".avif"}

MAX_LOG_LINES = 2500
MAX_PERSISTED_LOGS = 700
ANSI_RE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
LOG_LEVEL_RE = re.compile(
    r"^\s*(?:\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[,.]\d+)?\s+)?"
    r"(DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL|FATAL)\b[:\s-]*",
    re.IGNORECASE,
)
TIMESTAMP_PREFIX_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[,.]\d+)?\s+"
)
PROMPT_RE = re.compile(
    r"(?:two[- ]factor|verification|security|6[- ]digit|authentication code|"
    r"mfa code|passcode|password|passphrase|验证码|双重认证|密码).*[:：]\s*$",
    re.IGNORECASE,
)

DEFAULT_CONFIG = {
    "username": "",
    "domain": "com",
    "download_dir": str(Path.home() / "Pictures" / "iCloud Photos"),
    "cookie_dir": str(Path.home() / ".pyicloud"),
    "recent_dirs": [],
}

CONFIG_LOCK = threading.RLock()
JOBS_LOCK = threading.RLock()
MEDIA_LOCK = threading.RLock()
THUMB_SEMAPHORE = threading.BoundedSemaphore(4)

MEDIA_CACHE: dict = {"at": 0.0, "items": [], "stats": {}, "dir": None}
ENGINE_CACHE: dict = {"at": 0.0, "value": None}
ENGINE_LOCK = threading.RLock()
JOBS: dict[str, "Job"] = {}
ACTIVE_JOB_ID: str | None = None
SHUTDOWN_REQUEST = None


# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def json_default(value):
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"not JSON serializable: {type(value)!r}")


def write_json_atomic(path: Path, payload, indent: int | None = 2) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=indent, default=json_default)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def load_json(path: Path, fallback):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return fallback


def human_bytes(num: float) -> str:
    if num is None:
        return "—"
    n = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def strip_ansi(text: str) -> str:
    text = ANSI_RE.sub("", text)
    text = text.replace("\x00", "").replace("\b", "")
    return text


def parse_log_level(text: str) -> str:
    m = LOG_LEVEL_RE.search(text)
    if not m:
        return "info"
    level = m.group(1).upper()
    if level in ("WARN", "WARNING"):
        return "warn"
    if level in ("ERROR", "CRITICAL", "FATAL"):
        return "error"
    if level == "DEBUG":
        return "debug"
    return "info"


def is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except Exception:
        return False


def safe_relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except Exception:
        try:
            return path.relative_to(root).as_posix()
        except Exception:
            return path.name


def normalize_media_ext(path: Path) -> str:
    return path.suffix.lower()


def is_media(path: Path) -> bool:
    return normalize_media_ext(path) in MEDIA_EXTS


def account_key(username: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (username or "").lower())


def load_config() -> dict:
    with CONFIG_LOCK:
        cfg = dict(DEFAULT_CONFIG)
        loaded = load_json(CONFIG_PATH, {})
        if isinstance(loaded, dict):
            for key in DEFAULT_CONFIG:
                if key in loaded:
                    cfg[key] = loaded[key]
        if cfg.get("domain") not in ("com", "cn"):
            cfg["domain"] = "com"
        for key in ("download_dir", "cookie_dir"):
            if not isinstance(cfg.get(key), str) or not cfg.get(key):
                cfg[key] = DEFAULT_CONFIG[key]
        if not isinstance(cfg.get("recent_dirs"), list):
            cfg["recent_dirs"] = []
        return cfg


def save_config(cfg: dict) -> dict:
    with CONFIG_LOCK:
        clean = {}
        for key in DEFAULT_CONFIG:
            clean[key] = cfg.get(key, DEFAULT_CONFIG[key])
        clean["username"] = str(clean.get("username") or "").strip()
        clean["domain"] = clean.get("domain") if clean.get("domain") in ("com", "cn") else "com"
        clean["download_dir"] = str(clean.get("download_dir") or DEFAULT_CONFIG["download_dir"])
        clean["cookie_dir"] = str(clean.get("cookie_dir") or DEFAULT_CONFIG["cookie_dir"])
        clean["recent_dirs"] = [str(p) for p in clean.get("recent_dirs", []) if p][:12]
        write_json_atomic(CONFIG_PATH, clean)
        return clean


def remember_recent_dir(cfg: dict, directory: str) -> None:
    directory = str(directory)
    dirs = [d for d in cfg.get("recent_dirs", []) if d and d != directory]
    dirs.insert(0, directory)
    cfg["recent_dirs"] = dirs[:12]
    save_config(cfg)


def ensure_app_dirs() -> None:
    for folder in (DATA_DIR, JOBS_DIR, THUMB_DIR):
        folder.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# icloudpd engine
# ---------------------------------------------------------------------------


def engine_argv() -> list[str]:
    override = os.environ.get("ICLOUDPD_BIN", "").strip()
    if override:
        if override.lower().endswith(".py"):
            return [sys.executable, override]
        return [override]
    found = shutil.which("icloudpd")
    if found:
        return [found]
    return ["icloudpd"]


def child_env() -> dict:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def decode_output(raw: bytes) -> str:
    for encoding in ("utf-8", locale.getpreferredencoding(False), "gb18030", "latin-1"):
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "replace")


def engine_info(force: bool = False) -> dict:
    with ENGINE_LOCK:
        cached = ENGINE_CACHE.get("value")
        if not force and cached is not None and time.time() - float(ENGINE_CACHE.get("at") or 0) < 300:
            return dict(cached)
        cmd = engine_argv()
        info = {
            "available": False,
            "path": cmd[-1],
            "command": " ".join(cmd),
            "version": None,
            "raw": "",
            "error": None,
            "folder": None,
        }
        try:
            proc = subprocess.run(
                cmd + ["--version"],
                capture_output=True,
                timeout=15,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                env=child_env(),
            )
            raw = (proc.stdout or b"") + (proc.stderr or b"")
            text = decode_output(raw).strip()
            info["raw"] = text
            info["available"] = proc.returncode == 0
            match = re.search(r"version:\s*([0-9][^\s,]+)", text, re.IGNORECASE)
            if match:
                info["version"] = match.group(1)
            elif text:
                info["version"] = text.splitlines()[0][:80]
            if proc.returncode != 0 and not info["error"]:
                info["error"] = text[-500:] or f"exit {proc.returncode}"
            found = shutil.which(cmd[-1]) if not os.path.isabs(cmd[-1]) else cmd[-1]
            if found:
                info["folder"] = str(Path(found).resolve().parent)
        except Exception as exc:
            info["error"] = str(exc)
        ENGINE_CACHE["at"] = time.time()
        ENGINE_CACHE["value"] = dict(info)
        return info


def cookie_profiles(cfg: dict) -> list[dict]:
    cookie_dir = Path(os.path.expandvars(os.path.expanduser(cfg.get("cookie_dir") or "")))
    profiles: dict[str, dict] = {}
    if cookie_dir.is_dir():
        try:
            for entry in cookie_dir.iterdir():
                if not entry.is_file():
                    continue
                name = entry.name
                if name.endswith(".session"):
                    key = name[: -len(".session")]
                    kind = "session"
                elif not name.startswith(".") and not name.endswith((".json", ".tmp")):
                    key = name
                    kind = "cookie"
                else:
                    continue
                item = profiles.setdefault(
                    key,
                    {"key": key, "has_cookie": False, "has_session": False, "updated_at": None},
                )
                if kind == "session":
                    item["has_session"] = True
                else:
                    item["has_cookie"] = True
                try:
                    mtime = entry.stat().st_mtime
                    if not item["updated_at"] or mtime > datetime.fromisoformat(item["updated_at"]).timestamp():
                        item["updated_at"] = datetime.fromtimestamp(mtime).astimezone().isoformat(timespec="seconds")
                except Exception:
                    pass
        except OSError:
            pass
    current = account_key(cfg.get("username", ""))
    result = []
    for item in profiles.values():
        item["matched"] = bool(current) and item["key"] == current
        result.append(item)
    result.sort(key=lambda x: (not x["matched"], x["updated_at"] or ""), reverse=False)
    result.sort(key=lambda x: x["updated_at"] or "", reverse=True)
    return result


# ---------------------------------------------------------------------------
# Media index / thumbnails
# ---------------------------------------------------------------------------


def scan_media(directory: str, force: bool = False) -> tuple[list[dict], dict]:
    directory = str(directory or "")
    with MEDIA_LOCK:
        if (
            not force
            and MEDIA_CACHE.get("dir") == directory
            and time.time() - MEDIA_CACHE.get("at", 0) < 20
        ):
            return list(MEDIA_CACHE["items"]), dict(MEDIA_CACHE["stats"])

    root = Path(os.path.expandvars(os.path.expanduser(directory)))
    items: list[dict] = []
    stats = {
        "count": 0,
        "photos": 0,
        "videos": 0,
        "bytes": 0,
        "latest_at": None,
        "exists": root.is_dir(),
        "path": str(root),
        "scanned_at": now_iso(),
        "truncated": False,
    }
    if root.is_dir():
        skip_dirs = {".thumbnails", "@eaDir", "#recycle", ".Trash", "node_modules"}
        try:
            for current, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if d not in skip_dirs and not d.startswith(".")]
                for filename in filenames:
                    if filename.startswith("."):
                        continue
                    ext = Path(filename).suffix.lower()
                    if ext not in MEDIA_EXTS:
                        continue
                    abs_path = Path(current) / filename
                    try:
                        st = abs_path.stat()
                    except OSError:
                        continue
                    kind = "video" if ext in VIDEO_EXTS else "photo"
                    stats["count"] += 1
                    stats["photos" if kind == "photo" else "videos"] += 1
                    stats["bytes"] += st.st_size
                    mtime_iso = datetime.fromtimestamp(st.st_mtime).astimezone().isoformat(timespec="seconds")
                    if not stats["latest_at"] or mtime_iso > stats["latest_at"]:
                        stats["latest_at"] = mtime_iso
                    if len(items) < 30000:
                        items.append(
                            {
                                "rel": safe_relative(abs_path, root).replace("\\", "/"),
                                "name": filename,
                                "kind": kind,
                                "ext": ext.lstrip("."),
                                "size": st.st_size,
                                "size_label": human_bytes(st.st_size),
                                "modified_at": mtime_iso,
                                "mtime": st.st_mtime,
                                "renderable": ext in BROWSER_IMAGE_EXTS,
                            }
                        )
                    else:
                        stats["truncated"] = True
                if len(items) >= 30000:
                    break
        except OSError:
            pass
    items.sort(key=lambda x: (x["mtime"], x["rel"]), reverse=True)
    stats["bytes_label"] = human_bytes(stats["bytes"])
    with MEDIA_LOCK:
        MEDIA_CACHE.update({"at": time.time(), "items": items, "stats": stats, "dir": directory})
    return list(items), dict(stats)


def thumb_cache_path(abs_path: Path, width: int) -> Path:
    try:
        st = abs_path.stat()
    except OSError:
        st = None
    seed = f"{abs_path.resolve()}|{st.st_mtime_ns if st else 0}|{st.st_size if st else 0}|{width}"
    digest = hashlib.sha1(seed.encode("utf-8", "replace")).hexdigest()
    return THUMB_DIR / f"{digest}.jpg"


def make_image_thumb(src: Path, dest: Path, width: int, quality: int = 82) -> bool:
    if Image is None:
        return False
    try:
        with Image.open(src) as img:
            img = ImageOps.exif_transpose(img)
            if getattr(img, "is_animated", False):
                img.seek(0)
            if img.mode not in ("RGB", "L"):
                if img.mode in ("RGBA", "LA", "P"):
                    background = Image.new("RGB", img.size, (246, 244, 239))
                    converted = img.convert("RGBA")
                    background.paste(converted, mask=converted.split()[-1])
                    img = background
                else:
                    img = img.convert("RGB")
            else:
                img = img.convert("RGB")
            ratio = width / max(img.width, 1)
            height = max(1, int(img.height * ratio))
            if ratio < 1 or len(img.getbands()) > 3:
                img = img.resize((width, height), Image.Resampling.LANCZOS)
            img.save(dest, "JPEG", quality=quality, optimize=True, progressive=True)
        return True
    except Exception:
        return False


def make_video_thumb(src: Path, dest: Path, width: int) -> bool:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False
    try:
        cmd = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            "0.4",
            "-i",
            str(src),
            "-frames:v",
            "1",
            "-vf",
            f"scale={width}:-2",
            "-q:v",
            "3",
            "-y",
            str(dest),
        ]
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return proc.returncode == 0 and dest.is_file() and dest.stat().st_size > 0
    except Exception:
        return False


def get_thumbnail(abs_path: Path, width: int) -> Path | None:
    width = max(64, min(2400, int(width)))
    dest = thumb_cache_path(abs_path, width)
    if dest.is_file() and dest.stat().st_size > 0:
        return dest
    THUMB_DIR.mkdir(parents=True, exist_ok=True)
    acquired = THUMB_SEMAPHORE.acquire(timeout=20)
    if not acquired:
        return None
    try:
        if dest.is_file() and dest.stat().st_size > 0:
            return dest
        tmp = dest.with_name(f".{dest.name}.{threading.get_ident()}.tmp.jpg")
        ext = normalize_media_ext(abs_path)
        ok = False
        if ext in IMAGE_EXTS:
            ok = make_image_thumb(abs_path, tmp, width)
        elif ext in VIDEO_EXTS:
            ok = make_video_thumb(abs_path, tmp, width)
        if ok and tmp.is_file() and tmp.stat().st_size > 0:
            os.replace(tmp, dest)
            return dest
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        return None
    finally:
        THUMB_SEMAPHORE.release()


# ---------------------------------------------------------------------------
# Job model
# ---------------------------------------------------------------------------


class Job:
    def __init__(self, kind: str, username: str, config: dict, title: str):
        self.id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + hashlib.sha1(
            f"{time.time()}-{os.getpid()}-{threading.get_ident()}".encode()
        ).hexdigest()[:4]
        self.kind = kind
        self.username = username
        self.config = config  # sanitized, never contains password
        self.title = title
        self.status = "running"
        self.created_at = now_iso()
        self.started_at = now_iso()
        self.ended_at = None
        self.exit_code = None
        self.error = None
        self.stage = "starting"
        self.prompt = None
        self.awaiting_input = False
        self.stop_requested = False
        self.logs: list[dict] = []
        self.seq = 0
        self.progress = {"current": 0, "total": 0, "downloaded": 0, "skipped": 0, "errors": 0}
        self.command_preview = ""
        self.password: str | None = None
        self.proc: subprocess.Popen | None = None
        self.reader: threading.Thread | None = None
        self.lock = threading.RLock()
        self.last_persist = 0.0
        self._partial = ""

    # -- logs -----------------------------------------------------------
    def add_log(self, text: str, level: str | None = None, prompt: bool = False) -> None:
        text = strip_ansi(text).replace("\r", "").rstrip("\n")
        if not text.strip():
            return
        with self.lock:
            self.seq += 1
            entry = {
                "seq": self.seq,
                "ts": now_iso(),
                "level": level or parse_log_level(text),
                "text": text[:8000],
                "prompt": bool(prompt),
            }
            self.logs.append(entry)
            if len(self.logs) > MAX_LOG_LINES:
                del self.logs[: len(self.logs) - MAX_LOG_LINES]
            self._observe(text, prompt)
        self.persist(throttle=True)

    def _observe(self, text: str, prompt: bool) -> None:
        low = text.lower()
        if prompt:
            self.awaiting_input = True
            self.prompt = text.strip()
            self.stage = "mfa" if ("code" in low or "factor" in low or "验证码" in text) else "password"
            return
        if "authenticating" in low:
            self.stage = "authenticating"
        if "two-factor" in low or "two factor" in low or "mfa" in low or "verification code" in low:
            self.stage = "mfa"
        if re.search(r"\bdownloading\b", low):
            if self.stage not in ("downloading", "finalizing"):
                self.stage = "downloading"
            m = re.search(r"downloading\s+(\d+)\s+(?:of|/)\s+(\d+)", text, re.IGNORECASE)
            if m:
                self.progress["current"] = int(m.group(1))
                self.progress["total"] = int(m.group(2))
        if re.search(r"\bdownloaded\b", low) and "downloading" not in low:
            self.progress["downloaded"] += 1
            if self.progress["total"]:
                self.progress["current"] = max(
                    self.progress["current"], self.progress["downloaded"]
                )
        if "skipping" in low or "already exists" in low:
            self.progress["skipped"] += 1
        if re.search(r"\berror\b", low):
            self.progress["errors"] += 1
            self.error = text[-500:]
            if self.stage not in ("mfa", "password"):
                self.stage = "error"
        if "authentication successful" in low or "authenticated successfully" in low:
            self.stage = "authenticated"
            self.error = None
        if "download complete" in low or "all photos downloaded" in low:
            self.stage = "finalizing"
        if "only print" in low or "would download" in low:
            self.stage = "preview"

    # -- public ---------------------------------------------------------
    def public(self, detail: bool = False, after: int | None = None) -> dict:
        with self.lock:
            total = self.progress.get("total") or 0
            current = self.progress.get("current") or 0
            if self.status == "completed":
                percent = 100
            elif total:
                percent = min(99, max(0, round(current / total * 100)))
            elif self.stage in ("download", "downloading") and self.progress.get("downloaded"):
                percent = min(95, 8 + self.progress["downloaded"] % 88)
            else:
                percent = None
            data = {
                "id": self.id,
                "kind": self.kind,
                "username": self.username,
                "title": self.title,
                "status": self.status,
                "stage": self.stage,
                "created_at": self.created_at,
                "started_at": self.started_at,
                "ended_at": self.ended_at,
                "exit_code": self.exit_code,
                "error": self.error,
                "config": self.config,
                "command_preview": self.command_preview,
                "progress": dict(self.progress),
                "percent": percent,
                "awaiting_input": self.awaiting_input,
                "prompt": self.prompt,
                "log_seq": self.seq,
                "stop_requested": self.stop_requested,
            }
            if detail:
                start = 0 if after is None else max(0, int(after))
                data["logs"] = [line for line in self.logs if line["seq"] > start]
                data["log_seq"] = self.seq
            return data

    # -- persistence ----------------------------------------------------
    def persist(self, throttle: bool = False) -> None:
        now = time.time()
        if throttle and now - self.last_persist < 1.5:
            return
        self.last_persist = now
        try:
            payload = self.public(detail=False)
            with self.lock:
                payload["logs"] = self.logs[-MAX_PERSISTED_LOGS:]
            write_json_atomic(JOBS_DIR / f"{self.id}.json", payload)
        except Exception:
            pass

    # -- process --------------------------------------------------------
    def attach_process(self, proc: subprocess.Popen, preview: str) -> None:
        with self.lock:
            self.proc = proc
            self.command_preview = preview
        self.reader = threading.Thread(target=self._read_output, name=f"job-{self.id}", daemon=True)
        self.reader.start()

    def _read_output(self) -> None:
        stream = self.proc.stdout if self.proc else None
        if stream is None:
            return
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        buffer = ""
        try:
            while True:
                raw = stream.read(1)
                if not raw:
                    break
                char = decoder.decode(raw)
                if not char:
                    continue
                buffer += char
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    self.add_log(line)
                if "\r" in buffer:
                    parts = buffer.split("\r")
                    buffer = parts[-1]
                    for part in parts[:-1]:
                        self.add_log(part)
                if buffer and len(buffer) < 4000:
                    candidate = strip_ansi(buffer).strip()
                    if candidate and PROMPT_RE.search(candidate):
                        self.add_log(buffer, prompt=True)
                        buffer = ""
                if len(buffer) > 8000:
                    self.add_log(buffer[:8000])
                    buffer = buffer[8000:]
        except Exception as exc:
            self.add_log(f"[ui] 读取输出时出错: {exc}", level="error")
        finally:
            try:
                buffer += decoder.decode(b"", final=True)
            except Exception:
                pass
            if buffer.strip():
                self.add_log(buffer)
            self._finish()

    def _finish(self) -> None:
        with self.lock:
            if self.proc is None:
                return
            try:
                code = self.proc.wait(timeout=5)
            except Exception:
                code = self.proc.poll()
            self.exit_code = code
            self.awaiting_input = False
            self.prompt = None
            if self.stop_requested:
                self.status = "stopped"
                self.stage = "stopped"
            elif code == 0:
                self.status = "completed"
                self.stage = "completed"
                self.progress["current"] = self.progress["total"] or self.progress["downloaded"]
            else:
                self.status = "failed"
                self.stage = "failed"
                if not self.error:
                    self.error = f"icloudpd 退出码 {code}"
            self.ended_at = now_iso()
            self.password = None
        MEDIA_CACHE["at"] = 0
        self.add_log(
            "[ui] 任务完成" if self.status == "completed" else f"[ui] 任务结束：{self.status}",
            level="success" if self.status == "completed" else ("warn" if self.status == "stopped" else "error"),
        )
        self.persist(throttle=False)
        global ACTIVE_JOB_ID
        with JOBS_LOCK:
            if ACTIVE_JOB_ID == self.id:
                ACTIVE_JOB_ID = None

    def send_input(self, text: str) -> bool:
        with self.lock:
            proc = self.proc
            if not proc or proc.poll() is not None:
                return False
            try:
                assert proc.stdin is not None
                proc.stdin.write((text + "\n").encode("utf-8"))
                proc.stdin.flush()
                self.awaiting_input = False
                self.prompt = None
                hidden = "验证码" if self.stage == "mfa" else "输入"
                self.add_log(f"[ui] 已提交{hidden}（内容已隐藏）", level="system")
                return True
            except Exception as exc:
                self.add_log(f"[ui] 提交失败: {exc}", level="error")
                return False

    def stop(self) -> None:
        with self.lock:
            if self.status != "running" or not self.proc:
                return
            self.stop_requested = True
            pid = self.proc.pid
        self.add_log("[ui] 正在停止任务…", level="warn")
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    capture_output=True,
                    timeout=15,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            else:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=3)
                except Exception:
                    self.proc.kill()
        except Exception as exc:
            self.add_log(f"[ui] 停止失败: {exc}", level="error")


# ---------------------------------------------------------------------------
# Job construction
# ---------------------------------------------------------------------------


def build_args(
    cfg: dict,
    username: str,
    password: str | None,
    directory: str | None,
    options: dict,
    auth_only: bool = False,
    list_albums: bool = False,
) -> tuple[list[str], str]:
    args = engine_argv().copy()
    safe_args = args.copy()
    domain = options.get("domain") or cfg.get("domain") or "com"
    args += ["--domain", "cn" if domain == "cn" else "com"]
    safe_args += ["--domain", "cn" if domain == "cn" else "com"]
    args += ["--username", username]
    safe_args += ["--username", username]
    if password:
        args += ["--password", password]
        safe_args += ["--password", "••••••••"]
    if list_albums:
        args += ["--list-albums"]
        safe_args += ["--list-albums"]
    if auth_only:
        args += ["--auth-only"]
        safe_args += ["--auth-only"]
    if directory:
        args += ["--directory", directory]
        safe_args += ["--directory", directory]
    if not list_albums:
        args += ["--no-progress-bar", "--log-level", "info"]
        safe_args += ["--no-progress-bar", "--log-level", "info"]
    if options.get("preview"):
        args.append("--only-print-filenames")
        safe_args.append("--only-print-filenames")
    if options.get("dry_run"):
        args.append("--dry-run")
        safe_args.append("--dry-run")
    if options.get("skip_photos"):
        args.append("--skip-photos")
        safe_args.append("--skip-photos")
    if options.get("skip_videos"):
        args.append("--skip-videos")
        safe_args.append("--skip-videos")
    if options.get("skip_live_photos"):
        args.append("--skip-live-photos")
        safe_args.append("--skip-live-photos")
    if options.get("size") in ("original", "medium", "thumb", "adjusted", "alternative"):
        args += ["--size", options["size"]]
        safe_args += ["--size", options["size"]]
    if options.get("recent"):
        args += ["--recent", str(int(options["recent"]))]
        safe_args += ["--recent", str(int(options["recent"]))]
    if options.get("until_found"):
        args += ["--until-found", str(int(options["until_found"]))]
        safe_args += ["--until-found", str(int(options["until_found"]))]
    for album in options.get("albums") or []:
        if album:
            args += ["--album", str(album)]
            safe_args += ["--album", str(album)]
    if options.get("folder_structure"):
        args += ["--folder-structure", str(options["folder_structure"])]
        safe_args += ["--folder-structure", str(options["folder_structure"])]
    if options.get("file_match_policy") in ("name-size-dedup-with-suffix", "name-id7"):
        args += ["--file-match-policy", options["file_match_policy"]]
        safe_args += ["--file-match-policy", options["file_match_policy"]]
    for flag in (
        "xmp_sidecar",
        "set_exif_datetime",
        "force_size",
        "keep_unicode_in_filenames",
        "delete_after_download",
        "auto_delete",
    ):
        if options.get(flag):
            args.append("--" + flag.replace("_", "-"))
            safe_args.append("--" + flag.replace("_", "-"))
    for key, flag in (
        ("skip_created_before", "--skip-created-before"),
        ("skip_created_after", "--skip-created-after"),
    ):
        if options.get(key):
            args += [flag, str(options[key])]
            safe_args += [flag, str(options[key])]
    if options.get("keep_icloud_recent_days") is not None and str(options.get("keep_icloud_recent_days")) != "":
        args += ["--keep-icloud-recent-days", str(int(options["keep_icloud_recent_days"]))]
        safe_args += ["--keep-icloud-recent-days", str(int(options["keep_icloud_recent_days"]))]
    if options.get("live_photo_mov_filename_policy") in ("suffix", "original"):
        args += ["--live-photo-mov-filename-policy", options["live_photo_mov_filename_policy"]]
        safe_args += ["--live-photo-mov-filename-policy", options["live_photo_mov_filename_policy"]]
    if options.get("align_raw") in ("as-is", "original", "alternative"):
        args += ["--align-raw", options["align_raw"]]
        safe_args += ["--align-raw", options["align_raw"]]

    def quote(part: str) -> str:
        return f'"{part}"' if " " in part else part

    preview = " ".join(quote(part) for part in safe_args)
    return args, preview


def start_job(kind: str, username: str, password: str | None, directory: str | None, options: dict) -> Job:
    global ACTIVE_JOB_ID
    with JOBS_LOCK:
        if ACTIVE_JOB_ID and ACTIVE_JOB_ID in JOBS:
            active = JOBS[ACTIVE_JOB_ID]
            if active.status == "running":
                raise RuntimeError("已有任务正在运行，请等待完成或先停止它。")
        cfg = load_config()
        if kind == "auth":
            title = "登录 iCloud"
            folder = None
        elif options.get("preview"):
            title = "预览待下载清单"
            folder = directory
        else:
            title = "同步 iCloud 照片"
            folder = directory
        args, preview = build_args(
            cfg,
            username,
            password,
            folder,
            options,
            auth_only=(kind == "auth"),
        )
        sanitized = {
            key: value
            for key, value in options.items()
            if key not in ("password",) and value not in (None, "", [], False)
        }
        sanitized["directory"] = folder
        sanitized["domain"] = options.get("domain") or cfg.get("domain") or "com"
        job = Job(kind, username, sanitized, title)
        if options.get("preview"):
            job.stage = "preview"
        job.password = password
        JOBS[job.id] = job
        ACTIVE_JOB_ID = job.id
        job.persist()

    try:
        proc = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            cwd=str(APP_DIR),
            env=child_env(),
        )
    except Exception as exc:
        with JOBS_LOCK:
            ACTIVE_JOB_ID = None
        job.status = "failed"
        job.stage = "failed"
        job.error = str(exc)
        job.ended_at = now_iso()
        job.add_log(f"[ui] 无法启动 icloudpd: {exc}", level="error")
        job.persist()
        raise
    job.attach_process(proc, preview)
    job.add_log(f"[ui] 已启动 icloudpd · {preview}", level="system")
    return job


def load_jobs() -> None:
    ensure_app_dirs()
    for path in sorted(JOBS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:80]:
        payload = load_json(path, None)
        if not isinstance(payload, dict) or not payload.get("id"):
            continue
        try:
            job = Job(
                payload.get("kind", "sync"),
                payload.get("username", ""),
                payload.get("config") or {},
                payload.get("title") or "同步任务",
            )
            job.id = payload["id"]
            job.status = payload.get("status", "completed")
            job.stage = payload.get("stage", job.status)
            job.created_at = payload.get("created_at") or job.created_at
            job.started_at = payload.get("started_at") or job.started_at
            job.ended_at = payload.get("ended_at")
            job.exit_code = payload.get("exit_code")
            job.error = payload.get("error")
            job.progress = payload.get("progress") or job.progress
            job.command_preview = payload.get("command_preview") or ""
            logs = payload.get("logs") or []
            job.logs = [line for line in logs if isinstance(line, dict)]
            job.seq = max([line.get("seq", 0) for line in job.logs] + [payload.get("log_seq", 0)])
            job.last_persist = 0
            if job.status in ("running", "queued"):
                job.status = "interrupted"
                job.stage = "interrupted"
                job.error = "服务已重启，任务被中断"
            JOBS[job.id] = job
        except Exception:
            continue


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------


def disk_info(path: str) -> dict:
    try:
        probe = Path(os.path.expandvars(os.path.expanduser(path or ".")))
        while not probe.exists() and probe.parent != probe:
            probe = probe.parent
        usage = shutil.disk_usage(str(probe))
        return {
            "ok": True,
            "path": str(probe),
            "total": usage.total,
            "used": usage.used,
            "free": usage.free,
            "total_label": human_bytes(usage.total),
            "used_label": human_bytes(usage.used),
            "free_label": human_bytes(usage.free),
            "percent": round(usage.used / usage.total * 100) if usage.total else 0,
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def filesystem_drives() -> list[dict]:
    drives = []
    if os.name == "nt":
        for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
            candidate = f"{letter}:\\"
            if os.path.exists(candidate):
                drives.append(
                    {
                        "name": f"{letter}:",
                        "path": candidate,
                        "kind": "drive",
                    }
                )
    else:
        drives.append({"name": "/", "path": "/", "kind": "drive"})
    return drives


def app_stats(cfg: dict) -> dict:
    items, stats = scan_media(cfg.get("download_dir", ""))
    disk = None
    try:
        target = Path(os.path.expandvars(os.path.expanduser(cfg.get("download_dir") or ".")))
        probe = target
        while not probe.exists() and probe.parent != probe:
            probe = probe.parent
        usage = shutil.disk_usage(str(probe))
        disk = {
            "total": usage.total,
            "used": usage.used,
            "free": usage.free,
            "total_label": human_bytes(usage.total),
            "used_label": human_bytes(usage.used),
            "free_label": human_bytes(usage.free),
            "percent": round(usage.used / usage.total * 100) if usage.total else 0,
        }
    except Exception:
        pass
    latest = stats.get("latest_at")
    return {
        "library": stats,
        "disk": disk,
        "latest_items": items[:3],
        "recent_jobs": [job.public() for job in sorted(JOBS.values(), key=lambda j: j.created_at, reverse=True)[:6]],
    }


def bootstrap_payload() -> dict:
    cfg = load_config()
    engine = engine_info()
    stats = app_stats(cfg)
    jobs = sorted(JOBS.values(), key=lambda j: j.created_at, reverse=True)
    return {
        "app": {
            "name": APP_NAME,
            "version": APP_VERSION,
            "platform": sys.platform,
            "python": sys.version.split()[0],
            "data_dir": str(DATA_DIR),
            "now": now_iso(),
            "host": "127.0.0.1",
        },
        "engine": engine,
        "config": cfg,
        "accounts": cookie_profiles(cfg),
        "stats": stats,
        "jobs": [job.public() for job in jobs[:30]],
        "active_job_id": ACTIVE_JOB_ID,
    }


def album_list(username: str, domain: str) -> dict:
    cfg = load_config()
    args, preview = build_args(
        cfg,
        username,
        None,
        None,
        {"domain": domain},
        list_albums=True,
    )
    args += ["--password-provider", "parameter"]
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            timeout=45,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            cwd=str(APP_DIR),
            env=child_env(),
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "读取专辑超时，请确认网络和 iCloud 会话状态。", "albums": []}
    raw = decode_output((proc.stdout or b"") + (proc.stderr or b""))
    lines = [strip_ansi(line).strip() for line in raw.replace("\r", "\n").split("\n")]
    albums: list[str] = []
    for line in lines:
        clean = TIMESTAMP_PREFIX_RE.sub("", line)
        clean = LOG_LEVEL_RE.sub("", clean).strip()
        if not clean:
            continue
        low = clean.lower()
        if any(
            token in low
            for token in (
                "processing user",
                "authenticating",
                "authentication required",
                "traceback",
                "available albums",
                "listing albums",
                "request",
                "response",
                "cookies",
                "headers",
                "url:",
            )
        ):
            continue
        if clean.startswith(("{", "}", '"')):
            continue
        if proc.returncode != 0 and ("error" in low or "exception" in low):
            continue
        if "," in clean and not clean.startswith("--"):
            parts = [part.strip() for part in clean.split(",")]
        else:
            parts = [clean]
        for part in parts:
            part = re.sub(r"^[-*•]\s*", "", part).strip()
            if not part or len(part) > 160:
                continue
            if part not in albums:
                albums.append(part)
    if proc.returncode != 0:
        message = ""
        for line in reversed(lines):
            if "error" in line.lower() or "authentication" in line.lower():
                message = TIMESTAMP_PREFIX_RE.sub("", line)
                break
        return {
            "ok": False,
            "albums": albums,
            "error": message or "无法读取专辑。请先在 UI 中登录 iCloud。",
            "raw": raw[-2500:],
        }
    return {"ok": True, "albums": albums, "error": None, "raw": raw[-2500:] if not albums else ""}


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------


class StudioServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
            return
        super().handle_error(request, client_address)


class Handler(BaseHTTPRequestHandler):
    server_version = f"iCloudPDStudio/{APP_VERSION}"
    protocol_version = "HTTP/1.1"

    # -- plumbing -------------------------------------------------------
    def log_message(self, fmt, *args):
        if os.environ.get("ICLOUDPD_UI_VERBOSE"):
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _send(self, status: int, body: bytes, content_type: str, extra: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        if extra:
            for key, value in extra.items():
                self.send_header(key, value)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def _json(self, payload, status: int = 200):
        body = json.dumps(payload, ensure_ascii=False, default=json_default).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8", {"Cache-Control": "no-store"})

    def _error(self, message: str, status: int = 400, **extra):
        payload = {"ok": False, "error": message}
        payload.update(extra)
        self._json(payload, status)

    def _read_json(self, limit: int = 1_500_000) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > limit:
            raise ValueError("请求体过大")
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            raise ValueError(f"JSON 解析失败: {exc}")
        if not isinstance(data, dict):
            raise ValueError("请求体必须是 JSON 对象")
        return data

    def _safe_static(self, url_path: str) -> Path | None:
        rel = url_path[len("/static/") :] if url_path.startswith("/static/") else url_path.lstrip("/")
        if not rel or rel == "index.html":
            rel = "index.html"
        candidate = (STATIC_DIR / rel).resolve()
        if not is_within(candidate, STATIC_DIR) or not candidate.is_file():
            return None
        return candidate

    def _media_path(self, rel: str) -> Path | None:
        cfg = load_config()
        root = Path(os.path.expandvars(os.path.expanduser(cfg.get("download_dir") or "")))
        if not root.is_dir():
            return None
        try:
            candidate = (root / rel).resolve() if not os.path.isabs(rel) else Path(rel).resolve()
        except Exception:
            return None
        if not is_within(candidate, root) or not candidate.is_file():
            return None
        if not is_media(candidate):
            return None
        return candidate

    # -- routes ---------------------------------------------------------
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)
        try:
            if path == "/api/bootstrap":
                return self._json({"ok": True, **bootstrap_payload()})
            if path == "/api/jobs":
                jobs = sorted(JOBS.values(), key=lambda j: j.created_at, reverse=True)
                return self._json(
                    {
                        "ok": True,
                        "jobs": [job.public() for job in jobs[:40]],
                        "active_job_id": ACTIVE_JOB_ID,
                    }
                )
            m = re.match(r"^/api/jobs/([0-9A-Za-z_-]+)$", path)
            if m:
                job = JOBS.get(m.group(1))
                if not job:
                    return self._error("任务不存在", 404)
                after = query.get("after", [None])[0]
                after_val = int(after) if after not in (None, "") else None
                return self._json({"ok": True, "job": job.public(detail=True, after=after_val)})
            if path == "/api/gallery":
                cfg = load_config()
                items, stats = scan_media(cfg.get("download_dir", ""), force=query.get("refresh") == ["1"])
                kind = (query.get("kind", ["all"])[0] or "all").lower()
                q = (query.get("q", [""])[0] or "").strip().lower()
                sort = query.get("sort", ["new"])[0]
                if kind in ("photo", "video"):
                    items = [item for item in items if item["kind"] == kind]
                if q:
                    items = [item for item in items if q in item["name"].lower() or q in item["rel"].lower()]
                if sort == "old":
                    items.sort(key=lambda x: (x["mtime"], x["rel"]))
                elif sort == "name":
                    items.sort(key=lambda x: x["name"].lower())
                elif sort == "size":
                    items.sort(key=lambda x: x["size"], reverse=True)
                try:
                    offset = max(0, int(query.get("offset", ["0"])[0]))
                    limit = max(1, min(200, int(query.get("limit", ["60"])[0])))
                except Exception:
                    offset, limit = 0, 60
                page = items[offset : offset + limit]
                for item in page:
                    item["thumb_url"] = "/api/thumb?w=720&path=" + urllib.parse.quote(item["rel"])
                    item["preview_url"] = (
                        "/api/media?path=" + urllib.parse.quote(item["rel"])
                        if item["renderable"]
                        else "/api/thumb?w=1800&path=" + urllib.parse.quote(item["rel"])
                    )
                    item["media_url"] = "/api/media?path=" + urllib.parse.quote(item["rel"])
                return self._json(
                    {
                        "ok": True,
                        "items": page,
                        "total": len(items),
                        "offset": offset,
                        "limit": limit,
                        "has_more": offset + len(page) < len(items),
                        "stats": stats,
                    }
                )
            if path == "/api/fs":
                return self._json({"ok": True, **self._list_fs(query.get("path", [""])[0])})
            if path == "/api/disk":
                return self._json(disk_info(query.get("path", [""])[0]))
            if path == "/api/thumb":
                return self._serve_thumb(query)
            if path == "/api/media":
                return self._serve_media(query)
            if path == "/api/health":
                return self._json({"ok": True, "name": APP_NAME, "version": APP_VERSION, "now": now_iso()})
            if path == "/" or path == "/index.html":
                return self._serve_index()
            if path == "/favicon.ico":
                return self._serve_static("favicon.svg", "image/svg+xml")
            if path.startswith("/static/"):
                return self._serve_static(path[len("/static/") :])
            return self._error("Not found", 404)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return
        except Exception as exc:
            traceback.print_exc()
            return self._error(f"服务器错误: {exc}", 500)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        try:
            body = self._read_json()
            if path == "/api/settings":
                return self._save_settings(body)
            if path == "/api/jobs":
                return self._create_job(body)
            m = re.match(r"^/api/jobs/([0-9A-Za-z_-]+)/stop$", path)
            if m:
                job = JOBS.get(m.group(1))
                if not job:
                    return self._error("任务不存在", 404)
                job.stop()
                return self._json({"ok": True, "job": job.public()})
            m = re.match(r"^/api/jobs/([0-9A-Za-z_-]+)/input$", path)
            if m:
                job = JOBS.get(m.group(1))
                if not job:
                    return self._error("任务不存在", 404)
                text = str(body.get("text") or "").strip()
                if not text:
                    return self._error("输入不能为空")
                ok = job.send_input(text)
                if not ok:
                    return self._error("任务已结束，无法提交输入", 409)
                return self._json({"ok": True, "job": job.public()})
            if path == "/api/auth":
                return self._create_auth(body)
            if path == "/api/albums":
                username = str(body.get("username") or "").strip()
                domain = str(body.get("domain") or "com")
                if not username:
                    return self._error("请先填写 Apple ID")
                return self._json({"ok": True, **album_list(username, domain)})
            if path == "/api/reveal":
                return self._reveal(body)
            if path == "/api/open":
                return self._open_file(body)
            if path == "/api/fs/mkdir":
                return self._mkdir(body)
            if path == "/api/jobs/clear":
                return self._clear_jobs()
            if path == "/api/shutdown":
                return self._shutdown()
            return self._error("Not found", 404)
        except ValueError as exc:
            return self._error(str(exc), 400)
        except RuntimeError as exc:
            return self._error(str(exc), 409)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return
        except Exception as exc:
            traceback.print_exc()
            return self._error(f"服务器错误: {exc}", 500)

    # -- API implementations -------------------------------------------
    def _serve_index(self):
        index = STATIC_DIR / "index.html"
        if not index.is_file():
            return self._error("UI 尚未安装：缺少 static/index.html", 500)
        body = index.read_bytes()
        self._send(
            200,
            body,
            "text/html; charset=utf-8",
            {"Cache-Control": "no-cache, no-store, must-revalidate"},
        )

    def _serve_static(self, rel: str, content_type: str | None = None):
        rel = urllib.parse.unquote(rel.split("?")[0])
        candidate = self._safe_static(rel)
        if not candidate:
            return self._error("Not found", 404)
        ctype = content_type or mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "image/svg+xml"):
            ctype += "; charset=utf-8"
        self._send(200, candidate.read_bytes(), ctype, {"Cache-Control": "no-cache"})

    def _save_settings(self, body: dict):
        cfg = load_config()
        if "username" in body:
            cfg["username"] = str(body.get("username") or "").strip()
        if "domain" in body and body.get("domain") in ("com", "cn"):
            cfg["domain"] = body["domain"]
        if "download_dir" in body and str(body.get("download_dir") or "").strip():
            cfg["download_dir"] = str(body["download_dir"]).strip()
        if "cookie_dir" in body and str(body.get("cookie_dir") or "").strip():
            cfg["cookie_dir"] = str(body["cookie_dir"]).strip()
        cfg = save_config(cfg)
        MEDIA_CACHE["at"] = 0
        return self._json({"ok": True, "config": cfg})

    def _create_auth(self, body: dict):
        username = str(body.get("username") or "").strip()
        password = str(body.get("password") or "")
        domain = str(body.get("domain") or "com")
        if not username:
            return self._error("请填写 Apple ID")
        if not password:
            return self._error("请填写 iCloud 密码")
        cfg = load_config()
        cfg["username"] = username
        cfg["domain"] = "cn" if domain == "cn" else "com"
        save_config(cfg)
        job = start_job(
            "auth",
            username,
            password,
            None,
            {"domain": cfg["domain"]},
        )
        return self._json({"ok": True, "job": job.public()}, 201)

    def _create_job(self, body: dict):
        username = str(body.get("username") or "").strip()
        password = str(body.get("password") or "") or None
        directory = str(body.get("directory") or "").strip()
        options = body.get("options") if isinstance(body.get("options"), dict) else {}
        if not username:
            return self._error("请填写 Apple ID")
        if not directory:
            return self._error("请选择下载目录")
        if not os.path.isabs(directory):
            return self._error("下载目录必须是绝对路径")
        if options.get("skip_photos") and options.get("skip_videos"):
            return self._error("不能同时跳过照片和视频")
        if options.get("delete_after_download") and options.get("auto_delete"):
            return self._error("“下载后删除”与“自动清理最近删除”不能同时启用")
        try:
            Path(directory).mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            return self._error(f"无法创建下载目录: {exc}")
        cfg = load_config()
        cfg["username"] = username
        cfg["domain"] = "cn" if options.get("domain") == "cn" else "com"
        save_config(cfg)
        remember_recent_dir(cfg, directory)
        job = start_job("sync", username, password, directory, options)
        MEDIA_CACHE["at"] = 0
        return self._json({"ok": True, "job": job.public()}, 201)

    def _list_fs(self, raw_path: str):
        home = Path.home()
        drives = filesystem_drives()
        shortcuts = [
            {"name": "下载", "path": str(home / "Downloads"), "kind": "shortcut"},
            {"name": "图片", "path": str(home / "Pictures"), "kind": "shortcut"},
            {"name": "桌面", "path": str(home / "Desktop"), "kind": "shortcut"},
            {"name": "用户目录", "path": str(home), "kind": "shortcut"},
        ]
        if not raw_path:
            return {
                "path": "",
                "parent": None,
                "entries": [],
                "drives": drives,
                "shortcuts": [s for s in shortcuts if os.path.isdir(s["path"])],
                "is_root": True,
            }
        target = Path(os.path.expandvars(os.path.expanduser(raw_path)))
        try:
            target = target.resolve()
        except Exception:
            return {
                "path": raw_path,
                "parent": None,
                "entries": [],
                "drives": drives,
                "error": "无效路径",
                "is_root": False,
            }
        if not target.is_dir():
            target = target.parent
        entries = []
        try:
            for entry in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
                if not entry.is_dir():
                    continue
                name = entry.name
                if name.lower() in ("$recycle.bin", "system volume information"):
                    continue
                entries.append(
                    {
                        "name": name,
                        "path": str(entry),
                        "kind": "folder",
                        "hidden": name.startswith("."),
                    }
                )
        except PermissionError:
            return {
                "path": str(target),
                "parent": str(target.parent) if target.parent != target else None,
                "entries": [],
                "drives": drives,
                "shortcuts": [s for s in shortcuts if os.path.isdir(s["path"])],
                "error": "没有权限读取此目录",
                "is_root": target.parent == target,
            }
        except OSError as exc:
            return {
                "path": str(target),
                "parent": str(target.parent) if target.parent != target else None,
                "entries": [],
                "drives": drives,
                "shortcuts": [s for s in shortcuts if os.path.isdir(s["path"])],
                "error": str(exc),
                "is_root": target.parent == target,
            }
        parent = str(target.parent) if target.parent != target else None
        return {
            "path": str(target),
            "parent": parent,
            "entries": entries,
            "drives": drives,
            "shortcuts": [s for s in shortcuts if os.path.isdir(s["path"])],
            "is_root": parent is None,
        }

    def _reveal(self, body: dict):
        cfg = load_config()
        root = Path(os.path.expandvars(os.path.expanduser(cfg.get("download_dir") or "")))
        rel = str(body.get("path") or "")
        target = (root / rel).resolve() if rel else root.resolve()
        if not is_within(target, root) or not target.exists():
            return self._error("路径不在下载目录内或不存在", 400)
        try:
            if os.name == "nt":
                if target.is_dir():
                    subprocess.Popen(["explorer", str(target)], close_fds=True)
                else:
                    subprocess.Popen(["explorer", "/select,", str(target)], close_fds=True)
            elif sys.platform == "darwin":
                if target.is_dir():
                    subprocess.Popen(["open", str(target)])
                else:
                    subprocess.Popen(["open", "-R", str(target)])
            else:
                subprocess.Popen(["xdg-open", str(target if target.is_dir() else target.parent)])
            return self._json({"ok": True})
        except Exception as exc:
            return self._error(f"无法打开资源管理器: {exc}", 500)

    def _open_file(self, body: dict):
        cfg = load_config()
        root = Path(os.path.expandvars(os.path.expanduser(cfg.get("download_dir") or "")))
        rel = str(body.get("path") or "")
        target = (root / rel).resolve() if rel else None
        if not target or not is_within(target, root) or not target.is_file():
            return self._error("文件不存在或不在下载目录内", 400)
        try:
            if os.name == "nt":
                os.startfile(str(target))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(target)])
            else:
                subprocess.Popen(["xdg-open", str(target)])
            return self._json({"ok": True})
        except Exception as exc:
            return self._error(f"无法打开文件: {exc}", 500)

    def _mkdir(self, body: dict):
        raw = str(body.get("path") or "").strip()
        name = str(body.get("name") or "").strip()
        if not raw or not name:
            return self._error("路径和名称不能为空")
        if not os.path.isabs(raw):
            return self._error("请先进入一个磁盘或文件夹")
        if any(ch in name for ch in '\\/:*?"<>|') or name in (".", ".."):
            return self._error("文件夹名称包含非法字符")
        target = Path(raw).resolve() / name
        try:
            target.mkdir(parents=False, exist_ok=False)
            return self._json({"ok": True, "path": str(target)})
        except Exception as exc:
            return self._error(f"创建文件夹失败: {exc}")

    def _clear_jobs(self):
        global ACTIVE_JOB_ID
        removed = 0
        with JOBS_LOCK:
            for job_id, job in list(JOBS.items()):
                if job.status != "running":
                    try:
                        (JOBS_DIR / f"{job_id}.json").unlink(missing_ok=True)
                    except Exception:
                        pass
                    del JOBS[job_id]
                    removed += 1
            ACTIVE_JOB_ID = next((j.id for j in JOBS.values() if j.status == "running"), None)
        return self._json({"ok": True, "removed": removed})

    def _shutdown(self):
        self._json({"ok": True})
        callback = globals().get("SHUTDOWN_REQUEST")
        if callback:
            threading.Thread(target=callback, daemon=True).start()

    # -- binary media ---------------------------------------------------
    def _serve_thumb(self, query: dict):
        rel = (query.get("path", [""])[0] or "").strip()
        try:
            width = int(query.get("w", ["720"])[0])
        except Exception:
            width = 720
        target = self._media_path(rel)
        if not target:
            return self._error("文件不存在", 404)
        thumb = get_thumbnail(target, width)
        if not thumb or not thumb.is_file():
            return self._error("无法生成缩略图", 415)
        body = thumb.read_bytes()
        etag = '"' + hashlib.sha1(body).hexdigest()[:20] + '"'
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._send(
            200,
            body,
            "image/jpeg",
            {
                "Cache-Control": "public, max-age=86400",
                "ETag": etag,
            },
        )

    def _serve_media(self, query: dict):
        rel = (query.get("path", [""])[0] or "").strip()
        target = self._media_path(rel)
        if not target:
            return self._error("文件不存在", 404)
        ext = target.suffix.lower()
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if ext in (".heic", ".heif"):
            ctype = "image/heic"
        size = target.stat().st_size
        range_header = self.headers.get("Range")
        start, end = 0, size - 1
        status = 200
        if range_header:
            m = re.match(r"bytes=(\d*)-(\d*)", range_header.strip())
            if m:
                if m.group(1):
                    start = int(m.group(1))
                if m.group(2):
                    if m.group(1):
                        end = min(size - 1, int(m.group(2)))
                    else:
                        suffix = max(1, int(m.group(2)))
                        start = max(0, size - suffix)
                        end = size - 1
                if start > end or start >= size:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                status = 206
        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "private, max-age=3600")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        try:
            with open(target, "rb") as fh:
                fh.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = fh.read(min(1024 * 512, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------


def choose_port(host: str, desired: int) -> int:
    import socket

    for candidate in range(desired, desired + 30):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, candidate))
                return candidate
            except OSError:
                continue
    raise RuntimeError(f"端口 {desired}-{desired + 29} 均被占用")


def main() -> int:
    parser = argparse.ArgumentParser(description="iCloudPD Studio - a local web UI for icloudpd")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    ensure_app_dirs()
    cfg = load_config()
    save_config(cfg)
    load_jobs()

    port = choose_port(args.host, args.port)
    server = StudioServer((args.host, port), Handler)
    server.daemon_threads = True
    url = f"http://{args.host}:{port}"

    print()
    print("  iCloudPD Studio")
    print("  ---------------------------------------------")
    engine_info(force=True)
    print(f"  Engine   {' '.join(engine_argv())}")
    print(f"  Web UI   {url}")
    print(f"  Data     {DATA_DIR}")
    print("  Stop     Ctrl+C")
    print()
    sys.stdout.flush()

    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    stopping = threading.Event()

    def shutdown(signum=None, frame=None):
        if stopping.is_set():
            return
        stopping.set()

        def do_stop():
            try:
                for job in list(JOBS.values()):
                    if job.status == "running":
                        job.stop()
            except Exception:
                pass
            try:
                server.shutdown()
            except Exception:
                pass

        # HTTPServer.shutdown() must not be called from the serve_forever thread.
        threading.Thread(target=do_stop, name="shutdown", daemon=True).start()

    global SHUTDOWN_REQUEST
    SHUTDOWN_REQUEST = shutdown

    try:
        signal.signal(signal.SIGINT, shutdown)
        signal.signal(signal.SIGTERM, shutdown)
    except Exception:
        pass

    try:
        server.serve_forever(poll_interval=0.4)
    except KeyboardInterrupt:
        shutdown()
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
