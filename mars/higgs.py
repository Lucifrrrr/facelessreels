"""Higgsfield-API (Guthaben aus console.higgsfield.ai) mit Budgetkontrolle und Wiederaufnahme."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from . import budget
from .common import get_logger, secret

BASE = "https://api.higgsfield.ai"
log = get_logger()


class HiggsError(RuntimeError):
    pass


def _req(method: str, url: str, body: dict | None = None, idem: str | None = None, timeout: int = 60) -> dict:
    url = url if url.startswith("http") else f"{BASE}/{url.lstrip('/')}"
    headers = {"Authorization": f"Key {secret('HF_KEY')}", "Accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if idem:
        headers["Idempotency-Key"] = idem
    for attempt in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data, headers, method=method), timeout=timeout) as r:
                raw = r.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:400]
            if e.code in (429, 500, 502, 503, 504) and attempt < 3:
                time.sleep(5 * (attempt + 1))
                continue
            raise HiggsError(f"HTTP {e.code} {method} {url}: {detail}") from e
        except urllib.error.URLError as e:
            if attempt < 3:
                time.sleep(5 * (attempt + 1))
                continue
            raise HiggsError(f"Netzwerkfehler {url}: {e.reason}") from e
    raise HiggsError("unerreichbar")


def estimate_usd(model: str, body: dict) -> float:
    r = _req("POST", f"estimate/{model}", body)
    if "usd" not in r:
        raise HiggsError(f"Keine exakte Kostenschätzung für {model}: {str(r)[:200]}")
    return float(r["usd"])


def generate_clip(cfg: dict, prompt: str, out: Path, checkpoint: dict, save_checkpoint) -> Path:
    """Erzeugt genau einen Clip. Max. 1 kostenpflichtiger Wiederholungsversuch.

    checkpoint["higgs"] speichert request_ids, damit ein abgebrochener Lauf den
    bereits bezahlten Auftrag weiterverfolgt statt neu zu bezahlen.
    """
    h = cfg["higgsfield"]
    model = h["model"]
    body = {"prompt": prompt[:3000], "duration": h["duration"], "resolution": h["resolution"], "aspect_ratio": "9:16"}
    state = checkpoint.setdefault("higgs", {"attempts": []})
    max_attempts = 1 + h["max_paid_retries"]

    while True:
        open_attempt = next((a for a in state["attempts"] if a["status"] not in ("completed", "failed", "nsfw", "canceled", "error")), None)
        if open_attempt is None:
            paid = [a for a in state["attempts"] if a.get("charged", True)]
            if len(paid) >= max_attempts:
                raise HiggsError(f"Higgsfield: {len(paid)} Versuche verbraucht, kein weiterer erlaubt.")
            usd = estimate_usd(model, body)
            eur = usd * cfg["budget"]["usd_to_eur"]
            budget.authorize(cfg, "higgsfield", eur, f"Higgsfield-Clip Tag {checkpoint.get('day')}")
            idem = str(uuid.uuid4())
            open_attempt = {"idem": idem, "usd": usd, "eur": eur, "status": "submitting", "request_id": None}
            state["attempts"].append(open_attempt)
            save_checkpoint()
            job = _req("POST", model, body, idem=idem)
            open_attempt.update(request_id=job.get("request_id"), status_url=job.get("status_url"), status="queued")
            save_checkpoint()
            log.info("Higgsfield-Auftrag %s (%.3f $)", open_attempt["request_id"], usd)
        elif open_attempt["status"] == "submitting":
            # Absturz zwischen Absenden und Antwort: gleicher Idempotency-Key liefert denselben Auftrag.
            job = _req("POST", model, body, idem=open_attempt["idem"])
            open_attempt.update(request_id=job.get("request_id"), status_url=job.get("status_url"), status="queued")
            save_checkpoint()

        url = open_attempt.get("status_url") or f"requests/{open_attempt['request_id']}/status"
        deadline = time.time() + 20 * 60
        delay = 5
        result = {}
        while time.time() < deadline:
            result = _req("GET", url)
            st = result.get("status")
            if st in ("completed", "failed", "nsfw", "canceled"):
                break
            time.sleep(delay)
            delay = min(delay + 3, 20)
        st = result.get("status", "timeout")
        open_attempt["status"] = st if st != "timeout" else "error"
        if st == "completed":
            open_attempt["charged"] = True
            budget.record(cfg, "higgsfield", open_attempt["eur"], f"Clip Tag {checkpoint.get('day')}",
                          cfg["project"], open_attempt["request_id"])
            video_url = (result.get("video") or {}).get("url")
            save_checkpoint()
            out.parent.mkdir(parents=True, exist_ok=True)
            urllib.request.urlretrieve(video_url, out)
            return out
        # Laut Higgsfield werden failed/nsfw/canceled nicht berechnet; Timeout zählt vorsichtshalber als bezahlt.
        open_attempt["charged"] = st == "timeout"
        if open_attempt["charged"]:
            budget.record(cfg, "higgsfield", open_attempt["eur"], "Timeout (vorsorglich verbucht)",
                          cfg["project"], open_attempt["request_id"])
        save_checkpoint()
        log.warning("Higgsfield-Versuch fehlgeschlagen: %s %s", st, result.get("error"))
        if len(state["attempts"]) >= max_attempts + 1:
            raise HiggsError(f"Higgsfield fehlgeschlagen ({st}).")
