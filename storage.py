"""Сховище дропшиперів і лічильник замовлень.

Файли контейнера зникають при деплої, тому основне джерело правди —
Google Таблиця (аркуш «Дропшипери»). Локальний JSON — швидкий кеш.
"""
import asyncio
import json
import os
import threading

import config

USERS_PATH = os.path.join(config.DATA_DIR, "users.json")
_lock = threading.Lock()

# кеш у пам'яті: {user_id_str: {"name","username","tier"}}
_approved_cache: dict[str, dict] = {}
_synced = False

# Номер замовлення продовжується з таблиці, а читання таблиці більше не
# тримає старт бота (інакше запуск тягнувся під півтори хвилини). Тому перед
# тим, як видати номер, оформлення чекає цієї події: інакше замовлення,
# зроблене в першу секунду після деплою, отримало б номер 1 і перезаписало
# чуже. Чекати доводиться лише в ці перші секунди.
_seq_ready = asyncio.Event()


def seq_give_up():
    """Таблиця не відповіла — далі нумеруємо локально, оформлення не тримаємо."""
    _seq_ready.set()


async def wait_seq_ready(timeout: float = 15) -> bool:
    """Дочекатися, поки лічильник замовлень підтягнеться з таблиці."""
    try:
        await asyncio.wait_for(_seq_ready.wait(), timeout)
        return True
    except asyncio.TimeoutError:
        return False

# допустимі значення колонки «Тариф» в аркуші «Дропшипери»
TIERS = ("drop1", "drop2", "drop3")


def _tier(value) -> str:
    """«Дроп 2», «drop2», «2» → 'drop2'. Порожнє або сміття → '' (загальний)."""
    s = str(value or "").strip().lower().replace(" ", "").replace("дроп", "drop")
    if s in ("1", "2", "3"):
        s = "drop" + s
    return s if s in TIERS else ""


# коли востаннє ходили в таблицю по список схвалених (щоб не ходити на кожне
# натискання кнопки незнайомця)
_last_sync = 0.0
SYNC_COOLDOWN = 60


async def ensure_synced(user_id: int) -> bool:
    """Перевірити доступ, за потреби перечитавши таблицю.

    Локальний файл зникає при редеплої, тож якщо синхронізація на старті не
    вдалася, схвалених немає взагалі й бот просить авторизуватися наново.
    Тут даємо йому другий шанс: перш ніж відмовити, перечитуємо аркуш.
    """
    global _last_sync
    import time
    if is_approved(user_id):
        return True
    if time.monotonic() - _last_sync < SYNC_COOLDOWN:
        return False
    _last_sync = time.monotonic()
    n, err = await sync_from_sheet()
    if err:
        import logging
        logging.getLogger(__name__).warning(
            "Список дропшиперів не перечитався: %s", err)
        return False
    return is_approved(user_id)


def price_tier(user_id: int | None = None) -> str:
    """Тариф дропшипера: власний із таблиці або загальний config.PRICE_TIER."""
    if user_id is None:
        return config.PRICE_TIER
    info = (_approved_cache.get(str(user_id))
            or _load()["approved"].get(str(user_id)) or {})
    return info.get("tier") or config.PRICE_TIER


def _load():
    if not os.path.exists(USERS_PATH):
        return {"approved": {}, "pending": {}, "order_seq": 0}
    try:
        with open(USERS_PATH, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:  # noqa: BLE001
        d = {}
    d.setdefault("approved", {})
    d.setdefault("pending", {})
    d.setdefault("order_seq", 0)
    return d


def _save(d):
    os.makedirs(config.DATA_DIR, exist_ok=True)
    with open(USERS_PATH, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)


def is_admin(user_id: int) -> bool:
    return user_id in config.ADMIN_IDS


def is_approved(user_id: int) -> bool:
    if is_admin(user_id):
        return True
    uid = str(user_id)
    if uid in _approved_cache:
        return True
    return uid in _load()["approved"]


async def sync_from_sheet(force: bool = False):
    """Підтягнути схвалених дропшиперів із Google Таблиці."""
    import sheets_store
    if not sheets_store.enabled():
        return 0, "SHEET_WEBHOOK_URL не задано — список зберігається лише локально"
    rows, err = await sheets_store.fetch_dropshippers()
    if err:
        return 0, err
    return apply_dropshippers(rows), None


def apply_dropshippers(rows) -> int:
    """Застосувати вже прочитані рядки аркуша «Дропшипери».

    Окремо від читання, бо ті самі рядки приходять і одним запитом
    what=bootstrap разом із назвами та залишками.
    """
    global _synced
    _approved_cache.clear()
    with _lock:
        d = _load()
        d["approved"] = {}
        for r in rows:
            if str(r.get("status", "")).strip().lower().startswith("схвал"):
                uid = str(r["tg_id"]).strip()
                info = {"name": r.get("name", ""), "username": r.get("username", ""),
                        "tier": _tier(r.get("tier"))}
                _approved_cache[uid] = info
                d["approved"][uid] = info
        _save(d)
    _synced = True
    return len(_approved_cache)


def add_pending(user_id: int, name: str, username: str):
    with _lock:
        d = _load()
        d["pending"][str(user_id)] = {"name": name, "username": username}
        _save(d)


def pending_users() -> dict:
    return _load()["pending"]


def approve_local(user_id: int) -> dict:
    with _lock:
        d = _load()
        uid = str(user_id)
        info = d["pending"].pop(uid, None) or {"name": "", "username": ""}
        # тариф ведеться в таблиці; при повторному схваленні його не можна
        # обнуляти — інакше дропшипер до перезапуску бачить загальні ціни
        old_tier = (_approved_cache.get(uid) or d["approved"].get(uid) or {}).get("tier")
        info["tier"] = info.get("tier") or old_tier or ""
        d["approved"][uid] = info
        _save(d)
    _approved_cache[uid] = info
    return info


async def approve(user_id: int, approved_by: str = "") -> tuple[dict, str | None]:
    """Схвалити: локально + записати в Google Таблицю (щоб не злетіло)."""
    info = approve_local(user_id)
    import sheets_store
    err = await sheets_store.push_dropshipper(
        user_id, info.get("name", ""), info.get("username", ""),
        "схвалений", approved_by)
    # одразу перечитуємо аркуш: там джерело правди і для статусу, і для тарифу
    if not err:
        await sync_from_sheet()
        info = _approved_cache.get(str(user_id), info)
    return info, err


async def deny(user_id: int, approved_by: str = ""):
    with _lock:
        d = _load()
        d["pending"].pop(str(user_id), None)
        d["approved"].pop(str(user_id), None)
        _save(d)
    _approved_cache.pop(str(user_id), None)
    import sheets_store
    if sheets_store.enabled():
        await sheets_store.push_dropshipper(user_id, "", "", "заблокований",
                                           approved_by)


def approved_users() -> dict:
    return _approved_cache or _load()["approved"]


async def init_order_seq():
    """Продовжити нумерацію замовлень із таблиці (після деплою файл чистий)."""
    import sheets_store
    if not sheets_store.enabled():
        _seq_ready.set()
        return
    max_no, err = await sheets_store.fetch_max_order_no()
    if err:
        return
    apply_max_order_no(max_no)


def apply_max_order_no(max_no: int):
    """Зсунути лічильник до номера з таблиці й відкрити оформлення."""
    try:
        max_no = int(max_no or 0)
    except (TypeError, ValueError):
        max_no = 0
    if max_no:
        with _lock:
            d = _load()
            if max_no > d["order_seq"]:
                d["order_seq"] = max_no
                _save(d)
    _seq_ready.set()


def next_order_no() -> int:
    with _lock:
        d = _load()
        d["order_seq"] += 1
        _save(d)
        return d["order_seq"]
