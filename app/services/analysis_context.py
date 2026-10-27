"""Сбор диагностического контекста одного запроса."""

import json
from datetime import date, datetime, timezone
from decimal import Decimal

from app.collectors.configuration import collect_configuration
from app.collectors.metadata import collect_metadata


def json_value(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"Unsupported diagnostic value: {type(value).__name__}")


def collect_analysis_context(connection, sql, plan_features):
    relations = sorted(
        {
            (node["schema"], node["relation"])
            for node in plan_features["nodes"]
            if isinstance(node.get("schema"), str)
            and node["schema"]
            and isinstance(node.get("relation"), str)
            and node["relation"]
        }
    )
    sources = {}
    collectors = (
        (
            "metadata",
            lambda: collect_metadata(
                connection, scope="query", sql=sql, additional_relations=relations
            ),
        ),
        ("configuration", lambda: collect_configuration(connection)),
    )
    for name, collect in collectors:
        started = datetime.now(timezone.utc).isoformat()
        try:
            with connection.transaction():
                data = collect()
                json.dumps(data, default=json_value)
            entry = {"status": "ok", "data": data, "error": None}
        except Exception as error:
            entry = {"status": "error", "data": None, "error": type(error).__name__}
        entry.update(started_at=started, finished_at=datetime.now(timezone.utc).isoformat())
        sources[name] = entry
    return sources
