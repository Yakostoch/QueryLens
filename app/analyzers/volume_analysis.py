"""Проверка выбираемых столбцов, OFFSET и COUNT для существования."""

from sqlglot import exp
from sqlglot.optimizer.normalize_identifiers import normalize_identifiers

from app.analyzers.sql_features import parse_query


def _unparen(node):
    while isinstance(node, exp.Paren):
        node = node.this
    return node


def _integer(node):
    if isinstance(node, exp.Literal) and not node.is_string:
        try:
            value = int(node.this)
            return value if value >= 0 else None
        except ValueError:
            pass
    return None


def _sources(select):
    from_ = select.args.get("from_")
    return ([from_.this] if from_ else []) + [j.this for j in select.args.get("joins", [])]


def _table(source, scope_id, metadata, cte_names):
    if (
        not isinstance(source, exp.Table)
        or not isinstance(source.this, exp.Identifier)
        or source.catalog
    ):
        return None
    if not source.db and source.name in cte_names:
        return None
    bindings = [
        b
        for b in metadata.get("source_bindings", [])
        if b["scope_id"] == scope_id and b["alias"] == source.alias_or_name
    ]
    if len(bindings) == 1:
        matches = [
            t
            for t in metadata.get("tables", [])
            if (t["schema"], t["name"]) == (bindings[0]["schema"], bindings[0]["name"])
        ]
    else:
        matches = [
            t
            for t in metadata.get("tables", [])
            if t["name"] == source.name
            and (not source.db or t["schema"] == source.db)
            and "sql" in t.get("metadata_origins", ["sql"])
        ]
    return matches[0] if len(matches) == 1 else None


def _metrics(node):
    return {
        k: node.get(k)
        for k in ("path", "node_type", "total_cost", "plan_rows", "plan_width", "estimated_bytes")
    }


def _children(node, nodes):
    return [n for n in nodes if n["parent_path"] == node["path"]]


def _entry(rule, expression, scope_id):
    return dict(
        rule_id=rule,
        expression=expression,
        scope_id=scope_id,
        status="insufficient_data",
        evidence=[],
        recommendations=[],
        slowdown_confirmed=False,
    )


def _star(select, target, sid, is_root, metadata, cte_names, plan, consumer):
    f = _entry("AP01", target.sql(dialect="postgres"), sid)
    f.update(
        star_scope="root" if is_root else "nested",
        expanded_columns_count=None,
        expanded_columns=[],
        required_columns_status="unknown",
        excess_columns=None,
        node_metrics=_metrics(plan["nodes"][0]) if is_root else None,
        plan_output_expressions=plan["nodes"][0].get("output") if is_root else None,
    )
    qualifier = target.table if isinstance(target, exp.Column) else None
    sources = _sources(select)
    relevant = [s for s in sources if not qualifier or s.alias_or_name == qualifier]
    using_star = not qualifier and any(
        j.args.get("using") or j.args.get("method") == "NATURAL"
        for j in select.args.get("joins", [])
    )
    expanded = []
    complete = bool(relevant) and not using_star
    for source in relevant:
        table = _table(source, sid, metadata, cte_names)
        if table is None or "all_fields" not in table:
            complete = False
            continue
        expanded.extend(
            dict(
                schema=table["schema"],
                table=table["name"],
                alias=source.alias_or_name,
                name=column["name"],
                type=column["type"],
            )
            for column in table["all_fields"]
        )
    if complete:
        f.update(expanded_columns_count=len(expanded), expanded_columns=expanded)
    f["star_expansion_status"] = "resolved" if complete else "requires_source_lineage"
    f["star_targets"] = [s.alias_or_name for s in relevant]
    if not complete:
        f["evidence"].append(
            "Для точного раскрытия звёздочки нужны сведения о выходе источника или объединении столбцов USING/NATURAL."
        )
    if not is_root:
        f.update(status="nested_projection_requires_plan_mapping")
        f["evidence"].append(
            "Звёздочка вложенного SELECT может быть сокращена оптимизатором; показатели корня ей не приписаны."
        )
        return f
    f["status"] = "consumer_requirements_unknown"
    f["evidence"].append("Наличие звёздочки и оценка ширины не доказывают избыточность результата.")
    if consumer.get("all_output_columns_required") is True:
        f.update(status="all_columns_required", required_columns_status="explicit_all")
        return f
    requested = consumer.get("required_columns")
    if requested is not None:
        simple = (
            complete and len(select.expressions) == 1 and len(relevant) == 1 and len(sources) == 1
        )
        names = [c["name"] for c in expanded]
        valid = (
            isinstance(requested, list)
            and bool(requested)
            and all(isinstance(c, str) for c in requested)
        )
        if simple and valid and set(requested) <= set(names):
            excess = [c for c in names if c not in requested]
            f.update(
                status="potential_excess_output" if excess else "all_columns_required",
                required_columns_status="explicit_subset",
                excess_columns=excess,
            )
            if excess:
                f["recommendations"].append(
                    "По явно заданному контракту перечислите нужные поля; ускорение требует отдельного сравнения."
                )
            return f
        f["required_columns_status"] = "not_validated_for_this_projection"
    f["recommendations"].append(
        "Уточните, нужны ли потребителю все поля; без этого исправленный список SELECT не определяется."
    )
    return f


def _order_uniqueness(select, sid, metadata, cte_names):
    order = select.args.get("order")
    sources = _sources(select)
    if order is None or len(sources) != 1 or select.args.get("joins"):
        return "unknown"
    table = _table(sources[0], sid, metadata, cte_names)
    if not table:
        return "unknown"
    alias = sources[0].alias_or_name
    output_aliases = {p.alias: p.this for p in select.expressions if isinstance(p, exp.Alias)}
    keys = set()
    for item in order.expressions:
        key = item.this
        if isinstance(key, exp.Column) and not key.table and key.name in output_aliases:
            key = output_aliases[key.name]
        if isinstance(key, exp.Column) and (not key.table or key.table == alias):
            keys.add(key.name)
    constraints = table.get("diagnostics", {}).get("sources", {}).get("constraints", {})
    if constraints.get("status") != "ok":
        return "unknown"
    fields = {f["name"]: f for f in table.get("all_fields", [])}
    for c in constraints.get("data") or []:
        columns = c.get("columns") or []
        nonnull = all(fields.get(n, {}).get("nullable") is False for n in columns)
        if columns and set(columns) <= keys and c.get("is_validated") is True:
            if c.get("kind") == "p" or c.get("kind") == "u" and nonnull:
                return "proven_by_catalog_key"
    return "unknown"


def _offset(select, sid, is_root, metadata, cte_names, plan):
    offset = select.args["offset"]
    value = _integer(offset.expression)
    limit = select.args.get("limit")
    limit_value = _integer(limit.expression) if limit else None
    f = _entry("AP08", offset.sql(dialect="postgres"), sid)
    f.update(
        offset_value=value,
        limit_value=limit_value,
        limit_status=(
            "absent" if limit is None else "known" if limit_value is not None else "unknown"
        ),
        offset_to_limit_ratio=value / limit_value if value is not None and limit_value else None,
        requested_prefix_rows=None,
        order_by_keys=(
            [x.sql(dialect="postgres") for x in select.args["order"].expressions]
            if select.args.get("order")
            else []
        ),
        order_uniqueness=_order_uniqueness(select, sid, metadata, cte_names),
        cursor_available=False,
        node_metrics=None,
        limit_child_rows=None,
    )
    if is_root and plan["nodes"][0]["node_type"] == "Limit":
        node = plan["nodes"][0]
        children = _children(node, plan["nodes"])
        f["node_metrics"] = _metrics(node)
        f["limit_child_rows"] = children[0].get("plan_rows") if len(children) == 1 else None
    if limit_value == 0:
        f.update(status="no_input_required_for_limit_zero", requested_prefix_rows=0)
        return f
    if value is None:
        f["evidence"].append("Неизвестно значение OFFSET; глубину пропускания определить нельзя.")
        return f
    if value == 0:
        f["status"] = "no_rows_skipped"
        return f
    f["status"] = "offset_work_present"
    if limit_value is not None:
        f["requested_prefix_rows"] = value + limit_value
    f["evidence"].append(
        f"OFFSET задаёт пропускание до {value} строк результирующего потока, если они существуют; это не число прочитанных строк таблицы."
    )
    if f["limit_child_rows"] is not None:
        f["evidence"].append(
            "Plan Rows дочернего узла — оценка его полного выхода, не фактически потреблённых строк."
        )
    f["recommendations"].append(
        "Для глубоких последовательных страниц рассмотрите курсор при стабильном уникальном порядке; нужны требования навигации и значения курсора."
    )
    if not f["order_by_keys"]:
        f["recommendations"].append("Если нужны стабильные страницы, задайте подходящий ORDER BY.")
    return f


def _boolean_count(select, count):
    if len(select.expressions) != 1:
        return None
    projection = select.expressions[0]
    expression = projection.this if isinstance(projection, exp.Alias) else projection
    expression = _unparen(expression)
    count_on_left = (
        isinstance(expression.this, exp.Count) if isinstance(expression, exp.Binary) else False
    )
    count_on_right = (
        isinstance(expression.expression, exp.Count)
        if isinstance(expression, exp.Binary)
        else False
    )
    if count_on_left:
        n = _integer(expression.expression)
        if (
            isinstance(expression, exp.GT)
            and n == 0
            or isinstance(expression, exp.GTE)
            and n == 1
            or isinstance(expression, exp.NEQ)
            and n == 0
        ):
            return True
        if isinstance(expression, exp.EQ) and n == 0:
            return False
    if count_on_right:
        n = _integer(expression.this)
        if isinstance(expression, exp.LT) and n == 0:
            return True
        if isinstance(expression, exp.EQ) and n == 0:
            return False
    return None


def _count(select, count, sid, is_root, metadata, cte_names, plan, consumer):
    f = _entry("AP11", count.sql(dialect="postgres"), sid)
    f.update(
        count_usage="unknown",
        count_scope="aggregate",
        rewrite_equivalence="not_established",
        candidate_sql=None,
        candidate_requires_review=True,
        count_input_rows=None,
        node_metrics=None,
        count_kind="star" if isinstance(count.this, exp.Star) else "expression",
        count_argument=count.this.sql(dialect="postgres") if count.this is not None else None,
    )
    wanted = _boolean_count(select, count)
    if wanted is not None:
        f["count_usage"] = "existence_only"
    elif consumer.get("count_usage") == "existence_only":
        f["count_usage"] = "consumer_existence_numeric_result"
    elif consumer.get("count_usage") == "exact_count":
        f["count_usage"] = "exact_count"
    if wanted is None and len(select.expressions) == 1:
        projection = select.expressions[0]
        projection = _unparen(projection.this if isinstance(projection, exp.Alias) else projection)
        if isinstance(projection, (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE)) and (
            isinstance(projection.this, exp.Count)
            and _integer(projection.expression) is not None
            or isinstance(projection.expression, exp.Count)
            and _integer(projection.this) is not None
        ):
            f["count_usage"] = "threshold"
    if not is_root:
        f.update(status="nested_count_requires_plan_mapping")
        return f
    if plan["nodes"][0]["node_type"] == "Aggregate":
        node = plan["nodes"][0]
        children = _children(node, plan["nodes"])
        f["node_metrics"] = _metrics(node)
        f["count_input_rows"] = children[0].get("plan_rows") if len(children) == 1 else None
    if f["count_usage"] == "exact_count":
        f["status"] = "exact_count_required"
        return f
    if f["count_usage"] == "threshold":
        f["status"] = "quantity_threshold_required"
        return f
    unsupported = any(
        select.args.get(k) is not None
        for k in ("group", "having", "limit", "offset", "distinct", "with_", "order")
    )
    unsupported = (
        unsupported or len(list(select.find_all(exp.Select))) != 1 or len(_sources(select)) != 1
    )
    unsupported = unsupported or any(isinstance(x, exp.Window) for x in select.walk())
    if unsupported:
        f.update(status="aggregate_semantics_requires_review")
        return f
    if wanted is None:
        f["status"] = (
            "consumer_contract_change_required"
            if f["count_usage"] == "consumer_existence_numeric_result"
            else "count_purpose_unknown"
        )
        f["recommendations"].append(
            "Сохраните COUNT, если нужно число. Проверка существования требует известного назначения и контракта результата."
        )
        return f
    if not isinstance(count.this, exp.Star):
        f["status"] = "count_argument_requires_review"
        f["evidence"].append(
            "COUNT(выражение) не учитывает NULL. Простой EXISTS с прежним WHERE может изменить результат."
        )
        f["recommendations"].append(
            "Проверьте NULL и свойства аргумента COUNT; готовая замена на EXISTS не формируется."
        )
        return f
    # Сложные выражения требуют отдельной проверки семантики.
    functions = [
        x
        for x in select.walk()
        if isinstance(x, exp.Func) and not isinstance(x, (exp.Count, exp.Connector))
    ]
    filtered = any(isinstance(x, exp.Filter) for x in select.walk())
    table = _table(_sources(select)[0], sid, metadata, cte_names)
    relation = (table or {}).get("diagnostics", {}).get("sources", {}).get("relation", {})
    source_data = relation.get("data") or {}
    physical = relation.get("status") == "ok" and source_data.get("relation_kind") == "r"
    rls = source_data.get("row_security_enabled")
    maths = any(isinstance(x, (exp.Add, exp.Sub, exp.Mul, exp.Div, exp.Mod)) for x in select.walk())
    if functions or filtered or maths or not physical or rls is not False:
        f["status"] = "existence_rewrite_requires_more_semantics"
        f["recommendations"].append(
            "Булева проверка допускает рассмотрение EXISTS; свойства источника, RLS и выражений требуют отдельной проверки."
        )
        return f
    inner = select.copy()
    inner.set("expressions", [exp.Literal.number(1)])
    exists = exp.Exists(this=inner)
    if not wanted:
        exists = exp.Not(this=exists)
    projection = select.expressions[0]
    alias = projection.alias if isinstance(projection, exp.Alias) else "?column?"
    candidate = exp.select(exp.alias_(exists, alias, quoted=True))
    f.update(
        status="boolean_existence_candidate",
        rewrite_equivalence="conditional_plain_case",
        candidate_sql=candidate.sql(dialect="postgres"),
    )
    f["evidence"].append(
        "Результат используется как булева проверка; точное число не возвращается."
    )
    f["recommendations"].append(
        "Сравните кандидат с EXISTS. Он требует проверки операторов, прав и результата; ускорение не гарантировано, особенно без совпадений."
    )
    return f


def analyze_volume(sql, syntax, plan, sources, consumer_context=None):
    consumer = consumer_context if isinstance(consumer_context, dict) else {}
    tree = normalize_identifiers(parse_query(sql), dialect="postgres")
    metadata_source = sources.get("metadata", {})
    metadata = metadata_source.get("data") or {} if metadata_source.get("status") == "ok" else {}
    cte_names = {x.alias_or_name for x in tree.find_all(exp.CTE)}
    findings = []
    for n, select in enumerate(tree.find_all(exp.Select), 1):
        sid = f"select_{n}"
        is_root = select is tree
        for projection in select.expressions:
            target = projection.this if isinstance(projection, exp.Alias) else projection
            if (
                isinstance(target, exp.Star)
                or isinstance(target, exp.Column)
                and isinstance(target.this, exp.Star)
            ):
                findings.append(
                    _star(select, target, sid, is_root, metadata, cte_names, plan, consumer)
                )
        if select.args.get("offset") is not None:
            findings.append(_offset(select, sid, is_root, metadata, cte_names, plan))
        for count in select.find_all(exp.Count):
            owner = count.find_ancestor(exp.Select)
            from app.analyzers.sql_features import _inside_window

            if owner is select and not _inside_window(count):
                findings.append(
                    _count(select, count, sid, is_root, metadata, cte_names, plan, consumer)
                )
    for offset in syntax["features"]["offsets"]:
        if offset.get("scope_id") is None:
            f = _entry("AP08", offset["expression"], None)
            f["evidence"].append(
                "OFFSET операции над наборами пока не сопоставлен с конкретным узлом; показатели корня не подставлены."
            )
            findings.append(f)
    return {
        "schema_version": 1,
        "findings": findings,
        "limitations": [
            "Оценки плана не измеряют фактическое чтение, время или сетевой трафик.",
            "Нужные поля и назначение числового COUNT не угадываются; контекст задаётся явно.",
            "Кандидат SQL не применяется автоматически и не является доказательством ускорения.",
            "Вложенным SELECT не приписываются показатели корневого узла.",
        ],
    }
