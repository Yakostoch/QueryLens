from datetime import datetime

from PySide6 import QtCore, QtWidgets


class HistoryPanel(QtWidgets.QFrame):
    restore_requested = QtCore.Signal(object)
    delete_requested = QtCore.Signal(int)

    def __init__(self):
        super().__init__()
        self.setObjectName("card")
        self.setMinimumWidth(220)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(14, 18, 14, 14)
        layout.setSpacing(12)
        title = QtWidgets.QLabel("История запросов")
        title.setObjectName("section")
        layout.addWidget(title)
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("Поиск по SQL…")
        self.search.setClearButtonEnabled(True)
        layout.addWidget(self.search)
        self.items = QtWidgets.QListWidget()
        self.items.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        layout.addWidget(self.items, 1)
        self.empty = QtWidgets.QLabel("История пока пуста.\nЗапустите первый анализ.")
        self.empty.setObjectName("muted")
        self.empty.setWordWrap(True)
        layout.addWidget(self.empty)
        hint = QtWidgets.QLabel("Двойной щелчок — открыть\nПоказаны последние 200 совпадений")
        hint.setObjectName("muted")
        layout.addWidget(hint)
        actions = QtWidgets.QHBoxLayout()
        self.open_button = QtWidgets.QPushButton("Открыть")
        self.delete_button = QtWidgets.QPushButton("Удалить")
        actions.addWidget(self.open_button)
        actions.addWidget(self.delete_button)
        layout.addLayout(actions)
        self.items.itemDoubleClicked.connect(self.restore)
        self.open_button.clicked.connect(self.restore)
        self.delete_button.clicked.connect(self.delete_selected)
        self.items.itemSelectionChanged.connect(self.update_actions)
        self.update_actions()

    def set_entries(self, entries):
        self.items.clear()
        for row in entries:
            entry = dict(row)
            preview = " ".join(entry["sql"].split())
            date = datetime.fromisoformat(entry["created_at"]).astimezone().strftime("%d.%m.%Y · %H:%M")
            item = QtWidgets.QListWidgetItem(f"{date}\n{preview[:65]}")
            item.setData(QtCore.Qt.ItemDataRole.UserRole, entry)
            item.setToolTip(entry["sql"][:2000])
            self.items.addItem(item)
        self.empty.setText("Ничего не найдено." if self.search.text() else
                           "История пока пуста.\nЗапустите первый анализ.")
        self.empty.setVisible(not entries)
        self.update_actions()

    def update_actions(self):
        selected = self.items.currentItem() is not None
        self.open_button.setEnabled(selected)
        self.delete_button.setEnabled(selected)

    def restore(self, *_):
        item = self.items.currentItem()
        if item:
            self.restore_requested.emit(item.data(QtCore.Qt.ItemDataRole.UserRole))

    def delete_selected(self):
        item = self.items.currentItem()
        if item:
            self.delete_requested.emit(item.data(QtCore.Qt.ItemDataRole.UserRole)["id"])
