"""Upload screen: browse / drag-drop, preview, metadata, start analysis."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl, Qt, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout,
    QWidget,
)

from ...core.ffmpeg_tools import VideoMetadata

VIDEO_EXT = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".ts", ".flv"}


class DropArea(QVideoWidget):
    """Video preview surface that also accepts dragged files."""
    file_dropped = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, e):
        urls = e.mimeData().urls() if e.mimeData().hasUrls() else []
        if urls and Path(urls[0].toLocalFile()).suffix.lower() in VIDEO_EXT:
            e.acceptProposedAction()

    def dropEvent(self, e):
        url = e.mimeData().urls()[0]
        self.file_dropped.emit(url.toLocalFile())
        e.acceptProposedAction()


class UploadScreen(QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        self.video_path: Path | None = None
        self.md: VideoMetadata | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(32, 28, 32, 24)
        root.setSpacing(16)

        title = QLabel("Upload Video")
        title.setObjectName("h1")
        root.addWidget(title)

        body = QHBoxLayout()
        # preview
        self.preview = DropArea()
        self.preview.setStyleSheet("background: #0d0e11; border-radius: 10px;")
        self.preview.setMinimumSize(560, 340)
        self.player = QMediaPlayer()
        self.audio = QAudioOutput()
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.preview)
        self.preview.file_dropped.connect(self._on_drop)
        body.addWidget(self.preview, 3)

        # metadata panel
        panel = QFrame()
        panel.setObjectName("card")
        pv = QVBoxLayout(panel)
        pv.setContentsMargins(20, 18, 20, 18)
        pv.setSpacing(10)
        self.lbl_name = QLabel("No video selected")
        self.lbl_name.setObjectName("h2")
        self.lbl_name.setWordWrap(True)
        self.lbl_meta = QLabel("-")
        self.lbl_meta.setObjectName("muted")
        self.lbl_warn = QLabel("")
        self.lbl_warn.setStyleSheet("color: #f5a524;")
        self.lbl_warn.setWordWrap(True)

        btn_browse = QPushButton("Browse…")
        btn_browse.clicked.connect(self._browse)
        self.btn_start = QPushButton("Start Analysis")
        self.btn_start.setObjectName("primary")
        self.btn_start.setEnabled(False)
        self.btn_start.clicked.connect(self._start)
        btn_cancel = QPushButton("Choose different file")
        btn_cancel.clicked.connect(self._reset)

        pv.addWidget(self.lbl_name)
        pv.addWidget(self.lbl_meta)
        pv.addWidget(self.lbl_warn)
        pv.addStretch(1)
        row = QHBoxLayout()
        row.addWidget(btn_browse)
        row.addWidget(btn_cancel)
        pv.addLayout(row)
        pv.addWidget(self.btn_start)
        body.addWidget(panel, 2)
        root.addLayout(body, 1)

        hint = QLabel("Tip: drag and drop a video file onto the preview. "
                      "Your original file is never modified or moved.")
        hint.setObjectName("muted")
        root.addWidget(hint)

    # ------------------------------------------------------------------
    def set_video(self, path: Path, md: VideoMetadata) -> None:
        self.video_path = path
        self.md = md
        self.lbl_name.setText(path.name)
        self.lbl_meta.setText(
            f"Duration:  {md.duration_hms()}\n"
            f"Resolution:  {md.resolution}  @ {md.fps:.0f} fps\n"
            f"Size:  {md.size_mb():.1f} MB\n"
            f"Video:  {md.video_codec}    Audio: "
            f"{md.audio_codec or 'NONE'}")
        if not md.has_audio:
            self.lbl_warn.setText(
                "⚠ This file has no audio track - clip discovery needs speech.")
            self.btn_start.setEnabled(False)
        elif md.duration < 60:
            self.lbl_warn.setText("Very short video - results may be limited.")
            self.btn_start.setEnabled(True)
        else:
            self.lbl_warn.setText("")
            self.btn_start.setEnabled(True)
        self.player.setSource(QUrl.fromLocalFile(str(path)))
        self.player.play()

    def _on_drop(self, path_str: str) -> None:
        from .dashboard import DashboardScreen
        self.main.screens["dashboard"]._load_video(Path(path_str))

    def _browse(self) -> None:
        self.main.screens["dashboard"]._pick_video()

    def _reset(self) -> None:
        self.player.stop()
        self.player.setSource(QUrl())
        self.video_path = None
        self.md = None
        self.lbl_name.setText("No video selected")
        self.lbl_meta.setText("-")
        self.lbl_warn.setText("")
        self.btn_start.setEnabled(False)

    def _start(self) -> None:
        if not self.video_path or not self.md:
            return
        # disk space preflight before kicking off long processing
        ok, free_gb = self.main_cfg_check()
        if not ok:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(
                self, "Not enough space",
                f"Only {free_gb} GB free on D:. Need at least 5 GB.")
            return
        self.player.stop()
        from ... import project_store
        project = project_store.Project.create(self.video_path)
        project.data["settings_snapshot"] = {
            "clip_count": self.main.settings.clip_count,
            "duration_mode": self.main.settings.duration_mode,
            "aspect_ratio": self.main.settings.aspect_ratio,
        }
        project.save()
        # show settings BEFORE any processing starts
        self.main.pending_project = project
        self.main.go("settings")

    def main_cfg_check(self):
        from ... import config
        return config.check_drive_space()

    def on_show(self) -> None:
        pass
