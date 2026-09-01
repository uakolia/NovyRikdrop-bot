"""Збереження налаштувань відправника НП, зроблених через /np_setup у боті.

Значення з цього файлу мають пріоритет над змінними оточення. Файл живе у
data/ (на хостингу зникає при передеплої), тому після налаштування бот
показує рядки для Railway Variables — щоб налаштування пережили редеплой.
"""
import json
import os

import config

PATH = os.path.join(config.DATA_DIR, "np_sender.json")

_ENV_MAP = {
    "api_key": "NP_API_KEY",
    "sender_ref": "NP_SENDER_REF",
    "contact_ref": "NP_SENDER_CONTACT_REF",
    "city_ref": "NP_SENDER_CITY_REF",
    "warehouse_ref": "NP_SENDER_WAREHOUSE_REF",
    "phone": "NP_SENDER_PHONE",
}


def _load() -> dict:
    if os.path.exists(PATH):
        try:
            with open(PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:  # noqa: BLE001
            return {}
    return {}


def save(**kw):
    d = _load()
    d.update({k: v for k, v in kw.items() if v})
    os.makedirs(config.DATA_DIR, exist_ok=True)
    with open(PATH, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)


def get(key: str) -> str:
    """Спершу data/np_sender.json, потім змінна оточення."""
    v = _load().get(key)
    if v:
        return v
    env_name = _ENV_MAP.get(key)
    return getattr(config, env_name, "") if env_name else ""


def all_values() -> dict:
    return {k: get(k) for k in _ENV_MAP}


def env_lines() -> str:
    """Готові рядки для Railway Variables."""
    vals = all_values()
    return "\n".join(f"{_ENV_MAP[k]}={vals[k]}" for k in _ENV_MAP if vals[k])


def is_ready() -> bool:
    vals = all_values()
    return all(vals[k] for k in ("api_key", "sender_ref", "contact_ref",
                                 "city_ref", "warehouse_ref", "phone"))
