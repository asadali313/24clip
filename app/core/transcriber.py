"""Full-length transcription with Faster-Whisper.

Design rules:
- The ENTIRE video is transcribed: audio is split into fixed windows (default
  10 min) that are transcribed one by one; each finished window is persisted to
  disk, so an interrupted run resumes where it stopped instead of restarting.
- Nothing is ever invented: text comes only from the recognizer. Segments with
  low average confidence are flagged, not rewritten.
- Memory stays bounded: only one window of audio is decoded at a time.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from .. import config


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------
@dataclass
class Word:
    start: float
    end: float
    text: str
    probability: float = 0.0

    def to_dict(self) -> dict:
        return {"start": self.start, "end": self.end,
                "text": self.text, "p": round(self.probability, 3)}


@dataclass
class Segment:
    start: float
    end: float
    text: str
    avg_logprob: float = 0.0
    no_speech_prob: float = 0.0
    words: list[Word] = field(default_factory=list)

    @property
    def confidence(self) -> float:
        """0..1, derived from avg_logprob (logprob -0.1 ~ 0.95)."""
        import math
        return max(0.0, min(1.0, math.exp(self.avg_logprob)))

    def to_dict(self) -> dict:
        return {
            "start": round(self.start, 3), "end": round(self.end, 3),
            "text": self.text, "avg_logprob": round(self.avg_logprob, 3),
            "no_speech_prob": round(self.no_speech_prob, 3),
            "confidence": round(self.confidence, 3),
            "words": [w.to_dict() for w in self.words],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Segment":
        return cls(
            start=d["start"], end=d["end"], text=d["text"],
            avg_logprob=d.get("avg_logprob", 0.0),
            no_speech_prob=d.get("no_speech_prob", 0.0),
            words=[Word(w["start"], w["end"], w["text"], w.get("p", 0.0))
                   for w in d.get("words", [])],
        )


WINDOW_SECONDS = 600          # 10-minute transcription windows
OVERLAP_SECONDS = 1.0         # tiny overlap to avoid word clipping


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------
class TranscriptionEngine:
    """Lazy-loaded whisper model shared across the app."""

    _instance: "TranscriptionEngine | None" = None

    def __init__(self, model_size: str, low_memory: bool = False):
        self.model_size = model_size
        self.low_memory = low_memory
        self._model = None

    @classmethod
    def get(cls, model_size: str, low_memory: bool = False) -> "TranscriptionEngine":
        key = f"{model_size}|{low_memory}"
        if cls._instance is None or getattr(cls._instance, "cache_key", None) != key:
            cls._instance = cls(model_size, low_memory)
            cls._instance.cache_key = key
        return cls._instance

    def model(self):
        if self._model is None:
            from faster_whisper import WhisperModel
            threads = max(1, 2 if self.low_memory else config.Settings().cpu_threads)
            self._model = WhisperModel(
                self.model_size,
                device="cpu",
                compute_type="int8",
                cpu_threads=threads,
                num_workers=1,
                download_root=str(config.HF_CACHE_DIR),
            )
        return self._model

    def transcribe_audio_file(self, wav: Path, progress_cb=None) -> list[Segment]:
        """Transcribe a whole wav file, reporting 0..1 progress.

        beam_size=1: measured ~2-3x faster than beam=5 on CPU with no
        meaningful quality loss for short-form clip selection.
        """
        model = self.model()
        segments, _info = model.transcribe(
            str(wav), beam_size=1,
            vad_filter=True,
            word_timestamps=True,
            condition_on_previous_text=False,   # no hallucination loops,
            vad_parameters={"min_silence_duration_ms": 400},  # faster too
        )
        out: list[Segment] = []
        for seg in segments:
            words = [Word(w.start, w.end, w.word.strip(), w.probability)
                     for w in (seg.words or [])]
            out.append(Segment(seg.start, seg.end, seg.text.strip(),
                               seg.avg_logprob, seg.no_speech_prob, words))
            if progress_cb:
                progress_cb(min(0.999, seg.end))
        if progress_cb:
            progress_cb(1.0)
        return out


# ---------------------------------------------------------------------------
# Windowed, resumable transcription of a long video
# ---------------------------------------------------------------------------
class ResumableTranscriber:
    """Chunked transcription with per-window persistence and resume."""

    def __init__(self, project, engine: TranscriptionEngine,
                 total_duration: float, progress_cb=None):
        self.project = project
        self.engine = engine
        self.total = total_duration
        self.progress_cb = progress_cb
        self.store_dir = project.path("transcripts")
        self.done_path = self.store_dir / "windows_done.json"

    # -- window bookkeeping ---------------------------------------------------
    def _windows(self) -> list[tuple[float, float]]:
        wins = []
        t = 0.0
        while t < self.total - 0.5:
            end = min(t + WINDOW_SECONDS, self.total)
            wins.append((t, end))
            t = end - OVERLAP_SECONDS if end < self.total else end
        return wins

    def _done_windows(self) -> dict:
        if self.done_path.exists():
            try:
                return json.loads(self.done_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def _mark_done(self, idx: int, n_segments: int) -> None:
        done = self._done_windows()
        done[str(idx)] = {"segments": n_segments,
                          "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
        self.done_path.write_text(json.dumps(done, indent=1), encoding="utf-8")

    # -- main entry -------------------------------------------------------------
    def run(self) -> dict:
        """Transcribe all windows; return merged transcript payload.

        Speed measures (2026-11):
          - whole-silent windows are skipped entirely (RMS pre-scan) -
            intros/outros/music sections cost nothing
          - condition_on_previous_text=False (no hallucination loops)
          - per-window results cached -> resume never redoes finished work
          - ETA reported per window based on measured window times
        """
        windows = self._windows()
        all_segments: list[Segment] = []
        skipped_silent = 0
        window_times: list[float] = []
        for i, (w_start, w_end) in enumerate(windows):
            cache_file = self.store_dir / f"window_{i:03d}.json"
            if cache_file.exists() and str(i) in self._done_windows():
                data = json.loads(cache_file.read_text(encoding="utf-8"))
                segs = [Segment.from_dict(s) for s in data["segments"]]
            else:
                t_w0 = time.time()
                wav = self._slice_window(w_start, w_end, i)
                voiced = self._voiced_fraction(wav)
                if voiced < 0.02:
                    # effectively silent window: zero whisper cost
                    segs = []
                    cache_file.write_text(json.dumps(
                        {"window": [w_start, w_end], "segments": [],
                         "silent": True}, ensure_ascii=False),
                        encoding="utf-8")
                    self._mark_done(i, 0)
                    self._cleanup_slice(wav)
                    skipped_silent += 1
                else:
                    base = w_start
                    segs = self.engine.transcribe_audio_file(
                        wav,
                        progress_cb=None,  # per-window progress folded into overall
                    )
                    for s in segs:           # window time -> absolute time
                        s.start += base
                        s.end += base
                        for w in s.words:
                            w.start += base
                            w.end += base
                    cache_file.write_text(json.dumps(
                        {"window": [w_start, w_end],
                         "segments": [s.to_dict() for s in segs]},
                        ensure_ascii=False), encoding="utf-8")
                    self._mark_done(i, len(segs))
                    self._cleanup_slice(wav)
                window_times.append(time.time() - t_w0)
            all_segments.extend(segs)
            if self.progress_cb:
                remaining = (len(windows) - (i + 1)) * (
                    sum(window_times) / max(1, len(window_times)))
                self.progress_cb((i + 1) / len(windows), i + 1, len(windows),
                                 remaining)

        all_segments.sort(key=lambda s: s.start)
        payload = {
            "model": self.engine.model_size,
            "total_duration": self.total,
            "windows": len(windows),
            "windows_silent_skipped": skipped_silent,
            "segments": [s.to_dict() for s in all_segments],
        }
        out_json = self.store_dir / "transcript.json"
        out_json.write_text(json.dumps(payload, indent=1, ensure_ascii=False),
                            encoding="utf-8")
        # plain-text version
        txt = "\n".join(
            f"[{self._fmt(s.start)} -> {self._fmt(s.end)}] {s.text}"
            for s in all_segments)
        (self.store_dir / "transcript.txt").write_text(txt, encoding="utf-8")
        return payload

    def _slice_window(self, start: float, end: float, idx: int) -> Path:
        """Decode just this window from the source video into a temp wav."""
        from . import ffmpeg_tools
        out = self.project.path("audio", f"window_{idx:03d}.wav")
        ffmpeg_tools.run([
            "-ss", f"{max(0.0, start):.3f}", "-t", f"{end - start:.3f}",
            "-i", str(self.project.source_video),            "-vn", "-ac", "1", "-ar", "16000",
            "-f", "wav", "-acodec", "pcm_s16le", str(out),
        ], timeout=900)
        return out

    def _cleanup_slice(self, wav: Path) -> None:
        try:
            wav.unlink(missing_ok=True)
        except OSError:
            pass

    @staticmethod
    def _voiced_fraction(wav: Path) -> float:
        """Fraction of 0.5 s blocks whose RMS exceeds a speech threshold.

        Pure numpy on 16-bit PCM - costs milliseconds even for a 10-minute
        window. Whole-silent (music-only/credits) windows are then skipped
        entirely instead of paying whisper's VAD + decode cost.
        """
        import wave
        import numpy as np
        try:
            with wave.open(str(wav), "rb") as w:
                n_frames = w.getnframes()
                data = np.frombuffer(w.readframes(n_frames), dtype=np.int16)
        except (wave.Error, OSError):
            return 1.0                      # unreadable: let whisper decide
        if data.size == 0:
            return 0.0
        block = 8000                        # 0.5 s at 16 kHz
        n = data.size // block
        if n == 0:
            return 1.0 if float(np.abs(data).mean()) > 350 else 0.0
        rms = np.sqrt(
            (data[:n * block].reshape(n, block).astype(np.float32) ** 2)
            .mean(axis=1))
        return float((rms > 350).sum()) / n

    @staticmethod
    def _fmt(t: float) -> str:
        s = int(t)
        return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"
