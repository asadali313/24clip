"""Smoke test: faster-whisper transcription of the generated test video's audio.
Downloads the 'tiny' model to D:\\AI_Clip_Generator\\models\\huggingface.
Run with the project venv python.
"""
import os, sys, time

os.environ["HF_HOME"] = r"D:\AI_Clip_Generator\models\huggingface"
os.environ["TMP"] = r"D:\AI_Clip_Generator\temp\python"
os.environ["TEMP"] = r"D:\AI_Clip_Generator\temp\python"
os.environ["TMPDIR"] = r"D:\AI_Clip_Generator\temp\python"

from faster_whisper import WhisperModel

t0 = time.time()
model = WhisperModel("tiny", device="cpu", compute_type="int8", download_root=os.environ["HF_HOME"])
print(f"MODEL_LOADED in {time.time()-t0:.1f}s")

t0 = time.time()
segments, info = model.transcribe(
    r"D:\AI_Clip_Generator\tests\sample_talk.mp4",
    vad_filter=True,
    word_timestamps=True,
    beam_size=1,
)
print(f"language={info.language} prob={info.language_probability:.2f}")

words = 0
for seg in segments:
    txt = seg.text.strip()
    words += len(txt.split())
    print(f"[{seg.start:7.2f} -> {seg.end:7.2f}] ({seg.avg_logprob:+.2f}) {txt}")
print(f"TRANSCRIPTION_DONE in {time.time()-t0:.1f}s, {words} words")
