"""Окно ручной проверки сохранённых диагностических примеров."""

import hashlib
import json
from copy import deepcopy
from pathlib import Path

from PySide6 import QtCore, QtWidgets

from app.analyzers.diagnostic_router import REGISTRY
from app.services.dataset_service import (
    DatasetError,
    read_records,
    review_sheet,
    validate_record,
    validate_review,
    write_records,
)
from app.services.evaluation_service import evaluate_records, format_evaluation


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


class DatasetReviewWindow(QtWidgets.QMainWindow):
    def __init__(self, source, output):
        super().__init__()
        self.source, self.output = Path(source).resolve(), Path(output).resolve()
        if self.source == self.output:
            raise DatasetError("Сохраняйте разметку отдельно от исходной очереди.")
        self.records = read_records(self.source)
        if not self.records:
            raise DatasetError("Очередь пуста.")
        by_id = {}
        for record in self.records:
            validate_record(record)
            validate_review(record)
            if record["case_id"] in by_id:
                raise DatasetError("В очереди повторяется case_id.")
            by_id[record["case_id"]] = record
        if self.output.exists():
            saved = read_records(self.output)
            if len(saved) != len(self.records) or {r["case_id"] for r in saved} != set(by_id):
                raise DatasetError("Существующий файл разметки относится к другой очереди.")
            for record in saved:
                validate_record(record)
                validate_review(record)
                by_id[record["case_id"]]["review"] = deepcopy(record["review"])
        self.output_digest = _digest(self.output)
        self.index, self.dirty, self.loading = -1, False, True
        self.setWindowTitle("QueryLens — разметка контрольных примеров")
        self.resize(1260, 900)
        self.setMinimumSize(900, 650)
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        layout = QtWidgets.QVBoxLayout(central)
        layout.setContentsMargins(16, 16, 16, 16)
        self.progress = QtWidgets.QLabel()
        layout.addWidget(self.progress)
        splitter = QtWidgets.QSplitter()
        self.cases = QtWidgets.QListWidget()
        self.cases.setAccessibleName("Примеры для разметки")
        splitter.addWidget(self.cases)
        right = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right)
        context = QtWidgets.QTabWidget()
        self.sql = QtWidgets.QPlainTextEdit()
        self.draft = QtWidgets.QPlainTextEdit()
        self.evidence = QtWidgets.QPlainTextEdit()
        for editor, title in (
            (self.sql, "SQL"),
            (self.draft, "Черновой вывод"),
            (self.evidence, "Исходные данные"),
        ):
            editor.setReadOnly(True)
            editor.setAccessibleName(title)
            context.addTab(editor, title)
        context.setMinimumHeight(145)
        context.setMaximumHeight(260)
        right_layout.addWidget(context, 1)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        form_widget = QtWidgets.QWidget()
        form_layout = QtWidgets.QVBoxLayout(form_widget)
        explanation = QtWidgets.QLabel(
            "Черновик — подсказка. Подтверждайте только проверенный ответ. "
            "Отметьте правила, которые вы проверили, отдельно от найденных замечаний."
        )
        explanation.setWordWrap(True)
        form_layout.addWidget(explanation)
        fields = QtWidgets.QFormLayout()
        self.reviewer = QtWidgets.QLineEdit()
        self.family = QtWidgets.QLineEdit()
        self.family.setPlaceholderText("Общая семья для запроса и его исправленных вариантов")
        self.status_combo = QtWidgets.QComboBox()
        for label, code in (
            ("Ожидает проверки", "pending"),
            ("Проверен и одобрен", "approved"),
            ("Исключён", "rejected"),
        ):
            self.status_combo.addItem(label, code)
        self.verdict = QtWidgets.QComboBox()
        for label, code in (
            ("Выберите вывод", ""),
            ("Подтверждено замечание", "problem"),
            ("Есть риск, нужна проверка", "potential"),
            ("Замечаний не найдено", "no_issue_found"),
            ("Недостаточно данных", "insufficient_data"),
        ):
            self.verdict.addItem(label, code)
        for label, editor in (
            ("Проверяющий", self.reviewer),
            ("Семья примеров", self.family),
            ("Статус", self.status_combo),
            ("Вывод", self.verdict),
        ):
            fields.addRow(label, editor)
        form_layout.addLayout(fields)
        self.rule_table = QtWidgets.QTableWidget(15, 3)
        self.rule_table.setHorizontalHeaderLabels(["Правило", "Проверено", "Замечание"])
        self.rule_table.verticalHeader().setVisible(False)
        self.rule_table.horizontalHeader().setSectionResizeMode(
            0, QtWidgets.QHeaderView.ResizeMode.Stretch
        )
        self.rule_table.horizontalHeader().setSectionResizeMode(
            1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents
        )
        self.rule_table.horizontalHeader().setSectionResizeMode(
            2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents
        )
        self.rule_table.setMinimumHeight(220)
        self.rule_table.setMaximumHeight(300)
        for row, (number, _, _, label, _) in enumerate(REGISTRY):
            title = QtWidgets.QTableWidgetItem(f"AP{number:02} · {label}")
            title.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled | QtCore.Qt.ItemFlag.ItemIsSelectable)
            self.rule_table.setItem(row, 0, title)
            for column in (1, 2):
                item = QtWidgets.QTableWidgetItem()
                item.setFlags(
                    QtCore.Qt.ItemFlag.ItemIsEnabled | QtCore.Qt.ItemFlag.ItemIsUserCheckable
                )
                item.setCheckState(QtCore.Qt.CheckState.Unchecked)
                self.rule_table.setItem(row, column, item)
        form_layout.addWidget(self.rule_table)
        self.refs = QtWidgets.QPlainTextEdit()
        self.refs.setPlaceholderText("Одна ссылка на доказательство в строке, например /input/sql")
        self.refs.setMinimumHeight(70)
        self.refs.setMaximumHeight(100)
        self.suggestions = QtWidgets.QLabel()
        self.suggestions.setWordWrap(True)
        self.suggestions.setTextInteractionFlags(
            QtCore.Qt.TextInteractionFlag.TextSelectableByMouse
        )
        form_layout.addWidget(QtWidgets.QLabel("Доказательства (JSON Pointer в запись)"))
        form_layout.addWidget(self.refs)
        form_layout.addWidget(self.suggestions)
        self.answer = QtWidgets.QPlainTextEdit()
        self.answer.setPlaceholderText(
            "Ваш проверенный короткий ответ: где замечание, почему и что сделать."
        )
        self.answer.setMinimumHeight(110)
        self.answer_label = QtWidgets.QLabel("Проверенный ответ пользователю")
        form_layout.addWidget(self.answer_label)
        form_layout.addWidget(self.answer)
        self.notes = QtWidgets.QPlainTextEdit()
        self.notes.setPlaceholderText("Заметки проверяющего; не входят в обучающий запрос.")
        self.notes.setMaximumHeight(70)
        form_layout.addWidget(self.notes)
        scroll.setWidget(form_widget)
        right_layout.addWidget(scroll, 2)
        buttons = QtWidgets.QHBoxLayout()
        self.save_button = QtWidgets.QPushButton("Сохранить разметку")
        self.save_button.clicked.connect(self.save_current)
        self.next_button = QtWidgets.QPushButton("Следующий")
        self.next_button.clicked.connect(
            lambda: self.cases.setCurrentRow(min(self.index + 1, len(self.records) - 1))
        )
        self.evaluate_button = QtWidgets.QPushButton("Оценка качества")
        self.evaluate_button.clicked.connect(self.show_evaluation)
        for button in (self.save_button, self.next_button, self.evaluate_button):
            buttons.addWidget(button)
        right_layout.addLayout(buttons)
        splitter.addWidget(right)
        splitter.setSizes([270, 950])
        splitter.setChildrenCollapsible(False)
        layout.addWidget(splitter, 1)
        self.message = QtWidgets.QLabel("Файл результата: " + str(self.output))
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        for widget in (self.reviewer, self.family):
            widget.textChanged.connect(self.mark_dirty)
        for widget in (self.status_combo, self.verdict):
            widget.currentIndexChanged.connect(self.mark_dirty)
        for widget in (self.refs, self.answer, self.notes):
            widget.textChanged.connect(self.mark_dirty)
        self.rule_table.itemChanged.connect(self.rule_changed)
        self.refresh_cases()
        self.cases.currentRowChanged.connect(self.select_case)
        self.loading = False
        self.cases.setCurrentRow(0)

    def refresh_cases(self):
        with QtCore.QSignalBlocker(self.cases):
            self.cases.clear()
            for i, record in enumerate(self.records, 1):
                status = record["review"]["status"]
                label = {"pending": "Ожидает", "approved": "Одобрен", "rejected": "Исключён"}[
                    status
                ]
                sql = " ".join(record["input"]["sql"].split())
                self.cases.addItem(f"{i}. {label}\n{sql[:85]}")
            self.cases.setCurrentRow(self.index)
        approved = sum(r["review"]["status"] == "approved" for r in self.records)
        self.progress.setText(
            f"Примеров: {len(self.records)} · Одобрено: {approved} · "
            "Сохранение разметки не запускает SQL и обучение"
        )

    def mark_dirty(self, *_):
        if not self.loading:
            self.dirty = True
            self.message.setText("Есть несохранённая разметка.")

    def rule_changed(self, item):
        if self.loading:
            return
        with QtCore.QSignalBlocker(self.rule_table):
            if item.column() == 2 and item.checkState() == QtCore.Qt.CheckState.Checked:
                self.rule_table.item(item.row(), 1).setCheckState(QtCore.Qt.CheckState.Checked)
            elif item.column() == 1 and item.checkState() == QtCore.Qt.CheckState.Unchecked:
                self.rule_table.item(item.row(), 2).setCheckState(QtCore.Qt.CheckState.Unchecked)
        self.mark_dirty()

    def confirm_navigation(self):
        if not self.dirty:
            return True
        answer = QtWidgets.QMessageBox.question(
            self,
            "Несохранённая разметка",
            "Сохранить изменения текущего примера?",
            QtWidgets.QMessageBox.StandardButton.Save
            | QtWidgets.QMessageBox.StandardButton.Discard
            | QtWidgets.QMessageBox.StandardButton.Cancel,
            QtWidgets.QMessageBox.StandardButton.Save,
        )
        if answer == QtWidgets.QMessageBox.StandardButton.Save:
            return self.save_current()
        return answer == QtWidgets.QMessageBox.StandardButton.Discard

    def select_case(self, row):
        if row < 0 or row == self.index:
            return
        if not self.confirm_navigation():
            with QtCore.QSignalBlocker(self.cases):
                self.cases.setCurrentRow(self.index)
            return
        self.index, self.loading = row, True
        record = self.records[row]
        review = record["review"]
        self.sql.setPlainText(record["input"]["sql"])
        self.draft.setPlainText(record["draft_answer"])
        self.evidence.setPlainText(
            json.dumps(
                dict(input=record["input"], automatic_analysis=record["automatic_analysis"]),
                ensure_ascii=False,
                indent=2,
            )
        )
        self.reviewer.setText(review.get("reviewed_by", ""))
        self.family.setText(review.get("family_id", ""))
        self.status_combo.setCurrentIndex(self.status_combo.findData(review["status"]))
        self.verdict.setCurrentIndex(max(0, self.verdict.findData(review.get("verdict", ""))))
        for i in range(15):
            rule = f"AP{i+1:02}"
            for column, field in ((1, "assessed_rules"), (2, "labels")):
                self.rule_table.item(i, column).setCheckState(
                    QtCore.Qt.CheckState.Checked
                    if rule in review.get(field, [])
                    else QtCore.Qt.CheckState.Unchecked
                )
        self.refs.setPlainText("\n".join(review.get("evidence_refs", [])))
        self.answer.setPlainText(review.get("answer", ""))
        self.notes.setPlainText(review.get("notes", ""))
        self.answer_label.setText(
            "Предложение ИИ — проверьте перед одобрением"
            if review.get("notes", "").startswith("Предложение ИИ")
            and review["status"] == "pending"
            else "Проверенный ответ пользователю"
        )
        hints = review_sheet([record])["items"][0]["evidence_suggestions"]
        self.suggestions.setText(
            "Возможные ссылки (проверьте): " + (", ".join(hints) or "нет готовых подсказок")
        )
        self.loading, self.dirty = False, False
        self.message.setText(
            f'Пример {row+1} · {record["case_id"][:12]} · разметка отдельно от исходных данных'
        )

    def current_review(self):
        checked = lambda col: [
            f"AP{i+1:02}"
            for i in range(15)
            if self.rule_table.item(i, col).checkState() == QtCore.Qt.CheckState.Checked
        ]
        return dict(
            status=self.status_combo.currentData(),
            reviewed_by=self.reviewer.text().strip(),
            family_id=self.family.text().strip(),
            verdict=self.verdict.currentData(),
            assessed_rules=checked(1),
            labels=checked(2),
            evidence_refs=[
                line.strip() for line in self.refs.toPlainText().splitlines() if line.strip()
            ],
            answer=self.answer.toPlainText().strip(),
            notes=self.notes.toPlainText(),
        )

    def save_current(self):
        if self.index < 0:
            return False
        proposed = deepcopy(self.records)
        proposed[self.index]["review"] = self.current_review()
        try:
            if (
                proposed[self.index]["review"]["status"] == "approved"
                and not proposed[self.index]["review"]["assessed_rules"]
            ):
                raise DatasetError(
                    "Отметьте в столбце «Проверено» хотя бы одно проверенное правило."
                )
            validate_review(proposed[self.index])
            if _digest(self.output) != self.output_digest:
                raise DatasetError(
                    "Файл результата изменён снаружи. Сохранение остановлено; откройте окно заново."
                )
            write_records(self.output, proposed, overwrite=self.output_digest is not None)
        except (OSError, ValueError, TypeError) as error:
            self.message.setText("Не сохранено: " + str(error))
            return False
        self.records = proposed
        if proposed[self.index]["review"]["status"] == "approved":
            self.answer_label.setText("Проверенный ответ пользователю")
        self.output_digest = _digest(self.output)
        self.dirty = False
        self.refresh_cases()
        self.message.setText("Разметка сохранена: " + str(self.output))
        return True

    def show_evaluation(self):
        if not self.confirm_navigation():
            return
        try:
            result = evaluate_records(self.records)
        except (DatasetError, ValueError, TypeError) as error:
            self.message.setText("Оценка не выполнена: " + str(error))
            return
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Качество по сохранённой разметке")
        dialog.resize(680, 330)
        layout = QtWidgets.QVBoxLayout(dialog)
        text = QtWidgets.QPlainTextEdit(format_evaluation(result))
        text.setReadOnly(True)
        layout.addWidget(text)
        close = QtWidgets.QPushButton("Закрыть")
        close.clicked.connect(dialog.accept)
        layout.addWidget(close)
        dialog.exec()

    def closeEvent(self, event):
        if self.confirm_navigation():
            event.accept()
        else:
            event.ignore()
