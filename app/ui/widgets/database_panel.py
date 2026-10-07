from PySide6 import QtCore, QtWidgets


class DatabasePanel(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        title = QtWidgets.QLabel("Анализ базы данных")
        title.setObjectName("brand")
        layout.addWidget(title)
        header = QtWidgets.QHBoxLayout()
        header.setSpacing(12)
        self.tools_button = QtWidgets.QPushButton("Выбор инструментов")
        header.addWidget(self.tools_button)
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
        self.hint = QtWidgets.QLabel("Выберите инструменты. Config требует локальную БД; Hardware можно запустить отдельно.")
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
        self.tables.setHeaderLabels(["Схема / таблица", "Поля", "Индексы"])
        self.tables.setRootIsDecorated(False)
        self.tables.setAccessibleName("Структура базы данных")
        self.tables.header().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.tables.header().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.tables.header().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        structure, structure_layout = self.make_card("Структура базы данных")
        structure_layout.addWidget(self.tables, 1)
        structure_hint = QtWidgets.QLabel("Структура появится после сбора Config Analyzer. Число индексов включает записи каталогов разделов.")
        structure_hint.setWordWrap(True)
        structure_hint.setObjectName("muted")
        structure_layout.addWidget(structure_hint)
        report, report_layout = self.make_card("Собранные факты и пояснения")
        report_layout.setSpacing(8)
        self.report = QtWidgets.QPlainTextEdit()
        self.report.setReadOnly(True)
        self.report.setAccessibleName("Локальный отчёт компьютера и PostgreSQL")
        self.report.setPlaceholderText("Нажмите «Собрать показатели».\nHardware: пять измерений примерно за 5 секунд.\nSQL и LLM пока не подключены.")
        report_layout.addWidget(self.report, 1)
        self.connect_button = QtWidgets.QPushButton("Подключить СУБД")
        self.connect_button.setObjectName("primary")
        report_layout.addWidget(self.connect_button, 0, QtCore.Qt.AlignmentFlag.AlignHCenter)
        tools_title = QtWidgets.QLabel("Выбранные инструменты")
        tools_title.setObjectName("section")
        report_layout.addWidget(tools_title)
        self.tool_states = {}
        for name in ("Config Analyzer", "Hardware Analyzer"):
            row = QtWidgets.QHBoxLayout()
            row.addWidget(QtWidgets.QLabel(name))
            row.addStretch()
            state = QtWidgets.QLabel()
            state.setObjectName("toolState")
            row.addWidget(state)
            self.tool_states[name] = state
            report_layout.addLayout(row)
        preview = QtWidgets.QLabel("Локальный сбор · Отчёт в памяти · Диагностика SQL — следующий этап")
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

    def set_tools(self, names):
        for name, label in self.tool_states.items():
            selected = name in names
            label.setText("Выбран" if selected else "Выключен")
            label.setProperty("selected", selected)
            label.style().unpolish(label)
            label.style().polish(label)
            label.update()

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
        if db:
            rows = db["tables"]
            self.metric_values["Таблицы"].setText(str(len(rows)))
            self.metric_values["Индексы"].setText(str(sum(row["indexes"] for row in rows)))
            self.metric_values["Размер БД"].setText(size(db.get("database_size_bytes")))
            for row in rows:
                self.tables.addTopLevelItem(QtWidgets.QTreeWidgetItem(
                    [f"{row['schema']}.{row['name']}", str(row["columns"]), str(row["indexes"])]
                ))
