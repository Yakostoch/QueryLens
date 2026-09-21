import os
import tempfile
import unittest
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6 import QtCore, QtGui, QtTest, QtWidgets

from app.sql_context import scan_sql, qt_position, python_position
from app.storage.preferences import Preferences
from app.ui.main_window import MainWindow
from app.ui.widgets.sql_editor import SqlEditor


class ContextTests(unittest.TestCase):
    def test_semicolons_in_quotes_comments_and_dollar_strings(self):
        text = "SELECT 'it''s; ok', \"a;b\", [a;b], `a;b`;\n/* outer; /* inner; */ ok */ SELECT $$a;b$$;\nSELECT $tag$x;y$tag$;"
        context = scan_sql(text)
        self.assertEqual(len(context.statements), 3)
        self.assertEqual(context.current(text.index("inner"))[0], 1)
        self.assertEqual(context.current(len(text))[0], 2)
        self.assertFalse(context.allows_completion(text.index("ok")))

    def test_boundaries_and_incomplete_input(self):
        text = "SELECT 1;\n\nSELECT 2;  "
        context = scan_sql(text)
        self.assertEqual(context.current(text.index(";"))[0], 0)
        self.assertEqual(context.current(text.index(";") + 1)[0], 1)
        self.assertEqual(context.current(len(text))[0], 1)
        self.assertEqual(scan_sql("-- comment;\n/* comment */").statements, [])
        self.assertEqual(len(scan_sql("SELECT 'unfinished;\nSELECT 2;").statements), 1)
        self.assertFalse(scan_sql("SELECT 'unfinished").allows_completion(18))
        self.assertIsNone(scan_sql("SELECT 1;\n-- note").current(17))

    def test_unicode_position_mapping(self):
        text = "SELECT '😀'; SELECT 2;"
        for position in range(len(text) + 1):
            self.assertEqual(python_position(text, qt_position(text, position)), position)


class EditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.editor = SqlEditor()
        self.editor.resize(700, 400)
        self.editor.show()
        self.editor.setFocus()
        self.app.processEvents()

    def tearDown(self):
        self.editor.close()

    def move_to(self, position):
        cursor = self.editor.textCursor()
        cursor.setPosition(qt_position(self.editor.toPlainText(), position))
        self.editor.setTextCursor(cursor)

    def test_cursor_selection_and_frame_rendering(self):
        text = "SELECT '😀;';\nSELECT id\nFROM users;"
        self.editor.setPlainText(text)
        self.move_to(text.index("id"))
        self.assertEqual(self.editor.sql_for_analysis(), "SELECT id\nFROM users;")
        cursor = self.editor.textCursor()
        cursor.setPosition(0)
        cursor.setPosition(6, QtGui.QTextCursor.MoveMode.KeepAnchor)
        self.editor.setTextCursor(cursor)
        self.assertEqual(self.editor.sql_for_analysis(), "SELECT")
        self.assertEqual(self.editor.context_label(), "Выделение")
        self.app.processEvents()
        self.assertFalse(self.editor.grab().isNull())

    def test_tab_completion_and_context(self):
        QtTest.QTest.keyClicks(self.editor, "sel")
        QtTest.QTest.keyClick(self.editor, QtCore.Qt.Key.Key_Tab)
        self.assertEqual(self.editor.toPlainText(), "SELECT")
        self.editor.undo()
        self.assertEqual(self.editor.toPlainText(), "sel")
        self.editor.setPlainText("-- sel")
        self.move_to(len("-- sel"))
        QtTest.QTest.keyClick(self.editor, QtCore.Qt.Key.Key_Tab)
        self.assertEqual(self.editor.toPlainText(), "-- sel    ")
        self.editor.setPlainText("SELECT 'sel")
        self.move_to(len("SELECT 'sel"))
        self.assertEqual(self.editor.completion_prefix(), "")
        self.editor.clear()
        QtTest.QTest.keyClick(self.editor, QtCore.Qt.Key.Key_Tab)
        self.assertEqual(self.editor.toPlainText(), "    ")

    def test_only_active_sql_is_saved_and_theme_keeps_result(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = QtCore.QSettings(str(Path(directory) / "settings.ini"), QtCore.QSettings.Format.IniFormat)
            window = MainWindow(directory, Preferences(settings))
            try:
                window.editor.setPlainText("SELECT 1; SELECT 2;")
                cursor = window.editor.textCursor()
                cursor.movePosition(QtGui.QTextCursor.MoveOperation.End)
                window.editor.setTextCursor(cursor)
                window.analyze()
                self.assertEqual(window.history_store.search()[0]["sql"], "SELECT 2;")
                result = window.output.toPlainText()
                window.preferences.save("light", 12, True)
                window.apply_preferences()
                self.app.processEvents()
                self.assertEqual(window.output.toPlainText(), result)
                cursor.setPosition(0)
                cursor.setPosition(8, QtGui.QTextCursor.MoveMode.KeepAnchor)
                window.editor.setTextCursor(cursor)
                window.analyze()
                self.assertEqual(window.history_store.search()[0]["sql"], "SELECT 1")
            finally:
                window.close()


if __name__ == "__main__":
    unittest.main()
