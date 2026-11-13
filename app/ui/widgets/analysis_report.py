"""Сворачиваемые разделы отчёта и справка по показателям PostgreSQL."""

import math
import re
from datetime import datetime
from html import escape

from PySide6 import QtCore, QtGui, QtWidgets

from app.services.analysis_presentation import MESSAGES, TITLES
from app.ui.widgets.sql_highlighter import SqlHighlighter

# Название и смысл показателя. Оценки отделены от фактических измерений.
METRICS = {
    "node_type": (
        "Операция",
        "Тип операции PostgreSQL: чтение, сортировка, соединение и другие узлы плана.",
    ),
    "path": (
        "Путь узла",
        "Положение операции в дереве плана; помогает сопоставить измерение с узлом EXPLAIN.",
    ),
    "total_cost": (
        "Оценочная стоимость",
        "Условная стоимость планировщика для получения всех строк узла. Это не миллисекунды и не измеренное время.",
    ),
    "plan_rows": (
        "Оценка строк",
        "Количество строк, которое планировщик ожидает на выходе одного выполнения узла. Это не число прочитанных строк.",
    ),
    "plan_width": ("Ширина строки, байт", "Оценочная средняя ширина выходной строки в байтах."),
    "estimated_bytes": (
        "Оценка объёма выхода, байт",
        "Оценка строк × ширина строки. Это объём выходных данных, а не потребление памяти операцией.",
    ),
    "execution_time_ms": (
        "Время на сервере, мс",
        "Execution Time — серверное выполнение под EXPLAIN ANALYZE, без передачи результата приложению. Измерение само может добавлять накладные расходы.",
    ),
    "planning_time_ms": (
        "Планирование, мс",
        "Planning Time — время PostgreSQL на построение и оптимизацию плана; отдельно от Execution Time.",
    ),
    "analysis_duration_ms": (
        "Затрачено на анализ, мс",
        "Интервал между началом и окончанием анализа QueryLens: получение плана, сбор контекста и диагностика. Это не время выполнения SQL на сервере.",
    ),
    "actual_rows_per_loop": (
        "Строк за цикл",
        "Actual Rows — среднее число выходных строк за одно выполнение узла. Усреднение и округление ограничивают точность.",
    ),
    "actual_loops": (
        "Количество циклов",
        "Loops — сколько раз выполнялся узел. При нуле узел не исполнялся.",
    ),
    "approximate_total_output_rows": (
        "Суммарный выход, приблизительно",
        "Actual Rows × loops: приблизительное число выходных строк за все циклы, а не уникальные строки или физические чтения.",
    ),
    "actual_total_time_ms_per_loop": (
        "Время узла за цикл, мс",
        "Среднее полное время узла за цикл, включая дочерние операции. Время разных узлов нельзя складывать по дереву.",
    ),
    "estimate_to_actual_ratio": (
        "Расхождение оценки и факта",
        "Большее из отношений оценки строк к факту и факта к оценке. Само по себе не доказывает причину замедления.",
    ),
    "row_comparison_status": (
        "Применимость сравнения строк",
        "LIMIT, semi/anti join, подзапросы и параллелизм могут ограничить чтение узлов. В таких случаях прямое сравнение оценки и факта ненадёжно.",
    ),
    "sort_space_type": (
        "Размещение сортировки",
        "Memory — сортировка в памяти; Disk — сортировка использовала диск в этом запуске.",
    ),
    "sort_space_used_kb": (
        "Объём сортировки, kB",
        "Объём памяти или дискового пространства сортировки; тип пространства указан отдельно.",
    ),
    "sort_method": (
        "Метод сортировки",
        "Алгоритм сортировки, выбранный PostgreSQL, например quicksort или external merge.",
    ),
    "disk_sort_observed": (
        "Сортировка на диске",
        "План фактического выполнения сообщает об использовании диска сортировкой этого узла.",
    ),
    "temporary_io_observed": (
        "Временный ввод-вывод",
        "Есть чтение или запись временных блоков. Это наблюдение, а не однозначное объяснение причины затрат.",
    ),
    "multi_batch_hash_observed": (
        "Хеширование несколькими пакетами",
        "Хеширование выполнялось несколькими batches. Проверьте объём входа, временный ввод-вывод и доступную память.",
    ),
    "worker_disk_sort_observed": (
        "Сортировка работника на диске",
        "Хотя бы один параллельный работник сообщил о сортировке с использованием диска.",
    ),
    "workers_planned": (
        "Работников запланировано",
        "Число параллельных работников, которое планировщик хотел использовать.",
    ),
    "workers_launched": (
        "Работников запущено",
        "Число фактически запущенных параллельных работников. Оно может быть меньше запланированного.",
    ),
    "runtime_counters": (
        "Счётчики узла",
        "Счётчики включают дочерние операции и повторные обращения. Их нельзя суммировать по дереву; worker-данные отдельно не прибавляются.",
    ),
    "worker_details": (
        "Параллельные работники",
        "Индивидуальные измерения работников. Уже учтены в общих показателях; не прибавляйте их повторно.",
    ),
    "incremental_sort_details": (
        "Инкрементальная сортировка",
        "Показатели групп полной и частичной сортировки, использующей уже упорядоченный префикс входа.",
    ),
    "status": (
        "Режим измерения",
        "measured — данные фактического выполнения; not_measured — запрос не выполнялся для измерения.",
    ),
}

VALUES = {
    "measured": "Измерено",
    "not_measured": "Не измерялось",
    "not_comparable": "Прямое сравнение ограничено",
    "not_evaluated": "Не оценивалось",
    "not_executed": "Узел не выполнялся",
    "ratio_only_not_cause": "Отношение, не причина",
    "both_zero": "Оценка и факт равны нулю",
    "zero_mismatch": "Нулевая оценка или факт",
    "Memory": "Память",
    "Disk": "Диск",
}


ENGLISH_METRICS = {
    "node_type": "Node Type",
    "path": "Plan Path",
    "total_cost": "Total Cost",
    "plan_rows": "Plan Rows",
    "plan_width": "Plan Width",
    "estimated_bytes": "Estimated Output Bytes",
    "execution_time_ms": "Execution Time",
    "planning_time_ms": "Planning Time",
    "analysis_duration_ms": "Analysis Duration",
    "actual_rows_per_loop": "Actual Rows",
    "actual_loops": "Actual Loops",
    "approximate_total_output_rows": "Actual Rows × Loops",
    "actual_total_time_ms_per_loop": "Actual Total Time",
    "estimate_to_actual_ratio": "Estimate / Actual Ratio",
    "row_comparison_status": "Row Comparison Status",
    "sort_space_type": "Sort Space Type",
    "sort_space_used_kb": "Sort Space Used",
    "sort_method": "Sort Method",
    "disk_sort_observed": "Disk Sort Observed",
    "temporary_io_observed": "Temporary I/O Observed",
    "multi_batch_hash_observed": "Multi-batch Hash Observed",
    "worker_disk_sort_observed": "Worker Disk Sort Observed",
    "workers_planned": "Workers Planned",
    "workers_launched": "Workers Launched",
    "runtime_counters": "Runtime Counters",
    "worker_details": "Workers",
    "incremental_sort_details": "Incremental Sort",
    "status": "Measurement Status",
}
COUNTER_LABELS = {
    "Heap Fetches": "Обращения к таблице",
    "Hash Batches": "Пакеты хеширования",
    "HashAgg Batches": "Пакеты хеш-агрегации",
    "Hash Buckets": "Корзины хеш-таблицы",
    "Peak Memory Usage": "Пиковая память, kB",
    "Disk Usage": "Использование диска, kB",
    "Worker Number": "Номер работника",
    "Actual Startup Time": "Время до первой строки, мс",
    "Full-sort Groups": "Группы полной сортировки",
    "Pre-sorted Groups": "Предварительно упорядоченные группы",
    "Group Count": "Количество групп",
    "Sort Methods Used": "Методы сортировки",
    "Sort Space Memory": "Память сортировки, kB",
    "Sort Space Disk": "Диск сортировки, kB",
    "Average Sort Space Used": "Средний объём сортировки, kB",
    "Peak Sort Space Used": "Пиковый объём сортировки, kB",
    "Original Hash Batches": "Исходное число пакетов хеширования",
    "Original Hash Buckets": "Исходное число корзин",
    "Rows Removed by Filter": "Строк отброшено фильтром за цикл",
    "Rows Removed by Join Filter": "Строк отброшено фильтром соединения за цикл",
    "Rows Removed by Index Recheck": "Строк отброшено перепроверкой индекса за цикл",
}
PLAN_NOTE = "Оценочный план без ANALYZE. Фактическое время выполнения не измерялось."
CANDIDATE_NOTE = (
    "Найденные конструкции — кандидаты для проверки, а не доказанные причины замедления."
)
PRIORITY_NOTE = (
    "Приоритет — порядок проверки гипотез, а не доказанная причина или тяжесть замедления."
)
UNMEASURED_NOTE = "Измеренного времени нет; используются оценки плана и доступные метаданные."
EXPLAIN_WARNING = (
    "EXPLAIN: фактическое время SQL не измерялось. " "Включите «Измерить (ANALYZE)» для измерения."
)


def metric_info(key):
    title, explanation = _metric_info(key)
    english = ENGLISH_METRICS.get(key, key)
    if title == key:
        title = COUNTER_LABELS.get(key, "Данные узла")
        if "Blocks" in key:
            area = (
                "Временные"
                if key.startswith("Temp")
                else ("Локальные" if key.startswith("Local") else "Общие")
            )
            action = next(
                (
                    r
                    for e, r in (
                        ("Hit", "в кэше"),
                        ("Read", "прочитаны"),
                        ("Dirtied", "изменены"),
                        ("Written", "записаны"),
                    )
                    if e in key
                ),
                "обработаны",
            )
            title = f"{area} блоки: {action}"
        elif "I/O" in key and "Time" in key:
            area = (
                "Временный"
                if key.startswith("Temp")
                else ("Локальный" if key.startswith("Local") else "Общий")
            )
            action = "чтение" if "Read" in key else "запись"
            title = f"{area} ввод-вывод: {action}, мс"
    return f"{english} / {title}", explanation


def _metric_info(key):
    if key in METRICS:
        return METRICS[key]
    aliases = {
        "Actual Rows": "actual_rows_per_loop",
        "Actual Loops": "actual_loops",
        "Actual Total Time": "actual_total_time_ms_per_loop",
        "Sort Method": "sort_method",
        "Sort Space Used": "sort_space_used_kb",
        "Sort Space Type": "sort_space_type",
    }
    if key in aliases:
        return METRICS[aliases[key]]
    if "Blocks" in key:
        action = next(
            (
                text
                for word, text in (
                    ("Hit", "найдены в буферном кэше"),
                    ("Read", "прочитаны"),
                    ("Dirtied", "изменены"),
                    ("Written", "записаны"),
                )
                if word in key
            ),
            "обработаны",
        )
        area = (
            "временные"
            if key.startswith("Temp")
            else ("локальные" if key.startswith("Local") else "общие")
        )
        return key, (
            f"Блоки ({area}), которые были {action}. Размер блока задаётся PostgreSQL "
            "(обычно 8 KiB). Счётчик включает дочерние операции и повторные обращения; "
            "чтение может обслуживаться кэшем ОС и не равно физическому чтению диска."
        )
    if "Rows Removed" in key:
        return (
            key,
            "Среднее число строк, отброшенных этим условием за цикл. Учитывайте loops; это не число уникальных строк.",
        )
    if "I/O" in key and "Time" in key:
        return (
            key,
            "Время соответствующего ввода-вывода в миллисекундах. Доступность зависит от настроек сбора времени PostgreSQL; включает дочерние операции.",
        )
    descriptions = {
        "Heap Fetches": "Обращения к таблице для проверки видимости строк при Index Only Scan.",
        "Hash Batches": "Число пакетов хеширования. Несколько пакетов могут сопровождаться временным вводом-выводом.",
        "HashAgg Batches": "Число пакетов хеш-агрегации; несколько пакетов требуют проверки временного ввода-вывода.",
        "Hash Buckets": "Число корзин хеш-таблицы, используемых для распределения ключей.",
        "Peak Memory Usage": "Пиковая память операции, kB; не потребление памяти всем запросом.",
        "Disk Usage": "Дисковое пространство операции, kB. Например, хеш-агрегация может сохранять промежуточные данные на диск.",
        "Worker Number": "Номер параллельного работника внутри этой операции.",
        "Actual Startup Time": "Среднее время до первой строки за цикл, мс, включая дочерние операции.",
        "Full-sort Groups": "Группы инкрементальной сортировки, для которых потребовалась полная сортировка.",
        "Pre-sorted Groups": "Группы, в которых сортировка использовала уже упорядоченную часть ключа.",
        "Group Count": "Количество групп этого вида инкрементальной сортировки.",
        "Sort Methods Used": "Алгоритмы, использованные для сортировки этой группы.",
        "Sort Space Memory": "Использование памяти при сортировке групп, kB.",
        "Sort Space Disk": "Использование диска при сортировке групп, kB.",
        "Average Sort Space Used": "Средний объём пространства сортировки группы, kB.",
        "Peak Sort Space Used": "Пиковый объём пространства сортировки группы, kB.",
        "Original Hash Batches": "Первоначально запланированное число пакетов хеширования.",
        "Original Hash Buckets": "Первоначально запланированное число корзин хеш-таблицы.",
    }
    return key, descriptions.get(
        key,
        f"Поле «{key}» из данных EXPLAIN PostgreSQL. Интерпретируйте его в контексте родительского узла и режима измерения.",
    )


def text_label(text, *, rich=False, code=False):
    label = QtWidgets.QLabel(text)
    font = QtGui.QFont(QtWidgets.QApplication.font())
    font.setPointSize(max(13, font.pointSize()))
    font.setWeight(QtGui.QFont.Weight.DemiBold)
    label.setFont(font)
    label.setTextFormat(QtCore.Qt.TextFormat.RichText if rich else QtCore.Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    label.setTextInteractionFlags(
        QtCore.Qt.TextInteractionFlag.TextSelectableByMouse
        | QtCore.Qt.TextInteractionFlag.TextSelectableByKeyboard
    )
    label.setSizePolicy(
        QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Preferred
    )
    if code:
        font = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.SystemFont.FixedFont)
        font.setPointSize(13)
        font.setWeight(QtGui.QFont.Weight.DemiBold)
        label.setFont(font)
        label.setObjectName("reportCode")
    return label


def readable_text(text):
    """Сохраняет все строки отчёта, отделяя заголовки проверок и их доказательства."""
    parts = []
    for line in text.splitlines():
        value = escape(line.strip())
        if not value:
            continue
        indent = min(len(line) - len(line.lstrip()), 8) * 5
        heading = bool(re.match(r"AP\d+\b", line.strip())) or line.rstrip().endswith(":")
        parts.append(
            f'<p style="margin: {12 if heading else 5}px 0 5px {indent}px;">'
            + (f"<b>{value}</b>" if heading else value)
            + "</p>"
        )
    return "".join(parts)


class ReportSql(QtWidgets.QTextBrowser):
    """Подсвеченный SQL с высотой по содержимому и общей прокруткой отчёта."""

    def __init__(self, sql):
        super().__init__()
        self.setObjectName("reportSql")
        self.setAccessibleName("SQL-запрос с подсветкой синтаксиса")
        self.setReadOnly(True)
        self.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setWordWrapMode(QtGui.QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Fixed)
        self.setFont(QtGui.QFont("Consolas", 13, QtGui.QFont.Weight.DemiBold))
        self.setPlainText(sql)
        self.highlighter = SqlHighlighter(self.document())
        self.document().documentLayout().documentSizeChanged.connect(self.fit_content)
        self.refresh_theme()

    def refresh_theme(self):
        theme = (
            "dark"
            if self.palette().color(QtGui.QPalette.ColorRole.Window).lightness() < 128
            else "light"
        )
        self.highlighter.set_theme(theme)

    def fit_content(self, *args):
        height = math.ceil(self.document().size().height()) + 24
        if height != self.height():
            self.setFixedHeight(max(56, height))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fit_content()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QtCore.QEvent.Type.PaletteChange and hasattr(self, "highlighter"):
            self.refresh_theme()


class ReportSection(QtWidgets.QFrame):
    def __init__(self, title, *, expanded=True, parent=None, tone=None):
        super().__init__(parent)
        self.setObjectName("reportSection")
        if tone:
            self.setProperty("tone", tone)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(10)
        self.toggle = QtWidgets.QToolButton()
        self.toggle.setText(title)
        self.toggle.setAccessibleName(title)
        self.toggle.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.toggle.setCheckable(True)
        self.toggle.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Preferred
        )
        self.toggle.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.toggle.setObjectName("reportToggle")
        self.header_widget = QtWidgets.QFrame()
        self.header_widget.setObjectName("reportHeader")
        header = QtWidgets.QHBoxLayout(self.header_widget)
        header.setContentsMargins(8, 6, 8, 6)
        self.header = header
        self.toggle.setFixedWidth(24)
        header.addWidget(self.toggle)
        self.title_label = text_label(title)
        self.title_label.setObjectName("reportTitle")
        self.title_label.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.NoTextInteraction)
        self.title_label.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.title_label.installEventFilter(self)
        header.addWidget(self.title_label, 1)
        layout.addWidget(self.header_widget)
        self.body = QtWidgets.QWidget()
        self.content = QtWidgets.QVBoxLayout(self.body)
        self.content.setContentsMargins(2, 0, 2, 0)
        self.content.setSpacing(10)
        layout.addWidget(self.body)
        self.toggle.toggled.connect(self.set_expanded)
        self.toggle.setChecked(expanded)
        self.set_expanded(expanded)

    def eventFilter(self, watched, event):
        if watched is self.title_label and event.type() == QtCore.QEvent.Type.MouseButtonRelease:
            if event.button() == QtCore.Qt.MouseButton.LeftButton:
                self.toggle.click()
                return True
        return super().eventFilter(watched, event)

    def set_expanded(self, expanded):
        self.body.setVisible(expanded)
        self.toggle.setArrowType(
            QtCore.Qt.ArrowType.DownArrow if expanded else QtCore.Qt.ArrowType.RightArrow
        )
        self.toggle.setChecked(expanded)
        self.toggle.setAccessibleDescription("Развёрнуто" if expanded else "Свёрнуто")

    def add_text(self, text, *, code=False):
        self.content.addWidget(
            text_label(text if code else readable_text(text), rich=not code, code=code)
        )

    def add_help(self, title, explanation, *, symbol="i"):
        self.header.addWidget(info_button(self, title, explanation, symbol=symbol))

    def add_advice(self, title, text):
        advice = ReportSection(title, tone="advice")
        advice.add_text(text)
        self.content.addWidget(advice)
        return advice

    def add_checks(self, text):
        current = self
        for paragraph in re.split(r"(?m)^(?=AP\d+:)", text):
            match = re.match(r"(AP\d+):", paragraph)
            if match:
                rule = match[1]
                current = ReportSection(TITLES.get(rule, rule), expanded=False)
                self.content.addWidget(current)
            current.add_text(paragraph.strip())


def info_button(parent, title, explanation, *, symbol="i"):
    button = QtWidgets.QToolButton()
    button.setText(symbol)
    button.setObjectName("metricInfo")
    button.setAccessibleName(f"О показателе: {title}")
    button.setToolTip(explanation)
    button.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
    button.clicked.connect(
        lambda checked=False: QtWidgets.QMessageBox.information(parent, title, explanation)
    )
    return button


def measurement_help(key, limitations):
    """Привязывает оговорки отчёта к тем показателям, которые они поясняют."""
    rules = (
        (("Execution Time",), {"execution_time_ms"}),
        (
            ("Actual Rows", "Rows Removed"),
            {
                "actual_rows_per_loop",
                "actual_loops",
                "approximate_total_output_rows",
                "Actual Rows",
                "Actual Loops",
            },
        ),
        (("Буферы", "Счётчики"), {"runtime_counters"}),
        (("Временные блоки", "Worker-данные"), {"temporary_io_observed", "worker_details"}),
        (
            ("LIMIT", "semi/anti"),
            {"row_comparison_status", "estimate_to_actual_ratio", "plan_rows"},
        ),
    )
    notes = []
    for message in limitations:
        matched = False
        for markers, keys in rules:
            if any(marker in message for marker in markers):
                matched = True
                applies = key in keys
                if "runtime_counters" in keys:
                    applies |= "Blocks" in key or "I/O" in key
                if "actual_rows_per_loop" in keys:
                    applies |= "Rows Removed" in key
                if "temporary_io_observed" in keys:
                    applies |= key.startswith("Temp ")
                if applies:
                    notes.append(message)
        if not matched and key == "status":
            notes.append(message)
    return list(dict.fromkeys(notes))


def add_metrics(section, data, *, limitations=(), definitions=None):
    for key, value in data.items():
        title, explanation = (definitions or {}).get(key) or metric_info(key)
        notes = measurement_help(key, limitations)
        if notes:
            explanation += "\n\n" + "\n\n".join(notes)
        if isinstance(value, (dict, list)):
            nested = ReportSection(title, expanded=False)
            nested.setToolTip(explanation)
            nested.add_help(title, explanation)
            section.content.addWidget(nested)
            if isinstance(value, dict):
                add_metrics(nested, value, limitations=limitations, definitions=definitions)
            else:
                for index, entry in enumerate(value, 1):
                    if isinstance(entry, dict):
                        child = ReportSection(f"{title} · {index}", expanded=False)
                        nested.content.addWidget(child)
                        add_metrics(child, entry, limitations=limitations, definitions=definitions)
                    else:
                        nested.add_text(str(entry))
            if not value:
                nested.add_text("Нет данных.")
            continue
        row = QtWidgets.QFrame()
        row.setObjectName("reportMetric")
        layout = QtWidgets.QVBoxLayout(row)
        layout.setContentsMargins(10, 8, 10, 8)
        header = QtWidgets.QHBoxLayout()
        name = text_label(title)
        name.setObjectName("muted")
        header.addWidget(name, 1)
        header.addWidget(info_button(section, title, explanation))
        layout.addLayout(header)
        if value is None:
            display = "Нет данных"
        elif isinstance(value, bool):
            display = "Да" if value else "Нет"
        elif isinstance(value, float):
            display = f"{value:,.3f}".rstrip("0").rstrip(".").replace(",", " ")
        else:
            display = VALUES.get(str(value), str(value))
        label = text_label(display)
        label.setObjectName("reportMetricValue")
        layout.addWidget(label)
        section.content.addWidget(row)


HEADINGS = {
    "SQL:": "SQL-запрос",
    "СВОДКА ПЛАНА": "Сводка плана",
    "ДЕРЕВО ДИАГНОСТИКИ И ПОРЯДОК ПРОВЕРКИ": "Дерево диагностики и порядок проверки",
    "ИЗМЕРЕНИЯ ВЫПОЛНЕНИЯ": "Измерения выполнения",
    "КОНСТРУКЦИИ ДЛЯ ПРОВЕРКИ": "Конструкции для проверки",
    "ПРОВЕРКА AP02/AP03 ПО ПЛАНУ": "Проверка по плану",
    "ПРОВЕРКА ОБЪЁМА И ЛИШНЕЙ РАБОТЫ": "Проверка объёма и лишней работы",
    "ПРОВЕРКА OR И NOT IN": "OR и NOT IN",
    "ПРОВЕРКА DISTINCT, СОРТИРОВОК, HAVING, CTE, ОКОН И JOIN": "DISTINCT, сортировки, HAVING, CTE, окна и JOIN",
    "СОБРАННЫЙ КОНТЕКСТ": "Собранный контекст",
}


def split_details(details):
    sections = {}
    current = "О режиме анализа"
    for line in details.splitlines():
        if line in HEADINGS:
            current = HEADINGS[line]
        else:
            sections.setdefault(current, []).append(line)
    return {key: "\n".join(lines).strip() for key, lines in sections.items()}


class AnalysisReportView(QtWidgets.QScrollArea):
    """Единая прокрутка для карточек; текстовый API сохраняет совместимость истории."""

    def __init__(self):
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setObjectName("analysisReport")
        self._text = ""
        self._placeholder = ""
        self.sections = []
        self.container = QtWidgets.QWidget()
        self.layout = QtWidgets.QVBoxLayout(self.container)
        self.layout.setContentsMargins(4, 12, 6, 12)
        self.layout.setSpacing(12)
        self.layout.setAlignment(QtCore.Qt.AlignmentFlag.AlignTop)
        self.setWidget(self.container)

    def clear(self):
        self._text = ""
        self.sections = []
        while self.layout.count():
            widget = self.layout.takeAt(0).widget()
            if widget:
                widget.hide()
                widget.deleteLater()
        self.verticalScrollBar().setValue(0)

    def toPlainText(self):
        return self._text

    def isReadOnly(self):
        return True

    def setPlaceholderText(self, text):
        self._placeholder = text
        if not self._text:
            self.setPlainText("")

    def setPlainText(self, text):
        self.clear()
        self._text = text
        self.layout.addWidget(text_label(text or self._placeholder))

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QtCore.QEvent.Type.FontChange and hasattr(self, "container"):
            size = max(13, self.font().pointSize())
            for label in self.container.findChildren(QtWidgets.QLabel):
                font = label.font()
                font.setPointSize(size)
                font.setWeight(QtGui.QFont.Weight.DemiBold)
                label.setFont(font)
            for sql in self.container.findChildren(ReportSql):
                font = sql.font()
                font.setPointSize(size)
                sql.setFont(font)

    def section(self, title, *, expanded=True):
        section = ReportSection(title, expanded=expanded)
        self.layout.addWidget(section)
        self.sections.append(section)
        return section

    def warning(self, text):
        warning = QtWidgets.QFrame()
        warning.setObjectName("reportWarning")
        warning.setAccessibleName("Предупреждение")
        layout = QtWidgets.QHBoxLayout(warning)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(12)
        icon = QtWidgets.QLabel("!")
        icon.setObjectName("warningIcon")
        icon.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        icon.setFixedSize(26, 26)
        layout.addWidget(icon, 0, QtCore.Qt.AlignmentFlag.AlignTop)
        layout.addWidget(text_label(text), 1)
        self.layout.addWidget(warning)

    def show_brief(self, presentation, payload):
        self.clear()
        self._text = presentation["brief"]
        runtime = payload.get("runtime_analysis", {})
        measured = runtime.get("status") == "measured"
        if not measured:
            self.warning(EXPLAIN_WARNING)
        timing = self.section("Время выполнения и анализа")
        metrics = {
            "execution_time_ms": runtime.get("execution_time_ms") if measured else None,
            "planning_time_ms": runtime.get("planning_time_ms") if measured else None,
        }
        try:
            duration = (
                datetime.fromisoformat(payload["finished_at"])
                - datetime.fromisoformat(payload["started_at"])
            ).total_seconds() * 1000
            if math.isfinite(duration) and duration >= 0:
                metrics["analysis_duration_ms"] = duration
        except (KeyError, ValueError, TypeError):
            pass
        add_metrics(timing, metrics)
        problems = self.section("Найденные проблемы")
        findings = presentation.get("all_findings", presentation["findings"])
        if not findings:
            problems.add_text(
                "По доступным данным конкретных проблем не выявлено. Это не гарантирует оптимальности запроса."
            )
        for item in findings:
            child = ReportSection(f"{item['label']} · {item['title']}")
            if "недостаточно данных" in item["label"].casefold():
                child.setProperty("tone", "insufficient")
            child.add_text(item["why"])
            if item.get("location"):
                child.add_text("Где: " + item["location"])
            child.add_advice("Что проверить", item["action"])
            problems.content.addWidget(child)
        causes = self.section("Причины замедления")
        causes.add_text(
            "Причинная связь с замедлением пока не подтверждена. Найденные конструкции и измеренные факты требуют сопоставления с планом и временем."
        )
        for fact in payload.get("diagnostic_tree", {}).get("measured_observations", []):
            causes.add_text("Измеренный факт: " + fact["label"])

    def show_details(self, presentation, payload):
        self.clear()
        self._text = presentation["details"]
        parts = split_details(self._text)
        sql = self.section("SQL-запрос")
        sql.content.addWidget(ReportSql(payload.get("sql", "Нет данных")))
        summary = self.section("Сводка плана")
        mode = "EXPLAIN ANALYZE" if payload.get("mode") == "explain_analyze" else "EXPLAIN"
        mode_badge = text_label("Использован " + mode)
        mode_badge.setObjectName("planMode")
        summary.content.addWidget(mode_badge)
        mode_note = (
            "Измеренный план ANALYZE: SELECT выполнен. Время относится к серверному выполнению под EXPLAIN."
            if mode == "EXPLAIN ANALYZE"
            else PLAN_NOTE
        )
        summary.add_help("О плане " + mode, mode_note + "\n\n" + CANDIDATE_NOTE, symbol="?")
        add_metrics(summary, payload.get("plan_summary", {}))
        if not payload.get("plan_summary"):
            summary.add_text(parts.get("Сводка плана", "Характеристики плана недоступны."))
        tree = self.section("Дерево диагностики и порядок проверки")
        self.show_tree(tree, payload, parts)
        runtime = self.section("Измерения выполнения")
        data = payload.get("runtime_analysis", {})
        add_metrics(
            runtime,
            {
                k: v
                for k, v in data.items()
                if k not in {"schema_version", "node_observations", "limitations"}
            },
            limitations=data.get("limitations", []),
        )
        for node in data.get("node_observations", []):
            child = ReportSection(
                f"{node.get('node_type', 'Узел')} · {node.get('path', '')}", expanded=False
            )
            runtime.content.addWidget(child)
            add_metrics(child, node, limitations=data.get("limitations", []))
        constructs = self.section("Конструкции для проверки")
        for finding in payload.get("triage", {}).get("findings", []):
            if finding.get("status") != "candidate":
                continue
            rule = finding["rule_id"]
            child = ReportSection(TITLES.get(rule, finding.get("name", rule)), expanded=False)
            if rule in MESSAGES:
                child.add_text(" ".join(MESSAGES[rule]))
            for match in finding.get("syntax_matches", []):
                child.add_text(f"{match['scope_id']}: {match['expression']}", code=True)
            constructs.content.addWidget(child)
        context_lines = [
            line
            for line in parts.get("Конструкции для проверки", "").splitlines()
            if line.startswith("Связи источников")
        ]
        if not constructs.content.count():
            constructs.add_text("Конструкции для проверки не найдены.")
        if context_lines:
            constructs.add_text("\n".join(context_lines))
        for title in ("Проверка по плану", "Проверка объёма и лишней работы"):
            self.section(title).add_checks(parts.get(title, "Для этой проверки данных нет."))
        checks = self.section("Проверки конструкций SQL")
        for title in ("LIKE/ILIKE", "N+1"):
            results = [
                line
                for line in parts.get("Конструкции для проверки", "").splitlines()
                if line.startswith(title)
            ]
            if results:
                child = ReportSection(title, expanded=False)
                child.add_text("\n".join(results))
                checks.content.addWidget(child)
        for title in ("OR и NOT IN", "DISTINCT, сортировки, HAVING, CTE, окна и JOIN"):
            child = ReportSection(title, expanded=False)
            child.add_checks(parts.get(title, "Конструкций для этой проверки не найдено."))
            checks.content.addWidget(child)
        context = self.section("Собранный контекст", expanded=False)
        context.add_text(parts.get("Собранный контекст", "Контекст недоступен."))
        # Сохранённые старые отчёты могут содержать дополнительные разделы.
        known = set(HEADINGS.values()) | {"О режиме анализа"}
        for title, content in parts.items():
            if title not in known and content:
                self.section(title).add_text(content)

    def show_tree(self, section, payload, parts):
        diagnostic = payload.get("diagnostic_tree") or {}
        measured = diagnostic.get("measurement_status") == "measured"
        measurement_note = (
            f"Execution Time / Время на сервере, мс: {diagnostic.get('execution_time_ms')}. "
            "Целевое время ответа не задано."
            if measured
            else UNMEASURED_NOTE
        )
        section.add_help(
            "О дереве диагностики", PRIORITY_NOTE + "\n\n" + measurement_note, symbol="?"
        )
        if not diagnostic:
            section.add_text(parts.get(section.toggle.text(), "Дерево диагностики отсутствует."))
            return
        for group in diagnostic.get("root", {}).get("children", []):
            child = ReportSection(group["label"], expanded=False)
            section.content.addWidget(child)
            active = [
                rule for rule in group.get("children", []) if rule.get("state") != "not_detected"
            ]
            if not active:
                child.add_text("Конструкции для проверки в этой группе не обнаружены.")
            for rule in active:
                rule_section = ReportSection(f"{rule['rule_id']} · {rule['label']}")
                child.content.addWidget(rule_section)
                rule_section.add_text(rule["state_label"])
                for check in rule.get("children", []):
                    rule_section.add_text(check["label"] + " → " + check["state_label"])
                if rule.get("scope_limitation"):
                    rule_section.add_text(rule["scope_limitation"])
        facts = diagnostic.get("measured_observations", [])
        if facts:
            observations = ReportSection("Измеренные факты", expanded=False)
            section.content.addWidget(observations)
            for fact in facts:
                observations.add_text(f"{fact['label']} · {fact.get('plan_path', '')}")
        advice = section.add_advice(
            "Что проверять дальше", "Проверяйте гипотезы в указанном порядке."
        )
        checks = diagnostic.get("recommended_checks", [])
        if not checks:
            advice.add_text(
                "Конкретных кандидатов изменения не сформировано; это не гарантирует отсутствия проблем."
            )
        for index, action in enumerate(checks, 1):
            advice.add_text(
                f"{index}. Приоритет {action['priority']} · "
                f"{action.get('rule_id') or 'Измерение'}\n{action['text']}"
            )
