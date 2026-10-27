"""Повторная проверка сохранённых планов и подготовка предложений разметки."""

import argparse
import json
import sys
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.analyzers.access_analysis import analyze_access
from app.analyzers.diagnostic_router import route_candidates
from app.analyzers.diagnostic_tree import build_diagnostic_tree
from app.analyzers.operation_analysis import analyze_operations
from app.analyzers.plan_features import extract_plan_features
from app.analyzers.predicate_analysis import analyze_predicates
from app.analyzers.runtime_analysis import analyze_runtime
from app.analyzers.sql_features import extract_sql_features
from app.analyzers.volume_analysis import analyze_volume
from app.services.analysis_presentation import resolve
from app.services.dataset_service import DatasetError, make_record, write_records


def prepare(captures, cases, control_plan, proposals):
    cases = {c["case_id"]: c for c in cases}
    rows, replay = {}, []
    for captured in captures["results"]:
        key = captured["case_id"]
        if captured["status"] != "ok":
            replay.append(
                dict(source_case_id=key, status="not_replayed", source_status=captured["status"])
            )
            continue
        case = cases[key]
        sql = case["sql"]
        syntax = extract_sql_features(sql)
        plan = extract_plan_features(captured["explain"]["plan"])
        sources = captured["sources"]
        consumer = case.get("consumer_context", {})
        triage = route_candidates(syntax)
        actual = sorted(f["rule_id"] for f in triage["findings"] if f["status"] == "candidate")
        if actual != sorted(case["expected_candidates"]):
            raise DatasetError("Не совпала спецификация синтаксиса: " + key)
        payload = dict(
            schema_version=2,
            sql=sql,
            consumer_context=consumer,
            mode="explain_without_analyze",
            explain=captured["explain"],
            sources=sources,
            sql_features=syntax,
            plan_features=plan,
            plan_summary=plan["summary"],
            runtime_analysis=analyze_runtime(plan, measured=False),
            triage=triage,
            access_analysis=analyze_access(sql, syntax, plan, sources),
            volume_analysis=analyze_volume(sql, syntax, plan, sources, consumer),
            predicate_analysis=analyze_predicates(sql, syntax, plan, sources),
            operation_analysis=analyze_operations(sql, syntax, plan, sources),
        )
        payload["diagnostic_tree"] = build_diagnostic_tree(payload)
        record = make_record(payload)
        record["provenance"].update(
            origin="step12_recomputed_rules_on_captured_plan",
            source_case_id=key,
            source_engine=captures.get("engine"),
            source_collected_at=captures.get("collected_at"),
            sql_executed_now=False,
        )
        rows[key] = record
        replay.append(
            dict(
                source_case_id=key,
                status="ok",
                syntax_spec_matched=True,
                candidate_rules=actual,
                case_id=record["case_id"],
            )
        )
    expected = {c["source_case_id"] for c in control_plan["cases"]}
    if expected != set(proposals):
        raise DatasetError("Предложения должны покрывать ровно выбранные контрольные случаи.")
    queue, suggested = [], []
    for selection in control_plan["cases"]:
        key = selection["source_case_id"]
        record = rows[key]
        queue.append(record)
        proposed = deepcopy(record)
        assessment = proposals[key]
        focus = selection["check_focus"]
        refs = ["/input/sql", "/input/plan_features/nodes"]
        if proposed["input"]["sources"].get("metadata", {}).get("status") == "ok":
            refs.append("/input/sources/metadata/data")
        if proposed["input"]["consumer_context"]:
            refs.append("/input/consumer_context")
        for ref in refs:
            resolve(proposed, ref)
        proposed["review"].update(
            status="pending",
            reviewed_by="",
            family_id="assistant_audit_" + focus,
            verdict=assessment["verdict"],
            assessed_rules=[focus],
            labels=[focus] if assessment["verdict"] in {"problem", "potential"} else [],
            evidence_refs=refs,
            answer=assessment["answer"],
            notes="Предложение ИИ; требует проверки. " + assessment["rationale"],
        )
        proposed["provenance"][
            "annotation_origin"
        ] = "assistant_proposal_not_independent_human_review"
        suggested.append(proposed)
    return (
        queue,
        suggested,
        dict(
            mode="offline_replay_not_new_SQL_execution",
            proposals=len(suggested),
            approved=0,
            independent_human_review=False,
            results=replay,
        ),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("captures", "cases", "control-plan", "proposals"):
        parser.add_argument("--" + flag, required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    try:
        read = lambda p: json.loads(p.read_text(encoding="utf-8"))
        queue, suggestions, replay = prepare(
            read(args.captures), read(args.cases), read(args.control_plan), read(args.proposals)
        )
        args.output_dir.mkdir()
        write_records(args.output_dir / "queue_50.jsonl", queue)
        write_records(args.output_dir / "assistant_proposals_50.jsonl", suggestions)
        (args.output_dir / "replay.json").write_text(
            json.dumps(replay, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"Подготовлено предложений: {len(suggestions)}. Одобрено: 0. SQL не выполнялся.")
    except (OSError, ValueError, KeyError, TypeError) as error:
        print("Аудит не подготовлен: " + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
