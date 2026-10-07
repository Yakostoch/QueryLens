"""Измерения ОС QueryLens; это не ресурсы одного SQL и не сведения удалённого сервера."""
import platform
import time
from datetime import datetime, timezone


def io_rates(before, after, seconds):
    if before is None or after is None:
        return None
    result = {}
    for disk in sorted(set(before) | set(after)):
        if disk not in before or disk not in after:
            result[disk] = {"status": "device_changed"}
            continue
        result[disk] = {}
        for field in ("read_bytes", "write_bytes", "read_count", "write_count"):
            delta = getattr(after[disk], field) - getattr(before[disk], field)
            result[disk][field + "_per_second"] = delta / seconds if delta >= 0 else None
    return result


def collect_system(samples=5, interval=1, cancelled=lambda: False):
    """Пять снимков с интервалом; отмена проверяется также во время ожидания."""
    import psutil

    if samples < 1 or interval <= 0:
        raise ValueError("Число измерений и интервал должны быть положительными.")

    def disks():
        try:
            return psutil.disk_io_counters(perdisk=True) or None
        except (OSError, NotImplementedError):
            return None

    result = {
        "os": platform.system(), "os_release": platform.release(),
        "architecture": platform.machine(),
        "physical_cores": psutil.cpu_count(logical=False),
        "logical_cores": psutil.cpu_count(logical=True),
        "ram_total_bytes": psutil.virtual_memory().total, "samples": [],
    }
    psutil.cpu_percent(interval=None, percpu=True)
    before, started = disks(), time.monotonic()
    for _ in range(samples):
        deadline = time.monotonic() + interval
        while time.monotonic() < deadline:
            if cancelled():
                return None
            time.sleep(min(0.05, max(0, deadline - time.monotonic())))
        if cancelled():
            return None
        cpu = psutil.cpu_percent(interval=None, percpu=True)
        after, now = disks(), time.monotonic()
        memory, swap = psutil.virtual_memory(), psutil.swap_memory()
        result["samples"].append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "interval_seconds": now - started,
            "cpu_percent_per_core": cpu, "ram_available_bytes": memory.available,
            "ram_used_percent": memory.percent, "swap_used_bytes": swap.used,
            "disk_io_rates": io_rates(before, after, now - started),
        })
        before, started = after, now
    return result
