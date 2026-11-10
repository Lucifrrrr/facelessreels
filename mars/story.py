"""Drehbuch, Higgsfield-Prompt, Bildunterschrift und Hashtags für ein Reel.

Vorlagenbasiert und deterministisch (keine laufenden KI-Kosten). Zahlen werden
für den Narrator als Wörter geschrieben, für Untertitel als Ziffern.
"""

from __future__ import annotations

import random
import re

from num2words import num2words

# Erzählereignisse: (Narration, englischer Higgsfield-Prompt). Higgsfield zeigt nur
# Nahaufnahmen/Innenräume, damit die in Blender gespeicherte Welt nie widersprochen wird.
STYLE = ("cinematic, photorealistic, consistent sci-fi production design: white and orange "
         "space suits with amber visors, white habitat modules, rust-red Martian dust, "
         "soft golden hour light, shallow depth of field, slow camera movement, vertical 9:16")

EVENTS = [
    ("Doch über Nacht zog ein Staubsturm auf. Die Kolonisten sicherten die Kuppeln – und hielten durch.",
     "Close-up of an astronaut bracing against a swirling red dust storm outside a habitat airlock, dust streaming past the visor"),
    ("Im Labor gelang ein Durchbruch: Im Marseis wurden Spuren von Mineralien gefunden, die uns Wasser sichern.",
     "Scientist in a white lab inside a Mars habitat examining a glowing ice core sample in a glass container, monitors in background"),
    ("Im Gewächshaus keimten die ersten Pflanzen. Zum ersten Mal wächst Leben auf dem roten Planeten.",
     "Macro shot of small green seedlings sprouting in red Martian soil under purple grow lights inside a greenhouse module, astronaut glove touching a leaf"),
    ("Ein Funksignal von der Erde erreichte die Kolonie. Millionen Menschen schauen jetzt auf uns.",
     "Astronaut in a dim control room listening to a radio transmission, holographic Earth display glowing, emotional close-up"),
    ("Ein Rover-Team brach zu einer Expedition in den Krater auf – und entdeckte eine Höhle.",
     "Six-wheeled Mars rover with headlights driving slowly into the shadow of a rocky crater cave entrance, dust trailing behind"),
    ("In der Nacht zog ein Meteoritenschauer über den Himmel. Ein seltener, wunderschöner Anblick.",
     "Two astronauts sitting on a ridge at night on Mars watching a meteor shower streak across a starry sky, silhouettes"),
    ("Die Nachtschicht hielt die Sauerstoffanlage am Laufen. Ohne sie gäbe es keine Kolonie.",
     "Engineer in a space suit repairing glowing pipes of an oxygen processing machine at night, sparks and steam, close-up"),
    ("Ein Sonnensturm traf den Mars. Alle Kolonisten zogen sich in die geschützten Module zurück.",
     "Colonists in a shielded habitat corridor looking up at red warning lights while a solar storm flashes through a small window"),
    ("Der Sonnenaufgang über dem Valles Marineris – ein Moment der Ruhe für die ganze Kolonie.",
     "Astronaut standing at a habitat window watching a pale blue Martian sunrise over vast canyons, reflection on the visor"),
    ("Ein Versorgungsschiff kündigte sich an. Neue Ausrüstung ist auf dem Weg zu uns.",
     "Supply capsule descending with retro rockets firing over red Martian desert, dust clouds billowing, astronauts watching in foreground"),
]


def words(n: int) -> str:
    return num2words(n, lang="de")


def digits(n: int) -> str:
    return f"{n:,}".replace(",", ".")


MARK = re.compile(r"⟦(\^?)(-?\d+)⟧")


def mark(n: int, cap: bool = False) -> str:
    """Platzhalter: Narrator spricht Wörter, Untertitel zeigen Ziffern."""
    return f"⟦{'^' if cap else ''}{int(n)}⟧"


def spoken(text: str) -> str:
    def rep(m):
        w = words(int(m.group(2)))
        return w.capitalize() if m.group(1) else w
    return MARK.sub(rep, text)


def written(text: str) -> str:
    return MARK.sub(lambda m: digits(int(m.group(2))), text)


def next_milestone(cfg: dict, population: int):
    for m in cfg["milestones"]:
        if m["followers"] > population:
            return m
    return None


def build(world: dict, cfg: dict, day: int, events: list[dict], vote_result: dict | None,
          won_building: dict | None, new_vote: dict) -> dict:
    rng = random.Random(f"{world['seed']}-story-{day}")
    pop = world["population"]
    pop_ev = next(e for e in events if e["kind"] == "population")
    delta = pop_ev["delta"]
    milestones = [e for e in events if e["kind"] == "milestone"]
    habitats = [e for e in events if e["kind"] == "habitat"]
    event_text, event_prompt = EVENTS[(day + world["seed"]) % len(EVENTS)]

    # 1) Hook
    hooks = [
        f"Tag {mark(day)} auf dem Mars.",
        f"Mars-Kolonie, Tag {mark(day)}.",
        f"Willkommen zurück auf dem Mars. Tag {mark(day)}.",
    ] if day > 1 else ["Tag eins. Auf dem Mars beginnt ein neues Kapitel der Menschheit."]
    if milestones:
        hook2 = "Heute ist ein historischer Tag für unsere Kolonie."
    elif delta > 0:
        hook2 = f"{mark(delta, cap=True)} neue Kolonisten sind gelandet."
    elif delta < 0:
        hook2 = "Einige Kolonisten sind zur Erde zurückgekehrt. Doch wir geben nicht auf."
    else:
        hook2 = "Die Kolonie hält stand – und plant den nächsten Schritt."
    seg_intro = f"{rng.choice(hooks)} {hook2}"

    # 2) Bau: Meilenstein > Abstimmungsergebnis > neue Wohnmodule
    focus = None
    if milestones:
        m = milestones[-1]
        seg_build = (f"Meilenstein erreicht: {mark(m['threshold'])} Kolonisten! "
                     f"Dafür errichten wir {_article(m['name'])}.")
        focus = m["building"]
    elif vote_result and won_building:
        t = vote_result["tally"]
        w = vote_result["winner"]
        if vote_result["valid_votes"] == 0:
            seg_build = f"Ihr habt entschieden – und wir bauen: {won_building['name']}."
        elif vote_result["tie"]:
            seg_build = (f"Gleichstand bei der Abstimmung! Nach unserer Regel gewinnt Option {w}: "
                         f"Wir bauen {_article(won_building['name'])}.")
        else:
            seg_build = (f"Ihr habt abgestimmt: Option {w} gewinnt mit {mark(t[w])} zu "
                         f"{mark(t['B' if w == 'A' else 'A'])} Stimmen. Wir bauen {_article(won_building['name'])}.")
        focus = won_building["id"]
    elif habitats:
        seg_build = f"Für die Neuankömmlinge entstehen {mark(len(habitats))} neue Wohnmodule."
        focus = habitats[-1]["building"]
    else:
        seg_build = "Die Ingenieure verstärken heute die Infrastruktur der Kolonie."

    # 3) Filmische Szene
    seg_cinematic = event_text

    # 4) Einwohner und nächstes Ziel
    nm = next_milestone(cfg, pop)
    seg_colony = f"Jetzt leben {mark(pop)} Menschen in unserer Kolonie."
    if nm:
        seg_colony += f" Bei {mark(nm['followers'])} Followern bauen wir {_article(nm['name'])}."

    # 5) Abstimmung
    la, lb = new_vote["labels"]["A"], new_vote["labels"]["B"]
    seg_vote = (f"Unsere Kolonie wächst. Aber was bauen wir als Nächstes? "
                f"Kommentiere A für {la} oder B für {lb}. Folge uns, und die Kolonie wächst mit dir.")

    segments = [
        {"id": "intro", "text": seg_intro, "shot": "orbit"},
        {"id": "build", "text": seg_build, "shot": "flyto", "building": focus},
        {"id": "cinematic", "text": seg_cinematic, "shot": "higgsfield"},
        {"id": "colony", "text": seg_colony, "shot": "pan"},
        {"id": "vote", "text": seg_vote, "shot": "pullout"},
    ]
    for s in segments:
        s["subtitle"] = written(s["text"])
        s["text"] = spoken(s["text"])
    seg_cinematic = written(seg_cinematic)
    milestone_name = milestones[-1]["name"] if milestones else None
    return {
        "day": day,
        "segments": segments,
        "focus_building": focus,
        "higgsfield_prompt": f"{event_prompt}. {STYLE}",
        "population_before": max(0, pop - delta),
        "population": pop,
        "milestone": milestone_name,
        "vote": {"A": la, "B": lb},
        "caption": caption(cfg, day, pop, delta, milestone_name, la, lb, seg_cinematic),
    }


ACCUSATIVE = {
    "Erste große Wohnkuppel": "die erste große Wohnkuppel",
    "Solarfarm": "eine Solarfarm",
    "Erweiterter Wohnkomplex": "einen erweiterten Wohnkomplex",
    "Kolonie-Hauptquartier": "das Kolonie-Hauptquartier",
    "Forschungszentrum": "ein Forschungszentrum",
    "Neuer Stadtbezirk": "einen neuen Stadtbezirk",
    "Riesige Marskuppel": "eine riesige Marskuppel",
    "Raumhafen": "einen Raumhafen",
    "Zweite Marsstadt": "eine zweite Marsstadt",
    "Monument der Kolonie": "das Monument der Kolonie",
    "Wohnmodul": "ein Wohnmodul",
    "Sauerstoffanlage": "eine Sauerstoffanlage",
    "Forschungslabor": "ein Forschungslabor",
    "Gewächshaus": "ein Gewächshaus",
    "Wasserförderanlage": "eine Wasserförderanlage",
    "Rover-Garage": "eine Rover-Garage",
    "Funkturm": "einen Funkturm",
    "Krankenstation": "eine Krankenstation",
    "Bergbauanlage": "eine Bergbauanlage",
    "Energiespeicher": "einen Energiespeicher",
    "Sternwarte": "eine Sternwarte",
    "Landemodul": "ein Landemodul",
    "Solarfeld": "ein Solarfeld",
    "Große Wohnkuppel": "eine große Wohnkuppel",
    "Wohnkomplex": "einen Wohnkomplex",
    "Hauptquartier": "ein Hauptquartier",
    "Stadtbezirk": "einen Stadtbezirk",
    "Megakuppel": "eine Megakuppel",
    "Monument": "ein Monument",
}


def _article(name: str) -> str:
    """Deutscher Akkusativ für die bekannten Gebäudenamen."""
    if name.strip() in ACCUSATIVE:
        return ACCUSATIVE[name.strip()]
    n = name.strip()
    lower = n.lower()
    if lower.startswith(("ein ", "eine ", "einen ")):
        return n
    feminine = ("kuppel", "farm", "anlage", "station", "stadt", "sternwarte", "garage")
    neutral = ("hauptquartier", "zentrum", "labor", "gewächshaus", "monument", "modul", "feld")
    last = lower.split()[-1]
    if any(last.endswith(s) for s in feminine):
        art = "eine"
    elif any(last.endswith(s) for s in neutral):
        art = "ein"
    else:
        art = "einen"
    adj = n.split()
    if len(adj) > 1 and art in ("ein", "eine", "einen"):
        # Adjektivendungen: "Riesige Marskuppel" -> "eine riesige Marskuppel"
        first = adj[0]
        if first[0].isupper() and first.lower().endswith("e"):
            if art == "einen":
                first = first[:-1].lower() + "en"
            elif art == "ein":
                first = first[:-1].lower() + "es"
            else:
                first = first.lower()
            n = " ".join([first] + adj[1:])
    return f"{art} {n}"


def short_label(label: str) -> str:
    """'eine neue Sauerstoffanlage' -> 'Sauerstoffanlage'"""
    for art in ("eine neue ", "einen neuen ", "ein neues ", "eine ", "einen ", "ein "):
        if label.startswith(art):
            label = label[len(art):]
            break
    return label[:1].upper() + label[1:]


def caption(cfg, day, pop, delta, milestone, la, lb, event) -> str:
    lines = [f"🚀 Mars-Kolonie – Tag {day}", ""]
    if milestone:
        lines.append(f"🏆 MEILENSTEIN: {milestone}!")
    lines.append(f"👩‍🚀 Einwohner: {digits(pop)}" + (f" (+{digits(delta)})" if delta > 0 else ""))
    lines.append(f"📡 {event}")
    lines += ["", "🗳️ Was bauen wir als Nächstes?", f"A = {short_label(la)}", f"B = {short_label(lb)}",
              "Kommentiere A oder B! Abstimmung läuft bis zum nächsten Reel (eine Stimme pro Account).",
              "", "Jeder neue Follower wird ein neuer Kolonist. 🔴", "",
              " ".join(cfg["instagram"]["hashtags"]) + f" #MarsKolonieTag{day}"]
    return "\n".join(lines)
