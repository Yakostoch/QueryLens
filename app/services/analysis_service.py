import json

from app.collectors.explain import collect_explain
from app.services.database_service import DatabaseService


class AnalysisService:
    """Получение плана SQL в вызывающем рабочем потоке."""

    def __init__(self, database_service=None):
        self.database_service = database_service if database_service is not None else DatabaseService()

    def analyze(self, sql, parameters, context="Запрос"):
        if not sql.strip():
            raise ValueError("Нет запроса для анализа.")
        with self.database_service.connect(parameters) as connection:
            result = collect_explain(connection, sql)
        plan = json.dumps(result["plan"], ensure_ascii=False, indent=2)
        return f"{context} · План PostgreSQL (EXPLAIN)\n\nSQL:\n{sql}\n\n{plan}"
