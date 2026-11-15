"""AI Clip Generator - entry point.

Usage:  D:\\AI_Clip_Generator\\.venv\\Scripts\\python.exe -m app.main
        D:\\AI_Clip_Generator\\.venv\\Scripts\\python.exe app\\main.py
"""
from __future__ import annotations

import sys
from pathlib import Path

# Support both package execution (`python -m app.main`) and direct script runs
# (`python app/main.py`) when the repo root is not on sys.path yet.
if __package__ in (None, ""):
    project_root = Path(__file__).resolve().parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from app import config
else:
    from . import config

# Storage policy first: redirect temp/cache to D: before any heavy import.
config.ensure_layout()

if __package__ in (None, ""):
    from app.gui.main_window import MainWindow
else:
    from .gui.main_window import MainWindow

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication, QMessageBox


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("AI Clip Generator")
    app.setOrganizationName("AI Clip Generator")
    icon_file = config.resource_path("assets/app.png")
    if icon_file.exists():
        app.setWindowIcon(QIcon(str(icon_file)))
    font = QFont("Segoe UI", 10)
    app.setFont(font)

    ok, free_gb = config.check_drive_space(min_free_gb=1.0)
    if not ok:
        QMessageBox.critical(
            None, "AI Clip Generator",
            f"Project drive {config.ROOT} has only {free_gb} GB free.\n"
            "The application needs free space to store projects and temp "
            "files. Please free up space and restart.")
        return 1

    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
