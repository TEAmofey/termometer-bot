from __future__ import annotations

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

