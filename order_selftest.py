"""Перевірка всього шляху замовлення очима конкретного дропшипера.

Навіщо. Після переходу на Sheets API шлях замовлення змінився цілком: ціни за
тарифом, запис у головну таблицю, дублювання в таблицю дропшипера. Перевіряти
це справжнім замовленням дорого — воно створює накладну в Новій Пошті, яку
потім треба видаляти вручну. Тут той самий шлях, але без Нової Пошти:

  /testorder            — лише показати, що вийде (нічого не пишемо)
  /testorder write      — записати тестове замовлення в обидві таблиці,
                          прочитати назад і прибрати за собою
  /testorder write 545995767  — те саме для конкретного дропшипера

Номер тестового замовлення — «T-HHMMSS», тож він не може зіткнутися зі
справжніми (там цілі числа) і одразу видно, що це перевірка. Прибираємо рядки
лише після того, як переконалися, що в них саме наш номер.
"""
import logging

import article_key
import catalog
import config
import orders

log = logging.getLogger(__name__)

TEST_FIO = "Тестовий Отримувач Перевірочний"
TEST_PHONE = "0500000000"
TEST_CITY = "Київ"
TEST_WAREHOUSE = "Відділення №1 (тест)"


def _who() -> int | None:
    """Кого перевіряємо за замовчуванням — перший дропшипер з експортом."""
    import dropshipper_export
    for tg_id in dropshipper_export.targets():
        try:
            return int(tg_id)
        except (TypeError, ValueError):
            continue
    return None


def _cart(user_id: int, limit: int = 2) -> list[dict]:
    """Кошик із того, що в дропшипера справді є в залишках.

    Беремо саме його позиції: так перевіряємо і персональні назви, і те, що
    артикули з «Залишків» знаходяться в каталозі.
    """
    import stock
    cart = []
    for row in (stock._cache.get(str(user_id)) or {}).values():
        item = catalog.by_article(str(row.get("article") or ""))
        if item:
            cart.append({"article": item["article"], "qty": 1})
        if len(cart) >= limit:
            break
    if not cart:                      # немає залишків — беремо перший товар
        items = catalog.items()
        if items:
            cart = [{"article": items[0]["article"], "qty": 1}]
    return cart


def build(user_id: int, order_no: str) -> tuple[list[dict], dict]:
    """Рядки тестового замовлення й довідка про те, що вийшло."""
    import storage
    cart = _cart(user_id)
    items = []
    for it in cart:
        v = catalog.by_article(it["article"])
        items.append({
            "article": v["article"],
            "product": v["model_ua"],
            "size": catalog.size_label(v),
            "qty": it["qty"],
            "price_drop": int(catalog.drop_price(v, user_id) * it["qty"]),
        })
    total = sum(i["price_drop"] for i in items)
    sale = total + 1000                      # «ціна продажу» для страхової суми

    rows = orders.new_rows(
        items,
        order_no=order_no,
        source="перевірка",
        dropshipper_id=user_id,
        dropshipper=storage.price_tier(user_id),
        payment="післяплата",
        sale_price=sale,
        prepaid=0,
        cod_amount=sale,
        recipient_fio=TEST_FIO,
        recipient_phone=TEST_PHONE,
        city=TEST_CITY,
        warehouse=TEST_WAREHOUSE,
        delivery="відділення",
        status="перевірка",
        comment="тестове замовлення бота, можна видаляти",
    )
    pairs = [(catalog.by_article(r["article"]), int(r["qty"])) for r in rows]
    info = {
        "tier": storage.price_tier(user_id),
        "items": items,
        "total": total,
        "sale": sale,
        "description": catalog.ttn_description_multi(
            [(i, q) for i, q in pairs if i], user_id),
        "seats": sum(int(r["qty"]) for r in rows),
        "weight": sum((i["weight_kg"] or 5) * q for i, q in pairs if i),
    }
    return rows, info


def preview(user_id: int, rows: list[dict], info: dict) -> list[str]:
    """Що дропшипер побачить і що поїде в таблиці та накладну."""
    import dropshipper_export
    import stock
    lines = [f"👤 Дропшипер <code>{user_id}</code>, тариф "
             f"<b>{info['tier']}</b>"]
    for r in rows:
        item = catalog.by_article(r["article"])
        left = stock.cached_available(user_id, r["article"])
        lines.append(f"🌲 {catalog.product_name(user_id, item)} — {r['size']} "
                     f"× {r['qty']} = {r['price_drop']} грн"
                     + (f" (вільно {left})" if left is not None else ""))
    lines += [
        f"💰 Разом за тарифом: <b>{info['total']} грн</b>",
        f"🧾 Страхова сума в накладній: <b>{info['sale']} грн</b>",
        f"📦 Місць {info['seats']}, вага {info['weight']:g} кг",
        f"📝 Опис для НП: <code>{info['description']}</code>",
    ]
    export_row = dropshipper_export._row_values(rows[0])
    lines.append(f"📤 У таблицю дропшипера: «{export_row[2]}», сума "
                 f"{export_row[6]}")
    return lines


async def write_and_clean(rows: list[dict], order_no: str) -> list[str]:
    """Записати, прочитати назад, прибрати. Рядки звіту."""
    import dropshipper_export
    import sheets_api
    out = []

    n, err = await sheets_api.write_order_rows(rows)
    if err:
        return [f"❌ Запис у головну таблицю: {err}"]
    out.append(f"✅ Записано в «Замовлення»: рядків {n}")

    export_err = await dropshipper_export.push(rows)
    out.append("✅ Продубльовано дропшиперу" if not export_err
               else f"❌ Таблиця дропшипера: {export_err}")

    # читаємо назад — записане має знайтися саме там, де ми його чекаємо
    found, err = await sheets_api.read_orders(0, 50)
    mine = [r for r in (found or [])
            if str(r.get("order_no") or "").strip() == order_no]
    if err:
        out.append(f"⚠️ Прочитати назад не вдалося: {err}")
    elif len(mine) == len(rows):
        same = all(article_key.canon(a.get("article"))
                   == article_key.canon(b.get("article"))
                   for a, b in zip(sorted(mine, key=lambda r: str(r["article"])),
                                   sorted(rows, key=lambda r: str(r["article"]))))
        out.append("✅ Прочитано назад: рядки на місці"
                   + ("" if same else ", але артикули не збігаються"))
    else:
        out.append(f"⚠️ Прочитано назад {len(mine)} рядків замість {len(rows)}")

    # і прибираємо за собою — з перевіркою, що видаляємо саме тестові рядки
    removed, err = await sheets_api.delete_order_rows(order_no)
    out.append(f"🧹 Прибрано з головної таблиці: {removed}"
               if not err else f"⚠️ Прибрати не вдалося: {err}")
    gone, err = await dropshipper_export.delete_rows(rows, order_no)
    out.append(f"🧹 Прибрано в дропшипера: {gone}"
               if not err else f"⚠️ У дропшипера прибрати не вдалося: {err}")
    return out
