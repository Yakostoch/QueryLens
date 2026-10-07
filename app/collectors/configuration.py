"""Конфигурация диагностической сессии PostgreSQL."""
from app.database.query_executor import QueryExecutor
SETTINGS = {
    "shared_buffers": "Буферный кеш PostgreSQL; сопоставлять с чтениями в плане.",
    "work_mem": "Память отдельной сортировки/хеширования; проверять временный диск.",
    "hash_mem_multiplier": "Множитель work_mem для хеширования; проверять Batches и temp.",
    "maintenance_work_mem": "Память обслуживания и построения индексов; влияние на SELECT косвенное.",
    "effective_cache_size": "Оценка кеша для планировщика; память не выделяет.",
    "effective_io_concurrency": "Оценка возможностей параллельного I/O; зависит от версии и ОС.",
    "random_page_cost": "Оценочная стоимость случайного чтения; влияет на выбор доступа.",
    "seq_page_cost": "Оценочная стоимость последовательного чтения; это не время в мс.",
    "default_statistics_target": "Подробность статистики по умолчанию; сравнивать оценки строк с фактом.",
    "max_connections": "Предел соединений; конкуренция и расход памяти при нагрузке.",
    "max_worker_processes": "Общий предел фоновых работников; контекст параллелизма.",
    "max_parallel_workers": "Общий предел параллельных работников для всех запросов.",
    "max_parallel_workers_per_gather": "Предел работников одного Gather; проверять реально запущенных.",
    "autovacuum": "Автоочистка и обновление статистики; on не гарантирует своевременность.",
    "autovacuum_vacuum_threshold": "Постоянная часть порога очистки; нужна статистика конкретной таблицы.",
    "autovacuum_vacuum_scale_factor": "Доля размера таблицы в пороге очистки; учитывать параметры таблицы.",
    "autovacuum_analyze_threshold": "Постоянная часть порога обновления статистики.",
    "autovacuum_analyze_scale_factor": "Доля размера таблицы в пороге ANALYZE; проверять свежесть статистики.",
    "max_wal_size": "Мягкий ориентир WAL; частые checkpoints могут создавать конкурирующую запись.",
    "checkpoint_timeout": "Интервал checkpoints; влияние на SELECT преимущественно через I/O.",
    "checkpoint_completion_target": "Распределение записи checkpoint по времени; не загрузка диска.",
    "jit": "Разрешение JIT; компиляция может ускорять расчёт или добавлять задержку.",
    "jit_above_cost": "Порог стоимости JIT; оценочные единицы, не миллисекунды.",
    "jit_inline_above_cost": "Порог встраивания функций JIT; проверять время компиляции.",
    "jit_optimize_above_cost": "Порог более дорогой оптимизации JIT.",
    "enable_seqscan": "Предпочтительность Seq Scan; последовательное чтение не является ошибкой.",
    "enable_indexscan": "Доступность индексного пути; не создаёт нужный индекс.",
    "enable_indexonlyscan": "Индексное чтение без heap при подходящих условиях; проверять Heap Fetches.",
    "enable_hashjoin": "Предпочтительность Hash Join; проверять размер входа и временный диск.",
    "enable_mergejoin": "Предпочтительность Merge Join; учитывать сортировки входов.",
    "enable_nestloop": "Предпочтительность Nested Loop; проверять loops и стоимость внутреннего доступа.",
    "track_io_timing": "Наблюдаемость времени I/O; off не означает отсутствие ожидания.",
    "compute_query_id": "Идентификатор для сопоставления запросов; не доказательство скорости.",
}


def collect_configuration(connection):
    rows = QueryExecutor(connection).fetch_all(
        "SELECT name, setting, unit, vartype, source, context, pending_restart "
        "FROM pg_catalog.pg_settings WHERE name = ANY(%s) ORDER BY name",
        (list(SETTINGS),),
    )
    return {
        "settings": rows,
        "settings_unavailable": sorted(set(SETTINGS) - {row["name"] for row in rows}),
    }


