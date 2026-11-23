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

# Shared text for both the AI button and the /ai command.
AI_INTRO = (
    "✨ <b>AI-замовлення</b>\n\n"
    "Напишіть товар, розмір, кількість, ціну продажу за 1 шт., тип оплати, "
    "ПІБ отримувача, телефон, населений пункт та відділення Нової Пошти. "
    "Ви можете надсилати дані кількома повідомленнями.\n\n"
    "<b>Приклад:</b> <i>Українська Преміум, 2.3 м, 1 шт., "
    "ціна продажу 8500 грн, післяплата. "
    "Петренко Іван, 0671234567, Київ, відділення НП №45.</i>\n\n"
    "AI допоможе підготувати замовлення. ТТН створюється тільки після "
    "Вашого підтвердження та виконання умов оплати."
)


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


async def resume(target, state):
    current = await state.get_state()
    if (current or "").startswith("Order:"):
        step = current.split(":", 1)[1]
        if step in order.SHOW:
            await order.SHOW[step](target, state)
            return True
    if (current or "").startswith("AIOrder:"):
        if (await state.get_data()).get("ai_draft"):
            await validate(target, state)
        else:
            await send(target, AI_INTRO, cancel_kb())
        return True
    return False


@router.callback_query(F.data == "ai:new")
async def start_ai(cb: CallbackQuery, state: FSMContext):
    if not allowed(cb.from_user.id):
        await cb.answer("AI-замовлення доступні лише схваленим користувачам", show_alert=True)
        return
    await cb.answer()
    if await resume(cb, state):
        return
    await state.clear()
    await state.set_state(AIOrder.collect)
    await send(cb, AI_INTRO, cancel_kb())


@router.message(Command("ai"))
async def command_ai(msg: Message, state: FSMContext):
    if not allowed(msg.from_user.id):
        await msg.answer("AI-замовлення зараз недоступні.")
        return
    if await resume(msg, state):
        return
    await state.clear()
    await state.set_state(AIOrder.collect)
    await msg.answer(AI_INTRO, reply_markup=cancel_kb())


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
    await send(cb, "✏️ Напишіть, що саме потрібно змінити. Інші дані залишаться в чернетці.",
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
        await send(target, ("✨ <b>Уточніть дані:</b>\n\n" +
                           "\n".join("• " + escape(v) for v in issues[:12]) +
                           "\n\nНапишіть доповнення або виправлення.")[:3900], cancel_kb())
        return
    shortage = await order.cart_shortage(uid, draft["cart"])
    if shortage:
        await send(target, stock.not_enough_text(shortage) +
                   "\nВкажіть іншу кількість або товар.", cancel_kb())
        return
    await state.update_data(ai_plan=draft)
    try:
        found = await np.search_city_page(draft["city_query"])
    except np.NPError:
        await send(target, "⚠️ Не вдалося перевірити населений пункт через Нову Пошту. "
                   "Повторіть повідомлення пізніше.", cancel_kb())
        return
    cities = found["cities"]
    if not cities:
        await send(target, "⚠️ Нова Пошта не знайшла населений пункт. "
                   "Уточніть назву.", cancel_kb())
        return
    if len(cities) == 1 and not found["has_next"] and found["total"] <= 1:
        await find_warehouse(target, state, cities[0])
        return
    await state.update_data(ai_cities=cities, ai_city_query=draft["city_query"],
                            ai_city_page=1, ai_city_total=found["total"],
                            ai_city_has_next=found["has_next"])
    await state.set_state(AIOrder.choose_city)
    await send(target, "🏙 Уточніть населений пункт:",
               ai_city_kb(cities, 1, found["has_next"]))


def ai_city_kb(cities, page: int, has_next: bool):
    buttons = [[InlineKeyboardButton(
        text=c["label"][:90], callback_data=f"ai:city:{page}:{i}")]
        for i, c in enumerate(cities)]
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton(text="⬅️ Попередні",
                                       callback_data=f"ai:cityp:{page - 1}"))
    if has_next:
        nav.append(InlineKeyboardButton(text="Наступні ➡️",
                                       callback_data=f"ai:cityp:{page + 1}"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(text="✖️ Скасувати",
                                         callback_data="ai:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@router.callback_query(F.data.startswith("ai:cityp:"), AIOrder.choose_city)
async def ai_cities_page(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    page = int(cb.data.rsplit(":", 1)[1])
    if page < 1 or page > data["ai_city_page"] + 1 or (
            page > data["ai_city_page"] and not data.get("ai_city_has_next")):
        await cb.answer("Сторінка недоступна", show_alert=True)
        return
    try:
        found = await np.search_city_page(data["ai_city_query"], page=page)
    except np.NPError as e:
        await cb.answer(f"Помилка Нової Пошти: {e}"[:180], show_alert=True)
        return
    if not found["cities"]:
        await cb.answer("Більше населених пунктів немає", show_alert=True)
        return
    await state.update_data(ai_cities=found["cities"], ai_city_page=page,
                            ai_city_total=found["total"],
                            ai_city_has_next=found["has_next"])
    await cb.message.edit_text(
        f"🏙 Уточніть населений пункт (сторінка {page}):",
        reply_markup=ai_city_kb(found["cities"], page, found["has_next"]))
    await cb.answer()


@router.callback_query(F.data.startswith("ai:city:"), AIOrder.choose_city)
async def city_selected(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    try:
        _, _, page, index = cb.data.split(":")
        if int(page) != data["ai_city_page"]:
            raise ValueError("stale city selection")
        city = data["ai_cities"][int(index)]
    except (IndexError, ValueError, KeyError):
        await cb.answer("Сторінку змінено. Оберіть пункт ще раз.", show_alert=True)
        return
    await cb.answer()
    await find_warehouse(cb, state, city)


@router.callback_query(F.data == "ai:refresh", AIOrder.choose_warehouse)
async def refresh_office(cb: CallbackQuery, state: FSMContext):
    await cb.answer("Запитуємо Нову Пошту…")
    await find_warehouse(cb, state, (await state.get_data())["ai_city"], refresh=True)


async def find_warehouse(target, state, city, refresh=False):
    data = await state.get_data()
    await state.update_data(ai_city=city, ai_warehouses=[])
    await state.set_state(AIOrder.choose_warehouse)
    buttons = [[InlineKeyboardButton(text="🔄 Оновити (якщо не бачите відділення)",
                                     callback_data="ai:refresh")], *cancel_kb().inline_keyboard]
    retry = InlineKeyboardMarkup(inline_keyboard=buttons)
    try:
        warehouses = await np.city_warehouses(city["ref"], refresh=refresh)
    except np.NPError:
        await send(target, "⚠️ Нова Пошта не відповіла. Ваша чернетка збережена.", retry)
        return
    number = data["ai_plan"]["warehouse_number"]
    matches = [w for w in warehouses
               if logic.normalize_warehouse(w.get("number")) == number]
    if not matches:
        await send(target, f"⚠️ У населеному пункті {escape(city.get('label', city['name']))} немає відділення "
                   f"№{escape(number)}. Оновіть список або вкажіть інший номер.", retry)
        return
    if len(matches) == 1:
        await to_confirmation(target, state, city, matches[0])
    else:
        await state.update_data(ai_city=city, ai_warehouses=matches)
        await state.set_state(AIOrder.choose_warehouse)
        buttons = [[InlineKeyboardButton(
            text=w["name"][:55], callback_data=f"ai:wh:{i}")]
            for i, w in enumerate(matches[:8])]
        buttons.extend(retry.inline_keyboard)
        await send(target, "🏢 Уточніть відділення:",
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
