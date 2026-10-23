"""Exact-version-guarded, one-time TTN migration. No API calls."""
import ast
import hashlib
from pathlib import Path

EXPECTED = {
    "handlers_order.py": "641e2ba1e98fc2ab1a29306dfddcecf9eff9c5a0",
    "catalog_parser.py": "088f3b0b200896144a0dac456decd4b23ea440af",
    "webhook.py": "1226fa0da5092cc32f304f37b2477d58e6afecd6",
    "novaposhta.py": "b781f3a58f6b133916777a5953a4bc646974efca",
    "ttn_retry.py": "3f0d4f5817ec336264311e5afd3ef0850a0ad997",
}

def once(s, old, new):
    if s.count(old) != 1:
        raise RuntimeError("Source context changed; refusing migration")
    return s.replace(old, new, 1)

def transform(name, s):
    if name == "catalog_parser.py":
        s = once(s, "import re\n", "import re\nimport math\n")
        for a,b in [
            ('"article": ("article",)', '"article": ("article", "артикул")'),
            ('"height": ("height",)', '"height": ("height", "висота")'),
            ('"weight": ("weight",)', '"weight": ("weight", "вага")'),
            ('"box": ("size box",)', '"box": ("size box", "розмір коробки")'),
            ('"parts": ("number of parts",)', '"parts": ("number of parts", "кількість частин")'),
            ('if not any(c == "article" for c in lowered):', 'if not any(c in ("article", "артикул") for c in lowered):'),
        ]:
            s = once(s, a, b)
        s = once(s, '        if weight is None and volume:\n            weight = round(volume * 120, 1)  # оцінка: ~120 кг/м³ для литих ялинок\n', '')
        start = s.index("    # добір ваги/об'єму")
        end = s.index("    return result", start)
        s = s[:start] + """    # Actual weight only; missing weight is an explicit 10 kg estimate.
    for it in result:
        w = it.get("weight_kg")
        if w is None or not math.isfinite(w) or w <= 0:
            it["weight_kg"] = 10.0
            it["weight_estimated"] = True
""" + s[end:]
    elif name == "handlers_order.py":
        s = once(s, "import stock\n", "import stock\nimport shipment_boxes\n")
        marker = "async def show_cart("
        helper = """def _cart_places(cart):
    try:
        return shipment_boxes.place_count(
            [(catalog.by_article(c["article"]) or {}, c["qty"]) for c in cart])
    except shipment_boxes.PackagingError:
        return "потрібне уточнення розмірів коробок"


"""
        s = once(s, marker, helper+marker)
        if s.count("sum(int(i['qty']) for i in cart)") != 2:
            raise RuntimeError("Cart count context changed")
        s = s.replace("sum(int(i['qty']) for i in cart)", "_cart_places(cart)")
        s = once(s, "    Вага, обʼєм і вартість — суми позицій, місць — стільки, скільки одиниць\n    товару: дві ялинки їдуть однією накладною на два місця.", "    Кожна фізична коробка є окремим місцем з індивідуальними параметрами.")
        s = once(s, '        if item:\n            items.append((item, int(row["qty"])))\n    if not items:', '        if item is None:\n            return False, "У каталозі немає артикула: " + str(row["article"])\n        items.append((item, int(row["qty"])))\n    if not items:')
        s = once(s, '    weight = sum((it["weight_kg"] or 5) * q for it, q in items)\n    volume = sum((it.get("volume_m3") or 0) * q for it, q in items)\n    seats = sum(q for _, q in items)', """    try:
        shipment = shipment_boxes.build_shipment(items)
    except shipment_boxes.PackagingError as exc:
        for row in rows:
            row["status"] = "ТТН НЕ створено"
            row["comment"] = str(exc)
        return False, str(exc)
    weight, volume, seats = shipment["weight"], shipment["volume"], shipment["seats"]
    for warning in shipment["warnings"]:
        log.warning("Параметри упаковки: %s", warning)""")
        s = once(s, '            seats=seats,', '            seats=seats,\n            options_seat=shipment["options_seat"],')
    elif name == "webhook.py":
        s = once(s, "import logging\n", "import logging\nimport shipment_boxes\n")
        s = once(s, '                    res = await np.create_ttn(', '                    shipment = shipment_boxes.build_shipment([(item, qty)])\n                    res = await np.create_ttn(')
        s = once(s, '                        weight=(item["weight_kg"] or 5) * qty,\n                        volume=(item.get("volume_m3") or 0) * qty or None,\n                        seats=qty)', '                        weight=shipment["weight"],\n                        volume=shipment["volume"],\n                        seats=shipment["seats"],\n                        options_seat=shipment["options_seat"])')
    elif name == "novaposhta.py":
        s = once(s, '                     street_ref: str = "", building: str = "", flat: str = ""):', '                     street_ref: str = "", building: str = "", flat: str = "",\n                     options_seat: list[dict] | None = None):')
        s = once(s, '    import np_store\n    if not np_store.is_ready():', '    if not options_seat:\n        raise NPError("Немає індивідуальних параметрів коробок; ТТН не створено")\n    import np_store\n    if not np_store.is_ready():')
        s = once(s, '    if volume:\n        props["VolumeGeneral"] = str(round(volume, 3))', """    props["OptionsSeat"] = options_seat
    props["SeatsAmount"] = str(len(options_seat))
    props["Weight"] = str(round(sum(float(p["weight"]) for p in options_seat), 2))
    props["VolumeGeneral"] = str(sum(float(p["volumetricVolume"]) for p in options_seat))""")
    elif name == "ttn_retry.py":
        s = once(s, "import catalog\n", "import catalog\nimport shipment_boxes\n")
        s = once(s, '    unknown = []\n', '    unknown = []\n    packaging_items = []\n')
        s = once(s, '        weight += (item.get("weight_kg") or 5) * qty\n        volume += (item.get("volume_m3") or 0) * qty', '        packaging_items.append((item, qty))')
        s = once(s, '    # страхова сума', """    try:
        shipment = shipment_boxes.build_shipment(packaging_items)
    except shipment_boxes.PackagingError as exc:
        raise RetryError(str(exc)) from exc
    weight, volume, seats = shipment["weight"], shipment["volume"], shipment["seats"]
    for warning in shipment["warnings"]:
        log.warning("Параметри упаковки: %s", warning)

    # страхова сума""")
        s = once(s, '        seats=max(seats, 1),', '        seats=seats,\n        options_seat=shipment["options_seat"],')
    ast.parse(s, filename=name)
    return s

def main():
    updated = {}
    for name, expected in EXPECTED.items():
        b = Path(name).read_bytes()
        sha = hashlib.sha1(b"blob "+str(len(b)).encode()+b"\0"+b).hexdigest()
        if sha != expected:
            raise RuntimeError(f"{name} differs from reviewed main; no files changed")
        updated[name] = transform(name, b.decode("utf-8"))
    # Reject any uncovered TTN call site before writing files.
    for path in Path('.').rglob('*.py'):
        if '.git' in path.parts or path.name.startswith('test_'):
            continue
        tree = ast.parse(updated.get(str(path), path.read_text(encoding="utf-8")))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'create_ttn':
                if not any(k.arg == 'options_seat' for k in node.keywords):
                    raise RuntimeError(f"Uncovered TTN call in {path}")
    for name, text in updated.items():
        Path(name).write_text(text, encoding="utf-8")
    print("Updated five TTN modules; no external API calls made.")

if __name__ == "__main__":
    main()
