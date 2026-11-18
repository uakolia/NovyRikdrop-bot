"""AI Telegram order intake, gated pilot. Existing order confirmation owns writes."""
from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import ai_order_core as logic
import ai_order_access
import ai_order_extract as extract
import aliases
import catalog
import config
import handlers_order as order
import keyboards as kb
import novaposhta as np
import stock
import storage

router = Router()


class AIOrder(StatesGroup):
    collect = State()
    choose_city = State()
    choose_warehouse = State()


def allowed(uid: int) -> bool:
    return ai_order_access.allowed(uid)


def cancel_kb():
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✖️ Скасувати AI-замовлення", callback_data="ai:cancel")
    ]])


async def send(target, text, keyboard=None):
    if isinstance(target, CallbackQuery):
        await target.message.answer(text, reply_markup=keyboard)
    else:
        await target.answer(text, reply_markup=keyboard)


def catalog_entries(user_id):
    """SKU and prices are sourced from trusted catalog, never the model."""
    rows = []
    for item in catalog.items():
        article = item["article"]
        names = [item["model_ua"], catalog.product_name(user_id, item),
                 stock.cached_name(user_id, article),
                 aliases.name_by_article(user_id, article, "")]
        rows.append({"article": article, "size": catalog.size_label(item),
                     "names": [n for n in names if n]})
    return rows


@router.callback_query(F.data == "ai:new")
async def start_ai(cb: CallbackQuery, state: FSMContext):
    if not allowed(cb.from_user.id):
        await cb.answer("AI-замовлення доступні лише адміністратору або тестовому акаунту", show_alert=True)
        return
    await state.clear()
    await state.set_state(AIOrder.collect)
    await cb.answer()
    await send(cb,
        "✨ <b>AI-замовлення (пілот)</b>\n\n"
        "Напиши товар, розмір, кількість, ціну продажу за одиницю, тип оплати, "
        "ПІБ, телефон, місто та відділення НП. Можна надсилати дані частинами.\n\n"
        "Приклад: <i>Українська Люкс 2.3м, 1 шт, продаж 8500 грн, післяплата. "
        "Петренко Іван, 0671234567, Київ, НП 45.</i>\n\n"
        "AI тільки готує чернетку. ТТН створюється виключно після твого підтвердження.",
        cancel_kb())


@router.message(Command("ai"))
async def command_ai(msg: Message, state: FSMContext):
    if not allowed(msg.from_user.id):
        await msg.answer("AI-замовлення зараз недоступні.")
        return
    await state.clear()
    await state.set_state(AIOrder.collect)
    await msg.answer(
        "✨ <b>AI-замовлення</b>\nВведи замовлення українською. "
        "Ціна продажу має бути вказана за кожну позицію. "
        "Можна доповнювати повідомленнями.",
        reply_markup=cancel_kb())


@router.callback_query(F.data == "ai:cancel")
async def cancel_ai(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.answer()
    await send(cb, "AI-замовлення скасовано.", kb.main_menu(cb.from_user.id))


@router.callback_query(F.data == "ai:edit", order.Order.confirm)
async def edit_ai(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data.get("ai_draft"):
        await cb.answer("Це звичайне замовлення", show_alert=True)
        return
    await state.set_state(AIOrder.collect)
    await cb.answer()
    await send(cb, "✏️ Напиши, що саме змінити. Інші дані залишаться в чернетці.",
               cancel_kb())


@router.message(AIOrder.collect, F.text)
@router.message(AIOrder.choose_city, F.text)
@router.message(AIOrder.choose_warehouse, F.text)
async def ai_text(msg: Message, state: FSMContext):
    if not allowed(msg.from_user.id):
        await state.clear()
        return
    if msg.text.strip().casefold() in ("скасувати", "відміна", "cancel"):
        await state.clear()
        await msg.answer("Замовлення скасовано.", reply_markup=kb.main_menu(msg.from_user.id))
        return
    data = await state.get_data()
    try:
        draft = await extract.extract(msg.text, data.get("ai_draft"))
    except extract.AIOrderError as exc:
        await state.set_state(AIOrder.collect)
        await msg.answer("⚠️ " + escape(str(exc)), reply_markup=cancel_kb())
        return
    await state.update_data(ai_draft=draft)
    await state.set_state(AIOrder.collect)
    await validate(msg, state)


async def validate(target, state):
    uid = target.from_user.id
    data = await state.get_data()
    await stock.rows_for(uid)
    draft, issues = logic.plan(data.get("ai_draft") or {}, catalog_entries(uid))
    if issues:
        await send(target, ("✨ <b>Уточни дані:</b>\n\n" +
                           "\n".join("• " + escape(v) for v in issues[:12]) +
                           "\n\nНапиши доповнення або виправлення.")[:3900], cancel_kb())
        return
    shortage = await order.cart_shortage(uid, draft["cart"])
    if shortage:
        await send(target, stock.not_enough_text(shortage) +
                   "\nНапиши іншу кількість або товар.", cancel_kb())
        return
    await state.update_data(ai_plan=draft)
    try:
        cities = await np.search_cities(draft["city_query"], limit=8)
    except np.NPError:
        await send(target, "⚠️ Не вдалося перевірити місто через Нову Пошту. "
                   "Повтори повідомлення пізніше.", cancel_kb())
        return
    if not cities:
        await send(target, "⚠️ Нова Пошта не знайшла місто. Уточни назву.", cancel_kb())
        return
    name = draft["city_query"].casefold().removeprefix("м.").strip()
    exact = [c for c in cities if c["name"].casefold().removeprefix("м.").strip() == name]
    if len(exact) == 1:
        await find_warehouse(target, state, exact[0])
    elif len(cities) == 1:
        await find_warehouse(target, state, cities[0])
    else:
        await state.update_data(ai_cities=cities)
        await state.set_state(AIOrder.choose_city)
        buttons = [[InlineKeyboardButton(
            text=f"{c['name']} ({c.get('area', '')})"[:60], callback_data=f"ai:city:{i}")]
            for i, c in enumerate(cities)]
        buttons.append([InlineKeyboardButton(text="✖️ Скасувати", callback_data="ai:cancel")])
        await send(target, "🏙 Уточни місто:", InlineKeyboardMarkup(inline_keyboard=buttons))


@router.callback_query(F.data.startswith("ai:city:"), AIOrder.choose_city)
async def city_selected(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    try:
        city = data["ai_cities"][int(cb.data.rsplit(":", 1)[1])]
    except (IndexError, ValueError, KeyError):
        await cb.answer("Невірне місто", show_alert=True)
        return
    await cb.answer()
    await find_warehouse(cb, state, city)


async def find_warehouse(target, state, city):
    data = await state.get_data()
    try:
        warehouses = await np.city_warehouses(city["ref"])
    except np.NPError:
        await state.set_state(AIOrder.collect)
        await send(target, "⚠️ Нова Пошта не повернула відділення. Спробуй ще раз.",
                   cancel_kb())
        return
    number = data["ai_plan"]["warehouse_number"]
    matches = [w for w in warehouses
               if logic.normalize_warehouse(w.get("number")) == number]
    if not matches:
        await state.set_state(AIOrder.collect)
        await send(target, f"⚠️ В місті {escape(city['name'])} немає відділення "
                   f"№{escape(number)}. Вкажи інший номер.", cancel_kb())
        return
    if len(matches) == 1:
        await to_confirmation(target, state, city, matches[0])
    else:
        await state.update_data(ai_city=city, ai_warehouses=matches)
        await state.set_state(AIOrder.choose_warehouse)
        buttons = [[InlineKeyboardButton(
            text=w["name"][:55], callback_data=f"ai:wh:{i}")]
            for i, w in enumerate(matches[:8])]
        buttons.append([InlineKeyboardButton(text="✖️ Скасувати", callback_data="ai:cancel")])
        await send(target, "🏢 Уточни відділення:",
                   InlineKeyboardMarkup(inline_keyboard=buttons))


@router.callback_query(F.data.startswith("ai:wh:"), AIOrder.choose_warehouse)
async def warehouse_selected(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    try:
        warehouse = data["ai_warehouses"][int(cb.data.rsplit(":", 1)[1])]
        city = data["ai_city"]
    except (IndexError, ValueError, KeyError):
        await cb.answer("Невірне відділення", show_alert=True)
        return
    await cb.answer()
    await to_confirmation(cb, state, city, warehouse)


async def to_confirmation(target, state, city, warehouse):
    """No shipping or writes here: existing confirm:yes remains authoritative."""
    p = (await state.get_data())["ai_plan"]
    await state.update_data(
        cart=p["cart"], payment=p["payment"], sale_price=p["sale_price"],
        prepaid=p["prepaid"], cod_amount=p["cod_amount"],
        fio=p["fio"], fname=p["fname"], lname=p["lname"], mname=p["mname"],
        phone=p["phone"], city=city, warehouse=warehouse, to_door=False,
    )
    await order.show_confirm(target, state)
