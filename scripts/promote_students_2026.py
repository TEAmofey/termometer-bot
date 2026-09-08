"""One-time 2026 promotion. Dry run by default; --apply requires --backup PATH."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils.users import get_direction_track

CUTOFF = datetime.fromisoformat("2026-08-01T00:00:00+03:00")
MIGRATION = "promotion_2026"


def promoted_profile(doc: dict, created_at: str) -> tuple[dict | None, str]:
    if "education" in doc:
        return None, "year_based_profile"
    if doc.get(MIGRATION) or int(doc.get("academic_year") or 0) >= 2026:
        return None, "already_current"
    track = get_direction_track(doc.get("direction"))
    if track not in ("bachelor", "master") or doc.get("education_status") == "graduate":
        return None, "not_student"
    if not doc.get("name"):
        return None, "incomplete"
    stamp = doc.get("registration_completed_at") or created_at
    try:
        registered = datetime.fromisoformat(stamp)
        if registered >= CUTOFF:
            return None, "new_registration"
    except (TypeError, ValueError):
        return None, "invalid_date"
    limit = 4 if track == "bachelor" else 2
    try:
        course = int(doc.get("magistracy_graduation_year", ""))
    except (TypeError, ValueError):
        return None, "invalid_course"
    if not 1 <= course <= limit:
        return None, "invalid_course"
    result = dict(doc)
    graduate = course == limit
    result.update(
        education_status="graduate" if graduate else "student",
        magistracy_graduation_year="" if graduate else str(course + 1),
        academic_year=2026,
    )
    now = datetime.now(timezone.utc).isoformat()
    result[MIGRATION] = {"applied_at": now, "previous_course": str(course), "cutoff": CUTOFF.isoformat()}
    result["updated_at"] = now
    return result, "graduate" if graduate else "promoted"


def main():
    import psycopg
    from psycopg import sql
    from config import POSTGRES_CONFIG, POSTGRES_SCHEMA
    from db.database import _build_conninfo

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--confirmed-profiles", type=Path, help="Profiles explicitly confirmed since August, extracted from registration logs")
    args = parser.parse_args()
    if args.apply and not args.backup:
        parser.error("--apply requires --backup")
    confirmed = json.loads(args.confirmed_profiles.read_text()) if args.confirmed_profiles else {}
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
            result, reason = promoted_profile(doc, created)
            recent = confirmed.get(str(tg_id))
            if result and recent and all(doc.get(k) == recent.get(k) for k in ("direction", "magistracy_graduation_year")):
                result = dict(doc)
                result["academic_year"] = 2026
                result["updated_at"] = datetime.now(timezone.utc).isoformat()
                result[MIGRATION] = {"action": "kept_recent_confirmation", "confirmed_at": recent["confirmed_at"]}
                reason = "kept_recent_confirmation"
            counts[reason] += 1
            if result:
                changes.append((tg_id, data, created, updated, result))
        print(json.dumps(dict(counts), ensure_ascii=False))
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
