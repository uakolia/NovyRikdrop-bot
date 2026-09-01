import os

from dotenv import load_dotenv

load_dotenv()


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

# який дроп-тариф показувати дропшиперам: drop1 / drop2 / drop3
PRICE_TIER = os.getenv("PRICE_TIER", "drop3")

# Google Apps Script вебхук для запису замовлень у таблицю
SHEET_WEBHOOK_URL = os.getenv("SHEET_WEBHOOK_URL", "")

# Google Sheet прайс-лист (для /reload)
PRICELIST_SHEET_ID = os.getenv("PRICELIST_SHEET_ID", "18OkfzTnujb_VFTX0UuDlkEwCBNiR1pFy5OeafHugYFg")
PRICELIST_GID = os.getenv("PRICELIST_GID", "0")

# Вебхук для замовлень із сайту (Weblium через Zapier/Make тощо)
WEBLIUM_SECRET = os.getenv("WEBLIUM_SECRET", "")
PORT = int(os.getenv("PORT", "8080"))

DATA_DIR = os.getenv("DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))
