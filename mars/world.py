"""Dauerhafter Weltzustand der Marskolonie.

Die Welt liegt in state/mars/world.json. Gebäude werden nur hinzugefügt, nie
automatisch entfernt. Positionen werden deterministisch auf einem Raster
vergeben, damit die Kolonie in jedem Reel exakt gleich aussieht.
"""

from __future__ import annotations

import math
from copy import deepcopy

from .common import STATE_DIR, read_json, write_json

WORLD_PATH = STATE_DIR / "world.json"

# Rastergröße der Gebäude (in Zellen) und Wohnkapazität.
BUILDING_SPECS = {
    "lander": {"size": 1, "capacity": 4, "name": "Landemodul"},
    "hab_small": {"size": 1, "capacity": 10, "name": "Wohnmodul"},
    "solar_small": {"size": 1, "capacity": 0, "name": "Solarfeld"},
    "oxygen_plant": {"size": 1, "capacity": 0, "name": "Sauerstoffanlage"},
    "dome_large": {"size": 2, "capacity": 100, "name": "Große Wohnkuppel"},
    "solar_farm": {"size": 2, "capacity": 0, "name": "Solarfarm"},
    "residential_complex": {"size": 2, "capacity": 400, "name": "Wohnkomplex"},
    "hq": {"size": 2, "capacity": 50, "name": "Hauptquartier"},
    "research_center": {"size": 2, "capacity": 80, "name": "Forschungszentrum"},
    "district": {"size": 3, "capacity": 4000, "name": "Stadtbezirk"},
    "mega_dome": {"size": 0, "capacity": 8000, "name": "Megakuppel"},
    "spaceport": {"size": 3, "capacity": 200, "name": "Raumhafen"},
    "second_city": {"size": 0, "capacity": 40000, "name": "Zweite Marsstadt"},
    "landmark": {"size": 2, "capacity": 0, "name": "Monument"},
    "research_lab": {"size": 1, "capacity": 10, "name": "Forschungslabor"},
    "greenhouse": {"size": 1, "capacity": 0, "name": "Gewächshaus"},
    "water_extractor": {"size": 1, "capacity": 0, "name": "Wasserförderanlage"},
    "rover_garage": {"size": 1, "capacity": 0, "name": "Rover-Garage"},
    "comm_tower": {"size": 1, "capacity": 0, "name": "Funkturm"},
    "medical_center": {"size": 1, "capacity": 10, "name": "Krankenstation"},
    "mining_rig": {"size": 1, "capacity": 0, "name": "Bergbauanlage"},
    "battery_bank": {"size": 1, "capacity": 0, "name": "Energiespeicher"},
}

MAX_NEW_HABITATS_PER_DAY = 6
GRID_RADIUS = 14  # Zellen in jede Richtung


def new_world(cfg: dict) -> dict:
    world = {
        "version": 1,
        "seed": cfg["seed"],
        "day": 0,
        "population": 0,
        "max_population": 0,
        "followers_history": [],
        "buildings": [],
        "milestones_reached": [],
        "votes": [],
        "pending_vote": None,
        "episodes": [],
        "paused": None,
    }
    # Startzustand: ein kleiner Außenposten.
    for btype in ("lander", "hab_small", "hab_small", "solar_small", "oxygen_plant"):
        add_building(world, btype, source="start", day=0)
    # Zentrum für das spätere Hauptquartier freihalten.
    return world


def load_world(cfg: dict) -> dict:
    world = read_json(WORLD_PATH)
    if world is None:
        world = new_world(cfg)
        save_world(world)
    return world


def save_world(world: dict) -> None:
    write_json(WORLD_PATH, world)


def _occupied(world: dict) -> set[tuple[int, int]]:
    cells = set()
    for b in world["buildings"]:
        for c in b.get("cells", []):
            cells.add(tuple(c))
    return cells


def _spiral_cells():
    cells = [(x, y) for x in range(-GRID_RADIUS, GRID_RADIUS + 1)
             for y in range(-GRID_RADIUS, GRID_RADIUS + 1)]
    cells.sort(key=lambda c: (round(math.hypot(*c), 3), math.atan2(c[1], c[0])))
    return cells


RESERVED_CENTER = {(0, 0), (1, 0), (0, 1), (1, 1)}  # 2x2 für das Hauptquartier


def _find_slot(world: dict, size: int, btype: str):
    occ = _occupied(world)
    if btype == "hq":
        if not (RESERVED_CENTER & occ):
            return [list(c) for c in sorted(RESERVED_CENTER)]
    blocked = occ | (set() if btype == "hq" else RESERVED_CENTER)
    for (x, y) in _spiral_cells():
        cells = [(x + dx, y + dy) for dx in range(size) for dy in range(size)]
        if all(abs(cx) <= GRID_RADIUS and abs(cy) <= GRID_RADIUS and (cx, cy) not in blocked
               for cx, cy in cells):
            return [list(c) for c in cells]
    raise RuntimeError("Kein freier Bauplatz mehr auf dem Raster.")


def add_building(world: dict, btype: str, source: str, day: int, label: str | None = None) -> dict:
    spec = BUILDING_SPECS[btype]
    index = len(world["buildings"]) + 1
    bid = f"b{index:04d}"
    rot = (index * 37 + world["seed"]) % 4 * 90
    if spec["size"] == 0:
        cells = []
        if btype == "mega_dome":
            pos = [0.0, 0.0]
        else:  # zweite Stadt am Horizont
            pos = [260.0, 180.0]
    else:
        cells = _find_slot(world, spec["size"], btype)
        xs = [c[0] for c in cells]
        ys = [c[1] for c in cells]
        pos = [sum(xs) / len(xs), sum(ys) / len(ys)]  # in Zellen, Blender multipliziert
    building = {
        "id": bid,
        "type": btype,
        "name": label or spec["name"],
        "cells": cells,
        "pos": pos,
        "pos_unit": "cell" if cells else "meter",
        "rot": rot,
        "built_day": day,
        "source": source,
    }
    world["buildings"].append(building)
    return building


def capacity(world: dict) -> int:
    return sum(BUILDING_SPECS[b["type"]]["capacity"] for b in world["buildings"])


def apply_followers(world: dict, cfg: dict, followers: int, day: int) -> list[dict]:
    """Einwohner = Follower. Meilensteine und Wohnmodule werden dauerhaft gebaut."""
    events: list[dict] = []
    previous = world["population"]
    world["population"] = followers
    world["max_population"] = max(world["max_population"], followers)
    world["followers_history"].append({"day": day, "followers": followers})
    events.append({"kind": "population", "before": previous, "after": followers, "delta": followers - previous})

    for m in cfg["milestones"]:
        if world["max_population"] >= m["followers"] and m["followers"] not in world["milestones_reached"]:
            b = add_building(world, m["type"], source="milestone", day=day, label=m["name"])
            world["milestones_reached"].append(m["followers"])
            events.append({"kind": "milestone", "threshold": m["followers"], "building": b["id"], "name": m["name"]})

    added = 0
    while capacity(world) < world["max_population"] and added < MAX_NEW_HABITATS_PER_DAY:
        b = add_building(world, "hab_small", source="growth", day=day)
        added += 1
        events.append({"kind": "habitat", "building": b["id"]})
    return events


def snapshot(world: dict) -> dict:
    return deepcopy(world)
