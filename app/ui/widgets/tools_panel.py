from PySide6 import QtCore, QtWidgets

from app.ui.widgets.toggle_switch import ToggleSwitch


class ToolsPanel(QtWidgets.QWidget):
    selection_changed = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        title = QtWidgets.QLabel("Инструменты анализа")
        title.setObjectName("brand")
        layout.addWidget(title)
        hint = QtWidgets.QLabel("Выберите модули локального сбора и перейдите к анализу БД.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.config_switch = self.add_tool(
            layout, "Config Analyzer", "Конфигурация СУБД",
            "Сбор 33 параметров PostgreSQL, статистики и структуры. Значения не являются готовым диагнозом.",
            True,
        )
        self.hardware_switch = self.add_tool(
            layout, "Hardware Analyzer", "Оборудование",
            "Пять измерений CPU, RAM, swap и I/O этого компьютера. Можно запускать без PostgreSQL.",
            False,
        )
        self.summary = QtWidgets.QLabel()
        self.summary.setObjectName("muted")
        layout.addWidget(self.summary)
        preview = QtWidgets.QLabel("Сборщики подключены · Анализ SQL и локальная LLM будут добавлены следующим этапом")
        preview.setObjectName("muted")
        preview.setWordWrap(True)
        layout.addWidget(preview)
        self.analysis_button = QtWidgets.QPushButton("Перейти к анализу БД")
        self.analysis_button.setObjectName("primary")
        layout.addWidget(self.analysis_button, 0, QtCore.Qt.AlignmentFlag.AlignLeft)
        self.hardware_button = QtWidgets.QPushButton("Проверить Hardware")
        self.hardware_button.setToolTip("Собрать CPU, RAM и показатели дисков без подключения к БД")
        layout.addWidget(self.hardware_button, 0, QtCore.Qt.AlignmentFlag.AlignLeft)
        layout.addStretch()
        self.config_switch.toggled.connect(self.update_summary)
        self.hardware_switch.toggled.connect(self.update_summary)
        self.update_summary()

    @staticmethod
    def add_tool(layout, name, subtitle, description, checked):
        card = QtWidgets.QFrame()
        card.setObjectName("card")
        row = QtWidgets.QHBoxLayout(card)
        row.setContentsMargins(24, 22, 24, 22)
        row.setSpacing(24)
        text = QtWidgets.QVBoxLayout()
        title = QtWidgets.QLabel(name)
        title.setObjectName("section")
        text.addWidget(title)
        label = QtWidgets.QLabel(subtitle)
        text.addWidget(label)
        hint = QtWidgets.QLabel(description)
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        text.addWidget(hint)
        row.addLayout(text, 1)
        switch = ToggleSwitch(name)
        switch.setChecked(checked)
        switch.setToolTip(f"Включить {name}")
        row.addWidget(switch)
        layout.addWidget(card)
        return switch

    def update_summary(self, *_):
        count = int(self.config_switch.isChecked()) + int(self.hardware_switch.isChecked())
        self.summary.setText(f"Выбрано модулей: {count} из 2")
        self.selection_changed.emit()
