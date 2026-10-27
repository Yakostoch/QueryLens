"""Проверка конструкций SQL на размеченном тестовом наборе."""

import json
import unittest
from pathlib import Path

from app.analyzers.access_analysis import analyze_access
from app.analyzers.diagnostic_router import route_candidates
from app.analyzers.sql_features import SQLInputError, extract_sql_features

CASES = json.loads((Path(__file__).parent / "fixtures/sql_cases.json").read_text())


class SQLCorpusTests(unittest.TestCase):
    def test_all_rules_have_four_cases_and_good_complex_controls(self):
        for rule in range(1, 16):
            self.assertGreaterEqual(sum(c["primary_rule"] == f"AP{rule:02}" for c in CASES), 4)
        self.assertEqual(len({c["case_id"] for c in CASES}), len(CASES))
        self.assertGreaterEqual(sum(c["category"] == "good" for c in CASES), 10)
        self.assertGreaterEqual(sum(c["category"] == "complex" for c in CASES), 10)

    def test_unknown_parameter_does_not_become_known_pattern(self):
        case = next(c for c in CASES if c["case_id"] == "parameter_like")
        findings = route_candidates(extract_sql_features(case["sql"]))["findings"]
        self.assertEqual(
            next(f for f in findings if f["rule_id"] == "AP03")["status"], "insufficient_data"
        )

    def test_unsupported_input_is_rejected(self):
        for sql in (
            "SELECT 1; SELECT 2",
            "DELETE FROM users",
            "SELECT id INTO new_table FROM users",
            "SELECT id FROM users FOR UPDATE",
            "SELECT (",
            "-- comment only",
        ):
            with self.subTest(sql=sql), self.assertRaises(SQLInputError):
                extract_sql_features(sql)


def case_test(case):
    def test(self):
        syntax = extract_sql_features(case["sql"])
        triage = route_candidates(syntax)
        actual = sorted(f["rule_id"] for f in triage["findings"] if f["status"] == "candidate")
        self.assertEqual(actual, sorted(case["expected_candidates"]), case["semantic_note"])
        self.assertTrue(all(f["cause_assessment"] == "not_evaluated" for f in triage["findings"]))
        self.assertEqual(
            next(f for f in triage["findings"] if f["rule_id"] == "AP10")["status"],
            "insufficient_data",
        )
        # Отсутствие плана или каталога не доказывает отсутствие индекса.
        access = analyze_access(
            case["sql"], syntax, {"nodes": []}, {"metadata": {"status": "unavailable"}}
        )
        self.assertTrue(
            all(
                not f["slowdown_confirmed"]
                and not f["recommendations"]
                and f["binding_status"] == "unknown"
                for f in access["findings"]
            )
        )

    return test


for case in CASES:
    setattr(SQLCorpusTests, "test_case_" + case["case_id"], case_test(case))

if __name__ == "__main__":
    unittest.main()
