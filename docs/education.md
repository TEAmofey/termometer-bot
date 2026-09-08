# Education storage

The users.data JSON stores one current education record:

- Bachelor: `education = {"stage": "bachelor", "graduation_year": 2030}`.
- Master: `education = {"stage": "master", "graduation_year": 2027}`.
- Postgraduate: `education = {"stage": "postgraduate", "graduation_year": null, "master_graduation_year": 2024}`. The existing form asks for master's completion year; it does not establish postgraduate completion year.
- Graduate with an unknown year: `education = {"stage": "master", "graduation_year": null, "graduated": true}`. Choosing “Выпускник” cannot establish an exact graduation year. A previously known graduation year is retained when re-confirming the same stage.

The `direction` field retains the selected program. Years are integers. No current course or current student/graduate status is persisted for records with a known graduation year.

The academic year starts August 1 in Europe/Moscow, matching the 2026 promotion cutoff. For academic year A and program duration D (bachelor 4, master 2), course = A + D + 1 - graduation_year. A graduation year <= A means graduate. Thus a master's graduation year of 2027 means course 2 through July 31, 2027, and graduate starting August 1, 2027. No annual database update or scheduled migration is needed.

All display and registration eligibility use utils/users.py; event audiences use the education stage and remain available to graduates of that stage. Registration inputs are unchanged, and course selection converts immediately to a graduation year. The repository removes legacy flat education fields on saving a canonical profile.

## Migration

`scripts/migrate_education_years.py` defaults to a read-only dry run. `--apply --backup /absolute/new-file.json` makes a transaction, locks users against writes, and writes original records to a restricted backup before updating them. Stop the bot before applying to avoid stale in-memory updates; deploy the matching code and restart afterwards. Re-running is a no-op.

The migration uses the explicit academic_year from the preceding 2026 promotion, or the academic year of an untouched registration since August 2026. It refuses ambiguous legacy records. Graduates produced by the 2026 promotion receive graduation_year 2026. Historical promotion_2026 metadata is kept for audit only. The old promotion script skips canonical education records.
