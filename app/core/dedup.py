"""Duplicate prevention: temporal, lexical and topical similarity."""
from __future__ import annotations

import difflib
import re

from .discovery import Candidate
from .segmentation import Sentence


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z']{3,}", text.lower()))


def time_overlap(a: Candidate, b: Candidate) -> float:
    """Overlap fraction relative to the shorter clip (0..1)."""
    lo = max(a.start, b.start)
    hi = min(a.end, b.end)
    if hi <= lo:
        return 0.0
    inter = hi - lo
    shorter = min(a.duration, b.duration)
    return inter / shorter if shorter > 0 else 0.0


def text_similarity(a: Candidate, b: Candidate) -> float:
    """Combined lexical similarity (jaccard + sequence ratio)."""
    ta, tb = _tokens(a.text), _tokens(b.text)
    if not ta or not tb:
        return 0.0
    jac = len(ta & tb) / len(ta | tb)
    seq = difflib.SequenceMatcher(None, a.text.lower(), b.text.lower()).ratio()
    return 0.5 * jac + 0.5 * seq


def top_tokens(text: str, k: int = 6) -> list[str]:
    """Most frequent content tokens - the clip's topical fingerprint."""
    from collections import Counter
    toks = [w for w in re.findall(r"[a-zA-Z']{3,}", text.lower())
            if w not in ("the", "and", "that", "this", "with", "for", "you",
                         "are", "was", "have", "not", "but", "they", "from",
                         "what", "when", "your", "just", "like", "about")]
    return [w for w, _ in Counter(toks).most_common(k)]


def topic_overlap(a: Candidate, b: Candidate) -> float:
    ta, tb = set(top_tokens(a.text, 8)), set(top_tokens(b.text, 8))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


def is_duplicate(a: Candidate, b: Candidate,
                 overlap_thresh: float = 0.30,
                 text_thresh: float = 0.55,
                 topic_thresh: float = 0.60) -> tuple[bool, str]:
    ov = time_overlap(a, b)
    tx = text_similarity(a, b)
    top = topic_overlap(a, b)
    if ov >= overlap_thresh and tx >= text_thresh:
        return True, f"same moment (overlap {ov:.0%}, text {tx:.0%})"
    if top >= topic_thresh and tx >= 0.42:
        return True, f"same topic (topic {top:.0%}, text {tx:.0%})"
    if ov >= 0.60:
        return True, f"heavy time overlap ({ov:.0%})"
    return False, ""


def deduplicate(candidates: list[Candidate],
                keep: int) -> tuple[list[Candidate], list[Candidate]]:
    """Greedy keep-best pass. Returns (kept, removed) with reasons."""
    ordered = sorted(candidates, key=lambda c: c.score, reverse=True)
    kept: list[Candidate] = []
    removed: list[Candidate] = []
    for c in ordered:
        dup_of = None
        for k in kept:
            dup, why = is_duplicate(c, k)
            if dup:
                dup_of = (k, why)
                break
        if dup_of:
            c.verdict = "rejected"
            c.reject_reason = f"duplicate of {dup_of[0].uid}: {dup_of[1]}"
            removed.append(c)
        else:
            kept.append(c)
        if len(kept) >= keep:
            # still need to mark the rest as rejected for the report
            rest_start = ordered.index(c) + 1
            for rest in ordered[rest_start:]:
                if rest.verdict == "pending":
                    rest.verdict = "rejected"
                    rest.reject_reason = "below max clips limit"
                    removed.append(rest)
            break
    # anything left in pending (didn't reach keep limit) is kept only if valid
    for c in ordered:
        if c.verdict == "pending" and c not in kept and c not in removed:
            c.verdict = "rejected"
            c.reject_reason = "score below threshold"
            removed.append(c)
    kept.sort(key=lambda c: c.start)          # chronological for the user
    return kept, removed


def timeline_distribution(candidates: list[Candidate], total: float,
                          buckets: int = 10) -> list[dict]:
    """Where in the video the clips come from (for the report)."""
    if total <= 0:
        return []
    out = []
    for i in range(buckets):
        lo, hi = total * i / buckets, total * (i + 1) / buckets
        n = sum(1 for c in candidates if c.start < hi and c.end > lo)
        out.append({
            "bucket": i, "from": round(lo, 1), "to": round(hi, 1),
            "clips": n,
            "label": f"{int(lo // 60)}-{int(hi // 60)} min",
        })
    return out
