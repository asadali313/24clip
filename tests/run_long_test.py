"""Long-video E2E: 13.5-min video, multi-window transcription, resume test.

Phase A: start pipeline, wait until transcription window 0 is done, kill
         the process mid-analysis.
Phase B: restart; assert window 0 was loaded from cache (mtime unchanged),
         pipeline completes; verify multi-window artifacts + dedup diversity.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, r"D:\AI_Clip_Generator")

ROOT = Path(r"D:\AI_Clip_Generator")
PY = ROOT / ".venv" / "Scripts" / "python.exe"
VIDEO = ROOT / "tests" / "long_talk.mp4"

DRIVER = r'''
import json, sys, time
from pathlib import Path
sys.path.insert(0, r"D:\AI_Clip_Generator")
from app import config, project_store, pipeline

config.ensure_layout()
video = config.TESTS_DIR / "long_talk.mp4"
settings = config.Settings()
settings.model_size = "tiny"
project = project_store.Project.create(video)
print("PROJECT:", project.id, flush=True)

def progress(stage, frac, msg):
    print(f"[{stage}] {frac*100:.0f}% {msg}", flush=True)

t0 = time.time()
pipeline.Pipeline(project, settings, progress=progress).run()
print(f"PIPELINE_DONE in {time.time()-t0:.1f}s", flush=True)
'''


def wait_for(predicate, timeout, poll=2.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if predicate():
            return True
        time.sleep(poll)
    return False


def main() -> int:
    checks = []

    def check(name, cond, extra=""):
        checks.append((name, bool(cond)))
        print(f"  {'PASS' if cond else 'FAIL'}: {name} {extra}")

    # ---------------- Phase A: interrupt ---------------------------------
    print("=== PHASE A: start + interrupt during transcription ===")
    proc = subprocess.Popen(
        [str(PY), "-I", "-c", DRIVER],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        encoding="utf-8", errors="replace",
        creationflags=subprocess.CREATE_NO_WINDOW)
    project_id = None
    t_wait0 = time.time()
    ok = False
    for line in proc.stdout:
        print("   ", line.rstrip())
        if line.startswith("PROJECT:"):
            project_id = line.split()[1]
        if "[transcribe] 100%" in line:
            ok = True
            break
    check("window 0 finished before kill", ok,
          f"(waited {time.time()-t_wait0:.0f}s)")
    time.sleep(2)  # let window_001.json start/none exist yet
    proc.kill()
    proj_dir = ROOT / "projects" / project_id
    win0 = proj_dir / "transcripts" / "window_000.json"
    mtime_before = win0.stat().st_mtime if win0.exists() else None
    check("window_000.json cached on disk", mtime_before is not None)
    done_before = json.loads(
        (proj_dir / "transcripts" / "windows_done.json").read_text())
    check("windows_done.json marks window 0", "0" in done_before)
    print(f"    killed mid-analysis; stage status="
          f"{json.loads((proj_dir / 'project.json').read_text())['status']}")

    # ---------------- Phase B: resume -------------------------------------
    print("=== PHASE B: resume and complete ===")
    t0 = time.time()
    proc2 = subprocess.run(
        [str(PY), "-I", "-c", DRIVER.replace(
            'project_store.Project.create(video)',
            f'project_store.Project(Path(r"{proj_dir}"))')],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=1800, creationflags=subprocess.CREATE_NO_WINDOW)
    print(proc2.stdout[-1200:])
    if proc2.stderr:
        print("STDERR:", proc2.stderr[-800:])
    mtime_after = win0.stat().st_mtime if win0.exists() else None
    check("window_000.json reused (mtime unchanged)",
          mtime_before == mtime_after,
          f"({mtime_before} -> {mtime_after})")

    # ---------------- verify final state -----------------------------------
    pj = json.loads((proj_dir / "project.json").read_text())
    cands = pj.get("candidates", [])
    tj = json.loads((proj_dir / "transcripts" / "transcript.json").read_text())
    dur = tj["total_duration"]
    n_windows = tj["windows"]
    check("pipeline completed on resume", "PIPELINE_DONE" in proc2.stdout)
    check("multiple transcription windows", n_windows >= 2,
          f"({n_windows} windows, {dur:.0f}s video)")
    words = sum(len(s["text"].split()) for s in tj["segments"])
    check("full transcript present", words > 800, f"({words} words)")
    check("timestamps reach video end",
          tj["segments"][-1]["end"] > dur * 0.9,
          f"(last word ends {tj['segments'][-1]['end']:.0f}s of {dur:.0f}s)")
    check("clips found", len(cands) >= 2, f"({len(cands)} clips)")
    check("duration limits respected",
          all(c["duration"] <= 50 for c in cands))
    check("duplicate-free",
          all("duplicate" not in (c.get("reject_reason") or "")
              for c in cands))

    # diversity: clips should come from different parts
    if len(cands) >= 2:
        starts = sorted(c["start"] for c in cands)
        spread = starts[-1] - starts[0]
        check("timeline spread", spread > 60, f"({spread:.0f}s between first/last)")
    rep = json.loads((proj_dir / "analysis" / "processing_report.json")
                     .read_text())
    print("    timeline:", [(b["label"], b["clips"])
                            for b in rep["timeline_distribution"] if b["clips"]])

    failed = [n for n, okp in checks if not okp]
    print(f"\n=== RESULT: {len(checks)-len(failed)}/{len(checks)} passed ===")
    if failed:
        print("FAILED:", failed)
        return 1
    print("LONG E2E PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
