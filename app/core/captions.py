"""Caption templates: Classic, MrBeast, Modern, Karaoke (+ SRT export).

All templates are generated locally from the transcript with word-level
timestamps. ASS styling is per template; positions keep text inside the
mobile-safe area (margins >= 170px on a 1920-high canvas).

Word times inside CaptionLine are relative to the CLIP start (same space as
line.start/end); karaoke tags convert them to line-relative at render time.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .. import config
from .segmentation import Sentence

MAX_LINE_CHARS = 34
MAX_LINE_WORDS = 6

TEMPLATE_KEYS = ("beast", "gaming", "karaoke", "documentary", "modern",
                 "podcast", "cinematic", "minimal", "classic")
TEMPLATE_LABELS = {'classic': 'Classic', 'beast': 'MrBeast Gaming', 'gaming': 'Gaming', 'karaoke': 'Karaoke', 'documentary': 'Documentary', 'modern': 'Modern Bold', 'podcast': 'Podcast', 'cinematic': 'Cinematic', 'minimal': 'Minimal'}
# backward compatibility with earlier settings files
TEMPLATE_ALIASES = {"simple": "classic", "pro": "beast"}


def normalize_template(mode: str) -> str:
    if mode in TEMPLATE_KEYS:
        return mode
    return TEMPLATE_ALIASES.get(mode, "classic")


@dataclass
class CaptionLine:
    start: float                      # relative to clip start
    end: float
    text: str
    words: list[tuple[float, float, str]] = field(default_factory=list)

    def srt_block(self, idx: int) -> str:
        return (f"{idx}\n{_ts_srt(self.start)} --> {_ts_srt(self.end)}\n"
                f"{self.text}\n")


def _ts_srt(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _ts_ass(t: float) -> str:
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


HIGHLIGHT_RE = re.compile(
    r"\b(never|always|best|worst|secret|mistake|important|free|new|why|how|"
    r"truth|amazing|must|should|stop|start|first|biggest|number|percent|"
    r"\d+([\d.,%]*)?)\b", re.IGNORECASE)

# ASS colours are &HAABBGGRR
WHITE = "&H00FFFFFF"
YELLOW = "&H0000DDFF"      # RGB 255,221,0
GREEN = "&H007FFF00"       # RGB 0,255,127
CYAN = "&H00FFE500"        # RGB 0,229,255
ORANGE = "&H00005AFF"      # RGB 255,90,0
GOLD = "&H0043C6FF"        # RGB 255,198,67 - documentary keyword
BEAST_PALETTE = [YELLOW, GREEN, CYAN, ORANGE]

# safe-area alignments: bottom (default), center, top
POSITIONS = {"bottom": (2, 180), "center": (5, 0), "top": (8, 180)}


def build_lines(sentences: list[Sentence], clip_start: float, clip_end: float,
                word_sync: bool = True, min_words: int = 3,
                max_words: int = 7, max_chars: int = 36) -> list[CaptionLine]:
    """Group words into meaningful PHRASES (not isolated words).

    A line breaks when: it reaches the word/char limit, a sentence ends and
    the line already holds >= min_words, or a speech pause >= 0.8 s falls
    after >= min_words. Tiny remainders are merged into a neighbour so the
    viewer never sees a lone 1-2 word caption.
    """
    # collect words with sentence boundaries
    seq: list[tuple[float, float, str, bool]] = []   # start, end, text, sent_end
    for s in sentences:
        if s.end <= clip_start or s.start >= clip_end:
            continue
        if word_sync and s.words:
            words = [(w.start, w.end, w.text) for w in s.words
                     if w.start >= clip_start - 0.2 and w.end <= clip_end + 0.2]
        else:
            approx = s.text.split()
            span = max(0.1, (min(s.end, clip_end) - max(s.start, clip_start)))
            per = span / max(1, len(approx))
            t0 = max(s.start, clip_start)
            words = [(t0 + i * per, t0 + (i + 1) * per, w)
                     for i, w in enumerate(approx)]
        for k, (a, b, w) in enumerate(words):
            seq.append((a, b, w, k == len(words) - 1))

    # first pass: greedy phrase building
    groups: list[list[tuple[float, float, str]]] = []
    cur: list[tuple[float, float, str]] = []
    prev_end: float | None = None
    for a, b, w, sent_end in seq:
        text = " ".join(x[2] for x in cur + [(a, b, w)])
        gap = (a - prev_end) if prev_end is not None else 0.0
        must_break = (
            len(cur) >= max_words
            or len(text) >= max_chars
            or (sent_end and len(cur) >= min_words)
            or (gap >= 0.8 and len(cur) >= min_words)
        )
        if cur and must_break:
            groups.append(cur)
            cur = []
        cur.append((a, b, w))
        prev_end = b
    if cur:
        groups.append(cur)

    # second pass: merge tiny lines (fewer than min_words) into a neighbour
    merged: list[list[tuple[float, float, str]]] = []
    for g in groups:
        if len(g) < min_words and merged:
            prev = merged[-1]
            if len(prev) + len(g) <= max_words + 2:
                prev.extend(g)
                continue
        merged.append(g)
    # merge a trailing tiny line backward
    if len(merged) >= 2 and len(merged[-1]) < min_words:
        if len(merged[-2]) + len(merged[-1]) <= max_words + 2:
            merged[-2].extend(merged[-1])
            merged.pop()

    lines: list[CaptionLine] = []
    for g in merged:
        text = _clean(" ".join(x[2] for x in g))
        lines.append(CaptionLine(g[0][0], g[-1][1], text, list(g)))
    for ln in lines:                       # clamp into the clip, relative time
        ln.start = max(0.0, ln.start - clip_start)
        ln.end = max(ln.start + 0.3, min(ln.end - clip_start,
                                         clip_end - clip_start))
        ln.words = [(max(0.0, a - clip_start), max(0.0, b - clip_start), w)
                    for a, b, w in ln.words]
    return [ln for ln in lines if ln.text.strip()]


def _clean(t: str) -> str:
    return re.sub(r"\s+", " ", t).strip()


def write_srt(lines: list[CaptionLine], out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(ln.srt_block(i + 1) for i, ln in enumerate(lines))
    out.write_text(body + "\n", encoding="utf-8")
    return out


# ---------------------------------------------------------------------------
# ASS generation per template
# ---------------------------------------------------------------------------
def _header(style: str, w: int = 1080, h: int = 1920) -> str:
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
{style}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _dialogue(ln: CaptionLine, text: str, style: str = "Cap") -> str:
    return (f"Dialogue: 0,{_ts_ass(ln.start)},{_ts_ass(ln.end)},"
            f"{style},,0,0,0,,{text}\n")


def _geom(s, out_w: int, out_h: int) -> dict:
    """Canvas geometry for the selected output format.

    Fonts scale with canvas height (sqrt keeps landscape readable rather
    than tiny); bottom margins keep text inside the mobile-safe area.
    """
    base = getattr(s, "caption_font_size", 46) if s is not None else 46
    font = max(36, round(base * (out_h / 1920) ** 0.5))
    return {"w": out_w, "h": out_h, "font": font,
            "mv": max(48, round(180 * out_h / 1920))}


def _align(s, geo: dict) -> tuple[int, int]:
    """User position override -> (Alignment, MarginV)."""
    pos = getattr(s, "caption_position", "bottom") if s is not None else "bottom"
    if pos == "center":
        return 5, 0
    if pos == "top":
        return 8, geo["mv"]
    return 2, geo["mv"]


def _render_classic(lines: list[CaptionLine], s, geo) -> str:
    font = geo["font"]
    align, mv = _align(s, geo)
    # fields: ...,BorderStyle,Outline,Shadow,Alignment,ML,MR,MV,Encoding
    head = _header(
        f"Style: Cap,Arial,{font},{WHITE},&H000000FF,&H00000000,&H7F000000,"
        f"-1,0,0,0,100,100,0,0,1,3,1,{align},60,60,{mv},1",
        geo["w"], geo["h"])
    body = "".join(_dialogue(ln, ln.text) for ln in lines)
    return head + body


def _render_beast(lines: list[CaptionLine], s, geo) -> str:
    font = max(50, geo["font"] + 6)
    align, mv = _align(s, geo)
    head = _header(
        f"Style: Cap,Arial Black,{font},{WHITE},&H000000FF,&H00000000,"
        f"&H90000000,-1,0,0,0,100,100,1,0,1,5,2,{align},60,60,{mv},1",
        geo["w"], geo["h"])
    out = []
    for ln in lines:
        words = ln.text.split()
        # map words back to timings (same order as ln.words, 1:1 if counts match)
        timed = ln.words if len(ln.words) == len(words) else []
        parts = []
        for k, wtext in enumerate(words):
            piece = wtext
            if timed:
                a, b, wtxt = timed[k]
                dur_cs = max(1, int((b - a) * 100))
                if HIGHLIGHT_RE.search(wtext):
                    color = BEAST_PALETTE[k % len(BEAST_PALETTE)]
                    piece = (f"{{\\rCap\\c{color}\\fscx78\\fscy78"
                             f"\\t(0,{dur_cs},\\fscx104\\fscy104)}}{wtext}"
                             f"{{\\rCap}}")
            parts.append(piece)
        out.append(_dialogue(ln, " ".join(parts)))
    return head + "".join(out)


def _render_modern(lines: list[CaptionLine], s, geo) -> str:
    font = max(44, geo["font"])
    align, mv = _align(s, geo)
    # BorderStyle=3: OutlineColour becomes an opaque rounded box behind text
    head = _header(
        f"Style: Cap,Segoe UI,{font},{WHITE},{WHITE},&H8C14181D,&H00000000,"
        f"-1,0,0,0,100,100,2,0,3,14,0,{align},60,60,{mv},1",
        geo["w"], geo["h"])
    out = []
    for ln in lines:
        # subtle pop+fade entrance
        text = f"{{\\fad(140,110)\\fscx92\\fscy92\\t(0,140,\\fscx100\\fscy100)}}{ln.text}"
        out.append(_dialogue(ln, text))
    return head + "".join(out)


def _render_karaoke(lines: list[CaptionLine], s, geo) -> str:
    font = max(46, geo["font"])
    align, mv = _align(s, geo)
    # Secondary = not yet sung (white), Primary = sung (yellow)
    head = _header(
        f"Style: Cap,Arial,{font},{YELLOW},{WHITE},&H00000000,&H7F000000,"
        f"-1,0,0,0,100,100,0,0,1,3,1,{align},60,60,{mv},1",
        geo["w"], geo["h"])
    out = []
    for ln in lines:
        parts = []
        prev_t = ln.start
        words = ln.words
        for i, (a, b, wtxt) in enumerate(words):
            lead = max(0, int((a - prev_t) * 100))
            dur = max(1, int((b - a) * 100))
            if lead:
                parts.append(f"{{\\k{lead}}}")
            parts.append(f"{{\\k{dur}}}{wtxt}")
            if i < len(words) - 1:
                parts.append(" ")           # keep word spacing
            prev_t = b
        out.append(_dialogue(ln, "".join(parts)))
    return head + "".join(out)


def _render_documentary(lines: list[CaptionLine], s, geo) -> str:
    """Clean professional white captions, keywords in soft gold."""
    font = geo["font"]
    align, mv = _align(s, geo)
    head = _header(
        f"Style: Cap,Georgia,{font},{WHITE},{WHITE},&H00000000,&H96000000,"
        f"0,0,0,0,100,100,0,0,1,2,1,{align},70,70,{mv},1",
        geo["w"], geo["h"])
    out = []
    for ln in lines:
        words = ln.text.split()
        timed = ln.words if len(ln.words) == len(words) else []
        parts = []
        for k, wtext in enumerate(words):
            if timed and HIGHLIGHT_RE.search(wtext):
                parts.append(f"{{\\rCap\\c{GOLD}}}{wtext}{{\\rCap}}")
            else:
                parts.append(wtext)
        text = f"{{\\fad(120,100)}}" + " ".join(parts)
        out.append(_dialogue(ln, text))
    return head + "".join(out)


def _render_podcast(lines: list[CaptionLine], s, geo) -> str:
    """Large readable captions, spoken emphasis in warm yellow."""
    font = max(56, geo["font"] + 12)
    align, mv = _align(s, geo)
    head = _header(
        f"Style: Cap,Verdana,{font},{WHITE},{YELLOW},&H00000000,&HAA000000,"
        f"-1,0,0,0,100,100,0,0,1,4,2,{align},50,50,{mv},1",
        geo["w"], geo["h"])
    out = []
    for ln in lines:
        words = ln.text.split()
        timed = ln.words if len(ln.words) == len(words) else []
        parts = []
        prev_t = ln.start
        for k, wtext in enumerate(words):
            if timed:
                a, b, _ = timed[k]
                dur = max(1, int((b - a) * 100))
                if HIGHLIGHT_RE.search(wtext):
                    parts.append(f"{{\\rCap\\c{YELLOW}\\b1}}{wtext}{{\\rCap}}")
                else:
                    parts.append(wtext)
                prev_t = b
            else:
                parts.append(wtext)
        out.append(_dialogue(ln, " ".join(parts)))
    return head + "".join(out)


def _render_cinematic(lines: list[CaptionLine], s, geo) -> str:
    """Elegant serif, letter-spaced, subtle fade, lower-third placement."""
    font = max(38, geo["font"] - 6)
    align, mv = _align(s, geo)
    mv = max(mv, 240)                       # sit higher: lower-third elegance
    head = _header(
        f"Style: Cap,Georgia,{font},{WHITE},{WHITE},&H50000000,&H3C000000,"
        f"0,1,0,0,100,100,3,0,1,1,2,{align},90,90,{mv},1",
        geo["w"], geo["h"])
    out = []
    for ln in lines:
        text = f"{{\\fad(220,260)\\fsp2}}{ln.text}"
        out.append(_dialogue(ln, text))
    return head + "".join(out)


def _render_gaming(lines: list[CaptionLine], s, geo) -> str:
    """Gaming: large expressive ALL-CAPS text, thick outline, energetic
    two-tone highlight with a pop-in scale animation."""
    font = max(52, geo["font"] + 10)
    align, mv = _align(s, geo)
    head = _header(
        f"Style: Cap,Arial Black,{font},{WHITE},{WHITE},&H00000000,"
        f"&H90000000,-1,0,0,0,100,100,0,0,1,5,3,{align},50,50,{mv},1",
        geo["w"], geo["h"])
    out = []
    for ln in lines:
        words = ln.text.split()
        timed = ln.words if len(ln.words) == len(words) else []
        parts = []
        for k, wtext in enumerate(words):
            piece = wtext.upper()
            if timed and HIGHLIGHT_RE.search(wtext):
                color = BEAST_PALETTE[k % len(BEAST_PALETTE)]
                piece = (r"{\rCap\c" + color + r"\fscx82\fscy82"
                         r"\t(0,90,\fscx108\fscy108)}" + piece
                         + r"{\rCap}")
            parts.append(piece)
        out.append(_dialogue(ln, " ".join(parts)))
    return head + "".join(out)


def _render_minimal(lines: list[CaptionLine], s, geo) -> str:
    """Minimal: clean small caps, thin, generous margin, gentle fade."""
    font = max(38, geo["font"] - 6)
    align, mv = _align(s, geo)
    mv = max(mv, 200)
    head = _header(
        f"Style: Cap,Segoe UI Light,{font},{WHITE},{WHITE},&H64000000,"
        f"&H00000000,0,0,0,0,100,100,1,0,1,1,0,{align},80,80,{mv},1",
        geo["w"], geo["h"])
    out = []
    for ln in lines:
        out.append(_dialogue(ln, f"{{\fad(180,180)}}{ln.text}"))
    return head + "".join(out)


_RENDERERS = {
    "classic": _render_classic,
    "beast": _render_beast,
    "gaming": _render_gaming,
    "karaoke": _render_karaoke,
    "documentary": _render_documentary,
    "modern": _render_modern,
    "podcast": _render_podcast,
    "cinematic": _render_cinematic,
    "minimal": _render_minimal,
}


def write_template_ass(lines: list[CaptionLine], out: Path, settings,
                       template: str | None = None,
                       out_w: int = 1080, out_h: int = 1920) -> Path:
    """Render caption lines in the selected style for the output format."""
    tpl = normalize_template(template or getattr(settings, "caption_mode",
                                                 "classic"))
    geo = _geom(settings, out_w, out_h)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(_RENDERERS[tpl](lines, settings, geo))
    return out


# ---------------------------------------------------------------------------
# Template previews (real renders, shown on the settings screen)
# ---------------------------------------------------------------------------
SAMPLE_SENTENCE_TEXT = ("This one mistake changes everything in seconds")


def render_template_previews(out_dir: Path, settings=None,
                             bg_image: Path | None = None) -> dict[str, Path]:
    """Burn one sample frame per template with ffmpeg -> PNG thumbnails.

    bg_image: a real frame from the user's own video when available, so
    previews show exactly how each template sits on their content. The
    SAME write_template_ass engine renders previews and exports."""
    settings = settings or config.Settings()
    out_dir.mkdir(parents=True, exist_ok=True)
    # a fake "sentence" spanning 0..2s so build_lines produces one line
    from .segmentation import Sentence
    from .transcriber import Word
    words_txt = SAMPLE_SENTENCE_TEXT.split()
    n = len(words_txt)
    per = 2.0 / n
    words = [Word(i * per, (i + 1) * per, w + ("." if i == n - 1 else ""), 0.9)
             for i, w in enumerate(words_txt)]
    sent = Sentence(0.0, 2.0, SAMPLE_SENTENCE_TEXT + ".", words, 1.0)
    lines = build_lines([sent], 0.0, 2.0)
    paths: dict[str, Path] = {}
    for tpl in TEMPLATE_KEYS:
        png = out_dir / f"tpl_{tpl}.png"
        ass = out_dir / f"tpl_{tpl}.ass"
        write_template_ass(lines, ass, settings, tpl)
        fspath = ass.resolve().as_posix().replace(":", "\\:")
        args = [
            config.ffmpeg(), "-y", "-v", "error",
            "-f", "lavfi", "-i", "color=c=0x232830:s=288x512:d=2.5:r=10",
            "-vf", (f"ass='{fspath}',"
                    "select=gte(t\\,1.3)"),
            "-frames:v", "1", str(png),
        ]
        proc = subprocess.run(args, capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              creationflags=subprocess.CREATE_NO_WINDOW)
        if proc.returncode == 0 and png.exists():
            paths[tpl] = png
    # cleanup ass temp files
    for tpl in TEMPLATE_KEYS:
        (out_dir / f"tpl_{tpl}.ass").unlink(missing_ok=True)
    return paths


def write_ass_highlighted(lines: list[CaptionLine], out: Path,
                          settings=None) -> Path:
    """Backward-compatible wrapper -> MrBeast-style highlighting."""
    return write_template_ass(lines, out, settings, "beast")
