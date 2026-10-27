"""Запуск SQL-набора в отдельной тестовой базе.

Созданные схемы, данные и расширения откатываются после проверки."""

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.analyzers.access_analysis import analyze_access
from app.analyzers.diagnostic_router import route_candidates
from app.analyzers.operation_analysis import analyze_operations
from app.analyzers.plan_features import extract_plan_features
from app.analyzers.predicate_analysis import analyze_predicates
from app.analyzers.sql_features import extract_sql_features
from app.analyzers.volume_analysis import analyze_volume
from app.collectors.explain import collect_explain
from app.services.analysis_context import collect_analysis_context, json_value
from app.services.analysis_service import AnalysisService


def check_case(case, plan, sources):
    syntax = extract_sql_features(case["sql"])
    triage = route_candidates(syntax)
    candidates = sorted(f["rule_id"] for f in triage["findings"] if f["status"] == "candidate")
    if candidates != sorted(case["expected_candidates"]):
        raise AssertionError("Unexpected syntax candidates")
    if sources["metadata"]["status"] != case.get("expected_metadata_status", "ok"):
        raise AssertionError("Unexpected metadata availability")
    access = analyze_access(case["sql"], syntax, plan, sources)
    volume = analyze_volume(case["sql"], syntax, plan, sources, case.get("consumer_context"))
    predicates = analyze_predicates(case["sql"], syntax, plan, sources)
    operations = analyze_operations(case["sql"], syntax, plan, sources)
    for rule, allowed in case.get("expected_operation_status", {}).items():
        actual = [f["status"] for f in operations["findings"] if f["rule_id"] == rule]
        if (not allowed and actual) or (
            allowed and (not actual or any(status not in allowed for status in actual))
        ):
            raise AssertionError(
                "Unexpected operation analysis status: "
                + case["case_id"]
                + " "
                + rule
                + " "
                + str(actual)
            )
    for rule, allowed in case.get("expected_predicate_status", {}).items():
        actual = [f["status"] for f in predicates["findings"] if f["rule_id"] == rule]
        if (not allowed and actual) or (
            allowed and (not actual or any(s not in allowed for s in actual))
        ):
            raise AssertionError("Unexpected predicate analysis status")
    for rule, statuses in case.get("expected_volume", {}).items():
        actual = sorted(f["status"] for f in volume["findings"] if f["rule_id"] == rule)
        if actual != sorted(statuses):
            raise AssertionError("Unexpected volume analysis status")
    if any(f["slowdown_confirmed"] for f in access["findings"]):
        raise AssertionError("An estimated plan must not confirm a slowdown")
    return {
        "case_id": case["case_id"],
        "category": case["category"],
        "status": "ok",
        "syntax_candidates": candidates,
        "plan_summary": plan["summary"],
        "metadata_status": sources["metadata"]["status"],
        "metadata_error": sources["metadata"].get("error"),
        "sources": sources,
        "access_analysis": access,
        "volume_analysis": volume,
        "predicate_analysis": predicates,
        "operation_analysis": operations,
    }


def main():
    import psycopg
    from psycopg.rows import dict_row

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--user", default=os.environ.get("PGUSER"))
    parser.add_argument("--host", choices=("127.0.0.1", "::1"), default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5432)
    parser.add_argument("--output", type=Path, default=Path("sql_lab_results.json"))
    parser.add_argument(
        "--with-trigram",
        action="store_true",
        help="Require pg_trgm in the dedicated test DB; extension creation is rolled back.",
    )
    args = parser.parse_args()
    if (
        not args.database.startswith("querylens_test")
        or "=" in args.database
        or "://" in args.database
    ):
        parser.error(
            "Use a dedicated database named querylens_test*. Do not use demo or production."
        )
    cases = json.loads((ROOT / "tests/fixtures/sql_cases.json").read_text())
    results = []
    with psycopg.connect(
        dbname=args.database,
        user=args.user,
        host=args.host,
        port=args.port,
        options="-c statement_timeout=5000",
        row_factory=dict_row,
    ) as connection:
        version = connection.execute("SELECT version() AS version").fetchone()["version"]
        try:
            # CREATE SCHEMA остановится, если тестовое имя уже занято.
            connection.execute(
                (ROOT / "tests/fixtures/sql_lab_setup.sql").read_text(), prepare=False
            )
            if args.with_trigram:
                connection.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
                connection.execute(
                    "CREATE INDEX products_trgm_idx ON querylens_lab.products USING gin(name gin_trgm_ops)"
                )
            for case in cases:
                if not case["server_executable"]:
                    results.append(
                        {
                            "case_id": case["case_id"],
                            "status": "syntax_only",
                            "reason": "parameter_value_not_provided",
                        }
                    )
                    continue
                try:
                    with connection.transaction():
                        connection.execute(
                            "SELECT set_config('search_path', %s, true)",
                            (case.get("search_path", '"$user", public'),),
                        )
                        explained = collect_explain(connection, case["sql"])
                        plan = extract_plan_features(explained["plan"])
                        sources = collect_analysis_context(connection, case["sql"], plan)
                        record = check_case(case, plan, sources)
                        comparisons = []
                        for finding in record["volume_analysis"]["findings"]:
                            if finding.get("candidate_sql"):
                                original = connection.execute(case["sql"], prepare=True).fetchall()
                                rewritten = connection.execute(
                                    finding["candidate_sql"], prepare=True
                                ).fetchall()
                                if original != rewritten:
                                    raise AssertionError(
                                        "Candidate result or output names changed on fixture"
                                    )
                                comparisons.append(
                                    {"rule_id": finding["rule_id"], "same_fixture_result": True}
                                )
                        record["candidate_result_checks"] = comparisons
                        bag = lambda rows: Counter(
                            json.dumps(row, sort_keys=True, default=json_value) for row in rows
                        )
                        predicate_checks = []
                        for finding in record["predicate_analysis"]["findings"]:
                            if finding.get("candidate_sql"):
                                original = connection.execute(case["sql"], prepare=True).fetchall()
                                rewritten = connection.execute(
                                    finding["candidate_sql"], prepare=True
                                ).fetchall()
                                if bag(original) != bag(rewritten):
                                    raise AssertionError(
                                        "Predicate candidate changed fixture multiset or names"
                                    )
                                predicate_checks.append(
                                    {"rule_id": finding["rule_id"], "same_fixture_multiset": True}
                                )
                        record["predicate_candidate_checks"] = predicate_checks
                        if case.get("naive_rewrite_sql"):
                            original = connection.execute(case["sql"], prepare=True).fetchall()
                            naive = connection.execute(
                                case["naive_rewrite_sql"], prepare=True
                            ).fetchall()
                            if bag(original) == bag(naive):
                                raise AssertionError(
                                    "NULL counterexample did not expose unsafe rewrite"
                                )
                            record["naive_rewrite_changes_result"] = True
                        if case["case_id"] in {
                            "ap02_expression_index",
                            "ap03_trigram_selective",
                            "op_distinct_pk",
                            "op_having_early",
                            "op_cte_materialized",
                            "op_window_named",
                            "op_join_left",
                        }:
                            from contextlib import nullcontext

                            class LabDatabase:
                                def connect(self, parameters):
                                    return nullcontext(connection)

                            report = AnalysisService(LabDatabase()).analyze(case["sql"], {})
                            payload = json.loads(
                                report.split("ДИАГНОСТИЧЕСКИЕ ДАННЫЕ В JSON\n", 1)[1]
                            )
                            if (
                                payload["sql"] != case["sql"]
                                or payload["sources"]["metadata"]["status"] != "ok"
                                or "operation_analysis" not in payload
                            ):
                                raise AssertionError("Service report failed")
                            record["service_report_ok"] = True
                        measured = connection.execute(
                            "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + case["sql"], prepare=True
                        ).fetchone()["QUERY PLAN"]
                        record["execution_time_ms"] = measured[0].get("Execution Time")
                        record["explain"] = explained
                        results.append(record)
                except Exception as error:
                    results.append(
                        {
                            "case_id": case["case_id"],
                            "status": "error",
                            "error": type(error).__name__,
                        }
                    )
        finally:
            connection.rollback()
    report = {
        "schema_version": 1,
        "engine": version,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "fixture_rolled_back": True,
        "trigram_requested": args.with_trigram,
        "results": results,
    }
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=json_value) + "\n"
    )
    failures = sum(r["status"] == "error" for r in results)
    print(f"SQL: {len(results)}; errors: {failures}; report: {args.output}")
    return bool(failures)


if __name__ == "__main__":
    sys.exit(main())
