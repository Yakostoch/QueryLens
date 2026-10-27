"""Чередующиеся измерения двух запросов."""

from statistics import median

from app.analyzers.plan_features import extract_plan_features
from app.analyzers.runtime_analysis import analyze_runtime
from app.analyzers.sql_features import parse_query
from app.collectors.explain import collect_explain
from app.database.query_executor import QueryExecutor

SESSION_SQL = """SELECT current_database() AS database, current_user AS role,
 current_setting('server_version_num') AS version,
 current_setting('search_path') AS search_path, current_setting('TimeZone') AS timezone,
 current_setting('work_mem') AS work_mem, current_setting('jit') AS jit,
 current_setting('max_parallel_workers_per_gather') AS parallel_workers,
 current_setting('transaction_read_only') AS read_only"""


def compare_queries(
    database_service,
    parameters,
    original,
    candidate,
    *,
    repeats=3,
    timeout_ms=5000,
    cancellation=None,
):
    parse_query(original)
    parse_query(candidate)
    if isinstance(repeats, bool) or not isinstance(repeats, int) or not 1 <= repeats <= 5:
        raise ValueError("Допустимо 1–5 измерений каждого запроса.")
    samples = []
    queries = {"original": original, "candidate": candidate}
    for iteration in range(repeats):
        order = ("original", "candidate") if iteration % 2 == 0 else ("candidate", "original")
        for label in order:
            if cancellation:
                cancellation.check()
            with database_service.connect(parameters) as connection:
                try:
                    if cancellation:
                        cancellation.bind(connection)
                    explained = collect_explain(
                        connection, queries[label], analyze=True, timeout_ms=timeout_ms
                    )
                    if cancellation:
                        cancellation.check()
                    session = QueryExecutor(connection).fetch_one(SESSION_SQL)
                    plan = extract_plan_features(explained["plan"])
                    runtime = analyze_runtime(plan, measured=True)
                    if runtime["execution_time_ms"] is None:
                        raise ValueError("Сервер не вернул Execution Time для сравнения.")
                    samples.append(
                        dict(
                            query=label,
                            iteration=iteration + 1,
                            session=session,
                            runtime_analysis=runtime,
                            explain=explained,
                        )
                    )
                finally:
                    if cancellation:
                        cancellation.unbind()
    if cancellation:
        cancellation.check()
    original_times = [
        s["runtime_analysis"]["execution_time_ms"] for s in samples if s["query"] == "original"
    ]
    candidate_times = [
        s["runtime_analysis"]["execution_time_ms"] for s in samples if s["query"] == "candidate"
    ]
    same = all(s["session"] == samples[0]["session"] for s in samples)
    first, second = median(original_times), median(candidate_times)
    return dict(
        schema_version=1,
        mode="explicit_alternating_execution",
        original_sql=original,
        candidate_sql=candidate,
        samples=samples,
        original_median_ms=first,
        candidate_median_ms=second,
        session_context_matches=same,
        median_ratio_original_to_candidate=first / second if same and second > 0 else None,
        equivalence_status="not_checked",
        limitations=[
            "Оба SELECT выполнялись по явному выбору. Равенство результатов не проверено.",
            "Сравниваются медианы повторов с чередованием порядка; это наблюдение, не гарантия ускорения.",
            "Соединение создаётся заново для каждого измерения. Кеш не сбрасывается; общая нагрузка и изменения данных не изолированы.",
            "Проверены перечисленные настройки сессии, но это не полный снимок конфигурации и данных.",
            "Execution Time не включает подключение и передачу результата клиенту. Ошибка/тайм-аут прерывают сравнение без готового вывода.",
        ],
    )
