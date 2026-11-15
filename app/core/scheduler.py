"""Publishing scheduler v2: durable job store with full lifecycle statuses.

Job status flow (mirrors reality - uploaded != publicly visible):
  waiting -> uploading (progress 0..1) -> processing (YouTube transcoding)
          -> published | failed | cancelled

Jobs persist in D:\\AI_Clip_Generator\\publishing\\jobs.json and survive app
restarts. Duplicate protection: one ACTIVE job per (project_id, uid) clip.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .. import config

JOBS_FILE = config.ROOT / "publishing" / "jobs.json"

ACTIVE_STATUSES = ("waiting", "uploading", "processing", "scheduled")


@dataclass
class PublishJob:
    id: str
    project_id: str
    uid: str
    file: str                     # absolute path to the mp4
    channel_id: str
    channel_title: str
    title: str
    when: str = ""                # ISO local time for scheduled uploads
    status: str = "waiting"       # waiting|scheduled|uploading|processing|published|failed|cancelled
    metadata: dict = field(default_factory=dict)
    # real upload tracking
    progress: float = 0.0         # 0..1 bytes sent (resumable session)
    video_id: str = ""
    video_url: str = ""
    processing_status: str = ""   # processing|processed|failed|terminated|succeeded
    upload_status: str = ""       # YouTube uploadStatus field
    privacy_status: str = ""
    thumbnail: str = ""           # local thumb path (pre-upload) or YT url
    captions_srt: str = ""        # optional local .srt uploaded after the video
    error: str = ""
    attempts: int = 0
    created_at: str = field(default_factory=lambda: time.strftime(
        "%Y-%m-%d %H:%M:%S"))
    upload_started_at: str = ""
    finished_at: str = ""

    @property
    def is_active(self) -> bool:
        return self.status in ACTIVE_STATUSES


def _load() -> list[dict]:
    if not JOBS_FILE.exists():
        return []
    try:
        return json.loads(JOBS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def _save(jobs: list[dict]) -> None:
    JOBS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = JOBS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(jobs, indent=2, ensure_ascii=False),
                   encoding="utf-8")
    tmp.replace(JOBS_FILE)


def _migrate(j: dict) -> dict:
    """Old jobs may predate new fields. NOTE: statuses are NOT rewritten
    here - live 'uploading' jobs must keep their status; recovery of stale
    jobs is the dashboard's job (_recover_interrupted)."""
    j.setdefault("progress", 0.0)
    j.setdefault("video_id", "")
    j.setdefault("processing_status", "")
    j.setdefault("upload_status", "")
    j.setdefault("privacy_status", "")
    j.setdefault("thumbnail", "")
    j.setdefault("captions_srt", "")
    j.setdefault("upload_started_at", "")
    return j


def add_job(job: PublishJob) -> PublishJob:
    jobs = _load()
    jobs.append(asdict(job))
    _save(jobs)
    return job


def update(job_id: str, **fields) -> None:
    jobs = _load()
    for j in jobs:
        if j["id"] == job_id:
            j.update(fields)
            _save(jobs)
            return


def all_jobs() -> list[dict]:
    jobs = [_migrate(j) for j in _load()]
    order = {"uploading": 0, "processing": 1, "waiting": 2, "scheduled": 3,
             "failed": 4, "published": 5, "cancelled": 6}
    return sorted(jobs, key=lambda j: (order.get(j["status"], 7),
                                       j.get("when") or j.get("created_at", "")))


def get_job(job_id: str) -> dict | None:
    for j in _load():
        if j["id"] == job_id:
            return _migrate(j)
    return None


def find_active_for_clip(project_id: str, uid: str) -> dict | None:
    """Duplicate protection: an active job for the same clip?"""
    for j in all_jobs():
        if (j.get("project_id") == project_id and j.get("uid") == uid
                and j["status"] in ACTIVE_STATUSES):
            return j
    return None


def find_published_for_clip(project_id: str, uid: str) -> dict | None:
    for j in all_jobs():
        if (j.get("project_id") == project_id and j.get("uid") == uid
                and j["status"] == "published"):
            return j
    return None


def cancel_job(job_id: str) -> bool:
    j = get_job(job_id)
    if not j or j["status"] not in ("waiting", "scheduled", "failed"):
        return False
    update(job_id, status="cancelled",
           finished_at=time.strftime("%Y-%m-%d %H:%M:%S"))
    return True


def retry_job(job_id: str) -> bool:
    j = get_job(job_id)
    if not j or j["status"] != "failed":
        return False
    update(job_id, status="scheduled" if j.get("when") else "waiting",
           error="", progress=0.0)
    return True


def due_jobs(now=None) -> list[dict]:
    """Scheduled jobs whose local time has arrived."""
    now = now or time.localtime()
    now_iso = time.strftime("%Y-%m-%dT%H:%M", now)
    out = []
    for j in all_jobs():
        if j["status"] != "scheduled" or not j.get("when"):
            continue
        try:
            when = j["when"].replace(" ", "T")[:16]
            if when <= now_iso:
                out.append(j)
        except (ValueError, AttributeError):
            continue
    return out


def new_job_id() -> str:
    return uuid.uuid4().hex[:12]
