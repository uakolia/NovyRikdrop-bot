"""Google Таблиця як постійне сховище (файли в контейнері зникають при деплої).

Працює через той самий Apps Script вебхук (SHEET_WEBHOOK_URL).
Кожен запит несе SHEETS_API_SECRET (POST — у тілі, GET — параметром secret),
бо Apps Script не бачить HTTP-заголовків:
  POST {type: "order", ...}        — додати замовлення
  POST {type: "dropshipper", ...}  — додати/оновити дропшипера
  GET  ?what=dropshippers          — список схвалених
  GET  ?what=orders&id=<tg_id>     — замовлення дропшипера
  GET  ?what=maxorder              — максимальний номер замовлення
  GET  ?what=aliases               — власні назви товарів дропшиперів
  GET  ?what=stock                 — «Залишки дропшиперів» (лише читання)
  POST {type: "alias", ...}        — додати/оновити власну назву
"""
import json
import re

import aiohttp

import config

NEED_UPDATE = ("скрипт таблиці старої версії. Apps Script → вставте новий код → "
               "Деплой → Керувати розгортаннями → ✏️ → Версія: Нова версія")

TIMEOUT = aiohttp.ClientTimeout(total=30)


UNAUTHORIZED = ("таблиця відхилила запит: SHEETS_API_SECRET не збігається з "
                "Властивостями скрипту (або не заданий там)")


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
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(config.SHEET_WEBHOOK_URL, json=payload,
                              timeout=TIMEOUT, allow_redirects=True) as r:
                if r.status >= 400:
                    return None, f"HTTP {r.status}"
                return _parse(await r.text())
    except Exception as e:  # noqa: BLE001
        return None, _scrub(str(e))


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
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(config.SHEET_WEBHOOK_URL, params=params,
                             timeout=TIMEOUT, allow_redirects=True) as r:
                if r.status >= 400:
                    return None, f"HTTP {r.status}"
                return _parse(await r.text())
    except Exception as e:  # noqa: BLE001
        return None, _scrub(str(e))


async def push_order(order: dict):
    data, err = await _post({"type": "order", **order})
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
    data, err = await _get({"what": "orders", "id": str(user_id),
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
    out = []
    for r in rows:
        clean = {k: (v.strip() if isinstance(v, str) else v) for k, v in r.items()}
        if clean.get("article"):
            out.append(clean)
    return out, None


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
