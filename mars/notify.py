"""Benachrichtigung bei Problemen.

Cloud (GitHub Actions): öffnet ein GitHub-Issue -> E-Mail an den Repo-Besitzer.
Lokal: Eintrag in state/mars/alerts.json und im Log.
"""

from __future__ import annotations

import os
import subprocess
from datetime import datetime

from .common import STATE_DIR, get_logger, read_json, write_json

log = get_logger()


def alert(message: str, level: str = "error") -> None:
    getattr(log, "error" if level == "error" else "warning")(message)
    path = STATE_DIR / "alerts.json"
    alerts = read_json(path, [])
    alerts.append({"time": datetime.now().isoformat(timespec="seconds"), "level": level, "message": message[:2000]})
    write_json(path, alerts[-100:])
    if os.environ.get("GITHUB_ACTIONS") and level == "error":
        try:
            subprocess.run(["gh", "issue", "create", "--title", f"[Mars Colony] {message.splitlines()[0][:90]}",
                            "--body", message[:6000], "--label", "alert"], check=False, capture_output=True, timeout=60)
        except Exception as e:  # noqa: BLE001
            log.warning("Issue konnte nicht erstellt werden: %s", e)
