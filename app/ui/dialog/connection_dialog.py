from PySide6 import QtCore, QtWidgets


class ConnectionDialog(QtWidgets.QDialog):
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
        hint = QtWidgets.QLabel("Выберите СУБД и укажите параметры подключения.")
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
        self.server_fields = []
        for label, widget in (("Сервер", self.host), ("Порт", self.port),
                              ("База данных", self.database), ("Пользователь", self.user),
                              ("Пароль", self.password), ("SSL", self.sslmode)):
            form.addRow(label, widget)
            self.server_fields.append((form.labelForField(widget), widget))
        self.sqlite_path = QtWidgets.QLineEdit()
        self.sqlite_path.setPlaceholderText("Путь к файлу .sqlite3 или .db")
        self.browse_button = QtWidgets.QPushButton("Обзор…")
        self.browse_button.clicked.connect(self.browse)
        self.file_row = QtWidgets.QWidget()
        file_layout = QtWidgets.QHBoxLayout(self.file_row)
        file_layout.setContentsMargins(0, 0, 0, 0)
        file_layout.addWidget(self.sqlite_path, 1)
        file_layout.addWidget(self.browse_button)
        form.addRow("Файл БД", self.file_row)
        self.file_label = form.labelForField(self.file_row)
        layout.addWidget(self.fields)
        for name, widget in (("Сервер", self.host), ("Порт", self.port),
                             ("База данных", self.database), ("Пользователь", self.user),
                             ("Пароль", self.password), ("Файл SQLite", self.sqlite_path)):
            widget.setAccessibleName(name)

        self.message = QtWidgets.QLabel("Предпросмотр интерфейса · Подключение к СУБД пока недоступно.")
        self.message.setObjectName("muted")
        self.message.setWordWrap(True)
        self.message.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        layout.addWidget(self.message)
        buttons = QtWidgets.QHBoxLayout()
        self.test_button = QtWidgets.QPushButton("Проверить подключение")
        self.test_button.setEnabled(False)
        self.test_button.setToolTip("Будет доступно после добавления подключения к СУБД")
        buttons.addWidget(self.test_button)
        buttons.addStretch()
        self.cancel_button = QtWidgets.QPushButton("Отмена")
        self.cancel_button.clicked.connect(self.reject)
        buttons.addWidget(self.cancel_button)
        self.connect_button = QtWidgets.QPushButton("Подключиться")
        self.connect_button.setObjectName("primary")
        self.connect_button.setEnabled(False)
        self.connect_button.setToolTip("Будет доступно после добавления подключения к СУБД")
        buttons.addWidget(self.connect_button)
        layout.addLayout(buttons)
        self.update_fields()

    def update_fields(self):
        engine = 'postgresql' 
        sqlite = engine == "sqlite"
        for label, widget in self.server_fields:
            label.setVisible(not sqlite)
            widget.setVisible(not sqlite)
        self.file_row.setVisible(sqlite)
        self.file_label.setVisible(sqlite)
        if not sqlite:
            self.port.setValue({"postgresql": 5432, "mysql": 3306, "mariadb": 3306, "mssql": 1433}[engine])
            self.user.setPlaceholderText("postgres" if engine == "postgresql" else "Имя пользователя")
            self.sslmode.setEnabled(engine != "mssql")
            self.sslmode.setToolTip("Настройка SSL для этой СУБД появится позже" if engine == "mssql" else "")

    def browse(self):
        filename, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Выбрать базу SQLite", "", "SQLite (*.sqlite *.sqlite3 *.db);;Все файлы (*)"
        )
        if filename:
            self.sqlite_path.setText(filename)

    def reject(self):
        self.password.clear()
        super().reject()
