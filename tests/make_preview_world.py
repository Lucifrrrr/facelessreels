"""Erzeugt eine Test-Welt und einen Shot-Plan für Blender-Vorschauen (ändert nicht die echte Welt)."""

import json
import os
import sys
from pathlib import Path

out = Path(sys.argv[1])
followers = int(sys.argv[2]) if len(sys.argv) > 2 else 1200
os.environ["MARS_STATE_DIR"] = str(out / "state")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mars import world as W  # noqa: E402
from mars.common import load_config  # noqa: E402

cfg = load_config()
w = W.load_world(cfg)
day = 1
for f in (60, 140, 300, 600, followers):
    events = W.apply_followers(w, cfg, f, day)
    day += 1
new = [e["building"] for e in events if "building" in e]
focus = new[0] if new else w["buildings"][-1]["id"]
plan = {
    "fps": 24, "width": 1080, "height": 1920,
    "shots": [
        {"name": "intro", "type": "orbit", "seconds": 4, "angle": 225},
        {"name": "build", "type": "flyto", "seconds": 5, "building": focus, "angle": 200},
        {"name": "colony", "type": "pan", "seconds": 4, "angle": 135},
        {"name": "outro", "type": "pullout", "seconds": 4, "angle": 250},
    ],
    "new_buildings": new, "build_shot": "build", "visible_colonists": 40,
}
(out / "world.json").write_text(json.dumps(w, ensure_ascii=False, indent=2), encoding="utf-8")
(out / "shots.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
print(len(w["buildings"]), "Gebäude; neu:", new)
