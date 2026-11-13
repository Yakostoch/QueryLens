"""Сворачиваемые факты PostgreSQL с пояснениями и источниками данных."""

from app.collectors.configuration import SETTINGS
from app.services.report_formatter import (
    format_report,
    setting_value,
    size,
)
from app.ui.widgets.analysis_report import AnalysisReportView, ReportSection, ReportSql, add_metrics
from app.ui.widgets.relation_report import show_relation

FACT_FIELDS = {
    "value": (
        "Value / Значение характеристики",
        "Значение, полученное при текущем сборе. Недоступные данные не заменяются нулём.",
    ),
    "source": (
        "Source / Источник",
        "Источник текущего значения в pg_settings: например, файл конфигурации, значение по умолчанию или настройка сессии.",
    ),
    "pending_restart": (
        "Pending Restart / Требуется перезапуск",
        "Да — изменение из файла конфигурации ожидает перезапуска PostgreSQL для применения. Нет — ожидающее перезапуска изменение не отмечено; это не означает, что параметр можно менять без перезапуска.",
    ),
    "version": ("Version / Версия PostgreSQL", "Версия подключённого сервера PostgreSQL."),
    "database_size": (
        "Database Size / Размер БД",
        "Общий размер подключённой базы данных, полученный через pg_database_size; это не объём данных одного SQL.",
    ),
    "collected_at": (
        "Collected At / Время сбора",
        "Время завершения сбора показателей. Часовой пояс указан в исходном значении.",
    ),
}
STATISTICS = {
    "numbackends": (
        "Текущие соединения",
        "Число серверных процессов, подключённых к этой базе в момент сбора; это не накопительный счётчик.",
    ),
    "xact_commit": (
        "Завершённые транзакции",
        "Накопленное число зафиксированных транзакций этой БД с момента сброса статистики.",
    ),
    "xact_rollback": (
        "Отменённые транзакции",
        "Накопленное число отменённых транзакций этой БД с момента сброса статистики.",
    ),
    "blks_read": (
        "Прочитанные блоки",
        "Накопленное число прочитанных блоков БД. Чтение может обслуживаться кэшем ОС; это не число физических обращений к диску одного SQL.",
    ),
    "blks_hit": (
        "Попадания в буферный кэш",
        "Накопленное число попаданий в буферный кэш PostgreSQL для этой БД; кэш ОС сюда не входит.",
    ),
    "temp_files": (
        "Временные файлы",
        "Число созданных временных файлов всех запросов этой базы с момента сброса статистики.",
    ),
    "temp_bytes": (
        "Объём временных файлов, байт",
        "Накопленный объём записи во временные файлы всех запросов БД. Сам по себе не устанавливает причину замедления конкретного SQL.",
    ),
    "deadlocks": (
        "Взаимоблокировки",
        "Число обнаруженных взаимоблокировок в этой БД с момента сброса статистики.",
    ),
    "stats_reset": (
        "Сброс статистики",
        "Время последнего сброса накопленной статистики этой базы; задаёт начало интервала для счётчиков.",
    ),
}


class DatabaseReportView(AnalysisReportView):
    def show_report(self, report):
        self.clear()
        self._text = format_report(report)
        context = self.section("Контекст сбора")
        context.add_help(
            "О собранных фактах",
            "SQL пользователя не выполнялся. Настройки диагностической сессии могут отличаться от сессии приложения. По одним настройкам причина медленного SQL не устанавливается.",
        )
        context.add_text(
            "SQL пользователя не выполнялся. Показаны факты выбранного сбора PostgreSQL."
        )
        for name, error in report.get("errors", {}).items():
            title = "PostgreSQL" if name == "postgres" else name
            self.warning(f"{title}: сбор не завершён ({error}). Проверьте подключение и права.")
            detail = report.get("error_details", {}).get(name)
            if detail:
                context.add_text(detail)
        db = report.get("postgres")
        if not db:
            context.add_text(
                "Данные PostgreSQL не получены."
                if "postgres" in report.get("errors", {})
                else "Сбор PostgreSQL выключен или не вернул данных."
            )
            return
        facts = {}
        if db.get("metadata_collected", True):
            facts.update(
                version=db.get("version") or None, database_size=size(db.get("database_size_bytes"))
            )
        if db.get("collected_at"):
            facts["collected_at"] = db["collected_at"]
        add_metrics(context, facts, definitions=FACT_FIELDS)
        if db.get("metadata_scope") == "query":
            snapshot = ReportSection("SQL / Запрос для сбора структуры", expanded=False)
            snapshot.add_help(
                "Область сбора",
                "Это снимок SQL на момент запуска. Структура ограничена связанными объектами; размер и накопленная статистика относятся ко всей БД.",
            )
            snapshot.content.addWidget(ReportSql(db.get("metadata_sql") or "Нет данных"))
            context.content.addWidget(snapshot)
        if db.get("configuration_collected", True):
            for row in db.get("settings", []):
                section = self.section(row["name"], expanded=False)
                section.add_help(
                    row["name"],
                    SETTINGS.get(
                        row["name"], "Параметр диагностической сессии PostgreSQL из pg_settings."
                    ),
                )
                add_metrics(
                    section,
                    {
                        "value": setting_value(row),
                        "source": row.get("source"),
                        "pending_restart": row.get("pending_restart"),
                    },
                    definitions=FACT_FIELDS,
                )
            unavailable = db.get("settings_unavailable", [])
            if unavailable:
                section = self.section("Недоступные настройки", expanded=False)
                section.add_help(
                    "Почему нет значения",
                    "Настройка не получена из pg_settings. Возможны отличия версии PostgreSQL или ограничения доступа; это не нулевое значение.",
                )
                for name in unavailable:
                    child = ReportSection(name, expanded=False)
                    child.add_help(name, SETTINGS.get(name, "Настройка PostgreSQL."))
                    child.add_text("Нет данных.")
                    section.content.addWidget(child)
        else:
            context.add_text("Сбор конфигурации PostgreSQL выключен.")
        if db.get("metadata_collected", True):
            self.show_statistics(db)
            metadata = self.section("Метаданные", expanded=False)
            metadata.add_help(
                "О метаданных",
                "Таблицы и другие объекты выбранной области сбора. Внутри каждого объекта показаны независимые источники данных, свойства, индексы, ограничения и статистика.",
            )
            for table in db.get("tables", []):
                show_relation(metadata, table)
            if not db.get("tables"):
                metadata.add_text("Объекты в выбранной области сбора не найдены.")
        else:
            context.add_text("Сбор структуры и статистики БД выключен.")

    def show_statistics(self, db):
        section = self.section("Накопленная статистика БД", expanded=False)
        section.add_help(
            "О статистике БД",
            "Источник: pg_stat_database. Это статистика всей базы, а не показатели одного SQL. Накопительные счётчики отсчитываются от stats_reset; numbackends показывает текущие соединения.",
        )
        data = db.get("database_statistics")
        if data is None:
            section.add_text("Статистика БД недоступна.")
            return
        if not data:
            section.add_text("Источник не вернул показателей статистики БД.")
        for key, value in data.items():
            title, description = STATISTICS.get(
                key, ("Показатель БД", "Показатель из pg_stat_database.")
            )
            child = ReportSection(f"{key} / {title}", expanded=False)
            child.add_help(f"{key} / {title}", description)
            add_metrics(child, {"value": value}, definitions=FACT_FIELDS)
            section.content.addWidget(child)
