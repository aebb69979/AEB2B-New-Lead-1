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


def current(require_leads: bool = True) -> dict:
    """The signed-in AE's rows for the selected month.

    Stops the page with an explanation rather than returning something empty --
    every caller would otherwise repeat the same four guards.
    """
    account = st.session_state["account"]
    months = boot.get_months()
    if not months:
        st.info(NO_SNAPSHOT, icon=":material/inbox:")
        st.stop()

    picked = st.session_state.get("picked_month") or months[0]["id"]
    df, meta = boot.get_leads(picked)
    if df is None:
        st.info(NO_SNAPSHOT, icon=":material/inbox:")
        st.stop()

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
    persistence past day 45 pay off? -- permanently uncollected.
    """
    if state.get("closed"):
        write(":material/lock_clock: Past the 45-day analysis cutoff. Still "
              "callable — later attempts are recorded, they just sit outside "
              "the primary analysis.")
    elif state.get("known") and state["days_left"] <= 10:
        write(f":material/schedule: {state['days_left']} days until the cutoff.")


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
