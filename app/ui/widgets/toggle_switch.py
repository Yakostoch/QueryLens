from PySide6 import QtCore, QtGui, QtWidgets


class ToggleSwitch(QtWidgets.QCheckBox):
    """A keyboard-accessible checkbox painted as a compact switch."""
    def __init__(self, accessible_name, parent=None):
        super().__init__(parent)
        self.setAccessibleName(accessible_name)
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(48, 28)

    def hitButton(self, position):
        return self.rect().contains(position)

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        color = self.palette().link().color() if self.isChecked() else self.palette().mid().color()
        if not self.isEnabled():
            color.setAlpha(100)
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawRoundedRect(QtCore.QRectF(2, 3, 44, 22), 11, 11)
        painter.setBrush(QtGui.QColor("#ffffff"))
        painter.drawEllipse(QtCore.QRectF(26 if self.isChecked() else 5, 6, 16, 16))
        if self.hasFocus():
            painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            painter.setPen(QtGui.QPen(self.palette().link().color(), 1, QtCore.Qt.PenStyle.DotLine))
            painter.drawRoundedRect(QtCore.QRectF(0.5, 0.5, 47, 27), 13, 13)
