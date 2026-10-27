"""Проверка тайм-аутов и отмены в отдельной базе querylens_test*."""

import os
import threading
import time
import unittest

from app.collectors.explain import collect_explain
from app.services.analysis_execution import AnalysisCancellation


@unittest.skipUnless(
    os.environ.get("QUERYLENS_TEST_DATABASE"),
    "No dedicated native PostgreSQL test database configured",
)
class NativeMeasuredTests(unittest.TestCase):
    def connect(self):
        import psycopg
        from psycopg.rows import dict_row

        database = os.environ["QUERYLENS_TEST_DATABASE"]
        if not database.startswith("querylens_test") or "=" in database or "://" in database:
            self.fail("Use a dedicated querylens_test* database")
        return psycopg.connect(
            dbname=database,
            host="127.0.0.1",
            connect_timeout=3,
            row_factory=dict_row,
            options="-c default_transaction_read_only=on -c statement_timeout=5000",
        )

    def test_native_readonly_and_metrics(self):
        with self.connect() as c:
            result = collect_explain(c, "SELECT 1 AS value", analyze=True)
            self.assertIsNotNone(result["plan"][0].get("Execution Time"))
            self.assertEqual(
                c.execute("SHOW transaction_read_only").fetchone()["transaction_read_only"], "on"
            )

    def test_native_statement_timeout(self):
        from psycopg.errors import QueryCanceled

        with self.assertRaises(QueryCanceled):
            with self.connect() as c:
                collect_explain(c, "SELECT pg_sleep(1)", analyze=True, timeout_ms=100)

    def test_native_active_query_cancellation(self):
        from psycopg.errors import QueryCanceled

        token = AnalysisCancellation()
        started = threading.Event()
        pids = []
        errors = []

        def run():
            try:
                with self.connect() as c:
                    token.bind(c)
                    pids.append(c.info.backend_pid)
                    started.set()
                    collect_explain(c, "SELECT pg_sleep(5)", analyze=True, timeout_ms=5000)
            except Exception as error:
                errors.append(error)
            finally:
                token.unbind()

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        try:
            self.assertTrue(started.wait(2))
            observed = False
            with self.connect() as observer:
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    row = observer.execute(
                        "SELECT wait_event FROM pg_catalog.pg_stat_activity WHERE pid=%s",
                        (pids[0],),
                    ).fetchone()
                    if row and row["wait_event"] == "PgSleep":
                        observed = True
                        break
                    observer.commit()
                    time.sleep(0.02)
            self.assertTrue(observed, "Did not observe the query in pg_sleep")
            token.cancel()
            thread.join(2)
            self.assertFalse(thread.is_alive())
            self.assertTrue(any(isinstance(e, QueryCanceled) for e in errors))
        finally:
            token.cancel()
            thread.join(6)
