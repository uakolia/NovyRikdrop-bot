"""Обробка замовлення: запис у CSV, Google Таблицю (вебхук) і текст для адміна."""
import csv
import datetime as dt
import os

import aiohttp

import config

ORDERS_CSV = os.path.join(config.DATA_DIR, "orders.csv")

FIELDS = ["order_no", "created_at", "source", "dropshipper_id", "dropshipper",
          "article", "product", "size", "qty", "price_drop", "payment",
          "recipient_fio", "recipient_phone", "city", "warehouse",
          "ttn", "status", "comment"]


def save_csv(order: dict):
    os.makedirs(config.DATA_DIR, exist_ok=True)
    new = not os.path.exists(ORDERS_CSV)
    with open(ORDERS_CSV, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerow(order)


async def send_to_sheet(order: dict) -> str | None:
    """POST у Google Apps Script вебхук. Повертає текст помилки або None."""
    if not config.SHEET_WEBHOOK_URL:
        return None
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(config.SHEET_WEBHOOK_URL, json=order,
                              timeout=aiohttp.ClientTimeout(total=30),
                              allow_redirects=True) as r:
                if r.status >= 400:
                    return f"HTTP {r.status}"
        return None
    except Exception as e:  # noqa: BLE001
        return str(e)


def new_order(**kw) -> dict:
    order = {k: "" for k in FIELDS}
    order["created_at"] = dt.datetime.now().strftime("%d.%m.%Y %H:%M")
    order["status"] = "нове"
    order["qty"] = 1
    order.update(kw)
    return order


def admin_text(order: dict) -> str:
    pay = order["payment"]
    lines = [
        f"🆕 <b>Замовлення №{order['order_no']}</b> ({order['source']})",
        f"🌲 {order['product']} — {order['size']}",
        f"Артикул: <code>{order['article']}</code> × {order['qty']}",
        f"💰 Дроп-ціна: {order['price_drop']} грн | Оплата: <b>{pay}</b>",
        "",
        f"👤 {order['recipient_fio']}",
        f"📞 {order['recipient_phone']}",
        f"📍 {order['city']}, {order['warehouse']}",
        "",
        f"Дропшипер: {order['dropshipper']} (id {order['dropshipper_id']})",
    ]
    if order.get("ttn"):
        lines.append(f"📦 ТТН: <code>{order['ttn']}</code>")
    if order.get("comment"):
        lines.append(f"💬 {order['comment']}")
    return "\n".join(lines)
