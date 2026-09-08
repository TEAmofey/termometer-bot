"""Replace legacy course fields with graduation years. Dry run by default."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from utils.users import (PROGRAM_YEARS, clear_legacy_education, current_academic_year,
                         education_selection, get_direction_track)


def migrate_profile(doc: dict) -> tuple[dict | None, str]:
    if "education" in doc:
        return None, "already_migrated"
    if not doc.get("direction"):
        return None, "incomplete"
    track = get_direction_track(doc.get("direction"))
    if not track:
        return None, "needs_review"
    value = str(doc.get("magistracy_graduation_year") or "")
    try:
        if track == "postgraduate":
            selection = education_selection(value, track)
        else:
            anchor = doc.get("academic_year")
            if anchor is None:
                # All pre-August students were promoted in the preceding migration.
                # Only untouched new registrations can lack an explicit anchor.
                stamp = datetime.fromisoformat(doc["registration_completed_at"])
                anchor = current_academic_year(stamp)
                if anchor < 2026:
                    return None, "needs_review"
            anchor = int(anchor)
            if doc.get("education_status") == "graduate":
                selection = education_selection("Выпускник", track, academic_year=anchor)
                promotion = doc.get("promotion_2026") or {}
                if (anchor == 2026 and promotion.get("previous_course") == str(PROGRAM_YEARS[track])):
                    selection = {"education": {"stage": track, "graduation_year": 2026}}
            else:
                selection = education_selection(value, track, academic_year=anchor)
    except (ValueError, TypeError, KeyError):
        return None, "needs_review"
    result = clear_legacy_education(doc)
    result.update(selection)
    result["updated_at"] = datetime.now(timezone.utc).isoformat()
    return result, "migrated"


def main():
    import psycopg
    from psycopg import sql
    from config import POSTGRES_CONFIG, POSTGRES_SCHEMA
    from db.database import _build_conninfo

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup", type=Path)
    args = parser.parse_args()
    if args.apply and not args.backup:
        parser.error("--apply requires --backup")
    table = sql.Identifier(POSTGRES_SCHEMA or "public", "users")
    with psycopg.connect(os.getenv("POSTGRES_DSN") or _build_conninfo(POSTGRES_CONFIG)) as conn:
        if args.apply:
            conn.execute(sql.SQL("LOCK TABLE {} IN EXCLUSIVE MODE").format(table))
        else:
            conn.execute("SET TRANSACTION READ ONLY")
        rows = conn.execute(sql.SQL("SELECT tg_id, data, created_at, updated_at FROM {}").format(table)).fetchall()
        changes, counts = [], Counter()
        for tg_id, data, created, updated in rows:
            doc = json.loads(data)
            result, reason = migrate_profile(doc)
            counts[reason] += 1
            if result:
                changes.append((tg_id, data, created, updated, result))
        print(json.dumps(dict(counts), ensure_ascii=False))
        if counts["needs_review"]:
            raise RuntimeError("Ambiguous legacy profiles; migration aborted without changes")
        if args.apply and changes:
            with args.backup.open("x", encoding="utf-8") as backup:
                os.chmod(args.backup, 0o600)
                json.dump([{"tg_id": t, "data": d, "created_at": c, "updated_at": u} for t,d,c,u,r in changes], backup, ensure_ascii=False)
                backup.flush()
                os.fsync(backup.fileno())
            for tg_id, data, created, updated, result in changes:
                conn.execute(sql.SQL("UPDATE {} SET data=%s, updated_at=%s WHERE tg_id=%s").format(table),
                             (json.dumps(result, ensure_ascii=False), result["updated_at"], tg_id))
    print("Applied" if args.apply else "Dry run; no changes")


if __name__ == "__main__":
    main()
