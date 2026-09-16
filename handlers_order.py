"""Сценарій оформлення замовлення дропшипером (з кнопками «Назад»)."""
import re

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (CallbackQuery, InlineKeyboardButton,
                           InlineKeyboardMarkup, Message)

import aliases
import article_key
import catalog, config, keyboards as kb, novaposhta as np, orders, payment, storage
import perf
import stock

router = Router()


class Order(StatesGroup):
    category = State()
    model = State()
    variant = State()
    qty = State()
    cart = State()
    payment = State()
    sale_price = State()
    prepaid = State()
    lname = State()
    fname = State()
    mname = State()
    phone = State()
    city = State()
    city_pick = State()
    delivery = State()
    warehouse = State()
    street = State()
    street_pick = State()
    building = State()
    flat = State()
    confirm = State()
    payment_proof = State()


# з якого кроку куди веде «⬅️ Назад»
PREV = {
    "model": "category", "variant": "model", "qty": "variant",
    "payment": "cart", "sale_price": "payment", "prepaid": "sale_price",
    "lname": "payment", "fname": "lname", "mname": "fname",
    "phone": "mname", "city": "phone", "city_pick": "city",
    "delivery": "city_pick", "warehouse": "delivery", "street": "delivery",
    "street_pick": "street", "building": "street_pick", "flat": "building",
    "confirm": "delivery", "payment_proof": "confirm",
}


def _back_kb(step: str, extra_rows=None):
    rows = list(extra_rows or [])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"back:{step}"),
                 InlineKeyboardButton(text="✖️ Скасувати", callback_data="order:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _skip_back_kb(step: str, skip_data: str, skip_text: str):
    return _back_kb(step, [[InlineKeyboardButton(text=skip_text,
                                                 callback_data=skip_data)]])


def _uid(target) -> int | None:
    u = getattr(target, "from_user", None)
    return u.id if u else None


async def _send(target, text, **kw):
    """Показати крок: для callback — редагуванням, для message — новим."""
    if isinstance(target, CallbackQuery):
        try:
            await target.message.edit_text(text, **kw)
            return
        except Exception:  # noqa: BLE001
            await target.message.answer(text, **kw)
            return
    await target.answer(text, **kw)


# ---------------------------------------------------------------- кроки

async def show_category(target, state: FSMContext):
    await state.set_state(Order.category)
    await _send(target, "🌲 <b>Нове замовлення</b>\n\nОберіть категорію:",
                reply_markup=kb.categories_kb())


async def show_model(target, state: FSMContext, page: int = 0):
    data = await state.get_data()
    # без прогрітих залишків не видно, які моделі в передзамовленні
    await stock.rows_for(_uid(target))
    await state.set_state(Order.model)
    cat = catalog.categories()[data["cat_idx"]]
    uid = _uid(target)
    hint = ("\n\n⭐ — ваше передзамовлення, ці моделі вгорі списку"
            if any(catalog.model_in_stock(uid, m)
                   for m in catalog.models_for(uid, cat)) else "")
    await _send(target, f"Категорія: <b>{cat}</b>\n\nОберіть модель:{hint}",
                reply_markup=kb.models_kb(data["cat_idx"], page, uid))


async def show_variant(target, state: FSMContext):
    data = await state.get_data()
    await stock.rows_for(_uid(target))          # прогріти залишки для підписів
    await state.set_state(Order.variant)
    await _send(target,
                f"Модель: <b>{catalog.model_label(data['model'], _uid(target))}</b>"
                "\n\nОберіть розмір (ціни — ваш дроп-тариф):",
                reply_markup=kb.variants_kb(data["cat_idx"], data["model_idx"],
                                            _uid(target)))


def cart_of(data) -> list[dict]:
    """Позиції замовлення: [{article, qty}]. Порожній список — кошик чистий."""
    return list(data.get("cart") or [])


def cart_qty(cart, article: str) -> int:
    """Скільки цього артикула вже в кошику (щоб не обійти залишок двома порціями)."""
    key = article_key.canon(article)
    return sum(int(i["qty"]) for i in cart
               if article_key.canon(i["article"]) == key)


def cart_add(cart, article: str, qty: int) -> list[dict]:
    """Той самий артикул не дублюємо рядком, а збільшуємо кількість."""
    key = article_key.canon(article)
    for item in cart:
        if article_key.canon(item["article"]) == key:
            item["qty"] = int(item["qty"]) + int(qty)
            return cart
    cart.append({"article": article, "qty": int(qty)})
    return cart


def cart_price(user_id, item_row) -> int:
    v = catalog.by_article(item_row["article"])
    return int(catalog.drop_price(v, user_id) * int(item_row["qty"]))


def cart_total(user_id, cart) -> int:
    return sum(cart_price(user_id, i) for i in cart)


async def show_cart(target, state: FSMContext):
    """Кошик: що вже обрано, і куди далі."""
    data = await state.get_data()
    cart = cart_of(data)
    uid = _uid(target)
    if not cart:
        await show_category(target, state)
        return
    await state.set_state(Order.cart)
    lines = ["🧺 <b>Ваше замовлення</b>\n"]
    buttons = []
    for i, it in enumerate(cart):
        v = catalog.by_article(it["article"])
        name = catalog.product_name(uid, v)
        price = cart_price(uid, it)
        lines.append(f"{i + 1}. {name} — {catalog.size_label(v)}\n"
                     f"    <code>{it['article']}</code> × {it['qty']} = "
                     f"{price:,} грн".replace(",", " "))
        buttons.append({"idx": i,
                        "label": f"{name} × {it['qty']}"[:40]})
    lines.append(f"\n💰 <b>Разом: {cart_total(uid, cart):,} грн</b>"
                 .replace(",", " "))
    lines.append(f"\nУсі позиції поїдуть однією накладною, місць: "
                 f"{sum(int(i['qty']) for i in cart)}.")
    await _send(target, "\n".join(lines), reply_markup=kb.cart_kb(buttons))


@router.callback_query(F.data == "cart:add")
async def cart_add_more(cb: CallbackQuery, state: FSMContext):
    await show_category(cb, state)          # кошик у стані лишається
    await cb.answer()


@router.callback_query(F.data == "cart:done")
async def cart_done(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not cart_of(data):
        await cb.answer("Кошик порожній", show_alert=True)
        return
    await show_payment(cb, state)
    await cb.answer()


@router.callback_query(F.data.startswith("cart:del:"))
async def cart_delete(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    cart = cart_of(data)
    idx = int(cb.data.rsplit(":", 1)[1])
    if 0 <= idx < len(cart):
        cart.pop(idx)
        await state.update_data(cart=cart)
    await cb.answer("Прибрано")
    if cart:
        await show_cart(cb, state)
    else:
        await show_category(cb, state)


async def show_qty(target, state: FSMContext):
    data = await state.get_data()
    v = catalog.by_article(data["article"])
    await state.set_state(Order.qty)
    left = await stock.available(_uid(target), v["article"])
    in_cart = cart_qty(cart_of(data), v["article"])
    left_line = ""
    if left is not None:
        left_line = f"Доступно: <b>{left} шт</b>\n"
        if in_cart:
            left_line += f"У кошику вже: {in_cart} шт\n"
    await _send(target,
                f"🌲 <b>{catalog.product_name(_uid(target), v)}</b> — "
                f"{catalog.size_label(v)}\n"
                f"Артикул: <code>{v['article']}</code>\n"
                f"{left_line}"
                f"Вага: ~{v['weight_kg']:g} кг\n\nКількість:",
                reply_markup=kb.qty_kb())


async def show_payment(target, state: FSMContext):
    await state.set_state(Order.payment)
    await _send(target,
                "💳 <b>Тип оплати</b>\n\n"
                "📦 <b>Післяплата</b> — клієнт платить всю суму при отриманні\n"
                "🔸 <b>Часткова передплата</b> — частину сплачено, решта при отриманні\n"
                "✅ <b>Передплата</b> — товар уже повністю оплачено",
                reply_markup=kb.payment_kb())


async def show_sale_price(target, state: FSMContext):
    await state.set_state(Order.sale_price)
    await _send(target, "💰 Введіть <b>вашу ціну продажу</b> для клієнта, грн\n"
                        "(наприклад: <i>4500</i>)",
                reply_markup=_back_kb("sale_price"))


async def show_prepaid(target, state: FSMContext):
    data = await state.get_data()
    await state.set_state(Order.prepaid)
    await _send(target, f"🔸 Ціна продажу: {data['sale_price']} грн\n\n"
                        "Скільки клієнт <b>уже передплатив</b>, грн?",
                reply_markup=_back_kb("prepaid"))


async def show_lname(target, state: FSMContext):
    await state.set_state(Order.lname)
    await _send(target, "👤 Введіть <b>прізвище</b> отримувача\n"
                        "(наприклад: <i>Шевченко</i>)",
                reply_markup=_back_kb("lname"))


async def show_fname(target, state: FSMContext):
    await state.set_state(Order.fname)
    await _send(target, "Введіть <b>ім'я</b> отримувача\n(наприклад: <i>Тарас</i>)",
                reply_markup=_back_kb("fname"))


async def show_mname(target, state: FSMContext):
    await state.set_state(Order.mname)
    await _send(target, "Введіть <b>по-батькові</b> (необов'язково)\n"
                        "(наприклад: <i>Григорович</i>)",
                reply_markup=_skip_back_kb("mname", "mname:skip", "⏭ Пропустити"))


async def show_phone(target, state: FSMContext):
    data = await state.get_data()
    fio = data.get("fio", "")
    head = f"👤 Отримувач: <b>{fio}</b>\n\n" if fio else ""
    await state.set_state(Order.phone)
    await _send(target, head + "📞 Введіть <b>номер телефону отримувача</b>\n"
                               "(наприклад: <i>0671234567</i>)",
                reply_markup=_back_kb("phone"))


async def show_city(target, state: FSMContext):
    await state.set_state(Order.city)
    await _send(target, "🏙 Введіть <b>місто отримувача</b> "
                        "(наприклад: <i>Львів</i>)",
                reply_markup=_back_kb("city"))


async def show_city_pick(target, state: FSMContext):
    data = await state.get_data()
    await state.set_state(Order.city_pick)
    rows = [[InlineKeyboardButton(text=f"{c['name']} ({c['area']} обл.)",
                                  callback_data=f"city:{i}")]
            for i, c in enumerate(data["cities"])]
    await _send(target, "Оберіть місто:", reply_markup=_back_kb("city_pick", rows))


async def show_delivery(target, state: FSMContext):
    data = await state.get_data()
    await state.set_state(Order.delivery)
    rows = [[InlineKeyboardButton(text="🏢 На відділення Нової Пошти",
                                  callback_data="dlv:warehouse")],
            [InlineKeyboardButton(text="🚚 Адресна доставка (курʼєром)",
                                  callback_data="dlv:door")]]
    await _send(target, f"📍 Місто: <b>{data['city']['name']}</b>\n\n"
                        "Куди доставити?",
                reply_markup=_back_kb("delivery", rows))


async def show_warehouse(target, state: FSMContext, page: int = 0):
    data = await state.get_data()
    await state.set_state(Order.warehouse)
    await _send(target,
                f"📍 <b>{data['city']['name']}</b> — оберіть <b>вантажне відділення</b> "
                "(🏗 вантажні, приймають понад 30 кг):",
                reply_markup=kb.warehouses_kb(data["warehouses"], page,
                                              back_step="warehouse"))


async def show_street(target, state: FSMContext):
    data = await state.get_data()
    await state.set_state(Order.street)
    await _send(target, f"🚚 Адресна доставка, {data['city']['name']}\n\n"
                        "Введіть <b>назву вулиці</b> (наприклад: <i>Шевченка</i>)",
                reply_markup=_back_kb("street"))


async def show_street_pick(target, state: FSMContext):
    data = await state.get_data()
    await state.set_state(Order.street_pick)
    rows = [[InlineKeyboardButton(text=f"{s['type']} {s['name']}".strip(),
                                  callback_data=f"str:{i}")]
            for i, s in enumerate(data["streets"])]
    await _send(target, "Оберіть вулицю:", reply_markup=_back_kb("street_pick", rows))


async def show_building(target, state: FSMContext):
    data = await state.get_data()
    await state.set_state(Order.building)
    await _send(target, f"🏠 Вулиця: <b>{data['street']['name']}</b>\n\n"
                        "Введіть <b>номер будинку</b> (наприклад: <i>14а</i>)",
                reply_markup=_back_kb("building"))


async def show_flat(target, state: FSMContext):
    await state.set_state(Order.flat)
    await _send(target, "Введіть <b>номер квартири</b> (необов'язково)",
                reply_markup=_skip_back_kb("flat", "flat:skip", "⏭ Без квартири"))


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


def _address_line(data) -> str:
    if data.get("to_door"):
        addr = f"{data['street']['name']}, {data['building']}"
        if data.get("flat"):
            addr += f", кв. {data['flat']}"
        return f"🚚 Курʼєром: {data['city']['name']}, {addr}"
    return f"🏢 {data['city']['name']}, {data['warehouse']['name']}"


def _not_enough_text(left: int, qty: int, in_cart: int = 0) -> str:
    cart_line = (f"\nУ кошику вже {in_cart} шт цієї позиції."
                 if in_cart else "")
    return ("⚠️ <b>Стільки немає в наявності</b>\n\n"
            f"Ви обрали {qty} шт, а за вашим передзамовленням вільно "
            f"{left} шт.{cart_line}\n\nЗменшіть кількість або "
            "напишіть менеджеру.")


async def cart_lines(user_id, cart) -> list[str]:
    """Позиції для екрана підтвердження — назвами очима дропшипера."""
    out = []
    for it in cart:
        v = catalog.by_article(it["article"])
        price = f"{cart_price(user_id, it):,}".replace(",", " ")
        line = (f"🌲 {catalog.product_name(user_id, v)} — "
                f"{catalog.size_label(v)}\n"
                f"    <code>{it['article']}</code> × {it['qty']} = {price} грн")
        left = await stock.available(user_id, it["article"])
        if left is not None:
            line += f" (доступно {left})"
        out.append(line)
    return out


async def cart_shortage(user_id, cart) -> list[dict]:
    """Позиції, яких не вистачає прямо зараз: [{article, available, requested}]."""
    short = []
    for it in cart:
        left = await stock.available(user_id, it["article"])
        if left is not None and int(it["qty"]) > left:
            short.append({"article": it["article"], "available": left,
                          "requested": int(it["qty"])})
    return short


async def show_confirm(target, state: FSMContext):
    data = await state.get_data()
    uid = _uid(target)
    cart = cart_of(data)
    await state.set_state(Order.confirm)
    if not cart:
        await show_category(target, state)
        return

    short = await cart_shortage(uid, cart)
    if short:
        await _send(target, stock.not_enough_text(short),
                    reply_markup=kb.cart_kb(
                        [{"idx": i, "label": f"{c['article']} × {c['qty']}"}
                         for i, c in enumerate(cart)]))
        return

    total = f"{cart_total(uid, cart):,}".replace(",", " ")
    lines = ["📋 <b>Перевірте замовлення</b>\n"]
    lines += await cart_lines(uid, cart)
    tail = (f"\nУсі позиції поїдуть однією накладною, місць: "
            f"{sum(int(i['qty']) for i in cart)}."
            if len(cart) > 1 else "")
    await _send(target,
                "\n".join(lines) +
                f"\n\n💰 <b>Разом: {total} грн</b>\n"
                f"{_payment_lines(data)}\n\n"
                f"👤 {data['fio']}\n"
                f"📞 {data['phone']}\n"
                f"{_address_line(data)}"
                f"{tail}\n\n"
                "Все вірно?",
                reply_markup=kb.confirm_kb())


async def show_payment_proof(target, state: FSMContext):
    data = await state.get_data()
    uid = _uid(target)
    cart = cart_of(data)
    due = payment.due_amount(cart_total(uid, cart), data.get("cod_amount"))
    await state.update_data(due_amount=due)
    await state.set_state(Order.payment_proof)
    if len(cart) == 1:
        v = catalog.by_article(cart[0]["article"])
        hint = f"{catalog.product_name(uid, v)} {catalog.size_label(v)}"
    else:
        hint = f"замовлення з {len(cart)} позицій"
    await _send(target, payment.details_text(due, hint),
                reply_markup=_skip_back_kb("payment_proof", "proof:later",
                                           "⏭ Надішлю чек пізніше"))


SHOW = {
    "category": show_category, "model": show_model, "variant": show_variant,
    "qty": show_qty, "cart": show_cart, "payment": show_payment, "sale_price": show_sale_price,
    "prepaid": show_prepaid, "lname": show_lname, "fname": show_fname,
    "mname": show_mname, "phone": show_phone, "city": show_city,
    "city_pick": show_city_pick, "delivery": show_delivery,
    "warehouse": show_warehouse, "street": show_street,
    "street_pick": show_street_pick, "building": show_building,
    "flat": show_flat, "confirm": show_confirm,
    "payment_proof": show_payment_proof,
}


@router.callback_query(F.data.startswith("back:"))
async def go_back(cb: CallbackQuery, state: FSMContext):
    step = cb.data.split(":", 1)[1]
    prev = PREV.get(step, "category")
    data = await state.get_data()
    # пропускаємо кроки, яких не було в цьому сценарії
    if prev == "prepaid" and data.get("payment") != "часткова":
        prev = "sale_price"
    if prev == "sale_price" and data.get("payment") == "передплата":
        prev = "payment"
    if prev == "mname" and not data.get("lname"):
        prev = "payment"
    await SHOW[prev](cb, state)
    await cb.answer()


# ---------------------------------------------------------------- вхід

@router.callback_query(F.data == "order:new")
async def start_order(cb: CallbackQuery, state: FSMContext):
    if not storage.is_approved(cb.from_user.id):
        await cb.answer("Доступ ще не схвалено", show_alert=True)
        return
    await state.clear()
    await show_category(cb, state)
    await cb.answer()


@router.callback_query(F.data == "order:cancel")
async def cancel_order(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.message.edit_text("Замовлення скасовано.", reply_markup=kb.main_menu())
    await cb.answer()


@router.callback_query(F.data.startswith("cat:"))
async def pick_category(cb: CallbackQuery, state: FSMContext):
    await state.update_data(cat_idx=int(cb.data.split(":")[1]))
    await show_model(cb, state)
    await cb.answer()


@router.callback_query(F.data.startswith("mdlp:"))
async def models_page(cb: CallbackQuery, state: FSMContext):
    _, cat_idx, page = cb.data.split(":")
    await state.update_data(cat_idx=int(cat_idx))
    await show_model(cb, state, int(page))
    await cb.answer()


@router.callback_query(F.data.startswith("mdl:"))
async def pick_model(cb: CallbackQuery, state: FSMContext):
    _, cat_idx, model_idx = cb.data.split(":")
    cat_idx, model_idx = int(cat_idx), int(model_idx)
    # той самий порядок, що й у клавіатурі, інакше номер вкаже не на ту модель
    model = catalog.models_for(cb.from_user.id,
                               catalog.categories()[cat_idx])[model_idx]
    await state.update_data(cat_idx=cat_idx, model_idx=model_idx, model=model)
    await show_variant(cb, state)
    await cb.answer()


@router.callback_query(F.data.startswith("var:"), Order.variant)
async def pick_variant(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    v = catalog.find_variant_short(data["model"], int(cb.data.split(":")[1]))
    if not v:
        await cb.answer("Не знайдено", show_alert=True)
        return
    await state.update_data(article=v["article"])
    await show_qty(cb, state)
    await cb.answer()


@router.callback_query(F.data.startswith("qty:"), Order.qty)
async def pick_qty(cb: CallbackQuery, state: FSMContext):
    qty = int(cb.data.split(":")[1])
    data = await state.get_data()
    article = data["article"]
    cart = cart_of(data)

    # залишок перевіряємо разом із тим, що вже лежить у кошику: інакше дві
    # порції по 10 обійшли б ліміт у 15
    left = await stock.available(cb.from_user.id, article)
    already = cart_qty(cart, article)
    if left is not None and already + qty > left:
        await cb.answer()
        await cb.message.edit_text(
            _not_enough_text(left, already + qty, already),
            reply_markup=kb.cart_kb(
                [{"idx": i, "label": f"{c['article']} × {c['qty']}"}
                 for i, c in enumerate(cart)]) if cart else kb.main_menu())
        return

    await state.update_data(cart=cart_add(cart, article, qty))
    await show_cart(cb, state)
    await cb.answer()


def _amount(text: str) -> int | None:
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits.isdigit() and 50 <= int(digits) <= 500000 else None


NAME_RE = re.compile(r"[А-ЯІЇЄҐа-яіїєґA-Za-z'’\-]{2,40}")


def _valid_name(text: str) -> str | None:
    name = text.strip()
    return name if NAME_RE.fullmatch(name) else None


@router.callback_query(F.data.startswith("pay:"), Order.payment)
async def pick_payment(cb: CallbackQuery, state: FSMContext):
    payment = cb.data.split(":", 1)[1]
    await state.update_data(payment=payment)
    if payment == "передплата":
        await state.update_data(sale_price=0, prepaid=0, cod_amount=0)
        await show_lname(cb, state)
    else:
        await show_sale_price(cb, state)
    await cb.answer()


@router.message(Order.sale_price, F.text)
async def input_sale_price(msg: Message, state: FSMContext):
    amount = _amount(msg.text)
    if amount is None:
        await msg.answer("⚠️ Введіть суму числом у гривнях, наприклад <i>4500</i>:",
                         reply_markup=_back_kb("sale_price"))
        return
    data = await state.get_data()
    await state.update_data(sale_price=amount)
    if data["payment"] == "часткова":
        await state.update_data(sale_price=amount)
        await show_prepaid(msg, state)
    else:
        await state.update_data(prepaid=0, cod_amount=amount)
        await show_lname(msg, state)


@router.message(Order.prepaid, F.text)
async def input_prepaid(msg: Message, state: FSMContext):
    digits = re.sub(r"[^\d]", "", msg.text)
    prepaid = int(digits) if digits.isdigit() else None
    data = await state.get_data()
    sale = data["sale_price"]
    if prepaid is None or prepaid <= 0 or prepaid >= sale:
        await msg.answer(f"⚠️ Передплата має бути більшою за 0 і меншою за ціну "
                         f"продажу ({sale} грн). Спробуйте ще раз:",
                         reply_markup=_back_kb("prepaid"))
        return
    await state.update_data(prepaid=prepaid, cod_amount=sale - prepaid)
    await msg.answer(f"✅ При отриманні клієнт сплатить: <b>{sale - prepaid} грн</b>")
    await show_lname(msg, state)


@router.message(Order.lname, F.text)
async def input_lname(msg: Message, state: FSMContext):
    name = _valid_name(msg.text)
    if not name:
        await msg.answer("⚠️ Введіть лише <b>прізвище</b>, одним словом:",
                         reply_markup=_back_kb("lname"))
        return
    await state.update_data(lname=name)
    await show_fname(msg, state)


@router.message(Order.fname, F.text)
async def input_fname(msg: Message, state: FSMContext):
    name = _valid_name(msg.text)
    if not name:
        await msg.answer("⚠️ Введіть лише <b>ім'я</b>, одним словом:",
                         reply_markup=_back_kb("fname"))
        return
    await state.update_data(fname=name)
    await show_mname(msg, state)


async def _fio_done(state: FSMContext, mname: str = ""):
    data = await state.get_data()
    fio = " ".join(x for x in (data["lname"], data["fname"], mname) if x)
    await state.update_data(fio=fio, mname=mname)
    return fio


@router.message(Order.mname, F.text)
async def input_mname(msg: Message, state: FSMContext):
    name = _valid_name(msg.text)
    if not name:
        await msg.answer("⚠️ Введіть по-батькові одним словом, або натисніть "
                         "«Пропустити»:",
                         reply_markup=_skip_back_kb("mname", "mname:skip",
                                                    "⏭ Пропустити"))
        return
    await _fio_done(state, name)
    await show_phone(msg, state)


@router.callback_query(F.data == "mname:skip", Order.mname)
async def skip_mname(cb: CallbackQuery, state: FSMContext):
    await _fio_done(state)
    await show_phone(cb, state)
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
                         "наприклад <i>0671234567</i>:",
                         reply_markup=_back_kb("phone"))
        return
    await state.update_data(phone=phone)
    await show_city(msg, state)


@router.message(Order.city, F.text)
async def input_city(msg: Message, state: FSMContext):
    q = msg.text.strip()
    if len(q) < 2:
        await msg.answer("⚠️ Введіть назву міста:", reply_markup=_back_kb("city"))
        return
    try:
        cities = await np.search_cities(q)
    except np.NPError as e:
        await msg.answer(f"⚠️ Помилка Нової Пошти: {e}\nСпробуйте ще раз:",
                         reply_markup=_back_kb("city"))
        return
    if not cities:
        await msg.answer("⚠️ Міст не знайдено. Перевірте назву:",
                         reply_markup=_back_kb("city"))
        return
    await state.update_data(cities=cities)
    await show_city_pick(msg, state)


@router.callback_query(F.data.startswith("city:"), Order.city_pick)
async def pick_city(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await state.update_data(city=data["cities"][int(cb.data.split(":")[1])])
    await show_delivery(cb, state)
    await cb.answer()


@router.callback_query(F.data == "dlv:warehouse", Order.delivery)
async def pick_delivery_warehouse(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    city = data["city"]
    try:
        whs = await np.cargo_warehouses(city["ref"])
    except np.NPError as e:
        await cb.message.edit_text(f"⚠️ Помилка Нової Пошти: {e}")
        await cb.answer()
        return
    if not whs:
        await state.update_data(warehouses=[])
        await cb.message.edit_text(
            f"⚠️ У місті <b>{city['name']}</b> немає вантажних відділень "
            "(потрібне таке, що приймає понад 30 кг).\n\n"
            "Оберіть адресну доставку або інше місто:",
            reply_markup=_back_kb("warehouse", [[InlineKeyboardButton(
                text="🚚 Адресна доставка", callback_data="dlv:door")]]))
        await cb.answer()
        return
    await state.update_data(warehouses=whs, to_door=False)
    await show_warehouse(cb, state)
    await cb.answer()


@router.callback_query(F.data == "dlv:door")
async def pick_delivery_door(cb: CallbackQuery, state: FSMContext):
    await state.update_data(to_door=True)
    await show_street(cb, state)
    await cb.answer()


@router.callback_query(F.data.startswith("whp:"), Order.warehouse)
async def warehouses_page(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await cb.message.edit_reply_markup(
        reply_markup=kb.warehouses_kb(data["warehouses"],
                                      int(cb.data.split(":")[1]),
                                      back_step="warehouse"))
    await cb.answer()


@router.callback_query(F.data.startswith("wh:"), Order.warehouse)
async def pick_warehouse(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await state.update_data(warehouse=data["warehouses"][int(cb.data.split(":")[1])])
    await show_confirm(cb, state)
    await cb.answer()


@router.message(Order.street, F.text)
async def input_street(msg: Message, state: FSMContext):
    data = await state.get_data()
    q = msg.text.strip()
    if len(q) < 3:
        await msg.answer("⚠️ Введіть назву вулиці (мінімум 3 літери):",
                         reply_markup=_back_kb("street"))
        return
    try:
        streets = await np.search_streets(data["city"]["ref"], q)
    except np.NPError as e:
        await msg.answer(f"⚠️ Помилка Нової Пошти: {e}\nСпробуйте ще раз:",
                         reply_markup=_back_kb("street"))
        return
    if not streets:
        await msg.answer("⚠️ Вулицю не знайдено. Спробуйте іншу назву "
                         "(без «вул.», лише назва):", reply_markup=_back_kb("street"))
        return
    await state.update_data(streets=streets)
    await show_street_pick(msg, state)


@router.callback_query(F.data.startswith("str:"), Order.street_pick)
async def pick_street(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await state.update_data(street=data["streets"][int(cb.data.split(":")[1])])
    await show_building(cb, state)
    await cb.answer()


@router.message(Order.building, F.text)
async def input_building(msg: Message, state: FSMContext):
    b = msg.text.strip()
    if not re.fullmatch(r"[0-9]{1,4}[а-яА-Яa-zA-Z]?(/[0-9]{1,4}[а-яА-Яa-zA-Z]?)?", b):
        await msg.answer("⚠️ Введіть номер будинку, наприклад <i>14</i>, "
                         "<i>14а</i> або <i>14/2</i>:",
                         reply_markup=_back_kb("building"))
        return
    await state.update_data(building=b)
    await show_flat(msg, state)


@router.message(Order.flat, F.text)
async def input_flat(msg: Message, state: FSMContext):
    f = msg.text.strip()
    if not re.fullmatch(r"[0-9]{1,5}[а-яА-Яa-zA-Z]?", f):
        await msg.answer("⚠️ Введіть номер квартири числом, або натисніть "
                         "«Без квартири»:",
                         reply_markup=_skip_back_kb("flat", "flat:skip",
                                                    "⏭ Без квартири"))
        return
    await state.update_data(flat=f)
    await show_confirm(msg, state)


@router.callback_query(F.data == "flat:skip", Order.flat)
async def skip_flat(cb: CallbackQuery, state: FSMContext):
    await state.update_data(flat="")
    await show_confirm(cb, state)
    await cb.answer()


# ---------------------------------------------------------------- підтвердження

@router.callback_query(F.data == "confirm:yes", Order.confirm)
async def confirm_order(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    cart = cart_of(data)
    short = await cart_shortage(cb.from_user.id, cart)
    if short:
        await cb.message.edit_text(stock.not_enough_text(short))
        await cb.answer()
        return
    due = payment.due_amount(cart_total(cb.from_user.id, cart),
                             data.get("cod_amount"))
    if due > 0:
        # частина або вся сума йде вам на рахунок — просимо скрін чека
        await show_payment_proof(cb, state)
        await cb.answer()
        return
    await _finalize(cb, state, proof_file_id=None, create_ttn=True)
    await cb.answer()


@router.message(Order.payment_proof, F.photo | F.document)
async def input_proof(msg: Message, state: FSMContext):
    file_id = msg.photo[-1].file_id if msg.photo else msg.document.file_id
    kind = "photo" if msg.photo else "document"
    await _finalize(msg, state, proof_file_id=(kind, file_id), create_ttn=True)


@router.message(Order.payment_proof, F.text)
async def proof_wrong_type(msg: Message, state: FSMContext):
    await msg.answer("📸 Надішліть, будь ласка, <b>скрін чека</b> — фото або файл. "
                     "Якщо оплатите пізніше, натисніть «Надішлю чек пізніше».",
                     reply_markup=_skip_back_kb("payment_proof", "proof:later",
                                                "⏭ Надішлю чек пізніше"))


@router.callback_query(F.data == "proof:later", Order.payment_proof)
async def proof_later(cb: CallbackQuery, state: FSMContext):
    await _finalize(cb, state, proof_file_id=None, create_ttn=False)
    await cb.answer()


async def _finalize(target, state: FSMContext, *, proof_file_id, create_ttn: bool):
    with perf.trace("оформлення замовлення"):
        await _finalize_inner(target, state, proof_file_id=proof_file_id,
                              create_ttn=create_ttn)


async def _finalize_inner(target, state: FSMContext, *, proof_file_id,
                          create_ttn: bool):
    data = await state.get_data()
    user = target.from_user
    cart = cart_of(data)
    if not cart:
        await show_category(target, state)
        return
    to_door = bool(data.get("to_door"))

    common = dict(
        order_no=storage.next_order_no(),
        source="telegram",
        dropshipper_id=user.id,
        dropshipper=f"@{user.username}" if user.username else user.full_name,
        payment=("часткова передплата" if data["payment"] == "часткова"
                 else data["payment"]),
        sale_price=data.get("sale_price") or "",
        prepaid=data.get("prepaid") or "",
        cod_amount=data.get("cod_amount") or "",
        recipient_fio=data["fio"],
        recipient_phone=data["phone"],
        city=data["city"]["name"],
        warehouse=(_address_line(data).split(": ", 1)[-1] if to_door
                   else data["warehouse"]["name"]),
        delivery=("адресна доставка" if to_door else "відділення"),
        due_amount=data.get("due_amount") or "",
        payment_proof=("надіслано" if proof_file_id else
                       ("очікується" if data.get("due_amount") else "не потрібен")),
    )

    items = []
    for it in cart:
        v = catalog.by_article(it["article"])
        qty = int(it["qty"])
        items.append({
            "article": v["article"],          # оригінальний рядок прайсу
            "product": v["model_ua"],
            "size": catalog.size_label(v),
            "qty": qty,
            "price_drop": int(catalog.drop_price(v, user.id) * qty),
        })
    rows = orders.new_rows(items, **common)
    if not create_ttn:
        for r in rows:
            r["status"] = "очікує оплати"

    if isinstance(target, CallbackQuery):
        await target.message.edit_text("⏳ Оформлюю замовлення…")
        out = target.message
    else:
        out = await target.answer("⏳ Оформлюю замовлення…")

    # ---- резерв УСІХ позицій до першої накладної, «все або нічого» ----
    to_reserve = []
    for it in items:
        if await stock.available(user.id, it["article"]) is not None:
            to_reserve.append({"article": it["article"], "qty": it["qty"]})
    if to_reserve:
        res, res_err = await stock.reserve_many(user.id, to_reserve)
        if res_err:
            missing = (res or {}).get("items") or []
            text = (stock.not_enough_text(missing) if res_err == "not enough"
                    else "❌ <b>Замовлення не оформлено</b>\n\n"
                         + stock.error_text(res_err))
            await out.edit_text(text)
            await _tell_admin(out.bot,
                              f"⚠️ Резерв не пройшов (замовлення №"
                              f"{common['order_no']}, {common['dropshipper']}): "
                              f"{res_err}")
            return

    # ---- одна накладна на все замовлення ----
    ttn_note = ""
    if config.NP_AUTO_TTN and create_ttn:
        ok, note = await _create_ttn(rows, data, user, to_door)
        if ok:
            ttn_note = note
        else:
            ttn_note = ("\n⚠️ ТТН не вдалося створити автоматично — "
                        "менеджер оформить вручну.")
            await _tell_admin(out.bot,
                              f"⚠️ Замовлення №{common['order_no']} "
                              f"({common['dropshipper']}): ТТН не створено — "
                              f"{note}")

    await state.clear()
    await _record_order(out, user, rows, _address_line(data), proof_file_id,
                        create_ttn, ttn_note)


async def _create_ttn(rows, data, user, to_door: bool):
    """Одна накладна на все замовлення. Повертає (успіх, примітка/помилка).

    Вага, обʼєм і вартість — суми позицій, місць — стільки, скільки одиниць
    товару: дві ялинки їдуть однією накладною на два місця.
    """
    items = []
    for row in rows:
        item = catalog.by_article(row["article"])
        if item:
            items.append((item, int(row["qty"])))
    if not items:
        return False, "немає позицій"

    weight = sum((it["weight_kg"] or 5) * q for it, q in items)
    volume = sum((it.get("volume_m3") or 0) * q for it, q in items)
    seats = sum(q for _, q in items)
    cost = sum(int(r["price_drop"] or 0) for r in rows)

    try:
        res = await np.create_ttn(
            recipient_city_ref=data["city"]["ref"],
            recipient_warehouse_ref=(data.get("warehouse") or {}).get("ref", ""),
            fio=data["fio"], phone=data["phone"],
            description=catalog.ttn_description_multi(items, user.id),
            cost=cost,
            weight=weight,
            volume=volume or None,
            seats=seats,
            cod_amount=data.get("cod_amount") or 0,
            to_door=to_door,
            street_ref=(data.get("street") or {}).get("ref", ""),
            building=data.get("building", ""),
            flat=data.get("flat", ""),
        )
    except Exception as e:  # noqa: BLE001
        for row in rows:
            row["status"] = "ТТН НЕ створено"
            row["comment"] = f"Помилка ТТН: {e}"
        return False, str(e)

    # ТТН одна на замовлення, тож стоїть у кожному рядку позиції
    for row in rows:
        row["ttn"] = res["ttn"]
        row["status"] = "ТТН створено"
    note = f"\n📦 <b>ТТН: <code>{res['ttn']}</code></b>"
    if res.get("estimated_date"):
        note += f"\n🗓 Орієнтовна доставка: {res['estimated_date']}"
    if seats > 1:
        note += f"\n📦 Місць у відправленні: {seats}"
    return True, note


async def _tell_admin(bot, text: str):
    if not config.ADMIN_CHAT_ID:
        return
    try:
        await bot.send_message(config.ADMIN_CHAT_ID, text)
    except Exception:  # noqa: BLE001
        pass


async def _record_order(out, user, rows, address_line: str, proof_file_id,
                        create_ttn: bool, ttn_note: str = ""):
    """Записати замовлення (CSV, локально, таблиця) і показати підсумок."""
    for row in rows:
        orders.save_csv(row)
        orders.save_local(row)
    sheet_err = await orders.send_to_sheet_rows(rows)
    if sheet_err:
        for row in rows:
            row["comment"] = (row.get("comment", "")
                              + f" | Sheet: {sheet_err}").strip(" |")

    bot = out.bot
    if config.ADMIN_CHAT_ID:
        try:
            text = orders.admin_text_multi(rows)
            if proof_file_id:
                kind, fid = proof_file_id
                if kind == "photo":
                    await bot.send_photo(config.ADMIN_CHAT_ID, fid, caption=text)
                else:
                    await bot.send_document(config.ADMIN_CHAT_ID, fid,
                                            caption=text)
            else:
                await bot.send_message(config.ADMIN_CHAT_ID, text)
        except Exception:  # noqa: BLE001
            pass

    head = rows[0]
    if not create_ttn:
        tail = ("\n\n💳 Замовлення збережено зі статусом <b>«очікує оплати»</b>. "
                f"Переказ на {head['due_amount']} грн і скрін чека — "
                "менеджеру в цей чат. ТТН створимо після оплати.")
    elif proof_file_id:
        tail = "\n\n📸 Чек передано менеджеру. Дякуємо! 🎄"
    else:
        tail = "\n\nДякуємо! 🎄"

    lines = [f"✅ <b>Замовлення №{head['order_no']} прийнято!</b>\n"]
    for row in rows:
        item = catalog.by_article(row["article"])
        lines.append(f"🌲 {catalog.product_name(user.id, item)} — "
                     f"{row['size']} × {row['qty']}")
    lines += ["", f"👤 {head['recipient_fio']}", address_line,
              f"💳 {head['payment']}"]
    await out.edit_text("\n".join(lines) + ttn_note + tail,
                        reply_markup=kb.main_menu())
