"""Timeline segmentation: merge raw whisper segments into sentences & thoughts.

A "thought" is the smallest unit that can stand alone: one or more consecutive
sentences bounded by a real pause. Discovery operates on thoughts, ranking
checks them, boundaries refine them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from .transcriber import Segment, Word

PAUSE_SPLIT = 0.65          # seconds of silence that ends a thought
MAX_THOUGHT_S = 60.0        # safety: never merge beyond this
SENT_END = (".", "!", "?", "。", "！", "？")


@dataclass
class Sentence:
    start: float
    end: float
    text: str
    words: list[Word] = field(default_factory=list)
    confidence: float = 1.0

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class Thought:
    start: float
    end: float
    sentences: list[Sentence] = field(default_factory=list)
    pause_before: float = 0.0   # silence gap before this thought starts

    @property
    def text(self) -> str:
        return " ".join(s.text for s in self.sentences).strip()

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def confidence(self) -> float:
        if not self.sentences:
            return 0.0
        return sum(s.confidence for s in self.sentences) / len(self.sentences)

    @property
    def words(self) -> list[Word]:
        return [w for s in self.sentences for w in s.words]


def build_sentences(segments: Iterable[Segment]) -> list[Sentence]:
    """Split whisper segments on sentence-ending punctuation using word stamps."""
    sentences: list[Sentence] = []
    for seg in segments:
        if not seg.words:
            # no word stamps: whole segment becomes one sentence
            sentences.append(Sentence(seg.start, seg.end, seg.text,
                                      [], seg.confidence))
            continue
        cur_words: list[Word] = []
        for w in seg.words:
            if not w.text:
                continue
            cur_words.append(w)
            stripped = w.text.rstrip("\"'”’)]")
            if stripped.endswith(SENT_END) or stripped.endswith("--"):
                text = " ".join(x.text for x in cur_words).strip()
                if text:
                    sentences.append(Sentence(
                        cur_words[0].start, cur_words[-1].end, text,
                        cur_words, seg.confidence))
                cur_words = []
        if cur_words:                       # remainder without final punctuation
            text = " ".join(x.text for x in cur_words).strip()
            if text:
                sentences.append(Sentence(
                    cur_words[0].start, cur_words[-1].end, text,
                    cur_words, seg.confidence))
    return sentences


def build_thoughts(sentences: list[Sentence]) -> list[Thought]:
    """Group consecutive sentences into thoughts using pause boundaries."""
    thoughts: list[Thought] = []
    cur: list[Sentence] = []
    cur_pause = 0.0
    prev_end: float | None = None
    for s in sentences:
        gap = (s.start - prev_end) if prev_end is not None else 0.0
        too_long = cur and (s.end - cur[0].start) > MAX_THOUGHT_S
        if cur and (gap >= PAUSE_SPLIT or too_long):
            thoughts.append(Thought(cur[0].start, cur[-1].end, cur, cur_pause))
            cur, cur_pause = [], gap
        elif not cur:
            cur_pause = gap
        cur.append(s)
        prev_end = s.end
    if cur:
        thoughts.append(Thought(cur[0].start, cur[-1].end, cur, cur_pause))
    return thoughts


def sentences_in_range(sentences: list[Sentence], start: float,
                       end: float) -> list[Sentence]:
    """All sentences overlapping [start, end] - used for context checks."""
    return [s for s in sentences if s.start < end and s.end > start]
