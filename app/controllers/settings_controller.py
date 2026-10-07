from dataclasses import dataclass

from PySide6 import QtCore

from app.storage.preferences import Preferences


@dataclass(frozen=True)
class SettingsValues:
    theme: str
    font_size: int
    save_history: bool


class SettingsController(QtCore.QObject):
    changed = QtCore.Signal(object)
    failed = QtCore.Signal(str)

    def __init__(self, preferences=None, parent=None):
        super().__init__(parent)
        self._preferences = preferences if preferences is not None else Preferences()

    @property
    def values(self):
        return SettingsValues(self._preferences.theme, self._preferences.font_size,
                              self._preferences.save_history)

    def save(self, theme, font_size, save_history):
        try:
            self._preferences.save(theme, font_size, save_history)
        except OSError as error:
            self.failed.emit(str(error))
        self.changed.emit(self.values)

    def set_font_size(self, size):
        values = self.values
        self.save(values.theme, size, values.save_history)
