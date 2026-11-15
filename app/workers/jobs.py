"""Background jobs (QThread). The UI never blocks on heavy processing."""
from __future__ import annotations

import time
import traceback
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from .. import config, pipeline, project_store
from ..core import export as export_mod


class AnalysisWorker(QThread):
    """Runs the full analysis pipeline for a project."""
    stage_progress = Signal(str, float, str)   # stage, 0..1, message
    finished_ok = Signal()
    failed = Signal(str)

    def __init__(self, project: project_store.Project,
                 settings: config.Settings, parent=None):
        super().__init__(parent)
        self.project = project
        self.settings = settings
        self._pipeline: pipeline.Pipeline | None = None

    def cancel(self) -> None:
        if self._pipeline:
            self._pipeline.cancel()

    def run(self) -> None:
        try:
            self._pipeline = pipeline.Pipeline(
                self.project, self.settings,
                progress=lambda s, f, m: self.stage_progress.emit(s, f, m))
            self._pipeline.run()
            self.finished_ok.emit()
        except pipeline.PipelineError as e:
            self.failed.emit(str(e))
        except Exception as e:                          # noqa: BLE001
            self.project.add_log("WORKER CRASH:\n" + traceback.format_exc())
            self.failed.emit(f"{type(e).__name__}: {e}")


class ExportWorker(QThread):
    """Exports a list of candidate dicts."""
    clip_progress = Signal(int, int, float, str)   # idx, total, frac, msg
    export_done = Signal(list, list)               # ok, failed
    export_error = Signal(str)

    def __init__(self, project: project_store.Project,
                 candidates: list[dict], settings: config.Settings, parent=None):
        super().__init__(parent)
        self.project = project
        self.candidates = candidates
        self.settings = settings

    def run(self) -> None:
        try:
            ok, failed = export_mod.export_many(
                self.project, self.candidates, self.settings,
                progress=lambda i, n, f, m: self.clip_progress.emit(
                    i, n, f, m))
            self.export_done.emit(ok, failed)
        except Exception as e:                          # noqa: BLE001
            self.project.add_log("EXPORT CRASH:\n" + traceback.format_exc())
            self.export_error.emit(f"{type(e).__name__}: {e}")


class RenderOneWorker(QThread):
    """Re-render a single clip from the editor screen."""
    progress = Signal(float, str)
    done = Signal(str, dict)        # out path, metadata
    failed = Signal(str)

    def __init__(self, project: project_store.Project, cand: dict,
                 settings: config.Settings, out_path, parent=None):
        super().__init__(parent)
        self.project = project
        self.cand = cand
        self.settings = settings
        self.out_path = out_path

    def run(self) -> None:
        try:
            meta = export_mod.export_clip(
                self.project, self.cand, self.settings, self.out_path.name,
                progress=lambda f, m: self.progress.emit(f, m))
            self.done.emit(str(self.out_path), meta)
        except Exception as e:                          # noqa: BLE001
            self.project.add_log("RENDER CRASH:\n" + traceback.format_exc())
            self.failed.emit(f"{type(e).__name__}: {e}")


class ConnectChannelWorker(QThread):
    """Runs the Google OAuth consent flow (opens the browser) off the UI."""
    connected = Signal(dict)
    failed = Signal(str)

    def run(self) -> None:
        try:
            from ..core import youtube
            info = youtube.connect_channel_local()
            self.connected.emit(info)
        except Exception as e:                          # noqa: BLE001
            self.failed.emit(str(e))


class UploadWorker(QThread):
    """Real upload lifecycle: waiting -> uploading -> processing -> published.

    Every state change is persisted to the job store; progress comes from the
    resumable upload session; processing status is polled from the YouTube
    API. No simulated success: 'published' requires YouTube to confirm.
    """
    upload_progress = Signal(str, float)            # job_id, 0..1
    status_changed = Signal(str, str)               # job_id, new status
    finished_ok = Signal(str, str)                  # job_id, video url
    failed = Signal(str, str)                       # job_id, error

    def __init__(self, job: dict, parent=None):
        super().__init__(parent)
        self.job = job

    def run(self) -> None:
        from ..core import scheduler, youtube
        job_id = self.job["id"]
        try:
            # duplicate protection: another active job for the same clip?
            active = scheduler.find_active_for_clip(
                self.job.get("project_id", ""), self.job.get("uid", ""))
            if active and active["id"] != job_id:
                raise youtube.YouTubeError(
                    "This clip already has an active upload job. Check the "
                    "Publishing dashboard (or cancel that job first).")
            if self.job.get("video_id"):
                # retry after upload succeeded but processing was pending:
                # skip straight to processing - never upload twice
                scheduler.update(job_id, status="processing", progress=1.0)
                self.status_changed.emit(job_id, "processing")
            else:
                file = Path(self.job["file"])
                if not file.exists():
                    raise youtube.YouTubeError(
                        f"Clip file missing: {file} - regenerate the clip "
                        "first.")
                scheduler.update(job_id, status="uploading",
                                 attempts=self.job.get("attempts", 0) + 1,
                                 upload_started_at=time.strftime(
                                     "%Y-%m-%d %H:%M:%S"),
                                 progress=0.0,
                                 error="")
                self.status_changed.emit(job_id, "uploading")
                result = youtube.upload_video(
                    self.job["channel_id"], file, self.job.get("metadata", {}),
                    progress_cb=lambda f, _sz: (
                        scheduler.update(job_id, progress=f or 0.0),
                        self.upload_progress.emit(job_id, f or 0.0)))
                scheduler.update(job_id, video_id=result["video_id"],
                                 video_url=result["url"],
                                 privacy_status=result.get("privacy", ""),
                                 progress=1.0)
                self.finished_ok.emit(job_id, result["url"])

            # ---- upload confirmed: now track REAL processing status -------
            vid = self.job.get("video_id") or scheduler.get_job(job_id)["video_id"]
            scheduler.update(job_id, status="processing")
            self.status_changed.emit(job_id, "processing")

            def on_status(st: dict) -> None:
                scheduler.update(
                    job_id, processing_status=st.get("processing_status", ""),
                    upload_status=st.get("upload_status", ""),
                    thumbnail=st.get("thumbnail",
                                     self.job.get("thumbnail", "")))

            final = youtube.wait_for_processing(self.job["channel_id"], vid,
                                                progress_cb=on_status)
            us = final.get("upload_status", "")
            if us in ("rejected", "failed", "deleted"):
                reason = final.get("failure_reason") or \
                    final.get("rejection_reason") or us
                raise youtube.YouTubeError(
                    f"YouTube rejected the video ({reason}).")
            # optional caption track (video elements) - non-fatal on error
            srt = (self.job.get("captions_srt")
                   or self.job.get("metadata", {}).get("captions_srt"))
            if srt and Path(srt).exists():
                try:
                    youtube.upload_captions(self.job["channel_id"], vid,
                                            Path(srt))
                except Exception as e:      # noqa: BLE001
                    scheduler.update(
                        job_id, error=f"note: captions upload failed ({e})")
            # processed: honour the privacy setting - the video is live per
            # the user's chosen visibility (or scheduled via publishAt)
            scheduler.update(
                job_id, status="published",
                processing_status=final.get("processing_status", "succeeded"),
                upload_status=us,
                finished_at=time.strftime("%Y-%m-%d %H:%M:%S"))
            self.status_changed.emit(job_id, "published")
        except youtube.YouTubeError as e:
            scheduler.update(job_id, status="failed", error=str(e),
                             finished_at=time.strftime("%Y-%m-%d %H:%M:%S"))
            self.failed.emit(job_id, str(e))
        except Exception as e:                          # noqa: BLE001
            err = youtube.map_api_error(e)
            scheduler.update(job_id, status="failed", error=err,
                             finished_at=time.strftime("%Y-%m-%d %H:%M:%S"))
            self.failed.emit(job_id, err)


class FullVideoWorker(QThread):
    """Full-video customization pipeline (cancellable)."""
    stage = Signal(str)                      # message
    finished_ok = Signal(list)               # export metadata dicts
    failed = Signal(str)

    def __init__(self, project, settings, parent=None):
        super().__init__(parent)
        self.project = project
        self.settings = settings
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        from ..core import fullvideo
        try:
            def cancel_check():
                if self._cancel:
                    raise fullvideo.FullVideoError(
                        "Cancelled by user - partial renders are deleted.")

            outputs = fullvideo.process_full_video(
                self.project, self.settings,
                progress_cb=lambda f, m: self.stage.emit(m),
                cancel_check=cancel_check)
            self.finished_ok.emit(outputs)
        except Exception as e:                       # noqa: BLE001
            self.project.add_log("FULLVIDEO CRASH: " + traceback.format_exc())
            self.failed.emit(f"{type(e).__name__}: {e}")
