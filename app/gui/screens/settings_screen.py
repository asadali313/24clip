"""Settings screen: shown AFTER upload, BEFORE any processing.

A. clip count  B. duration  C. captions  D. voice  E. output format
One "Start Processing" button. Nothing runs until it is clicked.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QFileDialog, QGridLayout, QGroupBox,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QRadioButton, QPushButton,
    QSlider, QSpinBox, QToolButton, QVBoxLayout, QWidget,
)

from ...core.captions import TEMPLATE_LABELS


class SettingsScreen(QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        root = QVBoxLayout(self)
        root.setContentsMargins(32, 24, 32, 20)
        root.setSpacing(12)

        title = QLabel("Processing Settings")
        title.setObjectName("h1")
        self.lbl_video = QLabel("No video selected.")
        self.lbl_video.setObjectName("muted")
        root.addWidget(title)
        root.addWidget(self.lbl_video)

        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(10)

        # ---- A. clip count -------------------------------------------------
        gA = QGroupBox("A. How many clips?")
        va = QVBoxLayout(gA)
        self.grp_count = QButtonGroup(self)
        self.count_widgets: dict[str, QRadioButton] = {}
        row1 = QHBoxLayout()
        for key, label in (("3", "3"), ("6", "6"), ("10", "10"), ("15", "15")):
            rb = QRadioButton(label)
            rb.setObjectName(f"count_{key}")
            self.grp_count.addButton(rb)
            self.count_widgets[key] = rb
            row1.addWidget(rb)
        row1.addStretch(1)
        self.rb_custom_count = QRadioButton("Custom:")
        self.grp_count.addButton(self.rb_custom_count)
        self.count_widgets["custom"] = self.rb_custom_count
        self.spin_count = QSpinBox()
        self.spin_count.setRange(1, 30)
        self.spin_count.setValue(6)
        self.spin_count.setEnabled(False)
        self.rb_custom_count.toggled.connect(self.spin_count.setEnabled)
        row1.addWidget(self.rb_custom_count)
        row1.addWidget(self.spin_count)
        va.addLayout(row1)
        self.lbl_count_hint = QLabel("Only genuinely good moments are used - "
                                     "you'll get an honest note if fewer exist.")
        self.lbl_count_hint.setObjectName("muted")
        va.addWidget(self.lbl_count_hint)
        grid.addWidget(gA, 0, 0)

        # ---- B. duration ------------------------------------------------------
        gB = QGroupBox("B. Clip duration")
        vb = QVBoxLayout(gB)
        self.grp_dur = QButtonGroup(self)
        self.dur_widgets: dict[str, QRadioButton] = {}
        for key, label in (
                ("auto", "Automatic - AI chooses the best natural duration"),
                ("max30", "Maximum 30 seconds"),
                ("max45", "Maximum 45 seconds"),
                ("max60", "Maximum 60 seconds"),
                ("custom", "Custom:")):
            rb = QRadioButton(label)
            self.grp_dur.addButton(rb)
            self.dur_widgets[key] = rb
            vb.addWidget(rb)
        crow = QHBoxLayout()
        crow.addSpacing(24)
        self.spin_cmin = QSpinBox()
        self.spin_ctgt = QSpinBox()
        self.spin_cmax = QSpinBox()
        for sp, lo, hi, val, suf in ((self.spin_cmin, 10, 90, 20, " min"),
                                     (self.spin_ctgt, 15, 90, 30, " target"),
                                     (self.spin_cmax, 20, 120, 45, " max")):
            sp.setRange(lo, hi)
            sp.setValue(val)
            sp.setSuffix(" s")
            sp.setEnabled(False)
            crow.addWidget(sp)
        self.spin_cmin.setSuffix("s min")
        self.spin_ctgt.setSuffix("s target")
        self.spin_cmax.setSuffix("s max")
        self.dur_widgets["custom"].toggled.connect(
            lambda on: [sp.setEnabled(on) for sp in
                        (self.spin_cmin, self.spin_ctgt, self.spin_cmax)])
        crow.addStretch(1)
        vb.addLayout(crow)
        self.lbl_dur_hint = QLabel("The maximum is respected; a meaningful "
                                   "idea is never cut short just to fit.")
        self.lbl_dur_hint.setObjectName("muted")
        vb.addWidget(self.lbl_dur_hint)
        grid.addWidget(gB, 0, 1)

        # ---- C. captions --------------------------------------------------------
        gC = QGroupBox("C. Captions (free, generated locally)")
        vc = QVBoxLayout(gC)
        self.rb_no_caps = QRadioButton("No captions")
        self.rb_no_caps.setChecked(True)
        vc.addWidget(self.rb_no_caps)
        self.grp_cap = QButtonGroup(self)
        self.grp_cap.addButton(self.rb_no_caps)
        self.cap_widgets: dict[str, QRadioButton] = {
            "none": self.rb_no_caps}
        self.tpl_buttons: dict[str, QToolButton] = {}
        from ...core.captions import TEMPLATE_KEYS
        tpl_grid = QGridLayout()
        tpl_grid.setSpacing(10)
        for idx, key in enumerate(TEMPLATE_KEYS):
            rb = QRadioButton(TEMPLATE_LABELS[key])
            self.grp_cap.addButton(rb)
            self.cap_widgets[key] = rb
            cell = QVBoxLayout()
            cw = QWidget()
            cell.addWidget(rb)
            btn = QToolButton()
            btn.setCheckable(True)
            btn.setFixedSize(150, 200)
            btn.setStyleSheet("QToolButton{border:2px solid #2a2f38;"
                              "border-radius:8px; background:#0d0e11;}"
                              "QToolButton:hover{border-color:#5b6472;}")
            btn.clicked.connect(
                lambda _c=False, k=key: self.cap_widgets[k].setChecked(True))
            self.tpl_buttons[key] = btn
            cell.addWidget(btn, 0, Qt.AlignHCenter)
            cell.addStretch(1)
            cw.setLayout(cell)
            tpl_grid.addWidget(cw, idx // 3, idx % 3)
        vc.addLayout(tpl_grid)
        lbl_safe = QLabel("Templates use word-level timing from the transcript "
                          "- synced, readable, inside the safe area.")
        lbl_safe.setObjectName("muted")
        lbl_safe.setWordWrap(True)
        vc.addWidget(lbl_safe)
        grid.addWidget(gC, 1, 0)

        # ---- D. voice -----------------------------------------------------------
        gD = QGroupBox("D. Voice / audio")
        vd = QVBoxLayout(gD)
        self.grp_voice = QButtonGroup(self)
        self.voice_widgets: dict[str, QRadioButton] = {}
        self.btn_voice_file = QPushButton("Choose file…")
        self.btn_voice_file.setEnabled(False)
        self.lbl_voice_file = QLabel("no file")
        self.lbl_voice_file.setObjectName("muted")
        self.btn_voice_file.clicked.connect(self._pick_voice)
        rowv = QHBoxLayout()
        for key, label in (
                ("keep", "Keep original video audio"),
                ("remove", "Remove original audio"),
                ("file", "Use my own recording:"),
                ("tts", "Local AI voice (free, offline, Windows voices)")):
            rb = QRadioButton(label)
            self.grp_voice.addButton(rb)
            self.voice_widgets[key] = rb
            if key == "file":
                rowv.addWidget(rb)
                rowv.addWidget(self.btn_voice_file)
                rowv.addWidget(self.lbl_voice_file, 1)
                vd.addLayout(rowv)
            else:
                vd.addWidget(rb)
        self.voice_widgets["file"].toggled.connect(
            self.btn_voice_file.setEnabled)

        # AI voice presets (only voices installed on THIS machine are shown)
        self.tts_panel = QWidget()
        tp = QVBoxLayout(self.tts_panel)
        tp.setContentsMargins(24, 4, 0, 0)
        prow = QHBoxLayout()
        prow.addWidget(QLabel("Voice preset:"))
        self.cmb_preset = QComboBox()
        from ...core import tts as tts_mod
        self._presets = tts_mod.list_presets()
        for p in self._presets:
            self.cmb_preset.addItem(f"{p.label} - {p.category}", p.id)
        if self._presets:
            idx = max(0, [p.id for p in self._presets]
                      .index(self.main.settings.voice_preset)
                      if self.main.settings.voice_preset in
                      [p.id for p in self._presets] else 0)
            self.cmb_preset.setCurrentIndex(idx)
        prow.addWidget(self.cmb_preset, 1)
        self.btn_sample = QPushButton("▶ Play sample")
        self.btn_sample.clicked.connect(self._play_sample)
        prow.addWidget(self.btn_sample)
        tp.addLayout(prow)
        sv = QHBoxLayout()
        sv.addWidget(QLabel("Speed"))
        self.sld_tts_speed = QSlider(Qt.Horizontal)
        self.sld_tts_speed.setRange(-5, 5)
        self.sld_tts_speed.setValue(self.main.settings.tts_speed)
        sv.addWidget(self.sld_tts_speed)
        sv.addWidget(QLabel("Volume"))
        self.sld_tts_vol = QSlider(Qt.Horizontal)
        self.sld_tts_vol.setRange(10, 100)
        self.sld_tts_vol.setValue(self.main.settings.tts_volume)
        sv.addWidget(self.sld_tts_vol)
        tp.addLayout(sv)
        langs = ", ".join(sorted({p.language for p in self._presets})) or "-"
        plang = QLabel(f"Installed voices: "
                       f"{len(self._presets)} presets, languages: {langs}. "
                       f"Narration is generated locally.")
        plang.setObjectName("muted")
        tp.addWidget(plang)
        vd.addWidget(self.tts_panel)
        self.tts_panel.setEnabled(False)
        self.voice_widgets["tts"].toggled.connect(self.tts_panel.setEnabled)
        note = QLabel("Your recordings never leave this computer. AI voice "
                      "replaces the original audio and adds quiet background "
                      "music; Original voice keeps the video audio untouched.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        vd.addWidget(note)
        grid.addWidget(gD, 1, 1)

        # ---- E. output -----------------------------------------------------------
        gE = QGroupBox("E. Output format")
        ve = QVBoxLayout(gE)
        self.grp_aspect = QButtonGroup(self)
        self.aspect_widgets: dict[str, QRadioButton] = {}
        arow = QHBoxLayout()
        arow.setSpacing(14)
        for key, label, sub in (
                ("9:16", "Portrait (9:16)",
                 "1080 × 1920 · Shorts, TikTok, Reels"),
                ("16:9", "Landscape (16:9)",
                 "1920 × 1080 · YouTube, widescreen")):
            cell = QVBoxLayout()
            rb = QRadioButton(label)
            self.grp_aspect.addButton(rb)
            self.aspect_widgets[key] = rb
            cell.addWidget(rb)
            shape = QLabel("▮" if key == "9:16" else "▬")
            shape.setAlignment(Qt.AlignCenter)
            shape.setFixedSize(64, 84 if key == "9:16" else 40)
            shape.setStyleSheet(
                "background:#2a2f38; border:2px solid #e5484d;"
                "border-radius:4px; font-size:22px;")
            cell.addWidget(shape, 0, Qt.AlignHCenter)
            sub_lbl = QLabel(sub)
            sub_lbl.setObjectName("muted")
            sub_lbl.setWordWrap(True)
            cell.addWidget(sub_lbl)
            cell.addStretch(1)
            wrap = QWidget()
            wrap.setLayout(cell)
            arow.addWidget(wrap)
        arow.addStretch(1)
        ve.addLayout(arow)
        ve.addWidget(QLabel("Subject tracking adapts to the format; "
                            "MP4 (H.264 + AAC), one file per clip."))
        grid.addWidget(gE, 2, 0)

        # ---- advanced --------------------------------------------------------------
        gF = QGroupBox("Advanced (optional)")
        vf = QHBoxLayout(gF)
        vf.addWidget(QLabel("Clip selection mode:"))
        self.cmb_clip_mode = QComboBox()
        for mid, mlabel in (("best", "Best moments"),
                            ("funny", "Funny moments"),
                            ("emotional", "Emotional moments"),
                            ("interesting", "Interesting moments"),
                            ("educational", "Educational moments"),
                            ("motivational", "Motivational moments"),
                            ("shocking", "Shocking moments"),
                            ("gaming", "Gaming highlights"),
                            ("podcast", "Podcast highlights"),
                            ("custom", "Custom AI prompt")):
            self.cmb_clip_mode.addItem(mlabel, mid)
        vf.addWidget(self.cmb_clip_mode)
        vf.addWidget(QLabel("Whisper model:"))
        self.cmb_model = QComboBox()
        self.cmb_model.addItems(["tiny  (fastest)", "base  (recommended)",
                                 "small  (best accuracy, slower)"])
        vf.addWidget(self.cmb_model)
        self.chk_lowmem = QCheckBox("Low-memory mode")
        vf.addWidget(self.chk_lowmem)
        self.chk_cleanup = QCheckBox("Audio cleanup (loudnorm)")
        vf.addWidget(self.chk_cleanup)
        vf.addStretch(1)
        grid.addWidget(gF, 2, 1)

        gX = QGroupBox("Export options")
        vx = QHBoxLayout(gX)
        vx.addWidget(QLabel("Resolution:"))
        self.cmb_res = QComboBox()
        self.cmb_res.addItems(["1080p", "720p"])
        vx.addWidget(self.cmb_res)
        vx.addWidget(QLabel("Output directory:"))
        self.txt_outdir = QLineEdit()
        self.txt_outdir.setPlaceholderText("default: project exports folder")
        vx.addWidget(self.txt_outdir, 1)
        self.btn_outdir = QPushButton("…")
        self.btn_outdir.setFixedWidth(36)
        self.btn_outdir.clicked.connect(self._pick_outdir)
        vx.addWidget(self.btn_outdir)
        grid.addWidget(gX, 3, 0, 1, 2)

        root.addLayout(grid)
        root.addStretch(1)

        # ---- start ----------------------------------------------------------------
        start_row = QHBoxLayout()
        self.btn_back = QPushButton("← Choose different video")
        self.btn_back.clicked.connect(self._back)
        self.btn_start = QPushButton("Start Processing")
        self.btn_start.setObjectName("primary")
        self.btn_start.clicked.connect(self._start)
        start_row.addWidget(self.btn_back)
        start_row.addStretch(1)
        start_row.addWidget(self.btn_start)
        root.addLayout(start_row)

        self._load_from_settings()

    # ------------------------------------------------------------------
    def _load_from_settings(self) -> None:
        s = self.main.settings
        for key, rb in self.count_widgets.items():
            if key == "custom":
                continue
            rb.setChecked(s.clip_count == int(key))
        if str(s.clip_count) not in ("3", "6", "10", "15"):
            self.rb_custom_count.setChecked(True)
            self.spin_count.setValue(s.clip_count)
        self.dur_widgets.get(s.duration_mode,
                             self.dur_widgets["auto"]).setChecked(True)
        mode = s.caption_mode if s.caption_mode in self.cap_widgets else "none"
        self.cap_widgets[mode].setChecked(True)
        self.aspect_widgets.get(s.aspect_ratio,
                                self.aspect_widgets["9:16"]).setChecked(True)
        self.voice_widgets.get(s.voice_mode,
                               self.voice_widgets["keep"]).setChecked(True)
        if s.voice_file:
            self.lbl_voice_file.setText(s.voice_file.split("/")[-1].split("\\")[-1])
        idx = {"tiny": 0, "base": 1, "small": 2}.get(s.model_size, 1)
        self.cmb_model.setCurrentIndex(idx)
        self.chk_lowmem.setChecked(s.low_memory_mode)
        i = self.cmb_clip_mode.findData(s.clip_mode)
        self.cmb_clip_mode.setCurrentIndex(max(0, i))
        self.chk_cleanup.setChecked(getattr(s, "audio_cleanup", False))
        self.cmb_res.setCurrentText(getattr(s, "export_resolution", "1080p"))
        self.txt_outdir.setText(getattr(s, "output_dir", ""))

    def _pick_voice(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose your voice recording", "",
            "Audio files (*.mp3 *.wav *.m4a *.ogg *.flac);;All files (*.*)")
        if path:
            self.main.settings.voice_file = path
            self.lbl_voice_file.setText(path.split("/")[-1].split("\\")[-1])

    def _play_sample(self) -> None:
        """Synthesize + play a short sample of the selected preset."""
        pid = self.cmb_preset.currentData()
        preset = next((p for p in self._presets if p.id == pid), None)
        if preset is None:
            return
        self.btn_sample.setEnabled(False)
        self.btn_sample.setText("Generating…")
        try:
            from ...core import tts as tts_mod
            sample_dir = config.TEMP_DIR / "render"
            wav = tts_mod.synthesize_sample(
                preset, sample_dir / f"voice_sample_{preset.id}.wav",
                speed_offset=self.sld_tts_speed.value(),
                volume=self.sld_tts_vol.value())
            from PySide6.QtCore import QUrl
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
            if not hasattr(self, "_player"):
                self._player = QMediaPlayer(self)
                self._audio = QAudioOutput(self)
                self._player.setAudioOutput(self._audio)
            self._player.stop()
            self._player.setSource(QUrl.fromLocalFile(str(wav)))
            self._player.play()
        except Exception as e:  # noqa: BLE001 - show honestly
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Voice sample failed", str(e))
        finally:
            self.btn_sample.setEnabled(True)
            self.btn_sample.setText("▶ Play sample")

    def _back(self) -> None:
        self.main.go("upload")

    # ------------------------------------------------------------------
    def collect(self) -> None:
        """Copy UI choices into the Settings object."""
        s = self.main.settings
        if self.rb_custom_count.isChecked():
            s.clip_count = self.spin_count.value()
        else:
            for key, rb in self.count_widgets.items():
                if key != "custom" and rb.isChecked():
                    s.clip_count = int(key)
        for key, rb in self.dur_widgets.items():
            if rb.isChecked():
                s.duration_mode = key
        if s.duration_mode == "custom":
            s.custom_min = self.spin_cmin.value()
            s.custom_target = self.spin_ctgt.value()
            s.custom_max = self.spin_cmax.value()
        for key, rb in self.cap_widgets.items():
            if rb.isChecked():
                s.caption_mode = key
        for key, rb in self.aspect_widgets.items():
            if rb.isChecked():
                s.aspect_ratio = key
        for key, rb in self.voice_widgets.items():
            if rb.isChecked():
                s.voice_mode = key
        if self.cmb_preset.currentData():
            s.voice_preset = self.cmb_preset.currentData()
        s.tts_speed = self.sld_tts_speed.value()
        s.tts_volume = self.sld_tts_vol.value()
        s.model_size = self.cmb_model.currentText().split()[0]
        s.low_memory_mode = self.chk_lowmem.isChecked()
        s.clip_mode = self.cmb_clip_mode.currentData() or "best"
        if s.clip_mode == "custom":
            prompt, ok = QInputDialog.getText(
                self, "Custom AI prompt",
                "Describe what to find (words to look for):")
            if ok:
                s.custom_prompt = prompt.strip()
        s.audio_cleanup = self.chk_cleanup.isChecked()
        s.export_resolution = self.cmb_res.currentText()
        s.output_dir = self.txt_outdir.text().strip()
        s.apply_workflow_choices()

    def _pick_outdir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Choose output directory")
        if d:
            self.txt_outdir.setText(d)

    def _start(self) -> None:
        self.collect()
        self.main.settings.save()
        proj = self.main.pending_project
        if proj is None:
            return
        self.main.start_processing(proj)

    def on_show(self) -> None:
        proj = self.main.pending_project
        self.btn_start.setEnabled(proj is not None)
        if proj:
            md = proj.data.get("metadata", {})
            name = proj.data.get("source_name", "")
            dur = md.get("duration", 0)
            h, m = int(dur // 3600), int(dur % 3600 // 60)
            self.lbl_video.setText(
                f"Ready to process: {name}  ({h}h {m:02d}m)" if h
                else f"Ready to process: {name}  ({m}m {int(dur % 60):02d}s)")
        self._ensure_previews()

    def _ensure_previews(self) -> None:
        """Render one REAL burned-in preview per template (same engine as
        the export), on a frame of the user's video when available."""
        if getattr(self, "_previews_done", False):
            return
        try:
            from ...core.captions import render_template_previews
            from ... import config as _cfg
            from PySide6.QtGui import QIcon, QPixmap
            out_dir = _cfg.resource_path("assets/caption_previews")
            bg = None
            proj = self.main.pending_project or self.main.project
            if proj:
                cands = proj.data.get("candidates", [])
                for i, _c in enumerate(cands):
                    t = proj.path("candidates", f"thumb_{i:02d}.jpg")
                    if t.exists():
                        bg = t
                        break
            paths = render_template_previews(out_dir, self.main.settings,
                                             bg_image=bg)
            for key, btn in self.tpl_buttons.items():
                p = paths.get(key)
                if p:
                    pm = QPixmap(str(p))
                    if not pm.isNull():
                        btn.setIcon(QIcon(str(p)))
                        btn.setIconSize(btn.size() * 0.96)
                    btn.setText("")
                    btn.setToolTip(TEMPLATE_LABELS.get(key, key))
            self._highlight_selection()
            self._previews_done = True
        except Exception:
            # previews are cosmetic; never block the settings screen
            self._previews_done = True

    def _highlight_selection(self) -> None:
        """Clearly mark the selected template card."""
        for key, btn in self.tpl_buttons.items():
            on = self.cap_widgets[key].isChecked()
            btn.setStyleSheet(
                "QToolButton{border:3px solid #e5484d; border-radius:8px;"
                "background:#0d0e11;}"
                if on else
                "QToolButton{border:2px solid #2a2f38; border-radius:8px;"
                "background:#0d0e11;}"
                "QToolButton:hover{border-color:#5b6472;}")
        # radio toggles re-highlight
        for key, rb in self.cap_widgets.items():
            try:
                rb.toggled.disconnect(self._highlight_selection)
            except RuntimeError:
                pass
            rb.toggled.connect(self._highlight_selection)
