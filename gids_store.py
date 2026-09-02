"""Номери вкладок прайсу (gid), задані командою /gids.

Потрібні, бо Google не завжди віддає боту повний список аркушів.
Беруться з URL таблиці: коли ви на потрібній вкладці, в адресі є #gid=123456
"""
import json
import os

import config

PATH = os.path.join(config.DATA_DIR, "pricelist_gids.json")


def get() -> list[str]:
    if not os.path.exists(PATH):
        return []
    try:
        with open(PATH, encoding="utf-8") as f:
            data = json.load(f)
        return [str(g) for g in data if str(g).isdigit()]
    except Exception:  # noqa: BLE001
        return []


def save(gids: list[str]):
    os.makedirs(config.DATA_DIR, exist_ok=True)
    with open(PATH, "w", encoding="utf-8") as f:
        json.dump([str(g) for g in gids if str(g).isdigit()], f)


def clear():
    if os.path.exists(PATH):
        os.remove(PATH)
