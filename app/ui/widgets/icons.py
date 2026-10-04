from PySide6 import QtCore, QtGui


def make_icon(name, color="#99b5e5"):
    """Small outline icons, drawn at high DPI without external image assets."""
    pixmap = QtGui.QPixmap(48, 48)
    pixmap.setDevicePixelRatio(2)
    pixmap.fill(QtCore.Qt.GlobalColor.transparent)
    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    pen = QtGui.QPen(QtGui.QColor(color), 1.7)
    pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(QtCore.Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    if name == "sql":
        painter.drawPolyline(QtGui.QPolygonF([QtCore.QPointF(7, 7), QtCore.QPointF(2, 12), QtCore.QPointF(7, 17)]))
        painter.drawLine(QtCore.QPointF(14, 5), QtCore.QPointF(10, 19))
        painter.drawPolyline(QtGui.QPolygonF([QtCore.QPointF(17, 7), QtCore.QPointF(22, 12), QtCore.QPointF(17, 17)]))
    elif name == "database":
        painter.drawEllipse(QtCore.QRectF(4, 3, 16, 6))
        painter.drawLine(4, 6, 4, 18)
        painter.drawLine(20, 6, 20, 18)
        painter.drawArc(QtCore.QRectF(4, 9, 16, 6), 180 * 16, 180 * 16)
        painter.drawArc(QtCore.QRectF(4, 15, 16, 6), 180 * 16, 180 * 16)
    elif name == "chart":
        for x, top in ((3, 13), (10, 8), (17, 3)):
            painter.drawRoundedRect(QtCore.QRectF(x, top, 4, 21 - top), 0.7, 0.7)
    elif name == "tools":
        path = QtGui.QPainterPath()
        path.moveTo(14, 3)
        path.lineTo(11, 6)
        path.lineTo(12, 10)
        path.lineTo(16, 11)
        path.lineTo(20, 7)
        path.cubicTo(23, 14, 17, 18, 13, 15)
        path.lineTo(6, 22)
        path.lineTo(2, 18)
        path.lineTo(9, 11)
        path.cubicTo(7, 7, 9, 2, 14, 3)
        painter.drawPath(path)
    elif name == "settings":
        painter.drawEllipse(QtCore.QRectF(5, 5, 14, 14))
        painter.drawEllipse(QtCore.QRectF(9, 9, 6, 6))
        for x1, y1, x2, y2 in ((12, 2, 12, 5), (12, 19, 12, 22),
                                (2, 12, 5, 12), (19, 12, 22, 12),
                                (5, 5, 7, 7), (17, 17, 19, 19),
                                (5, 19, 7, 17), (17, 7, 19, 5)):
            painter.drawLine(x1, y1, x2, y2)
    painter.end()
    return QtGui.QIcon(pixmap)
