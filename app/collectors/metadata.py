"""Каталоги и общая статистика БД; это не статистика конкретного SQL."""

from app.collectors.relation_details import collect_relation_details
from app.database.query_executor import QueryExecutor


def collect_metadata(connection, *, scope="database", sql=None, additional_relations=()):
    from app.services.query_structure import QueryStructureError, query_structure

    if scope not in {"database", "query"}:
        raise QueryStructureError("Неизвестная область сбора структуры.")
    if scope == "query" and (not sql or not sql.strip()):
        raise QueryStructureError("Введите SQL на вкладке SQL и поместите курсор в нужный запрос.")
    executor = QueryExecutor(connection)
    result = executor.fetch_one(
        "SELECT version() AS version, current_setting('server_version_num') AS version_number"
    )
    result["database_statistics"] = executor.fetch_one(
        "SELECT numbackends, xact_commit, xact_rollback, blks_read, blks_hit, "
        "temp_files, temp_bytes, deadlocks, stats_reset FROM pg_catalog.pg_stat_database "
        "WHERE datname = current_database()"
    )
    table_query = (
        "SELECT c.oid, n.nspname AS schema, c.relname AS name, "
        "(SELECT count(*) FROM pg_catalog.pg_attribute a WHERE a.attrelid = c.oid "
        "AND a.attnum > 0 AND NOT a.attisdropped) AS columns, "
        "(SELECT count(*) FROM pg_catalog.pg_index i WHERE i.indrelid = c.oid) AS indexes "
        "FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
        "WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f') "
    )

    def fields(oid):
        return executor.fetch_all(
            "SELECT attname AS name, pg_catalog.format_type(atttypid, atttypmod) AS type, "
            "NOT attnotnull AS nullable FROM pg_catalog.pg_attribute "
            "WHERE attrelid = %s AND attnum > 0 AND NOT attisdropped ORDER BY attnum",
            (oid,),
        )

    def resolve(name):
        table = executor.fetch_one(table_query + "AND c.oid = pg_catalog.to_regclass(%s)", (name,))
        if table:
            table["fields"] = fields(table["oid"])
            table["all_fields"] = list(table["fields"])
            table["schema_columns"] = len(table["all_fields"])
        return table

    if scope == "query":
        result["source_bindings"] = []
        result["tables"] = query_structure(sql, resolve, bindings=result["source_bindings"])
    else:
        result["tables"] = executor.fetch_all(
            table_query
            + "AND left(n.nspname, 3) <> 'pg_' AND n.nspname <> 'information_schema' ORDER BY n.nspname, c.relname"
        )
        for table in result["tables"]:
            table["fields"] = fields(table["oid"])
            table["all_fields"] = list(table["fields"])
            table["schema_columns"] = len(table["all_fields"])
    # План может содержать базовые таблицы представления, не названные в SQL.
    # Получаем их сведения через каталог.
    known = {(table["schema"], table["name"]): table for table in result["tables"]}
    for table in result["tables"]:
        table["metadata_origins"] = ["sql" if scope == "query" else "database"]
    result["additional_relation_errors"] = []
    for schema_name, relation_name in sorted(set(additional_relations)):
        key = (schema_name, relation_name)
        if key in known:
            if "plan" not in known[key]["metadata_origins"]:
                known[key]["metadata_origins"].append("plan")
            continue
        try:
            with connection.transaction():
                quoted = ".".join('"' + name.replace('"', '""') + '"' for name in key)
                table = resolve(quoted)
                if table is None:
                    raise LookupError("Plan relation no longer resolves")
            table["metadata_origins"] = ["plan"]
            table["column_usage_status"] = "not_resolved_from_sql"
            result["tables"].append(table)
            known[key] = table
        except Exception as error:
            result["additional_relation_errors"].append(
                {"schema": schema_name, "name": relation_name, "error": type(error).__name__}
            )
    for table in result["tables"]:
        table["diagnostics"] = collect_relation_details(connection, table)
    result["metadata_schema_version"] = 2
    result["metadata_scope"] = scope
    result["metadata_sql"] = sql if scope == "query" else None
    try:
        # Точка сохранения позволяет продолжить сбор при отказе в доступе.
        with connection.transaction():
            result["database_size_bytes"] = executor.fetch_one(
                "SELECT pg_catalog.pg_database_size(current_database()) AS bytes"
            )["bytes"]
    except Exception:
        result["database_size_bytes"] = None
    return result
