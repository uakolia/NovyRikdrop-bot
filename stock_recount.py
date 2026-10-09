"""Перерахунок «Зарезервовано» й «Отримано» з таблиці замовлень.

ЧОМУ ЦЕ ПОТРІБНО. Доти ці дві колонки бот вів приростами: при оформленні
+1 до резерву, при отриманні −1 з резерву й +1 до отриманих. Будь-який
збій — втрачена відповідь, повторна спроба, видалена накладна, рядок,
виправлений руками — лишав перекіс, який сам уже не виправлявся. А після
того, як резерв вимкнули, колонки взагалі застигли.

ЩО РОБИМО ЗАМІСТЬ. Рахуємо обидві колонки заново з аркуша «Замовлення» —
він джерело правди. Операція ідемпотентна: хоч раз, хоч сто разів підряд,
результат той самий, і жодна втрачена відповідь нічого не псує.

ПРАВИЛА:
  статус НП «отримано клієнтом»  → «Отримано»
  будь-який інший статус         → «Зарезервовано в активних замовленнях»
  «Статус» замовлення скасовано  → нікуди: товар повертається на склад

Статус Нової Пошти сам по собі товар НЕ звільняє — навіть «Видалено». Так
вийшло з досвіду: коли відправлення переоформлюють, старий номер НП гасить і
віддає «Видалено», хоч ялинка поїхала. Раніше бот на це повертав товар у
доступні, і залишки показували більше, ніж є насправді.

Тому звільнити позицію може лише людина: впишіть у колонку «Статус»
замовлення слово «скасовано» (або «відмінено»), і перерахунок поверне товар
у доступні.

Колонку «Виділено за передзамовленням» не чіпаємо НІКОЛИ: це ваша домовленість
із дропшипером, бот її не рахує. «Доступно» теж — там формула F−G−H.
"""
import logging

import article_key

log = logging.getLogger(__name__)

# статуси замовлення (не НП), за яких позиція вже нічого не тримає
DEAD_ORDER_MARKS = ("скасов", "відмін", "видален")


def _qty(value) -> int:
    try:
        return int(float(str(value).replace(",", ".").strip() or 0))
    except (TypeError, ValueError):
        return 0


def classify(row: dict) -> str:
    """«received» / «reserved» / «none» для одного рядка замовлення."""
    import np_tracking
    order_status = str(row.get("status") or "").lower()
    if any(mark in order_status for mark in DEAD_ORDER_MARKS):
        return "none"
    np_status = str(row.get("np_status") or "").strip()
    if np_status and np_tracking.category_of_text(np_status) == "received":
        return "received"
    # усе інше — товар за активним замовленням: ТТН ще не створена, їде,
    # переоформлена («Видалено»), повертається. Жоден статус НП не вирішує
    # сам, що товар знову на складі: це каже людина в колонці «Статус».
    return "reserved"


def plan(order_rows: list, stock_rows: list) -> dict:
    """Що треба виправити. Повертає звіт зі списком правок.

    stock_rows — рядки «Залишків» як їх віддає sheets_api (з номером рядка
    у полі "row", якщо він є).
    """
    want: dict[tuple, dict] = {}
    for r in order_rows or []:
        tg = str(r.get("dropshipper_id") or "").strip()
        art = str(r.get("article") or "").strip()
        if not tg or not art:
            continue
        key = (tg, article_key.canon(art))
        kind = classify(r)
        if kind == "none":
            continue
        slot = want.setdefault(key, {"reserved": 0, "received": 0,
                                     "articles": set(), "orders": []})
        slot[kind] += _qty(r.get("qty")) or 1
        slot["articles"].add(art)
        slot["orders"].append(str(r.get("order_no") or "?"))

    fixes = []
    seen = set()
    for row in stock_rows or []:
        tg = str(row.get("tg_id") or "").strip()
        art = str(row.get("article") or "").strip()
        key = (tg, article_key.canon(art))
        seen.add(key)
        counted = want.get(key) or {"reserved": 0, "received": 0, "orders": []}
        now_res = _qty(row.get("reserved"))
        now_rec = _qty(row.get("delivered"))
        if now_res == counted["reserved"] and now_rec == counted["received"]:
            continue
        fixes.append({
            "tg_id": tg, "article": art, "row": row.get("row"),
            "dropshipper": row.get("dropshipper") or tg,
            "reserved": [now_res, counted["reserved"]],
            "received": [now_rec, counted["received"]],
            "allocated": _qty(row.get("allocated")),
            "orders": counted["orders"],
        })

    # замовлення на товар, якого в «Залишках» немає — писати нікуди
    orphans = []
    for key, slot in want.items():
        if key in seen:
            continue
        orphans.append({"tg_id": key[0], "article": sorted(slot["articles"])[0],
                        "reserved": slot["reserved"],
                        "received": slot["received"],
                        "orders": slot["orders"]})
    return {"fixes": fixes, "orphans": orphans}


def report(data: dict) -> list:
    """Рядки для адміна."""
    fixes, orphans = data["fixes"], data["orphans"]
    if not fixes and not orphans:
        return ["✅ Залишки сходяться з таблицею замовлень — правити нічого"]
    lines = []
    for f in fixes:
        parts = []
        if f["reserved"][0] != f["reserved"][1]:
            parts.append(f"резерв {f['reserved'][0]} → <b>{f['reserved'][1]}</b>")
        if f["received"][0] != f["received"][1]:
            parts.append(f"отримано {f['received'][0]} → <b>{f['received'][1]}</b>")
        free = f["allocated"] - f["reserved"][1] - f["received"][1]
        lines.append(f"🔧 <code>{f['article']}</code> ({f['dropshipper']}): "
                     + ", ".join(parts) + f" → доступно {free}")
    for o in orphans:
        lines.append(f"❔ <code>{o['article']}</code>: у замовленнях є "
                     f"(резерв {o['reserved']}, отримано {o['received']}, "
                     f"№{', №'.join(o['orders'][:5])}), а рядка в «Залишках» "
                     f"немає — кількість там не ведеться")
    return lines


async def run(write: bool = False):
    """Перерахувати (і за потреби записати). Повертає (звіт, скільки, помилка)."""
    import sheets_api
    if not sheets_api.enabled():
        return [], 0, ("перерахунок працює лише з прямим доступом до таблиці "
                       "(ORDERS_SHEET_ID) — перевірте /sheetcheck")
    orders_rows, err = await sheets_api.read_orders(0, 1000)
    if err:
        return [], 0, err
    stock_rows, err = await sheets_api.read_stock()
    if err:
        return [], 0, err
    data = plan(orders_rows, stock_rows)
    lines = report(data)
    if not write or not data["fixes"]:
        return lines, 0, None
    n, err = await sheets_api.write_stock_counts(data["fixes"])
    if err:
        return lines, 0, err
    log.info("Перерахунок залишків: виправлено рядків %d", n)
    return lines, n, None
