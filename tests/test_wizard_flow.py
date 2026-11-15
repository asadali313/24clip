"""Full wizard flow test (offscreen): construct -> walk 5 pages -> payload.

This reproduces exactly what clicking 'Upload to YouTube' does, so any
exception that would make the button 'not respond' is caught here.
Set RUN_REAL_UPLOAD=1 to also execute the real upload of the produced job.
"""
import os
import time
import sys

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, r"D:\AI_Clip_Generator")

from app import config
config.ensure_layout()

from PySide6.QtWidgets import QApplication

checks = []
def check(name, cond, extra=""):
    checks.append((name, bool(cond)))
    print(f"  {'PASS' if cond else 'FAIL'}: {name} {extra}")


def main() -> int:
    app = QApplication(sys.argv)

    from app import project_store
    from app.core import publish_meta, scheduler, youtube
    from app.gui.screens.dialogs.publish_wizard import PublishWizard

    proj = project_store.Project(config.PROJECTS_DIR / "project_001")
    cands = [c for c in proj.data.get("candidates", [])
             if (proj.path("exports", c.get("export_file", "")).exists())]
    check("clip with rendered file available", len(cands) >= 1)
    cand = cands[0]
    clip = proj.path("exports", cand["export_file"])
    channels = youtube.list_channels()
    check("channels loaded", len(channels) >= 1,
          f"({[c.get('title') for c in channels]})")

    print("=== constructing wizard (what the button click does) ===")
    try:
        wiz = PublishWizard(None, proj, cand, clip, channels)
        check("wizard constructed without exception", True)
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        check("wizard constructed without exception", False, str(e)[:120])
        return finish()

    # walk the pages exactly as a user would
    print("=== page walk ===")
    try:
        p1 = wiz.page(0)
        check("page1 details populated", bool(p1.txt_title.text()))
        p1.validatePage()

        p2 = wiz.page(1)
        check("page2 elements (captions option shown)", hasattr(p2, "chk_caps"))

        p3 = wiz.page(2)
        wiz.page(2).initializePage() if hasattr(wiz.page(2),
                                                "initializePage") else None
        check("page3 checks ran", hasattr(p3, "_ok"))
        # live API check inside page3 must have succeeded
        check("page3 preflight passed", getattr(p3, "_ok", False))

        p4 = wiz.page(3)
        p4.cmb_vis.setCurrentIndex(1)             # unlisted (test upload)
        check("page4 visibility selectable", p4.privacy() == "unlisted")

        p5 = wiz.page(4)
        p5.initializePage()
        check("page5 summary built", "Title:" in p5.lbl_summary.text())
        check("channel selected", p5.channel_id().startswith("UC"))
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        check("page walk completed", False, str(e)[:120])
        return finish()

    print("=== job payload ===")
    try:
        payload = wiz.job_payload()
        check("payload complete", all(k in payload for k in
              ("project_id", "uid", "file", "channel_id", "metadata", "when")))
        check("payload metadata persisted with clip",
              publish_meta.get_saved_metadata(proj, cand["uid"]) is not None)
        check("privacy from page4", payload["metadata"]["privacy_status"]
              == "unlisted")
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        check("payload built", False, str(e)[:120])
        return finish()

    # optional real upload through the SAME code path the dashboard uses
    if os.environ.get("RUN_REAL_UPLOAD") == "1":
        print("=== REAL UPLOAD via wizard payload ===")
        from app.workers.jobs import UploadWorker
        from PySide6.QtCore import QEventLoop, QTimer
        job = scheduler.PublishJob(
            id=scheduler.new_job_id(), project_id=payload["project_id"],
            uid=payload["uid"], file=payload["file"],
            channel_id=payload["channel_id"],
            channel_title=payload["channel_title"],
            title=payload["metadata"].get("title", ""),
            when=payload.get("when", ""),
            metadata=payload["metadata"],
            captions_srt=payload.get("captions_srt", ""))
        scheduler.add_job(job)
        stored = scheduler.get_job(job.id)
        worker = UploadWorker(stored)
        result = {}
        loop = QEventLoop()

        def on_ok(jid, url):
            result["url"] = url
            loop.quit()

        def on_fail(jid, err):
            result["error"] = err
            loop.quit()

        worker.finished_ok.connect(on_ok)
        worker.failed.connect(on_fail)
        worker.upload_progress.connect(
            lambda jid, f: print(f"    upload {f * 100:.0f}%"))
        worker.status_changed.connect(
            lambda jid, st: print(f"    status -> {st}"))
        worker.start()
        QTimer.singleShot(900000, loop.quit)      # 15 min hard cap
        loop.exec()
        check("real upload published", "url" in result,
              result.get("error", result.get("url", ""))[:120])
        if "url" in result:
            from app.core import youtube as yt
            vid = scheduler.get_job(job.id)["video_id"]
            # the worker keeps polling processing in its thread; the test
            # polls the API directly until YouTube confirms (or 8 min)
            st = {}
            for _ in range(48):
                st = yt.get_video_status(payload["channel_id"], vid)
                if st.get("upload_status") == "processed":
                    break
                time.sleep(10)
            check("YouTube confirms processed",
                  st.get("upload_status") == "processed",
                  f"({st.get('upload_status')}/{st.get('processing_status')})")
            print(f"    LINK: {result['url']}")

    return finish()


def finish() -> int:
    failed = [n for n, p in checks if not p]
    print(f"\n=== RESULT: {len(checks) - len(failed)}/{len(checks)} passed ===")
    if failed:
        print("FAILED:", failed)
        return 1
    print("WIZARD FLOW PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
