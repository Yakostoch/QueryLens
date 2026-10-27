import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from PySide6 import QtCore, QtWidgets
from test_analysis_presentation import bundle, report

from app.services.dataset_service import (
    DatasetError,
    apply_review,
    fingerprint,
    import_records,
    make_record,
    parse_report,
    read_records,
    review_sheet,
    training_splits,
    validate_review,
    write_records,
)
from app.storage.preferences import Preferences
from app.ui.main_window import MainWindow


def approved(sql="SELECT id FROM users WHERE id=1", family="users_lookup"):
    record = make_record(bundle(sql))
    record["review"].update(
        status="approved",
        reviewed_by="test_reviewer",
        family_id=family,
        verdict="no_issue_found",
        answer="Конкретных замечаний не выявлено.",
    )
    return record


class DatasetTests(unittest.TestCase):
    def test_export_is_pending_and_allowlists_top_level_data(self):
        payload = bundle()
        payload.update(password="do-not-export", parameters={"password": "connection-secret"})
        record = make_record(payload)
        self.assertEqual(record["review"]["status"], "pending")
        self.assertEqual(record["review"]["answer"], "")
        self.assertNotIn("connection-secret", json.dumps(record))
        self.assertNotIn("do-not-export", json.dumps(record))
        self.assertTrue(record["draft_answer"])
        self.assertEqual(record["input"]["sql"], "SELECT 1")

    def test_sql_literals_are_preserved_not_falsely_anonymized(self):
        record = make_record(bundle("SELECT 'person@example.com'"))
        self.assertIn("person@example.com", record["input"]["sql"])

    def test_text_and_raw_json_import_are_equivalent(self):
        payload = bundle()
        self.assertEqual(
            make_record(parse_report(report(payload))),
            make_record(parse_report(json.dumps(payload))),
        )

    def test_bad_or_legacy_report_is_rejected(self):
        for value in ["old report", "{}", "[]", '{"sql":"SELECT 1"}']:
            with self.assertRaises(DatasetError):
                parse_report(value)

    def test_fingerprint_groups_literals_and_comments_preserves_identifiers(self):
        self.assertEqual(
            fingerprint("SELECT id FROM t WHERE id=1 -- comment"),
            fingerprint("SELECT id FROM t WHERE id=999"),
        )
        self.assertEqual(
            fingerprint("SELECT id FROM t WHERE name='a'"),
            fingerprint("SELECT id FROM t WHERE name='b'"),
        )
        self.assertNotEqual(fingerprint("SELECT id FROM t"), fingerprint("SELECT id FROM other.t"))

    def test_history_read_only_all_rows_dedup_and_legacy_skip(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "history.sqlite3"
            with sqlite3.connect(path) as conn:
                conn.execute("CREATE TABLE queries(id INTEGER PRIMARY KEY, result TEXT)")
                conn.executemany(
                    "INSERT INTO queries(result) VALUES (?)",
                    [(report(bundle()),), (report(bundle()),), ("old",)],
                )
            before = path.read_bytes()
            records, errors = import_records([path])
            self.assertEqual(len(records), 1)
            self.assertEqual(len(errors), 1)
            self.assertEqual(path.read_bytes(), before)

    def test_import_missing_history_does_not_create_database(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "missing.sqlite3"
            records, errors = import_records([path])
            self.assertEqual(records, [])
            self.assertTrue(errors)
            self.assertFalse(path.exists())

    def test_jsonl_atomic_no_overwrite_and_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "review.jsonl"
            record = make_record(bundle())
            write_records(path, [record])
            self.assertEqual(read_records(path), [record])
            with self.assertRaises(FileExistsError):
                write_records(path, [approved()])
            self.assertEqual(read_records(path), [record])
            self.assertEqual(list(Path(d).glob(".querylens-*")), [])

    def test_failed_serialization_does_not_touch_existing_output(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "data.jsonl"
            path.write_text("original")
            with self.assertRaises(ValueError):
                write_records(path, [{"bad": float("nan")}], overwrite=True)
            self.assertEqual(path.read_text(), "original")

    def test_pending_or_rejected_never_enter_training(self):
        pending = make_record(bundle())
        rejected = deepcopy(pending)
        rejected["review"]["status"] = "rejected"
        with self.assertRaises(DatasetError):
            training_splits([pending, rejected])
        splits, manifest = training_splits([pending, rejected, approved()])
        self.assertEqual(sum(map(len, splits.values())), 1)
        self.assertEqual(manifest["excluded"], 2)

    def test_approval_requires_manual_fields_not_generated_draft(self):
        for field in ("answer", "reviewed_by", "family_id"):
            record = approved()
            record["review"][field] = ""
            with self.assertRaises(DatasetError):
                validate_review(record)

    def test_positive_label_needs_valid_rule_and_evidence_pointer(self):
        record = approved()
        record["review"].update(verdict="potential", labels=["AP06"])
        with self.assertRaises(DatasetError):
            validate_review(record)
        record["review"]["evidence_refs"] = ["/input/sql"]
        self.assertTrue(validate_review(record))
        record["review"]["evidence_refs"] = ["/input/not_present"]
        with self.assertRaises(DatasetError):
            validate_review(record)
        record["review"].update(labels=[{}], evidence_refs=["/input/sql"])
        with self.assertRaises(DatasetError):
            validate_review(record)

    def test_n_plus_one_not_confirmed_from_single_sql(self):
        record = approved()
        record["review"].update(verdict="problem", labels=["AP10"], evidence_refs=["/input/sql"])
        with self.assertRaises(DatasetError):
            validate_review(record)
        record["review"]["verdict"] = "insufficient_data"
        self.assertTrue(validate_review(record))

    def test_modified_source_or_fingerprint_cannot_silently_pass(self):
        record = approved()
        record["input"]["sql"] = "SELECT 2"
        with self.assertRaises(DatasetError):
            training_splits([record])
        record = approved()
        record["query_fingerprint"] = "wrong"
        with self.assertRaises(DatasetError):
            training_splits([record])

    def test_duplicate_ids_rejected(self):
        record = approved()
        with self.assertRaises(DatasetError):
            training_splits([record, deepcopy(record)])

    def test_semantic_variants_in_one_family_never_cross_splits(self):
        records = [
            approved("SELECT DISTINCT id FROM users", "pair"),
            approved("SELECT id FROM users", "pair"),
        ]
        splits, manifest = training_splits(records)
        self.assertEqual(manifest["groups"], 1)
        self.assertEqual(sorted(len(v) for v in splits.values()), [0, 0, 2])

    def test_transitive_family_and_fingerprint_grouping_and_order_independence(self):
        records = [
            approved("SELECT id FROM users WHERE id=1", "A"),
            approved("SELECT id FROM users WHERE id=2", "B"),
            approved("SELECT DISTINCT id FROM users", "B"),
        ]
        a, ma = training_splits(records)
        b, mb = training_splits(list(reversed(records)))
        self.assertEqual(ma["groups"], 1)
        self.assertEqual(a, b)
        self.assertEqual(len({row["split"] for row in ma["assignments"]}), 1)

    def test_draft_review_notes_and_labels_are_not_in_training_prompt(self):
        record = approved()
        record["draft_answer"] = "DRAFT_SENTINEL"
        record["review"]["notes"] = "PRIVATE_REVIEW_SENTINEL"
        splits, _ = training_splits([record])
        row = next(rows[0] for rows in splits.values() if rows)
        prompt = row["messages"][1]["content"]
        self.assertNotIn("DRAFT_SENTINEL", prompt)
        self.assertNotIn("PRIVATE_REVIEW_SENTINEL", prompt)
        self.assertNotIn(record["review"]["answer"], prompt)
        self.assertEqual(row["messages"][2]["content"], record["review"]["answer"])

    def test_cli_import_and_split_only_approved_and_no_partial_failure(self):
        script = Path(__file__).resolve().parents[1] / "scripts" / "dataset_tools.py"
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            source = root / "report.txt"
            source.write_text(report(bundle()), encoding="utf-8")
            review = root / "review.jsonl"
            result = subprocess.run(
                [sys.executable, str(script), "import", str(source), "--output", str(review)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            destination = root / "splits"
            result = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    "split",
                    str(review),
                    "--output-dir",
                    str(destination),
                ],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(destination.exists())
            write_records(review, [approved()], overwrite=True)
            result = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    "split",
                    str(review),
                    "--output-dir",
                    str(destination),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads((destination / "manifest.json").read_text())["approved"], 1)
            self.assertEqual(
                sum(
                    len((destination / f"{name}.jsonl").read_text().splitlines())
                    for name in ("train", "validation", "test")
                ),
                1,
            )

    def test_review_sheet_changes_only_manual_fields_not_sql(self):
        records = [make_record(bundle())]
        sheet = review_sheet(records)
        sheet["items"][0]["sql"] = "SELECT 999"
        sheet["items"][0]["review"].update(
            status="approved",
            reviewed_by="expert",
            family_id="simple",
            verdict="no_issue_found",
            answer="Замечаний не выявлено.",
        )
        changed = apply_review(records, sheet)
        self.assertEqual(changed[0]["input"]["sql"], "SELECT 1")
        self.assertEqual(records[0]["review"]["status"], "pending")
        self.assertEqual(changed[0]["review"]["status"], "approved")

    def test_review_sheet_rejects_foreign_or_duplicate_cases_and_invalid_approval(self):
        records = [make_record(bundle())]
        for mode in ("foreign", "duplicate", "incomplete"):
            sheet = review_sheet(records)
            if mode == "foreign":
                sheet["items"][0]["case_id"] = "wrong"
            elif mode == "duplicate":
                sheet["items"].append(deepcopy(sheet["items"][0]))
            else:
                sheet["items"][0]["review"]["status"] = "approved"
            with self.assertRaises(DatasetError):
                apply_review(records, sheet)


class DatasetInterfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_save_current_report_cancel_and_clear(self):
        with tempfile.TemporaryDirectory() as d:
            prefs = Preferences(
                QtCore.QSettings(str(Path(d) / "settings.ini"), QtCore.QSettings.Format.IniFormat)
            )
            window = MainWindow(d, prefs)
            try:
                self.assertFalse(window.export_case_button.isEnabled())
                window.display_analysis_result(report(bundle()))
                self.assertTrue(window.export_case_button.isEnabled())
                target = Path(d) / "case.jsonl"
                with patch.object(
                    QtWidgets.QFileDialog, "getSaveFileName", return_value=(str(target), "")
                ):
                    window.export_case_button.click()
                self.assertEqual(read_records(target)[0]["review"]["status"], "pending")
                with patch.object(QtWidgets.QFileDialog, "getSaveFileName", return_value=("", "")):
                    window.export_case_button.click()
                window.show_analysis_error("Отменено")
                self.assertFalse(window.export_case_button.isEnabled())
                window.display_analysis_result("old report")
                self.assertFalse(window.export_case_button.isEnabled())
            finally:
                window.close()
