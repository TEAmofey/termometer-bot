from __future__ import annotations

import asyncio
from datetime import datetime, time as dt_time, timedelta, timezone, tzinfo
from typing import Any, Dict, Tuple
from zoneinfo import ZoneInfo

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from loguru import logger

from bot_instance import bot
from config import POMAGATOR_CHAT_ID
from db.database import Database
from db.user import User

THERMOMETER_MESSAGE_BASE = (
    "Привет! Это еженедельная проверка твоего самочувствия. Как твои дела? "
    "Если имеются какие-либо трудности, жми кнопку справа, и мы придём на помощь!"
)

THERMOMETER_OK_CALLBACK = "thermo:ok"
THERMOMETER_HELP_CALLBACK = "thermo:help"
THERMOMETER_OK_SUFFIX = "\n\n✅ Всё хорошо — приятно слышать!"
THERMOMETER_HELP_SUFFIX = "\n\n🆘 Пользователь попросил о помощи."

DEFAULT_THERMOMETER_SETTINGS: Dict[str, Any] = {
    "enabled": True,
    "weekday": 6,  # Sunday by default
    "time": "12:00",
    "last_sent_at": None,
}

WEEKDAY_CHOICES: Tuple[Tuple[int, str, str], ...] = (
    (0, "Понедельник", "Пн"),
    (1, "Вторник", "Вт"),
    (2, "Среда", "Ср"),
    (3, "Четверг", "Чт"),
    (4, "Пятница", "Пт"),
    (5, "Суббота", "Сб"),
    (6, "Воскресенье", "Вс"),
)

TIME_CHOICES: Tuple[str, ...] = ("10:00", "12:00", "15:00", "18:00")


def _safe_zone() -> tzinfo:
    try:
        return ZoneInfo("Europe/Moscow")
    except Exception:  # noqa: BLE001
        return timezone(timedelta(hours=3))


def merge_thermometer_settings(raw: Dict[str, Any] | None) -> Dict[str, Any]:
    settings = dict(DEFAULT_THERMOMETER_SETTINGS)
    if not isinstance(raw, dict):
        return settings

    if "enabled" in raw:
        settings["enabled"] = bool(raw["enabled"])
    if "weekday" in raw:
        try:
            weekday = int(raw["weekday"])
        except (TypeError, ValueError):
            weekday = settings["weekday"]
        if 0 <= weekday <= 6:
            settings["weekday"] = weekday
    if "time" in raw and isinstance(raw["time"], str):
        settings["time"] = raw["time"]
    if "last_sent_at" in raw:
        settings["last_sent_at"] = raw["last_sent_at"]
    return settings


def _parse_time(value: str) -> dt_time:
    try:
        return dt_time.fromisoformat(value)
    except ValueError:
        default = DEFAULT_THERMOMETER_SETTINGS["time"]
        return dt_time.fromisoformat(default)


def _parse_datetime(value: str | None, tz: tzinfo) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=tz)
    return dt.astimezone(tz)


def _current_schedule_datetime(settings: Dict[str, Any], now: datetime, tz: tzinfo) -> datetime:
    weekday = int(settings.get("weekday", DEFAULT_THERMOMETER_SETTINGS["weekday"]))
    send_time = _parse_time(settings.get("time", DEFAULT_THERMOMETER_SETTINGS["time"]))

    current_weekday = now.weekday()
    days_diff = (current_weekday - weekday) % 7
    scheduled_date = now.date() - timedelta(days=days_diff)
    scheduled_dt = datetime.combine(scheduled_date, send_time, tzinfo=tz)
    if scheduled_dt > now:
        scheduled_dt -= timedelta(days=7)
    return scheduled_dt


def _build_thermometer_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Все ок, спасибо",
                    callback_data=THERMOMETER_OK_CALLBACK,
                ),
                InlineKeyboardButton(
                    text="Хочу поговорить",
                    callback_data=THERMOMETER_HELP_CALLBACK,
                ),
            ]
        ]
    )


class ThermometerService:
    def __init__(self, db: Database | None = None):
        self.db = db or Database.get()
        self.timezone = _safe_zone()

    async def run(self) -> None:
        logger.info("Thermometer service: scheduler started.")
        try:
            while True:
                await self._tick()
                await asyncio.sleep(60)
        except asyncio.CancelledError:
            logger.info("Thermometer service: cancelled, stopping.")
            raise

    async def _tick(self) -> None:
        now = datetime.now(self.timezone)
        users = self.db.users.find()
        for doc in users:
            user_id = doc.get("tg_id")
            if not user_id:
                continue
            user = User(doc)
            if not user.is_registration_complete():
                continue
            settings = merge_thermometer_settings(doc.get("thermometer"))
            if not settings.get("enabled", True):
                continue

            scheduled_dt = _current_schedule_datetime(settings, now, self.timezone)
            last_sent_at = _parse_datetime(settings.get("last_sent_at"), self.timezone)

            if last_sent_at and last_sent_at >= scheduled_dt:
                continue
            if now < scheduled_dt:
                continue

            delivered = await self._send_thermometer_message(user_id)
            if delivered:
                settings["last_sent_at"] = now.isoformat()
                self._store_settings(user_id, settings)

    async def _send_thermometer_message(self, tg_id: int) -> bool:
        try:
            await bot.send_message(
                tg_id,
                THERMOMETER_MESSAGE_BASE,
                reply_markup=_build_thermometer_keyboard(),
            )
            logger.debug("Thermometer service: message delivered to {}", tg_id)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.error("Thermometer service: failed to deliver to {}: {}", tg_id, exc)
            return False

    def _store_settings(self, tg_id: int, settings: Dict[str, Any]) -> None:
        payload = dict(settings)
        self.db.users.update_one(
            {"tg_id": tg_id},
            {"$set": {"tg_id": tg_id, "thermometer": payload}},
            upsert=True,
        )


async def start_thermometer_service() -> None:
    service = ThermometerService()
    await service.run()


def build_pomagator_payload(user_id: int, full_name: str | None, username: str | None) -> str:
    display_name = full_name or "Пользователь"
    mention = f'<a href="tg://user?id={user_id}">{display_name}</a>'
    username_part = f" (@{username})" if username else ""
    timestamp = datetime.now(_safe_zone()).strftime("%d.%m.%Y %H:%M")
    return (
        "🌡 <b>Запрос термометра</b>\n"
        f"👤 {mention}{username_part}\n"
        f"🕒 {timestamp}\n\n"
        "Пользователь сообщил, что хочет поговорить."
    )


async def forward_to_pomagators(payload: str) -> bool:
    if not POMAGATOR_CHAT_ID:
        logger.warning("Thermometer service: POMAGATOR_CHAT_ID is not configured.")
        return False
    try:
        await bot.send_message(POMAGATOR_CHAT_ID, payload, reply_markup=None)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.error("Thermometer service: failed to notify pomagators: {}", exc)
        return False
