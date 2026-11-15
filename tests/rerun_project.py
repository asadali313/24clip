"""Resume the user's failed project with the sentence-window fix.

Resets discover/select/render stages, keeps the cached transcript, runs the
pipeline with the user's own settings (6 clips, auto duration).
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, r"D:\AI_Clip_Generator")

from app import config, project_store, pipeline

config.ensure_layout()

proj = project_store.Project(Path(r"D:\AI_Clip_Generator\projects\project_001"))

# reset failed downstream stages; keep prepare+transcribe cache
for stage in ("discover", "select", "render"):
    proj.data.get("stages", {}).pop(stage, None)
proj.data.get("stages", {}).pop("failed", None)
proj.data["candidates"] = []
proj.data["selection_note"] = ""
proj.data["exports"] = []
proj.save()
print("stages reset; transcribe cached:",
      proj.stage_state("transcribe") == "done")

settings = config.Settings()
settings.model_size = "base"
settings.clip_count = proj.data.get("settings_snapshot", {}).get(
    "clip_count", 6)
settings.duration_mode = proj.data.get("settings_snapshot", {}).get(
    "duration_mode", "auto")
settings.caption_mode = "none"      # user's screenshot run: no captions yet
settings.voice_mode = "keep"
settings.apply_workflow_choices()
print(f"settings: {settings.clip_count} clips, "
      f"duration={settings.duration_mode}, "
      f"durations={settings.effective_durations()}")


def progress(stage, frac, msg):
    print(f"  [{stage:<9}] {frac * 100:5.1f}%  {msg}")


t0 = time.time()
pipeline.Pipeline(proj, settings, progress=progress).run()
wall = time.time() - t0

# ---- verification -----------------------------------------------------
checks = []
def check(name, cond, extra=""):
    checks.append((name, bool(cond)))
    print(f"  {'PASS' if cond else 'FAIL'}: {name} {extra}")

cands = proj.data.get("candidates", [])
check("6 clips selected", len(cands) == 6, f"({len(cands)})")
dmin, dtgt, dmax = settings.effective_durations()
durs = [c["duration"] for c in cands]
check("durations within max", all(d <= dmax + 5 for d in durs),
      f"(max {max(durs, default=0):.0f}s, limit {dmax})")
starts = sorted(c["start"] for c in cands)
check("spread across 32 min", starts[-1] - starts[0] > 600,
      f"({starts[-1] - starts[0]:.0f}s span)")
check("all 6 MP4s rendered + validated",
      all(c.get("export_file") and
          proj.path("exports", c["export_file"]).exists() for c in cands),
      f"({[c.get('export_file') for c in cands]})")
check("selection note", bool(proj.data.get("selection_note")),
      f"('{proj.data.get('selection_note', '')}')")

print(f"\nwall time: {wall:.0f}s | stage timings: "
      f"{proj.data.get('stage_timings')}")
failed = [n for n, p in checks if not p]
print(f"RESULT: {len(checks) - len(failed)}/{len(checks)} passed")
sys.exit(1 if failed else 0)
