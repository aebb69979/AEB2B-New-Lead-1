"""What both sections need before they can show anything.

Summary and Per-company are two views of ONE batch. The month selector lives in
the entry script's sidebar so that switching months moves both of them together;
if each page owned its own selector they would drift apart, and an AE would log
a call against a company they were looking at in a different month.
"""
from __future__ import annotations

import streamlit as st

from . import bootstrap as boot
from . import leads as L
from . import schedule as sch

NO_SNAPSHOT = ("No lead snapshot is published yet. The control panel's Publish "
               "tab writes one; upload it to the Drive folder and share it with "
               "the service account.")


def batch() -> tuple:
    """(whole snapshot, metadata) for the selected month, or stop the page.

    Unfiltered -- callers narrow it with `leads_for` or `team_for`, the only two
    functions allowed to select rows for a person.
    """
    months = boot.get_months()
    if not months:
        st.info(NO_SNAPSHOT, icon=":material/inbox:")
        st.stop()
    picked = st.session_state.get("picked_month") or months[0]["id"]
    df, meta = boot.get_leads(picked)
    if df is None:
        st.info(NO_SNAPSHOT, icon=":material/inbox:")
        st.stop()
    return df, meta


def current(require_leads: bool = True) -> dict:
    """The signed-in AE's rows for the selected month.

    Stops the page with an explanation rather than returning something empty --
    every caller would otherwise repeat the same four guards.
    """
    account = st.session_state["account"]
    df, meta = batch()

    if not str(account.get("ae_id", "")).strip():
        st.info("Your account has no territory assigned, so there are no leads to "
                "show. An administrator assigns one when approving an account — "
                "administrators themselves usually have none.",
                icon=":material/info:")
        st.stop()

    # The ONE place rows are selected for a person. ae_id comes from the verified
    # session record; it is never read from a request parameter.
    mine = L.leads_for(df, account["ae_id"])
    if require_leads and not len(mine):
        st.warning("No leads are assigned to your territory in this snapshot.",
                   icon=":material/warning:")
        st.stop()

    return {"account": account, "df": df, "meta": meta, "mine": mine,
            "state": sch.batch_state(meta.get("generated", ""))}


def window_note(state: dict, write) -> None:
    """The one line about where this batch sits in its window.

    `closed` is past the ANALYSIS cutoff, not unavailable. Saying so matters:
    an AE who reads "closed" as "stop" would leave the follow-up data -- does
    persistence past the cutoff pay off? -- permanently uncollected.
    """
    if state.get("closed"):
        write(f":material/lock_clock: Past the {sch.FREEZE_DAY}-day analysis cutoff. Still "
              "callable — later attempts are recorded, they just sit outside "
              "the primary analysis.")
    elif state.get("known") and state["days_left"] <= 10:
        write(f":material/schedule: {state['days_left']} days until the cutoff.")


# Lead progress pills on Summary: grey not begun, orange under way, blue floor
# cleared, green reached someone.
LEAD_STATUS_COLOR = {"Not started": "gray", "In progress": "orange",
                     "Floor met": "blue", "Contacted": "green"}

# Company status badge colours, shared by the Company view and the lead table.
STATUS_COLOR = {"New": "green", "Existing": "gray"}
# Dropdown options are plain text, so a coloured dot stands in for the badge.
STATUS_DOT = {"New": "🟢", "Existing": "⚪"}
# st.badge has no size option. Scoped to the one container keyed "status_tag",
# so no other badge in the app changes.
STATUS_TAG_CSS = """<style>
.st-key-status_tag [data-testid="stMarkdownContainer"] > p > span,
.st-key-interest_tag [data-testid="stMarkdownContainer"] > p > span {
  padding: 0.3rem 1rem; border-radius: 0.5rem; border: 1px solid currentColor;
}
.st-key-status_tag [data-testid="stMarkdownContainer"] span,
.st-key-interest_tag [data-testid="stMarkdownContainer"] span {
  font-size: 1.35rem !important; font-weight: 600; line-height: 1.5;
}
</style>"""
# Interest from the call log: the answer on the most recent attempt that
# recorded one (calllog.lead_progress). Blank until someone has said.
INTEREST_LABEL = {"สนใจ (interested)": "Interest", "ไม่สนใจ (not_interested)": "No interest"}
INTEREST_EMOJI = {"Interest": "👍", "No interest": "👎"}
INTEREST_COLOR = {"Interest": "blue", "No interest": "red"}
INTEREST_HELP = {"Interest": "Interested, on the most recent call that recorded an answer.",
                 "No interest": "Not interested, on the most recent call that recorded an answer."}


def interest_of(p: dict) -> str:
    """'Interest' / 'No interest' / '' from one lead's progress entry."""
    return INTEREST_LABEL.get(str((p or {}).get("interest", "")), "")


STATUS_HELP = {"New": "No Enterprise AE owns this company in CCP+.",
               "Existing": "An Enterprise AE already owns this company in CCP+."}


def progress_for(mine) -> dict:
    """Attempt progress for these leads, or empty when logging is unconfigured."""
    from . import calllog as cl
    log = boot.get_call_log()
    ids = [str(x) for x in mine["lead_id"]]
    if log is None:
        return {i: {"attempts": 0, "connected": False, "floor_met": False,
                    "last_disposition": "", "last_logged_at": 0.0, "interest": ""}
                for i in ids}
    return cl.lead_progress(log.rows(), ids)


# --------------------------------------------------------------------------
# provenance of a phone number
# --------------------------------------------------------------------------
# The snapshot carries `verified` only for batches enriched by the grounded
# path. Older batches -- everything published before it existed -- have no such
# column, and "we did not record it" must not render as "we checked and failed".
# Hence two functions: one asks whether the batch knows, the other what it says.
_TRUE = {"true", "1", "yes", "t"}


def has_provenance(lead) -> bool:
    """True when this batch recorded where its numbers came from."""
    v = lead.get("verified", None)
    return v is not None and str(v).strip().lower() not in ("", "nan", "none")


def is_verified(lead) -> bool:
    """True when this number was read off a source the run cited."""
    return str(lead.get("verified", "")).strip().lower() in _TRUE
