from PySide6 import QtCore

from app.services.monitoring_service import monitor_resources


class MonitoringWorker(QtCore.QThread):
    sample_ready = QtCore.Signal(object)
    failed = QtCore.Signal(str)

    def __init__(self, options, interval, parent=None):
        super().__init__(parent)
        self.options = dict(options)
        self.interval = interval

    def run(self):
        try:
            for report in monitor_resources(self.options, self.interval, self.isInterruptionRequested):
                if not self.isInterruptionRequested():
                    self.sample_ready.emit(report)
        except ModuleNotFoundError as error:
            message = ("Для мониторинга установите psutil в окружение проекта: python -m pip install psutil"
                       if error.name == "psutil" else "Не найдена зависимость мониторинга.")
            self.failed.emit(message)
        except Exception as error:
            self.failed.emit(f"Мониторинг остановлен ({type(error).__name__}).")
