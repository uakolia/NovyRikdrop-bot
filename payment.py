"""Реквізити для оплати та розрахунок суми, яку дропшипер має переказати."""
import os

RECEIVER = os.getenv("PAY_RECEIVER", "ФОП Гордійчук Микола Сергійович")
IBAN = os.getenv("PAY_IBAN", "UA483220010000026009340165021")
TAX_ID = os.getenv("PAY_TAX_ID", "3760807391")
BANK = os.getenv("PAY_BANK", "АТ «УНІВЕРСАЛ БАНК»")
MFO = os.getenv("PAY_MFO", "322001")
BANK_EDRPOU = os.getenv("PAY_BANK_EDRPOU", "21133352")


def details_text(amount: int, order_hint: str = "") -> str:
    purpose = f"Оплата за ялинку{(' ' + order_hint) if order_hint else ''}"
    return (
        f"💳 <b>До сплати: {amount:,} грн</b>".replace(",", " ") + "\n\n"
        "<b>Реквізити для переказу:</b>\n"
        f"Отримувач: <code>{RECEIVER}</code>\n"
        f"IBAN: <code>{IBAN}</code>\n"
        f"ІПН/ЄДРПОУ: <code>{TAX_ID}</code>\n"
        f"Банк: {BANK}\n"
        f"МФО: {MFO} · ЄДРПОУ банку: {BANK_EDRPOU}\n"
        f"Призначення: <i>{purpose}</i>\n\n"
        "Натисніть на потрібний рядок, щоб скопіювати.\n\n"
        "📸 <b>Після оплати надішліть сюди скрін чека</b> — "
        "фото або файл одним повідомленням."
    )


def due_amount(price_drop: int, cod_amount) -> int:
    """Скільки дропшипер має переказати: дроп-ціна мінус наложений платіж."""
    try:
        cod = int(cod_amount or 0)
    except (TypeError, ValueError):
        cod = 0
    return max(int(price_drop) - cod, 0)
