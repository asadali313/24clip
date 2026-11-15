"""Analysis pipeline - simple, fast, single-pass.

Workflow (exactly the spec):
  prepare -> transcribe (once, whole video) -> discover -> select -> render

Optimizations over v1 (measured on 13.5-min test video):
  - full-video scene detection REMOVED (was 36.6s per 13.5 min, decodes every
    frame at full resolution and is useless for talking content)
  - visual signals: 3 tiny frames sampled per contender candidate (~40 max)
  - transcription beam_size=1 (~2-3x faster on CPU)
  - face tracking only for the final selected clips, at half resolution
Every stage is timed; timings land in processing_report.json.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable

from . import config, project_store
from .core import dedup, discovery, scoring, segmentation, transcriber, visual
from .core import export as export_mod
from .core.boundaries import BoundaryOptimizer
from .core.ffmpeg_tools import FFmpegError, extract_metadata

ProgressFn = Callable[[str, float, str], None]

STAGES = ["prepare", "transcribe", "discover", "select", "render"]


class PipelineError(RuntimeError):
    pass


class Pipeline:
    def __init__(self, project: project_store.Project, settings: config.Settings,
                 progress: ProgressFn | None = None):
        self.project = project
        self.settings = settings
        self.progress = progress or (lambda stage, frac, msg: None)
        self.timings: dict[str, float] = {}
        self._cancel = False
        self._stage_t0 = 0.0

    def cancel(self) -> None:
        self._cancel = True

    # ------------------------------------------------------------------
    def _emit(self, stage: str, frac: float, msg: str = "") -> None:
        if not self._cancel:
            self.progress(stage, frac, msg)

    def _check_cancel(self) -> None:
        if self._cancel:
            raise PipelineError("cancelled by user")

    def _begin_stage(self, stage: str) -> None:
        self._stage_t0 = time.time()
        if self.project.stage_state(stage) != "done":
            self.project.set_stage(stage, "running")
            self.project.save()

    def _end_stage(self, stage: str, msg: str = "") -> None:
        self.timings[stage] = round(time.time() - self._stage_t0, 1)
        self.project.data.setdefault("stage_timings", {})[stage] = \
            self.timings[stage]
        self.project.set_stage(stage, "done")
        self.project.save()
        t = self.timings[stage]
        out = f"{msg} ({t:.0f}s)" if msg else f"done in {t:.0f}s"
        self._emit(stage, 1.0, out)

    def _fail(self, msg: str) -> None:
        self.project.set_stage("failed", "error", msg)
        self.project.save()

    # ------------------------------------------------------------------
    def run(self) -> None:
        ok, free_gb = config.check_drive_space()
        if not ok:
            raise PipelineError(
                f"Not enough free space on D: ({free_gb} GB left, need 5 GB).")
        video = self.project.source_video
        if video is None or not video.exists():
            raise PipelineError(f"Source video missing: {video}")
        self.settings.apply_workflow_choices()   # derive flags from selections
        try:
            self._stage_prepare(video)
            self._stage_transcribe(video)
            self._stage_discover_select()
            self._stage_render()
            if self.project.stage_state("render") == "done":
                self._stage_report()
            self._cleanup_temp()
        except PipelineError:
            raise
        except FFmpegError as e:
            self._fail(str(e))
            raise PipelineError(f"Video processing failed: {e}")
        except Exception as e:                       # noqa: BLE001
            self.project.add_log(f"UNEXPECTED: {type(e).__name__}: {e}")
            self._fail(f"{type(e).__name__}: {e}")
            raise PipelineError(f"Unexpected error: {e}") from e

    # ------------------------------------------------------------------
    def _stage_prepare(self, video: Path) -> None:
        if self.project.stage_state("prepare") == "done":
            self._emit("prepare", 1.0, "metadata loaded")
            return
        self._begin_stage("prepare")
        self._emit("prepare", 0.3, "Reading video metadata")
        md = extract_metadata(video)
        if not md.has_audio and self.settings.voice_mode != "file":
            self._fail("no audio track")
            raise PipelineError(
                "This video has no audio track and no voice file is set. "
                "Speech analysis requires audio.")
        self.project.data["metadata"] = md.to_dict()
        self.project.data["aspect_ratio"] = self.settings.aspect_ratio
        self.project.save()
        self._end_stage("prepare",
                        f"{md.resolution} - {md.duration_hms()} - "
                        f"{md.size_mb():.0f} MB")

    # ------------------------------------------------------------------
    def _stage_transcribe(self, video: Path) -> None:
        if self.project.stage_state("transcribe") == "done":
            self._emit("transcribe", 1.0, "transcript loaded from cache")
            return
        md = self.project.data["metadata"]
        self._begin_stage("transcribe")
        engine = transcriber.TranscriptionEngine.get(
            self.settings.model_size, self.settings.low_memory_mode)
        rt = transcriber.ResumableTranscriber(
            self.project, engine, md["duration"],
            progress_cb=lambda f, i, n, eta=0.0: self._emit(
                "transcribe", f,
                f"section {i}/{n}" + (f" · ~{eta:.0f}s left" if eta > 3 else "")))
        payload = rt.run()
        n_words = sum(len(s.get("text", "").split()) for s in payload["segments"])
        low_conf = sum(1 for s in payload["segments"]
                       if s.get("confidence", 1) < 0.6)
        self.project.data["transcript_stats"] = {
            "segments": len(payload["segments"]), "words": n_words,
            "low_confidence_segments": low_conf, "windows": payload["windows"],
        }
        self.project.save()
        self._end_stage("transcribe", f"{n_words} words")

    # ------------------------------------------------------------------
    def _stage_discover_select(self) -> None:
        if self.project.stage_state("select") == "done":
            self._emit("select", 1.0, "selection loaded from cache")
            return
        t_all = time.time()
        self._begin_stage("discover")
        segs = self._load_transcript()
        if not segs:
            self._fail("no speech detected")
            raise PipelineError(
                "No speech was detected in this video, so no clips can be "
                "generated.")
        sentences = segmentation.build_sentences(segs)
        thoughts = segmentation.build_thoughts(sentences)
        if not thoughts:
            self._fail("no usable segments")
            raise PipelineError("Transcript produced no usable segments.")

        dmin, dtarget, dmax = self.settings.effective_durations()
        self._emit("discover", 0.1,
                   f"{len(sentences)} sentences - scanning the whole timeline")
        eng = discovery.DiscoveryEngine(
            sentences, visual_lookup=None, settings=self.settings,
            progress_cb=lambda f: self._emit("discover", 0.1 + 0.6 * f, ""))
        candidates = eng.generate(dmin, dmax, dtarget)
        if not candidates:
            # fallback: some videos have extreme pacing - retry relaxed
            candidates = eng.generate(max(8, dmin // 2), dmax * 2, dmax)
        if not candidates:
            self._fail("no candidates")
            raise PipelineError(
                "No candidate moments were found. The video may have too "
                "little structured speech.")
        self._emit("discover", 0.75,
                   f"{len(candidates)} moments found - scoring")
        self._end_stage("discover",
                        f"{len(candidates)} candidates from "
                        f"{len(sentences)} sentences")

        # ---- ranking (text-based; cheap) ------------------------------------
        self._begin_stage("select")
        scorer = scoring.Scorer(self.settings, sentences, thoughts,
                                visual_lookup=None)
        survivors: list[discovery.Candidate] = []
        for i, c in enumerate(candidates):
            self._check_cancel()
            scorer.score(c)                    # visual stays neutral 0.5 here
            reason = scorer.hard_reject(c)
            if reason:
                c.verdict = "rejected"
                c.reject_reason = reason
            else:
                survivors.append(c)
            if i % 50 == 0:
                self._emit("select", 0.1 + 0.25 * i / max(1, len(candidates)),
                           f"ranked {i + 1}/{len(candidates)}")

        # ---- cheap visual check for contenders only -------------------------
        self._check_cancel()
        # Diversity-aware contender pool: walk score-ranked survivors and
        # skip near-duplicate TOPICS, so repeated content (a phrase said 44
        # times in a long video) cannot crowd other topics out of the pool.
        from .core.dedup import top_tokens as _top_tokens
        contenders: list[discovery.Candidate] = []
        picked_tokens: list[set] = []
        for c in sorted(survivors, key=lambda c: c.score, reverse=True):
            if len(contenders) >= 60:
                break
            toks = set(_top_tokens(c.text, 8))
            if any(len(toks & pt) / max(1, min(len(toks), len(pt))) > 0.6
                   for pt in picked_tokens if pt):
                continue                          # same topic as a contender
            contenders.append(c)
            picked_tokens.append(toks)
        self._emit("select", 0.4,
                   f"visual check on {len(contenders)} contenders")
        sampler = visual.FrameSampler(self.project.source_video)
        vis_cache: dict[int, dict] = {}

        def vis_lookup(t: float) -> dict | None:
            key = int(t // 30)                 # 30s granularity buckets
            if key not in vis_cache:
                v = sampler.quick_score(max(0.0, key * 30), key * 30 + 30,
                                        want_face=True)
                vis_cache[key] = v.to_dict()
            return vis_cache[key]

        scorer2 = scoring.Scorer(self.settings, sentences, thoughts,
                                 visual_lookup=vis_lookup)
        for c in contenders:
            scorer2.score(c)

        # ---- dedup then select the requested number --------------------------
        self._emit("select", 0.6, "removing duplicates")
        kept, removed = dedup.deduplicate(contenders, len(contenders))
        pool = sorted(kept, key=lambda c: c.score, reverse=True)
        strong = [c for c in pool if c.score >= self.settings.min_score]
        fair = [c for c in pool if c.score < self.settings.min_score
                and c.score >= self.settings.min_score - 15]

        n = max(1, int(self.settings.clip_count))
        total = self.project.data.get("metadata", {}).get("duration", 0)
        selected, note = self._spread_select(strong, fair, n, total)

        # ---- boundary optimization on the final picks ------------------------
        self._emit("select", 0.8, "optimizing boundaries")
        opt = BoundaryOptimizer(sentences, thoughts, self.settings)
        final: list[discovery.Candidate] = []
        for c in selected:
            r = opt.optimize(c)
            c.start, c.end = r.start, r.end
            if r.notes:
                c.selected_reason = "; ".join(r.notes)
            if c.duration <= dmax + 5:
                final.append(c)
        final.sort(key=lambda c: c.start)

        # thumbnails (tiny, one frame per selected clip)
        for i, c in enumerate(final):
            sampler.extract_frame_jpg(c.start + 1.0,
                                      self.project.path("candidates",
                                                        f"thumb_{i:02d}.jpg"))

        self.project.data["candidates"] = [c.to_dict() for c in final]
        self.project.data["rejected_count"] = len(candidates) - len(final)
        self.project.data["candidates_all_count"] = len(candidates)
        self.project.data["selection_note"] = note
        self.project.save()
        self.timings["select"] = round(time.time() - self._stage_t0, 1)
        self.project.data.setdefault("stage_timings", {})["select"] = \
            self.timings["select"]
        self.project.set_stage("select", "done")
        self.project.save()
        self._emit("select", 1.0,
                   f"{len(final)} clips selected - {note}")

    # ------------------------------------------------------------------
    @staticmethod
    def _spread_select(strong: list, fair: list, n: int,
                       total: float) -> tuple[list, str]:
        """Pick n clips: best-per-timeline-zone first, then MMR-style fill.

        The fill step balances score against uniqueness: a candidate that is
        textually similar to an already-selected clip is penalized, so the
        final list covers different topics instead of near-duplicates.
        """
        from .core.dedup import text_similarity
        picks: list = []
        zones = max(1, min(n, 8))
        if total > 0:
            buckets: list[list] = [[] for _ in range(zones)]
            for c in strong:
                b = min(zones - 1, int(c.start / total * zones))
                buckets[b].append(c)
            for b in buckets:                  # time order -> natural spread
                if b and len(picks) < n:
                    picks.append(b[0])
        pool = [c for c in strong if c not in picks]
        while len(picks) < n and pool:
            # MMR: relevance minus similarity to what we already picked
            best, best_val = None, -1e9
            for c in pool:
                sim = max((text_similarity(c, k) for k in picks), default=0.0)
                val = c.score - 25.0 * sim
                if val > best_val:
                    best, best_val = c, val
            picks.append(best)
            pool.remove(best)
        n_strong = len(picks)
        fair_pool = [c for c in fair if c not in picks]
        while len(picks) < n and fair_pool:
            best, best_val = None, -1e9
            for c in fair_pool:
                sim = max((text_similarity(c, k) for k in picks), default=0.0)
                val = c.score - 25.0 * sim
                if val > best_val:
                    best, best_val = c, val
            best.verdict = "accepted"
            best.quality = "fair"
            picks.append(best)
            fair_pool.remove(best)
        picks.sort(key=lambda c: c.start)
        if len(picks) < n:
            note = (f"requested {n}, found {n_strong} strong"
                    + (f" + {len(picks) - n_strong} fair" if len(picks) > n_strong else "")
                    + f" - exported {len(picks)}")
        elif n_strong < n:
            note = (f"requested {n}: {n_strong} strong + "
                    f"{n - n_strong} fair moments included")
        else:
            note = f"top {n} moments, spread across the video"
        return picks, note

    # ------------------------------------------------------------------
    def _stage_render(self) -> None:
        if (self.project.stage_state("render") == "done"
                and self.project.data.get("candidates")):
            self._emit("render", 1.0, "clips loaded from cache")
            return
        cands = self.project.data.get("candidates", [])
        if not cands:
            self._fail("nothing selected")
            raise PipelineError(
                "No clips were selected, so nothing was rendered. "
                + self.project.data.get("selection_note", ""))
        self._begin_stage("render")
        ok_names: list[str] = []
        failed: list[dict] = []
        for i, cand in enumerate(cands):
            self._check_cancel()
            name = f"short_{i + 1:03d}.mp4"
            self._emit("render", i / len(cands),
                       f"clip {i + 1}/{len(cands)}: {cand['duration']:.0f}s")
            cand["voiceover"] = self._voiceover_for(cand)
            try:
                meta = export_mod.export_clip(
                    self.project, cand, self.settings, name,
                    progress=lambda f, m, i=i: self._emit(
                        "render", (i + f) / len(cands),
                        f"clip {i + 1}/{len(cands)}: {m}"))
                ok_names.append(name)
                cand["export_file"] = name
                self.project.save()
            except Exception as e:              # noqa: BLE001
                failed.append({"file": name, "uid": cand.get("uid"),
                               "error": str(e)})
                self.project.add_log(f"RENDER FAIL {name}: {e}")
        self.project.data["render_failures"] = failed
        self.project.save()
        if not ok_names and failed:
            self._fail(failed[0]["error"])
            raise PipelineError(f"All renders failed: {failed[0]['error']}")
        note = f"{len(ok_names)} clips rendered"
        if failed:
            note += f", {len(failed)} failed"
        self._end_stage("render", note)
        self.project.data["status"] = "ready"
        self.project.save()

    def _voiceover_for(self, cand: dict) -> str:
        """Resolve the per-clip voiceover source from voice_mode."""
        mode = self.settings.voice_mode
        if mode == "file" and self.settings.voice_file:
            return self.settings.voice_file
        if mode == "tts":
            from .core import tts
            preset_id = cand.get("voice_preset") or self.settings.voice_preset
            preset = tts.get_preset(preset_id) or tts.get_preset(
                "storytelling")
            if preset is None:
                self._fail("no TTS voices installed")
                raise PipelineError(
                    "No local TTS voices are installed on this Windows "
                    "system, so AI voice narration is unavailable. Install "
                    "Windows speech voices or choose another voice option.")
            wav = self.project.path(
                "voiceovers",
                f"tts_{preset.id}_{cand['uid']}.wav")
            if not wav.exists():
                self._emit("render", 0.0,
                           f"synthesizing local voice ({preset.category})")
                tts.synthesize(cand.get("text", ""), wav, preset,
                               speed_offset=self.settings.tts_speed,
                               volume=self.settings.tts_volume)
            return str(wav)
        return ""

    # ------------------------------------------------------------------
    def _cleanup_temp(self) -> None:
        import shutil as _shutil
        for sub in ("render", "ffmpeg", "python"):
            d = config.TEMP_DIR / sub
            if d.exists():
                for p in d.iterdir():
                    try:
                        if p.is_dir():
                            _shutil.rmtree(p, ignore_errors=True)
                        else:
                            p.unlink(missing_ok=True)
                    except OSError:
                        pass

    # ------------------------------------------------------------------
    def _load_transcript(self) -> list[transcriber.Segment]:
        tj = self.project.path("transcripts", "transcript.json")
        if not tj.exists():
            raise PipelineError("transcript.json missing - rerun analysis")
        data = json.loads(tj.read_text(encoding="utf-8"))
        return [transcriber.Segment.from_dict(s) for s in data["segments"]]

    def _stage_report(self) -> None:
        """Write the processing report (called after render)."""
        md = self.project.data.get("metadata", {})
        report = {
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "project": self.project.id,
            "video": {"name": self.project.data.get("source_name"),
                      "duration": md.get("duration", 0)},
            "stage_timings_seconds": self.project.data.get("stage_timings"),
            "transcript_stats": self.project.data.get("transcript_stats"),
            "candidates_total": self.project.data.get("candidates_all_count"),
            "clips_selected": len(self.project.data.get("candidates", [])),
            "selection_note": self.project.data.get("selection_note"),
            "render_failures": self.project.data.get("render_failures", []),
            "settings": {
                "clip_count": self.settings.clip_count,
                "duration_mode": self.settings.duration_mode,
                "caption_mode": self.settings.caption_mode,
                "voice_mode": self.settings.voice_mode,
                "model": self.settings.model_size,
            },
        }
        out = self.project.path("analysis", "processing_report.json")
        out.write_text(json.dumps(report, indent=2, ensure_ascii=False),
                       encoding="utf-8")
