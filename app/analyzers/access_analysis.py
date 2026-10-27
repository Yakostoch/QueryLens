"""Проверка выражений WHERE и шаблонов LIKE по плану и индексам."""

from sqlglot import exp, parse_one
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.normalize_identifiers import normalize_identifiers

from app.analyzers.sql_features import parse_query

COMPARISONS = (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE, exp.Like, exp.ILike)
TEXT_TYPES = {"text", "character varying", "varchar"}
CHAR_TYPES = TEXT_TYPES | {"character", "char", "bpchar"}


def _parse(text):
    return normalize_identifiers(parse_one(text, read="postgres"), dialect="postgres")


def _unwrap(node):
    while isinstance(node, exp.Paren):
        node = node.this
    return node


def _type_name(value):
    return value.split("(")[0].strip().lower() if isinstance(value, str) else ""


def _canonical(node, fields, aliases, parent=None):
    """Убирает скобки, известные псевдонимы и безопасные приведения к тексту."""
    node = _unwrap(node)
    if isinstance(node, exp.Cast) and node.args["to"].sql().upper() == "TEXT":
        source = _unwrap(node.this)
        if isinstance(source, exp.Literal) and source.is_string:
            return _canonical(source, fields, aliases, parent)
        if isinstance(source, exp.Column):
            allowed = CHAR_TYPES if isinstance(parent, (exp.Lower, exp.Upper)) else TEXT_TYPES
            if _type_name(fields.get(source.name, "")) in allowed:
                return _canonical(source, fields, aliases, parent)
    if isinstance(node, exp.Column):
        if node.table and node.table not in aliases:
            raise ValueError("Unbound qualifier")
        if node.name not in fields:
            raise ValueError("Unbound column")
        return ("column", node.name)
    args = []
    for key, value in sorted(node.args.items()):
        if value is None or value is False:
            continue
        if isinstance(value, exp.Expression):
            value = _canonical(value, fields, aliases, node)
        elif isinstance(value, list):
            value = tuple(
                _canonical(x, fields, aliases, node) if isinstance(x, exp.Expression) else x
                for x in value
            )
        args.append((key, value))
    return (type(node).__name__, tuple(args))


def _conjuncts(node):
    node = _unwrap(node)
    if isinstance(node, exp.And):
        return _conjuncts(node.this) + _conjuncts(node.expression)
    return [node]


def _unknown(rule, match, reason):
    return {
        "rule_id": rule,
        "expression": match["expression"],
        "scope_id": match.get("scope_id"),
        "binding_status": "unknown",
        "reason": reason,
        "predicate_plan_roles": [],
        "plan_evidence": [],
        "index_candidates": [],
        "risk_assessment": "insufficient_data",
        "recommendations": [],
        "slowdown_confirmed": False,
    }


def _context(tree, metadata, nodes, target):
    if not isinstance(tree, exp.Select) or len(list(tree.find_all(exp.Select))) != 1:
        return None, "complex_query_scope"
    if tree.args.get("with_"):
        return None, "cte_or_join_scope"
    from_ = tree.args.get("from_")
    sources = ([from_.this] if from_ else []) + [j.this for j in tree.args.get("joins", [])]
    if not sources or any(
        not isinstance(s, exp.Table) or not isinstance(s.this, exp.Identifier) or s.catalog
        for s in sources
    ):
        return None, "indirect_source"
    bindings = metadata.get("source_bindings", [])
    resolved = {}
    for source in sources:
        alias = source.alias_or_name
        authoritative = [b for b in bindings if b["alias"] == alias and b["scope_id"] == "select_1"]
        if len(authoritative) == 1:
            key = (authoritative[0]["schema"], authoritative[0]["name"])
            candidates = [t for t in metadata.get("tables", []) if (t["schema"], t["name"]) == key]
        else:
            candidates = [
                t
                for t in metadata.get("tables", [])
                if t["name"] == source.name
                and (not source.db or t["schema"] == source.db)
                and "sql" in t.get("metadata_origins", ["sql"])
            ]
        if len(candidates) != 1 or alias in resolved:
            return None, "catalog_relation_ambiguous_or_unavailable"
        table = candidates[0]
        fields = {f["name"]: f["type"] for f in table.get("all_fields", table.get("fields", []))}
        resolved[alias] = (table, fields)
    owners = set()
    for column in target.find_all(exp.Column):
        if column.table:
            candidates = (
                [column.table]
                if column.table in resolved and column.name in resolved[column.table][1]
                else []
            )
        else:
            candidates = [a for a, (_, f) in resolved.items() if column.name in f]
        if len(candidates) != 1:
            return None, "column_source_ambiguous"
        owners.add(candidates[0])
    if len(owners) != 1:
        return None, "expression_spans_sources"
    alias = next(iter(owners))
    table, fields = resolved[alias]
    relation = table.get("diagnostics", {}).get("sources", {}).get("relation", {})
    if relation.get("status") != "ok" or (relation.get("data") or {}).get("relation_kind") not in {
        "r",
        "m",
    }:
        return None, "view_partition_or_unknown_relation"
    scans = [
        n
        for n in nodes
        if n.get("relation") == table["name"]
        and n.get("schema") == table["schema"]
        and n.get("alias") == alias
    ]
    if len(scans) != 1:
        return None, "scan_mapping_ambiguous_or_optimized_away"
    return (table, scans[0], fields, {alias}), None


def _same_expression(first, second, fields, aliases):
    try:
        return _canonical(first, fields, aliases) == _canonical(second, fields, aliases)
    except (ValueError, KeyError):
        return False


def _selected_index_nodes(scan, nodes):
    # Связываем Bitmap Index Scan только с его родительским чтением таблицы.
    return [
        n
        for n in nodes
        if n.get("index_name")
        and (n["path"] == scan["path"] or n["path"].startswith(scan["path"] + ".Plans["))
    ]


def _bind(predicate, scan, nodes, fields, aliases):
    expected = _canonical(predicate, fields, aliases)
    evidence = []
    relevant = [scan] + [n for n in _selected_index_nodes(scan, nodes) if n is not scan]
    for node in relevant:
        for role in ("Filter", "Index Cond", "Recheck Cond"):
            raw = node.get("conditions", {}).get(role)
            if not raw:
                continue
            try:
                leaves = _conjuncts(_parse(raw))
                matches = [
                    leaf
                    for leaf in leaves
                    if isinstance(leaf, COMPARISONS)
                    and _canonical(leaf, fields, aliases) == expected
                ]
            except (SqlglotError, ValueError, KeyError):
                continue
            if matches:
                evidence.append(
                    {
                        "path": node["path"],
                        "node_type": node["node_type"],
                        "role": role,
                        "condition": raw,
                        "index_name": node.get("index_name"),
                    }
                )
    return evidence


def _index_candidates(table, search, fields, aliases, rule, evidence):
    source = table.get("diagnostics", {}).get("sources", {}).get("indexes", {})
    if source.get("status") != "ok":
        return None, []
    expected = _canonical(search, fields, aliases)
    candidates = []
    for index in source.get("data") or []:
        for key in index.get("keys", []):
            if key.get("expression"):
                try:
                    actual = _canonical(_parse(key["expression"]), fields, {""})
                except (SqlglotError, ValueError, KeyError):
                    continue
            elif key.get("column_name"):
                actual = ("column", key["column_name"])
            else:
                continue
            if actual != expected:
                continue
            trigram = (index.get("access_method"), key.get("operator_class")) in {
                ("gin", "gin_trgm_ops"),
                ("gist", "gist_trgm_ops"),
            }
            observed = any(
                e["role"] == "Index Cond" and e["index_name"] == index["name"] for e in evidence
            )
            flags_ok = all(index.get(k) is True for k in ("is_valid", "is_ready", "is_live"))
            reasons = []
            if not flags_ok:
                reasons.append("index_flags_not_ready_or_unknown")
            if index.get("predicate"):
                reasons.append("partial_predicate_implication_not_proven")
            if key.get("position", 1) != 1 and index.get("access_method") == "btree":
                reasons.append("preceding_index_keys_not_checked")
            if rule == "AP03" and not trigram:
                reasons.append("not_a_trigram_search_index")
            status = "observed_predicate_access" if observed else "requires_planner_check"
            if not flags_ok and not observed:
                status = "unusable_or_unknown_flags"
            candidates.append(
                {
                    "index_name": index["name"],
                    "access_method": index.get("access_method"),
                    "key_position": key.get("position"),
                    "key_expression": key.get("expression"),
                    "is_trigram": trigram,
                    "status": status,
                    "unproven_conditions": reasons
                    + (
                        []
                        if observed
                        else ["operator_type_collation_and_cost_require_planner_check"]
                    ),
                }
            )
    return True, candidates


def _base_indexes(table, search):
    source = table.get("diagnostics", {}).get("sources", {}).get("indexes", {})
    if source.get("status") != "ok":
        return None, []
    columns = {c.name for c in search.find_all(exp.Column)}
    found = [
        {
            "index_name": i["name"],
            "column_name": k["column_name"],
            "key_position": k.get("position"),
            "applicability": "not_proven",
        }
        for i in source.get("data") or []
        for k in i.get("keys", [])
        if k.get("column_name") in columns
    ]
    return bool(found), found


def analyze_access(sql, syntax, plan, sources):
    """Собирает признаки для проверки условий доступа."""
    matches = [("AP02", x) for x in syntax["features"]["where_expressions"]]
    matches += [
        ("AP03", x)
        for x in syntax["features"]["pattern_predicates"]
        if x.get("clause") == "where" and x["has_leading_wildcard"] is not False
    ]
    metadata_source = sources.get("metadata", {})
    if metadata_source.get("status") != "ok":
        return {
            "schema_version": 1,
            "findings": [_unknown(r, m, "metadata_unavailable") for r, m in matches],
        }
    tree = normalize_identifiers(parse_query(sql), dialect="postgres")
    nodes = plan["nodes"]
    findings = []
    seen = set()
    for rule, match in matches:
        identity = (rule, match.get("scope_id"), match["expression"])
        if identity in seen:
            continue
        seen.add(identity)
        try:
            target = _parse(match["expression"])
            context, reason = _context(tree, metadata_source["data"], nodes, target)
        except (SqlglotError, ValueError, KeyError):
            context, reason = None, "expression_or_type_not_supported"
        if context is None:
            findings.append(_unknown(rule, match, reason))
            continue
        table, scan, fields, aliases = context
        if rule == "AP03" and match["has_leading_wildcard"] is None:
            findings.append(_unknown(rule, match, "pattern_or_escape_unknown"))
            continue
        try:
            target = _parse(match["expression"])
            where = tree.args.get("where")
            leaves = _conjuncts(where.this) if where else []
            if rule == "AP02":
                predicates = [
                    leaf
                    for leaf in leaves
                    if isinstance(leaf, COMPARISONS)
                    and any(
                        _same_expression(x, target, fields, aliases)
                        for x in leaf.walk()
                        if isinstance(x, type(target))
                    )
                ]
            else:
                predicates = [
                    leaf
                    for leaf in leaves
                    if isinstance(leaf, (exp.Like, exp.ILike))
                    and _same_expression(leaf, target, fields, aliases)
                ]
            if len(predicates) != 1:
                findings.append(
                    _unknown(rule, match, "predicate_context_not_supported_or_ambiguous")
                )
                continue
            predicate = predicates[0]
            if predicate.args.get("negate"):
                findings.append(_unknown(rule, match, "negated_search_not_supported"))
                continue
            search = predicate.this
            # Обратное сравнение оставляем без переписывания.
            if not list(search.find_all(exp.Column)):
                findings.append(_unknown(rule, match, "column_not_on_supported_search_side"))
                continue
            evidence = _bind(predicate, scan, nodes, fields, aliases)
            if not evidence:
                findings.append(_unknown(rule, match, "predicate_not_structurally_matched"))
                continue
            available, indexes = _index_candidates(table, search, fields, aliases, rule, evidence)
            base_exists, base_indexes = _base_indexes(table, search)
        except (SqlglotError, ValueError, KeyError):
            findings.append(_unknown(rule, match, "expression_or_type_not_supported"))
            continue
        rows = table["diagnostics"]["sources"]["relation"]["data"].get("estimated_rows")
        used = any(x["role"] == "Index Cond" for x in evidence)
        small = isinstance(rows, (int, float)) and rows <= 1000
        risk = (
            "predicate_index_access_observed"
            if used
            else ("small_relation_context" if small else "filter_work_requires_measurement")
        )
        recommendations = []
        if not used and not small:
            if available is None:
                recommendations.append(
                    "Получить метаданные индексов: их отсутствие не установлено."
                )
            elif rule == "AP03":
                if any(x["is_trigram"] for x in indexes):
                    recommendations.append(
                        "Проверить применимость и стоимость найденного триграммного индекса на этом выражении."
                    )
                else:
                    recommendations.append(
                        "Если поиск подстроки необходим и нагрузка это оправдывает, проверить вариант с pg_trgm на искомом выражении."
                    )
                if (
                    match.get("longest_literal_run_length") is not None
                    and match["longest_literal_run_length"] < 3
                ):
                    recommendations.append(
                        "Короткий шаблон может не дать полезных триграмм; ускорение индексом не гарантировано."
                    )
            elif indexes:
                recommendations.append(
                    "Проверить применимость найденных индексов на выражении; их наличие не гарантирует выбор планировщиком."
                )
            else:
                recommendations.append(
                    "Проверить семантически эквивалентное условие либо индекс на выражении с учётом типов, NULL и затрат записи."
                )
        findings.append(
            {
                "rule_id": rule,
                "expression": match["expression"],
                "scope_id": match.get("scope_id"),
                "predicate_expression": predicate.sql(dialect="postgres"),
                "binding_status": "matched",
                "relation": {"schema": table["schema"], "name": table["name"]},
                "predicate_plan_roles": sorted({e["role"] for e in evidence}),
                "plan_evidence": evidence,
                "node_metrics": {
                    k: scan.get(k)
                    for k in ("path", "node_type", "total_cost", "plan_rows", "plan_width")
                },
                "table_estimated_rows": rows,
                "small_relation_threshold": 1000,
                "index_metadata_status": "ok" if available else "unknown",
                "index_candidates": indexes,
                "base_column_index_exists": base_exists,
                "base_column_indexes": base_indexes,
                "risk_assessment": risk,
                "recommendations": recommendations,
                "slowdown_confirmed": False,
            }
        )
    return {
        "schema_version": 1,
        "findings": findings,
        "limitations": [
            "Поддержан один SELECT с прямыми источниками, включая JOIN, и сравнениями в AND.",
            "CTE, вложенные SELECT, OR, NOT, представления и неоднозначное сопоставление требуют дальнейшего анализа.",
            "1000 строк — эвристика контекста, не граница медленного запроса.",
            "Структурное совпадение ключа индекса не доказывает применимость; нужны оператор, типы, collation, частичный предикат и стоимость.",
            "Причина и фактическое замедление без измерения не подтверждены.",
        ],
    }
