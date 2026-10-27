"""Перевод измеренных фактов в читаемый отчёт; диагнозов по одним настройкам нет."""

from app.collectors.configuration import SETTINGS


def size(value):
    if value is None:
        return "недоступно"
    for factor, unit in ((2**30, "GiB"), (2**20, "MiB"), (2**10, "KiB")):
        if value >= factor:
            return f"{value / factor:.2f} {unit}"
    return f"{value:.0f} B"


def setting_value(row):
    raw, unit = row["setting"], row.get("unit") or ""
    try:
        factor = {"8kB": 8192, "kB": 1024, "MB": 1024**2, "GB": 1024**3}.get(unit)
        if factor:
            value = float(raw) * factor
            human = f"{value / 2**20:.2f} MiB" if value < 2**30 else size(value)
            return f"{human} (исходное: {raw} {unit})"
    except (ValueError, TypeError):
        pass
    return f"{raw} {unit}".strip()


def format_relation_details(table):
    """Добавляет доступные диагностические сведения в отчёт."""
    details = table.get("diagnostics")
    if not details:
        return []
    lines = ["", f"Метаданные: {table['schema']}.{table['name']}"]
    sources = details["sources"]
    for key, title in (
        ("indexes", "Индексы"),
        ("constraints", "Ограничения"),
        ("relation", "Размеры и статистика таблицы"),
        ("column_statistics", "Статистика столбцов"),
    ):
        entry = sources[key]
        if entry["status"] != "ok":
            reason = f" ({entry['error']})" if entry.get("error") else ""
            lines.append(f"{title}: сведения недоступны{reason}.")
            continue
        data = entry["data"]
        if key in {"indexes", "constraints"}:
            lines.append(f"{title}: {len(data)}.")
            for row in data:
                lines.append(row["definition"])
                if key == "indexes" and not row["is_valid"]:
                    lines.append("Индекс невалиден; не считать доступным путём поиска.")
        elif key == "relation":
            rows = data.get("estimated_rows")
            kind = data.get("relation_kind")
            kinds = {
                "r": "таблица",
                "p": "секционированная таблица",
                "v": "представление",
                "m": "материализованное представление",
                "f": "внешняя таблица",
            }
            lines.append(f"Тип объекта: {kinds.get(kind, 'неизвестно')}.")
            if kind == "v":
                lines.append(
                    "Обычное представление не хранит собственные строки; анализировать нужно его источники."
                )
                continue
            lines.append(
                f"Оценка строк: {rows if rows is not None else 'неизвестно'}; "
                f"полный размер объекта: {size(data.get('total_bytes'))}."
            )
            lines.append("Размер относится к самому объекту, без рекурсивной суммы секций.")
            lines.append(
                f"Последний ANALYZE: {data.get('last_analyze') or 'нет сведений'}; "
                f"autoanalyze: {data.get('last_autoanalyze') or 'нет сведений'}."
            )
        else:
            lines.append(
                f"Строк статистики столбцов: {len(data)}. "
                "Это оценки распределения, а не ограничения целостности."
            )
    return lines


def format_report(report):
    lines = ["Локальный отчёт сбора", "SQL пользователя не выполнялся."]
    for name, title in (("postgres", "PostgreSQL"), ("system", "Компьютер")):
        if name in report["errors"]:
            lines.append(
                f"{title}: сбор не завершён ({report['errors'][name]}). Проверьте зависимости, подключение и права."
            )
            if name in report.get("error_details", {}):
                lines.append(report["error_details"][name])
        elif report[name] is None and name != "system":
            lines.append(f"{title}: сбор выключен.")
    db = report["postgres"]
    if db:
        lines += ["", "POSTGRESQL"]
        if db.get("metadata_scope") == "query":
            lines += [
                "Структура по текущему запросу (снимок SQL на момент запуска):",
                db["metadata_sql"],
                "Показаны поля из SELECT, JOIN, фильтров и других частей запроса.",
                "Размер и накопленная статистика ниже относятся ко всей БД.",
            ]
        if db.get("metadata_collected", True):
            lines += [db["version"], f"Размер БД: {size(db.get('database_size_bytes'))}"]
        if db.get("configuration_collected", True):
            lines.append(
                "Настройки текущей диагностической сессии; не обязательно совпадают с сессией приложения."
            )
        for row in db["settings"]:
            lines += [
                f"\n{row['name']}: {setting_value(row)}",
                SETTINGS[row["name"]],
                f"Источник: {row['source']}; требуется перезапуск: {row['pending_restart']}",
            ]
        if db["settings_unavailable"]:
            lines.append("Недоступные настройки: " + ", ".join(db["settings_unavailable"]))
        if db.get("metadata_collected", True):
            lines += ["", "Накопленная статистика всей базы — не показатели одного SQL:"]
        for key, value in (db["database_statistics"] or {}).items():
            lines.append(f"{key}: {value}")
        for table in db.get("tables", []):
            lines.extend(format_relation_details(table))
    system = report["system"]
    if system:
        lines += [
            "",
            "КОМПЬЮТЕР",
            f"ОС: {system['os']} {system['os_release']}; {system['architecture']}",
            f"Ядра: физических {system['physical_cores'] if system['physical_cores'] is not None else 'недоступно'}, "
            f"логических {system['logical_cores'] if system['logical_cores'] is not None else 'недоступно'}; RAM {size(system['ram_total_bytes'])}",
            "Показатели ОС, где запущен QueryLens; Docker/VM могут иметь другие лимиты.",
            "Измерения не связаны с SQL; общая нагрузка включает другие процессы.",
        ]
        for i, sample in enumerate(system["samples"], 1):
            cpu = ", ".join(f"{x:.0f}%" for x in sample["cpu_percent_per_core"])
            lines += [
                f"\nИзмерение {i}, интервал {sample['interval_seconds']:.2f} с",
                f"CPU по ядрам: {cpu}",
                f"RAM доступно: {size(sample['ram_available_bytes'])}; занято {sample['ram_used_percent']:.1f}%",
                f"Swap занято: {size(sample['swap_used_bytes'])}",
            ]
            disks = sample["disk_io_rates"]
            if disks is None:
                lines.append("Дисковые счётчики недоступны на этой ОС/конфигурации.")
            else:
                for disk, rates in disks.items():
                    if "status" in rates:
                        lines.append(f"{disk}: устройство изменилось между измерениями")
                        continue
                    for key, title in (
                        ("read_bytes_per_second", "чтение"),
                        ("write_bytes_per_second", "запись"),
                    ):
                        rate = rates[key]
                        value = "недоступно" if rate is None else f"{rate / 2**20:.2f} MiB/с"
                        lines.append(f"{disk}: {title} {value}")
        lines += [
            "",
            "Зачем: CPU — конкуренция за вычисления; RAM — давление на память; I/O — активность накопителей.",
            "Занятый swap не доказывает активную подкачку. Скорость I/O не измеряет задержку или предельную скорость диска.",
        ]
    lines += [
        "",
        "Причина медленного SQL ещё не установлена: нужны его контекст, план и измерения.",
        "Отчёт хранится только в памяти этого окна, отдельно от истории SQL.",
    ]
    return "\n".join(lines)
