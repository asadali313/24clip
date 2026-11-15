"""Main window: left navigation + stacked screens."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QListWidget, QMainWindow, QMessageBox,
    QStackedWidget, QVBoxLayout, QWidget,
)

from .. import config, project_store
from ..workers.jobs import AnalysisWorker, ExportWorker
from .screens.analysis import AnalysisScreen
from .screens.customize import CustomizeScreen
from .screens.dashboard import DashboardScreen
from .screens.editor import EditorScreen
from .screens.publishing import PublishingScreen
from .screens.results import ResultsScreen
from .screens.settings_screen import SettingsScreen
from .screens.upload import UploadScreen
from .theme import QSS

NAV = [
    ("dashboard", "Dashboard"),
    ("upload", "Upload Video"),
    ("customize", "Customize"),
    ("settings", "Settings"),
    ("processing", "Processing"),
    ("results", "Results"),
    ("publishing", "Publishing"),
]


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = config.Settings.load()
        self.project: project_store.Project | None = None
        self.pending_project: project_store.Project | None = None
        self.analysis_worker: AnalysisWorker | None = None
        self.export_worker: ExportWorker | None = None

        self.setWindowTitle("ClipNova AI")
        self.resize(1280, 800)
        self.setMinimumSize(QSize(1080, 700))

        root = QWidget()
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ---- left nav -----------------------------------------------------
        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.setFixedWidth(210)
        for _, label in NAV:
            self.nav.addItem(label)
        self.nav.currentRowChanged.connect(self._nav_changed)
        layout.addWidget(self.nav)

        # ---- stacked screens (editor is advanced, reachable from Results) ----
        self.stack = QStackedWidget()
        self.screens: dict[str, QWidget] = {}
        self.screens["dashboard"] = DashboardScreen(self)
        self.screens["upload"] = UploadScreen(self)
        self.screens["customize"] = CustomizeScreen(self)
        self.screens["settings"] = SettingsScreen(self)
        self.screens["processing"] = AnalysisScreen(self)
        self.screens["results"] = ResultsScreen(self)
        self.screens["publishing"] = PublishingScreen(self)
        self.screens["editor"] = EditorScreen(self)
        for key in ("dashboard", "upload", "customize", "settings",
                    "processing", "results", "publishing", "editor"):
            self.stack.addWidget(self.screens[key])
        layout.addWidget(self.stack, 1)
        self.setCentralWidget(root)

        self.nav.setCurrentRow(0)
        self.setStyleSheet(QSS)

    # ------------------------------------------------------------------
    def _nav_changed(self, row: int) -> None:
        key = NAV[row][0]
        screen = self.screens[key]
        if hasattr(screen, "on_show"):
            screen.on_show()
        self.stack.setCurrentWidget(screen)

    def go(self, key: str) -> None:
        for i, (k, _) in enumerate(NAV):
            if k == key:
                self.nav.setCurrentRow(i)
                return

    def open_editor(self, cand: dict) -> None:
        """Show the advanced editor without changing nav selection."""
        self.screens["editor"].load_candidate(cand)
        self.stack.setCurrentWidget(self.screens["editor"])

    def back_to_results(self) -> None:
        self.go("results")

    # ------------------------------------------------------------------
    def open_project(self, project: project_store.Project) -> None:
        self.project = project
        self.pending_project = project
        self.screens["results"].on_show()
        self.go("results")

    def start_processing(self, project: project_store.Project) -> None:
        self.project = project
        self.screens["processing"].begin(project)
        self.go("processing")

    def set_busy(self, busy: bool) -> None:
        """Block navigation to destructive screens while workers run."""
        self.nav.setEnabled(not busy)

    def confirm(self, title: str, text: str) -> bool:
        ret = QMessageBox.question(self, title, text,
                                   QMessageBox.Yes | QMessageBox.No)
        return ret == QMessageBox.Yes


    # ------------------------------------------------------------------
    def process_full_video(self, project) -> None:
        """Run the full-video customization in the background."""
        from .workers.jobs import FullVideoWorker
        self.project = project
        self.set_busy(True)
        self.screens["processing"].begin_full(project)
        self.go("processing")
        self.fv_worker = FullVideoWorker(project, self.settings)
        self.fv_worker.stage.connect(
            lambda m: self.screens["processing"].set_full_message(m))
        self.fv_worker.finished_ok.connect(self._full_done)
        self.fv_worker.failed.connect(self._full_failed)
        self.fv_worker.start()

    def _full_done(self, outputs: list) -> None:
        self.set_busy(False)
        self.project.data["status"] = "ready"
        self.project.save()
        self.screens["results"].on_show()
        self.go("results")

    def _full_failed(self, err: str) -> None:
        self.set_busy(False)
        self.screens["processing"].show_full_error(err)
