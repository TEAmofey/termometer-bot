from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from config import bot_token

bot = Bot(bot_token, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()