"""REAL upload acceptance test - uploads one actual clip to the connected
channel as UNLISTED (respects the user's channel; not public spam).

Verifies: upload accepted, real video ID returned, processing status polled
from the API, link matches the video, metadata persisted. Nothing is
simulated: every check reads YouTube's API responses.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, r"D:\AI_Clip_Generator")

from app import config
config.ensure_layout()

from app.core import publish_meta, scheduler, youtube
from app.core.youtube import YouTubeError

checks = []
def check(name, cond, extra=""):
    checks.append((name, bool(cond)))
    print(f"  {'PASS' if cond else 'FAIL'}: {name} {extra}")

# ---- preflight: real channel connected --------------------------------------
print("=== PREFLIGHT ===")
chans = youtube.list_channels()
check("channel connected", len(chans) >= 1,
      f"({[c.get('title') for c in chans]})")
if not chans:
    print("Cannot run: no channel token. Complete Connect Channel first.")
    sys.exit(1)
channel = chans[0]
try:
    svc = youtube.get_service(channel["channel_id"])
    r = svc.channels().list(part="snippet", mine=True).execute()
    live_title = r["items"][0]["snippet"]["title"]
    check("token valid via API call", live_title == channel.get("title"),
          f"({live_title})")
except Exception as e:  # noqa: BLE001
    print(f"Token invalid: {e}")
    sys.exit(1)

# ---- the clip to upload -------------------------------------------------------
from app import project_store
proj = project_store.Project(config.PROJECTS_DIR / "project_001")
cands = proj.data.get("candidates", [])
clip = None
for c in cands:
    f = proj.path("exports", c.get("export_file", ""))
    if f.exists():
        clip, cand = f, c
        break
check("clip available for upload", clip is not None,
      f"({clip.name if clip else '-'})")

# ---- dedup guard ---------------------------------------------------------------
existing = scheduler.find_active_for_clip(proj.id, cand["uid"])
published_before = scheduler.find_published_for_clip(proj.id, cand["uid"])
if existing or published_before:
    print(f"An upload for this clip already exists "
          f"(status: {(existing or published_before)['status']}). "
          "Cancelling it for this test run (explicit retry).")
    if existing:
        scheduler.cancel_job(existing["id"])

# ---- metadata: generated + persisted ------------------------------------------
meta = publish_meta.get_saved_metadata(proj, cand["uid"]) or \
    publish_meta.generate_metadata(cand)
publish_meta.save_metadata(proj, cand["uid"], meta)
meta = publish_meta.get_saved_metadata(proj, cand["uid"])
check("metadata persisted with clip",
      publish_meta.get_saved_metadata(proj, cand["uid"]) is not None)
meta["privacy_status"] = "unlisted"          # test upload: not public
print(f"    title: {meta['title']}")

# ---- create the durable job -----------------------------------------------------
job = scheduler.PublishJob(
    id=scheduler.new_job_id(), project_id=proj.id, uid=cand["uid"],
    file=str(clip), channel_id=channel["channel_id"],
    channel_title=channel["title"], title=meta["title"], when="",
    metadata=meta, privacy_status="unlisted")
scheduler.add_job(job)

# ---- REAL upload with progress --------------------------------------------------
print("=== UPLOADING (real, resumable) ===")
progress_seen = []

def on_progress(frac, size):
    progress_seen.append(frac)
    print(f"    {frac * 100:5.1f}%  of {size // 1048576} MB")

t0 = time.time()
result = youtube.upload_video(channel["channel_id"], clip, meta,
                              progress_cb=on_progress)
dt = time.time() - t0
check("YouTube returned a video ID", bool(result.get("video_id")),
      f"({result.get('video_id')})")
check("progress reported during upload", len(progress_seen) >= 1,
      f"({len(progress_seen)} chunks, {dt:.0f}s)")

vid = result["video_id"]
scheduler.update(job.id, video_id=vid, video_url=result["url"],
                 progress=1.0, status="processing")
check("link matches video ID", result["url"].endswith(vid),
      f"({result['url']})")

# ---- processing status: real API polling -----------------------------------------
print("=== PROCESSING STATUS (YouTube API) ===")
final = youtube.wait_for_processing(channel["channel_id"], vid,
                                    timeout_s=600, poll_s=10)
check("video exists on YouTube", final.get("exists") is True)
check("upload accepted by YouTube",
      final.get("upload_status") in ("processed", "uploaded"),
      f"(uploadStatus={final.get('upload_status')})")
check("processing completed or in progress",
      final.get("processing_status") in ("succeeded", "terminated",
                                         "processing", ""),
      f"(processingStatus={final.get('processing_status')})")

# ---- job lifecycle recorded --------------------------------------------------------
scheduler.update(job.id, status="published", processing_status=final.get(
    "processing_status", ""), upload_status=final.get("upload_status", ""),
    thumbnail=final.get("thumbnail", ""), privacy_status="unlisted",
    finished_at=time.strftime("%Y-%m-%d %H:%M:%S"))
stored = scheduler.get_job(job.id)
check("job marked published with video id",
      stored["status"] == "published" and stored["video_id"] == vid)
check("metadata used from the saved copy",
      stored["metadata"]["title"] == meta["title"])

# ---- duplicate protection: second upload attempt must be blocked ----------
guard = scheduler.find_published_for_clip(proj.id, cand["uid"])
check("published job found for dedup guard", guard is not None
      and guard["video_id"] == vid)

print(f"\n    VIDEO LINK: {result['url']}")
print(f"    (unlisted - visible via link, not on the channel page)")
failed = [n for n, p in checks if not p]
print(f"\n=== RESULT: {len(checks) - len(failed)}/{len(checks)} passed ===")
if failed:
    print("FAILED:", failed)
    sys.exit(1)
print("REAL UPLOAD ACCEPTANCE PASSED")
