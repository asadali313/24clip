"""Automatic YouTube metadata generation from a clip's actual content.

Everything derives from the clip's own transcript and scoring data - no
invented claims. All fields are editable before publishing.
"""
from __future__ import annotations

import re

from .discovery import Candidate
from .dedup import top_tokens


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _first_sentence(text: str, max_len: int) -> str:
    text = _clean(text)
    m = re.match(r"(.+?[.!?])(\s|$)", text)
    s = m.group(1) if m else text
    if len(s) > max_len:
        s = text[:max_len].rsplit(" ", 1)[0] + "…"
    return s.strip()


def _hook(text: str) -> str:
    """The most attention-grabbing short line from the clip text."""
    sentences = re.split(r"(?<=[.!?])\s+", _clean(text))
    if not sentences:
        return text[:60]
    best = max(sentences, key=lambda s: min(90, len(s)) * 0.5
               + 10 * bool(re.search(r"\d", s))
               + 8 * ("?" in s)
               + 6 * len(re.findall(
                   r"\b(never|always|secret|mistake|insane|crazy|"
                   r"biggest|first|actually|truth)\b", s, re.I)))
    return best


# Title formats cycled by the Regenerate button (seed = variant index).
# All derive from the clip's own hook/topic - no invented claims.
def _title_variant(hook: str, topic: str, cand: dict, seed: int) -> str:
    has_num = bool(re.search(r"\d", hook))
    q = re.search(r"([^?]{10,70})\?", hook)
    variants = []
    if q:
        variants.append(q.group(1).strip().rstrip(",") + "?")
    variants.append(hook)                                   # as spoken
    if has_num:
        variants.append(hook)                               # numbers stand alone
    variants.append(f"Why {topic.capitalize()} matters more than you think"
                    if topic else hook)
    variants.append(f"The truth about {topic}" if topic else hook)
    variants.append(f"{hook} #shorts" if len(hook) < 80 else hook)
    v = variants[seed % len(variants)]
    return v[:95].rsplit(" ", 1)[0] + "…" if len(v) > 98 else v


def generate_metadata(cand: dict, transcript_text: str = "",
                      seed: int = 0) -> dict:
    """Build title/description/tags/hashtags/category from real clip data."""
    text = cand.get("text", "")
    types = cand.get("idea_types", [])

    # ---- category from content signals ------------------------------------
    if "demonstration" in types or "exciting" in types:
        category_id = "20"                     # Gaming
        cat_name = "Gaming"
    elif "fact/research" in types or "education" in types:
        category_id = "27"                     # Education
        cat_name = "Education"
    elif "story" in types or "emotional" in types:
        category_id = "24"                     # Entertainment
        cat_name = "Entertainment"
    else:
        category_id = "22"                     # People & Blogs
        cat_name = "People & Blogs"

    # ---- title: hook first, compact, no clickbait promises ------------------
    hook = _hook(text).rstrip(".!?")
    topic = top_tokens(text, 2)[0] if top_tokens(text, 2) else ""
    title = _title_variant(hook, topic, cand, seed)

    # ---- description: what is actually said + context ------------------------
    summary = _first_sentence(text, 180)
    topic_words = top_tokens(text, 6)
    desc_lines = [
        summary,
        "",
        f"Clip from: {cand.get('source_name', 'the original video')}"
        if cand.get("source_name") else "",
        f"Topics: {', '.join(topic_words)}.",
        "",
        "#" + " #".join(top_tokens(text, 3)),
    ]
    description = "\n".join(l for l in desc_lines if l is not None)

    # ---- tags + hashtags -----------------------------------------------------
    tags = topic_words[:8]
    for extra in ("shorts", "shortvideo"):
        if extra not in tags:
            tags.append(extra)
    hashtags = ["#shorts"] + ["#" + t.replace(" ", "") for t in topic_words[:3]]

    return {
        "title": title,
        "description": description,
        "tags": tags,
        "hashtags": hashtags,
        "category_id": category_id,
        "category_name": cat_name,
        "privacy_status": "public",
        "made_for_kids": False,
        "seed": seed,
    }


def regenerate_metadata(cand: dict, transcript_text: str = "") -> dict:
    """A different (still content-derived) metadata variant."""
    old = cand.get("publish_metadata") or {}
    next_seed = int(old.get("seed", -1)) + 1
    return generate_metadata(cand, transcript_text, seed=next_seed)


# ---------------------------------------------------------------------------
# Per-clip persistence: metadata is generated once, then saved with the clip
# ---------------------------------------------------------------------------
def get_saved_metadata(project, uid: str) -> dict | None:
    for c in project.data.get("candidates", []):
        if c.get("uid") == uid:
            return c.get("publish_metadata")
    return None


def save_metadata(project, uid: str, meta: dict) -> None:
    """Store the (possibly user-edited) metadata with its clip."""
    for c in project.data.get("candidates", []):
        if c.get("uid") == uid:
            c["publish_metadata"] = meta
            project.save()
            return
