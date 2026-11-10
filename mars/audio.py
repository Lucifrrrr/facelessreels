"""Narrator (Piper, offline), selbst erzeugte Musik und Soundeffekte.

Musik und Effekte werden mathematisch erzeugt, daher bestehen keine fremden
Rechte. Die Stimme stammt aus dem freien Piper-Modell "thorsten" (CC0).
"""

from __future__ import annotations

import math
import wave
from pathlib import Path

import numpy as np

from .common import ASSET_DIR

SR = 44100
_VOICE = None


# --------------------------------------------------------------------------- Narrator

def _voice(name: str):
    global _VOICE
    if _VOICE is None:
        from piper import PiperVoice
        _VOICE = PiperVoice.load(str(ASSET_DIR / "voices" / f"{name}.onnx"))
    return _VOICE


def speak_elevenlabs(text: str, out: Path, tts: dict, speed: float = 1.0,
                     previous_text: str | None = None, next_text: str | None = None) -> float:
    """ElevenLabs (natürliche Erzählerstimme). Fester voice_id = gleichbleibende Stimme."""
    import json
    import subprocess
    import urllib.request

    from .common import secret
    from .compose import tool
    e = tts["elevenlabs"]
    body = {
        "text": text,
        "model_id": e.get("model_id", "eleven_multilingual_v2"),
        "language_code": "de",
        "voice_settings": {"stability": e.get("stability", 0.45), "similarity_boost": e.get("similarity_boost", 0.8),
                           "style": e.get("style", 0.35), "use_speaker_boost": True,
                           "speed": max(0.7, min(1.2, speed))},
    }
    if previous_text:
        body["previous_text"] = previous_text
    if next_text:
        body["next_text"] = next_text
    req = urllib.request.Request(
        f"https://api.elevenlabs.io/v1/text-to-speech/{e['voice_id']}?output_format=mp3_44100_192",
        data=json.dumps(body).encode(), method="POST",
        headers={"xi-api-key": secret("ELEVENLABS_API_KEY"), "Content-Type": "application/json",
                 "Accept": "audio/mpeg"})
    with urllib.request.urlopen(req, timeout=120) as r:
        mp3 = r.read()
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".mp3")
    tmp.write_bytes(mp3)
    subprocess.run([tool("ffmpeg"), "-y", "-loglevel", "error", "-i", str(tmp), "-ar", "44100", "-ac", "1", str(out)],
                   check=True)
    return wav_seconds(out)


def speak_any(text: str, out: Path, tts: dict, speed: float = 1.0, **ctx) -> tuple[float, str]:
    """ElevenLabs bevorzugt, bei Fehler/Kontingent automatisch Piper. Gibt (Dauer, Engine) zurück."""
    if tts.get("engine") == "elevenlabs":
        try:
            return speak_elevenlabs(text, out, tts, speed, **ctx), "elevenlabs"
        except Exception as e:  # noqa: BLE001
            from .common import get_logger
            get_logger().warning("ElevenLabs nicht verfügbar (%s) – nutze Piper", str(e)[:200])
    return speak(text, out, tts["voice"], 1 / speed), "piper"


def speak(text: str, out: Path, voice: str, length_scale: float = 1.0) -> float:
    """Text als WAV sprechen, gibt die Dauer in Sekunden zurück."""
    from piper import SynthesisConfig
    out.parent.mkdir(parents=True, exist_ok=True)
    v = _voice(voice)
    cfg = SynthesisConfig(length_scale=length_scale, noise_scale=0.6, noise_w_scale=0.7)
    with wave.open(str(out), "wb") as wf:
        v.synthesize_wav(text, wf, syn_config=cfg)
    with wave.open(str(out), "rb") as wf:
        return wf.getnframes() / wf.getframerate()


# --------------------------------------------------------------------------- Synth-Helfer

def _t(seconds: float) -> np.ndarray:
    return np.arange(int(seconds * SR)) / SR


def _lowpass(x: np.ndarray, cutoff: float) -> np.ndarray:
    spec = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1 / SR)
    spec *= 1 / np.sqrt(1 + (f / cutoff) ** 4)
    return np.fft.irfft(spec, len(x))


def _bandpass(x: np.ndarray, lo: float, hi: float) -> np.ndarray:
    spec = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1 / SR)
    spec *= (1 / np.sqrt(1 + (f / hi) ** 4)) * (1 / np.sqrt(1 + (lo / np.maximum(f, 1)) ** 4))
    return np.fft.irfft(spec, len(x))


def _reverb(x: np.ndarray, seconds: float = 2.8, mix: float = 0.35, seed: int = 1) -> np.ndarray:
    rng = np.random.default_rng(seed)
    n = int(seconds * SR)
    ir = rng.standard_normal(n) * np.exp(-np.linspace(0, 7, n))
    ir = _lowpass(ir, 5000)
    ir /= np.sqrt(np.sum(ir ** 2))
    size = len(x) + n
    wet = np.fft.irfft(np.fft.rfft(x, size) * np.fft.rfft(ir, size), size)[: len(x)]
    return x * (1 - mix) + wet * mix


def _saw(freq: float, t: np.ndarray, detune: float = 0.0) -> np.ndarray:
    ph = (freq * (1 + detune)) * t
    return 2 * (ph - np.floor(ph + 0.5))


def _note(n: int) -> float:
    return 440.0 * 2 ** ((n - 69) / 12)


# d-Moll Progression: Dm – Bb – F – C (episch, hoffnungsvoll)
CHORDS = [[50, 53, 57, 62], [46, 50, 53, 58], [41, 45, 48, 53], [48, 52, 55, 60]]


def music(seconds: float, out: Path, seed: int = 0, bpm: float = 96, accents: list[float] | None = None) -> Path:
    rng = np.random.default_rng(seed)
    total = _t(seconds + 3)
    n = len(total)
    pad = np.zeros(n)
    bass = np.zeros(n)
    arp = np.zeros(n)
    perc = np.zeros(n)
    beat = 60 / bpm
    bar = beat * 4
    offset = int(rng.integers(0, 4))
    chord_len = bar * 2
    i = 0
    start = 0.0
    while start < seconds + 3:
        chord = CHORDS[(i + offset) % len(CHORDS)]
        s0, s1 = int(start * SR), min(n, int((start + chord_len + 1.5) * SR))
        tt = total[s0:s1] - start
        env = np.minimum(1, tt / 1.6) * np.clip((chord_len + 1.5 - tt) / 1.5, 0, 1)
        for note in chord:
            f = _note(note)
            pad[s0:s1] += (_saw(f, tt, 0.003) + _saw(f, tt, -0.004)) * env * 0.08
        fb = _note(chord[0] - 12)
        bass[s0:s1] += np.sin(2 * np.pi * fb * tt) * env * 0.35
        # Arpeggio in Achteln
        steps = int(chord_len / (beat / 2))
        for k in range(steps):
            ts = start + k * beat / 2
            a0 = int(ts * SR)
            if a0 >= n:
                break
            dur = int(0.5 * SR)
            a1 = min(n, a0 + dur)
            tn = total[a0:a1] - ts
            note = chord[[0, 2, 1, 3, 2, 1, 3, 2][k % 8]] + 12
            amp = 0.11 if k % 2 == 0 else 0.07
            arp[a0:a1] += np.sin(2 * np.pi * _note(note) * tn) * np.exp(-tn * 7) * amp
        start += chord_len
        i += 1
    # Leichte Percussion ab Sekunde 4: Hi-Hat-Rauschen auf Achteln, Kick auf 1 und 3
    hat = rng.standard_normal(int(0.05 * SR))
    hat = _bandpass(hat, 6000, 14000) * np.exp(-np.linspace(0, 8, len(hat)))
    kick_t = _t(0.35)
    kick = np.sin(2 * np.pi * (45 + 80 * np.exp(-kick_t * 25)) * kick_t) * np.exp(-kick_t * 9)
    tb = 4.0
    while tb < seconds:
        k0 = int(tb * SR)
        perc[k0:k0 + len(hat)] += hat[: max(0, min(len(hat), n - k0))] * 0.05
        if round((tb - 4.0) / (beat / 2)) % 4 == 0:
            perc[k0:k0 + len(kick)] += kick[: max(0, min(len(kick), n - k0))] * 0.5
        tb += beat / 2
    pad = _lowpass(pad, 1800)
    mix = pad + bass + _reverb(arp, 2.0, 0.4, seed) + perc
    for acc in accents or []:
        mix += _boom(n, acc)
    mix = _reverb(mix, 3.0, 0.2, seed + 1)
    fade = np.ones(n)
    fi, fo = int(1.2 * SR), int(2.5 * SR)
    end = int(seconds * SR)
    fade[:fi] = np.linspace(0, 1, fi)
    fade[end - fo:end] = np.linspace(1, 0, fo)
    fade[end:] = 0
    mix = (mix * fade)[:end]
    return _write(out, mix, peak=0.8)


def _boom(n: int, at: float) -> np.ndarray:
    out = np.zeros(n)
    t = _t(2.5)
    b = np.sin(2 * np.pi * (38 + 40 * np.exp(-t * 6)) * t) * np.exp(-t * 2.2) * 0.6
    s = int(at * SR)
    m = min(len(b), n - s)
    if m > 0:
        out[s:s + m] = b[:m]
    return out


# --------------------------------------------------------------------------- Effekte

def sfx_whoosh(out: Path, seconds: float = 1.2, seed: int = 3) -> Path:
    rng = np.random.default_rng(seed)
    t = _t(seconds)
    x = rng.standard_normal(len(t))
    env = np.sin(np.pi * t / seconds) ** 2
    x = _bandpass(x, 300, 3000) * env
    return _write(out, x, peak=0.6)


def sfx_build(out: Path, seed: int = 4) -> Path:
    rng = np.random.default_rng(seed)
    t = _t(3.0)
    rumble = _lowpass(rng.standard_normal(len(t)), 140) * np.minimum(1, t / 0.4) * np.exp(-t * 1.2) * 3
    clank = np.zeros(len(t))
    for k, at in enumerate((0.25, 0.7, 1.1, 1.5)):
        s = int(at * SR)
        tn = t[: len(t) - s]
        clank[s:] += np.sin(2 * np.pi * (820 + k * 140) * tn) * np.exp(-tn * 18) * 0.25
    chime_t = t[int(1.9 * SR):] - 1.9
    chime = np.zeros(len(t))
    for f in (880, 1318.5, 1760):
        chime[int(1.9 * SR):] += np.sin(2 * np.pi * f * chime_t) * np.exp(-chime_t * 2.5) * 0.18
    return _write(out, _reverb(rumble + clank + chime, 1.6, 0.3), peak=0.8)


def sfx_tick(out: Path) -> Path:
    t = _t(0.08)
    x = np.sin(2 * np.pi * 2200 * t) * np.exp(-t * 60)
    return _write(out, x, peak=0.4)


def sfx_vote(out: Path) -> Path:
    t = _t(0.9)
    x = np.zeros(len(t))
    for i, f in enumerate((660, 990)):
        s = int(i * 0.12 * SR)
        tn = t[: len(t) - s]
        x[s:] += np.sin(2 * np.pi * f * tn) * np.exp(-tn * 6)
    return _write(out, _reverb(x, 1.0, 0.3), peak=0.5)


def _write(out: Path, x: np.ndarray, peak: float = 0.8) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    x = x / (np.max(np.abs(x)) + 1e-9) * peak
    stereo = np.stack([x, np.roll(x, int(0.012 * SR))], axis=1)
    data = (np.clip(stereo, -1, 1) * 32767).astype("<i2")
    with wave.open(str(out), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes(data.tobytes())
    return out


def wav_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as wf:
        return wf.getnframes() / wf.getframerate()


def silence_gap() -> float:
    return 0.35


__all__ = ["speak", "music", "sfx_whoosh", "sfx_build", "sfx_tick", "sfx_vote", "wav_seconds", "math"]
