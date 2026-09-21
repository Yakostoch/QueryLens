import sys

from PySide6 import QtGui, QtWidgets

from app.ui.main_window import MainWindow


if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    app.setOrganizationName("QueryLens")
    app.setApplicationName("QueryLens")
    app.setStyle("Fusion")
    app.setFont(QtGui.QFont("Segoe UI", 10))

    widget = MainWindow()
    widget.show()

    sys.exit(app.exec())
