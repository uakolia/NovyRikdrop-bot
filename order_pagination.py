"""Pure helpers for paginating a user's order history by ORDER, not sheet row.

The source returns newest rows first. One order may occupy multiple rows.
Fetching progressively more rows works with both the direct Sheets API and
the existing Apps Script endpoint, without requiring a deployed script change.
"""
from dataclasses import dataclass
import hashlib
from typing import Awaitable, Callable


PAGE_SIZE = 10


def group_orders(rows: list[dict]) -> list[list[dict]]:
    groups: dict[str, list[dict]] = {}
    keys: list[str] = []
    for row in rows:
        order_no = str(row.get("order_no") or "").strip()
        key = order_no or f"__missing_{len(keys)}"
        if key not in groups:
            groups[key] = []
            keys.append(key)
        groups[key].append(row)
    return [groups[key] for key in keys]


def order_token(group: list[dict]) -> str:
    """Stable short token; a changed page can never open another order by index."""
    if not group:
        return ""
    first = group[0]
    source = "|".join(str(first.get(k) or "") for k in (
        "order_no", "created_at", "article",
    ))
    return hashlib.blake2s(source.encode("utf-8"), digest_size=6).hexdigest()


@dataclass
class OrderPage:
    groups: list[list[dict]]
    has_next: bool
    error: str | None = None


async def load_page(
    fetch_rows: Callable[[int], Awaitable[tuple[list[dict] | None, str | None]]],
    page: int,
    page_size: int = PAGE_SIZE,
) -> OrderPage:
    """Retrieve enough complete groups to show page + one next-page marker.

    Do not confuse a page of 10 orders with 10 *rows*: multi-item orders must
    be grouped before slicing. The extra group guarantees that the tenth
    listed order has all its rows, assuming rows per order are contiguous.
    """
    if page < 0 or page_size <= 0:
        raise ValueError("Invalid page size or index")
    required_groups = (page + 1) * page_size + 1
    limit = max(50, required_groups * 2)
    for _ in range(16):
        rows, error = await fetch_rows(limit)
        if rows is None:
            return OrderPage([], False, error or "Не вдалося завантажити замовлення")
        grouped = group_orders(rows)
        exhausted = len(rows) < limit
        if len(grouped) >= required_groups or exhausted:
            start = page * page_size
            return OrderPage(
                groups=grouped[start:start + page_size],
                has_next=len(grouped) > start + page_size,
                error=error,
            )
        limit *= 2
    return OrderPage([], False, "Забагато позицій в історії замовлень; звернись до менеджера.")
