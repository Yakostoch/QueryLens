from PySide6 import QtCore


class AnalysisWorker(QtCore.QThread):
    result_ready = QtCore.Signal(str)
    failed = QtCore.Signal(str)

    def __init__(self, service, sql, parameters, context, parent=None):
        super().__init__(parent)
        self.service = service
        self.sql = sql
        self.parameters = dict(parameters)
        self.context = context

    def run(self):
        try:
            if self.isInterruptionRequested():
                return
            result = self.service.analyze(self.sql, self.parameters, self.context)
            if not self.isInterruptionRequested():
                self.result_ready.emit(result)
        except Exception as error:
            if not self.isInterruptionRequested():
                self.failed.emit(f"Не удалось получить план SQL ({type(error).__name__}).")
        finally:
            self.parameters = None
