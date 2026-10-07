import unittest
from unittest.mock import MagicMock, patch

from app.collectors.explain import collect_explain
from app.collectors.statements import collect_statements, StatementsUnavailable
from app.collectors.metadata import collect_metadata
from app.services.collection_service import collect_features


class FeatureCollectionTests(unittest.TestCase):
    def test_sources_are_isolated_and_connections_are_closed(self):
        connections = [MagicMock() for _ in range(4)]
        with patch("app.services.collection_service.connect_postgresql", side_effect=connections) as connect, \
                patch("app.services.collection_service.collect_explain", side_effect=RuntimeError("secret")), \
                patch("app.services.collection_service.collect_statements", side_effect=StatementsUnavailable()), \
                patch("app.services.collection_service.collect_metadata", return_value={"tables": []}), \
                patch("app.services.collection_service.collect_configuration", return_value={"settings": []}), \
                patch("app.services.collection_service.collect_system", return_value={"samples": []}):
            result = collect_features({"password": "secret"}, sql="SELECT 1", query_id=42)
        sources = result["sources"]
        self.assertEqual(sources["explain"]["status"], "error")
        self.assertEqual(sources["pg_stat_statements"]["status"], "unavailable")
        for name in ("metadata", "configuration", "system"):
            self.assertEqual(sources[name]["status"], "ok")
        self.assertNotIn("secret", str(result))
        self.assertEqual(connect.call_count, 4)
        for connection in connections:
            connection.__exit__.assert_called_once()

    def test_no_sql_or_query_id_skips_sources_and_hardware_needs_no_database(self):
        with patch("app.services.collection_service.connect_postgresql") as connect, \
                patch("app.services.collection_service.collect_system", return_value={"samples": []}):
            result = collect_features(None, configuration=False, metadata=False)
        connect.assert_not_called()
        for name in ("explain", "pg_stat_statements", "metadata", "configuration"):
            self.assertEqual(result["sources"][name]["status"], "skipped")
        self.assertEqual(result["sources"]["system"]["status"], "ok")

    def test_cancel_between_sources_prevents_next_connection(self):
        with patch("app.services.collection_service.connect_postgresql", return_value=MagicMock()) as connect, \
                patch("app.services.collection_service.collect_metadata", return_value={}), \
                patch("app.services.collection_service.collect_configuration") as configuration:
            # skipped EXPLAIN, skipped statements, metadata, then cancellation
            result = collect_features({}, cancelled=MagicMock(side_effect=[False, False, False, True]))
        self.assertIsNone(result)
        self.assertEqual(connect.call_count, 1)
        configuration.assert_not_called()

    def test_explain_uses_prepared_single_command_without_analyze(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = {"QUERY PLAN": [{"Plan": {"Node Type": "Result"}}]}
        result = collect_explain(connection, "SELECT 1;")
        cursor.execute.assert_called_once_with("EXPLAIN (FORMAT JSON) SELECT 1;", None, prepare=True)
        self.assertFalse(result["analyze"])
        self.assertEqual(result["plan"][0]["Plan"]["Node Type"], "Result")
        with self.assertRaises(ValueError):
            collect_explain(connection, " ")

    def test_statement_lookup_uses_extension_schema_and_query_id_parameter(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = {"schema": "custom stats"}
        cursor.fetchall.return_value = []
        result = collect_statements(connection, 42)
        statement, parameters = cursor.execute.call_args.args
        self.assertIn('"custom stats".pg_stat_statements', statement.as_string())
        self.assertIn("current_database()", statement.as_string())
        self.assertEqual(parameters, (42,))
        self.assertEqual(result["statements"], [])
        cursor.fetchone.return_value = None
        with self.assertRaises(StatementsUnavailable):
            collect_statements(connection, 42)

    def test_metadata_size_failure_preserves_tables_and_statistics(self):
        connection = MagicMock()
        with patch("app.collectors.metadata.QueryExecutor") as executor_class:
            executor = executor_class.return_value
            executor.fetch_one.side_effect = [{"version": "test"}, {"blks_hit": 5}, RuntimeError()]
            executor.fetch_all.return_value = [{"name": "users", "indexes": 1}]
            result = collect_metadata(connection)
        self.assertIsNone(result["database_size_bytes"])
        self.assertEqual(result["tables"][0]["name"], "users")
        self.assertEqual(result["database_statistics"]["blks_hit"], 5)


if __name__ == "__main__":
    unittest.main()
