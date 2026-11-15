"""Final customization tests: 9:16 full render + music-off + URL import
(local HTTP server for the direct-URL path - deterministic, no network)."""
import json
import sys
import threading
import time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path

sys.path.insert(0, r"D:\AI_Clip_Generator")

from app import config, project_store
config.ensure_layout()

from app.core import fullvideo, urlimport
from app.core.ffmpeg_tools import extract_metadata

checks = []
def check(name, cond, extra=""):
    checks.append((name, bool(cond)))
    print(f"  {'PASS' if cond else 'FAIL'}: {name} {extra}")


# ---- 1. 9:16 + music-off full render on the 53 s sample ----------------------
print("=== 1. 9:16 FULL RENDER, MUSIC OFF ===")
video = config.TESTS_DIR / "sample_talk.mp4"
project = project_store.Project.create(video)
settings = config.Settings()
settings.model_size = "tiny"
settings.processing_mode = "full"
settings.fv_voice = True
settings.fv_captions = True
settings.caption_mode = "minimal"
settings.fv_title = "Sleep Science"
settings.music_enabled = False
settings.fv_formats = ["9:16"]
settings.apply_workflow_choices()

t0 = time.time()
outputs = fullvideo.process_full_video(project, settings)
wall = time.time() - t0
meta = outputs[0]
out = project.path("exports", meta["file"])
md = extract_metadata(out)
check("9:16 full render", md.width == 1080 and md.height == 1920,
      f"({md.width}x{md.height}, {out.stat().st_size // 1048576} MB)")
check("music-off output has narration only", md.has_audio)
check("title overlay + captions present",
      meta["title_overlay"] == "Sleep Science" and meta["captions"])
import cv2, numpy as np
cap = cv2.VideoCapture(str(out))
cap.set(cv2.CAP_PROP_POS_MSEC, 1200)
okf, frame = cap.read()
cap.release()
band = frame[int(frame.shape[0] * 0.70):, :, :]
white = int(((band > 225).all(axis=2)).sum())
check("minimal captions visible", white > 100, f"({white} px)")
print(f"    ({wall:.0f}s)")

# ---- 2. URL import: direct MP4 via local HTTP server --------------------------
print("=== 2. URL IMPORT (direct MP4) ===")
src_file = config.TESTS_DIR / "sample_talk.mp4"
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        data = src_file.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


server = ThreadingHTTPServer(("127.0.0.1", 0), H)


port = server.server_address[1]
threading.Thread(target=server.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{port}/sample_talk.mp4"
info = urlimport.probe_info(url)
check("direct URL probed", info["kind"] == "direct"
      and info["duration"] > 50, f"({info['duration']:.0f}s)")
progress_seen = []
path = urlimport.download(url, progress_cb=progress_seen.append)
check("direct URL downloaded", Path(path).exists()
      and Path(path).stat().st_size > 1_000_000,
      f"({Path(path).stat().st_size // 1048576} MB, "
      f"{len(progress_seen)} progress ticks)")
server.shutdown()

# error handling: not-a-video URL
try:
    urlimport.probe_info("https://127.0.0.1:1/x.mp4")
    check("unreachable URL handled", False)
except urlimport.ImportURLError as e:
    check("unreachable URL handled", True, str(e)[:60])
try:
    urlimport.probe_info("notaurl")
    check("invalid URL handled", False)
except urlimport.ImportURLError as e:
    check("invalid URL handled", True, str(e)[:40])

# platform URL error mapping (no network call needed)
err = urlimport._map_ytdlp_error("ERROR: [youtube] xyz: Private video.")
check("youtube private video error mapped",
      "private" in err.lower())

failed = [n for n, p in checks if not p]
print(f"\n=== RESULT: {len(checks) - len(failed)}/{len(checks)} passed ===")
if failed:
    print("FAILED:", failed)
    sys.exit(1)
print("CUSTOMIZATION FINAL TESTS PASSED")
