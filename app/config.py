"""Central configuration and path management.

STORAGE POLICY (non-negotiable): every large artifact - videos, audio, models,
temp files, renders - lives on the project drive (D:). This module is the single
source of truth; all other modules must resolve paths through it.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path

# ---------------------------------------------------------------------------
# Root layout - portable: the project root is wherever the app/ package
# lives, so the application runs from any folder or drive. Large data
# (models, videos, temp) always stays inside this root - never C: system
# directories.
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
APP_DIR = ROOT / "app"
BIN_DIR = ROOT / "bin"
MODELS_DIR = ROOT / "models"
HF_CACHE_DIR = MODELS_DIR / "huggingface"
TEMP_DIR = ROOT / "temp"
PROJECTS_DIR = ROOT / "projects"
LOGS_DIR = ROOT / "logs"
TESTS_DIR = ROOT / "tests"

FFMPEG_EXE = BIN_DIR / "ffmpeg.exe"
FFPROBE_EXE = BIN_DIR / "ffprobe.exe"

SETTINGS_FILE = ROOT / "settings.json"

_MIN_FREE_GB = 5.0          # refuse to start processing below this free space
_WARN_FREE_GB = 20.0


def ensure_layout() -> None:
    """Create the directory tree and redirect temp/cache env vars to D:."""
    for d in (ROOT, BIN_DIR, MODELS_DIR, HF_CACHE_DIR, TEMP_DIR,
              PROJECTS_DIR, LOGS_DIR, TESTS_DIR):
        d.mkdir(parents=True, exist_ok=True)
    # Redirect every temp/cache mechanism to D: before heavy libs initialize.
    py_temp = str(TEMP_DIR / "python")
    Path(py_temp).mkdir(parents=True, exist_ok=True)
    os.environ["TMP"] = py_temp
    os.environ["TEMP"] = py_temp
    os.environ["TMPDIR"] = py_temp
    os.environ["HF_HOME"] = str(HF_CACHE_DIR)
    os.environ["XDG_CACHE_HOME"] = str(MODELS_DIR / "xdg")


def check_drive_space(min_free_gb: float | None = None) -> tuple[bool, float]:
    """Return (ok, free_gb). Never silently continue when D: is full."""
    min_free_gb = _MIN_FREE_GB if min_free_gb is None else min_free_gb
    usage = shutil.disk_usage(ROOT)
    free_gb = usage.free / (1024 ** 3)
    return free_gb >= min_free_gb, round(free_gb, 1)


def ffmpeg() -> str:
    if not FFMPEG_EXE.exists():
        raise FileNotFoundError(f"ffmpeg not found at {FFMPEG_EXE}")
    return str(FFMPEG_EXE)


def ffprobe() -> str:
    if not FFPROBE_EXE.exists():
        raise FileNotFoundError(f"ffprobe not found at {FFPROBE_EXE}")
    return str(FFPROBE_EXE)


# ---------------------------------------------------------------------------
# User settings (persisted to settings.json on the project drive)
# ---------------------------------------------------------------------------
@dataclass
class Settings:
    # ---- workflow choices (set on the Settings screen before processing) ----
    clip_count: int = 6                  # 3 | 6 | 10 | 15 | custom
    duration_mode: str = "auto"          # auto | max30 | max45 | max60 | custom
    custom_min: int = 20
    custom_target: int = 30
    custom_max: int = 45
    caption_mode: str = "none"           # none | classic | beast | karaoke | documentary | modern | podcast | cinematic | custom
    voice_mode: str = "keep"             # keep | remove | file | tts
    voice_file: str = ""                 # used when voice_mode == "file"
    voice_preset: str = "storytelling"   # AI voice preset id (see core.tts)
    tts_speed: int = 0                   # -5..+5 on top of the preset rate
    tts_volume: int = 100                # 0..100

    # ---- transcription ----
    model_size: str = "base"             # tiny | base | small
    language: str = "auto"               # ISO code or "auto"
    low_memory_mode: bool = False
    cpu_threads: int = min(4, os.cpu_count() or 4)
    # ---- scoring weights (sum to 100) ----
    w_hook: int = 20
    w_info: int = 20
    w_clarity: int = 20
    w_engagement: int = 15
    w_completeness: int = 15
    w_visual: int = 10
    min_score: int = 75
    # ---- caption styling (used by pro/custom modes) ----
    captions_enabled: bool = False       # derived from caption_mode
    caption_font_size: int = 46
    caption_position: str = "bottom"     # top | center | bottom
    caption_color: str = "&H00FFFFFF"    # ASS format white
    caption_highlight: bool = True
    # ---- audio ----
    keep_original_audio: bool = True     # derived from voice_mode
    original_volume: float = 1.0
    voiceover_volume: float = 1.0
    bgm_volume: float = 0.16             # background music under AI voice
    # ---- misc ----
    aspect_ratio: str = "9:16"           # "9:16" portrait | "16:9" landscape
    # ---- processing mode & full-video customization ----
    processing_mode: str = "clips"       # clips | full
    clip_mode: str = "best"              # best|funny|emotional|interesting|educational|motivational|shocking|gaming|podcast|custom
    custom_prompt: str = ""              # user instructions for "custom" mode
    export_resolution: str = "1080p"     # 1080p | 720p
    output_dir: str = ""                 # optional custom export directory
    audio_cleanup: bool = False          # loudnorm on the final mix
    music_enabled: bool = True           # background music on/off (explicit)
    music_category: str = "ambient"
    music_fade: float = 2.5              # seconds of fade-in/out (0 = off)
    fv_voice: bool = True                # full video: AI voice narration
    fv_translate: bool = False           # full video: translate narration
    fv_lang: str = "es"                  # translation target language
    fv_captions: bool = True             # full video: subtitles
    fv_title: str = ""                   # full video: intro text overlay
    fv_formats: list = field(default_factory=lambda: ["16:9", "9:16"])
    processing_quality: str = "balanced"  # fast | balanced | quality
    enable_ollama: bool = False
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "llama3.2:3b"

    _dirty: bool = field(default=False, repr=False, compare=False)

    def output_resolution(self) -> tuple[int, int]:
        """(width, height) for the selected aspect ratio. Never stretches:
        the renderer crops/pads to reach exactly this size."""
        if self.aspect_ratio == "16:9":
            return 1920, 1080
        return 1080, 1920

    # ------------------------------------------------------------------
    def effective_durations(self) -> tuple[int, int, int]:
        """(min, target, max) resolved from duration_mode."""
        if self.duration_mode == "max30":
            return 15, 25, 30
        if self.duration_mode == "max45":
            return 20, 32, 45
        if self.duration_mode == "max60":
            return 25, 40, 60
        if self.duration_mode == "custom":
            return (max(10, self.custom_min), max(15, self.custom_target),
                    max(20, self.custom_max))
        return 18, 30, 45                 # auto: AI picks natural duration

    def apply_workflow_choices(self) -> None:
        """Derive the low-level flags from the user's workflow selections."""
        self.captions_enabled = self.caption_mode in (
            "simple", "pro", "classic", "beast", "modern", "karaoke", "custom")
        self.caption_highlight = self.caption_mode in ("pro", "beast", "custom")
        # AI voice must fully REPLACE the original track; user recordings mix.
        self.keep_original_audio = self.voice_mode in ("keep", "file")
        if self.voice_mode == "keep":
            self.original_volume = 1.0
            self.voiceover_volume = 1.0

    def save(self) -> None:
        data = {k: v for k, v in asdict(self).items() if not k.startswith("_")}
        SETTINGS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")

    @classmethod
    def load(cls) -> "Settings":
        s = cls()
        if SETTINGS_FILE.exists():
            try:
                data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
                for k, v in data.items():
                    if hasattr(s, k) and not k.startswith("_"):
                        setattr(s, k, v)
            except (json.JSONDecodeError, OSError):
                pass  # corrupted settings fall back to defaults
        return s


def resource_path(rel: str) -> Path:
    """Path to a bundled app resource (icons, fonts)."""
    return APP_DIR / rel


def app_version() -> str:
    return "1.0.0"
