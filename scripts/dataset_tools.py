"""Импорт сохранённых примеров и разделение проверенной разметки."""

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.dataset_service import (
    DatasetError,
    apply_review,
    import_records,
    read_records,
    review_sheet,
    training_splits,
    write_records,
)
from app.services.evaluation_service import evaluate_records, format_evaluation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    importer = commands.add_parser("import", help="Read TXT/JSON reports or SQLite history")
    importer.add_argument("sources", nargs="+", type=Path)
    importer.add_argument("--output", required=True, type=Path)
    sheet = commands.add_parser("review-sheet", help="Create a compact editable annotation sheet")
    sheet.add_argument("records", type=Path)
    sheet.add_argument("--output", required=True, type=Path)
    apply = commands.add_parser(
        "apply-review", help="Attach manual annotations to diagnostic records"
    )
    apply.add_argument("records", type=Path)
    apply.add_argument("sheet", type=Path)
    apply.add_argument("--output", required=True, type=Path)
    splitter = commands.add_parser(
        "split", help="Only manually approved records enter training files"
    )
    splitter.add_argument("reviewed", type=Path)
    splitter.add_argument("--output-dir", required=True, type=Path)
    splitter.add_argument("--seed", default="querylens-v1")
    evaluator = commands.add_parser(
        "evaluate", help="Evaluate explicitly assessed rules against approved labels"
    )
    evaluator.add_argument("reviewed", type=Path)
    evaluator.add_argument("--output", required=True, type=Path)
    evaluator.add_argument("--manifest", type=Path)
    evaluator.add_argument("--split", choices=("train", "validation", "test"), default="test")
    args = parser.parse_args()
    try:
        if args.command == "import":
            records, errors = import_records(args.sources)
            if not records:
                raise DatasetError("Пригодных записей нет; выходной файл не создан.")
            write_records(args.output, records)
            print(
                json.dumps(
                    dict(
                        imported=len(records),
                        skipped=len(errors),
                        errors=errors,
                        output=str(args.output),
                    ),
                    ensure_ascii=False,
                    indent=2,
                )
            )
        elif args.command == "review-sheet":
            data = review_sheet(read_records(args.records))
            # Не перезаписываем существующий файл разметки.
            content = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)
            with args.output.open("x", encoding="utf-8") as stream:
                stream.write(content + "\n")
            print(f"Лист для ручной разметки: {args.output}")
        elif args.command == "apply-review":
            records = apply_review(
                read_records(args.records), json.loads(args.sheet.read_text(encoding="utf-8-sig"))
            )
            write_records(args.output, records)
            print(f"Разметка присоединена: {args.output}")
        elif args.command == "evaluate":
            manifest = (
                json.loads(args.manifest.read_text(encoding="utf-8")) if args.manifest else None
            )
            result = evaluate_records(
                read_records(args.reviewed), manifest=manifest, split=args.split
            )
            write_records(args.output, [result])
            print(format_evaluation(result))
        else:
            splits, manifest = training_splits(read_records(args.reviewed), args.seed)
            if args.output_dir.exists():
                raise DatasetError("Каталог результата уже существует; укажите новый.")
            # Сохраняем весь проверенный набор целиком.
            with tempfile.TemporaryDirectory(
                dir=args.output_dir.parent, prefix=".querylens-split-"
            ) as tmp:
                folder = Path(tmp) / "ready"
                folder.mkdir()
                for name, rows in splits.items():
                    write_records(folder / (name + ".jsonl"), rows)
                (folder / "manifest.json").write_text(
                    json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                os.rename(folder, args.output_dir)
            print(
                json.dumps(
                    {key: value for key, value in manifest.items() if key != "assignments"},
                    ensure_ascii=False,
                    indent=2,
                )
            )
    except (DatasetError, OSError, ValueError) as error:
        print(f"Экспорт не выполнен: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
