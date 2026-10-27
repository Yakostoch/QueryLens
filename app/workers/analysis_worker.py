from psycopg import InterfaceError, OperationalError
from PySide6 import QtCore

from app.analyzers.sql_features import SQLInputError
from app.services.analysis_execution import AnalysisCancellation, AnalysisCancelled


class AnalysisWorker(QtCore.QThread):
    result_ready = QtCore.Signal(str)
    failed = QtCore.Signal(str)
    cancelled = QtCore.Signal()

    def __init__(
        self, service, sql, parameters, context, parent=None, *, analyze=False, timeout_ms=5000
    ):
        super().__init__(parent)
        self.service = service
        self.sql = sql
        self.parameters = dict(parameters)
        self.context = context
        self.analyze_enabled = analyze
        self.timeout_ms = timeout_ms
        self.cancellation = AnalysisCancellation()

    def requestInterruption(self):
        super().requestInterruption()
        self.cancellation.cancel()

    def run(self):
        try:
            if self.isInterruptionRequested():
                return
            result = self.service.analyze(
                self.sql,
                self.parameters,
                self.context,
                analyze=self.analyze_enabled,
                timeout_ms=self.timeout_ms,
                cancellation=self.cancellation,
            )
            if not self.isInterruptionRequested():
                self.result_ready.emit(result)
        except AnalysisCancelled:
            pass
        except SQLInputError as error:
            if not self.isInterruptionRequested():
                self.failed.emit(str(error))
        except Exception as error:
            if not self.isInterruptionRequested():
                code = getattr(error, "sqlstate", None)
                if code == "57014":
                    self.failed.emit(
                        "Выполнение отменено PostgreSQL или превышен тайм-аут. Завершённых измерений нет."
                    )
                elif code == "55P03":
                    self.failed.emit(
                        "Превышен лимит ожидания блокировки. Завершённых измерений нет."
                    )
                elif code == "42601":
                    self.failed.emit(
                        "Ошибка синтаксиса SQL. Проверьте запрос и выбранный фрагмент."
                    )
                elif code == "42P01":
                    self.failed.emit(
                        "Источник данных не найден. Проверьте таблицу или представление, схему и выбранную базу."
                    )
                elif code == "42703":
                    self.failed.emit(
                        "Столбец не найден. Проверьте имя столбца, псевдонимы и регистр в кавычках."
                    )
                elif code == "42501":
                    self.failed.emit(
                        "Недостаточно прав для анализа запроса. Проверьте доступ роли к схеме и объектам."
                    )
                elif (isinstance(code, str) and code.startswith("08")) or isinstance(
                    error, (OperationalError, InterfaceError)
                ):
                    self.failed.emit(
                        "Подключение к PostgreSQL недоступно. Проверьте сервер и подключение, затем повторите анализ."
                    )
                else:
                    self.failed.emit(f"Не удалось проанализировать SQL ({type(error).__name__}).")
        finally:
            if self.isInterruptionRequested():
                self.cancelled.emit()
            self.parameters = None
