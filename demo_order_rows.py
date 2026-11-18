"""Read-only sample orders for one authorized Telegram account.

These rows exist ONLY inside the "My Orders" UI. Never store them in the
Google order journal, submit them to Nova Poshta, or include them in stock
calculations. Delete this module and its handler hook to remove the samples.
"""

DEMO_TELEGRAM_ID = 260287389
DEMO_ORDER_COUNT = 35

_PRODUCTS = (
    ("ТЕСТ · Українська Преміум", "2.3 м"),
    ("ТЕСТ · Класична", "2.5 м"),
    ("ТЕСТ · Лита Люкс", "2.1 м"),
    ("ТЕСТ · Слім", "1.8 м"),
)
_SCENARIOS = (
    ("нове", "", ""),
    ("ТТН створено", "Нова пошта очікує надходження від відправника", "TEST-NP"),
    ("ТТН створено", "Відправлення у дорозі", "TEST-NP"),
    ("ТТН створено", "Відправлення отримано", "TEST-NP"),
    ("ТТН створено", "Одержано і створено ЄН зворотньої доставки", "TEST-NP"),
    ("очікує оплати", "", ""),
)


def for_user(telegram_id: int) -> list[dict]:
    """Newest first; multi-item orders have adjacent rows, same order number."""
    if str(telegram_id) != str(DEMO_TELEGRAM_ID):
        return []

    output = []
    for n in range(DEMO_ORDER_COUNT, 0, -1):
        status, np_status, ttn_prefix = _SCENARIOS[(n - 1) % len(_SCENARIOS)]
        items = 2 if n % 7 == 0 else 1
        for item_index in range(items):
            name, height = _PRODUCTS[(n + item_index) % len(_PRODUCTS)]
            output.append({
                "order_no": f"DEMO-{n:03d}",
                "created_at": f"{(n % 17) + 1:02d}.11.2026 12:00",
                "source": "TEST ONLY",
                "dropshipper_id": str(DEMO_TELEGRAM_ID),
                "dropshipper": "Тестовий акаунт",
                "article": f"TEST-NO-STOCK-{n:03d}-{item_index + 1}",
                "product": name,
                "size": height,
                "qty": 1,
                "price_drop": "0",
                "payment": "ТЕСТ (без оплати)",
                "recipient_fio": "Тестовий отримувач — НЕ ВІДПРАВЛЯТИ",
                "recipient_phone": "—",
                "city": "Тестове місто",
                "warehouse": "Тестове відділення",
                "ttn": f"{ttn_prefix}-{n:03d}" if ttn_prefix else "",
                "status": status,
                "comment": "ДЕМО: не є справжнім замовленням",
                "sale_price": "0",
                "prepaid": "0",
                "cod_amount": "",
                "delivery": "",
                "due_amount": "",
                "payment_proof": "",
                "np_status": np_status,
                "updated_at": "18.11.2026 12:00 (ТЕСТ)",
            })
    return output
