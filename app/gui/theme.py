"""Application stylesheet - clean dark professional theme."""

QSS = """
QWidget {
    background: #16181d;
    color: #e8eaed;
    font-family: 'Segoe UI';
    font-size: 13px;
}
QMainWindow { background: #16181d; }

/* Left navigation */
QListWidget#nav {
    background: #1b1e24;
    border: none;
    border-right: 1px solid #262a31;
    font-size: 14px;
    outline: none;
}
QListWidget#nav::item {
    padding: 14px 18px;
    border-left: 3px solid transparent;
}
QListWidget#nav::item:selected {
    background: #232830;
    border-left: 3px solid #e5484d;
    color: #ffffff;
}
QListWidget#nav::item:hover { background: #20242b; }

/* Headers */
QLabel#h1 { font-size: 22px; font-weight: 600; }
QLabel#h2 { font-size: 16px; font-weight: 600; color: #aab2bf; }
QLabel#muted { color: #8a93a3; }

/* Buttons */
QPushButton {
    background: #2a2f38;
    border: 1px solid #363c47;
    border-radius: 6px;
    padding: 8px 16px;
}
QPushButton:hover { background: #333a45; }
QPushButton:pressed { background: #262b34; }
QPushButton#primary {
    background: #e5484d; border: none; color: white; font-weight: 600;
    padding: 10px 22px; font-size: 14px;
}
QPushButton#primary:hover { background: #ec5c61; }
QPushButton#primary:disabled { background: #4a3032; color: #7a6b6c; }
QPushButton#approve { background: #2d6a4f; border: none; color: white; }
QPushButton#approve:hover { background: #35805f; }
QPushButton#reject  { background: #5c2b2e; border: none; color: #ffb3b6; }

/* Inputs */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QDateTimeEdit {
    background: #20242b;
    border: 1px solid #363c47;
    border-radius: 6px;
    padding: 7px 10px;
    selection-background-color: #e5484d;
}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus { border-color: #5b6472; }
QComboBox::drop-down { border: none; width: 26px; }
QComboBox QAbstractItemView {
    background: #20242b; border: 1px solid #363c47;
    selection-background-color: #333a45;
}

/* Progress */
QProgressBar {
    background: #20242b; border: 1px solid #363c47; border-radius: 5px;
    text-align: center; height: 16px; color: #cfd6e0;
}
QProgressBar::chunk { background: #e5484d; border-radius: 4px; }

/* Lists & cards */
QListWidget#cards { background: transparent; border: none; }
QFrame#card {
    background: #1d2128; border: 1px solid #2a2f38; border-radius: 10px;
}
QFrame#card:hover { border-color: #3a4250; }

QScrollArea { border: none; }
QScrollBar:vertical { background: #16181d; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: #333a45; border-radius: 5px; min-height: 30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }

QStatusBar { background: #1b1e24; color: #8a93a3; }
QToolTip { background: #20242b; color: #e8eaed; border: 1px solid #363c47; }
"""
