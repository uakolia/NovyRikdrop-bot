"""Вимірювання часу зовнішніх викликів (таблиця, Нова Пошта).

Бот майже весь час чекає на мережу, тож перш ніж щось прискорювати, треба
бачити, ЩО саме довге. Кожен виклик пишеться в лог окремим рядком, а на час
оформлення замовлення вмикається «трасування»: у кінці — розклад по викликах
і загальний час.

    with perf.trace("замовлення №17"):
        ...
        async with perf.timed("таблиця reserve"):
            ...

Вимкнути: PERF_LOG=0 у змінних оточення.
"""
import contextlib
import contextvars
import logging
import os
import time

log = logging.getLogger("perf")

ENABLED = os.getenv("PERF_LOG", "1") != "0"

# поточне трасування: список (назва, мс) для одного замовлення
_trace: contextvars.ContextVar = contextvars.ContextVar("perf_trace", default=None)


def record(label: str, ms: float):
    """Записати вимір: у лог і, якщо є, у поточне трасування."""
    if not ENABLED:
        return
    log.info("⏱ %s: %.0f мс", label, ms)
    bucket = _trace.get()
    if bucket is not None:
        bucket.append((label, ms))


@contextlib.asynccontextmanager
async def timed(label: str):
    """Заміряти асинхронний виклик (час рахується і за помилки теж)."""
    start = time.perf_counter()
    try:
        yield
    finally:
        record(label, (time.perf_counter() - start) * 1000)


@contextlib.contextmanager
def trace(name: str):
    """Зібрати всі виміри всередині й вивести розклад у кінці."""
    if not ENABLED:
        yield
        return
    bucket: list = []
    token = _trace.set(bucket)
    start = time.perf_counter()
    try:
        yield bucket
    finally:
        _trace.reset(token)
        total = (time.perf_counter() - start) * 1000
        log.info("── %s: розклад ──", name)
        spent = 0.0
        for label, ms in bucket:
            spent += ms
            log.info("   %-36s %7.0f мс", label, ms)
        log.info("   %-36s %7.0f мс (з них у мережі %.0f мс, %.0f%%)",
                 "ВСЬОГО", total, spent, (spent / total * 100) if total else 0)
