"""Карточки объектов БД с поиском и читаемыми строками столбцов."""

from PySide6 import QtCore, QtWidgets

from app.ui.widgets.analysis_report import AnalysisReportView, ReportSection, text_label


class StructureCard(ReportSection):
    def __init__(self, table, *, query_scope=False):
        super().__init__(f"{table['schema']}.{table['name']}", expanded=False)
        self.table = table
        self.field_widgets = []
        self.loaded = False
        self.query = ""
        count = table.get("columns")
        total = table.get("schema_columns")
        fields = str(count) if count is not None else "нет данных"
        if query_scope and total is not None:
            fields += f" из {total}"
        caption = f"{'Поля запроса' if query_scope else 'Поля'}: {fields}"
        indexes = table.get("indexes")
        caption += f" · Индексы: {indexes if indexes is not None else 'нет данных'}"
        subtitle = text_label(caption)
        subtitle.setObjectName("structureSummary")
        self.header.removeWidget(self.title_label)
        titles = QtWidgets.QVBoxLayout()
        titles.setSpacing(4)
        titles.addWidget(self.title_label)
        titles.addWidget(subtitle)
        self.header.insertLayout(1, titles, 1)
        self.add_help(
            "О структуре объекта",
            "Столбцы и типы получены из каталогов PostgreSQL. "
            + (
                "Показаны поля, связанные с выбранным SQL, а не обязательно все поля объекта. "
                if query_scope
                else "Показаны поля в выбранной области сбора. "
            )
            + "NULL — допускает пустое значение; NOT NULL — запрещает. Число индексов по каталогу не доказывает их применимость к запросу.",
        )
        self.toggle.toggled.connect(self.load_fields)

    def load_fields(self, expanded):
        if not expanded or self.loaded:
            return
        self.loaded = True
        for field in self.table.get("fields", []):
            row = QtWidgets.QFrame()
            row.setObjectName("structureField")
            layout = QtWidgets.QVBoxLayout(row)
            layout.setContentsMargins(12, 10, 12, 10)
            layout.setSpacing(6)
            header = QtWidgets.QHBoxLayout()
            name = text_label(field["name"])
            name.setObjectName("structureFieldName")
            header.addWidget(name, 1)
            nullable = field.get("nullable")
            null_text = (
                "NULL"
                if nullable is True
                else ("NOT NULL" if nullable is False else "NULL: нет данных")
            )
            badge = QtWidgets.QLabel(null_text)
            badge.setObjectName("structureNull")
            badge.setProperty("required", nullable is False)
            badge.setToolTip(
                "Значение может быть NULL"
                if nullable is True
                else (
                    "NULL запрещён ограничением NOT NULL"
                    if nullable is False
                    else "Допустимость NULL неизвестна"
                )
            )
            header.addWidget(badge)
            layout.addLayout(header)
            data_type = text_label(field.get("type") or "Тип не получен")
            data_type.setObjectName("structureType")
            layout.addWidget(data_type)
            self.content.addWidget(row)
            self.field_widgets.append((row, field))
        if not self.field_widgets:
            self.add_text("Поля в выбранной области сбора не найдены.")
        self.filter_fields()

    def table_matches(self, query):
        return query in self.toggle.text().casefold()

    @staticmethod
    def field_matches(field, query):
        return any(query in str(field.get(key) or "").casefold() for key in ("name", "type"))

    def filter_fields(self):
        show_all = not self.query or self.table_matches(self.query)
        for row, field in self.field_widgets:
            row.setVisible(show_all or self.field_matches(field, self.query))

    def filter(self, query):
        self.query = query
        table_match = self.table_matches(query)
        field_match = any(
            self.field_matches(field, query) for field in self.table.get("fields", [])
        )
        visible = not query or table_match or field_match
        self.setVisible(visible)
        if query and not table_match and field_match:
            self.set_expanded(True)
        self.filter_fields()
        return visible


class DatabaseStructureView(AnalysisReportView):
    visible_count_changed = QtCore.Signal(int, int)

    def __init__(self):
        super().__init__()
        self.cards = []
        self.query = ""
        self.expansion_before_filter = []
        self.empty_label = None
        self.setAccessibleName("Структура базы данных")

    def clear(self):
        super().clear()
        self.cards = []
        self.query = ""
        self.expansion_before_filter = []
        self.empty_label = None

    def show_structure(self, db):
        self.clear()
        if not db or not db.get("metadata_collected", True):
            self.setPlainText("Соберите структуру и статистику БД, чтобы увидеть объекты и поля.")
            return
        for table in db.get("tables", []):
            card = StructureCard(table, query_scope=db.get("metadata_scope") == "query")
            self.cards.append(card)
            self.sections.append(card)
            self.layout.addWidget(card)
            if db.get("metadata_scope") == "query":
                card.set_expanded(True)
        self.empty_label = text_label("Объекты в выбранной области сбора не найдены.")
        self.empty_label.setObjectName("muted")
        self.layout.addWidget(self.empty_label)
        self.empty_label.setVisible(not self.cards)
        self.visible_count_changed.emit(len(self.cards), len(self.cards))

    def set_filter(self, text):
        query = text.strip().casefold()
        if query and not self.query:
            self.expansion_before_filter = [card.toggle.isChecked() for card in self.cards]
        self.query = query
        visible = sum(card.filter(query) for card in self.cards)
        if not query and self.expansion_before_filter:
            for card, expanded in zip(self.cards, self.expansion_before_filter):
                card.set_expanded(expanded)
            self.expansion_before_filter = []
        if self.empty_label:
            self.empty_label.setText(
                "Ничего не найдено. Измените название таблицы, поля или тип данных."
                if query
                else "Объекты в выбранной области сбора не найдены."
            )
            self.empty_label.setVisible(visible == 0)
        self.visible_count_changed.emit(visible, len(self.cards))
