"""
Workout files for indoor trainers, written here - no Garmin involved.

    .zwo   Zwift          power as a fraction of FTP
    .mrc   most trainers  power as percent of FTP
    .erg   most trainers  power in watts (needs the athlete's FTP for %FTP targets)

Cycling only, and only time-based steps: a trainer in ERG mode holds a
power for a duration; "1 km at 200 W" means nothing to it. (.fit, which the
watch reads, comes from Garmin itself - see services/workouts.py.)
"""

from dataclasses import dataclass
from typing import Optional
from xml.sax.saxutils import escape, quoteattr

from app.services.workout_schema import Repeat, Step, Workout


class FileError(ValueError):
    """This workout cannot be written in that format; the message says why, in Hebrew."""


@dataclass(frozen=True)
class Segment:
    seconds: int
    start_pct: Optional[float]  # None = no power target (free ride)
    end_pct: Optional[float]
    kind: str
    note: Optional[str] = None


def _pct(step: Step, ftp: Optional[int]) -> tuple[Optional[float], Optional[float]]:
    t = step.target
    if t.type == "power_ftp_pct":
        return t.low, t.high
    if t.type == "power":
        if not ftp:
            raise FileError("יש יעדי הספק בוואטים, אבל אין FTP בפרופיל. אפשר להוסיף אותו ב-/profile.")
        return t.low / ftp * 100, t.high / ftp * 100
    return None, None


def _segment(step: Step, ftp: Optional[int]) -> Segment:
    if step.end != "time":
        raise FileError("לקובץ טריינר כל שלב צריך להיות לפי זמן, לא לפי מרחק או כפתור הקפה.")
    low, high = _pct(step, ftp)
    if low is None:
        return Segment(step.seconds, None, None, step.kind, step.note)
    if step.kind == "warmup":
        return Segment(step.seconds, low, high, step.kind, step.note)
    if step.kind == "cooldown":
        return Segment(step.seconds, high, low, step.kind, step.note)
    middle = (low + high) / 2
    return Segment(step.seconds, middle, middle, step.kind, step.note)


def _check_cycling(workout: Workout) -> None:
    if workout.sport != "cycling":
        raise FileError("קבצי טריינר (.zwo / .erg / .mrc) הם לרכיבה בלבד.")


def segments(workout: Workout, ftp: Optional[int]) -> list[Segment]:
    _check_cycling(workout)
    result = []
    for item in workout.steps:
        if isinstance(item, Repeat):
            for _ in range(item.times):
                result += [_segment(s, ftp) for s in item.steps]
        else:
            result.append(_segment(item, ftp))
    return result


# --- .zwo ------------------------------------------------------------------

def _fraction(pct: float) -> str:
    return f"{pct / 100:.3f}"


def _text_event(note: Optional[str]) -> str:
    return f'<textevent timeoffset="0" message={quoteattr(note)}/>' if note else ""


def _zwo_element(seg: Segment) -> str:
    inner = _text_event(seg.note)
    if seg.start_pct is None:
        return f'<FreeRide Duration="{seg.seconds}">{inner}</FreeRide>'
    if seg.kind in ("warmup", "cooldown") and seg.start_pct != seg.end_pct:
        tag = "Warmup" if seg.kind == "warmup" else "Cooldown"
        return (
            f'<{tag} Duration="{seg.seconds}" PowerLow="{_fraction(seg.start_pct)}" '
            f'PowerHigh="{_fraction(seg.end_pct)}">{inner}</{tag}>'
        )
    return f'<SteadyState Duration="{seg.seconds}" Power="{_fraction(seg.start_pct)}">{inner}</SteadyState>'


def to_zwo(workout: Workout, ftp: Optional[int] = None) -> str:
    _check_cycling(workout)
    body = []
    for item in workout.steps:
        if isinstance(item, Repeat):
            parts = [_segment(s, ftp) for s in item.steps]
            if len(parts) == 2 and all(p.start_pct is not None for p in parts):
                on, off = parts
                body.append(
                    f'<IntervalsT Repeat="{item.times}" OnDuration="{on.seconds}" OffDuration="{off.seconds}" '
                    f'OnPower="{_fraction(on.start_pct)}" OffPower="{_fraction(off.start_pct)}"/>'
                )
            else:
                body += [_zwo_element(p) for _ in range(item.times) for p in parts]
        else:
            body.append(_zwo_element(_segment(item, ftp)))

    steps = "\n        ".join(body)
    return (
        "<workout_file>\n"
        "    <author>Garmin AI Coach</author>\n"
        f"    <name>{escape(workout.name)}</name>\n"
        f"    <description>{escape(workout.description or '')}</description>\n"
        "    <sportType>bike</sportType>\n"
        "    <tags/>\n"
        "    <workout>\n"
        f"        {steps}\n"
        "    </workout>\n"
        "</workout_file>\n"
    )


# --- .erg / .mrc ----------------------------------------------------------------

def _points(workout: Workout, ftp: Optional[int]) -> list[tuple[float, float]]:
    points, minute = [], 0.0
    for seg in segments(workout, ftp):
        if seg.start_pct is None:
            raise FileError("ב-.erg / .mrc כל שלב צריך יעד הספק (רכיבה חופשית אפשרית רק ב-.zwo).")
        points.append((minute, seg.start_pct))
        minute += seg.seconds / 60
        points.append((minute, seg.end_pct))
    return points


def _course(workout: Workout, header_extra: str, columns: str, rows: list[str]) -> str:
    description = (workout.description or workout.name).replace("\n", " ")
    return (
        "[COURSE HEADER]\n"
        "VERSION = 2\n"
        "UNITS = ENGLISH\n"
        f"DESCRIPTION = {description}\n"
        f"FILE NAME = {workout.name}\n"
        f"{header_extra}"
        f"{columns}\n"
        "[END COURSE HEADER]\n"
        "[COURSE DATA]\n"
        + "".join(f"{row}\n" for row in rows)
        + "[END COURSE DATA]\n"
    )


def to_mrc(workout: Workout, ftp: Optional[int] = None) -> str:
    rows = [f"{minute:.2f}\t{pct:.0f}" for minute, pct in _points(workout, ftp)]
    return _course(workout, "", "MINUTES PERCENT", rows)


def to_erg(workout: Workout, ftp: Optional[int] = None) -> str:
    if not ftp:
        raise FileError("קובץ .erg כתוב בוואטים, ולכן צריך FTP בפרופיל. בלי FTP אפשר .mrc או .zwo.")
    rows = [f"{minute:.2f}\t{round(ftp * pct / 100)}" for minute, pct in _points(workout, ftp)]
    return _course(workout, f"FTP = {ftp}\n", "MINUTES WATTS", rows)


FORMATS = {"zwo": to_zwo, "erg": to_erg, "mrc": to_mrc}


def available_formats(workout: Workout, ftp: Optional[int]) -> list[str]:
    """The trainer formats this workout can actually be written in."""
    ready = []
    for name, writer in FORMATS.items():
        try:
            writer(workout, ftp)
            ready.append(name)
        except FileError:
            pass
    return ready
