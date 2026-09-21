from PySide6 import QtGui


COLORS = {
    "dark": dict(bg="#10151f", panel="#192130", field="#121a27", text="#e7edf7",
                 muted="#99a9c1", border="#2a374b", accent="#81a6ff", button="#345fc5",
                 hover="#254068", selection="#304d78", statement="#b5e875"),
    "light": dict(bg="#f0f3f9", panel="#ffffff", field="#f8faff", text="#1c2940",
                  muted="#596b85", border="#d6deeb", accent="#345fc5", button="#345fc5",
                  hover="#e5edff", selection="#cfdefc", statement="#6b9e19"),
}


def apply_theme(app, name):
    c = COLORS[name]
    palette = QtGui.QPalette()
    for role, color in {
        "Window": c["bg"], "WindowText": c["text"], "Base": c["field"],
        "AlternateBase": c["panel"], "Text": c["text"], "Button": c["panel"],
        "ButtonText": c["text"], "Highlight": c["selection"],
        "HighlightedText": c["text"], "PlaceholderText": c["muted"],
        "ToolTipBase": c["panel"], "ToolTipText": c["text"],
    }.items():
        palette.setColor(getattr(QtGui.QPalette.ColorRole, role), QtGui.QColor(color))
    app.setPalette(palette)
    app.setStyleSheet("""
        QWidget { color: %(text)s; }
        QMainWindow, QDialog { background: %(bg)s; }
        QLabel#brand { font-size: 25px; font-weight: 700; }
        QLabel#muted, QLabel#status { color: %(muted)s; }
        QLabel#section { font-size: 14px; font-weight: 600; }
        QLabel#badge { color: %(accent)s; background: %(hover)s;
                       border-radius: 6px; padding: 5px 10px; }
        QFrame#card { background: %(panel)s; border: 1px solid %(border)s;
                       border-radius: 12px; }
        QPlainTextEdit, QListWidget, QLineEdit, QComboBox, QSpinBox {
            background: %(field)s; border: 1px solid %(border)s;
            border-radius: 7px; padding: 8px; selection-background-color: %(selection)s;
        }
        QPlainTextEdit:focus, QLineEdit:focus { border: 1px solid %(accent)s; }
        QPushButton { background: %(panel)s; border: 1px solid %(border)s;
                      border-radius: 7px; padding: 9px 14px; font-weight: 600; }
        QPushButton:hover { background: %(hover)s; border-color: %(accent)s; }
        QPushButton#primary { background: %(button)s; color: white; border-color: %(button)s; }
        QPushButton#primary:hover { background: #426fd8; }
        QPushButton:disabled { color: %(muted)s; }
        QListWidget { border: none; padding: 0; }
        QListWidget::item { padding: 10px 7px; border-bottom: 1px solid %(border)s; }
        QListWidget::item:selected { background: %(selection)s; color: %(text)s; }
        QListWidget::item:hover { background: %(hover)s; }
        QSplitter::handle { background: %(bg)s; }
        QToolTip { background: %(panel)s; color: %(text)s; border: 1px solid %(border)s; }
    """ % c)
