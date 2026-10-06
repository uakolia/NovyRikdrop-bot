"""Google Таблиця як постійне сховище (файли в контейнері зникають при деплої).

Працює через той самий Apps Script вебхук (SHEET_WEBHOOK_URL).
Кожен запит несе SHEETS_API_SECRET (POST — у тілі, GET — параметром secret),
бо Apps Script не бачить HTTP-заголовків:
  POST {type: "order", ...}        — додати замовлення
  POST {type: "dropshipper", ...}  — додати/оновити дропшипера
  GET  ?what=bootstrap             — дропшипери+назви+залишки+макс. номер
  GET  ?what=dropshippers          — список схвалених
  GET  ?what=orders&id=<tg_id>     — замовлення дропшипера
  GET  ?what=maxorder              — максимальний номер замовлення
  GET  ?what=aliases               — власні назви товарів дропшиперів
  GET  ?what=stock                 — «Залишки дропшиперів»
  GET  ?what=ttns                  — замовлення з ТТН і незавершеним статусом НП
  POST {type: "reserve"|"receive"|"release", tg_id, article, qty}
  POST {type: "reserve_many"|"release_many", tg_id, items: [{article, qty}]}
  POST {type: "ttn_status", updates: [{ttn, np_status}]}
  POST {type: "alias", ...}        — додати/оновити власну назву
"""
import asyncio
import json
import logging
import re

import aiohttp

import config
import http_client
import perf

log = logging.getLogger(__name__)

NEED_UPDATE = ("скрипт таблиці старої версії. Apps Script → вставте новий код → "
               "Деплой → Керувати розгортаннями → ✏️ → Версія: Нова версія")

# Таймаути. Apps Script віддає відповідь у два кроки (/exec → переадресація на
# script.googleusercontent.com), і якщо виконання стало в чергу, очікування
# тягнеться десятки секунд. Тридцять секунд на читання означали, що дропшипер
# стільки ж дивиться на зависле меню; дванадцяти вистачає з запасом (звичайне
# читання — 1–2 с), а повторна спроба вже є. Запис чекає довше: у скрипті на
# залишках стоїть замок із очікуванням до 20 с.
READ_TIMEOUT = aiohttp.ClientTimeout(total=12)
WRITE_TIMEOUT = aiohttp.ClientTimeout(total=20)

# Apps Script інколи виконує запис, але відповідь губиться на переадресації
# (googleusercontent віддає 404 або 5xx). Тому такі коди пробуємо ще раз —
# запис у скрипті ідемпотентний: рядок із тим самим номером і артикулом
# перезаписується, а не дублюється.
RETRY_STATUSES = (404, 429, 500, 502, 503, 504)
RETRY_PAUSE = 2

# ...але операції із залишками повторювати НЕ можна: вони додають і віднімають
# числа, тож якщо відповідь загубилася вже ПІСЛЯ запису, друга спроба
# зарезервує товар удвічі. Краще голосно не зробити, ніж тихо зробити двічі.
NO_RETRY_TYPES = ("reserve", "receive", "release", "reserve_many", "release_many")


UNAUTHORIZED = ("таблиця відхилила запит: SHEETS_API_SECRET не збігається з "
                "Властивостями скрипту (або не заданий там)")

# Стара версія скрипта не знає операцій із залишками: невідомий type у ній
# провалюється у гілку «замовлення», тож вона бадьоро відповідає {ok:true},
# дописавши у «Замовлення» зайвий рядок. Розпізнаємо це за відсутністю даних
# про залишок у відповіді — інакше бот вирішив би, що резерв пройшов.
OLD_SCRIPT = (NEED_UPDATE + "\n\n🔎 скрипт відповів без даних про залишки — "
              "схоже, розгорнуто стару версію коду (у ній немає reserve_many). "
              "Перевірте, що деплой зроблено як «Нова версія», а не збережено "
              "лише в редакторі")


def enabled() -> bool:
    return bool(config.SHEET_WEBHOOK_URL)


def _scrub(text: str) -> str:
    """Прибрати секрет із тексту помилки: aiohttp (напр. TooManyRedirects) кладе
    в повідомлення повний URL разом із ?secret=..., а помилки бачать люди."""
    # параметр ховаємо цілком: yarl кодує секрет по-своєму, рядком не впіймати
    text = re.sub(r"([?&]secret=)[^&#\s'\"]*", r"\1***", text)
    if config.SHEETS_API_SECRET:
        text = text.replace(config.SHEETS_API_SECRET, "***")
    return text


def _err_text(e: Exception) -> str:
    """Текст помилки, який ніколи не буває порожнім.

    str(asyncio.TimeoutError()) — порожній рядок, і через нього таймаут
    доходив до дропшипера як «скрипт таблиці старої версії»: виклик віддавав
    порожню помилку, перевірка `if err` її пропускала, і далі спрацьовувала
    гілка «немає даних у відповіді».
    """
    if isinstance(e, asyncio.TimeoutError):
        return "таблиця не відповіла за відведений час"
    text = _scrub(str(e)).strip()
    return text or f"збій зв'язку з таблицею ({type(e).__name__})"


def _not_ready():
    if not config.SHEET_WEBHOOK_URL:
        return "вебхук таблиці не налаштований (SHEET_WEBHOOK_URL)"
    if not config.SHEETS_API_SECRET:
        return "не задано SHEETS_API_SECRET — таблиця без нього не відповідає"
    return None


async def _post(payload: dict):
    err = _not_ready()
    if err:
        return None, err
    payload = {**payload, "secret": config.SHEETS_API_SECRET}
    label = payload.get("type", "?")
    attempts = (1,) if label in NO_RETRY_TYPES else (1, 2)
    for attempt in attempts:
        try:
            async with perf.timed(f"таблиця POST {label}"):
                s = http_client.session()
                async with s.post(config.SHEET_WEBHOOK_URL, json=payload,
                                  timeout=WRITE_TIMEOUT,
                                  allow_redirects=True) as r:
                    if r.status in RETRY_STATUSES and attempt != attempts[-1]:
                        log.warning("Таблиця відповіла HTTP %s на %s — "
                                    "пробую ще раз", r.status, label)
                        await asyncio.sleep(RETRY_PAUSE)
                        continue
                    if r.status >= 400:
                        return None, f"HTTP {r.status}"
                    return _parse(await r.text())
        except Exception as e:  # noqa: BLE001
            if attempt != attempts[-1]:
                log.warning("Таблиця не відповіла на %s (%s) — пробую ще раз",
                            label, _err_text(e)[:80])
                await asyncio.sleep(RETRY_PAUSE)
                continue
            return None, _err_text(e)
    return None, "таблиця не відповіла з двох спроб"


def _hint(text: str) -> str:
    """Витягти з HTML-відповіді Google щось, що пояснює причину."""
    import re as _re
    m = _re.search(r"<title[^>]*>(.*?)</title>", text, _re.S | _re.I)
    title = (m.group(1).strip() if m else "")[:80]
    body = _re.sub(r"<[^>]+>", " ", text)
    body = _re.sub(r"\s+", " ", body).strip()[:160]
    if "authoriz" in text.lower() or "sign in" in text.lower() or "Увійти" in text:
        return "Google просить авторизацію — у розгортанні доступ має бути «Усі» (Anyone)"
    if "Moved Temporarily" in text or "moved" in text.lower():
        return "Google повернув переадресацію без даних"
    return f"відповідь Google: {title or body or 'порожня'}"


def _parse(text: str):
    """Apps Script без doGet (або з помилкою) віддає HTML замість JSON."""
    try:
        data = json.loads(text)
    except ValueError:
        return None, f"{NEED_UPDATE}\n\n🔎 {_hint(text)}"
    if isinstance(data, dict) and data.get("error") == "unauthorized":
        return None, UNAUTHORIZED
    return data, None


async def _get(params: dict):
    err = _not_ready()
    if err:
        return None, err
    params = {**params, "secret": config.SHEETS_API_SECRET}
    label = params.get("what", "?")
    for attempt in (1, 2):
        try:
            async with perf.timed(f"таблиця GET {label}"):
                s = http_client.session()
                async with s.get(config.SHEET_WEBHOOK_URL, params=params,
                                 timeout=READ_TIMEOUT,
                                 allow_redirects=True) as r:
                    if r.status in RETRY_STATUSES and attempt == 1:
                        log.warning("Таблиця відповіла HTTP %s на читання %s — "
                                    "пробую ще раз", r.status, label)
                        await asyncio.sleep(RETRY_PAUSE)
                        continue
                    if r.status >= 400:
                        return None, f"HTTP {r.status}"
                    return _parse(await r.text())
        except Exception as e:  # noqa: BLE001
            if attempt == 1:
                log.warning("Таблиця не відповіла на читання %s (%s) — "
                            "пробую ще раз", label, _err_text(e)[:80])
                await asyncio.sleep(RETRY_PAUSE)
                continue
            return None, _err_text(e)
    return None, "таблиця не відповіла з двох спроб"


async def push_order(order: dict):
    return await push_order_rows([order])


async def push_order_rows(rows: list[dict]):
    """Замовлення одним запитом: спільні поля + items на кожну позицію.

    Один рядок — той самий формат, що й раніше (без items), тож замовлення
    з сайту і старі виклики працюють без змін.
    """
    if not rows:
        return None
    head = rows[0]
    if len(rows) == 1:
        payload = {"type": "order", **head}
    else:
        import orders
        common = {k: v for k, v in head.items() if k not in orders.ITEM_FIELDS}
        payload = {"type": "order", **common,
                   "items": [{k: r.get(k, "") for k in orders.ITEM_FIELDS}
                             for r in rows]}
    data, err = await _post(payload)
    if err:
        return err
    if not (isinstance(data, dict) and data.get("ok")):
        return f"таблиця не підтвердила запис: {_scrub(str(data))[:200]}"
    return None


async def push_dropshipper(user_id: int, name: str, username: str,
                           status: str = "схвалений", approved_by: str = ""):
    data, err = await _post({
        "type": "dropshipper", "tg_id": str(user_id), "name": name,
        "username": username, "status": status, "approved_by": approved_by,
    })
    return err


async def fetch_bootstrap():
    """Дропшипери + власні назви + залишки + максимальний номер — одним разом.

    Окремі читання стають у чергу: Google виконує скрипт одного користувача
    послідовно, тож чотири запити підряд при старті бота — це чотири виконання
    одне за одним, і останнє чекає всі попередні. Тут усе читається за одне.

    Повертає (дані, помилка). Якщо розгорнуто старий скрипт (він не знає
    what=bootstrap і відповідає підказкою), помилкою буде NEED_UPDATE —
    викликач має відкотитися на окремі читання.
    """
    data, err = await _get({"what": "bootstrap"})
    if err:
        return None, err
    if not isinstance(data, dict) or data.get("dropshippers") is None:
        return None, NEED_UPDATE
    # залишки чистимо так само, як у fetch_stock: назва з аркуша їде в опис ТТН
    data["stock"] = _clean_stock_rows(data.get("stock"))
    return data, None


async def fetch_dropshippers():
    """[{tg_id, name, username, status}] або (None, помилка)."""
    data, err = await _get({"what": "dropshippers"})
    if err:
        return None, err
    rows = (data or {}).get("rows")
    if rows is None:
        return None, NEED_UPDATE
    return rows, None


async def fetch_orders(user_id: int, limit: int = 10):
    """Замовлення дропшипера; user_id 0/None — усі замовлення.

    «Без фільтра» передаємо ПОРОЖНІМ рядком, а не нулем: у скрипті стоїть
    перевірка if (id && …), а рядок "0" у JavaScript істинний, тож скрипт
    шукав би замовлення дропшипера з ID 0 і повертав порожньо.
    """
    data, err = await _get({"what": "orders",
                            "id": str(user_id) if user_id else "",
                            "limit": str(limit)})
    if err:
        return None, err
    rows = (data or {}).get("rows")
    if rows is None:
        return None, NEED_UPDATE
    return rows, None


async def fetch_aliases():
    """[{tg_id, key, name}] — власні назви товарів дропшиперів."""
    data, err = await _get({"what": "aliases"})
    if err:
        return None, err
    rows = (data or {}).get("rows")
    if rows is None:
        return None, NEED_UPDATE
    return rows, None


async def fetch_stock():
    """Рядки «Залишків дропшиперів»: [{tg_id, article, name, available, ...}].

    Рядки лише читаємо. Текст обрізаємо з обох боків: у колонці «Персональна
    назва» трапляється хвостовий пробіл («Українська Люкс 2.5м »), а ця назва
    йде в опис ТТН на паперовій накладній Нової Пошти.

    Колонку «Дроп-ціна» свідомо не повертаємо: ціни беруться з прайсу за
    тарифом дропшипера (storage.price_tier), таблиця показує їх для ока.
    """
    data, err = await _get({"what": "stock"})
    if err:
        return None, err
    rows = (data or {}).get("rows")
    if rows is None:
        return None, NEED_UPDATE
    return _clean_stock_rows(rows), None


def _clean_stock_rows(rows) -> list:
    """Обрізати текст і відкинути рядки без артикула."""
    out = []
    for r in rows or []:
        clean = {k: (v.strip() if isinstance(v, str) else v) for k, v in r.items()}
        if clean.get("article"):
            out.append(clean)
    return out


async def stock_op(op: str, user_id: int, article: str, qty: int):
    """reserve / receive / release. Повертає (дані, помилка).

    Артикул шлемо ОРИГІНАЛЬНИЙ — у скрипті рядок шукається за канонічним
    ключем, але в таблиці лишається те, що написано в прайсі.
    """
    data, err = await _post({"type": op, "tg_id": str(user_id),
                             "article": article, "qty": int(qty)})
    if err:
        return None, err
    if not isinstance(data, dict):
        return None, f"{NEED_UPDATE}\n\n🔎 відповідь: {_scrub(str(data))[:200]}"
    if not data.get("ok"):
        # тіло віддаємо разом із помилкою: у «not enough» там актуальний залишок
        return data, data.get("error") or "таблиця відхилила операцію"
    if "available" not in data:
        return None, OLD_SCRIPT
    return data, None


async def stock_op_many(op: str, user_id: int, items: list[dict]):
    """reserve_many / release_many — усі позиції під одним замком скрипта.

    Резерв кількох позицій має бути «все або нічого»: якщо перевіряти й писати
    по одній, між викликами встигне вклинитися чужий резерв, і замовлення
    лишиться наполовину зарезервованим.

    Повертає (дані, помилка). При «not enough» у даних — список позицій,
    яких бракує: [{article, available, requested}].
    """
    payload = [{"article": i["article"], "qty": int(i["qty"])} for i in items]
    data, err = await _post({"type": op, "tg_id": str(user_id),
                             "items": payload})
    if err:
        return None, err
    if not isinstance(data, dict):
        return None, f"{NEED_UPDATE}\n\n🔎 відповідь: {_scrub(str(data))[:200]}"
    if not data.get("ok"):
        return data, data.get("error") or "таблиця відхилила операцію"
    if "items" not in data:
        return None, OLD_SCRIPT
    return data, None


async def fetch_ttns():
    """Замовлення з ТТН, статус яких ще не кінцевий."""
    data, err = await _get({"what": "ttns"})
    if err:
        return None, err
    rows = (data or {}).get("rows")
    if rows is None:
        return None, NEED_UPDATE
    return rows, None


async def push_ttn_statuses(updates: list[dict]):
    """[{ttn, np_status}] одним запитом. Повертає (скільки записано, помилка)."""
    if not updates:
        return 0, None
    data, err = await _post({"type": "ttn_status", "updates": updates})
    if err:
        return 0, err
    if not (isinstance(data, dict) and data.get("ok")):
        return 0, (data or {}).get("error") or NEED_UPDATE
    return data.get("updated") or 0, None


async def push_alias(user_id: int, key: str, name: str):
    _, err = await _post({"type": "alias", "tg_id": str(user_id),
                          "key": key, "name": name})
    return err


async def fetch_max_order_no():
    data, err = await _get({"what": "maxorder"})
    if err:
        return 0, err
    try:
        return int((data or {}).get("max") or 0), None
    except (TypeError, ValueError):
        return 0, "невірна відповідь"
