"""Непрерывный поток измерений ОС; не зависит от Qt и PostgreSQL."""
from app.collectors.system import collect_system


def monitor_resources(options, interval, cancelled):
    """Новый интервал измеряется в том же потоке, без накопления полного отчёта."""
    if not any(options.values()) or not 0.25 <= interval <= 60:
        raise ValueError("Выберите показатели и допустимый интервал.")
    while not cancelled():
        report = collect_system(samples=1, interval=interval, cancelled=cancelled, options=options)
        if report is None or cancelled():
            return
        yield report
