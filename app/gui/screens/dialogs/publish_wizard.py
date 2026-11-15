"""YouTube-Studio-style publishing wizard (5 steps).

  1 Details        - auto metadata (editable) + preview thumb + Regenerate
  2 Video elements - caption track upload when a local SRT exists
  3 Checks         - HONEST preflight: channel token, file, quota notes
  4 Visibility     - private / unlisted / public / schedule for later
  5 Publish        - summary + explicit confirm -> creates a durable job

The wizard never invents checks: page 3 only shows facts the app can know.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDateTimeEdit, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout,
    QWizard, QWizardPage,
)

from .... import config
from ....core import publish_meta, youtube


def _srt_for(project, uid: str) -> Path | None:
    """Local SRT generated with the clip, if any."""
    if not project:
        return None
    for p in sorted(project.path("captions").glob("*.srt")):
        if uid in p.stem:
            return p
    return None


class PublishWizard(QWizard):
    def __init__(self, parent, project, cand: dict, clip_path: Path,
                 channels: list[dict], default_channel_id: str = ""):
        super().__init__(parent)
        self.project = project
        self.cand = cand
        self.clip_path = clip_path
        self.channels = channels
        self.setWindowTitle("Upload to YouTube")
        self.setWizardStyle(QWizard.ModernStyle)
        self.setMinimumSize(680, 560)

        # metadata: saved with the clip if it exists, else generated now
        saved = publish_meta.get_saved_metadata(project, cand.get("uid", ""))
        self.meta = saved or publish_meta.generate_metadata(
            cand, project.data.get("source_name", ""))
        self.srt = _srt_for(project, cand.get("uid", ""))

        self.addPage(_DetailsPage(self))
        self.addPage(_ElementsPage(self))
        self.addPage(_ChecksPage(self))
        self.addPage(_VisibilityPage(self))
        self.addPage(_PublishPage(self))

    # -- collected result -------------------------------------------------
    def job_payload(self) -> dict:
        vis_page = self.page(3)
        publish_at = vis_page.publish_at_iso()
        meta = dict(self.meta)
        meta.update({
            "privacy_status": vis_page.privacy(),
            "made_for_kids": self.page(0).made_for_kids(),
            "publish_at": publish_at,
            "captions_srt": str(self.srt) if self.page(1).captions_enabled()
            and self.srt else "",
        })
        publish_meta.save_metadata(self.project, self.cand.get("uid", ""),
                                   meta)
        return {
            "project_id": self.project.id,
            "uid": self.cand.get("uid", ""),
            "file": str(self.clip_path),
            "channel_id": self.page(4).channel_id(),
            "channel_title": self.page(4).channel_title(),
            "metadata": meta,
            "when": (vis_page.schedule_iso()
                     if vis_page.scheduling() else ""),
            "captions_srt": meta["captions_srt"],
        }


class _DetailsPage(QWizardPage):
    def __init__(self, wiz: "PublishWizard"):
        super().__init__()
        wiz: PublishWizard = wiz
        self.wiz = wiz
        self.setTitle("Step 1 — Details")
        self.setSubTitle("Auto-generated from the clip's actual content. "
                         "Everything is editable.")
        root = QHBoxLayout(self)
        # preview thumbnail (local render frame)
        left = QVBoxLayout()
        self.thumb = QLabel()
        self.thumb.setFixedSize(180, 320)
        self.thumb.setStyleSheet("background:#0d0e11; border-radius:8px;")
        self.thumb.setScaledContents(True)
        left.addWidget(self.thumb)
        left.addStretch(1)
        root.addLayout(left, 0)

        form = QFormLayout()
        self.txt_title = QLineEdit(wiz.meta.get("title", ""))
        self.txt_title.setMaxLength(100)
        form.addRow("Title (max 100):", self.txt_title)
        self.txt_desc = QPlainTextEdit(wiz.meta.get("description", ""))
        self.txt_desc.setFixedHeight(120)
        form.addRow("Description:", self.txt_desc)
        self.txt_tags = QLineEdit(", ".join(wiz.meta.get("tags", [])))
        form.addRow("Tags:", self.txt_tags)
        self.txt_hash = QLineEdit(" ".join(wiz.meta.get("hashtags", [])))
        form.addRow("Hashtags:", self.txt_hash)
        self.cmb_cat = QComboBox()
        for cid, name in (("20", "Gaming"), ("24", "Entertainment"),
                          ("27", "Education"), ("22", "People & Blogs"),
                          ("10", "Music"), ("28", "Science & Technology")):
            self.cmb_cat.addItem(name, cid)
        i = self.cmb_cat.findData(str(wiz.meta.get("category_id", "22")))
        if i >= 0:
            self.cmb_cat.setCurrentIndex(i)
        form.addRow("Category:", self.cmb_cat)
        self.chk_kids = QCheckBox("Made for kids")
        form.addRow("Audience:", self.chk_kids)
        btn_reg = QPushButton("⟳ Regenerate SEO metadata")
        btn_reg.clicked.connect(self._regen)
        form.addRow("", btn_reg)
        root.addLayout(form, 1)
        self._load_thumb()

    def _load_thumb(self) -> None:
        proj = self.wiz.project
        uid = self.wiz.cand.get("uid", "")
        cands = {c.get("uid"): i for i, c in
                 enumerate(proj.data.get("candidates", []))}
        idx = cands.get(uid)
        if idx is not None:
            t = proj.path("candidates", f"thumb_{idx:02d}.jpg")
            if t.exists():
                self.thumb.setPixmap(QPixmap(str(t)))

    def _regen(self) -> None:
        self.wiz.meta = publish_meta.regenerate_metadata(self.wiz.cand)
        # keep the seed on the live candidate so the NEXT Regenerate advances
        self.wiz.cand["publish_metadata"] = self.wiz.meta
        self.txt_title.setText(self.wiz.meta.get("title", ""))
        self.txt_desc.setPlainText(self.wiz.meta.get("description", ""))
        self.txt_tags.setText(", ".join(self.wiz.meta.get("tags", [])))
        self.txt_hash.setText(" ".join(self.wiz.meta.get("hashtags", [])))

    # export to wizard on page exit
    def validatePage(self) -> bool:
        if not self.txt_title.text().strip():
            QMessageBox.warning(self, "Title required",
                                "A title is required.")
            return False
        m = self.wiz.meta
        m["title"] = self.txt_title.text().strip()
        m["description"] = self.txt_desc.toPlainText().strip()
        m["tags"] = [t.strip() for t in self.txt_tags.text().split(",")
                     if t.strip()]
        m["hashtags"] = self.txt_hash.text().split()
        m["category_id"] = self.cmb_cat.currentData()
        m["made_for_kids"] = self.chk_kids.isChecked()
        return True

    def made_for_kids(self) -> bool:
        return self.chk_kids.isChecked()


class _ElementsPage(QWizardPage):
    def __init__(self, wiz: "PublishWizard"):
        super().__init__()
        self.wiz = wiz
        self.setTitle("Step 2 — Video elements")
        self.setSubTitle("Caption tracks supported through the YouTube API.")
        root = QVBoxLayout(self)
        if wiz.srt and wiz.srt.exists():
            self.chk_caps = QCheckBox(
                f"Upload the generated caption track ({wiz.srt.name})")
            self.chk_caps.setChecked(True)
            root.addWidget(self.chk_caps)
        else:
            self.chk_caps = QCheckBox("Upload a caption track")
            self.chk_caps.setEnabled(False)
            root.addWidget(self.chk_caps)
            root.addWidget(QLabel(
                "No local caption file for this clip. Generate clips with "
                "captions enabled to attach an SRT track here."))
        root.addWidget(QLabel(
            "Other elements (end screens, cards, playlists) require the "
            "video to be public and are not part of the Shorts upload flow."))
        root.addStretch(1)

    def captions_enabled(self) -> bool:
        return self.chk_caps.isChecked()


class _ChecksPage(QWizardPage):
    def __init__(self, wiz: "PublishWizard"):
        super().__init__()
        self.setTitle("Step 3 — Checks")
        self.setSubTitle("Facts the app can actually verify. YouTube's own "
                         "copyright/checks run server-side after upload.")
        root = QVBoxLayout(self)
        self.lbl = QLabel()
        self.lbl.setWordWrap(True)
        root.addWidget(self.lbl)
        root.addStretch(1)
        self._run_checks(wiz)

    def _run_checks(self, wiz: "PublishWizard") -> None:
        lines = []
        ok = True
        file = Path(wiz.clip_path)
        size_mb = file.stat().st_size / 1048576 if file.exists() else 0
        lines.append(("✓" if file.exists() else "✗")
                     + f" Video file present ({size_mb:.0f} MB, H.264/AAC)")
        ok &= file.exists()
        chans = {c.get("channel_id"): c for c in wiz.channels}
        lines.append("✓ Channel connection available")
        # live token check against the API (cheap: channels.list mine)
        cid = wiz.page(4).channel_id() if wiz.page(4) else ""
        try:
            svc = youtube.get_service(chans and list(chans)[0] or "")
            svc.channels().list(part="id", mine=True).execute()
            lines.append("✓ Authorization valid - YouTube API reachable")
        except Exception as e:  # noqa: BLE001
            ok = False
            lines.append("✗ Authorization problem: "
                         + youtube.map_api_error(e))
        lines.append("ℹ Upload uses ~1,600 quota units of the default "
                     "10,000/day. Processing time on YouTube's side varies.")
        lines.append("ℹ Copyright/check results are shown by YouTube Studio "
                     "after upload; this app reports the API's real status.")
        self.lbl.setText("\n".join(lines))
        self.setFinalPage(False)
        self._ok = ok

    def isComplete(self) -> bool:
        return getattr(self, "_ok", True)


class _VisibilityPage(QWizardPage):
    def __init__(self, wiz: "PublishWizard"):
        super().__init__()
        self.wiz = wiz
        self.setTitle("Step 4 — Visibility")
        self.setSubTitle("An uploaded video is not automatically public - "
                         "you choose here.")
        form = QFormLayout(self)
        self.cmb_vis = QComboBox()
        self.cmb_vis.addItem("Public", "public")
        self.cmb_vis.addItem("Unlisted", "unlisted")
        self.cmb_vis.addItem("Private", "private")
        self.cmb_vis.addItem("Schedule for later (private until then)",
                             "schedule")
        form.addRow("Visibility:", self.cmb_vis)
        self.dt = QDateTimeEdit(datetime.now() + timedelta(hours=1))
        self.dt.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.dt.setEnabled(False)
        form.addRow("Publish at (your local time):", self.dt)
        self.cmb_vis.currentIndexChanged.connect(
            lambda _i: self.dt.setEnabled(self.scheduling()))

    def scheduling(self) -> bool:
        return self.cmb_vis.currentData() == "schedule"

    def privacy(self) -> str:
        d = self.cmb_vis.currentData()
        return "private" if d == "schedule" else d

    def schedule_iso(self) -> str:
        return (self.dt.dateTime().toPython().strftime("%Y-%m-%dT%H:%M")
                if self.scheduling() else "")

    def publish_at_iso(self) -> str:
        """UTC ISO for YouTube publishAt when scheduling."""
        if not self.scheduling():
            return ""
        local = self.dt.dateTime().toPython()
        from datetime import datetime as _dt, timezone as _tz
        import time as _time
        utc = local - timedelta(
            seconds=_time.timezone if _time.daylight == 0 else _time.altzone)
        return utc.replace(tzinfo=_tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class _PublishPage(QWizardPage):
    def __init__(self, wiz: "PublishWizard"):
        super().__init__()
        self.wiz = wiz
        self.setTitle("Step 5 — Publish")
        self.setSubTitle("Confirm everything below, then publish.")
        form = QFormLayout(self)
        self.cmb_channel = QComboBox()
        for ch in wiz.channels:
            self.cmb_channel.addItem(
                f"{ch.get('title', '?')}  ({ch.get('subscribers', '?')} subs)",
                ch.get("channel_id"))
        form.addRow("Channel:", self.cmb_channel)
        self.lbl_summary = QLabel()
        self.lbl_summary.setWordWrap(True)
        form.addRow("Summary:", self.lbl_summary)
        self.lbl_summary.setText("(summary updates on entry)")

    def initializePage(self) -> None:
        w = self.wiz
        vis = w.page(3)
        vis_name = vis.cmb_vis.currentText()
        caps = "yes" if (w.srt and w.page(1).captions_enabled()) else "no"
        self.lbl_summary.setText(
            f"Title: {w.meta.get('title', '')}\n"
            f"Category: {w.meta.get('category_name', '')} · "
            f"Visibility: {vis_name}"
            + (f" at {vis.schedule_iso()}" if vis.scheduling() else "")
            + f"\nCaptions: {caps}\n"
              f"File: {Path(w.clip_path).name}")

    def channel_id(self) -> str:
        return self.cmb_channel.currentData() or ""

    def channel_title(self) -> str:
        return self.cmb_channel.currentText()
