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


async def check_stock_articles():
    """Звірити «Залишки дропшиперів» із каталогом і голосно сказати про промахи.

    Мовчазний промах зіставлення — найгірший сценарій: залишок просто не
    знайдеться, і ніхто цього не помітить. Тому пишемо в лог і артикули без
    товару, і колізії канонічних ключів (якщо після оновлення прайсу два різні
    артикули раптом зведуться до одного ключа).
    """
    import sheets_store
    if not sheets_store.enabled():
        return
    dupes = article_key.collisions(i["article"] for i in catalog.items())
    for key, originals in dupes.items():
        log.error("Колізія ключа %s: %s — різні артикули збігаються після "
                  "нормалізації, зіставлення ненадійне", key, ", ".join(originals))
    n_rows, err = await stock.refresh()
    if err:
        log.warning("Залишки дропшиперів не прочитались: %s", err)
        return
    rows = stock.all_rows()
    missing = [r for r in rows if not catalog.by_article(r.get("article", ""))]
    for r in missing:
        log.error("Залишки: артикул %r (%s) не знайдено в каталозі — "
                  "перевірте прайс і config.PRICELIST_TABS",
                  r.get("article"), r.get("dropshipper") or r.get("tg_id"))
    log.info("Залишки дропшиперів: %d рядків, без товару в каталозі: %d",
             len(rows), len(missing))


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
            n, err = await storage.sync_from_sheet()
            if err:
                log.warning("Дропшипери не оновились: %s", err)
            n_alias, alias_err = await aliases.sync_from_sheet()
            n_stock, stock_err = await stock.refresh()
            if stock_err:
                log.warning("Залишки не оновились: %s", stock_err)
            log.info("Оновлено з таблиці: дропшиперів %d, назв %d, залишків %d",
                     n, n_alias, n_stock)
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

    # постійне сховище: підтягуємо схвалених дропшиперів і номер замовлення
    n, err = await storage.sync_from_sheet()
    if err:
        log.warning("Дропшипери з таблиці не підтягнулись: %s", err)
    else:
        log.info("Схвалених дропшиперів у таблиці: %d", n)
    n_alias, alias_err = await aliases.sync_from_sheet()
    if alias_err:
        log.warning("Власні назви не підтягнулись: %s", alias_err)
    else:
        log.info("Власних назв товарів: %d", n_alias)
    await storage.init_order_seq()
    await check_stock_articles()
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
        poller.cancel()
        syncer.cancel()
        wh_syncer.cancel()
        await runner.cleanup()
        await http_client.close()      # одна спільна сесія на весь бот


if __name__ == "__main__":
    asyncio.run(main())
