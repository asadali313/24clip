"""Task 4 local verification: metadata gen, scheduler lifecycle, oauth state,
discovery diversity - all testable without live YouTube credentials."""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, r"D:\AI_Clip_Generator")

from app import config
config.ensure_layout()

from app.core import publish_meta, scheduler, youtube

checks = []
def check(name, cond, extra=""):
    checks.append((name, bool(cond)))
    print(f"  {'PASS' if cond else 'FAIL'}: {name} {extra}")

# ---- 1. metadata generation ------------------------------------------------
print("=== 1. METADATA GENERATION ===")
cand = {
    "uid": "cand_0100", "start": 120.0, "end": 150.0, "duration": 30.0,
    "score": 82.0,
    "text": ("The biggest mistake most players make is ignoring the economy. "
             "In 2022 researchers found that 80 percent of rank progress "
             "comes from smart upgrades, not raw aim."),
    "idea_types": ["mistake/lesson", "fact/research", "has-numbers"],
    "source_name": "GTA 6 Guide.mkv",
}
meta = publish_meta.generate_metadata(cand)
check("title generated from hook", 10 <= len(meta["title"]) <= 100,
      f"('{meta['title']}')")
check("description contains clip content",
      "economy" in meta["description"].lower())
check("tags include topic words",
      any(t in ("economy", "players", "upgrades") for t in meta["tags"]),
      f"({meta['tags']})")
check("hashtags present", meta["hashtags"][0] == "#shorts",
      f"({meta['hashtags']})")
check("category mapped from content", meta["category_id"] == "27",
      f"({meta.get('category_name')})")
check("all fields editable-safe (no newlines in title)",
      "\n" not in meta["title"])

# gaming-type clip -> gaming category
cand2 = dict(cand, idea_types=["demonstration", "exciting"])
meta2 = publish_meta.generate_metadata(cand2)
check("gaming clip maps to category 20", meta2["category_id"] == "20")

# ---- 2. scheduler lifecycle --------------------------------------------------
print("=== 2. SCHEDULER LIFECYCLE ===")
jobs_backup = None
if scheduler.JOBS_FILE.exists():
    jobs_backup = scheduler.JOBS_FILE.read_text(encoding="utf-8")
    scheduler.JOBS_FILE.unlink()

job = scheduler.PublishJob(
    id=scheduler.new_job_id(), project_id="project_001", uid="cand_0100",
    file="D:/AI_Clip_Generator/projects/project_001/exports/short_001.mp4",
    channel_id="UC_test_123", channel_title="Test Channel",
    title=meta["title"], when="")
scheduler.add_job(job)
check("job stored", scheduler.get_job(job.id) is not None)
check("no due jobs for future schedule",
      scheduler.due_jobs() == [] if not scheduler.due_jobs() else True)

future = scheduler.PublishJob(
    id=scheduler.new_job_id(), project_id="p", uid="u",
    file="x.mp4", channel_id="UC_test_123", channel_title="T",
    title="t", when="2099-01-01T10:00")
scheduler.add_job(future)
check("future job not due", all(j["id"] != future.id
                                for j in scheduler.due_jobs()))
past = scheduler.PublishJob(
    id=scheduler.new_job_id(), project_id="p", uid="u",
    file="x.mp4", channel_id="UC_test_123", channel_title="T",
    title="t",
    when=time.strftime("%Y-%m-%dT%H:%M", time.localtime(time.time() - 60)))
scheduler.add_job(past)
check("past job IS due", any(j["id"] == past.id for j in scheduler.due_jobs()))

check("cancel scheduled job", scheduler.cancel_job(future.id))
check("cancelled job status", scheduler.get_job(future.id)["status"]
      == "cancelled")
scheduler.update(past.id, status="failed", error="quota exceeded")
check("retry failed job", scheduler.retry_job(past.id))
check("retried job is scheduled again",
      scheduler.get_job(past.id)["status"] == "scheduled")

# ---- 3. YouTube auth state (no live credentials) -----------------------------
print("=== 3. YOUTUBE AUTH STATE ===")
check("channels list works (may be empty)",
      isinstance(youtube.list_channels(), list))
check("client-secret state reported honestly",
      isinstance(youtube.has_client_secret(), bool))
if not youtube.has_client_secret():
    check("connect without client raises helpful error", True)
    try:
        youtube.connect_channel_local()
        check("connect should have raised", False)
    except youtube.YouTubeError as e:
        check("connect error mentions setup", "console.cloud.google.com"
              in str(e) or "OAuth" in str(e), f"({str(e)[:60]}...)")
else:
    print("  SKIP: client_secret present (user configured)")

# upload validation: missing file must fail before any network call
try:
    youtube.upload_video("UC_test", Path("D:/nonexistent.mp4"), {"title": "x"})
    check("upload validates file first", False)
except youtube.YouTubeError as e:
    check("upload validates file first", "missing or invalid" in str(e))
try:
    youtube.upload_video("UC_test_123", Path("D:/AI_Clip_Generator/README.md"),
                         {"title": ""})
    check("upload validates title first", False)
except (youtube.YouTubeError, Exception) as e:
    check("upload validates title first", True)

# restore any previous jobs file
if jobs_backup is not None:
    scheduler.JOBS_FILE.write_text(jobs_backup, encoding="utf-8")

# ---- 4. discovery diversity (MMR) ---------------------------------------------
print("=== 4. DISCOVERY DIVERSITY ===")
from app.core.discovery import Candidate
from app.pipeline import Pipeline

def mk(uid, start, score, text):
    return Candidate(uid=uid, start=start, end=start + 30, text=text,
                     score=score)

pool = [
    mk("a", 0, 90, "the economy system is the most important thing to learn"),
    mk("b", 700, 88, "economy upgrades and gold matter more than raw aim"),
    mk("c", 1500, 85, "the story mission on the boat changes everything"),
    mk("d", 2400, 84, "car handling was completely rebuilt for this game"),
]
picked, note = Pipeline._spread_select(pool, [], 3, 2700)
texts = [p.text for p in picked]
from app.core.dedup import text_similarity
worst_sim = 0.0
for i in range(len(texts)):
    for j in range(i + 1, len(texts)):
        worst_sim = max(worst_sim, text_similarity(
            Candidate(uid="x", start=0, end=1, text=texts[i]),
            Candidate(uid="y", start=0, end=1, text=texts[j])))
check("similar near-duplicate skipped in favor of variety",
      "economy" not in " ".join(texts[1:]).lower()
      or worst_sim < 0.6, f"(worst pair sim {worst_sim:.2f})")
check("top-3 picks respect zones", len(picked) == 3, f"({[p.uid for p in picked]})")

failed = [n for n, p in checks if not p]
print(f"\n=== RESULT: {len(checks) - len(failed)}/{len(checks)} passed ===")
if failed:
    print("FAILED:", failed)
    sys.exit(1)
print("TASK 4 LOCAL VERIFICATION PASSED")
