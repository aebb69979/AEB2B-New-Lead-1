"""Wiring: secrets in, resources out.

Two modes, chosen by whether a service account is configured.

    cloud   Sheets-backed accounts, snapshot pulled from Drive
    local   a JSON file for accounts, snapshot read off disk

Local mode exists so the whole app -- signup, approval, login, lockout, the lead
view -- can be exercised before any Google setup happens, and so a change can be
tested without touching production accounts. It is never reachable once secrets
are set, because `has_cloud()` decides and cloud always wins.
"""
from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from .store import COLUMNS, MemoryIdentityStore, SheetsIdentityStore, ensure_header

LOCAL_ACCOUNTS = Path(".local_accounts.json")
SNAPSHOT_GLOB = "m*_leads_*.csv"

# Local mode reads snapshots off disk, and where they are depends on where the
# app was launched from. Rather than demand one working directory, look in the
# places they actually live: beside the app, in the project root, or one level
# up from either. Cloud mode never touches this.
LOCAL_SNAPSHOT_DIRS = [
    Path("m1_run/output"),                 # launched from the project root
    Path("../m1_run/output"),              # launched from app_ae/
    Path(__file__).resolve().parent.parent.parent / "m1_run" / "output",
]


def local_snapshots() -> list[Path]:
    """Every snapshot on disk, newest name first, wherever it lives."""
    seen, out = set(), []
    for d in LOCAL_SNAPSHOT_DIRS:
        try:
            files = sorted(d.glob(SNAPSHOT_GLOB), reverse=True)
        except OSError:
            continue
        for f in files:
            key = f.resolve()
            if key not in seen:
                seen.add(key)
                out.append(f)
    return out


# Streamlit looks for secrets.toml under the CURRENT WORKING DIRECTORY, not
# beside the script. Launching from the project root -- `streamlit run
# app_ae/streamlit_app.py`, which is what the README says -- therefore finds
# nothing, and the symptom is an app with no Admin page and no way to approve
# anyone. So fall back to the file that ships with the app.
APP_SECRETS = Path(__file__).resolve().parent.parent / ".streamlit" / "secrets.toml"


@st.cache_resource(show_spinner=False)
def _file_secrets() -> dict:
    try:
        import tomllib
        with APP_SECRETS.open("rb") as fh:
            return tomllib.load(fh)
    except Exception:                                      # noqa: BLE001
        return {}


def secret(name: str, default=None):
    """A secret, or a default. `st.secrets` raises when the file is absent, which
    would make local mode impossible."""
    try:
        if name in st.secrets:
            return st.secrets[name]
    except Exception:                                      # noqa: BLE001
        pass
    return _file_secrets().get(name, default)


def has_cloud() -> bool:
    return bool(secret("gcp_service_account")) and bool(secret("drive_folder_id"))


def pepper() -> str:
    return str(secret("password_pepper", "dev-only-pepper-not-for-production"))


def session_secret() -> str:
    return str(secret("session_secret", "dev-only-session-secret"))


def admin_emails() -> set[str]:
    raw = secret("admin_emails", [])
    if isinstance(raw, str):
        raw = [x.strip() for x in raw.split(",")]
    return {str(x).strip().lower() for x in (raw or []) if str(x).strip()}


def is_dev() -> bool:
    return not has_cloud()


# --------------------------------------------------------------------------
# accounts
# --------------------------------------------------------------------------
class _JsonStore(MemoryIdentityStore):
    """Local-mode accounts, persisted to a file so a signup survives a rerun.

    Re-reads whenever the file changes on disk. Without that, this object holds
    whatever it loaded at construction -- and because the store is a cached
    resource, a change made by another process (or another tab) would be
    invisible until the cache was cleared. The Sheets store gets the same
    property from its TTL; this is the local equivalent.
    """

    def __init__(self, path: Path):
        self._path = path
        self._mtime = -1.0
        super().__init__([])
        self._reload()

    def _reload(self) -> None:
        try:
            m = self._path.stat().st_mtime
        except OSError:
            return
        if m == self._mtime:
            return
        try:
            rows = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        self._rows = {r["email"]: dict(r) for r in rows}
        self._mtime = m

    def _flush(self):
        self._path.write_text(json.dumps(self.all_records(), indent=2), encoding="utf-8")
        try:
            self._mtime = self._path.stat().st_mtime
        except OSError:
            pass

    def get(self, email):
        self._reload(); return super().get(email)

    def all_records(self):
        # guard against _flush -> all_records -> _reload clobbering a pending write
        if not getattr(self, "_flushing", False):
            self._reload()
        return super().all_records()

    def create(self, record):
        self._reload(); super().create(record)
        self._flushing = True
        try: self._flush()
        finally: self._flushing = False

    def update(self, email, **fields):
        self._reload(); super().update(email, **fields)
        self._flushing = True
        try: self._flush()
        finally: self._flushing = False


@st.cache_resource(show_spinner=False)
def get_store():
    """The account store. cache_resource because it holds a network client and
    is shared across sessions -- session_state would give every visitor their
    own copy and their own cache."""
    if not has_cloud():
        return _JsonStore(LOCAL_ACCOUNTS)
    import gspread
    gc = gspread.service_account_from_dict(dict(secret("gcp_service_account")))
    sh = gc.open_by_key(str(secret("identity_sheet_id")))
    try:
        ws = sh.worksheet("ae_identity")
    except Exception:                                      # noqa: BLE001
        ws = sh.add_worksheet("ae_identity", rows=200, cols=len(COLUMNS))
    ensure_header(ws)
    return SheetsIdentityStore(ws)


# --------------------------------------------------------------------------
# leads
# --------------------------------------------------------------------------
@st.cache_data(ttl=300, show_spinner=False)
def get_months() -> list[dict]:
    """Every published month, newest first, one entry each.

    Short TTL because this is how a newly uploaded month becomes visible; the
    snapshots themselves are immutable so they cache far longer.
    """
    from . import leads as L
    if not has_cloud():
        out = []
        for f in local_snapshots():
            k = L.snapshot_sort_key(f.name)
            if k:
                out.append({"id": str(f), "name": f.name, "month": k[0],
                            "generated": k[1], "label": L.month_label(f.name),
                            "modifiedTime": ""})
        return L.dedupe_months(sorted(out, key=lambda x: (x["month"], x["generated"]),
                                      reverse=True))
    return L.dedupe_months(L.list_snapshots(dict(secret("gcp_service_account")),
                                            str(secret("drive_folder_id"))))


def month_options(months: list[dict]) -> dict:
    """id -> selector label, with the window state folded in."""
    from . import schedule as sch
    out = {}
    for m in months:
        st = sch.batch_state(m.get("generated", ""))
        suffix = ""
        if st["known"]:
            suffix = " · closed" if st["closed"] else f" · {st['days_left']}d left"
        out[m["id"]] = m["label"] + suffix
    return out


@st.cache_data(ttl=3600, show_spinner="Loading leads…")
def get_leads(file_id: str | None = None):
    """(frame, metadata) for one month. Immutable once published, so an hour.

    Keyed on file_id, so switching months is one download each and then cached --
    an AE flipping back and forth does not re-fetch.
    """
    from . import leads as L
    months = get_months()
    if not months:
        return None, None
    meta = next((m for m in months if m["id"] == file_id), months[0])
    if not has_cloud():
        return L.load_local(meta["id"]), meta
    return L.read_csv_bytes(
        L.download(dict(secret("gcp_service_account")), meta["id"])), meta


def setup_problems() -> list[str]:
    """What is missing, for the admin page. Better to say so plainly than to let
    a misconfigured deployment fail one login at a time."""
    out = []
    if has_cloud():
        if not secret("identity_sheet_id"):
            out.append("`identity_sheet_id` is not set — accounts cannot be stored.")
        if pepper().startswith("dev-only"):
            out.append("`password_pepper` is still the development value.")
        if session_secret().startswith("dev-only"):
            out.append("`session_secret` is still the development value.")
        if not call_log_sheet_id():
            out.append("`call_log_sheet_id` is not set — AEs can see leads but "
                       "cannot log calls. Create a separate spreadsheet (not the "
                       "identity one, which holds password hashes), share it with "
                       "the service account as Editor, and add its id.")
    if not admin_emails():
        out.append("`admin_emails` is empty — nobody can approve accounts.")
    return out


# --------------------------------------------------------------------------
# the call log
# --------------------------------------------------------------------------
LOCAL_CALL_LOG = Path(".local_call_log.json")


def call_log_sheet_id() -> str:
    """Deliberately NOT defaulted to `identity_sheet_id`.

    The identity sheet holds password hashes. The call log is analysis data and
    will be shared with whoever runs the analysis. Falling back to one sheet
    would quietly hand out the hashes with it, so an unset id disables logging
    instead -- loud and safe rather than silent and wrong.
    """
    return str(secret("call_log_sheet_id", "") or "")


def call_log_available() -> bool:
    return (not has_cloud()) or bool(call_log_sheet_id())


@st.cache_resource(show_spinner=False)
def get_call_log():
    """Append-only attempt log. None when cloud mode has no sheet configured."""
    from .calllog import LOG_COLUMNS, LocalCallLog, SheetsCallLog, ensure_header as _hdr
    if not has_cloud():
        return LocalCallLog(LOCAL_CALL_LOG)
    if not call_log_sheet_id():
        return None
    import gspread
    gc = gspread.service_account_from_dict(dict(secret("gcp_service_account")))
    sh = gc.open_by_key(call_log_sheet_id())
    try:
        ws = sh.worksheet("call_log")
    except Exception:                                      # noqa: BLE001
        ws = sh.add_worksheet("call_log", rows=5000, cols=len(LOG_COLUMNS))
    _hdr(ws)
    return SheetsCallLog(ws)
