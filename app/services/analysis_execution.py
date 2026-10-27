"""Отмена диагностического соединения из другого потока."""

from threading import Event, Lock, Thread


class AnalysisCancelled(RuntimeError):
    pass


class AnalysisCancellation:
    def __init__(self):
        self._event = Event()
        self._lock = Lock()
        self._connection = None

    def check(self):
        if self._event.is_set():
            raise AnalysisCancelled()

    def bind(self, connection):
        with self._lock:
            self._connection = connection
        self.check()

    def unbind(self):
        with self._lock:
            self._connection = None

    def cancel(self):
        self._event.set()
        with self._lock:
            connection = self._connection
        if connection is not None:
            # Отмена через libpq может ждать сеть, поэтому выполняем её вне интерфейса.
            Thread(target=self._send, args=(connection,), daemon=True).start()

    @staticmethod
    def _send(connection):
        try:
            connection.cancel_safe(timeout=1)
        except Exception:
            # Тайм-аут сервера ограничивает время запроса независимо от отмены.
            pass
