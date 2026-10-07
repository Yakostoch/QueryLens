import time
from unittest.mock import MagicMock

from PySide6 import QtWidgets, QtTest


def connect_fake_database(controller):
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.cursor.return_value.__enter__.return_value.fetchone.return_value = {
        "QUERY PLAN": [{"Plan": {"Node Type": "Result"}}]
    }
    controller.connection.service.connect = MagicMock(return_value=connection)
    controller.connect_database(dict(host="localhost", port=5432, database="demo",
                                     user="reader", password="secret", sslmode="prefer"))
    return connection


def wait_analysis(controller):
    for _ in range(300):
        QtWidgets.QApplication.processEvents()
        if controller.analysis_worker is None:
            return
        QtTest.QTest.qWait(10)
        time.sleep(0.005)
    raise AssertionError("Analysis worker did not finish")
