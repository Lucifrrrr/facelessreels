"""Sucht deutsche Erzählerstimmen in der ElevenLabs-Bibliothek und lädt die kostenlosen Hörproben."""

import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mars.common import secret  # noqa: E402

out = Path(sys.argv[1] if len(sys.argv) > 1 else "work/voices")
out.mkdir(parents=True, exist_ok=True)
key = secret("ELEVENLABS_API_KEY")


def get(path, **params):
    url = f"https://api.elevenlabs.io{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"xi-api-key": key})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


found = {}
for use_case in ("narrative_story", "informative_educational", "characters_animation"):
    for gender in ("male", "female"):
        res = get("/v1/shared-voices", language="de", use_cases=use_case, gender=gender,
                  page_size=15, sort="usage_character_count_1y")
        for v in res.get("voices", []):
            if v.get("language") != "de":
                continue
            found.setdefault(v["voice_id"], v)

ranked = sorted(found.values(), key=lambda v: -(v.get("usage_character_count_1y") or 0))
rows = []
for v in ranked[:12]:
    rows.append({
        "name": v.get("name"), "gender": v.get("gender"), "age": v.get("age"), "accent": v.get("accent"),
        "use_case": v.get("use_case"), "descriptive": v.get("descriptive"),
        "usage_1y": v.get("usage_character_count_1y"), "voice_id": v["voice_id"],
        "public_owner_id": v.get("public_owner_id"), "preview": v.get("preview_url"),
        "description": (v.get("description") or "")[:140],
    })
(out / "candidates.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
for i, r in enumerate(rows, 1):
    print(f"{i:2}. {r['name'][:40]:40} {r['gender']:6} {str(r['age']):12} {str(r['descriptive']):12} {r['use_case']}  ({r['usage_1y']})")
print("Konto:", get("/v1/user/subscription").get("tier", "?"))
