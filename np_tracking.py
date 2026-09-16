"""Щогодинне оновлення статусів Нової Пошти й залишків за ними.

Головне правило: реагуємо на ЗМІНУ статусу, не на сам статус.
getStatusDocuments щоразу віддає поточний стан; якщо діяти на стан, то
«Відправлення отримано» щогодини перекладало б товар із «Зарезервовано» в
«Отримано» і за добу зіпсувало б залишки. Тому кожен рядок порівнюємо зі
«Статусом Nova Poshta», збереженим у таблиці: не змінився — нічого не робимо.

Що робимо на переходах (лише коли КАТЕГОРІЯ статусу змінилася):
  → отримано              stock.receive()  (резерв стає видачею)
  → відмова / повернення  повідомляємо адміну, резерв НЕ чіпаємо — рішення за людиною
  → видалено / не знайдено stock.release() (резерв повертається)

Коди звірені з офіційною документацією НП (developers.novaposhta.ua,
розділ «Актуальні статуси трекінгу»):
   1  створено, ще не передано в доставку  ← НЕ «видалено»: це звичайний стан
                                             щойно створеної ТТН, резерв тримаємо
   2  видалено            3  номер не знайдено
   4/41/5/6/7/8/12/101/104/111/112  у дорозі
   9  отримано           10  отримано, переказ у дорозі   11  отримано, переказ видано
   102 відмова (відправник створив повернення)   103 відмова одержувача
   105 припинено зберігання (їде назад)          106 отримано + створено зворотну ЄН
   108 стара відмова (у поточній таблиці немає, лишаємо про всяк випадок)

Текст статусу зберігаємо як є, українською; рішення приймаємо за StatusCode.
Телефони одержувачів у лог не пишемо ніколи — НП вимагає їх у запиті, але це
персональні дані клієнтів дропшипера.
"""
import asyncio
import logging

import config

log = logging.getLogger(__name__)

RECEIVED_CODES = {"9", "10", "11"}
REFUSAL_CODES = {"102", "103", "105", "106", "108"}
DEAD_CODES = {"2", "3"}

BATCH = 100          # обмеження НП на кількість документів в одному запиті


def category(code: str) -> str:
    code = str(code or "").strip()
    if code in RECEIVED_CODES:
        return "received"
    if code in REFUSAL_CODES:
        return "refusal"
    if code in DEAD_CODES:
        return "dead"
    return "transit"


def category_of_text(status: str) -> str:
    """Категорія збереженого тексту — щоб знати, що було ДО цього оновлення.

    Коду ми не зберігаємо (в таблиці лежить людський текст НП), тож стару
    категорію впізнаємо за словами. Потрібно лише для того, щоб не зробити
    receive() двічі, коли НП перемикається 10 → 11 (обидва «отримано»).
    """
    s = (status or "").strip().lower()
    if not s:
        return ""
    # 106 «Одержано і створено ЄН зворотньої доставки» — це повернення,
    # хоч і починається з «одержано»: перевіряємо його раніше
    if "відмов" in s or "поверн" in s or "зворотн" in s or "припинено зберігання" in s:
        return "refusal"
    if "отримано" in s or "одержано" in s:
        return "received"
    if "видалено" in s or "не знайдено" in s:
        return "dead"
    return "transit"


async def fetch_statuses(docs: list[dict]) -> dict:
    """{ТТН: {"status": текст, "code": код}} для списку {ttn, phone}."""
    import novaposhta as np
    out = {}
    for start in range(0, len(docs), BATCH):
        chunk = docs[start:start + BATCH]
        data = await np._call("TrackingDocument", "getStatusDocuments", {
            "Documents": [{"DocumentNumber": d["ttn"], "Phone": d.get("phone", "")}
                          for d in chunk],
        })
        for row in data or []:
            ttn = str(row.get("Number") or "").strip()
            if ttn:
                out[ttn] = {"status": str(row.get("Status") or "").strip(),
                            "code": str(row.get("StatusCode") or "").strip()}
    return out


async def poll_once(bot=None):
    """Один прохід. Повертає (скільки оновлено, помилка)."""
    import sheets_store
    import stock
    if not sheets_store.enabled():
        return 0, None
    rows, err = await sheets_store.fetch_ttns()
    if err:
        return 0, err
    if not rows:
        return 0, None

    statuses = await fetch_statuses([{"ttn": r["ttn"], "phone": r.get("phone", "")}
                                     for r in rows if r.get("ttn")])
    updates = []
    for r in rows:
        fresh = statuses.get(str(r.get("ttn") or "").strip())
        if not fresh:
            continue
        was_text = str(r.get("np_status") or "").strip()
        if fresh["status"] == was_text:
            continue                                   # нічого не змінилося
        updates.append({"ttn": r["ttn"], "np_status": fresh["status"]})

        was, now = category_of_text(was_text), category(fresh["code"])
        if was == now:
            continue                                   # рух усередині категорії
        await _apply_transition(bot, r, now, fresh, stock)

    if not updates:
        return 0, None
    n, err = await sheets_store.push_ttn_statuses(updates)
    if err:
        return 0, err
    return n, None


async def _apply_transition(bot, row, now: str, fresh: dict, stock):
    """Дія на перехід у нову категорію. Помилки не зупиняють решту рядків."""
    ttn = row.get("ttn")
    tg_id = row.get("tg_id")
    article = row.get("article")
    try:
        qty = int(float(row.get("qty") or 1))
    except (TypeError, ValueError):
        qty = 1
    if not (tg_id and article):
        return
    try:
        if now == "received":
            _, err = await stock.receive(tg_id, article, qty)
            log.info("ТТН %s: отримано → видано %s × %s%s", ttn, article, qty,
                     f" (не вдалось: {err})" if err else "")
        elif now == "dead":
            _, err = await stock.release(tg_id, article, qty)
            log.info("ТТН %s: %s → резерв знято з %s × %s%s", ttn,
                     fresh["status"], article, qty,
                     f" (не вдалось: {err})" if err else "")
        elif now == "refusal":
            # резерв НЕ знімаємо: товар фізично їде назад, рішення за адміном
            log.warning("ТТН %s: %s — резерв %s × %s лишаємо, потрібне рішення",
                        ttn, fresh["status"], article, qty)
            if bot and config.ADMIN_CHAT_ID:
                await bot.send_message(
                    config.ADMIN_CHAT_ID,
                    f"↩️ <b>Відмова / повернення</b>\n"
                    f"ТТН <code>{ttn}</code> — {fresh['status']}\n"
                    f"Замовлення №{row.get('order_no', '?')}, "
                    f"{article} × {qty}\n"
                    "Резерв у залишках залишено — зніміть вручну, "
                    "коли товар повернеться на склад.")
    except Exception as e:  # noqa: BLE001
        log.warning("ТТН %s: перехід у «%s» не оброблено: %s", ttn, now, e)


async def run_forever(bot):
    """Фоновий цикл. Будь-яка помилка всередині не має вбивати задачу."""
    period = config.TTN_POLL_SECONDS
    if period <= 0:
        log.info("Опитування статусів НП вимкнено (TTN_POLL_SECONDS=0)")
        return
    log.info("Статуси НП оновлюються кожні %d с", period)
    while True:
        await asyncio.sleep(period)
        try:
            n, err = await poll_once(bot)
            if err:
                log.warning("Статуси НП не оновились: %s", err)
            elif n:
                log.info("Оновлено статусів НП: %d", n)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.exception("Помилка в циклі статусів НП: %s", e)
