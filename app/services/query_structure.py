"""Определение используемых полей по AST, с разрешением имён через каталог БД."""


class QueryStructureError(ValueError):
    pass


def query_structure(sql, resolve_table):
    """resolve_table(quoted_name) возвращает schema/name/oid/fields из PostgreSQL.

    SQL не выполняется. Поддерживаются SELECT, JOIN, CTE, подзапросы и UNION.
    Неразрешимые конструкции дают ошибку вместо предположений о полях.
    """
    import sqlglot
    from sqlglot import exp
    from sqlglot.optimizer.normalize_identifiers import normalize_identifiers
    from sqlglot.optimizer.qualify import qualify
    from sqlglot.optimizer.scope import traverse_scope
    from sqlglot.schema import MappingSchema

    try:
        expressions = [item for item in sqlglot.parse(sql, read="postgres") if item is not None]
        if len(expressions) != 1 or not isinstance(expressions[0], exp.Query):
            raise QueryStructureError("Выберите один SELECT-запрос для сбора структуры.")
        tree = normalize_identifiers(expressions[0], dialect="postgres")
        if any(join.args.get("method") == "NATURAL" for join in tree.find_all(exp.Join)):
            raise QueryStructureError("Для NATURAL JOIN выберите «Вся база данных» или укажите JOIN ON/USING.")
        if any(with_.args.get("recursive") for with_ in tree.find_all(exp.With)):
            raise QueryStructureError("Для рекурсивного CTE выберите «Вся база данных».")
        schema = MappingSchema(dialect="postgres", normalize=False)
        tables, cached = {}, {}
        for scope in traverse_scope(tree):
            for alias, (_, source) in scope.selected_sources.items():
                if not isinstance(source, exp.Table):
                    continue
                if not isinstance(source.this, exp.Identifier) or source.catalog:
                    raise QueryStructureError("Источник запроса не поддерживается. Выберите «Вся база данных».")
                name = ".".join('"' + part.name.replace('"', '""') + '"' for part in source.parts)
                if name not in cached:
                    cached[name] = resolve_table(name)
                table = cached[name]
                if table is None:
                    raise QueryStructureError("Таблица запроса не найдена в текущей БД/search_path.")
                key = (table["schema"], table["name"])
                tables[key] = table
                # Preserve the query's alias while binding its real catalog relation.
                source.set("this", exp.to_identifier(table["name"], quoted=True))
                source.set("db", exp.to_identifier(table["schema"], quoted=True))
                source.set("alias", exp.TableAlias(this=exp.to_identifier(alias, quoted=True)))
                schema.add_table(source, {field["name"]: "UNKNOWN" for field in table["fields"]}, normalize=False)
        tree = qualify(tree, dialect="postgres", schema=schema, infer_schema=False,
                       validate_qualify_columns=True)
        used = {key: set() for key in tables}
        for scope in traverse_scope(tree):
            for column in scope.columns:
                owner = scope
                while owner is not None:
                    source = owner.sources.get(column.table)
                    if source is not None:
                        if isinstance(source, exp.Table):
                            key = (source.db, source.name)
                            if key not in used:
                                raise QueryStructureError("Не удалось определить источник поля.")
                            used[key].add(column.name)
                        break
                    owner = owner.parent
                if owner is None:
                    raise QueryStructureError("Не удалось однозначно определить таблицу поля.")
        return [dict(table, fields=[field for field in table["fields"] if field["name"] in used[key]],
                     columns=len(used[key])) for key, table in tables.items()]
    except QueryStructureError:
        raise
    except sqlglot.errors.SqlglotError as error:
        raise QueryStructureError("Не удалось разобрать поля запроса. Проверьте SQL и псевдонимы; "
                                  "для неподдерживаемого синтаксиса выберите «Вся база данных».") from error
