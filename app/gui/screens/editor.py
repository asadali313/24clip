"""Clip editor: adjust boundaries, framing, captions, voiceover, re-render."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl, Qt
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QFrame,
    QHBoxLayout, QLabel, QMessageBox, QProgressBar, QPushButton, QSlider,
    QVBoxLayout, QWidget,
)

from ...core import renderer


class EditorScreen(QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        self.cand: dict | None = None
        self.vo_path: Path | None = None
        self.render_worker = None

        root = QVBoxLayout(self)
        root.setContentsMargins(32, 28, 32, 24)
        root.setSpacing(14)

        title = QLabel("Clip Editor (advanced)")
        title.setObjectName("h1")
        btn_back = QPushButton("← Back to Results")
        btn_back.clicked.connect(lambda: self.main.back_to_results())
        head = QHBoxLayout()
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(btn_back)
        root.addLayout(head)

        body = QHBoxLayout()
        # preview
        self.video = QVideoWidget()
        self.video.setMinimumSize(360, 480)
        self.video.setStyleSheet("background:#0d0e11; border-radius:10px;")
        self.player = QMediaPlayer()
        self.audio_out = QAudioOutput()
        self.player.setAudioOutput(self.audio_out)
        self.player.setVideoOutput(self.video)
        body.addWidget(self.video, 2)

        form_frame = QFrame()
        form_frame.setObjectName("card")
        form = QFormLayout(form_frame)
        form.setContentsMargins(18, 16, 18, 16)
        form.setSpacing(10)

        self.lbl_uid = QLabel("No clip loaded")
        self.lbl_uid.setStyleSheet("font-weight:600;")
        form.addRow(self.lbl_uid)
        self.spin_start = QDoubleSpinBox()
        self.spin_end = QDoubleSpinBox()
        for sp in (self.spin_start, self.spin_end):
            sp.setRange(0.0, 1e7)
            sp.setDecimals(1)
            sp.setSuffix(" s")
            sp.setSingleStep(0.5)
        form.addRow("Start (original):", self.spin_start)
        form.addRow("End (original):", self.spin_end)
        self.lbl_dur = QLabel("-")
        form.addRow("Duration:", self.lbl_dur)
        self.spin_start.valueChanged.connect(self._update_dur)
        self.spin_end.valueChanged.connect(self._update_dur)

        self.cmb_framing = QComboBox()
        self.cmb_framing.addItems(["auto", "crop (face-guided)",
                                   "crop (center)", "blur padding",
                                   "passthrough"])
        form.addRow("Vertical framing:", self.cmb_framing)

        self.chk_captions = QCheckBox("Burn in captions")
        form.addRow("", self.chk_captions)

        tpl_row = QHBoxLayout()
        tpl_row.addWidget(QLabel("Template:"))
        self.cmb_template = QComboBox()
        from ...core.captions import TEMPLATE_KEYS, TEMPLATE_LABELS
        self.cmb_template.addItem("Project default", None)
        for k in TEMPLATE_KEYS:
            self.cmb_template.addItem(TEMPLATE_LABELS[k], k)
        tpl_row.addWidget(self.cmb_template)
        tpl_row.addStretch(1)
        form.addRow("", tpl_row)

        vo_row = QHBoxLayout()
        self.btn_vo = QPushButton("Choose voice file…")
        self.btn_vo.clicked.connect(self._pick_vo)
        self.lbl_vo = QLabel("none")
        self.lbl_vo.setObjectName("muted")
        vo_row.addWidget(self.btn_vo)
        vo_row.addWidget(self.lbl_vo, 1)
        form.addRow("Personal voice:", vo_row)

        self.sld_orig = QSlider(Qt.Horizontal)
        self.sld_orig.setRange(0, 100)
        self.sld_orig.setValue(100)
        self.sld_vo = QSlider(Qt.Horizontal)
        self.sld_vo.setRange(0, 100)
        self.sld_vo.setValue(100)
        form.addRow("Original volume:", self.sld_orig)
        form.addRow("Voiceover volume:", self.sld_vo)

        btn_row = QHBoxLayout()
        self.btn_preview = QPushButton("Preview clip")
        self.btn_preview.clicked.connect(self._preview)
        self.btn_render = QPushButton("Render again")
        self.btn_render.setObjectName("primary")
        self.btn_render.clicked.connect(self._render)
        btn_row.addWidget(self.btn_preview)
        btn_row.addWidget(self.btn_render)
        form.addRow("", btn_row)

        self.lbl_status = QLabel("")
        self.lbl_status.setObjectName("muted")
        self.lbl_status.setWordWrap(True)
        form.addRow("", self.lbl_status)
        self.btn_broll = QPushButton("Suggest B-roll moments")
        self.btn_broll.clicked.connect(self._suggest_broll)
        form.addRow("", self.btn_broll)
        self.lbl_broll = QLabel("")
        self.lbl_broll.setObjectName("muted")
        self.lbl_broll.setWordWrap(True)
        form.addRow("", self.lbl_broll)
        body.addWidget(form_frame, 3)
        root.addLayout(body, 1)

        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.hide()
        root.addWidget(self.bar)

    # ------------------------------------------------------------------
    def reset(self) -> None:
        self.cand = None
        self.lbl_uid.setText("No clip loaded")
        self.player.stop()

    def load_candidate(self, cand: dict) -> None:
        self.cand = cand
        self.lbl_uid.setText(f"Editing {cand['uid']}")
        proj = self.main.project
        max_dur = 1e7
        if proj and proj.data.get("metadata"):
            max_dur = max(1.0, float(proj.data["metadata"].get("duration", 1e7)))
        self.spin_start.setRange(0.0, max_dur)
        self.spin_end.setRange(0.0, max_dur)
        self.spin_start.setValue(float(cand["start"]))
        self.spin_end.setValue(float(cand["end"]))
        self.chk_captions.setChecked(self.main.settings.captions_enabled)
        self.lbl_status.setText(cand.get("text", "")[:220])
        self._preview()

    def _update_dur(self) -> None:
        d = max(0.0, self.spin_end.value() - self.spin_start.value())
        self.lbl_dur.setText(f"{d:.1f} s")

    def _preview(self) -> None:
        if not self.main.project or not self.main.project.source_video:
            return
        self.player.stop()
        self.player.setSource(QUrl.fromLocalFile(
            str(self.main.project.source_video)))
        self.player.play()
        # seek handled by user scrubbing; start position via seek
        self.player.setPosition(int(self.spin_start.value() * 1000))

    def _pick_vo(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose a voice recording", "",
            "Audio files (*.mp3 *.wav *.m4a *.ogg *.flac);;All files (*.*)")
        if path:
            self.vo_path = Path(path)
            self.lbl_vo.setText(self.vo_path.name)

    # ------------------------------------------------------------------
    def _suggest_broll(self) -> None:
        """Visually active moments elsewhere in the video (manual insert)."""
        if not self.main.project or not self.main.project.source_video:
            return
        try:
            from ...core.visual import broll_suggestions
            md_dur = float(self.main.project.data.get("metadata", {})
                           .get("duration", 0))
            sugs = broll_suggestions(self.main.project.source_video,
                                     md_dur,
                                     (self.spin_start.value(),
                                      self.spin_end.value()))
            if sugs:
                txt = "B-roll ideas (most active scenes): " + "; ".join(
                    f"{s['start']:.0f}-{s['end']:.0f}s"
                    + (" (face)" if s["face"] else "") for s in sugs)
            else:
                txt = "No distinctly more active scenes found."
            self.lbl_broll.setText(txt)
        except Exception as e:  # noqa: BLE001
            self.lbl_broll.setText(f"B-roll scan failed: {e}")

    def _render(self) -> None:
        if not self.cand or not self.main.project:
            return
        start = self.spin_start.value()
        end = self.spin_end.value()
        if end - start < 3:
            QMessageBox.warning(self, "Too short",
                                "The clip must be at least 3 seconds long.")
            return
        self.cand["start"] = start
        self.cand["end"] = end
        self.cand["captions_enabled"] = self.chk_captions.isChecked()
        self.cand["caption_template"] = self.cmb_template.currentData()
        self.cand["voiceover"] = str(self.vo_path) if self.vo_path else ""
        s = self.main.settings
        s.captions_enabled = self.chk_captions.isChecked()
        s.original_volume = self.sld_orig.value() / 100.0
        s.voiceover_volume = self.sld_vo.value() / 100.0

        out_name = f"preview_{self.cand['uid']}.mp4"
        out_path = self.main.project.path("clips", out_name)
        self.bar.show()
        self.bar.setValue(0)
        self.btn_render.setEnabled(False)
        self.main.set_busy(True)
        from ...workers.jobs import RenderOneWorker
        self.render_worker = RenderOneWorker(self.main.project, self.cand,
                                             s, out_path)
        self.render_worker.progress.connect(
            lambda f, m: (self.bar.setValue(int(f * 1000)),
                          self.lbl_status.setText(m)))
        self.render_worker.done.connect(self._rendered)
        self.render_worker.failed.connect(self._render_failed)
        self.render_worker.start()

    def _rendered(self, out_path: str, meta: dict) -> None:
        self.bar.setValue(1000)
        self.btn_render.setEnabled(True)
        self.main.set_busy(False)
        self.lbl_status.setText(
            f"Rendered: {out_path}\n"
            f"{meta.get('resolution')} · {meta.get('duration', 0):.1f}s · "
            f"framing: {meta.get('framing', {}).get('method')}")
        self.player.stop()
        self.player.setSource(QUrl.fromLocalFile(out_path))
        self.player.play()

    def _render_failed(self, err: str) -> None:
        self.btn_render.setEnabled(True)
        self.main.set_busy(False)
        self.lbl_status.setText(f"Render failed: {err}")
        self.lbl_status.setStyleSheet("color:#ff7b7b;")

    def on_show(self) -> None:
        pass
