from __future__ import annotations

from datetime import date

from aiogram.types import BotCommand, BotCommandScopeChat

from bot_instance import bot
from constants import ADMIN_IDS


def format_datetime(value: date) -> str:
    return value.strftime("%d.%m.%Y")


async def update_commands_for_user(user_id: int) -> None:
    commands = [
        BotCommand(command="sos", description="Напишите помогаторам"),
        BotCommand(command="help", description="Что умеет бот"),
        BotCommand(command="events", description="Посмотреть события"),
        BotCommand(command="profile", description="Посмотреть профиль"),
        BotCommand(command="thermometer", description="Настроить термометр"),
        BotCommand(command="feedback", description="Отправить отзыв"),
    ]

    await bot.set_my_commands(commands, scope=BotCommandScopeChat(chat_id=user_id))
