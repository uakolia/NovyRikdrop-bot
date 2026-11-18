"""Read-only, human-friendly labels for Nova Poshta statuses saved in Sheets.

Having a TTN does not mean a parcel was actually handed to Nova Poshta.
Keep the carrier's original status visible so an unfamiliar status is never
silently guessed to mean "shipped" or "received".
"""

from html import escape
import re


def ttn_text(value) -> str:
    """Google Sheets can represent a 14-digit TTN as an integral float."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    text = str(value if value is not None else "").strip()
    if re.fullmatch(r"\d{14}\.0", text):
        return text[:-2]
    return text


def delivery_label(row: dict) -> str:
    """Show three useful delivery stages without hiding exceptional states."""
    if not ttn_text(row.get("ttn")):
        return "⏳ Ще не відправлено · ТТН немає"
    status = str(row.get("np_status") or "").strip().casefold()
    if not status:
        return "🕓 ТТН створено · статус НП ще невідомий"
    if any(word in status for word in (
        "відмов", "повернен", "зворотн", "припинено зберігання"
    )):
        return "↩️ Відмова / повернення"
    if "не знайдено" in status:
        return "⚠️ ТТН не знайдено Новою Поштою"
    if "видалено" in status:
        return "❌ ТТН видалено"
    if "отримано для відправлення" in status or "одержано для відправлення" in status:
        return "🚚 Відправлено"
    if (
        "відправлення отримано" in status
        or "відправлення одержано" in status
        or "одержано отримувачем" in status
        or "отримано отримувачем" in status
        or "отримано одержувачем" in status
        or "одержано одержувачем" in status
        or "вручено" in status
        or status in {"отримано", "одержано"}
    ):
        return "✅ Отримано"
    if any(word in status for word in (
        "очікує надходження від відправника", "очікує відправника",
        "очікує відправлення", "ще не передано", "не передано",
        "не відправлено", "створено ттн", "ттн створено",
        "створено ен", "створено експрес", "оформлено накладну"
    )):
        return "⏳ Ще не відправлено"
    if any(word in status for word in (
        "у дорозі", "в дорозі", "прямує", "прибул", "прибув",
        "доставлено", "передано кур'єру", "передано кур’єру",
        "у відділенні", "на відділенні", "у поштоматі",
        "у місті", "відправлено", "передано на доставку",
        "отримано для відправлення"
    )):
        return "🚚 Відправлено"
    return "📦 Статус Нової Пошти"


def delivery_html(row: dict) -> str:
    """Safe Telegram HTML; status text from the sheet is never HTML markup."""
    label = delivery_label(row)
    original = str(row.get("np_status") or "").strip()
    if ttn_text(row.get("ttn")) and original:
        return f"{label}\n    <i>НП: {escape(original[:180])}</i>"
    return label
