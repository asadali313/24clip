"""Publishing dashboard v2: real upload management.

- Thumbnails (local preview before upload, YouTube's after)
- Per-job live progress (bytes from the resumable session) and processing
  status straight from the YouTube API
- Auto-refresh: scheduler heartbeat + recovery of jobs interrupted by an app
  restart (they are requeued honestly, never marked successful)
"""
from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QMessageBox, QProgressBar, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ... import config
from ...core import scheduler, youtube
from ...workers.jobs import ConnectChannelWorker, UploadWorker

STATUS_COLORS = {
    "waiting": "#8a93a3", "scheduled": "#f0b429", "uploading": "#4da3ff",
    "processing": "#c58fff", "published": "#4ade80", "failed": "#ff7b7b",
    "cancelled": "#666e7a",
}
THUMB_DIR = config.ROOT / "publishing" / "thumbs"


class _ThumbFetcher(QThread):
    """Downloads YouTube thumbnails to disk (small, cached, non-blocking)."""
    done = Signal()

    def __init__(self, jobs: list[dict]):
        super().__init__()
        self.jobs = jobs

    def run(self) -> None:
        import urllib.request
        THUMB_DIR.mkdir(parents=True, exist_ok=True)
        for j in self.jobs:
            url = j.get("thumbnail", "")
            vid = j.get("video_id", "")
            if not url or not vid:
                continue
            dest = THUMB_DIR / f"{vid}.jpg"
            if dest.exists() and dest.stat().st_size > 500:
                continue
            try:
                req = urllib.request.Request(url, headers={
                    "User-Agent": "Mozilla/5.0 (AI Clip Generator)"})
                data = urllib.request.urlopen(req, timeout=15).read()
                dest.write_bytes(data)
            except Exception:              # thumbnails are cosmetic
                continue
        self.done.emit()


class PublishingScreen(QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        self.connect_worker: ConnectChannelWorker | None = None
        self.upload_workers: dict[str, UploadWorker] = {}
        self.thumb_fetcher: _ThumbFetcher | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(32, 24, 32, 20)
        root.setSpacing(12)

        title = QLabel("Publishing")
        title.setObjectName("h1")
        root.addWidget(title)

        self.lbl_channels = QLabel("No YouTube channels connected.")
        self.lbl_channels.setWordWrap(True)
        root.addWidget(self.lbl_channels)
        ch_row = QHBoxLayout()
        self.btn_connect = QPushButton("Connect channel")
        self.btn_connect.setObjectName("primary")
        self.btn_connect.clicked.connect(self._connect)
        self.btn_disconnect = QPushButton("Disconnect selected")
        self.btn_disconnect.clicked.connect(self._disconnect)
        self.btn_refresh = QPushButton("Refresh now")
        self.btn_refresh.clicked.connect(self.on_show)
        ch_row.addWidget(self.btn_connect)
        ch_row.addWidget(self.btn_disconnect)
        ch_row.addWidget(self.btn_refresh)
        ch_row.addStretch(1)
        root.addLayout(ch_row)
        self.cmb_default = _ChannelCombo()
        root.addWidget(self.cmb_default)

        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels(
            ["", "Title", "Channel", "When", "Status", "Progress",
             "Video ID", "Link", "Actions"])
        self.table.setColumnWidth(0, 84)      # thumbnail
        self.table.setColumnWidth(1, 250)
        self.table.setColumnWidth(2, 140)
        self.table.setColumnWidth(3, 130)
        self.table.setColumnWidth(4, 100)
        self.table.setColumnWidth(5, 130)
        self.table.setColumnWidth(6, 130)
        self.table.setColumnWidth(7, 110)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(self.table.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(self.table.EditTrigger.NoEditTriggers)
        self.table.setColumnHeight = None
        root.addWidget(self.table, 1)

        self.lbl_status = QLabel("")
        self.lbl_status.setObjectName("muted")
        self.lbl_status.setWordWrap(True)
        root.addWidget(self.lbl_status)

        # heartbeats
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._heartbeat)
        self.timer.start(15000)

    # ------------------------------------------------------------------
    def on_show(self) -> None:
        chans = youtube.list_channels()
        if chans:
            lines = [f"• {c['title']}  ({c.get('subscribers', '?')} subscribers)"
                     for c in chans]
            self.lbl_channels.setText("Connected channels:\n" + "\n".join(lines))
        else:
            self.lbl_channels.setText(
                "No YouTube channels connected yet.\n"
                + youtube.setup_instructions())
        self.cmb_default.reload(chans)
        self._refresh_jobs()
        self._recover_interrupted()

    # ------------------------------------------------------------------
    def _connect(self) -> None:
        secret = youtube.find_client_secret()
        if secret is None:
            QMessageBox.warning(self, "OAuth client required",
                                youtube.client_secret_error()
                                + "\n\n" + youtube.setup_instructions())
            return
        self.btn_connect.setEnabled(False)
        self.lbl_status.setText(
            "Opening Google sign-in in your browser — choose your account "
            "and grant access…")
        self.connect_worker = ConnectChannelWorker()
        self.connect_worker.connected.connect(self._connected)
        self.connect_worker.failed.connect(self._connect_failed)
        self.connect_worker.start()

    def _connected(self, info: dict) -> None:
        self.btn_connect.setEnabled(True)
        self.lbl_status.setText(f"Connected: {info.get('title')}")
        self.lbl_status.setStyleSheet("")
        self.on_show()

    def _connect_failed(self, err: str) -> None:
        self.btn_connect.setEnabled(True)
        self.lbl_status.setText(f"Connection failed: {err}")
        self.lbl_status.setStyleSheet("color: #ff7b7b;")

    def _disconnect(self) -> None:
        ch = self.cmb_default.current_channel_id()
        if not ch:
            return
        if not self.main.confirm(
                "Disconnect channel",
                "Disconnect this channel? Scheduled jobs for it will fail "
                "until you reconnect."):
            return
        youtube.disconnect_channel(ch)
        self.on_show()

    # ------------------------------------------------------------------
    def _heartbeat(self) -> None:
        """Timer tick: process due jobs + refresh visible states."""
        self._process_due()
        self._refresh_jobs()

    def _process_due(self) -> None:
        for job in scheduler.due_jobs():
            self._start_upload(job)

    def _recover_interrupted(self) -> None:
        """Requeue jobs whose worker died (app restart mid-upload/process).

        An 'uploading' job with no live worker can only come from a restart
        (the resumable session died with the process), so it is requeued
        honestly with a note - never marked successful."""
        changed = False
        for j in scheduler.all_jobs():
            if j["status"] == "waiting" and j.get("when"):
                continue                       # scheduled: wait for its time
            if j["status"] == "uploading" and j["id"] not in self.upload_workers:
                scheduler.update(j["id"], status="waiting", progress=0.0,
                                 error="interrupted by app restart - requeued")
                changed = True
                j = scheduler.get_job(j["id"])
            if j["status"] in ("waiting", "processing") \
                    and j["id"] not in self.upload_workers:
                self._start_upload(j)
        if changed:
            self._refresh_jobs()

    def _start_upload(self, job: dict) -> None:
        if job["id"] in self.upload_workers \
                and self.upload_workers[job["id"]].isRunning():
            return
        w = UploadWorker(job)
        w.upload_progress.connect(self._upload_progress)
        w.status_changed.connect(lambda jid, st: self._refresh_jobs())
        w.finished_ok.connect(self._upload_done)
        w.failed.connect(self._upload_failed)
        self.upload_workers[job["id"]] = w
        w.start()

    def enqueue(self, job_payload: dict) -> None:
        """Create a durable job from the wizard payload and start it."""
        from ...core import scheduler as sch
        job = sch.PublishJob(
            id=sch.new_job_id(),
            project_id=job_payload.get("project_id", ""),
            uid=job_payload.get("uid", ""),
            file=job_payload.get("file", ""),
            channel_id=job_payload["channel_id"],
            channel_title=job_payload["channel_title"],
            title=job_payload["metadata"].get("title", ""),
            when=job_payload.get("when", ""),
            metadata=job_payload.get("metadata", {}),
            captions_srt=job_payload.get("captions_srt", ""),
            privacy_status=job_payload["metadata"].get("privacy_status",
                                                       "public"),
        )
        scheduler.add_job(job)
        if job_payload.get("when"):
            self.lbl_status.setText(
                f"Scheduled for {job.when} on {job.channel_title}.")
        else:
            stored = scheduler.get_job(job.id)
            self._start_upload(stored)
        self._refresh_jobs()

    # ------------------------------------------------------------------
    def _upload_progress(self, job_id: str, frac: float) -> None:
        self.lbl_status.setText(f"Uploading… {frac * 100:.0f}%")
        # update the progress cell live without a full table rebuild
        row = self._row_for_job(job_id)
        if row is not None:
            w = self.table.cellWidget(row, 5)
            if isinstance(w, QProgressBar):
                w.setValue(int(frac * 100))
                w.setFormat(f"{frac * 100:.0f}%")

    def _upload_done(self, job_id: str, url: str) -> None:
        self.lbl_status.setText(
            f"Upload confirmed by YouTube: {url} (processing…)")
        self.lbl_status.setStyleSheet("")
        self._refresh_jobs()

    def _upload_failed(self, job_id: str, err: str) -> None:
        self.lbl_status.setText(f"Upload failed: {err}")
        self.lbl_status.setStyleSheet("color: #ff7b7b;")
        self._refresh_jobs()

    def _row_for_job(self, job_id: str) -> int | None:
        for r in range(self.table.rowCount()):
            it = self.table.item(r, 1)
            if it and it.data(Qt.UserRole) == job_id:
                return r
        return None

    # ------------------------------------------------------------------
    def _refresh_jobs(self) -> None:
        jobs = scheduler.all_jobs()
        self.table.setRowCount(len(jobs))
        need_thumbs: list[dict] = []
        for i, j in enumerate(jobs):
            # -- thumbnail -----------------------------------------------
            pix = self._thumb_for(j)
            lbl = QLabel()
            lbl.setFixedSize(80, 45)
            lbl.setAlignment(Qt.AlignCenter)
            if pix and not pix.isNull():
                lbl.setPixmap(pix.scaled(80, 45, Qt.KeepAspectRatio,
                                         Qt.SmoothTransformation))
            else:
                lbl.setText("—")
            self.table.setCellWidget(i, 0, lbl)
            # -- columns ---------------------------------------------------
            title_item = QTableWidgetItem(j.get("title") or
                                          Path(j.get("file", "?")).name)
            title_item.setData(Qt.UserRole, j["id"])
            self.table.setItem(i, 1, title_item)
            self.table.setItem(i, 2, QTableWidgetItem(
                j.get("channel_title", "")))
            self.table.setItem(i, 3, QTableWidgetItem(
                j.get("when") or j.get("upload_started_at")
                or j.get("created_at", "")))
            status_item = QTableWidgetItem(j["status"])
            status_item.setForeground(Qt.white)
            status_item.setBackground(Qt.transparent)
            self.table.setItem(i, 4, QTableWidgetItem(
                f"{j['status']}"
                + (f" ({j['processing_status']})"
                   if j.get("processing_status") and j["status"]
                   == "processing" else "")))
            prog = QProgressBar()
            prog.setRange(0, 100)
            if j["status"] == "published":
                prog.setValue(100)
                prog.setFormat("done")
            elif j["status"] == "processing":
                prog.setValue(100)
                prog.setFormat("processing")
            else:
                prog.setValue(int((j.get("progress") or 0) * 100))
                prog.setFormat(f"{(j.get('progress') or 0) * 100:.0f}%")
            self.table.setCellWidget(i, 5, prog)
            self.table.setItem(i, 6, QTableWidgetItem(j.get("video_id", "")))
            link = j.get("video_url", "")
            self.table.setItem(i, 7, QTableWidgetItem(link))
            # actions
            acts = QWidget()
            ah = QHBoxLayout(acts)
            ah.setContentsMargins(2, 2, 2, 2)
            if j["status"] == "failed":
                b = QPushButton("Retry")
                b.clicked.connect(lambda _=False, jid=j["id"]:
                                  self._retry(jid))
                ah.addWidget(b)
            if j["status"] in ("waiting", "scheduled", "failed"):
                b = QPushButton("Cancel")
                b.clicked.connect(lambda _=False, jid=j["id"]:
                                  self._cancel(jid))
                ah.addWidget(b)
            if j["status"] == "published" and link:
                b = QPushButton("Open")
                b.clicked.connect(lambda _=False, u=link: self._open(u))
                ah.addWidget(b)
            ah.addStretch(1)
            self.table.setCellWidget(i, 8, acts)
            # color the status text
            st_item = self.table.item(i, 4)
            from PySide6.QtGui import QColor
            st_item.setForeground(QColor(STATUS_COLORS.get(j["status"],
                                                           "#e8eaed")))
            if j.get("thumbnail", "").startswith("http"):
                need_thumbs.append(j)
        # fetch YouTube thumbnails for published/processing jobs
        if need_thumbs and (self.thumb_fetcher is None
                            or not self.thumb_fetcher.isRunning()):
            self.thumb_fetcher = _ThumbFetcher(need_thumbs)
            self.thumb_fetcher.done.connect(self._refresh_jobs)
            self.thumb_fetcher.start()

    def _thumb_for(self, job: dict) -> QPixmap | None:
        """Local render thumb before upload; YouTube thumb after."""
        vid = job.get("video_id", "")
        if vid:
            cached = THUMB_DIR / f"{vid}.jpg"
            if cached.exists():
                return QPixmap(str(cached))
        if job.get("project_id") and job.get("uid"):
            from ... import project_store
            pdir = config.PROJECTS_DIR / job["project_id"]
            if pdir.exists():
                proj = project_store.Project(pdir)
                cands = proj.data.get("candidates", [])
                for i, c in enumerate(cands):
                    if c.get("uid") == job.get("uid"):
                        t = proj.path("candidates", f"thumb_{i:02d}.jpg")
                        if t.exists():
                            return QPixmap(str(t))
        return None

    def _retry(self, job_id: str) -> None:
        scheduler.retry_job(job_id)
        job = scheduler.get_job(job_id)
        if job and not job.get("when"):
            self._start_upload(job)
        self._refresh_jobs()

    def _cancel(self, job_id: str) -> None:
        scheduler.cancel_job(job_id)
        self._refresh_jobs()

    def _open(self, url: str) -> None:
        import os
        os.startfile(url)


class _ChannelCombo(QComboBox):
    def __init__(self):
        super().__init__()
        self._ids: list[str] = []

    def reload(self, channels: list[dict]) -> None:
        current = self.current_channel_id()
        self.blockSignals(True)
        self.clear()
        self._ids = []
        if channels:
            self.addItem("Default publish target:", "")
            for c in channels:
                self.addItem(c.get("title", "?"), c.get("channel_id"))
                self._ids.append(c.get("channel_id", ""))
            if current:
                i = self.findData(current)
                if i >= 0:
                    self.setCurrentIndex(i)
                else:
                    self.setCurrentIndex(1)
        self.blockSignals(False)

    def current_channel_id(self) -> str:
        return self.currentData() or ""
