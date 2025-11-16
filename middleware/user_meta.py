from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware, Bot
from aiogram.types import TelegramObject, User as AiogramUser

from db import Database
from utils.misc import update_commands_for_user


class UserMetaMiddleware(BaseMiddleware):
    """Middleware для обновления метаданных пользователя на каждом событии.

    - Обновляет/создаёт пользователя в БД: tg_id и username
    - Обновляет список команд для пользователя
    """

    def __init__(self, bot: Bot):
        self.bot = bot

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        tg_user: AiogramUser | None = data.get("event_from_user")

        if tg_user is not None:
            try:
                # Обновляем/создаём пользователя в БД
                db = Database.get()
                users = db.users
                username_value = tg_user.username or ""

                users.update_one(
                    {"tg_id": tg_user.id},
                    {"$set": {"tg_id": tg_user.id, "username": username_value}},
                    upsert=True,
                )

                # Обновляем доступные команды для пользователя
                await update_commands_for_user(tg_user.id)
            except Exception:
                # Ничего не делаем, чтобы не ломать обработку события
                pass

        return await handler(event, data)
