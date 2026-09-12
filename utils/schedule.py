"""The calling window: what is open, what is closed, and who is behind.

    day  0   batch issued
    day 21   intervention check — the last point where a behind AE can recover
             without working harder than the plan assumed
    day 30   soft deadline
    day 45   ANALYSIS CUTOFF

The cutoff freezes the MEASUREMENT, not the work. Its whole purpose is to stop
lead age at time of call drifting by AE -- otherwise an AE effect and a lead-age
effect become inseparable, which lands hardest on the primary arm whose premise
is "newly registered, actively setting up". Nothing in that argument requires an
AE to stop dialling.

So a closed batch stays fully callable. Attempts logged after the cutoff are
recorded like any other and simply fall outside the primary analysis, where they
become a second dataset in their own right: does persistence past the window pay
off, and at what lead age does it stop paying?

NOTHING IS COPIED AT THE CUTOFF. Because the call log is append-only with a
server-stamped `logged_at`, the frozen view is a filter -- attempts at or before
`freeze_at`, latest row per (lead_id, attempt_seq) within that window. That is
reproducible from the live log forever, needs no process to remember, and lets
day-30 and day-90 views be derived from the same rows whenever they are wanted.
A copied spreadsheet would give exactly one cutoff and could drift from it.
"""
from __future__ import annotations

import datetime as dt

CHECKPOINT_DAY = 21      # intervention check
SOFT_DAY = 30            # soft deadline
FREEZE_DAY = 45          # analysis cutoff

# Minimum dials a batch implies: every lead gets the 3-attempt floor.
ATTEMPT_FLOOR = 3


def issued_date(generated: str) -> dt.date | None:
    """The date in a snapshot filename, which is when the batch was generated.

    Used as the issue date. Generating on the 12th and handing out on the 15th
    would make this three days pessimistic; pass an explicit override in that
    case rather than letting the window quietly start early.
    """
    try:
        return dt.date.fromisoformat(generated)
    except (TypeError, ValueError):
        return None


def freeze_date(generated: str, window: int = FREEZE_DAY) -> dt.date | None:
    d = issued_date(generated)
    return d + dt.timedelta(days=window) if d else None


def checkpoint_date(generated: str, day: int = CHECKPOINT_DAY) -> dt.date | None:
    d = issued_date(generated)
    return d + dt.timedelta(days=day) if d else None


def batch_state(generated: str, today: dt.date | None = None,
                window: int = FREEZE_DAY) -> dict:
    """Where a batch sits in its window.

    `closed` means past the analysis cutoff, NOT unavailable -- the app keeps it
    callable and labels it.
    """
    today = today or dt.date.today()
    issued = issued_date(generated)
    if not issued:
        return {"known": False, "closed": False, "label": ""}
    age = (today - issued).days
    freeze = issued + dt.timedelta(days=window)
    left = (freeze - today).days
    if age < SOFT_DAY:
        phase = "open"
    elif age < window:
        phase = "past soft deadline"
    else:
        phase = "closed"
    return {
        "known": True, "issued": issued, "freeze": freeze, "age_days": age,
        "days_left": left, "phase": phase, "closed": age >= window,
        "past_checkpoint": age >= CHECKPOINT_DAY,
        "label": (f"closed {freeze:%-d %b}" if age >= window
                  else f"{left} day{'s' if left != 1 else ''} left"),
    }


# --------------------------------------------------------------------------
# the day-21 check
# --------------------------------------------------------------------------
def expected_progress(day: int, leads: int, floor: int = ATTEMPT_FLOOR,
                      soft: int = SOFT_DAY) -> float:
    """Share of the dial floor an on-plan AE would have logged by `day`."""
    if leads <= 0 or soft <= 0:
        return 0.0
    return min(day / soft, 1.0)


def projected_finish(dials_logged: int, day: int, leads: int,
                     floor: int = ATTEMPT_FLOOR) -> int | None:
    """Day number this AE finishes the floor at their current rate.

    This is the number that makes the check actionable. "You are behind" invites
    an argument; "at this rate you finish on day 57, twelve days past the cutoff"
    does not.
    """
    if day <= 0 or dials_logged <= 0:
        return None
    needed = leads * floor
    rate = dials_logged / day
    return int(round(needed / rate)) if rate > 0 else None


def ae_status(dials_logged: int, day: int, leads: int,
              floor: int = ATTEMPT_FLOOR, window: int = FREEZE_DAY) -> dict:
    """Per-AE progress against the window, for the day-21 check.

    Flags on the PROJECTION rather than on today's shortfall, because an AE can
    be behind and still comfortably finish, and the response to those two is not
    the same.
    """
    needed = max(leads * floor, 1)
    done = max(int(dials_logged or 0), 0)
    finish = projected_finish(done, day, leads, floor)
    expected = expected_progress(day, leads, floor) * needed
    return {
        "dials": done, "needed": needed, "share": done / needed,
        "expected": expected, "behind_by": max(expected - done, 0),
        "projected_finish_day": finish,
        "will_miss": bool(finish and finish > window),
        "at_checkpoint": day >= CHECKPOINT_DAY,
    }
