import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

from analysis_support import connect_fake_database, wait_analysis
from psycopg import OperationalError
from PySide6 import QtWidgets
from test_analysis_presentation import bundle, report

from app.ui.main_window import MainWindow
from app.workers.analysis_worker import AnalysisWorker


class InterfaceRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.window = MainWindow(self.temp.name)
        connect_fake_database(self.window.controller)
        self.window.editor.setPlainText("SELECT 1")

    def tearDown(self):
        self.window.controller.close()
        wait_analysis(self.window.controller)
        self.window.close()
        self.temp.cleanup()

    def test_reconnect_invalidates_all_views_and_export(self):
        w = self.window
        w.display_analysis_result(report(bundle()))
        self.assertTrue(w.export_case_button.isEnabled())
        w.controller.connect_database(
            dict(
                host="localhost",
                port=5432,
                database="another_database",
                user="reader",
                password="secret",
                sslmode="prefer",
            )
        )
        self.assertEqual(w.output.toPlainText(), "")
        self.assertEqual(w.detail_output.toPlainText(), "")
        self.assertEqual(w.json_output.toPlainText(), "")
        self.assertFalse(w.export_case_button.isEnabled())
        self.assertIn("another_database", w.connection_label.text())

    def test_history_cannot_replace_sql_during_active_analysis(self):
        w = self.window
        entered, release = threading.Event(), threading.Event()

        def analyze(*args, **kwargs):
            entered.set()
            release.wait(2)
            return "CURRENT_RESULT"

        w.controller.analysis_service = MagicMock()
        w.controller.analysis_service.analyze.side_effect = analyze
        w.analyze()
        try:
            self.assertTrue(entered.wait(1))
            with patch.object(
                QtWidgets.QMessageBox,
                "question",
                return_value=QtWidgets.QMessageBox.StandardButton.Yes,
            ):
                w.restore_query(dict(sql="SELECT 2", result="OTHER_RESULT"))
            self.assertEqual(w.editor.toPlainText(), "SELECT 1")
        finally:
            release.set()
            wait_analysis(w.controller)
        self.assertEqual(w.detail_output.toPlainText(), "CURRENT_RESULT")
        self.assertTrue(w.history_panel.isEnabled())

    def test_error_then_success_removes_error_tooltip(self):
        w = self.window
        w.show_analysis_error("Старая ошибка")
        w.controller.analysis_service = MagicMock()
        w.controller.analysis_service.analyze.return_value = "RECOVERED"
        w.analyze()
        wait_analysis(w.controller)
        self.assertEqual(w.detail_output.toPlainText(), "RECOVERED")
        self.assertEqual(w.status.toolTip(), "")
        self.assertTrue(w.analyze_button.isEnabled())


class UserFacingDatabaseErrorTests(unittest.TestCase):
    def test_connection_error_without_sqlstate_has_recovery_action(self):
        service = MagicMock()
        service.analyze.side_effect = OperationalError("private connection details")
        worker = AnalysisWorker(service, "SELECT 1", {}, "test")
        messages = []
        worker.failed.connect(messages.append)
        worker.run()
        self.assertIn("подключение", messages[0].lower())
        self.assertNotIn("private", messages[0])

    def test_common_sql_errors_have_actionable_safe_messages(self):
        for code, phrase in [
            ("42601", "синтаксис"),
            ("42P01", "таблицу"),
            ("42703", "столбца"),
            ("42501", "прав"),
            ("08006", "подключение"),
        ]:
            with self.subTest(code=code):
                error = type("DatabaseError", (Exception,), {"sqlstate": code})(
                    "private password and SQL"
                )
                service = MagicMock()
                service.analyze.side_effect = error
                worker = AnalysisWorker(service, "SELECT 1", {}, "test")
                messages = []
                worker.failed.connect(messages.append)
                worker.run()
                self.assertEqual(len(messages), 1)
                self.assertIn(phrase, messages[0].lower())
                self.assertNotIn("private", messages[0])
                self.assertIsNone(worker.parameters)
