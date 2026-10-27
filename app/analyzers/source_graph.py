"""Связи между источниками данных в SQL."""

from sqlglot import exp
from sqlglot.optimizer.normalize_identifiers import normalize_identifiers

from app.analyzers.sql_features import parse_query

COMPARISONS = (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE)


def _unparen(node):
    while isinstance(node, exp.Paren):
        node = node.this
    return node


def _edges(node, aliases):
    node = _unparen(node)
    if isinstance(node, (exp.And, exp.Or)):
        a, ua = _edges(node.this, aliases)
        b, ub = _edges(node.expression, aliases)
        return (a | b, ua or ub) if isinstance(node, exp.And) else (a & b, ua or ub or a != b)
    if isinstance(node, exp.Not) or any(isinstance(x, exp.Query) for x in node.walk()):
        return set(), True
    columns = list(node.find_all(exp.Column))
    if any(not c.table or c.table not in aliases for c in columns):
        return set(), True
    if not isinstance(node, COMPARISONS):
        return set(), len({c.table for c in columns}) > 1
    left = {c.table for c in node.this.find_all(exp.Column)}
    right = {c.table for c in node.expression.find_all(exp.Column)}
    if len(left) == len(right) == 1 and left != right:
        return {tuple(sorted((next(iter(left)), next(iter(right)))))}, False
    return set(), len(left | right) > 1


def analyze_source_graph(sql):
    tree = normalize_identifiers(
        parse_query(sql) if isinstance(sql, str) else sql.copy(), dialect="postgres"
    )
    reports = []
    for number, select in enumerate(tree.find_all(exp.Select), 1):
        source = select.args.get("from_")
        sources = ([source.this] if source else []) + [j.this for j in select.args.get("joins", [])]
        if len(sources) < 2:
            continue
        aliases = [s.alias_or_name for s in sources]
        report = {
            "scope_id": f"select_{number}",
            "sources": aliases,
            "edges": [],
            "status": "unknown",
            "components": [],
            "reason": None,
        }
        if any(
            not isinstance(s, exp.Table) or not isinstance(s.this, exp.Identifier) for s in sources
        ) or len(set(aliases)) != len(aliases):
            report["reason"] = "indirect_or_duplicate_source"
            reports.append(report)
            continue
        cte_names = {c.alias_or_name for c in tree.find_all(exp.CTE)}
        if any(not s.db and s.name in cte_names for s in sources):
            report["reason"] = "cte_lineage_unknown"
            reports.append(report)
            continue
        edges = set()
        uncertain = False
        outer = False
        for join in select.args.get("joins", []):
            outer = outer or join.side.upper() in {"LEFT", "RIGHT", "FULL"}
            if join.args.get("method") == "NATURAL":
                uncertain = True
            if join.args.get("using"):
                if len(sources) == 2:
                    edges.add(tuple(sorted(aliases)))
                else:
                    uncertain = True
            on = join.args.get("on")
            if on is not None:
                found, unknown = _edges(on, set(aliases))
                edges |= found
                uncertain |= unknown
        where = select.args.get("where")
        if where is not None:
            found, unknown = _edges(where.this, set(aliases))
            edges |= found
            uncertain |= unknown
        components = [{a} for a in aliases]
        for a, b in edges:
            first = next(c for c in components if a in c)
            second = next(c for c in components if b in c)
            if first is not second:
                first.update(second)
                components.remove(second)
        report.update(
            edges=[list(e) for e in sorted(edges)], components=[sorted(c) for c in components]
        )
        if len(components) == 1:
            report.update(status="connected", reason="connecting_predicates_present")
        elif uncertain or outer:
            report["reason"] = "connection_semantics_unknown"
        else:
            report.update(status="disconnected", reason="independent_source_groups")
        reports.append(report)
    return {
        "schema_version": 1,
        "scopes": reports,
        "limitations": [
            "Связность не доказывает эффективный JOIN и полноту составного ключа.",
            "Несвязанные источники могут быть намеренными; добавлять условие автоматически нельзя.",
        ],
    }
