"""Просте JSON-сховище: схвалені дропшипери та лічильник замовлень."""
import json
import os
import threading

import config

USERS_PATH = os.path.join(config.DATA_DIR, "users.json")
_lock = threading.Lock()


def _load():
    if not os.path.exists(USERS_PATH):
        return {"approved": {}, "pending": {}, "order_seq": 0}
    with open(USERS_PATH, encoding="utf-8") as f:
        d = json.load(f)
    d.setdefault("approved", {})
    d.setdefault("pending", {})
    d.setdefault("order_seq", 0)
    return d


def _save(d):
    os.makedirs(config.DATA_DIR, exist_ok=True)
    with open(USERS_PATH, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)


def is_approved(user_id: int) -> bool:
    return str(user_id) in _load()["approved"] or user_id in config.ADMIN_IDS


def add_pending(user_id: int, name: str, username: str):
    with _lock:
        d = _load()
        d["pending"][str(user_id)] = {"name": name, "username": username}
        _save(d)


def approve(user_id: int) -> dict | None:
    with _lock:
        d = _load()
        info = d["pending"].pop(str(user_id), None) or {"name": "", "username": ""}
        d["approved"][str(user_id)] = info
        _save(d)
        return info


def deny(user_id: int):
    with _lock:
        d = _load()
        d["pending"].pop(str(user_id), None)
        d["approved"].pop(str(user_id), None)
        _save(d)


def approved_users() -> dict:
    return _load()["approved"]


def next_order_no() -> int:
    with _lock:
        d = _load()
        d["order_seq"] += 1
        _save(d)
        return d["order_seq"]
