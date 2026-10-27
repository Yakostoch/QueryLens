import unittest
from copy import deepcopy

from test_access_analysis import scan
from test_predicate_analysis import meta

from app.analyzers.operation_analysis import analyze_operations
from app.analyzers.plan_features import extract_plan_features
from app.analyzers.sql_features import extract_sql_features


def node(kind, **values):
    return {"Node Type": kind, "Plan Rows": 10, "Plan Width": 8, "Total Cost": 100, **values}


class OperationTests(unittest.TestCase):
    def analyze(self, sql, root=None, sources=None):
        sources = deepcopy(meta() if sources is None else sources)
        for t in sources.get("metadata", {}).get("data", {}).get("tables", []):
            t["diagnostics"]["sources"]["relation"]["data"].setdefault(
                "has_inheritance_children", False
            )
        return analyze_operations(
            sql,
            extract_sql_features(sql),
            extract_plan_features([{"Plan": root or node("Result")}]),
            sources,
        )["findings"]

    def test_distinct_primary_key_proves_simple_projection(self):
        f = self.analyze("SELECT DISTINCT id,email FROM users", node("Unique", Plans=[scan()]))[0]
        self.assertEqual(f["status"], "projection_unique_by_primary_key")
        self.assertTrue(f["uniqueness_proven"])
        self.assertIsNone(f["candidate_sql"])

    def test_distinct_inheritance_does_not_prove_uniqueness(self):
        sources = meta()
        sources["metadata"]["data"]["tables"][0]["diagnostics"]["sources"]["relation"]["data"][
            "has_inheritance_children"
        ] = True
        f = self.analyze("SELECT DISTINCT id FROM users", sources=sources)[0]
        self.assertIsNone(f["uniqueness_proven"])

    def test_distinct_set_returning_projection_not_proven(self):
        f = self.analyze("SELECT DISTINCT id,unnest(ARRAY[1,1]) FROM users")[0]
        self.assertIsNone(f["uniqueness_proven"])

    def test_distinct_join_not_proven_by_one_key(self):
        f = self.analyze("SELECT DISTINCT u.id FROM users u JOIN deny d ON d.key=u.id")[0]
        self.assertIsNone(f["uniqueness_proven"])

    def test_distinct_on_not_removed(self):
        f = self.analyze("SELECT DISTINCT ON (email) id,email FROM users ORDER BY email,id")[0]
        self.assertEqual(f["status"], "distinct_on_requires_order_semantics")

    def test_aggregate_not_misattributed_to_distinct(self):
        f = self.analyze(
            "SELECT DISTINCT count(*) FROM users", node("Aggregate", Strategy="Plain")
        )[0]
        self.assertEqual(f["plan_mapping_status"], "unknown")
        self.assertEqual(f["plan_nodes"], [])

    def test_sort_uses_child_input_not_limit_output(self):
        root = node(
            "Limit",
            **{"Plan Rows": 2, "Plans": [node("Sort", **{"Sort Key": ["id"], "Plans": [scan()]})]},
        )
        f = self.analyze("SELECT id FROM users ORDER BY id LIMIT 2", root)[0]
        self.assertEqual(f["status"], "sort_input_observed")
        self.assertEqual(f["plan_nodes"][0]["path"], "Plan.Plans[0]")
        self.assertIsNone(f["sort_details"][0]["sort_method"])

    def test_order_index_path_not_called_missing_index(self):
        f = self.analyze("SELECT id FROM users ORDER BY id", node("Index Only Scan"))[0]
        self.assertEqual(f["status"], "no_explicit_sort")
        self.assertEqual(f["matching_order_index"], "unknown")

    def test_window_sort_not_misattributed_to_result_order(self):
        root = node("WindowAgg", Plans=[node("Sort", **{"Sort Key": ["id"]})])
        fs = self.analyze("SELECT id,row_number() OVER (ORDER BY id) FROM users ORDER BY id", root)
        self.assertEqual(
            next(f for f in fs if f["rule_id"] == "AP07")["status"], "sort_purpose_mapping_unknown"
        )

    def test_having_early_filter_detected(self):
        root = node("Aggregate", Plans=[scan("users.id=1")])
        f = self.analyze("SELECT id,count(*) FROM users GROUP BY id HAVING id=1", root)[0]
        self.assertTrue(f["safe_to_pushdown"])
        self.assertEqual(f["predicates"][0]["predicate_plan_location"], "before_aggregation")

    def test_having_late_filter_detected(self):
        root = node("Aggregate", Filter="id=1", Plans=[scan("TRUE")])
        f = self.analyze("SELECT id,count(*) FROM users GROUP BY id HAVING id=1", root)[0]
        self.assertEqual(f["predicates"][0]["predicate_plan_location"], "after_aggregation")

    def test_having_aggregate_cannot_move(self):
        f = self.analyze("SELECT id,count(*) FROM users GROUP BY id HAVING count(*)>1")[0]
        self.assertFalse(f["predicates"][0]["safe_to_pushdown"])
        self.assertIsNone(f["candidate_sql"])

    def test_having_or_and_grouping_sets_abstain(self):
        for sql in [
            "SELECT id,count(*) FROM users GROUP BY id HAVING id=1 OR count(*)>1",
            "SELECT id,count(*) FROM users GROUP BY ROLLUP(id) HAVING id=1",
        ]:
            f = self.analyze(sql)[0]
            self.assertIsNot(f["safe_to_pushdown"], True)

    def test_cte_uses_named_producer_and_scan(self):
        root = node(
            "CTE Scan",
            **{
                "CTE Name": "recent",
                "Filter": "id=1",
                "Plans": [
                    node(
                        "Seq Scan",
                        **{
                            "Subplan Name": "CTE recent",
                            "Parent Relationship": "InitPlan",
                            "Plan Rows": 1000,
                        },
                    )
                ],
            },
        )
        f = self.analyze(
            "WITH recent AS MATERIALIZED (SELECT id FROM users) SELECT id FROM recent WHERE id=1",
            root,
        )[0]
        self.assertEqual(f["status"], "cte_producer_observed")
        self.assertEqual(f["producer_node"]["plan_rows"], 1000)
        self.assertEqual(f["cte_reference_count"], 1)

    def test_materialize_does_not_imply_cte(self):
        f = self.analyze(
            "WITH recent AS (SELECT id FROM users) SELECT id FROM recent", node("Materialize")
        )[0]
        self.assertEqual(f["status"], "no_explicit_cte_nodes")

    def test_cte_reused_reference_count(self):
        fs = self.analyze(
            "WITH x AS (SELECT id FROM users) SELECT a.id FROM x a JOIN x b ON a.id=b.id"
        )
        f = next(f for f in fs if f["rule_id"] == "AP13")
        self.assertEqual(f["cte_reference_count"], 2)

    def test_recursive_cte_no_inline_recommendation(self):
        fs = self.analyze(
            "WITH RECURSIVE x(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM x WHERE n<3) SELECT n FROM x"
        )
        self.assertEqual(
            next(f for f in fs if f["rule_id"] == "AP13")["cte_inline_eligibility"], "ineligible"
        )

    def test_named_window_definitions_not_counted_as_functions(self):
        f = self.analyze(
            "SELECT sum(id) OVER w FROM users WINDOW w AS (ORDER BY id)",
            node("WindowAgg", Plans=[scan()]),
        )[0]
        self.assertEqual(f["window_functions"], ["SUM(id)"])
        self.assertEqual(f["window_specs"][0]["named_window"], "w")
        self.assertEqual(len(f["named_window_definitions"]), 1)

    def test_window_multiple_passes_mapping_unknown(self):
        root = node("WindowAgg", Plans=[node("WindowAgg", Plans=[scan()])])
        f = self.analyze(
            "SELECT row_number() OVER (ORDER BY id),rank() OVER (ORDER BY email) FROM users", root
        )[0]
        self.assertEqual(f["status"], "window_pass_mapping_unknown")
        self.assertEqual(f["plan_nodes"], [])

    def test_join_inner_parameterized_index_is_observed(self):
        inner = node("Index Only Scan", **{"Index Name": "deny_key", "Index Cond": "d.key=u.id"})
        root = node("Nested Loop", **{"Join Type": "Anti", "Plans": [scan(), inner]})
        f = self.analyze("SELECT u.id FROM users u JOIN deny d ON d.key=u.id", root)[0]
        self.assertEqual(f["join_nodes"][0]["inner_index_conditions"][0]["index_name"], "deny_key")
        self.assertIsNone(f["join_nodes"][0]["estimated_fanout"])

    def test_multijoin_mapping_only_query_level(self):
        root = node(
            "Hash Join",
            **{
                "Join Type": "Inner",
                "Plans": [scan(), node("Hash Join", **{"Join Type": "Inner"})],
            },
        )
        f = self.analyze(
            "SELECT u.id FROM users u JOIN deny d ON d.key=u.id JOIN deny z ON z.key=d.key", root
        )[0]
        self.assertEqual(f["logical_join_mapping"], "query_level_only")

    def test_nested_scope_never_gets_root_metrics(self):
        fs = self.analyze("SELECT id FROM (SELECT DISTINCT id FROM users) q", node("Unique"))
        self.assertEqual(fs[0]["status"], "insufficient_data")
        self.assertEqual(fs[0]["plan_nodes"], [])

    def test_subplan_sort_not_attributed_to_outer_order(self):
        root = node(
            "Index Only Scan",
            Plans=[node("Sort", **{"Parent Relationship": "SubPlan", "Sort Key": ["email"]})],
        )
        f = self.analyze("SELECT id FROM users ORDER BY id", root)[0]
        self.assertEqual(f["status"], "no_explicit_sort")

    def test_missing_metadata_and_all_findings_remain_unconfirmed(self):
        fs = self.analyze(
            "SELECT DISTINCT id,row_number() OVER (ORDER BY id) FROM users ORDER BY id", sources={}
        )
        self.assertTrue(
            all(f["slowdown_confirmed"] is False and f["candidate_sql"] is None for f in fs)
        )

    def test_set_operation_order_is_explicitly_unknown(self):
        f = self.analyze(
            "SELECT id FROM users UNION ALL SELECT key FROM deny ORDER BY id", node("Sort")
        )[0]
        self.assertEqual(f["rule_id"], "AP07")
        self.assertEqual(f["status"], "insufficient_data")
        self.assertEqual(f["plan_nodes"], [])

    def test_having_without_group_does_not_push_filter(self):
        f = self.analyze("SELECT count(*) FROM users HAVING count(*)>0")[0]
        self.assertFalse(f["predicates"][0]["safe_to_pushdown"])

    def test_having_aggregate_filter_location_observed(self):
        root = node("Aggregate", Filter="(count(*) > 5)", Plans=[scan("TRUE")])
        f = self.analyze("SELECT count(*) FROM users HAVING count(*)>5", root)[0]
        self.assertEqual(f["predicates"][0]["predicate_plan_location"], "after_aggregation")
        self.assertTrue(f["predicates"][0]["depends_on_aggregate"])
