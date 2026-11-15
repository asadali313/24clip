"""Clip boundary optimization: snap candidate edges to natural speech points.

Rules:
- Start at (or just before) the first sentence start inside the candidate.
- End at the first sentence end at/after the last complete sentence.
- Never cut a word: edges land in silence gaps whenever the transcript shows one.
- Keep a necessary lead-in question by extending backward.
- Preserve the idea: if the full thought needs 34s, keep 34s (within max).
"""
from __future__ import annotations

from dataclasses import dataclass

from .discovery import Candidate
from .segmentation import Sentence, Thought

PAD_START = 0.25          # gentle pre-roll
PAD_END = 0.45            # gentle post-roll


@dataclass
class BoundaryResult:
    start: float
    end: float
    notes: list[str]

    @property
    def duration(self) -> float:
        return self.end - self.start


class BoundaryOptimizer:
    def __init__(self, sentences: list[Sentence], thoughts: list[Thought],
                 settings=None):
        self.sentences = sorted(sentences, key=lambda s: s.start)
        self.thoughts = thoughts
        self.settings = settings

    # ------------------------------------------------------------------
    def _prev_gap(self, t: float) -> float:
        """Silence available just before time t (based on sentence gaps)."""
        prev = None
        for s in self.sentences:
            if s.end <= t:
                prev = s
            else:
                break
        if prev is None:
            return PAD_START
        return max(0.0, t - prev.end)

    def optimize(self, c: Candidate) -> BoundaryResult:
        notes: list[str] = []
        s = self.settings
        target, dmin, dmax = (30, 20, 45)
        if s is not None and hasattr(s, "effective_durations"):
            dmin, target, dmax = s.effective_durations()
        elif s is not None:
            target = getattr(s, "target_duration", 30)
            dmin = getattr(s, "min_duration", 20)
            dmax = getattr(s, "max_duration", 45)

        inside = [x for x in self.sentences if x.start >= c.start - 0.5
                  and x.end <= c.end + 0.5]
        if not inside:
            return BoundaryResult(c.start, c.end, ["no transcript inside"])

        # --- start: first sentence start, but consider extending back -----
        start = inside[0].start
        first_text = inside[0].text
        # if the first sentence is a continuation ("and...", "so..."),
        # pull in the previous sentence when it fits
        from .discovery import CONTINUATION_START
        room_back = start - c.start
        if (CONTINUATION_START.match(first_text) and len(inside) > 1
                and room_back > 0.2):
            prev_idx = self.sentences.index(inside[0]) - 1
            if prev_idx >= 0:
                prev = self.sentences[prev_idx]
                if (c.end - prev.start) <= dmax and prev.end <= start:
                    start = prev.start
                    notes.append("pulled in lead-in sentence")

        # --- end: last sentence end -------------------------------------
        end = inside[-1].end
        # if the clip ends on a question with no answer after it inside,
        # extend to include the answer sentence if present
        joined = " ".join(x.text for x in inside)
        if joined.count("?") > 0:
            q_last = [x for x in inside if "?" in x.text]
            if q_last and inside[-1] is q_last[-1]:
                q_idx = self.sentences.index(q_last[-1])
                if q_idx + 1 < len(self.sentences):
                    nxt = self.sentences[q_idx + 1]
                    if (nxt.end - start) <= dmax:
                        end = nxt.end
                        notes.append("extended to include the answer")

        # --- honor duration targets without destroying the idea ----------
        dur = end - start
        if dur > dmax:
            # drop leading sentences until it fits (keep the hook at the end
            # untouched; hooks live at the start, so drop from the end first)
            while inside and (end - inside[0].start) > dmax and len(inside) > 1:
                dropped = inside.pop(0)
                start = inside[0].start
                notes.append(f"trimmed opening \"{dropped.text[:30]}...\"")
            if end - start > dmax:
                notes.append("duration exceeds max despite trims")
        elif dur < dmin:
            # extend with the following sentence(s) when they exist
            last_idx = self.sentences.index(inside[-1])
            j = last_idx + 1
            while j < len(self.sentences) and (end - start) < dmin:
                nxt = self.sentences[j]
                if nxt.end - start > dmax:
                    break
                end = nxt.end
                inside.append(nxt)
                j += 1
                notes.append("extended to reach minimum duration")

        # --- padding: only into silence -----------------------------------
        avail = self._prev_gap(start)
        start -= min(PAD_START, max(0.0, avail * 0.6))
        end += PAD_END

        return BoundaryResult(max(0.0, start), end, notes)
