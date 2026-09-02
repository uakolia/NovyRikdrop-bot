"""Каталог товарів: завантаження, категорії, пошук, оновлення з Google Таблиці."""
import json
import os
import re

import aiohttp

import config
from catalog_parser import parse_csv_text, parse_rows

CATALOG_PATH = os.path.join(os.path.dirname(__file__), "catalog.json")

_catalog = {"items": []}


def load():
    global _catalog
    with open(CATALOG_PATH, encoding="utf-8") as f:
        _catalog = json.load(f)
    return _catalog


def items_or_empty():
    return _catalog.get("items") or []


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
        return f"{item['height_m']:g} м"
    size = str(item.get("size") or "").strip()
    if size:
        # «0,6» без одиниць — це метри (подарункові/настінні)
        if re.fullmatch(r"\d+[.,]\d+", size):
            return size.replace(",", ".") + " м"
        return re.sub(r"\s*cm\b", " см", size).strip()
    art = item["article"]
    # вінки: «WR-Cl-Сr11-d Ø30» → «Ø30 см»
    m = re.search(r"d\s*Ø\s*(\d+)", art)
    if m:
        return f"Ø{m.group(1)} см"
    # гірлянди: «GR-Nb-Сr8-100» / «...-270Fr» → «100 см»
    m = re.search(r"-(\d{2,3})(Fr)?$", art)
    if m:
        return f"{m.group(1)} см"
    return art


_SIZE_TAIL = re.compile(r"[-\s]*(?:d\s*Ø\s*)?\d+(?:/\d+)?[A-Za-zА-Яа-я]*$")


def article_prefix(model_ua: str) -> str:
    """Базовий артикул моделі без розміру: 'Cr3-150' → 'Cr3'."""
    vs = variants(model_ua)
    if not vs:
        return ""
    base = _SIZE_TAIL.sub("", vs[0]["article"]).strip(" -")
    return base


def model_label(model_ua: str, user_id: int | None = None) -> str:
    """Назва моделі з артикулом: 'Українська (Cr3)'.

    Якщо у дропшипера є власна назва — показуємо її (артикул лишаємо,
    щоб можна було звіритися з прайсом і швидко знайти позицію в підтримці).
    """
    import aliases
    pref = article_prefix(model_ua)
    vs = variants(model_ua)
    shown = aliases.model_name(user_id, model_ua,
                               vs[0]["article"] if vs else "")
    return f"{shown} ({pref})" if pref else shown


_TYPE_WORDS = ("віночок", "гірлянда", "ікебана", "настінна", "подарункова",
               "підвісна", "ялинка")


def ttn_description(item) -> str:
    """Опис для ТТН: «Ялинка Грандія 2.2 м (Cr6G-220)»."""
    name = item["model_ua"]
    if not name.lower().startswith(_TYPE_WORDS):
        name = f"Ялинка {name}"
    size = size_label(item)
    if size == item["article"]:
        return f"{name} ({item['article']})"[:100]
    return f"{name} {size} ({item['article']})"[:100]


def find_variant_short(model_ua: str, idx: int):
    v = variants(model_ua)
    return v[idx] if 0 <= idx < len(v) else None


SHEET_BASE = "https://docs.google.com/spreadsheets/d/{sid}"

_TR = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.S | re.I)
_TD = re.compile(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", re.S | re.I)
_TAG = re.compile(r"<[^>]+>")


def _html_rows(html: str):
    """Рядки таблиці з HTML-експорту аркуша Google Таблиці."""
    import html as _html
    for tr in _TR.findall(html):
        cells = []
        for cell in _TD.findall(tr):
            text = _TAG.sub(" ", cell)
            text = _html.unescape(text)
            cells.append(re.sub(r"\s+", " ", text).strip())
        if cells:
            yield [""] + cells  # зсув на 1: парсер очікує технічну колонку зліва


MAX_DOWNLOAD = 25 * 1024 * 1024   # 25 МБ — більше в контейнер не тягнемо


async def _fetch_bytes(session, url, max_bytes: int = MAX_DOWNLOAD):
    """Завантажити з жорстким лімітом — інакше zip із фото з'їдає всю пам'ять."""
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=180)) as r:
        if r.status != 200:
            return r.status, b""
        size = r.headers.get("Content-Length")
        if size and size.isdigit() and int(size) > max_bytes:
            return 0, b""                      # завеликий — навіть не читаємо
        buf = bytearray()
        async for chunk in r.content.iter_chunked(64 * 1024):
            buf.extend(chunk)
            if len(buf) > max_bytes:
                return 0, b""                  # обриваємо на льоту
        return 200, bytes(buf)


async def _fetch(session, url):
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=90)) as r:
        return r.status, (await r.text() if r.status == 200 else "")


_GID_PATTERNS = (
    r'sheet-button-(\d{1,12})',          # htmlview: кнопки вкладок
    r'[\'"]gid[\'"]\s*:\s*[\'"]?(\d{1,12})',
    r'[?&#]gid=(\d{1,12})',
)


async def _discover_gids(session, sid):
    """Знайти номери всіх вкладок таблиці."""
    base = SHEET_BASE.format(sid=sid)
    gids, seen = [], set()
    for url in (base + "/htmlview", base + "/edit"):
        status, blob = await _fetch_bytes(session, url, max_bytes=8 * 1024 * 1024)
        if status != 200 or not blob:
            continue
        html = blob.decode("utf-8", "replace")
        for pat in _GID_PATTERNS:
            for m in re.finditer(pat, html):
                g = m.group(1)
                if g not in seen:
                    seen.add(g)
                    gids.append(g)
        if len(gids) > 1:      # знайшли кілька вкладок — цього достатньо
            break
    return gids[:40]


async def reload_from_google():
    """Перечитати прайс із Google Таблиці — всі вкладки. Повертає (к-сть, помилка).

    Читаємо CSV-експорт кожної вкладки: лише текст, без фото — тому швидко
    й без навантаження на пам'ять. Номери вкладок беремо з налаштувань
    (/gids) або шукаємо на сторінці таблиці.
    """
    sid = config.PRICELIST_SHEET_ID
    export = SHEET_BASE.format(sid=sid) + "/export?format=csv"
    try:
        async with aiohttp.ClientSession() as s:
            # --- спроба 1: CSV по кожній вкладці (мало пам'яті, надійно)
            import gids_store
            gids = (gids_store.get() or config.PRICELIST_GIDS
                    or await _discover_gids(s, sid))

            merged, ok_tabs, last_status, report = {}, 0, None, []
            urls = [f"{export}&gid={g}" for g in gids] or [export]
            for gid, url in zip(gids or ["перша"], urls):
                status, text = await _fetch(s, url)
                last_status = status
                name = config.TAB_NAMES.get(str(gid), f"gid {gid}")
                if status != 200:
                    report.append(f"{name}: ⚠️ HTTP {status}")
                    continue
                items = parse_csv_text(text)
                report.append(f"{name}: {len(items)}")
                if items:
                    ok_tabs += 1
                    for it in items:
                        merged.setdefault(it["article"], it)

            if not merged:
                status, text = await _fetch(s, export)
                last_status = status
                if status == 200:
                    for it in parse_csv_text(text):
                        merged.setdefault(it["article"], it)

        if not merged:
            if last_status in (401, 403):
                return 0, ("немає доступу до таблиці. Відкрийте доступ "
                           "«Усі, хто має посилання — Переглядач»")
            return 0, f"не вдалося прочитати таблицю (HTTP {last_status})"

        saved = _commit(merged, ok_tabs)
        if saved[1] is None:
            _last_report[:] = report
        return saved
    except Exception as e:  # noqa: BLE001
        return 0, str(e)


_last_report: list[str] = []


def last_report() -> list[str]:
    return list(_last_report)


def _commit(merged: dict, tabs_count: int):
    """Записати каталог із запобіжником проти втрати товарів."""
    new_items = list(merged.values())
    current = len(items_or_empty())
    if current and len(new_items) < current * 0.8:
        return 0, (f"розпізнано лише {len(new_items)} товарів із {tabs_count} вкладок, "
                   f"а в каталозі зараз {current}. Схоже, прочиталися не всі "
                   "вкладки — оновлення скасовано, каталог не змінено")
    if len(new_items) < 10:
        return 0, f"розпізнано лише {len(new_items)} товарів — оновлення скасовано"
    _catalog["items"] = new_items
    with open(CATALOG_PATH, "w", encoding="utf-8") as f:
        json.dump(_catalog, f, ensure_ascii=False, indent=1)
    return len(new_items), None
