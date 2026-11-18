"""Display order product names without repeating a matching tree height."""

import re
from decimal import Decimal, InvalidOperation

_HEIGHT = re.compile(
    r"(?<!\d)(\d{1,3}(?:[.,]\d{1,2})?)\s*(\u0441\u043c|cm|\u043c|m)\b",
    re.IGNORECASE,
)
_BARE_HEIGHT = re.compile(r"\d{1,3}(?:[.,]\d{1,2})?")
_TRAILING_CM = re.compile(r"(?<!\d)(\d{3})\s*$")


def _cm(value: str, unit: str) -> int | None:
    try:
        number = Decimal(value.replace(",", "."))
    except InvalidOperation:
        return None
    centimeters = number if unit.casefold() in ("cm", "\u0441\u043c") else number * 100
    if 100 <= centimeters <= 500 and centimeters == centimeters.to_integral_value():
        return int(centimeters)
    return None


def _size_cm(size: str) -> int | None:
    match = _HEIGHT.fullmatch(size.strip())
    if match:
        return _cm(match.group(1), match.group(2))
    match = _BARE_HEIGHT.fullmatch(size.strip())
    if not match:
        return None
    try:
        number = Decimal(match.group().replace(",", "."))
    except InvalidOperation:
        return None
    if 100 <= number <= 500 and number == number.to_integral_value():
        return int(number)
    if 1 <= number <= 5:
        return _cm(str(number), "m")
    return None


def product_with_height(name: str, size: str) -> str:
    """Keep height in product name once when the stored size is equivalent.

    Examples: 2.3m / 230, 2.6m / 2.6 m, 230cm / 2.3m.
    A contradictory size is preserved, not silently hidden.
    """
    name = str(name or "").strip()
    size = str(size or "").strip()
    if not name:
        return size
    if not size:
        return name
    wanted = _size_cm(size)
    if wanted is not None:
        for match in _HEIGHT.finditer(name):
            if _cm(match.group(1), match.group(2)) == wanted:
                return name
        trailing = _TRAILING_CM.search(name)
        if trailing and _size_cm(trailing.group(1)) == wanted:
            return name
    return f"{name} \u2014 {size}"
