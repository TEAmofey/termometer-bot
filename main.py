import asyncio
import locale
import sys

from loguru import logger

from bot_instance import bot, dp
from handlers import get_routers
from handlers.notifications import start_notification_service
from middleware.user_meta import UserMetaMiddleware
from services.thermometer import start_thermometer_service

logger.remove()
logger.add(sys.stderr, level="INFO")
logger.add("bot.log", rotation="1 day", compression="zip", level="DEBUG")

locale.setlocale(locale.LC_TIME, "ru_RU.UTF-8")


async def main():
    logger.info("Бот запускается...")
    # Подключаем middleware для обновления username и команд
    dp.message.middleware(UserMetaMiddleware(bot))
    for router in get_routers():
        dp.include_router(router)
    background_tasks = [
        asyncio.create_task(start_notification_service()),
        asyncio.create_task(start_thermometer_service()),
    ]
    try:
        await dp.start_polling(bot)
    finally:
        for task in background_tasks:
            task.cancel()
        await asyncio.gather(*background_tasks, return_exceptions=True)
    logger.info("Бот завершил работу.")


if __name__ == "__main__":
    asyncio.run(main())
