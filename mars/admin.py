"""Verwaltung: Status, Instagram-Prüfung, Freigabe, Pause.

  python -m mars.admin status
  python -m mars.admin check-instagram
  python -m mars.admin activate --confirm "JA, VEROEFFENTLICHEN"
  python -m mars.admin pause "Grund"
  python -m mars.admin resume
"""

from __future__ import annotations

import argparse
import json
import sys

from . import budget, world as W
from .common import STATE_DIR, load_config, now, read_json, write_json

ACTIVATION = STATE_DIR / "activation.json"
PHRASE = "JA, VEROEFFENTLICHEN"


def main(argv=None):
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("check-instagram")
    act = sub.add_parser("activate")
    act.add_argument("--confirm", required=True)
    pa = sub.add_parser("pause")
    pa.add_argument("reason")
    sub.add_parser("resume")
    a = p.parse_args(argv)
    cfg = load_config()

    if a.cmd == "status":
        w = W.load_world(cfg)
        print(json.dumps({
            "tag": w["day"], "einwohner": w["population"], "gebaeude": len(w["buildings"]),
            "meilensteine": w["milestones_reached"], "offene_abstimmung": (w.get("pending_vote") or {}).get("options"),
            "pausiert": w.get("paused"), "freigabe": read_json(ACTIVATION, {}),
            "budget": budget.report(cfg),
        }, ensure_ascii=False, indent=2))
    elif a.cmd == "check-instagram":
        from . import instagram as ig
        info = ig.account(cfg)
        print("Konto:", info.get("username"), "| Typ:", info.get("account_type"),
              "| Follower:", info.get("followers_count"), "| Beiträge:", info.get("media_count"))
        print("Veröffentlichungs-Kontingent:", ig.publishing_quota(cfg).get("data"))
        print("Instagram-Verbindung OK (nur gelesen, nichts veröffentlicht).")
    elif a.cmd == "activate":
        if a.confirm != PHRASE:
            sys.exit(f'Zur Freigabe genau --confirm "{PHRASE}" angeben.')
        write_json(ACTIVATION, {"public_publishing_enabled": True, "activated_at": now(cfg).isoformat()})
        print("Öffentliche Veröffentlichung freigegeben.")
    elif a.cmd == "pause":
        w = W.load_world(cfg)
        w["paused"] = {"reason": a.reason, "date": now(cfg).isoformat()}
        W.save_world(w)
        print("Pausiert.")
    elif a.cmd == "resume":
        w = W.load_world(cfg)
        w["paused"] = None
        W.save_world(w)
        write_json(STATE_DIR / "failures.json", [])
        print("Fortgesetzt.")


if __name__ == "__main__":
    main()
