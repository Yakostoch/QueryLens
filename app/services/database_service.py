from app.database.connection import connect_postgresql


class DatabaseService:
    """Database operations independent of Qt and the interface."""

    @staticmethod
    def normalize_parameters(parameters):
        values = dict(parameters)
        for key in ("host", "database", "user"):
            values[key] = values.get(key, "").strip()
        if not all(values[key] for key in ("host", "database", "user")):
            raise ValueError("Заполните сервер, базу данных и пользователя.")
        return values

    def connect(self, parameters):
        return connect_postgresql(**self.normalize_parameters(parameters))
