"""Проверки без PostgreSQL, Qt и сетевых подключений."""

import importlib
import json
import sys
import types
import unittest
from unittest.mock import patch

from app.analyzers.plan_features import PlanFormatError, extract_plan_features
from app.analyzers.sql_features import SQLInputError, extract_sql_features, parse_query


class SQLFeaturesTests(unittest.TestCase):
    def features(self, sql):
        return extract_sql_features(sql)["features"]

    def test_star_and_count_are_separate(self):
        features = self.features("SELECT p.*, COUNT(*) OVER () FROM products p")
        self.assertEqual(len(features["select_stars"]), 1)
        self.assertEqual(features["select_stars"][0]["qualifier"], "p")
        self.assertEqual(len(features["counts"]), 1)
        self.assertTrue(features["counts"][0]["is_count_star"])
        self.assertEqual(len(features["windows"]), 1)

    def test_comments_and_literals_are_not_constructs(self):
        features = self.features("SELECT 'SELECT *; OFFSET 100; LIKE %' /* DISTINCT */")
        self.assertFalse(any(features.values()))

    def test_quoted_identifier_is_not_star(self):
        self.assertEqual(self.features('SELECT "*" FROM t')["select_stars"], [])

    def test_column_function_but_not_constant_function(self):
        features = self.features(
            "SELECT id FROM users WHERE LOWER(email) = 'x' " "AND created_at >= to_timestamp(100)"
        )
        self.assertEqual(len(features["where_expressions"]), 1)
        self.assertEqual(features["where_expressions"][0]["referenced_columns"], ["email"])

    def test_arithmetic_cast_and_scope(self):
        result = extract_sql_features(
            "SELECT id FROM users WHERE price + 1 > 2 AND id IN "
            "(SELECT user_id FROM orders WHERE CAST(code AS TEXT) = 'x')"
        )
        items = result["features"]["where_expressions"]
        self.assertEqual(len(items), 2)
        self.assertEqual(len({item["scope_id"] for item in items}), 2)

    def test_leading_wildcard(self):
        p = self.features("SELECT id FROM t WHERE name ILIKE '%phone%'")["pattern_predicates"][0]
        self.assertTrue(p["has_leading_wildcard"])
        self.assertEqual(p["longest_literal_run_length"], 5)
        self.assertEqual(p["operator"], "ILIKE")

    def test_escaped_percent_and_prefix(self):
        for condition in ["name LIKE '!%phone%' ESCAPE '!'", "name LIKE 'phone%'"]:
            with self.subTest(condition=condition):
                p = self.features("SELECT id FROM t WHERE " + condition)["pattern_predicates"][0]
                self.assertFalse(p["has_leading_wildcard"])

    def test_empty_and_percent_escape(self):
        p = self.features("SELECT id FROM t WHERE name LIKE '%x' ESCAPE ''")["pattern_predicates"][
            0
        ]
        self.assertTrue(p["has_leading_wildcard"])
        p = self.features("SELECT id FROM t WHERE name LIKE '%%x' ESCAPE '%'")[
            "pattern_predicates"
        ][0]
        self.assertFalse(p["has_leading_wildcard"])

    def test_unknown_pattern_and_escape(self):
        for condition in ["name LIKE $1", "name LIKE '%x' ESCAPE $1", "name LIKE '%' || $1 || '%'"]:
            with self.subTest(condition=condition):
                p = self.features("SELECT id FROM t WHERE " + condition)["pattern_predicates"][0]
                self.assertIsNone(p["has_leading_wildcard"])

    def test_or_not_in_distinct_having_offset(self):
        f = self.features(
            "SELECT DISTINCT category, COUNT(*) FROM t "
            "WHERE (a=1 OR b=2 OR c=3) AND id NOT IN (SELECT id FROM banned) "
            "GROUP BY category HAVING COUNT(*) > 1 ORDER BY category LIMIT 5 OFFSET 20"
        )
        self.assertEqual(len(f["or_groups"]), 1)
        self.assertEqual(len(f["not_in_subqueries"]), 1)
        self.assertEqual(f["distinct"][0]["distinct_kind"], "DISTINCT")
        self.assertEqual(len(f["having"]), 1)
        self.assertEqual(f["offsets"][0]["offset_value"], 20)
        self.assertEqual(f["orders"][0]["purpose"], "query")

    def test_unknown_offset(self):
        self.assertIsNone(self.features("SELECT id FROM t OFFSET $1")["offsets"][0]["offset_value"])

    def test_distinct_on(self):
        f = self.features("SELECT DISTINCT ON (user_id) id FROM orders ORDER BY user_id, id")
        self.assertEqual(f["distinct"][0]["distinct_kind"], "DISTINCT ON")

    def test_cte_sources_not_treated_as_real_relations(self):
        result = extract_sql_features(
            "WITH recent AS MATERIALIZED (SELECT id FROM orders) SELECT * FROM recent"
        )
        self.assertTrue(result["features"]["ctes"][0]["materialized"])
        references = [
            s["reference"] for s in result["sources"] if s["source_kind"] == "relation_reference"
        ]
        self.assertEqual(references, ["orders"])
        self.assertTrue(any(s["source_kind"] == "derived_or_cte" for s in result["sources"]))

    def test_recursive_cte_syntax_is_supported_without_catalog_resolution(self):
        f = self.features(
            "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x<3) "
            "SELECT x FROM n"
        )
        self.assertTrue(f["ctes"][0]["recursive"])

    def test_window_order_is_not_result_order(self):
        f = self.features("SELECT ROW_NUMBER() OVER (ORDER BY id) FROM t ORDER BY id DESC")
        self.assertEqual(sorted(item["purpose"] for item in f["orders"]), ["query", "window"])

    def test_join_without_on_is_not_diagnosed_as_cartesian(self):
        f = self.features("SELECT a.id FROM a, b WHERE a.id=b.id")
        self.assertFalse(f["joins"][0]["has_on"])
        self.assertEqual(f["joins"][0]["cartesian_assessment"], "not_assessed")

    def test_union_and_semicolon_in_string(self):
        result = extract_sql_features("SELECT ';' AS value UNION ALL SELECT 'x'; -- end")
        self.assertEqual(len(result["scopes"]), 2)
        json.dumps(result, ensure_ascii=False)

    def test_input_policy(self):
        for sql in [
            "",
            "-- only comment",
            "SELECT 1; SELECT 2",
            "DELETE FROM t",
            "WITH d AS (DELETE FROM t RETURNING id) SELECT id FROM d",
            "SELECT * INTO new_table FROM t",
            "SELECT id FROM t FOR UPDATE",
            "EXPLAIN SELECT 1",
            "SELECT (1",
        ]:
            with self.subTest(sql=sql), self.assertRaises(SQLInputError):
                parse_query(sql)


class PlanFeaturesTests(unittest.TestCase):
    def test_nodes_keep_own_metrics_and_path(self):
        result = extract_plan_features(
            [
                {
                    "Plan": {
                        "Node Type": "Limit",
                        "Plan Rows": 5,
                        "Plan Width": 8,
                        "Total Cost": 2,
                        "Plans": [
                            {
                                "Node Type": "Seq Scan",
                                "Plan Rows": 1000,
                                "Plan Width": 8,
                                "Total Cost": 100,
                                "Filter": "(id > 0)",
                            }
                        ],
                    }
                }
            ]
        )
        self.assertEqual(result["summary"]["estimated_bytes"], 40)
        self.assertEqual(result["nodes"][1]["estimated_bytes"], 8000)
        self.assertEqual(result["nodes"][1]["parent_path"], "Plan")
        self.assertEqual(result["nodes"][1]["path"], "Plan.Plans[0]")
        self.assertIsNone(result["execution_time_ms"])
        self.assertIsNone(result["nodes"][1]["actual_rows"])

    def test_plan_without_width(self):
        result = extract_plan_features([{"Plan": {"Node Type": "Result"}}])
        self.assertIsNone(result["summary"]["estimated_bytes"])

    def test_invalid_plan(self):
        for plan in [[], {}, [None], [{}], [{"Plan": []}], [{"Plan": {"Plans": [None]}}]]:
            with self.subTest(plan=plan), self.assertRaises(PlanFormatError):
                extract_plan_features(plan)


class ServiceIntegrationTests(unittest.TestCase):
    """Проверяет сервис и сборщик с заменой соединения базы."""

    def setUp(self):
        self.executed = []
        executed = self.executed

        class Executor:
            def __init__(self, connection):
                pass

            def fetch_one(self, statement):
                executed.append(statement)
                return {
                    "QUERY PLAN": [
                        {
                            "Plan": {
                                "Node Type": "Seq Scan",
                                "Relation Name": "products",
                                "Plan Rows": 10,
                                "Plan Width": 8,
                                "Total Cost": 10,
                            }
                        }
                    ]
                }

        executor_module = types.ModuleType("app.database.query_executor")
        executor_module.QueryExecutor = Executor
        database_module = types.ModuleType("app.services.database_service")
        database_module.DatabaseService = object
        self.saved_modules = {
            name: sys.modules.get(name)
            for name in ("app.collectors.explain", "app.services.analysis_service")
        }
        for name in self.saved_modules:
            sys.modules.pop(name, None)
        self.module_patch = patch.dict(
            sys.modules,
            {
                "app.database.query_executor": executor_module,
                "app.services.database_service": database_module,
            },
        )
        self.module_patch.start()
        self.service_module = importlib.import_module("app.services.analysis_service")

    def tearDown(self):
        self.module_patch.stop()
        for name, old in self.saved_modules.items():
            if old is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old

    def service(self):
        from contextlib import nullcontext

        class Database:
            def connect(self, parameters):
                return nullcontext(object())

        return self.service_module.AnalysisService(Database())

    def test_report_and_original_sql(self):
        sql = "SELECT id FROM products WHERE name LIKE '%phone%';"
        report = self.service().analyze(sql, {}, "Запрос 1")
        self.assertIsInstance(report, str)
        self.assertIn("LIKE/ILIKE с начальным %: 1", report)
        self.assertIn('"mode": "explain_without_analyze"', report)
        self.assertEqual(self.executed, ["EXPLAIN (VERBOSE, FORMAT JSON) " + sql])

    def test_bad_input_never_reaches_executor(self):
        with self.assertRaises(SQLInputError):
            self.service().analyze("SELECT 1; DELETE FROM t", {})
        self.assertEqual(self.executed, [])


if __name__ == "__main__":
    unittest.main()
