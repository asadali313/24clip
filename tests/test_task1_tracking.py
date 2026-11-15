"""Task 1 acceptance: content-type tracking, scene changes, real footage.

1. Gaming simulation: action blob left, scene cut, action right.
   -> content_type=action, camera follows action, re-centers after the cut.
2. Real GTA video: faces stay inside the crop window where detected.
3. Static/no-signal content -> safe centered crop (fallback rule).
"""
import json
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, r"D:\AI_Clip_Generator")

from app import config
config.ensure_layout()

from app.core import framing

checks = []
def check(name, cond, extra=""):
    checks.append((name, bool(cond)))
    print(f"  {'PASS' if cond else 'FAIL'}: {name} {extra}")


work = config.TEMP_DIR / "render"
work.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------- 1. gaming
print("=== 1. GAMING CONTENT (action tracking + scene change) ===")
game = work / "game_sim.mp4"
W, H, FPS = 1280, 720, 10
vw = cv2.VideoWriter(str(game), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (W, H))
for i in range(160):                                   # 16 s, two scenes
    if i < 80:                                          # scene A: dark map, action LEFT
        fr = np.full((H, W, 3), 30, np.uint8)
        cv2.rectangle(fr, (20, 640), (300, 700), (90, 90, 90), -1)
        cv2.rectangle(fr, (980, 640), (1260, 700), (90, 90, 90), -1)
        ax = 200 + 60 * np.sin(i / 6)
        cv2.circle(fr, (int(ax) + 60, 300), 46, (60, 200, 255), -1)
        cv2.circle(fr, (int(ax) + 130, 330), 24, (80, 160, 255), -1)
        cv2.circle(fr, (int(ax), 340), 14, (200, 220, 255), -1)
    else:                                               # scene B: bright arena, action RIGHT
        fr = np.full((H, W, 3), (35, 80, 70), np.uint8)
        cv2.rectangle(fr, (20, 640), (300, 700), (150, 170, 190), -1)
        cv2.rectangle(fr, (980, 640), (1260, 700), (150, 170, 190), -1)
        ax = 900 + 60 * np.sin(i / 5)
        cv2.circle(fr, (int(ax), 300), 46, (80, 255, 120), -1)
        cv2.circle(fr, (int(ax) - 90, 330), 26, (120, 200, 160), -1)
        cv2.circle(fr, (int(ax) + 40, 250), 16, (220, 255, 220), -1)
    vw.write(fr)
vw.release()

tr = framing.track_subject(game, 0.0, 16.0, W, H, int(H * 9 / 16))
check("classified as action", tr.content_type == "action",
      f"({tr.content_type}, faces {tr.face_hits}/{tr.samples}, "
      f"motion {tr.motion_hits}, cuts {tr.scene_cuts})")
check("scene cut detected", tr.scene_cuts >= 1, f"({tr.scene_cuts})")
half = len(tr.xs) // 2
a_mean = float(np.mean(tr.xs[:half]))
b_mean = float(np.mean(tr.xs[half:]))
check("camera followed action in scene A (left-ish)",
      a_mean < (W - int(H * 9 / 16)) * 0.55, f"(sceneA x={a_mean:.0f})")
check("camera re-centered after scene cut (right-ish)",
      b_mean > a_mean + 200, f"(sceneB x={b_mean:.0f})")
jumps = [abs(b - a) for a, b in zip(tr.xs, tr.xs[1:])]
check("smooth follow inside scenes",
      max(jumps) <= 2 * W * framing.MAX_JUMP_FRACTION * 2.0 + 1,
      f"(max jump {max(jumps):.0f}px)")

# ------------------------------------------------- 2. real video with faces
print("=== 2. REAL VIDEO (faces stay in frame) ===")
pj = config.PROJECTS_DIR / "project_001" / "project.json"
gta = None
if pj.exists():
    gta = Path(json.loads(pj.read_text(encoding="utf-8"))["source_video"])
if gta and Path(gta).exists():
    crop_w = int(1440 * 9 / 16)                # 810 for 2560x1440
    tr2 = framing.track_subject(Path(gta), 60.0, 90.0, 2560, 1440, crop_w)
    check("tracking ran on real video", tr2.samples >= 10,
          f"({tr2.samples} samples, type={tr2.content_type}, "
          f"faces {tr2.face_hits}, motion {tr2.motion_hits}, "
          f"cuts {tr2.scene_cuts})")
    if tr2.face_hits >= 3 and len(tr2.xs) == len(tr2.ts):
        # verify detected faces stay inside the crop window at face samples
        cap = cv2.VideoCapture(str(gta))
        inside = 0
        face_samples = 0
        step = tr2.ts[1] - tr2.ts[0] if len(tr2.ts) > 1 else 2.0
        yunet = framing._yunet(640, 360)
        for i, t in enumerate(tr2.ts):
            cap.set(cv2.CAP_PROP_POS_MSEC, (60.0 + t) * 1000)
            ok, frame = cap.read()
            if not ok:
                continue
            small = cv2.resize(frame, (640, 360))
            faces = framing._detect_faces(small, yunet, None)
            if not faces:
                continue
            face_samples += 1
            f = max(faces, key=lambda d: d["w"] * d["h"])
            fx_full = f["cx"] / (640 / 2560)
            x0 = tr2.xs[min(i, len(tr2.xs) - 1)]
            x1 = x0 + crop_w
            # allow the face CENTER to be inside (edges clamp at frame borders)
            if x0 - 40 <= fx_full <= x1 + 40:
                inside += 1
        cap.release()
        if face_samples:
            ratio = inside / face_samples
            check("faces remain inside the vertical frame", ratio >= 0.8,
                  f"({inside}/{face_samples} face samples)")
        else:
            print("  SKIP: no face samples in this window")
else:
    print("  SKIP: real GTA video not found")

# ------------------------------------------------- 3. static fallback
print("=== 3. FALLBACK (no signal) ===")
static = work / "static.mp4"
vw = cv2.VideoWriter(str(static), cv2.VideoWriter_fourcc(*"mp4v"), 10,
                     (W, H))
for _ in range(60):
    vw.write(np.full((H, W, 3), 45, np.uint8))
vw.release()
tr3 = framing.track_subject(static, 0.0, 6.0, W, H, int(H * 9 / 16))
check("static content -> safe centered crop", tr3.xs == [W / 2 - int(H * 9 / 16) / 2]
      or (len(tr3.xs) == 1 and abs(tr3.xs[0] - (W - int(H * 9 / 16)) / 2) < 2),
      f"(xs={tr3.xs[:1]}, type={tr3.content_type})")

failed = [n for n, p in checks if not p]
print(f"\n=== RESULT: {len(checks) - len(failed)}/{len(checks)} passed ===")
if failed:
    print("FAILED:", failed)
    sys.exit(1)
print("TASK 1 TRACKING PASSED")
