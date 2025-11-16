from __future__ import annotations

from datetime import datetime
from typing import Iterable, Optional, Any

from constants import ADMIN_IDS, EVENT_TAGS
from db.base_event import EventRecord, EventsRepository, STATUS_APPROVED
from db.database import Database
from db.user import User
from utils.users import get_direction_track

TAG_TITLE_BY_SLUG = {slug: title for slug, title in EVENT_TAGS}
LEGACY_TAG_ALL = "all"
TAG_ORDER = [slug for slug, _ in EVENT_TAGS]
PARTICIPANTS_PER_PAGE = 10


def number_to_emoji(number: int) -> str:
    return "".join(f"{int(digit)}️⃣" for digit in str(number))


def events_repo() -> EventsRepository:
    return Database.get().events


def load_user(tg_id: int) -> Optional[User]:
    doc = Database.get().users.find_one({"tg_id": tg_id})
    return User(doc) if doc else None


def user_track(user: Optional[User]) -> Optional[str]:
    if not user:
        return None
    return user.raw.get("direction_track") or get_direction_track(user.get_direction())


def event_visible_for_user(event: EventRecord, user: Optional[User]) -> bool:
    if user and user.tg_id in ADMIN_IDS:
        return True
    if user and user.tg_id == event.created_by:
        return True
    if event.status != STATUS_APPROVED:
        return False
    if not event.tags:
        return True
    if LEGACY_TAG_ALL in event.tags:
        return True
    track = user_track(user)
    if not track:
        return False
    return track in event.tags


def sort_events(events: Iterable[EventRecord]) -> list[EventRecord]:
    return sorted(
        events,
        key=lambda event: event.scheduled_datetime() or datetime.max,
    )


def is_user_registered(event: EventRecord, user_id: int) -> bool:
    return user_id in event.attendees


def can_manage_event(user_id: int, event: EventRecord) -> bool:
    return user_id in ADMIN_IDS or user_id == event.created_by


def load_event_attendees(event: EventRecord) -> list[User]:
    if not event.attendees:
        return []
    db = Database.get()
    users: list[User] = []
    seen: set[int] = set()
    for user_id in event.attendees:
        if user_id in seen:
            continue
        doc = db.users.find_one({"tg_id": user_id})
        if doc:
            users.append(User(doc))
            seen.add(user_id)
    return sorted(
        users,
        key=lambda item: (item.get_name() or "").lower() or str(item.tg_id or ""),
    )


def normalize_tags(tags: Iterable[str]) -> list[str]:
    selected = set(tags)
    return [slug for slug in TAG_ORDER if slug in selected]


def build_contact_info(user) -> tuple[str, str]:
    name = user.full_name or user.username or "Контакт"
    url = f"https://t.me/{user.username}" if user.username else f"tg://user?id={user.id}"
    return name, url


def format_tags(tags: Iterable[str]) -> str:
    titles: list[str] = []
    for tag in tags:
        if tag == LEGACY_TAG_ALL:
            titles.append("Для всех")
        else:
            titles.append(TAG_TITLE_BY_SLUG.get(tag, tag))
    return ", ".join(titles) if titles else "Не указано"


def format_time_range(event: EventRecord) -> str:
    start_dt = event.scheduled_datetime()
    end_dt = event.end_datetime()
    if not start_dt:
        return "Дата уточняется"
    date_part = start_dt.strftime("%d.%m.%Y")
    time_part = start_dt.strftime("%H:%M")
    if end_dt:
        time_part = f"{time_part} – {end_dt.strftime('%H:%M')}"
    return f"{date_part} · {time_part}"


def update_event_record(event_id: int, updates: dict[str, Any]) -> Optional[EventRecord]:
    return events_repo().update(event_id, updates)
