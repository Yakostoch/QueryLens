"""Структурированное представление метаданных одного объекта PostgreSQL."""

from app.services.report_formatter import size
from app.ui.widgets.analysis_report import ReportSection, ReportSql, add_metrics

FIELDS = {
    "columns": ("Columns / Столбцы", "Число столбцов в выбранной области сбора."),
    "indexes": (
        "Indexes / Индексы",
        "Число индексов объекта по каталогу; не гарантирует их валидность и применимость к SQL.",
    ),
    "name": ("Name / Название", "Имя объекта PostgreSQL."),
    "schema": ("Schema / Схема", "Схема, содержащая объект."),
    "oid": ("OID / Идентификатор", "Внутренний идентификатор объекта в текущей базе."),
    "type": ("Type / Тип данных", "Тип столбца по каталогу PostgreSQL."),
    "nullable": (
        "Nullable / Допускает NULL",
        "Да — в столбце разрешён NULL; нет — действует NOT NULL.",
    ),
    "relation_kind": (
        "Relation Kind / Тип объекта",
        "Тип объекта: таблица, представление, секционированная таблица и другие варианты.",
    ),
    "is_partition": ("Is Partition / Секция", "Является ли объект секцией другой таблицы."),
    "row_security_enabled": (
        "Row Security / Защита строк",
        "Включены ли политики защиты строк RLS.",
    ),
    "row_security_forced": (
        "Force Row Security / Принудительная RLS",
        "Применяются ли политики защиты строк также к владельцу таблицы.",
    ),
    "has_inheritance_children": (
        "Inheritance Children / Дочерние объекты",
        "Есть ли дочерние объекты наследования или секции; их размеры здесь не суммируются.",
    ),
    "estimated_rows": (
        "Estimated Rows / Оценка строк",
        "Оценка reltuples, а не точный подсчёт строк. Неизвестная оценка не заменяется нулём.",
    ),
    "estimated_pages": (
        "Estimated Pages / Оценка страниц",
        "Оценка числа страниц объекта из relpages; зависит от актуальности статистики.",
    ),
    "main_fork_bytes": (
        "Main Fork Size / Основные данные",
        "Размер основного слоя объекта; не включает все служебные слои, TOAST и индексы.",
    ),
    "table_bytes": (
        "Table Size / Таблица со служебными данными",
        "Размер таблицы со служебными слоями и TOAST, без индексов таблицы.",
    ),
    "indexes_bytes": (
        "Indexes Size / Размер индексов",
        "Суммарный размер индексов самого объекта.",
    ),
    "total_bytes": (
        "Total Size / Полный размер объекта",
        "Размер объекта с индексами и TOAST. Без рекурсивной суммы секций и дочерних таблиц.",
    ),
    "options": (
        "Storage Options / Параметры хранения",
        "Параметры reloptions, заданные для объекта.",
    ),
    "parent_oids": (
        "Parent OIDs / Родительские объекты",
        "Идентификаторы родителей в наследовании или секционировании.",
    ),
    "last_analyze": (
        "Last ANALYZE / Последний ручной ANALYZE",
        "Время последнего ручного обновления статистики; отсутствие данных не доказывает отсутствие обслуживания.",
    ),
    "last_autoanalyze": (
        "Last Autoanalyze / Последний автоанализ",
        "Время последнего автоматического обновления статистики таблицы.",
    ),
    "n_mod_since_analyze": (
        "Modifications Since ANALYZE / Изменения после анализа",
        "Оценка числа изменённых строк после последнего обновления статистики.",
    ),
    "n_live_tup": ("Live Tuples / Живые строки", "Оценка числа живых строк; это не точный COUNT."),
    "n_dead_tup": (
        "Dead Tuples / Мёртвые строки",
        "Оценка числа мёртвых версий строк, а не доказанная причина медленного SQL.",
    ),
    "access_method": (
        "Access Method / Метод доступа",
        "Метод индекса, например btree, gin или gist.",
    ),
    "predicate": (
        "Predicate / Условие частичного индекса",
        "Условие, ограничивающее строки частичного индекса. Отсутствие условия означает обычный полный индекс.",
    ),
    "is_unique": ("Unique / Уникальный", "Проверяет ли индекс уникальность ключа."),
    "is_primary": ("Primary / Первичный ключ", "Обслуживает ли индекс первичный ключ."),
    "is_valid": ("Valid / Валидный", "Невалидный индекс нельзя считать доступным путём поиска."),
    "is_ready": ("Ready / Готов к обновлению", "Готов ли индекс принимать изменения данных."),
    "is_live": ("Live / Активный", "Индекс не находится в процессе удаления."),
    "is_immediate": (
        "Immediate / Немедленная проверка",
        "Проверяется ли уникальность немедленно, а не с отсрочкой.",
    ),
    "nulls_not_distinct": (
        "NULLs Not Distinct / NULL считаются равными",
        "Равнозначность NULL при проверке уникальности; отсутствие данных может зависеть от версии сервера.",
    ),
    "included_columns": (
        "INCLUDE / Включённые столбцы",
        "Дополнительные столбцы индекса, которые не являются поисковыми ключами.",
    ),
    "position": ("Position / Позиция ключа", "Порядок элемента в определении индекса."),
    "column_name": (
        "Column / Столбец",
        "Имя столбца; у индексного выражения вместо имени может быть NULL.",
    ),
    "expression": (
        "Expression / Выражение",
        "Индексируемое выражение, если ключ не является простой ссылкой на столбец.",
    ),
    "operator_class_schema": (
        "Operator Class Schema / Схема класса операторов",
        "Схема класса операторов индексного ключа.",
    ),
    "operator_class": (
        "Operator Class / Класс операторов",
        "Правила сравнения и поиска для ключа. Наличие класса само по себе не доказывает применимость к SQL.",
    ),
    "collation_schema": (
        "Collation Schema / Схема сопоставления",
        "Схема правила сортировки и сопоставления строк.",
    ),
    "collation": (
        "Collation / Сопоставление",
        "Правило сортировки и сопоставления строк индексного ключа.",
    ),
    "direction": (
        "Direction / Направление",
        "Порядок ASC или DESC для поддерживающего его индексного ключа.",
    ),
    "nulls_first": ("NULLs First / NULL в начале", "Положение NULL в порядке индексного ключа."),
    "kind": (
        "Constraint Type / Тип ограничения",
        "Вид ограничения: первичный ключ, внешний ключ, уникальность, CHECK и другие.",
    ),
    "is_validated": ("Validated / Проверено", "Проверено ли ограничение на существующих строках."),
    "is_deferrable": (
        "Deferrable / Возможна отсрочка",
        "Разрешено ли откладывать проверку ограничения.",
    ),
    "initially_deferred": (
        "Initially Deferred / Изначально отложено",
        "Откладывается ли проверка по умолчанию до завершения транзакции.",
    ),
    "is_enforced": (
        "Enforced / Применяется",
        "Обеспечивается ли выполнение ограничения; недоступное свойство не считается False.",
    ),
    "supporting_index_oid": (
        "Supporting Index OID / Индекс ограничения",
        "Идентификатор индекса, обслуживающего ограничение.",
    ),
    "referenced_relation_oid": (
        "Referenced Relation OID / Связанный объект",
        "Идентификатор таблицы, на которую ссылается внешний ключ.",
    ),
    "referenced_schema": (
        "Referenced Schema / Связанная схема",
        "Схема таблицы, на которую ссылается внешний ключ.",
    ),
    "referenced_relation": (
        "Referenced Relation / Связанная таблица",
        "Таблица, на которую ссылается внешний ключ.",
    ),
    "referenced_columns": (
        "Referenced Columns / Связанные столбцы",
        "Столбцы внешнего ключа на стороне связанной таблицы, в исходном порядке.",
    ),
    "inherited": (
        "Inherited / С учётом наследования",
        "Относится ли статистика также к дочерним объектам.",
    ),
    "null_frac": (
        "NULL Fraction / Доля NULL",
        "Доля NULL от 0 до 1 по статистической выборке; это не ограничение NOT NULL.",
    ),
    "avg_width": (
        "Average Width / Средняя ширина, байт",
        "Оценочная средняя ширина значения столбца.",
    ),
    "n_distinct": (
        "Distinct Values / Различные значения",
        "Положительное число — оценка количества различных значений; отрицательное — доля от числа строк со знаком минус. Например, −1 означает примерно одно различное значение на строку, но не доказывает уникальность.",
    ),
    "correlation": (
        "Correlation / Корреляция",
        "Корреляция порядка значений с физическим порядком строк от −1 до 1. Это статистическая оценка.",
    ),
}
KINDS = {
    "r": "Таблица",
    "p": "Секционированная таблица",
    "v": "Представление",
    "m": "Материализованное представление",
    "f": "Внешняя таблица",
}
CONSTRAINTS = {
    "p": "Первичный ключ",
    "f": "Внешний ключ",
    "u": "Уникальность",
    "c": "CHECK",
    "x": "Исключение",
    "n": "NOT NULL",
    "t": "Триггер",
}


def metadata_values(section, data, *, definitions=None):
    values = {}
    for key, value in data.items():
        if key.endswith("_bytes") and isinstance(value, (int, float)):
            value = f"{size(value)} ({value:,} байт)".replace(",", " ")
        elif isinstance(value, list) and all(not isinstance(x, (list, dict)) for x in value):
            value = ", ".join(str(x) for x in value) if value else "Нет элементов"
        values[key] = value
    add_metrics(section, values, definitions={**FIELDS, **(definitions or {})})


def child_section(parent, title, description, *, expanded=False):
    child = ReportSection(title, expanded=expanded)
    child.add_help(title, description)
    parent.content.addWidget(child)
    return child


def show_relation(parent, table):
    section = child_section(
        parent,
        f"{table['schema']}.{table['name']}",
        "Метаданные одного объекта PostgreSQL. Источники собираются независимо; ошибка одного не означает отсутствие индексов или ограничений.",
    )

    def load(expanded):
        if expanded:
            section.toggle.toggled.disconnect(load)
            populate_relation(section, table)

    section.toggle.toggled.connect(load)
    return section


def populate_relation(section, table):
    sources = (table.get("diagnostics") or {}).get("sources", {})
    for key, title, description in (
        (
            "relation",
            "Свойства, размеры и статистика",
            "Данные pg_class и pg_stat_all_tables. Размеры относятся к самому объекту без рекурсивной суммы секций.",
        ),
        (
            "indexes",
            "Индексы",
            "Определения и свойства индексов по pg_index. Проверяйте валидность и применимость к условию запроса.",
        ),
        (
            "constraints",
            "Ограничения",
            "Ограничения целостности из pg_constraint: ключи, проверки, связи и правила отсрочки.",
        ),
        (
            "column_statistics",
            "Статистика столбцов",
            "Оценки pg_stats, а не ограничения целостности. NULL и отрицательные n_distinct имеют самостоятельный смысл.",
        ),
    ):
        group = child_section(section, title, description)
        entry = sources.get(key) or {}
        if entry.get("status") != "ok" or entry.get("data") is None:
            error = f" ({entry['error']})" if entry.get("error") else ""
            group.add_text(f"Сведения недоступны{error}.")
            continue
        data = entry["data"]
        if key == "relation":
            show_properties(group, data)
        elif key == "column_statistics":
            for row in data:
                column = child_section(
                    group,
                    row["column_name"] + (" · с наследованием" if row.get("inherited") else ""),
                    "Распределение значений столбца по последней доступной статистике.",
                )
                metadata_values(column, {k: v for k, v in row.items() if k != "column_name"})
        else:
            for row in data:
                item = child_section(group, row.get("name", "Без имени"), description)
                values = {k: v for k, v in row.items() if k not in {"name", "definition", "keys"}}
                if key == "constraints" and "kind" in values:
                    values["kind"] = CONSTRAINTS.get(values["kind"], values["kind"])
                definitions = {
                    "columns": (
                        "Columns / Столбцы ограничения",
                        "Столбцы ограничения в исходном порядке.",
                    )
                }
                metadata_values(item, values, definitions=definitions)
                if key == "indexes" and row.get("is_valid") is False:
                    item.add_text("Индекс невалиден; его нельзя считать доступным путём поиска.")
                if row.get("definition"):
                    definition = child_section(
                        item,
                        "Определение SQL",
                        "Определение по каталогу PostgreSQL; SQL автоматически не выполняется.",
                    )
                    definition.content.addWidget(ReportSql(row["definition"]))
                if row.get("keys"):
                    keys = child_section(
                        item, "Ключи индекса", "Элементы ключа в порядке определения индекса."
                    )
                    for index, element in enumerate(row["keys"], 1):
                        element_section = child_section(
                            keys,
                            f"Ключ {index}",
                            "Столбец или выражение и правила поиска для этой позиции ключа.",
                        )
                        metadata_values(element_section, element)
        if isinstance(data, list) and not data:
            group.add_text("Сведения получены: объектов не найдено.")
    columns = child_section(
        section, "Столбцы", "Поля объекта, полученные в выбранной области сбора структуры."
    )
    for field in table.get("fields", []):
        item = child_section(
            columns, field["name"], "Тип и допустимость NULL по каталогу столбцов."
        )
        metadata_values(item, {k: v for k, v in field.items() if k != "name"})
    if not table.get("fields"):
        columns.add_text("Сведения о столбцах не получены в этой области сбора.")


def show_properties(section, data):
    kind = data.get("relation_kind")
    properties = {
        k: v
        for k, v in data.items()
        if k
        not in {
            "main_fork_bytes",
            "table_bytes",
            "indexes_bytes",
            "total_bytes",
            "estimated_rows",
            "estimated_pages",
            "last_analyze",
            "last_autoanalyze",
            "n_mod_since_analyze",
            "n_live_tup",
            "n_dead_tup",
        }
    }
    if "relation_kind" in properties:
        properties["relation_kind"] = KINDS.get(kind, kind)
    metadata_values(section, properties)
    if kind == "v":
        section.add_text(
            "Обычное представление не хранит собственные строки. Размеры и статистику нужно смотреть у его источников."
        )
        return
    for title, names, description in (
        (
            "Размеры объекта",
            ("main_fork_bytes", "table_bytes", "indexes_bytes", "total_bytes"),
            "Размеры самого объекта; дочерние секции рекурсивно не суммируются.",
        ),
        (
            "Строки и актуальность статистики",
            (
                "estimated_rows",
                "estimated_pages",
                "last_analyze",
                "last_autoanalyze",
                "n_mod_since_analyze",
                "n_live_tup",
                "n_dead_tup",
            ),
            "Оценки строк и сведения об обновлении статистики, а не точные результаты COUNT.",
        ),
    ):
        child = child_section(section, title, description)
        values = {k: data[k] for k in names if k in data}
        if values:
            metadata_values(child, values)
        else:
            child.add_text("Нет данных.")
