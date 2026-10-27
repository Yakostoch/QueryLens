import unittest

from app.services.validation_report import summarize_validation, validation_text
from scripts.check_all_rules import validate_database_name


class ValidationReportTests(unittest.TestCase):
    def setUp(self):
        self.cases = [
            dict(case_id="star", primary_rule="AP01", server_executable=True),
            dict(case_id="parameter", primary_rule="AP03", server_executable=False),
            dict(case_id="n_plus_one", primary_rule="AP10", server_executable=True),
        ]
        self.report = dict(
            engine="PostgreSQL test",
            fixture_rolled_back=True,
            results=[
                dict(case_id="star", status="ok"),
                dict(case_id="parameter", status="syntax_only"),
                dict(case_id="n_plus_one", status="error"),
            ],
        )

    def test_failures_and_unexecuted_cases_are_not_successes(self):
        summary = summarize_validation(self.cases, self.report)
        self.assertEqual((summary["passed"], summary["failed"], summary["syntax_only"]), (1, 1, 1))
        self.assertEqual(summary["failures"], ["n_plus_one"])
        self.assertIn(
            "не независимая", validation_text(summary).replace("а не независимая", "не независимая")
        )
        self.assertIn("нужна трасса", validation_text(summary))

    def test_missing_case_rejected(self):
        self.report["results"].pop()
        with self.assertRaises(ValueError):
            summarize_validation(self.cases, self.report)

    def test_duplicate_case_rejected(self):
        self.report["results"].append(dict(case_id="star", status="ok"))
        with self.assertRaises(ValueError):
            summarize_validation(self.cases, self.report)

    def test_unknown_case_rejected(self):
        self.report["results"][0]["case_id"] = "unknown"
        with self.assertRaises(ValueError):
            summarize_validation(self.cases, self.report)

    def test_server_case_cannot_be_silently_skipped(self):
        self.report["results"][0]["status"] = "syntax_only"
        summary = summarize_validation(self.cases, self.report)
        self.assertEqual(summary["failed"], 2)

    def test_parameter_case_cannot_be_reported_as_executed(self):
        self.report["results"][1]["status"] = "ok"
        summary = summarize_validation(self.cases, self.report)
        self.assertEqual(summary["syntax_only"], 0)
        self.assertEqual(summary["failed"], 2)

    def test_rollback_evidence_required(self):
        self.report["fixture_rolled_back"] = False
        with self.assertRaises(ValueError):
            summarize_validation(self.cases, self.report)

    def test_only_dedicated_database_names(self):
        for name in [
            "demo",
            "postgres",
            "querylens_test;DROP",
            "querylens_test/other",
            "querylens_test" + "a" * 64,
        ]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_database_name(name)
        validate_database_name("querylens_test_validation")
