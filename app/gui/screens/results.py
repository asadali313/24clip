"""Results screen: the final rendered clips - nothing else.

Per clip: video preview, number, original timestamp, duration, engagement
score, Download MP4. Plus Regenerate / Delete per clip and Download All.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QFileDialog, QFrame, QHBoxLayout, QLabel, QMessageBox, QPushButton,
    QScrollArea, QVBoxLayout, QWidget,
)


def _fmt(t: float) -> str:
    s = int(t)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 \
        else f"{s // 60}:{s % 60:02d}"


class ResultCard(QFrame):
    def __init__(self, main, cand: dict, index: int, thumb_path):
        super().__init__()
        self.setObjectName("card")
        self.main = main
        self.cand = cand
        self.index = index

        h = QHBoxLayout(self)
        h.setContentsMargins(14, 14, 14, 14)
        h.setSpacing(14)

        # thumbnail / preview surface
        self.thumb = QLabel()
        self.thumb.setFixedSize(120, 68)
        self.thumb.setStyleSheet("background:#0d0e11; border-radius:6px;")
        self.thumb.setScaledContents(True)
        if thumb_path and Path(thumb_path).exists():
            pm = QPixmap(str(thumb_path))
            if not pm.isNull():
                self.thumb.setPixmap(pm)
        h.addWidget(self.thumb)

        # info
        info = QVBoxLayout()
        head = QHBoxLayout()
        n_badge = f"Clip {index + 1}"
        title = QLabel(f"{n_badge}   ·   from {_fmt(cand['start'])} in the "
                       f"original   ·   {cand['duration']:.0f}s")
        title.setStyleSheet("font-weight: 600; font-size: 14px;")
        score = QLabel(f"{cand['score']:.0f}/100")
        color = "#4ade80" if cand["score"] >= 85 else \
            "#f0b429" if cand["score"] >= 70 else "#8a93a3"
        score.setStyleSheet(f"font-weight: 700; color: {color};")
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(score)
        info.addLayout(head)

        if cand.get("quality") == "fair":
            warn = QLabel("fair quality - below your strong-moment threshold")
            warn.setStyleSheet("color: #f0b429;")
            warn.setWordWrap(True)
            info.addWidget(warn)

        qc = cand.get("qc")
        if qc and isinstance(qc, dict):
            from ...core.quality import summarize
            lvl = {"pass": "#4ade80", "warn": "#f0b429",
                   "fail": "#ff7b7b"}.get(qc.get("status", ""), "#8a93a3")
            q = QLabel(f"QC [{qc.get('status', '?')}]: {summarize(qc)}")
            q.setStyleSheet(f"color: {lvl};")
            q.setWordWrap(True)
            info.addWidget(q)

        txt = QLabel(cand.get("text", "")[:200] +
                     ("…" if len(cand.get("text", "")) > 200 else ""))
        txt.setWordWrap(True)
        txt.setStyleSheet("color:#cfd6e0;")
        info.addWidget(txt)

        file_lbl = QLabel(f"file: exports/{cand.get('export_file', '?')}")
        file_lbl.setObjectName("muted")
        info.addWidget(file_lbl)
        h.addLayout(info, 1)

        # actions
        actions = QVBoxLayout()
        btn_dl = QPushButton("Download MP4")
        btn_dl.setObjectName("primary")
        btn_dl.clicked.connect(self._download)
        btn_play = QPushButton("Preview")
        btn_play.clicked.connect(self._preview)
        btn_yt = QPushButton("Upload to YouTube")
        btn_yt.clicked.connect(self._publish)
        btn_adj = QPushButton("Adjust…")
        btn_adj.clicked.connect(
            lambda: self.main.open_editor(self.cand))
        btn_re = QPushButton("Regenerate")
        btn_re.clicked.connect(self._regen)
        btn_del = QPushButton("Delete")
        btn_del.setObjectName("reject")
        btn_del.clicked.connect(self._delete)
        for b in (btn_dl, btn_play, btn_yt, btn_adj, btn_re, btn_del):
            actions.addWidget(b)
        actions.addStretch(1)
        h.addLayout(actions)

    # ------------------------------------------------------------------
    def _file(self) -> Path | None:
        name = self.cand.get("export_file")
        if not name or not self.main.project:
            return None
        p = self.main.project.path("exports", name)
        return p if p.exists() else None

    def _download(self) -> None:
        src = self._file()
        if not src:
            QMessageBox.warning(self, "Missing", "Rendered file not found.")
            return
        dst, _ = QFileDialog.getSaveFileName(
            self, "Save clip", src.name, "MP4 video (*.mp4)")
        if dst:
            shutil.copyfile(src, dst)

    def _preview(self) -> None:
        src = self._file()
        if src:
            self.main.screens["results"].play(src)

    def _publish(self) -> None:
        """Open the publishing wizard: details -> elements -> checks ->
        visibility -> publish. Metadata auto-generated (or saved) per clip."""
        src = self._file()
        if not src:
            QMessageBox.warning(self, "Missing", "Rendered file not found.")
            return
        from ...core import youtube
        from .dialogs.publish_wizard import PublishWizard
        channels = youtube.list_channels()
        if not channels:
            QMessageBox.information(
                self, "No channel connected",
                "Connect a YouTube channel on the Publishing screen first.\n\n"
                + youtube.setup_instructions())
            self.main.go("publishing")
            return
        wiz = PublishWizard(self, self.main.project, self.cand, src,
                            channels)
        if wiz.exec() == PublishWizard.Accepted:
            payload = wiz.job_payload()
            self.main.screens["publishing"].enqueue(payload)
            self.main.go("publishing")

    def _regen(self) -> None:
        self.main.screens["results"].regenerate(self.cand)

    def _delete(self) -> None:
        self.main.screens["results"].delete_clip(self.cand)


class ResultsScreen(QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        self.player = QMediaPlayer()
        self.audio_out = QAudioOutput()
        self.player.setAudioOutput(self.audio_out)
        self.video = QVideoWidget()
        self.video.setFixedHeight(230)
        self.video.setStyleSheet("background:#0d0e11; border-radius:10px;")
        self.player.setVideoOutput(self.video)

        root = QVBoxLayout(self)
        root.setContentsMargins(32, 24, 32, 20)
        root.setSpacing(10)

        title = QLabel("Your Clips")
        title.setObjectName("h1")
        self.lbl_summary = QLabel("")
        self.lbl_summary.setObjectName("muted")
        self.lbl_summary.setWordWrap(True)
        root.addWidget(title)
        root.addWidget(self.lbl_summary)
        root.addWidget(self.video)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        self.cards = QVBoxLayout(inner)
        self.cards.setSpacing(12)
        self.cards.addStretch(1)
        scroll.setWidget(inner)
        root.addWidget(scroll, 1)

        row = QHBoxLayout()
        self.btn_all = QPushButton("Download all clips")
        self.btn_all.setObjectName("primary")
        self.btn_all.clicked.connect(self._download_all)
        self.btn_folder = QPushButton("Open folder")
        self.btn_folder.clicked.connect(self._open_folder)
        self.btn_new = QPushButton("Process another video")
        self.btn_new.clicked.connect(lambda: self.main.go("upload"))
        row.addWidget(self.btn_all)
        row.addWidget(self.btn_folder)
        row.addWidget(self.btn_new)
        row.addStretch(1)
        root.addLayout(row)

    # ------------------------------------------------------------------
    def play(self, path: Path) -> None:
        self.player.stop()
        self.player.setSource(QUrl.fromLocalFile(str(path)))
        self.player.play()

    def on_show(self) -> None:
        proj = self.main.project
        if not proj:
            self.lbl_summary.setText("No project loaded.")
            return
        while self.cards.count() > 1:
            item = self.cards.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        cands = proj.data.get("candidates", [])
        note = proj.data.get("selection_note", "")
        parts = [f"{len(cands)} clips ready in {proj.path('exports')}"]
        if note:
            parts.append(note)
        self.lbl_summary.setText("   ·   ".join(parts))
        if not cands:
            empty = QLabel("No clips. Process a video first.")
            empty.setObjectName("muted")
            self.cards.insertWidget(0, empty)
            return
        for i, cand in enumerate(cands):
            thumb = proj.path("candidates", f"thumb_{i:02d}.jpg")
            self.cards.insertWidget(self.cards.count() - 1,
                                    ResultCard(self.main, cand, i,
                                               thumb if thumb.exists() else None))

    # ------------------------------------------------------------------
    def regenerate(self, cand: dict) -> None:
        proj = self.main.project
        if not proj:
            return
        from ...workers.jobs import RenderOneWorker
        from ... import config
        old = cand.get("export_file")
        if old and proj.path("exports", old).exists():
            proj.path("exports", old).unlink()
        self.lbl_summary.setText(f"Regenerating clip {cand.get('uid')}…")
        self.btn_all.setEnabled(False)
        self.main.set_busy(True)
        worker = RenderOneWorker(proj, cand, self.main.settings,
                                 proj.path("exports", old or
                                           f"regen_{cand.get('uid')}.mp4"))
        worker.done.connect(lambda p, m: self._regen_done(cand, p, m))
        worker.failed.connect(self._regen_failed)
        worker.start()
        self._regen_worker = worker

    def _regen_done(self, cand: dict, path: str, meta: dict) -> None:
        cand["export_file"] = Path(path).name
        self.main.project.save()
        self.main.set_busy(False)
        self.btn_all.setEnabled(True)
        self.on_show()
        self.play(Path(path))

    def _regen_failed(self, err: str) -> None:
        self.main.set_busy(False)
        self.btn_all.setEnabled(True)
        QMessageBox.warning(self, "Regenerate failed", err)
        self.on_show()

    def delete_clip(self, cand: dict) -> None:
        proj = self.main.project
        if not proj:
            return
        if not self.main.confirm(
                "Delete clip",
                f"Delete clip {cand.get('uid')} and its MP4 file? "
                "The original video is not touched."):
            return
        name = cand.get("export_file")
        if name:
            f = proj.path("exports", name)
            f.unlink(missing_ok=True)
        proj.data["candidates"] = [c for c in proj.data.get("candidates", [])
                                   if c.get("uid") != cand.get("uid")]
        # renumber export files sequentially
        for i, c in enumerate(proj.data["candidates"]):
            old = c.get("export_file")
            new = f"short_{i + 1:03d}.mp4"
            if old and old != new:
                src = proj.path("exports", old)
                if src.exists():
                    src.replace(proj.path("exports", new))
                c["export_file"] = new
            elif not old:
                c["export_file"] = new
        proj.save()
        self.on_show()

    def _download_all(self) -> None:
        proj = self.main.project
        if not proj:
            return
        files = [proj.path("exports", c.get("export_file", ""))
                 for c in proj.data.get("candidates", [])]
        files = [f for f in files if f.exists()]
        if not files:
            QMessageBox.information(self, "Nothing to download",
                                    "No rendered clips found.")
            return
        dst_dir = QFileDialog.getExistingDirectory(
            self, "Choose folder for all clips")
        if not dst_dir:
            return
        for f in files:
            shutil.copyfile(f, Path(dst_dir) / f.name)
        QMessageBox.information(
            self, "Downloaded",
            f"{len(files)} MP4 files copied to:\n{dst_dir}")

    def _open_folder(self) -> None:
        import os
        if self.main.project:
            p = self.main.project.path("exports")
            p.mkdir(parents=True, exist_ok=True)
            os.startfile(str(p))
