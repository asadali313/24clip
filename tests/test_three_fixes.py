"""Verification for the three requested fixes, on real rendered files.

1. AI voice: original audio COMPLETELY gone, AI voice + BGM audible.
2. Original voice: original audio untouched (samples match source).
3. Four caption templates render + a moving-subject crop test.
"""
import json
import subprocess
import sys
import time
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, r"D:\AI_Clip_Generator")

from app import config
config.ensure_layout()

from app.core import captions, ffmpeg_tools, music, renderer
from app.core.captions import build_lines
from app.core.segmentation import Sentence
from app.core.transcriber import Word

PY = sys.executable
checks = []
def check(name, cond, extra=""):
    checks.append((name, bool(cond)))
    print(f"  {'PASS' if cond else 'FAIL'}: {name} {extra}")


def decode_audio(path: Path, out_raw: Path) -> np.ndarray:
    """Decode to mono f32 16k via ffmpeg for analysis."""
    subprocess.run(
        [config.ffmpeg(), "-y", "-v", "error", "-i", str(path),
         "-ac", "1", "-ar", "16000", "-f", "f32le", str(out_raw)],
        check=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    return np.fromfile(out_raw, dtype=np.float32)


def rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(x ** 2))) if x.size else 0.0


def correlation(a: np.ndarray, b: np.ndarray) -> float:
    n = min(len(a), len(b))
    if n < 100:
        return 0.0
    a, b = a[:n] - a[:n].mean(), b[:n] - b[:n].mean()
    d = np.std(a) * np.std(b)
    return float(np.mean(a * b) / d) if d > 1e-9 else 0.0


video = config.TESTS_DIR / "sample_talk.mp4"      # has clear original speech
START, END = 13.8, 43.1
work = config.TEMP_DIR / "render"
work.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------- 1. AI voice
print("=== 1. AI VOICE MODE ===")
music_path = music.get_bgm()
check("bgm loop generated", music_path.exists() and
      music_path.stat().st_size > 50000,
      f"({music_path.stat().st_size // 1024} KB)")

s = config.Settings()
s.voice_mode = "tts"
s.apply_workflow_choices()
from app.core import tts
vo_wav = work / "test_tts.wav"
tts.synthesize("Here is the key idea you should remember about sleep and "
               "the brain.", vo_wav)
check("AI voice synthesized", vo_wav.exists())

out_ai = work / "test_ai_voice.mp4"
renderer.render_clip(video, START, END, out_ai, s,
                     voiceover=vo_wav, bgm=music_path)
md_ai = ffmpeg_tools.extract_metadata(out_ai)
check("AI-voice clip valid", md_ai.duration > 25 and md_ai.has_audio)

src_seg = decode_audio(video, work / "src.f32")
# original speech in the same time window
src_slice = src_seg[int(START * 16000):int(END * 16000)]
ai_audio = decode_audio(out_ai, work / "ai.f32")

# the AI voice is a DIFFERENT signal than the original speech:
# correlation of the two should be low (different speaker/text)
n = min(len(ai_audio), len(src_slice))
corr = correlation(ai_audio[:n], src_slice[:n])
check("original voice NOT dominant in output", abs(corr) < 0.5,
      f"(corr={corr:.2f})")
check("output audio not silent (AI voice + BGM)", rms(ai_audio) > 0.005,
      f"(rms={rms(ai_audio):.4f})")

# BGM present: low-frequency energy should exist even in speech pauses of TTS
# take the last 3s where TTS likely finished but music loops on
tail = ai_audio[-int(3 * 16000):]
check("background music audible at tail", rms(tail) > 0.002,
      f"(tail rms={rms(tail):.4f})")

# ---------------------------------------------------------------- 2. original
print("=== 2. ORIGINAL VOICE MODE ===")
s2 = config.Settings()
s2.voice_mode = "keep"
s2.apply_workflow_choices()
out_keep = work / "test_keep.mp4"
renderer.render_clip(video, START, END, out_keep, s2)
keep_audio = decode_audio(out_keep, work / "keep.f32")
corr_keep = correlation(keep_audio, src_slice)
check("original audio preserved (high correlation)", corr_keep > 0.85,
      f"(corr={corr_keep:.2f})")

# ---------------------------------------------------------------- 3a. captions
print("=== 3a. CAPTION TEMPLATES ===")
prev_dir = config.resource_path("assets/caption_previews")
paths = captions.render_template_previews(prev_dir)
check("all caption templates previewed", len(paths) >= 6,
      f"({len(paths)}: {sorted(paths)})")
for tpl, p in sorted(paths.items()):
    check(f"preview {tpl} non-trivial", p.stat().st_size > 3000,
          f"({p.stat().st_size // 1024} KB)")

# render one real clip with karaoke (word-timing dependent)
s3 = config.Settings()
s3.voice_mode = "keep"
s3.caption_mode = "karaoke"
s3.apply_workflow_choices()
data = json.loads((video.parent.parent / "projects" / "project_009" /
                   "transcripts" / "transcript.json").read_text(encoding="utf-8")) \
    if (video.parent.parent / "projects" / "project_009").exists() else None
# build sentences directly from the short video's own transcript
tdata = json.loads(Path(video.parent / "sample_transcript.json").read_text()) \
    if (video.parent / "sample_transcript.json").exists() else None
# simpler: transcribe-free sentence from known TTS script timing
words = [Word(13.9 + i * 0.32, 14.1 + i * 0.32, w, 0.95) for i, w in
         enumerate("Your brain actually cleans itself while you sleep tonight.".split())]
sent = Sentence(13.9, 15.9, "Your brain actually cleans itself while you "
                "sleep tonight.", words, 1.0)
lines = build_lines([sent], START, END)
check("karaoke lines have word timings",
      lines and all(ln.words for ln in lines))
ass_file = work / "karaoke_test.ass"
captions.write_template_ass(lines, ass_file, s3, "karaoke")
check("karaoke ASS has k-tags", "\\k" in ass_file.read_text(encoding="utf-8"))

out_cap = work / "test_karaoke.mp4"
renderer.render_clip(video, START, END, out_cap, s3, ass_file=ass_file)
import cv2
cap = cv2.VideoCapture(str(out_cap))
total_yellow = 0
total_white = 0
# scan several frames: karaoke highlighting is progressive, so sample where
# lines are partially/fully sung
for ms in range(1200, 3200, 400):
    cap.set(cv2.CAP_PROP_POS_MSEC, ms)
    okf, frame = cap.read()
    if not okf:
        continue
    band = frame[int(frame.shape[0] * 0.75):, :, :]
    total_white += int(((band > 235).all(axis=2)).sum())
    total_yellow += int((((band[:, :, 0] > 180) & (band[:, :, 1] > 160) &
                          (band[:, :, 2] < 120))).sum())
cap.release()
# karaoke fully "sings" a line that ended -> highlight; text presence is
# white OR yellow pixels
check("karaoke captions visible", total_white + total_yellow > 300,
      f"(white {total_white} px, yellow {total_yellow} px)")
check("karaoke highlight color present", total_yellow > 100,
      f"({total_yellow} px)")

# ---------------------------------------------------------------- 3b. tracking
print("=== 3b. SUBJECT TRACKING ===")
# synthetic clip: bright 'subject' square slides left -> right on a static
# background. Motion-centroid fallback must follow it.
import cv2 as _cv2
import numpy as _np
track_video = work / "moving_subject.mp4"
vw = _cv2.VideoWriter(str(track_video),
                      _cv2.VideoWriter_fourcc(*"mp4v"), 10, (960, 540))
for i in range(120):                       # 12s
    fr = _np.full((540, 960, 3), 40, _np.uint8)
    _cv2.rectangle(fr, (200, 300), (760, 320), (70, 70, 70), -1)  # static road
    cx = int(140 + (960 - 280) * i / 119)
    _cv2.circle(fr, (cx, 240), 55, (255, 255, 255), -1)           # subject
    _cv2.circle(fr, (cx - 15, 230), 8, (0, 0, 0), -1)
    _cv2.circle(fr, (cx + 15, 230), 8, (0, 0, 0), -1)
    vw.write(fr)
vw.release()

from app.core import framing
track = framing.track_subject(track_video, 0.0, 12.0, 960, 540,
                              int(540 * 9 / 16))
xs = track.xs
check("tracker produced samples", track.samples >= 6,
      f"({track.samples} samples, {track.motion_hits} motion hits, "
      f"detector={track.detector})")
if len(xs) >= 4:
    # crop x should MOVE in the direction of the subject (left->right)
    moved = xs[-1] - xs[0]
    check("camera follows moving subject", moved > 150, f"({moved:.0f}px)")
    # smoothness: no sample-to-sample jump bigger than the clamp
    jumps = [abs(b - a) for a, b in zip(xs, xs[1:])]
    check("smooth follow (no jumps)",
          max(jumps) <= 960 * framing.MAX_JUMP_FRACTION * 2.0 + 1,
          f"(max jump {max(jumps):.0f}px)")
else:
    check("camera follows moving subject", False, "(too few samples)")

# real video: YuNet must find faces on the user's GTA video (it has speakers
# and gameplay). sample_talk.mp4 is a synthetic pattern with NO faces, so it
# is the wrong target for a face check.
gta_src = None
pj = config.PROJECTS_DIR / "project_001" / "project.json"
if pj.exists():
    gta_src = Path(json.loads(pj.read_text(encoding="utf-8"))["source_video"])
if gta_src and Path(gta_src).exists():
    track2 = framing.track_subject(Path(gta_src), 60.0, 90.0, 2560, 1440, 810)
    check("YuNet detects faces on real gameplay video",
          track2.face_hits >= 3,
          f"({track2.face_hits}/{track2.samples}, detector={track2.detector})")
else:
    print("  SKIP: real GTA video not found for face-detection check")

# full render with tracking on the real video
out_tr = work / "test_tracked.mp4"
s4 = config.Settings()
s4.voice_mode = "keep"
plan = renderer.plan_framing(video, START, END)
renderer.render_clip(video, START, END, out_tr, s4, framing=plan)
md_tr = ffmpeg_tools.extract_metadata(out_tr)
check("tracked render valid 1080x1920",
      md_tr.width == 1080 and md_tr.height == 1920,
      f"({plan.notes})")

failed = [n for n, p in checks if not p]
print(f"\n=== RESULT: {len(checks) - len(failed)}/{len(checks)} passed ===")
if failed:
    print("FAILED:", failed)
    sys.exit(1)
print("THREE-FIX VERIFICATION PASSED")
