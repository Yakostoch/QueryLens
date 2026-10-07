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
        self.temp = tempfile.TemporaryDirectory()
        preferences = Preferences(QtCore.QSettings(str(Path(self.temp.name) / "settings.ini"),
                                                  QtCore.QSettings.Format.IniFormat))
        self.window = MainWindow(self.temp.name, preferences)
        self.window.tools_panel.config_switch.setChecked(False)
        self.window.tools_panel.hardware_switch.setChecked(True)

    def tearDown(self):
        self.window.close()
        for _ in range(100):
            self.app.processEvents()
            if self.window.controller.collection.worker is None:
                break
            QtTest.QTest.qWait(10)
        self.temp.cleanup()

    def test_hardware_without_database_and_report_does_not_enter_sql_history(self):
        from app.collectors.system import collect_system

        with patch("app.services.collection_service.collect_system",
                   side_effect=lambda **kwargs: collect_system(1, 0.05, **kwargs)):
            self.assertTrue(self.window.database_panel.run_button.isEnabled())
            self.window.start_collection()
            self.assertFalse(self.window.database_panel.run_button.isEnabled())
            for _ in range(100):
                self.app.processEvents()
                if self.window.controller.collection.worker is None:
                    break
                QtTest.QTest.qWait(10)
        self.assertIsNone(self.window.controller.collection.worker)
        self.assertIn("RAM", self.window.database_panel.report.toPlainText())
        self.assertEqual(self.window.controller.history.store.search(), [])

    def test_close_during_collection_waits_for_worker_without_blocking(self):
        self.window.show()
        self.window.start_collection()
        self.window.close()
        self.assertTrue(self.window.controller.closing)
        for _ in range(100):
            self.app.processEvents()
            if self.window.controller.collection.worker is None:
                break
            QtTest.QTest.qWait(10)
        self.assertIsNone(self.window.controller.collection.worker)
        self.assertFalse(self.window.isVisible())

    def test_hardware_button_selects_only_hardware_and_opens_report(self):
        self.window.tools_panel.config_switch.setChecked(True)
        self.window.tools_panel.hardware_switch.setChecked(False)
        self.window.navigate(3)
        with patch.object(self.window.controller, "collect") as collect:
            self.window.tools_panel.hardware_button.click()
        collect.assert_called_once_with(False, True)
        self.assertFalse(self.window.tools_panel.config_switch.isChecked())
        self.assertTrue(self.window.tools_panel.hardware_switch.isChecked())
        self.assertEqual(self.window.pages.currentIndex(), 1)


if __name__ == "__main__":
    unittest.main()
