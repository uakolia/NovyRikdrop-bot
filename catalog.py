"""Каталог товарів: завантаження, категорії, пошук, оновлення з Google Таблиці."""
import json
import os
import re

import aiohttp

import article_key
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


def model_in_stock(user_id: int | None, model_ua: str) -> bool:
    """Чи є в дропшипера передзамовлення хоч на один розмір цієї моделі."""
    if user_id is None:
        return False
    import stock
    return any(stock.cached_row(user_id, v["article"]) for v in variants(model_ua))


def models_for(user_id: int | None, category: str):
    """Моделі категорії: спершу ті, що в передзамовленні цього дропшипера.

    Лана продає переважно свої передзамовлені моделі, тож гортати через увесь
    прайс їй не треба. Порядок усередині груп лишається прайсовий (стабільне
    сортування).

    ВАЖЛИВО: цим списком мають користуватися ВСІ місця, де модель береться за
    номером (клавіатура, вибір моделі, список розмірів) — інакше кнопка вела б
    не на ту модель.
    """
    ms = models(category)
    if user_id is None:
        return ms
    return sorted(ms, key=lambda m: not model_in_stock(user_id, m))


def variants(model_ua: str):
    """Всі розміри/варіанти моделі."""
    return [i for i in items() if i["model_ua"] == model_ua]


def by_article(article: str):
    """Товар за артикулом. Спершу точний збіг, далі — канонічний ключ.

    Канонічний ключ потрібен через кириличні двійники в прайсі
    («Cr6сustom-220» з кириличною с) — див. article_key.
    """
    for i in items():
        if i["article"] == article:
            return i
    key = article_key.canon(article)
    if not key:
        return None
    for i in items():
        if article_key.canon(i["article"]) == key:
            return i
    return None


def drop_price(item, user_id: int | None = None) -> float:
    """Дроп-ціна за тарифом цього дропшипера (у кого свого немає — загальний).

    Колонка «Дроп-ціна» в «Залишках дропшиперів» — лише для показу в таблиці,
    ціни звідти не беремо: джерело правди — прайс плюс тариф.
    """
    import storage
    tier = "price_" + storage.price_tier(user_id)
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


# хвіст назви з розміром: «2.2м», «1,8 м», «60 см», «Ø30 см»
# Хвіст із розміром: «2.2м», «1,8 м», «60 см», «Ø30 см», «2.5».
# Голе ціле число без одиниць НЕ чіпаємо — воно може бути частиною назви
# («Ялинка 3» лишається «Ялинка 3»).
_SIZE_SUFFIX = re.compile(
    r"[\s,–-]*(?:(?:Ø\s*)?\d+(?:[.,]\d+)?\s*(?:м|m|см|cm)|\d+[.,]\d+)$",
    re.IGNORECASE)


def strip_size(name: str) -> str:
    """«Нью Йорк 2.2м» → «Нью Йорк». Порожній результат — лишаємо як було."""
    clean = _SIZE_SUFFIX.sub("", (name or "").strip()).strip(" -–,")
    return clean or (name or "").strip()


def model_label(model_ua: str, user_id: int | None = None) -> str:
    """Назва моделі з артикулом: 'Українська (Cr3)'.

    Якщо у дропшипера є власна назва — показуємо її (артикул лишаємо,
    щоб можна було звіритися з прайсом і швидко знайти позицію в підтримці).
    """
    import aliases
    import stock
    pref = article_prefix(model_ua)
    vs = variants(model_ua)
    # «Персональна назва» ведеться за розміром; для назви МОДЕЛІ беремо першу,
    # яка є в залишках, і прибираємо з неї розмір — «Нью Йорк 2.2м» у списку
    # моделей має бути просто «Нью Йорк», розміри йдуть наступним екраном
    own = ""
    for v in vs:
        own = strip_size(stock.cached_name(user_id, v["article"]))
        if own:
            break
    shown = own or aliases.model_name(user_id, model_ua,
                                      vs[0]["article"] if vs else "")
    return f"{shown} ({pref})" if pref else shown


_TYPE_WORDS = ("віночок", "гірлянда", "ікебана", "настінна", "подарункова",
               "підвісна", "ялинка", "штучна")

# Чим починати опис у ТТН, якщо назва сама не каже, що це за річ. Нова Пошта
# має бачити вміст коробки: від цього залежать оголошена вартість і претензії.
_TTN_PREFIX = {
    "Ялинки": "Штучна ялинка",
    "Ялинки в горщику": "Штучна ялинка",
    "Підвісні ялинки": "Штучна ялинка",
    "Настінні ялинки": "Штучна ялинка",
    "Подарункові ялинки": "Штучна ялинка",
    "Ікебани": "Ікебана",
    "Віночки": "Віночок",
    "Гірлянди": "Гірлянда",
}


def ttn_prefix(item) -> str:
    """Слово типу для опису ТТН ("" — назва вже починається з такого слова)."""
    return _TTN_PREFIX.get(category_of(item), "Штучна ялинка")


def _with_type(name: str, item) -> str:
    """Додати «Штучна ялинка» / «Віночок» / … якщо назва цього ще не каже."""
    clean = (name or "").strip()
    if clean.lower().startswith(_TYPE_WORDS):
        return clean
    prefix = ttn_prefix(item)
    return f"{prefix} {clean}".strip() if prefix else clean


def product_name(user_id: int | None, item) -> str:
    """Назва товару очима дропшипера.

    Порядок: «Персональна назва» із «Залишків дропшиперів» → власна назва з
    «Назв товарів» (aliases) → заводська назва з прайсу.
    """
    if not item:
        return ""
    import aliases
    import stock
    own = stock.cached_name(user_id, item.get("article", ""))
    return own or aliases.item_name(user_id, item)


_TTN_PLURAL = {
    "Штучна ялинка": "Штучні ялинки",
    "Віночок": "Віночки",
    "Гірлянда": "Гірлянди",
    "Ікебана": "Ікебани",
}


def ttn_description_multi(items, user_id: int | None = None) -> str:
    """Опис однієї накладної на кілька позицій.

    items = [(товар, кількість)]. Персональні назви тут не влізуть, тому
    перелічуємо артикули: «Штучні ялинки: Cr6G-220 ×1, Cr3P-210 ×2».
    Артикули — оригінальні рядки прайсу, їх і знає фабрика.

    Якщо категорії різні — пишемо «Новорічний декор». Ліміт НП 100 символів:
    ріжемо по межі позиції й дописуємо «+N поз.», щоб артикул не розірвався
    посередині.
    """
    items = [(it, int(q)) for it, q in items if it]
    if not items:
        return ""
    if len(items) == 1:
        return ttn_description(items[0][0], user_id)

    kinds = {ttn_prefix(it) for it, _ in items}
    head = (_TTN_PLURAL.get(next(iter(kinds)), next(iter(kinds)))
            if len(kinds) == 1 else "Новорічний декор")

    parts = [f"{it['article']} ×{q}" for it, q in items]
    out = f"{head}: {parts[0]}"
    for i, part in enumerate(parts[1:], start=1):
        candidate = f"{out}, {part}"
        left = len(parts) - i - 1          # скільки ще лишиться після цієї
        tail = f" +{left} поз." if left else ""
        if len(candidate) + len(tail) <= 100:
            out = candidate
        else:
            rest = len(parts) - i
            out = f"{out} +{rest} поз."
            break
    return out[:100]


def ttn_description(item, user_id: int | None = None) -> str:
    """Опис для ТТН: «Штучна ялинка Грандія 2.2 м (Cr6G-220)».

    Починається зі слова типу («Штучна ялинка», «Віночок», «Гірлянда»,
    «Ікебана»), щоб у Нової Пошти було видно, що в коробці — це впливає на
    оголошену вартість і на претензії. Якщо назва вже починається з такого
    слова, вдруге його не додаємо.

    Якщо у дропшипера є «Персональна назва» — далі беремо ЇЇ як є: вона вже
    містить розмір («Нью Йорк 2.2м»), і size_label() дав би «Нью Йорк 2.2м
    2.2 м». Тому розмір окремо додаємо лише до заводської назви.

    В дужках завжди ОРИГІНАЛЬНИЙ артикул прайсу (не канонічний ключ) — саме
    його знають фабрика й прайс. В описі лише дані про товар: жодних ПІБ,
    телефонів чи адрес.
    """
    import stock
    own = stock.cached_name(user_id, item.get("article", ""))
    if own:
        return f"{_with_type(own, item)} ({item['article']})"[:100]
    name = _with_type(item["model_ua"], item)
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

        # якщо якась вкладка віддала 0 позицій — читання неповне, тому
        # старі товари з непрочитаних вкладок зберігаємо, а не втрачаємо
        empty_tabs = [r for r in report if r.endswith(": 0") or "HTTP" in r]
        kept = 0
        if empty_tabs:
            for old in items_or_empty():
                if old["article"] not in merged:
                    merged[old["article"]] = old
                    kept += 1
            if kept:
                report.append(f"збережено з попереднього каталогу: {kept}")

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
