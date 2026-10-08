from PySide6 import QtWidgets

from app.ui.widgets.toggle_switch import ToggleSwitch


class DatabaseOptionsDialog(QtWidgets.QDialog):
    """Только выбор отображаемых опций; сбором управляет контроллер."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Что анализировать")
        self.setMinimumWidth(480)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(18)
        title = QtWidgets.QLabel("Что анализировать")
        title.setObjectName("brand")
        layout.addWidget(title)
        self.config_option = self.add_option(
            layout, "Конфигурация PostgreSQL", "Параметры сервера и пояснения к настройкам.")
        self.metadata_option = self.add_option(
            layout, "Структура и статистика БД", "Таблицы, индексы, размер базы и накопленная статистика.")
        scope_row = QtWidgets.QHBoxLayout()
        scope_row.addWidget(QtWidgets.QLabel("Область структуры:"))
        self.metadata_scope = QtWidgets.QComboBox()
        self.metadata_scope.addItem("Текущий запрос", "query")
        self.metadata_scope.addItem("Вся база данных", "database")
        self.metadata_scope.setCurrentIndex(1)
        self.metadata_scope.setAccessibleName("Область сбора структуры")
        self.metadata_option.toggled.connect(self.metadata_scope.setEnabled)
        scope_row.addWidget(self.metadata_scope, 1)
        layout.addLayout(scope_row)
        hint = QtWidgets.QLabel("Выбор применяется сразу. Для сбора включите хотя бы одну опцию.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        buttons = QtWidgets.QDialogButtonBox()
        done = buttons.addButton("Готово", QtWidgets.QDialogButtonBox.ButtonRole.AcceptRole)
        done.setObjectName("primary")
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

    @staticmethod
    def add_option(layout, title, description):
        card = QtWidgets.QFrame()
        card.setObjectName("card")
        row = QtWidgets.QHBoxLayout(card)
        row.setContentsMargins(16, 16, 16, 16)
        text = QtWidgets.QVBoxLayout()
        label = QtWidgets.QLabel(title)
        label.setObjectName("section")
        text.addWidget(label)
        hint = QtWidgets.QLabel(description)
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        text.addWidget(hint)
        row.addLayout(text, 1)
        switch = ToggleSwitch(title)
        switch.setChecked(True)
        row.addWidget(switch)
        layout.addWidget(card)
        return switch
