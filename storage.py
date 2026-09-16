"""Сховище дропшиперів і лічильник замовлень.

Файли контейнера зникають при деплої, тому основне джерело правди —
Google Таблиця (аркуш «Дропшипери»). Локальний JSON — швидкий кеш.
"""
import json
import os
import threading

import config

USERS_PATH = os.path.join(config.DATA_DIR, "users.json")
_lock = threading.Lock()

# кеш у пам'яті: {user_id_str: {"name","username","tier"}}
_approved_cache: dict[str, dict] = {}
_synced = False

# допустимі значення колонки «Тариф» в аркуші «Дропшипери»
TIERS = ("drop1", "drop2", "drop3")


def _tier(value) -> str:
    """«Дроп 2», «drop2», «2» → 'drop2'. Порожнє або сміття → '' (загальний)."""
    s = str(value or "").strip().lower().replace(" ", "").replace("дроп", "drop")
    if s in ("1", "2", "3"):
        s = "drop" + s
    return s if s in TIERS else ""


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
    global _synced
    import sheets_store
    if not sheets_store.enabled():
        return 0, "SHEET_WEBHOOK_URL не задано — список зберігається лише локально"
    rows, err = await sheets_store.fetch_dropshippers()
    if err:
        return 0, err
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
    return len(_approved_cache), None


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
        info = d["pending"].pop(str(user_id), None) or {"name": "", "username": ""}
        info.setdefault("tier", "")
        d["approved"][str(user_id)] = info
        _save(d)
    _approved_cache[str(user_id)] = info
    return info


async def approve(user_id: int, approved_by: str = "") -> tuple[dict, str | None]:
    """Схвалити: локально + записати в Google Таблицю (щоб не злетіло)."""
    info = approve_local(user_id)
    import sheets_store
    err = await sheets_store.push_dropshipper(
        user_id, info.get("name", ""), info.get("username", ""),
        "схвалений", approved_by)
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
        return
    max_no, err = await sheets_store.fetch_max_order_no()
    if err or not max_no:
        return
    with _lock:
        d = _load()
        if max_no > d["order_seq"]:
            d["order_seq"] = max_no
            _save(d)


def next_order_no() -> int:
    with _lock:
        d = _load()
        d["order_seq"] += 1
        _save(d)
        return d["order_seq"]
