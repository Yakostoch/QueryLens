"""Повторная проверка сохранённых планов без выполнения SQL."""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.analyzers.diagnostic_router import route_candidates
from app.analyzers.diagnostic_tree import build_diagnostic_tree
from app.analyzers.sql_features import extract_sql_features


def validate_refs(payload, value):
    count = 0

    def resolve(pointer):
        current = payload
        for key in pointer.split("/")[1:]:
            key = key.replace("~1", "/").replace("~0", "~")
            current = current[int(key)] if isinstance(current, list) else current[key]

    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"source_ref", "candidate_sql_ref"} and item is not None:
                resolve(item)
                count += 1
            elif key in {"evidence_refs", "related_setting_refs"}:
                for ref in item:
                    resolve(ref)
                    count += 1
            else:
                count += validate_refs(payload, item)
    elif isinstance(value, list):
        count += sum(validate_refs(payload, item) for item in value)
    return count


def replay(document, cases):
    cases = {c["case_id"]: c for c in cases}
    results = []
    for record in document["results"]:
        if record["status"] != "ok":
            results.append(
                dict(
                    case_id=record["case_id"], status="not_replayed", source_status=record["status"]
                )
            )
            continue
        case = cases[record["case_id"]]
        syntax = extract_sql_features(case["sql"])
        payload = dict(
            sql=case["sql"],
            sql_features=syntax,
            triage=route_candidates(syntax),
            mode="explain_without_analyze",
            explain=record["explain"],
            sources=record["sources"],
        )
        for block in (
            "access_analysis",
            "volume_analysis",
            "predicate_analysis",
            "operation_analysis",
        ):
            payload[block] = record.get(block, {"findings": []})
        tree = build_diagnostic_tree(payload)
        assert (
            tree["cause_assessment"] == "not_established" and tree["summary"]["rules_total"] == 15
        )
        assert tree["measurement_status"] == "not_measured"
        refs = validate_refs(payload, tree)
        results.append(
            dict(
                case_id=case["case_id"],
                status="ok",
                references_verified=refs,
                rule_states={
                    r["rule_id"]: r["state"]
                    for g in tree["root"]["children"]
                    for r in g["children"]
                },
                recommended_checks=tree["recommended_checks"],
            )
        )
    return dict(
        schema_version=1,
        mode="offline_replay_not_new_sql_execution",
        source_engine=document.get("engine"),
        results=results,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.input.resolve() == args.output.resolve():
        parser.error("Выберите другой файл результата.")
    report = replay(
        json.loads(args.input.read_text()),
        json.loads((ROOT / "tests/fixtures/sql_cases.json").read_text()),
    )
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print("Проверено сохранённых планов:", sum(r["status"] == "ok" for r in report["results"]))
    print("Повторного выполнения SQL не было.")


if __name__ == "__main__":
    main()
