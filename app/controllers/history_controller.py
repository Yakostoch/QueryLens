import sqlite3

from PySide6 import QtCore

from app.storage.history import HistoryStore


class HistoryController(QtCore.QObject):
    entries_changed = QtCore.Signal(object)
    failed = QtCore.Signal(str)

    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.store = None
        self.initial_error = None
        self.last_error = None
        self._search = ""
        try:
            self.store = HistoryStore(path)
        except (OSError, sqlite3.Error) as error:
            self.initial_error = str(error)

    def search(self, text=""):
        self._search = text
        try:
            entries = self.store.search(text) if self.store is not None else []
            self.entries_changed.emit([dict(entry) for entry in entries])
        except sqlite3.Error as error:
            self.failed.emit(str(error))

    def add(self, sql, result):
        self.last_error = None
        if self.store is None:
            return False
        try:
            self.store.add(sql, result)
        except sqlite3.Error as error:
            self.last_error = str(error)
            self.failed.emit(str(error))
            return False
        self.search(self._search)
        return True

    def delete(self, query_id):
        if self.store is None:
            return False
        try:
            self.store.delete(query_id)
        except sqlite3.Error as error:
            self.failed.emit(str(error))
            return False
        self.search(self._search)
        return True

    def close(self):
        if self.store is not None:
            self.store.close()
            self.store = None
