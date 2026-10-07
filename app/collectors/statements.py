"""Накопленные показатели pg_stat_statements по известному queryid."""
from app.database.query_executor import QueryExecutor


class StatementsUnavailable(RuntimeError):
    """Расширение pg_stat_statements не установлено в текущей базе."""


def collect_statements(connection, query_id):
    from psycopg import sql

    if isinstance(query_id, bool) or not isinstance(query_id, int):
        raise ValueError("Нужен целочисленный queryid PostgreSQL.")
    executor = QueryExecutor(connection)
    extension = executor.fetch_one(
        "SELECT n.nspname AS schema FROM pg_catalog.pg_extension e "
        "JOIN pg_catalog.pg_namespace n ON n.oid = e.extnamespace "
        "WHERE e.extname = 'pg_stat_statements'"
    )
    if extension is None:
        raise StatementsUnavailable()
    # Schema is discovered from the extension, not assumed to be public.
    # Keep separate rows per role/toplevel; don't merge unrelated workloads.
    rows = executor.fetch_all(sql.SQL(
        "SELECT s.* FROM {}.pg_stat_statements s "
        "WHERE s.dbid = (SELECT oid FROM pg_catalog.pg_database WHERE datname = current_database()) "
        "AND s.queryid = %s ORDER BY s.userid"
    ).format(sql.Identifier(extension["schema"])), (query_id,))
    return {"query_id": query_id, "scope": "current_database", "statements": rows}
