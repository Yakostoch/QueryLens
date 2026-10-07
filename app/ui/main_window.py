import sqlite3
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from app.storage.history import HistoryStore
from app.storage.preferences import Preferences
from app.ui.dialog.settings_dialog import SettingsDialog
from app.ui.dialog.connection_dialog import ConnectionDialog
from app.ui.theme import apply_theme
from app.ui.widgets.history_panel import HistoryPanel
from app.ui.widgets.sql_editor import SqlEditor
from app.ui.widgets.database_panel import DatabasePanel
from app.ui.widgets.tools_panel import ToolsPanel
from app.ui.widgets.icons import make_icon
from app.ui.collection_worker import CollectionWorker


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, data_dir=None, preferences=None):
        super().__init__()
        self.setWindowTitle("QueryLens — SQL Workspace")
        self.resize(1320, 800)
        self.setMinimumSize(960, 600)
        self.preferences = preferences if preferences is not None else Preferences()
        self.history_store = None
        self.storage_error = None
        self.connection = None
        self.connection_parameters = None
        self.collection_worker = None
        self._closing_after_collection = False
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
        self.tagline = QtWidgets.QLabel("Рабочее пространство SQL")
        self.tagline.setObjectName("muted")
        header.addWidget(self.tagline)
        header.addStretch()
        navigation = QtWidgets.QFrame()
        navigation.setObjectName("navigation")
        nav_layout = QtWidgets.QHBoxLayout(navigation)
        nav_layout.setContentsMargins(4, 4, 4, 4)
        nav_layout.setSpacing(4)
        self.nav_group = QtWidgets.QButtonGroup(self)
        self.nav_buttons = {}
        for index, (name, icon) in enumerate((("SQL", "sql"), ("Подключение", "database"),
                                               ("Анализ БД", "chart"), ("Инструменты", "tools"))):
            button = QtWidgets.QPushButton(name)
            button.setIcon(make_icon(icon))
            button.setIconSize(QtCore.QSize(20, 20))
            button.setObjectName("navButton")
            button.setCheckable(True)
            button.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            button.setAccessibleName(name)
            self.nav_group.addButton(button, index)
            self.nav_buttons[name] = button
            nav_layout.addWidget(button)
        self.nav_group.idClicked.connect(self.navigate)
        self.nav_buttons["SQL"].setChecked(True)
        header.addWidget(navigation)
        header.addStretch()
        self.badge = QtWidgets.QLabel("ДЕМО")
        self.badge.setObjectName("badge")
        header.addWidget(self.badge)
        self.settings_button = QtWidgets.QPushButton("Настройки")
        self.settings_button.setIcon(make_icon("settings"))
        self.settings_button.clicked.connect(self.open_settings)
        header.addWidget(self.settings_button)
        layout.addLayout(header)

        connection_bar = QtWidgets.QFrame()
        connection_bar.setObjectName("connectionBar")
        connection_layout = QtWidgets.QHBoxLayout(connection_bar)
        connection_layout.setContentsMargins(16, 10, 16, 10)
        connection_layout.setSpacing(12)
        db_icon = QtWidgets.QLabel()
        db_icon.setPixmap(make_icon("database").pixmap(22, 22))
        connection_layout.addWidget(db_icon)
        self.connection_label = QtWidgets.QLabel("СУБД не подключена")
        connection_layout.addWidget(self.connection_label)
        self.connection_state = QtWidgets.QLabel("●  Нет подключения")
        self.connection_state.setObjectName("muted")
        connection_layout.addWidget(self.connection_state)
        connection_layout.addStretch()
        connection_details = QtWidgets.QLabel("Версия: —   |   Схема: —")
        connection_details.setObjectName("muted")
        connection_layout.addWidget(connection_details)
        self.connect_button = QtWidgets.QPushButton("Подключить")
        self.connect_button.clicked.connect(self.open_connection)
        connection_layout.addWidget(self.connect_button)
        layout.addWidget(connection_bar)

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
        self.pages = QtWidgets.QStackedWidget()
        self.pages.addWidget(self.splitter)
        self.database_panel = DatabasePanel()
        self.pages.addWidget(self.scroll_page(self.database_panel))
        self.tools_panel = ToolsPanel()
        self.pages.addWidget(self.scroll_page(self.tools_panel))
        self.database_panel.connect_button.clicked.connect(self.open_connection)
        self.database_panel.tools_button.clicked.connect(lambda: self.navigate(3))
        self.database_panel.run_button.clicked.connect(self.start_collection)
        self.database_panel.cancel_button.clicked.connect(self.cancel_collection)
        self.tools_panel.analysis_button.clicked.connect(lambda: self.navigate(2))
        self.tools_panel.selection_changed.connect(self.update_tool_selection)
        self.update_tool_selection()
        layout.addWidget(self.pages, 1)

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

    def navigate(self, index):
        if index == 1:
            self.open_connection()
            return
        page = {0: 0, 2: 1, 3: 2}[index]
        self.pages.setCurrentIndex(page)
        self.nav_group.button(index).setChecked(True)
        self.status.setVisible(index == 0)
        if index == 0:
            self.editor.setFocus()

    def open_connection(self):
        if self.collection_worker is not None:
            return
        self.nav_buttons["Подключение"].setChecked(True)
        dialog = ConnectionDialog(self)
        if dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted and dialog.connection is not None:
            if self.connection is not None:
                self.connection.close()
            self.connection = dialog.connection
            self.connection_parameters = dialog.parameters
            self.connection_label.setText(f"PostgreSQL: {dialog.database.text().strip()}")
            self.connection_state.setText("●  Подключено")
            self.connect_button.setText("Переподключиться")
            self.status.setText("Подключение к PostgreSQL установлено")
            self.database_panel.clear_report()
            self.refresh_collection_button()
        self.nav_group.button({0: 0, 1: 2, 2: 3}[self.pages.currentIndex()]).setChecked(True)
        dialog.deleteLater()

    def update_tool_selection(self):
        names = []
        if self.tools_panel.config_switch.isChecked():
            names.append("Config Analyzer")
        if self.tools_panel.hardware_switch.isChecked():
            names.append("Hardware Analyzer")
        self.database_panel.set_tools(names)
        self.refresh_collection_button()

    def refresh_collection_button(self):
        config = self.tools_panel.config_switch.isChecked()
        hardware = self.tools_panel.hardware_switch.isChecked()
        self.database_panel.run_button.setEnabled(
            self.collection_worker is None and (config or hardware)
            and (not config or self.connection_parameters is not None)
        )

    def start_collection(self):
        if not self.database_panel.run_button.isEnabled() or self.collection_worker is not None:
            return
        config = self.tools_panel.config_switch.isChecked()
        hardware = self.tools_panel.hardware_switch.isChecked()
        self.database_panel.clear_report()
        self.database_panel.hint.setText("Собираю локальные показатели… Hardware: примерно 5 секунд. SQL не выполняется.")
        self.collection_worker = CollectionWorker(self.connection_parameters, config, hardware, self)
        self.collection_worker.report_ready.connect(self.collection_ready)
        self.collection_worker.finished.connect(self.collection_finished)
        self.database_panel.cancel_button.show()
        self.tools_panel.setEnabled(False)
        self.refresh_collection_button()
        self.collection_worker.start()

    def cancel_collection(self):
        if self.collection_worker is not None:
            self.collection_worker.requestInterruption()
            self.database_panel.hint.setText("Отмена… Текущий запрос к каталогам ограничен таймаутом.")

    def collection_ready(self, report):
        if self._closing_after_collection:
            return
        self.database_panel.show_report(report)
        self.database_panel.hint.setText("Сбор завершён с ошибками отдельных модулей." if report["errors"]
                                         else "Сбор завершён. Ниже — факты, а не диагноз конкретного SQL.")

    def collection_finished(self):
        interrupted = self.collection_worker.isInterruptionRequested()
        self.collection_worker.deleteLater()
        self.collection_worker = None
        self.database_panel.cancel_button.hide()
        self.tools_panel.setEnabled(True)
        self.refresh_collection_button()
        if interrupted:
            self.database_panel.hint.setText("Сбор отменён. Можно запустить заново.")
        if self._closing_after_collection:
            self.close()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "tagline"):
            self.tagline.setVisible(event.size().width() >= 1250)
            self.badge.setVisible(event.size().width() >= 1100)

    @staticmethod
    def scroll_page(widget):
        scroll = QtWidgets.QScrollArea()
        scroll.setObjectName("pageScroll")
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(widget)
        return scroll

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
        if self.pages.currentIndex() != 0:
            return
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
        if self.collection_worker is not None:
            self._closing_after_collection = True
            self.cancel_collection()
            event.ignore()
            return
        self.connection_parameters = None
        if self.connection is not None:
            self.connection.close()
        if self.history_store is not None:
            self.history_store.close()
        super().closeEvent(event)
