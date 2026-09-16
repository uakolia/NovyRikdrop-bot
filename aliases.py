"""Власні назви товарів для кожного дропшипера.

Джерело правди — аркуш «Назви товарів» у таблиці замовлень:
    Telegram ID | Артикул або модель | Своя назва
Ключ може бути:
  • артикулом  — «Cr6G-220» → назва лише для цього розміру
  • моделлю    — «Грандія»  → назва для всіх розмірів моделі
Локальний JSON — кеш, щоб працювало й без таблиці.
"""
import json
import os

import article_key
import config

PATH = os.path.join(config.DATA_DIR, "aliases.json")

# {user_id_str: {ключ_у_нижньому_регістрі: "Своя назва"}}
_cache: dict[str, dict[str, str]] = {}


def _norm(key: str) -> str:
    """Ключ пошуку (артикул або назва моделі) — канонічний, з обох боків.

    Кириличні двійники в артикулах («Cr6сustom-220») інакше не знаходяться.
    """
    return article_key.canon(key)


def _load_local():
    global _cache
    if os.path.exists(PATH):
        try:
            with open(PATH, encoding="utf-8") as f:
                _cache = json.load(f)
        except Exception:  # noqa: BLE001
            _cache = {}
    return _cache


def _save_local():
    os.makedirs(config.DATA_DIR, exist_ok=True)
    with open(PATH, "w", encoding="utf-8") as f:
        json.dump(_cache, f, ensure_ascii=False, indent=1)


def loaded() -> bool:
    return bool(_cache)


async def sync_from_sheet():
    """Підтягнути власні назви з таблиці. Повертає (к-сть, помилка)."""
    global _cache
    import sheets_store
    if not sheets_store.enabled():
        _load_local()
        return sum(len(v) for v in _cache.values()), None
    rows, err = await sheets_store.fetch_aliases()
    if err:
        _load_local()
        return sum(len(v) for v in _cache.values()), err
    new: dict[str, dict[str, str]] = {}
    for r in rows:
        uid = str(r.get("tg_id", "")).strip()
        key = _norm(r.get("key"))
        name = str(r.get("name", "")).strip()
        if uid and key and name:
            new.setdefault(uid, {})[key] = name
    _cache = new
    _save_local()
    return sum(len(v) for v in _cache.values()), None


def set_alias(user_id: int, key: str, name: str):
    _cache.setdefault(str(user_id), {})[_norm(key)] = name.strip()
    _save_local()


def remove_alias(user_id: int, key: str) -> bool:
    d = _cache.get(str(user_id)) or {}
    return d.pop(_norm(key), None) is not None


def for_user(user_id: int) -> dict[str, str]:
    return _cache.get(str(user_id)) or {}


def item_name(user_id: int | None, item) -> str:
    """Назва товару очима цього дропшипера (артикул > модель > наша назва)."""
    if user_id is None or not item:
        return item["model_ua"] if item else ""
    d = for_user(user_id)
    if not d:
        return item["model_ua"]
    return (d.get(_norm(item.get("article")))
            or d.get(_norm(item.get("model_ua")))
            or item["model_ua"])


def model_name(user_id: int | None, model_ua: str, article_sample: str = "") -> str:
    """Назва моделі очима дропшипера."""
    if user_id is None:
        return model_ua
    d = for_user(user_id)
    if not d:
        return model_ua
    return (d.get(_norm(model_ua))
            or (d.get(_norm(article_sample)) if article_sample else None)
            or model_ua)


def name_by_article(user_id: int | None, article: str, fallback: str = "") -> str:
    """Для «Моїх замовлень»: назва за артикулом із замовлення."""
    if user_id is None:
        return fallback
    d = for_user(user_id)
    if not d:
        return fallback
    return d.get(_norm(article)) or fallback
