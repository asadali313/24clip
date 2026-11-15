"""Publishing v2 local tests: metadata persistence/regeneration, dedup,
status transitions. Live upload tested separately against the real channel."""
import sys
from pathlib import Path

sys.path.insert(0, r"D:\AI_Clip_Generator")

from app import config
config.ensure_layout()

from app.core import publish_meta, scheduler

checks = []
def check(name, cond, extra=""):
    checks.append((name, bool(cond)))
    print(f"  {'PASS' if cond else 'FAIL'}: {name} {extra}")

# ---- 1. metadata persistence + regeneration --------------------------------
print("=== 1. METADATA SAVE / REGENERATE ===")
from app import project_store
proj = project_store.Project(config.PROJECTS_DIR / "project_001")
cands = proj.data.get("candidates", [])
check("project has clips", len(cands) >= 1, f"({len(cands)})")
cand = dict(cands[0])
uid = cand["uid"]

m1 = publish_meta.generate_metadata(cand)
publish_meta.save_metadata(proj, uid, m1)
m_saved = publish_meta.get_saved_metadata(proj, uid)
check("metadata saved with clip", m_saved is not None
      and m_saved["title"] == m1["title"])

m2 = publish_meta.regenerate_metadata(cand)
# mirror the wizard: the generated meta rides on the candidate dict so the
# NEXT regenerate advances the seed
cand["publish_metadata"] = m2
check("regenerate produces a variant",
      m2["title"] != m1["title"] or m2.get("seed", 0) != m1.get("seed", 0),
      f"('{m1['title'][:40]}' -> '{m2['title'][:40]}')")
m3 = publish_meta.regenerate_metadata(cand)
cand["publish_metadata"] = m3
check("regenerate cycles further", m3["title"] != m2["title"]
      or m3.get("seed") != m2.get("seed"))

# ---- 2. dedup protection -----------------------------------------------------
print("=== 2. DUPLICATE PROTECTION ===")
# clean old entries: stale test-channel jobs + any leftover jobs for this clip
for j in scheduler.all_jobs():
    if (j.get("channel_id", "").startswith("UC_test")
            or (j.get("project_id") == proj.id and j.get("uid") == uid)):
        scheduler.update(j["id"], status="cancelled")

j = scheduler.PublishJob(
    id=scheduler.new_job_id(), project_id=proj.id, uid=uid,
    file=str(proj.path("exports", "short_001.mp4")),
    channel_id="UC5I6ZK8zsSTPdGgqmQyxrNg", channel_title="Vice Music",
    title=m1["title"], when="")
scheduler.add_job(j)
active = scheduler.find_active_for_clip(proj.id, uid)
check("active job detected for clip", active is not None
      and active["id"] == j.id)
# a second active job for the same clip would be a duplicate - UploadWorker
# refuses when one exists
published = scheduler.find_published_for_clip(proj.id, uid)
check("no false published flag", published is None)

# status transition path (worker logic mirrored here for store mechanics)
scheduler.update(j.id, status="uploading", progress=0.4)
check("uploading with progress persisted",
      scheduler.get_job(j.id)["status"] == "uploading"
      and abs(scheduler.get_job(j.id)["progress"] - 0.4) < 0.01)
scheduler.update(j.id, status="processing", progress=1.0, video_id="dbgvid")
check("processing after upload",
      scheduler.get_job(j.id)["status"] == "processing"
      and scheduler.get_job(j.id)["video_id"] == "dbgvid")
scheduler.update(j.id, status="published", video_url="https://youtu.be/dbgvid")
check("published with url",
      scheduler.get_job(j.id)["status"] == "published")
# live 'uploading' jobs must NOT be silently rewritten by migration
scheduler.update(j.id, status="uploading", progress=0.7)
check("live uploading status preserved by reads",
      scheduler.get_job(j.id)["status"] == "uploading")

# cancel and clean up the debug job (only waiting/scheduled/failed cancel;
# an actively-uploading job correctly cannot be cancelled)
scheduler.update(j.id, status="waiting")
check("debug job cancelled", scheduler.cancel_job(j.id)
      and scheduler.get_job(j.id)["status"] == "cancelled")

failed = [n for n, p in checks if not p]
print(f"\n=== RESULT: {len(checks) - len(failed)}/{len(checks)} passed ===")
if failed:
    print("FAILED:", failed)
    sys.exit(1)
print("PUBLISHING V2 LOCAL TESTS PASSED")
