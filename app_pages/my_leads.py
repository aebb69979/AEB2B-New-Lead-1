"""Summary: the whole batch at a glance, and how far through it this AE is.

The table answers "what do I have"; the progress columns answer "what is still
outstanding". Both matter, and the second is the one that keeps the 3-attempt
floor honest -- without it an AE would have to open forty companies to find the
ones they never called, and the floor would be discovered in a weekly audit
instead of on the screen where the work happens.
"""
import pandas as pd
import streamlit as st

from utils import bootstrap as boot
from utils import calllog as cl
from utils import leads as L
from utils import view as V

ctx = V.current()
mine, meta, state, account = ctx["mine"], ctx["meta"], ctx["state"], ctx["account"]
prog = V.progress_for(mine)

st.header("Summary")

# A wrapping row, not st.columns([3, 1]): a fixed quarter-width column cut the
# filter off ("Done" vanished) whenever the sidebar was open. Sized to its
# content, the filter keeps all four options and drops below the caption
# only when the screen is too narrow for both.
top = st.container(horizontal=True, vertical_alignment="bottom")
left = top.container(width="stretch")
right = top.container(width="content", horizontal=True, vertical_alignment="bottom")
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
m.metric("Leads", len(mine), border=True)
m.metric("Started", f"{started} / {len(mine)}", border=True)
m.metric(f"{cl.MIN_ATTEMPTS}-attempt floor met", f"{floor_met} / {len(mine)}", border=True)
m.metric("Contact made", connected, border=True)
m.metric("Dials logged", attempts, border=True)

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
# New / Existing from the batch's final file; no file, no column and no filter.
company_status = boot.get_company_status(meta.get("name", ""))
if company_status:
    view.insert(1, "company_status",
                [[company_status[i]] if i in company_status else []
                 for i in mine["lead_id"].astype(str)])
view.insert(0, "status", [_status(prog[str(i)]) for i in mine["lead_id"]])
# Yes / No from the most recent attempt that recorded an answer; blank until then.
_YN = {"Interest": "Yes", "No interest": "No"}
view.insert(view.columns.get_loc("company_name") + 1, "interest",
            [[_YN[w]] if (w := V.interest_of(prog[str(i)])) else [] for i in mine["lead_id"]])
view.insert(1, "attempts", [prog[str(i)]["attempts"] for i in mine["lead_id"]])
view["last_outcome"] = [prog[str(i)]["last_disposition"] for i in mine["lead_id"]]

# Only for batches that recorded it. On an older batch the column would be a
# row of identical "unverified" ticks, which reads as a finding about the leads
# rather than what it is -- a gap in what was recorded at the time.
SHOW_VERIFIED = any(V.has_provenance(r) for _, r in mine.iterrows())
if SHOW_VERIFIED:
    view.insert(view.columns.get_loc("phone") + 1, "verified",
                [V.is_verified(r) for _, r in mine.iterrows()])

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
kind = None
if company_status:
    # Counts follow the Show filter, so each number is what clicking it shows.
    n = {"All": len(view), **{k: int(view["company_status"].apply(lambda v, k=k: k in v).sum())
                              for k in ("New", "Existing")}}
    kind = right.segmented_control("Company status", ["All", "New", "Existing"], default="All",
                                   key="company_filter",
                                   format_func=lambda k: f"{k} · {n[k]}")
    if kind in ("New", "Existing"):
        view = view[view["company_status"].apply(lambda v: kind in v)]

if not len(view):
    shown = " · ".join(x for x in (pick, kind) if x and x != "All") or "All"
    st.info(f"Nothing in “{shown}”.", icon=":material/filter_alt:")
    st.stop()

ids_shown = [str(i) for i in mine.loc[view.index, "lead_id"]]

# Status as a coloured pill. Filtering above works on the plain string; only
# the copy that is drawn gets the one-item list a pill column needs.
shown_view = view.assign(status=[[x] for x in view["status"]])
event = st.dataframe(
    shown_view, hide_index=True, width="stretch", height=560,
    on_select="rerun", selection_mode="single-row", key="lead_table",
    column_config={
        "status": st.column_config.MultiselectColumn(
            "Status", width=110, options=list(V.LEAD_STATUS_COLOR),
            color=list(V.LEAD_STATUS_COLOR.values())),
        "attempts": st.column_config.ProgressColumn(
            "Tries", width="small", min_value=0, max_value=cl.MIN_ATTEMPTS,
            format=f"%d/{cl.MIN_ATTEMPTS}",
            help=f"Dials logged against the {cl.MIN_ATTEMPTS}-attempt floor."),
        "company_name": st.column_config.TextColumn("Company", width="large"),
        "company_status": st.column_config.MultiselectColumn(
            "Company status", width=110, options=list(V.STATUS_COLOR),
            color=list(V.STATUS_COLOR.values()),
            help="New: no Enterprise AE in CCP+. Existing: an Enterprise AE owns it."),
        "interest": st.column_config.MultiselectColumn(
            "Interest", width="small", options=["Yes", "No"], color=["blue", "red"],
            help="From the most recent call that recorded an interest answer. "
                 "Blank until someone has said."),
        "registration_number": st.column_config.TextColumn("Business ID", width="medium"),
        "phone": st.column_config.TextColumn("Phone", width="small"),
        "verified": st.column_config.CheckboxColumn(
            "Verified", width="small",
            help="Ticked when this number was read off a public listing and the "
                 "source was recorded. Unticked means no source was found for "
                 "it — the number may still be right, but nothing backs it."),
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
