"""Перенесення залишків зі складської таблиці в наш прайс.

ДЖЕРЕЛО — .xlsx у Drive (не рідна Google-таблиця), тому читаємо через
files().get_media() і openpyxl. ПРИЙМАЧ — наш прайс, рідна Google-таблиця,
пишемо через Sheets API.

Прайс — джерело правди для цін. Тому пишемо ТІЛЬКИ в колонку «К-сть на складі»,
знайдену за заголовком, і ніколи за індексом: у кожній вкладці своя розкладка
(заголовок то в 2-му, то в 4-му рядку, артикул то в D, то в H, то в J).

Що може піти не так із чужим файлом — і що ми робимо:
  • data_only=True віддає КЕШОВАНІ значення формул. Якщо весь стовпець залишку
    порожній, це майже напевно відсутність кешу, а не «все розпродано», —
    відмовляємось від синхронізації, нулів не пишемо;
  • об'єднані клітинки: назва моделі стоїть лише в першому рядку групи, решта
    читаються як None — протягуємо останню побачену вниз, як catalog_parser;
  • немає заголовка «К-сть на складі» — вкладку пропускаємо з помилкою:
    краще вчорашні числа, ніж сміття;
  • файл зник, доступу немає, аркуш перейменували — ERROR і лист адміну, не
    частіше разу на годину.

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

# Як може називатися колонка залишку. Звіряємо ПОВНИЙ текст заголовка, а не
# підрядок: у прайсі є цінова колонка «Дроп 3 — товар у наявності на складі»,
# і пошук за «склад» записав би залишки просто в ціни.
STOCK_HEADERS = {
    "к-сть на складі", "кількість на складі", "кількість", "кількістсь",
    "наявність", "залишок", "залишки",
}
# заголовок із цими словами — не залишок, хай там що ще в ньому написано
PRICE_WORDS = ("ціна", "дроп", "price", "грн", "euro", "євро")

ARTICLE_HEADER = "article"

# рядки-дублікат cr6custom із неправильними наявностями
SKIP_SOURCE_ROWS = range(118, 128)

# «є» без числа
YES_WORDS = {"так", "є", "yes", "+", "да", "in stock", "у наявності"}

SCOPES = ["https://www.googleapis.com/auth/drive.readonly",
          "https://www.googleapis.com/auth/spreadsheets"]

# щоб не засипати адміна: не частіше разу на годину
ALERT_COOLDOWN = 3600
_last_alert = 0.0


class WarehouseError(Exception):
    """Синхронізація неможлива — краще нічого не робити, ніж зіпсувати прайс."""


# ---------------------------------------------------------------- допоміжне

def _norm(s) -> str:
    return re.sub(r"\s+", " ", str(s if s is not None else "")).strip()


def parse_stock(value):
    """Клітинка залишку → (стан, число). Стани: qty / yes / unknown."""
    s = _norm(value).lower()
    if not s:
        return "unknown", None
    if s in YES_WORDS:
        return "yes", None
    cleaned = re.sub(r"[^\d,.-]", "", s.replace("\xa0", "").replace(" ", ""))
    cleaned = cleaned.replace(",", ".")
    try:
        n = float(cleaned)
    except ValueError:
        # текст на кшталт «під замовлення» — не число і не «є»
        return "yes" if any(w in s for w in YES_WORDS) else "unknown", None
    if n < 0:
        return "unknown", None
    return "qty", int(n)


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
            if c in STOCK_HEADERS and not any(w in c for w in PRICE_WORDS):
                cols["stock"] = j
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
    try:
        from googleapiclient.discovery import build
    except ImportError as e:  # noqa: BLE001
        raise WarehouseError(f"немає бібліотеки google-api-python-client: {e}")
    return (build("drive", "v3", credentials=creds, cache_discovery=False),
            build("sheets", "v4", credentials=creds, cache_discovery=False))


def download_source(drive, file_id: str) -> bytes:
    """Завантажити .xlsx. get_media, а НЕ export_media: файл не Google-формату."""
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaIoBaseDownload
    buf = io.BytesIO()
    try:
        request = drive.files().get_media(fileId=file_id)
        downloader = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
    except HttpError as e:
        code = getattr(e, "status_code", None) or e.resp.status
        if code == 404:
            raise WarehouseError("файл складу не знайдено (видалено або "
                                 "змінився WAREHOUSE_FILE_ID)")
        if code in (401, 403):
            raise WarehouseError("немає доступу до файла складу — дайте "
                                 "службовому акаунту право читання")
        raise WarehouseError(f"Drive відповів помилкою {code}")
    return buf.getvalue()


def parse_source(blob: bytes):
    """Розібрати .xlsx. Повертає (позиції, дата оновлення, примітки).

    позиції = {канонічний_артикул: {"article", "model", "state", "qty"}}
    """
    try:
        import openpyxl
    except ImportError as e:  # noqa: BLE001
        raise WarehouseError(f"немає бібліотеки openpyxl: {e}")

    wb = openpyxl.load_workbook(io.BytesIO(blob), data_only=True)
    items, notes = {}, []
    updated_at = ""
    sheets_used = 0

    for ws in wb.worksheets:
        rows = [[c.value for c in row] for row in ws.iter_rows()]
        if not rows:
            continue
        # R1 — дата оновлення складу (18-та колонка першого рядка)
        if not updated_at and len(rows[0]) > 17:
            updated_at = _norm(rows[0][17])
        hdr, cols = _find_header_row(rows, need_stock=True)
        if hdr is None or not cols:
            continue
        sheets_used += 1
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
            raw = row[cols["stock"]] if len(row) > cols["stock"] else None
            state, qty = parse_stock(raw)
            items[article_key.canon(article)] = {
                "article": article, "model": model, "state": state, "qty": qty,
            }
        if skipped:
            notes.append(f"аркуш «{ws.title}»: пропущено рядки "
                         f"{SKIP_SOURCE_ROWS.start}–{SKIP_SOURCE_ROWS.stop - 1} "
                         f"({', '.join(skipped[:6])})")

    if not sheets_used:
        raise WarehouseError("у файлі складу не знайдено колонки залишку "
                             "(шукали: " + ", ".join(sorted(STOCK_HEADERS))
                             + ") — структуру змінили?")
    if not items:
        raise WarehouseError("у файлі складу немає жодного артикула")

    known = [i for i in items.values() if i["state"] != "unknown"]
    if not known:
        # data_only=True без кешу формул віддає суцільні None
        raise WarehouseError(
            f"стовпець залишку порожній у всіх {len(items)} рядках — це схоже "
            "на відсутність кешу формул, а не на розпродаж. Прайс не чіпаємо; "
            "відкрийте файл складу в Excel, збережіть і повторіть")
    return items, updated_at, notes


# ---------------------------------------------------------------- прайс

def _tab_names():
    """Вкладки прайсу, які синхронізуємо (config.WAREHOUSE_TABS)."""
    wanted = [t.strip() for t in (config.WAREHOUSE_TABS or "").split(",")
              if t.strip()]
    known = [name for _, name in config.PRICELIST_TABS]
    return [t for t in wanted if t in known] or wanted


def _desired(item) -> str | None:
    """Що має стояти в клітинці прайсу. None — не чіпати."""
    if item is None:
        return ""                       # артикула більше немає в джерелі
    if item["state"] == "qty":
        return str(item["qty"])
    if item["state"] == "yes":
        return "є"
    return None                         # невідомо — лишаємо як було


def sync_tab(sheets, tab: str, items: dict, updated_at: str, report: dict):
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
        report["skipped"].append(f"{tab}: немає колонки «К-сть на складі»")
        return

    letter = col_letter(cols["stock"])
    updates, seen = [], set()
    for n, row in enumerate(rows[hdr + 1:], start=hdr + 2):
        if len(row) <= cols["article"]:
            continue
        article = _norm(row[cols["article"]])
        if len(article) < 3:
            continue
        key = article_key.canon(article)
        seen.add(key)
        item = items.get(key)
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
        updates.append({"range": f"'{tab}'!{letter}{n}",
                        "values": [[want]]})

    # дата складу й час синхронізації — у клітинку НАД заголовком залишку
    stamp = f"'{tab}'!{letter}{hdr}"       # hdr — 0-based, тобто рядок вище
    parts = []
    if updated_at:
        parts.append(f"Склад від {updated_at}")
    parts.append(f"синхр. {config.now().strftime('%d.%m.%Y %H:%M')}")
    updates.append({"range": stamp, "values": [[" · ".join(parts)]]})

    if updates:
        sheets.spreadsheets().values().batchUpdate(
            spreadsheetId=config.PRICELIST_SHEET_ID,
            body={"valueInputOption": "USER_ENTERED", "data": updates},
        ).execute()
    report["written"] += len(updates) - 1          # позначка часу не рахується
    report["tabs"].append(f"{tab}: {len(updates) - 1} змін")
    report["in_price"] |= seen


# ---------------------------------------------------------------- запуск

def _sync_blocking() -> dict:
    """Уся робота з мережею. Синхронна — викликати через asyncio.to_thread."""
    if not config.WAREHOUSE_FILE_ID:
        raise WarehouseError("не задано WAREHOUSE_FILE_ID")
    creds = _credentials()
    drive, sheets = _services(creds)
    blob = download_source(drive, config.WAREHOUSE_FILE_ID)
    items, updated_at, notes = parse_source(blob)

    report = {"written": 0, "tabs": [], "skipped": [], "gone": [], "unknown": [],
              "in_price": set(), "notes": notes, "source_rows": len(items),
              "updated_at": updated_at}
    from googleapiclient.errors import HttpError
    for tab in _tab_names():
        try:
            sync_tab(sheets, tab, items, updated_at, report)
        except HttpError as e:
            code = getattr(e, "status_code", None) or e.resp.status
            if code == 400:
                report["skipped"].append(f"{tab}: вкладки немає в прайсі")
            else:
                raise WarehouseError(f"Sheets відповів помилкою {code}")

    # є в джерелі, немає в прайсі — не пишемо, повідомляємо
    report["extra"] = [f"{i['model'] or '?'} ({i['article']})"
                       for k, i in items.items() if k not in report["in_price"]]
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
    if report["skipped"]:
        lines.append("⚠️ Пропущено: " + "; ".join(report["skipped"]))
    if report["unknown"]:
        lines.append(f"❔ Без числа в джерелі (лишили як було): "
                     f"{len(report['unknown'])}")
    if report["gone"]:
        lines.append(f"🧹 Зникли з джерела, очищено: {len(report['gone'])}")
    if report["extra"]:
        head = ", ".join(report["extra"][:8])
        more = f" і ще {len(report['extra']) - 8}" if len(report["extra"]) > 8 else ""
        lines.append(f"➕ Є на складі, немає в прайсі: {head}{more}")
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
    if not config.WAREHOUSE_FILE_ID:
        log.info("Синхронізація складу вимкнена: не задано WAREHOUSE_FILE_ID")
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
