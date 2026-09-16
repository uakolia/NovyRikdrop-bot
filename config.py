import os

from dotenv import load_dotenv

load_dotenv()


# Часовий пояс, у якому живуть дати в таблиці. Береться з TZ (Railway), але
# навіть якщо змінна зникне при перезбірці контейнера, лишиться Europe/Kyiv:
# мовчазний відкат на UTC зсунув би вік замовлень на 2-3 години.
TIMEZONE = os.getenv("TZ") or "Europe/Kyiv"


def tz():
    """ZoneInfo для TIMEZONE; якщо зони немає в системі — UTC і гучний лог."""
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    for name in (TIMEZONE, "Europe/Kyiv", "Europe/Kiev"):
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError, KeyError):
            continue
    import datetime as _dt
    import logging
    logging.getLogger(__name__).warning(
        "Часовий пояс %r не знайдено — рахуємо час за UTC. Перевірте TZ і "
        "пакет tzdata", TIMEZONE)
    return _dt.timezone.utc


def now():
    """Поточний час у часовому поясі таблиці (завжди з зоною)."""
    import datetime as _dt
    return _dt.datetime.now(tz())


def _ids(s):
    return [int(x) for x in (s or "").replace(";", ",").split(",") if x.strip().isdigit()]


BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_IDS = _ids(os.getenv("ADMIN_IDS"))
# куди слати сповіщення про замовлення (група або особистий чат); типово — перший адмін
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID") or (ADMIN_IDS[0] if ADMIN_IDS else 0))

# Нова Пошта
NP_API_KEY = os.getenv("NP_API_KEY", "")
NP_SENDER_REF = os.getenv("NP_SENDER_REF", "")             # Ref контрагента-відправника
NP_SENDER_CONTACT_REF = os.getenv("NP_SENDER_CONTACT_REF", "")
NP_SENDER_CITY_REF = os.getenv("NP_SENDER_CITY_REF", "")
NP_SENDER_WAREHOUSE_REF = os.getenv("NP_SENDER_WAREHOUSE_REF", "")
NP_SENDER_PHONE = os.getenv("NP_SENDER_PHONE", "")
# хто платить за доставку: Sender або Recipient
NP_PAYER_TYPE = os.getenv("NP_PAYER_TYPE", "Recipient")
# мінімальна вантажопідйомність відділення, кг (вимога: вантажні відділення)
NP_MIN_WAREHOUSE_WEIGHT = float(os.getenv("NP_MIN_WAREHOUSE_WEIGHT", "30"))
# створювати ТТН автоматично одразу після підтвердження замовлення
NP_AUTO_TTN = os.getenv("NP_AUTO_TTN", "1") == "1"
# додавати в ТТН «Контроль оплати» на суму «При отриманні» (1 — так)
NP_PAYMENT_CONTROL = os.getenv("NP_PAYMENT_CONTROL", "1") == "1"

# який дроп-тариф показувати дропшиперам: drop1 / drop2 / drop3
PRICE_TIER = os.getenv("PRICE_TIER", "drop3")

# Google Apps Script вебхук для запису замовлень у таблицю
SHEET_WEBHOOK_URL = os.getenv("SHEET_WEBHOOK_URL", "")
# спільний секрет із Властивостями Apps Script (без нього скрипт відхиляє запити)
SHEETS_API_SECRET = os.getenv("SHEETS_API_SECRET", "")

# як часто перепитувати статуси ТТН у Нової Пошти, секунд (0 — не опитувати)
TTN_POLL_SECONDS = int(os.getenv("TTN_POLL_SECONDS", "3600"))

# Google Sheet прайс-лист (для /reload)
PRICELIST_SHEET_ID = os.getenv("PRICELIST_SHEET_ID", "18OkfzTnujb_VFTX0UuDlkEwCBNiR1pFy5OeafHugYFg")
# вкладки прайсу: (gid, назва). Номер видно в URL таблиці: #gid=...
# Змінити можна командою /gids у боті або змінною PRICELIST_GIDS.
PRICELIST_TABS = [
    ("878017772", "PE Umbrella System"),
    ("1028706328", "NEW 2026 PE Umbrella System"),
    ("1450227207", "Christmas tree in a pot"),
    ("2086408970", "Wreaths"),
    ("1211818706", "Garlands"),
    ("33952076", "Ikebana"),
    ("498807060", "New Fly Tree"),
    ("525872130", "NEW Wall Tree"),
    ("1680206781", "Mini Tree"),
]

_env_gids = [g.strip() for g in os.getenv("PRICELIST_GIDS", "").replace(";", ",").split(",")
             if g.strip().isdigit()]
PRICELIST_GIDS = _env_gids or [g for g, _ in PRICELIST_TABS]
TAB_NAMES = {g: n for g, n in PRICELIST_TABS}

# Вебхук для замовлень із сайту (Weblium через Zapier/Make тощо)
WEBLIUM_SECRET = os.getenv("WEBLIUM_SECRET", "")
PORT = int(os.getenv("PORT", "8080"))

DATA_DIR = os.getenv("DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))
