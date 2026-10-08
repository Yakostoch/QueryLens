import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6 import QtCore, QtTest, QtWidgets

from app.collectors.system import io_rates
from app.database.connection import connect_postgresql
from app.services.collection_service import collect_report
from app.services.report_formatter import setting_value
from app.storage.preferences import Preferences
from app.ui.main_window import MainWindow


class CollectorTests(unittest.TestCase):
    def test_outbound_host_and_connection_string_rejected_before_driver(self):
        args = dict(port=5432, database="demo", user="reader", password="secret", sslmode="prefer")
        with patch("psycopg.connect") as connect:
            for host in ("example.com", "192.168.1.2", "127.0.0.1.example.com"):
                with self.assertRaises(ValueError):
                    connect_postgresql(host=host, **args)
            for database in ("host=example.com dbname=demo", "postgresql://example.com/demo"):
                with self.assertRaises(ValueError):
                    connect_postgresql(host="localhost", **{**args, "database": database})
            connect.assert_not_called()
            connect_postgresql(host="localhost", **args)
            self.assertEqual(connect.call_args.kwargs["hostaddr"], "127.0.0.1")
            self.assertIn("default_transaction_read_only=on", connect.call_args.kwargs["options"])

    def test_module_failure_does_not_discard_hardware_or_leak_error(self):
        with patch("app.services.collection_service.collect_postgres", side_effect=RuntimeError("secret")), \
                patch("app.services.collection_service.collect_system", return_value={"samples": []}):
            report = collect_report({"password": "secret"}, True, True)
        self.assertEqual(report["errors"], {"postgres": "RuntimeError"})
        self.assertIsNotNone(report["system"])
        self.assertNotIn("secret", str(report))

    def test_missing_counters_and_reset_are_not_zero(self):
        self.assertIsNone(io_rates(None, {}, 1))
        before = {"disk": SimpleNamespace(read_bytes=100, write_bytes=100, read_count=10, write_count=10)}
        after = {"disk": SimpleNamespace(read_bytes=50, write_bytes=300, read_count=5, write_count=20)}
        rates = io_rates(before, after, 2)["disk"]
        self.assertIsNone(rates["read_bytes_per_second"])
        self.assertEqual(rates["write_bytes_per_second"], 100)
        self.assertIn("128.00 MiB", setting_value({"setting": "16384", "unit": "8kB"}))


class CollectionInterfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        from analysis_support import connect_fake_database
        self.temp = tempfile.TemporaryDirectory()
        preferences = Preferences(QtCore.QSettings(str(Path(self.temp.name) / "settings.ini"),
                                                  QtCore.QSettings.Format.IniFormat))
        self.window = MainWindow(self.temp.name, preferences)
        connect_fake_database(self.window.controller)

    def wait_collection(self):
        import time
        for _ in range(200):
            self.app.processEvents()
            if not self.window.controller.collection.busy:
                return
            time.sleep(0.005)
        self.fail("Collection did not stop")

    def tearDown(self):
        self.window.close()
        self.wait_collection()
        self.temp.cleanup()

    def test_database_options_control_sources_without_collecting_hardware(self):
        self.window.database_panel.config_option.setChecked(False)
        self.window.navigate(1)
        with patch("app.services.collection_service.collect_postgres", return_value={}) as postgres, \
                patch("app.services.collection_service.collect_system") as system:
            self.window.start_collection()
            self.assertFalse(self.window.database_panel.run_button.isEnabled())
            self.wait_collection()
        postgres.assert_called_once_with(self.window.controller.connection.parameters, config=False, metadata=True,
                                         scope="database", sql="")
        system.assert_not_called()
        self.assertEqual(self.window.controller.history.store.search(), [])
        self.window.database_panel.metadata_option.setChecked(False)
        self.assertFalse(self.window.database_panel.run_button.isEnabled())

    def test_close_during_collection_waits_for_worker_without_blocking(self):
        import threading
        entered, release = threading.Event(), threading.Event()

        def collect(*args, **kwargs):
            entered.set()
            release.wait(2)
            return {}

        with patch("app.services.collection_service.collect_postgres", side_effect=collect):
            self.window.show()
            self.window.start_collection()
            try:
                self.assertTrue(entered.wait(1))
                self.window.close()
                self.assertTrue(self.window.controller.closing)
                self.assertTrue(self.window.isVisible())
            finally:
                release.set()
                self.wait_collection()
        self.assertFalse(self.window.isVisible())

    def test_current_query_scope_uses_editor_statement_and_empty_sql_is_rejected(self):
        from PySide6 import QtGui
        panel = self.window.database_panel
        panel.metadata_scope.setCurrentIndex(0)
        self.window.editor.setPlainText("SELECT 1; SELECT name FROM users;")
        cursor = self.window.editor.textCursor()
        cursor.movePosition(QtGui.QTextCursor.MoveOperation.End)
        self.window.editor.setTextCursor(cursor)
        with patch.object(self.window.controller, "collect_database") as collect:
            panel.run_button.click()
        collect.assert_called_once_with(True, True, "query", "SELECT name FROM users;")
        self.window.editor.clear()
        panel.run_button.click()
        self.assertFalse(self.window.controller.collection.busy)
        self.assertIn("Введите SQL", panel.hint.text())


if __name__ == "__main__":
    unittest.main()
