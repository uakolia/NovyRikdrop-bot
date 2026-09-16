"""Обробка замовлення: запис у CSV, Google Таблицю (вебхук) і текст для адміна."""
import csv
import datetime as dt
import json
import os

import config
import sheets_store

ORDERS_CSV = os.path.join(config.DATA_DIR, "orders.csv")

FIELDS = ["order_no", "created_at", "source", "dropshipper_id", "dropshipper",
          "article", "product", "size", "qty", "price_drop", "payment",
          "recipient_fio", "recipient_phone", "city", "warehouse",
          "ttn", "status", "comment", "sale_price", "prepaid", "cod_amount",
          "delivery", "due_amount", "payment_proof",
          # нові поля — лише в кінець, щоб не зсунути колонки таблиці
          "np_status", "updated_at"]


ORDERS_JSON = os.path.join(config.DATA_DIR, "orders.json")
_MAX_KEEP = 3000


def save_local(order: dict):
    """Зберегти замовлення у локальний журнал (для «Мої замовлення»)."""
    os.makedirs(config.DATA_DIR, exist_ok=True)
    data = []
    if os.path.exists(ORDERS_JSON):
        try:
            with open(ORDERS_JSON, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:  # noqa: BLE001
            data = []
    data.append({k: order.get(k, "") for k in FIELDS})
    data = data[-_MAX_KEEP:]
    with open(ORDERS_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def list_for_user(user_id: int, limit: int = 10):
    if not os.path.exists(ORDERS_JSON):
        return []
    try:
        with open(ORDERS_JSON, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:  # noqa: BLE001
        return []
    mine = [o for o in data if str(o.get("dropshipper_id")) == str(user_id)]
    return list(reversed(mine))[:limit]


def save_csv(order: dict):
    os.makedirs(config.DATA_DIR, exist_ok=True)
    new = not os.path.exists(ORDERS_CSV)
    with open(ORDERS_CSV, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerow(order)


async def send_to_sheet(order: dict) -> str | None:
    """Записати замовлення в Google Таблицю. Повертає текст помилки або None.

    Йде через sheets_store: там секрет і перевірка відповіді {"ok": false}
    (Apps Script відхиляє запит з HTTP 200, тож статус нічого не каже)."""
    if not config.SHEET_WEBHOOK_URL:
        return None
    return await sheets_store.push_order(order)


def new_order(**kw) -> dict:
    order = {k: "" for k in FIELDS}
    order["created_at"] = dt.datetime.now().strftime("%d.%m.%Y %H:%M")
    order["status"] = "нове"
    order["qty"] = 1
    order.update(kw)
    return order


def admin_text(order: dict) -> str:
    pay = order["payment"]
    pay_line = f"💰 Дроп-ціна: {order['price_drop']} грн | Оплата: <b>{pay}</b>"
    if order.get("cod_amount"):
        extra = f" | 💵 При отриманні: <b>{order['cod_amount']} грн</b>"
        if order.get("prepaid"):
            extra = (f" | Продаж: {order['sale_price']} грн, "
                     f"передплата: {order['prepaid']} грн" + extra)
        pay_line += extra
    lines = [
        f"🆕 <b>Замовлення №{order['order_no']}</b> ({order['source']})",
        f"🌲 {order['product']} — {order['size']}",
        f"Артикул: <code>{order['article']}</code> × {order['qty']}",
        pay_line,
        "",
        f"👤 {order['recipient_fio']}",
        f"📞 {order['recipient_phone']}",
        f"📍 {order['city']}, {order['warehouse']}",
        "",
        f"Дропшипер: {order['dropshipper']} (id {order['dropshipper_id']})",
    ]
    if order.get("ttn"):
        lines.append(f"📦 ТТН: <code>{order['ttn']}</code>")
    if order.get("due_amount"):
        pf = {"надіслано": "📸 чек надіслано", "очікується": "⏳ чек не надіслано"}
        lines.append(f"🏦 На рахунок: <b>{order['due_amount']} грн</b> · "
                     f"{pf.get(order.get('payment_proof'), '—')}")
    if order.get("comment"):
        lines.append(f"💬 {order['comment']}")
    return "\n".join(lines)
