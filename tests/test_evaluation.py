import json
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from test_analysis_presentation import bundle

from app.services.dataset_service import DatasetError, make_record, training_splits, write_records
from app.services.evaluation_service import evaluate_records, format_evaluation, predictions


def case(kind="good", sql=None):
    if kind == "flag":
        payload = bundle(
            sql or "SELECT DISTINCT id FROM users",
            [
                dict(
                    rule_id="AP06",
                    expression="DISTINCT",
                    status="projection_unique_by_primary_key",
                    uniqueness_proven=True,
                    unique_key=["id"],
                )
            ],
        )
    elif kind == "unknown":
        payload = bundle(
            sql or "SELECT * FROM users",
            [dict(rule_id="AP01", expression="*", status="consumer_requirements_unknown")],
            "volume_analysis",
        )
    else:
        payload = bundle(sql or "SELECT id FROM users")
    record = make_record(payload)
    record["review"].update(
        status="approved",
        reviewed_by="synthetic_test_only",
        family_id=kind,
        verdict="potential" if kind == "flag" else "no_issue_found",
        labels=["AP06"] if kind == "flag" else [],
        assessed_rules=["AP06"],
        evidence_refs=["/input/sql"],
        answer="Синтетическая тестовая разметка.",
    )
    return record


class EvaluationTests(unittest.TestCase):
    def test_pending_dataset_has_no_fake_accuracy(self):
        result = evaluate_records([make_record(bundle())])
        self.assertEqual(result["status"], "awaiting_review")
        self.assertIsNone(result["micro"]["precision"])
        self.assertIsNone(result["micro"]["strict_accuracy"])
        self.assertIn("пока не измерено", format_evaluation(result))

    def test_positive_and_negative_match(self):
        result = evaluate_records([case("flag"), case()])
        self.assertEqual(result["micro"]["tp"], 1)
        self.assertEqual(result["micro"]["tn"], 1)
        self.assertEqual(result["micro"]["f1"], 1)
        self.assertEqual(result["strict_case_match"], 1)

    def test_false_positive(self):
        record = case("flag")
        record["review"].update(verdict="no_issue_found", labels=[])
        result = evaluate_records([record])
        self.assertEqual(result["micro"]["fp"], 1)
        self.assertEqual(result["micro"]["precision"], 0)
        self.assertEqual(result["strict_case_match"], 0)

    def test_false_negative(self):
        record = case()
        record["review"].update(verdict="potential", labels=["AP06"])
        result = evaluate_records([record])
        self.assertEqual(result["micro"]["fn"], 1)
        self.assertEqual(result["micro"]["recall"], 0)

    def test_abstention_on_positive_counts_as_missed_warning(self):
        record = case("unknown")
        record["review"].update(verdict="potential", labels=["AP01"], assessed_rules=["AP01"])
        result = evaluate_records([record])
        self.assertEqual(result["micro"]["abstained"], 1)
        self.assertEqual(result["micro"]["fn"], 1)
        self.assertEqual(result["micro"]["coverage"], 0)
        self.assertEqual(result["micro"]["strict_accuracy"], 0)
        self.assertIsNone(result["micro"]["accuracy_when_answered"])

    def test_abstention_on_negative_is_not_a_true_negative(self):
        record = case()
        record["review"]["assessed_rules"] = ["AP10"]
        result = evaluate_records([record])
        self.assertEqual(result["micro"]["tn"], 0)
        self.assertEqual(result["micro"]["abstained"], 1)
        self.assertEqual(result["strict_case_match"], 0)

    def test_explicit_unknown_truth_is_separate(self):
        record = case("unknown")
        record["review"].update(verdict="insufficient_data", assessed_rules=["AP01"])
        result = evaluate_records([record])
        self.assertEqual(result["unknown_expectations"]["rate"], 1)
        self.assertEqual(result["micro"]["decisions"], 0)
        self.assertEqual(result["scored_cases"], 0)

    def test_unchecked_rules_are_not_assumed_negative(self):
        record = case("flag")
        record["review"].update(labels=[], assessed_rules=["AP08"], verdict="no_issue_found")
        result = evaluate_records([record])
        self.assertEqual(result["micro"]["fp"], 0)
        self.assertEqual(result["micro"]["decisions"], 1)
        self.assertEqual(result["per_rule"]["AP06"]["decisions"], 0)

    def test_old_approved_without_assessed_rules_is_excluded(self):
        record = case()
        record["review"].pop("assessed_rules")
        result = evaluate_records([record])
        self.assertEqual(result["excluded"]["no_assessed_rules"], 1)
        self.assertEqual(result["status"], "awaiting_review")

    def test_labels_outside_assessed_rules_are_rejected(self):
        record = case("flag")
        record["review"]["assessed_rules"] = ["AP08"]
        with self.assertRaises(DatasetError):
            evaluate_records([record])

    def test_only_normal_sort_or_join_does_not_become_warning(self):
        for rule, status in [("AP07", "sort_input_observed"), ("AP15", "join_plan_observed")]:
            record = make_record(bundle(findings=[dict(rule_id=rule, status=status)]))
            states, visible, all_flags = predictions(record)
            self.assertEqual(states[rule], "clear")
            self.assertNotIn(rule, all_flags)

    def test_fourth_warning_is_scored_but_display_limit_is_explicit(self):
        payload = bundle(
            findings=[
                dict(rule_id=rule, status="offset_work_present", expression=rule)
                for rule in ["AP02", "AP04", "AP07", "AP08"]
            ],
            block="volume_analysis",
        )
        states, shown, all_flags = predictions(make_record(payload))
        self.assertEqual(len(all_flags), 4)
        self.assertEqual(len(shown), 3)

    def test_manifest_only_scores_selected_partition(self):
        rows = [case(), case("flag")]
        _, manifest = training_splits(rows)
        partition = manifest["assignments"][0]["split"]
        expected_count = sum(r["split"] == partition for r in manifest["assignments"])
        result = evaluate_records(rows, manifest=manifest, split=partition)
        self.assertEqual(result["scored_cases"], expected_count)
        self.assertEqual(result["scope"], "family_partition_" + partition)

    def test_modified_manifest_or_family_does_not_silently_pass(self):
        rows = [case()]
        _, manifest = training_splits(rows)
        manifest["assignments"][0]["group_id"] = "bad"
        with self.assertRaises(DatasetError):
            evaluate_records(rows, manifest=manifest)
        with self.assertRaises(DatasetError):
            evaluate_records(rows, manifest=[])

    def test_duplicate_or_modified_capture_rejected(self):
        record = case()
        with self.assertRaises(DatasetError):
            evaluate_records([record, deepcopy(record)])
        record["input"]["sql"] = "SELECT 2"
        with self.assertRaises(DatasetError):
            evaluate_records([record])

    def test_cli_pending_report_is_available_and_null_metrics(self):
        script = Path(__file__).resolve().parents[1] / "scripts/dataset_tools.py"
        with tempfile.TemporaryDirectory() as d:
            input_path, output_path = Path(d) / "pending.jsonl", Path(d) / "evaluation.json"
            write_records(input_path, [make_record(bundle())])
            p = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    "evaluate",
                    str(input_path),
                    "--output",
                    str(output_path),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(p.returncode, 0, p.stderr)
            result = json.loads(output_path.read_text())
            self.assertEqual(result["status"], "awaiting_review")
            self.assertIsNone(result["strict_case_match"])
