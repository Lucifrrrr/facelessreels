"""Higgsfield-API-Helfer für die Faceless-Reels-Projekte.

Nutzt das API-Guthaben (console.higgsfield.ai), nicht die App-Credits der CLI.
Nur Python-Standardbibliothek, keine Installation nötig.

Zugangsdaten: Umgebungsvariable HF_KEY im Format "KEY_ID:KEY_SECRET".
Der Key wird nie ausgegeben oder gespeichert.

Beispiele:
  python hf_api.py check
  python hf_api.py estimate --project mars "Eine Kolonie unter rotem Himmel"
  python hf_api.py generate --project zombie "Abandoned city at dawn, slow dolly shot"

"generate" zeigt immer zuerst Modell, Parameter und geschätzte Kosten und fragt
dann nach Bestätigung. Ohne ein eingetipptes "ja" wird nichts erzeugt.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

BASE_URL = "https://api.higgsfield.ai"
ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "output"

MODELS = {
    "seedance": "bytedance/seedance-2.5/text-to-video",
    "kling": "kling-video/v3.0-turbo/text-to-video",
    "wan": "alibaba/wan-3.0-prime/text-to-video",
    "grok-image": "xai/grok-imagine-image-2.0",
}

# Vorgaben je Projekt: Reels im Hochformat, kurz und günstig.
PROJECTS = {
    # Standard ist Kling 3.0 Turbo (~0,56 $ pro 5-s-Clip); Seedance 2.5 kostet ~2,31 $.
    "mars": {"name": "mars-colony", "language": "de", "model": "kling",
             "params": {"aspect_ratio": "9:16", "resolution": "720p", "duration": 5}},
    "zombie": {"name": "zombie-apocalypse", "language": "en", "model": "kling",
               "params": {"aspect_ratio": "9:16", "resolution": "720p", "duration": 5}},
}

TERMINAL = {"completed", "failed", "nsfw", "canceled"}


def load_key() -> str:
    key = os.environ.get("HF_KEY", "").strip()
    if not key and sys.platform == "win32":
        # Neu gesetzte Benutzervariable auch ohne Neustart der App finden.
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
                key = str(winreg.QueryValueEx(k, "HF_KEY")[0]).strip()
        except OSError:
            key = ""
    if ":" not in key:
        sys.exit("HF_KEY fehlt oder hat nicht das Format KEY_ID:KEY_SECRET.")
    return key


def request(method: str, path_or_url: str, body: dict | None = None,
            idempotency_key: str | None = None) -> dict:
    url = path_or_url if path_or_url.startswith("http") else f"{BASE_URL}/{path_or_url.lstrip('/')}"
    headers = {"Authorization": f"Key {load_key()}", "Accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:500]
        if e.code == 401:
            sys.exit("Fehler 401: API-Key ungültig. Prüfe HF_KEY (KEY_ID:KEY_SECRET).")
        sys.exit(f"Fehler {e.code} bei {method} {url}: {detail}")
    except urllib.error.URLError as e:
        sys.exit(f"Keine Verbindung zu {url}: {e.reason}")
    return json.loads(raw) if raw else {}


def build(args) -> tuple[str, dict, dict | None]:
    project = PROJECTS.get(args.project) if args.project else None
    model_alias = args.model or (project["model"] if project else "seedance")
    model = MODELS.get(model_alias, model_alias)
    params = dict(project["params"]) if project else {}
    if model_alias == "grok-image" or model.startswith("xai/"):
        params = {"aspect_ratio": params.get("aspect_ratio", "9:16"), "resolution": "1k", "quality": "medium"}
    for field in ("duration", "resolution", "aspect_ratio"):
        value = getattr(args, field, None)
        if value is not None:
            params[field] = value
    if getattr(args, "no_audio", False):
        params["generate_audio"] = False
    body = {"prompt": args.prompt, **params}
    return model, body, project


def estimate(model: str, body: dict) -> str:
    resp = request("POST", f"estimate/{model}", body)
    if "usd" in resp:
        return str(resp["usd"])
    text = resp.get("pricing_description")
    if not text:
        return f"unbekannt ({json.dumps(resp, ensure_ascii=False)[:300]})"
    # Manche Modelle (z. B. Seedance) liefern nur eine Preisbeschreibung pro Sekunde.
    import re
    res, dur = body.get("resolution"), body.get("duration")
    m = re.search(rf"\$([\d.]+)\s*(?:per second[^,]*?)?\s*at {re.escape(str(res))}", text) if res else None
    if m and dur:
        return f"ca. {float(m.group(1)) * int(dur):.2f} $ (grob: {m.group(1)} $/s × {dur} s)\n  Hinweis von Higgsfield: {text}"
    return f"nicht exakt verfügbar\n  Hinweis von Higgsfield: {text}"


def show(model: str, body: dict, usd: str) -> None:
    print(f"Modell:   {model}")
    for k, v in body.items():
        print(f"{k + ':':<14}{v}")
    print(f"Geschätzte Kosten: {usd}" + ("" if "$" in usd or "\n" in usd else " $"))


def cmd_check(_args) -> None:
    key = load_key()
    print(f"HF_KEY gefunden (Key-ID beginnt mit {key.split(':', 1)[0][:4]}…).")
    # Kostenlose Schätzung als Verbindungstest.
    usd = estimate(MODELS["grok-image"], {"prompt": "test", "resolution": "1k", "quality": "low"})
    print(f"Verbindung zur Higgsfield-API OK. (Testschätzung: {usd} $, nichts erzeugt)")


def cmd_estimate(args) -> None:
    model, body, _ = build(args)
    show(model, body, estimate(model, body))


def cmd_generate(args) -> None:
    model, body, project = build(args)
    usd = estimate(model, body)
    show(model, body, usd)
    answer = input("\nKostenpflichtig erzeugen? Tippe 'ja' zum Bestätigen: ").replace("﻿", "").strip().lower()
    if answer != "ja":
        print("Abgebrochen. Es wurde nichts erzeugt.")
        return

    job = request("POST", model, body, idempotency_key=str(uuid.uuid4()))
    status_url = job.get("status_url") or f"requests/{job['request_id']}/status"
    print(f"Auftrag angenommen: {job.get('request_id')}")

    delay = 3
    while True:
        result = request("GET", status_url)
        status = result.get("status")
        print(f"  Status: {status}")
        if status in TERMINAL:
            break
        time.sleep(delay)
        delay = min(delay + 2, 15)

    if status != "completed":
        sys.exit(f"Nicht erfolgreich ({status}): {result.get('error')}. Laut Higgsfield nicht berechnet.")

    url = (result.get("video") or {}).get("url") or ((result.get("images") or [{}])[0]).get("url")
    if not url:
        sys.exit(f"Fertig, aber keine Ergebnis-URL gefunden: {json.dumps(result)[:500]}")

    folder = OUTPUT_DIR / (project["name"] if project else "misc")
    folder.mkdir(parents=True, exist_ok=True)
    ext = Path(url.split("?")[0]).suffix or ".mp4"
    target = folder / f"{datetime.now():%Y%m%d-%H%M%S}{ext}"
    urllib.request.urlretrieve(url, target)
    (target.with_suffix(".json")).write_text(
        json.dumps({"model": model, "body": body, "estimate_usd": usd,
                    "request_id": result.get("request_id"), "url": url},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Gespeichert: {target}")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description="Higgsfield API (Guthaben aus console.higgsfield.ai)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="API-Key und Verbindung prüfen (kostenlos)")
    for name, help_text in (("estimate", "Kosten schätzen (kostenlos)"),
                            ("generate", "Schätzen, bestätigen lassen, dann erzeugen")):
        sp = sub.add_parser(name, help=help_text)
        sp.add_argument("prompt")
        sp.add_argument("--project", choices=sorted(PROJECTS))
        sp.add_argument("--model", help=f"Alias ({', '.join(MODELS)}) oder volle Modell-ID")
        sp.add_argument("--duration", type=int)
        sp.add_argument("--resolution")
        sp.add_argument("--aspect-ratio", dest="aspect_ratio")
        sp.add_argument("--no-audio", action="store_true")
    args = p.parse_args()
    {"check": cmd_check, "estimate": cmd_estimate, "generate": cmd_generate}[args.cmd](args)


if __name__ == "__main__":
    main()
