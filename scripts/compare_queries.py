"""Сравнение двух запросов из выбранных файлов."""

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.services.database_service import DatabaseService
from app.services.query_comparison import compare_queries


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("--host", choices=("127.0.0.1", "::1", "localhost"), default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5432)
    parser.add_argument("--original", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--repeats", type=int, choices=range(1, 6), default=3)
    parser.add_argument("--timeout-ms", type=int, default=5000)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Explicitly execute both SELECT queries in read-only transactions.",
    )
    args = parser.parse_args()
    if not args.execute:
        parser.error("Добавьте --execute только после проверки обоих SQL: запросы будут выполнены.")
    if not 100 <= args.timeout_ms <= 30000:
        parser.error("Тайм-аут должен быть 100–30000 мс.")
    if args.output.resolve() in {args.original.resolve(), args.candidate.resolve()}:
        parser.error("Файл отчёта не должен совпадать с файлом SQL.")
    parameters = dict(
        host=args.host,
        port=args.port,
        database=args.database,
        user=args.user,
        password=os.environ.get("PGPASSWORD"),
        sslmode="prefer",
    )
    try:
        result = compare_queries(
            DatabaseService(),
            parameters,
            args.original.read_text(encoding="utf-8"),
            args.candidate.read_text(encoding="utf-8"),
            repeats=args.repeats,
            timeout_ms=args.timeout_ms,
        )
    except Exception as error:
        print("Сравнение не завершено: " + type(error).__name__, file=sys.stderr)
        return 1
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("Медиана исходного SQL, мс:", result["original_median_ms"])
    print("Медиана кандидата, мс:", result["candidate_median_ms"])
    print("Равенство результатов не проверено. Отчёт:", args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
