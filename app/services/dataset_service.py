"""Сохранение диагностических примеров и проверенной разметки."""

import hashlib
import json
import os
import sqlite3
import tempfile
from contextlib import closing
from copy import deepcopy
from pathlib import Path

import sqlglot
from sqlglot import exp

from app.services.analysis_presentation import MARKER, resolve, summarize

INPUT_KEYS = (
    "sql",
    "consumer_context",
    "mode",
    "explain",
    "sources",
    "sql_features",
    "plan_summary",
    "plan_features",
    "runtime_analysis",
    "operation_trace",
)
ANALYSIS_KEYS = (
    "triage",
    "access_analysis",
    "volume_analysis",
    "predicate_analysis",
    "operation_analysis",
    "diagnostic_tree",
)
RULES = {f"AP{i:02}" for i in range(1, 16)}


class DatasetError(ValueError):
    pass


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def fingerprint(sql):
    """Объединяет варианты параметров, сохраняя имена объектов."""
    tree = sqlglot.parse_one(sql, read="postgres")

    def normalize(node):
        if isinstance(node, exp.Literal):
            return exp.Literal.string("__value__") if node.is_string else exp.Literal.number("0")
        if isinstance(node, exp.Boolean):
            return exp.Boolean(this=True)
        return node

    canonical = tree.transform(normalize).sql(dialect="postgres", comments=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def parse_report(text):
    _, separator, raw = text.rpartition(MARKER)
    try:
        payload = json.loads(raw if separator else text)
    except (ValueError, TypeError) as error:
        raise DatasetError("Нужен отчёт с диагностическим JSON или сам JSON-пакет.") from error
    if (
        not isinstance(payload, dict)
        or not isinstance(payload.get("sql"), str)
        or not payload["sql"].strip()
    ):
        raise DatasetError("В диагностическом пакете нет SQL.")
    if not isinstance(payload.get("diagnostic_tree"), dict) or not isinstance(
        payload.get("explain"), dict
    ):
        raise DatasetError("В пакете нет плана или дерева диагностики. Выполните анализ заново.")
    return payload


def make_record(payload):
    """Выбирает диагностические блоки без параметров подключения.

    Значения в SQL и статистике сохраняются без обезличивания."""
    payload = parse_report(_json(payload))
    try:
        short = summarize(payload)["brief"]
        sql_fingerprint = fingerprint(payload["sql"])
    except (
        ValueError,
        TypeError,
        KeyError,
        IndexError,
        AttributeError,
        sqlglot.errors.SqlglotError,
    ) as error:
        raise DatasetError("Диагностический пакет не удалось проверить.") from error
    inputs = {key: deepcopy(payload[key]) for key in INPUT_KEYS if key in payload}
    automatic = {key: deepcopy(payload[key]) for key in ANALYSIS_KEYS if key in payload}
    case_id = hashlib.sha256(
        _json(dict(input=inputs, automatic_analysis=automatic)).encode()
    ).hexdigest()
    return dict(
        record_schema_version=1,
        case_id=case_id,
        query_fingerprint=sql_fingerprint,
        provenance=dict(
            analysis_id=payload.get("analysis_id"),
            mode=payload.get("mode"),
            collected_at=payload.get("finished_at"),
            origin="querylens_diagnostic_bundle",
        ),
        input=inputs,
        automatic_analysis=automatic,
        draft_answer=short,
        review=dict(
            status="pending",
            reviewed_by="",
            family_id="",
            verdict="",
            labels=[],
            assessed_rules=[],
            evidence_refs=[],
            answer="",
            notes="",
        ),
    )


def write_records(path, records, *, overwrite=False):
    """Проверяет весь набор перед записью и сохраняет его целиком."""
    path = Path(path)
    content = "".join(_json(record) + "\n" for record in records)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=".querylens-", delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(content)
        if overwrite:
            os.replace(temporary, path)
        else:
            # Публикуем файл без перезаписи, в том числе при одновременной записи.
            os.link(temporary, path)
        return len(content.encode("utf-8"))
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def import_records(paths):
    """Импортирует сохранённые отчёты без запуска сборщиков."""
    records, errors, seen = [], [], set()
    for item in paths:
        path = Path(item)
        if path.suffix.lower() in {".sqlite", ".sqlite3", ".db"}:
            try:
                with closing(
                    sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
                ) as connection:
                    texts = list(connection.execute("SELECT id, result FROM queries ORDER BY id"))
            except sqlite3.Error:
                errors.append(dict(source=str(path), error="Не удалось прочитать историю."))
                continue
        else:
            try:
                texts = [(None, path.read_text(encoding="utf-8-sig"))]
            except (OSError, UnicodeError):
                errors.append(dict(source=str(path), error="Не удалось прочитать файл."))
                continue
        for entry_id, text in texts:
            try:
                record = make_record(parse_report(text))
            except (DatasetError, ValueError, TypeError):
                errors.append(
                    dict(
                        source=str(path),
                        entry_id=entry_id,
                        error="Нет пригодного диагностического пакета.",
                    )
                )
                continue
            if record["case_id"] not in seen:
                seen.add(record["case_id"])
                records.append(record)
    return records, errors


def read_records(path):
    result = []
    for number, line in enumerate(Path(path).read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
            if not isinstance(record, dict) or record.get("record_schema_version") != 1:
                raise ValueError()
            if (
                not isinstance(record.get("input"), dict)
                or not isinstance(record.get("automatic_analysis"), dict)
                or not isinstance(record.get("review"), dict)
                or not isinstance(record.get("case_id"), str)
                or not isinstance(record.get("query_fingerprint"), str)
            ):
                raise ValueError()
            result.append(record)
        except (ValueError, TypeError) as error:
            raise DatasetError(f"Некорректная запись JSONL: строка {number}.") from error
    return result


def validate_review(record):
    """Проверяет заполнение ответа, автора и семьи перед одобрением."""
    review = record.get("review") or {}
    if not isinstance(review, dict):
        raise DatasetError("review должен быть объектом.")
    if review.get("status") not in {"pending", "approved", "rejected"}:
        raise DatasetError("Допустимы статусы pending, approved, rejected.")
    if review.get("status") != "approved":
        return False
    for field in ("reviewed_by", "family_id", "answer"):
        if not isinstance(review.get(field), str) or not review[field].strip():
            raise DatasetError(f"Для approved нужно заполнить review.{field}.")
    if review.get("verdict") not in {"problem", "potential", "no_issue_found", "insufficient_data"}:
        raise DatasetError("Нужен допустимый review.verdict.")
    labels = review.get("labels")
    if not isinstance(labels, list) or any(
        not isinstance(label, str) or label not in RULES for label in labels
    ):
        raise DatasetError("review.labels должен содержать только идентификаторы AP01–AP15.")
    assessed = review.get("assessed_rules", [])
    if not isinstance(assessed, list) or any(
        not isinstance(rule, str) or rule not in RULES for rule in assessed
    ):
        raise DatasetError("review.assessed_rules должен содержать только AP01–AP15.")
    if assessed and not set(labels).issubset(assessed):
        raise DatasetError("Каждую выбранную метку включите в assessed_rules.")
    if review["verdict"] == "no_issue_found" and labels:
        raise DatasetError("Для no_issue_found список замечаний labels должен быть пуст.")
    if review["verdict"] in {"problem", "potential"} and not labels:
        raise DatasetError("Для замечания нужны review.labels.")
    if (
        "AP10" in labels
        and review["verdict"] in {"problem", "potential"}
        and not record.get("input", {}).get("operation_trace")
    ):
        raise DatasetError("Для разметки N+1 нужна трасса операции приложения.")
    refs = review.get("evidence_refs")
    if not isinstance(refs, list) or (review["verdict"] in {"problem", "potential"} and not refs):
        raise DatasetError("Для замечания нужны ссылки review.evidence_refs на доказательства.")
    for ref in refs:
        if not isinstance(ref, str) or not ref.startswith(("/input/", "/automatic_analysis/")):
            raise DatasetError("Ссылки должны вести в input или automatic_analysis.")
        try:
            resolve(record, ref)
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise DatasetError("Ссылка на доказательство не разрешается.") from error
    return True


def validate_record(record):
    """Проверяет целостность исходных данных независимо от разметки."""
    try:
        expected = make_record({**record["input"], **record["automatic_analysis"]})
    except (KeyError, TypeError, ValueError) as error:
        raise DatasetError("Нарушена структура записи.") from error
    if expected["case_id"] != record.get("case_id") or expected["query_fingerprint"] != record.get(
        "query_fingerprint"
    ):
        raise DatasetError("Исходные данные изменены после экспорта; сформируйте запись заново.")


def review_sheet(records):
    """Создаёт отдельную запись для редактируемой разметки."""
    items = []
    for record in records:
        suggestions = []
        payload = {**record["input"], **record["automatic_analysis"]}
        for finding in summarize(payload)["findings"]:
            ref = finding["source_ref"]
            block = ref.split("/")[1]
            prefix = "/automatic_analysis" if block in ANALYSIS_KEYS else "/input"
            suggestions.append(prefix + ref)
        items.append(
            dict(
                case_id=record["case_id"],
                sql=record["input"]["sql"],
                draft_answer=record["draft_answer"],
                evidence_suggestions=suggestions,
                review=deepcopy(record["review"]),
            )
        )
    return dict(sheet_schema_version=1, items=items)


def apply_review(records, sheet):
    if (
        not isinstance(sheet, dict)
        or sheet.get("sheet_schema_version") != 1
        or not isinstance(sheet.get("items"), list)
    ):
        raise DatasetError("Неверный формат листа разметки.")
    result = deepcopy(records)
    by_id = {record["case_id"]: record for record in result}
    if len(by_id) != len(result):
        raise DatasetError("Повторный case_id в исходных записях.")
    seen = set()
    for item in sheet["items"]:
        if not isinstance(item, dict) or not isinstance(item.get("case_id"), str):
            raise DatasetError("В листе нужен case_id.")
        key = item["case_id"]
        if key not in by_id or key in seen:
            raise DatasetError("В листе неизвестный или повторный case_id.")
        seen.add(key)
        review = item.get("review")
        if not isinstance(review, dict) or review.get("status") not in {
            "pending",
            "approved",
            "rejected",
        }:
            raise DatasetError("Допустимы статусы pending, approved, rejected.")
        by_id[key]["review"] = deepcopy(review)
        validate_review(by_id[key])
    return result


def training_splits(records, seed="querylens-v1"):
    """Помещает связанные примеры в одну выборку по семье или шаблону SQL."""
    approved, seen = [], set()
    for record in records:
        if not validate_review(record):
            continue
        validate_record(record)
        if record["case_id"] in seen:
            raise DatasetError("Повторный case_id: устраните дубликаты перед разделением.")
        seen.add(record["case_id"])
        approved.append(record)
    if not approved:
        raise DatasetError("Нет записей с заполненной ручной разметкой approved.")
    parents = list(range(len(approved)))

    def find(i):
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i

    keys = {}
    for i, record in enumerate(approved):
        for key in (
            ("family", record["review"]["family_id"].strip()),
            ("sql", record["query_fingerprint"]),
        ):
            if key in keys:
                parents[find(i)] = find(keys[key])
            keys[key] = i
    components = {}
    for i, record in enumerate(approved):
        components.setdefault(find(i), []).append(record)
    splits = dict(train=[], validation=[], test=[])
    assignments = []
    for members in components.values():
        families = sorted({r["review"]["family_id"].strip() for r in members})
        group_id = hashlib.sha256(_json(families).encode()).hexdigest()
        bucket = int(hashlib.sha256((seed + ":" + group_id).encode()).hexdigest()[:8], 16) / 2**32
        split = "train" if bucket < 0.8 else "validation" if bucket < 0.9 else "test"
        for record in sorted(members, key=lambda r: r["case_id"]):
            # Ответы, заметки и метки проверяющего не включаем во входные данные.
            prompt = dict(input=record["input"], diagnostic_evidence=record["automatic_analysis"])
            splits[split].append(
                dict(
                    case_id=record["case_id"],
                    group_id=group_id,
                    messages=[
                        dict(
                            role="system",
                            content="Объясни результат проверки PostgreSQL кратко: где замечание, почему и что проверить. Не выдумывай факты и не объявляй гипотезу доказанной причиной.",
                        ),
                        dict(role="user", content=_json(prompt)),
                        dict(role="assistant", content=record["review"]["answer"].strip()),
                    ],
                )
            )
            assignments.append(dict(case_id=record["case_id"], group_id=group_id, split=split))
    for items in splits.values():
        items.sort(key=lambda r: r["case_id"])
    return splits, dict(
        schema_version=1,
        seed=seed,
        approved=len(approved),
        excluded=len(records) - len(approved),
        groups=len(components),
        proportions="hash-based 80/10/10; small datasets may have empty splits",
        counts={key: len(value) for key, value in splits.items()},
        assignments=assignments,
    )
