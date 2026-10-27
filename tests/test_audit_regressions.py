import json
import unittest
from copy import deepcopy
from unittest.mock import MagicMock, patch

from test_analysis_presentation import bundle
from test_volume_analysis import plan, source

from app.analyzers.diagnostic_router import route_candidates
from app.analyzers.sql_features import extract_sql_features
from app.analyzers.volume_analysis import analyze_volume
from app.services.analysis_presentation import MARKER, summarize
from app.services.analysis_service import AnalysisService
from app.services.dataset_service import make_record


class AuditRegressions(unittest.TestCase):
    def test_count_column_distinct_literal_and_filter_never_get_naive_exists(self):
        for sql in (
            "SELECT count(email)>0 FROM users",
            "SELECT count(DISTINCT email)>0 FROM users",
            "SELECT count(1)>0 FROM users",
            "SELECT count(email) FILTER(WHERE id>0)>0 FROM users",
        ):
            syntax = extract_sql_features(sql)
            self.assertEqual(
                next(f for f in route_candidates(syntax)["findings"] if f["rule_id"] == "AP11")[
                    "status"
                ],
                "candidate",
            )
            findings = analyze_volume(sql, syntax, plan(), source())["findings"]
            self.assertTrue(findings)
            self.assertTrue(all(f["candidate_sql"] is None for f in findings))

    def test_window_count_column_remains_window_not_existence(self):
        syntax = extract_sql_features("SELECT count(email) OVER () FROM users")
        self.assertEqual(
            next(f for f in route_candidates(syntax)["findings"] if f["rule_id"] == "AP11")[
                "status"
            ],
            "no_syntax_candidate",
        )

    def test_short_pattern_explanation_does_not_promise_index_fix(self):
        p = bundle(
            "SELECT id FROM products WHERE name ILIKE '%ab%'",
            [
                dict(
                    rule_id="AP03",
                    scope_id="select_1",
                    expression="name ILIKE '%ab%'",
                    risk_assessment="filter_work_requires_measurement",
                    index_candidates=[dict(is_trigram=True)],
                )
            ],
            "access_analysis",
        )
        text = summarize(p)["brief"]
        self.assertIn("Короткий шаблон", text)
        self.assertIn("не гарантирует", text)

    def test_existing_trigram_is_acknowledged_for_long_pattern(self):
        p = bundle(
            "SELECT id FROM products WHERE name LIKE '%phone%'",
            [
                dict(
                    rule_id="AP03",
                    scope_id="select_1",
                    expression="name LIKE '%phone%'",
                    risk_assessment="filter_work_requires_measurement",
                    index_candidates=[dict(is_trigram=True)],
                )
            ],
            "access_analysis",
        )
        self.assertIn("индекс уже найден", summarize(p)["brief"])

    def cte_bundle(self):
        return bundle(
            "WITH x AS MATERIALIZED (SELECT id FROM users) SELECT id FROM x WHERE id=1",
            [
                dict(
                    rule_id="AP13",
                    status="cte_producer_observed",
                    cte_kind="select",
                    cte_materialization_hint=True,
                    cte_reference_count=1,
                    producer_mapping_status="matched_by_unique_name",
                    producer_node={"path": "Plan.Plans[0]"},
                    cte_scan_nodes=[{"conditions": {"Filter": "id=1"}}],
                )
            ],
        )

    def test_matched_materialized_cte_with_late_filter_is_actionable_hypothesis(self):
        text = summarize(self.cte_bundle())["brief"]
        self.assertIn("фильтр применяется к его готовому результату", text)
        self.assertIn("можно ли безопасно", text)
        self.assertIn("пока не подтверждена", text)

    def test_cte_without_specific_evidence_is_not_flagged_by_operator_name(self):
        for change in (
            {"cte_reference_count": 2},
            {"cte_kind": "recursive"},
            {"producer_node": None},
            {"cte_materialization_hint": False},
            {"cte_scan_nodes": []},
        ):
            p = self.cte_bundle()
            p["operation_analysis"]["findings"][0].update(change)
            self.assertEqual(summarize(p)["total_findings"], 0)

    def test_consumer_contract_is_preserved_and_unrelated_connection_fields_excluded(self):
        service = AnalysisService(MagicMock())
        fake = service.database_service.connect.return_value.__enter__.return_value
        with (
            patch(
                "app.services.analysis_service.collect_explain",
                return_value={
                    "plan": [{"Plan": {"Node Type": "Seq Scan", "Plan Rows": 1, "Plan Width": 8}}],
                    "analyze": False,
                },
            ),
            patch(
                "app.services.analysis_service.collect_analysis_context",
                return_value={
                    **source(),
                    "configuration": {"status": "error", "error": "Unavailable", "data": None},
                },
            ),
        ):
            report = service.analyze(
                "SELECT * FROM users",
                {},
                consumer_context={"required_columns": ["id"], "password": "omit-me"},
            )
        payload = json.loads(report.rpartition(MARKER)[2])
        self.assertEqual(payload["consumer_context"], {"required_columns": ["id"]})
        record = make_record(payload)
        self.assertEqual(record["input"]["consumer_context"], {"required_columns": ["id"]})
        self.assertNotIn("omit-me", json.dumps(record))

    def test_count_column_service_report_remains_renderable(self):
        service = AnalysisService(MagicMock())
        with (
            patch(
                "app.services.analysis_service.collect_explain",
                return_value={
                    "plan": [{"Plan": {"Node Type": "Aggregate", "Plan Rows": 1, "Plan Width": 1}}],
                    "analyze": False,
                },
            ),
            patch(
                "app.services.analysis_service.collect_analysis_context",
                return_value={
                    **source(),
                    "configuration": {"status": "error", "error": "Unavailable", "data": None},
                },
            ),
        ):
            report = service.analyze("SELECT count(email)>0 FROM users", {})
        self.assertIn("count_argument_requires_review", report)
        self.assertIn(
            "не считает NULL", summarize(json.loads(report.rpartition(MARKER)[2]))["brief"]
        )
