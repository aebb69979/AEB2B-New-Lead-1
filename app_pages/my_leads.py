"""The AE's own leads. Read-only in step 3 — logging attempts is step 4."""
import streamlit as st

from utils import bootstrap as boot
from utils import leads as L
from utils import schedule as sch

account = st.session_state["account"]

st.header("My leads")

months = boot.get_months()
if not months:
    st.info("No lead snapshot is published yet. The control panel's Publish tab "
            "writes one; upload it to the Drive folder and share it with the "
            "service account.", icon=":material/inbox:")
    st.stop()

# Newest first, and the newest is the default. Earlier months stay available so a
# batch does not disappear from under an AE the moment the next one is published.
if len(months) > 1:
    picked = st.segmented_control(
        "Batch", options=[m["id"] for m in months],
        format_func=lambda i: boot.month_options(months)[i],
        default=months[0]["id"], key="month_pick")
    picked = picked or months[0]["id"]
else:
    picked = months[0]["id"]

df, meta = boot.get_leads(picked)
if df is None:
    st.info("No lead snapshot is published yet. The control panel's Publish tab "
            "writes one; upload it to the Drive folder and share it with the "
            "service account.", icon=":material/inbox:")
    st.stop()

# The ONE place rows are selected for a person. ae_id comes from the verified
# session record; it is never read from a request parameter.
mine = L.leads_for(df, account["ae_id"])

if not str(account.get("ae_id", "")).strip():
    st.info("Your account has no territory assigned, so there are no leads to "
            "show. An administrator assigns one when approving an account — "
            "administrators themselves usually have none.",
            icon=":material/info:")
    st.stop()

if not len(mine):
    st.warning("No leads are assigned to your territory in this snapshot.",
               icon=":material/warning:")
    st.stop()

state = sch.batch_state(meta.get("generated", ""))
left, right = st.columns([3, 1])
left.caption(f"{len(mine)} leads · territory {account['ae_id']} · {meta['label']}"
             + (f" · {state['label']}" if state["known"] else ""))
if state.get("closed"):
    # Closed means past the ANALYSIS cutoff, not unavailable. The batch stays
    # fully callable; the AE just should not think it is still the live one.
    left.caption(":material/lock_clock: Past the 45-day analysis cutoff. Still "
                 "callable — later attempts are recorded, they just sit outside "
                 "the primary analysis.")
elif state.get("known") and state["days_left"] <= 10:
    left.caption(f":material/schedule: {state['days_left']} days until the cutoff.")
# Deliberately the only filter offered. Sorting is not: the list order is a
# stored shuffle that distributes older leads through it, and any sort the AE
# could apply would undo that.
show_all = right.toggle("Show all", value=True,
                        help="Off hides leads you have marked as done locally.")

st.dataframe(
    mine[L.DISPLAY],
    hide_index=True,
    width="stretch",
    height=560,
    column_config={
        "company_name": st.column_config.TextColumn("Company", width="large"),
        "phone": st.column_config.TextColumn("Phone", width="small"),
        "district": st.column_config.TextColumn("District", width="small"),
        "province": st.column_config.TextColumn("Province", width="small"),
        "business_description": st.column_config.TextColumn("Business", width="large"),
    },
)

with st.expander("Contact details"):
    extra = [c for c in ("lead_id", "name_en", "email", "line_id", "website", "facebook")
             if c in mine.columns]
    st.dataframe(mine[extra], hide_index=True, width="stretch")

st.caption(f":material/database: {L.snapshot_label(meta, len(df))}")
