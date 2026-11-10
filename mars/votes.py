"""Community-Abstimmungen über Instagram-Kommentare.

Regeln (transparent und dauerhaft dokumentiert):
- Gültig ist ein Kommentar, der mit "A" oder "B" beginnt (z. B. "A", "b!", "A bitte").
- Nur Kommentare innerhalb des Abstimmungszeitraums zählen.
- Pro Account zählt nur die erste gültige Stimme (sofern die API den Account liefert).
- Gleichstand: siehe config vote.tie_rule.
"""

from __future__ import annotations

import random
import re
from datetime import datetime

VOTE_RE = re.compile(r"^\s*[\"'„“(\[]*\s*([AaBb])(?![A-Za-zÄÖÜäöüß])")


def parse_vote(text: str) -> str | None:
    m = VOTE_RE.match(text or "")
    return m.group(1).upper() if m else None


def _ts(value: str) -> datetime:
    # Instagram: 2017-08-31T19:16:02+0000
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S%z")


def count_votes(comments: list[dict], opens_at: str, closes_at: str, tie_rule: str) -> dict:
    start, end = datetime.fromisoformat(opens_at), datetime.fromisoformat(closes_at)
    seen_accounts: set[str] = set()
    first_vote: dict[str, datetime] = {}
    tally = {"A": 0, "B": 0}
    ignored = {"outside_window": 0, "duplicate_account": 0, "no_vote": 0}
    accounts_known = True
    for c in sorted(comments, key=lambda c: c.get("timestamp", "")):
        choice = parse_vote(c.get("text", ""))
        if not choice:
            ignored["no_vote"] += 1
            continue
        ts = _ts(c["timestamp"])
        if not (start <= ts < end):
            ignored["outside_window"] += 1
            continue
        account = (c.get("from") or {}).get("id") or c.get("username")
        if account:
            if account in seen_accounts:
                ignored["duplicate_account"] += 1
                continue
            seen_accounts.add(account)
        else:
            accounts_known = False
        tally[choice] += 1
        first_vote.setdefault(choice, ts)

    if tally["A"] != tally["B"]:
        winner, tie = ("A" if tally["A"] > tally["B"] else "B"), False
    elif tally["A"] == 0:
        winner, tie = "A", True
    else:
        winner, tie = ("A" if first_vote["A"] <= first_vote["B"] else "B"), True
    return {
        "tally": tally,
        "winner": winner,
        "tie": tie,
        "tie_rule": tie_rule if tie else None,
        "ignored": ignored,
        "one_vote_per_account_enforced": accounts_known,
        "valid_votes": tally["A"] + tally["B"],
    }


def choose_options(world: dict, cfg: dict, day: int) -> dict:
    """Zwei Bauoptionen wählen; bevorzugt solche, die selten gebaut wurden."""
    pool = list(cfg["vote_options"].keys())
    built = {t: 0 for t in pool}
    for b in world["buildings"]:
        if b["type"] in built:
            built[b["type"]] += 1
    rng = random.Random(f"{world['seed']}-vote-{day}")
    rng.shuffle(pool)
    pool.sort(key=lambda t: built[t])
    candidates = pool[:4]
    a, b = rng.sample(candidates, 2)
    return {"A": a, "B": b}


def new_vote(world: dict, cfg: dict, day: int) -> dict:
    options = choose_options(world, cfg, day)
    return {
        "id": f"v{day:04d}",
        "day": day,
        "options": options,
        "labels": {k: cfg["vote_options"][v] for k, v in options.items()},
        "media_id": None,
        "opens_at": None,
        "closes_at": None,
        "result": None,
    }
