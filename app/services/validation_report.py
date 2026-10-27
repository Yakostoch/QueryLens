"""Сводка проверки по полному набору тестовых сценариев."""

from collections import Counter

RULE_NAMES = {
    "AP01": "SELECT *",
    "AP02": "Выражения в WHERE",
    "AP03": "LIKE/ILIKE с начальным %",
    "AP04": "OR",
    "AP05": "NOT IN",
    "AP06": "DISTINCT",
    "AP07": "Сортировки",
    "AP08": "OFFSET",
    "AP09": "HAVING",
    "AP10": "N+1: ограниченность данных",
    "AP11": "COUNT для существования",
    "AP12": "Декартово произведение",
    "AP13": "CTE",
    "AP14": "Оконные функции",
    "AP15": "JOIN",
}


def summarize_validation(cases, report):
    expected = {case["case_id"]: case for case in cases}
    if len(expected) != len(cases):
        raise ValueError("Повторяющиеся идентификаторы в спецификации.")
    rows = report.get("results", [])
    seen = Counter(row["case_id"] for row in rows)
    if set(seen) != set(expected) or any(count != 1 for count in seen.values()):
        raise ValueError("Отчёт не покрывает ровно весь набор запросов.")
    if report.get("fixture_rolled_back") is not True or not report.get("engine"):
        raise ValueError("Нет сведений о сервере или откате тестовых объектов.")
    groups = {
        rule: dict(name=name, passed=0, failed=0, syntax_only=0)
        for rule, name in RULE_NAMES.items()
    }
    failures, skipped = [], []
    for row in rows:
        case = expected[row["case_id"]]
        group = groups[case["primary_rule"]]
        if not case["server_executable"] and row["status"] == "syntax_only":
            skipped.append(case["case_id"])
            group["syntax_only"] += 1
        elif case["server_executable"] and row["status"] == "ok":
            group["passed"] += 1
        else:
            failures.append(case["case_id"])
            group["failed"] += 1
    return dict(
        total=len(rows),
        passed=sum(g["passed"] for g in groups.values()),
        failed=len(failures),
        syntax_only=len(skipped),
        failures=failures,
        skipped=skipped,
        groups=groups,
        engine=report["engine"],
        scope="authored_regression_suite_not_independent_diagnostic_accuracy",
    )


def validation_text(summary):
    lines = [
        "ПРОВЕРКА QUERYLENS НА POSTGRESQL",
        "Сервер: " + summary["engine"],
        f"Сценариев: {summary['total']}; прошли: {summary['passed']}; ошибок: {summary['failed']}; "
        f"без запуска SQL: {summary['syntax_only']}.",
        "",
        "По группам (пройдено / ошибок / без запуска):",
    ]
    for rule, group in summary["groups"].items():
        lines.append(
            f"{group['name']}: {group['passed']} / {group['failed']} / {group['syntax_only']}"
        )
    if summary["failures"]:
        lines.extend(["", "Нужно исправить: " + ", ".join(summary["failures"])])
    if summary["skipped"]:
        lines.extend(
            ["", "Без значений параметров SQL не запускался: " + ", ".join(summary["skipped"])]
        )
    lines.extend(
        [
            "",
            "Пройденный сценарий означает выполнение проверок тестового стенда, а не доказательство замедления.",
            "Это авторский регрессионный набор, а не независимая оценка точности диагностики.",
            "Для N+1 здесь проверяется корректный отказ от вывода по одному SQL; нужна трасса приложения.",
            "Работа со всеми версиями PostgreSQL, Windows и всеми возможными схемами этим запуском не подтверждается.",
        ]
    )
    return "\n".join(lines) + "\n"
