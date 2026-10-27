"""Проверка набора SQL в отдельной тестовой базе PostgreSQL."""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from getpass import getpass, getuser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.services.validation_report import summarize_validation, validation_text


def validate_database_name(name):
    if (
        not name.startswith("querylens_test")
        or not name.isascii()
        or not all(c.isalnum() or c == "_" for c in name)
        or len(name) > 63
    ):
        raise ValueError(
            "Тестовая база должна называться querylens_test… и содержать только латиницу, цифры и _ (до 63 символов)."
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Создать отдельную тестовую БД при необходимости и выполнить тесты.",
    )
    parser.add_argument("--database", default="querylens_test_validation")
    parser.add_argument(
        "--maintenance-db",
        default="demo",
        help="Существующая БД для подключения при создании тестовой БД.",
    )
    parser.add_argument("--user")
    parser.add_argument("--port", type=int, default=5432)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument(
        "--without-trigram",
        action="store_true",
        help="Проверка без pg_trgm; результат отмечается как неполный по индексу триграмм.",
    )
    args = parser.parse_args()
    if not args.execute:
        parser.error(
            "Добавьте --execute: запуск создаёт отдельную тестовую БД при необходимости и выполняет SQL."
        )
    try:
        validate_database_name(args.database)
    except ValueError as error:
        parser.error(str(error))
    user = args.user or input(f"Пользователь PostgreSQL [{getuser()}]: ").strip() or getuser()
    password = os.environ.get("PGPASSWORD")
    if password is None:
        password = getpass("Пароль PostgreSQL (Enter — .pgpass/без пароля): ") or None
    folder = args.output_dir or Path.home() / "Desktop" / (
        "QueryLens_validation_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    )
    folder.mkdir(parents=True, exist_ok=False)
    import psycopg
    from psycopg import sql

    parameters = dict(
        host="127.0.0.1",
        port=args.port,
        user=user,
        password=password,
        connect_timeout=5,
        options="-c statement_timeout=5000",
    )
    try:
        with psycopg.connect(
            dbname=args.maintenance_db, autocommit=True, **parameters
        ) as connection:
            exists = connection.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s", (args.database,)
            ).fetchone()
            if not exists:
                connection.execute(
                    sql.SQL("CREATE DATABASE {}").format(sql.Identifier(args.database))
                )
                print("Создана отдельная тестовая база: " + args.database, flush=True)
        print(
            "Проверяю SQL-набор. База demo не изменяется. Дождитесь итогового сообщения…",
            flush=True,
        )
        environment = dict(os.environ)
        if password is not None:
            environment["PGPASSWORD"] = password
        command = [
            sys.executable,
            str(ROOT / "scripts/run_sql_lab.py"),
            "--database",
            args.database,
            "--user",
            user,
            "--port",
            str(args.port),
            "--output",
            str(folder / "sql_results.json"),
        ]
        if not args.without_trigram:
            command.append("--with-trigram")
        completed = subprocess.run(
            command, cwd=ROOT, env=environment, capture_output=True, text=True
        )
        if not (folder / "sql_results.json").is_file():
            # Не сохраняем текст ошибки драйвера с параметрами подключения.
            raise RuntimeError("Лабораторный запуск не завершён")
        report = json.loads((folder / "sql_results.json").read_text(encoding="utf-8"))
        cases = json.loads((ROOT / "tests/fixtures/sql_cases.json").read_text(encoding="utf-8"))
        summary = summarize_validation(cases, report)
        with psycopg.connect(dbname=args.database, **parameters) as connection:
            remaining = connection.execute(
                "SELECT nspname FROM pg_namespace WHERE nspname = ANY(%s)",
                (["querylens_lab", "QL Tenant A", "QL Tenant B"],),
            ).fetchall()
            if remaining:
                raise RuntimeError("Найдены тестовые схемы после завершения запуска")
        summary["fixture_schemas_absent_after_run"] = True
        summary["trigram_requested"] = not args.without_trigram
        text = validation_text(summary)
        if args.without_trigram:
            text += "Запуск без pg_trgm: поведение с триграммным индексом на этом сервере не проверено.\n"
        text += "Тестовые схемы после запуска отсутствуют. Пустая тестовая БД сохранена для повторного запуска.\n"
        (folder / "SUMMARY.txt").write_text(text, encoding="utf-8")
        (folder / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(text, flush=True)
        print("Откройте:", folder / "SUMMARY.txt")
        return 1 if summary["failed"] or completed.returncode else 0
    except Exception as error:
        message = (
            "Проверка не завершена (" + type(error).__name__ + "). Успех не подтверждён.\n"
            "Проверьте роль/пароль, доступ к demo, право CREATEDB, отсутствие схем querylens_lab, "
            "QL Tenant A и QL Tenant B в тестовой БД, доступность pg_trgm и права на его установку.\n"
            "Существующие схемы и базы программа не удаляет. Если pg_trgm недоступен, "
            "можно отдельно повторить с --without-trigram; это будет неполная проверка триграммного доступа.\n"
        )
        (folder / "SUMMARY.txt").write_text(message, encoding="utf-8")
        print(message, file=sys.stderr)
        print("Отчёт:", folder / "SUMMARY.txt", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
