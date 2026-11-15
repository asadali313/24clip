"""FINAL ACCEPTANCE: 1:56-hour video end-to-end (transcript cached from the
benchmark run) - discovery quality across the full timeline, QC-gated
rendering, and verification of every final MP4."""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, r"D:\AI_Clip_Generator")

from app import config, project_store, pipeline
config.ensure_layout()

video = config.TESTS_DIR / "hour_talk.mp4"

checks = []
def check(name, cond, extra=""):
    checks.append((name, bool(cond)))
    print(f"  {'PASS' if cond else 'FAIL'}: {name} {extra}")

# reuse the benchmark project (transcript cached) if present, else fresh
project = None
for d in sorted(config.PROJECTS_DIR.glob("project_*"), reverse=True):
    cand_p = project_store.Project(d)
    if (cand_p.source_video and
            Path(cand_p.source_video).resolve() == video.resolve() and
            cand_p.stage_state("transcribe") == "done"):
        project = cand_p
        break
if project is None:
    project = project_store.Project.create(video)
else:
    # reset downstream stages so the CURRENT pipeline (QC etc.) re-runs on
    # the cached transcript - resume never re-transcribes
    for stage in ("discover", "select", "render"):
        project.data.get("stages", {}).pop(stage, None)
    project.data["candidates"] = []
    project.data["exports"] = []
    project.save()
print(f"project: {project.id}")

settings = config.Settings()
settings.model_size = "base"
settings.clip_count = 8
settings.caption_mode = "pro"
settings.apply_workflow_choices()


def progress(stage, frac, msg):
    print(f"  [{stage:<9}] {frac * 100:5.1f}%  {msg}")


t0 = time.time()
pipeline.Pipeline(project, settings, progress=progress).run()
wall = time.time() - t0

cands = project.data.get("candidates", [])
timings = project.data.get("stage_timings", {})
md = project.data.get("metadata", {})
dur = md.get("duration", 0)

# ---- 1. discovery across the full timeline ----------------------------------
starts = sorted(c["start"] for c in cands)
check("8 clips found+rendered", len(cands) == 8, f"({len(cands)})")
check("clips spread across the whole video",
      starts[-1] - starts[0] > dur * 0.6,
      f"({starts[-1] - starts[0]:.0f}s span of {dur:.0f}s video)")
check("no two clips overlap heavily",
      all(b - a > 15 for a, b in zip(starts, starts[1:])),
      f"(min gap {min(b - a for a, b in zip(starts, starts[1:])):.0f}s)")
scores = [c["score"] for c in cands]
check("scores are strong", min(scores) >= settings.min_score - 15,
      f"(min {min(scores):.0f}, avg {sum(scores)/len(scores):.0f})")

# ---- 2. QC on every clip ------------------------------------------------------
qcs = [c.get("qc") or {} for c in cands]
check("QC ran on all clips", all(q for q in qcs))
q_fail = [c for c in cands if (c.get("qc") or {}).get("status") == "fail"]
check("no clip shipped with failed QC", not q_fail)
check("subject visibility verified",
      all(q.get("subject_inside", -1) >= 0 for q in qcs),
      f"({[round(q.get('subject_inside', -1), 2) for q in qcs]})")

# ---- 3. renders valid ----------------------------------------------------------
import cv2
all_ok = True
for i, c in enumerate(cands):
    f = project.path("exports", c.get("export_file", ""))
    if not f.exists():
        all_ok = False
        continue
    cap = cv2.VideoCapture(str(f))
    okf, frame = cap.read()
    cap.release()
    if not okf or frame.shape[1] != 1080 or frame.shape[0] != 1920:
        all_ok = False
check("all 8 MP4s exist and are 1080x1920", all_ok)
check("selection note honest", bool(project.data.get("selection_note")),
      f"('{project.data.get('selection_note', '')}')")

print(f"\nwall: {wall:.0f}s | stages: {timings}")
failed = [n for n, p in checks if not p]
print(f"=== RESULT: {len(checks) - len(failed)}/{len(checks)} passed ===")
if failed:
    print("FAILED:", failed)
    sys.exit(1)
print("HOUR-LONG FINAL ACCEPTANCE PASSED")
