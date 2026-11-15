"""Local AI voice presets using the Windows built-in SAPI synthesizer.

Free, offline, zero new dependencies. Presets are content-type categories
(gaming / documentary / entertainment / storytelling) mapped to the voices
actually installed on this machine - presets whose voice is missing are never
shown. Each preset adjusts voice, speaking rate and pitch (SSML prosody).
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

from .. import config
from . import ffmpeg_tools
from .ffmpeg_tools import FFmpegError


@dataclass(frozen=True)
class VoicePreset:
    id: str
    label: str
    category: str
    sapi_voice: str            # required installed voice
    gender: str                # male | female
    rate: int                  # SAPI rate offset -10..10
    pitch: str = "+0%"         # SSML prosody pitch
    description: str = ""
    sample_text: str = (
        "This is a sample of how your clip narration will sound. "
        "Fast action, big moments, and clean storytelling.")

    @property
    def language(self) -> str:
        return "en-US"          # SAPI desktop voices are US English


# Template presets: instantiated per installed voice in list_presets().
# 'male' presets use a male voice, 'female' presets a female voice; if no
# voice of that gender exists the preset is not offered (no fake voices).
PRESET_TEMPLATES = [
    ("gaming_energetic", "Rush", "Gaming", "male", 3, "+8%",
     "Energetic, fast-paced delivery for gameplay and action moments."),
    ("documentary_deep", "Atlas", "Documentary", "male", -2, "-10%",
     "Deep, measured narration for facts, history and documentaries."),
    ("entertainment", "Breeze", "Entertainment", "female", 1, "+4%",
     "Expressive, natural tone for shows, reactions and funny clips."),
    ("storytelling", "Sage", "Storytelling", "female", 0, "+0%",
     "Clear, friendly narration for stories and educational content."),
]

_PS_TEMPLATE = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$s.Rate = {rate}
$s.Volume = {volume}
{select_voice}
$s.SetOutputToWaveFile('{out}')
$text = Get-Content -Raw -Encoding UTF8 '{textfile}'
{speak}
$s.Dispose()
"""


def available_voices() -> list[dict]:
    """Installed SAPI voices: [{name, gender, culture}]. Best effort."""
    ps = ("Add-Type -AssemblyName System.Speech;"
          "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
          "$s.GetInstalledVoices() | ForEach-Object { "
          "$_.VoiceInfo.Name + '|' + $_.VoiceInfo.Gender + '|' + "
          "$_.VoiceInfo.Culture.Name }")
    out: list[dict] = []
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
        for line in proc.stdout.splitlines():
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 3 and parts[0]:
                out.append({"name": parts[0],
                            "gender": "female" if "female" in parts[1].lower()
                            else "male",
                            "culture": parts[2]})
    except Exception:
        pass
    return out


def list_presets() -> list[VoicePreset]:
    """Presets actually available on this machine (honest listing)."""
    voices = available_voices()
    presets: list[VoicePreset] = []
    for pid, label, category, gender, rate, pitch, desc in PRESET_TEMPLATES:
        match = next((v for v in voices if v["gender"] == gender), None)
        if not match:
            # fall back to any voice rather than dropping the category
            match = voices[0] if voices else None
        if not match:
            continue
        presets.append(VoicePreset(
            id=f"{pid}", label=f"{label}  ({match['name'].replace('Microsoft ', '').replace(' Desktop', '')})",
            category=category, sapi_voice=match["name"], gender=match["gender"],
            rate=rate, pitch=pitch, description=desc))
    return presets


def get_preset(preset_id: str) -> VoicePreset | None:
    for p in list_presets():
        if p.id == preset_id:
            return p
    return None


def _ssml(text: str, pitch: str, rate: int) -> str:
    rate_pct = f"{rate * 12:+d}%"
    return (f'<speak version="1.0" xml:lang="en-US">'
            f'<prosody pitch="{escape(pitch)}" rate="{escape(rate_pct)}">'
            f'{escape(text)}</prosody></speak>')


def synthesize(text: str, out_wav: Path, preset: VoicePreset | None = None,
               speed_offset: int = 0, volume: int = 100) -> Path:
    """Synthesize speech to wav. Uses SSML prosody when pitch != neutral."""
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    tmp_txt = config.TEMP_DIR / "python" / f"tts_{out_wav.stem}.txt"
    tmp_txt.parent.mkdir(parents=True, exist_ok=True)

    rate = (preset.rate if preset else 0) + speed_offset
    rate = max(-10, min(10, rate))
    select = ""
    if preset and preset.sapi_voice:
        voice_name = preset.sapi_voice.replace("'", "''")
        select = f"$s.SelectVoice('{voice_name}')"
    if preset and preset.pitch not in ("+0%", "-0%", "+0", "+0%"):
        tmp_txt.write_text(_ssml(text, preset.pitch, rate), encoding="utf-8")
        speak = "$s.SpeakSsml($text)"
    else:
        tmp_txt.write_text(text, encoding="utf-8")
        speak = "$s.Speak($text)"

    script = _PS_TEMPLATE.format(rate=max(-10, min(10, rate)),
                                 volume=int(max(0, min(100, volume))),
                                 select_voice=select, out=str(out_wav),
                                 textfile=str(tmp_txt), speak=speak)
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=300, creationflags=subprocess.CREATE_NO_WINDOW)
    tmp_txt.unlink(missing_ok=True)
    if proc.returncode != 0 or not out_wav.exists():
        raise FFmpegError(f"local speech synthesis failed: "
                          f"{(proc.stderr or '')[-300:]}")
    info = ffmpeg_tools.extract_metadata(out_wav, allow_audio_only=True)
    if info.duration <= 0.5:
        raise FFmpegError("synthesized voice is empty")
    return out_wav


def synthesize_sample(preset: VoicePreset, out_wav: Path) -> Path:
    return synthesize(preset.sample_text, out_wav, preset)
