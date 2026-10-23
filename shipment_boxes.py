"""Individual Nova Poshta places from existing dimensions (cm) and weights (kg)."""
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

class PackagingError(ValueError):
    pass

_N = r"\d+(?:[.,]\d+)?"
_BOX = re.compile(rf"({_N})\s*[*xх×]\s*({_N})\s*[*xх×]\s*({_N})", re.I)

def positive(value):
    try:
        n = Decimal(str(value).replace("\xa0", "").replace(" ", "").replace(",", "."))
        return n if n.is_finite() and n > 0 else None
    except (InvalidOperation, ValueError):
        return None

def parse_boxes(value):
    text = str(value or "").strip()
    matches = list(_BOX.finditer(text))
    rest = re.sub(r"(?:см|cm)", "", _BOX.sub("", text), flags=re.I)
    if not matches or rest.strip(" \t\r\n;,/"):
        raise PackagingError("Розміри коробки: потрібен формат 50*50*160 35*35*120")
    boxes = [tuple(Decimal(v.replace(",", ".")) for v in m.groups()) for m in matches]
    if any(d <= 0 for box in boxes for d in box):
        raise PackagingError("Розміри коробки мають бути більшими за нуль")
    return boxes

def quantity(value):
    n = positive(value)
    if n is None or n != n.to_integral_value():
        raise PackagingError("Кількість має бути додатним цілим числом")
    return int(n)

def place_count(items):
    return sum(len(parse_boxes(it.get("box"))) * quantity(q) for it, q in items)

def build_shipment(items):
    places, warnings = [], []
    for item, q in items:
        qty = quantity(q)
        article = str(item.get("article") or "?")
        try:
            boxes = parse_boxes(item.get("box"))
        except PackagingError as exc:
            raise PackagingError(f"{article}: {exc}") from exc
        measured = positive(item.get("weight_kg"))
        estimated = measured is None or bool(item.get("weight_estimated"))
        total = (Decimal("10") if estimated else measured).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if estimated:
            warnings.append(f"{article}: використано оцінку 10 кг на одиницю")
        volumes = [a*b*c/Decimal("1000000") for a, b, c in boxes]
        weights = [(total*v/sum(volumes)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) for v in volumes[:-1]]
        weights.append(total-sum(weights))
        if any(w <= 0 for w in weights):
            raise PackagingError(f"{article}: замала вага для розподілу між коробками")
        if len(boxes) > 1:
            warnings.append(f"{article}: вага коробок оцінена пропорційно об'єму")
        for _ in range(qty):
            for (a, b, c), weight, volume in zip(boxes, weights, volumes):
                places.append({"volumetricLength": format(a, "f"), "volumetricWidth": format(b, "f"), "volumetricHeight": format(c, "f"), "volumetricVolume": format(volume, "f"), "weight": format(weight, "f")})
    if not places:
        raise PackagingError("Немає коробок для створення ТТН")
    return {"options_seat": places, "seats": len(places), "weight": float(sum(Decimal(p["weight"]) for p in places)), "volume": float(sum(Decimal(p["volumetricVolume"]) for p in places)), "warnings": list(dict.fromkeys(warnings))}
