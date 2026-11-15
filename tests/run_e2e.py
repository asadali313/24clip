"""Headless E2E for the fast pipeline: settings -> auto-render -> results.

Verifies: clip count honored, duration respected, captions/voice options
applied, separate MP4s rendered, stage timings recorded, honest shortfall.
"""
import json
import sys
import time

sys.path.insert(0, r"D:\AI_Clip_Generator")

from app import config, project_store, pipeline
from app.core.ffmpeg_tools import FFmpegError, extract_metadata

config.ensure_layout()


def progress(stage, frac, msg):
    print(f"  [{stage:<9}] {frac * 100:5.1f}%  {msg}")


def run_case(name, video, tweaks: dict, expect: dict) -> bool:
    print(f"\n=== CASE: {name} ===")
    settings = config.Settings()
    settings.model_size = "tiny"
    for k, v in tweaks.items():
        setattr(settings, k, v)
    settings.apply_workflow_choices()

    project = project_store.Project.create(video)
    t0 = time.time()
    pipeline.Pipeline(project, settings, progress=progress).run()
    total = time.time() - t0

    checks = []
    def check(nm, cond, extra=""):
        checks.append((nm, bool(cond)))
        print(f"  {'PASS' if cond else 'FAIL'}: {nm} {extra}")

    cands = project.data.get("candidates", [])
    check("clips selected", len(cands) >= 1, f"({len(cands)})")
    check("requested count", len(cands) <= expect["max_count"],
          f"({len(cands)} <= {expect['max_count']})")
    dmin, _, dmax = settings.effective_durations()
    check("duration respected",
          all(c["duration"] <= dmax + 5 for c in cands),
          f"(max seen {max((c['duration'] for c in cands), default=0):.0f}s "
          f"<= {dmax + 5})")
    # every selected clip rendered as its own validated mp4
    rendered = [c.get("export_file") for c in cands]
    ok_files = all(r and project.path("exports", r).exists() for r in rendered)
    check("all clips rendered as separate files", ok_files,
          f"({rendered})")
    check("stage timings recorded",
          set(project.data.get("stage_timings", {}).keys()) >=
          {"prepare", "transcribe", "discover", "select", "render"})
    note = project.data.get("selection_note", "")
    if expect.get("want_shortfall_note"):
        check("honest shortfall note", bool(note), f"('{note}')")
    else:
        print(f"  note: '{note}'")
    if expect.get("expect_captions"):
        check("captions rendered",
              any(project.path("captions").glob("*.srt")),
              "(srt present)")
    if expect.get("expect_silent"):
        out0 = project.path("exports", rendered[0])
        md = extract_metadata(out0)
        check("original audio removed", not md.has_audio, "(no audio stream)")
    if expect.get("expect_music_only"):
        import numpy as np
        import subprocess
        raw = project.path("exports", rendered[0]).with_suffix(".f32")
        subprocess.run([config.ffmpeg(), "-y", "-v", "error",
                        "-i", str(project.path("exports", rendered[0])),
                        "-ac", "1", "-ar", "16000", "-f", "f32le",
                        str(raw)], check=True,
                       creationflags=subprocess.CREATE_NO_WINDOW)
        a = np.fromfile(raw, dtype=np.float32)
        rms = float(np.sqrt((a ** 2).mean())) if a.size else 0.0
        raw.unlink(missing_ok=True)
        check("music-only track present (original removed)", rms > 0.002,
              f"(rms={rms:.4f})")
    if expect.get("expect_voiceover"):
        check("voiceover used",
              any(project.path("voiceovers").iterdir()), "(files present)")

    # timeline spread when multiple clips
    if len(cands) >= 3:
        starts = sorted(c["start"] for c in cands)
        spread = starts[-1] - starts[0]
        check("timeline spread", spread > 60, f"({spread:.0f}s)")

    print(f"  total wall time: {total:.1f}s | stages: "
          f"{project.data.get('stage_timings')}")
    failed = [n for n, p in checks if not p]
    if failed:
        print(f"  CASE FAILED: {failed}")
        return False
    print("  CASE PASSED")
    return True


def main() -> int:
    video = config.TESTS_DIR / "sample_talk.mp4"
    ok = True
    # 1. default: 3 clips, auto duration, no captions, keep audio
    ok &= run_case("default 3 clips / auto / no captions / keep audio",
                   video,
                   {"clip_count": 3},
                   {"max_count": 3})
    # 2. 6 clips requested on a 53s video -> honest shortfall + fair fill
    ok &= run_case("6 clips on tiny video (shortfall expected)",
                   video,
                   {"clip_count": 6, "min_score": 75},
                   {"max_count": 6, "want_shortfall_note": True})
    # 3. max 30s + highlighted captions + AI voice
    ok &= run_case("max30 + pro captions + local AI voice",
                   video,
                   {"clip_count": 2, "duration_mode": "max30",
                    "caption_mode": "pro", "voice_mode": "tts"},
                   {"max_count": 2, "expect_captions": True,
                    "expect_voiceover": True})
    # 4. remove original audio (music explicitly off -> silent video)
    ok &= run_case("no captions + removed audio",
                   video,
                   {"clip_count": 1, "duration_mode": "max45",
                    "caption_mode": "none", "voice_mode": "remove",
                    "music_enabled": False},
                   {"max_count": 1, "expect_silent": True})
    # 5. removed audio + music enabled -> music-only track
    ok &= run_case("removed audio + background music",
                   video,
                   {"clip_count": 1, "duration_mode": "max45",
                    "caption_mode": "none", "voice_mode": "remove",
                    "music_enabled": True},
                   {"max_count": 1, "expect_music_only": True})
    print("\n=== E2E:", "ALL CASES PASSED" if ok else "FAILURES PRESENT", "===")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
