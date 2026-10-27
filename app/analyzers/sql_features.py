"""Извлечение конструкций SQL для последующих проверок."""

import sqlglot
from sqlglot import exp
from sqlglot.errors import ErrorLevel, SqlglotError
from sqlglot.optimizer.scope import traverse_scope


class SQLInputError(ValueError):
    """Ошибка с текстом, пригодным для показа пользователю."""


def parse_query(sql):
    if not isinstance(sql, str) or not sql.strip():
        raise SQLInputError("Введите один SELECT-запрос.")
    try:
        statements = [
            node
            for node in sqlglot.parse(sql, read="postgres", error_level=ErrorLevel.RAISE)
            if node is not None and not isinstance(node, exp.Semicolon)
        ]
    except SqlglotError as error:
        raise SQLInputError("Не удалось разобрать SQL. Проверьте синтаксис PostgreSQL.") from error
    if len(statements) != 1:
        raise SQLInputError("Выберите ровно один SELECT-запрос.")
    tree = statements[0]
    if not isinstance(tree, (exp.Select, exp.SetOperation, exp.Subquery)):
        raise SQLInputError("На этом этапе поддерживаются SELECT и их объединения.")
    # SQL проверяет PostgreSQL; права чтения и тайм-аут задаются в соединении.
    forbidden = {
        "Insert",
        "Update",
        "Delete",
        "Merge",
        "Create",
        "Drop",
        "Alter",
        "Command",
        "Copy",
        "Into",
        "Lock",
        "Transaction",
        "Commit",
        "Rollback",
    }
    if any(type(node).__name__ in forbidden for node in tree.walk()):
        raise SQLInputError(
            "Пока поддерживается SELECT без изменения данных, INTO и блокировки строк."
        )
    return tree


def _sql(node):
    return node.sql(dialect="postgres") if node is not None else None


def _select_owner(node):
    while node is not None:
        if isinstance(node, exp.Select):
            return node
        node = node.parent
    return None


def _clause(node):
    parent = node.parent
    while parent is not None:
        if isinstance(parent, exp.Where):
            return "where"
        if isinstance(parent, exp.Having):
            return "having"
        if isinstance(parent, exp.Join):
            return "on"
        if isinstance(parent, exp.Select):
            return "select"
        parent = parent.parent
    return "unknown"


def _inside_window(node):
    parent = node.parent
    while parent is not None and not isinstance(parent, exp.Select):
        if isinstance(parent, exp.Window):
            return True
        parent = parent.parent
    return False


def _pattern(like):
    value = like.expression
    # Не вычисляем функции SQL и не подставляем неизвестные параметры.
    pattern = value.this if isinstance(value, exp.Literal) and value.is_string else None
    escape = "\\"
    if isinstance(like.parent, exp.Escape) and like.parent.this is like:
        escape_node = like.parent.expression
        if isinstance(escape_node, exp.Literal) and escape_node.is_string:
            escape = escape_node.this
        else:
            escape = None
    valid_escape = escape is not None and len(escape) <= 1
    leading = None
    longest = None
    if pattern is not None and valid_escape:
        leading = pattern.startswith("%") and escape != "%"
        current = longest = 0
        index = 0
        while index < len(pattern):
            char = pattern[index]
            if escape and char == escape:
                index += 1
                if index == len(pattern):
                    # Ошибочный шаблон проверит PostgreSQL.
                    leading = longest = None
                    break
                current += 1
            elif char in "%_":
                longest = max(longest, current)
                current = 0
            else:
                current += 1
            longest = max(longest, current)
            index += 1
    return {
        "operator": "ILIKE" if isinstance(like, exp.ILike) else "LIKE",
        "search_expression": _sql(like.this),
        "pattern_expression": _sql(value),
        "pattern_value": pattern,
        "escape_character": escape,
        "has_leading_wildcard": leading,
        "longest_literal_run_length": longest,
    }


def extract_sql_features(sql):
    """Сохраняет исходные конструкции SQL до разрешения имён."""
    tree = parse_query(sql)
    selects = list(tree.find_all(exp.Select))
    scope_ids = {id(node): f"select_{number}" for number, node in enumerate(selects, 1)}
    features = {
        key: []
        for key in (
            "select_stars",
            "where_expressions",
            "pattern_predicates",
            "or_groups",
            "not_in_subqueries",
            "distinct",
            "orders",
            "offsets",
            "having",
            "counts",
            "joins",
            "ctes",
            "windows",
        )
    }
    scopes = []
    arithmetic_types = (exp.Add, exp.Sub, exp.Mul, exp.Div, exp.Mod)

    def record(key, node, **values):
        owner = _select_owner(node)
        features[key].append(
            {"scope_id": scope_ids.get(id(owner)), "expression": _sql(node), **values}
        )

    for select in selects:
        sid = scope_ids[id(select)]
        scopes.append({"scope_id": sid, "projections": [_sql(x) for x in select.expressions]})
        for projection in select.expressions:
            target = projection.this if isinstance(projection, exp.Alias) else projection
            if isinstance(target, exp.Star) or (
                isinstance(target, exp.Column) and isinstance(target.this, exp.Star)
            ):
                record(
                    "select_stars",
                    target,
                    qualifier=target.table if isinstance(target, exp.Column) else None,
                )
        where = select.args.get("where")
        if where is not None:
            for node in where.walk():
                if _select_owner(node) is not select:
                    continue
                if isinstance(node, exp.Connector):
                    continue
                if not isinstance(node, (exp.Func,) + arithmetic_types):
                    continue
                columns = [
                    column
                    for column in node.find_all(exp.Column)
                    if _select_owner(column) is select
                ]
                if columns:
                    kind = (
                        "cast"
                        if isinstance(node, exp.Cast)
                        else ("function" if isinstance(node, exp.Func) else "arithmetic")
                    )
                    record(
                        "where_expressions",
                        node,
                        expression_kind=kind,
                        referenced_columns=sorted({_sql(column) for column in columns}),
                    )
        distinct = select.args.get("distinct")
        if distinct is not None:
            record(
                "distinct",
                distinct,
                distinct_kind="DISTINCT ON" if distinct.args.get("on") is not None else "DISTINCT",
            )
        having = select.args.get("having")
        if having is not None:
            record("having", having)

    for node in tree.walk():
        if isinstance(node, (exp.Like, exp.ILike)):
            record("pattern_predicates", node, clause=_clause(node), **_pattern(node))
        elif isinstance(node, exp.Or):
            parent = node.parent
            while isinstance(parent, exp.Paren):
                parent = parent.parent
            if not isinstance(parent, exp.Or):
                record("or_groups", node, clause=_clause(node))
        elif isinstance(node, exp.Not) and isinstance(node.this, exp.In):
            if node.this.args.get("query") is not None:
                record("not_in_subqueries", node)
        elif isinstance(node, exp.Order):
            # Сортировка окна может отличаться от порядка результата.
            purpose = (
                "window"
                if isinstance(node.parent, exp.Window)
                else ("query" if isinstance(node.parent, exp.Query) else "expression")
            )
            record("orders", node, purpose=purpose)
        elif isinstance(node, exp.Offset):
            literal = node.expression
            value = None
            if isinstance(literal, exp.Literal) and not literal.is_string:
                try:
                    value = int(literal.this)
                except ValueError:
                    pass
            record("offsets", node, offset_value=value)
        elif isinstance(node, exp.Count):
            record(
                "counts",
                node,
                is_count_star=isinstance(node.this, exp.Star),
                count_scope="window" if _inside_window(node) else "aggregate",
                usage="unknown",
            )
        elif isinstance(node, exp.Join):
            record(
                "joins",
                node,
                has_on=node.args.get("on") is not None,
                has_using=bool(node.args.get("using")),
                method=node.args.get("method"),
                kind=node.args.get("kind"),
                side=node.args.get("side"),
                cartesian_assessment="not_assessed",
            )
        elif isinstance(node, exp.CTE):
            with_node = node.parent
            record(
                "ctes",
                node,
                name=node.alias_or_name,
                recursive=bool(with_node.args.get("recursive")),
                materialized=node.args.get("materialized"),
            )
        elif isinstance(node, exp.Window):
            record("windows", node)

    sources = []
    try:
        for scope in traverse_scope(tree):
            for alias, (_, source) in scope.selected_sources.items():
                sources.append(
                    {
                        "scope_id": scope_ids.get(id(scope.expression)),
                        "alias": alias,
                        "source_kind": (
                            "relation_reference"
                            if isinstance(source, exp.Table)
                            else "derived_or_cte"
                        ),
                        "reference": _sql(source) if isinstance(source, exp.Table) else None,
                        "catalog_resolved": False,
                    }
                )
    except SqlglotError as error:
        raise SQLInputError("Не удалось определить источники SQL. Проверьте псевдонимы.") from error
    from app.analyzers.source_graph import analyze_source_graph

    return {
        "schema_version": 1,
        "dialect": "postgres",
        "statement_type": type(tree).__name__,
        "source_connection_graph": analyze_source_graph(tree),
        "scopes": scopes,
        "sources": sources,
        "columns": [
            {"scope_id": scope_ids.get(id(_select_owner(node))), "reference": _sql(node)}
            for node in tree.find_all(exp.Column)
        ],
        "features": features,
        "limitations": [
            "Обнаружены синтаксические конструкции; замедление не установлено.",
            "Ссылки на таблицы и столбцы ещё не разрешены через каталог PostgreSQL.",
            "Связь конструкций с узлами плана и применимость индексов ещё не проверяются.",
            "Для N+1 нужна трасса операции приложения; одного SQL недостаточно.",
            "SQLGlot не заменяет проверку синтаксиса и семантики сервером PostgreSQL.",
        ],
    }
