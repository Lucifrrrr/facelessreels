"""Videoschnitt mit FFmpeg: Einstellungen, Higgsfield-Clip, Ton, Untertitel, Overlays, Prüfung."""

from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
from pathlib import Path

from .story import digits


# --------------------------------------------------------------------------- Werkzeuge

def tool(name: str) -> str:
    env = os.environ.get(f"{name.upper()}_BIN")
    if env:
        return env
    found = shutil.which(name)
    if found:
        return found
    candidates = []
    if name in ("ffmpeg", "ffprobe"):
        candidates += glob.glob(os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet",
                                             "Packages", "Gyan.FFmpeg*", "*", "bin", f"{name}.exe"))
    if name == "blender":
        candidates += sorted(glob.glob(r"C:\Program Files\Blender Foundation\Blender *\blender.exe"), reverse=True)
    if candidates:
        return candidates[0]
    raise RuntimeError(f"{name} nicht gefunden")


def run(cmd: list[str], timeout: int = 3600) -> subprocess.CompletedProcess:
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError(f"Befehl fehlgeschlagen ({p.returncode}): {' '.join(cmd[:4])} …\n{p.stderr[-2500:]}")
    return p


def probe(path: Path) -> dict:
    p = run([tool("ffprobe"), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)])
    return json.loads(p.stdout)


def font_name() -> str:
    return "Arial" if os.name == "nt" else "DejaVu Sans"


# --------------------------------------------------------------------------- Clips

def frames_to_clip(frame_dir: Path, out: Path, fps: int) -> Path:
    frames = sorted(frame_dir.glob("*.jpg"))
    if not frames:
        raise RuntimeError(f"Keine Frames in {frame_dir}")
    listfile = frame_dir / "frames.txt"
    listfile.write_text("".join(f"file '{f.name}'\nduration {1 / fps:.6f}\n" for f in frames), encoding="utf-8")
    run([tool("ffmpeg"), "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(listfile),
         "-vf", f"fps={fps},format=yuv420p", "-c:v", "libx264", "-preset", "medium", "-crf", "16", str(out)])
    return out


def conform_clip(src: Path, out: Path, seconds: float, w: int, h: int, fps: int) -> Path:
    """Fremdclip (Higgsfield) auf 1080x1920, fps und exakte Länge bringen."""
    vf = (f"scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos,crop={w}:{h},"
          f"fps={fps},tpad=stop_mode=clone:stop_duration=10,trim=duration={seconds:.3f},setpts=PTS-STARTPTS,"
          f"eq=saturation=1.05,format=yuv420p")
    run([tool("ffmpeg"), "-y", "-loglevel", "error", "-i", str(src), "-an", "-vf", vf,
         "-c:v", "libx264", "-preset", "medium", "-crf", "16", str(out)])
    return out


# --------------------------------------------------------------------------- Untertitel & Overlays

def _ass_time(t: float) -> str:
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def _chunks(text: str, max_words: int = 4) -> list[str]:
    words = text.split()
    out, cur = [], []
    for w in words:
        cur.append(w)
        if len(cur) >= max_words or w.endswith((".", "!", "?", ":", "–")):
            out.append(" ".join(cur))
            cur = []
    if cur:
        out.append(" ".join(cur))
    return out


def build_ass(path: Path, timeline: list[dict], script: dict, total: float, w: int, h: int) -> Path:
    font = font_name()
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Sub,{font},78,&H00FFFFFF,&H00FFFFFF,&H00101010,&H96000000,1,0,0,0,100,100,0,0,1,6,2,2,80,80,620,1
Style: Title,{font},44,&H00FFFFFF,&H00FFFFFF,&H00000000,&H7A000000,1,0,0,0,100,100,4,0,3,14,0,8,60,60,190,1
Style: Counter,{font},96,&H0045C8FF,&H0045C8FF,&H00101010,&H96000000,1,0,0,0,100,100,0,0,1,7,3,8,60,60,330,1
Style: Label,{font},46,&H00FFFFFF,&H00FFFFFF,&H00101010,&H96000000,1,0,0,0,100,100,2,0,1,4,2,8,60,60,450,1
Style: Banner,{font},62,&H00FFFFFF,&H00FFFFFF,&H000A6AFF,&H000A6AFF,1,0,0,0,100,100,2,0,3,22,0,8,60,60,330,1
Style: Vote,{font},70,&H00FFFFFF,&H00FFFFFF,&H00000000,&HB4000000,1,0,0,0,100,100,0,0,3,24,0,5,70,70,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ev = []

    def add(start, end, style, text, layer=0):
        ev.append(f"Dialogue: {layer},{_ass_time(start)},{_ass_time(end)},{style},,0,0,0,,{text}")

    add(0, total, "Title", f"MARS-KOLONIE · TAG {script['day']}")
    for seg in timeline:
        text = seg["subtitle"]
        chunks = _chunks(text)
        start, dur = seg["voice_start"], seg["voice_seconds"]
        weights = [len(c) + 4 for c in chunks]
        acc = start
        for c, wgt in zip(chunks, weights):
            d = dur * wgt / sum(weights)
            add(acc, acc + d, "Sub", "{\\fad(60,60)}" + c.replace("{", "(").replace("}", ")"))
            acc += d

    by_id = {s["id"]: s for s in timeline}
    # Einwohnerzähler im Kolonie-Abschnitt (zählt hoch)
    col = by_id["colony"]
    a, b = script["population_before"], script["population"]
    steps = 24
    t0, span = col["start"] + 0.3, min(2.5, col["seconds"] - 0.6)
    for i in range(steps):
        v = round(a + (b - a) * (1 - (1 - (i + 1) / steps) ** 3))
        s = t0 + span * i / steps
        e = t0 + span * (i + 1) / steps if i < steps - 1 else col["end"]
        add(s, e, "Counter", f"{digits(v)}", layer=2)
    add(t0, col["end"], "Label", "KOLONISTEN", layer=2)

    if script.get("milestone"):
        bs = by_id["build"]
        add(bs["start"] + 0.8, bs["end"] - 0.2, "Banner",
            "{\\fad(250,250)}MEILENSTEIN ERREICHT\\N" + script["milestone"].upper(), layer=3)

    vs = by_id["vote"]
    ts = vs["start"] + vs["seconds"] * 0.35
    add(ts, total, "Vote",
        "{\\fad(300,0)\\pos(%d,%d)}JETZT ABSTIMMEN\\N\\N{\\c&H45C8FF&}A{\\c&HFFFFFF&}  %s\\N{\\c&H45C8FF&}B{\\c&HFFFFFF&}  %s"
        % (w // 2, int(h * 0.44), _short(script["vote"]["A"]), _short(script["vote"]["B"])), layer=4)
    path.write_text(header + "\n".join(ev) + "\n", encoding="utf-8-sig")
    return path


def _short(label: str) -> str:
    from .story import short_label
    return short_label(label)


# --------------------------------------------------------------------------- Endschnitt

def final_mix(clips: list[Path], timeline: list[dict], music: Path, sfx: list[tuple[Path, float, float]],
              ass: Path, out: Path, total: float, fps: int) -> Path:
    ff = tool("ffmpeg")
    work = out.parent
    concat = work / "concat.txt"
    concat.write_text("".join(f"file '{c.resolve().as_posix()}'\n" for c in clips), encoding="utf-8")
    video_only = work / "video_only.mp4"
    run([ff, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(concat), "-c", "copy", str(video_only)])

    # Schritt 1: Ton separat mischen (vermeidet Deadlocks in großen Filtergraphen).
    inputs = ["-i", str(music)]
    filters = []
    labels = []
    idx = 1
    for seg in timeline:
        inputs += ["-i", str(seg["voice_file"])]
        ms = int(seg["voice_start"] * 1000)
        filters.append(f"[{idx}:a]aresample=44100,aformat=channel_layouts=stereo,adelay={ms}|{ms},apad[v{idx}]")
        labels.append(f"[v{idx}]")
        idx += 1
    filters.append(f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0,atrim=0:{total:.3f}[voice]")
    sfx_labels = []
    for path, at, vol in sfx:
        inputs += ["-i", str(path)]
        ms = int(at * 1000)
        filters.append(f"[{idx}:a]aresample=44100,adelay={ms}|{ms},volume={vol},apad[s{idx}]")
        sfx_labels.append(f"[s{idx}]")
        idx += 1
    filters.append("[voice]asplit=2[voice1][voicesc]")
    filters.append(f"[0:a]aresample=44100,aformat=channel_layouts=stereo,volume=0.55,apad,atrim=0:{total:.3f}[mus]")
    filters.append("[mus][voicesc]sidechaincompress=threshold=0.03:ratio=6:attack=20:release=400[duck]")
    mix_in = "[voice1][duck]" + "".join(sfx_labels)
    filters.append(f"{mix_in}amix=inputs={2 + len(sfx_labels)}:normalize=0,atrim=0:{total:.3f},"
                   f"loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[aout]")
    mix_wav = work / "mix.wav"
    run([ff, "-y", "-loglevel", "error", *inputs, "-filter_complex", ";".join(filters),
         "-map", "[aout]", "-t", f"{total:.3f}", "-c:a", "pcm_s16le", str(mix_wav)], timeout=600)

    # Schritt 2: Bild mit Untertiteln/Overlays + fertigen Ton zusammenführen.
    ass_path = ass.resolve().as_posix().replace(":", "\\:")
    run([ff, "-y", "-loglevel", "error", "-i", str(video_only), "-i", str(mix_wav),
         "-vf", f"subtitles='{ass_path}',format=yuv420p", "-map", "0:v", "-map", "1:a", "-t", f"{total:.3f}",
         "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-profile:v", "high", "-pix_fmt", "yuv420p",
         "-r", str(fps), "-g", str(fps * 2),
         "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", str(out)], timeout=1800)
    return out


# --------------------------------------------------------------------------- Prüfung

def quality_check(path: Path, cfg: dict) -> dict:
    v = cfg["video"]
    info = probe(path)
    vs = [s for s in info["streams"] if s["codec_type"] == "video"]
    aus = [s for s in info["streams"] if s["codec_type"] == "audio"]
    dur = float(info["format"]["duration"])
    size = int(info["format"]["size"])
    problems = []
    if not vs:
        problems.append("keine Videospur")
    else:
        s = vs[0]
        if (s["width"], s["height"]) != (v["width"], v["height"]):
            problems.append(f"Auflösung {s['width']}x{s['height']}")
        if s["codec_name"] != "h264":
            problems.append(f"Codec {s['codec_name']}")
    if not aus:
        problems.append("keine Tonspur")
    if not (v["min_seconds"] <= dur <= v["max_seconds"]):
        problems.append(f"Dauer {dur:.1f}s außerhalb {v['min_seconds']}–{v['max_seconds']}s")
    if size > v["max_bytes"]:
        problems.append(f"Datei zu groß ({size / 1e6:.0f} MB)")
    # Lautstärke und schwarze Bilder
    p = subprocess.run([tool("ffmpeg"), "-hide_banner", "-i", str(path), "-af", "volumedetect",
                        "-vf", "blackdetect=d=1.0:pix_th=0.08", "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    mean_vol = None
    for line in p.stderr.splitlines():
        if "mean_volume" in line:
            mean_vol = float(line.split("mean_volume:")[1].split("dB")[0])
        if "black_start" in line:
            problems.append("schwarze Bildstrecke: " + line.strip()[-60:])
    if mean_vol is None or mean_vol < -35:
        problems.append(f"Ton zu leise ({mean_vol} dB)")
    return {"ok": not problems, "problems": problems, "duration": round(dur, 2), "bytes": size,
            "mean_volume_db": mean_vol}
