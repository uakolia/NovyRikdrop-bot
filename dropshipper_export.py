"""Дублювання замовлень у власну таблицю дропшипера — напряму через Sheets API.

Раніше це робив Apps Script усередині запису замовлення
(`dropshipper_export.gs`), і саме через нього скрипт мусив лишатися на шляху
замовлення: писати в таблицю напряму ми могли, а експорт при цьому тихо
перестав би оновлюватися. Тут та сама логіка на Python, тож Apps Script із
оформлення зникає зовсім.

Куди писати — у змінній середовища (той самий формат, що був у Властивостях
скрипту, щоб не переписувати налаштування):

    DROPSHIPPER_EXPORT_SHEETS_JSON = {"545995767": "1o0EH…TtFw"}

Ключ — Telegram ID дропшипера, значення — ID його таблиці. Сервісний акаунт
має бути РЕДАКТОРОМ тієї таблиці.

Правило лишається незмінним: збій експорту НІКОЛИ не валить замовлення. Усе
обгорнуте, помилка йде в лог і адміну, бот каже дропшиперу «прийнято».
"""
import json
import logging

import article_key
import config
import sheets_api

log = logging.getLogger(__name__)

TAB = "Замовлення"

HEADERS = ["Номер замовлення", "Дата", "Назва товару", "Артикул",
           "Кількість", "Дроп-ціна", "Сума", "ПІБ отримувача",
           "Телефон отримувача", "Місто", "Відділення / адреса", "ТТН",
           "Статус замовлення", "Статус Nova Poshta", "Коментар"]

ORDER_NO_COL = 1
ARTICLE_COL = 4

# «· резерв знято» — наша внутрішня позначка в головній таблиці; у дропшипера
# в колонці має стояти лише статус від НП
INTERNAL_MARKS = (" · резерв знято", "· резерв знято")


def targets() -> dict:
    """{tg_id: sheet_id} або {} якщо не налаштовано."""
    raw = (config.DROPSHIPPER_EXPORT_SHEETS_JSON or "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError as e:
        log.warning("DROPSHIPPER_EXPORT_SHEETS_JSON не читається як JSON: %s", e)
        return {}
    return {str(k).strip(): str(v).strip() for k, v in (data or {}).items()}


def enabled() -> bool:
    return bool(sheets_api.enabled() and targets())


def public_status(value) -> str:
    """Статус НП без наших внутрішніх позначок."""
    text = str(value if value is not None else "")
    for mark in INTERNAL_MARKS:
        text = text.replace(mark, "")
    return text.strip()


def _name_for(tg_id, article: str, fallback: str) -> str:
    """Назва очима дропшипера: «Персональна назва» → власна назва → заводська.

    Та сама, що бот показує в меню і пише в накладну, — інакше дропшипер
    бачив би в своїй таблиці назви, яких не бачить більше ніде.
    """
    try:
        import aliases
        import stock
        uid = int(str(tg_id).strip())
    except (TypeError, ValueError):
        return fallback or ""
    name = stock.cached_name(uid, article)
    if name:
        return name
    return aliases.name_by_article(uid, article, fallback or "")


def _row_values(order: dict) -> list:
    """Рядок у порядку HEADERS."""
    qty = sheets_api._num(order.get("qty"))
    price = sheets_api._num(order.get("price_drop"))
    article = str(order.get("article") or "")
    total = price * qty if (price and qty) else ""
    return [
        order.get("order_no", ""),
        order.get("created_at", ""),
        _name_for(order.get("dropshipper_id"), article, order.get("product", "")),
        article,
        order.get("qty", ""),
        order.get("price_drop", ""),
        total,
        order.get("recipient_fio", ""),
        order.get("recipient_phone", ""),
        order.get("city", ""),
        order.get("warehouse", ""),
        order.get("ttn", ""),
        order.get("status", ""),
        public_status(order.get("np_status")),
        order.get("comment", ""),
    ]


async def push(rows: list[dict]) -> str | None:
    """Залити рядки замовлення в таблиці їхніх дропшиперів.

    Повертає текст помилки (для адміна) або None. Виключень не кидає.
    """
    if not enabled():
        return None
    try:
        return await _push(rows)
    except Exception as e:  # noqa: BLE001
        log.exception("Експорт у таблицю дропшипера не вдався: %s", e)
        return str(e)[:160]


async def _push(rows: list[dict]) -> str | None:
    by_sheet: dict[str, list[dict]] = {}
    where = targets()
    for r in rows:
        sheet_id = where.get(str(r.get("dropshipper_id") or "").strip())
        if sheet_id:
            by_sheet.setdefault(sheet_id, []).append(r)
    if not by_sheet:
        return None

    problems = []
    for sheet_id, mine in by_sheet.items():
        try:
            await _push_one(sheet_id, mine)
        except sheets_api.ApiError as e:
            problems.append(str(e))
            log.warning("Експорт у таблицю %s не вдався: %s",
                        sheets_api._short_id(sheet_id), e)
    return "; ".join(problems) or None


async def _push_one(sheet_id: str, rows: list[dict]):
    """Один аркуш: що є — оновити, чого немає — дописати."""
    existing = await sheets_api.rows_index(
        sheet_id, TAB, ORDER_NO_COL, ARTICLE_COL, width=len(HEADERS),
        headers=HEADERS)

    updates = []
    appends = []
    for r in rows:
        key = (str(r.get("order_no") or "").strip(),
               article_key.canon(r.get("article")))
        values = _row_values(r)
        row_no = existing.get(key)
        if row_no:
            updates.append((row_no, values))
        else:
            appends.append(values)
            # два рядки того самого замовлення в одному виклику: другий не має
            # знову піти в append, якщо артикул той самий
            existing[key] = -1
    await sheets_api.write_rows(sheet_id, TAB, HEADERS, updates, appends)


async def check() -> list:
    """Чи дійсно можемо писати в таблиці дропшиперів — рядки для адміна.

    Без цієї перевірки збій виявився б лише на першому справжньому
    замовленні: немає доступу, не той ID, перейменований аркуш.
    """
    where = targets()
    if not where:
        return ["⚠️ <b>Експорт дропшиперам</b>: не налаштовано "
                "(DROPSHIPPER_EXPORT_SHEETS_JSON) — замовлення пишуться лише "
                "в головну таблицю"]
    lines = []
    for tg_id, sheet_id in where.items():
        who = tg_id
        try:
            import storage
            info = (storage._approved_cache.get(str(tg_id))
                    or storage._load()["approved"].get(str(tg_id)) or {})
            who = info.get("name") or tg_id
        except Exception:  # noqa: BLE001
            pass
        try:
            rows = await sheets_api._read_table(sheet_id, TAB, len(HEADERS))
            lines.append(f"✅ <b>{who}</b>: таблиця {sheets_api._short_id(sheet_id)}, "
                         f"аркуш «{TAB}», рядків {len(rows)}")
        except sheets_api.ApiError as e:
            lines.append(f"❌ <b>{who}</b>: {e} "
                         f"(таблиця {sheets_api._short_id(sheet_id)})")
    return lines


async def delete_rows(rows: list[dict], order_no: str):
    """Прибрати рядки тестового замовлення з таблиць дропшиперів."""
    where = targets()
    done, problems = 0, []
    for r in rows:
        sheet_id = where.get(str(r.get("dropshipper_id") or "").strip())
        if not sheet_id:
            continue
        n, err = await sheets_api.delete_rows_by_key(
            sheet_id, TAB, ORDER_NO_COL, order_no, len(HEADERS))
        done += n
        if err:
            problems.append(err)
        break                     # усі позиції замовлення в одному аркуші
    return done, "; ".join(problems) or None
