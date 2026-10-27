import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from PySide6 import QtWidgets

from app.analyzers.diagnostic_router import route_candidates
from app.analyzers.diagnostic_tree import build_diagnostic_tree, format_diagnostic_tree
from app.analyzers.sql_features import extract_sql_features
from app.ui.main_window import MainWindow


def bundle(sql="SELECT 1"):
    syntax = extract_sql_features(sql)
    return dict(
        sql_features=syntax,
        triage=route_candidates(syntax),
        sources={},
        mode="explain_without_analyze",
        explain={"analyze": False},
        runtime_analysis={"status": "not_measured"},
        access_analysis={"findings": []},
        volume_analysis={"findings": []},
        predicate_analysis={"findings": []},
        operation_analysis={"findings": []},
    )


def rules(tree):
    return {r["rule_id"]: r for g in tree["root"]["children"] for r in g["children"]}


def resolve(payload, pointer):
    current = payload
    for key in pointer.split("/")[1:]:
        key = key.replace("~1", "/").replace("~0", "~")
        current = current[int(key)] if isinstance(current, list) else current[key]
    return current


def verify_refs(test, payload, node):
    if isinstance(node, dict):
        for k, v in node.items():
            if k in {"source_ref", "candidate_sql_ref"} and v is not None:
                resolve(payload, v)
            elif k in {"evidence_refs", "related_setting_refs"}:
                for ref in v:
                    resolve(payload, ref)
            else:
                verify_refs(test, payload, v)
    elif isinstance(node, list):
        for v in node:
            verify_refs(test, payload, v)


class DiagnosticTreeTests(unittest.TestCase):
    def test_all_fifteen_rules_and_six_groups_are_present(self):
        t = build_diagnostic_tree(bundle())
        self.assertEqual(len(rules(t)), 15)
        self.assertEqual(len(t["root"]["children"]), 6)
        self.assertEqual(t["cause_assessment"], "not_established")

    def test_good_query_not_called_slow_or_guaranteed_fast(self):
        t = build_diagnostic_tree(bundle())
        r = rules(t)
        self.assertTrue(all(v["state"] == "not_detected" for k, v in r.items() if k != "AP10"))
        self.assertEqual(t["recommended_checks"], [])
        self.assertEqual(t["slowdown_assessment"], "no_explicit_response_time_target")

    def test_n_plus_one_missing_trace_is_explicit_not_a_false_diagnosis(self):
        t = build_diagnostic_tree(bundle())
        f = rules(t)["AP10"]
        self.assertEqual(f["state"], "needs_evidence")
        self.assertIn("трасса", f["scope_limitation"])
        self.assertFalse(any(a.get("rule_id") == "AP10" for a in t["recommended_checks"]))

    def test_primary_key_candidate_has_traceable_catalog_evidence(self):
        p = bundle("SELECT DISTINCT id FROM users")
        p["operation_analysis"]["findings"] = [
            dict(
                rule_id="AP06",
                status="projection_unique_by_primary_key",
                uniqueness_proven=True,
                unique_key=["id"],
                expression="DISTINCT",
                recommendations=["Проверить без DISTINCT"],
            )
        ]
        t = build_diagnostic_tree(p)
        c = rules(t)["AP06"]["children"][0]
        self.assertEqual(c["state"], "optimization_candidate")
        self.assertEqual(c["evidence_level"], "catalog_supported")
        self.assertEqual(t["recommended_checks"][0]["priority"], 1)
        verify_refs(self, p, t)

    def test_not_in_null_is_semantics_review_without_claiming_null_present(self):
        p = bundle("SELECT id FROM users WHERE id NOT IN (SELECT key FROM deny)")
        p["predicate_analysis"]["findings"] = [
            dict(
                rule_id="AP05",
                status="null_semantics_requires_review",
                nullability_evidence={"outer": "unknown", "inner": "unknown"},
                recommendations=["Уточнить NULL"],
            )
        ]
        c = rules(build_diagnostic_tree(p))["AP05"]["children"][0]
        self.assertEqual(c["state"], "semantic_review")
        self.assertEqual(c["evidence_level"], "syntax_or_context")

    def test_required_count_does_not_receive_exists_recommendation(self):
        p = bundle("SELECT COUNT(*) FROM users")
        p["volume_analysis"]["findings"] = [dict(rule_id="AP11", status="exact_count_required")]
        t = build_diagnostic_tree(p)
        self.assertEqual(rules(t)["AP11"]["state"], "access_observed")
        self.assertEqual(t["recommended_checks"], [])

    def test_index_access_and_small_relation_do_not_trigger_index_action(self):
        for status in ["predicate_index_access_observed", "small_relation_context"]:
            p = bundle("SELECT id FROM users WHERE lower(email)='x'")
            p["access_analysis"]["findings"] = [
                dict(rule_id="AP02", risk_assessment=status, binding_status="matched")
            ]
            t = build_diagnostic_tree(p)
            self.assertEqual(rules(t)["AP02"]["state"], "access_observed")
            self.assertEqual(t["recommended_checks"], [])

    def test_unknown_expression_gets_data_request_not_missing_index_claim(self):
        p = bundle("SELECT id FROM users WHERE lower(email)='x'")
        p["access_analysis"]["findings"] = [
            dict(rule_id="AP02", risk_assessment="insufficient_data", binding_status="unknown")
        ]
        t = build_diagnostic_tree(p)
        self.assertEqual(rules(t)["AP02"]["state"], "needs_evidence")
        self.assertEqual(t["recommended_checks"][0]["priority"], 4)
        self.assertIn("дополнительные сведения", t["recommended_checks"][0]["text"])

    def test_two_occurrences_keep_different_states_and_scopes(self):
        p = bundle("SELECT * FROM users")
        p["volume_analysis"]["findings"] = [
            dict(rule_id="AP01", status="all_columns_required", scope_id="select_1"),
            dict(
                rule_id="AP01",
                status="nested_projection_requires_plan_mapping",
                scope_id="select_2",
            ),
        ]
        f = rules(build_diagnostic_tree(p))["AP01"]
        self.assertEqual(f["state"], "mixed_evidence")
        self.assertEqual(len(f["children"]), 2)
        self.assertEqual(f["children"][1]["scope_id"], "select_2")

    def test_having_early_filter_not_offered_as_late_problem(self):
        p = bundle("SELECT id,count(*) FROM users GROUP BY id HAVING id=1")
        p["operation_analysis"]["findings"] = [
            dict(
                rule_id="AP09",
                status="having_predicates_checked",
                predicates=[
                    dict(safe_to_pushdown=True, predicate_plan_location="before_aggregation")
                ],
            )
        ]
        self.assertEqual(rules(build_diagnostic_tree(p))["AP09"]["state"], "access_observed")

    def test_late_having_is_review_candidate(self):
        p = bundle()
        p["operation_analysis"]["findings"] = [
            dict(
                rule_id="AP09",
                status="having_predicates_checked",
                predicates=[
                    dict(safe_to_pushdown=True, predicate_plan_location="after_aggregation")
                ],
            )
        ]
        self.assertEqual(rules(build_diagnostic_tree(p))["AP09"]["state"], "review_candidate")

    def test_disconnected_sources_require_intended_relation(self):
        p = bundle("SELECT u.id,d.key FROM users u CROSS JOIN deny d")
        t = build_diagnostic_tree(p)
        f = rules(t)["AP12"]
        self.assertEqual(f["state"], "semantic_review")
        self.assertEqual(f["missing_evidence"], ["intended_relationship"])
        verify_refs(self, p, t)

    def test_measured_facts_require_all_three_mode_markers(self):
        p = bundle()
        p["runtime_analysis"] = {
            "status": "measured",
            "execution_time_ms": 5,
            "node_observations": [{"path": "Plan", "disk_sort_observed": True}],
        }
        t = build_diagnostic_tree(p)
        self.assertEqual(t["measured_observations"], [])
        self.assertIsNone(t["execution_time_ms"])
        p["mode"] = "explain_analyze"
        self.assertEqual(build_diagnostic_tree(p)["measured_observations"], [])
        p["explain"]["analyze"] = True
        self.assertEqual(len(build_diagnostic_tree(p)["measured_observations"]), 1)

    def test_measured_temp_buffers_not_duplicated_by_ancestor_and_child(self):
        p = bundle()
        p.update(
            mode="explain_analyze",
            explain={"analyze": True},
            runtime_analysis=dict(
                status="measured",
                execution_time_ms=5,
                node_observations=[
                    {
                        "path": "Plan.Plans[0]",
                        "temporary_io_observed": True,
                        "disk_sort_observed": True,
                    },
                    {"path": "Plan", "temporary_io_observed": True},
                ],
            ),
        )
        t = build_diagnostic_tree(p)
        kinds = [f["kind"] for f in t["measured_observations"]]
        self.assertEqual(kinds.count("temporary_io"), 1)
        self.assertEqual(kinds.count("disk_sort"), 1)
        verify_refs(self, p, t)

    def test_setting_alone_does_not_generate_memory_tuning(self):
        p = bundle()
        p["sources"]["configuration"] = {
            "status": "ok",
            "data": {"settings": [{"name": "work_mem", "setting": "4096", "unit": "kB"}]},
        }
        t = build_diagnostic_tree(p)
        self.assertEqual(t["recommended_checks"], [])
        self.assertEqual(t["measured_observations"], [])
        verify_refs(self, p, t)

    def test_local_resources_do_not_become_server_diagnosis(self):
        p = bundle()
        p["sources"]["system"] = {"status": "ok", "data": {"ram_used_percent": 99}}
        t = build_diagnostic_tree(p)
        self.assertEqual(t["context_notes"][0]["status"], "not_correlated")
        self.assertEqual(t["recommended_checks"], [])

    def test_actions_deduplicated_without_losing_sources(self):
        p = bundle()
        p["predicate_analysis"]["findings"] = [
            dict(
                rule_id="AP04",
                status="or_group_filter_observed",
                recommendations=["Измерить ветви"],
            )
        ] * 2
        t = build_diagnostic_tree(p)
        self.assertEqual(len(t["recommended_checks"]), 1)
        self.assertEqual(len(t["recommended_checks"][0]["evidence_refs"]), 2)
        verify_refs(self, p, t)

    def test_candidate_sql_is_referenced_not_executed_or_copied(self):
        p = bundle()
        p["volume_analysis"]["findings"] = [
            dict(
                rule_id="AP11",
                status="boolean_existence_candidate",
                candidate_sql="SELECT EXISTS(SELECT 1)",
            )
        ]
        t = build_diagnostic_tree(p)
        c = rules(t)["AP11"]["children"][0]
        self.assertEqual(resolve(p, c["candidate_sql_ref"]), "SELECT EXISTS(SELECT 1)")
        self.assertTrue(c["candidate_requires_review"])

    def test_input_is_not_mutated_and_output_deterministic(self):
        p = bundle("SELECT DISTINCT id FROM users")
        before = deepcopy(p)
        first = build_diagnostic_tree(p)
        self.assertEqual(first, build_diagnostic_tree(p))
        self.assertEqual(p, before)
        self.assertIn("ДЕРЕВО ДИАГНОСТИКИ", "\n".join(format_diagnostic_tree(first)))

    def test_unrecognized_future_status_remains_unknown(self):
        p = bundle()
        p["operation_analysis"]["findings"] = [dict(rule_id="AP14", status="future_unrecognized")]
        self.assertEqual(rules(build_diagnostic_tree(p))["AP14"]["state"], "needs_evidence")


class StatusRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_timeout_and_cancel_replace_running_status(self):
        with tempfile.TemporaryDirectory() as d:
            w = MainWindow(d)
            for message in [
                "Анализ отменён. Незавершённые измерения не сохранены.",
                "Выполнение отменено PostgreSQL или превышен тайм-аут. Завершённых измерений нет.",
            ]:
                w.status.setText("Измеряю выполнение SELECT…")
                w.show_analysis_error(message)
                w.show_analysis_busy(False)
                self.assertEqual(w.status.text(), message)
                self.assertEqual(w.output.toPlainText(), message)
            w.close()
