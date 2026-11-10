"""Gemeinsame Helfer: Pfade, Konfiguration, atomares JSON, Logging."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "mars.json"
STATE_DIR = Path(os.environ.get("MARS_STATE_DIR", ROOT / "state" / "mars"))
WORK_DIR = Path(os.environ.get("MARS_WORK_DIR", ROOT / "work" / "mars"))
ASSET_DIR = ROOT / "assets"
LOG_DIR = STATE_DIR / "logs"


def load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data) -> None:
    """Atomar schreiben, damit ein Absturz nie halbe Dateien hinterlässt."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def now(cfg: dict | None = None) -> datetime:
    tz = ZoneInfo((cfg or load_config())["timezone"])
    return datetime.now(tz)


def get_logger(name: str = "mars") -> logging.Logger:
    log = logging.getLogger(name)
    if log.handlers:
        return log
    log.setLevel(logging.INFO)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    fh = logging.FileHandler(LOG_DIR / "mars.log", encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    log.addHandler(fh)
    log.addHandler(sh)
    return log


def secret(name: str, required: bool = True) -> str | None:
    """Zugangsdaten nur aus Umgebungsvariablen (lokal auch Windows-Benutzervariablen)."""
    value = os.environ.get(name, "").strip()
    if not value and os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
                value = str(winreg.QueryValueEx(k, name)[0]).strip()
        except OSError:
            value = ""
    if required and not value:
        raise RuntimeError(f"Zugangsdaten fehlen: Umgebungsvariable {name} ist nicht gesetzt.")
    return value or None
