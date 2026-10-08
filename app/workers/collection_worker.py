from PySide6 import QtCore

from app.services.collection_service import collect_report


class CollectionWorker(QtCore.QThread):
    report_ready = QtCore.Signal(object)
    failed = QtCore.Signal(str)

    def __init__(self, parameters, config, hardware, parent=None, *, metadata=None, scope="database", sql=None):
        super().__init__(parent)
        self.parameters = dict(parameters) if parameters is not None else None
        self.config, self.hardware = config, hardware
        self.metadata = metadata
        self.scope, self.sql = scope, sql

    def run(self):
        try:
            report = collect_report(
                self.parameters, self.config, self.hardware, self.isInterruptionRequested,
                metadata=self.metadata,
                scope=self.scope, sql=self.sql,
            )
            if report is not None and not self.isInterruptionRequested():
                self.report_ready.emit(report)
        except Exception as error:
            self.failed.emit(type(error).__name__)
        finally:
            self.parameters = None
