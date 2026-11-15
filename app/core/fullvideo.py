"""Full-video processing: the whole original video, customized.

Pipeline (reuses existing engines - transcript cache, tracking, captions,
TTS, music):
  1. transcribe (cached/resumable - see transcriber.py)
  2. optional translation of the transcript sentences (Argos, local)
  3. optional AI narration: per-sentence TTS placed at the original sentence
     timestamps (speed-fit with atempo when the narration runs long)
  4. captions for the whole video in the selected template
  5. optional intro title text overlay
  6. optional background music with volume + fades
  7. render in every selected orientation (16:9 and/or 9:16), one MP4 each

The original video file is never modified. Nothing leaves the machine.
"""
from __future__ import annotations

import json
import time
import wave
from pathlib import Path

import numpy as np

from .. import config
from ..core import captions, ffmpeg_tools, music, renderer, segmentation
from ..core.ffmpeg_tools import FFmpegError
from ..core.transcriber import Segment, TranscriptionEngine, ResumableTranscriber
from ..core import tts as tts_mod

SR = 44100


class FullVideoError(RuntimeError):
    pass


def _load_sentences(project) -> list[Segment]:
    tj = project.path("transcripts", "transcript.json")
    if not tj.exists():
        raise FullVideoError("No transcript found - run processing first.")
    data = json.loads(tj.read_text(encoding="utf-8"))
    return [Segment.from_dict(s) for s in data["segments"]]


def _load_sentences(project) -> list[Segment]:
    data = json.loads(project.path("transcripts", "transcript.json")
                      .read_text(encoding="utf-8"))
    return [Segment.from_dict(s) for s in data["segments"]]


def _tts_duration(path: Path) -> float:
    md = ffmpeg_tools.extract_metadata(path, allow_audio_only=True)
    return md.duration


def build_narration(project, sentences: list[Segment], texts: list[str],
                    settings, progress_cb=None, cancel_check=None) -> tuple[Path, float]:
    """Build one full-length narration wav aligned to sentence timestamps.

    Returns (wav_path, actual_duration). Sentences are speed-fitted into
    their original span with atempo (max 2x) so sync stays close.
    """
    preset = tts_mod.get_preset(settings.voice_preset) or \
        tts_mod.get_preset("storytelling")
    if preset is None:
        raise FullVideoError("No local TTS voices are installed.")
    cache_dir = project.path("voiceovers", "full")
    cache_dir.mkdir(parents=True, exist_ok=True)
    duration = sentences[-1].end if sentences else 0.0
    master = cache_dir / f"narration_{preset.id}_{SR}.pcm"
    total_frames = int(duration * SR) + SR
    n = max(1, len(sentences))
    cursor = 0.0        # seconds already written

    with open(master, "wb") as pcm:
        for i, (seg, text) in enumerate(zip(sentences, texts)):
            if cancel_check:
                cancel_check()
            text = text.strip()
            if not text:
                continue
            span = max(0.4, seg.end - seg.start)
            wav = cache_dir / f"line_{i:04d}.wav"
            if not wav.exists():
                tts_mod.synthesize(text, wav, preset)
            dur = _tts_duration(wav)
            # speed-fit: narration must roughly fit the original span
            tempo = 1.0
            if dur > span * 1.08:
                tempo = min(2.0, dur / (span * 0.98))
            fitted = cache_dir / f"line_{i:04d}_f.wav"
            if tempo > 1.01:
                ffmpeg_tools.run([
                    "-y", "-v", "error", "-i", str(wav),
                    "-filter:a", f"atempo={tempo:.3f}",
                    "-ar", str(SR), "-ac", "1", str(fitted)])
            else:
                ffmpeg_tools.run([
                    "-y", "-v", "error", "-i", str(wav),
                    "-ar", str(SR), "-ac", "1", str(fitted)])
            # write silence up to the sentence start
            start = max(0.0, min(seg.start, duration))
            gap = int(max(0.0, start - cursor) * SR) * 2
            if gap:
                pcm.write(b"\x00" * gap)
                cursor = start
            frames = np.fromfile(fitted, dtype=np.int16)
            span_frames = int(span * SR)
            if frames.size > span_frames:
                frames = frames[:span_frames]
            pcm.write(frames.tobytes())
            cursor = start + frames.size / SR
            fitted.unlink(missing_ok=True)
            if progress_cb:
                progress_cb((i + 1) / n)

    wav_out = cache_dir / f"narration_{preset.id}.wav"
    ffmpeg_tools.run([
        "-y", "-v", "error", "-f", "s16le", "-ar", str(SR), "-ac", "1",
        "-i", str(master), "-ar", str(SR), "-ac", "1", str(wav_out)])
    master.unlink(missing_ok=True)
    return wav_out, duration


def process_full_video(project, settings, progress_cb=None,
                       cancel_check=None) -> list[dict]:
    """Run the full-video customization. Returns export metadata dicts."""
    video = project.source_video
    if video is None or not video.exists():
        raise FullVideoError("Source video is missing.")
    md = ffmpeg_tools.extract_metadata(video)
    duration = md.duration
    stages = 4 + len(settings.fv_formats)
    done = [0]

    def tick(msg: str):
        done[0] += 1
        if progress_cb:
            progress_cb(min(0.95, done[0] / stages), msg)

    def no_cancel():
        if cancel_check:
            cancel_check()

    # ---- 1. transcript (cached / resumable) --------------------------------
    if project.stage_state("transcribe") != "done":
        if progress_cb:
            progress_cb(0.02, "transcribing (cached windows are reused)")
        engine = TranscriptionEngine.get(settings.model_size,
                                         settings.low_memory_mode)
        rt = ResumableTranscriber(project, engine, duration,
                                  progress_cb=lambda f, i, n, eta=0: (
                                      progress_cb and progress_cb(
                                          0.02 + 0.28 * f,
                                          f"transcribing section {i}/{n}"
                                          + (f" · ~{eta:.0f}s left"
                                             if eta > 3 else ""))))
        rt.run()
        project.set_stage("transcribe", "done")
        project.save()
    tick("transcript ready")

    # ---- 2. sentences + optional translation --------------------------------
    sentences = segmentation.build_sentences(_load_sentences(project))
    texts = [s.text for s in sentences]
    from_code = settings.language if settings.language != "auto" else "en"
    if settings.fv_translate:
        from . import translate as tr
        if progress_cb:
            progress_cb(0.32, f"translating to {settings.fv_lang} "
                              "(first run downloads a language pack)")
        tr.ensure_pair(from_code, settings.fv_lang)
        texts = tr.translate_sentences(
            [{"text": s.text} for s in sentences], from_code,
            settings.fv_lang,
            progress_cb=lambda f: progress_cb and progress_cb(
                0.32 + 0.1 * f, "translating…"))
    tick("text ready")

    # ---- 3. AI narration (optional) ------------------------------------------
    narration = None
    if settings.fv_voice:
        if progress_cb:
            progress_cb(0.45, "synthesizing AI narration "
                              "(per sentence, synced to original timing)")
        narration, _ = build_narration(
            project, sentences, texts, settings,
            progress_cb=lambda f: progress_cb and progress_cb(
                0.45 + 0.2 * f, "synthesizing narration…"),
            cancel_check=no_cancel)
    tick("voice ready")

    # ---- 4. captions + title overlay -----------------------------------------
    ass_by_fmt: dict[str, Path] = {}
    if settings.fv_captions:
        sents_small = [
            type("S", (), {"start": s.start, "end": s.end, "text": t,
                           "words": s.words, "confidence": s.confidence})()
            for s, t in zip(sentences, texts)]
        for aspect in settings.fv_formats:
            ow, oh = (1920, 1080) if aspect == "16:9" else (1080, 1920)
            lines = captions.build_lines(sents_small, 0.0, duration)
            if settings.fv_title:
                from .captions import CaptionLine
                lines.insert(0, CaptionLine(0.0, min(4.0, duration),
                                            settings.fv_title))
            ass = project.path("captions", f"full_{aspect.replace(':', 'x')}.ass")
            captions.write_template_ass(lines, ass, settings,
                                        settings.caption_mode,
                                        out_w=ow, out_h=oh)
            ass_by_fmt[aspect] = ass
    tick("captions ready")

    # ---- 5. music --------------------------------------------------------------
    bgm = music.get_bgm(settings.music_category) \
        if settings.music_enabled else None

    # ---- 6. render each selected orientation -----------------------------------
    outputs: list[dict] = []
    exports_meta = project.data.setdefault("exports", [])
    for idx, aspect in enumerate(settings.fv_formats):
        if cancel_check:
            cancel_check()
        progress_cb and progress_cb(
            0.8 + 0.15 * idx / max(1, len(settings.fv_formats)),
            f"rendering full video ({aspect})…")
        ow, oh = (1920, 1080) if aspect == "16:9" else (1080, 1920)
        plan = renderer.plan_framing(
            video, 0.0, duration, settings.low_memory_mode,
            aspect=aspect,
            speech_intervals=[(s.start, s.end) for s in sentences][:200])
        out = project.path("exports", f"full_{aspect.replace(':', 'x')}.mp4")
        renderer.render_clip(video, 0.0, duration, out, settings,
                             framing=plan,
                             ass_file=ass_by_fmt.get(aspect),
                             voiceover=narration if settings.fv_voice else None,
                             bgm=bgm,
                             progress_cb=lambda f: progress_cb and
                             progress_cb(
                                 0.8 + 0.15 * (idx + f) /
                                 max(1, len(settings.fv_formats)),
                                 f"rendering full video ({aspect}) "
                                 f"{f * 100:.0f}%"))
        validated = renderer.validate_output(
            out, expected_duration=duration,
            expect_audio=settings.fv_voice or bool(bgm))
        meta = {
            "file": out.name,
            "source_video": str(video),
            "source_timestamps": {"start": 0.0, "end": round(duration, 3)},
            "original_timestamps_preserved": True,
            "duration": validated["duration"],
            "resolution": f"{validated['width']}x{validated['height']}",
            "size_bytes": validated["size_bytes"],
            "framing": plan.to_dict(),
            "full_video": True,
            "aspect": aspect,
            "narration": settings.fv_voice,
            "translated": settings.fv_translate,
            "translated_lang": settings.fv_lang if settings.fv_translate
            else "",
            "music": settings.music_enabled,
            "captions": settings.fv_captions,
            "title_overlay": settings.fv_title,
            "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "validated": True,
        }
        exports_meta[:] = [e for e in exports_meta
                           if e.get("file") != out.name]
        exports_meta.append(meta)
        outputs.append(meta)
    project.save()
    return outputs
