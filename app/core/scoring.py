"""Candidate scoring: weighted criteria with hard rejection rules.

Weights (default, configurable via Settings):
    hook 20 | info 20 | clarity 20 | engagement 15 | completeness 15 | visual 10
Overall score is 0..100. Hard rules can reject regardless of score.
"""
from __future__ import annotations

import statistics

from .discovery import Candidate, DANGLING_START, BAD_OPENERS, _hits, \
    _lexical_stats
from .discovery import FACT_PATTERNS, HOOK_PATTERNS, TIP_PATTERNS
from .segmentation import Sentence, Thought


class Scorer:
    def __init__(self, settings=None, sentences: list[Sentence] | None = None,
                 thoughts: list[Thought] | None = None, visual_lookup=None):
        self.settings = settings
        self.sentences = sentences or []
        self.thoughts = thoughts or []
        self.visual_lookup = visual_lookup or (lambda t: None)

    # -- individual sub-scores -------------------------------------------------
    def score_hook(self, c: Candidate) -> float:
        base = min(1.0, 0.25 * len(c.hook_signals))
        first = c.text.split(".")[0] if "." in c.text else c.text[:80]
        if "?" in first:
            base += 0.15                       # opens with a question
        if any(k in c.idea_types for k in ("mistake/lesson", "surprise")):
            base += 0.1
        # type-specific highlights ARE the hook for their content type
        vtype = getattr(c, "video_type", "general")
        if any(t.endswith("highlight") or t.endswith("moment")
               or t.endswith("insight") for t in c.idea_types):
            base += 0.15
        # a number early in the clip is a strong attention cue
        head = c.text[:120]
        import re
        if re.search(r"\d", head):
            base += 0.1
        return min(1.0, base)

    def score_info(self, c: Candidate) -> float:
        density, numbers, n_words = _lexical_stats(c.text)
        score = 0.45 * density
        score += min(0.25, 0.08 * numbers)
        # useful content types carry information even without drama
        if any(t in c.idea_types for t in ("tip/advice", "fact/research")):
            score += 0.2
        if "payoff" in c.idea_types:
            score += 0.08                       # the clip concludes something
        if n_words >= 60:
            score += 0.1
        # flat monotone delivery bores viewers regardless of content
        score -= 0.25 * getattr(c, "monotony", 0.0)
        return min(1.0, max(0.0, score))

    def score_clarity(self, c: Candidate) -> float:
        """Can a cold viewer understand this without extra context?"""
        first_words = c.text.strip().split()
        if not first_words:
            return 0.0
        opening = " ".join(first_words[:4])
        penalty = 0.0
        if DANGLING_START.match(opening):
            penalty += 0.45                    # starts mid-thought
        # references to visuals the viewer may not see
        if _hits([r"\b(this one|that one|these|those|this here)\b"], opening):
            penalty += 0.15
        # contains a complete idea: some subject and a predicate
        has_verb = _hits([r"\b(is|are|was|were|do|does|did|can|will|should|"
                          r"make|makes|get|gets|have|has|know|means|"
                          r"\w+ed\b|\w+s\b)"], c.text)
        score = (0.6 if has_verb else 0.2) - penalty
        # pronoun-heavy openers are confusing
        import re
        opener_pronouns = len(re.findall(
            r"^(it|this|that|they|these|those)\b", c.text, re.IGNORECASE))
        score -= 0.05 * opener_pronouns
        return max(0.0, min(1.0, score))

    def score_engagement(self, c: Candidate) -> float:
        """Heuristics: speech pace, energy/emotion, duration fit, motion."""
        dur = c.duration
        # words per second: 2.2-3.5 is a natural engaging pace
        wps = c.words / max(0.1, dur)
        pace = 1.0 if 2.0 <= wps <= 3.6 else max(0.0, 1.0 - abs(wps - 2.8) / 2.5)
        energy = _hits([r"\b(amazing|incredible|crazy|insane|huge|massive|"
                        r"love|hate|best|worst|never|always|shocking)\b"],
                       c.text)
        score = 0.5 * pace + 0.12 * min(3, len(energy)) / 3
        # emotional impact: laughter, disbelief, excitement markers
        emotion = 0.0
        if "emotional" in c.idea_types:
            emotion += 0.12
        if "exciting" in c.idea_types:
            emotion += 0.08
        import re
        if re.search(r"!{2,}", c.text):
            emotion += 0.05
        score += min(0.2, emotion)
        scene = self.visual_lookup(c.start + c.duration / 2) \
            if self.visual_lookup else None
        if scene:
            score += 0.2 * min(1.0, scene.get("motion", 0) * 6)
        # clips far outside target are less punchy
        fit = 1.0 - min(1.0, abs(dur - 30) / 60)
        score += 0.18 * fit
        return max(0.0, min(1.0, score))

    def score_completeness(self, c: Candidate) -> float:
        """Does speech start and end cleanly?"""
        inside = [s for s in self.sentences
                  if s.start >= c.start - 0.3 and s.end <= c.end + 0.3]
        if not inside:
            return 0.2
        first, last = inside[0], inside[-1]
        score = 0.4
        # starts at a sentence beginning (clip start close to sentence start)
        if abs(c.start - first.start) < 0.35:
            score += 0.25
        # ends with sentence-final punctuation
        if first.text and last.text and last.text.rstrip("\"'”’)]").endswith(
                (".", "!", "?")):
            score += 0.25
        # no low-confidence speech inside
        low_conf = [s for s in inside if s.confidence < 0.55]
        if low_conf:
            score -= 0.2
        # question answered inside the clip?
        text = " ".join(s.text for s in inside)
        if text.count("?") > 0:
            q_pos = text.find("?")
            score += 0.1 if len(text) - q_pos > 40 else -0.1
        return max(0.0, min(1.0, score))

    def score_visual(self, c: Candidate) -> float:
        v = self.visual_lookup(c.start + c.duration / 2) \
            if self.visual_lookup else None
        if not v:
            return 0.5                                # neutral when unknown
        score = 0.5
        bright = v.get("brightness", 0.5)
        if 0.25 <= bright <= 0.85:
            score += 0.25
        elif bright < 0.15 or bright > 0.92:
            score -= 0.2
        score += 0.15 * min(1.0, v.get("motion", 0) * 6)
        if v.get("face_found"):
            score += 0.1
        return max(0.0, min(1.0, score))

    # -- assembly ---------------------------------------------------------------
    def score(self, c: Candidate) -> Candidate:
        c.s_hook = self.score_hook(c)
        c.s_info = self.score_info(c)
        c.s_clarity = self.score_clarity(c)
        c.s_engagement = self.score_engagement(c)
        c.s_completeness = self.score_completeness(c)
        c.s_visual = self.score_visual(c)
        s = self.settings
        w = (getattr(s, "w_hook", 20), getattr(s, "w_info", 20),
             getattr(s, "w_clarity", 20), getattr(s, "w_engagement", 15),
             getattr(s, "w_completeness", 15), getattr(s, "w_visual", 10))
        total = (c.s_hook * w[0] + c.s_info * w[1] + c.s_clarity * w[2]
                 + c.s_engagement * w[3] + c.s_completeness * w[4]
                 + c.s_visual * w[5])
        # selection-mode boost: the chosen mode's moment types rank first
        mode = getattr(s, "clip_mode", "best") if s else "best"
        from .discovery import MODE_TYPES, prompt_keywords
        boost = 0.0
        for t in MODE_TYPES.get(mode, []):
            if t in c.idea_types:
                boost = max(boost, 8.0)
        prompt = getattr(s, "custom_prompt", "") if s else ""
        if mode == "custom" and prompt:
            kws = prompt_keywords(prompt)
            text_l = c.text.lower()
            hits = sum(1 for k in kws if k in text_l)
            boost = max(boost, min(14.0, 4.5 * hits))
        c.score = round(min(100.0, total + boost), 1)
        return c

    # -- hard rejection -------------------------------------------------------
    def hard_reject(self, c: Candidate) -> str:
        """Return reject reason or '' if the candidate may pass."""
        s = self.settings
        if s is not None and hasattr(s, "effective_durations"):
            _, dmin, dmax = s.effective_durations()
        else:
            dmin = getattr(s, "min_duration", 20) if s else 20
            dmax = getattr(s, "max_duration", 45) if s else 45
        # 1. duration bounds (hard)
        if c.duration < dmin - 2.0:
            return f"too short ({c.duration:.0f}s < {dmin}s)"
        if c.duration > dmax + 5.0:
            return f"too long ({c.duration:.0f}s > {dmax}s)"
        # 2. excessive silence
        if c.speech_ratio < 0.55:
            return f"mostly silence (speech ratio {c.speech_ratio:.2f})"
        # 3. very few words
        if c.words < 35:
            return f"too little speech ({c.words} words)"
        # 4. incomplete sentences at boundaries
        head = c.text.strip()[:40].lower()
        if DANGLING_START.match(c.text.strip()):
            return "starts mid-sentence"
        if BAD_OPENERS.match(c.text.strip()):
            return "starts mid-conversation ('as I was saying', 'anyway'…)"
        if not c.text.rstrip("\"'”’)]").endswith((".", "!", "?")):
            return "ends mid-sentence"
        # 4b. flat monotone delivery without any signal is boring
        if getattr(c, "monotony", 0.0) > 0.85:
            return "flat monotone segment (boring)"
        # 5. no meaningful takeaway
        has_content = (c.s_info > 0.25 or c.s_hook > 0.3
                       or any(t in c.idea_types
                              for t in ("tip/advice", "fact/research",
                                        "mistake/lesson", "story",
                                        "question", "opinion")))
        if not has_content:
            return "no clear takeaway"
        # 6. unreliable transcription
        if c.s_completeness < 0.15:
            return "transcription unreliable at boundaries"
        return ""
