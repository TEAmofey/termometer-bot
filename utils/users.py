from __future__ import annotations

from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo
from typing import Optional

from constants import BACHELOR_DIRECTIONS, MASTER_DIRECTIONS, POSTGRADUATE_DIRECTION


def get_direction_track(direction: Optional[str]) -> Optional[str]:
    if not direction:
        return None
    normalized = direction.strip()
    if normalized in BACHELOR_DIRECTIONS:
        return "bachelor"
    if normalized in MASTER_DIRECTIONS:
        return "master"
    if normalized == POSTGRADUATE_DIRECTION:
        return "postgraduate"
    return None


PROGRAM_YEARS = {"bachelor": 4, "master": 2}
LEGACY_EDUCATION_FIELDS = ("magistracy_graduation_year", "education_status", "academic_year", "direction_track")


def current_academic_year(now: datetime | None = None) -> int:
    """Academic year starts on August 1, in Moscow time."""
    now = now or datetime.now(ZoneInfo("Europe/Moscow"))
    now = now.astimezone(ZoneInfo("Europe/Moscow"))
    return now.year if now.month >= 8 else now.year - 1


def education_stage(data: dict) -> str | None:
    return (data.get("education") or {}).get("stage") or get_direction_track(data.get("direction"))


def education_course(data: dict, *, academic_year: int | None = None) -> int | None:
    education = data.get("education") or {}
    duration = PROGRAM_YEARS.get(education.get("stage"))
    year = education.get("graduation_year")
    if not duration or not isinstance(year, int):
        return None
    current = current_academic_year() if academic_year is None else academic_year
    course = current + duration + 1 - year
    return course if 1 <= course <= duration else None


def education_status(data: dict, *, academic_year: int | None = None) -> str | None:
    education = data.get("education") or {}
    stage = education.get("stage")
    if stage == "postgraduate":
        return "postgraduate" if education.get("master_graduation_year") else None
    if stage not in PROGRAM_YEARS:
        return None
    year = education.get("graduation_year")
    current = current_academic_year() if academic_year is None else academic_year
    if isinstance(year, int):
        if year <= current:
            return "graduate"
        if education_course(data, academic_year=current) is not None:
            return "student"
        return None
    # The unchanged registration form allows graduates to omit their year.
    return "graduate" if education.get("graduated") else None


def education_lines(data: dict) -> list[str]:
    direction = data.get("direction") or "Не указано"
    lines = [f"🎯 <b>Направление:</b> {escape(direction)}"]
    education = data.get("education") or {}
    if education_status(data) == "graduate":
        lines.append("🎓 <b>Статус:</b> Выпускник")
    elif education_stage(data) == "postgraduate":
        value = education.get("master_graduation_year") or "Не указан"
        lines.append(f"📅 <b>Год окончания магистратуры:</b> {escape(str(value))}")
    else:
        value = education_course(data) or "Не указан"
        lines.append(f"🎓 <b>Курс:</b> {escape(str(value))}")
    return lines


def education_selection(value: str, track: str, *, academic_year: int | None = None) -> dict:
    current = current_academic_year() if academic_year is None else academic_year
    education = {"stage": track, "graduation_year": None}
    if track == "postgraduate":
        year = int(value)
        if not 2000 <= year <= 2100:
            raise ValueError("Invalid master's graduation year")
        education["master_graduation_year"] = year
    elif track in PROGRAM_YEARS:
        if value == "Выпускник":
            education["graduated"] = True
        else:
            course = int(value)
            if not 1 <= course <= PROGRAM_YEARS[track]:
                raise ValueError("Invalid course")
            education["graduation_year"] = current + PROGRAM_YEARS[track] + 1 - course
    else:
        raise ValueError("Unknown education stage")
    return {"education": education}


def clear_legacy_education(data: dict) -> dict:
    result = dict(data)
    for key in LEGACY_EDUCATION_FIELDS:
        result.pop(key, None)
    return result
