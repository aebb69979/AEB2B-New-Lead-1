"""Where accounts live.

Two implementations behind one interface: a Google Sheet for real use, and an
in-memory dict for tests. The auth flows in `accounts.py` never import gspread,
so every login, lockout and approval path can be exercised without credentials,
a network, or a browser.

The Sheet is the whole database for identity. That is fine at 25 rows, and the
record is small enough to read wholesale and cache briefly.
"""
from __future__ import annotations

import time
from typing import Protocol

from . import timefmt

COLUMNS = [
    "email", "ae_id", "display_name", "password_hash", "status",
    "created_at", "approved_at", "failed_attempts", "locked_until",
    "session_nonce", "role", "regions",
]
PENDING, APPROVED, DISABLED = "pending", "approved", "disabled"
# role: blank is an AE (every account written before roles existed). A manager
# has no ae_id -- so the AE pages, which select by ae_id, show them nothing and
# they can log no call -- and sees the AEs of `regions` read-only instead.
AE, MANAGER = "", "manager"
REGIONS = ["BMA-East", "BMA-West", "Central-West", "East", "North",
           "Northeast1", "Northeast2", "South"]


def blank_record(email: str, password_hash: str, nonce: str,
                 display_name: str = "") -> dict:
    return {
        "email": email, "ae_id": "", "display_name": display_name,
        "password_hash": password_hash, "status": PENDING,
        "created_at": timefmt.text(), "approved_at": "",
        "failed_attempts": "0", "locked_until": "", "session_nonce": nonce,
        "role": AE, "regions": "",
    }


def is_manager(record: dict | None) -> bool:
    return bool(record) and str(record.get("role", "")).strip().lower() == MANAGER


class IdentityStore(Protocol):
    def get(self, email: str) -> dict | None: ...
    def create(self, record: dict) -> None: ...
    def update(self, email: str, **fields) -> None: ...
    def all_records(self) -> list[dict]: ...


class MemoryIdentityStore:
    """For tests. Same semantics as the Sheet, including returning copies so a
    caller cannot mutate stored state by accident."""

    def __init__(self, records: list[dict] | None = None):
        self._rows: dict[str, dict] = {r["email"]: dict(r) for r in (records or [])}

    def get(self, email: str) -> dict | None:
        r = self._rows.get(email)
        return dict(r) if r else None

    def create(self, record: dict) -> None:
        if record["email"] in self._rows:
            raise ValueError("account already exists")
        self._rows[record["email"]] = dict(record)

    def update(self, email: str, **fields) -> None:
        if email in self._rows:
            self._rows[email].update({k: str(v) for k, v in fields.items()})

    def all_records(self) -> list[dict]:
        return [dict(r) for r in self._rows.values()]


class SheetsIdentityStore:
    """Google Sheet backed. Reads are cached for `ttl` seconds because Streamlit
    re-runs the whole script on every interaction and the Sheets API has per-user
    rate limits; writes invalidate the cache immediately so a login that just
    incremented failed_attempts sees its own write."""

    def __init__(self, worksheet, ttl: float = 20.0):
        self._ws = worksheet
        self._ttl = ttl
        self._cache: list[dict] | None = None
        self._cached_at = 0.0

    # -- internals ---------------------------------------------------------
    def _rows(self, force: bool = False) -> list[dict]:
        if force or self._cache is None or time.time() - self._cached_at > self._ttl:
            self._cache = self._ws.get_all_records()
            self._cached_at = time.time()
        return self._cache

    def _row_index(self, email: str) -> int | None:
        for i, r in enumerate(self._rows(), start=2):     # row 1 is the header
            if str(r.get("email", "")).strip().lower() == email:
                return i
        return None

    def _invalidate(self) -> None:
        self._cache = None

    # -- interface ---------------------------------------------------------
    def get(self, email: str) -> dict | None:
        for r in self._rows():
            if str(r.get("email", "")).strip().lower() == email:
                return {k: ("" if v is None else str(v)) for k, v in r.items()}
        return None

    def create(self, record: dict) -> None:
        if self.get(record["email"]):
            raise ValueError("account already exists")
        self._ws.append_row([str(record.get(c, "")) for c in COLUMNS],
                            value_input_option="RAW")
        self._invalidate()

    def update(self, email: str, **fields) -> None:
        idx = self._row_index(email)
        if idx is None:
            return
        # one batched call rather than a write per field -- 25 users on a shared
        # per-minute quota is not much headroom
        updates = [{"range": f"{_col_letter(COLUMNS.index(k) + 1)}{idx}",
                    "values": [[str(v)]]}
                   for k, v in fields.items() if k in COLUMNS]
        if updates:
            self._ws.batch_update(updates, value_input_option="RAW")
            self._invalidate()

    def all_records(self) -> list[dict]:
        return [{k: ("" if v is None else str(v)) for k, v in r.items()}
                for r in self._rows(force=True)]


def _col_letter(n: int) -> str:
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def ensure_header(worksheet) -> None:
    """Write the header row if the sheet is empty. Saves a setup step and makes
    the column order authoritative in code rather than in whoever made the tab."""
    try:
        first = worksheet.row_values(1)
    except Exception:                                      # noqa: BLE001
        first = []
    if not first:
        worksheet.update([COLUMNS], "A1", value_input_option="RAW")
    elif first == COLUMNS[:len(first)] and len(first) < len(COLUMNS):
        # A sheet made before a column was added. Columns only ever go on the
        # END, so extending the header in place keeps every existing row valid;
        # without it get_all_records drops the new fields and writes land under
        # a blank header. The tab was created exactly as wide as the old header,
        # so it has to grow first or the write falls outside the grid.
        if getattr(worksheet, "col_count", len(COLUMNS)) < len(COLUMNS):
            worksheet.add_cols(len(COLUMNS) - worksheet.col_count)
        worksheet.update([COLUMNS[len(first):]], f"{_col_letter(len(first) + 1)}1",
                         value_input_option="RAW")
