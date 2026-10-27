import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services.experiment_service import (
    check_results,
    concise_experiment,
    difference_sql,
    run_experiment,
)


class ExperimentTests(unittest.TestCase):
    def database(self, second_type=23):
        connection = MagicMock()
        cursors = []
        for oid in (23, second_type):
            cursor = MagicMock()
            cursor.description = [SimpleNamespace(name="id", type_code=oid)]
            cursors.append(nullcontext(cursor))
        connection.cursor.side_effect = cursors
        database = MagicMock()
        database.connect.return_value = nullcontext(connection)
        return database

    def test_bidirectional_bag_comparison_and_one_statement(self):
        sql = difference_sql("SELECT 1 AS id; -- comment", "SELECT 1 AS id")
        self.assertEqual(sql.count("EXCEPT ALL"), 2)
        self.assertIn("UNION ALL", sql)
        self.assertNotIn(";", sql)

    def test_unsafe_or_multiple_statements_never_connect(self):
        for sql in ("DELETE FROM users", "SELECT 1; SELECT 2"):
            db = MagicMock()
            with self.assertRaises(ValueError):
                run_experiment(db, {}, sql, "SELECT 1")
            db.connect.assert_not_called()

    def test_equal_on_snapshot_sets_transaction_before_reads(self):
        db = self.database()
        with patch("app.services.experiment_service.QueryExecutor") as executor:
            executor.return_value.fetch_one.side_effect = [None, None, {"differs": False}]
            result = check_results(db, {}, "SELECT 1 AS id", "SELECT 1 AS id")
        self.assertEqual(result["status"], "equal_on_snapshot")
        executor.return_value.execute.assert_called_once_with(
            "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
        )

    def test_type_difference_stops_before_row_comparison(self):
        db = self.database(second_type=20)
        with patch("app.services.experiment_service.QueryExecutor") as executor:
            result = check_results(db, {}, "SELECT 1 AS id", "SELECT 1::bigint AS id")
        self.assertEqual(result["status"], "different_contract")
        self.assertEqual(executor.return_value.fetch_one.call_count, 2)

    def test_different_rows_stop_timing(self):
        with (
            patch(
                "app.services.experiment_service.check_results",
                return_value={"status": "different_rows"},
            ),
            patch("app.services.experiment_service.compare_queries") as compare,
        ):
            result = run_experiment(MagicMock(), {}, "SELECT 1", "SELECT 2")
        compare.assert_not_called()
        self.assertIsNone(result["measurements"])
        self.assertIn("Результаты различаются", concise_experiment(result))

    def test_timeout_prevents_comparison(self):
        with (
            patch("app.services.experiment_service.check_results", side_effect=TimeoutError),
            patch("app.services.experiment_service.compare_queries") as compare,
        ):
            with self.assertRaises(TimeoutError):
                run_experiment(MagicMock(), {}, "SELECT 1", "SELECT 1")
        compare.assert_not_called()

    def test_invalid_timeout_or_repeats_never_connect(self):
        db = MagicMock()
        for timeout in (0, True, 30001):
            with self.assertRaises(ValueError):
                run_experiment(db, {}, "SELECT 1", "SELECT 1", timeout_ms=timeout)
        with self.assertRaises(ValueError):
            run_experiment(db, {}, "SELECT 1", "SELECT 1", repeats=6)
        db.connect.assert_not_called()

    def test_equal_runs_timing_and_reports_without_guarantee(self):
        measured = dict(original_median_ms=20, candidate_median_ms=15, session_context_matches=True)
        with (
            patch(
                "app.services.experiment_service.check_results",
                return_value={"status": "equal_on_snapshot"},
            ),
            patch(
                "app.services.experiment_service.compare_queries", return_value=measured
            ) as compare,
        ):
            result = run_experiment(MagicMock(), {}, "SELECT 1", "SELECT 1")
        compare.assert_called_once()
        self.assertIn("20.00", concise_experiment(result))
        self.assertIn("не доказано", concise_experiment(result))
