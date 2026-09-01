"""Точка входу: Telegram-бот (long polling) + HTTP-вебхук для сайту."""
import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiohttp import web

import catalog, config
import handlers_admin as admin
import handlers_order as order
import handlers_start as start
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
    dp.include_router(admin.router)
    dp.include_router(start.router)
    dp.include_router(order.router)

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
