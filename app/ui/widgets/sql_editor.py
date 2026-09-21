import math
import re

from PySide6 import QtCore, QtGui, QtWidgets

from app.sql_context import python_position, qt_position, scan_sql
from app.ui.theme import COLORS
from app.ui.widgets.sql_highlighter import KEYWORDS, SqlHighlighter


class LineNumbers(QtWidgets.QWidget):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor

    def sizeHint(self):
        return QtCore.QSize(self.editor.number_area_width(), 0)

    def paintEvent(self, event):
        self.editor.paint_line_numbers(event)


class SqlEditor(QtWidgets.QPlainTextEdit):
    """Редактор с номерами строк, отступами и подсветкой текущей строки."""

    context_changed = QtCore.Signal()
    font_size_changed = QtCore.Signal(int)

    def __init__(self):
        super().__init__()
        font = QtGui.QFont("Consolas")
        font.setPointSize(11)
        self.setFont(font)
        self.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.NoWrap)
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance(" ") * 4)

        self.highlighter = SqlHighlighter(self.document())
        self._context = scan_sql("")
        self._statement_color = QtGui.QColor(COLORS["dark"]["statement"])
        self._hover_candidate = None
        self._hover_statement = None
        self._hover_timer = QtCore.QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.setInterval(500)
        self._hover_timer.timeout.connect(self.show_hover_statement)
        self._wheel_remainder = 0.0
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.completer = QtWidgets.QCompleter(sorted(KEYWORDS), self)
        self.completer.setWidget(self)
        self.completer.setCaseSensitivity(QtCore.Qt.CaseSensitivity.CaseInsensitive)
        self.completer.setCompletionMode(QtWidgets.QCompleter.CompletionMode.PopupCompletion)
        self.completer.setMaxVisibleItems(6)
        self.completer.activated[str].connect(self.insert_completion)
        self.verticalScrollBar().valueChanged.connect(self.completer.popup().hide)
        self.horizontalScrollBar().valueChanged.connect(self.completer.popup().hide)
        self.verticalScrollBar().valueChanged.connect(self.reset_hover)
        self.horizontalScrollBar().valueChanged.connect(self.reset_hover)

        self.numbers = LineNumbers(self)
        self.blockCountChanged.connect(self.update_number_margin)
        self.updateRequest.connect(self.update_number_area)
        self.cursorPositionChanged.connect(self.highlight_current_line)
        self.cursorPositionChanged.connect(self.update_context)
        self.selectionChanged.connect(self.update_context)
        self.textChanged.connect(self.update_context)
        self.update_number_margin()
        self.highlight_current_line()

    def set_font_size(self, size):
        size = max(10, min(20, int(size)))
        self.reset_hover()
        self.completer.popup().hide()
        font = self.font()
        font.setPointSize(size)
        self.setFont(font)
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance(" ") * 4)
        self.update_number_margin()
        self.numbers.update()

    def refresh_theme(self, theme="dark"):
        self._statement_color = QtGui.QColor(COLORS[theme]["statement"])
        self.highlighter.set_theme(theme)
        self.highlight_current_line()
        self.numbers.update()
        self.viewport().update()

    def update_context(self):
        text = self.toPlainText()
        if text != self._context.text:
            self.reset_hover()
            self._context = scan_sql(text)
        self.completer.popup().hide()
        self.viewport().update()
        self.context_changed.emit()

    def current_statement(self):
        return self._context.current(python_position(self._context.text, self.textCursor().position()))

    def sql_for_analysis(self):
        cursor = self.textCursor()
        if cursor.hasSelection():
            return cursor.selectedText().replace("\u2029", "\n")
        current = self.current_statement()
        if current is None:
            return ""
        statement = current[1]
        return self._context.text[statement.start:statement.end]

    def context_label(self):
        if self.textCursor().hasSelection():
            return "Выделение"
        current = self.current_statement()
        return f"Запрос {current[0] + 1}/{len(self._context.statements)}" if current else "Нет запроса"

    def paintEvent(self, event):
        super().paintEvent(event)
        statement = self._hover_statement
        if statement is None:
            return
        text = self._context.text
        start = qt_position(text, statement.start)
        end = qt_position(text, statement.end)
        block = self.firstVisibleBlock()
        region = QtGui.QRegion()
        width = self.viewport().width()
        while block.isValid():
            bounds = self.blockBoundingGeometry(block).translated(self.contentOffset())
            if bounds.top() > self.viewport().height():
                break
            block_start = block.position()
            block_end = block_start + block.length()
            if block.isVisible() and block_start < end and block_end > start:
                left, right = 2, width - 3
                cursor = QtGui.QTextCursor(self.document())
                if start >= block_start:
                    cursor.setPosition(start)
                    left = max(left, self.cursorRect(cursor).left() - 2)
                if end < block_end:
                    cursor.setPosition(end)
                    right = min(right, self.cursorRect(cursor).left() + 3)
                if right > left:
                    top = math.floor(bounds.top())
                    bottom = math.ceil(bounds.bottom())
                    region |= QtGui.QRegion(QtCore.QRect(left, top, right - left, bottom - top))
            block = block.next()
        painter = QtGui.QPainter(self.viewport())
        painter.setClipRect(event.rect())
        painter.setPen(QtGui.QPen(self._statement_color, 1))
        path = QtGui.QPainterPath()
        path.addRegion(region)
        painter.drawPath(path.simplified())

    def reset_hover(self, *_):
        self._hover_timer.stop()
        self._hover_candidate = None
        self._hover_statement = None
        self.viewport().update()

    def statement_at_point(self, point):
        if not self.viewport().rect().contains(point):
            return None
        cursor = self.cursorForPosition(point)
        block = cursor.block()
        bounds = self.blockBoundingGeometry(block).translated(self.contentOffset())
        if not block.text().strip() or not bounds.top() <= point.y() < bounds.bottom():
            return None
        # cursorForPosition clamps to the nearest text: exclude the empty area
        # to the right of a line and below the final line of the document.
        edge = QtGui.QTextCursor(block)
        left = self.cursorRect(edge).left()
        edge.movePosition(QtGui.QTextCursor.MoveOperation.EndOfBlock)
        right = self.cursorRect(edge).left()
        if not left <= point.x() <= right + 2:
            return None
        position = cursor.position()
        if position > block.position() and (cursor.atBlockEnd() or point.x() < self.cursorRect(cursor).left()):
            position -= 1
        position = python_position(self._context.text, position)
        return next((statement for statement in self._context.statements
                     if statement.terminated and statement.start <= position < statement.end), None)

    def mouseMoveEvent(self, event):
        super().mouseMoveEvent(event)
        if event.buttons() != QtCore.Qt.MouseButton.NoButton:
            self.reset_hover()
            return
        candidate = self.statement_at_point(event.position().toPoint())
        if candidate != self._hover_candidate:
            self.reset_hover()
            self._hover_candidate = candidate
            if candidate is not None:
                self._hover_timer.start()

    def show_hover_statement(self):
        point = self.viewport().mapFromGlobal(QtGui.QCursor.pos())
        if self._hover_candidate is not None and self.statement_at_point(point) == self._hover_candidate:
            self._hover_statement = self._hover_candidate
            self.viewport().update()
        else:
            self.reset_hover()

    def viewportEvent(self, event):
        if event.type() in (QtCore.QEvent.Type.Leave, QtCore.QEvent.Type.Hide) and hasattr(self, "_hover_timer"):
            self.reset_hover()
        return super().viewportEvent(event)

    def mousePressEvent(self, event):
        self.reset_hover()
        super().mousePressEvent(event)

    def wheelEvent(self, event):
        if event.modifiers() & QtCore.Qt.KeyboardModifier.ControlModifier:
            # Accumulate small deltas from high-resolution wheels / touchpads.
            delta = event.angleDelta().y() / 120 if event.angleDelta().y() else event.pixelDelta().y() / 40
            if delta * self._wheel_remainder < 0:
                self._wheel_remainder = 0.0
            self._wheel_remainder += delta
            steps = math.trunc(self._wheel_remainder)
            self._wheel_remainder -= steps
            size = max(10, min(20, self.font().pointSize() + steps))
            if size != self.font().pointSize():
                self.set_font_size(size)
                self.font_size_changed.emit(size)
            event.accept()
            return
        self._wheel_remainder = 0.0
        super().wheelEvent(event)

    def completion_prefix(self):
        cursor = self.textCursor()
        text = self.toPlainText()
        position = python_position(text, cursor.position())
        if cursor.hasSelection() or not self._context.allows_completion(position):
            return ""
        match = re.search(r"\b[A-Za-z_][A-Za-z_0-9]*$", text[:position])
        if not match or (match.start() > 0 and text[match.start() - 1] in ".$:@"):
            return ""
        return match.group()

    def show_completions(self, force=False):
        prefix = self.completion_prefix()
        if not prefix or (len(prefix) < 2 and not force):
            self.completer.popup().hide()
            return False
        self.completer.setCompletionPrefix(prefix)
        if not self.completer.completionCount():
            self.completer.popup().hide()
            return False
        self.completer.popup().setCurrentIndex(self.completer.completionModel().index(0, 0))
        rect = self.cursorRect()
        rect.setWidth(max(180, self.completer.popup().sizeHintForColumn(0) + 24))
        self.completer.complete(rect)
        self.position_completion_popup()
        return True

    def position_completion_popup(self):
        popup = self.completer.popup()
        cursor = self.cursorRect()
        anchor = self.viewport().mapToGlobal(cursor.topLeft())
        screen = QtGui.QGuiApplication.screenAt(anchor) or self.screen()
        available = screen.availableGeometry()
        gap = 6
        row_height = max(popup.sizeHintForRow(0), popup.fontMetrics().height() + 4)
        rows = min(self.completer.completionCount(), self.completer.maxVisibleItems())
        desired_height = rows * row_height + 2 * popup.frameWidth() + 2
        width = min(max(180, popup.sizeHintForColumn(0) + 24), available.width())
        space_above = anchor.y() - gap - available.top()
        if space_above >= row_height + 2 * popup.frameWidth():
            height = min(desired_height, space_above)
            top = anchor.y() - gap - height
        else:
            # At the top edge of the screen keep the input line unobstructed.
            top = self.viewport().mapToGlobal(cursor.bottomLeft()).y() + gap
            height = min(desired_height, max(1, available.bottom() - top + 1))
        left = max(available.left(), min(anchor.x(), available.right() - width + 1))
        popup.setGeometry(left, top, width, height)

    def insert_completion(self, completion):
        prefix = self.completion_prefix()
        if not prefix:
            return
        text = self.toPlainText()
        cursor = self.textCursor()
        position = python_position(text, cursor.position())
        suffix = re.match(r"[A-Za-z_0-9]*", text[position:]).group()
        cursor.beginEditBlock()
        cursor.setPosition(qt_position(text, position - len(prefix)))
        cursor.setPosition(qt_position(text, position + len(suffix)), QtGui.QTextCursor.MoveMode.KeepAnchor)
        cursor.insertText(completion)
        cursor.endEditBlock()
        self.setTextCursor(cursor)
        self.completer.popup().hide()

    def number_area_width(self):
        digits = len(str(max(1, self.blockCount())))
        return 16 + self.fontMetrics().horizontalAdvance("9") * digits

    def update_number_margin(self, *_):
        self.setViewportMargins(self.number_area_width(), 0, 0, 0)
        rect = self.contentsRect()
        self.numbers.setGeometry(
            rect.left(), rect.top(), self.number_area_width(), rect.height()
        )

    def update_number_area(self, rect, dy):
        if dy:
            self.numbers.scroll(0, dy)
        else:
            self.numbers.update(0, rect.y(), self.numbers.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self.update_number_margin()

    def resizeEvent(self, event):
        if hasattr(self, "_hover_timer"):
            self.reset_hover()
        super().resizeEvent(event)
        self.update_number_margin()

    def paint_line_numbers(self, event):
        painter = QtGui.QPainter(self.numbers)
        painter.fillRect(event.rect(), self.palette().alternateBase())
        painter.setPen(self.palette().color(QtGui.QPalette.ColorRole.PlaceholderText))
        painter.setFont(self.font())
        block = self.firstVisibleBlock()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        while block.isValid() and top <= event.rect().bottom():
            height = round(self.blockBoundingRect(block).height())
            if block.isVisible() and top + height >= event.rect().top():
                painter.drawText(
                    0, top, self.numbers.width() - 8, self.fontMetrics().height(),
                    QtCore.Qt.AlignmentFlag.AlignRight, str(block.blockNumber() + 1)
                )
            top += height
            block = block.next()

    def highlight_current_line(self):
        selection = QtWidgets.QTextEdit.ExtraSelection()
        color = self.palette().color(QtGui.QPalette.ColorRole.Highlight)
        color.setAlpha(25)
        selection.format.setBackground(color)
        selection.format.setProperty(QtGui.QTextFormat.Property.FullWidthSelection, True)
        selection.cursor = self.textCursor()
        selection.cursor.clearSelection()
        self.setExtraSelections([selection])

    def keyPressEvent(self, event):
        self.reset_hover()
        popup = self.completer.popup()
        if popup.isVisible() and event.key() == QtCore.Qt.Key.Key_Escape:
            popup.hide()
            return
        if event.key() == QtCore.Qt.Key.Key_Tab and not event.modifiers():
            if popup.isVisible():
                completion = popup.currentIndex().data() or self.completer.currentCompletion()
                self.insert_completion(completion)
            elif self.show_completions(force=True):
                self.insert_completion(self.completer.currentCompletion())
            else:
                self.insertPlainText("    ")
            return
        if (event.key() == QtCore.Qt.Key.Key_Space
                and event.modifiers() == QtCore.Qt.KeyboardModifier.ControlModifier):
            self.show_completions(force=True)
            return
        super().keyPressEvent(event)
        if event.text() or event.key() in (QtCore.Qt.Key.Key_Backspace, QtCore.Qt.Key.Key_Delete):
            self.show_completions()
