"""Створити накладну для вже записаного замовлення: /ttn <номер>.

Потрібно, коли НП відмовила при оформленні — через символ в описі, неоднозначне
відділення, збій зв'язку. Раніше єдиним виходом було переоформлювати замовлення
заново, і дропшипер мусив удруге вводити отримувача.

Дані беремо з таблиці замовлень, а не в дропшипера. Але в таблиці зберігаються
НАЗВИ міста й відділення, а НП для накладної потрібні довідники (Ref), тож їх
доводиться шукати за назвою — і саме тут можлива неоднозначність. Якщо місто або
відділення не знаходяться однозначно, накладну НЕ створюємо й кажемо, що саме
не вийшло: створити накладну не туди гірше, ніж не створити.
"""
import logging
import re

import article_key
import catalog

log = logging.getLogger(__name__)


class RetryError(Exception):
    """Накладну створити не можемо — з текстом причини для адміна."""


def _num(value) -> float:
    s = re.sub(r"[^\d.,-]", "", str(value or "").replace("\xa0", ""))
    s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return 0.0


def _int(value, default: int = 1) -> int:
    n = _num(value)
    return int(n) if n > 0 else default


async def find_order(order_no: str, limit: int = 200):
    """Усі рядки замовлення з таблиці. Кидає RetryError, якщо не знайдено."""
    import sheets_store
    rows, err = await sheets_store.fetch_orders(0, limit)   # 0 — без фільтра
    if err:
        raise RetryError(f"таблиця не відповіла: {err}")
    want = str(order_no).strip()
    mine = [r for r in (rows or [])
            if str(r.get("order_no") or "").strip() == want]
    if not mine:
        raise RetryError(f"замовлення №{want} не знайдено серед останніх "
                         f"{limit} рядків таблиці")
    return mine


async def resolve_destination(rows):
    """(city_ref, warehouse_ref, опис) за назвами з таблиці."""
    import novaposhta as np
    head = rows[0]
    city_name = str(head.get("city") or "").strip()
    wh_text = str(head.get("warehouse") or "").strip()
    delivery = str(head.get("delivery") or "").lower()

    if "адрес" in delivery:
        raise RetryError(
            "це замовлення з адресною доставкою, а в таблиці зберігається лише "
            "текст адреси без довідників вулиці. Таку накладну доведеться "
            "оформити вручну в кабінеті НП або переоформити замовлення в боті")
    if not city_name:
        raise RetryError("у замовленні не вказано місто")

    cities = await np.search_cities(city_name, limit=10)
    exact = [c for c in cities if c["name"].strip().lower() == city_name.lower()]
    if len(exact) != 1:
        found = ", ".join(f"{c['name']} ({c['area']})" for c in cities[:5])
        raise RetryError(f"місто «{city_name}» не знайшлося однозначно. "
                         f"НП пропонує: {found or 'нічого'}")
    city = exact[0]

    number = re.sub(r"\D", "", wh_text.split(":")[0])   # «Відділення №1: …» → 1
    if not number:
        raise RetryError(f"з тексту відділення «{wh_text}» не видно номера")
    whs = await np.cargo_warehouses(city["ref"])
    match = [w for w in whs if str(w["number"]) == number]
    if not match:
        whs = await np.all_warehouses(city["ref"])
        match = [w for w in whs if str(w["number"]) == number]
    if len(match) != 1:
        raise RetryError(f"у місті {city['name']} не знайшлося рівно одного "
                         f"відділення №{number} (знайдено {len(match)})")
    return city, match[0]


async def create_for_order(order_no: str, force: bool = False):
    """Створити накладну для замовлення. Повертає (рядки, дані ТТН).

    Рядки повертаються вже з ТТН і статусом, записаними в таблицю.
    """
    import novaposhta as np
    import orders
    import sheets_store

    rows = await find_order(order_no)
    existing = [r.get("ttn") for r in rows if str(r.get("ttn") or "").strip()]
    if existing and not force:
        raise RetryError(f"у замовлення №{order_no} вже є накладна "
                         f"{existing[0]}. Якщо це помилка й потрібна нова — "
                         f"«/ttn {order_no} force»")

    city, warehouse = await resolve_destination(rows)
    head = rows[0]

    # вага, обʼєм, місця й вартість — як при звичайному оформленні
    weight = volume = 0.0
    seats = 0
    cost = 0
    unknown = []
    for r in rows:
        item = catalog.by_article(str(r.get("article") or ""))
        qty = _int(r.get("qty"))
        seats += qty
        cost += int(_num(r.get("price_drop")))
        if item is None:
            unknown.append(r.get("article"))
            continue
        weight += (item.get("weight_kg") or 5) * qty
        volume += (item.get("volume_m3") or 0) * qty
    if unknown:
        raise RetryError("у каталозі немає артикулів: " + ", ".join(unknown)
                         + ". Спробуйте /reload")

    # страхова сума — ціна продажу клієнту (одна на замовлення), не дроп-ціна
    sale = int(_num(head.get("sale_price")))
    if sale:
        cost = sale
    first = catalog.by_article(str(head.get("article") or ""))
    if len(rows) == 1:
        description = catalog.ttn_description(first,
                                              head.get("dropshipper_id"))
    else:
        pairs = [(catalog.by_article(str(r.get("article") or "")), _int(r.get("qty")))
                 for r in rows]
        description = catalog.ttn_description_multi(
            [(i, q) for i, q in pairs if i], head.get("dropshipper_id"))

    res = await np.create_ttn(
        recipient_city_ref=city["ref"],
        recipient_warehouse_ref=warehouse["ref"],
        fio=str(head.get("recipient_fio") or ""),
        phone=str(head.get("recipient_phone") or ""),
        description=description,
        cost=cost,
        weight=weight,
        volume=volume or None,
        seats=max(seats, 1),
        cod_amount=_num(head.get("cod_amount")),
        to_door=False,
    )

    for r in rows:
        r["ttn"] = res["ttn"]
        r["status"] = "ТТН створено"
        r["warehouse"] = warehouse["name"]
        r["city"] = city["name"]
    # запис ідемпотентний: рядки з тим самим номером і артикулом оновляться
    err = await sheets_store.push_order_rows(rows)
    if err:
        log.warning("ТТН %s створено, але в таблицю не записалось: %s",
                    res["ttn"], err)
        res["sheet_error"] = err
    return rows, res


def report(order_no: str, rows, res) -> str:
    """Текст для адміна."""
    lines = [f"✅ <b>Накладну для замовлення №{order_no} створено</b>",
             f"📦 ТТН: <code>{res['ttn']}</code>"]
    if res.get("estimated_date"):
        lines.append(f"🗓 Орієнтовна доставка: {res['estimated_date']}")
    if len(rows) > 1:
        lines.append(f"Позицій: {len(rows)}")
    for r in rows:
        lines.append(f"🌲 {r.get('product', '')} — {r.get('size', '')} × "
                     f"{r.get('qty', '')}")
    lines.append(f"📍 {rows[0].get('city', '')}, {rows[0].get('warehouse', '')}")
    if res.get("sheet_error"):
        lines.append(f"\n⚠️ У таблицю не записалось: {res['sheet_error']}\n"
                     "ТТН уже існує — впишіть номер у таблицю вручну, "
                     "щоб бот не створив другу.")
    return "\n".join(lines)
