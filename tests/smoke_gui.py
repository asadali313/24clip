"""GUI smoke test: instantiate every screen offscreen, cycle through them."""
import os
import sys

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, r"D:\AI_Clip_Generator")

from app import config
config.ensure_layout()

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from app.gui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()

    errors = []
    # cycle through all nav screens
    for key in ("dashboard", "upload", "customize", "settings", "processing",
                "results", "publishing"):
        try:
            win.go(key)
            app.processEvents()
            print(f"screen OK: {key}")
        except Exception as e:  # noqa: BLE001
            errors.append(f"{key}: {type(e).__name__}: {e}")
            print(f"screen FAIL: {key}: {e}")

    # settings screen collect() round-trip
    try:
        ss = win.screens["settings"]
        ss.collect()
        assert 1 <= win.settings.clip_count <= 30
        assert win.settings.duration_mode in ("auto", "max30", "max45",
                                              "max60", "custom")
        assert win.settings.caption_mode in (
            "none", "simple", "pro", "classic", "beast", "gaming",
            "karaoke", "documentary", "modern", "podcast", "cinematic",
            "minimal", "custom")
        assert win.settings.voice_mode in ("keep", "remove", "file", "tts")
        assert hasattr(win.settings, "voice_preset")
        print("settings collect OK "
              f"(count={win.settings.clip_count}, "
              f"dur={win.settings.duration_mode}, "
              f"cap={win.settings.caption_mode}, "
              f"voice={win.settings.voice_mode})")
    except Exception as e:  # noqa: BLE001
        errors.append(f"settings collect: {type(e).__name__}: {e}")
        print(f"settings collect FAIL: {e}")

    # editor open/back
    try:
        win.open_editor({"uid": "cand_test", "start": 1.0, "end": 30.0,
                         "text": "test"})
        app.processEvents()
        win.back_to_results()
        app.processEvents()
        print("editor open/back OK")
    except Exception as e:  # noqa: BLE001
        errors.append(f"editor: {type(e).__name__}: {e}")
        print(f"editor FAIL: {e}")

    QTimer.singleShot(300, app.quit)
    app.exec()
    if errors:
        print("GUI SMOKE FAILED:", errors)
        return 1
    print("GUI SMOKE PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
