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


def _sheet_id() -> str:
    return (getattr(config, "ORDERS_SHEET_ID", "") or "").strip()


# ─── служба ──────────────────────────────────────────────────────────────────
_service = None
_lock: asyncio.Lock | None = None
_lock_loop = None


def _stock_lock() -> asyncio.Lock:
    """Замок на операції із залишками. Створюється в робочому циклі."""
    global _lock, _lock_loop
    loop = asyncio.get_running_loop()
    if _lock is None or _lock_loop is not loop:
        _lock = asyncio.Lock()
        _lock_loop = loop
    return _lock


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
        return ("сервісний акаунт не має доступу до таблиці замовлень — "
                "додайте його редактором")
    if code == 404:
        return "таблицю замовлень не знайдено (перевірте ORDERS_SHEET_ID)"
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
