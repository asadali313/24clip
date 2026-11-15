"""Export management: render approved clips to separate validated MP4s.

- short_001.mp4 ... short_NNN.mp4 in <project>/exports/
- clip_metadata.json with original timestamps preserved
- a file is only reported exported after ffprobe validation passes
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable

from .. import config, project_store
from . import quality
from . import captions, ffmpeg_tools, renderer, segmentation, voiceover
from .discovery import Candidate


class ExportError(RuntimeError):
    pass


def _next_export_names(project_dir: Path, count: int) -> list[str]:
    n = 1
    existing = set(p.name for p in (project_dir / "exports").glob("short_*.mp4"))
    names = []
    while len(names) < count:
        name = f"short_{n:03d}.mp4"
        if name not in existing:
            names.append(name)
        n += 1
    return names


def export_clip(project: project_store.Project, cand: dict,
                settings: config.Settings, out_name: str,
                progress: Callable[[float, str], None] | None = None) -> dict:
    """Render one candidate (possibly user-edited) into exports/, validated."""
    video = project.source_video
    if video is None or not video.exists():
        raise ExportError("source video is missing")
    start = float(cand["start"])
    end = float(cand["end"])
    duration = end - start
    if duration < 3:
        raise ExportError(f"clip too short to export ({duration:.1f}s)")

    progress and progress(0.05, "planning framing")
    aspect = getattr(settings, "aspect_ratio", "9:16")
    out_w, out_h = settings.output_resolution()
    # who-is-speaking signal: transcript spans (clip-relative) for framing
    speech_intervals: list[tuple[float, float]] = []
    tj = project.path("transcripts", "transcript.json")
    if tj.exists():
        try:
            tdata = json.loads(tj.read_text(encoding="utf-8"))
            from .transcriber import Segment as _Seg
            for s in tdata.get("segments", []):
                a, b = float(s["start"]) - start, float(s["end"]) - start
                if b > 0 and a < (end - start):
                    speech_intervals.append((max(0.0, a), min(b, end - start)))
        except (json.JSONDecodeError, OSError, KeyError, ValueError):
            pass
    framing = renderer.plan_framing(video, start, end,
                                    getattr(settings, "low_memory_mode",
                                            False),
                                    aspect=aspect,
                                    speech_intervals=speech_intervals)
    framing.resolution = getattr(settings, "export_resolution", "1080p")

    # ---- captions --------------------------------------------------------
    ass_file = None
    caption_line_count = 0
    if getattr(settings, "captions_enabled", False):
        progress and progress(0.15, "building captions")
        segs = []
        tj = project.path("transcripts", "transcript.json")
        if tj.exists():
            data = json.loads(tj.read_text(encoding="utf-8"))
            from .transcriber import Segment
            segs = [Segment.from_dict(s) for s in data["segments"]]
        sents = segmentation.build_sentences(segs) if segs else []
        lines = captions.build_lines(sents, start, end)
        caption_line_count = len(lines)
        # per-clip template override falls back to the project-wide setting
        template = cand.get("caption_template") or settings.caption_mode
        ass_file = captions.write_template_ass(
            lines, project.path("captions", f"{Path(out_name).stem}.ass"),
            settings, template, out_w=out_w, out_h=out_h)
        captions.write_srt(lines, project.path("captions", f"{Path(out_name).stem}.srt"))

    # ---- voiceover --------------------------------------------------------
    vo_file = None
    vo_assign = (cand.get("voiceover") or "")
    if vo_assign:
        vo_path = Path(vo_assign)
        if vo_path.exists():
            progress and progress(0.2, "preparing voiceover")
            vo_file = voiceover.prepare_for_clip(
                vo_path, duration, project.path("voiceovers"))
        else:
            raise ExportError(f"assigned voiceover missing: {vo_path}")

    # ---- quality control (before rendering) ---------------------------------
    progress and progress(0.28, "quality check")
    qc = quality.qc_check(
        cand, framing.to_dict(), getattr(framing, "subject_inside", -1.0),
        duration, caption_lines=caption_line_count,
        others=[c for c in project.data.get("candidates", [])
                if c.get("uid") != cand.get("uid")])
    cand["qc"] = qc
    project.save()
    if qc["status"] == "fail":
        detail = "; ".join(c["detail"] for c in qc["checks"]
                           if c["level"] == "fail")
        raise ExportError(f"Quality check failed - not rendered: {detail}")

    # ---- render ------------------------------------------------------------
    out_path = project.path("exports", out_name)
    progress and progress(0.3, "rendering")
    # AI voice mode: add quiet background music, original audio is dropped
    bgm_file = None
    if getattr(settings, "music_enabled", False):
        from . import music
        bgm_file = music.get_bgm(
            getattr(settings, "music_category", "ambient"))

    def rp(frac: float) -> None:
        progress and progress(0.3 + 0.6 * frac, "rendering")
    renderer.render_clip(video, start, end, out_path, settings,
                         framing=framing, ass_file=ass_file,
                         voiceover=vo_file, bgm=bgm_file, progress_cb=rp)

    # ---- validate + metadata ------------------------------------------------
    # per-clip thumbnail saved next to the export
    try:
        from .visual import FrameSampler
        FrameSampler(video).extract_frame_jpg(
            start + 1.0, out_path.with_suffix(".jpg"), width=640)
    except Exception:
        pass
    # custom output directory: copy after validation
    out_dir_setting = getattr(settings, "output_dir", "")
    if out_dir_setting:
        try:
            import shutil
            dest_dir = Path(out_dir_setting)
            dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(out_path, dest_dir / out_path.name)
        except Exception:
            pass

    progress and progress(0.95, "validating output")
    expect_audio = getattr(settings, "voice_mode", "keep") != "remove" or bool(vo_file)
    md = renderer.validate_output(out_path, expected_duration=duration,
                                  expect_audio=expect_audio)
    meta = {
        "file": out_name,
        "source_video": str(video),
        "source_timestamps": {"start": round(start, 3), "end": round(end, 3)},
        "original_timestamps_preserved": True,
        "duration": md["duration"],
        "resolution": f"{md['width']}x{md['height']}",
        "size_bytes": md["size_bytes"],
        "framing": framing.to_dict(),
        "captions": bool(ass_file), "voiceover": bool(vo_file),
        "score": cand.get("score"), "text_preview": cand.get("text", "")[:200],
        "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "validated": True,
    }
    exports_meta = project.data.setdefault("exports", [])
    exports_meta[:] = [e for e in exports_meta if e.get("file") != out_name]
    exports_meta.append(meta)
    project.save()
    progress and progress(1.0, "done")
    return meta


def export_many(project: project_store.Project, candidates: list[dict],
                settings: config.Settings,
                progress: Callable[[int, int, float, str], None] | None = None
                ) -> tuple[list[dict], list[dict]]:
    """Export a list of candidates; returns (succeeded, failed) metadata."""
    names = _next_export_names(project.dir, len(candidates))
    ok, failed = [], []
    for i, (cand, name) in enumerate(zip(candidates, names)):
        def cb(frac: float, msg: str, i=i) -> None:
            progress and progress(i + 1, len(candidates), frac, msg)
        try:
            meta = export_clip(project, cand, settings, name, cb)
            ok.append(meta)
        except Exception as e:                      # noqa: BLE001
            failed.append({"file": name, "uid": cand.get("uid"),
                           "error": str(e)})
            project.add_log(f"EXPORT FAIL {name}: {e}")
    return ok, failed
