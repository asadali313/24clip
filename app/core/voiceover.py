"""Personal voiceover support (fully local; recordings never leave the machine).

- Accepts MP3/WAV/M4A/OGG/FLAC (anything ffmpeg decodes).
- Trim to clip length, adjust volumes, mix with or replace original audio.
- The actual mixing happens inside renderer.render_clip; this module provides
  inspection, normalization pre-checks and per-clip assignment storage.
"""
from __future__ import annotations

from pathlib import Path

from .. import config
from . import ffmpeg_tools

SUPPORTED_EXT = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac", ".wma"}


def is_supported(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED_EXT


def inspect(path: Path) -> dict:
    """Duration + codec info for a voiceover file."""
    if not path.exists():
        raise ffmpeg_tools.FFmpegError(f"voiceover not found: {path}")
    if not is_supported(path):
        raise ffmpeg_tools.FFmpegError(
            f"unsupported voiceover format: {path.suffix} "
            f"(supported: {', '.join(sorted(SUPPORTED_EXT))})")
    md = ffmpeg_tools.extract_metadata(path, allow_audio_only=True)
    return {
        "path": str(path), "duration": md.duration,
        "codec": md.audio_codec or "unknown",
        "sample_rate": md.sample_rate,
        "has_audio": md.has_audio,
    }


def prepare_for_clip(vo_path: Path, clip_duration: float,
                     out_dir: Path) -> Path:
    """Trim (or pad with silence) a voiceover to exactly the clip length.

    Returns the prepared wav placed in the project's voiceovers dir.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"prepared_{vo_path.stem}.wav"
    info = inspect(vo_path)
    if info["duration"] > clip_duration:
        args = ["-i", str(vo_path), "-t", f"{clip_duration:.3f}"]
    else:
        args = ["-i", str(vo_path),
                "-af", f"apad=pad_dur={clip_duration - info['duration']:.3f}"]
    ffmpeg_tools.run([
        *args, "-ac", "1", "-ar", "44100", "-acodec", "pcm_s16le",
        str(out),
    ], timeout=600)
    return out
