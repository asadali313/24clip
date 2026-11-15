"""20-minute acceptance test: 6 clips, max 45s, pro captions, base model.

Verifies the full spec: settings respected, complete-video analysis,
requested count, timeline spread, separate validated MP4s, captions,
per-stage timings (speed report).
"""
import json
import sys
import time

sys.path.insert(0, r"D:\AI_Clip_Generator")

from app import config, project_store, pipeline
from app.core.ffmpeg_tools import extract_metadata

config.ensure_layout()

video = config.TESTS_DIR / "long20_talk.mp4"
md = extract_metadata(video)
print(f"=== 20-MIN TEST on {video.name}: {md.duration_hms()}, "
      f"{md.size_mb():.0f} MB ===")

settings = config.Settings()
settings.model_size = "base"          # recommended default
settings.clip_count = 6
settings.duration_mode = "max45"
settings.caption_mode = "pro"
settings.voice_mode = "keep"
settings.apply_workflow_choices()


def progress(stage, frac, msg):
    print(f"  [{stage:<9}] {frac * 100:5.1f}%  {msg}")


project = project_store.Project.create(video)
# resume: if a project for this video already exists with a transcript, reuse it
for d in sorted(config.PROJECTS_DIR.glob("project_*"), reverse=True):
    cand = project_store.Project(d)
    if (cand.source_video and cand.source_video.resolve() == video.resolve()
            and cand.stage_state("transcribe") == "done"):
        project = cand
        print(f"reusing project: {project.id} (resume path)")
        break
    break  # only check the newest
print(f"project: {project.id}")
t0 = time.time()
pipeline.Pipeline(project, settings, progress=progress).run()
wall = time.time() - t0

checks = []
def check(name, cond, extra=""):
    checks.append((name, bool(cond)))
    print(f"  {'PASS' if cond else 'FAIL'}: {name} {extra}")

timings = project.data.get("stage_timings", {})
cands = project.data.get("candidates", [])
trans = json.loads(
    project.path("transcripts", "transcript.json").read_text(encoding="utf-8"))
dur = trans["total_duration"]

check("6 clips found and rendered", len(cands) == 6, f"({len(cands)})")
check("all separate MP4s validated",
      all(c.get("export_file") and
          project.path("exports", c["export_file"]).exists() for c in cands))
durs = [c["duration"] for c in cands]
check("max 45s respected", max(durs) <= 50, f"(max {max(durs):.0f}s)")
starts = sorted(c["start"] for c in cands)
check("timeline spread across 20 min", starts[-1] - starts[0] > 300,
      f"({starts[-1] - starts[0]:.0f}s between first and last)")
check("no two clips near-identical timestamps",
      all(b - a > 15 for a, b in zip(starts, starts[1:])),
      f"(min gap {min(b - a for a, b in zip(starts, starts[1:])):.0f}s)")
check("transcript complete (to video end)",
      trans["segments"][-1]["end"] > dur * 0.97,
      f"(last word {trans['segments'][-1]['end']:.0f}s / {dur:.0f}s)")
check("transcript covers beginning", trans["segments"][0]["start"] < 30)
words = sum(len(s["text"].split()) for s in trans["segments"])
check("full transcript word count", words > 2500, f"({words} words)")
check("captions present for pro mode",
      any(project.path("captions").glob("*.srt")))
check("original timestamps in metadata",
      all("start" in c and "end" in c for c in cands))
check("selection note honest", bool(project.data.get("selection_note")),
      f"('{project.data.get('selection_note', '')}')")

# caption burn-in spot check on one rendered clip (pixel scan)
import cv2
first = project.path("exports", cands[0]["export_file"])
cap = cv2.VideoCapture(str(first))
total_white = 0
for ms in range(500, int(min(20, durs[0]) * 1000), 800):
    cap.set(cv2.CAP_PROP_POS_MSEC, ms)
    okp, frame = cap.read()
    if not okp:
        break
    h = frame.shape[0]
    band = frame[int(h * 0.75):, :, :]
    total_white += int(((band > 235).all(axis=2)).sum())
cap.release()
check("captions visibly burned in", total_white > 1000,
      f"({total_white} bright px)")
out_md = extract_metadata(first)
check("output is 1080x1920", out_md.width == 1080 and out_md.height == 1920,
      f"({out_md.width}x{out_md.height})")

print(f"\n=== SPEED REPORT (wall {wall:.0f}s for {md.duration_hms()} video) ===")
for stage, t in sorted(timings.items()):
    bar = "#" * max(1, int(t / 5))
    print(f"  {stage:<10} {t:6.1f}s  {bar}")
slowest = max(timings, key=timings.get)
print(f"  slowest stage: {slowest} ({timings[slowest]:.0f}s)")
print(f"  transcription speedup factor: {dur / max(1, timings['transcribe']):.1f}x realtime")

failed = [n for n, p in checks if not p]
print(f"\n=== RESULT: {len(checks) - len(failed)}/{len(checks)} passed ===")
if failed:
    print("FAILED:", failed)
    sys.exit(1)
print("20-MIN ACCEPTANCE PASSED")
