"""Per-product sale prices, durable row comments and per-place declared values."""
import re
from decimal import Decimal, ROUND_FLOOR
from html import escape
import shipment_boxes as boxes

_PRICE = re.compile(r"Ціна продажу за 1 шт\.: (\d+) грн; сума позиції: (\d+) грн\.")

def sale_comment(unit, qty):
    return f"Ціна продажу за 1 шт.: {int(unit)} грн; сума позиції: {int(unit)*int(qty)} грн."

def keep_comment(old, note):
    return "\n".join(str(x) for x in (old, note) if x)

def line_values(rows):
    values = []
    for row in rows:
        matches = _PRICE.findall(str(row.get("comment") or ""))
        if len(matches) == 1:
            unit, total = map(int, matches[0])
            if unit <= 0 or unit*boxes.quantity(row.get("qty")) != total:
                raise boxes.PackagingError("Ціна позиції не відповідає кількості; потрібне уточнення")
            values.append(total)
        elif len(rows) == 1 and not matches and boxes.positive(row.get("sale_price")):
            values.append(int(boxes.positive(row["sale_price"])))
        else:
            raise boxes.PackagingError("У старому замовленні немає індивідуальних цін продажу; уточніть ціни позицій")
    return values

def allocate(total, weights):
    total = int(total)
    weights = [Decimal(str(v)) for v in weights]
    if total < len(weights) or not weights or any(v <= 0 for v in weights):
        raise boxes.PackagingError("Недостатня оголошена вартість для кількості коробок")
    shares = [Decimal(total-len(weights))*v/sum(weights) for v in weights]
    amounts = [1+int(v.to_integral_value(rounding=ROUND_FLOOR)) for v in shares]
    order = sorted(range(len(weights)), key=lambda i: shares[i]-int(shares[i]), reverse=True)
    for i in order[:total-sum(amounts)]:
        amounts[i] += 1
    return amounts

def priced_shipment(items, totals):
    if len(items) != len(totals):
        raise boxes.PackagingError("Кількість цін не відповідає позиціям")
    shipment = boxes.build_shipment(items)
    places, offset = shipment["options_seat"], 0
    for (item, q), total in zip(items, totals):
        qty = boxes.quantity(q)
        count = len(boxes.parse_boxes(item["box"])) if str(item.get("box") or "").strip() else 1
        unit_values = allocate(total, [1]*qty)
        for unit_value in unit_values:
            group = places[offset:offset+count]
            if len(group) != count:
                raise boxes.PackagingError("Кількість коробок не відповідає товару")
            for place, value in zip(group, allocate(unit_value, [p["volumetricVolume"] for p in group])):
                place["cost"] = str(value)
            offset += count
    if offset != len(places):
        raise boxes.PackagingError("Зайві коробки у відправленні")
    shipment["cost"] = sum(int(t) for t in totals)
    return shipment

async def show_price(target, state):
    import handlers_order as flow
    data = await state.get_data()
    cart = flow.cart_of(data)
    if not cart:
        return await flow.show_category(target, state)
    idx = min(max(int(data.get("sale_price_idx", 0)), 0), len(cart)-1)
    await state.update_data(sale_price_idx=idx)
    await state.set_state(flow.Order.sale_price)
    row = cart[idx]
    item = flow.catalog.by_article(row["article"])
    name = flow.catalog.product_name(flow._uid(target), item) if item else row["article"]
    await flow._send(target,
        f"💰 Позиція {idx+1} з {len(cart)}\n\n{escape(str(name))}\n"
        f"Артикул: <code>{escape(row['article'])}</code>\nКількість: {row['qty']} шт.\n\n"
        "Введіть <b>ціну продажу за 1 шт.</b> у гривнях, цілим числом.\n"
        "Цю суму буде використано для оголошеної вартості товару в Новій пошті.",
        reply_markup=flow._back_kb("sale_price"))

async def input_price(msg, state):
    import handlers_order as flow
    text = re.sub(r"[\s\u00a0]", "", str(msg.text or ""))
    if not re.fullmatch(r"[0-9]{1,7}", text) or not 1 <= int(text) <= 500000:
        await msg.answer("Введіть ціну за 1 шт. цілим числом від 1 до 500000 грн.", reply_markup=flow._back_kb("sale_price"))
        return
    data = await state.get_data()
    cart = flow.cart_of(data)
    if not cart:
        return await flow.show_category(msg, state)
    idx = min(max(int(data.get("sale_price_idx", 0)), 0), len(cart)-1)
    cart[idx] = dict(cart[idx], unit_sale_price=int(text))
    await state.update_data(cart=cart)
    if idx+1 < len(cart):
        await state.update_data(sale_price_idx=idx+1)
        return await show_price(msg, state)
    if any(not c.get("unit_sale_price") for c in cart):
        await state.update_data(sale_price_idx=0)
        return await show_price(msg, state)
    total = sum(int(c["unit_sale_price"])*int(c["qty"]) for c in cart)
    await state.update_data(sale_price=total)
    if data["payment"] == "часткова":
        await flow.show_prepaid(msg, state)
    elif data["payment"] == "передплата":
        await state.update_data(prepaid=total, cod_amount=0)
        await flow.show_lname(msg, state)
    else:
        await state.update_data(prepaid=0, cod_amount=total)
        await flow.show_lname(msg, state)

async def back_price(target, state):
    import handlers_order as flow
    data = await state.get_data()
    idx = int(data.get("sale_price_idx", 0))
    if idx > 0:
        await state.update_data(sale_price_idx=idx-1)
        await show_price(target, state)
    else:
        await flow.show_payment(target, state)
