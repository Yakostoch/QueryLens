"""Выполнение запросов на переданном соединении; жизненным циклом владеет сервис."""


class QueryExecutor:
    def __init__(self, connection):
        self.connection = connection

    def fetch_all(self, query, parameters=None):
        from psycopg.rows import dict_row

        with self.connection.cursor(row_factory=dict_row) as cursor:
            # Расширенный протокол не допускает несколько команд в одном вызове.
            cursor.execute(query, parameters, prepare=True)
            return cursor.fetchall()

    def fetch_one(self, query, parameters=None):
        from psycopg.rows import dict_row

        with self.connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(query, parameters, prepare=True)
            return cursor.fetchone()

    def execute(self, query, parameters=None):
        """Выполняет команду без получения набора строк."""
        with self.connection.cursor() as cursor:
            cursor.execute(query, parameters, prepare=True)
