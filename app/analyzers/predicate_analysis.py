"""Проверка OR и семантики NULL в NOT IN."""

import re

from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.normalize_identifiers import normalize_identifiers

from app.analyzers.access_analysis import (
    _canonical,
    _conjuncts,
    _context,
    _index_candidates,
    _parse,
    _unwrap,
)
from app.analyzers.sql_features import _clause, parse_query
from app.analyzers.volume_analysis import _sources, _table


def _entry(rule, node, sid):
    return dict(
        rule_id=rule,
        scope_id=sid,
        expression=node.sql(dialect="postgres"),
        status="insufficient_data",
        evidence=[],
        recommendations=[],
        candidate_sql=None,
        candidate_requires_review=True,
        slowdown_confirmed=False,
    )


def _alternatives(node, kind=exp.Or):
    node = _unwrap(node)
    return (
        _alternatives(node.this, kind) + _alternatives(node.expression, kind)
        if isinstance(node, kind)
        else [node]
    )


def _signature(node, fields, aliases):
    node = _unwrap(node)
    if isinstance(node, (exp.And, exp.Or)):
        return (
            type(node).__name__,
            tuple(
                sorted(
                    (_signature(x, fields, aliases) for x in _alternatives(node, type(node))),
                    key=repr,
                )
            ),
        )
    return _canonical(node, fields, aliases)


def _matches(condition, target, fields, aliases):
    try:
        parsed = _parse(condition)
        choices = [parsed] + _conjuncts(parsed)
        return any(
            _signature(x, fields, aliases) == _signature(target, fields, aliases) for x in choices
        )
    except (SqlglotError, ValueError, KeyError):
        return False


def _or(select, node, sid, tree, metadata, plan):
    f = _entry("AP04", node, sid)
    f.update(
        or_branch_count=len(_alternatives(node)),
        predicate_plan_roles=[],
        branches=[],
        bitmap_or_present=False,
        bitmap_or_group_mapping="not_established",
        branches_disjoint="not_assessed",
        in_expression_candidate=None,
    )
    if select is not tree or _clause(node) != "where":
        f["evidence"].append("Эта область OR требует отдельного сопоставления.")
        return f
    context, reason = _context(tree, metadata, plan["nodes"], node)
    if not context:
        f["evidence"].append("Нет однозначного источника/узла для всей группы OR: " + reason)
        return f
    table, scan, fields, aliases = context
    f["node_metrics"] = {
        k: scan.get(k) for k in ("path", "node_type", "total_cost", "plan_rows", "plan_width")
    }
    if not any(
        _matches(x.sql(dialect="postgres"), node, fields, aliases)
        for x in _conjuncts(select.args["where"].this)
    ):
        f["evidence"].append(
            "Группа не является отдельным положительным условием AND; контекст не переписывается."
        )
        return f
    nodes = [
        n
        for n in plan["nodes"]
        if n["path"] == scan["path"] or n["path"].startswith(scan["path"] + ".Plans[")
    ]
    roles = []
    for n in nodes:
        for role, raw in n.get("conditions", {}).items():
            if role in {"Filter", "Index Cond", "Recheck Cond"} and _matches(
                raw, node, fields, aliases
            ):
                roles.append(dict(path=n["path"], role=role, condition=raw))
    f["predicate_plan_roles"] = roles
    branches = _alternatives(node)
    all_branch_evidence = []
    for branch in branches:
        observed = [
            dict(path=n["path"], index_name=n["index_name"], role="Index Cond")
            for n in nodes
            if n.get("index_name")
            and n.get("conditions", {}).get("Index Cond")
            and _matches(n["conditions"]["Index Cond"], branch, fields, aliases)
        ]
        candidates = []
        catalog = None
        if isinstance(branch, (exp.EQ, exp.GT, exp.GTE, exp.LT, exp.LTE, exp.Like, exp.ILike)):
            try:
                catalog, candidates = _index_candidates(
                    table, branch.this, fields, aliases, "AP04", observed
                )
            except (ValueError, KeyError):
                pass
        f["branches"].append(
            dict(
                expression=branch.sql(dialect="postgres"),
                referenced_columns=sorted(
                    {c.sql(dialect="postgres") for c in branch.find_all(exp.Column)}
                ),
                access_status=(
                    "observed_index_condition"
                    if observed
                    else "catalog_candidate_only" if candidates else "not_established"
                ),
                index_candidates=candidates,
                index_evidence=observed,
                index_metadata_status="ok" if catalog else "unknown",
            )
        )
        all_branch_evidence.append(observed)
    bitmap_nodes = [n for n in nodes if n["node_type"] == "BitmapOr"]
    f["bitmap_or_present"] = bool(bitmap_nodes)
    for bitmap in bitmap_nodes:
        children = [n for n in nodes if n["parent_path"] == bitmap["path"]]
        possible = []
        for evidence in all_branch_evidence:
            possible.append(
                {
                    i
                    for i, c in enumerate(children)
                    if any(
                        e["path"] == c["path"] or e["path"].startswith(c["path"] + ".Plans[")
                        for e in evidence
                    )
                }
            )

        def assign(i, used):
            return (
                True
                if i == len(possible)
                else any(assign(i + 1, used | {j}) for j in possible[i] - used)
            )

        if len(children) == len(branches) and roles and assign(0, set()):
            f["bitmap_or_group_mapping"] = "matched"
            break
    if not bitmap_nodes:
        f["bitmap_or_group_mapping"] = "not_present"
    f["status"] = (
        "bitmap_or_group_observed"
        if f["bitmap_or_group_mapping"] == "matched"
        else ("or_group_filter_observed" if roles else "or_group_plan_mapping_unknown")
    )
    if f["status"] == "bitmap_or_group_observed":
        f["evidence"].append(
            "Группа и отдельные индексные ветви сопоставлены с BitmapOr. OR не объявляется ошибкой."
        )
    else:
        f["recommendations"].append(
            "Проверьте селективность и фактическую работу ветвей; число OR и наличие ключа индекса не доказывают замедление."
        )
    simple = all(
        isinstance(b, exp.EQ)
        and isinstance(b.this, exp.Column)
        and isinstance(b.expression, exp.Literal)
        for b in branches
    )
    if simple:
        keys = [_canonical(b.this, fields, aliases) for b in branches]
        if all(k == keys[0] for k in keys) and len({b.expression.is_string for b in branches}) == 1:
            replacement = exp.In(
                this=branches[0].this.copy(), expressions=[b.expression.copy() for b in branches]
            )
            f["in_expression_candidate"] = replacement.sql(dialect="postgres")
            # Показываем выражение: полный запрос требует проверки областей видимости и повторов.
            f["recommendations"].append(
                "Равенства одного столбца можно рассмотреть в форме IN; это упрощение записи, а не доказанное ускорение."
            )
    f["evidence"].append(
        "UNION/UNION ALL не предлагаются: кратность и пересечения ветвей не доказаны."
    )
    return f


def _direct(select, sid, key, metadata, cte_names):
    sources = _sources(select)
    if len(sources) != 1 or not isinstance(key, exp.Column):
        return None
    source = sources[0]
    table = _table(source, sid, metadata, cte_names)
    if not table or key.table and key.table != source.alias_or_name:
        return None
    fields = {x["name"]: x for x in table.get("all_fields", [])}
    if key.name not in fields:
        return None
    return source, table, fields[key.name]


def _nonnull(select, key, resolved, metadata):
    if not resolved:
        return "unknown", "source_not_resolved"
    source, table, field = resolved
    fields = {x["name"]: x["type"] for x in table.get("all_fields", [])}
    where = select.args.get("where")
    if where:
        for predicate in _conjuncts(where.this):
            predicate = _unwrap(predicate)
            test = _unwrap(predicate.this) if isinstance(predicate, exp.Not) else predicate
            if isinstance(test, exp.Is) and (
                isinstance(predicate, exp.Not) != bool(test.args.get("negate"))
            ):
                if isinstance(test.expression, exp.Null):
                    try:
                        if _canonical(test.this, fields, {source.alias_or_name}) == _canonical(
                            key, fields, {source.alias_or_name}
                        ):
                            return "not_null", "where_is_not_null"
                    except (ValueError, KeyError):
                        pass
    if field.get("nullable") is not False:
        return "nullable_or_unproven", "column_allows_null_or_other_constraints_unchecked"
    constraints = table.get("diagnostics", {}).get("sources", {}).get("constraints", {})
    relevant = [
        c
        for c in constraints.get("data") or []
        if key.name in (c.get("columns") or []) and c.get("kind") in {"n", "p"}
    ]
    if any(c.get("kind") == "n" and c.get("is_validated") is not True for c in relevant):
        return "unknown", "not_null_constraint_not_validated"
    if any(c.get("is_validated") is True and c.get("is_enforced") is not False for c in relevant):
        return "not_null", "validated_catalog_constraint"
    try:
        version = int(metadata.get("version_number", 0))
    except (ValueError, TypeError):
        version = 0
    if 0 < version < 180000:
        return "not_null", "pre18_column_not_null_flag"
    return "unknown", "constraint_validation_not_established"


def _correlated(inner, outer_resolved, inner_resolved):
    if not outer_resolved or not inner_resolved:
        return None
    local = inner_resolved[0].alias_or_name
    external = outer_resolved[0].alias_or_name
    fields = {x["name"] for x in inner_resolved[1].get("all_fields", [])}
    for column in inner.find_all(exp.Column):
        if column.table and column.table != local:
            return True if column.table == external else None
        if not column.table and column.name not in fields:
            return None
    return False


def _subplan(inner_resolved, nodes):
    if not inner_resolved:
        return None
    source, table, _ = inner_resolved
    candidates = []
    for node in nodes:
        if not node.get("subplan_name"):
            continue
        subtree = [
            n
            for n in nodes
            if n["path"] == node["path"] or n["path"].startswith(node["path"] + ".Plans[")
        ]
        if any(
            n.get("schema") == table["schema"]
            and n.get("relation") == table["name"]
            and n.get("alias") == source.alias_or_name
            for n in subtree
        ):
            candidates.append(node)
    return candidates[0] if len(candidates) == 1 else None


def _not_in(outer, node, sid, tree, select_ids, metadata, plan, cte_names):
    f = _entry("AP05", node, sid)
    f.update(
        outer_nullability="unknown",
        inner_nullability="unknown",
        is_correlated=None,
        rewrite_equivalence="not_established",
        subplan_kind="unknown",
        subplan_mapping_status="unknown",
        inner_plan_rows=None,
        predicate_plan_roles=[],
    )
    in_ = node.this
    query = in_.args.get("query")
    inner = query.this if isinstance(query, exp.Subquery) else query
    key = _unwrap(in_.this)
    if (
        not isinstance(inner, exp.Select)
        or len(inner.expressions) != 1
        or not isinstance(key, exp.Column)
    ):
        f["evidence"].append(
            "Многоколоночное или непрямое сравнение требует отдельной проверки NULL."
        )
        return f
    inner_key = inner.expressions[0]
    if isinstance(inner_key, exp.Alias):
        inner_key = inner_key.this
    inner_key = _unwrap(inner_key)
    outer_resolved = _direct(outer, sid, key, metadata, cte_names)
    inner_resolved = _direct(inner, select_ids[id(inner)], inner_key, metadata, cte_names)
    f["outer_nullability"], outer_proof = _nonnull(outer, key, outer_resolved, metadata)
    f["inner_nullability"], inner_proof = _nonnull(inner, inner_key, inner_resolved, metadata)
    f["nullability_evidence"] = {"outer": outer_proof, "inner": inner_proof}
    if isinstance(inner_key, exp.Null):
        f["inner_nullability"] = "nullable_or_unproven"
    f["is_correlated"] = _correlated(inner, outer_resolved, inner_resolved)
    producer = _subplan(inner_resolved, plan["nodes"])
    if producer:
        f.update(
            subplan_mapping_status="inner_source_matched", inner_plan_rows=producer.get("plan_rows")
        )
        name = producer["subplan_name"]
        for n in plan["nodes"]:
            for role, condition in n.get("conditions", {}).items():
                if re.search(r"\b" + re.escape(name) + r"\b", condition):
                    f["predicate_plan_roles"].append(
                        dict(path=n["path"], role=role, condition=condition)
                    )
                    f["subplan_kind"] = (
                        "hashed SubPlan"
                        if re.search(r"\bhashed\s+" + re.escape(name) + r"\b", condition)
                        else "SubPlan"
                    )
    if f["outer_nullability"] != "not_null" or f["inner_nullability"] != "not_null":
        f["status"] = "null_semantics_requires_review"
        f["recommendations"].append(
            "Проверьте требуемую обработку NULL. NOT IN не заменяется на NOT EXISTS автоматически; пустой внутренний результат тоже важен."
        )
        return f
    f["status"] = "nonnull_keys_established"
    unsupported = (
        outer is not tree
        or len(select_ids) != 2
        or _clause(node) != "where"
        or outer.args.get("with_") is not None
        or f["is_correlated"] is not False
    )
    unsupported = unsupported or any(
        inner.args.get(k) is not None
        for k in ("limit", "offset", "group", "having", "distinct", "order", "with_")
    )
    unsupported = unsupported or len(list(inner.find_all(exp.Select))) != 1
    unsupported = unsupported or not any(x is node for x in _conjuncts(outer.args["where"].this))
    unsupported = unsupported or any(
        isinstance(x, exp.Func) and not isinstance(x, (exp.Connector, exp.Exists))
        for x in inner.walk()
    )
    unsupported = unsupported or any(
        isinstance(x, (exp.Add, exp.Sub, exp.Mul, exp.Div, exp.Mod)) for x in inner.walk()
    )
    for resolved in (outer_resolved, inner_resolved):
        data = (
            resolved[1].get("diagnostics", {}).get("sources", {}).get("relation", {}).get("data")
            or {}
        )
        unsupported = (
            unsupported
            or data.get("relation_kind") != "r"
            or data.get("row_security_enabled") is not False
        )
    types = [r[2]["type"] for r in (outer_resolved, inner_resolved)]
    builtin = {"integer", "bigint", "smallint", "text", "uuid", "boolean", "date"}
    unsupported = unsupported or types[0] != types[1] or types[0] not in builtin
    if unsupported:
        f["evidence"].append(
            "Отсутствие NULL установлено, но форма/типы/свойства запроса требуют отдельной проверки переписывания."
        )
        return f
    # Меняем внутренний псевдоним, чтобы ссылки не смешались с внешними.
    existing = {x.alias_or_name for x in tree.find_all(exp.Table)}
    alias = "ql_notin_1"
    while alias in existing:
        alias += "x"
    inner_copy = inner.copy()
    inner_source = _sources(inner_copy)[0]
    old = inner_source.alias_or_name
    inner_source.set("alias", exp.TableAlias(this=exp.to_identifier(alias, quoted=True)))
    for column in inner_copy.find_all(exp.Column):
        if not column.table or column.table == old:
            column.set("table", exp.to_identifier(alias, quoted=True))
            column.set("db", None)
            column.set("catalog", None)
    equality = exp.EQ(
        this=exp.column(inner_key.name, table=alias, quoted=True),
        expression=exp.column(key.name, table=outer_resolved[0].alias_or_name, quoted=True),
    )
    where = inner_copy.args.get("where")
    inner_copy.set("where", exp.Where(this=exp.and_(where.this, equality) if where else equality))
    inner_copy.set("expressions", [exp.Literal.number(1)])
    result = tree.copy()
    target = next(
        x
        for x in result.find_all(exp.Not)
        if isinstance(x.this, exp.In) and x.sql(dialect="postgres") == node.sql(dialect="postgres")
    )
    target.replace(exp.Not(this=exp.Exists(this=inner_copy)))
    f.update(
        status="not_exists_candidate",
        rewrite_equivalence="conditional_nonnull_plain_case",
        candidate_sql=result.sql(dialect="postgres"),
    )
    f["recommendations"].append(
        "Сравните кандидат NOT EXISTS при подтверждённых ненулевых ключах; проверьте операторы и результат. Ускорение не гарантировано."
    )
    return f


def analyze_predicates(sql, syntax, plan, sources):
    tree = normalize_identifiers(parse_query(sql), dialect="postgres")
    selects = list(tree.find_all(exp.Select))
    ids = {id(s): f"select_{i}" for i, s in enumerate(selects, 1)}
    source = sources.get("metadata", {})
    metadata = (source.get("data") or {}) if source.get("status") == "ok" else {}
    ctes = {c.alias_or_name for c in tree.find_all(exp.CTE)}
    findings = []
    for node in tree.walk():
        if isinstance(node, exp.Or):
            if _clause(node) not in {"where", "on"}:
                continue
            parent = node.parent
            while isinstance(parent, exp.Paren):
                parent = parent.parent
            if isinstance(parent, exp.Or):
                continue
            select = node.find_ancestor(exp.Select)
            if select is not None:
                findings.append(_or(select, node, ids[id(select)], tree, metadata, plan))
        elif (
            isinstance(node, exp.Not)
            and isinstance(node.this, exp.In)
            and node.this.args.get("query") is not None
        ):
            select = node.find_ancestor(exp.Select)
            if select is not None:
                findings.append(
                    _not_in(select, node, ids[id(select)], tree, ids, metadata, plan, ctes)
                )
    return {
        "schema_version": 1,
        "findings": findings,
        "limitations": [
            "Ключ индекса по каталогу не доказывает применимость; BitmapOr связывается с группой только по условиям ветвей.",
            "NULL-статистика не является гарантией NOT NULL. Кандидаты не выполняются автоматически.",
            "Сопоставление SubPlan по внутреннему источнику не является полным доказательством всех преобразований предиката.",
            "Ускорение и причина замедления без измерений не подтверждены.",
        ],
    }
