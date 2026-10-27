"""Получение JSON-плана с необязательным измерением выполнения."""

from app.analyzers.sql_features import parse_query
from app.database.query_executor import QueryExecutor


def collect_explain(connection, sql, *, analyze=False, timeout_ms=5000):
    if not isinstance(sql, str) or not sql.strip():
        raise ValueError("Для EXPLAIN нужен непустой SQL-запрос.")
    parse_query(sql)
    if not isinstance(analyze, bool):
        raise ValueError("Режим ANALYZE должен быть булевым.")
    if (
        isinstance(timeout_ms, bool)
        or not isinstance(timeout_ms, int)
        or not 100 <= timeout_ms <= 30000
    ):
        raise ValueError("Допустимый тайм-аут: 100–30000 мс.")
    executor = QueryExecutor(connection)
    if analyze:
        # Ошибка настройки новой транзакции должна остановить выполнение.
        executor.execute("SET TRANSACTION READ ONLY")
        executor.fetch_one("SELECT set_config('statement_timeout', %s, true)", (str(timeout_ms),))
        executor.fetch_one(
            "SELECT set_config('lock_timeout', %s, true)", (str(min(timeout_ms, 1000)),)
        )
    options = (
        "ANALYZE, BUFFERS, VERBOSE, TIMING, SUMMARY, FORMAT JSON"
        if analyze
        else "VERBOSE, FORMAT JSON"
    )
    row = executor.fetch_one("EXPLAIN (" + options + ") " + sql)
    return {
        "format": "json",
        "analyze": analyze,
        "verbose": True,
        "timeout_ms": timeout_ms if analyze else None,
        "plan": row["QUERY PLAN"],
    }
