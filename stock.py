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
    fresh: dict[str, dict[str, dict]] = {}
    for r in rows:
        uid = str(r.get("tg_id") or "").strip()
        art = str(r.get("article") or "").strip()
        if uid and art:
            fresh.setdefault(uid, {})[article_key.canon(art)] = r
    _cache.clear()
    _cache.update(fresh)
    _stale.clear()
    return len(rows), None


def all_rows() -> list:
    """Усі рядки залишків з кешу (для перевірки при старті)."""
    return [r for by_user in _cache.values() for r in by_user.values()]


async def rows_for(user_id) -> dict:
    """{канонічний_артикул: рядок} цього дропшипера (з кешу або з таблиці)."""
    uid = _key(user_id)
    if uid not in _cache or uid in _stale:
        _, err = await refresh()
        if err:
            log.warning("Залишки не прочитались: %s", err)
            return _cache.get(uid) or {}     # краще застарілі числа, ніж нічого
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
    if err:
        # навіть відмова «не вистачає» означає, що наші числа застарілі:
        # хтось інший устиг зарезервувати
        invalidate(user_id)
    else:
        apply_write(user_id, data)
    return data, err


async def _op_many(op: str, user_id, items: list[dict]):
    import sheets_store
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
