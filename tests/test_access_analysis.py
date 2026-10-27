import unittest

from app.analyzers.access_analysis import analyze_access
from app.analyzers.plan_features import extract_plan_features
from app.analyzers.sql_features import extract_sql_features


def index(
    name="email_lower_idx",
    expression="lower(email)",
    method="btree",
    opclass="text_ops",
    column=None,
    predicate=None,
    valid=True,
    position=1,
):
    return {
        "name": name,
        "access_method": method,
        "predicate": predicate,
        "is_valid": valid,
        "is_ready": True,
        "is_live": True,
        "keys": [
            {
                "expression": expression,
                "column_name": column,
                "position": position,
                "operator_class": opclass,
            }
        ],
        "included_columns": [],
    }


def metadata(indexes=(), rows=100000, kind="r", index_status="ok"):
    return {
        "metadata": {
            "status": "ok",
            "data": {
                "tables": [
                    {
                        "schema": "public",
                        "name": "users",
                        "metadata_origins": ["sql", "plan"],
                        "all_fields": [
                            {"name": "id", "type": "integer"},
                            {"name": "email", "type": "text"},
                            {"name": "code", "type": "character(3)"},
                        ],
                        "diagnostics": {
                            "sources": {
                                "relation": {
                                    "status": "ok",
                                    "data": {"relation_kind": kind, "estimated_rows": rows},
                                },
                                "indexes": {"status": index_status, "data": list(indexes)},
                            }
                        },
                    }
                ]
            },
        }
    }


def scan(
    condition="lower(users.email) = 'x'::text",
    role="Filter",
    node_type="Seq Scan",
    name=None,
    alias="users",
):
    return {
        "Node Type": node_type,
        "Schema": "public",
        "Relation Name": "users",
        "Alias": alias,
        "Plan Rows": 10,
        "Plan Width": 12,
        "Total Cost": 900,
        role: condition,
        **({"Index Name": name} if name else {}),
    }


class AccessAnalysisTests(unittest.TestCase):
    def analyze(
        self, sql="SELECT id FROM public.users WHERE lower(email) = 'x'", node=None, meta=None
    ):
        return analyze_access(
            sql,
            extract_sql_features(sql),
            extract_plan_features([{"Plan": node or scan()}]),
            meta if meta is not None else metadata(),
        )["findings"]

    def test_filter_and_expression_index_candidate_are_distinct(self):
        f = self.analyze(meta=metadata([index()]))[0]
        self.assertEqual(f["binding_status"], "matched")
        self.assertEqual(f["predicate_plan_roles"], ["Filter"])
        self.assertEqual(f["index_candidates"][0]["status"], "requires_planner_check")
        self.assertFalse(f["slowdown_confirmed"])

    def test_observed_index_condition(self):
        f = self.analyze(
            node=scan(role="Index Cond", node_type="Index Scan", name="email_lower_idx"),
            meta=metadata([index()]),
        )[0]
        self.assertEqual(f["risk_assessment"], "predicate_index_access_observed")
        self.assertEqual(f["index_candidates"][0]["status"], "observed_predicate_access")
        self.assertEqual(f["recommendations"], [])

    def test_index_for_other_condition_is_not_credited(self):
        node = scan()
        node.update(
            {"Node Type": "Index Scan", "Index Name": "users_pkey", "Index Cond": "(id = 42)"}
        )
        f = self.analyze(
            node=node, meta=metadata([index(name="users_pkey", expression=None, column="id")])
        )[0]
        self.assertEqual(f["predicate_plan_roles"], ["Filter"])
        self.assertEqual(f["index_candidates"], [])
        self.assertNotEqual(f["risk_assessment"], "predicate_index_access_observed")

    def test_real_aircrafts_coercion_and_like_operator(self):
        sql = "SELECT code FROM public.users WHERE LOWER(code) LIKE '%a%'"
        fs = self.analyze(sql, scan("(lower((users.code)::text) ~~ '%a%'::text)"), metadata(rows=9))
        self.assertEqual([f["rule_id"] for f in fs], ["AP02", "AP03"])
        for f in fs:
            self.assertEqual(f["binding_status"], "matched")
            self.assertEqual(f["risk_assessment"], "small_relation_context")
            self.assertEqual(f["recommendations"], [])

    def test_ilike_trigram_expression_index_is_observed(self):
        sql = "SELECT id FROM users WHERE lower(email) ILIKE '%phone%'"
        fs = self.analyze(
            sql,
            scan("(lower(users.email) ~~* '%phone%'::text)", "Index Cond", "Index Scan", "trgm"),
            metadata([index("trgm", method="gist", opclass="gist_trgm_ops")]),
        )
        self.assertTrue(all(f["binding_status"] == "matched" for f in fs))
        self.assertTrue(fs[1]["index_candidates"][0]["is_trigram"])

    def test_trigram_on_original_column_does_not_match_lower(self):
        sql = "SELECT id FROM users WHERE lower(email) LIKE '%phone%'"
        fs = self.analyze(
            sql,
            scan("lower(users.email) ~~ '%phone%'::text"),
            metadata(
                [
                    index(
                        "trgm",
                        expression=None,
                        column="email",
                        method="gin",
                        opclass="gin_trgm_ops",
                    )
                ]
            ),
        )
        self.assertEqual(fs[1]["index_candidates"], [])
        self.assertTrue(fs[1]["base_column_index_exists"])

    def test_include_column_is_not_an_index_search_key(self):
        original = index("id_include_email", expression=None, column="id")
        original["included_columns"] = ["email"]
        f = self.analyze(meta=metadata([original]))[0]
        self.assertFalse(f["base_column_index_exists"])
        self.assertEqual(f["base_column_indexes"], [])

    def test_partial_invalid_multicolumn_indexes_remain_conditional(self):
        indexes = [
            index("partial", predicate="active"),
            index("invalid", valid=False),
            index("second", position=2),
        ]
        candidates = self.analyze(meta=metadata(indexes))[0]["index_candidates"]
        self.assertIn(
            "partial_predicate_implication_not_proven", candidates[0]["unproven_conditions"]
        )
        self.assertEqual(candidates[1]["status"], "unusable_or_unknown_flags")
        self.assertIn("preceding_index_keys_not_checked", candidates[2]["unproven_conditions"])

    def test_unknown_indexes_do_not_become_no_indexes(self):
        f = self.analyze(meta=metadata(index_status="error"))[0]
        self.assertEqual(f["index_metadata_status"], "unknown")
        self.assertTrue(any("отсутствие не установлено" in x for x in f["recommendations"]))

    def test_bitmap_index_and_recheck_are_both_mapped(self):
        sql = "SELECT id FROM users WHERE email LIKE '%phone%'"
        node = scan("email ~~ '%phone%'::text", "Recheck Cond", "Bitmap Heap Scan")
        node["Plans"] = [
            {
                "Node Type": "Bitmap Index Scan",
                "Index Name": "trgm",
                "Index Cond": "email ~~ '%phone%'::text",
            }
        ]
        f = self.analyze(
            sql,
            node,
            metadata(
                [
                    index(
                        "trgm",
                        expression=None,
                        column="email",
                        method="gin",
                        opclass="gin_trgm_ops",
                    )
                ]
            ),
        )[0]
        self.assertEqual(f["predicate_plan_roles"], ["Index Cond", "Recheck Cond"])
        self.assertEqual(f["index_candidates"][0]["status"], "observed_predicate_access")

    def test_alias_and_and_conjunct(self):
        sql = "SELECT u.id FROM public.users u WHERE u.id > 0 AND lower(u.email)='x'"
        f = self.analyze(sql, scan("(u.id > 0) AND (lower(u.email) = 'x'::text)", alias="u"))[0]
        self.assertEqual(f["binding_status"], "matched")

    def test_do_not_match_different_value_or_function(self):
        for cond in ["lower(email)='y'", "upper(email)='x'", "lower(email)='x' OR id=1"]:
            with self.subTest(cond=cond):
                self.assertEqual(self.analyze(node=scan(cond))[0]["binding_status"], "unknown")

    def test_do_not_erase_numeric_cast(self):
        sql = "SELECT id FROM users WHERE CAST(id AS smallint)=1"
        f = self.analyze(sql, scan("id=1"))[0]
        self.assertEqual(f["binding_status"], "unknown")

    def test_arithmetic_can_bind_but_is_not_automatically_rewritten(self):
        sql = "SELECT id FROM users WHERE id + 1 = 5"
        f = self.analyze(sql, scan("(users.id + 1) = 5"))[0]
        self.assertEqual(f["binding_status"], "matched")
        self.assertNotIn("corrected_sql", f)

    def test_complex_scopes_abstain(self):
        queries = [
            "SELECT u.id FROM users u JOIN users v ON u.id=v.id WHERE lower(u.email)='x'",
            "WITH x AS (SELECT id FROM users WHERE lower(email)='x') SELECT id FROM x",
            "SELECT id FROM users WHERE id IN (SELECT id FROM users WHERE lower(email)='x')",
            "SELECT id FROM users WHERE lower(email)='x' OR id=1",
            "SELECT id FROM users WHERE NOT (lower(email)='x')",
        ]
        for sql in queries:
            with self.subTest(sql=sql):
                fs = self.analyze(sql)
                self.assertTrue(fs)
                self.assertTrue(all(f["binding_status"] == "unknown" for f in fs))
                self.assertTrue(all(not f["recommendations"] for f in fs))

    def test_view_and_partition_do_not_inherit_false_index_claims(self):
        for kind in ("v", "p", "f"):
            with self.subTest(kind=kind):
                self.assertEqual(
                    self.analyze(meta=metadata(kind=kind))[0]["binding_status"], "unknown"
                )

    def test_unknown_pattern_and_missing_metadata(self):
        sql = "SELECT id FROM users WHERE email LIKE $1"
        f = self.analyze(sql)[0]
        self.assertEqual(f["reason"], "pattern_or_escape_unknown")
        self.assertEqual(
            self.analyze(meta={"metadata": {"status": "error"}})[0]["reason"],
            "metadata_unavailable",
        )

    def test_no_function_on_constant_or_plain_equality_findings(self):
        for sql in ["SELECT id FROM users WHERE id=1", "SELECT id FROM users WHERE id=abs(-1)"]:
            self.assertEqual(self.analyze(sql), [])

    def test_negative_like_is_not_given_positive_search_advice(self):
        fs = self.analyze(
            "SELECT id FROM users WHERE lower(email) NOT LIKE '%phone%'",
            scan("lower(email) !~~ '%phone%'::text"),
        )
        self.assertTrue(fs)
        self.assertTrue(
            all(f["binding_status"] == "unknown" and not f["recommendations"] for f in fs)
        )

    def test_short_pattern_warning_does_not_promise_trigrams(self):
        f = self.analyze(
            "SELECT id FROM users WHERE email LIKE '%ab%'", scan("email ~~ '%ab%'::text")
        )[0]
        self.assertTrue(any("не гарантировано" in r for r in f["recommendations"]))


if __name__ == "__main__":
    unittest.main()
