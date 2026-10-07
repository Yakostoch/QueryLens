from PySide6 import QtCore

from app.workers.collection_worker import CollectionWorker


class CollectionController(QtCore.QObject):
    started = QtCore.Signal()
    report_ready = QtCore.Signal(object)
    failed = QtCore.Signal(str)
    finished = QtCore.Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.worker = None

    @property
    def busy(self):
        return self.worker is not None

    def can_start(self, parameters, config, hardware):
        return not self.busy and bool(config or hardware) and (not config or parameters is not None)

    def start(self, parameters, config, hardware):
        if not self.can_start(parameters, config, hardware):
            return False
        self.worker = CollectionWorker(parameters, config, hardware, self)
        self.worker.report_ready.connect(self.report_ready.emit)
        self.worker.failed.connect(self.failed.emit)
        self.worker.finished.connect(self._finished)
        self.started.emit()
        self.worker.start()
        return True

    def cancel(self):
        if self.worker is not None:
            self.worker.requestInterruption()

    def _finished(self):
        interrupted = self.worker.isInterruptionRequested()
        self.worker.deleteLater()
        self.worker = None
        self.finished.emit(interrupted)
