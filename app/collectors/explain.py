"""План PostgreSQL в JSON, без EXPLAIN ANALYZE."""
from app.database.query_executor import QueryExecutor


def collect_explain(connection, sql):
    if not isinstance(sql, str) or not sql.strip():
        raise ValueError("Для EXPLAIN нужен непустой SQL-запрос.")
    row = QueryExecutor(connection).fetch_one("EXPLAIN (FORMAT JSON) " + sql)
    return {"format": "json", "analyze": False, "plan": row["QUERY PLAN"]}
