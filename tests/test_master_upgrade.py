"""Master-upgrade verification: selection modes, custom prompt, 720p,
thumbnails, output dir, loudnorm, Urdu caption rendering."""
import sys
from pathlib import Path

sys.path.insert(0, r"D:\AI_Clip_Generator")

from app import config
config.ensure_layout()

checks = []
def check(name, cond, extra=""):
    checks.append((name, bool(cond)))
    print(f"  {'PASS' if cond else 'FAIL'}: {name} {extra}")

from app.core import discovery, scoring, segmentation, renderer
from app.core.transcriber import Segment

sents = [
    Segment(0, 6, "The biggest mistake players make is ignoring the economy.", [], 0.9),
    Segment(7, 14, "Hahaha that was the funniest fail I have ever seen in my life!", [], 0.9),
    Segment(15, 22, "Research from Stanford shows multitasking hurts focus by 40 percent.", [], 0.9),
    Segment(23, 30, "When I was a beginner I lost every match for three months straight.", [], 0.9),
    Segment(31, 38, "So the lesson is simple: upgrade your gear before you push ranks.", [], 0.9),
]
from app.core.segmentation import Sentence
sents = [Sentence(s.start, s.end, s.text, [], 0.9) for s in sents]
thoughts = segmentation.build_thoughts(sents)


def top_for_mode(mode, prompt=""):
    s = config.Settings()
    s.clip_mode = mode
    s.custom_prompt = prompt
    eng = discovery.DiscoveryEngine(sents)
    cands = eng.generate(15, 45, 25)
    scorer = scoring.Scorer(s, sents, thoughts, None)
    for c in cands:
        scorer.score(c)
    return max(cands, key=lambda c: c.score)


print("=== SELECTION MODES ===")
check("funny mode picks the fail moment",
      "funniest" in top_for_mode("funny").text.lower())
check("educational mode picks research/tip",
      "stanford" in top_for_mode("educational").text.lower()
      or "lesson" in top_for_mode("educational").text.lower())
check("motivational mode picks the lesson/payoff",
      "lesson" in top_for_mode("motivational").text.lower())
check("custom prompt keyword matching",
      "economy" in top_for_mode("custom", "find clips about the economy "
                                "system").text.lower())
check("best mode unaffected by boosts",
      top_for_mode("best").score <= 100)

print("=== 720p EXPORT ===")
s720 = config.Settings()
s720.export_resolution = "720p"
s720.caption_mode = "classic"
s720.apply_workflow_choices()
from app.core import renderer
from pathlib import Path
video = config.TESTS_DIR / "sample_talk.mp4"
out720 = config.TEMP_DIR / "render" / "v720.mp4"
renderer.render_clip(video, 13.8, 33.8, out720, s720)
from app.core.ffmpeg_tools import extract_metadata
md = extract_metadata(out720)
check("720p vertical export", (md.width, md.height) == (720, 1280),
      f"({md.width}x{md.height})")

print("=== THUMBNAIL + OUTPUT DIR ===")
outdir = config.TEMP_DIR / "render" / "custom_out"
s720.output_dir = str(outdir)
s720.music_enabled = False
from app.core import export as export_mod
proj = None
from app import project_store
for d in sorted(config.PROJECTS_DIR.glob("project_*"), reverse=True):
    pp = project_store.Project(d)
    if (pp.source_video and Path(pp.source_video).name == "sample_talk.mp4"
            and pp.data.get("candidates")):
        proj = pp
        break
check("project with candidates found", proj is not None)
if proj:
    cand = dict(proj.data["candidates"][0])
    ok, failed = export_mod.export_many(proj, [cand], s720)
    check("export with 720p + custom dir + thumbnail", len(ok) == 1)
    thumb = proj.path("exports",
                      Path(ok[0]["file"]).with_suffix(".jpg").name)
    check("per-clip thumbnail exported", thumb.exists(),
          f"({thumb.stat().st_size // 1024} KB)")
    copied = outdir / ok[0]["file"]
    check("custom output directory copy", copied.exists())

print("=== URDU CAPTION RENDERING ===")
from app.core.captions import build_lines, write_template_ass
from app.core.segmentation import Sentence
from app.core.transcriber import Word
import cv2
urdu = "یہ سب سے بڑی غلطی ہے جو لوگ کرتے ہیں"
words = [Word(0.1 + i * 0.4, 0.45 + i * 0.4, w, 0.9)
         for i, w in enumerate(urdu.split())]
sent = Sentence(0.1, 0.5 + len(words) * 0.4, urdu, words, 1.0)
lines = build_lines([sent], 0, 12)
s_u = config.Settings()
s_u.caption_mode = "classic"
ass = write_template_ass(lines, config.TEMP_DIR / "render" / "ur.ass",
                         s_u, "classic")
png = config.TEMP_DIR / "render" / "ur.png"
import subprocess
subprocess.run([config.ffmpeg(), "-y", "-v", "error", "-f", "lavfi",
                "-i", "color=c=0x232830:s=288x512:d=2:r=10",
                "-vf", f"ass='{ass.as_posix().replace(':', chr(92) + ':')}',"
                       "select=gte(t\\,1.0)",
                "-frames:v", "1", str(png)],
               capture_output=True, creationflags=0x08000000)
okf = png.exists() and png.stat().st_size > 2000
frame = cv2.imread(str(png)) if okf else None
white = int(((frame > 200).all(axis=2)).sum()) if frame is not None else 0
check("Urdu captions render (RTL script text)", white > 100,
      f"({white} px; system font shaping via libass)")

failed = [n for n, p in checks if not p]
print(f"\n=== RESULT: {len(checks) - len(failed)}/{len(checks)} passed ===")
if failed:
    print("FAILED:", failed)
    sys.exit(1)
print("MASTER UPGRADE VERIFICATION PASSED")
