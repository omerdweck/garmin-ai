"""One valid workout per sport, as the coach would send it to build_workout."""

EXAMPLES = {
    "running": {
        "sport": "running", "name": "5x1K", "steps": [
            {"kind": "warmup", "end": "time", "seconds": 600, "target": {"type": "heart_rate_zone", "zone": 2}},
            {"kind": "repeat", "times": 5, "steps": [
                {"kind": "interval", "end": "distance", "meters": 1000, "target": {"type": "pace", "low": 270, "high": 285}},
                {"kind": "recovery", "end": "time", "seconds": 120},
            ]},
            {"kind": "cooldown", "end": "lap_button"},
        ],
    },
    "walking": {"sport": "walking", "name": "Brisk walk", "steps": [
        {"kind": "interval", "end": "time", "seconds": 2400, "target": {"type": "pace", "low": 540, "high": 600}},
    ]},
    "hiking": {"sport": "hiking", "name": "Hill hike", "steps": [
        {"kind": "interval", "end": "distance", "meters": 8000, "target": {"type": "heart_rate", "low": 120, "high": 140}},
    ]},
    "cycling": {
        "sport": "cycling", "name": "Sweet spot 3x10", "steps": [
            {"kind": "warmup", "end": "time", "seconds": 600, "target": {"type": "power_ftp_pct", "low": 50, "high": 75}},
            {"kind": "repeat", "times": 3, "steps": [
                {"kind": "interval", "end": "time", "seconds": 600, "target": {"type": "power_ftp_pct", "low": 88, "high": 94}},
                {"kind": "recovery", "end": "time", "seconds": 300, "target": {"type": "power_ftp_pct", "low": 50, "high": 60}},
            ]},
            {"kind": "cooldown", "end": "time", "seconds": 600, "target": {"type": "power_ftp_pct", "low": 40, "high": 60}},
        ],
    },
    "swimming": {
        "sport": "swimming", "name": "Swim 1500", "pool_length_m": 25, "steps": [
            {"kind": "warmup", "end": "distance", "meters": 300, "stroke": "free"},
            {"kind": "repeat", "times": 8, "steps": [
                {"kind": "interval", "end": "distance", "meters": 100, "stroke": "free"},
                {"kind": "rest", "end": "time", "seconds": 20},
            ]},
            {"kind": "interval", "end": "distance", "meters": 200, "stroke": "back"},
            {"kind": "cooldown", "end": "distance", "meters": 200, "stroke": "any"},
        ],
    },
    "strength": {
        "sport": "strength", "name": "Legs", "steps": [
            {"kind": "warmup", "end": "time", "seconds": 300},
            {"kind": "exercise", "exercise": "Barbell Back Squat", "sets": 4, "reps": 6, "weight_kg": 80, "rest_seconds": 120},
            {"kind": "exercise", "exercise": "Plank", "sets": 3, "seconds": 45, "rest_seconds": 30},
        ],
    },
    "hiit": {"sport": "hiit", "name": "Tabata", "steps": [
        {"kind": "repeat", "times": 8, "steps": [
            {"kind": "interval", "end": "time", "seconds": 20},
            {"kind": "rest", "end": "time", "seconds": 10},
        ]},
    ]},
    "yoga": {"sport": "yoga", "name": "Flow", "steps": [{"kind": "other", "end": "time", "seconds": 1800}]},
    "mobility": {"sport": "mobility", "name": "Hips", "steps": [{"kind": "other", "end": "time", "seconds": 900}]},
    "cardio": {"sport": "cardio", "name": "Elliptical", "steps": [
        {"kind": "interval", "end": "time", "seconds": 1800, "target": {"type": "heart_rate_zone", "zone": 2}},
    ]},
}
