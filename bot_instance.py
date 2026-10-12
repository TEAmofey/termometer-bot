import os

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
import config
from config import bot_token

proxy_url = os.getenv("TELEGRAM_PROXY_URL") or getattr(config, "TELEGRAM_PROXY_URL", None)
session = AiohttpSession(proxy=proxy_url)
bot = Bot(bot_token, session=session, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()
