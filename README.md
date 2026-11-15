# AI Clip Generator

Upload → Choose Settings → Start → AI Finds Best Clips → Download individually.
Fully local and free - no paid APIs, no watermarks, nothing leaves this PC.

## Run

Double-click the **AI Clip Generator** desktop shortcut
(or `Start_AI_Clip_Generator.bat`, or `.venv\Scripts\python.exe -m app.main`).

## Workflow

1. **Upload Video** - browse or drag & drop.
2. **Settings** (nothing processes until you click Start):
   - **A. Clip count** - 3 / 6 / 10 / 15 / custom. If fewer good moments exist,
     you get fewer clips and an honest explanation - never weak filler.
   - **B. Duration** - Automatic, Max 30/45/60 s, or custom min/target/max.
     The maximum is respected; a meaningful idea is never cut unnaturally.
   - **C. Captions** - none / simple white / professional (keyword highlight) /
     custom size-position-highlight. Generated locally from the transcript.
   - **D. Voice** - keep original / remove audio / your own recording (MP3,
     WAV, M4A...) / optional offline Windows AI voice reading the clip's words.
   - **E. Output** - vertical 9:16, 1080x1920, MP4 (H.264 + AAC), one file per clip.
3. **Processing** - five fast stages, all timed:
   prepare → transcribe (whole video, once) → find moments → rank & select → render.
4. **Results** - preview each clip, Download MP4 / Adjust / Regenerate / Delete,
   or Download all.

## Measured speed (20:13 test video, 6 clips, 1080p, burned captions)

| Stage | Time |
|---|---|
| Prepare | 0.0 s |
| Transcribe (base model, beam 1) | 62 s (~19x realtime here; expect ~4-8x on i3) |
| Find moments | 0.1 s |
| Rank & select | 3 s |
| Render 6 clips | 48 s |
| **Total** | **~2 min** |

Removed waste from v1: full-video scene detection (decoded every frame at full
resolution - was ~3 min/hour and useless for talking content), beam-5
transcription, full-resolution face analysis. Visual checks now sample 3 tiny
frames per contender and face-track only the final clips at half resolution.

## Storage (D: drive only)

`projects\project_NNN\` per video (transcripts, candidates, exports, logs),
`models\` Whisper weights, `temp\` auto-cleaned after each run, `bin\` bundled
FFmpeg. The app refuses to run below 5 GB free and never writes data to C:.

## Privacy

Everything runs locally. Your voice recordings are never uploaded anywhere;
the optional AI voice is the Windows built-in offline synthesizer.

## Publishing to YouTube (multi-channel)

The **Publishing** screen connects your YouTube channels via official Google
OAuth 2.0 (this app never sees your Google password). One-time setup:

1. Create a free Google Cloud project → enable **YouTube Data API v3**.
2. OAuth consent screen → External → add your own account as tester.
3. Create **OAuth Client ID → Desktop app** → download the JSON →
   save it as `D:\AI_Clip_Generator\credentials\client_secret.json`.
4. In the app: Publishing → **Connect channel** (repeat for each channel).

Then on Results → **Upload to YouTube**: pick the channel, review/edit the
auto-generated title/description/tags, choose Publish now or Schedule for
later. Uploads are resumable; tokens are stored on D: only. Each upload uses
~1,600 quota units of the default 10,000/day (≈6 uploads/day).

## Tests

```
.venv\Scripts\python.exe -I tests\run_e2e.py           # settings cases
.venv\Scripts\python.exe -I tests\test_task1_tracking.py  # subject tracking
.venv\Scripts\python.exe -I tests\test_three_fixes.py  # captions/voice/tracking
.venv\Scripts\python.exe -I tests\test_task4.py        # metadata/scheduler/oauth
.venv\Scripts\python.exe -I tests\run_20min_test.py    # 20-min acceptance
.venv\Scripts\python.exe -I tests\smoke_gui.py         # GUI construction
```
