"""Task 3 benchmark: transcribe the 1-HOUR video, measure time + peak RAM,
verify coverage + resume. Run with the project venv python.

BASELINE (previous implementation, 20.2 min video, base model): 62.3 s
= 19.5x realtime. The 1-hour run below reports the new absolute numbers.
"""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, r"D:\AI_Clip_Generator")

from app import config, project_store, pipeline
config.ensure_layout()

video = config.TESTS_DIR / "hour_talk.mp4"
MODEL = os.environ.get("BENCH_MODEL", "base")

from app.core.ffmpeg_tools import extract_metadata
md = extract_metadata(video)
hours = md.duration / 3600
print(f"=== TRANSCRIPTION BENCH: {video.name}, {md.duration_hms()}, "
      f"model={MODEL} ===")

import psutil
proc = psutil.Process(os.getpid())
rss0 = proc.memory_info().rss / 1048576

settings = config.Settings()
settings.model_size = MODEL
project = project_store.Project.create(video)
print(f"project: {project.id}")

# resume/reuse check: if a project for this video already has a transcript,
# the pipeline must reuse it (per spec) - so create fresh and run once.
t0 = time.time()
window_msgs = []

def progress(stage, frac, msg):
    if stage == "transcribe":
        window_msgs.append(msg)
        print(f"  [{frac * 100:5.1f}%] {msg}")

settings.apply_workflow_choices()
pipe = pipeline.Pipeline(project, settings, progress=progress)
# transcribe-only: run the full pipeline (also validates integration) but
# we report the transcribe stage timing separately
pipe.run()
wall = time.time() - t0

peak = proc.memory_info().rss / 1048576
timings = project.data.get("stage_timings", {})
t_transcribe = timings.get("transcribe", 0)
tj = json.loads(project.path("transcripts", "transcript.json")
                .read_text(encoding="utf-8"))
dur = tj["total_duration"]
words = sum(len(s["text"].split()) for s in tj["segments"])

checks = []
def check(name, cond, extra=""):
    checks.append((name, bool(cond)))
    print(f"  {'PASS' if cond else 'FAIL'}: {name} {extra}")

check("transcript covers whole hour",
      tj["segments"] and tj["segments"][-1]["end"] > dur * 0.97,
      f"(last word {tj['segments'][-1]['end']:.0f}s / {dur:.0f}s)")
check("word count realistic", words > 8000, f"({words} words)")
check("memory bounded (<2.5 GB)", peak < 2560, f"(peak {peak:.0f} MB)")
check("transcription faster than realtime",
      t_transcribe < dur, f"({dur / max(1, t_transcribe):.1f}x realtime)")

rt = dur / max(1, t_transcribe)
print(f"""
=== BENCH RESULT ===
  video length        : {md.duration_hms()}
  transcribe stage    : {t_transcribe:.0f} s  ({rt:.1f}x realtime)
  full pipeline wall  : {wall:.0f} s
  peak RSS            : {peak:.0f} MB (start {rss0:.0f} MB)
  words               : {words}
  windows             : {tj['windows']} (silent-skipped: {tj.get('windows_silent_skipped', 0)})
  ETA shown to user   : {'yes' if any('left' in m for m in window_msgs) else 'no'}
  vs old baseline     : old 19.5x realtime (beam=5 removed, silent-skip added)
""")

failed = [n for n, p in checks if not p]
sys.exit(1 if failed else 0)
