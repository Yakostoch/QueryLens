import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from PySide6 import QtCore, QtWidgets
from test_analysis_presentation import bundle

from app.services.dataset_service import DatasetError, make_record, read_records, write_records
from app.ui.dataset_review_window import DatasetReviewWindow


class DatasetReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.source = Path(self.directory.name) / "queue.jsonl"
        self.output = Path(self.directory.name) / "reviewed.jsonl"
        write_records(self.source, [make_record(bundle()), make_record(bundle("SELECT 2"))])
        self.original = self.source.read_bytes()
        self.window = DatasetReviewWindow(self.source, self.output)

    def tearDown(self):
        self.window.dirty = False
        self.window.close()
        self.directory.cleanup()

    def approve_good(self):
        w = self.window
        w.reviewer.setText("synthetic_test_only")
        w.family.setText("simple")
        w.verdict.setCurrentIndex(w.verdict.findData("no_issue_found"))
        w.status_combo.setCurrentIndex(w.status_combo.findData("approved"))
        w.rule_table.item(5, 1).setCheckState(QtCore.Qt.CheckState.Checked)
        w.answer.setPlainText("Синтетическая разметка: замечаний не выявлено.")

    def test_open_does_not_approve_or_create_output_or_copy_draft(self):
        w = self.window
        self.assertEqual(w.status_combo.currentData(), "pending")
        self.assertTrue(w.draft.toPlainText())
        self.assertEqual(w.answer.toPlainText(), "")
        self.assertFalse(self.output.exists())
        self.assertFalse(w.dirty)

    def test_prefilled_assistant_proposal_is_labelled_and_not_approved(self):
        rows = read_records(self.source)
        rows[0]["review"].update(
            answer="Предложенный ответ.", notes="Предложение ИИ; требует проверки."
        )
        output = Path(self.directory.name) / "proposals.jsonl"
        write_records(output, rows)
        other = DatasetReviewWindow(self.source, output)
        try:
            self.assertEqual(other.status_combo.currentData(), "pending")
            self.assertEqual(other.reviewer.text(), "")
            self.assertIn("Предложение ИИ", other.answer_label.text())
            self.assertEqual(other.answer.toPlainText(), "Предложенный ответ.")
        finally:
            other.close()

    def test_save_pending_and_approved_preserves_source_and_resumes(self):
        self.window.notes.setPlainText("work in progress")
        self.assertTrue(self.window.save_current())
        self.assertEqual(read_records(self.output)[0]["review"]["status"], "pending")
        self.approve_good()
        self.assertTrue(self.window.save_current())
        self.assertEqual(self.source.read_bytes(), self.original)
        other = DatasetReviewWindow(self.source, self.output)
        try:
            self.assertEqual(other.status_combo.currentData(), "approved")
            self.assertEqual(other.answer.toPlainText(), self.window.answer.toPlainText())
        finally:
            other.close()

    def test_incomplete_approval_does_not_save_or_navigate_on_save_choice(self):
        w = self.window
        w.status_combo.setCurrentIndex(w.status_combo.findData("approved"))
        self.assertFalse(w.save_current())
        self.assertFalse(self.output.exists())
        with patch.object(
            QtWidgets.QMessageBox,
            "question",
            return_value=QtWidgets.QMessageBox.StandardButton.Save,
        ):
            w.cases.setCurrentRow(1)
        self.assertEqual(w.index, 0)
        self.assertEqual(w.cases.currentRow(), 0)

    def test_checkbox_relation_and_navigation_cancel_discard(self):
        w = self.window
        w.rule_table.item(5, 2).setCheckState(QtCore.Qt.CheckState.Checked)
        self.assertEqual(w.rule_table.item(5, 1).checkState(), QtCore.Qt.CheckState.Checked)
        w.rule_table.item(5, 1).setCheckState(QtCore.Qt.CheckState.Unchecked)
        self.assertEqual(w.rule_table.item(5, 2).checkState(), QtCore.Qt.CheckState.Unchecked)
        with patch.object(
            QtWidgets.QMessageBox,
            "question",
            return_value=QtWidgets.QMessageBox.StandardButton.Cancel,
        ):
            w.cases.setCurrentRow(1)
        self.assertEqual(w.index, 0)
        with patch.object(
            QtWidgets.QMessageBox,
            "question",
            return_value=QtWidgets.QMessageBox.StandardButton.Discard,
        ):
            w.cases.setCurrentRow(1)
        self.assertEqual(w.index, 1)
        self.assertFalse(self.output.exists())

    def test_external_file_edit_not_overwritten(self):
        self.assertTrue(self.window.save_current())
        self.output.write_text("externally changed", encoding="utf-8")
        self.window.notes.setPlainText("new edit")
        self.assertFalse(self.window.save_current())
        self.assertEqual(self.output.read_text(), "externally changed")

    def test_existing_other_queue_and_same_source_output_rejected(self):
        with self.assertRaises(DatasetError):
            DatasetReviewWindow(self.source, self.source)
        write_records(self.output, [make_record(bundle("SELECT 999"))])
        with self.assertRaises(DatasetError):
            DatasetReviewWindow(self.source, self.output)

    def test_save_navigation_does_not_carry_approval_to_next_example(self):
        self.approve_good()
        with patch.object(
            QtWidgets.QMessageBox,
            "question",
            return_value=QtWidgets.QMessageBox.StandardButton.Save,
        ):
            self.window.cases.setCurrentRow(1)
        self.assertEqual(self.window.index, 1)
        self.assertEqual(self.window.status_combo.currentData(), "pending")
        self.assertEqual(self.window.answer.toPlainText(), "")
        self.assertEqual(read_records(self.output)[0]["review"]["status"], "approved")
