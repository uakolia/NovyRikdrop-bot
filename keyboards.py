"""Інлайн-клавіатури."""
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import aliases
import catalog

PER_PAGE = 8


def main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌲 Нове замовлення", callback_data="order:new")],
        [InlineKeyboardButton(text="📋 Мої замовлення", callback_data="my:orders")],
        [InlineKeyboardButton(text="ℹ️ Допомога", callback_data="help")],
        [InlineKeyboardButton(text="✍️ Повідомити про проблему",
                              callback_data="support:new")],
    ])


def support_only_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✍️ Написати менеджеру",
                              callback_data="support:new")],
    ])


def categories_kb():
    rows = [[InlineKeyboardButton(text=c, callback_data=f"cat:{i}")]
            for i, c in enumerate(catalog.categories())]
    rows.append([InlineKeyboardButton(text="✖️ Скасувати", callback_data="order:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def models_kb(cat_idx: int, page: int = 0, user_id: int | None = None):
    cats = catalog.categories()
    ms = catalog.models(cats[cat_idx])
    chunk = ms[page * PER_PAGE:(page + 1) * PER_PAGE]
    rows = [[InlineKeyboardButton(text=catalog.model_label(m, user_id),
                                  callback_data=f"mdl:{cat_idx}:{page * PER_PAGE + i}")]
            for i, m in enumerate(chunk)]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"mdlp:{cat_idx}:{page - 1}"))
    if (page + 1) * PER_PAGE < len(ms):
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"mdlp:{cat_idx}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="back:model"),
                 InlineKeyboardButton(text="✖️ Скасувати", callback_data="order:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def variants_kb(cat_idx: int, model_idx: int, user_id: int | None = None):
    cats = catalog.categories()
    model = catalog.models(cats[cat_idx])[model_idx]
    rows = []
    for i, v in enumerate(catalog.variants(model)):
        price = catalog.drop_price(v)
        label = f"{catalog.size_label(v)} — {price:,.0f} грн".replace(",", " ")
        rows.append([InlineKeyboardButton(text=label, callback_data=f"var:{i}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="back:variant"),
                 InlineKeyboardButton(text="✖️ Скасувати", callback_data="order:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def qty_kb():
    row = [InlineKeyboardButton(text=str(n), callback_data=f"qty:{n}") for n in range(1, 6)]
    return InlineKeyboardMarkup(inline_keyboard=[
        row,
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back:qty"),
         InlineKeyboardButton(text="✖️ Скасувати", callback_data="order:cancel")],
    ])


def payment_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📦 Післяплата (вся сума при отриманні)",
                              callback_data="pay:післяплата")],
        [InlineKeyboardButton(text="🔸 Часткова передплата",
                              callback_data="pay:часткова")],
        [InlineKeyboardButton(text="✅ Передплата (вже оплачено повністю)",
                              callback_data="pay:передплата")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back:payment"),
         InlineKeyboardButton(text="✖️ Скасувати", callback_data="order:cancel")],
    ])


def warehouses_kb(warehouses, page: int = 0, back_step: str = "warehouse"):
    chunk = warehouses[page * PER_PAGE:(page + 1) * PER_PAGE]
    rows = []
    for i, w in enumerate(chunk):
        mark = "🏗" if w["cargo"] else "🏢"
        limit = f" (до {w['max_weight']:g} кг)" if w.get("max_weight") else ""
        name = w["name"]
        if len(name) + len(limit) > 55:
            name = name[:52 - len(limit)] + "…"
        rows.append([InlineKeyboardButton(text=f"{mark} {name}{limit}",
                                          callback_data=f"wh:{page * PER_PAGE + i}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"whp:{page - 1}"))
    if (page + 1) * PER_PAGE < len(warehouses):
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"whp:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="⬅️ Назад",
                                      callback_data=f"back:{back_step}"),
                 InlineKeyboardButton(text="✖️ Скасувати", callback_data="order:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Підтвердити замовлення", callback_data="confirm:yes")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back:confirm"),
         InlineKeyboardButton(text="✖️ Скасувати", callback_data="order:cancel")],
    ])


def approve_kb(user_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Схвалити", callback_data=f"approve:{user_id}"),
         InlineKeyboardButton(text="🚫 Відхилити", callback_data=f"deny:{user_id}")],
    ])
