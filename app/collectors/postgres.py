"""Совместимый вход для старого отчёта: объединяет два отдельных сборщика."""
from datetime import datetime, timezone

from app.collectors.configuration import collect_configuration
from app.collectors.metadata import collect_metadata
from app.database.connection import connect_postgresql


def collect_postgres(parameters, *, config=True, metadata=True, scope="database", sql=None):
    """Создаёт соединение в вызывающем потоке и сохраняет прежний формат отчёта."""
    with connect_postgresql(**parameters) as connection:
        result = {"version": "", "tables": [], "database_statistics": None,
                  "settings": [], "settings_unavailable": [], "database_size_bytes": None,
                  "configuration_collected": config, "metadata_collected": metadata}
        if metadata:
            result.update(collect_metadata(connection, scope=scope, sql=sql))
        if config:
            result.update(collect_configuration(connection))
    result["collected_at"] = datetime.now(timezone.utc).isoformat()
    return result
