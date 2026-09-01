"""Інлайн-клавіатури."""
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import catalog

PER_PAGE = 8


def main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌲 Нове замовлення", callback_data="order:new")],
        [InlineKeyboardButton(text="ℹ️ Допомога", callback_data="help")],
    ])


def categories_kb():
    rows = [[InlineKeyboardButton(text=c, callback_data=f"cat:{i}")]
            for i, c in enumerate(catalog.categories())]
    rows.append([InlineKeyboardButton(text="✖️ Скасувати", callback_data="order:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def models_kb(cat_idx: int, page: int = 0):
    cats = catalog.categories()
    ms = catalog.models(cats[cat_idx])
    chunk = ms[page * PER_PAGE:(page + 1) * PER_PAGE]
    rows = [[InlineKeyboardButton(text=m, callback_data=f"mdl:{cat_idx}:{page * PER_PAGE + i}")]
            for i, m in enumerate(chunk)]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"mdlp:{cat_idx}:{page - 1}"))
    if (page + 1) * PER_PAGE < len(ms):
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"mdlp:{cat_idx}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="↩️ Категорії", callback_data="order:new"),
                 InlineKeyboardButton(text="✖️ Скасувати", callback_data="order:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def variants_kb(cat_idx: int, model_idx: int):
    cats = catalog.categories()
    model = catalog.models(cats[cat_idx])[model_idx]
    rows = []
    for i, v in enumerate(catalog.variants(model)):
        price = catalog.drop_price(v)
        label = f"{catalog.size_label(v)} — {price:,.0f} грн".replace(",", " ")
        rows.append([InlineKeyboardButton(text=label, callback_data=f"var:{i}")])
    rows.append([InlineKeyboardButton(text="↩️ Моделі", callback_data=f"mdlp:{cat_idx}:0"),
                 InlineKeyboardButton(text="✖️ Скасувати", callback_data="order:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def qty_kb():
    row = [InlineKeyboardButton(text=str(n), callback_data=f"qty:{n}") for n in range(1, 6)]
    return InlineKeyboardMarkup(inline_keyboard=[
        row,
        [InlineKeyboardButton(text="✖️ Скасувати", callback_data="order:cancel")],
    ])


def payment_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📦 Післяплата (клієнт платить при отриманні)",
                              callback_data="pay:післяплата")],
        [InlineKeyboardButton(text="✅ Передплата (вже оплачено)",
                              callback_data="pay:передплата")],
        [InlineKeyboardButton(text="✖️ Скасувати", callback_data="order:cancel")],
    ])


def cities_kb(cities):
    rows = [[InlineKeyboardButton(text=f"{c['name']} ({c['area']} обл.)",
                                  callback_data=f"city:{i}")]
            for i, c in enumerate(cities)]
    rows.append([InlineKeyboardButton(text="🔄 Ввести іншу назву", callback_data="city:again"),
                 InlineKeyboardButton(text="✖️ Скасувати", callback_data="order:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def warehouses_kb(warehouses, page: int = 0):
    chunk = warehouses[page * PER_PAGE:(page + 1) * PER_PAGE]
    rows = []
    for i, w in enumerate(chunk):
        mark = "🏗" if w["cargo"] else "🏢"
        name = w["name"]
        if len(name) > 55:
            name = name[:52] + "…"
        rows.append([InlineKeyboardButton(text=f"{mark} {name}",
                                          callback_data=f"wh:{page * PER_PAGE + i}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"whp:{page - 1}"))
    if (page + 1) * PER_PAGE < len(warehouses):
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"whp:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="↩️ Інше місто", callback_data="city:again"),
                 InlineKeyboardButton(text="✖️ Скасувати", callback_data="order:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Підтвердити замовлення", callback_data="confirm:yes")],
        [InlineKeyboardButton(text="✖️ Скасувати", callback_data="order:cancel")],
    ])


def approve_kb(user_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Схвалити", callback_data=f"approve:{user_id}"),
         InlineKeyboardButton(text="🚫 Відхилити", callback_data=f"deny:{user_id}")],
    ])
