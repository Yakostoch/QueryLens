"""Каталоги и общая статистика БД; это не статистика конкретного SQL."""
from app.database.query_executor import QueryExecutor


def collect_metadata(connection):
    executor = QueryExecutor(connection)
    result = executor.fetch_one(
        "SELECT version() AS version, current_setting('server_version_num') AS version_number"
    )
    result["database_statistics"] = executor.fetch_one(
        "SELECT numbackends, xact_commit, xact_rollback, blks_read, blks_hit, "
        "temp_files, temp_bytes, deadlocks, stats_reset FROM pg_catalog.pg_stat_database "
        "WHERE datname = current_database()"
    )
    result["tables"] = executor.fetch_all(
        "SELECT n.nspname AS schema, c.relname AS name, "
        "(SELECT count(*) FROM pg_catalog.pg_attribute a WHERE a.attrelid = c.oid "
        "AND a.attnum > 0 AND NOT a.attisdropped) AS columns, "
        "(SELECT count(*) FROM pg_catalog.pg_index i WHERE i.indrelid = c.oid) AS indexes "
        "FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
        "WHERE c.relkind IN ('r', 'p') AND n.nspname NOT LIKE 'pg_%' "
        "AND n.nspname <> 'information_schema' ORDER BY n.nspname, c.relname"
    )
    try:
        # Savepoint preserves other results when this role cannot read the size.
        with connection.transaction():
            result["database_size_bytes"] = executor.fetch_one(
                "SELECT pg_catalog.pg_database_size(current_database()) AS bytes"
            )["bytes"]
    except Exception:
        result["database_size_bytes"] = None
    return result
