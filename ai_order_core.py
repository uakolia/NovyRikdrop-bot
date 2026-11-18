"""Deterministic validation of AI-extracted order drafts; no external writes.

The model extracts text only. Product/SKU, prices, phone and payment are
resolved or validated here before the normal order FSM is allowed to confirm.
"""
import difflib
import re
import unicodedata
from decimal import Decimal, InvalidOperation

COMMON = {"ялинка", "ялинку", "ялинки", "штучна", "штучну", "шт", "модель", "моделі"}
SIZE = re.compile(r"(?<!\d)(\d{1,3}(?:[.,]\d{1,2})?)\s*(см|cm|м|m)\b", re.I)
BARE_SIZE = re.compile(r"(?<!\d)(\d{3}|\d[.,]\d{1,2})(?!\d)")
FIO_WORD = re.compile(r"[А-ЯІЇЄҐа-яіїєґA-Za-z'’\-]{2,40}\Z")


def cm(raw):
    """Convert an explicit size to centimeters; return None if ambiguous."""
    s = str(raw or "").strip().lower()
    m = SIZE.search(s)
    if m:
        number = m.group(1)
        unit = m.group(2)
    else:
        m = BARE_SIZE.search(s)
        if not m:
            return None
        number = m.group(1)
        unit = "cm" if len(number) == 3 and number.isdigit() else "m"
    try:
        x = Decimal(number.replace(",", "."))
        n = x if unit in ("см", "cm") else x * 100
        return int(n) if 30 <= n <= 500 and n == int(n) else None
    except (InvalidOperation, ValueError):
        return None


def clean_name(value):
    """Normalize names for comparison; ignore size and generic product words."""
    value = str(value or "").casefold().replace("ё", "е")
    value = SIZE.sub(" ", value)
    value = BARE_SIZE.sub(" ", value)
    value = unicodedata.normalize("NFKC", value)
    tokens = re.findall(r"[\w]+", value, flags=re.UNICODE)
    return " ".join(t for t in tokens if t not in COMMON)


def normalize_phone(value):
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) == 10 and digits.startswith("0"):
        return "+38" + digits
    if len(digits) == 12 and digits.startswith("380"):
        return "+" + digits
    return ""


def normalize_warehouse(value):
    text = str(value or "").strip()
    found = re.search(r"\d{1,5}", text)
    return str(int(found.group())) if found else ""


def _score(query, name):
    a, b = clean_name(query), clean_name(name)
    if not a or not b:
        return 0
    if a == b:
        return 100
    if len(a) >= 4 and (b.startswith(a + " ") or a in b.split()):
        return 83
    if len(a) >= 5 and a in b:
        return 78
    ratio = difflib.SequenceMatcher(None, a, b).ratio()
    return int(ratio * 70) if ratio >= 0.86 else 0


def resolve_item(item, entries):
    """Return (matching catalog row, problem). Never choose close alternatives."""
    query = str(item.get("query") or "").strip()
    size = str(item.get("size") or "").strip()
    if not query:
        return None, "Вкажіть назву або артикул товару."
    height = cm(size) or cm(query)
    exact_sku = [e for e in entries if e["article"].casefold() == query.casefold()]
    if exact_sku:
        if height and cm(exact_sku[0]["size"]) != height:
            return None, f"Артикул {query} не відповідає висоті {size}."
        return exact_sku[0], ""
    possible = []
    for e in entries:
        if height is not None and cm(e["size"]) != height:
            continue
        score = max((_score(query, name) for name in e["names"]), default=0)
        if score >= 75:
            possible.append((score, e))
    if not possible:
        return None, f"Не знайдено точного товару «{query}» ({size or 'без висоти'}). Вкажіть артикул або повну назву і висоту."
    best = max(score for score, _ in possible)
    tied = [e for score, e in possible if score == best]
    if len(tied) != 1:
        examples = ", ".join(e["article"] for e in tied[:4])
        return None, f"«{query}»: є кілька варіантів ({examples}). Уточніть артикул або висоту."
    return tied[0], ""


def plan(draft, entries):
    """Return (fields, issues). Fields may be used only when issues are empty."""
    errors, cart = [], []
    items = draft.get("items") or []
    if not isinstance(items, list) or not items:
        errors.append("Вкажіть товар, розмір і кількість.")
    elif len(items) > 6:
        errors.append("Максимум 6 різних позицій в одному AI-замовленні.")
    else:
        for i, raw in enumerate(items, 1):
            if not isinstance(raw, dict):
                errors.append(f"Позиція {i}: невірні дані.")
                continue
            try:
                qty = int(raw.get("quantity") or 0)
                price = int(raw.get("unit_sale_price") or 0)
            except (ValueError, TypeError):
                qty, price = 0, 0
            if not 1 <= qty <= 20:
                errors.append(f"Позиція {i}: вкажіть кількість від 1 до 20 шт.")
            if not 1 <= price <= 500000:
                errors.append(f"Позиція {i}: вкажіть ціну продажу за 1 шт. в гривнях.")
            resolved, problem = resolve_item(raw, entries)
            if problem:
                errors.append(f"Позиція {i}: {problem}")
            if resolved and 1 <= qty <= 20 and 1 <= price <= 500000:
                key = resolved["article"]
                if any(c["article"] == key for c in cart):
                    errors.append(f"Позиція {i}: той самий артикул указаний двічі. Об'єднайте кількість.")
                else:
                    cart.append({"article": key, "qty": qty, "unit_sale_price": price})
    payment = str(draft.get("payment") or "").strip()
    if payment not in ("післяплата", "передплата", "часткова"):
        errors.append("Тип оплати: післяплата, передплата або часткова передплата?")
    amount = sum(c["qty"] * c["unit_sale_price"] for c in cart)
    prepaid = 0
    if payment == "часткова":
        try:
            prepaid = int(draft.get("prepaid") or 0)
        except (TypeError, ValueError):
            prepaid = 0
        if not 0 < prepaid < amount:
            errors.append("Для часткової передплати вкажіть уже сплачену суму в гривнях.")
    elif payment == "передплата":
        prepaid = amount
    fio = str(draft.get("fio") or "").strip()
    parts = fio.split()
    if len(parts) not in (2, 3) or any(not FIO_WORD.fullmatch(x) for x in parts):
        errors.append("Вкажіть ПІБ отримувача (прізвище та ім'я, по батькові необов'язково).")
    phone = normalize_phone(draft.get("phone"))
    if not phone:
        errors.append("Вкажіть український номер отримувача, наприклад 0671234567.")
    city = str(draft.get("city") or "").strip()
    if len(city) < 2 or len(city) > 80:
        errors.append("Вкажіть місто одержувача.")
    warehouse_no = normalize_warehouse(draft.get("warehouse_number"))
    if not warehouse_no:
        errors.append("Вкажіть номер відділення Нової Пошти (поки підтримуємо відділення, не адресну доставку).")
    return ({"cart": cart, "payment": payment, "sale_price": amount,
             "prepaid": prepaid, "cod_amount": amount - prepaid,
             "fio": fio, "fname": parts[1] if len(parts) >= 2 else "",
             "lname": parts[0] if parts else "",
             "mname": parts[2] if len(parts) == 3 else "",
             "phone": phone, "city_query": city,
             "warehouse_number": warehouse_no}, errors)
