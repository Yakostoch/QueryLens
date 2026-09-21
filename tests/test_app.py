import os
import tempfile
import unittest
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6 import QtCore, QtGui, QtTest, QtWidgets

from app.storage.history import HistoryStore
from app.storage.preferences import Preferences
from app.ui.main_window import MainWindow
from app.ui.settings_dialog import SettingsDialog


class HistoryTests(unittest.TestCase):
    def test_persistence_search_delete_and_literal_sql(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            store = HistoryStore(path)
            original = "SELECT 'Привет', '100%';\n-- DROP TABLE queries;"
            query_id = store.add(original, "Результат")
            store.add("SELECT id FROM users;", "second")
            store.close()
            store = HistoryStore(path)
            self.assertEqual(len(store.search()), 2)
            self.assertEqual(store.search()[0]["result"], "second")
            self.assertEqual(store.search("%")[0]["sql"], original)
            self.assertEqual(len(store.search("USERS")), 1)
            self.assertEqual(store.search("no match"), [])
            store.delete(query_id)
            self.assertEqual(len(store.search()), 1)
            store.close()


class InterfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        cls.app.setStyle("Fusion")
        # Offscreen Qt on Windows may not discover installed fonts automatically.
        for filename in ("segoeui.ttf", "consola.ttf"):
            path = Path("C:/Windows/Fonts") / filename
            if path.exists():
                QtGui.QFontDatabase.addApplicationFont(str(path))
        cls.app.setFont(QtGui.QFont("Segoe UI", 10))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings_path = str(Path(self.temp.name) / "settings.ini")
        self.preferences = Preferences(QtCore.QSettings(self.settings_path, QtCore.QSettings.Format.IniFormat))
        self.window = MainWindow(self.temp.name, self.preferences)
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.window.close()
        self.app.processEvents()
        self.temp.cleanup()

    def test_analysis_history_restore_and_opt_out(self):
        window = self.window
        window.analyze_button.click()
        self.assertEqual(window.history_store.search(), [])
        sql = "SELECT id, name\nFROM users\nWHERE active = 1;"
        window.editor.setPlainText(sql)
        window.analyze_button.click()
        result = window.output.toPlainText()
        self.assertTrue(window.output.isReadOnly())
        self.assertEqual(window.history_panel.items.count(), 1)
        window.editor.setPlainText("different")
        self.assertEqual(window.output.toPlainText(), "")
        window.history_panel.items.setCurrentRow(0)
        window.history_panel.open_button.click()
        self.assertEqual(window.editor.toPlainText(), sql)
        self.assertEqual(window.output.toPlainText(), result)
        self.preferences.save("light", 16, False)
        window.apply_preferences()
        window.analyze_button.click()
        self.assertEqual(len(window.history_store.search()), 1)
        window.history_panel.delete_button.click()
        self.assertEqual(window.history_store.search(), [])
        saved = Preferences(QtCore.QSettings(self.settings_path, QtCore.QSettings.Format.IniFormat))
        self.assertEqual(saved.theme, "light")
        self.assertEqual(saved.font_size, 16)
        self.assertFalse(saved.save_history)

    def test_editor_themes_settings_and_rendering(self):
        window = self.window
        window.editor.clear()
        QtTest.QTest.keyClick(window.editor, QtCore.Qt.Key.Key_Tab)
        self.assertEqual(window.editor.toPlainText(), "    ")
        window.editor.setPlainText("SELECT id, name\nFROM users\nWHERE active = 1;")
        window.analyze_button.click()
        window.editor.setFocus()
        QtTest.QTest.keyClick(window.editor, QtCore.Qt.Key.Key_Return, QtCore.Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(len(window.history_store.search()), 2)
        preview_dir = Path(__file__).resolve().parents[1] / ".artifacts"
        preview_dir.mkdir(exist_ok=True)
        for theme in ("dark", "light"):
            self.preferences.save(theme, 12, True)
            window.apply_preferences()
            self.app.processEvents()
            self.assertTrue(window.grab().save(str(preview_dir / f"{theme}.png")))
        dialog = SettingsDialog(self.preferences, window)
        dialog.show()
        self.app.processEvents()
        self.assertTrue(dialog.grab().save(str(preview_dir / "settings.png")))
        dialog.close()
        window.editor.setPlainText("\n".join("SELECT 1;" for _ in range(150)))
        window.editor.verticalScrollBar().setValue(80)
        window.resize(960, 600)
        self.app.processEvents()
        self.assertGreater(window.editor.numbers.width(), 0)


if __name__ == "__main__":
    unittest.main()
