"""Розділ «Мої замовлення» (дані з Google Таблиці, локальний журнал — резерв)."""
from html import escape

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

import aliases
import delivery_view
import demo_order_rows
import keyboards as kb
import orders
import order_pagination
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
        ttn = delivery_view.ttn_text(r.get("ttn"))
        if ttn:
            line += f"\n    📦 <code>{escape(ttn)}</code>"
        line += "\n    " + delivery_view.delivery_html(r)
        if not ttn and r.get("status") and r["status"] != "нове":
            line += f" · {escape(str(r['status']))}"
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
    if head_row.get("np_status") and head_row.get("updated_at"):
        lines.append(f"🕒 Дані оновлено: {escape(str(head_row['updated_at']))}")
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
    ttn = delivery_view.ttn_text(o.get("ttn"))
    if ttn:
        lines.append(f"📦 ТТН: <code>{escape(ttn)}</code>")
        if ttn.isascii() and ttn.isdigit():
            lines.append(f"🔗 https://novaposhta.ua/tracking/?cargo_number={ttn}")
    lines.append(delivery_view.delivery_html(o))
    if not ttn and o.get("status") and o["status"] != "нове":
        lines.append(f"Стан замовлення: {escape(str(o['status']))}")
    if o.get("np_status") and o.get("updated_at"):
        lines.append(f"🕒 Дані оновлено: {escape(str(o['updated_at']))}")
    return "\n".join(lines)


def _summary(group: list[dict], user_id: int) -> str:
    """One compact line per order to fit ten orders in a Telegram message."""
    first = group[0]
    no = escape(str(first.get("order_no") or "?")[:35])
    date = escape(str(first.get("created_at") or "")[:16])
    if len(group) == 1:
        product = escape(_product_name(user_id, first)[:65])
        size = escape(str(first.get("size") or "")[:20])
        item = f"{product} {size}".strip()
        status = delivery_view.delivery_label(first)
    else:
        item = f"{len(group)} позиції"
        labels = {delivery_view.delivery_label(r) for r in group}
        status = labels.pop() if len(labels) == 1 else "📦 Різні статуси позицій"
    return f"<b>№{no}</b> · {date}\n🌲 {item}\n{status}"


async def _load_page(user_id: int, page: int):
    """Google Sheets is authoritative, local journal is the existing fallback."""
    import stock
    await stock.rows_for(user_id)

    async def fetch(limit: int):
        # Read-only demos are visible only to the designated account.
        # They NEVER enter Sheets, the order journal, NP, or stock tracking.
        demo = demo_order_rows.for_user(user_id)
        real_limit = max(0, limit - len(demo))
        if real_limit == 0:
            return demo[:limit], None
        if sheets_store.enabled():
            rows, err = await sheets_store.fetch_orders(user_id, real_limit)
            if rows is not None:
                return (demo + rows)[:limit], err
            local = orders.list_for_user(user_id, real_limit)
            if local:
                return (demo + local)[:limit], "Журнал у таблиці недоступний; показано локальні дані."
            if demo:
                return demo[:limit], "Журнал у таблиці недоступний; реальні замовлення не показано."
            return None, err
        return (demo + orders.list_for_user(user_id, real_limit))[:limit], None

    return await order_pagination.load_page(fetch, page)


def _page_text(page: int, data: order_pagination.OrderPage, user_id: int) -> str:
    if not data.groups:
        if data.error:
            return "📋 <b>Мої замовлення</b>\n\n⚠️ Не вдалося завантажити історію. Спробуйте ще раз."
        if page:
            return "📋 <b>Мої замовлення</b>\n\nНа цій сторінці немає замовлень."
        return "📋 <b>Мої замовлення</b>\n\nПоки що замовлень немає."
    start = page * order_pagination.PAGE_SIZE + 1
    end = start + len(data.groups) - 1
    blocks = "\n\n".join(_summary(group, user_id) for group in data.groups)
    header = f"📋 <b>Мої замовлення</b> · сторінка {page + 1}\nПоказано {start}–{end}"
    warning = "\n\n⚠️ Показано локальні дані." if data.error else ""
    return f"{header}\n\n{blocks}{warning}"


def _page_keyboard(page: int, data: order_pagination.OrderPage):
    choices = [
        (index, str(group[0].get("order_no") or "?"),
         order_pagination.order_token(group))
        for index, group in enumerate(data.groups)
    ]
    return kb.my_orders_menu(page, data.has_next, choices)


async def _edit_orders(cb: CallbackQuery, text: str, markup):
    """Telegram reports unchanged text and keyboard as an error."""
    try:
        await cb.message.edit_text(text, reply_markup=markup,
                                   disable_web_page_preview=True)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise


async def _show_page(cb: CallbackQuery, page: int):
    await cb.answer()
    if page < 0 or page > 10000:
        return
    data = await _load_page(cb.from_user.id, page)
    await _edit_orders(cb, _page_text(page, data, cb.from_user.id),
                       _page_keyboard(page, data))


@router.callback_query(F.data == "my:orders")
async def cb_my_orders(cb: CallbackQuery):
    await _show_page(cb, 0)


@router.callback_query(F.data.startswith("my:page:"))
async def cb_my_page(cb: CallbackQuery):
    try:
        page = int((cb.data or "").split(":")[2])
    except (IndexError, ValueError):
        await cb.answer("Невірна сторінка", show_alert=True)
        return
    await _show_page(cb, page)


@router.callback_query(F.data.startswith("my:detail:"))
async def cb_my_detail(cb: CallbackQuery):
    parts = (cb.data or "").split(":")
    if len(parts) != 5:
        await cb.answer("Невірне посилання", show_alert=True)
        return
    try:
        page, index = int(parts[2]), int(parts[3])
    except ValueError:
        await cb.answer("Невірне посилання", show_alert=True)
        return
    if not (0 <= page <= 10000 and 0 <= index < order_pagination.PAGE_SIZE):
        await cb.answer("Невірне посилання", show_alert=True)
        return
    await cb.answer()
    data = await _load_page(cb.from_user.id, page)
    if index >= len(data.groups):
        await _edit_orders(cb, "Замовлення змінились. Оновіть список.",
                           kb.my_orders_menu(page, data.has_next))
        return
    group = data.groups[index]
    if order_pagination.order_token(group) != parts[4]:
        await _edit_orders(cb, "Список замовлень змінився. Оновіть сторінку.",
                           kb.my_orders_menu(page, data.has_next))
        return
    text = "📋 <b>Деталі замовлення</b>\n\n" + _fmt_group(group, cb.from_user.id)
    if len(text) > 3900:
        text = ("📋 <b>Деталі замовлення</b>\n\n"
                + _summary(group, cb.from_user.id)
                + "\n\nℹ️ У замовленні багато позицій. "
                  "Повні дані доступні в журналі замовлень.")
    await _edit_orders(cb, text, kb.my_order_detail_menu(page))


@router.message(Command("myorders"))
async def cmd_my_orders(msg: Message):
    data = await _load_page(msg.from_user.id, 0)
    await msg.answer(_page_text(0, data, msg.from_user.id),
                     reply_markup=_page_keyboard(0, data),
                     disable_web_page_preview=True)


@router.message(Command("orders"))
async def cmd_all_orders(msg: Message):
    """Адмін: останні замовлення всіх дропшиперів."""
    if not storage.is_admin(msg.from_user.id):
        return
    rows, err = (None, None)
    if sheets_store.enabled():
        rows, err = await sheets_store.fetch_orders(0, 10)   # 0 — усі дропшипери
    if not rows:
        await msg.answer("Замовлень не знайдено." + (f"\n⚠️ {err}" if err else ""))
        return
    blocks = "\n\n".join(_fmt_group(g) + f"\n👔 {g[0].get('dropshipper', '')}"
                          for g in group_orders(rows))
    await msg.answer(f"📋 <b>Останні замовлення</b>\n\n{blocks}"[:4000],
                     disable_web_page_preview=True)
