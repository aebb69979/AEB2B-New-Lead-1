"""Timestamps that a person can read, and that code can still order.

Epoch seconds are unambiguous and sort trivially, which is why they were used
first -- but nobody can read 1789225841 in a spreadsheet, and a column nobody
can read is a column nobody checks.

THE FORMAT IS SORTABLE ON PURPOSE. `YYYY-MM-DD HH:MM:SS` is fixed width and
big-endian, so sorting the text gives chronological order -- in Sheets, in a
CSV, in pandas, anywhere. `DD-MM-YYYY` reads a little more naturally to a Thai
or British eye but sorts by day-of-month, which silently scrambles any ordering
done in the sheet, and it is ambiguous against the `MM-DD-YYYY` that US-locale
tools produce. That trade is not worth it on a field the analysis depends on.

BANGKOK, NOT THE SERVER'S CLOCK. Streamlit Cloud runs in UTC, so a raw local
time would render a 10am call as 03:00 and look like a bug. A fixed +07:00
offset is used rather than a named zone because Thailand has had no DST since
1976, and a fixed offset needs no tzdata in the container.

Values are written as TEXT with value_input_option="RAW", so Sheets stores them
verbatim instead of coercing them into its own date type -- which would
otherwise re-render them in the viewer's locale, Buddhist-era years included.
"""
from __future__ import annotations

import datetime as dt

BANGKOK = dt.timezone(dt.timedelta(hours=7), "ICT")


def now() -> dt.datetime:
    return dt.datetime.now(BANGKOK)


def text(when: dt.datetime | None = None) -> str:
    """'2026-09-13 10:36' -- account records, where the minute is plenty."""
    return (when or now()).strftime("%Y-%m-%d %H:%M")


def stamp(when: dt.datetime | None = None) -> str:
    """'2026-09-13 10:36:19+07:00' -- the call log.

    Carries the offset because this is the field the analysis reads: an exported
    CSV should not need a side note explaining which clock it came from.
    """
    w = when or now()
    return w.strftime("%Y-%m-%d %H:%M:%S") + w.strftime("%z")[:3] + ":" + w.strftime("%z")[3:]


def parse(value) -> float:
    """Epoch seconds from anything this app has ever written, else 0.0.

    Handles the legacy all-digit epoch values as well as the readable formats,
    so rows written before this change still order correctly against rows
    written after it. Never raises: it reads a spreadsheet cell that a person
    can edit.
    """
    s = str(value or "").strip()
    if not s:
        return 0.0
    try:                                                   # legacy epoch seconds
        return float(s)
    except ValueError:
        pass
    try:
        d = dt.datetime.fromisoformat(s)
    except ValueError:
        return 0.0
    if d.tzinfo is None:                                   # written without an offset
        d = d.replace(tzinfo=BANGKOK)
    return d.timestamp()


def human(value) -> str:
    """Render whatever is stored, readably. For showing legacy rows."""
    ts = parse(value)
    return text(dt.datetime.fromtimestamp(ts, BANGKOK)) if ts else ""
