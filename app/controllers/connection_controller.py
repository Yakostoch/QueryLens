from PySide6 import QtCore

from app.services.database_service import DatabaseService


class ConnectionController(QtCore.QObject):
    busy_changed = QtCore.Signal(bool)
    failed = QtCore.Signal(str)
    tested = QtCore.Signal()
    connected = QtCore.Signal(str)

    def __init__(self, service=None, parent=None):
        super().__init__(parent)
        self.service = service if service is not None else DatabaseService()
        self.connection = None
        self._parameters = None

    @property
    def parameters(self):
        return dict(self._parameters) if self._parameters is not None else None

    def connect_database(self, parameters, test_only=False):
        self.busy_changed.emit(True)
        try:
            parameters = self.service.normalize_parameters(parameters)
            connection = self.service.connect(parameters)
            if test_only:
                connection.close()
                self.tested.emit()
            else:
                previous = self.connection
                self.connection = connection
                self._parameters = parameters
                if previous is not None:
                    previous.close()
                self.connected.emit(parameters["database"])
        except ValueError as error:
            self.failed.emit(str(error))
        except Exception as error:
            self.failed.emit(
                f"Не удалось подключиться ({type(error).__name__}). "
                "Проверьте сервер, имя базы, роль и пароль."
            )
        finally:
            self.busy_changed.emit(False)

    def close(self):
        self._parameters = None
        if self.connection is not None:
            self.connection.close()
            self.connection = None
