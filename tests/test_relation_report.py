"""Группировка метаданных и сохранение смысла показателей объектов."""

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets
from test_database_report import collected_report

from app.ui.widgets.analysis_report import ReportSection, ReportSql
from app.ui.widgets.database_report import DatabaseReportView


def metadata_report():
    report = collected_report()
    report["postgres"]["tables"][0]["diagnostics"] = {
        "sources": {
            "relation": {
                "status": "ok",
                "data": dict(
                    relation_kind="r",
                    is_partition=False,
                    estimated_rows=12000,
                    total_bytes=1048576,
                    main_fork_bytes=524288,
                    table_bytes=786432,
                    indexes_bytes=262144,
                    estimated_pages=64,
                    last_analyze=None,
                    last_autoanalyze="2026-11-13T10:00:00+00:00",
                    n_dead_tup=12,
                ),
            },
            "indexes": {
                "status": "ok",
                "data": [
                    dict(
                        name="orders_pkey",
                        schema="public",
                        access_method="btree",
                        is_valid=True,
                        is_primary=True,
                        is_unique=True,
                        nulls_not_distinct=None,
                        definition="CREATE UNIQUE INDEX orders_pkey ON public.orders (id)",
                        included_columns=["created_at"],
                        keys=[dict(position=1, column_name="id", direction="ASC")],
                    )
                ],
            },
            "constraints": {
                "status": "ok",
                "data": [
                    dict(
                        name="orders_customer_fk",
                        kind="f",
                        definition="FOREIGN KEY (customer_id) REFERENCES customers(id)",
                        columns=["customer_id"],
                        referenced_relation="customers",
                        referenced_columns=["id"],
                        is_validated=True,
                        is_enforced=None,
                    )
                ],
            },
            "column_statistics": {
                "status": "ok",
                "data": [
                    dict(
                        column_name="id",
                        inherited=False,
                        null_frac=0,
                        avg_width=4,
                        n_distinct=-0.5,
                        correlation=0.98,
                    )
                ],
            },
        }
    }
    report["postgres"]["tables"].append(
        dict(
            schema="public",
            name="customers",
            columns=1,
            indexes=0,
            fields=[dict(name="id", type="integer", nullable=False)],
        )
    )
    return report


class RelationReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.view = DatabaseReportView()

    def tearDown(self):
        self.view.close()
        self.view.deleteLater()
        self.app.processEvents()

    def show_table(self, report=None):
        self.view.show_report(report or metadata_report())
        metadata = next(s for s in self.view.sections if s.toggle.text() == "Метаданные")
        metadata.set_expanded(True)
        table = next(
            s for s in metadata.findChildren(ReportSection) if s.toggle.text() == "public.orders"
        )
        table.set_expanded(True)
        return table

    def test_one_metadata_section_contains_all_tables_and_loads_once(self):
        self.view.show_report(metadata_report())
        titles = [s.toggle.text() for s in self.view.sections]
        self.assertEqual(titles.count("Метаданные"), 1)
        self.assertFalse(any(t.startswith("Метаданные /") for t in titles))
        metadata = next(s for s in self.view.sections if s.toggle.text() == "Метаданные")
        tables = metadata.findChildren(ReportSection)
        self.assertEqual([s.toggle.text() for s in tables], ["public.orders", "public.customers"])
        self.assertEqual(tables[0].content.count(), 0)
        tables[0].set_expanded(True)
        count = tables[0].content.count()
        self.assertEqual(count, 5)
        tables[0].set_expanded(False)
        tables[0].set_expanded(True)
        self.assertEqual(tables[0].content.count(), count)

    def test_table_has_structured_properties_indexes_constraints_and_statistics(self):
        table = self.show_table()
        titles = [s.toggle.text() for s in table.findChildren(ReportSection)]
        for title in (
            "Размеры объекта",
            "Строки и актуальность статистики",
            "Индексы",
            "Ограничения",
            "Статистика столбцов",
            "Столбцы",
            "orders_pkey",
            "orders_customer_fk",
            "Ключи индекса",
        ):
            self.assertIn(title, titles)
        labels = [w.text() for w in table.findChildren(QtWidgets.QLabel)]
        for value in (
            "1.00 MiB (1 048 576 байт)",
            "-0.5",
            "Нет данных",
            "Внешний ключ",
            "created_at",
        ):
            self.assertIn(value, labels)
        definitions = [w.toPlainText() for w in table.findChildren(ReportSql)]
        self.assertTrue(any("CREATE UNIQUE INDEX" in sql for sql in definitions))
        self.assertTrue(any("FOREIGN KEY" in sql for sql in definitions))

    def test_unavailable_index_source_does_not_look_like_zero(self):
        report = metadata_report()
        report["postgres"]["tables"][0]["diagnostics"]["sources"]["indexes"] = {
            "status": "error",
            "error": "PermissionError",
            "data": None,
        }
        table = self.show_table(report)
        indexes = next(s for s in table.findChildren(ReportSection) if s.toggle.text() == "Индексы")
        text = " ".join(w.text() for w in indexes.findChildren(QtWidgets.QLabel))
        self.assertIn("Сведения недоступны (PermissionError)", text)
        self.assertNotIn("объектов не найдено", text)

    def test_empty_index_source_is_distinct_from_failure(self):
        report = metadata_report()
        report["postgres"]["tables"][0]["diagnostics"]["sources"]["indexes"]["data"] = []
        table = self.show_table(report)
        indexes = next(s for s in table.findChildren(ReportSection) if s.toggle.text() == "Индексы")
        text = " ".join(w.text() for w in indexes.findChildren(QtWidgets.QLabel))
        self.assertIn("Сведения получены: объектов не найдено", text)

    def test_view_does_not_present_zero_size_as_stored_table_data(self):
        report = metadata_report()
        report["postgres"]["tables"][0]["diagnostics"]["sources"]["relation"]["data"][
            "relation_kind"
        ] = "v"
        table = self.show_table(report)
        titles = [s.toggle.text() for s in table.findChildren(ReportSection)]
        self.assertNotIn("Размеры объекта", titles)
        labels = [w.text() for w in table.findChildren(QtWidgets.QLabel)]
        self.assertIn("Представление", labels)

    def test_column_statistic_info_explains_negative_distinct_values(self):
        table = self.show_table()
        button = next(
            w
            for w in table.findChildren(QtWidgets.QToolButton)
            if w.accessibleName().startswith("О показателе: Distinct Values /")
        )
        with patch.object(QtWidgets.QMessageBox, "information") as dialog:
            button.click()
        self.assertIn("отрицательное", dialog.call_args.args[2])
        self.assertIn("не доказывает уникальность", dialog.call_args.args[2])
