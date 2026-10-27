"""Краткое представление результатов проверок."""

import json
import math

MARKER = "ДИАГНОСТИЧЕСКИЕ ДАННЫЕ В JSON\n"
TITLES = {
    "AP01": "Выбор столбцов SELECT *",
    "AP02": "Вычисление в условии поиска",
    "AP03": "Поиск внутри строки",
    "AP04": "Условие с OR",
    "AP05": "Исключение строк через NOT IN",
    "AP06": "Удаление повторов DISTINCT",
    "AP07": "Сортировка",
    "AP08": "Пропуск строк OFFSET",
    "AP09": "Фильтрация после группировки",
    "AP11": "Подсчёт строк COUNT",
    "AP12": "Связи между таблицами",
    "AP13": "Промежуточный результат WITH",
    "AP14": "Оконные вычисления",
    "AP15": "Соединение таблиц",
}
# Показываем подтверждённый факт и подходящее действие.
MESSAGES = {
    "AP01": (
        "Неизвестно, нужны ли все выбранные столбцы.",
        "Оставьте только нужные поля, если полный набор не требуется.",
    ),
    "AP02": (
        "Выражение проверяется фильтром; его затраты ещё не измерены.",
        "Сравните план и время варианта с подходящим индексом или равнозначным условием.",
    ),
    "AP03": (
        "Поиск с % в начале проверяется фильтром; замедление не подтверждено.",
        "Если поиск подстроки нужен, проверьте применимость триграммного индекса.",
    ),
    "AP04": (
        "Группа OR найдена в условиях плана; дорогая обработка не подтверждена.",
        "Проверьте доступ по каждой ветви и измерьте время запроса.",
    ),
    "AP05": (
        "Замена NOT IN требует проверки NULL и смысла результата.",
        "Уточните допустимость NULL; не заменяйте на NOT EXISTS без проверки результата.",
    ),
    "AP06": (
        "В плане есть обработка повторов; её необходимость пока не доказана.",
        "Проверьте, нужны ли повторы от соединений и требуется ли DISTINCT.",
    ),
    "AP07": (
        "В плане есть отдельная сортировка; сама по себе она не является ошибкой.",
        "Проверьте объём её входа и индекс под требуемый порядок.",
    ),
    "AP08": (
        "Запрос вычисляет и пропускает строки перед нужной страницей.",
        "Для последовательного просмотра рассмотрите переход по последнему ключу страницы.",
    ),
    "AP09": (
        "Часть условий может применяться после группировки.",
        "Переносите в WHERE только условия, сохраняющие результат группировки.",
    ),
    "AP11": (
        "Назначение результата подсчёта требует уточнения.",
        "Если нужно только наличие строки, рассмотрите EXISTS; точный подсчёт сохраните.",
    ),
    "AP12": (
        "Найдены независимые группы таблиц: возможны все сочетания их строк.",
        "Проверьте, нужны ли эти сочетания; добавляйте связь только по смыслу задачи.",
    ),
    "AP13": (
        "В плане выделен промежуточный результат WITH; его польза зависит от потребителей.",
        "Проверьте объём результата и возможность перенести общий фильтр внутрь WITH.",
    ),
    "AP14": (
        "В плане есть оконные вычисления; затраты зависят от входа и порядка.",
        "Проверьте нужный порядок и возможность безопасно уменьшить входные данные.",
    ),
    "AP15": (
        "В плане есть соединение таблиц; выбранный алгоритм сам по себе корректен.",
        "Проверьте количество входных строк, условия связи и доступ по ключам.",
    ),
}
SPECIAL = {
    "count_argument_requires_review": (
        "Недостаточно данных",
        "COUNT(выражение) не считает NULL. Простой EXISTS может вернуть другой ответ.",
        "Проверьте NULL и свойства аргумента COUNT перед заменой; готовый SQL не предлагается.",
    ),
    "consumer_requirements_unknown": (
        "Недостаточно данных",
        "Неизвестно, какие из выбранных столбцов нужны приложению.",
        "Уточните нужные поля; если требуются все столбцы, SELECT * можно сохранить.",
    ),
    "projection_unique_by_primary_key": (
        "Лишняя операция",
        "Выбранные поля включают подтверждённый первичный ключ: строки уже уникальны.",
        "Проверьте вариант без DISTINCT и сравните время.",
    ),
    "potential_excess_output": (
        "Лишние данные",
        "Выбираются столбцы, не нужные по заданным требованиям.",
        "Перечислите только необходимые столбцы.",
    ),
    "boolean_existence_candidate": (
        "Можно упростить",
        "COUNT считает все совпадения, хотя результат нужен только как «есть / нет».",
        "Проверьте вариант с EXISTS, который может остановиться на первом совпадении.",
    ),
    "not_exists_candidate": (
        "Можно проверить замену",
        "Ненулевые ключи позволяют рассмотреть NOT EXISTS для этого простого случая.",
        "Сравните результат, планы и время вариантов NOT IN и NOT EXISTS.",
    ),
    "null_semantics_requires_review": (
        "Проверьте смысл",
        "Отсутствие NULL не доказано. NULL в подзапросе может изменить ожидаемый результат NOT IN.",
        "Уточните обработку NULL перед изменением запроса.",
    ),
    "distinct_on_requires_order_semantics": (
        "Проверьте смысл",
        "DISTINCT ON выбирает одну строку из группы; выбор зависит от порядка.",
        "Проверьте ORDER BY и то, какая строка должна остаться.",
    ),
    "consumer_contract_change_required": (
        "Проверьте требования",
        "Замена числа на «есть / нет» меняет формат результата.",
        "Сначала уточните, как приложение использует подсчёт.",
    ),
    "aggregate_semantics_requires_review": (
        "Проверьте смысл",
        "Группировка или агрегаты требуют отдельной проверки результата.",
        "Не переносите условия и не заменяйте подсчёт без проверки смысла.",
    ),
    "existence_rewrite_requires_more_semantics": (
        "Недостаточно данных",
        "Найдена проверка наличия через COUNT, но безопасная замена пока не установлена.",
        "Уточните особенности выражения и проверьте равнозначность EXISTS.",
    ),
}
RUNTIME = {
    "disk_sort": (
        "Сортировка использовала диск",
        "В этом запуске сортировке понадобилось дисковое пространство.",
        "Проверьте объём входа и порядок. Память операции меняйте с учётом общей нагрузки.",
    ),
    "temporary_io": (
        "Обнаружены временные данные",
        "В этом запуске измерены обращения к временным блокам.",
        "В подробностях найдите операцию, создавшую временные данные.",
    ),
    "multi_batch_hash": (
        "Хеширование в несколько этапов",
        "В этом запуске хеширование выполнялось несколькими пакетами.",
        "Проверьте объём входа, временные данные и доступную память операции.",
    ),
}


def _short(value, limit=110):
    value = " ".join(str(value or "").split())
    return value if len(value) <= limit else value[: limit - 1] + "…"


def resolve(payload, pointer):
    current = payload
    for key in pointer.split("/")[1:]:
        key = key.replace("~1", "/").replace("~0", "~")
        current = current[int(key)] if isinstance(current, list) else current[key]
    return current


def summarize(payload):
    """Возвращает до трёх замечаний; остальные сохраняет в подробном отчёте."""
    tree = payload["diagnostic_tree"]
    items = []
    candidates = []
    for group in tree["root"]["children"]:
        for rule in group["children"]:
            rule_id = rule["rule_id"]
            if rule_id not in TITLES:
                continue  # Для N+1 нужна трасса приложения.
            checks = rule.get("children") or []
            for check in checks:
                if check["state"] in {"not_detected", "access_observed"}:
                    continue
                status = check.get("observed_status")
                source = resolve(payload, check["source_ref"])
                late_cte = (
                    status == "cte_producer_observed"
                    and source.get("cte_kind") == "select"
                    and source.get("cte_materialization_hint") is True
                    and source.get("cte_reference_count") == 1
                    and source.get("producer_mapping_status") == "matched_by_unique_name"
                    and bool(source.get("producer_node"))
                    and any(
                        n.get("conditions", {}).get("Filter")
                        for n in source.get("cte_scan_nodes", [])
                    )
                )
                if (
                    status
                    in {
                        "join_plan_observed",
                        "window_pass_observed",
                        "cte_producer_observed",
                        "cte_scan_observed",
                        "sort_input_observed",
                    }
                    and not late_cte
                ):
                    # Обычный узел плана сам по себе не является проблемой.
                    # Его сведения остаются в подробном отчёте.
                    continue
                label = (
                    "Недостаточно данных"
                    if check["state"] == "needs_evidence"
                    else "Стоит проверить"
                )
                why, action = MESSAGES[rule_id]
                if check["state"] == "needs_evidence":
                    why = (
                        "Конструкция найдена, но её влияние на работу запроса пока не установлено."
                    )
                    action = (
                        "Откройте подробности: там указано, каких данных не хватает для проверки."
                    )
                if status in SPECIAL:
                    label, why, action = SPECIAL[status]
                if late_cte:
                    why = "CTE принудительно материализован, а фильтр применяется к его готовому результату."
                    action = "Проверьте, можно ли безопасно применить фильтр внутри WITH; сравните равнозначные варианты."
                if rule_id == "AP03" and status == "filter_work_requires_measurement":
                    matches = [
                        p
                        for p in payload.get("sql_features", {})
                        .get("features", {})
                        .get("pattern_predicates", [])
                        if p.get("scope_id") == check.get("scope_id")
                        and p.get("expression") == source.get("expression")
                    ]
                    if (
                        len(matches) == 1
                        and isinstance(matches[0].get("longest_literal_run_length"), int)
                        and matches[0]["longest_literal_run_length"] < 3
                    ):
                        why = "Короткий шаблон может не дать полезных триграмм; условие проверяется фильтром."
                        action = "Измерьте затраты поиска; триграммный индекс не гарантирует ускорение такого шаблона."
                    elif any(c.get("is_trigram") for c in source.get("index_candidates", [])):
                        action = "Триграммный индекс уже найден. Проверьте его применимость и измерьте затраты поиска."
                if status == "projection_unique_by_primary_key":
                    keys = ", ".join(source.get("unique_key") or [])
                    if keys:
                        why = f"Первичный ключ {_short(keys, 65)} уже обеспечивает уникальность выбранных строк."
                location = _short(check.get("label"))
                if location == rule_id:
                    location = ""
                item = dict(
                    title=TITLES[rule_id],
                    label=label,
                    why=why,
                    action=action,
                    location=location,
                    priority=check.get("priority") or 4,
                    source_ref=check["source_ref"],
                )
                items.append(item)
                candidate_ref = check.get("candidate_sql_ref")
                if candidate_ref:
                    sql = resolve(payload, candidate_ref)
                    if isinstance(sql, str) and sql.strip():
                        candidates.append((item["title"], sql))
            if rule_id == "AP12":
                for graph in rule.get("source_graph_observations") or []:
                    if graph.get("status") == "disconnected":
                        why, action = MESSAGES["AP12"]
                        items.append(
                            dict(
                                title=TITLES[rule_id],
                                label="Проверьте смысл",
                                why=why,
                                action=action,
                                location="",
                                priority=1,
                                source_ref=graph["source_ref"],
                            )
                        )
            if not checks and rule["state"] == "needs_evidence" and rule.get("source_ref"):
                route = resolve(payload, rule["source_ref"])
                if route.get("status") == "candidate":
                    items.append(
                        dict(
                            title=TITLES[rule_id],
                            label="Недостаточно данных",
                            why="Конструкция найдена, но её выполнение пока не сопоставлено с планом.",
                            action="Откройте подробности для проверки этой части запроса.",
                            location="",
                            priority=4,
                            source_ref=rule["source_ref"],
                        )
                    )
    facts = tree.get("measured_observations") or []
    for fact in facts:
        kind = fact.get("kind")
        if kind not in RUNTIME:
            continue
        # Не дублируем временный ввод-вывод, уже объяснённый сортировкой на диске.
        if kind == "temporary_io" and any(f.get("kind") == "disk_sort" for f in facts):
            continue
        title, why, action = RUNTIME[kind]
        items.append(
            dict(
                title=title,
                label="Измерено",
                why=why,
                action=action,
                location="",
                priority=2,
                source_ref=fact["source_ref"],
            )
        )
    items.sort(key=lambda item: (item["priority"], item["source_ref"]))
    unique = []
    seen = set()
    for item in items:
        key = (item["title"], item["why"], item["action"], item["location"])
        if key not in seen:
            unique.append(item)
            seen.add(key)
    lines = []
    execution_ms = tree.get("execution_time_ms")
    if (
        tree.get("measurement_status") == "measured"
        and isinstance(execution_ms, (int, float))
        and not isinstance(execution_ms, bool)
        and math.isfinite(execution_ms)
        and execution_ms >= 0
    ):
        time_label = (
            "менее 0,1 мс" if execution_ms < 0.1 else f"{execution_ms:,.1f} мс".replace(",", " ")
        )
        lines += ["Время на сервере: " + time_label, ""]
    else:
        lines += ["Время выполнения не измерялось.", ""]
    if not unique:
        lines += [
            "По доступным данным конкретных проблем не выявлено.",
            "Это не гарантирует оптимальность запроса.",
        ]
    for i, item in enumerate(unique[:3], 1):
        lines += [f"{i}. {item['title']} · {item['label']}"]
        if item["location"]:
            lines += ["Где: " + item["location"]]
        lines += [item["why"], "Что сделать: " + item["action"], ""]
    if unique:
        lines += ["Причина замедления пока не подтверждена."]
    if len(unique) > 3:
        lines += [f"Других замечаний: {len(unique) - 3}. См. «Подробности»."]
    candidate_text = "\n\n".join(title + "\n" + sql for title, sql in dict.fromkeys(candidates))
    if candidate_text:
        candidate_text = (
            "Варианты для ручной проверки. Они не выполнялись автоматически.\n"
            "Проверьте сохранение результата и сравните время.\n\n" + candidate_text
        )
    return dict(
        brief="\n".join(lines).strip(),
        candidates=candidate_text,
        findings=unique[:3],
        all_findings=unique,
        total_findings=len(unique),
    )


def present_report(report):
    """Готовит представления отчёта, включая старый текстовый формат."""
    details, separator, raw = report.rpartition(MARKER)
    if separator:
        try:
            payload = json.loads(raw)
            result = summarize(payload)
            return dict(**result, details=details.rstrip(), raw=raw.strip())
        except (ValueError, TypeError, KeyError, IndexError, AttributeError):
            pass
    # Старый или повреждённый отчёт показываем исходным текстом.
    return dict(
        brief="Для этого сохранённого отчёта краткий вывод недоступен.\n"
        "Откройте «Подробности» или запустите анализ заново.",
        details=report,
        raw="",
        candidates="",
        findings=[],
        total_findings=0,
    )
