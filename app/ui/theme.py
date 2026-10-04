from PySide6 import QtGui


COLORS = {
    "dark": dict(bg="#0b1422", panel="#121e30", field="#0d1726", text="#e0e8ff",
                 muted="#99b2d9", border="#24364f", accent="#4b8cff", button="#2459ed",
                 hover="#182e55", selection="#233f73", statement="#b5e875"),
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
        "ToolTipBase": c["panel"], "ToolTipText": c["text"], "Mid": c["border"], "Link": c["accent"],
    }.items():
        palette.setColor(getattr(QtGui.QPalette.ColorRole, role), QtGui.QColor(color))
    app.setPalette(palette)
    app.setStyleSheet("""
        QWidget { color: %(text)s; }
        QMainWindow, QDialog { background: %(bg)s; }
        QLabel#brand { font-size: 25px; font-weight: 700; }
        QLabel#muted, QLabel#status { color: %(muted)s; }
        QLabel#section { font-size: 14px; font-weight: 600; }
        QLabel#metricValue { font-size: 28px; font-weight: 600; color: %(accent)s; }
        QLabel#badge { color: %(accent)s; background: %(hover)s;
                       border-radius: 6px; padding: 5px 10px; }
        QFrame#card { background: %(panel)s; border: 1px solid %(border)s;
                       border-radius: 12px; }
        QFrame#navigation, QFrame#connectionBar { background: %(panel)s;
            border: 1px solid %(border)s; border-radius: 9px; }
        QScrollArea#pageScroll { background: transparent; border: none; }
        QScrollArea#pageScroll > QWidget > QWidget { background: %(bg)s; }
        QPushButton#navButton { background: transparent; border: 1px solid transparent;
            font-weight: 400; padding: 10px 12px; }
        QPushButton#navButton:hover { background: %(hover)s; }
        QPushButton#navButton:checked { background: %(hover)s; border: 1px solid %(accent)s; }
        QPlainTextEdit, QListWidget, QTreeWidget, QLineEdit, QComboBox, QSpinBox {
            background: %(field)s; border: 1px solid %(border)s;
            border-radius: 7px; padding: 8px; selection-background-color: %(selection)s;
        }
        QPlainTextEdit:focus, QLineEdit:focus { border: 1px solid %(accent)s; }
        QPushButton { background: %(panel)s; border: 1px solid %(border)s;
                      border-radius: 7px; padding: 9px 14px; font-weight: 600; }
        QPushButton:hover { background: %(hover)s; border-color: %(accent)s; }
        QPushButton#primary { background: %(button)s; color: white; border-color: %(button)s; }
        QPushButton#primary:hover { background: #426fd8; }
        QPushButton:disabled, QPushButton#primary:disabled {
            background: %(field)s; color: %(muted)s; border-color: %(border)s; }
        QHeaderView::section { background: %(panel)s; color: %(muted)s;
            border: none; border-bottom: 1px solid %(border)s; padding: 10px 8px; }
        QTreeWidget { outline: none; }
        QComboBox QAbstractItemView { background: %(panel)s; color: %(text)s;
            selection-background-color: %(selection)s; border: 1px solid %(border)s; }
        QScrollBar:vertical { background: %(field)s; width: 10px; margin: 0; }
        QScrollBar::handle:vertical { background: %(border)s; min-height: 28px; border-radius: 5px; }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
        QListWidget { border: none; padding: 0; }
        QListWidget::item { padding: 10px 7px; border-bottom: 1px solid %(border)s; }
        QListWidget::item:selected { background: %(selection)s; color: %(text)s; }
        QListWidget::item:hover { background: %(hover)s; }
        QSplitter::handle { background: %(bg)s; }
        QToolTip { background: %(panel)s; color: %(text)s; border: 1px solid %(border)s; }
    """ % c)
