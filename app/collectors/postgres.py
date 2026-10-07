"""Совместимый вход для старого отчёта: объединяет два отдельных сборщика."""
from datetime import datetime, timezone

from app.collectors.configuration import collect_configuration
from app.collectors.metadata import collect_metadata
from app.database.connection import connect_postgresql


def collect_postgres(parameters):
    """Создаёт соединение в вызывающем потоке и сохраняет прежний формат отчёта."""
    with connect_postgresql(**parameters) as connection:
        result = collect_metadata(connection)
        result.update(collect_configuration(connection))
    result["collected_at"] = datetime.now(timezone.utc).isoformat()
    return result
