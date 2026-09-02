"""Google Таблиця як постійне сховище (файли в контейнері зникають при деплої).

Працює через той самий Apps Script вебхук (SHEET_WEBHOOK_URL):
  POST {type: "order", ...}        — додати замовлення
  POST {type: "dropshipper", ...}  — додати/оновити дропшипера
  GET  ?what=dropshippers          — список схвалених
  GET  ?what=orders&id=<tg_id>     — замовлення дропшипера
  GET  ?what=maxorder              — максимальний номер замовлення
  GET  ?what=aliases               — власні назви товарів дропшиперів
  POST {type: "alias", ...}        — додати/оновити власну назву
"""
import json

import aiohttp

import config

NEED_UPDATE = ("скрипт таблиці старої версії. Apps Script → вставте новий код → "
               "Деплой → Керувати розгортаннями → ✏️ → Версія: Нова версія")

TIMEOUT = aiohttp.ClientTimeout(total=30)


def enabled() -> bool:
    return bool(config.SHEET_WEBHOOK_URL)


async def _post(payload: dict):
    if not enabled():
        return None, "вебхук таблиці не налаштований (SHEET_WEBHOOK_URL)"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(config.SHEET_WEBHOOK_URL, json=payload,
                              timeout=TIMEOUT, allow_redirects=True) as r:
                if r.status >= 400:
                    return None, f"HTTP {r.status}"
                return _parse(await r.text())
    except Exception as e:  # noqa: BLE001
        return None, str(e)


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
        return json.loads(text), None
    except ValueError:
        return None, f"{NEED_UPDATE}\n\n🔎 {_hint(text)}"


async def _get(params: dict):
    if not enabled():
        return None, "вебхук таблиці не налаштований (SHEET_WEBHOOK_URL)"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(config.SHEET_WEBHOOK_URL, params=params,
                             timeout=TIMEOUT, allow_redirects=True) as r:
                if r.status >= 400:
                    return None, f"HTTP {r.status}"
                return _parse(await r.text())
    except Exception as e:  # noqa: BLE001
        return None, str(e)


async def push_order(order: dict):
    data, err = await _post({"type": "order", **order})
    return err


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
