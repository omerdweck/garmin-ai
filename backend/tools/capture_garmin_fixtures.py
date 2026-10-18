"""
Saves real Garmin responses for one workout as test fixtures.

Run ONCE, by the owner, against their own linked account:

    docker compose run --rm worker python -m tools.capture_garmin_fixtures --user-id 1

Why this exists: the per-workout endpoints the next phase needs (splits,
heart-rate zones, the second-by-second detail stream) are undocumented -
garminconnect is an unofficial library. Writing the analysis code against
guessed field names would break the first time it met real data. Capturing
one real response gives the code, and its tests, the actual shape.

Privacy is the part that needs care. The output goes into git, so before
anything is written:
  - the GPS polyline is never requested (maxpoly=0)
  - every latitude/longitude value is removed, including the positional
    columns inside the detail stream
  - names, profile and device identifiers are removed, and the workout
    name is replaced (Garmin names workouts after the city)
  - the real activity id is replaced with a fixed fake one
and the script refuses to write a file if a location-like key survived.

Reads the stored token like the sync does - no password is asked for.
"""

import argparse
import json
import re
from pathlib import Path

from sqlmodel import Session, col, select

from app.core.crypto import decrypt
from app.core.garmin_client import resume_garmin_session
from app.db.session import engine
from app.models.activity import Activity
from app.models.garmin_account import GarminAccount

OUT_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "garmin"
FAKE_ACTIVITY_ID = 10_000_000_001

# Deliberately whole-word-ish: a bare "lat" would also match "calculated"
# and "accumulated" and silently strip fields the analysis needs.
LOCATION_KEY = re.compile(
    r"(latitude|longitude|polyline|location|^lat$|^lon$|^lng$|Lat$|Lon$)", re.I
)
IDENTITY_KEY = re.compile(
    r"(owner|userProfile|displayName|fullName|userName|profileImage|"
    r"deviceId|unitId|serialNumber|userId)",
    re.I,
)


def _scrub(value, real_id: int):
    if isinstance(value, dict):
        return {
            key: (
                FAKE_ACTIVITY_ID if key == "activityId"
                # Garmin names workouts after the city ("Tel Aviv Running").
                else "Fixture activity" if key == "activityName"
                else _scrub(item, real_id)
            )
            for key, item in value.items()
            if not LOCATION_KEY.search(key) and not IDENTITY_KEY.search(key)
        }
    if isinstance(value, list):
        return [_scrub(item, real_id) for item in value]
    if value == real_id or value == str(real_id):
        return FAKE_ACTIVITY_ID
    return value


def _scrub_detail_stream(details: dict) -> dict:
    """
    The detail stream stores samples positionally: metricDescriptors says
    "column 4 is directLatitude", and each sample is a bare list of numbers.
    Key-based scrubbing cannot see those values, so the location columns are
    blanked by position here before the general scrub runs.
    """
    location_columns = [
        d["metricsIndex"]
        for d in details.get("metricDescriptors") or []
        if isinstance(d.get("metricsIndex"), int) and LOCATION_KEY.search(d.get("key") or "")
    ]
    for sample in details.get("activityDetailMetrics") or []:
        metrics = sample.get("metrics") or []
        for index in location_columns:
            if index < len(metrics):
                metrics[index] = None
    return details


def _assert_clean(data, path="") -> None:
    if isinstance(data, dict):
        for key, item in data.items():
            if LOCATION_KEY.search(key):
                raise RuntimeError(f"location-like key survived scrubbing: {path}.{key}")
            _assert_clean(item, f"{path}.{key}")
    elif isinstance(data, list):
        for item in data:
            _assert_clean(item, path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--user-id", type=int, required=True)
    parser.add_argument(
        "--activity-id",
        type=int,
        help="Garmin activity id; defaults to the user's most recent run",
    )
    args = parser.parse_args()

    with Session(engine) as session:
        account = session.exec(
            select(GarminAccount).where(GarminAccount.user_id == args.user_id)
        ).first()
        if account is None:
            raise SystemExit(f"user {args.user_id} has no linked Garmin account")

        activity_id = args.activity_id
        if activity_id is None:
            latest_run = session.exec(
                select(Activity)
                .where(
                    Activity.user_id == args.user_id,
                    Activity.activity_type == "running",
                    Activity.garmin_activity_id.is_not(None),
                )
                .order_by(col(Activity.start_time).desc())
            ).first()
            if latest_run is None:
                raise SystemExit("no synced run found - pass --activity-id")
            activity_id = latest_run.garmin_activity_id

        garmin = resume_garmin_session(decrypt(account.encrypted_token))

    responses = {
        "activity_summary": garmin.get_activity(activity_id),
        "activity_splits": garmin.get_activity_splits(activity_id),
        "activity_split_summaries": garmin.get_activity_split_summaries(activity_id),
        "activity_hr_zones": garmin.get_activity_hr_in_timezones(activity_id),
        # maxpoly=0: the GPS track is never even downloaded.
        "activity_details": _scrub_detail_stream(
            garmin.get_activity_details(activity_id, maxchart=2000, maxpoly=0)
        ),
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, raw in responses.items():
        clean = _scrub(raw, activity_id)
        _assert_clean(clean)
        target = OUT_DIR / f"{name}.json"
        target.write_text(json.dumps(clean, ensure_ascii=False, indent=2))
        top = list(clean)[:12] if isinstance(clean, dict) else f"list[{len(clean)}]"
        print(f"saved {target.relative_to(OUT_DIR.parent.parent)}  keys: {top}")

    print("\nOpen the files and look before committing them - the scrub is a")
    print("safety net, not a guarantee that nothing personal is left.")


if __name__ == "__main__":
    main()
