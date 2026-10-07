"""Независимый сбор модулей: отказ БД не скрывает успешные показатели ОС."""
from app.collectors.postgres import collect_postgres
from app.collectors.system import collect_system


def collect_report(parameters, config, hardware, cancelled=lambda: False):
    report = {"schema_version": 1, "postgres": None, "system": None, "errors": {}}
    for name, enabled, collector in (
        ("postgres", config, lambda: collect_postgres(parameters)),
        ("system", hardware, lambda: collect_system(cancelled=cancelled)),
    ):
        if cancelled():
            return None
        if enabled:
            try:
                if name == "postgres" and parameters is None:
                    raise ValueError("Подключение не задано")
                report[name] = collector()
            except Exception as error:
                # Сообщение драйвера может содержать параметры: не показываем/не логируем.
                report["errors"][name] = type(error).__name__
    return None if cancelled() else report
