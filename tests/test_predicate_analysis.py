import unittest
from copy import deepcopy

from test_access_analysis import index, metadata, scan

from app.analyzers.diagnostic_router import route_candidates
from app.analyzers.plan_features import extract_plan_features
from app.analyzers.predicate_analysis import analyze_predicates
from app.analyzers.sql_features import extract_sql_features


def meta(nullable=False):
    result = metadata(
        [
            index("id_idx", expression=None, column="id"),
            index("email_idx", expression=None, column="email"),
        ]
    )
    data = result["metadata"]["data"]
    data["version_number"] = "180004"
    users = data["tables"][0]
    for field in users["all_fields"]:
        field["nullable"] = False
    users["diagnostics"]["sources"]["relation"]["data"]["row_security_enabled"] = False
    users["diagnostics"]["sources"]["constraints"] = {
        "status": "ok",
        "data": [{"kind": "p", "columns": ["id"], "is_validated": True, "is_enforced": True}],
    }
    inner = deepcopy(users)
    inner["name"] = "deny"
    inner["all_fields"] = [{"name": "key", "type": "integer", "nullable": nullable}]
    inner["diagnostics"]["sources"]["constraints"]["data"] = (
        [{"kind": "n", "columns": ["key"], "is_validated": True, "is_enforced": True}]
        if not nullable
        else []
    )
    data["tables"].append(inner)
    return result


def plan(node=None):
    return extract_plan_features([{"Plan": node or scan("(NOT (hashed SubPlan 1))")}])


class PredicateTests(unittest.TestCase):
    def analyze(self, sql, node=None, sources=None):
        return analyze_predicates(sql, extract_sql_features(sql), plan(node), sources or meta())[
            "findings"
        ]

    def test_or_group_filter_and_catalog_candidates(self):
        f = self.analyze(
            "SELECT id FROM users WHERE id=1 OR email='x'",
            scan("(users.id=1 OR users.email='x'::text)"),
        )[0]
        self.assertEqual(f["status"], "or_group_filter_observed")
        self.assertEqual(f["or_branch_count"], 2)
        self.assertEqual(
            [b["access_status"] for b in f["branches"]], ["catalog_candidate_only"] * 2
        )
        self.assertIsNone(f["candidate_sql"])
        self.assertFalse(f["slowdown_confirmed"])

    def test_bitmap_or_requires_matching_group_and_separate_children(self):
        node = scan("id=1 OR email='x'::text", "Recheck Cond", "Bitmap Heap Scan")
        node["Plans"] = [
            {
                "Node Type": "BitmapOr",
                "Plans": [
                    {
                        "Node Type": "Bitmap Index Scan",
                        "Index Name": "id_idx",
                        "Index Cond": "id=1",
                    },
                    {
                        "Node Type": "Bitmap Index Scan",
                        "Index Name": "email_idx",
                        "Index Cond": "email='x'::text",
                    },
                ],
            }
        ]
        f = self.analyze("SELECT id FROM users WHERE id=1 OR email='x'", node)[0]
        self.assertEqual(f["status"], "bitmap_or_group_observed")
        self.assertEqual(f["bitmap_or_group_mapping"], "matched")
        node["Plans"][0]["Plans"][1]["Index Cond"] = "email='different'"
        f = self.analyze("SELECT id FROM users WHERE id=1 OR email='x'", node)[0]
        self.assertNotEqual(f["bitmap_or_group_mapping"], "matched")

    def test_unrelated_bitmap_or_not_credited(self):
        node = scan(
            "(id=1 OR id=2) AND (email='x' OR email='y')", "Recheck Cond", "Bitmap Heap Scan"
        )
        node["Plans"] = [
            {
                "Node Type": "BitmapOr",
                "Plans": [
                    {
                        "Node Type": "Bitmap Index Scan",
                        "Index Name": "email_idx",
                        "Index Cond": "email='x'",
                    },
                    {
                        "Node Type": "Bitmap Index Scan",
                        "Index Name": "email_idx",
                        "Index Cond": "email='y'",
                    },
                ],
            }
        ]
        fs = self.analyze(
            "SELECT id FROM users WHERE (id=1 OR id=2) AND (email='x' OR email='y')", node
        )
        first = next(f for f in fs if f["expression"].startswith("id"))
        self.assertNotEqual(first["bitmap_or_group_mapping"], "matched")

    def test_in_simplification_is_not_union_or_speed_claim(self):
        f = self.analyze("SELECT id FROM users WHERE id=1 OR id=2", scan("id=1 OR id=2"))[0]
        self.assertEqual(f["in_expression_candidate"], "id IN (1, 2)")
        self.assertEqual(f["branches_disjoint"], "not_assessed")
        self.assertIsNone(f["candidate_sql"])

    def test_or_across_tables_abstains(self):
        f = self.analyze(
            "SELECT u.id FROM users u JOIN deny d ON d.key=u.id WHERE u.id=1 OR d.key=2"
        )[0]
        self.assertEqual(f["status"], "insufficient_data")

    def test_projection_or_is_not_access_candidate(self):
        syntax = extract_sql_features("SELECT id=1 OR id=2 AS value FROM users")
        self.assertEqual(
            next(f for f in route_candidates(syntax)["findings"] if f["rule_id"] == "AP04")[
                "status"
            ],
            "no_syntax_candidate",
        )
        self.assertEqual(self.analyze("SELECT id=1 OR id=2 AS value FROM users"), [])

    def test_not_in_nonnull_candidate_preserves_alias_scope(self):
        f = self.analyze("SELECT d.id FROM users d WHERE d.id NOT IN (SELECT d.key FROM deny d)")[0]
        self.assertEqual(f["status"], "not_exists_candidate")
        self.assertIn("NOT EXISTS", f["candidate_sql"])
        self.assertIn('"ql_notin_1"."key" = "d"."id"', f["candidate_sql"])
        self.assertEqual(f["outer_nullability"], "not_null")
        self.assertEqual(f["inner_nullability"], "not_null")

    def test_nullable_inner_no_rewrite_even_zero_null_stats(self):
        sources = meta(True)
        sources["metadata"]["data"]["tables"][1]["diagnostics"]["sources"]["column_statistics"] = {
            "status": "ok",
            "data": [{"column_name": "key", "null_frac": 0}],
        }
        f = self.analyze(
            "SELECT id FROM users WHERE id NOT IN (SELECT key FROM deny)", sources=sources
        )[0]
        self.assertEqual(f["status"], "null_semantics_requires_review")
        self.assertIsNone(f["candidate_sql"])

    def test_inner_is_not_null_can_prove_selected_values(self):
        f = self.analyze(
            "SELECT id FROM users WHERE id NOT IN (SELECT key FROM deny WHERE key IS NOT NULL)",
            sources=meta(True),
        )[0]
        self.assertEqual(f["inner_nullability"], "not_null")
        self.assertEqual(f["status"], "not_exists_candidate")

    def test_negation_of_not_null_does_not_prove_nonnull(self):
        f = self.analyze(
            "SELECT id FROM users WHERE id NOT IN (SELECT key FROM deny WHERE NOT (key IS NOT NULL))",
            sources=meta(True),
        )[0]
        self.assertNotEqual(f["inner_nullability"], "not_null")
        self.assertIsNone(f["candidate_sql"])

    def test_nullable_outer_empty_inner_case_not_rewritten(self):
        sources = meta()
        sources["metadata"]["data"]["tables"][0]["all_fields"][0]["nullable"] = True
        f = self.analyze(
            "SELECT id FROM users WHERE id NOT IN (SELECT key FROM deny WHERE FALSE)",
            sources=sources,
        )[0]
        self.assertIsNone(f["candidate_sql"])
        self.assertEqual(f["outer_nullability"], "nullable_or_unproven")

    def test_unvalidated_not_null_constraint_is_not_a_guarantee(self):
        sources = meta()
        sources["metadata"]["data"]["tables"][1]["diagnostics"]["sources"]["constraints"]["data"][
            0
        ]["is_validated"] = False
        f = self.analyze(
            "SELECT id FROM users WHERE id NOT IN (SELECT key FROM deny)", sources=sources
        )[0]
        self.assertEqual(f["inner_nullability"], "unknown")
        self.assertIsNone(f["candidate_sql"])

    def test_correlated_limit_cast_tuple_and_rls_abstain(self):
        for sql in (
            "SELECT u.id FROM users u WHERE u.id NOT IN (SELECT d.key FROM deny d WHERE d.key=u.id)",
            "SELECT id FROM users WHERE id NOT IN (SELECT key FROM deny LIMIT 1)",
            "SELECT id FROM users WHERE id NOT IN (SELECT key::text FROM deny)",
            "SELECT id FROM users WHERE (id,email) NOT IN (SELECT key,key::text FROM deny)",
        ):
            with self.subTest(sql=sql):
                self.assertIsNone(self.analyze(sql)[0]["candidate_sql"])
        sources = meta()
        sources["metadata"]["data"]["tables"][1]["diagnostics"]["sources"]["relation"]["data"][
            "row_security_enabled"
        ] = True
        self.assertIsNone(
            self.analyze(
                "SELECT id FROM users WHERE id NOT IN (SELECT key FROM deny)", sources=sources
            )[0]["candidate_sql"]
        )

    def test_hashed_subplan_mapped_by_source(self):
        node = scan("(NOT (hashed SubPlan 1))")
        node["Plans"] = [
            {
                "Node Type": "Seq Scan",
                "Schema": "public",
                "Relation Name": "deny",
                "Alias": "deny",
                "Subplan Name": "SubPlan 1",
                "Parent Relationship": "SubPlan",
                "Plan Rows": 123,
            }
        ]
        f = self.analyze("SELECT id FROM users WHERE id NOT IN (SELECT key FROM deny)", node)[0]
        self.assertEqual(f["subplan_kind"], "hashed SubPlan")
        self.assertEqual(f["inner_plan_rows"], 123)
        self.assertEqual(f["subplan_mapping_status"], "inner_source_matched")

    def test_missing_metadata_does_not_prove_nonnull(self):
        f = self.analyze(
            "SELECT id FROM users WHERE id NOT IN (SELECT key FROM deny)",
            sources={"metadata": {"status": "error"}},
        )[0]
        self.assertEqual(f["outer_nullability"], "unknown")
        self.assertIsNone(f["candidate_sql"])


if __name__ == "__main__":
    unittest.main()
