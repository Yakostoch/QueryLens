"""Выбор проверок по конструкциям SQL."""

# Идентификаторы проверок постоянны; группы могут пересекаться.
REGISTRY = (
    (1, "volume", "select_stars", "SELECT *", "required_columns"),
    (2, "access", "where_expressions", "Выражение над столбцом WHERE", "predicate_to_plan_binding"),
    (3, "access", "pattern_predicates", "Начальный % в LIKE/ILIKE", "index_applicability"),
    (4, "access", "or_groups", "Условия OR", "predicate_to_plan_binding"),
    (5, "subqueries", "not_in_subqueries", "NOT IN с подзапросом", "nullability_and_plan"),
    (6, "multiplicity", "distinct", "DISTINCT", "uniqueness_and_required_semantics"),
    (7, "sorting", "orders", "Сортировка", "sort_node_binding"),
    (8, "volume", "offsets", "OFFSET", "pagination_contract"),
    (9, "sorting", "having", "HAVING", "aggregation_semantics_and_pushdown"),
    (10, "application", None, "N+1", "application_operation_trace"),
    (11, "volume", "counts", "COUNT для существования", "consumer_needs_boolean"),
    (12, "multiplicity", "joins", "Декартово произведение", "join_keys_and_expected_cardinality"),
    (13, "subqueries", "ctes", "CTE", "cte_plan_binding"),
    (14, "sorting", "windows", "Оконные функции", "window_plan_binding"),
    (15, "multiplicity", "joins", "JOIN: проверка эффективности", "join_plan_binding"),
)


def route_candidates(sql_features):
    """Возвращает найденные конструкции и недостающие данные."""
    features = sql_features["features"]
    findings = []
    for number, group, feature, name, required in REGISTRY:
        matches = features.get(feature, []) if feature else []
        unknown_pattern = False
        unknown_graph = False
        if number == 3:
            matches = [x for x in matches if x.get("clause") in {"where", "on"}]
            unknown_pattern = any(x["has_leading_wildcard"] is None for x in matches)
            matches = [x for x in matches if x["has_leading_wildcard"] is True]
        if number == 4:
            matches = [x for x in matches if x.get("clause") in {"where", "on"}]
        if number == 7:
            matches = [x for x in matches if x["purpose"] == "query"]
        if number == 11:
            matches = [x for x in matches if x.get("count_scope") != "window"]
        if number == 8:
            matches = [x for x in matches if x["offset_value"] is None or x["offset_value"] > 0]
        if number == 12:
            scopes = sql_features.get("source_connection_graph", {}).get("scopes", [])
            disconnected = {x["scope_id"] for x in scopes if x["status"] == "disconnected"}
            unknown_graph = any(x["status"] == "unknown" for x in scopes)
            matches = [x for x in matches if x["scope_id"] in disconnected]
        if number == 10 or (unknown_pattern or unknown_graph) and not matches:
            status = "insufficient_data"
        elif matches:
            status = "candidate"
        else:
            status = "no_syntax_candidate"
        findings.append(
            {
                "rule_id": f"AP{number:02}",
                "group": group,
                "name": name,
                "status": status,
                "syntax_matches": matches,
                "cause_assessment": "not_evaluated",
                "missing_evidence": [required] if status != "no_syntax_candidate" else [],
                "recommendation_status": "not_generated",
            }
        )
    return {
        "schema_version": 1,
        "mode": "syntax_triage",
        "findings": findings,
        "limitations": [
            "Маршрутизация по синтаксису; применимость индексов, связь с планом и причина пока не установлены.",
            "no_syntax_candidate не доказывает отсутствие проблемы; поддерживаемый синтаксис ограничен.",
        ],
    }
