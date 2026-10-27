import json
import unittest
from copy import deepcopy
from pathlib import Path
from random import Random

from test_access_analysis import metadata, scan

from app.analyzers.access_analysis import analyze_access
from app.analyzers.plan_features import extract_plan_features
from app.analyzers.sql_features import extract_sql_features
from app.services.query_structure import query_structure


class JoinPortabilityTests(unittest.TestCase):
    def analyze(self, sql, node, meta):
        return analyze_access(
            sql, extract_sql_features(sql), extract_plan_features([{"Plan": node}]), meta
        )["findings"]

    def fixture(self):
        meta = metadata(rows=9)
        second = deepcopy(meta["metadata"]["data"]["tables"][0])
        second["name"] = "orders"
        meta["metadata"]["data"]["tables"].append(second)
        node = {
            "Node Type": "Hash Join",
            "Plans": [
                scan("lower(u.email) ~~ '%x%'::text", alias="u"),
                dict(scan("o.id>0", alias="o"), **{"Relation Name": "orders"}),
            ],
        }
        return meta, node

    def test_filter_is_bound_to_its_table_and_scan(self):
        meta, node = self.fixture()
        fs = self.analyze(
            "SELECT u.id FROM public.users u JOIN public.orders o ON u.id=o.id WHERE lower(u.email) LIKE '%x%'",
            node,
            meta,
        )
        self.assertEqual([f["binding_status"] for f in fs], ["matched", "matched"])
        self.assertTrue(all(f["node_metrics"]["path"] == "Plan.Plans[0]" for f in fs))

    def test_other_alias_filter_is_not_credited(self):
        meta, node = self.fixture()
        node["Plans"][0]["Filter"] = "u.id>0"
        node["Plans"][1]["Filter"] = "lower(o.email) ~~ '%x%'::text"
        fs = self.analyze(
            "SELECT u.id FROM users u JOIN orders o ON u.id=o.id WHERE lower(u.email) LIKE '%x%'",
            node,
            meta,
        )
        self.assertTrue(all(f["binding_status"] == "unknown" for f in fs))

    def test_self_join_uses_alias(self):
        meta, node = self.fixture()
        meta["metadata"]["data"]["tables"].pop()
        node["Plans"][1]["Relation Name"] = "users"
        fs = self.analyze(
            "SELECT u.id FROM users u JOIN users o ON u.id=o.id WHERE lower(u.email) LIKE '%x%'",
            node,
            meta,
        )
        self.assertTrue(
            all(
                f["binding_status"] == "matched" and f["node_metrics"]["path"] == "Plan.Plans[0]"
                for f in fs
            )
        )

    def test_ambiguous_unqualified_column_abstains(self):
        meta, node = self.fixture()
        fs = self.analyze(
            "SELECT u.id FROM users u JOIN orders o ON u.id=o.id WHERE lower(email) LIKE '%x%'",
            node,
            meta,
        )
        self.assertTrue(all(f["reason"] == "column_source_ambiguous" for f in fs))

    def test_expression_spanning_tables_abstains(self):
        meta, node = self.fixture()
        fs = self.analyze(
            "SELECT u.id FROM users u JOIN orders o ON u.id=o.id WHERE lower(u.email || o.email)='x'",
            node,
            meta,
        )
        self.assertTrue(all(f["binding_status"] == "unknown" for f in fs))

    def test_catalog_binding_disambiguates_same_name_in_two_schemas(self):
        meta, node = self.fixture()
        a, b = meta["metadata"]["data"]["tables"]
        a["schema"] = "chosen"
        b.update(schema="other", name="users")
        meta["metadata"]["data"]["source_bindings"] = [
            {"scope_id": "select_1", "alias": "u", "schema": "chosen", "name": "users"},
            {"scope_id": "select_1", "alias": "o", "schema": "other", "name": "users"},
        ]
        node["Plans"][0]["Schema"] = "chosen"
        node["Plans"][1].update(Schema="other", **{"Relation Name": "users"})
        sql = "SELECT u.id FROM users u JOIN other.users o ON u.id=o.id WHERE lower(u.email) LIKE '%x%'"
        self.assertTrue(
            all(
                f["binding_status"] == "matched" and f["relation"]["schema"] == "chosen"
                for f in self.analyze(sql, node, meta)
            )
        )
        del meta["metadata"]["data"]["source_bindings"]
        self.assertTrue(
            all(f["binding_status"] == "unknown" for f in self.analyze(sql, node, meta))
        )

    def test_quoted_unicode_and_random_names(self):
        rng = Random(414)
        names = [
            ("CRM Space", "Client Data", "E-Mail"),
            ("данные", "Клиенты", "Имя"),
            ("Mixed", "Order", "Group"),
        ]
        names += [
            (
                "s_" + str(rng.randrange(10**8)),
                "t_" + str(rng.randrange(10**8)),
                "c_" + str(rng.randrange(10**8)),
            )
            for _ in range(20)
        ]
        for schema, table, column in names:
            with self.subTest(schema=schema):
                meta = metadata()
                t = meta["metadata"]["data"]["tables"][0]
                t.update(schema=schema, name=table, all_fields=[{"name": column, "type": "text"}])
                sql = f"""SELECT "Q"."{column}" FROM "{schema}"."{table}" "Q" WHERE lower("Q"."{column}")='x' """
                node = scan(f"""lower("Q"."{column}") = 'x'::text""", alias="Q")
                node.update(Schema=schema, **{"Relation Name": table})
                self.assertEqual(self.analyze(sql, node, meta)[0]["binding_status"], "matched")

    def test_resolved_bindings_keep_schema_and_scope(self):
        bindings = []

        def resolve(name):
            schema = "tenant_a" if name == '"users"' else "tenant_b"
            return dict(
                schema=schema,
                name="users",
                oid=1 if schema == "tenant_a" else 2,
                fields=[dict(name="id", type="integer"), dict(name="email", type="text")],
            )

        query_structure(
            "SELECT u.email FROM users u JOIN tenant_b.users b ON u.id=b.id",
            resolve,
            bindings=bindings,
        )
        self.assertEqual(
            {(b["alias"], b["schema"], b["scope_id"]) for b in bindings},
            {("u", "tenant_a", "select_1"), ("b", "tenant_b", "select_1")},
        )


if __name__ == "__main__":
    unittest.main()
