"""Dashboard: stats, upload entry point, recent projects."""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from ... import config, project_store
from ...core.ffmpeg_tools import FFmpegError, extract_metadata


def _stat_card(title: str, value: str) -> QFrame:
    f = QFrame()
    f.setObjectName("card")
    v = QVBoxLayout(f)
    v.setContentsMargins(18, 14, 18, 14)
    lbl_val = QLabel(value)
    lbl_val.setStyleSheet("font-size: 26px; font-weight: 700;")
    lbl_t = QLabel(title)
    lbl_t.setObjectName("muted")
    v.addWidget(lbl_val)
    v.addWidget(lbl_t)
    return f


class DashboardScreen(QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        root = QVBoxLayout(self)
        root.setContentsMargins(32, 28, 32, 24)
        root.setSpacing(18)

        title = QLabel("AI Clip Generator")
        title.setObjectName("h1")
        sub = QLabel("Turn long videos into ready-to-post vertical Shorts - "
                     "fully local, no paid services.")
        sub.setObjectName("muted")
        root.addWidget(title)
        root.addWidget(sub)

        # stats row
        stats_row = QHBoxLayout()
        self.card_videos = _stat_card("Videos processed", "0")
        self.card_clips = _stat_card("Clips generated", "0")
        self.card_status = _stat_card("Processing status", "Idle")
        stats_row.addWidget(self.card_videos)
        stats_row.addWidget(self.card_clips)
        stats_row.addWidget(self.card_status)
        root.addLayout(stats_row)

        # actions row
        actions = QHBoxLayout()
        btn_upload = QPushButton("Upload Video")
        btn_upload.setObjectName("primary")
        btn_upload.clicked.connect(self._pick_video)
        btn_settings = QPushButton("Default settings")
        btn_settings.clicked.connect(self._open_settings)
        actions.addWidget(btn_upload)
        actions.addWidget(btn_settings)
        actions.addStretch(1)
        root.addLayout(actions)

        # recent projects
        lbl = QLabel("Recent projects")
        lbl.setObjectName("h2")
        root.addWidget(lbl)
        self.recent = QListWidget()
        self.recent.itemDoubleClicked.connect(self._open_recent)
        root.addWidget(self.recent, 1)

    # ------------------------------------------------------------------
    def on_show(self) -> None:
        stats = project_store.global_stats()
        self.card_videos.findChild(QLabel).setText(str(stats["videos"]))
        labels = self.card_clips.findChildren(QLabel)
        if labels:
            labels[0].setText(str(stats["clips"]))
        self._refresh_recent()

    def _refresh_recent(self) -> None:
        self.recent.clear()
        for row in project_store.recent_projects():
            item = QListWidgetItem(
                f"{row['name']}   |   {row['created']}   |   "
                f"{row['status']}   |   {row['clips']} clips")
            item.setData(Qt.UserRole, row["dir"])
            self.recent.addItem(item)

    # ------------------------------------------------------------------
    def _pick_video(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose a video", "",
            "Video files (*.mp4 *.mkv *.mov *.avi *.webm *.m4v *.ts *.flv);;"
            "All files (*.*)")
        if path:
            self._load_video(Path(path))

    def _load_video(self, path: Path) -> None:
        try:
            md = extract_metadata(path)
        except (FFmpegError, OSError) as e:
            QMessageBox.critical(self, "Unsupported file",
                                 f"This file could not be opened as video:\n{e}")
            return
        if md.duration < 60:
            if not self.main.confirm(
                    "Short video",
                    f"This video is only {md.duration:.0f} seconds long.\n"
                    "Shorts discovery works best on videos of several "
                    "minutes or more. Continue anyway?"):
                return
        upload = self.main.screens["upload"]
        upload.set_video(path, md)
        self.main.go("upload")

    def _open_settings(self) -> None:
        """Open the settings screen as defaults for the next video."""
        self.main.go("settings")

    def _open_recent(self, item) -> None:
        pdir = Path(item.data(Qt.UserRole))
        proj = project_store.Project(pdir)
        if proj.data.get("status") == "ready":
            self.main.open_project(proj)
        else:
            QMessageBox.information(
                self, "Project not finished",
                f"This project's status is '{proj.data.get('status')}'.\n"
                "Re-process the video from Upload to resume it.")
