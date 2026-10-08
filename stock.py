"""Залишки дропшиперів: скільки з передзамовлення ще вільно.

Джерело правди — аркуш «Залишки дропшиперів» у таблиці замовлень:

    A Telegram ID | B Дропшипер | C Артикул | D Персональна назва | E Дроп-ціна
    F Виділено    | G Зарезервовано | H Отримано | I Доступно | J Оновлено

Колонку I («Доступно») веде формула =F-G-H — ні бот, ні скрипт її не пишуть.
Колонка E довідкова: ціну бот завжди бере з прайсу за тарифом дропшипера.

Артикули зіставляємо канонічним ключем (article_key) — у прайсі трапляються
кириличні двійники латинських літер. Зберігаємо й показуємо оригінальний рядок.

Товар БЕЗ рядка в цьому аркуші замовляється як завжди, просто без залишку:
available() віддає None, і бот нічого не резервує.
"""
import asyncio
import logging
import time

import article_key
import config

log = logging.getLogger(__name__)

# {user_id_str: {канонічний_артикул: рядок}}
_cache: dict[str, dict[str, dict]] = {}

# кого треба перечитати при наступному асинхронному зверненні
_stale: set[str] = set()


def _key(user_id) -> str:
    return str(user_id)


def invalidate(user_id=None):
    """Позначити, що числа застаріли — але НЕ викидати рядки.

    У кеші лежать не лише кількості, а й «Персональна назва». Якщо стерти
    рядок цілком, синхронні місця (підписи кнопок, підсумкове повідомлення)
    втрачають назву дропшипера й показують заводську, доки хтось не перечитає
    таблицю. Тому дані лишаємо, а позначку «застаріло» знімає найближчий
    rows_for(), який сходить у таблицю.
    """
    if user_id is None:
        _stale.update(_cache.keys())
    else:
        _stale.add(_key(user_id))


def forget(user_id=None):
    """Викинути рядки зовсім (потрібно хіба що в тестах)."""
    if user_id is None:
        _cache.clear()
        _stale.clear()
    else:
        _cache.pop(_key(user_id), None)
        _stale.discard(_key(user_id))


def apply_write(user_id, data):
    """Оновити кеш відповіддю скрипта після reserve/receive/release.

    Скрипт повертає нові «Зарезервовано» й «Доступно», тож ходити за ними в
    таблицю ще раз не треба: підставляємо числа в кеш і лишаємо назви на місці.
    Якщо у відповіді чогось немає — позначаємо застарілим, хай перечитає.
    """
    uid = _key(user_id)
    rows = (_cache.get(uid) or {})
    if not rows or not isinstance(data, dict):
        invalidate(user_id)
        return
    items = data.get("items")
    if items is None and data.get("article"):
        items = [data]                      # відповідь одиночної операції
    if not items:
        invalidate(user_id)
        return
    touched = 0
    for it in items:
        row = rows.get(article_key.canon(it.get("article")))
        if row is None:
            continue
        if it.get("available") is not None:
            row["available"] = it["available"]
        if it.get("reserved") is not None:
            row["reserved"] = it["reserved"]
        if it.get("delivered") is not None:
            row["delivered"] = it["delivered"]
        touched += 1
    if touched != len(items):
        invalidate(user_id)


async def refresh():
    """Перечитати весь аркуш. Повертає (к-сть рядків, помилка)."""
    import sheets_store
    if not sheets_store.enabled():
        return 0, None
    rows, err = await sheets_store.fetch_stock()
    if err:
        return 0, err
    return apply_rows(rows), None


def _mark_read():
    global _last_read
    _last_read = time.monotonic()


def apply_rows(rows) -> int:
    """Застосувати вже прочитані рядки «Залишків дропшиперів».

    Окремо від читання: ті самі рядки приходять і одним запитом
    what=bootstrap. Рядки тут уже мають бути очищені (sheets_store обрізає
    пробіли й відкидає порожні артикули).
    """
    fresh: dict[str, dict[str, dict]] = {}
    for r in rows or []:
        uid = str(r.get("tg_id") or "").strip()
        art = str(r.get("article") or "").strip()
        if uid and art:
            fresh.setdefault(uid, {})[article_key.canon(art)] = r
    _cache.clear()
    _cache.update(fresh)
    _stale.clear()
    _mark_read()
    return sum(len(v) for v in fresh.values())


def all_rows() -> list:
    """Усі рядки залишків з кешу (для перевірки при старті)."""
    return [r for by_user in _cache.values() for r in by_user.values()]


# Читання всього аркуша «Залишки дропшиперів» — найдорожча операція на шляху
# кліку: через Apps Script це 1–3 с, а якщо таблиця не відповідає, то ще й
# таймаут із повтором. Дропшипер при цьому дивиться на застигле меню.
# Тому на кліку таблицю НЕ чекаємо: віддаємо те, що в кеші, а перечитування
# пускаємо у фон — свіжі числа будуть на наступному екрані. Чекаємо лише коли
# в кеші взагалі нічого немає (перший клік після деплою, якщо старт не встиг).
STOCK_COOLDOWN = 30          # не частіше ніж раз на стільки секунд на всіх
_last_read = 0.0
_reading: "asyncio.Task | None" = None


def _fresh_enough() -> bool:
    return (time.monotonic() - _last_read) < STOCK_COOLDOWN


async def _refresh_logged():
    global _last_read
    _last_read = time.monotonic()
    n, err = await refresh()
    if err:
        log.warning("Залишки не перечитались: %s", err)
    return n, err


def refresh_soon():
    """Перечитати залишки у фоні, якщо давно не читали. Не блокує клік."""
    global _reading
    if _fresh_enough():
        return
    if _reading is not None and not _reading.done():
        return
    try:
        _reading = asyncio.create_task(_refresh_logged())
    except RuntimeError:                     # поза робочим циклом (тести)
        _reading = None


async def rows_for(user_id) -> dict:
    """{канонічний_артикул: рядок} цього дропшипера.

    Кеш головніший за свіжість: застарілі на пів хвилини числа краще, ніж
    секунди очікування на кожному натисканні.
    """
    uid = _key(user_id)
    rows = _cache.get(uid)
    if rows:
        if uid in _stale:
            refresh_soon()                   # підтягнемо до наступного екрана
        return rows
    # у кеші нічого — тут уже доводиться чекати, інакше не буде ні кількостей,
    # ні персональних назв
    if _fresh_enough():
        return _cache.get(uid) or {}
    await _refresh_logged()
    return _cache.get(uid) or {}


async def row_for(user_id, article: str) -> dict | None:
    return (await rows_for(user_id)).get(article_key.canon(article))


def cached_row(user_id, article: str) -> dict | None:
    """Рядок із кешу без звернення до таблиці (для синхронних місць).

    Кеш гріється при старті (main) і після кожного запису; якщо порожній —
    просто немає персональної назви й залишку, замовлення це не блокує.
    """
    if user_id is None:
        return None
    return (_cache.get(_key(user_id)) or {}).get(article_key.canon(article))


def cached_name(user_id, article: str) -> str:
    return str((cached_row(user_id, article) or {}).get("name") or "").strip()


def sheet_stock(article: str):
    """«Наявність» із прайсу або None.

    Для ікебан, настінних і подарункових ялинок персональних залишків немає —
    кількість ведеться однією колонкою в прайсі. Без цього бот показував би
    такі товари без обмежень і дав би замовити більше, ніж є на складі.
    """
    import catalog
    item = catalog.by_article(article)
    if not item:
        return None
    value = item.get("stock_sheet")
    return None if value is None else _int(value)


def has_row(user_id, article: str) -> bool:
    """Чи ведеться для цього товару ПЕРСОНАЛЬНИЙ залишок (видано/резерв).

    Тільки такі позиції можна резервувати: для решти рядка в таблиці немає,
    і скрипт відповів би «no stock row».
    """
    return cached_row(user_id, article) is not None


def cached_available(user_id, article: str):
    row = cached_row(user_id, article)
    if row is None:
        return sheet_stock(article)
    return _int(row.get("available"))


def _int(value) -> int:
    try:
        return int(float(str(value).replace(",", "").replace("\xa0", "").strip()))
    except (TypeError, ValueError):
        return 0


async def available(user_id, article: str) -> int | None:
    """Скільки вільно. None — кількість для цього товару не ведеться ніде.

    Спершу персональний залишок дропшипера, далі «Наявність» із прайсу.
    """
    row = await row_for(user_id, article)
    if row is None:
        return sheet_stock(article)
    return _int(row.get("available"))


async def display_name(user_id, article: str) -> str:
    """«Персональна назва» дропшипера або "" — назва вже містить розмір."""
    row = await row_for(user_id, article)
    return str((row or {}).get("name") or "").strip()


SKIPPED = {"ok": True, "skipped": True, "items": []}


def writes_enabled() -> bool:
    """Чи пише бот резерв у таблицю. False — наявність лише для показу."""
    return bool(config.STOCK_RESERVE)


async def _op(op: str, user_id, article: str, qty: int):
    """Спільна частина reserve/receive/release. Повертає (дані, помилка)."""
    import sheets_store
    if not writes_enabled():
        log.info("Резервування вимкнено: %s %s × %s не записую",
                 op, article, qty)
        return dict(SKIPPED), None
    if not sheets_store.enabled():
        return None, "таблиця не налаштована"
    data, err = await sheets_store.stock_op(op, user_id, article, qty)
    if err:
        # навіть відмова «не вистачає» означає, що наші числа застарілі:
        # хтось інший устиг зарезервувати
        invalidate(user_id)
    else:
        apply_write(user_id, data)
    return data, err


async def _op_many(op: str, user_id, items: list[dict]):
    import sheets_store
    if not writes_enabled():
        log.info("Резервування вимкнено: %s на %d позицій не записую",
                 op, len(items))
        return dict(SKIPPED), None
    if not sheets_store.enabled():
        return None, "таблиця не налаштована"
    data, err = await sheets_store.stock_op_many(op, user_id, items)
    if err:
        invalidate(user_id)
    else:
        apply_write(user_id, data)
    return data, err


async def reserve_many(user_id, items: list[dict]):
    """Зарезервувати кілька позицій за один раз, «все або нічого».

    items = [{article, qty}]. Помилка «not enough» означає, що в таблиці
    НІЧОГО не змінилося — скрипт перевіряє всі позиції до запису, тож
    відкочувати часткові резерви не доводиться.
    """
    return await _op_many("reserve_many", user_id, items)


async def release_many(user_id, items: list[dict]):
    """Зняти резерв із кількох позицій (скасування замовлення)."""
    return await _op_many("release_many", user_id, items)


async def reserve(user_id, article: str, qty: int):
    """Зарезервувати qty. Повертає (дані, помилка); помилка «not enough» —
    залишку не вистачило (перевірку робить скрипт під замком)."""
    return await _op("reserve", user_id, article, qty)


async def receive(user_id, article: str, qty: int):
    """Клієнт забрав: із «Зарезервовано» в «Отримано»."""
    return await _op("receive", user_id, article, qty)


async def release(user_id, article: str, qty: int):
    """Повернути резерв (замовлення скасоване або ТТН видалена)."""
    return await _op("release", user_id, article, qty)


def not_enough_text(missing: list[dict]) -> str:
    """Текст про позиції, яких не вистачило (відповідь reserve_many)."""
    lines = []
    for m in missing or []:
        lines.append(f"• <code>{m.get('article')}</code>: просили "
                     f"{m.get('requested')} шт, вільно {m.get('available')} шт")
    body = "\n".join(lines) or "позиції немає в наявності"
    return ("⚠️ <b>Не вистачає залишку</b>\n\n" + body +
            "\n\nЗамовлення не оформлено, накладних не створювали. "
            "Змініть кількість у кошику або зверніться до менеджера.")


def error_text(err: str, available_now=None) -> str:
    """Людською мовою — те, що побачить дропшипер."""
    if err == "not enough":
        have = "0" if available_now is None else str(available_now)
        return (f"На складі за вашим передзамовленням лишилось {have} шт. "
                "Зменшіть кількість або зверніться до менеджера.")
    if err == "no stock row":
        return "Цієї позиції немає у вашому передзамовленні."
    if err == "sheet busy":
        return "Таблиця зараз зайнята — спробуйте ще раз за хвилину."
    return f"Не вдалося оновити залишок: {err}"
