"""Customize workspace: processing mode + full-video customization.

Sections: Source · Processing Mode · AI Voice & Language · Captions ·
Title Overlay · Background Music · Orientation · Process button.
Short Clips mode keeps the existing flow untouched.
"""
from __future__ import annotations

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QFileDialog, QGridLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QRadioButton, QPushButton,
    QScrollArea, QSlider, QVBoxLayout, QWidget,
)

from ... import config
from ...core import music, translate, urlimport


class CustomizeScreen(QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        self._music_player = QMediaPlayer(self)
        self._music_audio = QAudioOutput(self)
        self._music_player.setAudioOutput(self._music_audio)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        root = QVBoxLayout(inner)
        root.setContentsMargins(32, 24, 32, 20)
        root.setSpacing(12)

        title = QLabel("Customize")
        title.setObjectName("h1")
        self.lbl_source = QLabel("No video loaded - use Upload Video first.")
        self.lbl_source.setObjectName("muted")
        root.addWidget(title)
        root.addWidget(self.lbl_source)

        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(10)

        # ---- processing mode -------------------------------------------------
        gM = QGroupBox("Processing mode")
        vm = QVBoxLayout(gM)
        self.grp_mode = QButtonGroup(self)
        self.rb_clips = QRadioButton(
            "Short Clips - find the best moments (existing workflow)")
        self.rb_full = QRadioButton(
            "Full Video - customize and process the entire video")
        self.rb_clips.setChecked(True)
        self.grp_mode.addButton(self.rb_clips)
        self.grp_mode.addButton(self.rb_full)
        vm.addWidget(self.rb_clips)
        vm.addWidget(self.rb_full)
        grid.addWidget(gM, 0, 0)

        # ---- source + URL import --------------------------------------------
        gS = QGroupBox("Video source")
        vs = QVBoxLayout(gS)
        self.thumb = QLabel()
        self.thumb.setFixedSize(180, 101)
        self.thumb.setStyleSheet("background:#0d0e11; border-radius:6px;")
        self.thumb.setScaledContents(True)
        vs.addWidget(self.thumb)
        btn_url = QPushButton("Import video from URL…")
        btn_url.clicked.connect(self._import_url)
        vs.addWidget(btn_url)
        vs.addStretch(1)
        grid.addWidget(gS, 0, 1)

        # ---- AI voice + language --------------------------------------------
        gV = QGroupBox("AI voice (replaces original audio)")
        vv = QVBoxLayout(gV)
        self.chk_voice = QCheckBox("Replace original voice with AI narration")
        self.chk_voice.setChecked(True)
        vv.addWidget(self.chk_voice)
        prow = QHBoxLayout()
        prow.addWidget(QLabel("Voice preset:"))
        self.cmb_preset = QComboBox()
        from ...core import tts as tts_mod
        self._presets = tts_mod.list_presets()
        for p in self._presets:
            self.cmb_preset.addItem(f"{p.label} - {p.category}", p.id)
        prow.addWidget(self.cmb_preset, 1)
        self.btn_sample = QPushButton("▶")
        self.btn_sample.setFixedWidth(40)
        self.btn_sample.setToolTip("Play voice sample")
        self.btn_sample.clicked.connect(self._play_voice_sample)
        prow.addWidget(self.btn_sample)
        vv.addLayout(prow)
        srow = QHBoxLayout()
        srow.addWidget(QLabel("Speed"))
        self.sld_speed = QSlider(Qt.Horizontal)
        self.sld_speed.setRange(-5, 5)
        srow.addWidget(self.sld_speed)
        srow.addWidget(QLabel("Volume"))
        self.sld_vol = QSlider(Qt.Horizontal)
        self.sld_vol.setRange(10, 100)
        self.sld_vol.setValue(100)
        srow.addWidget(self.sld_vol)
        vv.addLayout(srow)
        lrow = QHBoxLayout()
        self.chk_translate = QCheckBox("Translate narration to:")
        self.chk_translate.toggled.connect(
            lambda on: self.cmb_lang.setEnabled(on))
        lrow.addWidget(self.chk_translate)
        self.cmb_lang = QComboBox()
        self.cmb_lang.setEnabled(False)
        self._fill_languages()
        lrow.addWidget(self.cmb_lang)
        lrow.addStretch(1)
        vv.addLayout(lrow)
        note = QLabel("Voices are generated locally (Windows voices shown "
                      "with their real names). Translation uses offline "
                      "Argos packs downloaded to D: on first use.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        vv.addWidget(note)
        grid.addWidget(gV, 1, 0)

        # ---- orientation + captions + title ----------------------------------
        gO = QGroupBox("Formats, captions & text overlay")
        vo = QVBoxLayout(gO)
        frow = QHBoxLayout()
        self.chk_169 = QCheckBox("Landscape 16:9 (1920x1080)")
        self.chk_169.setChecked(True)
        self.chk_916 = QCheckBox("Portrait 9:16 (1080x1920)")
        frow.addWidget(self.chk_169)
        frow.addWidget(self.chk_916)
        frow.addStretch(1)
        vo.addLayout(frow)
        self.chk_caps = QCheckBox("Subtitles / captions (uses the selected "
                                  "template from Settings)")
        self.chk_caps.setChecked(True)
        vo.addWidget(self.chk_caps)
        trow = QHBoxLayout()
        trow.addWidget(QLabel("Intro title overlay (first 4 s):"))
        self.txt_title = QLineEdit()
        self.txt_title.setPlaceholderText("optional - e.g. 10 Focus Lessons")
        trow.addWidget(self.txt_title, 1)
        vo.addLayout(trow)
        grid.addWidget(gO, 1, 1)

        # ---- background music --------------------------------------------------
        gMu = QGroupBox("Background music (optional)")
        vmu = QVBoxLayout(gMu)
        self.grp_music = QButtonGroup(self)
        self.rb_music_off = QRadioButton("No background music")
        self.rb_music_on = QRadioButton("Enable background music:")
        self.rb_music_off.setChecked(True)
        self.grp_music.addButton(self.rb_music_off)
        self.grp_music.addButton(self.rb_music_on)
        vmu.addWidget(self.rb_music_off)
        vmu.addWidget(self.rb_music_on)
        self.music_panel = QWidget()
        mp = QVBoxLayout(self.music_panel)
        mp.setContentsMargins(24, 0, 0, 0)
        self._music_players: dict[str, tuple[QMediaPlayer, QAudioOutput]] = {}
        for cat in music.list_categories():
            row = QHBoxLayout()
            lbl = QLabel(f"{cat['label']} - {cat['desc']}")
            row.addWidget(lbl, 1)
            rb = QRadioButton()
            rb.setToolTip(f"Select {cat['label']}")
            self.grp_music.addButton(rb)
            rb.setProperty("cat", cat["id"])
            row.addWidget(rb)
            btn_play = QPushButton("▶")
            btn_play.setFixedWidth(36)
            btn_play.clicked.connect(
                lambda _=False, cid=cat["id"]: self._play_music(cid))
            row.addWidget(btn_play)
            mp.addLayout(row)
        mrow = QHBoxLayout()
        mrow.addWidget(QLabel("Volume"))
        self.sld_music_vol = QSlider(Qt.Horizontal)
        self.sld_music_vol.setRange(1, 40)
        self.sld_music_vol.setValue(16)
        mrow.addWidget(self.sld_music_vol)
        mrow.addWidget(QLabel("Fade (s)"))
        self.sld_fade = QSlider(Qt.Horizontal)
        self.sld_fade.setRange(0, 8)
        self.sld_fade.setValue(3)
        mrow.addWidget(self.sld_fade)
        mp.addLayout(mrow)
        vmu.addWidget(self.music_panel)
        self.music_panel.setEnabled(False)
        self.rb_music_on.toggled.connect(self.music_panel.setEnabled)
        grid.addWidget(gMu, 2, 0)

        # ---- export / run --------------------------------------------------------
        gX = QGroupBox("Process full video")
        vx = QVBoxLayout(gX)
        self.lbl_info = QLabel("Processing renders the complete original "
                               "video with your selected customizations. "
                               "The original file is never modified.")
        self.lbl_info.setObjectName("muted")
        self.lbl_info.setWordWrap(True)
        vx.addWidget(self.lbl_info)
        self.btn_process = QPushButton("Process Full Video")
        self.btn_process.setObjectName("primary")
        self.btn_process.clicked.connect(self._process)
        vx.addWidget(self.btn_process)
        grid.addWidget(gX, 2, 1)

        root.addLayout(grid)
        root.addStretch(1)
        scroll.setWidget(inner)
        outer.addWidget(scroll)

        self.rb_clips.toggled.connect(self._mode_changed)

    # ------------------------------------------------------------------
    def _fill_languages(self) -> None:
        from ...core import translate as tr
        langs = ["es", "fr", "de", "pt", "it", "hi", "ar", "ru", "ja",
                 "zh", "ko", "tr"]
        names = {"es": "Spanish", "fr": "French", "de": "German",
                 "pt": "Portuguese", "it": "Italian", "hi": "Hindi",
                 "ar": "Arabic", "ru": "Russian", "ja": "Japanese",
                 "zh": "Chinese", "ko": "Korean", "tr": "Turkish"}
        pairs = tr.available_pairs()
        for code in langs:
            has = any(f == "en" and t == code for f, t in pairs)
            self.cmb_lang.addItem(f"{names.get(code, code)} ({code})"
                                  + ("" if has else " - pack downloads "
                                                    "on first use"), code)

    def _play_voice_sample(self) -> None:
        pid = self.cmb_preset.currentData()
        preset = next((p for p in self._presets if p.id == pid), None)
        if preset is None:
            return
        try:
            from ...core import tts as tts_mod
            wav = tts_mod.synthesize_sample(
                preset, config.TEMP_DIR / "render" / f"vs_{preset.id}.wav")
            if not hasattr(self, "_voice_player"):
                self._voice_player = QMediaPlayer(self)
                self._voice_audio = QAudioOutput(self)
                self._voice_player.setAudioOutput(self._voice_audio)
            self._voice_player.stop()
            self._voice_player.setSource(QUrl.fromLocalFile(str(wav)))
            self._voice_player.play()
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Voice sample failed", str(e))

    def _play_music(self, category: str) -> None:
        try:
            wav = music.get_bgm(category)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Music preview failed", str(e))
            return
        self._music_player.stop()
        self._music_player.setSource(QUrl.fromLocalFile(str(wav)))
        self._music_player.play()

    def _import_url(self) -> None:
        """Import a video from a YouTube/direct URL (yt-dlp / stream)."""
        from PySide6.QtWidgets import QInputDialog
        url, ok = QInputDialog.getText(
            self, "Import video from URL", "Paste a YouTube or direct URL:")
        if not ok or not url.strip():
            return
        url = url.strip()
        from ...core import urlimport as ui
        self.lbl_source.setText("Validating link…")
        try:
            info = ui.probe_info(url)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Import failed", str(e))
            self.lbl_source.setText("URL import failed.")
            return
        self.lbl_source.setText(
            f"URL OK: {info['title']} "
            f"({info.get('duration', 0) / 60:.1f} min)")
        if not self.main.confirm(
                "Download video",
                f"Download '{info['title']}' now? It is saved under "
                "D:\\AI_Clip_Generator\\downloads."):
            return
        self.lbl_source.setText("Downloading… 0%")
        try:
            path = ui.download(url, progress_cb=lambda f: (
                self.lbl_source.setText(f"Downloading… {f * 100:.0f}%")))
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Download failed", str(e))
            self.lbl_source.setText("Download failed.")
            return
        self.lbl_source.setText(f"Imported: {Path(path).name}")
        from ...core.ffmpeg_tools import extract_metadata
        try:
            md = extract_metadata(Path(path))
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Invalid video", str(e))
            return
        self.main.screens["upload"].set_video(Path(path), md)
        self.main.go("upload")

    def _mode_changed(self) -> None:
        self.main.settings.processing_mode = (
            "full" if self.rb_full.isChecked() else "clips")

    def collect(self) -> None:
        """Copy UI choices into Settings (full-video options)."""
        s = self.main.settings
        s.processing_mode = ("full" if self.rb_full.isChecked() else "clips")
        s.fv_voice = self.chk_voice.isChecked()
        if self.cmb_preset.currentData():
            s.voice_preset = self.cmb_preset.currentData()
        s.tts_speed = self.sld_speed.value()
        s.tts_volume = self.sld_vol.value()
        s.fv_translate = self.chk_translate.isChecked()
        if self.cmb_lang.currentData():
            s.fv_lang = self.cmb_lang.currentData()
        s.fv_captions = self.chk_caps.isChecked()
        s.fv_title = self.txt_title.text().strip()
        s.music_enabled = self.rb_music_on.isChecked()
        sel = self.grp_music.checkedButton()
        if sel is not None and sel.property("cat"):
            s.music_category = sel.property("cat")
        s.bgm_volume = self.sld_music_vol.value() / 100.0
        s.music_fade = float(self.sld_fade.value())
        fmts = []
        if self.chk_169.isChecked():
            fmts.append("16:9")
        if self.chk_916.isChecked():
            fmts.append("9:16")
        s.fv_formats = fmts or ["16:9"]
        s.aspect_ratio = s.fv_formats[0]
        s.apply_workflow_choices()

    def _process(self) -> None:
        self.collect()
        self.main.settings.save()
        proj = self.main.pending_project or self.main.project
        if proj is None:
            QMessageBox.information(
                self, "No video", "Upload or import a video first.")
            return
        if not self.chk_169.isChecked() and not self.chk_916.isChecked():
            QMessageBox.warning(self, "No format",
                                "Select at least one output format.")
            return
        if self.main.confirm(
                "Process full video",
                "Render the complete video with your customizations? "
                "Long videos take a while (runs in the background, "
                "cancellable)."):
            self.main.process_full_video(proj)

    def on_show(self) -> None:
        proj = self.main.pending_project or self.main.project
        if proj:
            self.lbl_source.setText(
                f"Source: {proj.data.get('source_name', '')} - project "
                f"{proj.id}")
            cands = proj.data.get("candidates", [])
            for i, _c in enumerate(cands):
                t = proj.path("candidates", f"thumb_{i:02d}.jpg")
                if t.exists():
                    pm = QPixmap(str(t))
                    if not pm.isNull():
                        self.thumb.setPixmap(pm)
                    break
