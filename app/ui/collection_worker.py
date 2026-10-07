from PySide6 import QtCore

from app.services.collection_service import collect_report


class CollectionWorker(QtCore.QThread):
    report_ready = QtCore.Signal(object)

    def __init__(self, parameters, config, hardware, parent=None):
        super().__init__(parent)
        self.parameters = dict(parameters) if parameters is not None else None
        self.config, self.hardware = config, hardware

    def run(self):
        try:
            report = collect_report(
                self.parameters, self.config, self.hardware, self.isInterruptionRequested,
            )
            if report is not None and not self.isInterruptionRequested():
                self.report_ready.emit(report)
        finally:
            self.parameters = None
