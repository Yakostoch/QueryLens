"""Два сравнения запросов без изменения данных базы."""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from getpass import getpass, getuser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.database_service import DatabaseService
from app.services.experiment_service import concise_experiment, run_experiment

CASES = [
    (
        "distinct",
        "Лишний DISTINCT по первичному ключу",
        "SELECT DISTINCT flight_id FROM bookings.flights",
        "SELECT flight_id FROM bookings.flights",
    ),
    (
        "exists",
        "Проверка наличия билетов",
        "SELECT COUNT(*) > 0 AS has_tickets FROM bookings.ticket_flights WHERE flight_id = 1",
        "SELECT EXISTS(SELECT 1 FROM bookings.ticket_flights WHERE flight_id = 1) AS has_tickets",
    ),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute", action="store_true", help="Выполнить проверки и замеры двух пар SELECT."
    )
    parser.add_argument("--database")
    parser.add_argument("--user")
    parser.add_argument("--port", type=int, default=5432)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--original", type=Path, help="Свой исходный SQL вместо примеров bookings.")
    parser.add_argument(
        "--candidate", type=Path, help="Свой проверенный вариант; порядок строк не сравнивается."
    )
    args = parser.parse_args()
    if not args.execute:
        parser.error("Нужно явно указать --execute: оба варианта SQL будут выполнены.")
    if bool(args.original) != bool(args.candidate):
        parser.error("Для своей БД укажите одновременно --original и --candidate.")
    database = args.database or input("Название базы [demo]: ").strip() or "demo"
    user = args.user or input(f"Пользователь PostgreSQL [{getuser()}]: ").strip() or getuser()
    password = os.environ.get("PGPASSWORD")
    if password is None:
        password = getpass("Пароль PostgreSQL (Enter — использовать .pgpass/без пароля): ") or None
    parameters = dict(
        host="127.0.0.1",
        port=args.port,
        database=database,
        user=user,
        password=password,
        sslmode="prefer",
    )
    folder = args.output or Path.home() / "Desktop" / (
        "QueryLens_results_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    )
    folder.mkdir(parents=True, exist_ok=False)
    cases = CASES
    if args.original:
        cases = [
            (
                "custom",
                "Сравнение своих SQL",
                args.original.read_text(encoding="utf-8"),
                args.candidate.read_text(encoding="utf-8"),
            )
        ]
    summaries = [
        "ПРОВЕРКА QUERYLENS",
        "Повторы: 5 на вариант, порядок чередуется. Лимит SQL: 5 секунд.",
        "",
    ]
    failures = 0
    for key, title, original, candidate in cases:
        print(title + " — проверяю результаты и измеряю…", flush=True)
        try:
            result = run_experiment(DatabaseService(), parameters, original, candidate)
            result["recorded_at"] = datetime.now(timezone.utc).isoformat()
            (folder / (key + ".json")).write_text(
                json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            summary = concise_experiment(result)
            if result["result_check"]["status"] != "equal_on_snapshot":
                failures += 1
        except Exception as error:
            # Не включаем параметры подключения и текст ошибки сервера в отчёт.
            failures += 1
            summary = (
                "Проверка не завершена ("
                + type(error).__name__
                + "). Проверьте подключение, права, "
                "наличие таблиц и лимит времени. Завершённых замеров для этого примера нет."
            )
        print(summary, flush=True)
        summaries.extend([title, summary, ""])
    summaries.extend(
        [
            "Равенство на текущих данных не доказывает эквивалентность на любых данных.",
            "Это два контрольных примера, а не проверка всех правил приложения.",
            "Время относится к выполнению на сервере под EXPLAIN ANALYZE.",
        ]
    )
    (folder / "RESULTS.txt").write_text("\n".join(summaries) + "\n", encoding="utf-8")
    print("Готово. Откройте:", folder / "RESULTS.txt")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
