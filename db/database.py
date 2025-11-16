from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Dict, List, Mapping, Optional

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row

try:
    from config import POSTGRES_CONFIG, POSTGRES_SCHEMA
except ImportError:  # pragma: no cover - config is always present in production
    POSTGRES_CONFIG: Dict[str, Any] = {}
    POSTGRES_SCHEMA = "public"

from db.base_event import EventsRepository


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_settings(settings: Mapping[str, Any]) -> Dict[str, Any]:
    normalized: Dict[str, Any] = {}
    for key, value in settings.items():
        if value in (None, ""):
            continue
        actual_key = key
        if key == "database":
            actual_key = "dbname"
        elif key == "ssl":
            actual_key = "sslmode"
        if actual_key == "host" and isinstance(value, (list, tuple)):
            value = ",".join(str(item) for item in value)
        normalized[actual_key] = value
    return normalized


def _build_conninfo(settings: Mapping[str, Any]) -> str:
    filtered = _normalize_settings(settings)
    if not filtered:
        return ""
    return make_conninfo(**filtered)


class UsersRepository:
    def __init__(self, database: "Database"):
        self._db = database

    def ensure_table(self) -> None:
        with self._db.connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id BIGSERIAL PRIMARY KEY,
                    tg_id BIGINT NOT NULL UNIQUE,
                    data TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    @staticmethod
    def _ensure_created_updated(doc: Dict[str, Any]) -> Dict[str, Any]:
        updated = doc.copy()
        now = _utcnow_iso()
        if "created_at" not in updated:
            updated["created_at"] = now
        updated["updated_at"] = now
        return updated

    @staticmethod
    def _serialize(doc: Dict[str, Any]) -> str:
        return json.dumps(doc, ensure_ascii=False)

    @staticmethod
    def _deserialize(payload: str) -> Dict[str, Any]:
        return json.loads(payload)

    def _load_all(self) -> List[Dict[str, Any]]:
        with self._db.connection() as conn:
            cursor = conn.execute("SELECT data FROM users")
            rows = cursor.fetchall()
        return [self._deserialize(row["data"] if isinstance(row, Mapping) else row[0]) for row in rows]

    def _matches_condition(self, doc: Mapping[str, Any], condition: Mapping[str, Any]) -> bool:
        if not condition:
            return True
        for key, value in condition.items():
            if key == "$or":
                if not isinstance(value, (list, tuple)):
                    return False
                return any(self._matches_condition(doc, clause) for clause in value)
            if isinstance(value, Mapping):
                for operator, operand in value.items():
                    if operator == "$ne":
                        if doc.get(key) == operand:
                            return False
                    else:
                        return False
            else:
                if doc.get(key) != value:
                    return False
        return True

    def find(self, condition: Optional[Mapping[str, Any]] = None) -> List[Dict[str, Any]]:
        docs = self._load_all()
        if not condition:
            return docs
        return [doc for doc in docs if self._matches_condition(doc, condition)]

    def find_one(self, condition: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
        if not condition:
            raise ValueError("find_one requires a condition.")
        docs = self.find(condition)
        return docs[0] if docs else None

    def save(self, doc: Dict[str, Any]) -> Dict[str, Any]:
        if "tg_id" not in doc:
            raise ValueError("User document must contain 'tg_id'.")

        prepared = self._ensure_created_updated(doc)
        tg_id = prepared["tg_id"]
        payload = self._serialize(prepared)

        with self._db.connection() as conn:
            conn.execute(
                """
                INSERT INTO users (tg_id, data, created_at, updated_at)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (tg_id) DO UPDATE
                SET data = EXCLUDED.data,
                    updated_at = EXCLUDED.updated_at
                """,
                (tg_id, payload, prepared["created_at"], prepared["updated_at"]),
            )
        return prepared

    def update_one(
        self,
        condition: Mapping[str, Any],
        update_document: Mapping[str, Any],
        *,
        upsert: bool = False,
    ) -> Optional[Dict[str, Any]]:
        existing = self.find_one(condition)
        set_payload = update_document.get("$set", {}) if isinstance(update_document, Mapping) else {}

        if existing:
            existing.update(set_payload)
            return self.save(existing)

        if upsert:
            base: Dict[str, Any] = {}
            for key, value in condition.items():
                if not key.startswith("$"):
                    base[key] = value
            base.update(set_payload)
            return self.save(base)

        return None


class Database:
    _instance: Optional["Database"] = None
    _lock: Lock = Lock()

    def __init__(self, conninfo: Optional[str] = None):
        env_conninfo = os.getenv("POSTGRES_DSN")
        config_conninfo = _build_conninfo(POSTGRES_CONFIG)
        self._conninfo = conninfo or env_conninfo or config_conninfo
        self._schema = (POSTGRES_SCHEMA or "public").strip() or "public"
        if not self._conninfo:
            raise RuntimeError(
                "PostgreSQL connection info is not configured. "
                "Set POSTGRES_DSN env var or fill POSTGRES_CONFIG in config.py"
            )

        self._users_repo = UsersRepository(self)
        self.events = EventsRepository(self)
        self.lectures = _ReadOnlyEmptyCollection()
        self.courses = _ReadOnlyEmptyCollection()
        self.meetups = _ReadOnlyEmptyCollection()
        self._ensure_schema_exists()
        self._ensure_schema()

    @contextmanager
    def connection(self):
        conn = psycopg.connect(self._conninfo, row_factory=dict_row)
        try:
            if self._schema:
                conn.execute(
                    sql.SQL("SET search_path TO {}, public").format(
                        sql.Identifier(self._schema)
                    )
                )
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _ensure_schema_exists(self) -> None:
        if not self._schema or self._schema == "public":
            return
        with psycopg.connect(self._conninfo) as conn:
            conn.execute(
                sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(
                    sql.Identifier(self._schema)
                )
            )
            conn.commit()

    def _ensure_schema(self) -> None:
        self._users_repo.ensure_table()
        self.events.ensure_table()

    @property
    def users(self) -> UsersRepository:
        return self._users_repo

    @classmethod
    def get(cls) -> "Database":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def close(self) -> None:
        Database._instance = None


class _ReadOnlyEmptyCollection:
    """Fallback collection to satisfy existing code paths that expect Mongo-like objects."""

    def find(self, *args: Any, **kwargs: Any) -> list[Any]:
        return []
