"""Проверки карточек конфигурации и статистики базы данных."""

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets

from app.collectors.configuration import SETTINGS
from app.ui.widgets.analysis_report import ReportSection
from app.ui.widgets.database_panel import DatabasePanel


def collected_report():
    return {
        "errors": {},
        "system": None,
        "postgres": {
            "version": "PostgreSQL 17",
            "collected_at": "2026-11-13T10:00:00+00:00",
            "metadata_collected": True,
            "configuration_collected": True,
            "database_size_bytes": 1024**3,
            "metadata_scope": "database",
            "settings": [
                dict(
                    name="shared_buffers",
                    setting="16384",
                    unit="8kB",
                    source="configuration file",
                    pending_restart=True,
                ),
                dict(
                    name="work_mem",
                    setting="4096",
                    unit="kB",
                    source="default",
                    pending_restart=False,
                ),
                dict(name="jit", setting="on", unit="", source="session", pending_restart=None),
            ],
            "settings_unavailable": ["track_io_timing"],
            "database_statistics": {"numbackends": 3, "temp_bytes": 0, "stats_reset": None},
            "tables": [
                dict(
                    schema="public",
                    name="orders",
                    columns=2,
                    indexes=1,
                    fields=[dict(name="id", type="integer", nullable=False)],
                )
            ],
        },
    }


class DatabaseReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.panel = DatabasePanel()

    def tearDown(self):
        self.panel.close()
        self.panel.deleteLater()
        self.app.processEvents()

    def test_settings_are_collapsible_with_value_source_and_restart(self):
        self.panel.show_report(collected_report())
        sections = {s.toggle.text(): s for s in self.panel.report.sections}
        section = sections["shared_buffers"]
        self.assertFalse(section.toggle.isChecked())
        section.toggle.click()
        self.assertFalse(section.body.isHidden())
        labels = [w.text() for w in section.findChildren(QtWidgets.QLabel)]
        for text in (
            "Value / Значение характеристики",
            "128.00 MiB (исходное: 16384 8kB)",
            "Source / Источник",
            "configuration file",
            "Pending Restart / Требуется перезапуск",
            "Да",
        ):
            self.assertIn(text, labels)
        self.assertIn(
            "Нет", [w.text() for w in sections["work_mem"].findChildren(QtWidgets.QLabel)]
        )
        self.assertIn(
            "Нет данных", [w.text() for w in sections["jit"].findChildren(QtWidgets.QLabel)]
        )

    def test_setting_description_opens_from_title_info(self):
        self.panel.show_report(collected_report())
        section = next(s for s in self.panel.report.sections if s.toggle.text() == "work_mem")
        button = next(
            w
            for w in section.findChildren(QtWidgets.QToolButton)
            if w.accessibleName() == "О показателе: work_mem"
        )
        with patch.object(QtWidgets.QMessageBox, "information") as dialog:
            button.click()
        self.assertEqual(dialog.call_args.args[2], SETTINGS["work_mem"])

    def test_metadata_and_summary_survive_and_clear_resets_all(self):
        self.panel.show_report(collected_report())
        self.assertEqual(self.panel.metric_values["Таблицы"].text(), "1")
        self.assertEqual(self.panel.metric_values["Индексы"].text(), "1")
        self.assertEqual(self.panel.metric_values["Размер БД"].text(), "1.00 GiB")
        self.assertEqual(self.panel.tables.cards[0].toggle.text(), "public.orders")
        statistics = next(
            s for s in self.panel.report.sections if s.toggle.text() == "Накопленная статистика БД"
        )
        self.assertIn(
            "temp_bytes / Объём временных файлов, байт",
            [s.toggle.text() for s in statistics.findChildren(ReportSection)],
        )
        self.panel.clear_report()
        self.assertEqual(self.panel.report.toPlainText(), "")
        self.assertEqual(self.panel.report.sections, [])
        self.assertEqual(len(self.panel.tables.cards), 0)
        self.assertEqual(self.panel.metric_values["Таблицы"].text(), "—")

    def test_config_only_and_missing_settings_do_not_invent_statistics(self):
        report = collected_report()
        report["postgres"]["metadata_collected"] = False
        self.panel.show_report(report)
        titles = [s.toggle.text() for s in self.panel.report.sections]
        self.assertNotIn("Накопленная статистика БД", titles)
        missing = next(
            s for s in self.panel.report.sections if s.toggle.text() == "Недоступные настройки"
        )
        self.assertIn(
            "track_io_timing", [s.toggle.text() for s in missing.findChildren(ReportSection)]
        )
        self.assertEqual(self.panel.metric_values["Размер БД"].text(), "—")

    def test_collection_error_is_readable(self):
        self.panel.show_report(
            {"errors": {"postgres": "PermissionError"}, "postgres": None, "system": None}
        )
        self.assertIn("PermissionError", self.panel.report.toPlainText())
        self.assertIsNotNone(self.panel.report.findChild(QtWidgets.QFrame, "reportWarning"))
