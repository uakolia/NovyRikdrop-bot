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


async def send_to_sheet_rows(rows: list[dict]) -> str | None:
    """Усе замовлення одним запитом: рядок на позицію, спільний номер."""
    if not rows:
        return None
    if not config.SHEET_WEBHOOK_URL:
        return None
    return await sheets_store.push_order_rows(rows)


async def send_to_sheet(order: dict) -> str | None:
    """Записати замовлення в Google Таблицю. Повертає текст помилки або None.

    Йде через sheets_store: там секрет і перевірка відповіді {"ok": false}
    (Apps Script відхиляє запит з HTTP 200, тож статус нічого не каже)."""
    if not config.SHEET_WEBHOOK_URL:
        return None
    return await sheets_store.push_order(order)


# поля, які в замовленні з кількох позицій у кожного рядка свої;
# решта (отримувач, оплата, доставка, номер) — спільні
ITEM_FIELDS = ("article", "product", "size", "qty", "price_drop", "ttn",
               "status", "comment")


def new_rows(items: list[dict], **common) -> list[dict]:
    """Рядки одного замовлення: по одному на позицію, спільні поля однакові.

    Замовлення з однієї позиції дає один рядок — точно такий, як раніше,
    тож старі замовлення читаються без змін.
    """
    rows = []
    for it in items:
        row = new_order(**common)
        for k in ITEM_FIELDS:
            if k in it:
                row[k] = it[k]
        rows.append(row)
    return rows


def new_order(**kw) -> dict:
    order = {k: "" for k in FIELDS}
    # час у зоні таблиці, а не в тій, що трапилась контейнеру
    order["created_at"] = config.now().strftime("%d.%m.%Y %H:%M")
    order["status"] = "нове"
    order["qty"] = 1
    order.update(kw)
    return order


def rows_total(rows: list[dict]) -> int:
    total = 0
    for r in rows:
        try:
            total += int(r.get("price_drop") or 0)
        except (TypeError, ValueError):
            pass
    return total


def admin_text_multi(rows: list[dict]) -> str:
    """Замовлення з кількох позицій одним повідомленням адміну."""
    if not rows:
        return ""
    if len(rows) == 1:
        return admin_text(rows[0])
    head = rows[0]
    lines = [f"🆕 <b>Замовлення №{head['order_no']}</b> ({head['source']}) — "
             f"{len(rows)} позиції"]
    for r in rows:
        line = (f"\n🌲 {r['product']} — {r['size']}\n"
                f"Артикул: <code>{r['article']}</code> × {r['qty']} — "
                f"{r['price_drop']} грн")
        if r.get("ttn"):
            line += f"\n📦 ТТН: <code>{r['ttn']}</code>"
        elif r.get("status"):
            line += f"\n⚠️ {r['status']}"
        lines.append(line)
    lines.append(f"\n💰 Разом: <b>{rows_total(rows)} грн</b> | "
                 f"Оплата: <b>{head['payment']}</b>")
    if head.get("cod_amount"):
        lines.append(f"💵 При отриманні: <b>{head['cod_amount']} грн</b>")
    lines += ["",
              f"👤 {head['recipient_fio']}",
              f"📞 {head['recipient_phone']}",
              f"📍 {head['city']}, {head['warehouse']}",
              "",
              f"Дропшипер: {head['dropshipper']} (id {head['dropshipper_id']})"]
    if head.get("due_amount"):
        pf = {"надіслано": "📸 чек надіслано", "очікується": "⏳ чек не надіслано"}
        lines.append(f"🏦 На рахунок: <b>{head['due_amount']} грн</b> · "
                     f"{pf.get(head.get('payment_proof'), '—')}")
    comments = [r["comment"] for r in rows if r.get("comment")]
    if comments:
        lines.append("💬 " + " | ".join(dict.fromkeys(comments)))
    return "\n".join(lines)


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
