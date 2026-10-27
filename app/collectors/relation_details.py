"""Сведения о таблицах, индексах, ограничениях и статистике."""

from datetime import datetime, timezone

from app.database.query_executor import QueryExecutor

INDEX_QUERY = """
SELECT i.indexrelid AS oid, ni.nspname AS schema, ci.relname AS name,
       am.amname AS access_method, pg_catalog.pg_get_indexdef(i.indexrelid) AS definition,
       pg_catalog.pg_get_expr(i.indpred, i.indrelid) AS predicate,
       i.indisunique AS is_unique, i.indisprimary AS is_primary,
       i.indisvalid AS is_valid, i.indisready AS is_ready, i.indislive AS is_live,
       i.indimmediate AS is_immediate,
       (pg_catalog.to_jsonb(i)->>'indnullsnotdistinct')::boolean AS nulls_not_distinct,
       k.position, k.position <= i.indnkeyatts AS is_key,
       a.attname AS column_name,
       CASE WHEN i.indkey[k.position - 1] = 0
            THEN pg_catalog.pg_get_indexdef(i.indexrelid, k.position, true) END AS expression,
       no.nspname AS operator_class_schema, op.opcname AS operator_class,
       nc.nspname AS collation_schema, co.collname AS collation,
       CASE WHEN am.amname = 'btree' AND k.position <= i.indnkeyatts
            THEN CASE WHEN (i.indoption[k.position - 1] & 1) = 1 THEN 'DESC' ELSE 'ASC' END
       END AS direction,
       CASE WHEN am.amname = 'btree' AND k.position <= i.indnkeyatts
            THEN (i.indoption[k.position - 1] & 2) = 2 END AS nulls_first
FROM pg_catalog.pg_index i
JOIN pg_catalog.pg_class ci ON ci.oid = i.indexrelid
JOIN pg_catalog.pg_namespace ni ON ni.oid = ci.relnamespace
JOIN pg_catalog.pg_am am ON am.oid = ci.relam
CROSS JOIN LATERAL pg_catalog.generate_series(1, i.indnatts::integer) AS k(position)
LEFT JOIN pg_catalog.pg_attribute a ON a.attrelid = i.indrelid
     AND a.attnum = i.indkey[k.position - 1] AND NOT a.attisdropped
LEFT JOIN pg_catalog.pg_opclass op ON op.oid = i.indclass[k.position - 1]
LEFT JOIN pg_catalog.pg_namespace no ON no.oid = op.opcnamespace
LEFT JOIN pg_catalog.pg_collation co ON co.oid = i.indcollation[k.position - 1]
LEFT JOIN pg_catalog.pg_namespace nc ON nc.oid = co.collnamespace
WHERE i.indrelid = %s
ORDER BY i.indexrelid, k.position
"""

CONSTRAINT_QUERY = """
SELECT c.oid, c.conname AS name, c.contype AS kind,
       pg_catalog.pg_get_constraintdef(c.oid, true) AS definition,
       c.convalidated AS is_validated, c.condeferrable AS is_deferrable,
       c.condeferred AS initially_deferred,
       (pg_catalog.to_jsonb(c)->>'conenforced')::boolean AS is_enforced,
       NULLIF(c.conindid, 0) AS supporting_index_oid,
       ARRAY(SELECT a.attname FROM pg_catalog.unnest(c.conkey) WITH ORDINALITY AS k(attnum, position)
             JOIN pg_catalog.pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum
             ORDER BY k.position) AS columns,
       NULLIF(c.confrelid, 0) AS referenced_relation_oid,
       rn.nspname AS referenced_schema, rc.relname AS referenced_relation,
       ARRAY(SELECT a.attname FROM pg_catalog.unnest(c.confkey) WITH ORDINALITY AS k(attnum, position)
             JOIN pg_catalog.pg_attribute a ON a.attrelid = c.confrelid AND a.attnum = k.attnum
             ORDER BY k.position) AS referenced_columns
FROM pg_catalog.pg_constraint c
LEFT JOIN pg_catalog.pg_class rc ON rc.oid = c.confrelid
LEFT JOIN pg_catalog.pg_namespace rn ON rn.oid = rc.relnamespace
WHERE c.conrelid = %s
ORDER BY c.oid
"""

RELATION_QUERY = """
SELECT c.relkind AS relation_kind, c.relispartition AS is_partition,
       c.relrowsecurity AS row_security_enabled, c.relforcerowsecurity AS row_security_forced,
       EXISTS (SELECT 1 FROM pg_catalog.pg_inherits inh WHERE inh.inhparent = c.oid) AS has_inheritance_children,
       CASE WHEN c.reltuples >= 0 THEN c.reltuples END AS estimated_rows,
       c.relpages AS estimated_pages, c.reloptions AS options,
       pg_catalog.pg_relation_size(c.oid) AS main_fork_bytes,
       pg_catalog.pg_table_size(c.oid) AS table_bytes,
       pg_catalog.pg_indexes_size(c.oid) AS indexes_bytes,
       pg_catalog.pg_total_relation_size(c.oid) AS total_bytes,
       ARRAY(SELECT inhparent FROM pg_catalog.pg_inherits WHERE inhrelid = c.oid
             ORDER BY inhseqno) AS parent_oids,
       s.last_analyze, s.last_autoanalyze, s.n_mod_since_analyze,
       s.n_live_tup, s.n_dead_tup
FROM pg_catalog.pg_class c
LEFT JOIN pg_catalog.pg_stat_all_tables s ON s.relid = c.oid
WHERE c.oid = %s
"""

COLUMN_STATS_QUERY = """
SELECT attname AS column_name, inherited, null_frac, avg_width, n_distinct, correlation
FROM pg_catalog.pg_stats
WHERE schemaname = %s AND tablename = %s
ORDER BY attname, inherited
"""


def _indexes(executor, oid):
    indexes = {}
    element_fields = (
        "position",
        "column_name",
        "expression",
        "operator_class_schema",
        "operator_class",
        "collation_schema",
        "collation",
        "direction",
        "nulls_first",
    )
    for row in executor.fetch_all(INDEX_QUERY, (oid,)):
        if row["oid"] not in indexes:
            indexes[row["oid"]] = {
                key: value
                for key, value in row.items()
                if key not in element_fields and key != "is_key"
            }
            indexes[row["oid"]].update(keys=[], included_columns=[])
        index = indexes[row["oid"]]
        if row["is_key"]:
            index["keys"].append({key: row[key] for key in element_fields})
        else:
            index["included_columns"].append(row["column_name"])
    return list(indexes.values())


def collect_relation_details(connection, table):
    """Собирает источники отдельно; ошибка одного не скрывает остальные."""
    oid = table["oid"]
    if isinstance(oid, bool) or not isinstance(oid, int) or oid <= 0:
        raise ValueError("Expected a resolved PostgreSQL relation OID")
    executor = QueryExecutor(connection)
    sources = {}
    collectors = (
        ("indexes", lambda: _indexes(executor, oid)),
        ("constraints", lambda: executor.fetch_all(CONSTRAINT_QUERY, (oid,))),
        ("relation", lambda: executor.fetch_one(RELATION_QUERY, (oid,))),
        (
            "column_statistics",
            lambda: executor.fetch_all(COLUMN_STATS_QUERY, (table["schema"], table["name"])),
        ),
    )
    for name, collect in collectors:
        try:
            # Во вложенной транзакции создаётся точка сохранения.
            with connection.transaction():
                data = collect()
            status = (
                "unavailable" if data is None or name == "column_statistics" and not data else "ok"
            )
            sources[name] = {"status": status, "data": data, "error": None}
        except Exception as error:
            # Текст ошибки драйвера может содержать SQL или параметры подключения.
            sources[name] = {"status": "error", "data": None, "error": type(error).__name__}
    return {
        "schema_version": 1,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "size_scope": "relation_only",
        "sources": sources,
    }
