"""Таблиця замовлень напряму через Google Sheets API, без Apps Script.

ЧОМУ. Весь обмін ішов через вебхук Apps Script, і це давало постійні збої не
через баги, а через конструкцію:

  • відповідь приходить у два кроки («/exec» → переадресація на
    script.googleusercontent.com), і другий крок Google інколи не віддає —
    звідси HTTP 404 на цілком успішному записі;
  • виконання скрипта для одного користувача Google СЕРІАЛІЗУЄ, тож наші
    запити стають у чергу (в логах траплялося виконання на 22,6 с);
  • саме виконання коштує 1–2 с, бо скрипт мусить відкрити таблицю.

Sheets API не має ні переадресації, ні черги: один HTTP-запит — одна
відповідь, 0,2–0,4 с. Сервісний акаунт і бібліотеки вже є в проєкті —
warehouse_sync пише ними прайс.

ЩО ТУТ Є. Усі ЧИТАННЯ (одним batchGet замість чотирьох запитів) і операції
з ЗАЛИШКАМИ — те, на чому бот збоїв найчастіше.

ЧОГО ТУТ СВІДОМО НЕМА. Запис замовлень і статусів ТТН лишається у вебхуці:
скрипт на цих записах ще й переносить рядки в таблицю дропшипера
(dropshipper_export.gs). Якби ми писали замовлення напряму, експорт у Лани
просто перестав би оновлюватися.

ЗАМОК. В Apps Script був LockService. Тут його немає, тому операції із
залишками серіалізуємо замком у процесі бота — цього достатньо, бо колонку
«Зарезервовано» пише ТІЛЬКИ бот, і він один. Вузьке місце лишається одне:
кілька секунд під час редеплою, коли старий і новий контейнер живуть разом.

УВІМКНЕННЯ. Потрібні ORDERS_SHEET_ID і GOOGLE_SERVICE_ACCOUNT_JSON, а
сервісний акаунт має бути РЕДАКТОРОМ таблиці замовлень. Якщо чогось немає,
enabled() віддає False і бот працює через вебхук, як раніше.
"""
import asyncio
import json
import logging
import os

import article_key
import config
import perf

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

ORDERS_TAB = "Замовлення"
DROPS_TAB = "Дропшипери"
ALIAS_TAB = "Назви товарів"
STOCK_TAB = "Залишки дропшиперів"

# Порядок колонок аркуша «Замовлення» — той самий ORDER_KEYS, що в скрипті.
# Нові колонки додаються ЛИШЕ в кінець, інакше поїдуть наявні дані.
ORDER_KEYS = ["order_no", "created_at", "source", "dropshipper_id", "dropshipper",
              "article", "product", "size", "qty", "price_drop", "payment",
              "recipient_fio", "recipient_phone", "city", "warehouse", "ttn",
              "status", "comment", "sale_price", "prepaid", "cod_amount",
              "delivery", "due_amount", "payment_proof", "np_status",
              "updated_at"]

# «Залишки дропшиперів»: номери колонок (1 = A)
STOCK_COLS = {"tg_id": 1, "dropshipper": 2, "article": 3, "name": 4, "price": 5,
              "allocated": 6, "reserved": 7, "delivered": 8, "available": 9,
              "updated": 10}

DROP_TIER_COL = 7

# статуси, після яких за накладною вже нема чого стежити (входження, не початок)
NP_FINAL = ("отримано", "одержано", "відмов", "поверн", "видалено",
            "припинено зберігання", "резерв знято")

# Транзієнтні відповіді Google: ліміт і внутрішні збої. Читання повторюємо
# спокійно, запис — теж, бо він іде під замком і перечитує дані заново.
RETRY_CODES = (429, 500, 502, 503, 504)
RETRY_PAUSE = 2
ATTEMPTS = 3


class ApiError(Exception):
    """Помилка з текстом для людини (їде в чат адміна)."""


def enabled() -> bool:
    return bool(_sheet_id() and (config.GOOGLE_SERVICE_ACCOUNT_JSON or "").strip())


def settings_report() -> list:
    """Що бот бачить у змінних середовища — рядками для адміна.

    Потрібне, бо «прямий доступ вимкнено» нічого не каже про причину: змінну
    могли назвати інакше, задати в іншому сервісі Railway або не передеплоїти.
    Пошту сервісного акаунта показуємо окремо: саме її треба додати редактором
    таблиці, і саме тут видно, чи це той акаунт, якому ви дали доступ.
    """
    lines = []
    sid = _sheet_id()
    lines.append(f"ORDERS_SHEET_ID: {'✅ ' + _short_id(sid) if sid else '❌ не задано'}")

    raw = (config.GOOGLE_SERVICE_ACCOUNT_JSON or "").strip()
    if not raw:
        lines.append("GOOGLE_SERVICE_ACCOUNT_JSON: ❌ не задано")
    else:
        email = project = ""
        try:
            info = json.load(open(raw, encoding="utf-8")) if os.path.exists(raw) \
                else json.loads(raw)
            email = str(info.get("client_email") or "")
            project = str(info.get("project_id") or "")
        except (OSError, ValueError) as e:
            lines.append(f"GOOGLE_SERVICE_ACCOUNT_JSON: ⚠️ не читається ({e})")
        if email:
            lines.append(f"GOOGLE_SERVICE_ACCOUNT_JSON: ✅ {email}")
            if project:
                lines.append(f"проєкт Google: {project}")

    export = (config.DROPSHIPPER_EXPORT_SHEETS_JSON or "").strip()
    if not export:
        lines.append("DROPSHIPPER_EXPORT_SHEETS_JSON: ❌ не задано — "
                     "замовлення не дублюються дропшиперу")
    else:
        try:
            n = len(json.loads(export) or {})
            lines.append(f"DROPSHIPPER_EXPORT_SHEETS_JSON: ✅ таблиць {n}")
        except ValueError as e:
            lines.append(f"DROPSHIPPER_EXPORT_SHEETS_JSON: ⚠️ не JSON ({e})")

    hook = "✅" if config.SHEET_WEBHOOK_URL else "❌"
    lines.append(f"SHEET_WEBHOOK_URL (запас через Apps Script): {hook}")
    return lines


def _sheet_id() -> str:
    return (getattr(config, "ORDERS_SHEET_ID", "") or "").strip()


# ─── служба ──────────────────────────────────────────────────────────────────
_service = None
_lock: asyncio.Lock | None = None
_lock_loop = None


_locks: dict = {}
_locks_loop = None


def _named_lock(name: str) -> asyncio.Lock:
    """Замок на «читаю-міняю-пишу». Створюється в робочому циклі.

    У Sheets API немає LockService, тож операції, де ми читаємо аркуш і одразу
    пишемо, серіалізуємо в себе. Цього достатньо: таблицю пише лише бот.
    """
    global _locks, _locks_loop
    loop = asyncio.get_running_loop()
    if _locks_loop is not loop:
        _locks = {}
        _locks_loop = loop
    if name not in _locks:
        _locks[name] = asyncio.Lock()
    return _locks[name]


def _stock_lock() -> asyncio.Lock:
    return _named_lock("stock")


def _orders_lock() -> asyncio.Lock:
    return _named_lock("orders")


def _build():
    """Клієнт Sheets. Синхронний — викликається лише в окремому потоці."""
    global _service
    if _service is not None:
        return _service
    raw = (config.GOOGLE_SERVICE_ACCOUNT_JSON or "").strip()
    if not raw:
        raise ApiError("не задано GOOGLE_SERVICE_ACCOUNT_JSON")
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
    except ImportError as e:  # noqa: BLE001
        raise ApiError(f"немає бібліотек Google: {e}")
    try:
        info = json.load(open(raw, encoding="utf-8")) if os.path.exists(raw) \
            else json.loads(raw)
    except (OSError, ValueError) as e:
        raise ApiError(f"GOOGLE_SERVICE_ACCOUNT_JSON не читається: {e}")
    creds = service_account.Credentials.from_service_account_info(
        info, scopes=SCOPES)
    _service = build("sheets", "v4", credentials=creds, cache_discovery=False)
    return _service


def _status(e) -> int:
    for attr in ("status_code", "resp"):
        obj = getattr(e, attr, None)
        code = getattr(obj, "status", None) if attr == "resp" else obj
        if code:
            try:
                return int(code)
            except (TypeError, ValueError):
                pass
    return 0


def _human(e) -> str:
    """Текст помилки Google без простирадла HTML і без ID таблиці."""
    code = _status(e)
    text = str(e)
    if code == 403:
        return ("сервісний акаунт не має доступу до таблиці — додайте його "
                "редактором")
    if code == 404:
        return "таблицю не знайдено (перевірте ID)"
    if code == 400 and "Unable to parse range" in text:
        return "в таблиці немає потрібного аркуша (перевірте назви аркушів)"
    short = text.split("returned", 1)[-1].strip(' "').split('".', 1)[0]
    return (short or type(e).__name__)[:160]


async def _run(fn, label: str):
    """Виконати синхронний виклик Google у потоці, з повторами на 429/5xx."""
    last = None
    for attempt in range(1, ATTEMPTS + 1):
        try:
            async with perf.timed(f"Sheets {label}"):
                return await asyncio.to_thread(fn)
        except Exception as e:  # noqa: BLE001
            last = e
            if _status(e) in RETRY_CODES and attempt < ATTEMPTS:
                log.warning("Sheets %s: %s — спроба %d з %d",
                            label, _human(e), attempt + 1, ATTEMPTS)
                await asyncio.sleep(RETRY_PAUSE * attempt)
                continue
            raise ApiError(_human(e)) from e
    raise ApiError(_human(last))


def _num(value) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value or "").replace("\xa0", "").replace(" ", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return 0.0


def _int(value) -> int:
    return int(_num(value))


def _tidy(value: float):
    """Цілі числа пишемо цілими: у таблиці «4», а не «4,0»."""
    return int(value) if float(value).is_integer() else value


def _cell(row: list, col: int):
    """Значення колонки (1 = A); порожній хвіст рядка Google просто не віддає."""
    return row[col - 1] if len(row) >= col else ""


def _text(row: list, col: int) -> str:
    v = _cell(row, col)
    return str(v).strip() if v is not None else ""


# ─── читання ─────────────────────────────────────────────────────────────────
async def read_all():
    """Дропшипери + назви + залишки + максимальний номер — одним запитом.

    Повертає (дані, помилка) у тому самому вигляді, що й what=bootstrap.
    """
    ranges = [f"{DROPS_TAB}!A2:G", f"{ALIAS_TAB}!A2:C",
              f"{STOCK_TAB}!A2:J", f"{ORDERS_TAB}!A2:A"]

    def call():
        return _build().spreadsheets().values().batchGet(
            spreadsheetId=_sheet_id(), ranges=ranges,
            valueRenderOption="UNFORMATTED_VALUE").execute()

    try:
        resp = await _run(call, "batchGet")
    except ApiError as e:
        return None, str(e)
    got = [v.get("values") or [] for v in (resp.get("valueRanges") or [])]
    while len(got) < 4:
        got.append([])
    drops, alias, stock_rows, order_nos = got
    return {
        "dropshippers": _drops(drops),
        "aliases": _aliases(alias),
        "stock": _stock(stock_rows),
        "maxorder": max([_int(r[0]) for r in order_nos if r and _num(r[0])] or [0]),
    }, None


def _drops(rows) -> list:
    out = []
    for r in rows:
        if not _text(r, 1):
            continue
        out.append({"tg_id": _text(r, 1), "name": _text(r, 2),
                    "username": _text(r, 3),
                    "status": _text(r, 4) or "схвалений",
                    "tier": _text(r, DROP_TIER_COL)})
    return out


def _aliases(rows) -> list:
    return [{"tg_id": _text(r, 1), "key": _text(r, 2), "name": _text(r, 3)}
            for r in rows if _text(r, 1) and _text(r, 2) and _text(r, 3)]


def _stock(rows) -> list:
    out = []
    for r in rows:
        art = _text(r, STOCK_COLS["article"])
        if not art:
            continue
        out.append({
            "tg_id": _text(r, STOCK_COLS["tg_id"]),
            "dropshipper": _text(r, STOCK_COLS["dropshipper"]),
            "article": art,
            # назву обрізаємо: вона потрапляє в опис ТТН на паперовій накладній
            "name": _text(r, STOCK_COLS["name"]),
            "allocated": _cell(r, STOCK_COLS["allocated"]),
            "reserved": _cell(r, STOCK_COLS["reserved"]),
            "delivered": _cell(r, STOCK_COLS["delivered"]),
            "available": _cell(r, STOCK_COLS["available"]),
        })
    return out


async def _order_rows():
    """Усі рядки «Замовлення» як словники (дати — текстом, як у скрипті)."""
    def call():
        return _build().spreadsheets().values().get(
            spreadsheetId=_sheet_id(), range=f"{ORDERS_TAB}!A2:Z",
            valueRenderOption="UNFORMATTED_VALUE",
            dateTimeRenderOption="FORMATTED_STRING").execute()

    resp = await _run(call, f"get {ORDERS_TAB}")
    out = []
    for r in resp.get("values") or []:
        if not _text(r, 1):
            continue
        out.append({k: _cell(r, i + 1) for i, k in enumerate(ORDER_KEYS)})
    return out


async def read_orders(user_id, limit: int = 10):
    """Останні замовлення дропшипера; user_id 0/None — усі."""
    try:
        rows = await _order_rows()
    except ApiError as e:
        return None, str(e)
    want = str(user_id or "").strip()
    out = []
    for r in reversed(rows):
        if want and str(r.get("dropshipper_id") or "").strip() != want:
            continue
        out.append(r)
        if len(out) >= limit:
            break
    return out, None


async def read_ttns():
    """Замовлення з ТТН, статус яких ще не кінцевий."""
    try:
        rows = await _order_rows()
    except ApiError as e:
        return None, str(e)
    out = []
    for r in rows:
        ttn = str(r.get("ttn") or "").strip()
        if not ttn:
            continue
        st = str(r.get("np_status") or "").strip().lower()
        if any(final in st for final in NP_FINAL):
            continue
        out.append({"order_no": r.get("order_no"),
                    "created_at": r.get("created_at"),
                    "ttn": ttn,
                    "tg_id": str(r.get("dropshipper_id") or "").strip(),
                    "article": str(r.get("article") or "").strip(),
                    "qty": r.get("qty"),
                    "phone": str(r.get("recipient_phone") or "").strip(),
                    "np_status": str(r.get("np_status") or "").strip()})
    return out, None


async def read_max_order_no():
    data, err = await read_all()
    if err:
        return 0, err
    return data["maxorder"], None


# ─── самоперевірка ───────────────────────────────────────────────────────────
# Очікувані заголовки. Якщо в таблиці переставлять колонки, бот читав би
# сусідню — тому перед тим, як довіряти прямому доступу, звіряємо рядок 1.
EXPECT = {
    ORDERS_TAB: ["№", "Дата", "Джерело", "ID дропшипера", "Дропшипер",
                 "Артикул", "Товар", "Розмір", "К-сть", "Дроп-ціна", "Оплата",
                 "ПІБ отримувача", "Телефон", "Місто", "Відділення / адреса",
                 "ТТН", "Статус", "Коментар", "Ціна продажу", "Передплата",
                 "При отриманні", "Доставка", "На рахунок", "Чек",
                 "Статус Nova Poshta", "Оновлено"],
    DROPS_TAB: ["Telegram ID", "Ім'я", "Username", "Статус", "Дата",
                "Хто схвалив", "Тариф"],
    ALIAS_TAB: ["Telegram ID", "Артикул або модель", "Своя назва"],
}

# «Залишки дропшиперів» ведуть руками, точного списку заголовків у коді немає,
# тож тут перевіряємо не дослівну назву, а СУТЬ колонки за ключовим словом —
# інакше перевірка кричала б на кожне перейменування.
STOCK_EXPECT = {1: ("telegram", "id"), 3: ("артикул",), 6: ("видан", "виділ",
                "передзамов", "замовлен"), 7: ("резерв",), 8: ("отрим", "видач",
                "доставлен"), 9: ("доступ",)}


async def check():
    """Звірити живі заголовки з очікуваними. Повертає (рядки звіту, усе_ок).

    Порівнюємо лише стільки колонок, скільки очікуємо, і лише їхній ПОРЯДОК:
    назви в таблиці ведуть руками, тож зайві колонки праворуч не страшні, а
    переставлена колонка — страшна.
    """
    tabs = list(EXPECT) + [STOCK_TAB]
    ranges = [f"{tab}!1:1" for tab in tabs]

    def call():
        return _build().spreadsheets().values().batchGet(
            spreadsheetId=_sheet_id(), ranges=ranges,
            valueRenderOption="UNFORMATTED_VALUE").execute()

    try:
        resp = await _run(call, "batchGet заголовки")
    except ApiError as e:
        return [f"❌ {e}"], False
    lines = []
    ok = True
    got = resp.get("valueRanges") or []
    heads = []
    for i in range(len(tabs)):
        row = (got[i].get("values") or [[]])[0] if i < len(got) else []
        heads.append([str(h).strip() for h in row])
    for i, (tab, want) in enumerate(EXPECT.items()):
        head = heads[i]
        bad = [(n + 1, want[n], head[n] if n < len(head) else "—")
               for n in range(len(want))
               if (head[n] if n < len(head) else "") != want[n]]
        if bad:
            ok = False
            lines.append(f"❌ <b>{tab}</b>: не збігається "
                         + ", ".join(f"колонка {n} — очікували «{w}», "
                                     f"а там «{g}»" for n, w, g in bad[:4]))
        else:
            lines.append(f"✅ <b>{tab}</b>: {len(want)} колонок на місці")

    stock_head = heads[-1]
    bad = []
    for col, words in STOCK_EXPECT.items():
        got_name = (stock_head[col - 1] if col <= len(stock_head) else "").lower()
        if not any(w in got_name for w in words):
            bad.append((col, words[0], got_name or "—"))
    if bad:
        ok = False
        lines.append(f"❌ <b>{STOCK_TAB}</b>: "
                     + ", ".join(f"колонка {c} має бути про «{w}», "
                                 f"а там «{g}»" for c, w, g in bad))
    else:
        lines.append(f"✅ <b>{STOCK_TAB}</b>: артикул, резерв, отримано й "
                     f"доступно на своїх колонках")
    return lines, ok


async def counts():
    """Скільки рядків бачимо напряму — щоб побачити, що читаємо саме те."""
    data, err = await read_all()
    if err:
        return f"❌ {err}"
    return (f"дропшиперів {len(data['dropshippers'])}, "
            f"назв {len(data['aliases'])}, залишків {len(data['stock'])}, "
            f"максимальний номер замовлення {data['maxorder']}")


def _short_id(sheet_id: str) -> str:
    """ID таблиці для логів — без повного значення."""
    s = str(sheet_id or "")
    return (s[:6] + "…" + s[-4:]) if len(s) > 12 else s


async def _read_table(sheet_id: str, tab: str, width: int) -> list:
    """Рядки аркуша з другого (дані під заголовком). Один запит."""
    last = _col_letter(width)

    def call():
        return _build().spreadsheets().values().get(
            spreadsheetId=sheet_id, range=f"{tab}!A2:{last}",
            valueRenderOption="UNFORMATTED_VALUE",
            dateTimeRenderOption="FORMATTED_STRING").execute()

    return (await _run(call, f"get {tab}")).get("values") or []


def _index_rows(rows: list, key_col: int, art_col: int) -> dict:
    """{(номер, канонічний артикул): номер рядка у таблиці}.

    Ключ складений: у замовленні з кількох позицій рядків із тим самим
    номером кілька, і пошук лише за номером перезаписував би перший раз за
    разом. Артикул зіставляємо канонічним ключем — у прайсі є кириличні
    двійники літер.
    """
    out = {}
    for n, r in enumerate(rows):
        no = _text(r, key_col)
        if not no:
            continue
        out.setdefault((no, article_key.canon(_text(r, art_col))), n + 2)
    return out


_known_tabs: set = set()


async def ensure_tab(sheet_id: str, tab: str, headers: list):
    """Створити аркуш із заголовками, якщо його ще немає.

    Таблицю дропшипера раніше готував Apps Script; тепер це робимо ми, бо
    інакше перший же запис упав би на «Unable to parse range».
    """
    if (sheet_id, tab) in _known_tabs:
        return False

    def meta():
        return _build().spreadsheets().get(
            spreadsheetId=sheet_id, fields="sheets.properties.title").execute()

    titles = [s["properties"]["title"]
              for s in (await _run(meta, "get аркуші")).get("sheets") or []]
    if tab in titles:
        _known_tabs.add((sheet_id, tab))
        return False

    def add():
        return _build().spreadsheets().batchUpdate(
            spreadsheetId=sheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": tab}}}]}
        ).execute()

    await _run(add, f"addSheet {tab}")

    def head():
        return _build().spreadsheets().values().update(
            spreadsheetId=sheet_id, range=f"{tab}!A1",
            valueInputOption="USER_ENTERED", body={"values": [headers]}).execute()

    await _run(head, f"заголовки {tab}")
    log.info("У таблиці %s створено аркуш «%s»", _short_id(sheet_id), tab)
    _known_tabs.add((sheet_id, tab))
    return True


async def write_rows(sheet_id: str, tab: str, headers: list,
                     updates: list, appends: list):
    """Оновити наявні рядки й дописати нові. Два запити максимум."""
    if updates:
        last = _col_letter(len(headers))
        data = [{"range": f"{tab}!A{row}:{last}{row}", "values": [vals]}
                for row, vals in updates]

        def write():
            return _build().spreadsheets().values().batchUpdate(
                spreadsheetId=sheet_id,
                body={"valueInputOption": "USER_ENTERED", "data": data}).execute()

        await _run(write, f"batchUpdate {tab}")

    if appends:
        def add():
            return _build().spreadsheets().values().append(
                spreadsheetId=sheet_id, range=f"{tab}!A1",
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body={"values": appends}).execute()

        await _run(add, f"append {tab}")


async def rows_index(sheet_id: str, tab: str, key_col: int, art_col: int,
                     width: int, headers: list | None = None) -> dict:
    """Індекс рядків чужого аркуша (для експорту). Створює аркуш, якщо треба."""
    if await ensure_tab(sheet_id, tab, headers or []):
        return {}
    try:
        rows = await _read_table(sheet_id, tab, width)
    except ApiError:
        # аркуш міг зникнути після того, як ми його побачили (перейменували,
        # видалили) — забуваємо кеш і створюємо наново, замовлення через це
        # втрачати не станемо
        _known_tabs.discard((sheet_id, tab))
        if await ensure_tab(sheet_id, tab, headers or []):
            return {}
        raise
    return _index_rows(rows, key_col, art_col)


async def write_order_rows(rows: list[dict]):
    """Записати рядки замовлення в головну таблицю. Повертає (скільки, помилка).

    Ідемпотентно: рядок із тим самим номером і артикулом перезаписується, а не
    дублюється — повторна спроба після обриву зв'язку нічого не псує.

    На відміну від Apps Script, якого поля в переданому рядку немає — лишаємо
    те, що стоїть у таблиці. Інакше /ttn, що надсилає рядок без «Статусу Nova
    Poshta», витирав би статус, який уже проставив фоновий цикл.
    """
    if not rows:
        return 0, None
    async with _orders_lock():
        try:
            table = await _read_table(_sheet_id(), ORDERS_TAB, len(ORDER_KEYS))
        except ApiError as e:
            return 0, str(e)
        index = _index_rows(table, ORDER_KEYS.index("order_no") + 1,
                            ORDER_KEYS.index("article") + 1)
        updates, appends = [], []
        for r in rows:
            key = (str(r.get("order_no") or "").strip(),
                   article_key.canon(r.get("article")))
            row_no = index.get(key)
            old = table[row_no - 2] if row_no else []
            values = []
            for i, field in enumerate(ORDER_KEYS):
                if field in r and r[field] not in (None, ""):
                    values.append(r[field])
                else:                       # нема чого писати — лишаємо старе
                    values.append(_cell(old, i + 1) if row_no else "")
            if row_no:
                updates.append((row_no, values))
            else:
                appends.append(values)
                index[key] = -1             # друга позиція того ж замовлення
        try:
            await write_rows(_sheet_id(), ORDERS_TAB, ORDER_KEYS,
                             updates, appends)
        except ApiError as e:
            return 0, str(e)
    return len(rows), None


# ─── залишки ─────────────────────────────────────────────────────────────────
def _col_letter(col: int) -> str:
    out = ""
    while col:
        col, rest = divmod(col - 1, 26)
        out = chr(65 + rest) + out
    return out


async def stock_op_many(op: str, user_id, items: list[dict]):
    """reserve_many / release_many — усі позиції «все або нічого».

    Повторює логіку скрипта: спершу перевіряємо ВСІ позиції проти даних,
    прочитаних у цьому ж виклику, і лише тоді пишемо. Інакше між перевіркою
    й записом встигло б утнутися чуже замовлення, і резерв розʼїхався б.

    Повертає (дані, помилка) у форматі скрипта.
    """
    single = op in ("reserve", "receive", "release")
    kind = op.replace("_many", "")
    tg = str(user_id).strip()
    want = [{"article": str(i["article"]), "qty": int(i["qty"])} for i in items]
    if not want:
        return {"ok": True, "items": []}, None

    async with _stock_lock():
        def read():
            return _build().spreadsheets().values().get(
                spreadsheetId=_sheet_id(), range=f"{STOCK_TAB}!A2:J",
                valueRenderOption="UNFORMATTED_VALUE").execute()

        try:
            rows = (await _run(read, f"get {STOCK_TAB}")).get("values") or []
        except ApiError as e:
            return None, str(e)

        # 1) знайти рядки (артикул зіставляємо канонічним ключем з обох боків)
        planned: dict[str, dict] = {}
        unknown = []
        for it in want:
            key = article_key.canon(it["article"])
            state = planned.get(key)
            if state is None:
                found = None
                for n, r in enumerate(rows):
                    if (_text(r, STOCK_COLS["tg_id"]) == tg
                            and article_key.canon(
                                _text(r, STOCK_COLS["article"])) == key):
                        found = (n + 2, r)
                        break
                if found is None:
                    unknown.append(it["article"])
                    continue
                row_no, r = found
                state = {"row": row_no, "article": it["article"], "asked": 0,
                         "allocated": _num(_cell(r, STOCK_COLS["allocated"])),
                         "reserved": _num(_cell(r, STOCK_COLS["reserved"])),
                         "delivered": _num(_cell(r, STOCK_COLS["delivered"]))}
                planned[key] = state
            state["asked"] += it["qty"]        # той самий артикул двічі — сумуємо

        if unknown:
            return ({"ok": False, "error": "no stock row", "articles": unknown},
                    "no stock row")

        # 2) перевірити все до першого запису
        missing = []
        for st in planned.values():
            if kind == "reserve":
                free = st["allocated"] - st["reserved"] - st["delivered"]
                if st["asked"] > free:
                    missing.append({"article": st["article"], "available": free,
                                    "requested": st["asked"]})
            elif st["asked"] > st["reserved"]:
                missing.append({"article": st["article"],
                                "reserved": st["reserved"],
                                "requested": st["asked"]})
        if missing:
            err = "not enough" if kind == "reserve" else "not enough reserved"
            return {"ok": False, "error": err, "items": missing}, err

        # 3) усе сходиться — пишемо «Зарезервовано», «Отримано» і «Оновлено»
        now = config.now().strftime("%d.%m.%Y %H:%M:%S")
        data_cells = []
        result = []
        for st in planned.values():
            reserved = (st["reserved"] + st["asked"] if kind == "reserve"
                        else st["reserved"] - st["asked"])
            delivered = (st["delivered"] + st["asked"] if kind == "receive"
                         else st["delivered"])
            r_col = _col_letter(STOCK_COLS["reserved"])
            u_col = _col_letter(STOCK_COLS["updated"])
            data_cells.append({"range": f"{STOCK_TAB}!{r_col}{st['row']}",
                               "values": [[_tidy(reserved)]]})
            if kind == "receive":
                d_col = _col_letter(STOCK_COLS["delivered"])
                data_cells.append({"range": f"{STOCK_TAB}!{d_col}{st['row']}",
                                   "values": [[_tidy(delivered)]]})
            data_cells.append({"range": f"{STOCK_TAB}!{u_col}{st['row']}",
                               "values": [[now]]})
            result.append({"article": st["article"],
                           "reserved": _tidy(reserved),
                           "delivered": _tidy(delivered),
                           "available": _tidy(st["allocated"] - reserved
                                              - delivered)})

        def write():
            return _build().spreadsheets().values().batchUpdate(
                spreadsheetId=_sheet_id(),
                body={"valueInputOption": "USER_ENTERED", "data": data_cells}
            ).execute()

        try:
            await _run(write, f"batchUpdate {kind}")
        except ApiError as e:
            return None, str(e)

    if single and result:
        out = dict(result[0])
        out["ok"] = True
        return out, None
    return {"ok": True, "items": result}, None


async def stock_op(op: str, user_id, article: str, qty: int):
    """reserve / receive / release для однієї позиції."""
    return await stock_op_many(op, user_id, [{"article": article, "qty": qty}])
