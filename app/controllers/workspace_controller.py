from pathlib import Path

from PySide6 import QtCore

from app.controllers.collection_controller import CollectionController
from app.controllers.connection_controller import ConnectionController
from app.controllers.history_controller import HistoryController
from app.controllers.settings_controller import SettingsController
from app.services.analysis_service import AnalysisService
from app.workers.analysis_worker import AnalysisWorker


class WorkspaceController(QtCore.QObject):
    """Coordinates workflows and owns application resources, never widgets."""

    analysis_ready = QtCore.Signal(str, str, bool)
    analysis_failed = QtCore.Signal(str)
    analysis_busy_changed = QtCore.Signal(bool)
    close_ready = QtCore.Signal()

    def __init__(self, data_dir=None, preferences=None, parent=None, analysis_service=None,
                 database_service=None):
        super().__init__(parent)
        directory = Path(data_dir) if data_dir is not None else Path(
            QtCore.QStandardPaths.writableLocation(QtCore.QStandardPaths.StandardLocation.AppLocalDataLocation)
        )
        self.settings = SettingsController(preferences, self)
        self.history = HistoryController(directory / "history.sqlite3", self)
        self.connection = ConnectionController(database_service, self)
        self.collection = CollectionController(self)
        self.analysis_service = analysis_service if analysis_service is not None else AnalysisService(self.connection.service)
        self.analysis_worker = None
        self.closing = False
        self._closed = False
        self.collection.finished.connect(self._collection_finished)

    def connect_database(self, parameters, test_only=False):
        if not self.closing and not self.collection.busy and self.analysis_worker is None:
            self.connection.connect_database(parameters, test_only)

    def can_collect(self, config, hardware):
        return not self.closing and self.collection.can_start(self.connection.parameters, config, hardware)

    def collect(self, config, hardware):
        if self.can_collect(config, hardware):
            self.collection.start(self.connection.parameters, config, hardware)

    def analyze(self, sql, context="Запрос"):
        if self.closing or self.analysis_worker is not None:
            return
        if not sql.strip():
            self.analysis_failed.emit("Нет запроса для анализа. Поместите курсор в SQL-запрос или выделите текст.")
            return
        parameters = self.connection.parameters
        if parameters is None:
            self.analysis_failed.emit("Сначала подключитесь к PostgreSQL через диалог подключения.")
            return
        self.analysis_worker = AnalysisWorker(self.analysis_service, sql, parameters, context, self)
        self.analysis_worker.result_ready.connect(self._analysis_ready)
        self.analysis_worker.failed.connect(self._analysis_failed)
        self.analysis_worker.finished.connect(self._analysis_finished)
        self.analysis_busy_changed.emit(True)
        self.analysis_worker.start()

    def _analysis_ready(self, result):
        if self.closing:
            return
        sql = self.analysis_worker.sql
        saved = self.settings.values.save_history and self.history.add(sql, result)
        self.analysis_ready.emit(sql, result, saved)

    def _analysis_failed(self, message):
        if not self.closing:
            self.analysis_failed.emit(message)

    def _analysis_finished(self):
        self.analysis_worker.deleteLater()
        self.analysis_worker = None
        self.analysis_busy_changed.emit(False)
        if self.closing and self.close():
            self.close_ready.emit()

    def close(self):
        if self._closed:
            return True
        self.closing = True
        if self.collection.busy or self.analysis_worker is not None:
            self.collection.cancel()
            if self.analysis_worker is not None:
                self.analysis_worker.requestInterruption()
            return False
        self.connection.close()
        self.history.close()
        self._closed = True
        return True

    def _collection_finished(self, interrupted):
        if self.closing and self.close():
            self.close_ready.emit()
