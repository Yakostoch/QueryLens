import json
import unittest
from copy import deepcopy
from unittest.mock import MagicMock, patch

from test_access_analysis import metadata

from app.analyzers.plan_features import extract_plan_features
from app.analyzers.sql_features import extract_sql_features
from app.analyzers.volume_analysis import analyze_volume
from app.services.analysis_service import AnalysisService


def source():
    result = metadata()
    t = result["metadata"]["data"]["tables"][0]
    for f in t["all_fields"]:
        f["nullable"] = False
    t["diagnostics"]["sources"]["relation"]["data"]["row_security_enabled"] = False
    t["diagnostics"]["sources"]["constraints"] = {
        "status": "ok",
        "data": [{"kind": "p", "columns": ["id"], "is_validated": True}],
    }
    return result


def plan(kind="Seq Scan", rows=10, child_rows=None):
    node = {
        "Node Type": kind,
        "Plan Rows": rows,
        "Plan Width": 16,
        "Total Cost": 5,
        "Output": ["id", "email", "code"],
    }
    if child_rows is not None:
        node["Plans"] = [{"Node Type": "Seq Scan", "Plan Rows": child_rows, "Plan Width": 32}]
    return extract_plan_features([{"Plan": node}])


class VolumeTests(unittest.TestCase):
    def analyze(self, sql, context=None, meta=None, p=None):
        return analyze_volume(
            sql, extract_sql_features(sql), p or plan(), meta or source(), context
        )["findings"]

    def test_star_count_and_expansion(self):
        f = self.analyze("SELECT * FROM users")[0]
        self.assertEqual(f["expanded_columns_count"], 3)
        self.assertEqual(f["status"], "consumer_requirements_unknown")
        self.assertIsNone(f["excess_columns"])
        self.assertFalse(f["slowdown_confirmed"])

    def test_all_columns_can_be_intentional(self):
        f = self.analyze("SELECT * FROM users", {"all_output_columns_required": True})[0]
        self.assertEqual(f["status"], "all_columns_required")
        self.assertEqual(f["recommendations"], [])

    def test_explicit_contract_can_identify_excess_but_not_speed(self):
        f = self.analyze("SELECT * FROM users", {"required_columns": ["id"]})[0]
        self.assertEqual(f["excess_columns"], ["email", "code"])
        self.assertEqual(f["status"], "potential_excess_output")
        self.assertFalse(f["slowdown_confirmed"])

    def test_invalid_contract_does_not_create_a_rewrite(self):
        f = self.analyze("SELECT * FROM users", {"required_columns": ["nonexistent"]})[0]
        self.assertEqual(f["required_columns_status"], "not_validated_for_this_projection")
        self.assertIsNone(f["excess_columns"])

    def test_nested_star_does_not_receive_root_width(self):
        fs = self.analyze("SELECT x.id FROM (SELECT * FROM users) x", p=plan(rows=100000))
        self.assertIsNone(fs[0]["node_metrics"])
        self.assertEqual(fs[0]["status"], "nested_projection_requires_plan_mapping")

    def test_join_using_global_star_is_not_naively_summed(self):
        meta = source()
        t = deepcopy(meta["metadata"]["data"]["tables"][0])
        t["name"] = "other"
        meta["metadata"]["data"]["tables"].append(t)
        self.assertIsNone(
            self.analyze("SELECT * FROM users JOIN other USING(id)", meta=meta)[0][
                "expanded_columns_count"
            ]
        )
        self.assertEqual(
            self.analyze("SELECT u.* FROM users u JOIN other o USING(id)", meta=meta)[0][
                "expanded_columns_count"
            ],
            3,
        )

    def test_derived_cte_and_missing_metadata_remain_unknown(self):
        f = self.analyze("WITH x AS (SELECT id FROM users) SELECT * FROM x")[0]
        self.assertIsNone(f["expanded_columns_count"])
        f = self.analyze("SELECT * FROM users", meta={"metadata": {"status": "error"}})[0]
        self.assertIsNone(f["expanded_columns_count"])

    def test_count_star_is_not_projection_star(self):
        fs = self.analyze("SELECT count(*) FROM users", p=plan("Aggregate", 1, 10000))
        self.assertEqual([f["rule_id"] for f in fs], ["AP11"])
        self.assertEqual(fs[0]["count_input_rows"], 10000)
        self.assertEqual(fs[0]["status"], "count_purpose_unknown")

    def test_deep_offset_uses_stream_not_root_row_count(self):
        f = self.analyze(
            "SELECT id FROM users ORDER BY id LIMIT 10 OFFSET 100000", p=plan("Limit", 10, 200000)
        )[0]
        self.assertEqual(f["offset_to_limit_ratio"], 10000)
        self.assertEqual(f["requested_prefix_rows"], 100010)
        self.assertEqual(f["limit_child_rows"], 200000)
        self.assertEqual(f["order_uniqueness"], "proven_by_catalog_key")
        self.assertFalse(f["cursor_available"])

    def test_limit_zero_suppresses_skip_work_recommendation(self):
        f = self.analyze("SELECT id FROM users LIMIT 0 OFFSET 1000000")[0]
        self.assertEqual(f["status"], "no_input_required_for_limit_zero")
        self.assertEqual(f["requested_prefix_rows"], 0)
        self.assertEqual(f["recommendations"], [])

    def test_unknown_offset_and_limit_are_not_evaluated(self):
        for sql in ("SELECT id FROM users OFFSET $1", "SELECT id FROM users LIMIT $2 OFFSET $1"):
            f = self.analyze(sql)[0]
            self.assertEqual(f["status"], "insufficient_data")
            self.assertIsNone(f["offset_value"])
            self.assertIsNone(f["requested_prefix_rows"])

    def test_offset_zero_is_not_skip_work(self):
        f = self.analyze("SELECT id FROM users LIMIT 10 OFFSET 0")[0]
        self.assertEqual(f["status"], "no_rows_skipped")
        self.assertEqual(f["recommendations"], [])

    def test_nested_offset_does_not_get_root_limit(self):
        f = self.analyze(
            "SELECT id FROM (SELECT id FROM users OFFSET 5) x LIMIT 2", p=plan("Limit", 2, 100)
        )[0]
        self.assertIsNone(f["node_metrics"])
        self.assertIsNone(f["limit_child_rows"])

    def test_union_offset_is_explicitly_unknown(self):
        f = self.analyze("SELECT id FROM users UNION ALL SELECT id FROM users LIMIT 5 OFFSET 10")[0]
        self.assertEqual(f["status"], "insufficient_data")
        self.assertIsNone(f["scope_id"])

    def test_nullable_unique_is_not_proven_unique_order(self):
        meta = source()
        t = meta["metadata"]["data"]["tables"][0]
        t["all_fields"][1]["nullable"] = True
        t["diagnostics"]["sources"]["constraints"]["data"] = [
            {"kind": "u", "columns": ["email"], "is_validated": True}
        ]
        f = self.analyze("SELECT id FROM users ORDER BY email LIMIT 10 OFFSET 5", meta=meta)[0]
        self.assertEqual(f["order_uniqueness"], "unknown")

    def test_output_alias_cannot_fake_primary_key_order(self):
        f = self.analyze("SELECT email AS id FROM users ORDER BY id LIMIT 10 OFFSET 5")[0]
        self.assertEqual(f["order_uniqueness"], "unknown")
        f = self.analyze("SELECT id AS key FROM users ORDER BY key LIMIT 10 OFFSET 5")[0]
        self.assertEqual(f["order_uniqueness"], "proven_by_catalog_key")

    def test_boolean_count_has_reviewable_exists_candidate(self):
        for expr in ("count(*)>0", "count(*)>=1", "count(*)<>0", "0<count(*)", "count(*)=0"):
            with self.subTest(expr=expr):
                f = self.analyze(
                    "SELECT " + expr + " AS present FROM users WHERE id=42",
                    p=plan("Aggregate", 1, 100),
                )[0]
                self.assertEqual(f["status"], "boolean_existence_candidate")
                self.assertIn("EXISTS", f["candidate_sql"])
                self.assertIn('"present"', f["candidate_sql"])
                self.assertTrue(f["candidate_requires_review"])
                self.assertFalse(f["slowdown_confirmed"])

    def test_unaliased_count_preserves_default_boolean_column_name(self):
        f = self.analyze("SELECT count(*)>0 FROM users")[0]
        self.assertIn('"?column?"', f["candidate_sql"])

    def test_exact_count_and_numeric_consumer_do_not_change_output_type(self):
        for ctx, status in (
            ({"count_usage": "exact_count"}, "exact_count_required"),
            ({"count_usage": "existence_only"}, "consumer_contract_change_required"),
        ):
            f = self.analyze("SELECT count(*) FROM users", ctx)[0]
            self.assertEqual(f["status"], status)
            self.assertIsNone(f["candidate_sql"])

    def test_quantity_threshold_stays_count(self):
        for expr in ("count(*)>10", "count(*)=1", "count(*)>=0"):
            f = self.analyze("SELECT " + expr + " FROM users")[0]
            self.assertEqual(f["status"], "quantity_threshold_required")
            self.assertIsNone(f["candidate_sql"])
        self.assertEqual(self.analyze("SELECT count(*)+1 FROM users")[0]["count_usage"], "unknown")

    def test_grouping_limit_and_multiple_outputs_are_not_rewritten(self):
        for sql in (
            "SELECT count(*)>0 FROM users GROUP BY id",
            "SELECT count(*)>0 FROM users LIMIT 0",
            "SELECT count(*)>0, sum(id) FROM users",
            "SELECT count(*)>0 FROM users HAVING count(*)>1",
        ):
            with self.subTest(sql=sql):
                self.assertTrue(
                    all(
                        f["candidate_sql"] is None
                        for f in self.analyze(sql)
                        if f["rule_id"] == "AP11"
                    )
                )

    def test_window_and_nullable_count_are_not_count_star_existence(self):
        self.assertFalse(
            any(f["rule_id"] == "AP11" for f in self.analyze("SELECT count(*) OVER() FROM users"))
        )
        f = self.analyze("SELECT count(email)>0 FROM users")[0]
        self.assertEqual(f["status"], "count_argument_requires_review")
        self.assertIsNone(f["candidate_sql"])

    def test_rls_missing_metadata_view_or_functions_stop_candidate_generation(self):
        for sql in (
            "SELECT count(*)>0 FROM users WHERE random()>0.5",
            "SELECT count(*)>0 FROM users WHERE id+1>0",
        ):
            self.assertIsNone(self.analyze(sql)[0]["candidate_sql"])
        for value in (True, None):
            meta = source()
            meta["metadata"]["data"]["tables"][0]["diagnostics"]["sources"]["relation"]["data"][
                "row_security_enabled"
            ] = value
            self.assertIsNone(
                self.analyze("SELECT count(*)>0 FROM users", meta=meta)[0]["candidate_sql"]
            )

    def test_filtered_and_nested_count_never_receive_root_candidate(self):
        f = self.analyze("SELECT count(*) FILTER(WHERE id>0)>0 FROM users")[0]
        self.assertIsNone(f["candidate_sql"])
        f = self.analyze("SELECT (SELECT count(*) FROM users)>0")[0]
        self.assertEqual(f["status"], "nested_count_requires_plan_mapping")
        self.assertIsNone(f["node_metrics"])

    def test_service_contains_count_candidate_and_preserves_string_contract(self):
        sql = "SELECT count(*)>0 AS present FROM users WHERE id=1"
        raw = [
            {
                "Plan": {
                    "Node Type": "Aggregate",
                    "Plan Rows": 1,
                    "Plan Width": 1,
                    "Plans": [{"Node Type": "Seq Scan", "Plan Rows": 10, "Plan Width": 4}],
                }
            }
        ]
        sources = source()
        sources["configuration"] = {"status": "ok", "data": {}, "error": None}
        with (
            patch("app.services.analysis_service.collect_explain", return_value={"plan": raw}),
            patch("app.services.analysis_service.collect_analysis_context", return_value=sources),
        ):
            report = AnalysisService(MagicMock()).analyze(sql, {"password": "secret"})
        payload = json.loads(report.split("ДИАГНОСТИЧЕСКИЕ ДАННЫЕ В JSON\n", 1)[1])
        self.assertIn("EXISTS", payload["volume_analysis"]["findings"][0]["candidate_sql"])
        self.assertNotIn("secret", report)

    def test_service_consumer_context_is_explicit_not_guessed(self):
        raw = [{"Plan": {"Node Type": "Seq Scan", "Plan Rows": 1, "Plan Width": 10}}]
        sources = source()
        sources["configuration"] = {"status": "ok", "data": {}, "error": None}
        with (
            patch("app.services.analysis_service.collect_explain", return_value={"plan": raw}),
            patch("app.services.analysis_service.collect_analysis_context", return_value=sources),
        ):
            report = AnalysisService(MagicMock()).analyze(
                "SELECT * FROM users",
                {},
                consumer_context={"all_output_columns_required": True, "password": "DO_NOT_EXPORT"},
            )
        payload = json.loads(report.split("ДИАГНОСТИЧЕСКИЕ ДАННЫЕ В JSON\n", 1)[1])
        self.assertEqual(
            payload["volume_analysis"]["findings"][0]["status"], "all_columns_required"
        )
        self.assertNotIn("DO_NOT_EXPORT", report)


if __name__ == "__main__":
    unittest.main()
