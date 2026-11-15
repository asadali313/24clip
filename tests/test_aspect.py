"""Aspect-ratio acceptance: render BOTH formats as real videos and verify.

1. 9:16 from a 1280x720 source  -> 1080x1920, subject-follow, captions OK
2. 16:9 from the same source    -> 1920x1080 passthrough-scale, captions OK
3. 16:9 from a TALLER source    -> vertical subject-follow (y-tracking)
4. No stretching: crop/pad only (checked via framing plan + dims)
5. Settings round-trip + project persistence
"""
import json
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, r"D:\AI_Clip_Generator")

from app import config
config.ensure_layout()

from app.core import captions, renderer
from app.core.captions import build_lines
from app.core.ffmpeg_tools import extract_metadata
from app.core.segmentation import Sentence
from app.core.transcriber import Word

checks = []
def check(name, cond, extra=""):
    checks.append((name, bool(cond)))
    print(f"  {'PASS' if cond else 'FAIL'}: {name} {extra}")

video = config.TESTS_DIR / "sample_talk.mp4"     # 1280x720 with speech
START, END = 13.8, 43.1
work = config.TEMP_DIR / "render"
work.mkdir(parents=True, exist_ok=True)

txt = ("Your brain actually cleans itself while you sleep tonight and it "
       "changes everything about learning.")
words = [Word(13.9 + i * 0.3, 14.15 + i * 0.3, w, 0.95)
         for i, w in enumerate(txt.split())]
sent = Sentence(13.9, 14.15 + len(txt.split()) * 0.3, txt, words, 1.0)
lines = build_lines([sent], START, END)

# ---- 1. portrait 9:16 ---------------------------------------------------------
print("=== 1. PORTRAIT 9:16 ===")
s_v = config.Settings()
s_v.aspect_ratio = "9:16"
s_v.caption_mode = "classic"
s_v.apply_workflow_choices()
out_v = work / "aspect_portrait.mp4"
ass_v = work / "aspect_portrait.ass"
captions.write_template_ass(lines, ass_v, s_v, "classic", out_w=1080,
                            out_h=1920)
renderer.render_clip(video, START, END, out_v, s_v, ass_file=ass_v)
md_v = extract_metadata(out_v)
check("portrait renders 1080x1920", md_v.width == 1080 and md_v.height == 1920,
      f"({md_v.width}x{md_v.height})")
check("portrait has audio", md_v.has_audio)
plan_v = renderer.plan_framing(video, START, END, aspect="9:16")
check("9:16 plan is subject-follow crop",
      plan_v.method == "crop" and plan_v.crop_w < plan_v.src_w,
      f"({plan_v.notes[:60]})")
check("9:16 plan output declared", plan_v.out_w == 1080
      and plan_v.out_h == 1920)

# ---- 2. landscape 16:9 from 16:9-ish source -------------------------------------
print("=== 2. LANDSCAPE 16:9 (same source) ===")
s_h = config.Settings()
s_h.aspect_ratio = "16:9"
s_h.caption_mode = "classic"
s_h.apply_workflow_choices()
out_h = work / "aspect_landscape.mp4"
ass_h = work / "aspect_landscape.ass"
captions.write_template_ass(lines, ass_h, s_h, "classic", out_w=1920,
                            out_h=1080)
renderer.render_clip(video, START, END, out_h, s_h, ass_file=ass_h)
md_h = extract_metadata(out_h)
check("landscape renders 1920x1080",
      md_h.width == 1920 and md_h.height == 1080,
      f"({md_h.width}x{md_h.height})")
check("landscape has audio", md_h.has_audio)
plan_h = renderer.plan_framing(video, START, END, aspect="16:9")
check("16:9 plan passthrough for 16:9 source",
      plan_h.method == "passthrough", f"({plan_h.notes[:60]})")

# captions visible in both (white text check in bottom band)
for name, path, frac in (("portrait", out_v, 0.75), ("landscape", out_h, 0.80)):
    cap = cv2.VideoCapture(str(path))
    cap.set(cv2.CAP_PROP_POS_MSEC, 1500)
    okf, frame = cap.read()
    cap.release()
    band = frame[int(frame.shape[0] * frac):, :, :]
    white = int(((band > 235).all(axis=2)).sum())
    check(f"{name} shows burned-in captions", white > 100, f"({white} px)")

# ---- 3. 16:9 from a TALLER source: vertical follow ------------------------------
print("=== 3. 16:9 FROM TALLER SOURCE (y-tracking) ===")
tall = work / "tall_source.mp4"
W, H = 720, 1280                                  # portrait source
vw = cv2.VideoWriter(str(tall), cv2.VideoWriter_fourcc(*"mp4v"), 10, (W, H))
for i in range(100):                              # 10 s, subject top -> bottom
    fr = np.full((H, W, 3), 40, np.uint8)
    cy = int(200 + (H - 400) * i / 99)
    cv2.circle(fr, (W // 2, cy), 60, (255, 255, 255), -1)
    cv2.circle(fr, (W // 2 - 18, cy - 15), 9, (0, 0, 0), -1)
    cv2.circle(fr, (W // 2 + 18, cy - 15), 9, (0, 0, 0), -1)
    vw.write(fr)
vw.release()
# give the synthetic video a real audio track so renders validate end-to-end
config.ffmpeg_run_silent = None
subprocess_run = __import__("subprocess").run
subprocess_run(
    [str(config.ffmpeg()), "-y", "-v", "error", "-i", str(tall),
     "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
     "-c:v", "copy", "-c:a", "aac", "-shortest",
     str(tall.with_suffix(".aud.mp4"))],
    check=True, capture_output=True, creationflags=0x08000000)
tall.with_suffix(".mp4").unlink()
tall.with_suffix(".aud.mp4").replace(tall)

from app.core import framing
tr = framing.track_subject(tall, 0.0, 10.0, W, H, crop_w=W)
check("tall source tracks y-centers", len(tr.cys) >= 4,
      f"({len(tr.cys)} samples, type={tr.content_type})")
if len(tr.cys) >= 4:
    moved = tr.cys[-1] - tr.cys[0]
    check("subject followed downward", moved > 200, f"({moved:.0f}px)")

plan_t = renderer.plan_framing(tall, 0.0, 10.0, aspect="16:9")
check("16:9 plan crops taller source vertically",
      plan_t.method == "crop" and plan_t.crop_w == W
      and plan_t.crop_h < H,
      f"(crop {plan_t.crop_w}x{plan_t.crop_h} of {W}x{H})")
check("16:9 plan declares y-follow", len(plan_t.ys) > 1,
      f"({plan_t.notes[:60]})")

out_t = work / "aspect_from_tall.mp4"
s_t = config.Settings()
s_t.aspect_ratio = "16:9"
s_t.apply_workflow_choices()
renderer.render_clip(tall, 0.5, 9.5, out_t, s_t, framing=plan_t)
md_t = extract_metadata(out_t)
check("16:9 from tall renders 1920x1080",
      md_t.width == 1920 and md_t.height == 1080,
      f"({md_t.width}x{md_t.height})")
# subject should stay visible mid-frame: check bright pixels near center rows
cap = cv2.VideoCapture(str(out_t))
cap.set(cv2.CAP_PROP_POS_MSEC, 4500)
okf, frame = cap.read()
cap.release()
center_band = frame[int(1080 * 0.2):int(1080 * 0.8), :, :]
bright = int(((center_band > 240).all(axis=2)).sum())
check("moving subject stays inside the 16:9 frame", bright > 500,
      f"({bright} px in center band)")

# ---- 4. settings round-trip -------------------------------------------------------
print("=== 4. SETTINGS / PROJECT PERSISTENCE ===")
s_v2 = config.Settings()
s_v2.aspect_ratio = "16:9"
check("output_resolution respects selection",
      s_v2.output_resolution() == (1920, 1080))
s_v2.aspect_ratio = "9:16"
check("default portrait intact", s_v2.output_resolution() == (1080, 1920))

from app.gui.screens.settings_screen import SettingsScreen  # noqa: E402
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication  # noqa: E402
app = QApplication.instance() or QApplication([])
from app.gui.main_window import MainWindow  # noqa: E402
win = MainWindow()
win.settings.aspect_ratio = "16:9"
win.screens["settings"]._load_from_settings()
check("settings screen reflects landscape",
      win.screens["settings"].aspect_widgets["16:9"].isChecked())
win.screens["settings"].collect()
check("collect writes aspect to settings",
      win.settings.aspect_ratio == "16:9")

failed = [n for n, p in checks if not p]
print(f"\n=== RESULT: {len(checks) - len(failed)}/{len(checks)} passed ===")
if failed:
    print("FAILED:", failed)
    sys.exit(1)
print("ASPECT RATIO ACCEPTANCE PASSED")
