# 🎄 Бот для дропшиперів — ялинки та новорічний декор

Всі файли лежать пласко (без папок) — завантажуються на GitHub простим перетягуванням.

## Запуск
1. Створіть бота у @BotFather, збережіть токен
2. `python setup_np.py ВАШ_КЛЮЧ_НП "Ваше місто"` — видасть 6 рядків для змінних
3. Google Таблиця замовлень: Розширення → Apps Script → вставте `orders_webhook.gs` → Деплой як веб-додаток (доступ: усі) → URL = SHEET_WEBHOOK_URL; у Налаштуваннях проєкту → Властивості скрипту додайте SHEETS_API_SECRET (довгий випадковий рядок)
4. Railway: Deploy from GitHub repo → Variables:
   - BOT_TOKEN, ADMIN_IDS
   - NP_API_KEY, NP_SENDER_REF, NP_SENDER_CONTACT_REF, NP_SENDER_CITY_REF, NP_SENDER_WAREHOUSE_REF, NP_SENDER_PHONE
   - SHEET_WEBHOOK_URL, SHEETS_API_SECRET (те саме значення, що у Властивостях скрипту)
   - опційно: PRICE_TIER (drop1/drop2/drop3), NP_PAYER_TYPE (Recipient/Sender), ADMIN_CHAT_ID, WEBLIUM_SECRET

## Команди адміна
/users — список дропшиперів, /block ID — закрити доступ, /reload — оновити ціни з прайсу, /id — показати ID чату

## Оновлення цін
Прайс має бути доступний «усім за посиланням» → команда /reload у боті.

## Вебхук Weblium
POST на https://ВАШ-ДОМЕН/weblium?secret=WEBLIUM_SECRET з полями article/product, qty, payment, fio, phone, city, warehouse.
