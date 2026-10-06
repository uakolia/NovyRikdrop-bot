"""Точка входу: Telegram-бот (long polling) + HTTP-вебхук для сайту."""
import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiohttp import web

import access
import aliases
import article_key
import catalog, config
import http_client
import np_tracking
import stock
import warehouse_sync
import storage
import handlers_admin as admin
import handlers_order as order
import handlers_myorders as myorders
import handlers_start as start
import handlers_support as support
from webhook import make_app

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("yalynkar")


async def load_sheet_data(initial: bool = False):
    """Прочитати з таблиці все, що бот звідти бере, — одним запитом.

    Google виконує скрипт одного користувача послідовно, тож окремі читання
    стають у чергу: при старті бот робив чотири запити підряд, і останній чекав
    усі попередні (у логах траплялося виконання на 22 секунди). what=bootstrap
    віддає дропшиперів, назви, залишки й максимальний номер за один раз.

    Якщо в таблиці розгорнуто старий скрипт (він такого запиту не знає),
    відкочуємося на окремі читання — бот має працювати й до редеплою скрипта.
    """
    import sheets_store
    if not sheets_store.enabled():
        storage.seq_give_up()
        return
    data, err = await sheets_store.fetch_bootstrap()
    if data is not None:
        n = storage.apply_dropshippers(data.get("dropshippers") or [])
        n_alias = aliases.apply_rows(data.get("aliases"))
        n_stock = stock.apply_rows(data.get("stock"))
        storage.apply_max_order_no(data.get("maxorder") or 0)
        log.info("З таблиці (один запит): дропшиперів %d, назв %d, залишків %d",
                 n, n_alias, n_stock)
    else:
        log.warning("Одним запитом не прочиталось (%s) — читаю окремо", err)
        n, d_err = await storage.sync_from_sheet()
        if d_err:
            log.warning("Дропшипери не підтягнулись: %s", d_err)
        n_alias, a_err = await aliases.sync_from_sheet()
        if a_err:
            log.warning("Власні назви не підтягнулись: %s", a_err)
        n_stock, s_err = await stock.refresh()
        if s_err:
            log.warning("Залишки не підтягнулись: %s", s_err)
        await storage.init_order_seq()
        log.info("З таблиці (окремі запити): дропшиперів %d, назв %d, "
                 "залишків %d", n, n_alias, n_stock)
    # навіть якщо щось не прочиталось, оформлення далі не чекає лічильника
    storage.seq_give_up()
    if initial:
        check_stock_articles()


def check_stock_articles():
    """Звірити «Залишки дропшиперів» із каталогом і голосно сказати про промахи.

    Мовчазний промах зіставлення — найгірший сценарій: залишок просто не
    знайдеться, і ніхто цього не помітить. Тому пишемо в лог і артикули без
    товару, і колізії канонічних ключів (якщо після оновлення прайсу два різні
    артикули раптом зведуться до одного ключа).

    Нічого не читає: працює по вже завантаженому кешу залишків.
    """
    dupes = article_key.collisions(i["article"] for i in catalog.items())
    for key, originals in dupes.items():
        log.error("Колізія ключа %s: %s — різні артикули збігаються після "
                  "нормалізації, зіставлення ненадійне", key, ", ".join(originals))
    rows = stock.all_rows()
    missing = [r for r in rows if not catalog.by_article(r.get("article", ""))]
    for r in missing:
        log.error("Залишки: артикул %r (%s) не знайдено в каталозі — "
                  "перевірте прайс і config.PRICELIST_TABS",
                  r.get("article"), r.get("dropshipper") or r.get("tg_id"))
    log.info("Залишки дропшиперів: %d рядків, без товару в каталозі: %d",
             len(rows), len(missing))


async def startup_load():
    """Перше завантаження у фоні. Падіння тут не має валити бота мовчки."""
    try:
        await load_sheet_data(initial=True)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001
        log.exception("Дані з таблиці не завантажились: %s", e)
        storage.seq_give_up()


async def sync_forever():
    """Періодично перечитувати таблицю: схвалені, тарифи, назви, залишки.

    Інакше зміна тарифу чи нова персональна назва діяли б лише після
    перезапуску бота.
    """
    period = config.SHEET_SYNC_SECONDS
    if period <= 0:
        return
    log.info("Дані з таблиці оновлюються кожні %d с", period)
    while True:
        await asyncio.sleep(period)
        try:
            await load_sheet_data()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.exception("Помилка циклу оновлення з таблиці: %s", e)


async def main():
    if not config.BOT_TOKEN:
        raise SystemExit("Задайте BOT_TOKEN у .env")
    catalog.load()
    log.info("Каталог: %d товарів", len(catalog.items()))

    bot = Bot(config.BOT_TOKEN,
              default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    gate = access.ApprovalMiddleware()
    dp.message.middleware(gate)
    dp.callback_query.middleware(gate)
    dp.include_router(admin.router)
    dp.include_router(support.router)
    dp.include_router(myorders.router)
    dp.include_router(start.router)
    dp.include_router(order.router)

    # Дані з таблиці читаємо У ФОНІ: читання йде через Apps Script і в
    # продакшені займало до 87 секунд, а бот стільки часу взагалі не відповідав
    # (для дропшипера це виглядало як «бот зламався»). Доступ працює й до
    # завантаження: storage.ensure_synced перечитує список при першому ж
    # натисканні незнайомця, а номер замовлення чекає storage.wait_seq_ready.
    loader = asyncio.create_task(startup_load())
    poller = asyncio.create_task(np_tracking.run_forever(bot))
    syncer = asyncio.create_task(sync_forever())
    wh_syncer = asyncio.create_task(warehouse_sync.run_forever(bot))

    # HTTP-сервер (health-check для хостингу + вебхук Weblium)
    app = make_app(bot)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", config.PORT)
    await site.start()
    log.info("HTTP на порту %d", config.PORT)

    try:
        await dp.start_polling(bot)
    finally:
        loader.cancel()
        poller.cancel()
        syncer.cancel()
        wh_syncer.cancel()
        await runner.cleanup()
        await http_client.close()      # одна спільна сесія на весь бот


if __name__ == "__main__":
    asyncio.run(main())
