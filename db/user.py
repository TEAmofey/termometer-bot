from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from db.database import Database
from utils.users import education_course, education_status, education_stage, get_direction_track


@dataclass
class User:
    raw: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Work with own copy to avoid accidental external mutations
        self.raw = dict(self.raw)

    @property
    def tg_id(self) -> Optional[int]:
        return self.raw.get("tg_id")

    @tg_id.setter
    def tg_id(self, value: int) -> None:
        self.raw["tg_id"] = value

    def get_name(self) -> str:
        return self.raw.get("name", "")

    def get_direction(self) -> str:
        return self.raw.get("direction", "")

    def get_graduation_year(self) -> Optional[int]:
        return (self.raw.get("education") or {}).get("graduation_year")

    def get_course(self) -> Optional[int]:
        return education_course(self.raw)

    def get_education_status(self) -> Optional[str]:
        return education_status(self.raw)

    def get_education_stage(self) -> Optional[str]:
        return education_stage(self.raw)

    def get_username(self) -> str:
        return self.raw.get("username", "")

    def is_registration_complete(self) -> bool:
        return bool(
            self.get_name()
            and get_direction_track(self.get_direction())
            and get_direction_track(self.get_direction()) == self.get_education_stage()
            and self.get_education_status()
        )

    def save_to_db(self) -> Dict[str, Any]:
        if "tg_id" not in self.raw:
            raise ValueError("Cannot save user without 'tg_id'.")
        saved = Database.get().users.save(self.raw)
        self.raw = dict(saved)
        return self.raw

    @classmethod
    def get_by_tg_id(cls, tg_id: int) -> Optional["User"]:
        doc = Database.get().users.find_one({"tg_id": tg_id})
        return cls(doc) if doc else None
