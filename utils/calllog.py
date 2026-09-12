"""The call log: one row per dial, appended, never edited.

APPEND-ONLY IS THE WHOLE DESIGN. A correction is a new row with a later
`logged_at`; reads take the latest row per (lead_id, attempt_seq). Three things
fall out of that and none of them work with a mutable log:

  * Twenty-five people writing at once cannot lose each other's work. There is
    no read-modify-write to race.
  * The 45-day analysis cutoff is a filter -- `logged_at <= freeze_at` -- so the
    frozen dataset is derived, reproducible forever, and needs no copied
    spreadsheet that could drift from it.
  * Day-30 and day-90 views come from the same rows whenever they are wanted.

WHY BOTH ae_id AND ae_email. `ae_id` is the territory slot; `ae_email` is the
person. They come apart during a handover -- two people legitimately hold the
same id across a batch -- and `ae_id` alone would attribute the leaver's calls
to whoever holds the territory at analysis time.

WHY logged_at AND called_at_reported. `logged_at` is stamped server-side here;
`called_at_reported` is what the AE types. Self-reported call times get rounded
and back-filled at end of day, which is the known limitation that makes the
spreadsheet call-time analysis untrustworthy. Keeping both lets the gap between
them be measured rather than assumed.
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Protocol

# Mirrors m1_pipeline.call_sheets. The app ships in its own repo and cannot
# import the pipeline, so these are duplicated deliberately -- they must stay
# identical or app-logged and workbook-logged rows will not pool.
DISPOSITIONS = [
    "ติดต่อได้ (connected)",
    "ไม่รับสาย (no_answer)",
    "เบอร์ผิด (wrong_number)",
    "เบอร์ถูกยกเลิก (disconnected)",
    "ติดต่อผู้มีอำนาจไม่ได้ (gatekeeper_blocked)",
    "ปฏิเสธการสนทนา (refused)",
    "ไม่ใช่บริษัท (not_a_business)",
]
INTEREST = ["สนใจ (interested)", "ไม่สนใจ (not_interested)"]
CHANNELS = ["phone", "line", "email"]
CONNECTED = DISPOSITIONS[0]
MIN_ATTEMPTS = 3          # the floor, not a cap

LOG_COLUMNS = [
    "row_uuid", "lead_id", "ae_id", "ae_email", "batch", "attempt_seq",
    "logged_at", "called_at_reported", "channel", "disposition",
    "interest_outcome", "notes", "app_version",
]
APP_VERSION = "ae-app-1"


def new_row(lead_id: str, ae_id: str, ae_email: str, batch: str, attempt_seq: int,
            called_at_reported: str, channel: str, disposition: str,
            interest_outcome: str, notes: str, row_uuid: str | None = None) -> dict:
    """One attempt. `logged_at` is set HERE, never taken from the caller."""
    return {
        "row_uuid": row_uuid or str(uuid.uuid4()),
        "lead_id": lead_id, "ae_id": str(ae_id), "ae_email": ae_email,
        "batch": batch, "attempt_seq": int(attempt_seq),
        "logged_at": f"{time.time():.0f}",
        "called_at_reported": called_at_reported,
        "channel": channel, "disposition": disposition,
        "interest_outcome": interest_outcome, "notes": notes,
        "app_version": APP_VERSION,
    }


# --------------------------------------------------------------------------
# stores
# --------------------------------------------------------------------------
class CallLogStore(Protocol):
    def append(self, row: dict) -> bool: ...
    def rows(self) -> list[dict]: ...


class LocalCallLog:
    """Dev backend. Same append-only semantics, a JSON file instead of a Sheet."""

    def __init__(self, path: Path):
        self._path = Path(path)
        self._mtime = -1.0
        self._rows: list[dict] = []
        self._reload()

    def _reload(self) -> None:
        try:
            m = self._path.stat().st_mtime
        except OSError:
            return
        if m == self._mtime:
            return
        try:
            self._rows = json.loads(self._path.read_text(encoding="utf-8"))
            self._mtime = m
        except (json.JSONDecodeError, OSError):
            pass

    def rows(self) -> list[dict]:
        self._reload()
        return list(self._rows)

    def append(self, row: dict) -> bool:
        self._reload()
        if any(r.get("row_uuid") == row["row_uuid"] for r in self._rows):
            return False                                   # already recorded
        self._rows.append(row)
        self._path.write_text(json.dumps(self._rows, indent=2, ensure_ascii=False),
                              encoding="utf-8")
        try:
            self._mtime = self._path.stat().st_mtime
        except OSError:
            pass
        return True


class SheetsCallLog:
    """Google Sheet backend. `append_row` inserts server-side and Google
    serialises concurrent writers, which is why this never does read-modify-write."""

    def __init__(self, worksheet, ttl: float = 20.0):
        self._ws = worksheet
        self._ttl = ttl
        self._cache: list[dict] | None = None
        self._at = 0.0

    def rows(self, force: bool = False) -> list[dict]:
        if force or self._cache is None or time.time() - self._at > self._ttl:
            self._cache = self._ws.get_all_records()
            self._at = time.time()
        return self._cache

    def append(self, row: dict) -> bool:
        if any(str(r.get("row_uuid")) == row["row_uuid"] for r in self.rows(force=True)):
            return False
        self._ws.append_row([str(row.get(c, "")) for c in LOG_COLUMNS],
                            value_input_option="RAW")
        self._cache = None
        return True


def ensure_header(worksheet) -> None:
    try:
        first = worksheet.row_values(1)
    except Exception:                                      # noqa: BLE001
        first = []
    if not first:
        worksheet.update([LOG_COLUMNS], "A1", value_input_option="RAW")


# --------------------------------------------------------------------------
# reading
# --------------------------------------------------------------------------
def latest_attempts(rows: list[dict], lead_id: str | None = None) -> list[dict]:
    """Latest row per (lead_id, attempt_seq) -- corrections supersede originals.

    This is what makes append-only readable: a mistyped attempt is fixed by
    logging it again, and the later row wins.
    """
    best: dict[tuple, dict] = {}
    for r in rows:
        if lead_id is not None and str(r.get("lead_id")) != str(lead_id):
            continue
        key = (str(r.get("lead_id")), str(r.get("attempt_seq")))
        prev = best.get(key)
        if prev is None or float(r.get("logged_at") or 0) >= float(prev.get("logged_at") or 0):
            best[key] = r
    return sorted(best.values(), key=lambda r: (str(r.get("lead_id")),
                                                int(r.get("attempt_seq") or 0)))


def next_attempt_seq(rows: list[dict], lead_id: str) -> int:
    seqs = [int(r.get("attempt_seq") or 0) for r in latest_attempts(rows, lead_id)]
    return (max(seqs) + 1) if seqs else 1


def lead_progress(rows: list[dict], lead_ids: list[str],
                  floor: int = MIN_ATTEMPTS) -> dict[str, dict]:
    """Per lead: attempts, last disposition, whether the floor is met.

    `floor_met` is the weekly per-AE check the protocol asks for. An AE who
    abandons leads after one dial censors "not contacted" in a way that tracks
    their own judgement -- which is the confound that made the earlier
    contactability data unusable. Contact made earlier counts as met: the
    sequence legitimately ended.
    """
    by_lead: dict[str, list[dict]] = {str(i): [] for i in lead_ids}
    for r in latest_attempts(rows):
        lid = str(r.get("lead_id"))
        if lid in by_lead:
            by_lead[lid].append(r)
    out = {}
    for lid, rs in by_lead.items():
        n = len(rs)
        connected = any(str(r.get("disposition")) == CONNECTED for r in rs)
        last = max(rs, key=lambda r: int(r.get("attempt_seq") or 0)) if rs else None
        out[lid] = {
            "attempts": n,
            "connected": connected,
            "floor_met": bool(connected or n >= floor),
            "last_disposition": str(last.get("disposition")) if last else "",
            "last_logged_at": float(last.get("logged_at") or 0) if last else 0.0,
            "interest": str(last.get("interest_outcome")) if last else "",
        }
    return out


def ae_dials(rows: list[dict], ae_id_list: list[str]) -> int:
    """Total dials logged by this AE -- the input to the day-21 projection."""
    ids = {str(a) for a in ae_id_list}
    return sum(1 for r in latest_attempts(rows) if str(r.get("ae_id")) in ids)
