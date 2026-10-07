import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock

from PySide6 import QtCore, QtWidgets

from analysis_support import connect_fake_database, wait_analysis
from app.controllers.workspace_controller import WorkspaceController


class AnalysisFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        from app.storage.preferences import Preferences
        self.temp = tempfile.TemporaryDirectory()
        settings = Preferences(QtCore.QSettings(str(Path(self.temp.name) / "settings.ini"),
                                               QtCore.QSettings.Format.IniFormat))
        self.controller = WorkspaceController(self.temp.name, settings)

    def tearDown(self):
        self.controller.close()
        wait_analysis(self.controller)
        self.temp.cleanup()

    def test_missing_connection_and_driver_error_do_not_save_history(self):
        failures = []
        self.controller.analysis_failed.connect(failures.append)
        self.controller.analyze("SELECT 1")
        self.assertIsNone(self.controller.analysis_worker)
        self.assertIn("подключитесь", failures[-1])
        connection = connect_fake_database(self.controller)
        connection.cursor.return_value.__enter__.return_value.execute.side_effect = RuntimeError("secret")
        self.controller.analyze("SELECT 1")
        wait_analysis(self.controller)
        self.assertIn("RuntimeError", failures[-1])
        self.assertNotIn("secret", failures[-1])
        self.assertEqual(self.controller.history.store.search(), [])
        connection.__exit__.assert_called_once()

    def test_background_execution_duplicate_guard_and_close(self):
        connect_fake_database(self.controller)
        entered, release = threading.Event(), threading.Event()
        threads = []

        def analyze(sql, parameters, context):
            threads.append(threading.get_ident())
            entered.set()
            if not release.wait(3):
                raise RuntimeError("Test timed out")
            return "plan"

        self.controller.analysis_service = Mock()
        self.controller.analysis_service.analyze.side_effect = analyze
        ready, closed = [], []
        self.controller.analysis_ready.connect(lambda *args: ready.append(args))
        self.controller.close_ready.connect(lambda: closed.append(True))
        self.controller.analyze("SELECT 1")
        worker = self.controller.analysis_worker
        try:
            self.assertTrue(entered.wait(2))
            self.controller.analyze("SELECT 2")
            self.assertIs(self.controller.analysis_worker, worker)
            self.assertFalse(self.controller.close())
        finally:
            release.set()
            wait_analysis(self.controller)
        self.assertNotEqual(threads[0], threading.get_ident())
        self.controller.analysis_service.analyze.assert_called_once()
        self.assertEqual(ready, [])
        self.assertEqual(closed, [True])
        self.assertIsNone(worker.parameters)
