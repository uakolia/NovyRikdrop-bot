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
import logging

import article_key

log = logging.getLogger(__name__)

# {user_id_str: {канонічний_артикул: рядок}}
_cache: dict[str, dict[str, dict]] = {}


def _key(user_id) -> str:
    return str(user_id)


def invalidate(user_id=None):
    """Скинути кеш: після кожного запису, бо залишок міг змінитися."""
    if user_id is None:
        _cache.clear()
    else:
        _cache.pop(_key(user_id), None)


async def refresh():
    """Перечитати весь аркуш. Повертає (к-сть рядків, помилка)."""
    import sheets_store
    if not sheets_store.enabled():
        return 0, None
    rows, err = await sheets_store.fetch_stock()
    if err:
        return 0, err
    fresh: dict[str, dict[str, dict]] = {}
    for r in rows:
        uid = str(r.get("tg_id") or "").strip()
        art = str(r.get("article") or "").strip()
        if uid and art:
            fresh.setdefault(uid, {})[article_key.canon(art)] = r
    _cache.clear()
    _cache.update(fresh)
    return len(rows), None


def all_rows() -> list:
    """Усі рядки залишків з кешу (для перевірки при старті)."""
    return [r for by_user in _cache.values() for r in by_user.values()]


async def rows_for(user_id) -> dict:
    """{канонічний_артикул: рядок} цього дропшипера (з кешу або з таблиці)."""
    uid = _key(user_id)
    if uid not in _cache:
        _, err = await refresh()
        if err:
            log.warning("Залишки не прочитались: %s", err)
            return {}
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


def cached_available(user_id, article: str):
    row = cached_row(user_id, article)
    return None if row is None else _int(row.get("available"))


def _int(value) -> int:
    try:
        return int(float(str(value).replace(",", "").replace("\xa0", "").strip()))
    except (TypeError, ValueError):
        return 0


async def available(user_id, article: str) -> int | None:
    """Скільки вільно. None — рядка немає, залишок для цього товару не ведеться."""
    row = await row_for(user_id, article)
    return None if row is None else _int(row.get("available"))


async def display_name(user_id, article: str) -> str:
    """«Персональна назва» дропшипера або "" — назва вже містить розмір."""
    row = await row_for(user_id, article)
    return str((row or {}).get("name") or "").strip()


async def _op(op: str, user_id, article: str, qty: int):
    """Спільна частина reserve/receive/release. Повертає (дані, помилка)."""
    import sheets_store
    if not sheets_store.enabled():
        return None, "таблиця не налаштована"
    data, err = await sheets_store.stock_op(op, user_id, article, qty)
    # кеш скидаємо у будь-якому разі: після відмови «не вистачає» наші числа
    # однаково застарілі — хтось інший встиг зарезервувати
    invalidate(user_id)
    return data, err


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
