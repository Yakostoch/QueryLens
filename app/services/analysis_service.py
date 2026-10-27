"""Анализ SQL, плана и метаданных связанных объектов."""

import json
from datetime import datetime, timezone
from uuid import uuid4

from app.analyzers.access_analysis import analyze_access
from app.analyzers.diagnostic_router import route_candidates
from app.analyzers.diagnostic_tree import build_diagnostic_tree, format_diagnostic_tree
from app.analyzers.operation_analysis import analyze_operations
from app.analyzers.plan_features import extract_plan_features
from app.analyzers.predicate_analysis import analyze_predicates
from app.analyzers.runtime_analysis import analyze_runtime
from app.analyzers.sql_features import extract_sql_features
from app.analyzers.volume_analysis import analyze_volume
from app.collectors.explain import collect_explain
from app.services.analysis_context import collect_analysis_context, json_value
from app.services.database_service import DatabaseService


class AnalysisService:
    def __init__(self, database_service=None):
        self.database_service = (
            database_service if database_service is not None else DatabaseService()
        )

    def analyze(
        self,
        sql,
        parameters,
        context="Запрос",
        *,
        consumer_context=None,
        analyze=False,
        timeout_ms=5000,
        cancellation=None,
    ):
        syntax = extract_sql_features(sql)
        # Сохраняем требования к результату, повлиявшие на рекомендацию.
        raw_consumer = consumer_context if isinstance(consumer_context, dict) else {}
        consumer_context = {}
        if isinstance(raw_consumer.get("all_output_columns_required"), bool):
            consumer_context["all_output_columns_required"] = raw_consumer[
                "all_output_columns_required"
            ]
        if isinstance(raw_consumer.get("required_columns"), list) and all(
            isinstance(c, str) for c in raw_consumer["required_columns"]
        ):
            consumer_context["required_columns"] = list(raw_consumer["required_columns"])
        if raw_consumer.get("count_usage") in ("exact_count", "existence_only"):
            consumer_context["count_usage"] = raw_consumer["count_usage"]
        started = datetime.now(timezone.utc).isoformat()
        if cancellation:
            cancellation.check()
        with self.database_service.connect(parameters) as connection:
            try:
                if cancellation:
                    cancellation.bind(connection)
                explain = collect_explain(connection, sql, analyze=analyze, timeout_ms=timeout_ms)
                if cancellation:
                    cancellation.check()
                plan = extract_plan_features(explain["plan"])
                sources = collect_analysis_context(connection, sql, plan)
                if cancellation:
                    cancellation.check()
            finally:
                if cancellation:
                    cancellation.unbind()
        runtime = analyze_runtime(plan, measured=analyze)
        triage = route_candidates(syntax)
        access = analyze_access(sql, syntax, plan, sources)
        volume = analyze_volume(sql, syntax, plan, sources, consumer_context)
        predicates = analyze_predicates(sql, syntax, plan, sources)
        operations = analyze_operations(sql, syntax, plan, sources)
        payload = {
            "schema_version": 2,
            "analysis_id": uuid4().hex,
            "started_at": started,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "mode": "explain_analyze" if analyze else "explain_without_analyze",
            "sql": sql,
            "consumer_context": consumer_context,
            "runtime_analysis": runtime,
            "sql_features": syntax,
            "plan_summary": plan["summary"],
            "plan_features": plan,
            "explain": explain,
            "sources": sources,
            "triage": triage,
            "access_analysis": access,
            "volume_analysis": volume,
            "predicate_analysis": predicates,
            "operation_analysis": operations,
        }
        payload["diagnostic_tree"] = build_diagnostic_tree(payload)
        mode_label = "EXPLAIN ANALYZE" if analyze else "EXPLAIN"
        mode_message = (
            "Измеренный план ANALYZE: SELECT выполнен. Время относится к серверному выполнению под EXPLAIN."
            if analyze
            else "Оценочный план без ANALYZE. Фактическое время выполнения не измерялось."
        )
        lines = [
            f"{context} · План PostgreSQL ({mode_label}) и диагностический контекст",
            "",
            mode_message,
            "Найденные конструкции — кандидаты для проверки, а не доказанные причины замедления.",
            "",
            "SQL:",
            sql,
            "",
            "СВОДКА ПЛАНА",
        ]
        for key, label in (
            ("node_type", "Корневой узел"),
            ("total_cost", "Оценочная стоимость"),
            ("plan_rows", "Оценка выходных строк"),
            ("plan_width", "Ширина строки, байт"),
            ("estimated_bytes", "Оценка объёма выхода, байт"),
        ):
            value = plan["summary"][key]
            lines.append(f"{label}: {value if value is not None else 'неизвестно'}")
        lines += [""] + format_diagnostic_tree(payload["diagnostic_tree"])
        if analyze:
            lines += [
                "",
                "ИЗМЕРЕНИЯ ВЫПОЛНЕНИЯ",
                f"Execution Time, мс: {runtime['execution_time_ms']}",
                f"Planning Time, мс: {runtime['planning_time_ms']}",
            ]
            for node in runtime["node_observations"]:
                lines.append(
                    f"  {node['node_type']} · {node['path']}: Actual Rows/цикл {node['actual_rows_per_loop']}; loops {node['actual_loops']}; приблизительный суммарный выход {node['approximate_total_output_rows']}"
                )
                if node["runtime_counters"]:
                    lines.append(
                        "    Счётчики узла с дочерними операциями: " + str(node["runtime_counters"])
                    )
                if node["disk_sort_observed"]:
                    lines.append(
                        f"    Измерена сортировка с диском: {node['sort_method']}; объём {node['sort_space_used_kb']} kB."
                    )
                if node["worker_disk_sort_observed"]:
                    lines.append(
                        "    У одного из параллельных работников измерена сортировка с диском; подробности сохранены отдельно."
                    )
                if node["temporary_io_observed"]:
                    lines.append(
                        "    Измерены обращения к временным блокам; источник затрат требует сопоставления."
                    )
                if node["multi_batch_hash_observed"]:
                    lines.append(
                        "    Хеширование использовало несколько batches; проверьте временный I/O и память операции."
                    )
                if node["workers_planned"] is not None:
                    lines.append(
                        f"    Работников запланировано: {node['workers_planned']}; запущено: {node['workers_launched']}"
                    )
            lines.extend("  " + message for message in runtime["limitations"])
        lines += ["", "КОНСТРУКЦИИ ДЛЯ ПРОВЕРКИ"]
        for finding in triage["findings"]:
            if finding["status"] == "candidate":
                lines.append(
                    f"{finding['rule_id']} · {finding['name']}: {len(finding['syntax_matches'])}."
                )
                for match in finding["syntax_matches"]:
                    lines.append(f"  {match['scope_id']}: {match['expression']}")
        for scope in syntax["source_connection_graph"]["scopes"]:
            label = {
                "connected": "связи между источниками найдены; признака независимых групп нет",
                "disconnected": "найдены независимые группы источников; проверьте, нужны ли все сочетания",
                "unknown": "для проверки связей между источниками недостаточно данных",
            }[scope["status"]]
            lines.append(f"Связи источников ({scope['scope_id']}): {label}.")
        patterns = [
            item
            for item in syntax["features"]["pattern_predicates"]
            if item["clause"] in {"where", "on"}
        ]
        leading = sum(item["has_leading_wildcard"] is True for item in patterns)
        unknown = sum(item["has_leading_wildcard"] is None for item in patterns)
        lines += [
            f"LIKE/ILIKE с начальным %: {leading}; неизвестный шаблон/ESCAPE: {unknown}.",
            "N+1: недостаточно данных — требуется трасса операции приложения.",
            "",
            "ПРОВЕРКА AP02/AP03 ПО ПЛАНУ",
        ]
        risk_labels = {
            "predicate_index_access_observed": "Условие найдено в Index Cond; индексный доступ для него наблюдается.",
            "small_relation_context": "Оценка таблицы мала; оснований рекомендовать новый индекс без измерений недостаточно.",
            "filter_work_requires_measurement": "Условие найдено в фильтрации; затраты требуют измерения.",
        }
        reason_labels = {
            "metadata_unavailable": "метаданные недоступны",
            "complex_query_scope": "несколько областей SELECT требуют отдельного сопоставления",
            "cte_or_join_scope": "CTE пока требуют отдельной проверки",
            "indirect_source": "источник не является прямой ссылкой на таблицу",
            "catalog_relation_ambiguous_or_unavailable": "таблица не определена однозначно",
            "view_partition_or_unknown_relation": "представление, секционированная таблица или неизвестный тип источника",
            "scan_mapping_ambiguous_or_optimized_away": "узел доступа не найден однозначно",
            "pattern_or_escape_unknown": "неизвестен шаблон или символ экранирования",
            "predicate_context_not_supported_or_ambiguous": "контекст условия пока не поддержан или неоднозначен",
            "column_source_ambiguous": "столбец не связан с единственным источником",
            "expression_spans_sources": "выражение зависит от нескольких источников",
            "column_not_on_supported_search_side": "нужна отдельная проверка сторон сравнения",
            "predicate_not_structurally_matched": "не найдено достоверного соответствия условию плана",
            "expression_or_type_not_supported": "выражение или тип требует отдельной проверки",
            "negated_search_not_supported": "поиск с отрицанием требует отдельной проверки",
        }
        index_labels = {
            "observed_predicate_access": "используется для этого условия",
            "requires_planner_check": "ключ совпадает; применимость ещё требует проверки",
            "unusable_or_unknown_flags": "индекс недоступен для использования либо его состояние неизвестно",
        }
        for finding in access["findings"]:
            lines.append(f"{finding['rule_id']}: {finding['expression']}")
            if finding["binding_status"] != "matched":
                reason = reason_labels.get(finding["reason"], "недостаточно данных")
                lines.append(f"  Автоматическое сопоставление не установлено: {reason}.")
                continue
            lines.append("  " + risk_labels[finding["risk_assessment"]])
            for evidence in finding["plan_evidence"]:
                lines.append(
                    f"  {evidence['path']} · {evidence['node_type']} · {evidence['role']}: {evidence['condition']}"
                )
            for candidate in finding["index_candidates"]:
                lines.append(
                    f"  Индекс {candidate['index_name']}: {index_labels[candidate['status']]}."
                )
            if finding["base_column_indexes"]:
                names = ", ".join(sorted({i["index_name"] for i in finding["base_column_indexes"]}))
                lines.append(
                    f"  Индексы по исходным столбцам: {names}. Это не доказывает применимость к выражению."
                )
            lines.extend("  " + recommendation for recommendation in finding["recommendations"])
        if not access["findings"]:
            lines.append("Конструкций AP02/AP03 для этой проверки не найдено.")
        lines += ["", "ПРОВЕРКА ОБЪЁМА И ЛИШНЕЙ РАБОТЫ"]
        volume_labels = {
            "count_argument_requires_review": "COUNT(выражение) не учитывает NULL; готовая замена на EXISTS не формируется.",
            "consumer_requirements_unknown": "Неизвестно, какие поля нужны потребителю.",
            "all_columns_required": "По явно заданному контракту нужны все выбранные поля.",
            "potential_excess_output": "Часть полей не требуется по явно заданному контракту.",
            "nested_projection_requires_plan_mapping": "Вложенная звёздочка требует проверки фактического выхода после оптимизации.",
            "no_input_required_for_limit_zero": "LIMIT 0: получение строк входного потока не требуется.",
            "no_rows_skipped": "OFFSET 0 не задаёт пропускания строк.",
            "offset_work_present": "Задан пропуск строк результирующего потока; медленная работа не подтверждена.",
            "insufficient_data": "Недостаточно данных для вывода.",
            "exact_count_required": "По контракту нужен точный подсчёт; COUNT сохраняется.",
            "quantity_threshold_required": "Проверяется порог количества, а не простое существование; COUNT сохраняется.",
            "count_purpose_unknown": "Назначение числового результата COUNT неизвестно.",
            "consumer_contract_change_required": "Замена числа на boolean потребует изменения контракта результата.",
            "aggregate_semantics_requires_review": "Группировка или другие условия требуют отдельной проверки смысла.",
            "nested_count_requires_plan_mapping": "Вложенный COUNT не сопоставлен с конкретным агрегатным узлом.",
            "existence_rewrite_requires_more_semantics": "Булева проверка найдена; для переписывания нужны дополнительные сведения.",
            "boolean_existence_candidate": "Найдена булева проверка COUNT; подготовлен кандидат с EXISTS для проверки.",
        }
        for finding in volume["findings"]:
            lines.append(f"{finding['rule_id']}: {finding['expression']}")
            lines.append("  " + volume_labels[finding["status"]])
            if finding.get("expanded_columns_count") is not None:
                lines.append(
                    f"  Звёздочка раскрывается в {finding['expanded_columns_count']} столбцов."
                )
            if finding.get("count_input_rows") is not None:
                lines.append(
                    f"  Оценка выхода входного узла COUNT: {finding['count_input_rows']}; это не измеренное чтение."
                )
            lines.extend("  " + text for text in finding["evidence"] + finding["recommendations"])
            if finding.get("candidate_sql"):
                lines += [
                    "  SQL-кандидат для проверки, без автоматического выполнения:",
                    finding["candidate_sql"],
                ]
        if not volume["findings"]:
            lines.append("Конструкций AP01/AP08/AP11 для этой проверки не найдено.")
        lines += ["", "ПРОВЕРКА OR И NOT IN"]
        predicate_labels = {
            "insufficient_data": "Недостаточно данных для проверки этой конструкции.",
            "bitmap_or_group_observed": "Индексные ветви группы сопоставлены с BitmapOr.",
            "or_group_filter_observed": "Группа найдена в условии плана; затраты требуют измерения.",
            "or_group_plan_mapping_unknown": "Связь группы OR с планом пока не установлена.",
            "null_semantics_requires_review": "Отсутствие NULL не доказано; автоматической замены NOT IN нет.",
            "nonnull_keys_established": "Ненулевые ключи установлены, но переписывание требует дополнительных проверок.",
            "not_exists_candidate": "Для простого случая с ненулевыми ключами подготовлен кандидат NOT EXISTS.",
        }
        for finding in predicates["findings"]:
            lines += [
                f"{finding['rule_id']}: {finding['expression']}",
                "  " + predicate_labels[finding["status"]],
            ]
            if finding["rule_id"] == "AP04":
                lines.append(
                    f"  Альтернатив: {finding['or_branch_count']}; количество не является порогом проблемы."
                )
                for branch in finding["branches"]:
                    lines.append("  Ветвь: " + branch["expression"])
                    for item in branch["index_evidence"]:
                        lines.append(f"    Index Cond: {item['index_name']} · {item['path']}")
                if finding["in_expression_candidate"]:
                    lines.append(
                        "  Вариант записи для проверки типов: " + finding["in_expression_candidate"]
                    )
            else:
                null_labels = {
                    "not_null": "отсутствие NULL установлено",
                    "nullable_or_unproven": "NULL допускается либо ограничения не доказаны",
                    "unknown": "неизвестно",
                }
                lines.append("  Внешнее выражение: " + null_labels[finding["outer_nullability"]])
                lines.append("  Выражение подзапроса: " + null_labels[finding["inner_nullability"]])
                lines.append("  Реализация внутреннего результата: " + finding["subplan_kind"])
            lines.extend(
                "  " + message for message in finding["evidence"] + finding["recommendations"]
            )
            if finding["candidate_sql"]:
                lines += [
                    "  SQL-кандидат для проверки, без автоматического выполнения:",
                    finding["candidate_sql"],
                ]
        if not predicates["findings"]:
            lines.append("Конструкций AP04/AP05 для этой проверки не найдено.")
        lines += ["", "ПРОВЕРКА DISTINCT, СОРТИРОВОК, HAVING, CTE, ОКОН И JOIN"]
        operation_labels = {
            "insufficient_data": "Недостаточно данных для сопоставления области с планом.",
            "dedup_plan_observed": "Найден узел устранения повторов; необходимость DISTINCT требует проверки.",
            "dedup_node_not_observed": "Явный узел устранения повторов не найден.",
            "projection_unique_by_primary_key": "Проекция одной таблицы содержит подтверждённый первичный ключ.",
            "distinct_on_requires_order_semantics": "DISTINCT ON требует сохранения правил выбора строки и порядка.",
            "sort_input_observed": "Найдена сортировка и её оценочный вход.",
            "no_explicit_sort": "Отдельного узла сортировки не найдено.",
            "sort_purpose_mapping_unknown": "Есть сортировки, но их связь с ORDER BY неоднозначна.",
            "having_predicates_checked": "Проверено положение поддержанных условий HAVING.",
            "cte_producer_observed": "По имени сопоставлен производитель CTE.",
            "cte_scan_observed": "Найдено чтение CTE; производитель однозначно не установлен.",
            "no_explicit_cte_nodes": "Явных узлов CTE не найдено.",
            "recursive_cte_requires_separate_analysis": "Рекурсивный CTE требует отдельного анализа.",
            "window_pass_observed": "Найден единственный проход WindowAgg и его вход.",
            "window_pass_mapping_unknown": "Связь оконных спецификаций с проходами пока неоднозначна.",
            "join_plan_observed": "Найдены физические узлы соединения; алгоритм сам по себе не является проблемой.",
            "join_node_not_observed": "Явного физического узла JOIN не найдено.",
        }
        for finding in operations["findings"]:
            lines += [
                f"{finding['rule_id']}: {finding['expression']}",
                "  " + operation_labels[finding["status"]],
            ]
            for node in finding["plan_nodes"]:
                lines.append(
                    f"  Узел {node['node_type']} · {node['path']}; оценка выхода: {node['plan_rows']}"
                )
                for child in node["input_nodes"]:
                    lines.append(
                        f"    Вход: {child['node_type']}; строки: {child['plan_rows']}; полезный объём, байт: {child['estimated_bytes']} (не расход памяти)"
                    )
            if finding.get("order_by_keys"):
                lines.append("  Требуемый порядок: " + ", ".join(finding["order_by_keys"]))
            if finding.get("window_specs"):
                lines.extend("  Окно: " + spec["expression"] for spec in finding["window_specs"])
                lines.extend(
                    "  Именованная спецификация: " + spec
                    for spec in finding.get("named_window_definitions", [])
                )
            if finding.get("cte_name"):
                refs = finding["cte_reference_count"]
                lines.append(
                    f"  CTE: {finding['cte_name']}; синтаксических потребителей: {refs if refs is not None else 'неизвестно'}"
                )
            for predicate in finding.get("predicates", []):
                locations = {
                    "before_aggregation": "до агрегации",
                    "after_aggregation": "после агрегации",
                    "both": "до и после агрегации",
                    "unknown": "неизвестно",
                }
                lines.append(
                    "  HAVING: "
                    + predicate["expression"]
                    + "; положение: "
                    + locations[predicate["predicate_plan_location"]]
                )
            if finding.get("producer_node"):
                lines.append(
                    f"  Оценка выхода производителя CTE: {finding['producer_node']['plan_rows']}"
                )
            for node in finding.get("cte_scan_nodes", []):
                lines.append(f"  CTE Scan: {node['path']}; условия: {node['conditions']}")
            for node in finding.get("join_nodes", []):
                lines.append(
                    f"  {node['node_type']} / {node['join_type']} · {node['path']}; оценочная кратность: {node['estimated_fanout']}"
                )
                for index in node["inner_index_conditions"]:
                    lines.append(
                        f"    Внутренний индекс: {index['index_name']}; Index Cond: {index['condition']}"
                    )
            lines.extend(
                "  " + message for message in finding["evidence"] + finding["recommendations"]
            )
        if not operations["findings"]:
            lines.append("Конструкций для этой проверки не найдено.")
        lines += ["", "СОБРАННЫЙ КОНТЕКСТ"]
        metadata = sources["metadata"]
        if metadata["status"] == "ok":
            for table in metadata["data"]["tables"]:
                origins = ", ".join(table.get("metadata_origins", []))
                indexes = table["diagnostics"]["sources"]["indexes"]
                count = str(len(indexes["data"])) if indexes["status"] == "ok" else "неизвестно"
                lines.append(
                    f"{table['schema']}.{table['name']} · источник: {origins} · индексов: {count}"
                )
            for error in metadata["data"].get("additional_relation_errors", []):
                lines.append(
                    f"Источник плана {error['schema']}.{error['name']}: метаданные недоступны."
                )
        else:
            lines.append(
                f"Метаданные запроса недоступны ({metadata['error']}); отсутствие индексов не установлено."
            )
        configuration = sources["configuration"]
        lines.append(
            "Настройки диагностической сессии: "
            + (
                "получены."
                if configuration["status"] == "ok"
                else f"недоступны ({configuration['error']})."
            )
        )
        lines += [
            "Ресурсы ОС показаны отдельно на вкладке «Ресурсы»; с этим запуском пока не синхронизированы.",
            "",
            "ДИАГНОСТИЧЕСКИЕ ДАННЫЕ В JSON",
            json.dumps(payload, ensure_ascii=False, indent=2, default=json_value),
        ]
        return "\n".join(lines)
