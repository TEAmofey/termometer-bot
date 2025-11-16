from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timedelta, timezone, tzinfo
from html import escape
from typing import Iterable
from zoneinfo import ZoneInfo

from loguru import logger

from bot_instance import bot
from constants import EMOJI_REMINDER
from db.base_event import EventRecord, STATUS_APPROVED
from db.database import Database
from utils.misc import format_datetime


def _safe_zone() -> tzinfo:
    try:
        return ZoneInfo("Europe/Moscow")
    except Exception:  # noqa: BLE001
        return timezone(timedelta(hours=3))


def _ensure_local(dt: datetime, tzinfo: tzinfo) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=tzinfo)
    return dt.astimezone(tzinfo)


def _format_event_block(event: EventRecord, tzinfo: tzinfo) -> str:
    lines: list[str] = []
    title = escape(event.title or "Событие")
    lines.append(f"🔔 <b>{title}</b>")

    starts_at = event.scheduled_datetime()
    ends_at = event.end_datetime()
    if starts_at:
        start_local = _ensure_local(starts_at, tzinfo)
        date_part = start_local.strftime("%d.%m.%Y")
        time_part = start_local.strftime("%H:%M")
        if ends_at:
            end_local = _ensure_local(ends_at, tzinfo)
            time_part = f"{time_part} – {end_local.strftime('%H:%M')}"
        lines.append(f"🕒 {date_part} · {time_part}")
    else:
        lines.append("🕒 Дата уточняется")

    if event.location:
        lines.append(f"📍 {escape(event.location)}")

    description = (event.short_description or "").strip()
    if description:
        lines.append(f"📝 {escape(description)}")

    if event.registration_link:
        lines.append(f"🔗 {event.registration_link}")

    return "\n".join(lines)


class NotificationService:
    def __init__(self, db: Database):
        self.db = db
        self.events_repo = db.events
        self.timezone = _safe_zone()

    async def run_daily_notifications(self) -> None:
        logger.info("Notification service: scheduler started.")
        while True:
            now = datetime.now(self.timezone)
            target_time = now.replace(hour=20, minute=0, second=0, microsecond=0)
            if now >= target_time:
                target_time += timedelta(days=1)

            wait_seconds = max(60.0, (target_time - now).total_seconds())
            logger.info(
                "Notification service: sleeping for {:.0f} seconds until {}",
                wait_seconds,
                target_time.isoformat(),
            )
            try:
                await asyncio.sleep(wait_seconds)
            except asyncio.CancelledError:
                logger.info("Notification service: cancelled, stopping.")
                raise

            try:
                await self.send_tomorrows_reminders()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "Notification service: failed to send reminders: {}", exc
                )
                await asyncio.sleep(300)

    def _events_for_tomorrow(self, tomorrow: datetime) -> Iterable[EventRecord]:
        tomorrow_date = tomorrow.date()
        all_events = self.events_repo.list_all()
        for event in all_events:
            if event.status != STATUS_APPROVED:
                continue
            start = event.scheduled_datetime()
            if not start:
                continue
            start_local = _ensure_local(start, self.timezone)
            if start_local.date() == tomorrow_date:
                yield event

    async def send_tomorrows_reminders(self) -> None:
        now = datetime.now(self.timezone)
        tomorrow = now + timedelta(days=1)
        events = list(self._events_for_tomorrow(tomorrow))

        if not events:
            logger.info("Notification service: no events for {}.", tomorrow.date())
            return

        users_events: dict[int, list[EventRecord]] = defaultdict(list)
        for event in events:
            unique_attendees = {int(user_id) for user_id in event.attendees}
            if not unique_attendees:
                continue
            for user_id in unique_attendees:
                users_events[user_id].append(event)

        if not users_events:
            logger.info("Notification service: no attendees registered for tomorrow.")
            return

        date_display = format_datetime(tomorrow.date())
        logger.info(
            "Notification service: preparing reminders for {} users ({} events).",
            len(users_events),
            len(events),
        )

        sent_count = 0
        for user_id, user_events in users_events.items():
            sorted_events = sorted(
                user_events,
                key=lambda item: _ensure_local(
                    item.scheduled_datetime() or now, self.timezone
                ),
            )
            event_blocks = [
                _format_event_block(event, self.timezone) for event in sorted_events
            ]
            if not event_blocks:
                continue
            header = f"{EMOJI_REMINDER} <b>Напоминание: завтра у вас мероприятия — {date_display}</b>"
            payload = "\n\n".join([header, *event_blocks])

            try:
                await bot.send_message(
                    user_id,
                    payload,
                    disable_web_page_preview=True,
                )
                sent_count += 1
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "Notification service: failed to deliver reminder to {}: {}",
                    user_id,
                    exc,
                )

        logger.info(
            "Notification service: reminders delivered to {} users for {}.",
            sent_count,
            date_display,
        )


async def start_notification_service() -> None:
    db = Database.get()
    service = NotificationService(db)
    await service.run_daily_notifications()
