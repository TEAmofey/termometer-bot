from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Iterable, Mapping, Optional

if TYPE_CHECKING:
    from db.database import Database


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"


@dataclass
class EventRecord:
    id: Optional[int] = None
    title: str = ""
    starts_at: str = ""
    ends_at: str = ""
    location: str = ""
    short_description: str = ""
    contact: str = ""
    contact_name: str = ""
    contact_url: str = ""
    registration_link: str = ""
    attendees: list[int] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    status: str = STATUS_PENDING
    created_by: Optional[int] = None
    creator_name: str = ""
    creator_username: str = ""
    created_at: str = ""
    updated_at: str = ""
    approved_by: Optional[int] = None
    approved_at: Optional[str] = None
    moderator_note: Optional[str] = None
    moderation_messages: list[dict[str, int]] = field(default_factory=list)
    scheduled_at: str = ""

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EventRecord":
        tags_raw = data.get("tags") or []
        tags = (
            list(tags_raw)
            if isinstance(tags_raw, Iterable)
            and not isinstance(tags_raw, (str, bytes))
            else [tags_raw]
            if tags_raw
            else []
        )
        moderation_messages_raw = data.get("moderation_messages") or []
        moderation_messages: list[dict[str, int]] = []
        for item in moderation_messages_raw:
            if isinstance(item, Mapping):
                chat_id = item.get("chat_id")
                message_id = item.get("message_id")
                if isinstance(chat_id, int) and isinstance(message_id, int):
                    moderation_messages.append(
                        {"chat_id": chat_id, "message_id": message_id}
                    )

        starts_at = data.get("starts_at") or data.get("scheduled_at", "")
        scheduled_at = data.get("scheduled_at", starts_at)
        ends_at = data.get("ends_at", "")
        contact_name = data.get("contact_name", "")
        contact_url = data.get("contact_url", "")
        contact = data.get("contact", "")
        if not contact and contact_name and contact_url:
            contact = f"{contact_name} ({contact_url})"
        attendees_raw = data.get("attendees") or []
        attendees: list[int] = []
        for value in attendees_raw:
            try:
                attendees.append(int(value))
            except (TypeError, ValueError):
                continue
        return cls(
            id=data.get("id"),
            title=data.get("title", ""),
            starts_at=starts_at,
            ends_at=ends_at,
            location=data.get("location", ""),
            short_description=data.get("short_description", ""),
            contact=contact,
            contact_name=contact_name,
            contact_url=contact_url,
            registration_link=data.get("registration_link", ""),
            attendees=attendees,
            tags=tags,
            status=data.get("status", STATUS_PENDING),
            created_by=data.get("created_by"),
            creator_name=data.get("creator_name", ""),
            creator_username=data.get("creator_username", ""),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            approved_by=data.get("approved_by"),
            approved_at=data.get("approved_at"),
            moderator_note=data.get("moderator_note"),
            moderation_messages=moderation_messages,
            scheduled_at=scheduled_at,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "starts_at": self.starts_at,
            "ends_at": self.ends_at,
            "location": self.location,
            "short_description": self.short_description,
            "contact": self.contact,
            "contact_name": self.contact_name,
            "contact_url": self.contact_url,
            "registration_link": self.registration_link,
            "attendees": [int(value) for value in self.attendees],
            "tags": list(self.tags),
            "status": self.status,
            "created_by": self.created_by,
            "creator_name": self.creator_name,
            "creator_username": self.creator_username,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "approved_by": self.approved_by,
            "approved_at": self.approved_at,
            "moderator_note": self.moderator_note,
            "moderation_messages": list(self.moderation_messages),
            "scheduled_at": self.starts_at or self.scheduled_at,
        }

    def scheduled_datetime(self) -> Optional[datetime]:
        date_str = self.starts_at or self.scheduled_at
        if not date_str:
            return None
        try:
            return datetime.fromisoformat(date_str)
        except ValueError:
            return None

    def end_datetime(self) -> Optional[datetime]:
        if not self.ends_at:
            return None
        try:
            return datetime.fromisoformat(self.ends_at)
        except ValueError:
            return None


class EventsRepository:
    def __init__(self, database: "Database"):
        self._db = database

    def ensure_table(self) -> None:
        with self._db.connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id BIGSERIAL PRIMARY KEY,
                    data TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    @staticmethod
    def _serialize(doc: Mapping[str, Any]) -> str:
        return json.dumps(doc, ensure_ascii=False)

    @staticmethod
    def _deserialize(payload: str) -> dict[str, Any]:
        return json.loads(payload)

    def _prepare_payload(self, data: Mapping[str, Any], *, is_new: bool) -> dict[str, Any]:
        payload = dict(data)
        now = _utcnow_iso()
        if is_new:
            payload.setdefault("created_at", now)
        payload["updated_at"] = now
        tags = payload.get("tags") or []
        if isinstance(tags, str):
            payload["tags"] = [tags]
        elif isinstance(tags, Iterable):
            payload["tags"] = [tag for tag in tags if isinstance(tag, str)]
        else:
            payload["tags"] = []
        for key in (
            "starts_at",
            "ends_at",
            "contact",
            "contact_name",
            "contact_url",
            "registration_link",
        ):
            value = payload.get(key)
            if value is None:
                payload[key] = ""
        attendees_raw = payload.get("attendees") or []
        attendees: list[int] = []
        for value in attendees_raw:
            try:
                attendees.append(int(value))
            except (TypeError, ValueError):
                continue
        payload["attendees"] = attendees
        if not payload.get("scheduled_at"):
            payload["scheduled_at"] = payload.get("starts_at", "")
        return payload

    def insert(self, event: EventRecord | Mapping[str, Any]) -> EventRecord:
        base_payload = event.to_dict() if isinstance(event, EventRecord) else dict(event)
        prepared = self._prepare_payload(base_payload, is_new=True)
        serialized = self._serialize(prepared)
        with self._db.connection() as conn:
            cursor = conn.execute(
                "INSERT INTO events (data, created_at, updated_at) VALUES (%s, %s, %s) RETURNING id",
                (serialized, prepared["created_at"], prepared["updated_at"]),
            )
            row = cursor.fetchone()
            event_id = int(row["id"]) if row and "id" in row else None
            if event_id is None:
                raise RuntimeError("Failed to obtain event id from PostgreSQL.")
            prepared["id"] = event_id
            updated_payload = self._serialize(prepared)
            conn.execute(
                "UPDATE events SET data = %s, updated_at = %s WHERE id = %s",
                (updated_payload, prepared["updated_at"], event_id),
            )
        return EventRecord.from_dict(prepared)

    def update(self, event_id: int, updates: Mapping[str, Any]) -> Optional[EventRecord]:
        existing = self.get(event_id)
        if not existing:
            return None
        current_payload = existing.to_dict()
        current_payload.update(updates)
        if "id" not in current_payload:
            current_payload["id"] = event_id
        prepared = self._prepare_payload(current_payload, is_new=False)
        serialized = self._serialize(prepared)
        with self._db.connection() as conn:
            conn.execute(
                "UPDATE events SET data = %s, updated_at = %s WHERE id = %s",
                (serialized, prepared["updated_at"], event_id),
            )
        return EventRecord.from_dict(prepared)

    def get(self, event_id: int) -> Optional[EventRecord]:
        with self._db.connection() as conn:
            cursor = conn.execute(
                "SELECT data FROM events WHERE id = %s",
                (event_id,),
            )
            row = cursor.fetchone()
        if not row:
            return None
        payload = self._deserialize(row["data"] if isinstance(row, Mapping) else row[0])
        payload.setdefault("id", event_id)
        return EventRecord.from_dict(payload)

    def list_all(self) -> list[EventRecord]:
        with self._db.connection() as conn:
            cursor = conn.execute("SELECT id, data FROM events ORDER BY id DESC")
            rows = cursor.fetchall()
        events: list[EventRecord] = []
        for row in rows:
            payload = self._deserialize(row["data"] if isinstance(row, Mapping) else row[1])
            payload.setdefault("id", row["id"] if isinstance(row, Mapping) else row[0])
            events.append(EventRecord.from_dict(payload))
        return events


class FormattingOptions:  # Legacy compatibility stub
    def __init__(self, *args: Any, **kwargs: Any):
        raise NotImplementedError("FormattingOptions is not available in the new event system.")


def format_event_message(*args: Any, **kwargs: Any) -> str:
    raise NotImplementedError("format_event_message is not available in the new event system.")
