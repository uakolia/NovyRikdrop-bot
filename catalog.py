"""Каталог товарів: завантаження, категорії, пошук, оновлення з Google Таблиці."""
import json
import os
import re

import aiohttp

import config
from catalog_parser import parse_csv_text

CATALOG_PATH = os.path.join(os.path.dirname(__file__), "catalog.json")

_catalog = {"items": []}


def load():
    global _catalog
    with open(CATALOG_PATH, encoding="utf-8") as f:
        _catalog = json.load(f)
    return _catalog


def items():
    if not _catalog["items"]:
        load()
    return _catalog["items"]


def category_of(item) -> str:
    a, m = item["article"].upper(), item["model_ua"].lower()
    if a.startswith("WR-") or "віноч" in m or "вінок" in m:
        return "Віночки"
    if a.startswith("GR-") or "гірлянд" in m or "гірдянд" in m:
        return "Гірлянди"
    if a.startswith("IK-") or "ікебана" in m:
        return "Ікебани"
    if a.startswith("WT-") or "настінна" in m:
        return "Настінні ялинки"
    if a.startswith("GT-") or "подарункова" in m:
        return "Подарункові ялинки"
    if "fly" in item["article"].lower() or "підвісна" in m:
        return "Підвісні ялинки"
    if "горщику" in m:
        return "Ялинки в горщику"
    return "Ялинки"


CATEGORY_ORDER = ["Ялинки", "Ялинки в горщику", "Підвісні ялинки", "Настінні ялинки",
                  "Подарункові ялинки", "Ікебани", "Віночки", "Гірлянди"]


def categories():
    present = {category_of(i) for i in items()}
    return [c for c in CATEGORY_ORDER if c in present]


def models(category: str):
    """Список назв моделей у категорії (унікальні, у порядку прайсу)."""
    seen = []
    for i in items():
        if category_of(i) == category and i["model_ua"] not in seen:
            seen.append(i["model_ua"])
    return seen


def variants(model_ua: str):
    """Всі розміри/варіанти моделі."""
    return [i for i in items() if i["model_ua"] == model_ua]


def by_article(article: str):
    for i in items():
        if i["article"] == article:
            return i
    return None


def drop_price(item) -> float:
    tier = "price_" + config.PRICE_TIER
    return item.get(tier) or item.get("price_drop3") or item.get("price_drop2") or 0


def size_label(item) -> str:
    if item.get("height_m") and item["height_m"] <= 10:
        s = f"{item['height_m']:g} м"
    elif item.get("size"):
        s = str(item["size"])
    else:
        s = item["article"]
    return s


def find_variant_short(model_ua: str, idx: int):
    v = variants(model_ua)
    return v[idx] if 0 <= idx < len(v) else None


async def reload_from_google():
    """Перечитати прайс із Google Таблиці (CSV-експорт). Повертає (к-сть, помилка)."""
    base = (f"https://docs.google.com/spreadsheets/d/{config.PRICELIST_SHEET_ID}"
            f"/export?format=csv")
    # якщо gid не задано — експортуємо першу вкладку (надійніше, ніж вгадувати gid)
    urls = [f"{base}&gid={config.PRICELIST_GID}"] if config.PRICELIST_GID else []
    urls.append(base)
    try:
        text, last_status = None, None
        async with aiohttp.ClientSession() as s:
            for url in urls:
                async with s.get(url, timeout=aiohttp.ClientTimeout(total=90)) as r:
                    last_status = r.status
                    if r.status == 200:
                        text = await r.text()
                        break
        if text is None:
            if last_status in (401, 403):
                return 0, ("немає доступу до таблиці. Відкрийте доступ "
                           "«Усі, хто має посилання — Переглядач»")
            return 0, (f"HTTP {last_status}. Перевірте PRICELIST_SHEET_ID, "
                       "а PRICELIST_GID краще залишити порожнім")
        new_items = parse_csv_text(text)
        if len(new_items) < 10:
            return 0, (f"розпізнано лише {len(new_items)} товарів — "
                       "можливо, експортувалася не та вкладка. Оновлення скасовано")
        _catalog["items"] = new_items
        with open(CATALOG_PATH, "w", encoding="utf-8") as f:
            json.dump(_catalog, f, ensure_ascii=False, indent=1)
        return len(new_items), None
    except Exception as e:  # noqa: BLE001
        return 0, str(e)
