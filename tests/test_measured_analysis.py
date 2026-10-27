import json
import tempfile
import threading
import unittest
from contextlib import nullcontext
from unittest.mock import MagicMock, patch

from analysis_support import connect_fake_database, wait_analysis
from PySide6 import QtWidgets

from app.analyzers.plan_features import extract_plan_features
from app.analyzers.runtime_analysis import analyze_runtime
from app.collectors.explain import collect_explain
from app.services.analysis_execution import AnalysisCancellation, AnalysisCancelled
from app.services.analysis_service import AnalysisService
from app.services.query_comparison import compare_queries
from app.ui.main_window import MainWindow
from app.workers.analysis_worker import AnalysisWorker


def document(**values):
    return [
        {
            "Plan": {
                "Node Type": "Seq Scan",
                "Plan Rows": 10,
                "Plan Width": 8,
                "Actual Rows": 4,
                "Actual Loops": 3,
                **values,
            },
            "Execution Time": 1.25,
            "Planning Time": 0.2,
        }
    ]


def connection():
    c = MagicMock()
    c.__enter__.return_value = c
    c.cursor.return_value.__enter__.return_value.fetchone.return_value = {"QUERY PLAN": document()}
    return c


def runtime(**values):
    return analyze_runtime(extract_plan_features(document(**values)), measured=True)


class MeasuredCollectorTests(unittest.TestCase):
    def test_default_does_not_execute_analyze(self):
        c = connection()
        r = collect_explain(c, "SELECT 1")
        calls = c.cursor.return_value.__enter__.return_value.execute.call_args_list
        self.assertEqual(len(calls), 1)
        self.assertNotIn("ANALYZE", calls[0].args[0])
        self.assertFalse(r["analyze"])

    def test_readonly_and_timeouts_precede_measured_statement(self):
        c = connection()
        r = collect_explain(c, "SELECT 1", analyze=True, timeout_ms=2000)
        calls = c.cursor.return_value.__enter__.return_value.execute.call_args_list
        self.assertEqual(calls[0].args[0], "SET TRANSACTION READ ONLY")
        self.assertEqual(calls[1].args[1], ("2000",))
        self.assertEqual(calls[2].args[1], ("1000",))
        self.assertIn("ANALYZE, BUFFERS", calls[3].args[0])
        self.assertTrue(r["analyze"])
        self.assertTrue(all(x.kwargs["prepare"] for x in calls))

    def test_invalid_input_cannot_reach_driver(self):
        for sql in [
            "SELECT 1; SELECT 2",
            "DELETE FROM users",
            "SELECT id INTO z FROM users",
            "SELECT id FROM users FOR UPDATE",
        ]:
            c = connection()
            with self.assertRaises(ValueError):
                collect_explain(c, sql, analyze=True)
            c.cursor.assert_not_called()

    def test_invalid_timeout_cannot_reach_driver(self):
        for timeout in [True, 0, 30001, "5000"]:
            c = connection()
            with self.assertRaises(ValueError):
                collect_explain(c, "SELECT 1", analyze=True, timeout_ms=timeout)
            c.cursor.assert_not_called()

    def test_readonly_setup_failure_prevents_execution(self):
        c = connection()
        c.cursor.return_value.__enter__.return_value.execute.side_effect = RuntimeError("private")
        with self.assertRaises(RuntimeError):
            collect_explain(c, "SELECT 1", analyze=True)
        self.assertEqual(c.cursor.return_value.__enter__.return_value.execute.call_count, 1)


class RuntimeTests(unittest.TestCase):
    def test_estimated_plan_never_becomes_measured(self):
        r = analyze_runtime(extract_plan_features(document()), measured=False)
        self.assertEqual(r["status"], "not_measured")
        self.assertIsNone(r["execution_time_ms"])
        self.assertEqual(r["node_observations"], [])

    def test_rows_are_averaged_per_loop_not_distinct_output(self):
        n = runtime()["node_observations"][0]
        self.assertEqual(n["actual_rows_per_loop"], 4)
        self.assertEqual(n["approximate_total_output_rows"], 12)
        self.assertEqual(n["estimate_to_actual_ratio"], 2.5)

    def test_buffers_not_multiplied_by_loops_or_summed_with_children(self):
        n = runtime(**{"Shared Hit Blocks": 7, "Temp Written Blocks": 4})["node_observations"][0]
        self.assertEqual(n["runtime_counters"]["Shared Hit Blocks"], 7)
        self.assertTrue(n["temporary_io_observed"])

    def test_limit_parallel_and_anti_disable_cardinality_claims(self):
        for values in [
            {"Node Type": "Limit"},
            {"Node Type": "Gather"},
            {"Join Type": "Anti"},
            {"Parent Relationship": "SubPlan"},
        ]:
            n = runtime(**values)["node_observations"][0]
            self.assertIsNone(n["estimate_to_actual_ratio"])
            self.assertEqual(n["row_comparison_status"], "not_comparable")

    def test_never_executed_node_is_not_estimation_error(self):
        n = runtime(**{"Actual Rows": 0, "Actual Loops": 0})["node_observations"][0]
        self.assertEqual(n["row_comparison_status"], "not_executed")

    def test_disk_sort_requires_measured_disk_evidence(self):
        a = runtime(
            **{
                "Node Type": "Sort",
                "Sort Space Type": "Disk",
                "Sort Method": "external merge",
                "Sort Space Used": 56,
            }
        )["node_observations"][0]
        b = runtime(**{"Node Type": "Sort"})["node_observations"][0]
        self.assertTrue(a["disk_sort_observed"])
        self.assertFalse(b["disk_sort_observed"])
        self.assertIsNone(b["sort_space_used_kb"])

    def test_worker_counters_preserved_without_double_counting(self):
        workers = [{"Worker Number": 0, "Actual Rows": 8, "Sort Space Type": "Disk"}]
        n = runtime(Workers=workers, **{"Workers Planned": 2, "Workers Launched": 1})[
            "node_observations"
        ][0]
        self.assertTrue(n["worker_disk_sort_observed"])
        self.assertEqual(n["approximate_total_output_rows"], 12)
        self.assertEqual(n["workers_launched"], 1)
        self.assertEqual(n["worker_details"], workers)

    def test_hashagg_batches_and_new_io_names_preserved(self):
        n = runtime(**{"HashAgg Batches": 3, "Shared I/O Read Time": 1.2})["node_observations"][0]
        self.assertTrue(n["multi_batch_hash_observed"])
        self.assertEqual(n["runtime_counters"]["Shared I/O Read Time"], 1.2)

    def test_measured_service_report_preserves_mode_and_actual_metrics(self):
        service = AnalysisService(MagicMock(connect=lambda p: nullcontext(connection())))
        sources = {
            "metadata": {"status": "error", "data": None, "error": "Unavailable"},
            "configuration": {"status": "error", "data": None, "error": "Unavailable"},
        }
        with patch("app.services.analysis_service.collect_analysis_context", return_value=sources):
            text = service.analyze("SELECT 1", {}, analyze=True)
        payload = json.loads(text.split("ДИАГНОСТИЧЕСКИЕ ДАННЫЕ В JSON\n")[1])
        self.assertEqual(payload["mode"], "explain_analyze")
        self.assertEqual(payload["runtime_analysis"]["execution_time_ms"], 1.25)
        self.assertEqual(payload["diagnostic_tree"]["measurement_status"], "measured")
        self.assertEqual(payload["diagnostic_tree"]["summary"]["rules_total"], 15)
        self.assertIn("ДЕРЕВО ДИАГНОСТИКИ", text)
        self.assertIn("ИЗМЕРЕНИЯ ВЫПОЛНЕНИЯ", text)
        self.assertNotIn("Фактическое время выполнения не измерялось.", text)


class CancellationTests(unittest.TestCase):
    def test_cancel_before_connect_stops_work(self):
        token = AnalysisCancellation()
        token.cancel()
        db = MagicMock()
        with self.assertRaises(AnalysisCancelled):
            AnalysisService(db).analyze("SELECT 1", {}, cancellation=token)
        db.connect.assert_not_called()

    def test_cancel_request_is_sent_off_calling_thread(self):
        token = AnalysisCancellation()
        called = threading.Event()
        ids = []
        c = MagicMock()
        c.cancel_safe.side_effect = lambda **kw: (ids.append(threading.get_ident()), called.set())
        token.bind(c)
        token.cancel()
        self.assertTrue(called.wait(1))
        self.assertNotEqual(ids[0], threading.get_ident())
        token.unbind()
        with self.assertRaises(AnalysisCancelled):
            token.check()

    def test_timeout_error_message_contains_no_driver_text(self):
        class Timeout(Exception):
            sqlstate = "57014"

        service = MagicMock()
        service.analyze.side_effect = Timeout("private-password")
        worker = AnalysisWorker(service, "SELECT 1", {}, "test", analyze=True)
        messages = []
        worker.failed.connect(messages.append)
        worker.run()
        self.assertIn("тайм-аут", messages[0])
        self.assertNotIn("private", messages[0])
        self.assertIsNone(worker.parameters)

    def test_unbind_prevents_cancelling_closed_connection(self):
        c = MagicMock()
        token = AnalysisCancellation()
        token.bind(c)
        token.unbind()
        token.cancel()
        c.cancel_safe.assert_not_called()


class ComparisonTests(unittest.TestCase):
    def test_alternating_runs_and_medians_without_equivalence_claim(self):
        times = [9, 3, 5, 11, 10, 4]
        seen = []

        def explain(c, sql, **options):
            seen.append(sql)
            d = document()
            d[0]["Execution Time"] = times[len(seen) - 1]
            return {"plan": d}

        with (
            patch("app.services.query_comparison.collect_explain", side_effect=explain),
            patch(
                "app.services.query_comparison.QueryExecutor.fetch_one",
                return_value={"read_only": "on"},
            ),
        ):
            result = compare_queries(
                MagicMock(connect=lambda p: nullcontext(connection())), {}, "SELECT 1", "SELECT 2"
            )
        self.assertEqual(
            seen, ["SELECT 1", "SELECT 2", "SELECT 2", "SELECT 1", "SELECT 1", "SELECT 2"]
        )
        self.assertEqual(result["original_median_ms"], 10)
        self.assertEqual(result["candidate_median_ms"], 4)
        self.assertEqual(result["equivalence_status"], "not_checked")

    def test_different_session_context_suppresses_ratio(self):
        ex = {"plan": document()}
        with (
            patch("app.services.query_comparison.collect_explain", return_value=ex),
            patch(
                "app.services.query_comparison.QueryExecutor.fetch_one",
                side_effect=[{"work_mem": "4MB"}, {"work_mem": "8MB"}],
            ),
        ):
            result = compare_queries(
                MagicMock(connect=lambda p: nullcontext(connection())),
                {},
                "SELECT 1",
                "SELECT 2",
                repeats=1,
            )
        self.assertIsNone(result["median_ratio_original_to_candidate"])

    def test_invalid_repeats_do_not_execute(self):
        db = MagicMock()
        for repeat in [0, True, 6]:
            with self.assertRaises(ValueError):
                compare_queries(db, {}, "SELECT 1", "SELECT 2", repeats=repeat)
        db.connect.assert_not_called()


class MeasuredInterfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.window = MainWindow(self.temp.name)
        connect_fake_database(self.window.controller)
        self.window.editor.setPlainText("SELECT 1")

    def tearDown(self):
        self.window.controller.close()
        wait_analysis(self.window.controller)
        self.window.close()
        self.temp.cleanup()

    def test_default_is_explain_and_cancel_confirmation_does_not_start_worker(self):
        w = self.window
        self.assertFalse(w.measure_checkbox.isChecked())
        w.measure_checkbox.setChecked(True)
        with patch.object(
            QtWidgets.QMessageBox,
            "question",
            return_value=QtWidgets.QMessageBox.StandardButton.Cancel,
        ):
            w.analyze()
        self.assertIsNone(w.controller.analysis_worker)
        self.assertEqual(w.controller.history.store.search(), [])

    def test_confirmation_starts_measurement_and_history_records_mode(self):
        w = self.window
        w.measure_checkbox.setChecked(True)
        with patch.object(
            QtWidgets.QMessageBox, "question", return_value=QtWidgets.QMessageBox.StandardButton.Yes
        ):
            w.analyze()
        wait_analysis(w.controller)
        self.assertIn("EXPLAIN ANALYZE", w.detail_output.toPlainText())
        self.assertTrue(w.measure_checkbox.isEnabled())
        self.assertIn("ИЗМЕРЕНИЯ ВЫПОЛНЕНИЯ", w.controller.history.store.search()[0]["result"])

    def test_cancelled_active_worker_is_not_saved(self):
        w = self.window
        entered = threading.Event()
        release = threading.Event()

        def analyze(*args, cancellation, **kw):
            c = MagicMock()
            c.cancel_safe.side_effect = lambda **options: release.set()
            cancellation.bind(c)
            entered.set()
            release.wait(2)
            cancellation.check()
            return "must not be saved"

        w.controller.analysis_service = MagicMock()
        w.controller.analysis_service.analyze.side_effect = analyze
        w.analyze()
        self.assertTrue(entered.wait(1))
        w.controller.cancel_analysis()
        wait_analysis(w.controller)
        self.assertEqual(w.controller.history.store.search(), [])
        self.assertIn("отменён", w.output.toPlainText())
