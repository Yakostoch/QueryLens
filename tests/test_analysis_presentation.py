import json
import tempfile
import unittest
from pathlib import Path

from PySide6 import QtWidgets

from app.analyzers.diagnostic_router import route_candidates
from app.analyzers.diagnostic_tree import build_diagnostic_tree
from app.analyzers.sql_features import extract_sql_features
from app.services.analysis_presentation import MARKER, present_report, summarize
from app.ui.main_window import MainWindow


def bundle(sql="SELECT 1", findings=(), block="operation_analysis"):
    syntax = extract_sql_features(sql)
    payload = dict(
        sql=sql,
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
    payload[block]["findings"] = list(findings)
    payload["diagnostic_tree"] = build_diagnostic_tree(payload)
    return payload


def report(payload):
    return (
        "Полный отчёт\nSQL:\n"
        + payload["sql"]
        + "\n"
        + MARKER
        + json.dumps(payload, ensure_ascii=False)
    )


class PresentationTests(unittest.TestCase):
    def test_primary_key_is_short_specific_and_has_action(self):
        payload = bundle(
            "SELECT DISTINCT flight_id FROM other_schema.flights",
            [
                dict(
                    rule_id="AP06",
                    expression="DISTINCT",
                    status="projection_unique_by_primary_key",
                    uniqueness_proven=True,
                    unique_key=["flight_id"],
                )
            ],
        )
        result = present_report(report(payload))
        self.assertIn("flight_id", result["brief"])
        self.assertIn("без DISTINCT", result["brief"])
        self.assertLess(len(result["brief"]), 550)
        self.assertNotIn("AP06", result["brief"])
        self.assertNotIn("N+1", result["brief"])
        self.assertNotIn("schema_version", result["brief"])
        self.assertEqual(json.loads(result["raw"]), payload)
        self.assertIn("Полный отчёт", result["details"])

    def test_good_query_has_no_false_diagnosis_or_n_plus_one(self):
        result = summarize(bundle())
        self.assertEqual(result["total_findings"], 0)
        self.assertIn("не гарантирует", result["brief"])
        self.assertNotIn("N+1", result["brief"])

    def test_normal_join_sort_window_cte_are_not_problem_by_name(self):
        for rule, status in [
            ("AP15", "join_plan_observed"),
            ("AP07", "sort_input_observed"),
            ("AP14", "window_pass_observed"),
            ("AP13", "cte_scan_observed"),
        ]:
            payload = bundle(findings=[dict(rule_id=rule, status=status)])
            self.assertEqual(summarize(payload)["total_findings"], 0)

    def test_select_star_does_not_invent_unused_columns(self):
        result = summarize(
            bundle(
                "SELECT * FROM users",
                [dict(rule_id="AP01", expression="*", status="consumer_requirements_unknown")],
                "volume_analysis",
            )
        )
        self.assertIn("Недостаточно данных", result["brief"])
        self.assertNotIn("Лишние данные", result["brief"])

    def test_count_candidate_is_separate_from_short_answer(self):
        sql = "SELECT EXISTS (SELECT 1 FROM orders)"
        payload = bundle(
            "SELECT COUNT(*) > 0 FROM orders",
            [
                dict(
                    rule_id="AP11",
                    expression="COUNT(*) > 0",
                    status="boolean_existence_candidate",
                    candidate_sql=sql,
                )
            ],
            "volume_analysis",
        )
        result = present_report(report(payload))
        self.assertIn("первом совпадении", result["brief"])
        self.assertNotIn(sql, result["brief"])
        self.assertIn(sql, result["candidates"])
        self.assertIn("не выполнялись", result["candidates"])

    def test_nullable_not_in_does_not_claim_null_exists_or_generate_sql(self):
        payload = bundle(
            "SELECT id FROM users WHERE id NOT IN (SELECT id FROM orders)",
            [
                dict(
                    rule_id="AP05",
                    expression="id NOT IN (...)",
                    status="null_semantics_requires_review",
                )
            ],
            "predicate_analysis",
        )
        result = summarize(payload)
        self.assertIn("NULL не доказано", result["brief"])
        self.assertEqual(result["candidates"], "")

    def test_independent_tables_have_specific_warning_even_without_operation_finding(self):
        result = summarize(bundle("SELECT u.id, o.id FROM users u CROSS JOIN orders o"))
        self.assertIn("независимые группы", result["brief"])
        self.assertIn("по смыслу задачи", result["brief"])

    def test_top_three_and_hidden_count_keep_distinct_occurrences(self):
        findings = [
            dict(rule_id="AP08", expression=f"OFFSET {i}", status="offset_work_present")
            for i in (10, 20, 30, 40)
        ]
        result = summarize(bundle(findings=findings, block="volume_analysis"))
        self.assertEqual(len(result["findings"]), 3)
        self.assertEqual(result["total_findings"], 4)
        self.assertIn("Других замечаний: 1", result["brief"])
        self.assertNotIn("OFFSET 40", result["brief"])

    def test_observed_index_access_does_not_recommend_new_index(self):
        payload = bundle(
            findings=[dict(rule_id="AP02", risk_assessment="predicate_index_access_observed")],
            block="access_analysis",
        )
        self.assertEqual(summarize(payload)["total_findings"], 0)

    def test_measured_sort_deduplicates_temporary_io_and_keeps_time(self):
        payload = bundle()
        payload.update(
            mode="explain_analyze",
            explain={"analyze": True},
            runtime_analysis=dict(
                status="measured",
                execution_time_ms=123.4,
                node_observations=[
                    dict(path="Plan", disk_sort_observed=True, temporary_io_observed=True)
                ],
            ),
        )
        payload["diagnostic_tree"] = build_diagnostic_tree(payload)
        result = summarize(payload)
        self.assertIn("123.4", result["brief"])
        self.assertEqual(result["total_findings"], 1)
        self.assertIn("использовала диск", result["brief"])

    def test_estimated_plan_cannot_promote_runtime_to_fact(self):
        payload = bundle()
        payload["runtime_analysis"] = dict(
            status="measured",
            execution_time_ms=1,
            node_observations=[dict(path="Plan", disk_sort_observed=True)],
        )
        payload["diagnostic_tree"] = build_diagnostic_tree(payload)
        self.assertNotIn("Измерено", summarize(payload)["brief"])

    def test_legacy_and_damaged_history_remain_readable(self):
        for text in [
            "Старый отчёт",
            MARKER + "{",
            MARKER + "[]",
            MARKER + '{"diagnostic_tree":null}',
        ]:
            result = present_report(text)
            self.assertEqual(result["details"], text)
            self.assertIn("краткий вывод недоступен", result["brief"])

    def test_all_fifteen_rules_have_supported_presentation_or_trace_exclusion(self):
        for i in range(1, 16):
            result = summarize(
                bundle(findings=[dict(rule_id=f"AP{i:02}", expression="test", status="unknown")])
            )
            self.assertEqual(result["total_findings"], 0 if i == 10 else 1)


class PresentationInterfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_restore_resets_tabs_and_edit_error_busy_clear_all_views(self):
        with tempfile.TemporaryDirectory() as directory:
            window = MainWindow(directory)
            try:
                payload = bundle(
                    "SELECT COUNT(*) > 0 FROM orders",
                    [
                        dict(
                            rule_id="AP11",
                            status="boolean_existence_candidate",
                            candidate_sql="SELECT EXISTS (SELECT 1 FROM orders)",
                        )
                    ],
                    "volume_analysis",
                )
                window.restore_query(dict(sql=payload["sql"], result=report(payload)))
                self.assertTrue(window.result_tabs.isTabVisible(2))
                self.assertTrue(window.result_tabs.isTabVisible(3))
                window.result_tabs.setCurrentIndex(3)
                window.editor.insertPlainText(" ")
                self.assertEqual(window.result_tabs.currentIndex(), 0)
                self.assertEqual(window.json_output.toPlainText(), "")
                self.assertFalse(window.result_tabs.isTabVisible(3))
                window.display_analysis_result(report(payload))
                window.show_analysis_error("Отменено")
                self.assertEqual(window.output.toPlainText(), "Отменено")
                self.assertEqual(window.detail_output.toPlainText(), "")
                window.display_analysis_result(report(payload))
                window.show_analysis_busy(True)
                self.assertEqual(window.output.toPlainText(), "")
                self.assertEqual(window.candidate_output.toPlainText(), "")
                window.show_analysis_busy(False)
                window.editor.document().setModified(False)
                window.restore_query(dict(sql="SELECT 1", result="Старый отчёт"))
                self.assertEqual(window.detail_output.toPlainText(), "Старый отчёт")
                self.assertFalse(window.result_tabs.isTabVisible(2))
            finally:
                window.close()
