from PySide6 import QtCore


class Preferences:
    def __init__(self, settings=None):
        self.settings = settings if settings is not None else QtCore.QSettings()

    @property
    def theme(self):
        value = self.settings.value("theme", "dark")
        return value if value in ("light", "dark") else "dark"

    @property
    def font_size(self):
        try:
            return max(10, min(20, int(self.settings.value("font_size", 12))))
        except (TypeError, ValueError):
            return 12

    @property
    def save_history(self):
        return self.settings.value("save_history", True, type=bool)

    def save(self, theme, font_size, save_history):
        self.settings.setValue("theme", theme)
        self.settings.setValue("font_size", font_size)
        self.settings.setValue("save_history", save_history)
        self.settings.sync()
        if self.settings.status() != QtCore.QSettings.Status.NoError:
            raise OSError("Не удалось сохранить настройки.")
