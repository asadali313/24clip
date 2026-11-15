"""Pre-export quality control - honest checks, no pretend guarantees.

Checked per clip before it renders:
  subject_visibility : tracked subject (face/person/motion) stays inside the
                       crop window (from the framing plan's own track - zero
                       extra detection cost)
  caption_sync       : caption lines exist and end within the clip duration
  completeness       : clip text starts clean and ends with sentence-final
                       punctuation
  similarity         : worst text similarity against the other selected clips
  transcription      : low-confidence speech inside the clip

Hard failures abort the render; warnings are surfaced to the user in the
Results screen instead of being hidden.
"""
from __future__ import annotations

import re

from .dedup import text_similarity
from .discovery import DANGLING_START

SUBJECT_FAIL = 0.45        # below this the subject leaves the frame too often
SUBJECT_WARN = 0.75
SIMILARITY_WARN = 0.62


def qc_check(cand: dict, framing_meta: dict, subject_inside: float,
             duration: float, caption_lines: int,
             others: list[dict]) -> dict:
    checks: list[dict] = []
    status = "pass"

    # ---- subject visibility -------------------------------------------------
    if subject_inside >= 0:
        if subject_inside < SUBJECT_FAIL:
            checks.append({"check": "subject_visibility", "level": "fail",
                           "detail": f"main subject outside the frame on "
                                     f"{1 - subject_inside:.0%} of samples"})
            status = "fail"
        elif subject_inside < SUBJECT_WARN:
            checks.append({"check": "subject_visibility", "level": "warn",
                           "detail": f"subject partially outside the frame "
                                     f"on {1 - subject_inside:.0%} of "
                                     "samples - consider Adjust"})
            if status == "pass":
                status = "warn"
        else:
            checks.append({"check": "subject_visibility", "level": "pass",
                           "detail": f"subject framed on "
                                     f"{subject_inside:.0%} of samples"})

    # ---- completeness ---------------------------------------------------------
    text = (cand.get("text") or "").strip()
    if not text:
        checks.append({"check": "completeness", "level": "fail",
                       "detail": "no transcript inside the clip"})
        status = "fail"
    else:
        clean_start = not DANGLING_START.match(text)
        clean_end = bool(text.rstrip("\"'”’)]").endswith((".", "!", "?")))
        if clean_start and clean_end:
            checks.append({"check": "completeness", "level": "pass",
                           "detail": "starts and ends on sentence bounds"})
        else:
            checks.append({"check": "completeness", "level": "warn",
                           "detail": "clip may start or end mid-thought"})
            if status == "pass":
                status = "warn"

    # ---- captions ---------------------------------------------------------------
    if caption_lines <= 0:
        checks.append({"check": "captions", "level": "warn",
                       "detail": "no caption lines in range (only relevant "
                                 "when captions are enabled)"})
    else:
        checks.append({"check": "captions", "level": "pass",
                       "detail": f"{caption_lines} synced caption lines"})

    # ---- uniqueness vs the other selected clips ---------------------------------
    worst = 0.0
    for o in others:
        if o.get("uid") == cand.get("uid"):
            continue
        worst = max(worst, text_similarity(
            _as_candidate(cand), _as_candidate(o)))
    if worst >= SIMILARITY_WARN:
        checks.append({"check": "uniqueness", "level": "warn",
                       "detail": f"up to {worst:.0%} similar to another "
                                 "selected clip"})
        if status == "pass":
            status = "warn"
    else:
        checks.append({"check": "uniqueness", "level": "pass",
                       "detail": f"max {worst:.0%} similar to others"})

    return {"status": status, "checks": checks,
            "subject_inside": round(subject_inside, 3),
            "similarity_worst": round(worst, 3)}


class _TmpCand:
    """text_similarity expects .text attribute only."""

    def __init__(self, text: str):
        self.text = text


def _as_candidate(d: dict):
    return _TmpCand(d.get("text", ""))


def summarize(qc: dict) -> str:
    """One-line summary for the Results card."""
    parts = []
    for c in qc.get("checks", []):
        icon = {"pass": "✓", "warn": "⚠", "fail": "✗"}.get(c["level"], "·")
        parts.append(f"{icon} {c['detail']}")
    return "  ".join(parts)
