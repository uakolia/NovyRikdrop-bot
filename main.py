"""Точка входу: Telegram-бот (long polling) + HTTP-вебхук для сайту."""
import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiohttp import web

import access
import catalog, config
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
    await storage.init_order_seq()

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
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
