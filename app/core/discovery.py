"""Candidate discovery: find moments that can stand alone as a Short.

Strategy (fully local, deterministic, explainable):
1. Walk the thought timeline; each thought is a potential seed idea.
2. Detect content signals with pattern analysis over the sentence text:
   hooks, tips, facts, mistakes, lessons, Q&A, strong opinions, stories.
3. Attach neighboring thoughts when they continue the same idea (context).
4. Estimate information density from lexical statistics.
5. Optionally enrich with a local Ollama model when the user enables it.
No text is ever generated or rewritten - we only select what was actually said.
"""
from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from .segmentation import Sentence, Thought

# ---------------------------------------------------------------------------
# Linguistic signal tables
# ---------------------------------------------------------------------------
HOOK_PATTERNS: list[tuple[str, str]] = [
    (r"\b(secret|secrets)\b", "mystery word"),
    (r"\b(most people|everyone|nobody|no one)\b", "universal claim"),
    (r"\b(mistake|mistakes|wrong|error|fail)\b", "mistake framing"),
    (r"\b(never|always|stop doing|don't|do not)\b", "directive"),
    (r"\b(did you know|you know what|guess what|here's why|here is why)\b", "curiosity gap"),
    (r"\b(the truth|actually|in fact|surprising|surprised|shocking)\b", "surprise"),
    (r"\b(number (one|two|three)|first|second|third|finally)\b", "list hook"),
    (r"\b(how to|the best way|the easiest way|a simple way)\b", "how-to"),
    (r"\b(tip|tips|trick|hack|advice)\b", "tip"),
    (r"\b(lesson|learned|learn the hard way)\b", "lesson"),
    (r"\b(billion|million|thousand|percent|%|\d+)\b", "number"),
    (r"\b(imagine|think about|picture this)\b", "invitation"),
    (r"\b(why|what if|how come)\b.*\?", "question hook"),
]

TIP_PATTERNS = [
    r"\b(make sure|be sure to|you should|try to|start by|the key is|"
    r"the trick is|what you want to do|my advice|the best way)\b",
]
MISTAKE_PATTERNS = [
    r"\b(mistake|mistakes|people get (this|it) wrong|don't do|do not do|"
    r"avoid|stop doing|the problem is|the issue is)\b",
]
FACT_PATTERNS = [
    r"\b(research|study|studies|scientists|according to|in \d{4}|"
    r"discovered|found that|shows that|proven|data)\b",
]
OPINION_PATTERNS = [
    r"\b(i think|i believe|in my opinion|personally|honestly|the truth is|"
    r"i'm convinced|i would argue)\b",
]
STORY_PATTERNS = [
    r"\b(when i was|one day|a few years ago|last (year|week|month)|"
    r"i remember|this reminds me|let me tell you|story)\b",
]
DEMO_PATTERNS = [
    r"\b(watch|look at|see how|let me show you|as you can see|"
    r"notice that|pay attention)\b",
]
EMOTION_PATTERNS = [
    r"\b(hilarious|funny|laugh|laughed|insane|crazy|wild|unbelievable|"
    r"incredible|shocking|wow|omg|no way|you won't believe|"
    r"you wont believe|jaw.?dropping|messed up)\b",
    r"!{2,}",                                # excited punctuation
]
EXCITEMENT_PATTERNS = [
    r"\b(fastest|biggest|craziest|intense|epic|final|boss|battle|fight|"
    r"chase|escape|explosion|winner|champion|record|extreme)\b",
]

# ---------------------------------------------------------------------------
# Content-type vocabularies: detected once per video, used to boost the
# signals that matter for THAT kind of content (gaming vs podcast vs ...).
# ---------------------------------------------------------------------------
VIDEO_TYPE_VOCAB: dict[str, list[str]] = {
    "gaming": [
        r"\b(gameplay|respawn|loadout|boss fight|level up|headshot|"
        r"clutch|squad|lobby|skins|patch|dps|speedrun|open world|quest|"
        r"npc|inventory|crafting)\b",
        r"\b(gta|minecraft|fortnite|valorant|elden ring|call of duty|"
        r"playstation|xbox|rockstar)\b",
    ],
    "sports": [
        r"\b(goal|scored|penalty|half.?time|kickoff|referee|championship|"
        r"playoff|touchdown|inning|tournament|stadium|league)\b",
    ],
    "podcast": [
        r"\b(hot take|unpopular opinion|my guest|great question|"
        r"back to the question|ask me anything|debate|disagree|panel)\b",
    ],
    "documentary": [
        r"\b(historians?|century|archaeologists?|evidence|archive|"
        r"records show|era|civilization|empire|footage|"
        r"according to experts)\b",
    ],
    "shows": [
        r"\b(episode|season|character|plot|scene|cast|trailer|spoiler|"
        r"finale|netflix|series|drama|reaction)\b",
    ],
}

TYPE_SPECIFIC_PATTERNS: dict[str, list[tuple[str, str]]] = {
    "gaming": [
        (r"\b(boss fight|headshot|clutch|comeback|new update|secret room|"
         r"secret (item|weapon)|insane (kill|play)|one hp|last second)\b",
         "gaming highlight"),
    ],
    "sports": [
        (r"\b(incredible goal|last minute|penalty (kick|shootout)|"
         r"world record|unbelievable save)\b", "sports highlight"),
    ],
    "podcast": [
        (r"\b(i strongly (disagree|agree)|the real question|nobody talks "
         r"about|controversial)\b", "podcast moment"),
    ],
    "documentary": [
        (r"\b(little.?known|recently discovered|declassified|"
         r"changed history)\b", "documentary insight"),
    ],
    "shows": [
        (r"\b(that scene|plot twist|best (episode|season)|fan theory)\b",
         "show moment"),
    ],
}

# Discourse markers that make a candidate start mid-conversation
BAD_OPENERS = re.compile(
    r"^(as i was saying|anyway|so anyway|where was i|going back to|"
    r"to answer (your|that) question)\b", re.IGNORECASE)

# Selection modes: which idea_types each mode prioritizes
MODE_TYPES: dict[str, list[str]] = {
    "best": [],
    "funny": ["emotional"],
    "emotional": ["emotional", "story"],
    "interesting": ["fact/research", "question", "has-numbers"],
    "educational": ["tip/advice", "fact/research", "mistake/lesson"],
    "motivational": ["payoff", "story", "opinion"],
    "shocking": ["emotional", "exciting"],
    "gaming": ["gaming highlight", "exciting", "demonstration"],
    "podcast": ["podcast moment", "opinion", "question"],
}


def prompt_keywords(prompt: str) -> list[str]:
    """Content words from the user's custom AI prompt."""
    return [w for w in re.findall(r"[a-zA-Z']{3,}", prompt.lower())
            if w not in STOPWORDS]


# Payoff markers: conclusions that make a clip feel complete
PAYOFF_PATTERNS = [
    r"\b(so the (lesson|point|moral) is|that'?s why|the takeaway|"
    r"in the end|which means|and that is (exactly )?why|bottom line)\b",
]

DANGLING_START = re.compile(
    r"^(and|but|so|because|which|that|this|it|they|he|she|also|then|"
    r"like i (said|mentioned)|as i (said|mentioned)|that's why|which is)\b",
    re.IGNORECASE)
QUESTION = re.compile(r"\?")
CONTINUATION_START = re.compile(
    r"^(and|but|so|because|also|then|plus|another|next|finally|"
    r"that('s| is) why|which)\b", re.IGNORECASE)

STOPWORDS = set("""a an the and or but so if then of to in on at for with by from
is are was were be been being it its it's this that these those i you he she we
they me him her us them my your his their our as not no do does did done doing
have has had having will would can could should may might must about into over
after before between out up down just very really more most some any all what
which who whom when where why how""".split())


@dataclass
class Candidate:
    """A candidate Short: a time range with its reasoning and metrics."""
    uid: str
    start: float
    end: float
    text: str
    idea_types: list[str] = field(default_factory=list)
    hook_signals: list[str] = field(default_factory=list)
    context_notes: str = ""
    # sub-scores 0..1 (filled by scoring.py)
    s_hook: float = 0.0
    s_info: float = 0.0
    s_clarity: float = 0.0
    s_engagement: float = 0.0
    s_completeness: float = 0.0
    s_visual: float = 0.0
    score: float = 0.0          # 0..100
    verdict: str = "pending"    # pending | accepted | rejected
    quality: str = "strong"     # strong | fair  (fair = below min-score gate)
    reject_reason: str = ""
    selected_reason: str = ""
    speech_ratio: float = 1.0
    monotony: float = 0.0          # 0 lively .. 1 flat/boring
    video_type: str = "general"    # detected content type of the source
    words: int = 0
    llm_note: str = ""          # optional Ollama comment

    @property
    def duration(self) -> float:
        return self.end - self.start

    def to_dict(self) -> dict:
        return {
            "uid": self.uid, "start": round(self.start, 2),
            "end": round(self.end, 2), "duration": round(self.duration, 2),
            "text": self.text, "idea_types": self.idea_types,
            "hook_signals": self.hook_signals,
            "context_notes": self.context_notes,
            "subscores": {
                "hook": round(self.s_hook, 3), "info": round(self.s_info, 3),
                "clarity": round(self.s_clarity, 3),
                "engagement": round(self.s_engagement, 3),
                "completeness": round(self.s_completeness, 3),
                "visual": round(self.s_visual, 3),
            },
            "score": round(self.score, 1), "verdict": self.verdict,
            "quality": self.quality,
            "reject_reason": self.reject_reason,
            "selected_reason": self.selected_reason,
            "speech_ratio": round(self.speech_ratio, 3), "words": self.words,
            "monotony": round(self.monotony, 2),
            "video_type": self.video_type,
            "llm_note": self.llm_note,
        }


# ---------------------------------------------------------------------------
# Signal helpers
# ---------------------------------------------------------------------------
def detect_video_type(full_text: str) -> str:
    """Classify the video from vocabulary density (gaming/sports/podcast/
    documentary/shows/general). Used to pick type-specific signals."""
    best_type, best_score = "general", 0.0
    words = max(50, len(full_text.split()))
    for vtype, patterns in VIDEO_TYPE_VOCAB.items():
        score = sum(len(re.findall(p, full_text, re.IGNORECASE))
                    for p in patterns) / words * 1000
        if score > best_score:
            best_type, best_score = vtype, score
    return best_type if best_score >= 1.0 else "general"


def _hits(patterns: list[str] | list[tuple[str, str]], text: str) -> list[str]:
    out = []
    for p in patterns:
        name = p[1] if isinstance(p, tuple) else p
        pat = p[0] if isinstance(p, tuple) else p
        if re.search(pat, text, re.IGNORECASE):
            out.append(name)
    return out


def _monotony(text: str) -> float:
    """0 = lively, 1 = flat monotone delivery (boring signal).

    Flatness = long uniform sentences, no questions, no numbers, no
    punctuation energy, low lexical variety.
    """
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    if not sentences:
        return 0.5
    lens = [len(s.split()) for s in sentences]
    uniform = 1.0 - min(1.0, (max(lens) - min(lens)) / 12)
    questions = 1 if "?" in text else 0
    numbers = 1 if re.search(r"\d", text) else 0
    energy = 1 if re.search(r"[!?]", text) else 0
    toks = re.findall(r"[a-zA-Z']{3,}", text.lower())
    variety = (len(set(toks)) / max(1, len(toks)))
    flat = (0.4 * uniform + 0.25 * (1 - max(questions, numbers, energy))
            + 0.35 * (1.0 - min(1.0, variety * 1.8)))
    return max(0.0, min(1.0, flat))


def content_words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-zA-Z']+", text.lower())
            if w not in STOPWORDS and len(w) > 2]


def _lexical_stats(text: str) -> tuple[float, float, int]:
    """Return (density 0..1, numbers_present, content word count)."""
    words = content_words(text)
    if not words:
        return 0.0, 0, 0
    uniq = len(set(words))
    density = min(1.0, uniq / max(8, len(words)))
    numbers = len(re.findall(r"\b\d[\d.,%]*\b|\b(one|two|three|ten|hundred|"
                             r"thousand|million|billion|percent)\b",
                             text, re.IGNORECASE))
    return density, numbers, len(words)


# ---------------------------------------------------------------------------
# Core discovery
# ---------------------------------------------------------------------------
class DiscoveryEngine:
    def __init__(self, sentences: list[Sentence], visual_lookup=None,
                 settings=None, progress_cb=None):
        # Windows are built over SENTENCES, not pause-delimited thoughts:
        # continuous narration (video essays, voiceovers) has almost no
        # pauses, so thought grouping can yield 60s monsters that always
        # overflow the duration limit. Sentences exist in any speech.
        self.sentences = sentences
        self.visual_lookup = visual_lookup or (lambda t: None)
        self.settings = settings
        self.progress_cb = progress_cb
        # content-type detection once per video (vocabulary density)
        full = " ".join(s.text for s in sentences)
        self.video_type = detect_video_type(full)

    # -- context assembly ------------------------------------------------
    def _context_text(self, i: int, before: int, after: int) -> str:
        parts = []
        for j in range(max(0, i - before),
                       min(len(self.sentences), i + after + 1)):
            parts.append(self.sentences[j].text)
        return " ".join(parts)

    # -- candidate generation ----------------------------------------------
    def generate(self, min_duration: float, max_duration: float,
                 target_duration: float) -> list[Candidate]:
        """Sliding window over the whole sentence timeline.

        Every part of the video gets considered: a window starts at each
        sentence and accumulates following sentences until it is close to the
        target duration (never beyond the max). Overlapping windows are fine -
        duplicate removal keeps the best one.
        """
        candidates: list[Candidate] = []
        n = len(self.sentences)
        for i in range(n):
            if self.progress_cb:
                self.progress_cb((i + 1) / max(1, n))
            first = self.sentences[i]
            if len(first.text.split()) < 4:
                continue
            group: list[Sentence] = [first]
            dur = first.duration
            j = i + 1
            while j < n and dur < target_duration * 0.92:
                nxt = self.sentences[j]
                # stop expanding once we have enough, unless the window is
                # still shorter than 60% of the minimum
                if dur + nxt.duration > max_duration * 1.05 \
                        and dur >= min_duration * 0.6:
                    break
                group.append(nxt)
                dur += nxt.duration
                j += 1
            start, end = group[0].start, group[-1].end
            span = end - start
            if span < min_duration * 0.45 or span > max_duration * 1.35:
                continue

            text = " ".join(s.text for s in group)
            # boring-segment rejection: a flat monotone window with zero
            # signal words is not Short material regardless of placement
            flat = _monotony(text)
            if flat > 0.82:
                sig = (_hits(HOOK_PATTERNS, text) + _hits(TIP_PATTERNS, text)
                       + _hits(FACT_PATTERNS, text)
                       + _hits(EMOTION_PATTERNS, text))
                if not sig:
                    continue
            c = Candidate(
                uid=f"cand_{i:04d}",
                start=start, end=end, text=text,
                words=len(text.split()),
            )
            c.idea_types = self._idea_types(text)
            c.hook_signals = _hits(HOOK_PATTERNS, text)
            c.monotony = flat
            c.video_type = self.video_type
            c.context_notes = self._context_note(i, group)
            # speech ratio: voiced sentence time / total span
            c.speech_ratio = min(1.0, dur / max(0.001, span))
            candidates.append(c)
        return candidates

    # -- classification ------------------------------------------------------
    def _idea_types(self, text: str) -> list[str]:
        types = []
        if _hits(MISTAKE_PATTERNS, text):
            types.append("mistake/lesson")
        if _hits(TIP_PATTERNS, text):
            types.append("tip/advice")
        if _hits(FACT_PATTERNS, text):
            types.append("fact/research")
        if _hits(OPINION_PATTERNS, text):
            types.append("opinion")
        if _hits(STORY_PATTERNS, text):
            types.append("story")
        if _hits(DEMO_PATTERNS, text):
            types.append("demonstration")
        if _hits(EMOTION_PATTERNS, text):
            types.append("emotional")
        if _hits(EXCITEMENT_PATTERNS, text):
            types.append("exciting")
        # type-specific signals (gaming highlight, sports highlight, ...)
        for sig in _hits(
                TYPE_SPECIFIC_PATTERNS.get(self.video_type, []), text):
            types.append(sig)
        if _hits(PAYOFF_PATTERNS, text):
            types.append("payoff")
        if QUESTION.search(text):
            types.append("question")
        _, numbers, _ = _lexical_stats(text)
        if numbers:
            types.append("has-numbers")
        if not types:
            types.append("statement")
        return types

    def _context_note(self, i: int, group: list[Sentence]) -> str:
        """Human-readable note on surrounding context for review."""
        notes = []
        first_idx = self.sentences.index(group[0]) if self.sentences else i
        if first_idx > 0:
            prev = self.sentences[first_idx - 1].text
            if len(prev.split()) > 3:
                notes.append(f"before: \"{prev[:70]}...\"" if len(prev) > 70
                             else f"before: \"{prev}\"")
        last_idx = first_idx + len(group) - 1
        if last_idx + 1 < len(self.sentences):
            nxt = self.sentences[last_idx + 1].text
            if len(nxt.split()) > 3:
                notes.append(f"after: \"{nxt[:70]}...\"" if len(nxt) > 70
                             else f"after: \"{nxt}\"")
        return " | ".join(notes)


# ---------------------------------------------------------------------------
# Optional Ollama enrichment (never mandatory, off by default)
# ---------------------------------------------------------------------------
OLLAMA_PROMPT = """You are a shorts-clipping assistant. Rate this spoken excerpt
for use as a standalone vertical short. Excerpt:
\"\"\"{text}\"\"\"
Context before/after (do NOT include in the clip): {context}
Answer with strict JSON only: {{"score": 0-10, "standalone": true/false, "note": "<=15 words why"}}"""


def ollama_enrich(candidates: list[Candidate], settings, timeout: int = 20) -> None:
    """Best-effort scoring via local Ollama. Silently skips on any failure."""
    if not getattr(settings, "enable_ollama", False):
        return
    url = settings.ollama_url.rstrip("/") + "/api/generate"
    for c in candidates[:20]:                       # cap cost
        prompt = OLLAMA_PROMPT.format(text=c.text[:1200], context=c.context_notes)
        payload = json.dumps({
            "model": settings.ollama_model, "prompt": prompt,
            "stream": False, "format": "json",
            "options": {"temperature": 0.2},
        }).encode("utf-8")
        try:
            req = urllib.request.Request(
                url, data=payload, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            inner = json.loads(data.get("response", "{}"))
            score = float(inner.get("score", 5)) / 10.0
            c.s_llm = score
            c.llm_note = f"{inner.get('standalone', '')}; {inner.get('note', '')}"
        except Exception:
            continue    # offline / not installed / slow: enrichment is optional
