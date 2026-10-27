"""Запуск QueryLens на macOS, Windows и Linux."""

import argparse
import os
import re
import subprocess
import sys
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEPENDENCIES = {
    "PySide6": ((6, 8), (7, 0)),
    "psycopg": ((3, 2), (4, 0)),
    "psutil": ((7, 2), (8, 0)),
    "sqlglot": ((30, 21), (31, 0)),
}


def child_environment(environment):
    values = dict(environment)
    for key in (
        "QT_PLUGIN_PATH",
        "QT_QPA_PLATFORM_PLUGIN_PATH",
        "QT_QPA_PLATFORM",
        "DYLD_LIBRARY_PATH",
        "DYLD_FRAMEWORK_PATH",
    ):
        values.pop(key, None)
    return values


def dependency_issues():
    issues = []
    if sys.version_info < (3, 10):
        issues.append("Нужен Python 3.10 или новее; для воспроизведения рекомендуется Python 3.12.")
    for name, (minimum, maximum) in DEPENDENCIES.items():
        try:
            version = metadata.version(name)
        except metadata.PackageNotFoundError:
            issues.append(name + ": не установлен")
            continue
        match = re.fullmatch(r"(\d+)\.(\d+)(?:\.\d+)*(?:\+[^ ]+)?", version)
        if not match or not minimum <= tuple(map(int, match.group(1, 2))) < maximum:
            issues.append(f"{name}: версия {version} вне проверяемого диапазона requirements.txt")
    return issues


def main():
    parser = argparse.ArgumentParser(description="Запуск QueryLens и локальная проверка окружения.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Проверить зависимости и Qt без открытия рабочего окна и подключения к БД.",
    )
    args = parser.parse_args()
    issues = dependency_issues()
    if issues:
        print("Окружение не готово:\n" + "\n".join(issues), file=sys.stderr)
        print(
            "Установите зависимости в выбранное окружение: python -m pip install -r requirements.txt",
            file=sys.stderr,
        )
        return 1
    environment = child_environment(os.environ)
    if args.check:
        environment["QT_QPA_PLATFORM"] = "offscreen"
        code = (
            "from PySide6 import QtWidgets; import psycopg, psutil, sqlglot; import main; "
            "app = QtWidgets.QApplication([]); print('Импорт приложения и Qt offscreen: OK')"
        )
        try:
            result = subprocess.run(
                [sys.executable, "-c", code],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                timeout=20,
            )
        except (OSError, subprocess.TimeoutExpired):
            print("Проверка импорта или Qt не завершена.", file=sys.stderr)
            return 1
        if result.returncode:
            print(
                "Не удалось импортировать приложение или запустить Qt. Проверьте установку зависимостей.",
                file=sys.stderr,
            )
            print(result.stderr[-3000:], file=sys.stderr)
            return 1
        print("Окружение готово. Python:", sys.version.split()[0])
        for name in DEPENDENCIES:
            print(name + ": " + metadata.version(name))
        print(result.stdout.strip())
        print(
            "База данных не подключалась. Отображение рабочего окна на этой ОС ещё нужно проверить."
        )
        return 0
    return subprocess.call([sys.executable, str(ROOT / "main.py")], cwd=ROOT, env=environment)


if __name__ == "__main__":
    raise SystemExit(main())
