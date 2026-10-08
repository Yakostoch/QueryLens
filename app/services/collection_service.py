"""Независимый сбор модулей: отказ БД не скрывает успешные показатели ОС."""
from app.collectors.postgres import collect_postgres
from app.collectors.system import collect_system
from app.collectors.configuration import collect_configuration
from app.collectors.metadata import collect_metadata
from app.collectors.explain import collect_explain
from app.collectors.statements import collect_statements, StatementsUnavailable
from app.database.connection import connect_postgresql
from datetime import datetime, timezone
from app.services.query_structure import QueryStructureError


def collect_features(parameters, *, sql=None, query_id=None, configuration=True,
                     metadata=True, hardware=True, cancelled=lambda: False):
    """Пять независимых источников схемы; вход для будущего анализатора.

    sql и query_id задаются явно вызывающим кодом. query_id не вычисляется
    из текста SQL. Парсер и модель не входят в ответственность сборщиков.
    Каждый источник БД получает собственное соединение в вызывающем потоке.
    """
    def database(collector, *args):
        if parameters is None:
            raise ValueError("Подключение не задано")
        with connect_postgresql(**parameters) as connection:
            return collector(connection, *args)

    sources = {}
    for name, enabled, collect in (
        ("explain", sql is not None, lambda: database(collect_explain, sql)),
        ("pg_stat_statements", query_id is not None, lambda: database(collect_statements, query_id)),
        ("metadata", metadata, lambda: database(collect_metadata)),
        ("configuration", configuration, lambda: database(collect_configuration)),
        ("system", hardware, lambda: collect_system(cancelled=cancelled)),
    ):
        if cancelled():
            return None
        entry = {"status": "skipped", "data": None, "error": None}
        if enabled:
            try:
                entry.update(status="ok", data=collect())
            except StatementsUnavailable:
                entry.update(status="unavailable", error="StatementsUnavailable")
            except Exception as error:
                # Driver messages can contain credentials and SQL: expose only the type.
                entry.update(status="error", error=type(error).__name__)
        sources[name] = entry
    if cancelled():
        return None
    return {"schema_version": 1, "collected_at": datetime.now(timezone.utc).isoformat(),
            "sources": sources}


def collect_report(parameters, config, hardware, cancelled=lambda: False, *, metadata=None,
                   scope="database", sql=None):
    report = {"schema_version": 1, "postgres": None, "system": None, "errors": {}}
    for name, enabled, collector in (
        ("postgres", config or metadata, lambda: collect_postgres(parameters, config=config,
                                                                 metadata=config if metadata is None else metadata,
                                                                 scope=scope, sql=sql)),
        ("system", hardware, lambda: collect_system(cancelled=cancelled)),
    ):
        if cancelled():
            return None
        if enabled:
            try:
                if name == "postgres" and parameters is None:
                    raise ValueError("Подключение не задано")
                report[name] = collector()
            except QueryStructureError as error:
                report["errors"][name] = type(error).__name__
                report.setdefault("error_details", {})[name] = str(error)
            except Exception as error:
                # Сообщение драйвера может содержать параметры: не показываем/не логируем.
                report["errors"][name] = type(error).__name__
    return None if cancelled() else report
