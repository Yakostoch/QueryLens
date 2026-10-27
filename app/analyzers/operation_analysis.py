"""Проверка DISTINCT, сортировок, группировки, CTE, окон и JOIN."""

from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.normalize_identifiers import normalize_identifiers
from sqlglot.optimizer.scope import Scope, traverse_scope

from app.analyzers.access_analysis import _bind, _conjuncts, _context, _unwrap
from app.analyzers.predicate_analysis import _nonnull
from app.analyzers.sql_features import parse_query
from app.analyzers.volume_analysis import _metrics, _sources, _table


def _sql(node):
    return node.sql(dialect="postgres") if node is not None else None


def _finding(rule, node, sid):
    return dict(
        rule_id=rule,
        scope_id=sid,
        expression=_sql(node),
        status="insufficient_data",
        evidence=[],
        recommendations=[],
        plan_mapping_status="unknown",
        plan_nodes=[],
        candidate_sql=None,
        slowdown_confirmed=False,
    )


def _children(node, nodes):
    return [
        n
        for n in nodes
        if n["parent_path"] == node["path"]
        and n.get("parent_relationship") not in {"InitPlan", "SubPlan"}
    ]


def _pipeline(nodes):
    excluded = [n["path"] for n in nodes if n.get("parent_relationship") in {"InitPlan", "SubPlan"}]
    return [
        n
        for n in nodes
        if not any(n["path"] == p or n["path"].startswith(p + ".Plans[") for p in excluded)
    ]


def _attach(f, selected, nodes):
    f["plan_nodes"] = [
        dict(
            **_metrics(n),
            strategy=n.get("strategy"),
            input_nodes=[_metrics(c) for c in _children(n, nodes)],
            conditions=n.get("conditions", {}),
        )
        for n in selected
    ]
    f["plan_mapping_status"] = "matched_simple_scope" if selected else "no_explicit_node"


def _unique_projection(select, sid, metadata, ctes):
    if len(_sources(select)) != 1 or select.args.get("joins") or select.args.get("group"):
        return None
    source = _sources(select)[0]
    table = _table(source, sid, metadata, ctes)
    if table is None:
        return None
    values = [p.this if isinstance(p, exp.Alias) else p for p in select.expressions]
    if any(not isinstance(p, (exp.Column, exp.Literal, exp.Null)) for p in values):
        return None
    fields = {f["name"]: f for f in table.get("all_fields", [])}
    chosen = set()
    for projection in select.expressions:
        value = projection.this if isinstance(projection, exp.Alias) else projection
        if (
            isinstance(value, exp.Column)
            and value.name in fields
            and (not value.table or value.table == source.alias_or_name)
        ):
            chosen.add(value.name)
    constraints = table.get("diagnostics", {}).get("sources", {}).get("constraints", {})
    relation = table.get("diagnostics", {}).get("sources", {}).get("relation", {})
    data = relation.get("data") or {}
    if (
        constraints.get("status") != "ok"
        or data.get("relation_kind") != "r"
        or data.get("has_inheritance_children") is not False
    ):
        return None
    for c in constraints.get("data") or []:
        keys = c.get("columns") or []
        if (
            c.get("kind") == "p"
            and c.get("is_validated") is True
            and c.get("is_enforced") is not False
            and keys
            and set(keys) <= chosen
        ):
            if all(
                _nonnull(
                    select,
                    exp.column(k, table=source.alias_or_name),
                    (source, table, fields[k]),
                    metadata,
                )[0]
                == "not_null"
                for k in keys
            ):
                return keys
    return None


def _distinct(select, sid, simple, nodes, metadata, ctes):
    node = select.args["distinct"]
    f = _finding("AP06", node, sid)
    f.update(
        distinct_kind="DISTINCT ON" if node.args.get("on") is not None else "DISTINCT",
        projection=[_sql(x) for x in select.expressions],
        uniqueness_proven=None,
    )
    if not simple:
        return f
    dedup = [
        n
        for n in nodes
        if n["node_type"] == "Unique"
        or n["node_type"] == "Aggregate"
        and n.get("strategy") in {"Hashed", "Sorted"}
    ]
    # Aggregate также обслуживает GROUP BY и агрегаты с DISTINCT.
    aggregates = [x for x in select.walk() if isinstance(x, exp.AggFunc)]
    if select.args.get("group") or aggregates:
        f["evidence"].append("Группировка или агрегаты мешают однозначно приписать узлы DISTINCT.")
    else:
        _attach(f, dedup, nodes)
        f["status"] = "dedup_plan_observed" if dedup else "dedup_node_not_observed"
    if node.args.get("on") is not None:
        f["status"] = "distinct_on_requires_order_semantics"
        f["evidence"].append(
            "DISTINCT ON выбирает строку группы с учётом порядка; удаление не предлагается."
        )
    elif not aggregates and not list(select.find_all(exp.Window)):
        keys = _unique_projection(select, sid, metadata, ctes)
        if keys:
            f.update(
                status="projection_unique_by_primary_key", uniqueness_proven=True, unique_key=keys
            )
            f["recommendations"].append(
                "Проекция содержит подтверждённый первичный ключ одной таблицы; рассмотрите проверку варианта без DISTINCT и сравните планы."
            )
    if select.args.get("joins"):
        f["evidence"].append("Ключ одной стороны не доказывает уникальность результата JOIN.")
        f["recommendations"].append(
            "Если нужны только строки одной стороны с наличием совпадения, проверьте вариант EXISTS с сохранением кратности."
        )
    return f


def _sort(select, sid, simple, nodes):
    f = _finding("AP07", select.args["order"], sid)
    f.update(
        order_by_keys=[_sql(x) for x in select.args["order"].expressions],
        limit_expression=_sql(select.args.get("limit")),
        matching_order_index="unknown",
    )
    if not simple:
        return f
    sort_nodes = [n for n in nodes if n["node_type"] in {"Sort", "Incremental Sort"}]
    unambiguous = not (
        select.args.get("distinct")
        or select.args.get("group")
        or list(select.find_all(exp.Window))
        or any(isinstance(x, exp.AggFunc) for x in select.walk())
    )
    if unambiguous and len(sort_nodes) == 1:
        _attach(f, sort_nodes, nodes)
        f["status"] = "sort_input_observed"
    elif not sort_nodes:
        f["status"] = "no_explicit_sort"
        f["evidence"].append(
            "Отдельной сортировки нет; порядок может поступать из доступа или быть упрощён. Это не доказательство дешёвого запроса."
        )
    else:
        f["status"] = "sort_purpose_mapping_unknown"
        f["candidate_sort_paths"] = [n["path"] for n in sort_nodes]
    f["sort_details"] = [
        dict(
            path=n["path"],
            sort_keys=n.get("sort_keys"),
            presorted_keys=n.get("presorted_keys"),
            sort_method=n.get("sort_method"),
            sort_space_type=n.get("sort_space_type"),
            sort_space_used=n.get("sort_space_used"),
        )
        for n in sort_nodes
    ]
    f["recommendations"].append(
        "Проверьте вход сортировки и индекс для требуемого порядка. LIMIT допустим только по задаче; work_mem оценивается после измерения выхода на диск."
    )
    return f


def _having(select, sid, simple, nodes, metadata, plan):
    f = _finding("AP09", select.args["having"], sid)
    f.update(
        grouping_keys=[_sql(x) for x in (select.args.get("group") or exp.Group()).expressions],
        predicates=[],
        safe_to_pushdown=None,
    )
    if not simple:
        return f
    group = select.args.get("group")
    ordinary = (
        group is not None
        and bool(group.expressions)
        and all(isinstance(x, exp.Column) for x in group.expressions)
    )
    ordinary = ordinary and not any(
        group.args.get(k) for k in ("grouping_sets", "rollup", "cube", "all")
    )
    for predicate in _conjuncts(select.args["having"].this):
        predicate = _unwrap(predicate)
        item = dict(
            expression=_sql(predicate),
            safe_to_pushdown=None,
            predicate_plan_location="unknown",
            plan_roles=[],
        )
        if (
            isinstance(predicate, exp.EQ)
            and isinstance(predicate.this, exp.Column)
            and isinstance(predicate.expression, exp.Literal)
            and ordinary
        ):
            context, _ = _context(select, metadata, plan["nodes"], predicate)
            if context:
                table, scan, fields, aliases = context
                from app.analyzers.access_analysis import _canonical

                key = _canonical(predicate.this, fields, aliases)
                if any(
                    isinstance(x, exp.Column)
                    and (not x.table or x.table in aliases)
                    and x.name in fields
                    and _canonical(x, fields, aliases) == key
                    for x in group.expressions
                ) and fields.get(predicate.this.name) in {
                    "integer",
                    "bigint",
                    "smallint",
                    "text",
                    "uuid",
                    "boolean",
                    "date",
                }:
                    item["safe_to_pushdown"] = True
                    early = _bind(predicate, scan, plan["nodes"], fields, aliases)
                    late = []
                    for n in nodes:
                        if n["node_type"] in {"Aggregate", "Group"} and n.get("conditions", {}).get(
                            "Filter"
                        ):
                            late.extend(_bind(predicate, n, [], fields, aliases))
                    item["plan_roles"] = early + late
                    item["predicate_plan_location"] = (
                        "both"
                        if early and late
                        else (
                            "before_aggregation"
                            if early
                            else "after_aggregation" if late else "unknown"
                        )
                    )
        item["depends_on_aggregate"] = any(isinstance(x, exp.AggFunc) for x in predicate.walk())
        if item["depends_on_aggregate"]:
            item["safe_to_pushdown"] = False
        # Наличие фильтра группы ещё не означает возможность переноса в WHERE.
        context, _ = _context(select, metadata, plan["nodes"], predicate)
        if context or not list(predicate.find_all(exp.Column)):
            fields = context[2] if context else {}
            aliases = context[3] if context else set()
            late = []
            for n in nodes:
                if n["node_type"] in {"Aggregate", "Group"} and n.get("conditions", {}).get(
                    "Filter"
                ):
                    late.extend(_bind(predicate, n, [], fields, aliases))
            early = [
                r
                for r in item["plan_roles"]
                if not any(
                    r["path"] == n["path"]
                    for n in nodes
                    if n["node_type"] in {"Aggregate", "Group"}
                )
            ]
            item["plan_roles"] = early + late
            item["predicate_plan_location"] = (
                "both"
                if early and late
                else "before_aggregation" if early else "after_aggregation" if late else "unknown"
            )
        f["predicates"].append(item)
    if f["predicates"] and all(x["safe_to_pushdown"] is True for x in f["predicates"]):
        f["safe_to_pushdown"] = True
    f["status"] = "having_predicates_checked"
    _attach(f, [n for n in nodes if n["node_type"] in {"Aggregate", "Group"}], nodes)
    f["recommendations"].append(
        "Условия по агрегатам оставьте в HAVING. Для безопасного фильтра ключа проверьте, не применил ли PostgreSQL ранний отбор уже; перенос OR и особых группировок требует отдельной проверки."
    )
    return f


def _cte(cte, tree, nodes, sid=None):
    f = _finding("AP13", cte, sid)
    name = cte.alias_or_name
    recursive = bool(cte.parent.args.get("recursive"))
    f.update(
        cte_name=name,
        cte_materialization_hint=cte.args.get("materialized"),
        cte_kind="recursive" if recursive else "select",
        cte_reference_count=None,
        cte_inline_eligibility="unknown",
        cte_scan_nodes=[],
        producer_mapping_status="unknown",
    )
    same = [c for c in tree.find_all(exp.CTE) if c.alias_or_name == name]
    if len(same) != 1:
        f["evidence"].append("Имя CTE повторяется в областях; сопоставление по имени неоднозначно.")
        return f
    try:
        f["cte_reference_count"] = sum(
            isinstance(source, Scope) and source.expression is cte.this
            for scope in traverse_scope(tree)
            for _, source in scope.selected_sources.values()
        )
    except (SqlglotError, ValueError, KeyError):
        pass
    scans = [n for n in nodes if n.get("cte_name") == name and n["node_type"] == "CTE Scan"]
    producers = [n for n in nodes if n.get("subplan_name") == "CTE " + name]
    f["cte_scan_nodes"] = [dict(**_metrics(n), conditions=n["conditions"]) for n in scans]
    if len(producers) == 1:
        f.update(
            status="cte_producer_observed",
            producer_mapping_status="matched_by_unique_name",
            producer_node=_metrics(producers[0]),
            plan_mapping_status="matched_by_unique_name",
        )
    else:
        f["status"] = "cte_scan_observed" if scans else "no_explicit_cte_nodes"
    if recursive:
        f["status"] = "recursive_cte_requires_separate_analysis"
        f["cte_inline_eligibility"] = "ineligible"
    f["evidence"].append(
        "Отсутствие CTE Scan не доказывает встраивание: источник может быть не использован или упрощён. Materialize сам по себе не является CTE."
    )
    f["recommendations"].append(
        "Сопоставьте производителя и фильтры всех потребителей. MATERIALIZED/NOT MATERIALIZED меняют объём повторной работы; свойства функций и версия требуют проверки. Внешний фильтр не переносится автоматически."
    )
    return f


def _windows(select, sid, simple, nodes):
    windows = [
        x
        for x in select.find_all(exp.Window)
        if x.find_ancestor(exp.Select) is select and x not in (select.args.get("windows") or [])
    ]
    if not windows:
        return None
    f = _finding("AP14", windows[0], sid)
    f.update(
        window_specs=[
            dict(
                expression=_sql(w),
                partition_by=[_sql(x) for x in w.args.get("partition_by") or []],
                order_by=_sql(w.args.get("order")),
                frame=_sql(w.args.get("spec")),
                named_window=w.args.get("alias").name if w.args.get("alias") else None,
                frame_default_unresolved=w.args.get("spec") is None,
            )
            for w in windows
        ],
        window_functions=[_sql(w.this) for w in windows],
        filter_pushdown_safety="unknown",
        named_window_definitions=[_sql(x) for x in select.args.get("windows") or []],
    )
    if simple:
        selected = [n for n in nodes if n["node_type"] == "WindowAgg"]
        f["window_agg_count"] = len(selected)
        f["status"] = (
            "window_pass_observed" if len(selected) == 1 else "window_pass_mapping_unknown"
        )
        if len(selected) == 1:
            _attach(f, selected, nodes)
        else:
            f["candidate_window_paths"] = [n["path"] for n in selected]
        f["window_run_conditions"] = [
            dict(path=n["path"], condition=n["conditions"].get("Run Condition")) for n in selected
        ]
    f["evidence"].append(
        "Окно сохраняет строки. Run Condition не гарантирует малого входа; рамки и именованные окна необходимо учитывать по смыслу."
    )
    f["recommendations"].append(
        "Проверьте вход WindowAgg и требуемый порядок. Перенос фильтра может изменить ранги, суммы и соседние строки; ROW_NUMBER, RANK и DENSE_RANK не взаимозаменяемы."
    )
    return f


def _joins(select, sid, simple, nodes):
    f = _finding("AP15", select, sid)
    f["expression"] = "; ".join(_sql(j) for j in select.args["joins"])
    f.update(join_nodes=[], logical_join_mapping="unknown")
    if not simple:
        return f
    selected = [n for n in nodes if n["node_type"] in {"Nested Loop", "Hash Join", "Merge Join"}]
    f["status"] = "join_plan_observed" if selected else "join_node_not_observed"
    f["logical_join_mapping"] = (
        "single_join_scope"
        if len(select.args["joins"]) == 1 and len(selected) == 1
        else "query_level_only"
    )
    for n in selected:
        children = _children(n, nodes)
        item = dict(
            **_metrics(n),
            join_type=n.get("join_type"),
            conditions=n["conditions"],
            input_nodes=[_metrics(c) for c in children],
            estimated_fanout=None,
            inner_index_conditions=[],
        )
        if len(children) == 2:
            outer, inner = children
            if (
                n.get("join_type") in {"Inner", "Left"}
                and outer.get("plan_rows")
                and n.get("plan_rows") is not None
            ):
                item["estimated_fanout"] = n["plan_rows"] / outer["plan_rows"]
            subtree = [
                v
                for v in nodes
                if v["path"] == inner["path"] or v["path"].startswith(inner["path"] + ".Plans[")
            ]
            item["inner_index_conditions"] = [
                dict(
                    path=v["path"],
                    index_name=v["index_name"],
                    condition=v["conditions"]["Index Cond"],
                )
                for v in subtree
                if v.get("index_name") and v.get("conditions", {}).get("Index Cond")
            ]
        f["join_nodes"].append(item)
    f["plan_mapping_status"] = "query_level_observation"
    f["evidence"].append(
        "Алгоритм JOIN и высокая кратность не доказывают ошибку. Внутренний Index Cond может содержать связь даже без Join Filter."
    )
    f["recommendations"].append(
        "Проверьте требуемую кратность, полноту ключа и входы соединения. Ошибки оценки, повторные обращения и затраты на диск проверяются измерением; перенос условий OUTER JOIN требует сохранения смысла."
    )
    return f


def analyze_operations(sql, syntax, plan, sources):
    tree = normalize_identifiers(parse_query(sql), dialect="postgres")
    selects = list(tree.find_all(exp.Select))
    ctes = {c.alias_or_name for c in tree.find_all(exp.CTE)}
    metadata_source = sources.get("metadata", {})
    metadata = metadata_source.get("data") or {} if metadata_source.get("status") == "ok" else {}
    nodes = plan["nodes"]
    pipeline = _pipeline(nodes)
    findings = []
    for i, select in enumerate(selects, 1):
        sid = f"select_{i}"
        simple = select is tree and len(selects) == 1 and not tree.args.get("with_")
        if select.args.get("distinct") is not None:
            findings.append(_distinct(select, sid, simple, pipeline, metadata, ctes))
        if select.args.get("order") is not None:
            findings.append(_sort(select, sid, simple, pipeline))
        if select.args.get("having") is not None:
            findings.append(_having(select, sid, simple, pipeline, metadata, plan))
        window = _windows(select, sid, simple, pipeline)
        if window:
            findings.append(window)
        if select.args.get("joins"):
            findings.append(_joins(select, sid, simple, pipeline))
    if isinstance(tree, exp.SetOperation) and tree.args.get("order") is not None:
        findings.append(_finding("AP07", tree.args["order"], None))
    findings.extend(
        _cte(
            c,
            tree,
            nodes,
            next((f"select_{i}" for i, s in enumerate(selects, 1) if s is c.this), None),
        )
        for c in tree.find_all(exp.CTE)
    )
    return dict(
        schema_version=1,
        findings=findings,
        limitations=[
            "Узлы операций сопоставляются только в поддержанной области; вложенным SELECT не приписаны показатели корня.",
            "Оценки входа — не объём чтения, расход памяти или длительность. Стоимости родителя и потомка не суммируются.",
            "Индексы для порядка, свойства функций и эквивалентность сложных преобразований могут оставаться неизвестными.",
            "Рекомендации не выполняются автоматически; подтверждение ускорения требует измерений.",
        ],
    )
