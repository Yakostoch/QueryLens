"""Поведение карточек структуры, поиска и сведений о столбцах."""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets
from test_database_report import collected_report

from app.ui.widgets.database_panel import DatabasePanel


class DatabaseStructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.panel = DatabasePanel()
        self.report = collected_report()
        self.report["postgres"]["tables"] = [
            dict(
                schema="public",
                name="orders",
                columns=3,
                indexes=2,
                fields=[
                    dict(name="id", type="bigint", nullable=False),
                    dict(name="created_at", type="timestamp with time zone", nullable=True),
                    dict(name="unknown_flag", type="boolean", nullable=None),
                ],
            ),
            dict(
                schema="archive",
                name="customers",
                columns=1,
                indexes=0,
                fields=[
                    dict(name="email", type="text", nullable=True),
                ],
            ),
        ]
        self.panel.show_report(self.report)

    def tearDown(self):
        self.panel.close()
        self.panel.deleteLater()
        self.app.processEvents()

    def test_card_header_has_summary_and_lazy_field_rows(self):
        card = self.panel.tables.cards[0]
        self.assertFalse(card.loaded)
        self.assertIn(
            "Поля: 3 · Индексы: 2", [w.text() for w in card.findChildren(QtWidgets.QLabel)]
        )
        card.set_expanded(True)
        self.assertEqual(len(card.field_widgets), 3)
        labels = [w.text() for w in card.findChildren(QtWidgets.QLabel)]
        for text in (
            "id",
            "bigint",
            "NOT NULL",
            "NULL",
            "NULL: нет данных",
            "timestamp with time zone",
        ):
            self.assertIn(text, labels)
        card.set_expanded(False)
        card.set_expanded(True)
        self.assertEqual(len(card.field_widgets), 3)

    def test_field_search_expands_matching_object_and_restores_state(self):
        self.panel.structure_search.setText("CREATED_AT")
        first, second = self.panel.tables.cards
        self.assertTrue(first.toggle.isChecked())
        self.assertFalse(first.isHidden())
        self.assertTrue(second.isHidden())
        self.assertEqual(
            [field["name"] for row, field in first.field_widgets if not row.isHidden()],
            ["created_at"],
        )
        self.panel.structure_search.clear()
        self.assertFalse(first.toggle.isChecked())
        self.assertFalse(second.isHidden())
        self.assertTrue(all(not row.isHidden() for row, _ in first.field_widgets))

    def test_table_and_type_search_and_no_matches(self):
        self.panel.structure_search.setText("archive.customers")
        self.assertTrue(self.panel.tables.cards[0].isHidden())
        self.assertFalse(self.panel.tables.cards[1].isHidden())
        self.panel.structure_search.setText("timestamp")
        self.assertFalse(self.panel.tables.cards[0].isHidden())
        self.panel.structure_search.setText("missing_table")
        self.assertTrue(all(card.isHidden() for card in self.panel.tables.cards))
        self.assertFalse(self.panel.tables.empty_label.isHidden())
        self.assertIn("0 из 2", self.panel.structure_hint.text())

    def test_query_scope_shows_selected_field_count_and_expands(self):
        db = self.report["postgres"]
        db["metadata_scope"] = "query"
        db["metadata_sql"] = "SELECT id FROM orders"
        db["tables"][0].update(
            columns=1, schema_columns=3, fields=[dict(name="id", type="bigint", nullable=False)]
        )
        self.panel.show_report(self.report)
        card = self.panel.tables.cards[0]
        self.assertTrue(card.toggle.isChecked())
        self.assertIn(
            "Поля запроса: 1 из 3 · Индексы: 2",
            [w.text() for w in card.findChildren(QtWidgets.QLabel)],
        )
        self.assertEqual(len(card.field_widgets), 1)

    def test_refresh_and_disabled_metadata_reset_search_and_cards(self):
        self.panel.structure_search.setText("created_at")
        self.report["postgres"]["metadata_collected"] = False
        self.panel.show_report(self.report)
        self.assertEqual(self.panel.structure_search.text(), "")
        self.assertEqual(self.panel.tables.cards, [])
        self.assertIn("Соберите структуру", self.panel.tables.toPlainText())
