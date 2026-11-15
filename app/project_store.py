"""Project storage: one folder per analyzed video, with processing manifest."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from . import config

SUBDIRS = [
    "original_video", "audio", "transcripts", "analysis",
    "candidates", "clips", "captions", "voiceovers", "exports", "logs",
]


class Project:
    """A single analysis project and its on-disk layout."""

    def __init__(self, project_dir: Path):
        self.dir = project_dir
        self.json_path = self.dir / "project.json"
        self._data: dict[str, Any] = {}
        self.load()

    # -- creation ----------------------------------------------------------
    @classmethod
    def create(cls, source_video: Path) -> "Project":
        n = 1
        while (config.PROJECTS_DIR / f"project_{n:03d}").exists():
            n += 1
        pdir = config.PROJECTS_DIR / f"project_{n:03d}"
        pdir.mkdir(parents=True)
        for sub in SUBDIRS:
            (pdir / sub).mkdir()
        (pdir / "logs" / "app.log").parent.mkdir(exist_ok=True)
        proj = cls(pdir)
        proj._data = {
            "id": pdir.name,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "source_video": str(source_video),
            "source_name": source_video.name,
            "source_size_bytes": source_video.stat().st_size,
            "status": "created",
            "stages": {},          # stage name -> {state, error, ts}
            "metadata": {},        # video metadata from ffprobe
            "settings_snapshot": {},
            "candidates": [],
            "exports": [],
        }
        proj.save()
        # Record the source path only; never copy/move the original video.
        return proj

    # -- persistence ---------------------------------------------------------
    def load(self) -> None:
        if self.json_path.exists():
            try:
                self._data = json.loads(self.json_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self._data = {}

    def save(self) -> None:
        self.json_path.write_text(
            json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8")

    # -- accessors -----------------------------------------------------------
    @property
    def data(self) -> dict[str, Any]:
        return self._data

    @property
    def id(self) -> str:
        return self._data.get("id", self.dir.name)

    @property
    def source_video(self) -> Path | None:
        p = self._data.get("source_video")
        return Path(p) if p else None

    def path(self, sub: str, name: str = "") -> Path:
        d = self.dir / sub
        d.mkdir(parents=True, exist_ok=True)
        return d / name if name else d

    def set_stage(self, stage: str, state: str, error: str = "") -> None:
        self._data.setdefault("stages", {})[stage] = {
            "state": state, "error": error, "ts": time.strftime("%H:%M:%S"),
        }
        self._data["status"] = stage
        self.save()

    def stage_state(self, stage: str) -> str:
        return self._data.get("stages", {}).get(stage, {}).get("state", "pending")

    def add_log(self, text: str) -> None:
        logf = self.dir / "logs" / "app.log"
        with open(logf, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {text}\n")


def recent_projects(limit: int = 12) -> list[dict[str, Any]]:
    """Summary rows for the dashboard, newest first."""
    rows = []
    if not config.PROJECTS_DIR.exists():
        return rows
    dirs = sorted(config.PROJECTS_DIR.glob("project_*"), reverse=True)
    for d in dirs[: limit * 3]:
        jp = d / "project.json"
        if not jp.exists():
            continue
        try:
            data = json.loads(jp.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        n_clips = len(data.get("candidates", []))
        rows.append({
            "id": data.get("id", d.name),
            "name": data.get("source_name", d.name),
            "created": data.get("created_at", ""),
            "status": data.get("status", "unknown"),
            "clips": n_clips,
            "dir": str(d),
        })
        if len(rows) >= limit:
            break
    return rows


def global_stats() -> dict[str, int]:
    rows = recent_projects(limit=1000)
    return {
        "videos": len(rows),
        "clips": sum(r["clips"] for r in rows),
    }
