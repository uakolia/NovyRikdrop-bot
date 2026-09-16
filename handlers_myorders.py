"""Розділ «Мої замовлення» (дані з Google Таблиці, локальний журнал — резерв)."""
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

import aliases
import keyboards as kb
import orders
import sheets_store
import storage

router = Router()

STATUS_ICON = {
    "ТТН створено": "📦",
    "очікує оплати": "💳",
    "ТТН НЕ створено": "⚠️",
    "нове": "🆕",
}


def _product_name(user_id: int | None, o: dict) -> str:
    """Назва очима дропшипера: «Персональна назва» → власна назва → з таблиці."""
    import stock
    article = o.get("article", "")
    own = stock.cached_name(user_id, article)
    if own:
        return own
    return aliases.name_by_article(user_id, article, o.get("product", ""))


def group_orders(rows: list[dict]) -> list[list[dict]]:
    """Рядки → замовлення: кілька позицій з одним номером ідуть разом.

    Старе замовлення з одного рядка стає групою з одного елемента, тож
    показується точно як раніше.
    """
    groups: dict[str, list] = {}
    order: list[str] = []
    for r in rows:
        key = str(r.get("order_no") or "").strip() or f"_{len(order)}"
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(r)
    return [groups[k] for k in order]


def _fmt_group(rows: list[dict], user_id: int | None = None) -> str:
    """Замовлення цілком: спільна шапка, далі позиції з їхніми ТТН."""
    if len(rows) == 1:
        return _fmt(rows[0], user_id)
    head_row = rows[0]
    icon = STATUS_ICON.get(str(head_row.get("status", "")).strip(), "•")
    lines = [f"{icon} <b>№{head_row.get('order_no', '?')}</b> · "
             f"{head_row.get('created_at', '')} · {len(rows)} позиції"]
    for r in rows:
        line = f"🌲 {_product_name(user_id, r)} — {r.get('size', '')}"
        if str(r.get("qty", "1")) not in ("", "1"):
            line += f" × {r['qty']}"
        if r.get("ttn"):
            line += (f"\n    📦 <code>{r['ttn']}</code>"
                     f" · {r.get('np_status') or r.get('status') or ''}")
        elif r.get("status"):
            line += f"\n    {r['status']}"
        lines.append(line)
    lines += [f"👤 {head_row.get('recipient_fio', '')} · "
              f"{head_row.get('recipient_phone', '')}",
              f"📍 {head_row.get('city', '')}, {head_row.get('warehouse', '')}"]
    pay = f"💳 {head_row.get('payment', '')}"
    if head_row.get("cod_amount"):
        pay += f" · при отриманні {head_row['cod_amount']} грн"
    lines.append(pay)
    if head_row.get("due_amount"):
        mark = "✅" if head_row.get("payment_proof") == "надіслано" else "⏳"
        lines.append(f"🏦 На рахунок: {head_row['due_amount']} грн {mark}")
    return "\n".join(lines)


def _fmt(o: dict, user_id: int | None = None) -> str:
    icon = STATUS_ICON.get(str(o.get("status", "")).strip(), "•")
    name = _product_name(user_id, o)
    head = (f"{icon} <b>№{o.get('order_no', '?')}</b> · {o.get('created_at', '')}\n"
            f"🌲 {name} — {o.get('size', '')}")
    if str(o.get("qty", "1")) not in ("", "1"):
        head += f" × {o['qty']}"
    lines = [head,
             f"👤 {o.get('recipient_fio', '')} · {o.get('recipient_phone', '')}",
             f"📍 {o.get('city', '')}, {o.get('warehouse', '')}"]
    pay = f"💳 {o.get('payment', '')}"
    if o.get("cod_amount"):
        pay += f" · при отриманні {o['cod_amount']} грн"
    lines.append(pay)
    if o.get("due_amount"):
        mark = "✅" if o.get("payment_proof") == "надіслано" else "⏳"
        lines.append(f"🏦 На рахунок: {o['due_amount']} грн {mark}")
    if o.get("ttn"):
        lines.append(f"📦 ТТН: <code>{o['ttn']}</code>\n"
                     f"🔗 https://novaposhta.ua/tracking/?cargo_number={o['ttn']}")
    elif o.get("status"):
        lines.append(f"Статус: {o['status']}")
    return "\n".join(lines)


async def _load_orders(user_id: int, limit: int = 10):
    """Спершу таблиця (виживає деплої), потім локальний журнал.

    Повертає ГРУПИ рядків: замовлення з трьох позицій — це три рядки, які
    мають показатися як одне замовлення й зайняти один слот із limit.
    Тому з таблиці беремо із запасом і ріжемо вже після групування.
    """
    import stock
    await stock.rows_for(user_id)          # щоб були персональні назви
    err = None
    rows = None
    if sheets_store.enabled():
        rows, err = await sheets_store.fetch_orders(user_id, limit * 5)
        if rows is not None:
            err = None
    if rows is None:
        rows = orders.list_for_user(user_id, limit * 5)
    return group_orders(rows)[:limit], err


@router.callback_query(F.data == "my:orders")
async def cb_my_orders(cb: CallbackQuery):
    await cb.answer()
    rows, err = await _load_orders(cb.from_user.id)
    if not rows:
        text = "📋 <b>Мої замовлення</b>\n\nПоки що замовлень немає."
        if err:
            text += f"\n\n<i>⚠️ {err}</i>"
        await cb.message.edit_text(text, reply_markup=kb.main_menu())
        return
    blocks = "\n\n".join(_fmt_group(g, cb.from_user.id) for g in rows)
    text = f"📋 <b>Мої замовлення</b> (останні {len(rows)})\n\n{blocks}"
    if err:
        text += ("\n\n<i>⚠️ Журнал у таблиці недоступний, показано локальні дані.\n"
                 f"{err}</i>")
    await cb.message.edit_text(text[:4000], reply_markup=kb.main_menu(),
                               disable_web_page_preview=True)


@router.message(Command("myorders"))
async def cmd_my_orders(msg: Message):
    rows, err = await _load_orders(msg.from_user.id)
    if not rows:
        await msg.answer("Поки що замовлень немає." + (f"\n{err}" if err else ""))
        return
    blocks = "\n\n".join(_fmt_group(g, msg.from_user.id) for g in rows)
    await msg.answer(f"📋 <b>Мої замовлення</b>\n\n{blocks}"[:4000],
                     disable_web_page_preview=True)


@router.message(Command("orders"))
async def cmd_all_orders(msg: Message):
    """Адмін: останні замовлення всіх дропшиперів."""
    if not storage.is_admin(msg.from_user.id):
        return
    rows, err = (None, None)
    if sheets_store.enabled():
        rows, err = await sheets_store.fetch_orders(0, 10)  # id=0 → без фільтра
    if not rows:
        await msg.answer("Замовлень не знайдено." + (f"\n⚠️ {err}" if err else ""))
        return
    blocks = "\n\n".join(_fmt_group(g) + f"\n👔 {g[0].get('dropshipper', '')}"
                          for g in group_orders(rows))
    await msg.answer(f"📋 <b>Останні замовлення</b>\n\n{blocks}"[:4000],
                     disable_web_page_preview=True)
