"""Проверки сворачиваемого представления отчётов и справки измерений."""

import json
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtGui, QtTest, QtWidgets
from test_analysis_presentation import bundle, report

from app.analyzers.diagnostic_tree import build_diagnostic_tree
from app.analyzers.runtime_analysis import analyze_runtime
from app.services.analysis_presentation import MARKER, present_report
from app.ui.theme import apply_theme
from app.ui.widgets.analysis_report import (
    EXPLAIN_WARNING,
    AnalysisReportView,
    ReportSection,
    ReportSql,
    metric_info,
)
from app.ui.widgets.sql_highlighter import SYNTAX_COLORS


def measured_report():
    payload = bundle("SELECT DISTINCT id FROM orders ORDER BY id")
    payload.update(
        mode="explain_analyze",
        explain={"analyze": True},
        started_at="2026-11-13T09:00:00+00:00",
        finished_at="2026-11-13T09:00:01.200000+00:00",
        plan_summary=dict(
            node_type="Sort", total_cost=1200.5, plan_rows=8000, plan_width=8, estimated_bytes=64000
        ),
        runtime_analysis=dict(
            status="measured",
            execution_time_ms=42.125,
            planning_time_ms=0.4,
            node_observations=[
                dict(
                    node_type="Sort",
                    path="Plan",
                    actual_rows_per_loop=8000,
                    actual_loops=1,
                    approximate_total_output_rows=8000,
                    actual_total_time_ms_per_loop=41.0,
                    plan_rows=8000,
                    runtime_counters={"Shared Hit Blocks": 60, "Temp Read Blocks": 12},
                    sort_method="external merge",
                    sort_space_type="Disk",
                    sort_space_used_kb=256,
                    worker_details=[{"Worker Number": 0, "Actual Rows": 4000}],
                )
            ],
            limitations=["Счётчики включают дочерние операции."],
        ),
    )
    payload["diagnostic_tree"] = build_diagnostic_tree(payload)
    details = (
        "EXPLAIN ANALYZE: SELECT выполнен.\nSQL:\n"
        + payload["sql"]
        + "\nСВОДКА ПЛАНА\nКорневой узел: Sort\n"
        "ПРОВЕРКА AP02/AP03 ПО ПЛАНУ\nНет конструкций.\n"
        "ПРОВЕРКА ОБЪЁМА И ЛИШНЕЙ РАБОТЫ\nНет конструкций.\n"
        "ПРОВЕРКА OR И NOT IN\nНет конструкций.\n"
        "ПРОВЕРКА DISTINCT, СОРТИРОВОК, HAVING, CTE, ОКОН И JOIN\n"
        "AP06: DISTINCT\n  Нужна проверка уникальности.\n"
        "AP07: ORDER BY id\n  Отдельная сортировка в плане.\n"
        "СОБРАННЫЙ КОНТЕКСТ\nМетаданные получены.\n"
    )
    return details + MARKER + json.dumps(payload, ensure_ascii=False), payload


class ReportBlockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.view = AnalysisReportView()
        self.view.resize(360, 700)

    def tearDown(self):
        self.view.close()
        self.view.deleteLater()
        self.app.processEvents()

    def test_brief_has_three_blocks_and_separate_timings(self):
        text, payload = measured_report()
        self.view.show_brief(present_report(text), payload)
        self.assertEqual(
            [s.toggle.text() for s in self.view.sections],
            ["Время выполнения и анализа", "Найденные проблемы", "Причины замедления"],
        )
        labels = [w.text() for w in self.view.findChildren(QtWidgets.QLabel)]
        self.assertIn("42.125", labels)
        self.assertIn("1 200", labels)

    def test_details_preserve_data_and_nested_checks(self):
        text, payload = measured_report()
        self.view.show_details(present_report(text), payload)
        self.assertEqual(len(self.view.sections), 9)
        labels = [w.text() for w in self.view.findChildren(QtWidgets.QLabel)]
        self.assertIn("external merge", labels)
        self.assertIn("Shared Hit Blocks / Общие блоки: в кэше", labels)
        self.assertIn("Workers / Параллельные работники", labels)
        titles = [w.toggle.text() for w in self.view.findChildren(ReportSection)]
        self.assertIn("Удаление повторов DISTINCT", titles)
        self.assertIn("Сортировка", titles)
        self.assertIn("EXPLAIN ANALYZE", self.view.toPlainText())

    def test_collapsing_title_and_arrow_are_independent_of_other_blocks(self):
        text, payload = measured_report()
        self.view.show_brief(present_report(text), payload)
        self.view.show()
        self.app.processEvents()
        first, second = self.view.sections[:2]
        QtTest.QTest.mouseClick(first.title_label, QtCore.Qt.MouseButton.LeftButton)
        self.assertTrue(first.body.isHidden())
        self.assertTrue(second.toggle.isChecked())
        QtTest.QTest.mouseClick(first.toggle, QtCore.Qt.MouseButton.LeftButton)
        self.assertFalse(first.body.isHidden())

    def test_info_button_opens_explanation_dialog(self):
        text, payload = measured_report()
        self.view.show_brief(present_report(text), payload)
        button = next(
            w
            for w in self.view.findChildren(QtWidgets.QToolButton)
            if w.objectName() == "metricInfo"
        )
        with patch.object(QtWidgets.QMessageBox, "information") as dialog:
            button.click()
        self.assertIn("Execution Time", dialog.call_args.args[2])
        self.assertIn("не измеренное время", metric_info("total_cost")[1])

    def test_explain_does_not_invent_timings_and_old_text_stays_readable(self):
        payload = bundle()
        self.view.show_brief(present_report(report(payload)), payload)
        labels = [w.text() for w in self.view.findChildren(QtWidgets.QLabel)]
        self.assertIn("Нет данных", labels)
        self.assertFalse(any(w.text() == "0" for w in self.view.findChildren(QtWidgets.QLabel)))
        self.view.setPlainText("Старый отчёт <script>")
        self.assertEqual(self.view.toPlainText(), "Старый отчёт <script>")
        self.view.clear()
        self.assertEqual(self.view.toPlainText(), "")
        self.assertEqual(len(self.view.sections), 0)

    def test_long_headers_fit_narrow_report(self):
        text, payload = measured_report()
        self.view.show_details(present_report(text), payload)
        self.view.show()
        self.app.processEvents()
        self.assertLessEqual(self.view.widget().width(), self.view.viewport().width())
        for section in self.view.sections:
            self.assertLessEqual(section.width(), self.view.viewport().width())

    def test_unknown_findings_have_burgundy_card_and_advice(self):
        text, payload = measured_report()
        self.view.show_brief(present_report(text), payload)
        problems = self.view.sections[1]
        findings = problems.findChildren(ReportSection)
        insufficient = [s for s in findings if s.property("tone") == "insufficient"]
        self.assertTrue(insufficient)
        for section in insufficient:
            self.assertTrue(section.toggle.text().startswith("Недостаточно данных · "))
            advice = section.findChildren(ReportSection)
            self.assertTrue(
                any(
                    s.toggle.text() == "Что проверить" and s.property("tone") == "advice"
                    for s in advice
                )
            )

    def test_explain_warning_precedes_all_sections(self):
        payload = bundle()
        self.view.show_brief(present_report(report(payload)), payload)
        warning = self.view.layout.itemAt(0).widget()
        self.assertEqual(warning.objectName(), "reportWarning")
        self.assertIn(EXPLAIN_WARNING, [w.text() for w in warning.findChildren(QtWidgets.QLabel)])
        self.assertEqual(self.view.layout.itemAt(1).widget(), self.view.sections[0])
        timing_labels = [w.text() for w in self.view.sections[0].findChildren(QtWidgets.QLabel)]
        self.assertNotIn(EXPLAIN_WARNING, timing_labels)
        text, payload = measured_report()
        self.view.show_brief(present_report(text), payload)
        self.assertEqual(self.view.layout.itemAt(0).widget(), self.view.sections[0])

    def test_like_and_application_results_are_under_sql_checks(self):
        text, payload = measured_report()
        lines = [
            "LIKE/ILIKE с начальным %: 1; неизвестный шаблон/ESCAPE: 0.",
            "N+1: недостаточно данных — требуется трасса операции приложения.",
        ]
        text = text.replace(
            "ПРОВЕРКА AP02/AP03 ПО ПЛАНУ",
            "КОНСТРУКЦИИ ДЛЯ ПРОВЕРКИ\n" + "\n".join(lines) + "\nПРОВЕРКА AP02/AP03 ПО ПЛАНУ",
        )
        self.view.show_details(present_report(text), payload)
        constructs = self.view.sections[4]
        checks = self.view.sections[7]
        for line in lines:
            self.assertFalse(
                any(line in w.text() for w in constructs.findChildren(QtWidgets.QLabel))
            )
            self.assertTrue(any(line in w.text() for w in checks.findChildren(QtWidgets.QLabel)))

    def test_execution_limitations_are_in_corresponding_metric_dialogs(self):
        text, payload = measured_report()
        limitations = analyze_runtime({}, measured=False)["limitations"]
        payload["runtime_analysis"]["limitations"] = limitations
        node = payload["runtime_analysis"]["node_observations"][0]
        node.update(row_comparison_status="not_comparable", temporary_io_observed=True)
        self.view.show_details(present_report(report(payload)), payload)
        runtime = self.view.sections[3]
        labels = [w.text() for w in runtime.findChildren(QtWidgets.QLabel)]
        for note in limitations:
            self.assertFalse(any(note in text for text in labels))
        expected = {
            "Execution Time /": limitations[0],
            "Actual Rows /": limitations[1],
            "Shared Hit Blocks /": limitations[2],
            "Temp Read Blocks /": limitations[3],
            "Row Comparison Status /": limitations[4],
        }
        for prefix, note in expected.items():
            button = next(
                w
                for w in runtime.findChildren(QtWidgets.QToolButton)
                if w.accessibleName().startswith("О показателе: " + prefix)
            )
            with patch.object(QtWidgets.QMessageBox, "information") as dialog:
                button.click()
            self.assertIn(note, dialog.call_args.args[2])

    def test_summary_and_tree_question_buttons_explain_actual_mode(self):
        for measured in (False, True):
            if measured:
                text, payload = measured_report()
            else:
                payload = bundle()
                text = report(payload)
            self.view.show_details(present_report(text), payload)
            summary, tree = self.view.sections[1:3]
            for section in (summary, tree):
                question = next(
                    w for w in section.findChildren(QtWidgets.QToolButton) if w.text() == "?"
                )
                with patch.object(QtWidgets.QMessageBox, "information") as dialog:
                    question.click()
                explanation = dialog.call_args.args[2]
                if measured:
                    self.assertNotIn("Измеренного времени нет", explanation)
                    self.assertNotIn("Фактическое время выполнения не измерялось", explanation)
                elif section is summary:
                    self.assertIn("Оценочный план без ANALYZE", explanation)
                    self.assertIn("кандидаты для проверки", explanation)
                else:
                    self.assertIn("Приоритет — порядок проверки", explanation)
                    self.assertIn("Измеренного времени нет", explanation)
            titles = [s.toggle.text() for s in tree.findChildren(ReportSection)]
            self.assertIn("Объём и лишняя работа", titles)
            self.assertIn("Обращения приложения", titles)
            self.assertIn("Что проверять дальше", titles)

    def test_sql_is_read_only_highlighted_and_preserves_literals(self):
        payload = bundle("SELECT '<b>literal</b>' AS name, -- комментарий\n42 AS answer")
        self.view.show_details(present_report(report(payload)), payload)
        sql = self.view.findChild(ReportSql)
        self.assertEqual(sql.toPlainText(), payload["sql"])
        self.assertTrue(sql.isReadOnly())
        sql.highlighter.rehighlight()
        ranges = sql.document().firstBlock().layout().formats()
        keyword = next(span for span in ranges if span.start == 0)
        self.assertEqual(keyword.length, 6)
        self.assertEqual(
            keyword.format.foreground().color(),
            sql.highlighter.formats["keyword"].foreground().color(),
        )
        try:
            apply_theme(self.app, "light")
            self.app.processEvents()
            self.assertEqual(
                sql.highlighter.formats["keyword"].foreground().color(),
                QtGui.QColor(SYNTAX_COLORS["light"]["keyword"]),
            )
        finally:
            apply_theme(self.app, "dark")
