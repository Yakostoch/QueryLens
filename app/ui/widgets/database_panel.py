from PySide6 import QtCore, QtWidgets
from app.ui.dialog.database_options_dialog import DatabaseOptionsDialog


class DatabasePanel(QtWidgets.QWidget):
    options_changed = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        title = QtWidgets.QLabel("Анализ базы данных")
        title.setObjectName("brand")
        layout.addWidget(title)
        self.options_dialog = DatabaseOptionsDialog(self)
        self.config_option = self.options_dialog.config_option
        self.metadata_option = self.options_dialog.metadata_option
        self.metadata_scope = self.options_dialog.metadata_scope
        self.metadata_scope.currentIndexChanged.connect(lambda index: self.options_changed.emit())
        for option in (self.config_option, self.metadata_option):
            option.toggled.connect(lambda checked: self.options_changed.emit())
        header = QtWidgets.QHBoxLayout()
        header.setSpacing(12)
        self.options_widget = QtWidgets.QPushButton("Выбрать, что анализировать")
        self.options_widget.clicked.connect(self.options_dialog.exec)
        header.addWidget(self.options_widget)
        self.run_button = QtWidgets.QPushButton("Собрать показатели")
        self.run_button.setObjectName("collectButton")
        self.run_button.setEnabled(False)
        self.run_button.setToolTip("Собрать выбранные показатели; SQL из редактора не выполняется")
        header.addWidget(self.run_button)
        self.cancel_button = QtWidgets.QPushButton("Отменить")
        self.cancel_button.hide()
        header.addWidget(self.cancel_button)
        header.addStretch()
        layout.addLayout(header)
        self.options_summary = QtWidgets.QLabel()
        self.options_summary.setObjectName("muted")
        self.options_summary.setWordWrap(True)
        self.options_changed.connect(self.update_options_summary)
        self.update_options_summary()
        layout.addWidget(self.options_summary)
        self.hint = QtWidgets.QLabel("Подключитесь к PostgreSQL и выберите данные для сбора.")
        self.hint.setObjectName("muted")
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        metrics = QtWidgets.QHBoxLayout()
        metrics.setSpacing(14)
        self.metric_values = {}
        for title, caption in (("Таблицы", "Структура базы данных"),
                               ("Индексы", "Доступные индексы"), ("Размер БД", "Объём данных")):
            card, body = self.make_card(title)
            value = QtWidgets.QLabel("—")
            value.setObjectName("metricValue")
            self.metric_values[title] = value
            body.addWidget(value)
            hint = QtWidgets.QLabel(caption)
            hint.setObjectName("muted")
            body.addWidget(hint)
            metrics.addWidget(card, 1)
        layout.addLayout(metrics)
        split = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        split.setHandleWidth(12)
        self.tables = QtWidgets.QTreeWidget()
        self.tables.setHeaderLabels(["Таблица / поле", "Поля / тип", "Индексы / NULL"])
        self.tables.setRootIsDecorated(True)
        self.tables.setAccessibleName("Структура базы данных")
        self.tables.header().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.tables.header().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.tables.header().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        structure, structure_layout = self.make_card("Структура базы данных")
        structure_layout.addWidget(self.tables, 1)
        structure_hint = QtWidgets.QLabel("Включите «Структура и статистика БД», чтобы получить таблицы и индексы.")
        structure_hint.setWordWrap(True)
        structure_hint.setObjectName("muted")
        structure_layout.addWidget(structure_hint)
        report, report_layout = self.make_card("Собранные факты и пояснения")
        report_layout.setSpacing(8)
        self.report = QtWidgets.QPlainTextEdit()
        self.report.setReadOnly(True)
        self.report.setAccessibleName("Отчёт PostgreSQL")
        self.report.setPlaceholderText("Нажмите «Собрать показатели».\nЗдесь появятся выбранные данные PostgreSQL.")
        report_layout.addWidget(self.report, 1)
        preview = QtWidgets.QLabel("Отчёт хранится в памяти · Ресурсы компьютера — на вкладке «Ресурсы»")
        preview.setObjectName("muted")
        preview.setWordWrap(True)
        report_layout.addWidget(preview)
        split.addWidget(structure)
        split.addWidget(report)
        split.setSizes([460, 660])
        layout.addWidget(split, 1)

    @staticmethod
    def make_card(title):
        card = QtWidgets.QFrame()
        card.setObjectName("card")
        body = QtWidgets.QVBoxLayout(card)
        body.setContentsMargins(20, 20, 20, 20)
        body.setSpacing(12)
        label = QtWidgets.QLabel(title)
        label.setObjectName("section")
        body.addWidget(label)
        return card, body

    def update_options_summary(self):
        selected = []
        if self.config_option.isChecked():
            selected.append("конфигурация PostgreSQL")
        if self.metadata_option.isChecked():
            selected.append("структура: " + self.metadata_scope.currentText().lower())
        self.options_summary.setText("Выбрано: " + ", ".join(selected) if selected else "Ничего не выбрано")

    def clear_report(self):
        self.report.clear()
        self.tables.clear()
        for value in self.metric_values.values():
            value.setText("—")

    def show_report(self, report):
        from app.services.report_formatter import format_report, size

        self.clear_report()
        self.report.setPlainText(format_report(report))
        db = report["postgres"]
        if db and db.get("metadata_collected", True):
            rows = db["tables"]
            self.metric_values["Таблицы"].setText(str(len(rows)))
            self.metric_values["Индексы"].setText(str(sum(row["indexes"] for row in rows)))
            self.metric_values["Размер БД"].setText(size(db.get("database_size_bytes")))
            for row in rows:
                item = QtWidgets.QTreeWidgetItem(
                    [f"{row['schema']}.{row['name']}", str(row["columns"]), str(row["indexes"])]
                )
                self.tables.addTopLevelItem(item)
                for field in row.get("fields", []):
                    item.addChild(QtWidgets.QTreeWidgetItem(
                        [field["name"], field["type"], "NULL" if field["nullable"] else "NOT NULL"]))
                if db.get("metadata_scope") == "query":
                    item.setExpanded(True)
