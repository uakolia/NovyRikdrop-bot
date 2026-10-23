"""One-time exact-version migration; no Google or Nova Poshta API calls."""
import ast
import hashlib
from pathlib import Path

EXPECTED = {
    "handlers_order.py": "02bcd8b6d4a573dc95996374c2e7f0ad3c49c21f",
    "shipment_boxes.py": "dd7c826af6c243ddf34bfbf6cf620075e621778c",
    "catalog_parser.py": "43d66ef41ea88e5bea685167f61236de2105de35",
    "ttn_retry.py": "5de345daa9be27d690fff3ffb74513329aa54144",
    "novaposhta.py": "caaf42689574fdef408150def94d26ae98dbd207",
    "webhook.py": "dc9f2325eab40210630f58a6f5b85e1e6f08f47e",
}

def once(s, old, new):
    if s.count(old) != 1:
        raise RuntimeError("Source context changed; stopping")
    return s.replace(old, new, 1)

def function(s, name, replacement):
    nodes = [n for n in ast.parse(s).body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name]
    if len(nodes) != 1:
        raise RuntimeError("Function context changed: " + name)
    n = nodes[0]
    lines = s.splitlines(True)
    return ''.join(lines[:n.lineno-1]) + replacement.rstrip() + '\n' + ''.join(lines[n.end_lineno:])

def transform(name, s):
    if name == "handlers_order.py":
        s = once(s, 'import shipment_boxes\n', 'import shipment_boxes\nimport order_pricing\n')
        s = function(s, 'show_sale_price', 'async def show_sale_price(target, state: FSMContext):\n    await order_pricing.show_price(target, state)')
        s = function(s, 'input_sale_price', 'async def input_sale_price(msg: Message, state: FSMContext):\n    await order_pricing.input_price(msg, state)')
        s = once(s, '    await state.update_data(payment=payment)', """    data = await state.get_data()
    cart = [dict(c) for c in cart_of(data)]
    for c in cart:
        c.pop("unit_sale_price", None)
    await state.update_data(cart=cart, sale_price_idx=0, sale_price=0, prepaid=0, cod_amount=0)
    await state.update_data(payment=payment)""")
        s = once(s, '    step = cb.data.split(":", 1)[1]', """    step = cb.data.split(":", 1)[1]
    if step == "sale_price":
        await order_pricing.back_price(cb, state)
        await cb.answer()
        return""")
        s = once(s, '    to_door = bool(data.get("to_door"))', """    if any(not c.get("unit_sale_price") for c in cart):
        await state.update_data(sale_price_idx=0)
        await show_sale_price(target, state)
        return
    to_door = bool(data.get("to_door"))""")
        s = once(s, '            "price_drop": int(catalog.drop_price(v, user.id) * qty),', '            "price_drop": int(catalog.drop_price(v, user.id) * qty),\n            "comment": order_pricing.sale_comment(it["unit_sale_price"], qty),')
        s = once(s, 'shipment = shipment_boxes.build_shipment(items)', 'shipment = order_pricing.priced_shipment(items, order_pricing.line_values(rows))')
        s = once(s, '            row["comment"] = str(exc)', '            row["comment"] = order_pricing.keep_comment(row.get("comment"), str(exc))')
        s = once(s, '            row["comment"] = f"Помилка ТТН: {e}"', '            row["comment"] = order_pricing.keep_comment(row.get("comment"), f"Помилка ТТН: {e}")')
        s = once(s, '        out.append(line)\n    return out', """        if it.get("unit_sale_price"):
            unit = int(it["unit_sale_price"])
            line += f"\\n    Продаж: {unit} грн/шт.; сума: {unit*int(it['qty'])} грн"
        out.append(line)
    return out""")
    elif name == "catalog_parser.py":
        s = once(s, '"volume": ("packaging volume",),', '"volume": ("packaging volume", "об\'ємна вага", "об’ємна вага", "об\'єм упаковки", "об’єм упаковки"),')
    elif name == "shipment_boxes.py":
        s += FALLBACK
    elif name == "ttn_retry.py":
        s = once(s, 'import shipment_boxes\n', 'import shipment_boxes\nimport order_pricing\n')
        s = once(s, 'shipment = shipment_boxes.build_shipment(packaging_items)', 'shipment = order_pricing.priced_shipment(packaging_items, order_pricing.line_values(rows))')
        s = once(s, '    sale = int(_num(head.get("sale_price")))\n    if sale:\n        cost = sale', '    cost = shipment["cost"]')
    elif name == "webhook.py":
        s = once(s, 'import shipment_boxes\n', 'import shipment_boxes\nimport order_pricing\n')
        s = once(s, 'shipment = shipment_boxes.build_shipment([(item, qty)])', 'shipment = order_pricing.priced_shipment([(item, qty)], [int(catalog.drop_price(item)*qty)])')
    elif name == "novaposhta.py":
        s = once(s, '    import np_store\n    if not np_store.is_ready():', """    if any("cost" in p for p in options_seat):
        try:
            values = [int(p["cost"]) for p in options_seat]
        except (KeyError, TypeError, ValueError) as exc:
            raise NPError("Некоректна оголошена вартість місць") from exc
        if any(v <= 0 for v in values) or sum(values) != int(cost):
            raise NPError("Сума вартості місць не відповідає сумі замовлення")
    import np_store
    if not np_store.is_ready():""")
    ast.parse(s, filename=name)
    return s

def main():
    updated = {}
    for name, expected in EXPECTED.items():
        b = Path(name).read_bytes()
        sha = hashlib.sha1(b"blob "+str(len(b)).encode()+b"\0"+b).hexdigest()
        if sha != expected:
            raise RuntimeError(f"{name} differs from reviewed main; no files changed")
        updated[name] = transform(name, b.decode())
    for name, s in updated.items():
        Path(name).write_text(s, encoding="utf-8")
    print("Updated item-price checkout, persisted row prices, TTN retries and volume fallback")

FALLBACK = '\n# Keep measured dimensions authoritative. T is m3, never physical kilograms.\n_dimensions_shipment = build_shipment\n\ndef build_shipment(items):\n    if len(items) == 1:\n        item, qty = items[0]\n        volume = positive(item.get("volume_m3"))\n        if quantity(qty) == 1 and not str(item.get("box") or "").strip() and volume:\n            measured = positive(item.get("weight_kg"))\n            estimated = measured is None or bool(item.get("weight_estimated"))\n            weight = Decimal("10") if estimated else measured\n            warnings = ["Габарити відсутні: одне місце, резервний об\'єм з прайсу (м3)"]\n            if estimated:\n                warnings.append("Фактична вага відсутня: оцінка 10 кг")\n            return {"options_seat": [{"weight": format(weight, "f"), "volumetricVolume": format(volume, "f")}],\n                    "weight": float(weight), "volume": float(volume), "seats": 1, "warnings": warnings}\n    return _dimensions_shipment(items)\n\ndef place_count(items):\n    return build_shipment(items)["seats"]\n'

if __name__ == "__main__":
    main()
