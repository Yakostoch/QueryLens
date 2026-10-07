from PySide6 import QtCore, QtWidgets

class ConnectionDialog(QtWidgets.QDialog):
    connection_requested = QtCore.Signal(object, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Подключение к СУБД")
        self.setMinimumWidth(560)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(18)
        title = QtWidgets.QLabel("Подключение к СУБД")
        title.setObjectName("brand")
        layout.addWidget(title)
        hint = QtWidgets.QLabel("Локальная PostgreSQL: localhost, 127.0.0.1 или ::1. Пароль используется только в памяти.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.fields = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(self.fields)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(14)
        self.engine = QtWidgets.QLabel("PostgreSQL")
        self.engine.setAccessibleName("СУБД")
        form.addRow("СУБД", self.engine)
        self.host = QtWidgets.QLineEdit("localhost")
        self.port = QtWidgets.QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(5432)
        self.database = QtWidgets.QLineEdit()
        self.database.setPlaceholderText("Например, test_db")
        self.user = QtWidgets.QLineEdit()
        self.password = QtWidgets.QLineEdit()
        self.password.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText("Пароль пользователя")
        self.sslmode = QtWidgets.QComboBox()
        self.sslmode.addItem("Предпочитать SSL", "prefer")
        self.sslmode.addItem("Требовать SSL", "require")
        self.sslmode.addItem("Без SSL", "disable")
        for label, widget in (("Сервер", self.host), ("Порт", self.port),
                              ("База данных", self.database), ("Пользователь", self.user),
                              ("Пароль", self.password), ("SSL", self.sslmode)):
            form.addRow(label, widget)
        layout.addWidget(self.fields)
        for name, widget in (("Сервер", self.host), ("Порт", self.port),
                             ("База данных", self.database), ("Пользователь", self.user),
                             ("Пароль", self.password)):
            widget.setAccessibleName(name)

        self.message = QtWidgets.QLabel("Введите параметры и проверьте подключение.")
        self.message.setObjectName("muted")
        self.message.setWordWrap(True)
        self.message.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        layout.addWidget(self.message)
        buttons = QtWidgets.QHBoxLayout()
        self.test_button = QtWidgets.QPushButton("Проверить подключение")
        self.test_button.clicked.connect(lambda: self.request_connection(test_only=True))
        buttons.addWidget(self.test_button)
        buttons.addStretch()
        self.cancel_button = QtWidgets.QPushButton("Отмена")
        self.cancel_button.clicked.connect(self.reject)
        buttons.addWidget(self.cancel_button)
        self.connect_button = QtWidgets.QPushButton("Подключиться")
        self.connect_button.setObjectName("primary")
        self.connect_button.clicked.connect(lambda: self.request_connection(test_only=False))
        buttons.addWidget(self.connect_button)
        layout.addLayout(buttons)
        self.user.setPlaceholderText("postgres")

    def request_connection(self, test_only=False):
        self.connection_requested.emit(dict(
            host=self.host.text(), port=self.port.value(), database=self.database.text(),
            user=self.user.text(), password=self.password.text(), sslmode=self.sslmode.currentData(),
        ), test_only)

    def set_busy(self, busy):
        self.fields.setEnabled(not busy)
        self.test_button.setEnabled(not busy)
        self.connect_button.setEnabled(not busy)

    def show_error(self, message):
        self.message.setText(message)

    def show_test_success(self):
        self.message.setText("Подключение успешно проверено.")

    def show_connected(self, database):
        self.password.clear()
        self.accept()

    def reject(self):
        self.password.clear()
        super().reject()
