"""Täglicher Produktionsablauf der Marskolonie.

Jeder Schritt schreibt einen Checkpoint (state/mars/runs/<datum>.json). Ein
abgebrochener Lauf wird beim nächsten Start an derselben Stelle fortgesetzt.
Der dauerhafte Weltzustand (world.json) wird erst nach erfolgreichem Abschluss
übernommen, damit nichts doppelt gezählt oder gebaut wird.

  python -m mars.pipeline --mode test --followers 137       # Test-Reel, keine Veröffentlichung
  python -m mars.pipeline --mode publish                    # Täglicher Betrieb
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import sys
import time
import traceback
from datetime import timedelta
from pathlib import Path

from . import audio, budget, compose, story, votes, world as W
from .common import STATE_DIR, WORK_DIR, get_logger, load_config, now, read_json, write_json
from .notify import alert

log = get_logger()
RUNS_DIR = STATE_DIR / "runs"
ACTIVATION = STATE_DIR / "activation.json"


class Fatal(RuntimeError):
    """Schwerer Fehler: System pausiert, bis ein Mensch es freigibt."""


def retry(fn, what: str, attempts: int = 3, delay: int = 20):
    for i in range(attempts):
        try:
            return fn()
        except Fatal:
            raise
        except Exception as e:  # noqa: BLE001
            if i == attempts - 1:
                raise
            log.warning("%s fehlgeschlagen (%s), neuer Versuch in %ss", what, e, delay * (i + 1))
            time.sleep(delay * (i + 1))


class Run:
    def __init__(self, cfg: dict, mode: str, date: str, followers_override: int | None, skip_higgs: bool):
        self.cfg, self.mode, self.date = cfg, mode, date
        self.followers_override, self.skip_higgs = followers_override, skip_higgs
        self.path = RUNS_DIR / f"{date}-{mode}.json"
        self.cp = read_json(self.path, {"date": date, "mode": mode, "steps": {}})
        self.work = WORK_DIR / f"{date}-{mode}"
        self.work.mkdir(parents=True, exist_ok=True)

    def save(self):
        write_json(self.path, self.cp)

    def step(self, name: str, fn):
        if name in self.cp["steps"]:
            log.info("✓ %s (aus Checkpoint)", name)
            return self.cp["steps"][name]
        log.info("→ %s", name)
        t = time.time()
        result = fn()
        self.cp["steps"][name] = result if result is not None else True
        self.cp.setdefault("timings", {})[name] = round(time.time() - t, 1)
        self.save()
        return self.cp["steps"][name]

    # ------------------------------------------------------------------ Schritte

    def s_followers(self):
        if self.followers_override is not None:
            return {"followers": self.followers_override, "source": "override"}
        from . import instagram as ig
        return {"followers": retry(lambda: ig.followers(self.cfg), "Followerzahl"), "source": "instagram"}

    def s_update_world(self):
        world = copy.deepcopy(W.load_world(self.cfg))
        day = world["day"] + 1
        self.cp["day"] = day
        vote_result, won = None, None
        pv = world.get("pending_vote")
        if pv and pv.get("media_id"):
            if self.followers_override is None:
                from . import instagram as ig
                cm = retry(lambda: ig.comments(self.cfg, pv["media_id"]), "Kommentare")
            else:
                cm = []
            vote_result = votes.count_votes(cm, pv["opens_at"], pv["closes_at"], self.cfg["vote"]["tie_rule"])
            btype = pv["options"][vote_result["winner"]]
            won = W.add_building(world, btype, source="vote", day=day)
            pv["result"] = vote_result
            pv["building"] = won["id"]
            world["votes"].append(pv)
            world["pending_vote"] = None
        elif pv:
            # Abstimmung wurde nie veröffentlicht (z. B. Testlauf) – verwerfen.
            world["pending_vote"] = None
        events = W.apply_followers(world, self.cfg, self.cp["steps"]["followers"]["followers"], day)
        if won:
            events.append({"kind": "vote", "building": won["id"], "winner": vote_result["winner"]})
        world["day"] = day
        new_vote = votes.new_vote(world, self.cfg, day)
        return {"world": world, "events": events, "vote_result": vote_result,
                "won_building": won, "new_vote": new_vote, "day": day}

    def s_script(self):
        u = self.cp["steps"]["update_world"]
        return story.build(u["world"], self.cfg, u["day"], u["events"], u["vote_result"], u["won_building"], u["new_vote"])

    def s_voice(self):
        sc = self.cp["steps"]["script"]
        t = self.cfg["tts"]
        speed = 1 / t["length_scale"]
        texts = [s["text"] for s in sc["segments"]]
        for _ in range(3):
            segs, engines = [], set()
            for i, s in enumerate(sc["segments"]):
                f = self.work / "voice" / f"{s['id']}.wav"
                d, engine = audio.speak_any(s["text"], f, t, speed,
                                            previous_text=" ".join(texts[:i]) or None,
                                            next_text=" ".join(texts[i + 1:]) or None)
                engines.add(engine)
                segs.append({"id": s["id"], "file": str(f), "seconds": round(d, 3), "engine": engine})
            if len(engines) > 1:
                # Nie zwei verschiedene Stimmen in einem Reel: komplett mit Piper wiederholen.
                t = dict(t, engine="piper")
                continue
            total = self._plan_total(segs)
            if total <= self.cfg["video"]["max_seconds"] - 0.5:
                break
            speed *= 1.08
            log.info("Narration zu lang (%.1fs), spreche schneller (%.2f)", total, speed)
        return {"segments": segs, "speed": speed, "engine": engines.pop()}

    def _plan_total(self, segs):
        return sum(self._shot_seconds(s) for s in segs)

    def _shot_seconds(self, s):
        lead, tail = 0.35, 0.45
        base = s["seconds"] + lead + tail
        if s["id"] == "cinematic":
            return max(5.0, base)
        if s["id"] == "vote":
            return base + 2.2
        return base

    def s_timeline(self):
        v = self.cfg["video"]
        segs = self.cp["steps"]["voice"]["segments"]
        lengths = {s["id"]: self._shot_seconds(s) for s in segs}
        total = sum(lengths.values())
        target_min = v["min_seconds"] + 1.0
        if total < target_min:
            extra = target_min - total
            for sid, share in (("intro", 0.4), ("colony", 0.35), ("vote", 0.25)):
                lengths[sid] += extra * share
        sc = self.cp["steps"]["script"]
        timeline, t = [], 0.0
        for s, seg in zip(segs, sc["segments"]):
            dur = round(lengths[s["id"]], 3)
            timeline.append({"id": s["id"], "start": round(t, 3), "end": round(t + dur, 3), "seconds": dur,
                             "voice_file": s["file"], "voice_start": round(t + 0.35, 3),
                             "voice_seconds": s["seconds"], "subtitle": seg["subtitle"], "shot": seg["shot"],
                             "building": seg.get("building")})
            t += dur
        return {"timeline": timeline, "total": round(t, 3)}

    def s_higgsfield(self):
        if self.skip_higgs or not self.cfg["higgsfield"]["enabled"]:
            return {"ok": False, "reason": "deaktiviert"}
        from . import higgs
        out = self.work / "higgsfield.mp4"
        try:
            higgs.generate_clip(self.cfg, self.cp["steps"]["script"]["higgsfield_prompt"], out, self.cp, self.save)
            return {"ok": True, "file": str(out)}
        except budget.BudgetExceeded as e:
            alert(f"Budgetgrenze: {e}. Reel nutzt Blender-Ersatzszene.", level="warning")
            return {"ok": False, "reason": str(e)}
        except Exception as e:  # noqa: BLE001
            log.warning("Higgsfield ausgefallen, nutze Blender-Ersatz: %s", e)
            return {"ok": False, "reason": str(e)[:300]}

    def s_render(self):
        tl = self.cp["steps"]["timeline"]["timeline"]
        higgs_ok = self.cp["steps"]["higgsfield"]["ok"]
        u = self.cp["steps"]["update_world"]
        new_ids = [e["building"] for e in u["events"] if e.get("building")]
        angle0 = (u["day"] * 47) % 360
        shots = []
        for seg in tl:
            if seg["id"] == "cinematic" and higgs_ok:
                continue
            kind = {"orbit": "orbit", "flyto": "flyto", "pan": "pan", "pullout": "pullout",
                    "higgsfield": "orbit"}[seg["shot"]]
            shot = {"name": seg["id"], "type": kind, "seconds": seg["seconds"], "angle": angle0 + len(shots) * 70}
            if kind == "flyto":
                shot["building"] = seg.get("building") or (new_ids[-1] if new_ids else None)
                if not shot["building"]:
                    shot["type"] = "pan"
            if seg["id"] == "cinematic":  # Blender-Ersatz: tiefe, filmische Fahrt
                shot["lens"] = 85
            shots.append(shot)
        vis = min(self.cfg["colony"]["max_visible_colonists"], max(6, u["world"]["population"]))
        plan = {"fps": self.cfg["video"]["fps"], "width": self.cfg["video"]["width"],
                "height": self.cfg["video"]["height"], "shots": shots, "new_buildings": new_ids,
                "build_shot": "build", "build_delay": 1.0, "visible_colonists": vis}
        world_file = self.work / "world_render.json"
        write_json(world_file, u["world"])
        plan_file = self.work / "shots.json"
        write_json(plan_file, plan)
        frames = self.work / "frames"
        r = self.cfg["render"]
        cmd = [compose.tool("blender"), "-b", "--factory-startup", "-P",
               str(Path(__file__).resolve().parent.parent / "blender" / "colony_scene.py"), "--",
               "--world", str(world_file), "--plan", str(plan_file), "--out", str(frames),
               "--engine", r.get("engine", "EEVEE"), "--samples", str(r["samples"]),
               "--scale", str(r.get("preview_scale", 100))]
        retry(lambda: compose.run(cmd, timeout=4 * 3600), "Blender-Rendering", attempts=2)
        return {"frames": str(frames), "shots": [s["name"] for s in shots]}

    def s_audio_assets(self):
        tl = self.cp["steps"]["timeline"]
        by = {s["id"]: s for s in tl["timeline"]}
        build_t = by["build"]["start"] + 1.0
        d = self.work / "audio"
        audio.music(tl["total"], d / "music.wav", seed=self.cp["day"], accents=[build_t])
        audio.sfx_whoosh(d / "whoosh.wav")
        audio.sfx_build(d / "build.wav")
        audio.sfx_vote(d / "vote.wav")
        return {"dir": str(d), "build_t": build_t}

    def s_compose(self):
        v = self.cfg["video"]
        tl = self.cp["steps"]["timeline"]
        frames = Path(self.cp["steps"]["render"]["frames"])
        clips = []
        for seg in tl["timeline"]:
            out = self.work / "clips" / f"{seg['id']}.mp4"
            out.parent.mkdir(parents=True, exist_ok=True)
            if seg["id"] == "cinematic" and self.cp["steps"]["higgsfield"]["ok"]:
                compose.conform_clip(Path(self.cp["steps"]["higgsfield"]["file"]), out, seg["seconds"],
                                     v["width"], v["height"], v["fps"])
            else:
                compose.frames_to_clip(frames / seg["id"], out, v["fps"])
            clips.append(out)
        sc = self.cp["steps"]["script"]
        ass = compose.build_ass(self.work / "overlay.ass", tl["timeline"], sc, tl["total"], v["width"], v["height"])
        a = self.cp["steps"]["audio_assets"]
        ad = Path(a["dir"])
        sfx = [(ad / "whoosh.wav", max(0, s["start"] - 0.5), 0.45) for s in tl["timeline"][1:]]
        sfx.append((ad / "build.wav", a["build_t"], 0.8))
        vote = tl["timeline"][-1]
        sfx.append((ad / "vote.wav", vote["start"] + vote["seconds"] * 0.35, 0.6))
        out = self.work / f"mars_tag_{self.cp['day']:03d}.mp4"
        compose.final_mix(clips, tl["timeline"], ad / "music.wav", sfx, ass, out, tl["total"], v["fps"])
        return {"video": str(out)}

    def s_qa(self):
        video = Path(self.cp["steps"]["compose"]["video"])
        q = compose.quality_check(video, self.cfg)
        sc = self.cp["steps"]["script"]
        if not sc["segments"][-1]["text"].count(" A ") and "A für" not in sc["segments"][-1]["text"]:
            q["problems"].append("Abstimmungsfrage fehlt")
        q["ok"] = not q["problems"]
        if not q["ok"]:
            raise RuntimeError("Videoprüfung fehlgeschlagen: " + "; ".join(q["problems"]))
        return q

    def s_publish(self):
        if self.mode != "publish":
            return {"published": False, "reason": "Testlauf"}
        act = read_json(ACTIVATION, {})
        if not act.get("public_publishing_enabled"):
            raise Fatal("Öffentliche Veröffentlichung ist noch nicht freigegeben (state/mars/activation.json).")
        from . import instagram as ig
        ig.refresh_token_if_needed(self.cfg)
        tag = f"#MarsKolonieTag{self.cp['day']}"
        existing = ig.already_published(self.cfg, tag)
        if existing:
            log.warning("Reel %s ist bereits veröffentlicht – keine Doppelung.", tag)
            return {"published": True, "media_id": existing["id"], "permalink": existing.get("permalink"),
                    "duplicate_prevented": True}
        pub = self.cp.setdefault("publish_state", {})
        if not pub.get("container"):
            pub["container"] = retry(lambda: ig.create_reel_container(
                self.cfg, Path(self.cp["steps"]["compose"]["video"]), self.cp["steps"]["script"]["caption"]),
                "Reel-Upload", attempts=2, delay=60)
            self.save()
        ig.wait_container(self.cfg, pub["container"])
        info = ig.publish(self.cfg, pub["container"])
        return {"published": True, "media_id": info["id"], "permalink": info.get("permalink"),
                "timestamp": info.get("timestamp")}

    def s_commit(self):
        u = self.cp["steps"]["update_world"]
        pub = self.cp["steps"]["publish"]
        world = u["world"]
        vote = u["new_vote"]
        t = now(self.cfg)
        if pub.get("published"):
            vote.update(media_id=pub["media_id"], opens_at=t.isoformat(timespec="seconds"),
                        closes_at=(t + timedelta(hours=23, minutes=30)).isoformat(timespec="seconds"))
            world["pending_vote"] = vote
        world["episodes"].append({
            "day": u["day"], "date": self.date, "mode": self.mode,
            "video": self.cp["steps"]["compose"]["video"], "published": pub.get("published", False),
            "media_id": pub.get("media_id"), "permalink": pub.get("permalink"),
            "higgsfield": self.cp["steps"]["higgsfield"].get("ok"), "population": world["population"],
            "vote_options": vote["options"],
        })
        if self.mode == "publish":
            W.save_world(world)
            write_json(STATE_DIR / "publish_log.json",
                       read_json(STATE_DIR / "publish_log.json", []) + [world["episodes"][-1]])
        else:
            write_json(self.work / "world_after_test.json", world)
        log.info(budget.report(self.cfg))
        return {"committed": self.mode == "publish"}

    def execute(self):
        self.step("followers", self.s_followers)
        self.step("update_world", self.s_update_world)
        self.cp["day"] = self.cp["steps"]["update_world"]["day"]
        self.step("script", self.s_script)
        self.step("voice", self.s_voice)
        self.step("timeline", self.s_timeline)
        self.step("higgsfield", self.s_higgsfield)
        self.step("render", self.s_render)
        self.step("audio_assets", self.s_audio_assets)
        self.step("compose", self.s_compose)
        self.step("qa", self.s_qa)
        self.step("publish", self.s_publish)
        self.step("commit", self.s_commit)
        return self.cp


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=("test", "publish"), default="test")
    p.add_argument("--followers", type=int, help="Followerzahl vorgeben (nur Test)")
    p.add_argument("--date", help="Laufdatum (Standard: heute)")
    p.add_argument("--no-higgsfield", action="store_true")
    p.add_argument("--fresh", action="store_true", help="Checkpoint dieses Tages verwerfen (nur Test)")
    a = p.parse_args(argv)
    cfg = load_config()
    world = W.load_world(cfg)
    if world.get("paused") and a.mode == "publish":
        log.error("System pausiert: %s", world["paused"])
        return 2
    date = a.date or now(cfg).strftime("%Y-%m-%d")
    if a.mode == "publish" and a.followers is not None:
        p.error("--followers ist nur im Testmodus erlaubt")
    run = Run(cfg, a.mode, date, a.followers, a.no_higgsfield)
    if a.fresh and a.mode == "test":
        run.cp = {"date": date, "mode": a.mode, "steps": {}}
        shutil.rmtree(run.work, ignore_errors=True)
        run.work.mkdir(parents=True, exist_ok=True)
    try:
        cp = run.execute()
        log.info("Fertig: %s", json.dumps({k: cp["steps"][k] for k in ("qa", "publish")}, ensure_ascii=False))
        return 0
    except Fatal as e:
        world = W.load_world(cfg)
        world["paused"] = {"reason": str(e), "date": date}
        W.save_world(world)
        alert(f"Mars Colony PAUSIERT: {e}", level="error")
        return 2
    except Exception as e:  # noqa: BLE001
        alert(f"Mars Colony Lauf {date} fehlgeschlagen: {e}\n{traceback.format_exc()[-1500:]}", level="error")
        fails = read_json(STATE_DIR / "failures.json", [])
        fails.append({"date": date, "error": str(e)[:500]})
        write_json(STATE_DIR / "failures.json", fails[-30:])
        recent = [f for f in fails[-3:] if f["date"] != date] if len(fails) >= 3 else []
        if a.mode == "publish" and len({f["date"] for f in fails[-3:]}) == 3 and len(recent) == 2:
            world = W.load_world(cfg)
            world["paused"] = {"reason": "3 Tage in Folge fehlgeschlagen", "date": date}
            W.save_world(world)
            alert("Mars Colony nach 3 Fehltagen automatisch pausiert.", level="error")
        return 1


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUTF8", "1")
    sys.exit(main())
