import json
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from app.analyzers.diagnostic_router import REGISTRY, route_candidates
from app.analyzers.plan_features import extract_plan_features
from app.analyzers.sql_features import extract_sql_features
from app.collectors.metadata import collect_metadata
from app.services.analysis_context import collect_analysis_context
from app.services.analysis_service import AnalysisService
from app.services.report_formatter import format_relation_details, size

PLAN = [
    {
        "Plan": {
            "Node Type": "Seq Scan",
            "Schema": "bookings",
            "Relation Name": "aircrafts_data",
            "Alias": "ml",
            "Plan Rows": 9,
            "Plan Width": 52,
            "Total Cost": 3.36,
        }
    }
]


class AnalysisContextTests(unittest.TestCase):
    def test_plan_sources_are_deduplicated_and_metadata_uses_original_sql(self):
        connection = MagicMock()
        features = extract_plan_features(PLAN)
        features["nodes"].append(dict(features["nodes"][0]))
        with (
            patch(
                "app.services.analysis_context.collect_metadata", return_value={"tables": []}
            ) as metadata,
            patch(
                "app.services.analysis_context.collect_configuration", return_value={"settings": []}
            ),
        ):
            result = collect_analysis_context(connection, "SELECT * FROM aircrafts", features)
        metadata.assert_called_once_with(
            connection,
            scope="query",
            sql="SELECT * FROM aircrafts",
            additional_relations=[("bookings", "aircrafts_data")],
        )
        self.assertEqual(result["metadata"]["status"], "ok")
        self.assertEqual(connection.transaction.call_count, 2)

    def test_metadata_error_does_not_hide_configuration_or_leak_driver_text(self):
        with (
            patch(
                "app.services.analysis_context.collect_metadata",
                side_effect=PermissionError("password=secret"),
            ),
            patch(
                "app.services.analysis_context.collect_configuration", return_value={"settings": []}
            ),
        ):
            result = collect_analysis_context(MagicMock(), "SELECT 1", {"nodes": []})
        self.assertEqual(result["metadata"]["status"], "error")
        self.assertIsNone(result["metadata"]["data"])
        self.assertEqual(result["configuration"]["status"], "ok")
        self.assertNotIn("secret", str(result))

    def test_dates_are_supported_but_nonserializable_optional_source_is_an_error(self):
        with (
            patch(
                "app.services.analysis_context.collect_metadata",
                return_value={"time": datetime.now(timezone.utc)},
            ),
            patch("app.services.analysis_context.collect_configuration", return_value=object()),
        ):
            result = collect_analysis_context(MagicMock(), "SELECT 1", {"nodes": []})
        self.assertEqual(result["metadata"]["status"], "ok")
        self.assertEqual(result["configuration"]["error"], "TypeError")

    def test_unknown_schema_is_not_assumed_public(self):
        with (
            patch("app.services.analysis_context.collect_metadata", return_value={}) as metadata,
            patch("app.services.analysis_context.collect_configuration", return_value={}),
        ):
            collect_analysis_context(
                MagicMock(), "SELECT 1", {"nodes": [{"relation": "users", "schema": None}]}
            )
        self.assertEqual(metadata.call_args.kwargs["additional_relations"], [])

    def test_service_preserves_raw_plan_original_sql_and_string_contract(self):
        database = MagicMock()
        metadata = {
            "tables": [
                {
                    "schema": "bookings",
                    "name": "aircrafts_data",
                    "metadata_origins": ["plan"],
                    "diagnostics": {
                        "sources": {"indexes": {"status": "ok", "data": [{"name": "pkey"}]}}
                    },
                }
            ]
        }
        sources = {
            "metadata": {"status": "ok", "data": metadata, "error": None},
            "configuration": {"status": "ok", "data": {"settings": []}, "error": None},
        }
        sql = "SELECT * FROM aircrafts WHERE LOWER(model) LIKE '%boeing%'"
        with (
            patch(
                "app.services.analysis_service.collect_explain",
                return_value={"plan": PLAN, "analyze": False},
            ),
            patch("app.services.analysis_service.collect_analysis_context", return_value=sources),
        ):
            report = AnalysisService(database).analyze(sql, {"password": "DO_NOT_EXPORT"})
        self.assertIsInstance(report, str)
        payload = json.loads(report.split("ДИАГНОСТИЧЕСКИЕ ДАННЫЕ В JSON\n", 1)[1])
        self.assertEqual(payload["sql"], sql)
        self.assertEqual(payload["explain"]["plan"], PLAN)
        self.assertEqual(payload["plan_summary"]["estimated_bytes"], 468)
        self.assertIsNone(payload["plan_features"]["execution_time_ms"])
        findings = {x["rule_id"]: x for x in payload["triage"]["findings"]}
        self.assertEqual(findings["AP02"]["status"], "candidate")
        self.assertEqual(findings["AP03"]["status"], "candidate")
        self.assertEqual(findings["AP03"]["cause_assessment"], "not_evaluated")
        self.assertNotIn("DO_NOT_EXPORT", report)


class MetadataPlanSourcesTests(unittest.TestCase):
    def test_view_and_underlying_relation_are_collected_separately(self):
        view = {"oid": 1, "schema": "bookings", "name": "aircrafts", "columns": 2, "indexes": 0}
        base = {
            "oid": 2,
            "schema": "bookings",
            "name": "aircrafts_data",
            "columns": 2,
            "indexes": 1,
        }
        fields = [
            {"name": "aircraft_code", "type": "text", "nullable": False},
            {"name": "model", "type": "text", "nullable": False},
        ]
        with (
            patch("app.collectors.metadata.QueryExecutor") as executor_class,
            patch("app.collectors.metadata.collect_relation_details", return_value={}) as details,
        ):
            executor = executor_class.return_value
            executor.fetch_one.side_effect = [{"version": "test"}, {}, view, base, {"bytes": 100}]
            executor.fetch_all.side_effect = [fields, fields]
            result = collect_metadata(
                MagicMock(),
                scope="query",
                sql="SELECT model FROM bookings.aircrafts",
                additional_relations=[("bookings", "aircrafts_data")],
            )
        tables = result["tables"]
        self.assertEqual([x["name"] for x in tables], ["aircrafts", "aircrafts_data"])
        self.assertEqual(tables[0]["metadata_origins"], ["sql"])
        self.assertEqual(tables[1]["metadata_origins"], ["plan"])
        self.assertEqual(tables[0]["columns"], 1)
        self.assertEqual(tables[0]["schema_columns"], 2)
        self.assertEqual(details.call_count, 2)

    def test_missing_plan_relation_is_explicit_not_silently_empty(self):
        with patch("app.collectors.metadata.QueryExecutor") as executor_class:
            executor = executor_class.return_value
            executor.fetch_one.side_effect = [{"version": "test"}, {}, None, {"bytes": 10}]
            result = collect_metadata(
                MagicMock(), scope="query", sql="SELECT 1", additional_relations=[("gone", "table")]
            )
        self.assertEqual(result["tables"], [])
        self.assertEqual(
            result["additional_relation_errors"],
            [{"schema": "gone", "name": "table", "error": "LookupError"}],
        )

    def test_same_sql_and_plan_relation_is_not_read_twice(self):
        table = {"oid": 1, "schema": "public", "name": "users", "columns": 1, "indexes": 1}
        fields = [{"name": "id", "type": "int", "nullable": False}]
        with (
            patch("app.collectors.metadata.QueryExecutor") as executor_class,
            patch("app.collectors.metadata.collect_relation_details", return_value={}) as details,
        ):
            executor = executor_class.return_value
            executor.fetch_one.side_effect = [{"version": "test"}, {}, table, {"bytes": 10}]
            executor.fetch_all.return_value = fields
            result = collect_metadata(
                MagicMock(),
                scope="query",
                sql="SELECT id FROM public.users",
                additional_relations=[("public", "users")],
            )
        self.assertEqual(result["tables"][0]["metadata_origins"], ["sql", "plan"])
        details.assert_called_once()


class RoutingAndFormattingTests(unittest.TestCase):
    def route(self, sql):
        return {x["rule_id"]: x for x in route_candidates(extract_sql_features(sql))["findings"]}

    def test_registry_is_complete_and_multiple_branches_can_activate(self):
        self.assertEqual([x[0] for x in REGISTRY], list(range(1, 16)))
        r = self.route("SELECT DISTINCT * FROM users WHERE lower(email) LIKE '%x%'")
        self.assertTrue(
            all(r[k]["status"] == "candidate" for k in ("AP01", "AP02", "AP03", "AP06"))
        )

    def test_projection_like_is_not_an_access_predicate(self):
        self.assertEqual(
            self.route("SELECT name LIKE '%x%' FROM users")["AP03"]["status"], "no_syntax_candidate"
        )
        self.assertEqual(
            self.route("SELECT id FROM users WHERE name LIKE $1")["AP03"]["status"],
            "insufficient_data",
        )

    def test_n_plus_one_cannot_be_diagnosed_from_loops_or_one_sql(self):
        self.assertEqual(self.route("SELECT 1")["AP10"]["status"], "insufficient_data")

    def test_small_sizes_are_not_rounded_to_zero_gib(self):
        self.assertEqual(size(0), "0 B")
        self.assertEqual(size(512), "512 B")
        self.assertEqual(size(1536), "1.50 KiB")
        self.assertEqual(size(2**20), "1.00 MiB")
        self.assertEqual(size(None), "недоступно")

    def test_view_and_unknown_dates_are_explicit_in_report(self):
        def table(kind):
            sources = {
                k: {"status": "unavailable", "data": None, "error": None}
                for k in ("indexes", "constraints", "relation", "column_statistics")
            }
            sources["relation"] = {
                "status": "ok",
                "data": {
                    "relation_kind": kind,
                    "total_bytes": 8192,
                    "estimated_rows": None,
                    "last_analyze": None,
                    "last_autoanalyze": None,
                },
            }
            return {"schema": "public", "name": "obj", "diagnostics": {"sources": sources}}

        self.assertIn("представление", "\n".join(format_relation_details(table("v"))))
        report = "\n".join(format_relation_details(table("r")))
        self.assertIn("нет сведений", report)
        self.assertNotIn("None", report)
        self.assertIn("8.00 KiB", report)


if __name__ == "__main__":
    unittest.main()
