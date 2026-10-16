"""Contact people at a company: who the AE actually spoke to, and how to reach them.

Append-only, like the call log and for the same reason: twenty-five people
saving at once cannot lose each other's work when nothing is ever edited in
place. An edit is a new row for the same `contact_id` with a later `logged_at`;
a removal is a new row with `removed` = "1". Reads take the latest row per
contact_id.

Contacts belong to the LEAD, not to the AE: after a territory handover the next
AE sees whoever the last one found.

Stored in a `contacts` tab of the call-log spreadsheet, which the app creates
on first use. These are named people's phone numbers and emails (PDPA), so that
spreadsheet stays shared with the service account and the admins only.
"""
from __future__ import annotations

import uuid

from . import timefmt

CONTACT_COLUMNS = [
    "row_uuid", "contact_id", "lead_id", "ae_id", "ae_email", "logged_at",
    "name", "role", "contact_type", "contact_type_other", "contact_info", "note",
    "removed", "app_version",
    # Added later, so on the END (ensure_header extends an existing tab). Only
    # so the sheet reads on its own; the app keys on lead_id.
    "company_name", "business_id",
]
APP_VERSION = "ae-app-1"

# Stored value -> label. As with channels, a stored value is never renamed.
TYPE_LABELS = {
    "phone": "Phone", "email": "Email", "line": "LINE",
    "facebook": "Facebook", "instagram": "Instagram", "other": "Other",
}
TYPES = list(TYPE_LABELS)
# Contact type -> call-log channel. "other" has none; the AE picks one.
TYPE_TO_CHANNEL = {"phone": "phone", "email": "email", "line": "line",
                   "facebook": "facebook", "instagram": "instagram"}

# Call-log `contact_id` for "the number we supplied", before anyone has been
# named. Blank means the AE did not say.
LIST_PHONE = "list_phone"


def new_contact_id() -> str:
    return "c-" + uuid.uuid4().hex[:10]


def new_row(contact_id: str, lead_id: str, ae_id: str, ae_email: str, name: str,
            role: str, contact_type: str, contact_type_other: str, contact_info: str,
            note: str, removed: bool = False, row_uuid: str | None = None,
            company_name: str = "", business_id: str = "") -> dict:
    """One version of one contact. `logged_at` is set HERE, never by the caller."""
    return {
        "row_uuid": row_uuid or str(uuid.uuid4()),
        "contact_id": contact_id, "lead_id": lead_id,
        "ae_id": str(ae_id), "ae_email": ae_email,
        "logged_at": timefmt.stamp(),
        "name": name, "role": role, "contact_type": contact_type,
        "contact_type_other": contact_type_other if contact_type == "other" else "",
        "contact_info": contact_info, "note": note,
        "removed": "1" if removed else "",
        "app_version": APP_VERSION,
        "company_name": company_name, "business_id": business_id,
    }


def removed_row(current: dict, ae_id: str, ae_email: str) -> dict:
    """Remove a contact: its latest version again, flagged removed."""
    return new_row(current["contact_id"], current["lead_id"], ae_id, ae_email,
                   str(current.get("name", "")), str(current.get("role", "")),
                   str(current.get("contact_type", "")),
                   str(current.get("contact_type_other", "")),
                   str(current.get("contact_info", "")), str(current.get("note", "")),
                   removed=True, company_name=str(current.get("company_name", "")),
                   business_id=str(current.get("business_id", "")))


def latest_contacts(rows: list[dict], lead_id: str) -> list[dict]:
    """Current contacts of one lead, oldest first, removed ones dropped."""
    best: dict[str, dict] = {}
    first: dict[str, float] = {}
    for r in rows:
        if str(r.get("lead_id")) != str(lead_id):
            continue
        cid = str(r.get("contact_id"))
        t = timefmt.parse(r.get("logged_at"))
        first[cid] = min(first.get(cid, t), t)
        prev = best.get(cid)
        if prev is None or t >= timefmt.parse(prev.get("logged_at")):
            best[cid] = r
    live = [r for r in best.values() if str(r.get("removed", "")).strip() != "1"]
    return sorted(live, key=lambda r: first[str(r.get("contact_id"))])


def type_label(c: dict) -> str:
    t = str(c.get("contact_type", ""))
    if t == "other":
        return str(c.get("contact_type_other", "")).strip() or "Other"
    return TYPE_LABELS.get(t, t)


def same_number(a, b) -> bool:
    """'062-256-0155' and '0622560155' are the same phone."""
    da, db = "".join(ch for ch in str(a) if ch.isdigit()), "".join(ch for ch in str(b) if ch.isdigit())
    return bool(da) and da == db


def label(c: dict) -> str:
    """'Somchai (Owner) · Phone 0812345678' -- for the call-log selector."""
    who = str(c.get("name", "")).strip() or "Unknown"
    role = str(c.get("role", "")).strip()
    return f"{who}{f' ({role})' if role else ''} · {type_label(c)} {c.get('contact_info', '')}".strip()
