import importlib.util
import os
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6 import QtCore, QtWidgets

from app.collectors.system import collect_system
from app.storage.preferences import Preferences
from app.ui.main_window import MainWindow


def sample_report(options=None):
    return {"options": options or {"cpu": True, "ram": True, "disk": True},
            "logical_cores": 4, "ram_total_bytes": 16 * 2**30,
            "samples": [{"timestamp": datetime.now(timezone.utc).isoformat(), "interval_seconds": 1,
                         "cpu_percent_per_core": [20, 40, 60, 80], "ram_used_percent": 50,
                         "ram_available_bytes": 8 * 2**30, "swap_used_bytes": 0,
                         "disk_io_rates": {"disk": {"read_bytes_per_second": 2**20,
                                                     "write_bytes_per_second": 2 * 2**20}}}]}


class SystemSamplingTests(unittest.TestCase):
    def test_disabled_metrics_are_not_read(self):
        psutil = Mock()
        psutil.cpu_count.return_value = 4
        psutil.cpu_percent.return_value = [10, 20, 30, 40]
        with patch.dict("sys.modules", {"psutil": psutil}):
            report = collect_system(1, 0.001, options={"cpu": True, "ram": False, "disk": False})
        self.assertEqual(report["samples"][0]["cpu_percent_per_core"], [10, 20, 30, 40])
        psutil.virtual_memory.assert_not_called()
        psutil.swap_memory.assert_not_called()
        psutil.disk_io_counters.assert_not_called()

    @unittest.skipUnless(importlib.util.find_spec("psutil"), "psutil is not installed")
    def test_real_system_measurement(self):
        report = collect_system(1, 0.05)
        self.assertGreater(report["ram_total_bytes"], 0)
        self.assertEqual(len(report["samples"]), 1)


class MonitoringInterfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        settings = Preferences(QtCore.QSettings(str(Path(self.temp.name) / "settings.ini"),
                                               QtCore.QSettings.Format.IniFormat))
        self.window = MainWindow(self.temp.name, settings)

    def pump_until(self, condition):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            self.app.processEvents()
            if condition():
                return
            time.sleep(0.005)
        self.fail("Timed out")

    def tearDown(self):
        self.window.close()
        self.pump_until(lambda: not self.window.controller.monitoring.busy)
        self.temp.cleanup()

    @staticmethod
    def stream(options, interval, cancelled):
        while not cancelled():
            yield sample_report(options)
            time.sleep(0.01)

    def test_navigation_live_updates_stop_restart_and_no_database(self):
        window = self.window
        panel = window.resource_panel
        self.assertEqual(list(window.nav_buttons), ["SQL", "Анализ БД", "Ресурсы"])
        window.nav_buttons["Ресурсы"].click()
        self.assertEqual(window.pages.currentIndex(), 2)
        with patch("app.workers.monitoring_worker.monitor_resources", side_effect=self.stream):
            panel.start_button.click()
            self.pump_until(lambda: len(panel.charts["cpu"].points) >= 2)
            self.assertFalse(panel.options_widget.isEnabled())
            self.assertIn("50.0%", panel.values["cpu"].text())
            self.assertIn("MiB/с", panel.console.toPlainText())
            self.assertIsNone(window.controller.connection.parameters)
            self.assertEqual(window.controller.history.store.search(), [])
            window.navigate(0)
            self.assertTrue(window.controller.monitoring.busy)
            panel.stop_button.click()
            self.pump_until(lambda: not window.controller.monitoring.busy)
            self.assertTrue(panel.options_widget.isEnabled())
            panel.options["disk"].setChecked(False)
            panel.start_button.click()
            self.pump_until(lambda: len(panel.charts["cpu"].points) >= 1)
            self.assertEqual(len(panel.charts["disk"].points), 0)
            panel.stop_button.click()
            self.pump_until(lambda: not window.controller.monitoring.busy)

    def test_close_waits_for_monitor_and_releases_worker(self):
        self.window.show()
        with patch("app.workers.monitoring_worker.monitor_resources", side_effect=self.stream):
            self.window.start_monitoring()
            self.pump_until(lambda: len(self.window.resource_panel.charts["cpu"].points) > 0)
            self.window.close()
            self.pump_until(lambda: not self.window.controller.monitoring.busy)
        self.assertFalse(self.window.isVisible())

    def test_missing_dependency_is_visible_and_start_is_restored(self):
        error = ModuleNotFoundError("psutil", name="psutil")
        with patch("app.workers.monitoring_worker.monitor_resources", side_effect=error):
            self.window.start_monitoring()
            self.pump_until(lambda: not self.window.controller.monitoring.busy)
        panel = self.window.resource_panel
        self.assertIn("pip install psutil", panel.status.text())
        self.assertTrue(panel.start_button.isEnabled())

    def test_bounded_history_and_unavailable_disk_not_zero(self):
        panel = self.window.resource_panel
        for _ in range(550):
            panel.show_sample(sample_report())
        self.assertEqual(len(panel.charts["cpu"].points), 120)
        self.assertLessEqual(panel.console.document().blockCount(), 500)
        report = sample_report()
        report["samples"][0]["disk_io_rates"] = None
        panel.show_sample(report)
        self.assertEqual(panel.charts["disk"].points[-1][1], [None, None])
        self.assertIn("недоступны", panel.details["disk"].text())
        for option in panel.options.values():
            option.setChecked(False)
        self.assertFalse(panel.start_button.isEnabled())
