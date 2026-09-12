"""Summary: the whole batch at a glance, and how far through it this AE is.

The table answers "what do I have"; the progress columns answer "what is still
outstanding". Both matter, and the second is the one that keeps the 3-attempt
floor honest -- without it an AE would have to open forty companies to find the
ones they never called, and the floor would be discovered in a weekly audit
instead of on the screen where the work happens.
"""
import pandas as pd
import streamlit as st

from utils import calllog as cl
from utils import leads as L
from utils import view as V

ctx = V.current()
mine, meta, state, account = ctx["mine"], ctx["meta"], ctx["state"], ctx["account"]
prog = V.progress_for(mine)

st.header("Summary")

left, right = st.columns([3, 1])
left.caption(f"{len(mine)} leads · territory {account['ae_id']} · {meta['label']}"
             + (f" · {state['label']}" if state["known"] else ""))
V.window_note(state, left.caption)

# --------------------------------------------------------------------------
# where this AE stands
# --------------------------------------------------------------------------
attempts = sum(p["attempts"] for p in prog.values())
started = sum(1 for p in prog.values() if p["attempts"])
floor_met = sum(1 for p in prog.values() if p["floor_met"])
connected = sum(1 for p in prog.values() if p["connected"])

m = st.container(horizontal=True)
m.metric("Leads", len(mine))
m.metric("Started", f"{started} / {len(mine)}")
m.metric(f"{cl.MIN_ATTEMPTS}-attempt floor met", f"{floor_met} / {len(mine)}")
m.metric("Contact made", connected)
m.metric("Dials logged", attempts)

# --------------------------------------------------------------------------
# the table
# --------------------------------------------------------------------------
def _status(p: dict) -> str:
    if p["connected"]:
        return "Contacted"
    if p["floor_met"]:
        return "Floor met"
    if p["attempts"]:
        return "In progress"
    return "Not started"


view = mine[L.DISPLAY].copy()
view.insert(0, "status", [_status(prog[str(i)]) for i in mine["lead_id"]])
view.insert(1, "attempts", [prog[str(i)]["attempts"] for i in mine["lead_id"]])
view["last_outcome"] = [prog[str(i)]["last_disposition"] for i in mine["lead_id"]]

# A filter, not a sort. List order is a stored shuffle that spreads the older
# leads through the batch; any sort an AE could apply would undo it and make
# lead age track how far down the list they got.
FILTERS = {"All": None, "Not started": "Not started",
           "In progress": "In progress", "Done": ("Floor met", "Contacted")}
pick = right.segmented_control("Show", list(FILTERS), default="All", key="lead_filter")
want = FILTERS.get(pick or "All")
if isinstance(want, tuple):
    view = view[view["status"].isin(want)]
elif want:
    view = view[view["status"] == want]

if not len(view):
    st.info(f"Nothing in “{pick}”.", icon=":material/filter_alt:")
    st.stop()

ids_shown = [str(i) for i in mine.loc[view.index, "lead_id"]]

event = st.dataframe(
    view, hide_index=True, width="stretch", height=560,
    on_select="rerun", selection_mode="single-row", key="lead_table",
    column_config={
        "status": st.column_config.TextColumn("Status", width="small"),
        "attempts": st.column_config.NumberColumn("Tries", width="small"),
        "company_name": st.column_config.TextColumn("Company", width="large"),
        "phone": st.column_config.TextColumn("Phone", width="small"),
        "district": st.column_config.TextColumn("District", width="small"),
        "province": st.column_config.TextColumn("Province", width="small"),
        "business_description": st.column_config.TextColumn("Business", width="large"),
        "last_outcome": st.column_config.TextColumn("Last outcome", width="medium"),
    },
)

# Clicking a row opens that company. The guard is what stops it from being a
# trap: without it, returning to Summary would find the row still selected and
# bounce straight back out again.
rows = list(event.selection.rows) if event and event.selection else []
if rows:
    chosen = ids_shown[rows[0]]
    if st.session_state.get("_last_open") != chosen:
        st.session_state["_last_open"] = chosen
        st.session_state["open_lead"] = chosen
        st.switch_page("app_pages/per_company.py")
else:
    st.session_state.pop("_last_open", None)

st.caption(":material/touch_app: Click a row to open that company and log a call. "
           f"· :material/database: {L.snapshot_label(meta, len(ctx['df']))}")
