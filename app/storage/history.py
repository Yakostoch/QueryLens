import sqlite3
from datetime import datetime, timezone
from pathlib import Path


class HistoryStore:
    """История запусков. Каждый запуск сохраняется отдельно, включая результат."""

    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        with self.connection:
            self.connection.execute(
                "CREATE TABLE IF NOT EXISTS queries ("
                "id INTEGER PRIMARY KEY, sql TEXT NOT NULL, "
                "result TEXT NOT NULL, created_at TEXT NOT NULL)"
            )

    def add(self, sql, result):
        with self.connection:
            cursor = self.connection.execute(
                "INSERT INTO queries (sql, result, created_at) VALUES (?, ?, ?)",
                (sql, result, datetime.now(timezone.utc).isoformat()),
            )
        return cursor.lastrowid

    def search(self, text=""):
        # instr ищет буквальный текст: % и _ не превращаются в шаблоны SQL.
        return self.connection.execute(
            "SELECT * FROM queries WHERE instr(lower(sql), lower(?)) > 0 "
            "ORDER BY id DESC LIMIT 200", (text,),
        ).fetchall()

    def delete(self, query_id):
        with self.connection:
            self.connection.execute("DELETE FROM queries WHERE id = ?", (query_id,))

    def close(self):
        self.connection.close()
