"""Background Music Studio - free, local, license-clean.

Every track is SYNTHESIZED locally with numpy (no downloads, no licensing
risk: the app authors it). Categories cover common moods; each renders a
seamless loop cached under app/assets/music/.

The actual mixing (volume, fade-in/out, ducking under narration) happens in
renderer.render_clip via ffmpeg filters.
"""
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

from .. import config

SR = 44100

CATEGORIES: dict[str, dict] = {
    "ambient": {
        "label": "Ambient Calm",
        "desc": "Soft chord pad, neutral - fits narration of any kind",
        "seconds": 32,
    },
    "chill": {
        "label": "Chill Lo-Fi",
        "desc": "Relaxed pace, gentle warmth - vlogs and stories",
        "seconds": 24,
    },
    "upbeat": {
        "label": "Upbeat Light",
        "desc": "Subtle pulse with brighter chords - gaming and action",
        "seconds": 24,
    },
    "cinematic": {
        "label": "Cinematic Low",
        "desc": "Deep drone with slow swells - documentaries",
        "seconds": 40,
    },
}


def _pad_tone(freq: float, t: np.ndarray, amp: float,
              lfo_rate: float, phase: float) -> np.ndarray:
    lfo = 0.75 + 0.25 * np.sin(2 * np.pi * lfo_rate * t + phase)
    detune = 1.0 + 0.0015 * np.sin(2 * np.pi * 0.1 * t + phase * 1.7)
    tone = np.sin(2 * np.pi * freq * detune * t)
    tone += 0.35 * np.sin(2 * np.pi * freq * 2 * detune * t + phase)
    return amp * lfo * tone


def _chord_pad(chords, seconds, shimmer=False) -> tuple[np.ndarray, np.ndarray]:
    t_full = np.arange(int(SR * seconds)) / SR
    left = np.zeros_like(t_full)
    right = np.zeros_like(t_full)
    seg = seconds / len(chords)
    for i, chord in enumerate(chords):
        mask = (t_full >= i * seg) & (t_full < (i + 1) * seg)
        tc = t_full[mask] - i * seg
        fade = min(2.0, seg / 4)
        env = np.clip(np.minimum(tc / fade, (seg - tc) / fade), 0.0, 1.0)
        for k, f in enumerate(chord):
            amp = 0.16 / (1 + k * 0.35)
            left[mask] += env * _pad_tone(f, tc, amp, 0.11 + 0.03 * k,
                                          i + k * 1.3)
            right[mask] += env * _pad_tone(f * 1.0015, tc, amp,
                                           0.13 + 0.03 * k, i + k * 0.9)
    if shimmer:
        for start in np.arange(0, seconds, 4.0):
            mask = (t_full >= start) & (t_full < start + 1.2)
            ts = t_full[mask] - start
            env = np.exp(-ts * 3.0) * 0.03
            right[mask] += env * np.sin(2 * np.pi * 1046.5 * ts)
            left[mask] += env * np.sin(2 * np.pi * 1046.5 * 1.5 * ts)
    return left, right


def _render_ambient() -> tuple[np.ndarray, np.ndarray]:
    chords = [
        (130.81, 196.00, 261.63, 329.63),
        (110.00, 164.81, 220.00, 261.63),
        (87.31, 174.61, 220.00, 261.63),
        (98.00, 196.00, 246.94, 293.66),
    ]
    left, right = _chord_pad(chords, CATEGORIES["ambient"]["seconds"],
                             shimmer=True)
    peak = max(np.abs(left).max(), np.abs(right).max(), 1e-9)
    return left / peak * 0.5, right / peak * 0.5


def _render_chill() -> tuple[np.ndarray, np.ndarray]:
    chords = [
        (146.83, 220.00, 293.66, 349.23),   # D
        (130.81, 196.00, 246.94, 329.63),   # C
        (110.00, 164.81, 261.63, 329.63),   # Am
        (98.00, 174.61, 246.94, 293.66),    # Gm-ish
    ]
    seconds = CATEGORIES["chill"]["seconds"]
    left, right = _chord_pad(chords, seconds)
    t = np.arange(len(left)) / SR
    # lazy off-beat plucks
    for i, start in enumerate(np.arange(0.5, seconds - 0.5, 1.5)):
        mask = (t >= start) & (t < start + 0.4)
        f = [523.25, 587.33, 659.25, 493.88][i % 4]
        env = np.exp(-(t[mask] - start) * 9) * 0.10
        left[mask] += env * np.sin(2 * np.pi * f * (t[mask] - start))
        right[mask] += env * np.sin(2 * np.pi * f * 1.002 * (t[mask] - start))
    peak = max(np.abs(left).max(), np.abs(right).max(), 1e-9)
    return left / peak * 0.45, right / peak * 0.45


def _render_upbeat() -> tuple[np.ndarray, np.ndarray]:
    chords = [
        (130.81, 196.00, 261.63, 392.00),   # C add G
        (146.83, 220.00, 293.66, 440.00),   # D add A
        (174.61, 261.63, 349.23, 523.25),   # F
        (196.00, 246.94, 392.00, 587.33),   # G
    ]
    seconds = CATEGORIES["upbeat"]["seconds"]
    left, right = _chord_pad(chords, seconds)
    t = np.arange(len(left)) / SR
    # light four-on-the-floor pulse (soft kick-ish thump + hat tick)
    for beat in np.arange(0, seconds, 0.5):
        mask = (t >= beat) & (t < beat + 0.12)
        env = np.exp(-(t[mask] - beat) * 40) * 0.22
        left[mask] += env * np.sin(2 * np.pi * 60 * (t[mask] - beat))
        right[mask] += env * np.sin(2 * np.pi * 60 * (t[mask] - beat))
        hat_m = (t >= beat + 0.25) & (t < beat + 0.29)
        henv = np.exp(-(t[hat_m] - beat - 0.25) * 90) * 0.05
        if henv.size:
            noise = np.random.default_rng(int(beat * 100)).uniform(
                -1, 1, henv.size)
            left[hat_m] += henv * noise
            right[hat_m] += henv * noise * 0.9
    peak = max(np.abs(left).max(), np.abs(right).max(), 1e-9)
    return left / peak * 0.5, right / peak * 0.5


def _render_cinematic() -> tuple[np.ndarray, np.ndarray]:
    seconds = CATEGORIES["cinematic"]["seconds"]
    t = np.arange(int(SR * seconds)) / SR
    left = np.zeros_like(t)
    right = np.zeros_like(t)
    # low drone with slow swell
    for f, amp in ((55.0, 0.30), (82.41, 0.18), (110.0, 0.12)):
        swell = 0.6 + 0.4 * np.sin(2 * np.pi * t / seconds * 2 + 0.5)
        left += amp * swell * np.sin(2 * np.pi * f * t)
        right += amp * swell * np.sin(2 * np.pi * f * 1.003 * t + 0.4)
    # sparse high bell every 8 s
    for start in np.arange(0, seconds, 8.0):
        mask = (t >= start) & (t < start + 3.0)
        ts = t[mask] - start
        env = np.exp(-ts * 1.2) * 0.06
        left[mask] += env * np.sin(2 * np.pi * 880 * ts)
        right[mask] += env * np.sin(2 * np.pi * 1174.66 * ts)
    peak = max(np.abs(left).max(), np.abs(right).max(), 1e-9)
    return left / peak * 0.5, right / peak * 0.5


_RENDERERS = {
    "ambient": _render_ambient,
    "chill": _render_chill,
    "upbeat": _render_upbeat,
    "cinematic": _render_cinematic,
}


def _write_stereo(out_wav: Path, left: np.ndarray, right: np.ndarray) -> Path:
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out_wav), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        inter = np.empty(left.size * 2, dtype=np.int16)
        inter[0::2] = (left * 32767).astype(np.int16)
        inter[1::2] = (right * 32767).astype(np.int16)
        w.writeframes(inter.tobytes())
    return out_wav


def get_bgm(category: str = "ambient") -> Path:
    """Cached seamless loop for a category; generates on first use."""
    cat = category if category in _RENDERERS else "ambient"
    out = config.resource_path(f"assets/music/bgm_{cat}.wav")
    if not out.exists() or out.stat().st_size < 10000:
        left, right = _RENDERERS[cat]()
        _write_stereo(out, left, right)
    return out


def list_categories() -> list[dict]:
    """[{id, label, desc, path}] - generates lazily-cached paths."""
    return [{"id": k, "label": v["label"], "desc": v["desc"]}
            for k, v in CATEGORIES.items()]
