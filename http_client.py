"""Одна спільна HTTP-сесія на весь час життя бота.

Раніше кожен виклик створював нову aiohttp.ClientSession, тобто щоразу нові
TCP- і TLS-рукостискання. Вимірювання (6 запитів на хост):

    Нова Пошта   нова сесія 838 мс → спільна 182 мс   (-656 мс на виклик)
    Apps Script  нова сесія 115 мс → спільна  53 мс   (-62 мс на виклик)

За одне замовлення бот робить два виклики до НП і три до таблиці, тож на
рукостисканнях згорало близько півтори секунди.

Сесію створюємо ліниво, всередині робочого циклу (ClientSession прив'язується
до нього), і закриваємо при зупинці бота — close() у main.py. Якщо цикл
змінився (тести, перезапуск), стару сесію лишаємо позаду й робимо нову.
"""
import asyncio
import logging

import aiohttp

log = logging.getLogger(__name__)

# скільки з'єднань тримати відкритими; бот не робить сотень паралельних запитів
_LIMIT = 20
# скільки секунд тримати вільне з'єднання — Google і НП рвуть довгі простої самі
_KEEPALIVE = 60

_session: aiohttp.ClientSession | None = None
_loop: asyncio.AbstractEventLoop | None = None


def session() -> aiohttp.ClientSession:
    """Спільна сесія. Створюється при першому виклику в робочому циклі."""
    global _session, _loop
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:                     # поза циклом сесію робити не можна
        running = None
    if _session is None or _session.closed or (running is not None
                                               and _loop is not running):
        connector = aiohttp.TCPConnector(limit=_LIMIT,
                                         keepalive_timeout=_KEEPALIVE,
                                         enable_cleanup_closed=True)
        _session = aiohttp.ClientSession(connector=connector)
        _loop = running
    return _session


async def close():
    """Закрити сесію при зупинці бота (інакше aiohttp свариться в лог)."""
    global _session, _loop
    if _session is not None and not _session.closed:
        try:
            await _session.close()
        except Exception as e:  # noqa: BLE001
            log.warning("Сесію не вдалося закрити: %s", e)
    _session = None
    _loop = None
