"""Перевод измеренных фактов в читаемый отчёт; диагнозов по одним настройкам нет."""
from app.collectors.configuration import SETTINGS


def size(value):
    return "недоступно" if value is None else f"{value / 2**30:.2f} GiB"


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


def format_report(report):
    lines = ["Локальный отчёт сбора", "SQL пользователя не выполнялся. LLM пока не подключена."]
    for name, title in (("postgres", "PostgreSQL"), ("system", "Компьютер")):
        if name in report["errors"]:
            lines.append(f"{title}: сбор не завершён ({report['errors'][name]}). Проверьте зависимости, подключение и права.")
        elif report[name] is None:
            lines.append(f"{title}: сбор выключен.")
    db = report["postgres"]
    if db:
        lines += ["", "POSTGRESQL", db["version"], f"Размер БД: {size(db.get('database_size_bytes'))}",
                  "Настройки текущей диагностической сессии; не обязательно совпадают с сессией приложения."]
        for row in db["settings"]:
            lines += [f"\n{row['name']}: {setting_value(row)}", SETTINGS[row["name"]],
                      f"Источник: {row['source']}; требуется перезапуск: {row['pending_restart']}"]
        if db["settings_unavailable"]:
            lines.append("Недоступные настройки: " + ", ".join(db["settings_unavailable"]))
        lines += ["", "Накопленная статистика всей базы — не показатели одного SQL:"]
        for key, value in (db["database_statistics"] or {}).items():
            lines.append(f"{key}: {value}")
    system = report["system"]
    if system:
        lines += ["", "КОМПЬЮТЕР", f"ОС: {system['os']} {system['os_release']}; {system['architecture']}",
                  f"Ядра: физических {system['physical_cores'] if system['physical_cores'] is not None else 'недоступно'}, "
                  f"логических {system['logical_cores'] if system['logical_cores'] is not None else 'недоступно'}; RAM {size(system['ram_total_bytes'])}",
                  "Показатели ОС, где запущен QueryLens; Docker/VM могут иметь другие лимиты.",
                  "Измерения не связаны с SQL; общая нагрузка включает другие процессы."]
        for i, sample in enumerate(system["samples"], 1):
            cpu = ", ".join(f"{x:.0f}%" for x in sample["cpu_percent_per_core"])
            lines += [f"\nИзмерение {i}, интервал {sample['interval_seconds']:.2f} с",
                      f"CPU по ядрам: {cpu}",
                      f"RAM доступно: {size(sample['ram_available_bytes'])}; занято {sample['ram_used_percent']:.1f}%",
                      f"Swap занято: {size(sample['swap_used_bytes'])}"]
            disks = sample["disk_io_rates"]
            if disks is None:
                lines.append("Дисковые счётчики недоступны на этой ОС/конфигурации.")
            else:
                for disk, rates in disks.items():
                    if "status" in rates:
                        lines.append(f"{disk}: устройство изменилось между измерениями")
                        continue
                    for key, title in (("read_bytes_per_second", "чтение"), ("write_bytes_per_second", "запись")):
                        rate = rates[key]
                        value = "недоступно" if rate is None else f"{rate / 2**20:.2f} MiB/с"
                        lines.append(f"{disk}: {title} {value}")
        lines += ["", "Зачем: CPU — конкуренция за вычисления; RAM — давление на память; I/O — активность накопителей.",
                  "Занятый swap не доказывает активную подкачку. Скорость I/O не измеряет задержку или предельную скорость диска."]
    lines += ["", "Причина медленного SQL ещё не установлена: нужны его контекст, план и измерения.",
              "Отчёт хранится только в памяти этого окна, отдельно от истории SQL."]
    return "\n".join(lines)
