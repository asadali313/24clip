"""Processing screen: simple 5-stage progress, cancel, no freezing."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout, QWidget,
)

from ... import project_store
from ...workers.jobs import AnalysisWorker

STAGE_ROWS = [
    ("prepare", "Prepare"),
    ("transcribe", "Transcribe (whole video)"),
    ("discover", "Find best moments"),
    ("select", "Rank & select"),
    ("render", "Render clips"),
]


class StageRow(QWidget):
    def __init__(self, label: str):
        super().__init__()
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 4, 0, 4)
        self.lbl = QLabel(label)
        self.lbl.setFixedWidth(200)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setValue(0)
        self.status = QLabel("pending")
        self.status.setObjectName("muted")
        self.status.setFixedWidth(300)
        h.addWidget(self.lbl)
        h.addWidget(self.bar, 1)
        h.addWidget(self.status)


class AnalysisScreen(QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        self.worker: AnalysisWorker | None = None
        self.project: project_store.Project | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(32, 24, 32, 20)
        root.setSpacing(14)

        title = QLabel("Processing")
        title.setObjectName("h1")
        self.lbl_project = QLabel("")
        self.lbl_project.setObjectName("muted")
        root.addWidget(title)
        root.addWidget(self.lbl_project)

        self.rows: dict[str, StageRow] = {}
        for key, label in STAGE_ROWS:
            row = StageRow(label)
            self.rows[key] = row
            root.addWidget(row)

        self.lbl_current = QLabel("Idle.")
        self.lbl_current.setStyleSheet("font-weight: 600; color: #f0b429;")
        self.lbl_current.setWordWrap(True)
        root.addWidget(self.lbl_current)
        root.addStretch(1)

        btn_row = QHBoxLayout()
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self._cancel)
        self.btn_cancel.setEnabled(False)
        btn_row.addWidget(self.btn_cancel)
        btn_row.addStretch(1)
        root.addLayout(btn_row)

    # ------------------------------------------------------------------
    def begin(self, project: project_store.Project) -> None:
        self.project = project
        self.lbl_project.setText(
            f"{project.data.get('source_name')}  -  {project.id}")
        self.lbl_current.setStyleSheet("font-weight: 600; color: #f0b429;")
        for row in self.rows.values():
            row.bar.setValue(0)
            row.status.setText("pending")
        self.lbl_current.setText("Starting…")
        self.btn_cancel.setEnabled(True)
        self.main.set_busy(True)

        self.worker = AnalysisWorker(project, self.main.settings)
        self.worker.stage_progress.connect(self._on_progress)
        self.worker.finished_ok.connect(self._on_done)
        self.worker.failed.connect(self._on_failed)
        self.worker.start()

    def _on_progress(self, stage: str, frac: float, msg: str) -> None:
        row = self.rows.get(stage)
        if row:
            row.bar.setValue(int(max(0.0, min(1.0, frac)) * 1000))
            row.status.setText(msg or ("working…" if frac < 1 else "done"))
        self.lbl_current.setText(f"{stage}: {msg}" if msg else f"{stage}…")

    def _on_done(self) -> None:
        self.btn_cancel.setEnabled(False)
        self.main.set_busy(False)
        self.lbl_current.setText("Done.")
        if self.project:
            self.project.data["status"] = "ready"
            self.project.save()
        self.main.screens["results"].on_show()
        self.main.go("results")

    def _on_failed(self, err: str) -> None:
        self.btn_cancel.setEnabled(False)
        self.main.set_busy(False)
        self.lbl_current.setText(f"Failed: {err}")
        self.lbl_current.setStyleSheet("color: #ff7b7b; font-weight: 600;")
        if self.project:
            self.project.add_log(f"PROCESSING FAILED: {err}")

    def _cancel(self) -> None:
        if self.worker:
            self.lbl_current.setText("Cancelling after current step…")
            self.worker.cancel()

    def on_show(self) -> None:
        if self.project and self.project.stage_state("render") == "done":
            self.lbl_current.setText("Complete. See Results.")


    # ---- full-video mode -----------------------------------------------------
    def begin_full(self, project: project_store.Project) -> None:
        self.project = project
        self.lbl_project.setText(
            f"{project.data.get('source_name', '')} - full video "
            f"({project.id})")
        self.lbl_current.setStyleSheet("font-weight: 600; color: #f0b429;")
        for row in self.rows.values():
            row.bar.setValue(0)
            row.status.setText("")
        self.lbl_current.setText("Starting full-video processing…")
        self.btn_cancel.setEnabled(True)
        self.btn_cancel.clicked.connect(self._cancel_full)

    def set_full_message(self, msg: str) -> None:
        self.lbl_current.setText(msg)

    def show_full_error(self, err: str) -> None:
        self.lbl_current.setText(f"Failed: {err}")
        self.lbl_current.setStyleSheet("color: #ff7b7b; font-weight: 600;")

    def _cancel_full(self) -> None:
        w = getattr(self.main, "fv_worker", None)
        if w:
            self.lbl_current.setText("Cancelling…")
            w.cancel()
