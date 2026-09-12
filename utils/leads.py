"""Loading the published lead snapshot, and handing an AE only their own rows.

This app deploys to Streamlit Cloud from its own repo and does not ship the
pipeline, so the column allowlist is repeated here rather than imported from
`m1_pipeline.publish`. That duplication is deliberate but it can drift, so the
allowlist is enforced twice: as `usecols` at parse time, and as an assertion on
the loaded frame.

WHY THE ALLOWLIST EXISTS. The snapshot carries `registration_number` for
traceability. Digits 5-7 of a Thai juristic id are the Buddhist-era registration
year -- 565 is 2022, 569 is 2026 -- which recovers the cohort for every row. An
AE who can tell an old lead from a new one works them differently, and effort
then confounds the age comparison the two arms exist to measure. Reading with
`usecols` means the column is never parsed into memory, rather than loaded and
then remembered-not-to-render.
"""
from __future__ import annotations

import io
import re
from pathlib import Path

import pandas as pd

# Mirrors m1_pipeline.publish.AE_LOADABLE. Keep in step; the assertion below is
# what catches it if they drift.
AE_LOADABLE = [
    "lead_id", "company_name", "name_en",
    "phone", "email", "line_id", "website", "facebook",
    "business_description", "registered_capital",
    "address", "sub_district", "district", "province",
    "ae_id", "ae_name", "display_order",
]
# Never loaded. `registration_number` is the traceability field; the rest would
# name the arm outright.
FORBIDDEN = [
    "registration_number", "arm", "cohort", "corrected_registration_date",
    "register_date", "tranche", "batch", "sample_weight", "sample_weight_norm",
    "stratum_division", "ae_id_frame", "ae_name_frame",
]
# Shown in the lead list, in this order.
DISPLAY = ["company_name", "phone", "district", "province", "business_description"]

DRIVE_FILES = "https://www.googleapis.com/drive/v3/files"


# --------------------------------------------------------------------------
def prefer_ipv4_if_broken(probe_timeout: float = 3.0) -> bool:
    """Stop a dead IPv6 route from adding ~75s to every Google call.

    Google advertises IPv6 first. Python's socket.create_connection tries
    addresses in the order returned and waits for the OS to give up on each --
    there is no Happy Eyeballs racing the way curl does it. On a network where
    IPv6 resolves but does not route, the first Drive call therefore stalls for
    over a minute and looks exactly like a hang.

    So: probe once, and only if IPv6 is actually unreachable, tell urllib3 to
    ask for IPv4 addresses. No effect on networks where IPv6 works.
    """
    import socket
    try:
        infos = socket.getaddrinfo("oauth2.googleapis.com", 443, socket.AF_INET6)
    except OSError:
        return False                                   # no IPv6 offered; nothing to do
    if not infos:
        return False
    s = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    s.settimeout(probe_timeout)
    try:
        s.connect(infos[0][4][:2])
        return False                                   # IPv6 fine, leave it alone
    except OSError:
        pass
    finally:
        s.close()
    try:
        import urllib3.util.connection as u
        u.allowed_gai_family = lambda: socket.AF_INET
        return True
    except Exception:                                  # noqa: BLE001
        return False



def _check(df: pd.DataFrame) -> pd.DataFrame:
    leaked = [c for c in FORBIDDEN if c in df.columns]
    if leaked:
        raise ValueError(
            f"snapshot loaded with columns that un-blind the arm: {leaked}. "
            "AE_LOADABLE has drifted from the published file."
        )
    return df


def read_csv_bytes(raw: bytes) -> pd.DataFrame:
    """Parse a snapshot, keeping only the allowed columns.

    `usecols` with a callable tolerates a snapshot that gains or loses a column
    between months -- a stricter list would raise and take the app down rather
    than degrade.
    """
    df = pd.read_csv(io.BytesIO(raw), dtype=str,
                     usecols=lambda c: c in AE_LOADABLE)
    return _check(df)


def load_local(path: str | Path) -> pd.DataFrame:
    return read_csv_bytes(Path(path).read_bytes())


# --------------------------------------------------------------------------
# Drive
# --------------------------------------------------------------------------
def _session(service_account_info: dict):
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2.service_account import Credentials
    prefer_ipv4_if_broken()
    creds = Credentials.from_service_account_info(
        service_account_info,
        scopes=["https://www.googleapis.com/auth/drive.readonly",
                "https://www.googleapis.com/auth/spreadsheets"])
    return AuthorizedSession(creds)


SNAPSHOT_RE = re.compile(r"^m(\d+)_leads_(\d{4}-\d{2}-\d{2})\.csv$")


def snapshot_sort_key(name: str) -> tuple[int, str] | None:
    """(month number, generation date) for ordering, or None if not a snapshot.

    The month is parsed as an INTEGER on purpose. Sorting these names as strings
    puts m9 above m10, because '9' > '1' character by character -- so the app
    would quietly serve month 9's leads forever once month 10 arrived. Ten months
    away, invisible until it happens, and trivial to avoid now.
    """
    m = SNAPSHOT_RE.match(name)
    return (int(m.group(1)), m.group(2)) if m else None


def month_label(name: str) -> str:
    """'m2_leads_2026-10-15.csv' -> 'Month 2 · Oct 2026'.

    The date in the filename is when the batch was GENERATED, which is what an AE
    recognises -- "the leads I got in October" -- not the registration cohort of
    the companies inside it. Naming it by cohort would also leak the arm.
    """
    k = snapshot_sort_key(name)
    if not k:
        return name
    n, date = k
    try:
        import datetime as _dt
        d = _dt.date.fromisoformat(date)
        return f"Month {n} · {d.strftime('%b %Y')}"
    except ValueError:
        return f"Month {n}"


def list_snapshots(service_account_info: dict, folder_id: str) -> list[dict]:
    """Every published snapshot in the folder, newest month first.

    All of them, not just the latest: an AE needs to look back at last month's
    batch, and a month that vanished from the app the moment the next one landed
    would take unfinished work with it.

    Searching by name rather than pinning file ids means publishing next month is
    an upload and nothing else -- the folder is already shared, so a new file
    inherits access.
    """
    sess = _session(service_account_info)
    q = (f"'{folder_id}' in parents and trashed = false "
         f"and mimeType != 'application/vnd.google-apps.folder'")
    r = sess.get(DRIVE_FILES, params={
        "q": q, "fields": "files(id,name,modifiedTime,size)",
        "pageSize": 200,
        "supportsAllDrives": True, "includeItemsFromAllDrives": True})
    r.raise_for_status()
    out = []
    for f in r.json().get("files", []):
        k = snapshot_sort_key(f["name"])
        if k:
            out.append({**f, "month": k[0], "generated": k[1],
                        "label": month_label(f["name"])})
    # newest month first, and within a month the latest regeneration
    return sorted(out, key=lambda f: (f["month"], f["generated"]), reverse=True)


def dedupe_months(snaps: list[dict]) -> list[dict]:
    """One entry per month -- the latest regeneration of each.

    A month republished after a correction leaves two files in the folder. The
    AE should be offered the month, not both attempts at it.
    """
    seen, out = set(), []
    for s in snaps:                                    # already newest-first
        if s["month"] not in seen:
            seen.add(s["month"])
            out.append(s)
    return out


def find_latest_snapshot(service_account_info: dict, folder_id: str) -> dict | None:
    snaps = list_snapshots(service_account_info, folder_id)
    return snaps[0] if snaps else None


def download(service_account_info: dict, file_id: str) -> bytes:
    sess = _session(service_account_info)
    r = sess.get(f"{DRIVE_FILES}/{file_id}",
                 params={"alt": "media", "supportsAllDrives": True})
    r.raise_for_status()
    return r.content


def load_from_drive(service_account_info: dict, folder_id: str,
                    file_id: str | None = None) -> tuple[pd.DataFrame, dict]:
    """One snapshot. `file_id` selects a specific month; default is the newest."""
    if file_id:
        snaps = list_snapshots(service_account_info, folder_id)
        meta = next((s for s in snaps if s["id"] == file_id), None)
    else:
        meta = find_latest_snapshot(service_account_info, folder_id)
    if not meta:
        raise FileNotFoundError(
            "No m*_leads_*.csv in that Drive folder. Upload the snapshot from the "
            "control panel's Publish tab and share it with the service account.")
    return read_csv_bytes(download(service_account_info, meta["id"])), meta


# --------------------------------------------------------------------------
# per-AE view
# --------------------------------------------------------------------------
def parse_ae_ids(value) -> list[str]:
    """'90008404' or '90008404, 90099999' -> a list.

    A person can legitimately own more than one id ACROSS MONTHS. ae_id is an
    employee number, so someone replacing a leaver gets a different one -- but
    the batch the leaver was working is already published under the OLD id, and
    it is immutable. Holding both means the joiner can finish the handed-over
    batch and still receive their own from the next month.
    """
    if value is None:
        return []
    return [x.strip() for x in str(value).replace(";", ",").split(",") if x.strip()]


def leads_for(df: pd.DataFrame, ae_id) -> pd.DataFrame:
    """The one function allowed to select rows for a person.

    `ae_id` must come from the verified session record and never from a request
    parameter. Isolation is enforced by app code here rather than by the storage
    layer, so this is the single place to audit -- keep it that way.

    Accepts one id or several; several is how a mid-window handover works.
    """
    ids = parse_ae_ids(ae_id)
    if not ids:
        return df.iloc[:0]
    out = df[df["ae_id"].astype(str).isin(ids)]
    if "display_order" in out.columns:
        # The stored shuffle. Contrast leads are distributed through the list so
        # an AE who runs out of time does not leave that arm systematically
        # unworked -- re-sorting here would undo it.
        out = out.assign(_o=pd.to_numeric(out["display_order"], errors="coerce")) \
                 .sort_values("_o").drop(columns="_o")
    return out.reset_index(drop=True)


def snapshot_label(meta: dict | None, n_rows: int) -> str:
    """Footer text. A stale snapshot is invisible unless the app says which one
    it loaded, and 'I published new leads but they still see the old ones' is
    otherwise a mystery."""
    if not meta:
        return f"{n_rows:,} leads · local file"
    return f"{meta.get('name', '?')} · {n_rows:,} leads · uploaded {meta.get('modifiedTime', '')[:10]}"
