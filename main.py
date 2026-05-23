import asyncio
import logging

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
from services.cryptobot_polling import cryptobot_poller
from bot.commands import setup_commands

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


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

    # CryptoBot polling (не требует домена и HTTPS)
    cryptobot_poller.set_bot(bot)
    await cryptobot_poller.start()

    try:
        logger.info("Bot started.")
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await parser_manager.stop()
        await cryptobot_poller.stop()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
