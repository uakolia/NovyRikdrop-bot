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
    """Спершу таблиця (виживає деплої), потім локальний журнал."""
    import stock
    await stock.rows_for(user_id)          # щоб були персональні назви
    if sheets_store.enabled():
        rows, err = await sheets_store.fetch_orders(user_id, limit)
        if rows is not None:
            return rows, None
        local = orders.list_for_user(user_id, limit)
        return local, err
    return orders.list_for_user(user_id, limit), None


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
    blocks = "\n\n".join(_fmt(o, cb.from_user.id) for o in rows)
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
    blocks = "\n\n".join(_fmt(o, msg.from_user.id) for o in rows)
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
    blocks = "\n\n".join(_fmt(o) + f"\n👔 {o.get('dropshipper', '')}" for o in rows)
    await msg.answer(f"📋 <b>Останні замовлення</b>\n\n{blocks}"[:4000],
                     disable_web_page_preview=True)
