"""Clip extraction & rendering with intelligent framing for BOTH formats.

  9:16  1080x1920  Shorts / TikTok / Reels
  16:9  1920x1080  regular YouTube / widescreen

Framing priority (per spec):
  1. keep the active speaker / main subject visible
  2. preserve important visual content
  3. avoid cutting faces off
  4. track movement smoothly (x for vertical crops, y for 16:9 crops of
     taller sources)
  5. blurred-background padding when cropping is unsuitable

Nothing is ever stretched: crop or pad only.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .. import config
from . import ffmpeg_tools
from .ffmpeg_tools import FFmpegError

OUT_W, OUT_H = 1080, 1920            # default (9:16) output
ASPECTS = {"9:16": 9 / 16, "16:9": 16 / 9}
RESOLUTIONS = {"9:16": (1080, 1920), "16:9": (1920, 1080)}


@dataclass
class FramingPlan:
    method: str = "crop"     # "passthrough" | "crop" | "blur_pad"
    aspect: str = "9:16"
    src_w: int = 0
    src_h: int = 0
    crop_w: int = 0
    crop_h: int = 0
    xs: list[float] = field(default_factory=list)   # per-sample crop x
    ys: list[float] = field(default_factory=list)   # per-sample crop y
    ts: list[float] = field(default_factory=list)   # clip-relative times
    subject_inside: float = -1.0                    # QC ratio
    notes: str = ""

    resolution: str = "1080p"   # 1080p | 720p

    @property
    def out_w(self) -> int:
        w, h = RESOLUTIONS.get(self.aspect, (1080, 1920))
        if self.resolution == "720p":
            w, h = int(w * 720 / h), 720
        return w, h

    def _dims(self) -> tuple[int, int]:
        # standard 720p: 1280x720 landscape, 720x1280 portrait
        if self.resolution == "720p":
            return (720, 1280) if self.aspect == "9:16" else (1280, 720)
        return RESOLUTIONS.get(self.aspect, (1080, 1920))

    @property
    def out_w(self) -> int:
        return self._dims()[0]

    @property
    def out_h(self) -> int:
        return self._dims()[1]

    def to_dict(self) -> dict:
        return {"method": self.method, "aspect": self.aspect,
                "src": f"{self.src_w}x{self.src_h}",
                "crop": f"{self.crop_w}x{self.crop_h}",
                "output": f"{self.out_w}x{self.out_h}",
                "tracked": bool(len(self.xs) > 1 or len(self.ys) > 1),
                "notes": self.notes}


# ---------------------------------------------------------------------------
# Framing analysis
# ---------------------------------------------------------------------------
def plan_framing(video: Path, start: float, end: float,
                 low_memory: bool = False, aspect: str = "9:16",
                 speech_intervals: list[tuple[float, float]] | None = None,
                 resolution: str = "1080p") -> FramingPlan:
    """Decide how to turn [start,end] into the target format, no distortion.

    speech_intervals: clip-relative spans of active speech (from the
    transcript) used as a who-is-speaking signal for framing.
    """
    from . import framing
    md = ffmpeg_tools.extract_metadata(video)
    plan = FramingPlan(src_w=md.width, src_h=md.height, aspect=aspect,
                       resolution=resolution)
    speech_intervals = speech_intervals or []
    src_aspect = md.width / max(1, md.height)
    target = ASPECTS.get(aspect, 9 / 16)

    if aspect == "16:9":
        # ---- landscape 1920x1080 ------------------------------------------
        if abs(src_aspect - target) < 0.05:
            plan.method = "passthrough"        # already 16:9-ish
            plan.notes = "source is already 16:9 - scaled to 1920x1080"
            return plan
        if src_aspect < target:
            # taller than 16:9 (4:3, square, portrait): crop top/bottom and
            # follow the subject VERTICALLY (full width preserved)
            crop_h = int(round(md.width / target))
            if crop_h % 2:
                crop_h += 1
            plan.crop_w = md.width
            plan.crop_h = crop_h
            track = framing.track_subject(video, start, end, md.width,
                                          md.height, crop_w=md.width,
                                          speech_intervals=speech_intervals)
            max_y = md.height - crop_h
            if not track.cys or len(track.cys) <= 1:
                plan.ys = [max_y / 2]
                plan.ts = [0.0]
                plan.subject_inside = track.inside_ratio
                plan.notes = "centered vertical crop (no subject signal)"
                return plan
            plan.ys = [max(0.0, min(max_y, cy - crop_h / 2)) for cy in track.cys]
            plan.ts = track.ts
            plan.subject_inside = track.inside_ratio
            plan.notes = (f"vertical subject-follow ({track.content_type}, "
                          f"{track.face_hits}/{track.samples} face)")
            return plan
        # wider than 16:9: horizontal crop, full height
        crop_w = int(round(md.height * target))
        if crop_w % 2:
            crop_w += 1
        plan.crop_w = crop_w
        plan.crop_h = md.height
        max_x = md.width - crop_w
        track = framing.track_subject(video, start, end, md.width, md.height,
                                      crop_w,
                                      speech_intervals=speech_intervals)
        if not track.xs or len(track.xs) <= 1:
            plan.xs = [max_x / 2]
            plan.ts = [0.0]
            plan.subject_inside = track.inside_ratio
            plan.notes = "centered horizontal crop (no subject signal)"
            return plan
        plan.xs = track.xs
        plan.ts = track.ts
        plan.subject_inside = track.inside_ratio
        plan.notes = (f"horizontal subject-follow ({track.face_hits}/"
                      f"{track.samples} face, {track.motion_hits} motion)")
        return plan

    # ---- portrait 9:16 (existing behaviour) -------------------------------
    if src_aspect <= 0.66:                     # already portrait 9:16-ish
        plan.method = "passthrough"
        plan.notes = "source is already vertical"
        return plan

    if src_aspect >= 2.0:                      # cinemascope/ultrawide: a 9:16
        plan.method = "blur_pad"               # crop would keep < 25% of width
        plan.notes = ("very wide source - blurred padding preserves more "
                      "content than a hard crop")
        return plan

    crop_w = int(round(md.height * target))
    if crop_w % 2:
        crop_w += 1
    plan.crop_w = crop_w
    plan.crop_h = md.height
    max_x = md.width - crop_w

    track = framing.track_subject(video, start, end, md.width, md.height,
                                  crop_w,
                                  speech_intervals=speech_intervals)
    if not track.xs or len(track.xs) <= 1:
        # single safe center position
        plan.xs = [max_x / 2]
        plan.ts = [0.0]
        plan.subject_inside = track.inside_ratio
        plan.notes = "center crop (no reliable subject signal)"
        return plan
    plan.xs = track.xs
    plan.ts = track.ts
    plan.subject_inside = track.inside_ratio
    plan.notes = (f"subject-follow crop ({track.face_hits}/{track.samples} "
                  f"face, {track.motion_hits} motion, "
                  f"detector={track.detector})")
    return plan


def _crop_axis_expr(points: list[tuple[float, float]]) -> str:
    """Piecewise-linear crop-axis position as a per-frame ffmpeg expression.

    Evaluated by the crop filter every frame (it supports `t`), so no
    external command files are needed - sendcmd broke on Windows paths.
    """
    if len(points) == 1:
        return f"{points[0][1]:.0f}"
    expr = f"{points[0][1]:.0f}"

    def seg(i: int) -> str:
        """x for t >= points[i][0] (nested ifs, linear inside each segment)."""
        t0, x0 = points[i]
        if i == len(points) - 1:
            return f"{x0:.0f}"
        t1, x1 = points[i + 1]
        span = max(0.05, t1 - t0)
        linear = (f"{x0:.0f}+({x1:.0f}-{x0:.0f})"
                  f"*min(1,max(0,(t-{t0:.2f})/{span:.2f}))")
        return f"if(lt(t,{t1:.2f}),{linear},{seg(i + 1)})"

    expr = seg(0)
    return expr


def _crop_filter(plan: FramingPlan, start: float) -> str:
    """Build an ffmpeg filter string implementing the framing plan."""
    out_w, out_h = plan.out_w, plan.out_h
    if plan.method == "passthrough":
        # already the right shape: pure scale (never stretches - aspect
        # matches within tolerance verified in plan_framing)
        return f"scale={out_w}:{out_h},setsar=1"
    max_x = plan.src_w - plan.crop_w
    if plan.method == "blur_pad":
        return (
            f"split[a][b];[a]scale={out_w}:{out_h}:force_original_aspect_ratio="
            f"increase,crop={out_w}:{out_h},gblur=sigma=28[bg];"
            f"[b]scale={out_w}:{out_h}:force_original_aspect_ratio=decrease"
            f"[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2")
    # dynamic y-follow when the crop trims height (16:9 from taller sources)
    y_part = "0"
    max_y = plan.src_h - plan.crop_h
    if max_y > 2 and len(plan.ys) > 1:
        y_part = "'" + _crop_axis_expr(list(zip(plan.ts, plan.ys))) + "'"
    if len(plan.xs) <= 1 and len(plan.ys) <= 1:
        x0 = int(max(0, min(max_x, plan.xs[0] if plan.xs else max_x / 2)))
        y0 = int(max(0, min(max_y, plan.ys[0] if plan.ys else max_y / 2)))
        return (f"crop={plan.crop_w}:{plan.crop_h}:{x0}:{y0},"
                f"scale={out_w}:{out_h}")
    # dynamic subject-follow crop: per-frame evaluated x/y expressions
    x_part = "0"
    if len(plan.xs) > 1:
        x_part = "'" + _crop_axis_expr(list(zip(plan.ts, plan.xs))) + "'"
    elif plan.xs:
        x_part = str(int(max(0, min(max_x, plan.xs[0]))))
    if len(plan.ys) <= 1 and plan.ys:
        y_part = str(int(max(0, min(max_y, plan.ys[0]))))
    return (f"crop={plan.crop_w}:{plan.crop_h}:x={x_part}:y={y_part},"
            f"scale={out_w}:{out_h}")


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
QUALITY = {
    "fast":     {"preset": "veryfast", "crf": "26", "audio": "128k"},
    "balanced": {"preset": "medium",   "crf": "22", "audio": "160k"},
    "quality":  {"preset": "slow",     "crf": "19", "audio": "192k"},
}


def render_clip(video: Path, start: float, end: float, out_mp4: Path,
                settings=None, framing: FramingPlan | None = None,
                ass_file: Path | None = None,
                voiceover: Path | None = None,
                bgm: Path | None = None,
                progress_cb=None) -> Path:
    """Cut [start,end] and render a vertical MP4. Validates the result.

    Audio behaviour (spec):
      - AI voice (tts): original audio COMPLETELY removed; AI voice + quiet
        background music are the only tracks.
      - own recording (file): mixed with the original at user volumes.
      - keep: original untouched.  remove: silent video.
    """
    settings = settings or config.Settings()
    q = QUALITY.get(getattr(settings, "processing_quality", "balanced"),
                    QUALITY["balanced"])
    if framing is None:
        framing = plan_framing(video, start, end,
                               getattr(settings, "low_memory_mode", False),
                               aspect=getattr(settings, "aspect_ratio",
                                              "9:16"),
                               resolution=getattr(settings,
                                                  "export_resolution",
                                                  "1080p"))
    duration = end - start
    vf = _crop_filter(framing, start)
    if ass_file is not None:
        fspath = ass_file.resolve().as_posix().replace(":", "\\:")
        vf += f",subtitles='{fspath}'"
    # optional logo/watermark overlay (separate input, appended last)
    wm_input: list[str] = []
    wm = getattr(settings, "watermark_path", "") if settings else ""
    if wm and Path(wm).exists():
        op = max(0.05, min(1.0, float(getattr(settings,
                                              "watermark_opacity", 0.6))))
        corner = getattr(settings, "watermark_corner", "top-right")
        pos = {"top-right": "W-w-24:24", "top-left": "24:24",
               "bottom-right": "W-w-24:H-h-24",
               "bottom-left": "24:H-h-24"}.get(corner, "W-w-24:24")
        wm_idx = 1 + (1 if (voiceover is not None and
                            Path(voiceover).exists()) else 0) + (
            1 if (bgm is not None and Path(bgm).exists()) else 0)
        wm_input = ["-i", str(wm)]
        vf = (f"[0:v]{vf}[vbase];"
              f"[{wm_idx}:v]scale=iw*0.5:-1,format=rgba,"
              f"colorchannelmixer=aa={op:.2f}[wmark];"
              f"[vbase][wmark]overlay={pos}")


    # ---- inputs first (options apply to the next file they precede) -------
    has_vo = voiceover is not None and voiceover.exists()
    has_bgm = bgm is not None and Path(bgm).exists()
    args = ["-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", str(video)]
    if has_vo:
        args += ["-i", str(voiceover)]
    if has_bgm:
        args += ["-stream_loop", "-1", "-i", str(bgm)]
    if wm_input:
        args += wm_input

    # ---- output options (video) --------------------------------------------
    if vf.startswith("[0:v]"):
        # watermark overlay graph references an input label: needs
        # -filter_complex, not -vf
        args += ["-filter_complex", vf]
    else:
        args += ["-vf", vf,
        "-r", "30",
        "-c:v", "libx264", "-preset", q["preset"], "-crf", q["crf"],
        "-pix_fmt", "yuv420p",
    ]
    # ---- output options (audio) --------------------------------------------
    # music chain (any mode when a bgm is supplied): loop the category loop,
    # trim, apply fade-in/out, then place under the narration/original at
    # bgm_volume so it never overpowers the voice
    bgm_vol = float(getattr(settings, "bgm_volume", 0.16))
    fade_d = float(getattr(settings, "music_fade", 2.5) or 0)
    fade_d = max(0.0, min(fade_d, duration / 4))
    fades = ""
    if fade_d > 0.05:
        fades = (f",afade=t=in:st=0:d={fade_d:.2f},"
                 f"afade=t=out:st={max(0.0, duration - fade_d):.2f}"
                 f":d={fade_d:.2f}")
    ov_vol = float(getattr(settings, "voiceover_volume", 1.0))
    orig_vol = float(getattr(settings, "original_volume", 1.0))

    streams = []
    if has_vo and getattr(settings, "keep_original_audio", True):
        streams.append(
            f"[1:a]atrim=0:{duration:.3f},"
            f"aformat=sample_rates=44100:channel_layouts=stereo,"
            f"volume={ov_vol}[vo]")
        streams.append(
            f"[0:a]atrim=0:{duration:.3f},"
            f"aformat=sample_rates=44100:channel_layouts=stereo,"
            f"volume={orig_vol}[og]")
    elif has_vo:
        streams.append(
            f"[1:a]atrim=0:{duration:.3f},"
            f"aformat=sample_rates=44100:channel_layouts=stereo,"
            f"volume={ov_vol}[vo]")
    elif not has_vo and getattr(settings, "voice_mode", "keep") != "remove":
        streams.append(
            f"[0:a]atrim=0:{duration:.3f},"
            f"aformat=sample_rates=44100:channel_layouts=stereo,"
            f"volume={orig_vol}[og]")
    if has_bgm:
        bgm_label = 2 if has_vo else 1
        streams.append(
            f"[{bgm_label}:a]atrim=0:{duration:.3f},"
            f"aformat=sample_rates=44100:channel_layouts=stereo,"
            f"volume={bgm_vol}{fades}[bg]")

    if len(streams) >= 2:
        # collect the labels actually produced, in mix priority order
        labels = ""
        for name in ("vo", "og", "bg"):
            if f"[{name}]" in "||".join(streams):
                labels += f"[{name}]"
        n = labels.count("[")
        args += [
            "-filter_complex", ";".join(streams) + ";"
            f"{labels}amix=inputs={n}:duration=first:normalize=0[aout]",
            "-map", "0:v:0", "-map", "[aout]",
        ]
    elif streams:
        s = streams[0].rsplit("[", 1)[0].rstrip(",")   # drop [label]
        args += ["-filter_complex", s + "[aout]",
                 "-map", "0:v:0", "-map", "[aout]"]
    else:
        args += ["-map", "0:v:0"]
    if getattr(settings, "voice_mode", "keep") == "remove" and not has_vo             and not has_bgm:
        args += ["-an"]                            # user chose no audio
    if getattr(settings, "audio_cleanup", False) and "-filter_complex" not in args:
        # audio cleanup on the single-stream path
        i = args.index("-af") if "-af" in args else -1
        if i >= 0:
            args[i + 1] = args[i + 1] + ",loudnorm=I=-16:TP=-1.5:LRA=11"
    args += ["-c:a", "aac", "-b:a", q["audio"], "-ar", "44100"]

    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    # render into temp then move, so failures never leave half-written exports
    tmp_out = config.TEMP_DIR / "render" / f"{out_mp4.stem}.tmp.mp4"
    tmp_out.parent.mkdir(parents=True, exist_ok=True)
    full_args = [config.ffmpeg(), "-y", "-v", "error", *args, str(tmp_out)]

    proc = subprocess.run(
        full_args, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=3600,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if proc.returncode != 0:
        tmp_out.unlink(missing_ok=True)
        raise FFmpegError(f"render failed: {proc.stderr[-500:]}")
    expect_audio = not (getattr(settings, "voice_mode", "keep") == "remove"
                        and not has_vo)
    validate_output(tmp_out, expected_duration=duration,
                    expect_audio=expect_audio)
    tmp_out.replace(out_mp4)
    if progress_cb:
        progress_cb(1.0)
    return out_mp4


# ---------------------------------------------------------------------------
# Validation - never report success for a file that does not really exist
# ---------------------------------------------------------------------------
def validate_output(path: Path, expected_duration: float | None = None,
                    expect_audio: bool = True) -> dict:
    if not path.exists() or path.stat().st_size < 1024:
        raise FFmpegError(f"output missing or empty: {path}")
    md = ffmpeg_tools.extract_metadata(path)
    if md.duration <= 0:
        raise FFmpegError("output has no measurable duration")
    if expected_duration and abs(md.duration - expected_duration) > max(1.5, expected_duration * 0.1):
        raise FFmpegError(
            f"output duration {md.duration:.1f}s differs from expected "
            f"{expected_duration:.1f}s")
    if expect_audio and not md.has_audio:
        raise FFmpegError("output has no audio stream")
    return md.to_dict()
