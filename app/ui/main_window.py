import sqlite3
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from app.storage.history import HistoryStore
from app.storage.preferences import Preferences
from app.ui.settings_dialog import SettingsDialog
from app.ui.theme import apply_theme
from app.ui.widgets.history_panel import HistoryPanel
from app.ui.widgets.sql_editor import SqlEditor


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, data_dir=None, preferences=None):
        super().__init__()
        self.setWindowTitle("QueryLens — SQL Workspace")
        self.resize(1320, 800)
        self.setMinimumSize(960, 600)
        self.preferences = preferences if preferences is not None else Preferences()
        self.history_store = None
        self.storage_error = None
        self._result_sql = None
        directory = Path(data_dir) if data_dir is not None else Path(
            QtCore.QStandardPaths.writableLocation(QtCore.QStandardPaths.StandardLocation.AppLocalDataLocation)
        )
        try:
            self.history_store = HistoryStore(directory / "history.sqlite3")
        except (OSError, sqlite3.Error) as error:
            self.storage_error = str(error)

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        layout = QtWidgets.QVBoxLayout(central)
        layout.setContentsMargins(24, 20, 24, 16)
        layout.setSpacing(18)
        header = QtWidgets.QHBoxLayout()
        brand = QtWidgets.QLabel("QueryLens")
        brand.setObjectName("brand")
        header.addWidget(brand)
        tagline = QtWidgets.QLabel("Рабочее пространство SQL")
        tagline.setObjectName("muted")
        header.addWidget(tagline)
        header.addStretch()
        badge = QtWidgets.QLabel("ДЕМО")
        badge.setObjectName("badge")
        header.addWidget(badge)
        self.settings_button = QtWidgets.QPushButton("Настройки")
        self.settings_button.clicked.connect(self.open_settings)
        header.addWidget(self.settings_button)
        layout.addLayout(header)

        self.history_panel = HistoryPanel()
        self.history_panel.search.textChanged.connect(self.refresh_history)
        self.history_panel.restore_requested.connect(self.restore_query)
        self.history_panel.delete_requested.connect(self.delete_query)
        self.editor = SqlEditor()
        self.editor.setAccessibleName("Редактор SQL")
        self.output = QtWidgets.QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setAccessibleName("Результат анализа")
        self.output.setPlaceholderText("Результаты появятся здесь\n\nВведите запрос слева и нажмите «Анализировать».")

        editor_card, editor_layout = self.make_card("SQL-редактор", "Запрос", self.editor)
        editor_footer = QtWidgets.QHBoxLayout()
        self.position_label = QtWidgets.QLabel()
        self.position_label.setObjectName("muted")
        editor_footer.addWidget(self.position_label)
        editor_footer.addStretch()
        self.analyze_button = QtWidgets.QPushButton("Анализировать")
        self.analyze_button.setObjectName("primary")
        self.analyze_button.setToolTip("Ctrl+Enter — анализировать выделение или запрос под курсором")
        self.analyze_button.clicked.connect(self.analyze)
        editor_footer.addWidget(self.analyze_button)
        editor_layout.addLayout(editor_footer)
        output_card, output_layout = self.make_card("Результат анализа", "Вывод", self.output)
        output_hint = QtWidgets.QLabel("Демо-режим · SQL не выполняется")
        output_hint.setObjectName("muted")
        output_layout.addWidget(output_hint)

        self.splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        self.splitter.setHandleWidth(12)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(self.history_panel)
        self.splitter.addWidget(editor_card)
        self.splitter.addWidget(output_card)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 3)
        self.splitter.setStretchFactor(2, 2)
        self.splitter.setSizes([260, 560, 420])
        layout.addWidget(self.splitter, 1)

        self.status = QtWidgets.QLabel("Готово к работе · Ctrl+Enter — анализировать")
        self.status.setObjectName("status")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.shortcut = QtGui.QShortcut(QtGui.QKeySequence("Ctrl+Return"), self)
        self.shortcut.activated.connect(self.analyze)
        self.editor.context_changed.connect(self.update_position)
        self.editor.textChanged.connect(self.mark_output_stale)
        self.editor.font_size_changed.connect(self.save_editor_font_size)
        self.apply_preferences()
        self.refresh_history()
        self.update_position()
        self.editor.setFocus()
        if self.storage_error:
            self.status.setText("История недоступна. Можно продолжить работу без сохранения.")
            self.status.setToolTip(self.storage_error)

    @staticmethod
    def make_card(title, badge_text, widget):
        card = QtWidgets.QFrame()
        card.setObjectName("card")
        card.setMinimumWidth(260)
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(16, 18, 16, 16)
        layout.setSpacing(14)
        header = QtWidgets.QHBoxLayout()
        label = QtWidgets.QLabel(title)
        label.setObjectName("section")
        header.addWidget(label)
        header.addStretch()
        badge = QtWidgets.QLabel(badge_text)
        badge.setObjectName("muted")
        header.addWidget(badge)
        layout.addLayout(header)
        layout.addWidget(widget, 1)
        return card, layout

    def update_position(self):
        cursor = self.editor.textCursor()
        self.position_label.setText(f"{self.editor.context_label()} · Стр. {cursor.blockNumber() + 1}")
        self.position_label.setToolTip(f"Столбец {cursor.positionInBlock() + 1} · Tab — дополнить слово")

    def mark_output_stale(self):
        if self.output.toPlainText() and self.editor.toPlainText() != self._result_sql:
            self.output.clear()
            self.status.setText("Запрос изменён · Запустите анализ заново")

    def analyze(self):
        sql = self.editor.sql_for_analysis()
        self._result_sql = self.editor.toPlainText()
        if not sql.strip():
            self.output.setPlainText("Нет запроса для анализа. Поместите курсор в SQL-запрос или выделите текст.")
            self.editor.setFocus()
            return
        result = (
            f"{self.editor.context_label()} · Запрос получен\n\n"
            "Анализатор пока не подключён.\n"
            "SQL не выполняется и не проверяется.\n\n"
            f"Строк: {len(sql.splitlines())}\n"
            f"Символов: {len(sql)}\n\n"
            "Здесь появятся найденные ошибки,\nпредупреждения и рекомендации.\n\n"
            f"SQL для анализа:\n{sql}"
        )
        self.output.setPlainText(result)
        if self.preferences.save_history and self.history_store is not None:
            try:
                self.history_store.add(sql, result)
            except sqlite3.Error as error:
                self.show_storage_error(error)
                return
            if sql.strip() == self.editor.toPlainText().strip():
                self.editor.document().setModified(False)
            self.status.setText("Запрос сохранён в истории · Результат демонстрационный")
            self.refresh_history()
        else:
            self.status.setText("Результат демонстрационный · Запрос не сохранён в истории")

    def refresh_history(self, *_):
        if self.history_store is not None:
            try:
                self.history_panel.set_entries(self.history_store.search(self.history_panel.search.text()))
            except sqlite3.Error as error:
                self.show_storage_error(error)

    def restore_query(self, entry):
        if self.editor.document().isModified() and self.editor.toPlainText().strip():
            answer = QtWidgets.QMessageBox.question(
                self, "Открыть запрос", "Заменить текущий текст запросом из истории?",
                QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
                QtWidgets.QMessageBox.StandardButton.No,
            )
            if answer != QtWidgets.QMessageBox.StandardButton.Yes:
                return
        self.editor.setPlainText(entry["sql"])
        self._result_sql = entry["sql"]
        self.output.setPlainText(entry["result"])
        self.editor.document().setModified(False)
        self.status.setText("Открыт запрос из истории · Показан сохранённый результат")

    def delete_query(self, query_id):
        if self.history_store is None:
            return
        try:
            self.history_store.delete(query_id)
        except sqlite3.Error as error:
            self.show_storage_error(error)
            return
        self.refresh_history()
        self.status.setText("Запись удалена из истории")

    def show_storage_error(self, error):
        self.status.setText("Не удалось обновить историю. Текст запроса остался в редакторе.")
        self.status.setToolTip(str(error))

    def open_settings(self):
        dialog = SettingsDialog(self.preferences, self)
        if dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            try:
                self.preferences.save(dialog.theme.currentData(), dialog.font_size.value(), dialog.history.isChecked())
            except OSError as error:
                QtWidgets.QMessageBox.warning(self, "Настройки", str(error))
            self.apply_preferences()

    def save_editor_font_size(self, size):
        self.output.setFont(self.editor.font())
        try:
            self.preferences.save(self.preferences.theme, size, self.preferences.save_history)
        except OSError as error:
            self.status.setText("Размер шрифта изменён, но сохранить настройку не удалось.")
            self.status.setToolTip(str(error))

    def apply_preferences(self):
        apply_theme(QtWidgets.QApplication.instance(), self.preferences.theme)
        self.editor.set_font_size(self.preferences.font_size)
        self.output.setFont(self.editor.font())
        self.editor.refresh_theme(self.preferences.theme)

    def closeEvent(self, event):
        if self.history_store is not None:
            self.history_store.close()
        super().closeEvent(event)
