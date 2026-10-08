from PySide6 import QtCore

from app.workers.monitoring_worker import MonitoringWorker


class MonitoringController(QtCore.QObject):
    sample_ready = QtCore.Signal(object)
    failed = QtCore.Signal(str)
    busy_changed = QtCore.Signal(bool)
    finished = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.worker = None
        self.stopping = False

    @property
    def busy(self):
        return self.worker is not None

    def start(self, options, interval=1.0):
        if self.busy:
            return
        selected = {key: bool(options.get(key)) for key in ("cpu", "ram", "disk")}
        if not any(selected.values()):
            self.failed.emit("Выберите хотя бы один показатель.")
            return
        if not 0.25 <= interval <= 60:
            self.failed.emit("Недопустимый интервал мониторинга.")
            return
        self.stopping = False
        self.worker = MonitoringWorker(selected, interval, self)
        self.worker.sample_ready.connect(self._sample_ready)
        self.worker.failed.connect(self.failed.emit)
        self.worker.finished.connect(self._finished)
        self.busy_changed.emit(True)
        self.worker.start()

    def stop(self):
        self.stopping = True
        if self.worker is not None:
            self.worker.requestInterruption()

    def _sample_ready(self, report):
        if not self.stopping:
            self.sample_ready.emit(report)

    def _finished(self):
        self.worker.deleteLater()
        self.worker = None
        self.busy_changed.emit(False)
        self.finished.emit()
