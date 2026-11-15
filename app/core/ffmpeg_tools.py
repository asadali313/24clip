"""FFmpeg/FFprobe wrappers. All subprocess temp output stays on the project drive."""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .. import config


class FFmpegError(RuntimeError):
    """Raised when an ffmpeg/ffprobe invocation fails."""


def _run(args: list[str], timeout: int = 600) -> subprocess.CompletedProcess:
    env = {
        "TMP": str(config.TEMP_DIR / "ffmpeg"),
        "TEMP": str(config.TEMP_DIR / "ffmpeg"),
        "PATH": str(config.BIN_DIR) + r";" + __import__("os").environ.get("PATH", ""),
    }
    proc = subprocess.run(
        args, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=timeout, env=env,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return proc


def probe(video: Path) -> dict:
    """Return structured ffprobe metadata (format + streams)."""
    args = [
        config.ffprobe(), "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(video),
    ]
    proc = _run(args, timeout=120)
    if proc.returncode != 0:
        raise FFmpegError(f"ffprobe failed: {proc.stderr[:400]}")
    return json.loads(proc.stdout)


@dataclass
class VideoMetadata:
    path: str = ""
    duration: float = 0.0
    width: int = 0
    height: int = 0
    fps: float = 0.0
    video_codec: str = ""
    audio_codec: str = ""
    bitrate: int = 0
    size_bytes: int = 0
    has_audio: bool = False
    sample_rate: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def resolution(self) -> str:
        return f"{self.width}x{self.height}"

    def duration_hms(self) -> str:
        s = int(self.duration)
        return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"

    def size_mb(self) -> float:
        return self.size_bytes / (1024 * 1024)

    def to_dict(self) -> dict:
        return {
            "path": self.path, "duration": self.duration,
            "width": self.width, "height": self.height, "fps": self.fps,
            "video_codec": self.video_codec, "audio_codec": self.audio_codec,
            "bitrate": self.bitrate, "size_bytes": self.size_bytes,
            "has_audio": self.has_audio, "sample_rate": self.sample_rate,
            "errors": self.errors,
        }


def _int(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _float(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def extract_metadata(video: Path, allow_audio_only: bool = False) -> VideoMetadata:
    """Probe a video; raise FFmpegError only when the file is unusable.

    With allow_audio_only=True an audio-only file (voiceover wav/mp3) is
    accepted and simply reports has_audio without video dimensions.
    """
    if not video.exists():
        raise FFmpegError(f"File not found: {video}")
    data = probe(video)
    md = VideoMetadata(path=str(video))
    fmt = data.get("format", {})
    md.duration = _float(fmt.get("duration"))
    md.bitrate = _int(fmt.get("bit_rate"))
    md.size_bytes = _int(fmt.get("size"))
    streams = data.get("streams", [])
    vstreams = [s for s in streams if s.get("codec_type") == "video"]
    astreams = [s for s in streams if s.get("codec_type") == "audio"]
    if not vstreams:
        if allow_audio_only and astreams:
            a = astreams[0]
            md.has_audio = True
            md.audio_codec = a.get("codec_name", "")
            md.sample_rate = _int(a.get("sample_rate"))
            return md
        raise FFmpegError("No video stream found - unsupported or corrupted file.")
    vs = vstreams[0]
    md.width = _int(vs.get("width"))
    md.height = _int(vs.get("height"))
    md.video_codec = vs.get("codec_name", "")
    rate = vs.get("avg_frame_rate") or vs.get("r_frame_rate") or "0/1"
    num, _, den = rate.partition("/")
    try:
        md.fps = float(num) / float(den or 1)
    except (ZeroDivisionError, ValueError):
        md.fps = 0.0
    if astreams:
        a = astreams[0]
        md.has_audio = True
        md.audio_codec = a.get("codec_name", "")
        md.sample_rate = _int(a.get("sample_rate"))
    if md.duration <= 0:
        md.errors.append("duration unknown")
    return md


def extract_audio(video: Path, out_wav: Path, progress_cb=None) -> Path:
    """Extract 16 kHz mono PCM audio (whisper-friendly) with progress."""
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    dur = extract_metadata(video).duration or 0.0
    args = [
        config.ffmpeg(), "-y", "-v", "error", "-i", str(video),
        "-vn", "-ac", "1", "-ar", "16000",
        "-f", "wav", "-acodec", "pcm_s16le",
    ]
    # progress parsing needs stdout; run without -v error only when a cb is given
    if progress_cb and dur > 0:
        args = [
            config.ffmpeg(), "-y", "-i", str(video),
            "-vn", "-ac", "1", "-ar", "16000",
            "-f", "wav", "-acodec", "pcm_s16le",
            "-progress", "pipe:1", "-nostats",
        ]
        proc = subprocess.Popen(
            args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8",
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            if line.startswith("out_time_ms="):
                try:
                    t = float(line.split("=", 1)[1]) / 1_000_000.0
                    progress_cb(min(0.999, t / dur))
                except ValueError:
                    pass
        proc.wait()
        if proc.returncode != 0:
            raise FFmpegError(f"audio extraction failed (code {proc.returncode})")
    else:
        proc = _run(args)
        if proc.returncode != 0:
            raise FFmpegError(f"audio extraction failed: {proc.stderr[:400]}")
    if progress_cb:
        progress_cb(1.0)
    if not out_wav.exists() or out_wav.stat().st_size == 0:
        raise FFmpegError("audio extraction produced no output")
    return out_wav


def run(args: list[str], timeout: int = 3600) -> None:
    """Run a raw ffmpeg command; raise FFmpegError on failure."""
    proc = _run([config.ffmpeg(), "-y", "-v", "error", *args], timeout=timeout)
    if proc.returncode != 0:
        raise FFmpegError(proc.stderr.strip()[-600:] or "ffmpeg failed")
