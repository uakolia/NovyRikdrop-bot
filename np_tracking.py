"""Щогодинне оновлення статусів Нової Пошти й залишків за ними.

Головне правило: реагуємо на ЗМІНУ статусу, не на сам статус.
getStatusDocuments щоразу віддає поточний стан; якщо діяти на стан, то
«Відправлення отримано» щогодини перекладало б товар із «Зарезервовано» в
«Отримано» і за добу зіпсувало б залишки. Тому кожен рядок порівнюємо зі
«Статусом Nova Poshta», збереженим у таблиці: не змінився — нічого не робимо.

Одна накладна — одне замовлення, але позицій у ньому може бути кілька, і в
таблиці кожна позиція має свій рядок із тією самою ТТН. Тому рядки групуємо за
ТТН і на переході обробляємо КОЖНУ позицію з її власною кількістю: інакше
замовлення на дві ялинки списало б із залишків лише першу.

Що робимо на переходах (лише коли КАТЕГОРІЯ статусу змінилася):
  → отримано              stock.receive()  (резерв стає видачею)
  → відмова / повернення  повідомляємо адміну, резерв НЕ чіпаємо — рішення за людиною
  → видалено (код 2)      stock.release()  (накладної більше немає)
  → номер не знайдено (код 3) — окреме, обережне правило, див. нижче

«Номер не знайдено» саме по собі нічого не знімає: НП віддає код 3 і на щойно
створену накладну, поки не проіндексувала її, а зняти резерв із посилки, яка
ось-ось поїде, — гірше, ніж потримати його зайву добу. Тому:
  • перший код 3 — пишемо статус і повідомляємо адміна, резерв стоїть;
  • код 3 вдруге поспіль І замовлення старше 24 годин — знімаємо резерв і
    дописуємо до статусу позначку, щоб не зняти його вдруге;
  • вік невідомий (дата не розпізналась) — не знімаємо нічого, пишемо в лог.

Коди звірені з офіційною документацією НП (developers.novaposhta.ua,
розділ «Актуальні статуси трекінгу»):
   1  створено, ще не передано в доставку  ← НЕ «видалено»: це звичайний стан
                                             щойно створеної ТТН, резерв тримаємо
   2  видалено (остаточно)   3  номер не знайдено (обережно, див. вище)
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
import datetime as dt
import logging

import config

log = logging.getLogger(__name__)

RECEIVED_CODES = {"9", "10", "11"}
REFUSAL_CODES = {"102", "103", "105", "106", "108"}
DEAD_CODES = {"2"}            # видалено — накладної більше немає
MISSING_CODES = {"3"}         # номер не знайдено — можливо, ще не проіндексовано

BATCH = 100          # обмеження НП на кількість документів в одному запиті

# скільки має «прожити» замовлення, перш ніж код 3 можна вважати правдою
MISSING_MIN_AGE = dt.timedelta(hours=24)

# дописуємо до статусу, коли резерв знято за кодом 3 — щоб не зняти його вдруге
# (і щоб скрипт таблиці більше не віддавав цей рядок на опитування)
RELEASED_MARK = " · резерв знято"


def category(code: str) -> str:
    code = str(code or "").strip()
    if code in RECEIVED_CODES:
        return "received"
    if code in REFUSAL_CODES:
        return "refusal"
    if code in DEAD_CODES:
        return "dead"
    if code in MISSING_CODES:
        return "missing"
    return "transit"


# Скрипт віддає дату зі зсувом («2026-09-16T00:53:00+03:00»), якщо в комірці
# справжня дата. Якщо ж там лишився рядок бота («16.09.2026 0:53»), зсуву немає
# — такий час вважаємо часом у зоні таблиці (config.TIMEZONE), а не часом
# контейнера: TZ на хостингу може зникнути при перезбірці, і тоді порівняння
# тихо поїхало б на 2-3 години.
_DATE_FORMATS = (
    "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S%z",
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
    "%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%Y",
    "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S",
)


def parse_created(created_at) -> "dt.datetime | None":
    """Дата створення як datetime із зоною. None — не розпізнали."""
    s = str(created_at or "").strip()
    if not s:
        return None
    # «...Z» strptime із %z не бере, а ISO-розбір бере
    try:
        parsed = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        parsed = None
    if parsed is None:
        for fmt in _DATE_FORMATS:
            try:
                parsed = dt.datetime.strptime(s, fmt)
                break
            except ValueError:
                continue
    if parsed is None:
        return None
    if parsed.tzinfo is None:                      # час без зсуву — зона таблиці
        parsed = parsed.replace(tzinfo=config.tz())
    return parsed


def order_age(created_at) -> "dt.timedelta | None":
    """Скільки минуло від створення замовлення. None — дату не розпізнали.

    Нерозпізнана дата тихо вимикає все правило коду 3 (без віку резерв ніколи
    не знімається), тому про кожен такий випадок кричимо у WARNING.
    """
    s = str(created_at or "").strip()
    if not s:
        log.warning("Замовлення без дати створення — вік не порахувати, "
                    "правило «номер не знайдено» для нього не спрацює")
        return None
    parsed = parse_created(s)
    if parsed is None:
        log.warning("Дата замовлення %r не підходить під жоден відомий формат "
                    "— резерв за кодом 3 не знімаємо, потрібна увага", s)
        return None
    return config.now() - parsed


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
    if "видалено" in s:
        return "dead"
    if "не знайдено" in s:
        return "missing"
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
        log.info("Немає накладних для перевірки (усі статуси кінцеві або ТТН "
                 "ще не створені)")
        return 0, None

    # рядки однієї накладної — це позиції одного замовлення
    by_ttn: dict[str, list] = {}
    for r in rows:
        ttn = str(r.get("ttn") or "").strip()
        if ttn:
            by_ttn.setdefault(ttn, []).append(r)

    log.info("Перевіряю статуси НП: накладних %d, рядків %d",
             len(by_ttn), len(rows))
    statuses = await fetch_statuses(
        [{"ttn": ttn, "phone": group[0].get("phone", "")}
         for ttn, group in by_ttn.items()])
    updates = []
    for ttn, group in by_ttn.items():
        fresh = statuses.get(ttn)
        if not fresh:
            continue
        head = group[0]
        was_text = str(head.get("np_status") or "").strip()
        changed = fresh["status"] != was_text
        was, now = category_of_text(was_text), category(fresh["code"])

        if now == "missing":
            # тут важливо й те, що статус НЕ змінився: саме повтор коду 3
            # підтверджує, що номера справді немає
            mark = await _handle_missing(bot, group, fresh, stock, was, was_text)
            if changed or mark:
                updates.append({"ttn": ttn,
                                "np_status": fresh["status"] + mark})
            continue

        if not changed:
            continue                                   # нічого не змінилося
        updates.append({"ttn": ttn, "np_status": fresh["status"]})
        if was == now:
            continue                                   # рух усередині категорії
        await _apply_transition(bot, group, now, fresh, stock)

    if not updates:
        return 0, None
    n, err = await sheets_store.push_ttn_statuses(updates)
    if err:
        return 0, err
    return n, None


async def _handle_missing(bot, group: list, fresh: dict, stock, was: str,
                          was_text: str):
    """Код 3. Повертає позначку для статусу ("" — резерв не чіпали).

    Резерв знімаємо, лише якщо НП віддала «номер не знайдено» вже вдруге
    поспіль І замовленню більше доби. Інакше це, найімовірніше, свіжа накладна,
    яку НП ще не проіндексувала, і товар ось-ось поїде.
    """
    head = group[0]
    ttn = head.get("ttn")
    items = [{"article": r.get("article"), "qty": _qty(r)}
             for r in group if r.get("article")]
    listing = ", ".join(f"{i['article']} × {i['qty']}" for i in items)
    if RELEASED_MARK.strip() in (was_text or ""):
        return ""                                    # резерв уже знімали
    if was != "missing":
        log.warning("ТТН %s: НП не знає цього номера. Резерв лишаємо "
                    "до наступної перевірки", ttn)
        await _tell_admin(
            bot,
            "❓ <b>НП не знає накладної</b>\n"
            f"ТТН <code>{ttn}</code> — {fresh['status']}\n"
            f"Замовлення №{head.get('order_no', '?')}, {listing}\n"
            "Резерв поки лишили: свіжу накладну НП іноді ще не бачить. "
            "Якщо номер не з'явиться до наступної перевірки і замовленню буде "
            "понад добу — резерв знімемо автоматично.", ttn)
        return ""

    age = order_age(head.get("created_at"))
    if age is None:
        log.warning("ТТН %s: номера немає вдруге, але дату замовлення (%r) не "
                    "розпізнано — резерв не чіпаємо", ttn, head.get("created_at"))
        return ""
    if age < MISSING_MIN_AGE:
        log.info("ТТН %s: номера немає вдруге, але замовленню лише %s — чекаємо",
                 ttn, str(age).split(".")[0])
        return ""
    if not (head.get("tg_id") and items):
        return ""
    _, err = await stock.release_many(head["tg_id"], items)
    log.warning("ТТН %s: номера немає вдруге й замовленню понад добу — "
                "резерв %s знято%s", ttn, listing,
                f" (не вдалось: {err})" if err else "")
    if err:
        return ""
    await _tell_admin(
        bot,
        "🔓 <b>Резерв знято, стеження припинено</b>\n"
        f"Замовлення №{head.get('order_no', '?')}, ТТН <code>{ttn}</code>\n"
        f"{listing}\n\n"
        "Нова Пошта не знає цього номера вже вдруге поспіль, а замовленню "
        "понад добу. Резерв повернуто в залишки.\n\n"
        "⚠️ Бот <b>більше не перевіряє цю накладну</b>. Якщо посилка все ж "
        "з’явиться і поїде, залишки за нею доведеться звести вручну: "
        "статус НП більше не оновлюватиметься, і «Отримано» бот не проставить.",
        ttn)
    return RELEASED_MARK


async def _tell_admin(bot, text: str, ttn=None):
    if not (bot and config.ADMIN_CHAT_ID):
        return
    try:
        await bot.send_message(config.ADMIN_CHAT_ID, text)
    except Exception as e:  # noqa: BLE001
        log.warning("Не вдалося повідомити адміна про ТТН %s: %s", ttn, e)


def _qty(row) -> int:
    try:
        return int(float(row.get("qty") or 1))
    except (TypeError, ValueError):
        return 1


async def _apply_transition(bot, group: list, now: str, fresh: dict, stock):
    """Дія на перехід у нову категорію — для КОЖНОЇ позиції замовлення.

    Одна накладна може везти кілька позицій, тож і «отримано», і «видалено»
    треба провести по всіх рядках із їхніми кількостями.
    """
    head = group[0]
    ttn = head.get("ttn")
    tg_id = head.get("tg_id")
    items = [{"article": r.get("article"), "qty": _qty(r)}
             for r in group if r.get("article")]
    if not (tg_id and items):
        return
    listing = ", ".join(f"{i['article']} × {i['qty']}" for i in items)
    try:
        if now == "received":
            for it in items:
                _, err = await stock.receive(tg_id, it["article"], it["qty"])
                if err:
                    log.warning("ТТН %s: видача %s × %s не пройшла: %s",
                                ttn, it["article"], it["qty"], err)
            log.info("ТТН %s: отримано → видано %s", ttn, listing)
        elif now == "dead":
            _, err = await stock.release_many(tg_id, items)
            log.info("ТТН %s: %s → резерв знято з %s%s", ttn, fresh["status"],
                     listing, f" (не вдалось: {err})" if err else "")
        elif now == "refusal":
            # резерв НЕ знімаємо: товар фізично їде назад, рішення за адміном
            log.warning("ТТН %s: %s — резерв %s лишаємо, потрібне рішення",
                        ttn, fresh["status"], listing)
            await _tell_admin(
                bot,
                "↩️ <b>Відмова / повернення</b>\n"
                f"ТТН <code>{ttn}</code> — {fresh['status']}\n"
                f"Замовлення №{head.get('order_no', '?')}, {listing}\n"
                "Резерв у залишках залишено — зніміть вручну, коли товар "
                "повернеться на склад.", ttn)
    except Exception as e:  # noqa: BLE001
        log.warning("ТТН %s: перехід у «%s» не оброблено: %s", ttn, now, e)


# скільки чекати перед ПЕРШИМ опитуванням: дати боту стартувати, але не
# відкладати на годину — редеплой Railway обнуляє відлік, і при частих
# перезапусках статуси не оновлювалися б ніколи
FIRST_RUN_DELAY = 60


async def run_forever(bot):
    """Фоновий цикл. Будь-яка помилка всередині не має вбивати задачу."""
    period = config.TTN_POLL_SECONDS
    if period <= 0:
        log.info("Опитування статусів НП вимкнено (TTN_POLL_SECONDS=0)")
        return
    log.info("Статуси НП: перша перевірка через %d с, далі кожні %d с",
             FIRST_RUN_DELAY, period)
    delay = min(FIRST_RUN_DELAY, period)
    while True:
        await asyncio.sleep(delay)
        delay = period
        try:
            n, err = await poll_once(bot)
            if err:
                log.warning("Статуси НП не оновились: %s", err)
            else:
                # пишемо і нулі: так у логах видно, що цикл живий
                log.info("Статуси НП перевірено, оновлено рядків: %d", n)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.exception("Помилка в циклі статусів НП: %s", e)
