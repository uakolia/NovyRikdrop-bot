"""Сценарій оформлення замовлення дропшипером."""
import re

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

import catalog, config, keyboards as kb, novaposhta as np, orders, storage

router = Router()


class Order(StatesGroup):
    category = State()
    model = State()
    variant = State()
    qty = State()
    payment = State()
    sale_price = State()
    prepaid = State()
    lname = State()
    fname = State()
    mname = State()
    phone = State()
    city = State()
    city_pick = State()
    warehouse = State()
    confirm = State()


def _approved(user_id: int) -> bool:
    return storage.is_approved(user_id)


@router.callback_query(F.data == "order:new")
async def start_order(cb: CallbackQuery, state: FSMContext):
    if not _approved(cb.from_user.id):
        await cb.answer("Доступ ще не схвалено", show_alert=True)
        return
    await state.clear()
    await state.set_state(Order.category)
    await cb.message.edit_text("🌲 <b>Нове замовлення</b>\n\nОберіть категорію:",
                               reply_markup=kb.categories_kb())
    await cb.answer()


@router.callback_query(F.data == "order:cancel")
async def cancel_order(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.message.edit_text("Замовлення скасовано.", reply_markup=kb.main_menu())
    await cb.answer()


@router.callback_query(F.data.startswith("cat:"))
async def pick_category(cb: CallbackQuery, state: FSMContext):
    cat_idx = int(cb.data.split(":")[1])
    await state.update_data(cat_idx=cat_idx)
    await state.set_state(Order.model)
    cat = catalog.categories()[cat_idx]
    await cb.message.edit_text(f"Категорія: <b>{cat}</b>\n\nОберіть модель:",
                               reply_markup=kb.models_kb(cat_idx))
    await cb.answer()


@router.callback_query(F.data.startswith("mdlp:"))
async def models_page(cb: CallbackQuery, state: FSMContext):
    _, cat_idx, page = cb.data.split(":")
    await state.set_state(Order.model)
    cat = catalog.categories()[int(cat_idx)]
    await cb.message.edit_text(f"Категорія: <b>{cat}</b>\n\nОберіть модель:",
                               reply_markup=kb.models_kb(int(cat_idx), int(page)))
    await cb.answer()


@router.callback_query(F.data.startswith("mdl:"))
async def pick_model(cb: CallbackQuery, state: FSMContext):
    _, cat_idx, model_idx = cb.data.split(":")
    cat_idx, model_idx = int(cat_idx), int(model_idx)
    cat = catalog.categories()[cat_idx]
    model = catalog.models(cat)[model_idx]
    await state.update_data(cat_idx=cat_idx, model_idx=model_idx, model=model)
    await state.set_state(Order.variant)
    await cb.message.edit_text(
        f"Модель: <b>{catalog.model_label(model)}</b>\n\nОберіть розмір (ціни — ваш дроп-тариф):",
        reply_markup=kb.variants_kb(cat_idx, model_idx))
    await cb.answer()


@router.callback_query(F.data.startswith("var:"), Order.variant)
async def pick_variant(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    v = catalog.find_variant_short(data["model"], int(cb.data.split(":")[1]))
    if not v:
        await cb.answer("Не знайдено", show_alert=True)
        return
    await state.update_data(article=v["article"])
    await state.set_state(Order.qty)
    await cb.message.edit_text(
        f"🌲 <b>{v['model_ua']}</b> — {catalog.size_label(v)}\n"
        f"Артикул: <code>{v['article']}</code>\n"
        f"Вага: ~{v['weight_kg']:g} кг\n\n"
        "Кількість:",
        reply_markup=kb.qty_kb())
    await cb.answer()


@router.callback_query(F.data.startswith("qty:"), Order.qty)
async def pick_qty(cb: CallbackQuery, state: FSMContext):
    await state.update_data(qty=int(cb.data.split(":")[1]))
    await state.set_state(Order.payment)
    await cb.message.edit_text(
        "💳 <b>Тип оплати</b>\n\n"
        "📦 <b>Післяплата</b> — клієнт платить всю суму при отриманні\n"
        "🔸 <b>Часткова передплата</b> — частину сплачено, решта при отриманні\n"
        "✅ <b>Передплата</b> — товар уже повністю оплачено",
        reply_markup=kb.payment_kb())
    await cb.answer()


FIO_PROMPT = ("👤 Введіть <b>прізвище</b> отримувача\n"
              "(наприклад: <i>Шевченко</i>)")

NAME_RE = re.compile(r"[А-ЯІЇЄҐа-яіїєґA-Za-z'’\-]{2,40}")


def _valid_name(text: str) -> str | None:
    name = text.strip()
    return name if NAME_RE.fullmatch(name) else None


def _amount(text: str) -> int | None:
    digits = re.sub(r"[^\d]", "", text)
    if digits.isdigit() and 50 <= int(digits) <= 500000:
        return int(digits)
    return None


@router.callback_query(F.data.startswith("pay:"), Order.payment)
async def pick_payment(cb: CallbackQuery, state: FSMContext):
    payment = cb.data.split(":", 1)[1]
    await state.update_data(payment=payment)
    if payment == "передплата":
        await state.update_data(sale_price=0, prepaid=0, cod_amount=0)
        await state.set_state(Order.lname)
        await cb.message.edit_text(FIO_PROMPT)
    else:
        await state.set_state(Order.sale_price)
        await cb.message.edit_text(
            "💰 Введіть <b>вашу ціну продажу</b> для клієнта, грн\n"
            "(наприклад: <i>4500</i>)")
    await cb.answer()


@router.message(Order.sale_price, F.text)
async def input_sale_price(msg: Message, state: FSMContext):
    amount = _amount(msg.text)
    if amount is None:
        await msg.answer("⚠️ Введіть суму числом у гривнях, наприклад <i>4500</i>:")
        return
    data = await state.get_data()
    await state.update_data(sale_price=amount)
    if data["payment"] == "часткова":
        await state.set_state(Order.prepaid)
        await msg.answer(f"🔸 Ціна продажу: {amount} грн\n\n"
                         "Скільки клієнт <b>уже передплатив</b>, грн?")
    else:  # післяплата — вся сума при отриманні
        await state.update_data(prepaid=0, cod_amount=amount)
        await state.set_state(Order.lname)
        await msg.answer(FIO_PROMPT)


@router.message(Order.prepaid, F.text)
async def input_prepaid(msg: Message, state: FSMContext):
    digits = re.sub(r"[^\d]", "", msg.text)
    prepaid = int(digits) if digits.isdigit() else None
    if prepaid is None:
        await msg.answer("⚠️ Введіть суму передплати числом, наприклад <i>1000</i>:")
        return
    data = await state.get_data()
    sale = data["sale_price"]
    if prepaid <= 0 or prepaid >= sale:
        await msg.answer(f"⚠️ Передплата має бути більшою за 0 і меншою за ціну "
                         f"продажу ({sale} грн). Спробуйте ще раз:")
        return
    await state.update_data(prepaid=prepaid, cod_amount=sale - prepaid)
    await state.set_state(Order.lname)
    await msg.answer(f"✅ При отриманні клієнт сплатить: <b>{sale - prepaid} грн</b>\n\n"
                     + FIO_PROMPT)


@router.message(Order.lname, F.text)
async def input_lname(msg: Message, state: FSMContext):
    name = _valid_name(msg.text)
    if not name:
        await msg.answer("⚠️ Введіть лише <b>прізвище</b>, одним словом "
                         "(наприклад: <i>Шевченко</i>):")
        return
    await state.update_data(lname=name)
    await state.set_state(Order.fname)
    await msg.answer("Тепер введіть <b>ім'я</b> отримувача\n(наприклад: <i>Тарас</i>)")


@router.message(Order.fname, F.text)
async def input_fname(msg: Message, state: FSMContext):
    name = _valid_name(msg.text)
    if not name:
        await msg.answer("⚠️ Введіть лише <b>ім'я</b>, одним словом "
                         "(наприклад: <i>Тарас</i>):")
        return
    await state.update_data(fname=name)
    await state.set_state(Order.mname)
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    await msg.answer(
        "Введіть <b>по-батькові</b> (необов'язково)\n(наприклад: <i>Григорович</i>)",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="⏭ Пропустити", callback_data="mname:skip")]]))


async def _fio_done(state: FSMContext, mname: str = ""):
    data = await state.get_data()
    fio = " ".join(x for x in (data["lname"], data["fname"], mname) if x)
    await state.update_data(fio=fio)
    await state.set_state(Order.phone)
    return fio


@router.message(Order.mname, F.text)
async def input_mname(msg: Message, state: FSMContext):
    name = _valid_name(msg.text)
    if not name:
        await msg.answer("⚠️ Введіть по-батькові одним словом, або натисніть "
                         "«Пропустити» вище:")
        return
    fio = await _fio_done(state, name)
    await msg.answer(f"👤 Отримувач: <b>{fio}</b>\n\n"
                     "📞 Введіть <b>номер телефону отримувача</b>\n"
                     "(наприклад: <i>0671234567</i> або <i>+380671234567</i>)")


@router.callback_query(F.data == "mname:skip", Order.mname)
async def skip_mname(cb: CallbackQuery, state: FSMContext):
    fio = await _fio_done(state)
    await cb.message.edit_text(f"👤 Отримувач: <b>{fio}</b>\n\n"
                               "📞 Введіть <b>номер телефону отримувача</b>\n"
                               "(наприклад: <i>0671234567</i> або <i>+380671234567</i>)")
    await cb.answer()


@router.message(Order.phone, F.text)
async def input_phone(msg: Message, state: FSMContext):
    digits = re.sub(r"\D", "", msg.text)
    if digits.startswith("380") and len(digits) == 12:
        phone = "+" + digits
    elif digits.startswith("0") and len(digits) == 10:
        phone = "+38" + digits
    else:
        await msg.answer("⚠️ Невірний формат. Введіть український номер, "
                         "наприклад <i>0671234567</i>:")
        return
    await state.update_data(phone=phone)
    await state.set_state(Order.city)
    await msg.answer("🏙 Введіть <b>місто отримувача</b> (наприклад: <i>Львів</i>)")


@router.message(Order.city, F.text)
async def input_city(msg: Message, state: FSMContext):
    q = msg.text.strip()
    if len(q) < 2:
        await msg.answer("⚠️ Введіть назву міста:")
        return
    try:
        cities = await np.search_cities(q)
    except np.NPError as e:
        await msg.answer(f"⚠️ Помилка Нової Пошти: {e}\nСпробуйте ще раз:")
        return
    if not cities:
        await msg.answer("⚠️ Міст не знайдено. Перевірте назву і спробуйте ще раз:")
        return
    await state.update_data(cities=cities)
    await state.set_state(Order.city_pick)
    await msg.answer("Оберіть місто:", reply_markup=kb.cities_kb(cities))


@router.callback_query(F.data == "city:again")
async def city_again(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Order.city)
    await cb.message.edit_text("🏙 Введіть <b>місто отримувача</b>:")
    await cb.answer()


@router.callback_query(F.data.startswith("city:"), Order.city_pick)
async def pick_city(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    city = data["cities"][int(cb.data.split(":")[1])]
    await state.update_data(city=city)
    try:
        whs = await np.cargo_warehouses(city["ref"])
    except np.NPError as e:
        await cb.message.edit_text(f"⚠️ Помилка Нової Пошти: {e}")
        await cb.answer()
        return
    if not whs:
        await state.set_state(Order.city)
        await cb.message.edit_text(
            f"⚠️ У місті <b>{city['name']}</b> немає вантажних відділень "
            "(потрібне відділення, що приймає понад 30 кг).\n\n"
            "Введіть інше місто (наприклад, найближче велике):")
        await cb.answer()
        return
    await state.update_data(warehouses=whs)
    await state.set_state(Order.warehouse)
    await cb.message.edit_text(
        f"📍 <b>{city['name']}</b> — оберіть <b>вантажне відділення</b> "
        f"(🏗 вантажні, приймають понад 30 кг):",
        reply_markup=kb.warehouses_kb(whs))
    await cb.answer()


@router.callback_query(F.data.startswith("whp:"), Order.warehouse)
async def warehouses_page(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    page = int(cb.data.split(":")[1])
    await cb.message.edit_reply_markup(reply_markup=kb.warehouses_kb(data["warehouses"], page))
    await cb.answer()


@router.callback_query(F.data.startswith("wh:"), Order.warehouse)
async def pick_warehouse(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    wh = data["warehouses"][int(cb.data.split(":")[1])]
    await state.update_data(warehouse=wh)
    item = catalog.by_article(data["article"])
    price = catalog.drop_price(item)
    qty = data.get("qty", 1)
    await state.set_state(Order.confirm)
    total = f"{price * qty:,.0f}".replace(",", " ")
    pay = _payment_lines(data)
    await cb.message.edit_text(
        "📋 <b>Перевірте замовлення</b>\n\n"
        f"🌲 {item['model_ua']} — {catalog.size_label(item)}\n"
        f"Артикул: <code>{item['article']}</code> × {qty}\n"
        f"💰 Дроп-ціна: {total} грн\n"
        f"{pay}\n\n"
        f"👤 {data['fio']}\n"
        f"📞 {data['phone']}\n"
        f"📍 {data['city']['name']}, {wh['name']}\n\n"
        "Все вірно?",
        reply_markup=kb.confirm_kb())
    await cb.answer()


def _payment_lines(data) -> str:
    p = data["payment"]
    if p == "передплата":
        return "💳 Оплата: <b>передплата</b> (оплачено повністю)"
    if p == "часткова":
        return (f"💳 Оплата: <b>часткова передплата</b>\n"
                f"   Ціна продажу: {data['sale_price']} грн, "
                f"передплачено: {data['prepaid']} грн\n"
                f"   💵 При отриманні: <b>{data['cod_amount']} грн</b>")
    return (f"💳 Оплата: <b>післяплата</b>\n"
            f"   💵 При отриманні: <b>{data['cod_amount']} грн</b>")


@router.callback_query(F.data == "confirm:yes", Order.confirm)
async def confirm_order(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    item = catalog.by_article(data["article"])
    qty = data.get("qty", 1)
    price = catalog.drop_price(item)
    user = cb.from_user
    order = orders.new_order(
        order_no=storage.next_order_no(),
        source="telegram",
        dropshipper_id=user.id,
        dropshipper=f"@{user.username}" if user.username else user.full_name,
        article=item["article"],
        product=item["model_ua"],
        size=catalog.size_label(item),
        qty=qty,
        price_drop=int(price * qty),
        payment=("часткова передплата" if data["payment"] == "часткова"
                 else data["payment"]),
        sale_price=data.get("sale_price") or "",
        prepaid=data.get("prepaid") or "",
        cod_amount=data.get("cod_amount") or "",
        recipient_fio=data["fio"],
        recipient_phone=data["phone"],
        city=data["city"]["name"],
        warehouse=data["warehouse"]["name"],
    )
    await state.clear()
    await cb.message.edit_text("⏳ Оформлюю замовлення…")
    await cb.answer()

    # 1) ТТН Нової Пошти
    ttn_note = ""
    if config.NP_AUTO_TTN:
        try:
            desc = f"Штучна ялинка {item['model_ua']} {catalog.size_label(item)}"
            res = await np.create_ttn(
                recipient_city_ref=data["city"]["ref"],
                recipient_warehouse_ref=data["warehouse"]["ref"],
                fio=data["fio"], phone=data["phone"],
                description=desc, cost=price * qty,
                weight=(item["weight_kg"] or 5) * qty,
                volume=(item.get("volume_m3") or 0) * qty or None,
                seats=qty,
                cod_amount=data.get("cod_amount") or 0,
            )
            order["ttn"] = res["ttn"]
            order["status"] = "ТТН створено"
            ttn_note = (f"\n📦 <b>ТТН: <code>{res['ttn']}</code></b>"
                        + (f"\n🗓 Орієнтовна доставка: {res['estimated_date']}"
                           if res.get("estimated_date") else ""))
        except Exception as e:  # noqa: BLE001
            order["status"] = "ТТН НЕ створено"
            order["comment"] = f"Помилка ТТН: {e}"
            ttn_note = ("\n⚠️ ТТН не вдалося створити автоматично — "
                        "менеджер оформить вручну.")

    # 2) журнал: CSV + Google Таблиця
    orders.save_csv(order)
    sheet_err = await orders.send_to_sheet(order)
    if sheet_err:
        order["comment"] = (order.get("comment", "") + f" | Sheet: {sheet_err}").strip(" |")

    # 3) сповіщення адміну
    if config.ADMIN_CHAT_ID:
        try:
            await cb.bot.send_message(config.ADMIN_CHAT_ID, orders.admin_text(order))
        except Exception:  # noqa: BLE001
            pass

    await cb.message.edit_text(
        f"✅ <b>Замовлення №{order['order_no']} прийнято!</b>\n\n"
        f"🌲 {order['product']} — {order['size']} × {qty}\n"
        f"👤 {order['recipient_fio']}\n"
        f"📍 {order['city']}, {order['warehouse']}\n"
        f"💳 {order['payment']}"
        f"{ttn_note}\n\n"
        "Дякуємо! 🎄",
        reply_markup=kb.main_menu())
