"""Сравнение результатов и времени двух выбранных SELECT."""

from app.analyzers.sql_features import parse_query
from app.database.query_executor import QueryExecutor
from app.services.query_comparison import compare_queries


def difference_sql(original, candidate):
    # Для сравнения и измерения используем один нормализованный SQL.
    before = parse_query(original).sql(dialect="postgres")
    after = parse_query(candidate).sql(dialect="postgres")
    return f"""WITH ql_before AS ({before}), ql_after AS ({after})
SELECT EXISTS (
 (SELECT * FROM ql_before EXCEPT ALL SELECT * FROM ql_after)
 UNION ALL
 (SELECT * FROM ql_after EXCEPT ALL SELECT * FROM ql_before)
) AS differs"""


def check_results(database_service, parameters, original, candidate, *, timeout_ms=5000):
    if (
        isinstance(timeout_ms, bool)
        or not isinstance(timeout_ms, int)
        or not 100 <= timeout_ms <= 30000
    ):
        raise ValueError("Допустимый тайм-аут: 100–30000 мс.")
    before = parse_query(original).sql(dialect="postgres")
    after = parse_query(candidate).sql(dialect="postgres")
    query = difference_sql(before, after)
    with database_service.connect(parameters) as connection:
        executor = QueryExecutor(connection)
        executor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        executor.fetch_one("SELECT set_config('statement_timeout', %s, true)", (str(timeout_ms),))
        executor.fetch_one(
            "SELECT set_config('lock_timeout', %s, true)", (str(min(timeout_ms, 1000)),)
        )
        signatures = []
        for sql in (before, after):
            with connection.cursor() as cursor:
                cursor.execute(f"SELECT * FROM ({sql}) AS ql_result LIMIT 0", prepare=True)
                signatures.append(
                    [(column.name, column.type_code) for column in cursor.description]
                )
        if signatures[0] != signatures[1]:
            return dict(status="different_contract", columns=signatures)
        row = executor.fetch_one(query)
        if not row or type(row.get("differs")) is not bool:
            raise ValueError("Сервер не вернул результат проверки равенства.")
        return dict(
            status="different_rows" if row["differs"] else "equal_on_snapshot",
            columns=signatures,
            comparison="unordered_multiset_except_all",
        )


def run_experiment(
    database_service, parameters, original, candidate, *, repeats=5, timeout_ms=5000
):
    if isinstance(repeats, bool) or not isinstance(repeats, int) or not 1 <= repeats <= 5:
        raise ValueError("Допустимо 1–5 измерений каждого запроса.")
    before = parse_query(original).sql(dialect="postgres")
    after = parse_query(candidate).sql(dialect="postgres")
    equality = check_results(database_service, parameters, before, after, timeout_ms=timeout_ms)
    result = dict(
        schema_version=1,
        original_sql=before,
        candidate_sql=after,
        result_check=equality,
        measurements=None,
        limitations=[
            "Равенство проверено только на одном снимке текущих данных, а не для всех возможных данных.",
            "EXCEPT ALL учитывает повторы и NULL по правилам сравнения PostgreSQL; порядок строк не проверяется.",
            "Проверяются имена и OID типов полей; это не полный контракт приложения (например, collation и typmod).",
            "Подходят только вручную проверенные детерминированные SELECT, для которых порядок результата не важен.",
            "Замеры идут после проверки, в отдельных соединениях. Данные и нагрузка между запусками могут меняться.",
            "Проверка равенства прогревает кеш; сравнение не является измерением холодного запуска.",
        ],
    )
    if equality["status"] != "equal_on_snapshot":
        return result
    result["measurements"] = compare_queries(
        database_service, parameters, before, after, repeats=repeats, timeout_ms=timeout_ms
    )
    return result


def concise_experiment(result):
    status = result["result_check"]["status"]
    if status == "different_contract":
        return "Поля результата отличаются по имени или типу. Замеры остановлены."
    if status == "different_rows":
        return "Результаты различаются. Замеры остановлены: замена требует проверки смысла запроса."
    measured = result["measurements"]
    lines = [
        "Результаты совпали на текущих данных (без учёта порядка).",
        f"Медиана до: {measured['original_median_ms']:.2f} мс; после: {measured['candidate_median_ms']:.2f} мс.",
    ]
    if not measured["session_context_matches"]:
        lines.append("Настройки сессий различаются. Сравнение времени ненадёжно.")
    else:
        lines.append("Это наблюдение повторных запусков; устойчивое ускорение пока не доказано.")
    return "\n".join(lines)
