"""Открытие сохранённых примеров для ручной проверки."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6 import QtWidgets

from app.services.dataset_service import DatasetError
from app.ui.dataset_review_window import DatasetReviewWindow
from app.ui.theme import apply_theme


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app.setStyle("Fusion")
    apply_theme(app, "dark")
    try:
        window = DatasetReviewWindow(args.input, args.output)
    except (DatasetError, OSError, ValueError, TypeError) as error:
        print("Окно разметки не открыто: " + str(error), file=sys.stderr)
        return 1
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
