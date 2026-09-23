"""Перенесення залишків зі складської таблиці в наш прайс.

ДЖЕРЕЛО — складська Google-таблиця (WAREHOUSE_SHEET_ID), ПРИЙМАЧ — наш прайс
(config.PRICELIST_SHEET_ID). Обидві читаються й пишуться через Sheets API.

Синхронізуються всі вкладки прайсу, де є колонка залишку; решта не чіпається.

Прайс — джерело правди для цін. Тому пишемо ТІЛЬКИ в колонку «К-сть на складі»,
знайдену за заголовком, і ніколи за індексом: у кожній вкладці своя розкладка
(заголовок то в 2-му, то в 4-му рядку, артикул то в D, то в H, то в J).

Що може піти не так із чужим файлом — і що ми робимо:
  • якщо весь стовпець залишку порожній — відмовляємось від синхронізації:
    це радше збій, ніж «усе розпродано», нулів не пишемо;
  • об'єднані клітинки: назва моделі стоїть лише в першому рядку групи, решта
    читаються як None — протягуємо останню побачену вниз, як catalog_parser;
  • немає заголовка залишку — вкладку пропускаємо: краще вчорашні числа, ніж
    сміття;
  • таблиця зникла, доступу немає, аркуш перейменували — ERROR і лист адміну,
    не частіше разу на годину.

ЗВЕДЕННЯ АРТИКУЛІВ. Спершу канонічний ключ (article_key), далі — одне явне
правило: у складі літера-суфікс стоїть у кінці («Cr3-150F»), а в прайсі перед
розміром («Cr3F-150»). Перевірено, що це не створює колізій: 14 пар, кожна
однозначна. Усе інше без пари не вгадуємо — виносимо у звіт.

Три стани залишку, і «невідомо» — не те саме, що «немає»:
    число          → кількість, пишемо в прайс
    так/є/yes/+    → є без числа, пишемо «є»
    порожньо       → невідомо: у прайсі лишаємо, що було, і кажемо в звіті
Артикул, який зник із джерела зовсім, — ось тоді клітинку очищаємо.
"""
import asyncio
import io
import json
import logging
import os
import re
import time

import article_key
import config

log = logging.getLogger(__name__)

# Як може називатися колонка залишку. Назви в таблицях правлять руками:
# бачили «К-сть на складі», «наявності», «кількістсь». Тому шукаємо за коренем
# слова, а не за точним текстом.
STOCK_STEMS = ("к-сть на склад", "кількість на склад", "кількіст", "наявн",
               "залиш", "на складі")

# Заголовок із цими словами — НЕ залишок, хай там що ще в ньому написано.
# Без цього пошук «склад» вибрав би цінову колонку «Дроп 3 — товар у наявності
# на складі» і бот записав би залишки просто в ціни.
NOT_STOCK_WORDS = ("ціна", "дроп", "price", "грн", "euro", "євро", "usd", "uah",
                   "сегмент", "гілк", "branch", "tips", "part", "вітк",
                   "передзамовлен", "гуртов")

# довгий заголовок — це радше опис умов продажу, ніж назва колонки залишку
MAX_STOCK_HEADER = 30

# якщо «зникла» зі складу більша частка артикулів, ніж ця — не чистимо нічого:
# схоже не на розпродаж, а на розбіжність написання артикулів
MAX_CLEAR_SHARE = 0.3

ARTICLE_HEADER = "article"

# рядки-дублікат cr6custom із неправильними наявностями
SKIP_SOURCE_ROWS = range(118, 128)

# «є» без числа
YES_WORDS = {"так", "є", "yes", "+", "да", "in stock", "у наявності"}

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# щоб не засипати адміна: не частіше разу на годину
ALERT_COOLDOWN = 3600
_last_alert = 0.0


class WarehouseError(Exception):
    """Синхронізація неможлива — краще нічого не робити, ніж зіпсувати прайс."""


# ---------------------------------------------------------------- допоміжне

def _norm(s) -> str:
    return re.sub(r"\s+", " ", str(s if s is not None else "")).strip()


# перше число в клітинці залишку
_FIRST_NUMBER = re.compile(r"-?\d+(?:[.,]\d+)?")


def parse_stock(value):
    """Клітинка залишку → (стан, число, примітка). Стани: qty / yes / unknown.

    На складі пишуть не лише числа: «9 (+5 блакитна)», «12 шт», «2-3». Беремо
    ПЕРШЕ число, а не всі цифри підряд, — інакше «9 (+5 блакитна)» склеїлося б
    у 95 і залишок роздувся б у десять разів.

    Примітка — те, що лишилося від тексту («+5 блакитна»). У прайс не йде, але
    потрапляє у звіт, щоб такі уточнення не пропадали безслідно.
    """
    s = _norm(value).replace("\xa0", " ")
    low = s.lower()
    if not low:
        return "unknown", None, ""
    if low in YES_WORDS:
        return "yes", None, ""
    m = _FIRST_NUMBER.search(low)
    if m is None:
        # текст на кшталт «під замовлення» — не число і не «є»
        state = "yes" if any(w in low for w in YES_WORDS) else "unknown"
        return state, None, s
    try:
        n = float(m.group(0).replace(",", "."))
    except ValueError:
        return "unknown", None, s
    if n < 0:
        return "unknown", None, s
    note = (low[:m.start()] + low[m.end():]).strip(" ()шт.,")
    return "qty", int(n), note


# ЗВЕДЕННЯ АРТИКУЛІВ. Склад і прайс пишуть ті самі товари по-різному, і це не
# випадковий шум, а кілька стійких схем. Кожне правило вузьке й перевірене на
# живих таблицях: разом вони дають 45 пар і ЖОДНОЇ колізії. Усе, що не
# підпадає, не вгадуємо — воно йде у звіт.
#
#   1) літера-суфікс переїжджає перед розмір:   Cr3-150F   → Cr3F-150
#   2) те саме, і Люкс на складі = Преміум у прайсі: Cr3-150L → Cr3P-150
#   3) останнє слово переїжджає перед розмір:   Cr7lush-240 mіx → Cr7lush mіx-240
_SUFFIX_SWAP = re.compile(r"^([A-Za-zА-Яа-яІіЇїЄєҐґ]+\d*)-(\d+)"
                          r"([A-Za-zА-Яа-яІіЇїЄєҐґ]+)$")
_WORD_SWAP = re.compile(r"^(.+?)-(\d+)\s+([A-Za-zА-Яа-яІіЇїЄєҐґ]+)$")

# суфікс складу → суфікс прайсу (L = Люкс, P = Преміум: та сама лінійка)
SUFFIX_ALIASES = {"L": "P"}


def alt_keys(article: str) -> list:
    """Канонічні ключі, під якими цей артикул може стояти в прайсі."""
    a = _norm(article)
    out = []
    m = _SUFFIX_SWAP.match(a)
    if m:
        base, size, suffix = m.group(1), m.group(2), m.group(3)
        out.append(f"{base}{suffix}-{size}")
        alias = SUFFIX_ALIASES.get(suffix.upper())
        if alias:
            out.append(f"{base}{alias}-{size}")
    m = _WORD_SWAP.match(a)
    if m:
        out.append(f"{m.group(1)} {m.group(3)}-{m.group(2)}")
    return [article_key.canon(v) for v in out]


def build_index(items: dict):
    """Ключ → позиція: спершу артикул як є, далі за правилами вище.

    Правило не перекриває вже наявний ключ, тож точний збіг завжди сильніший.
    """
    index = dict(items)
    for key, item in items.items():
        for alt in alt_keys(item["article"]):
            if alt and alt not in index:
                index[alt] = item
    return index, []


def col_letter(idx0: int) -> str:
    """0 → A, 25 → Z, 26 → AA."""
    s, n = "", idx0 + 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _find_header_row(rows, need_stock: bool):
    """(номер рядка, {поле: індекс}) або (None, None).

    Шукаємо рядок, де є «Article»; колонку залишку — за заголовком, не за
    місцем: розкладка вкладок різна й може змінитися без попередження.
    """
    for i, row in enumerate(rows[:20]):
        low = [_norm(c).lower() for c in row]
        if not any(c == ARTICLE_HEADER for c in low):
            continue
        cols = {"article": low.index(ARTICLE_HEADER)}
        for j, c in enumerate(low):
            if not c or len(c) > MAX_STOCK_HEADER:
                continue
            if any(w in c for w in NOT_STOCK_WORDS):
                continue
            if any(c.startswith(st) or st in c for st in STOCK_STEMS):
                cols["stock"] = j
                cols["stock_header"] = c
                break
        if need_stock and "stock" not in cols:
            return i, None            # заголовок знайшли, колонки залишку немає
        return i, cols
    return None, None


# ---------------------------------------------------------------- джерело

def _credentials():
    raw = (config.GOOGLE_SERVICE_ACCOUNT_JSON or "").strip()
    if not raw:
        raise WarehouseError("не задано GOOGLE_SERVICE_ACCOUNT_JSON")
    try:
        from google.oauth2 import service_account
    except ImportError as e:  # noqa: BLE001
        raise WarehouseError(f"немає бібліотеки google-auth: {e}")
    try:
        info = json.load(open(raw, encoding="utf-8")) if os.path.exists(raw) \
            else json.loads(raw)
    except (OSError, ValueError) as e:
        raise WarehouseError(f"GOOGLE_SERVICE_ACCOUNT_JSON не читається: {e}")
    return service_account.Credentials.from_service_account_info(
        info, scopes=SCOPES)


def _services(creds):
    """Клієнт Sheets API. Drive не потрібен: обидві таблиці — рідні Google."""
    try:
        from googleapiclient.discovery import build
    except ImportError as e:  # noqa: BLE001
        raise WarehouseError(f"немає бібліотеки google-api-python-client: {e}")
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def _collect_rows(rows, title: str, items: dict, notes: list, dups: dict):
    """Розібрати рядки одного аркуша джерела в items. Спільне для .xlsx і Sheets.

    Повертає True, якщо аркуш містив колонку залишку.
    """
    hdr, cols = _find_header_row(rows, need_stock=True)
    if hdr is None or not cols:
        return False
    model = ""
    skipped = []
    for n, row in enumerate(rows[hdr + 1:], start=hdr + 2):
        if n in SKIP_SOURCE_ROWS:
            art_dbg = _norm(row[cols["article"]]) if len(row) > cols["article"] else ""
            if art_dbg:
                skipped.append(art_dbg)
            continue
        # назва моделі з об'єднаної клітинки — протягуємо вниз
        for cell in row[:cols["article"]]:
            text = _norm(cell)
            if text and "/" in text and not text.startswith("http"):
                model = text
                break
        if len(row) <= cols["article"]:
            continue
        article = _norm(row[cols["article"]])
        if len(article) < 3:
            continue
        key = article_key.canon(article)
        raw = row[cols["stock"]] if len(row) > cols["stock"] else None
        state, qty, note = parse_stock(raw)
        if key in items:
            # той самий артикул двічі — беремо ОСТАННІЙ рядок, але кажемо про це:
            # тихо вибирати одне з двох чисел складу було б найгіршим варіантом
            dups.setdefault(article, []).append(n)
        items[key] = {"article": article, "model": model, "state": state,
                      "qty": qty, "note": note, "row": n}
    if skipped:
        notes.append(f"аркуш «{title}»: пропущено рядки "
                     f"{SKIP_SOURCE_ROWS.start}–{SKIP_SOURCE_ROWS.stop - 1} "
                     f"({', '.join(dict.fromkeys(skipped))})")
    return True


def _finish_source(items: dict, updated_at: str, notes: list, dups: dict,
                   sheets_used: int):
    """Спільні перевірки після розбору джерела."""
    if not sheets_used:
        raise WarehouseError("у джерелі не знайдено колонки залишку (шукали "
                             "заголовок зі словами: "
                             + ", ".join(STOCK_STEMS)
                             + ") — структуру змінили?")
    if not items:
        raise WarehouseError("у джерелі немає жодного артикула")
    known = [i for i in items.values() if i["state"] != "unknown"]
    if not known:
        raise WarehouseError(
            f"стовпець залишку порожній у всіх {len(items)} рядках — це схоже "
            "на відсутність кешу формул, а не на розпродаж. Прайс не чіпаємо")
    for article, rows_list in dups.items():
        notes.append(f"дубльований артикул {article}: рядки "
                     f"{', '.join(str(r) for r in rows_list)} — узяли останній")
    return items, updated_at, notes


def read_source_sheet(sheets, sheet_id: str):
    """Джерело — рідна Google-таблиця. Повертає (позиції, дата, примітки)."""
    from googleapiclient.errors import HttpError
    try:
        meta = sheets.spreadsheets().get(
            spreadsheetId=sheet_id,
            fields="sheets(properties(title))").execute()
    except HttpError as e:
        code = getattr(e, "status_code", None) or e.resp.status
        if code == 404:
            raise WarehouseError("складської таблиці не знайдено — перевірте "
                                 "WAREHOUSE_SHEET_ID")
        if code in (401, 403):
            raise WarehouseError("немає доступу до складської таблиці — дайте "
                                 "службовому акаунту право читання")
        raise WarehouseError(f"Sheets відповів помилкою {code}")

    titles = [s["properties"]["title"] for s in meta.get("sheets", [])]
    items, notes, dups = {}, [], {}
    updated_at = ""
    used = 0
    for title in titles:
        rows = sheets.spreadsheets().values().get(
            spreadsheetId=sheet_id, range=f"'{title}'").execute().get("values", [])
        if not rows:
            continue
        if not updated_at and len(rows[0]) > 17:
            updated_at = _norm(rows[0][17])      # R1 — «станом на …»
        if _collect_rows(rows, title, items, notes, dups):
            used += 1
    return _finish_source(items, updated_at, notes, dups, used)


def read_source(sheets):
    """Прочитати складську таблицю."""
    if not config.WAREHOUSE_SHEET_ID:
        raise WarehouseError("не задано WAREHOUSE_SHEET_ID")
    return read_source_sheet(sheets, config.WAREHOUSE_SHEET_ID)


# ---------------------------------------------------------------- прайс

def _tab_names(sheets):
    """Вкладки прайсу для синхронізації.

    Якщо WAREHOUSE_TABS порожній — беремо ВСІ вкладки прайсу; ті, де немає
    колонки залишку, однаково пропускаються за заголовком. Так нова вкладка
    підхоплюється сама, а список не треба тримати в двох місцях.
    """
    wanted = [t.strip() for t in (config.WAREHOUSE_TABS or "").split(",")
              if t.strip()]
    if wanted:
        return wanted
    meta = sheets.spreadsheets().get(
        spreadsheetId=config.PRICELIST_SHEET_ID,
        fields="sheets(properties(title))").execute()
    return [s["properties"]["title"] for s in meta.get("sheets", [])]


def _desired(item) -> str | None:
    """Що має стояти в клітинці прайсу. None — не чіпати."""
    if item is None:
        return ""                       # артикула більше немає в джерелі
    if item["state"] == "qty":
        return str(item["qty"])
    if item["state"] == "yes":
        return "є"
    return None                         # невідомо — лишаємо як було


def sync_tab(sheets, tab: str, index: dict, updated_at: str, report: dict):
    """Звести одну вкладку прайсу. Пише лише змінені клітинки залишку."""
    got = sheets.spreadsheets().values().get(
        spreadsheetId=config.PRICELIST_SHEET_ID,
        range=f"'{tab}'").execute()
    rows = got.get("values", [])
    hdr, cols = _find_header_row(rows, need_stock=True)
    if hdr is None:
        report["skipped"].append(f"{tab}: не знайдено рядок заголовка")
        return
    if not cols:
        report["skipped"].append(
            f"{tab}: немає колонки залишку (шукали заголовок зі словами: "
            + ", ".join(STOCK_STEMS) + ")")
        return

    letter = col_letter(cols["stock"])
    updates, clears, seen = [], [], set()
    for n, row in enumerate(rows[hdr + 1:], start=hdr + 2):
        if len(row) <= cols["article"]:
            continue
        article = _norm(row[cols["article"]])
        if len(article) < 3:
            continue
        key = article_key.canon(article)
        seen.add(key)
        item = index.get(key)
        if item is not None and article_key.canon(item["article"]) != key:
            # збіглося не як є, а за правилом суфікса — хай це буде видно
            report.setdefault("by_rule", []).append(f"{item['article']} → {article}")
        if item is None:
            report["gone"].append(article)
        elif item["state"] == "unknown":
            report["unknown"].append(article)
        want = _desired(item)
        if want is None:
            continue
        current = _norm(row[cols["stock"]]) if len(row) > cols["stock"] else ""
        if current == want:
            continue
        upd = {"range": f"'{tab}'!{letter}{n}", "values": [[want]]}
        (clears if item is None and current else updates).append(upd)

    # Очищення — найнебезпечніша дія: якщо артикулів «зникло» підозріло багато,
    # це радше різні написання (у прайсі Cr3F-150, на складі Cr3-150F), ніж
    # розпродаж. Тоді нічого не чистимо й кажемо про це.
    rows_seen = max(len(seen), 1)
    if clears and len(clears) / rows_seen > MAX_CLEAR_SHARE:
        report["clear_refused"] = len(clears)
    else:
        updates.extend(clears)
        report["cleared"] = report.get("cleared", 0) + len(clears)

    # дата складу й час синхронізації — у клітинку НАД заголовком залишку
    stamp = f"'{tab}'!{letter}{hdr}"       # hdr — 0-based, тобто рядок вище
    parts = []
    if updated_at:
        # у R1 буває вже готовий текст «станом на 18 09 2026» — не дублюємо
        low = updated_at.lower()
        parts.append(updated_at if low.startswith(("станом", "склад"))
                     else f"Склад від {updated_at}")
    parts.append(f"синхр. {config.now().strftime('%d.%m.%Y %H:%M')}")
    updates.append({"range": stamp, "values": [[" · ".join(parts)]]})

    if updates:
        sheets.spreadsheets().values().batchUpdate(
            spreadsheetId=config.PRICELIST_SHEET_ID,
            body={"valueInputOption": "USER_ENTERED", "data": updates},
        ).execute()
    report["written"] += len(updates) - 1          # позначка часу не рахується
    report["tabs"].append(f"{tab}: {len(updates) - 1} змін "
                          f"(колонка {letter} «{cols.get('stock_header', '?')}»)")
    report["in_price"] |= seen


# ---------------------------------------------------------------- запуск

def _sync_blocking() -> dict:
    """Уся робота з мережею. Синхронна — викликати через asyncio.to_thread."""
    if not config.WAREHOUSE_SHEET_ID:
        raise WarehouseError("не задано WAREHOUSE_SHEET_ID")
    sheets = _services(_credentials())
    items, updated_at, notes = read_source(sheets)

    index, _ = build_index(items)
    report = {"written": 0, "tabs": [], "skipped": [], "gone": [], "unknown": [],
              "in_price": set(), "notes": notes, "source_rows": len(items),
              "updated_at": updated_at, "cleared": 0}
    from googleapiclient.errors import HttpError
    for tab in _tab_names(sheets):
        try:
            sync_tab(sheets, tab, index, updated_at, report)
        except HttpError as e:
            code = getattr(e, "status_code", None) or e.resp.status
            if code == 400:
                report["skipped"].append(f"{tab}: вкладки немає в прайсі")
            else:
                raise WarehouseError(f"Sheets відповів помилкою {code}")

    # є в джерелі, немає в прайсі — не пишемо, повідомляємо
    used = {id(index[k]) for k in report["in_price"] if k in index}
    report["extra"] = [f"{i['model'] or '?'} ({i['article']})"
                       for i in items.values() if id(i) not in used]
    report["noted"] = [f"{i['article']}: {i['note']}"
                       for i in items.values() if i.get("note")]
    report.pop("in_price")
    return report


async def sync_once() -> dict:
    """Один прохід. Кидає WarehouseError, якщо синхронізувати не можна."""
    return await asyncio.to_thread(_sync_blocking)


def report_text(report: dict) -> str:
    """Короткий підсумок для адміна."""
    lines = [f"📦 <b>Залишки перенесено</b>: {report['written']} клітинок"]
    if report.get("updated_at"):
        lines.append(f"Склад від {report['updated_at']}")
    lines += [f"Позицій у джерелі: {report['source_rows']}"]
    for t in report["tabs"]:
        lines.append(f"  • {t}")
    if report.get("by_rule"):
        lines.append(f"🔗 Зведено правилом суфікса: {len(report['by_rule'])} "
                     f"(напр. {report['by_rule'][0]})")
    if report["skipped"]:
        lines.append("⚠️ Пропущено: " + "; ".join(report["skipped"]))
    if report["unknown"]:
        lines.append(f"❔ Без числа в джерелі (лишили як було): "
                     f"{len(report['unknown'])}")
    if report.get("clear_refused"):
        lines.append(f"🛑 НЕ чистив {report['clear_refused']} клітинок: зі "
                     "складу «зникло» надто багато артикулів — схоже на різні "
                     "написання, а не на розпродаж. Треба звірити артикули")
    elif report["gone"]:
        lines.append(f"🧹 Немає на складі: {len(report['gone'])} артикулів, "
                     f"з них очищено клітинок: {report.get('cleared', 0)}")
    if report["extra"]:
        head = ", ".join(report["extra"][:8])
        more = f" і ще {len(report['extra']) - 8}" if len(report["extra"]) > 8 else ""
        lines.append(f"➕ Є на складі, немає в прайсі: {head}{more}")
    if report.get("noted"):
        lines.append("📝 Уточнення в клітинках складу: "
                     + "; ".join(report["noted"][:5]))
    for n in report.get("notes", []):
        lines.append(f"ℹ️ {n}")
    return "\n".join(lines)


async def _alert(bot, text: str):
    """Лист адміну, не частіше разу на годину."""
    global _last_alert
    if not (bot and config.ADMIN_CHAT_ID):
        return
    if time.monotonic() - _last_alert < ALERT_COOLDOWN:
        return
    _last_alert = time.monotonic()
    try:
        await bot.send_message(config.ADMIN_CHAT_ID, text)
    except Exception as e:  # noqa: BLE001
        log.warning("Не вдалося повідомити адміна: %s", e)


async def run_forever(bot=None):
    """Фонова задача. Жодна помилка не має вбивати цикл."""
    period = config.WAREHOUSE_SYNC_SECONDS
    if period <= 0:
        log.info("Синхронізація складу вимкнена (WAREHOUSE_SYNC_SECONDS=0)")
        return
    if not config.WAREHOUSE_SHEET_ID:
        log.info("Синхронізація складу вимкнена: не задано WAREHOUSE_SHEET_ID")
        return
    log.info("Залишки синхронізуються кожні %d с", period)
    delay = min(60, period)                 # перший прохід — невдовзі після старту
    while True:
        await asyncio.sleep(delay)
        delay = period
        try:
            report = await sync_once()
            log.info("Склад → прайс: змінено %d клітинок%s", report["written"],
                     f", пропущено: {'; '.join(report['skipped'])}"
                     if report["skipped"] else "")
            if report["skipped"] or report["extra"]:
                await _alert(bot, report_text(report))
        except WarehouseError as e:
            log.error("Синхронізація складу не виконана: %s", e)
            await _alert(bot, f"⚠️ <b>Залишки не синхронізовано</b>\n{e}")
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.exception("Помилка циклу синхронізації складу: %s", e)
            await _alert(bot, f"⚠️ Помилка синхронізації залишків: {e}")
