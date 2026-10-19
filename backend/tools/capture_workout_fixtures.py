"""
Saves real Garmin workout JSON as test fixtures - one per sport.

The workout converter (app/services/workout_garmin.py) writes JSON for an
unofficial API. Its tests compare what it writes with what Garmin itself
stores, and these fixtures are that "what Garmin stores".

How to make them, once, on the owner's own account:
  1. In Garmin Connect (app or web), create one workout per sport you want
     to check - with a pace or power target, a repeat block, and for
     swimming a few strokes. Name each one starting with "fixture".
  2. Run:
        docker compose run --rm worker python -m tools.capture_workout_fixtures --user-id 1
  3. Commit tests/fixtures/garmin_workouts/*.json.

Only workouts whose name starts with "fixture" are read. Owner, author and
every id are removed before writing; the script refuses to write a file in
which an identity-like key survived. Read-only: nothing on Garmin changes.
"""

import argparse
import json
import re
from pathlib import Path

from sqlmodel import Session, select

from app.core.crypto import decrypt
from app.core.garmin_client import get_workout, list_workouts, resume_garmin_session
from app.db.session import engine
from app.models.garmin_account import GarminAccount

OUT_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "garmin_workouts"
DROP_KEYS = re.compile(
    r"^(owner\w*|author|workoutId|stepId|createdDate|updatedDate|uploadTimestamp|consumer\w*|"
    r"workoutProvider|workoutSourceId|trainingPlanId|shared|locale|atpPlanId|.*[Uu]serId|"
    r"displayName|fullName|userName|profileImage\w*)$"
)
IDENTITY = re.compile(r"(owner|displayName|fullName|userName|profileImage|userId|author)", re.I)


def scrub(value):
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items() if not DROP_KEYS.match(k)}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    return value


def _leaks(value) -> list[str]:
    if isinstance(value, dict):
        found = [k for k in value if IDENTITY.search(k)]
        return found + [leak for v in value.values() for leak in _leaks(v)]
    if isinstance(value, list):
        return [leak for v in value for leak in _leaks(v)]
    return []


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-id", type=int, required=True)
    args = parser.parse_args()

    with Session(engine) as session:
        account = session.exec(select(GarminAccount).where(GarminAccount.user_id == args.user_id)).one()
        garmin = resume_garmin_session(decrypt(account.encrypted_token))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    written = 0
    for summary in list_workouts(garmin):
        if not str(summary.get("workoutName", "")).lower().startswith("fixture"):
            continue
        full = scrub(get_workout(garmin, summary["workoutId"]))
        leaks = _leaks(full)
        if leaks:
            raise SystemExit(f"refusing to write {summary['workoutName']!r}: identity keys left {leaks}")
        sport = full["sportType"]["sportTypeKey"]
        full["workoutName"] = f"fixture {sport}"
        (OUT_DIR / f"{sport}.json").write_text(json.dumps(full, indent=2, ensure_ascii=False))
        print(f"wrote {sport}.json")
        written += 1
    if not written:
        print('No workouts named "fixture..." found in this Garmin account.')


if __name__ == "__main__":
    main()
