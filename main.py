import asyncio
import logging

from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from config import load_config
from database import init_db
from bot.middlewares import DatabaseMiddleware, ActivityMiddleware
from bot.handlers.user import user_router
from bot.handlers.admin import admin_router
from parser.manager import parser_manager
from services.cryptobot_webhook import cryptobot_webhook_handler
from bot.commands import setup_commands

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

WEBHOOK_PORT = 8080


async def main() -> None:
    config = load_config()

    await init_db()
    logger.info("Database initialized.")

    bot = Bot(
        token=config.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())

    dp.update.middleware(DatabaseMiddleware())
    dp.update.middleware(ActivityMiddleware())

    dp.include_router(admin_router)
    dp.include_router(user_router)

    await setup_commands(bot)
    logger.info("Bot commands configured.")

    parser_manager.set_bot(bot)
    await parser_manager.start()
    logger.info("Parser manager started.")

    # CryptoBot webhook server (optional — only if CRYPTOBOT_TOKEN is set)
    webhook_runner = None
    if config.cryptobot_token:
        app = web.Application()
        app.router.add_post("/cryptobot/webhook", cryptobot_webhook_handler)
        webhook_runner = web.AppRunner(app)
        await webhook_runner.setup()
        site = web.TCPSite(webhook_runner, "0.0.0.0", WEBHOOK_PORT)
        await site.start()
        logger.info("CryptoBot webhook server started on port %s.", WEBHOOK_PORT)

    try:
        logger.info("Bot started.")
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await parser_manager.stop()
        if webhook_runner:
            await webhook_runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
