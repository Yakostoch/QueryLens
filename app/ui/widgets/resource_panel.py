"""Представление мониторинга: графики и ограниченный журнал, без измерений ОС."""
from collections import deque
from datetime import datetime

from PySide6 import QtCore, QtGui, QtWidgets


class LiveChart(QtWidgets.QWidget):
    def __init__(self, colors, unit, fixed_max=None, parent=None):
        super().__init__(parent)
        self.colors = colors
        self.unit = unit
        self.fixed_max = fixed_max
        self.points = deque(maxlen=120)
        self.setMinimumHeight(145)
        self.setMinimumWidth(200)
        self.setAccessibleName(f"График, единицы: {unit}; текущие значения указаны над графиком")

    def append(self, elapsed, values):
        self.points.append((elapsed, values))
        self.update()

    def clear(self):
        self.points.clear()
        self.update()

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        bounds = QtCore.QRectF(43, 13, max(1, self.width() - 53), max(1, self.height() - 39))
        text = self.palette().color(QtGui.QPalette.ColorRole.PlaceholderText)
        grid = QtGui.QColor(text)
        grid.setAlpha(45)
        values = [value for _, row in self.points for value in row if value is not None]
        ceiling = self.fixed_max or max(1.0, max(values, default=0) * 1.15)
        for fraction in (0, 0.5, 1):
            y = bounds.bottom() - fraction * bounds.height()
            painter.setPen(grid)
            painter.drawLine(QtCore.QPointF(bounds.left(), y), QtCore.QPointF(bounds.right(), y))
            painter.setPen(text)
            painter.drawText(QtCore.QRectF(0, y - 9, 38, 18), QtCore.Qt.AlignmentFlag.AlignRight,
                             f"{ceiling * fraction:.0f}" if ceiling >= 10 else f"{ceiling * fraction:.1f}")
        if not self.points:
            painter.setPen(text)
            painter.drawText(bounds, QtCore.Qt.AlignmentFlag.AlignCenter, "Ожидание измерений")
            return
        start, end = self.points[0][0], self.points[-1][0]
        span = max(1, end - start)
        painter.setPen(text)
        painter.drawText(QtCore.QRectF(bounds.left(), bounds.bottom() + 5, bounds.width(), 20),
                         QtCore.Qt.AlignmentFlag.AlignLeft, f"−{end - start:.0f} с")
        painter.drawText(QtCore.QRectF(bounds.left(), bounds.bottom() + 5, bounds.width(), 20),
                         QtCore.Qt.AlignmentFlag.AlignRight, "сейчас")
        painter.save()
        painter.setClipRect(bounds.adjusted(-2, -2, 2, 2))
        for index, color in enumerate(self.colors):
            painter.setPen(QtGui.QPen(QtGui.QColor(color), 2))
            path = QtGui.QPainterPath()
            connected = False
            for elapsed, row in self.points:
                value = row[index]
                if value is None:
                    connected = False
                    continue
                point = QtCore.QPointF(bounds.left() + (elapsed - start) / span * bounds.width(),
                                      bounds.bottom() - min(ceiling, max(0, value)) / ceiling * bounds.height())
                if connected:
                    path.lineTo(point)
                else:
                    path.moveTo(point)
                connected = True
                painter.drawEllipse(point, 1.5, 1.5)
            painter.drawPath(path)
        painter.restore()


class ResourcePanel(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.elapsed = 0.0
        self._failed = False
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        title = QtWidgets.QLabel("Мониторинг ресурсов")
        title.setObjectName("brand")
        layout.addWidget(title)
        caption = QtWidgets.QLabel("Этот компьютер · Работает без подключения к БД · Последние 120 измерений")
        caption.setObjectName("muted")
        caption.setWordWrap(True)
        layout.addWidget(caption)

        self.options_widget = QtWidgets.QWidget()
        options_layout = QtWidgets.QHBoxLayout(self.options_widget)
        options_layout.setContentsMargins(0, 0, 0, 0)
        self.options = {}
        for key, name in (("cpu", "CPU"), ("ram", "Память"), ("disk", "Диски")):
            checkbox = QtWidgets.QCheckBox(name)
            checkbox.setChecked(True)
            checkbox.toggled.connect(self.update_options)
            self.options[key] = checkbox
            options_layout.addWidget(checkbox)
        options_layout.addSpacing(16)
        options_layout.addWidget(QtWidgets.QLabel("Интервал:"))
        self.interval = QtWidgets.QComboBox()
        for value in (0.5, 1, 2, 5):
            self.interval.addItem(f"{value} с", value)
        self.interval.setCurrentIndex(1)
        options_layout.addWidget(self.interval)
        options_layout.addStretch()
        layout.addWidget(self.options_widget)
        actions = QtWidgets.QHBoxLayout()
        self.start_button = QtWidgets.QPushButton("Начать мониторинг")
        self.start_button.setObjectName("collectButton")
        self.stop_button = QtWidgets.QPushButton("Остановить")
        self.stop_button.setEnabled(False)
        self.clear_button = QtWidgets.QPushButton("Очистить")
        self.clear_button.clicked.connect(self.clear)
        for button in (self.start_button, self.stop_button, self.clear_button):
            actions.addWidget(button)
        actions.addStretch()
        layout.addLayout(actions)
        self.status = QtWidgets.QLabel("Готов к запуску")
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self.charts_widget = QtWidgets.QWidget()
        charts = QtWidgets.QHBoxLayout(self.charts_widget)
        charts.setContentsMargins(0, 0, 0, 0)
        charts.setSpacing(12)
        self.cards, self.values, self.details, self.charts = {}, {}, {}, {}
        for key, title, colors, unit, ceiling in (
            ("cpu", "CPU", ["#4b8cff"], "%", 100),
            ("ram", "Память", ["#22c55e"], "%", 100),
            ("disk", "Дисковый ввод-вывод", ["#4b8cff", "#e69b27"], "MiB/с", None),
        ):
            card = QtWidgets.QFrame()
            card.setObjectName("card")
            body = QtWidgets.QVBoxLayout(card)
            body.setContentsMargins(14, 14, 14, 14)
            label = QtWidgets.QLabel(title)
            label.setObjectName("section")
            body.addWidget(label)
            value = QtWidgets.QLabel("—")
            value.setObjectName("resourceValue")
            body.addWidget(value)
            detail = QtWidgets.QLabel("Ожидание данных")
            detail.setObjectName("muted")
            detail.setWordWrap(True)
            body.addWidget(detail)
            chart = LiveChart(colors, unit, ceiling)
            body.addWidget(chart)
            if key == "disk":
                legend = QtWidgets.QLabel('<span style="color:#4b8cff">● Чтение</span> &nbsp; '
                                         '<span style="color:#e69b27">● Запись</span> · MiB/с')
                body.addWidget(legend)
            self.cards[key], self.values[key], self.details[key], self.charts[key] = card, value, detail, chart
            charts.addWidget(card, 1)
        layout.addWidget(self.charts_widget)
        self.charts_widget.hide()
        console_title = QtWidgets.QLabel("Консоль мониторинга")
        console_title.setObjectName("section")
        layout.addWidget(console_title)
        self.console = QtWidgets.QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setAccessibleName("Консоль мониторинга ресурсов")
        self.console.setMinimumHeight(150)
        self.console.document().setMaximumBlockCount(500)
        self.console.setPlaceholderText("Измерения появятся после запуска. Журнал хранится только в памяти.")
        layout.addWidget(self.console, 1)

    def selected_options(self):
        return {key: checkbox.isChecked() for key, checkbox in self.options.items()}

    def update_options(self):
        if not hasattr(self, "start_button"):
            return
        self.start_button.setEnabled(any(self.selected_options().values()))
        for key, enabled in self.selected_options().items():
            self.cards[key].setVisible(enabled)

    def clear(self):
        self.elapsed = 0
        self.charts_widget.hide()
        self.console.clear()
        for chart in self.charts.values():
            chart.clear()
        for value in self.values.values():
            value.setText("—")
        for detail in self.details.values():
            detail.setText("Ожидание данных")

    def set_busy(self, busy):
        self.options_widget.setEnabled(not busy)
        self.start_button.setEnabled(not busy and any(self.selected_options().values()))
        self.stop_button.setEnabled(busy)
        if busy:
            self._failed = False
            self.clear()
            self.status.setText("● Мониторинг работает · Первое измерение появится через выбранный интервал")
        elif not self._failed:
            self.status.setText("Мониторинг остановлен · Последние данные сохранены на экране")

    def show_stopping(self):
        self.stop_button.setEnabled(False)
        self.status.setText("Останавливаю мониторинг…")

    def show_error(self, message):
        self._failed = True
        self.status.setText(message)
        self.console.appendPlainText(message)

    def show_sample(self, report):
        self.charts_widget.show()
        sample = report["samples"][-1]
        self.elapsed += sample["interval_seconds"]
        timestamp = datetime.fromisoformat(sample["timestamp"]).astimezone().strftime("%H:%M:%S")
        parts = [timestamp]
        options = report["options"]
        if options.get("cpu"):
            cores = sample["cpu_percent_per_core"]
            value = sum(cores) / len(cores) if cores else None
            self.values["cpu"].setText(f"{value:.1f}%" if value is not None else "—")
            self.details["cpu"].setText(f"Средняя загрузка · {report['logical_cores']} логических ядер")
            self.charts["cpu"].append(self.elapsed, (value,))
            parts.append(f"CPU {self.values['cpu'].text()}")
        if options.get("ram"):
            value = sample["ram_used_percent"]
            self.values["ram"].setText(f"{value:.1f}%" if value is not None else "—")
            self.details["ram"].setText(
                f"Доступно {sample['ram_available_bytes'] / 2**30:.1f} из {report['ram_total_bytes'] / 2**30:.1f} GiB"
                f" · Swap {sample['swap_used_bytes'] / 2**30:.1f} GiB")
            self.charts["ram"].append(self.elapsed, (value,))
            parts.append(f"RAM {self.values['ram'].text()}")
        if options.get("disk"):
            rates = sample["disk_io_rates"]
            totals = []
            for field in ("read_bytes_per_second", "write_bytes_per_second"):
                values = [row.get(field) for row in (rates or {}).values()]
                totals.append(sum(values) / 2**20 if values and all(v is not None for v in values) else None)
            read, write = (f"{v:.2f}" if v is not None else "—" for v in totals)
            self.values["disk"].setText(f"↓ {read}   ↑ {write}")
            self.details["disk"].setText("Сумма счётчиков устройств · MiB/с" if all(v is not None for v in totals)
                                         else "Счётчики недоступны или изменились")
            self.charts["disk"].append(self.elapsed, totals)
            parts.append(f"Диск: чтение {read}, запись {write} MiB/с")
        self.console.appendPlainText("  |  ".join(parts))
        self.status.setText(f"● Мониторинг работает · Обновлено в {timestamp}")
