"""Объединение результатов 15 проверок и порядок их рассмотрения."""

from app.analyzers.diagnostic_router import REGISTRY

GROUPS = (
    ("volume", "Объём и лишняя работа"),
    ("access", "Доступ к данным"),
    ("multiplicity", "Кратность и соединения"),
    ("sorting", "Сортировка, группы и окна"),
    ("subqueries", "Подзапросы и промежуточные результаты"),
    ("application", "Обращения приложения"),
)
REQUIREMENTS = {
    "AP01": "требования потребителя к нужным столбцам",
    "AP02": "сопоставление выражения с условием доступа и метаданные индексов",
    "AP03": "известный шаблон поиска, его положение в плане и подходящие индексы",
    "AP04": "положение всей группы OR и доступ для каждой ветви",
    "AP05": "семантика NULL, типы ключей и реализация подзапроса",
    "AP06": "уникальность всей проекции и необходимость устранения повторов",
    "AP07": "назначение сортировки, её вход и требуемый порядок",
    "AP08": "размер страницы, смещение и требования к навигации",
    "AP09": "ключи группировки, зависимости от агрегатов и положение фильтра",
    "AP10": "трасса запросов одной операции приложения",
    "AP11": "назначение результата подсчёта и семантика агрегата",
    "AP12": "требуемые связи и кратность результата",
    "AP13": "производитель CTE, все потребители и свойства выражений",
    "AP14": "область окна, рамка и соответствующий проход плана",
    "AP15": "оба входа соединения, условия и ожидаемая кратность",
}
STATE_LABELS = {
    "not_detected": "Конструкция не обнаружена в поддержанном синтаксисе",
    "needs_evidence": "Недостаточно данных для вывода",
    "review_candidate": "Есть гипотеза для проверки",
    "optimization_candidate": "Есть обоснованный кандидат для проверки изменения",
    "semantic_review": "Сначала проверьте смысл и ограничения результата",
    "access_observed": "Наблюдается поддержанный доступ или требуемое поведение",
    "mixed_evidence": "Разные вхождения имеют разные результаты проверки",
    "measured_observation": "Измеренный факт этого запуска",
}
CANDIDATES = {
    "projection_unique_by_primary_key",
    "potential_excess_output",
    "boolean_existence_candidate",
    "not_exists_candidate",
}
REVIEW = {
    "offset_work_present",
    "filter_work_requires_measurement",
    "or_group_filter_observed",
    "dedup_plan_observed",
    "sort_input_observed",
    "cte_producer_observed",
    "cte_scan_observed",
    "window_pass_observed",
    "join_plan_observed",
}
SEMANTIC = {
    "null_semantics_requires_review",
    "distinct_on_requires_order_semantics",
    "aggregate_semantics_requires_review",
    "consumer_contract_change_required",
    "existence_rewrite_requires_more_semantics",
}
OBSERVED = {
    "all_columns_required",
    "no_rows_skipped",
    "no_input_required_for_limit_zero",
    "exact_count_required",
    "quantity_threshold_required",
    "predicate_index_access_observed",
    "small_relation_context",
    "bitmap_or_group_observed",
    "no_explicit_sort",
}


def _unique(items):
    return list(dict.fromkeys(items))


def _state(finding):
    status = finding.get("status") or finding.get("risk_assessment")
    if status in CANDIDATES:
        return "optimization_candidate", 1
    if status in SEMANTIC:
        return "semantic_review", 1
    if status in OBSERVED:
        return "access_observed", None
    if status == "having_predicates_checked":
        predicates = finding.get("predicates") or []
        if any(
            p.get("safe_to_pushdown") is True
            and p.get("predicate_plan_location") in {"after_aggregation", "both"}
            for p in predicates
        ):
            return "review_candidate", 3
        if predicates and all(
            p.get("predicate_plan_location") == "before_aggregation"
            or p.get("depends_on_aggregate") is True
            for p in predicates
        ):
            return "access_observed", None
        return "needs_evidence", 4
    if status in REVIEW:
        return "review_candidate", 3
    return "needs_evidence", 4


def _evidence_refs(finding, ref):
    refs = []
    for key in (
        "plan_nodes",
        "plan_evidence",
        "predicate_plan_roles",
        "node_metrics",
        "producer_node",
        "cte_scan_nodes",
        "join_nodes",
        "nullability_evidence",
    ):
        if finding.get(key):
            refs.append(ref + "/" + key)
    if finding.get("uniqueness_proven") is True:
        refs.extend([ref + "/uniqueness_proven", ref + "/unique_key"])
    return refs


def _check(finding, ref, number):
    state, priority = _state(finding)
    refs = _evidence_refs(finding, ref)
    catalog = (
        finding.get("uniqueness_proven") is True
        or finding.get("outer_nullability") == "not_null"
        and finding.get("inner_nullability") == "not_null"
        and all(
            p in {"validated_catalog_constraint", "pre18_column_not_null_flag"}
            for p in (finding.get("nullability_evidence") or {}).values()
        )
        and len(finding.get("nullability_evidence") or {}) == 2
    )
    bound = any(
        finding.get(k)
        for k in (
            "plan_nodes",
            "plan_evidence",
            "predicate_plan_roles",
            "node_metrics",
            "producer_node",
            "cte_scan_nodes",
            "join_nodes",
        )
    )
    level = "catalog_supported" if catalog else "plan_bound" if bound else "syntax_or_context"
    return dict(
        id=finding["rule_id"] + ".check" + str(number),
        label=finding.get("predicate_expression")
        or finding.get("expression")
        or finding["rule_id"],
        scope_id=finding.get("scope_id"),
        state=state,
        state_label=STATE_LABELS[state],
        priority=priority,
        evidence_level=level,
        source_ref=ref,
        evidence_refs=refs,
        observed_status=finding.get("status")
        or finding.get("risk_assessment")
        or finding.get("binding_status"),
        evidence=finding.get("evidence") or [],
        next_steps=_unique(
            finding.get("recommendations")
            or (
                [
                    "Для этой области нужны дополнительные сведения: "
                    + REQUIREMENTS.get(finding["rule_id"], "связь с планом")
                    + "."
                ]
                if state == "needs_evidence"
                else []
            )
        ),
        candidate_sql_ref=ref + "/candidate_sql" if finding.get("candidate_sql") else None,
        candidate_requires_review=True,
        cause_assessment="not_established",
    )


def build_diagnostic_tree(payload):
    all_findings = {}
    for block in ("access_analysis", "volume_analysis", "predicate_analysis", "operation_analysis"):
        for i, finding in enumerate(payload.get(block, {}).get("findings", [])):
            if finding.get("rule_id"):
                all_findings.setdefault(finding["rule_id"], []).append(
                    (finding, f"/{block}/findings/{i}")
                )
    triage = {
        f["rule_id"]: (f, f"/triage/findings/{i}")
        for i, f in enumerate(payload.get("triage", {}).get("findings", []))
    }
    groups = {key: dict(id="group." + key, label=label, children=[]) for key, label in GROUPS}
    actions = []
    rules = []
    for number, group, _, label, required in REGISTRY:
        rule = f"AP{number:02}"
        route, route_ref = triage.get(rule, ({}, None))
        checks = [
            _check(finding, ref, i)
            for i, (finding, ref) in enumerate(all_findings.get(rule, []), 1)
        ]
        if checks:
            states = {c["state"] for c in checks}
            state = checks[0]["state"] if len(states) == 1 else "mixed_evidence"
        else:
            state = (
                "not_detected" if route.get("status") == "no_syntax_candidate" else "needs_evidence"
            )
        priorities = [c["priority"] for c in checks if c["priority"] is not None]
        priority = min(priorities) if priorities else (4 if state == "needs_evidence" else None)
        item = dict(
            id=rule,
            rule_id=rule,
            label=label,
            state=state,
            state_label=STATE_LABELS[state],
            priority=priority,
            source_ref=route_ref,
            children=checks,
            cause_assessment="not_established",
            missing_evidence=(
                [required]
                if state == "needs_evidence" or any(c["state"] == "needs_evidence" for c in checks)
                else []
            ),
        )
        if rule == "AP12":
            scopes = (
                payload.get("sql_features", {}).get("source_connection_graph", {}).get("scopes", [])
            )
            graph = [
                dict(
                    scope_id=s.get("scope_id"),
                    status=s.get("status"),
                    source_ref=f"/sql_features/source_connection_graph/scopes/{i}",
                )
                for i, s in enumerate(scopes)
            ]
            item["source_graph_observations"] = graph
            if any(s.get("status") == "disconnected" for s in scopes):
                item.update(
                    state="semantic_review", state_label=STATE_LABELS["semantic_review"], priority=1
                )
                item["missing_evidence"] = ["intended_relationship"]
                actions.append(
                    dict(
                        rule_id=rule,
                        priority=1,
                        text="Проверьте, нужны ли все сочетания независимых групп источников; условие связи без требований не добавляется.",
                        evidence_refs=[
                            g["source_ref"] for g in graph if g["status"] == "disconnected"
                        ],
                    )
                )
        if rule == "AP10":
            item["scope_limitation"] = (
                "Для N+1 нужна трасса одной операции приложения. Частота calls и Actual Loops этого не доказывают."
            )
        for check in checks:
            for text in check["next_steps"]:
                if check["priority"] is not None:
                    actions.append(
                        dict(
                            rule_id=rule,
                            priority=check["priority"],
                            text=text,
                            evidence_refs=[check["source_ref"]] + check["evidence_refs"],
                        )
                    )
        if not checks and route.get("status") == "candidate" and rule != "AP12":
            actions.append(
                dict(
                    rule_id=rule,
                    priority=4,
                    text="Для проверки «"
                    + label
                    + "» нужны дополнительные данные: "
                    + REQUIREMENTS[rule]
                    + ".",
                    evidence_refs=[route_ref] if route_ref else [],
                )
            )
        groups[group]["children"].append(item)
        rules.append(item)
    runtime = payload.get("runtime_analysis") or {}
    facts = []
    # Оценочный план не содержит измерений выполнения.
    measured = (
        payload.get("mode") == "explain_analyze"
        and payload.get("explain", {}).get("analyze") is True
        and runtime.get("status") == "measured"
    )
    if measured:
        for i, observation in sorted(
            enumerate(runtime.get("node_observations", [])),
            key=lambda pair: (pair[1].get("path") or "").count(".Plans["),
        ):
            ref = f"/runtime_analysis/node_observations/{i}"
            kinds = []
            if observation.get("disk_sort_observed") or observation.get(
                "worker_disk_sort_observed"
            ):
                kinds.append(
                    (
                        "disk_sort",
                        "Измерена сортировка с использованием диска",
                        "Проверьте объём входа и нужный порядок; work_mem оценивайте с учётом одновременных операций, без автоматического увеличения.",
                    )
                )
            if observation.get("multi_batch_hash_observed"):
                kinds.append(
                    (
                        "multi_batch_hash",
                        "Наблюдается несколько batches хеширования",
                        "Проверьте временный I/O, объём входа и память операции; несколько batches сами по себе не задают нужный work_mem.",
                    )
                )
            # Показываем счётчик старшего узла: он уже включает дочерние операции.
            path = observation.get("path")
            if observation.get("temporary_io_observed") and not any(
                f["kind"] == "temporary_io"
                and isinstance(path, str)
                and isinstance(f.get("plan_path"), str)
                and path.startswith(f["plan_path"] + ".Plans[")
                for f in facts
            ):
                kinds.append(
                    (
                        "temporary_io",
                        "Измерены обращения к временным блокам",
                        "Найдите операцию, создавшую временные данные; счётчики родителя и потомков не складывайте.",
                    )
                )
            for kind, label, text in kinds:
                fact = dict(
                    id=f"runtime.{i}.{kind}",
                    kind=kind,
                    label=label,
                    state="measured_observation",
                    priority=2,
                    plan_path=path,
                    source_ref=ref,
                    evidence_refs=[ref],
                    next_steps=[text],
                    cause_assessment="not_established",
                )
                facts.append(fact)
                actions.append(
                    dict(
                        rule_id=None,
                        observation_id=fact["id"],
                        priority=2,
                        text=text,
                        evidence_refs=[ref],
                    )
                )
    notes = [
        dict(
            id="workstation_resources",
            status="not_correlated",
            text="Метрики компьютера QueryLens не сопоставлены с этим запуском и не доказывают ограничение ресурсов сервера БД.",
        )
    ]
    sources = payload.get("sources") or {}
    for name in ("metadata", "configuration"):
        source = sources.get(name) or {}
        status = source.get("status", "unknown")
        notes.append(
            dict(
                id=name,
                status=status,
                source_ref="/sources/" + name if name in sources else None,
                text=(
                    "Получен диагностический контекст; настройки не являются диагнозом."
                    if status == "ok"
                    else "Источник недоступен или не собран; отсутствие сведений не означает отсутствие индекса или ограничения."
                ),
            )
        )
    settings = ((sources.get("configuration") or {}).get("data") or {}).get("settings") or []
    memory = [
        dict(
            name=r["name"],
            setting=r.get("setting"),
            unit=r.get("unit"),
            source_ref=f"/sources/configuration/data/settings/{i}",
        )
        for i, r in enumerate(settings)
        if r.get("name") in {"work_mem", "hash_mem_multiplier", "track_io_timing"}
    ]
    for fact in facts:
        fact["related_setting_refs"] = (
            [m["source_ref"] for m in memory if m["name"] in {"work_mem", "hash_mem_multiplier"}]
            if fact["kind"] in {"disk_sort", "multi_batch_hash"}
            else []
        )
    # Объединяем повторяющиеся действия, сохраняя ссылки на факты.
    merged = {}
    for action in actions:
        key = (action.get("rule_id"), action["text"])
        if key in merged:
            merged[key]["evidence_refs"] = _unique(
                merged[key]["evidence_refs"] + action["evidence_refs"]
            )
            merged[key]["priority"] = min(merged[key]["priority"], action["priority"])
        else:
            merged[key] = action
    ordered = sorted(
        merged.values(), key=lambda a: (a["priority"], a.get("rule_id") or "", a["text"])
    )
    for i, a in enumerate(ordered, 1):
        a["id"] = "action." + str(i)
    return dict(
        schema_version=1,
        policy_version="review_order_v1",
        root=dict(
            id="query_diagnostics", label="Дерево проверки запроса", children=list(groups.values())
        ),
        measured_observations=facts,
        context_notes=notes,
        related_settings=memory,
        recommended_checks=ordered,
        execution_time_ms=runtime.get("execution_time_ms") if measured else None,
        measurement_status="measured" if measured else "not_measured",
        cause_assessment="not_established",
        slowdown_assessment="no_explicit_response_time_target",
        summary=dict(
            rules_total=len(rules),
            rules_with_findings=sum(bool(r["children"]) for r in rules),
            rules_with_unknowns=sum(
                r["state"] == "needs_evidence"
                or any(c["state"] == "needs_evidence" for c in r["children"])
                for r in rules
            ),
            measured_observations=len(facts),
            recommended_checks=len(ordered),
        ),
        limitations=[
            "Приоритет 1: смысл и обоснованные кандидаты; 2: измеренные факты; 3: гипотезы плана; 4: сбор данных. Это порядок проверки, не тяжесть проблемы.",
            "Один запрос может иметь несколько ветвей. Несколько признаков не доказывают несколько независимых причин.",
            "Конструкция не обнаружена — не гарантия хорошей производительности. Эффективный доступ тоже не доказывает отсутствие других затрат.",
            "Без целевого времени ответа запрос не классифицируется как медленный только по cost, ширине или одному времени.",
            "SQL, индексы и настройки автоматически не меняются. Нужны сохранение результата и повторные измерения.",
        ],
    )


def format_diagnostic_tree(tree):
    lines = [
        "ДЕРЕВО ДИАГНОСТИКИ И ПОРЯДОК ПРОВЕРКИ",
        "Приоритет — порядок проверки гипотез, а не доказанная причина или тяжесть замедления.",
    ]
    if tree["measurement_status"] == "measured":
        lines.append(
            f"Время этого запуска, мс: {tree['execution_time_ms']}; целевое время ответа не задано."
        )
    else:
        lines.append("Измеренного времени нет; используются оценки плана и доступные метаданные.")
    for group in tree["root"]["children"]:
        active = [r for r in group["children"] if r["state"] != "not_detected"]
        if not active:
            continue
        lines.append(group["label"] + ":")
        for rule in active:
            lines.append(f"  {rule['rule_id']} · {rule['label']}: {rule['state_label']}")
            for check in rule["children"]:
                lines.append("    " + check["label"] + " → " + check["state_label"])
            if rule.get("scope_limitation"):
                lines.append("    " + rule["scope_limitation"])
    for fact in tree["measured_observations"]:
        lines.append("Измеренный факт: " + fact["label"] + " · " + str(fact["plan_path"]))
    if tree["recommended_checks"]:
        lines.append("Что проверить дальше:")
        for action in tree["recommended_checks"]:
            lines.append(
                f"  Приоритет {action['priority']} · {action.get('rule_id') or 'Измерение'}: {action['text']}"
            )
    else:
        lines.append(
            "Конкретных кандидатов изменения по доступным доказательствам не сформировано; это не гарантия отсутствия проблем."
        )
    lines.append(
        "Подробные доказательства и JSON-ссылки сохранены в diagnostic_tree. Причинная связь с замедлением не установлена."
    )
    return lines
