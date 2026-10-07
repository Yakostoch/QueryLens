"""Чтение конфигурации и системных каталогов; пользовательский SQL не выполняется."""
from datetime import datetime, timezone

from app.database.connection import connect_postgresql

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


def collect_postgres(parameters):
    """Создаёт и закрывает собственное соединение внутри рабочего потока."""
    from psycopg.rows import dict_row

    with connect_postgresql(**parameters) as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute("SELECT version() AS version, current_setting('server_version_num') AS version_number")
            result = dict(cursor.fetchone())
            cursor.execute(
                "SELECT name, setting, unit, vartype, source, context, pending_restart "
                "FROM pg_catalog.pg_settings WHERE name = ANY(%s) ORDER BY name",
                (list(SETTINGS),),
            )
            result["settings"] = cursor.fetchall()
            cursor.execute(
                "SELECT numbackends, xact_commit, xact_rollback, blks_read, blks_hit, "
                "temp_files, temp_bytes, deadlocks, stats_reset FROM pg_catalog.pg_stat_database "
                "WHERE datname = current_database()"
            )
            result["database_statistics"] = cursor.fetchone()
            cursor.execute(
                "SELECT n.nspname AS schema, c.relname AS name, "
                "(SELECT count(*) FROM pg_catalog.pg_attribute a WHERE a.attrelid = c.oid "
                "AND a.attnum > 0 AND NOT a.attisdropped) AS columns, "
                "(SELECT count(*) FROM pg_catalog.pg_index i WHERE i.indrelid = c.oid) AS indexes "
                "FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
                "WHERE c.relkind IN ('r', 'p') AND n.nspname NOT LIKE 'pg_%' "
                "AND n.nspname <> 'information_schema' ORDER BY n.nspname, c.relname"
            )
            result["tables"] = cursor.fetchall()
            # Размер может быть недоступен роли; не теряем уже собранные настройки.
            try:
                with connection.transaction():
                    cursor.execute("SELECT pg_catalog.pg_database_size(current_database()) AS bytes")
                    result["database_size_bytes"] = cursor.fetchone()["bytes"]
            except Exception:
                result["database_size_bytes"] = None
    available = {row["name"] for row in result["settings"]}
    result["settings_unavailable"] = sorted(set(SETTINGS) - available)
    result["collected_at"] = datetime.now(timezone.utc).isoformat()
    return result
