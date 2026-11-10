"""Schnelltests für Weltzustand, Meilensteine, Abstimmungen und Budget (ohne Netzwerk)."""

import os
import shutil
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp())
os.environ["MARS_STATE_DIR"] = str(TMP / "state")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mars import budget as B  # noqa: E402
from mars import votes as V  # noqa: E402
from mars import world as W  # noqa: E402
from mars.common import load_config  # noqa: E402


def test_world():
    cfg = load_config()
    w = W.load_world(cfg)
    assert len(w["buildings"]) == 5
    W.apply_followers(w, cfg, 40, 1)
    ev = W.apply_followers(w, cfg, 120, 2)
    assert any(e["kind"] == "milestone" and e["threshold"] == 100 for e in ev)
    n = len(w["buildings"])
    W.apply_followers(w, cfg, 90, 3)  # Follower sinken
    assert w["population"] == 90 and len(w["buildings"]) == n, "Gebäude dürfen nicht verschwinden"
    W.apply_followers(w, cfg, 120, 4)
    assert w["milestones_reached"].count(100) == 1, "Meilenstein nicht doppelt"
    W.apply_followers(w, cfg, 30000, 5)
    assert set(w["milestones_reached"]) >= {100, 250, 500, 1000, 2500, 5000, 10000, 25000}
    cells = [tuple(c) for b in w["buildings"] for c in b["cells"]]
    assert len(cells) == len(set(cells)), "Gebäude überlappen"
    hq = next(b for b in w["buildings"] if b["type"] == "hq")
    assert sorted(map(tuple, hq["cells"])) == sorted(W.RESERVED_CENTER)
    W.save_world(w)
    assert W.load_world(cfg)["population"] == 30000
    print("Welt ok:", len(w["buildings"]), "Gebäude")


def test_votes():
    rule = "tie"
    c = [
        {"text": "A", "timestamp": "2026-11-10T10:00:00+0000", "username": "x"},
        {"text": "a nochmal", "timestamp": "2026-11-10T10:01:00+0000", "username": "x"},
        {"text": "B!", "timestamp": "2026-11-10T10:02:00+0000", "username": "y"},
        {"text": "Bitte A", "timestamp": "2026-11-10T10:03:00+0000", "username": "z"},
        {"text": "Banane", "timestamp": "2026-11-10T10:03:00+0000", "username": "q"},
        {"text": "„b“", "timestamp": "2026-11-10T10:04:00+0000", "username": "r"},
        {"text": "B", "timestamp": "2026-11-12T10:00:00+0000", "username": "w"},
    ]
    r = V.count_votes(c, "2026-11-10T00:00:00+00:00", "2026-11-11T00:00:00+00:00", rule)
    assert r["tally"] == {"A": 1, "B": 2}, r
    assert r["winner"] == "B" and not r["tie"]
    assert r["ignored"] == {"outside_window": 1, "duplicate_account": 1, "no_vote": 2}
    tie = V.count_votes(c[:3], "2026-11-10T00:00:00+00:00", "2026-11-11T00:00:00+00:00", rule)
    assert tie["tie"] and tie["winner"] == "A"  # erste Stimme war A
    none = V.count_votes([], "2026-11-10T00:00:00+00:00", "2026-11-11T00:00:00+00:00", rule)
    assert none["winner"] == "A" and none["tie"]
    print("Abstimmung ok:", r["tally"])


def test_budget():
    cfg = load_config()
    B.LEDGER_PATH = TMP / "ledger.json"
    B.authorize(cfg, "higgsfield", 40, "test")
    B.record(cfg, "higgsfield", 40, "test", "mars-colony")
    try:
        B.authorize(cfg, "higgsfield", 6, "zu viel")
        raise AssertionError("Budgetgrenze nicht durchgesetzt")
    except B.BudgetExceeded:
        pass
    print("Budget ok:", B.report(cfg))


if __name__ == "__main__":
    try:
        test_world()
        test_votes()
        test_budget()
        print("ALLE TESTS BESTANDEN")
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
