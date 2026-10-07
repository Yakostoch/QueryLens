from analysis_support import connect_fake_database, wait_analysis
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, MagicMock, patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6 import QtCore, QtWidgets

from app.controllers.connection_controller import ConnectionController
from app.controllers.workspace_controller import WorkspaceController
from app.services.database_service import DatabaseService
from app.storage.preferences import Preferences
from app.ui.dialog.connection_dialog import ConnectionDialog
from app.ui.main_window import MainWindow


class ControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_connection_test_failure_and_reconnect_resource_ownership(self):
        service = DatabaseService()
        controller = ConnectionController(service)
        failures, connected, tested = [], [], []
        controller.failed.connect(failures.append)
        controller.connected.connect(connected.append)
        controller.tested.connect(lambda: tested.append(True))
        parameters = dict(host=" localhost ", database=" demo ", user=" reader ",
                          password="secret", port=5432, sslmode="prefer")
        first, probe, replacement = Mock(), Mock(), Mock()
        with patch.object(service, "connect", side_effect=[first, probe, RuntimeError("secret"), replacement]):
            controller.connect_database(parameters)
            controller.connect_database(parameters, test_only=True)
            probe.close.assert_called_once()
            first.close.assert_not_called()
            self.assertIs(controller.connection, first)
            controller.connect_database(parameters)
            self.assertIs(controller.connection, first)
            self.assertNotIn("secret", failures[0])
            controller.connect_database(parameters)
        first.close.assert_called_once()
        self.assertEqual(connected, ["demo", "demo"])
        self.assertEqual(tested, [True])
        copy = controller.parameters
        copy["password"] = "modified"
        self.assertEqual(controller.parameters["password"], "secret")
        controller.close()
        controller.close()
        replacement.close.assert_called_once()
        self.assertIsNone(controller.parameters)

    def test_dialog_only_emits_input_without_opening_database(self):
        dialog = ConnectionDialog()
        requests = []
        dialog.connection_requested.connect(lambda parameters, test: requests.append((parameters, test)))
        dialog.database.setText("demo")
        dialog.user.setText("reader")
        with patch("app.services.database_service.connect_postgresql") as connect:
            dialog.test_button.click()
            dialog.connect_button.click()
            connect.assert_not_called()
        self.assertEqual([test for _, test in requests], [True, False])
        self.assertEqual(requests[0][0]["database"], "demo")
        dialog.close()

    def test_workspace_analysis_history_failure_and_close_without_window(self):
        with tempfile.TemporaryDirectory() as directory:
            preferences = Preferences(QtCore.QSettings(str(Path(directory) / "settings.ini"),
                                                      QtCore.QSettings.Format.IniFormat))
            controller = WorkspaceController(directory, preferences)
            connect_fake_database(controller)
            results, failures = [], []
            controller.analysis_ready.connect(lambda *args: results.append(args))
            controller.analysis_failed.connect(failures.append)
            controller.analyze(" ")
            wait_analysis(controller)
            self.assertEqual(len(failures), 1)
            self.assertEqual(controller.history.store.search(), [])
            controller.analyze("SELECT 1;")
            wait_analysis(controller)
            self.assertTrue(results[-1][2])
            controller.settings.save("light", 14, False)
            controller.analyze("SELECT 2;")
            wait_analysis(controller)
            self.assertFalse(results[-1][2])
            self.assertEqual(len(controller.history.store.search()), 1)
            controller.settings.save("light", 14, True)
            with patch.object(controller.history.store, "add", side_effect=sqlite3.OperationalError("disk full")):
                controller.analyze("SELECT 3;")
                wait_analysis(controller)
            self.assertIn("SELECT 3;", results[-1][1])
            self.assertFalse(results[-1][2])
            self.assertEqual(controller.history.last_error, "disk full")
            self.assertTrue(controller.close())
            self.assertTrue(controller.close())
            self.assertFalse(controller.can_collect(False, True))

    def test_window_connection_dialog_wiring(self):
        with tempfile.TemporaryDirectory() as directory:
            preferences = Preferences(QtCore.QSettings(str(Path(directory) / "settings.ini"),
                                                      QtCore.QSettings.Format.IniFormat))
            window = MainWindow(directory, preferences)
            connection = MagicMock()
            connection.__enter__.return_value = connection
            cursor = connection.cursor.return_value.__enter__.return_value
            cursor.fetchone.return_value = {"QUERY PLAN": [{"Plan": {"Node Type": "Result"}}]}

            def submit():
                dialog = window.findChild(ConnectionDialog)
                dialog.database.setText("demo")
                dialog.user.setText("reader")
                dialog.connect_button.click()

            with patch("app.services.database_service.connect_postgresql", return_value=connection) as connect:
                QtCore.QTimer.singleShot(0, submit)
                window.open_connection()
                window.editor.setPlainText("SELECT 42;")
                window.analyze_button.click()
                wait_analysis(window.controller)
                self.assertEqual(connect.call_count, 2)
                self.assertEqual(connect.call_args.kwargs["database"], "demo")
                self.assertEqual(connect.call_args.kwargs["user"], "reader")
                cursor.execute.assert_called_once_with("EXPLAIN (FORMAT JSON) SELECT 42;", None, prepare=True)
                self.assertIn('"Node Type": "Result"', window.output.toPlainText())
            self.assertEqual(window.connection_label.text(), "PostgreSQL: demo")
            self.assertIs(window.controller.connection.connection, connection)
            window.close()
            connection.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
