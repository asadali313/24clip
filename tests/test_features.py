"""Feature test: export with caption burn-in (SRT+ASS) and voiceover mixing."""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, r"D:\AI_Clip_Generator")

ROOT = Path(r"D:\AI_Clip_Generator")
from app import config, project_store
from app.core import export as export_mod

# generate a personal voiceover with Windows TTS
PS = r'''
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$s.SetOutputToWaveFile("D:\AI_Clip_Generator\tests\voiceover.wav")
$s.Speak("Here is a quick summary of the key idea, recorded as a personal voiceover for testing.")
$s.Dispose()
'''
subprocess.run(["powershell", "-NoProfile", "-Command", PS], check=True,
               creationflags=subprocess.CREATE_NO_WINDOW)

checks = []
def check(name, cond, extra=""):
    checks.append((name, bool(cond)))
    print(f"  {'PASS' if cond else 'FAIL'}: {name} {extra}")

# use the short project (project_001)
proj = project_store.Project(ROOT / "projects" / "project_001")
cands = proj.data.get("candidates", [])
check("project has candidates", len(cands) >= 1)

settings = config.Settings()
settings.captions_enabled = True
settings.caption_position = "bottom"
settings.caption_highlight = True
cand = dict(cands[0])
cand["voiceover"] = str(ROOT / "tests" / "voiceover.wav")

print("=== exporting with captions + voiceover ===")
ok, failed = export_mod.export_many(proj, [cand], settings)
check("export succeeded", len(ok) == 1 and not failed,
      failed[0]["error"] if failed else "")
if ok:
    meta = ok[0]
    stem = Path(meta["file"]).stem
    check("srt written", proj.path("captions", f"{stem}.srt").exists())
    check("ass written", proj.path("captions", f"{stem}.ass").exists())
    check("captions flagged in meta", meta.get("captions") is True)
    check("voiceover flagged in meta", meta.get("voiceover") is True)
    out = proj.path("exports", meta["file"])
    check("output exists", out.exists(),
          f"({out.stat().st_size // 1024} KB)")
    # probe duration of output
    import app.core.ffmpeg_tools as ft
    md = ft.extract_metadata(out)
    expected = cand["end"] - cand["start"]
    check("duration matches clip", abs(md.duration - expected) < 2.0,
          f"({md.duration:.1f}s vs {expected:.1f}s)")
    print(f"  framing used: {meta.get('framing')}")

failed_checks = [n for n, p in checks if not p]
print(f"\n=== RESULT: {len(checks)-len(failed_checks)}/{len(checks)} passed ===")
sys.exit(1 if failed_checks else 0)
