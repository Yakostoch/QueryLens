import unittest
from copy import deepcopy
from unittest.mock import MagicMock, patch

from app.services.query_structure import query_structure, QueryStructureError


class QueryStructureTests(unittest.TestCase):
    def setUp(self):
        self.tables = {}
        for oid, (schema, name, fields) in enumerate([
            ("public", "users", ["id", "name", "active", "unused"]),
            ("public", "orders", ["id", "user_id", "amount"]),
            ("Case", "People", ["ID", "Name"]),
        ], 1):
            table = dict(oid=oid, schema=schema, name=name, columns=len(fields), indexes=1,
                         fields=[dict(name=field, type="text", nullable=True) for field in fields])
            self.tables[f'"{schema}"."{name}"'] = table
            self.tables[f'"{name}"'] = table

    def resolve(self, name):
        return deepcopy(self.tables.get(name))

    def used(self, sql):
        return {row["name"]: {field["name"] for field in row["fields"]}
                for row in query_structure(sql, self.resolve)}

    def test_join_filters_group_order_and_alias(self):
        self.assertEqual(self.used("SELECT u.name, sum(o.amount) total FROM users u "
                                   "JOIN orders o ON o.user_id=u.id WHERE u.active "
                                   "GROUP BY u.name ORDER BY total"),
                         {"users": {"name", "id", "active"}, "orders": {"amount", "user_id"}})

    def test_cte_subquery_and_correlated_reference(self):
        self.assertEqual(self.used("WITH chosen AS (SELECT id, name FROM users WHERE active) "
                                   "SELECT c.name FROM chosen c WHERE EXISTS "
                                   "(SELECT 1 FROM orders o WHERE o.user_id = c.id)"),
                         {"users": {"id", "name", "active"}, "orders": {"user_id"}})
        self.assertEqual(self.used("SELECT u.name FROM users u WHERE EXISTS "
                                   "(SELECT 1 FROM orders o WHERE o.user_id = u.id)"),
                         {"users": {"id", "name"}, "orders": {"user_id"}})

    def test_star_count_and_no_tables(self):
        self.assertEqual(self.used("SELECT u.* FROM users u"), {"users": {"id", "name", "active", "unused"}})
        self.assertEqual(self.used("SELECT count(*) FROM users"), {"users": set()})
        self.assertEqual(self.used("SELECT 1"), {})

    def test_quoted_identifiers_and_using(self):
        self.assertEqual(self.used('SELECT "Name" FROM "Case"."People" WHERE "ID" = 1'),
                         {"People": {"Name", "ID"}})
        self.assertEqual(self.used("SELECT users.name FROM users JOIN orders USING (id)"),
                         {"users": {"name", "id"}, "orders": {"id"}})

    def test_invalid_ambiguous_multiple_or_unsupported_are_explicit_errors(self):
        for sql in ("", "SELECT 1; SELECT 2", "DELETE FROM users", "SELECT missing FROM users",
                    "SELECT id FROM users JOIN orders ON users.id=orders.user_id",
                    "SELECT * FROM missing", "SELECT * FROM users NATURAL JOIN orders"):
            with self.subTest(sql=sql), self.assertRaises(QueryStructureError):
                self.used(sql)

    def test_metadata_reads_only_referenced_relation_and_filters_fields(self):
        from app.collectors.metadata import collect_metadata
        table = deepcopy(self.tables['"users"'])
        with patch("app.collectors.metadata.QueryExecutor") as executor_class:
            executor = executor_class.return_value
            executor.fetch_one.side_effect = [{"version": "test"}, {}, table, {"bytes": 100}]
            executor.fetch_all.return_value = table["fields"]
            report = collect_metadata(MagicMock(), scope="query", sql="SELECT name FROM users WHERE active")
        self.assertEqual([f["name"] for f in report["tables"][0]["fields"]], ["name", "active"])
        self.assertEqual(report["tables"][0]["columns"], 2)
        self.assertEqual(executor.fetch_one.call_args_list[2].args[1], ('"users"',))
        self.assertEqual(executor.fetch_all.call_count, 1)
        for call in executor.fetch_one.call_args_list + executor.fetch_all.call_args_list:
            self.assertNotIn("SELECT name FROM users", call.args[0])


if __name__ == "__main__":
    unittest.main()
