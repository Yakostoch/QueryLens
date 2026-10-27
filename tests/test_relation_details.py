import unittest
from unittest.mock import MagicMock, patch

from app.collectors.relation_details import collect_relation_details
from app.services.report_formatter import format_relation_details

TABLE = {"oid": 42, "schema": "pgdata", "name": 'odd " table'}


def index_element(position=1, **overrides):
    row = {
        "oid": 50,
        "schema": "pgdata",
        "name": "idx",
        "access_method": "btree",
        "definition": "CREATE INDEX idx ...",
        "predicate": "active",
        "is_unique": False,
        "is_primary": False,
        "is_valid": True,
        "is_ready": True,
        "is_live": True,
        "is_immediate": True,
        "nulls_not_distinct": None,
        "position": position,
        "is_key": True,
        "column_name": "email",
        "expression": None,
        "operator_class_schema": "pg_catalog",
        "operator_class": "text_ops",
        "collation_schema": "pg_catalog",
        "collation": "default",
        "direction": "ASC",
        "nulls_first": False,
    }
    return dict(row, **overrides)


class RelationDetailsTests(unittest.TestCase):
    def collect(self, index_rows=None, constraints=None, stats=None, relation=None):
        connection = MagicMock()
        executor = MagicMock()
        executor.fetch_all.side_effect = [index_rows or [], constraints or [], stats or []]
        executor.fetch_one.return_value = (
            relation if relation is not None else {"estimated_rows": 100}
        )
        with patch("app.collectors.relation_details.QueryExecutor", return_value=executor):
            result = collect_relation_details(connection, TABLE)
        return result, executor, connection

    def test_expression_partial_index_and_include_preserve_key_order(self):
        result, _, _ = self.collect(
            index_rows=[
                index_element(column_name=None, expression="lower(email)"),
                index_element(2, column_name="created_at", direction="DESC", nulls_first=True),
                index_element(3, column_name="id", is_key=False),
            ]
        )
        indexes = result["sources"]["indexes"]["data"]
        self.assertEqual(len(indexes), 1)
        index = indexes[0]
        self.assertEqual([x["position"] for x in index["keys"]], [1, 2])
        self.assertEqual(index["keys"][0]["expression"], "lower(email)")
        self.assertEqual(index["keys"][1]["direction"], "DESC")
        self.assertEqual(index["included_columns"], ["id"])
        self.assertEqual(index["predicate"], "active")
        self.assertIsNone(index["nulls_not_distinct"])
        self.assertNotIn("is_key", index)

    def test_trigram_opclass_does_not_imply_applicability(self):
        result, _, _ = self.collect(
            index_rows=[
                index_element(
                    access_method="gin",
                    operator_class="gin_trgm_ops",
                    direction=None,
                    nulls_first=None,
                )
            ]
        )
        index = result["sources"]["indexes"]["data"][0]
        self.assertEqual(index["keys"][0]["operator_class"], "gin_trgm_ops")
        self.assertNotIn("applicable", index)

    def test_empty_indexes_are_known_but_empty_column_stats_are_unavailable(self):
        result, _, _ = self.collect()
        self.assertEqual(result["sources"]["indexes"], {"status": "ok", "data": [], "error": None})
        self.assertEqual(result["sources"]["column_statistics"]["status"], "unavailable")

    def test_failed_index_source_does_not_discard_other_facts_or_error_text(self):
        connection, executor = MagicMock(), MagicMock()
        executor.fetch_all.side_effect = [RuntimeError("password=secret"), [{"kind": "p"}], []]
        executor.fetch_one.return_value = {"estimated_rows": 10}
        with patch("app.collectors.relation_details.QueryExecutor", return_value=executor):
            result = collect_relation_details(connection, TABLE)
        self.assertEqual(
            result["sources"]["indexes"], {"status": "error", "data": None, "error": "RuntimeError"}
        )
        self.assertEqual(result["sources"]["constraints"]["status"], "ok")
        self.assertEqual(result["sources"]["relation"]["data"]["estimated_rows"], 10)
        self.assertEqual(connection.transaction.call_count, 4)
        exits = connection.transaction.return_value.__exit__.call_args_list
        self.assertIs(exits[0].args[0], RuntimeError)
        self.assertNotIn("secret", str(result))

    def test_names_and_oid_are_parameters_not_sql_interpolation(self):
        _, executor, _ = self.collect()
        calls = executor.fetch_all.call_args_list
        self.assertEqual(calls[0].args[1], (42,))
        self.assertEqual(calls[1].args[1], (42,))
        self.assertEqual(calls[2].args[1], (TABLE["schema"], TABLE["name"]))
        self.assertNotIn(TABLE["name"], calls[2].args[0])

    def test_negative_ndistinct_and_unavailable_properties_stay_raw(self):
        result, _, _ = self.collect(
            stats=[{"column_name": "id", "n_distinct": -1.0}], constraints=[{"is_enforced": None}]
        )
        self.assertEqual(result["sources"]["column_statistics"]["data"][0]["n_distinct"], -1.0)
        self.assertIsNone(result["sources"]["constraints"]["data"][0]["is_enforced"])
        self.assertEqual(result["size_scope"], "relation_only")

    def test_invalid_oid_rejected_before_database_access(self):
        for oid in (True, 0, -1, "42"):
            with self.subTest(oid=oid), self.assertRaises(ValueError):
                collect_relation_details(MagicMock(), dict(TABLE, oid=oid))

    def test_report_shows_index_definitions_and_distinguishes_failure_from_zero(self):
        result, _, _ = self.collect(index_rows=[index_element(is_valid=False)])
        table = dict(TABLE, diagnostics=result)
        text = "\n".join(format_relation_details(table))
        self.assertIn("CREATE INDEX", text)
        self.assertIn("невалиден", text)
        result["sources"]["indexes"] = {"status": "error", "data": None, "error": "PermissionError"}
        text = "\n".join(format_relation_details(table))
        self.assertIn("Индексы: сведения недоступны", text)
        self.assertNotIn("Индексы: 0", text)

    def test_user_schema_pgdata_is_not_excluded_as_system_schema(self):
        from app.collectors.metadata import collect_metadata

        table = dict(TABLE, columns=1, indexes=0)
        with (
            patch("app.collectors.metadata.QueryExecutor") as executor_class,
            patch("app.collectors.metadata.collect_relation_details", return_value={}),
        ):
            executor = executor_class.return_value
            executor.fetch_one.side_effect = [{"version": "test"}, {}, {"bytes": 10}]
            executor.fetch_all.side_effect = [
                [table],
                [{"name": "id", "type": "integer", "nullable": False}],
            ]
            result = collect_metadata(MagicMock())
        self.assertIn("left(n.nspname, 3) <> 'pg_'", executor.fetch_all.call_args_list[0].args[0])
        self.assertEqual(result["tables"][0]["schema"], "pgdata")
        self.assertEqual(result["tables"][0]["schema_columns"], 1)


if __name__ == "__main__":
    unittest.main()
