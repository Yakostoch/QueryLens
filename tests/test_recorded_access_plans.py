"""Проверки на сохранённых планах PostgreSQL."""

import json
import unittest
from pathlib import Path

from app.analyzers.access_analysis import analyze_access
from app.analyzers.plan_features import extract_plan_features
from app.analyzers.sql_features import extract_sql_features

CAPTURE = json.loads((Path(__file__).parent / "fixtures/access_postgres_plans.json").read_text())


class RecordedAccessPlanTests(unittest.TestCase):
    def test_expression_index_is_observed_in_captured_plan(self):
        c = next(c for c in CAPTURE["cases"] if c["case_id"] == "ap02_expression_index")
        f = analyze_access(
            c["sql"],
            extract_sql_features(c["sql"]),
            extract_plan_features(c["explain"]["plan"]),
            c["sources"],
        )["findings"][0]
        self.assertEqual(f["binding_status"], "matched")
        self.assertEqual(f["predicate_plan_roles"], ["Index Cond"])
        self.assertTrue(
            any(
                i["index_name"] == "products_lower_name_idx"
                and i["status"] == "observed_predicate_access"
                for i in f["index_candidates"]
            )
        )
        self.assertFalse(f["slowdown_confirmed"])
        self.assertEqual(f["recommendations"], [])

    def test_trigram_recheck_and_index_condition_are_observed_in_captured_plan(self):
        c = next(c for c in CAPTURE["cases"] if c["case_id"] == "ap03_trigram_selective")
        f = analyze_access(
            c["sql"],
            extract_sql_features(c["sql"]),
            extract_plan_features(c["explain"]["plan"]),
            c["sources"],
        )["findings"][0]
        self.assertEqual(f["predicate_plan_roles"], ["Index Cond", "Recheck Cond"])
        self.assertEqual(f["risk_assessment"], "predicate_index_access_observed")
        self.assertTrue(
            any(
                i["is_trigram"] and i["status"] == "observed_predicate_access"
                for i in f["index_candidates"]
            )
        )
        self.assertFalse(f["slowdown_confirmed"])
        self.assertEqual(f["recommendations"], [])


if __name__ == "__main__":
    unittest.main()
