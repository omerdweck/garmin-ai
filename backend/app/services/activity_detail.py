"""
One workout second by second, turned into what the Mini App's workout page
draws. Pure functions: Garmin's three responses in, a compact dict out -
stored once in Activity.detail and never recomputed.

What comes out:
    points      the charts: time, distance, heart rate, speed, elevation,
                cadence, power, lat/lon - downsampled to MAX_POINTS
    track       the GPS line for the map, with everything within
                PRIVACY_RADIUS_M of the start and of the end removed
    splits      per km (per 5 km cycling), computed from the stream itself
                so it doesn't depend on the watch's lap settings
    laps        the watch's own laps
    lengths     pool swims: every length, with stroke, strokes and SWOLF
    zones       seconds in each heart-rate zone
    drift       cardiac drift, %: how much more heart rate the same speed
                cost in the second half than in the first

Garmin's field names are read defensively - a missing metric (no power
meter, no GPS indoors, an older watch) leaves that series empty, never a
substitute value.
"""

import math
from typing import Optional

MAX_POINTS = 600
MAX_TRACK = 800
PRIVACY_RADIUS_M = 200
SPLIT_METERS = {"cycling": 5000}
DEFAULT_SPLIT_METERS = 1000
# Splits make sense for sports measured in distance.
SPLIT_SPORTS = {"running", "walking", "cycling"}
MIN_DRIFT_SECONDS = 20 * 60
MOVING_SPEED = 0.5  # m/s; slower is standing still


def _column(rows: list, index: dict, *keys: str) -> list:
    for key in keys:
        if key in index:
            i = index[key]
            return [row[i] if row and i < len(row) else None for row in rows]
    return []


def _haversine(a: tuple, b: tuple) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6_371_000 * math.asin(math.sqrt(h))


def _private(point: tuple, start: tuple, end: tuple) -> bool:
    return _haversine(point, start) < PRIVACY_RADIUS_M or _haversine(point, end) < PRIVACY_RADIUS_M


def _stride(n: int, limit: int) -> list[int]:
    """Indexes keeping at most `limit` evenly spaced items, always the last."""
    if n <= limit:
        return list(range(n))
    step = n / limit
    idx = sorted({int(i * step) for i in range(limit)} | {n - 1})
    return idx


def _r(value, digits=1):
    return None if value is None else round(float(value), digits)


def _track(streams: dict, lat: list, lon: list) -> list:
    poly = ((streams.get("geoPolylineDTO") or {}).get("polyline")) or []
    points = [(p.get("lat"), p.get("lon")) for p in poly if p.get("lat") is not None and p.get("lon") is not None]
    if not points:
        points = [(a, b) for a, b in zip(lat, lon) if a is not None and b is not None]
    if len(points) < 2:
        return []
    start, end = points[0], points[-1]
    kept = [p for p in points if not _private(p, start, end)]
    return [[round(a, 5), round(b, 5)] for a, b in (kept[i] for i in _stride(len(kept), MAX_TRACK))]


def _splits(t: list, d: list, hr: list, elev: list, sport: str) -> list:
    if sport not in SPLIT_SPORTS:
        return []
    unit = SPLIT_METERS.get(sport, DEFAULT_SPLIT_METERS)
    samples = [(ti, di, hi, ei) for ti, di, hi, ei in zip(t, d, hr or [None] * len(t), elev or [None] * len(t))
               if ti is not None and di is not None]
    if len(samples) < 2:
        return []

    splits, start_i, mark = [], 0, unit
    total = samples[-1][1]

    def close(end_i: int, meters: float):
        seg = samples[start_i:end_i + 1]
        seconds = seg[-1][0] - seg[0][0]
        hrs = [s[2] for s in seg if s[2]]
        gain = sum(max(0.0, b[3] - a[3]) for a, b in zip(seg, seg[1:]) if a[3] is not None and b[3] is not None)
        if seconds <= 0 or meters <= 0:
            return
        splits.append({
            "index": len(splits) + 1,
            "meters": round(meters),
            "seconds": round(seconds),
            # Per km (or per 5 km) even for a short last split, so bars compare.
            "pace": round(seconds / meters * unit),
            "avg_hr": round(sum(hrs) / len(hrs)) if hrs else None,
            "gain": round(gain),
        })

    for i, (_, di, _, _) in enumerate(samples):
        if di >= mark:
            close(i, unit)
            start_i, mark = i, mark + unit
    rest = total - (mark - unit)
    if rest >= 0.1 * unit:
        close(len(samples) - 1, rest)
    return splits


def _drift(t: list, hr: list, speed: list) -> Optional[float]:
    if not t or not hr or not speed or (t[-1] or 0) - (t[0] or 0) < MIN_DRIFT_SECONDS:
        return None
    moving = [(ti, h, s) for ti, h, s in zip(t, hr, speed) if ti is not None and h and s and s > MOVING_SPEED]
    if len(moving) < 20:
        return None
    half = (moving[0][0] + moving[-1][0]) / 2
    first = [m for m in moving if m[0] < half]
    second = [m for m in moving if m[0] >= half]
    if not first or not second:
        return None

    def cost(part):  # heart beats per metre-per-second
        return (sum(m[1] for m in part) / len(part)) / (sum(m[2] for m in part) / len(part))

    return round((cost(second) / cost(first) - 1) * 100, 1)


def _laps(laps: dict) -> tuple[list, list]:
    out, lengths = [], []
    for lap in (laps or {}).get("lapDTOs") or []:
        out.append({
            "index": lap.get("lapIndex"),
            "meters": _r(lap.get("distance"), 0),
            "seconds": _r(lap.get("movingDuration") or lap.get("duration"), 0),
            "avg_hr": _r(lap.get("averageHR"), 0),
            "avg_speed": _r(lap.get("averageSpeed"), 3),
            "gain": _r(lap.get("elevationGain"), 0),
        })
        for length in lap.get("lengthDTOs") or []:
            if not length.get("duration"):
                continue  # a rest inside the set, not a length
            lengths.append({
                "index": len(lengths) + 1,
                "seconds": _r(length.get("duration"), 1),
                "meters": _r(length.get("distance"), 0),
                "stroke": (length.get("swimStroke") or lap.get("swimStroke") or "").lower() or None,
                "strokes": length.get("totalNumberOfStrokes"),
                "swolf": length.get("averageSwolf"),
            })
    return out, lengths


def _zones(zones) -> list:
    return [
        {"zone": z.get("zoneNumber"), "seconds": round(z.get("secsInZone") or 0), "low": z.get("zoneLowBoundary")}
        for z in (zones or []) if isinstance(z, dict) and z.get("zoneNumber")
    ]


def build_detail(streams: dict, laps: dict, zones, sport: str) -> dict:
    streams = streams or {}
    index = {d.get("key"): d.get("metricsIndex") for d in streams.get("metricDescriptors") or []}
    rows = [m.get("metrics") for m in streams.get("activityDetailMetrics") or []]

    t = _column(rows, index, "sumDuration", "sumElapsedDuration")
    if not t:
        stamps = _column(rows, index, "directTimestamp")
        t = [(s - stamps[0]) / 1000 if s is not None and stamps[0] is not None else None for s in stamps]
    d = _column(rows, index, "sumDistance")
    hr = _column(rows, index, "directHeartRate")
    speed = _column(rows, index, "directSpeed")
    elev = _column(rows, index, "directElevation")
    cad = _column(rows, index, "directRunCadence", "directDoubleCadence", "directBikeCadence")
    power = _column(rows, index, "directPower")
    lat = _column(rows, index, "directLatitude")
    lon = _column(rows, index, "directLongitude")

    # The map line is cut from the raw coordinates: blanking first would
    # move "the start" inward and cut a second circle.
    track = _track(streams, lat, lon)

    # Lat/lon of the chart points too, so touching a chart can move a dot on
    # the map - but blanked inside the private circles, like the track.
    coords = [(a, b) for a, b in zip(lat, lon) if a is not None and b is not None]
    if len(coords) >= 2:
        start, end = coords[0], coords[-1]
        lat, lon = zip(*[
            (a, b) if a is not None and b is not None and not _private((a, b), start, end) else (None, None)
            for a, b in zip(lat, lon)
        ])

    keep = _stride(len(rows), MAX_POINTS)

    def pick(series, digits=1):
        return [_r(series[i], digits) for i in keep] if series else []

    lap_list, lengths = _laps(laps)
    return {
        "version": 1,
        "points": {
            "t": pick(t, 0), "d": pick(d, 0), "hr": pick(hr, 0), "speed": pick(speed, 3),
            "elev": pick(elev, 1), "cad": pick(cad, 0), "power": pick(power, 0),
            "lat": pick(list(lat), 5), "lon": pick(list(lon), 5),
        },
        "track": track,
        "splits": _splits(t, d, hr, elev, sport),
        "split_meters": SPLIT_METERS.get(sport, DEFAULT_SPLIT_METERS),
        "laps": lap_list,
        "lengths": lengths,
        "zones": _zones(zones),
        "drift": _drift(t, hr, speed) if sport in {"running", "cycling", "walking"} else None,
    }
