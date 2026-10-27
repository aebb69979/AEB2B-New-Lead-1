"""Team progress: a sales manager's read-only view of their AEs' calling.

READ-ONLY BY CONSTRUCTION, not by hiding buttons. A manager account has no
ae_id, and every write path -- the logging form on Company view -- takes ae_id
from the session record, so there is nothing a manager could log against. This
page renders tables and nothing that writes, except the one view row below.

WHO SEES WHOM. `team_for` keeps AEs whose `ae_region` touches the manager's
regions; administrators see every region. The arm is not in the snapshot, so
nothing here can show which leads are old and which are new.

EVERY OPEN IS RECORDED (once per browser session per batch) in `manager_views`.
A manager watching is an intervention in its own right, and the analysis has to
be able to tell an AE effect from a manager effect.
"""
import pandas as pd
import streamlit as st

from utils import bootstrap as boot
from utils import calllog as cl
from utils import leads as L
from utils import schedule as sch
from utils import timefmt as tf
from utils import view as V
from utils.store import REGIONS, is_manager

account = st.session_state["account"]
# An admin viewing as an SM sees the SM's page, not the admin's.
is_admin = (str(account["email"]).lower() in boot.admin_emails()
            and not st.session_state.get("viewing_as"))
if not (is_manager(account) or is_admin):
    st.error("This page is for sales managers.", icon=":material/block:")
    st.stop()

df, meta = V.batch()
state = sch.batch_state(meta.get("generated", ""))

st.header("Team progress")

regions = L.parse_regions(account.get("regions"))
if is_admin and not is_manager(account):
    regions = st.pills("Regions", REGIONS, selection_mode="multi", default=REGIONS,
                       key="team_regions")
regions = [r for r in REGIONS if r in regions]             # display order
team = L.team_for(df, regions)

st.caption(f"{', '.join(regions) or 'No region'} · {meta['label']}"
           + (f" · {state['label']}" if state["known"] else ""))
V.window_note(state, st.caption)

if "ae_region" not in df.columns:
    st.info("This batch was published before team views existed, so it does not "
            "say which region each AE is in. Batches from now on do.",
            icon=":material/info:")
    st.stop()
if not len(team):
    st.info("No AEs in your region in this batch.", icon=":material/group_off:")
    st.stop()

# One row per manager per batch per browser session -- not per rerun, which
# would be every click.
_seen = f"_viewed_{meta['id']}"
# Not for an admin viewing as an SM: that view was the admin's, not the manager's.
if (is_manager(account) and not st.session_state.get("viewing_as")
        and not st.session_state.get(_seen)):
    views = boot.get_view_log()
    if views is not None:
        try:
            views.append(cl.new_view(account["email"], account.get("regions", ""),
                                     meta.get("name", "")))
        except Exception:                                  # noqa: BLE001
            pass         # losing one view row must never block the page
    st.session_state[_seen] = True

# --------------------------------------------------------------------------
# per lead, then per AE
# --------------------------------------------------------------------------
log = boot.get_call_log()
rows = log.rows() if log is not None else []
ids = [str(i) for i in team["lead_id"]]
prog = cl.lead_progress(rows, ids)
disp = cl.disposition_counts(rows, ids)
CODES = {d: d[d.rfind("(") + 1:-1] for d in cl.DISPOSITIONS}   # "connected", ...


def _status(p: dict) -> str:
    if p["connected"]:
        return "Contacted"
    if p["floor_met"]:
        return "Floor met"
    return "In progress" if p["attempts"] else "Not started"


lead = team[["lead_id", "ae_id", "ae_name", "ae_region", "company_name",
             "registration_number", "district", "province"]].copy()
lead["status"] = [_status(prog[i]) for i in ids]
lead["attempts"] = [prog[i]["attempts"] for i in ids]
lead["started"] = lead["attempts"] > 0
lead["floor_met"] = [prog[i]["floor_met"] for i in ids]
lead["contacted"] = [prog[i]["connected"] for i in ids]
# From the most recent call that recorded an answer, as on the AE's pages.
lead["interest"] = [V.interest_of(prog[i]) for i in ids]
lead["interested"] = lead["interest"] == "Interest"
lead["last_outcome"] = [prog[i]["last_disposition"] for i in ids]
lead["last_at"] = [prog[i]["last_logged_at"] for i in ids]
for d, code in CODES.items():
    lead[code] = [disp[i].get(d, 0) for i in ids]

ae = (lead.groupby(["ae_id", "ae_name", "ae_region"], sort=False)
      .agg(leads=("lead_id", "size"), started=("started", "sum"),
           floor_met=("floor_met", "sum"), contacted=("contacted", "sum"),
           interested=("interested", "sum"),
           dials=("attempts", "sum"), last_at=("last_at", "max"),
           **{c: (c, "sum") for c in CODES.values()})
      .reset_index())
ae["vacant"] = ae["ae_name"].str.contains("vacant", case=False)
ae["floor_share"] = ae["floor_met"] / ae["leads"]
ae["last_call"] = [tf.human(x) if x else "" for x in ae["last_at"]]


def _pace(r) -> str:
    if r["vacant"]:
        return "Vacant"
    if not state["known"]:
        return ""
    s = sch.ae_status(int(r["dials"]), state["age_days"], int(r["leads"]))
    if not r["dials"]:
        return "No calls yet"
    if s["will_miss"]:
        return f"Behind · finishes day {s['projected_finish_day']}"
    return "On track"


ae["pace"] = [_pace(r) for _, r in ae.iterrows()]

# Each region's SM, from the approved manager accounts. An AE spanning two
# regions (Northeast1; Northeast2) shows the managers of both, once each.
_sms = boot.managers_by_region()
ae["sm"] = [", ".join(dict.fromkeys(n for reg in L.parse_regions(r) for n in _sms.get(reg, [])))
            or "—" for r in ae["ae_region"]]
_order = {r: i for i, r in enumerate(REGIONS)}
ae["_r"] = [min((_order.get(x, 99) for x in L.parse_regions(r)), default=99)
            for r in ae["ae_region"]]
ae = ae.sort_values(["_r", "ae_region", "vacant", "ae_name"]).reset_index(drop=True)

# --------------------------------------------------------------------------
# headline
# --------------------------------------------------------------------------
staffed = ae[~ae["vacant"]]
m = st.container(horizontal=True)
m.metric("AEs", len(staffed))
m.metric("Leads", int(staffed["leads"].sum()))
m.metric("Started", int(staffed["started"].sum()))
m.metric(f"{cl.MIN_ATTEMPTS}-attempt floor met", int(staffed["floor_met"].sum()))
m.metric("Contact made", int(staffed["contacted"].sum()))
m.metric("Interested", int(staffed["interested"].sum()),
         help="จำนวนบริษัทที่ตอบว่าสนใจ จากการโทรครั้งล่าสุดที่มีการบันทึกคำตอบ")
m.metric("Dials logged", int(staffed["dials"].sum()))

# --------------------------------------------------------------------------
# by AE
# --------------------------------------------------------------------------
head, opt = st.columns([3, 1], vertical_alignment="bottom")
# The SM column is for administrators comparing regions. A manager would see
# only their own name on every row, so for them it is dropped, and the title
# with it.
show_sm = is_admin
head.subheader("By SM / AE" if show_sm else "By AE")
grouped = opt.toggle("Group by Region", value=True, key="team_group")
st.caption("Outcome columns count dials, so one lead called three times adds three. "
           f"Floor met: the lead was contacted or tried {cl.MIN_ATTEMPTS} times.")
outcome_cfg = {code: st.column_config.NumberColumn(code.replace("_", " ").capitalize(),
                                                   help=d, width="small")
               for d, code in CODES.items()}
shown = ae.copy()
if grouped:
    # Region and SM on the first row of each region only, like a pivot table.
    first = ~shown["ae_region"].duplicated()
    shown.loc[~first, ["ae_region", "sm"]] = ""
st.dataframe(
    shown[["ae_region", *(["sm"] if show_sm else []), "ae_name", "pace", "leads", "started",
           "floor_share",
           "contacted", "interested", "dials", *CODES.values(), "last_call"]],
    hide_index=True, width="stretch",
    column_config={
        "ae_region": st.column_config.TextColumn("Region", width="small"),
        "sm": st.column_config.TextColumn("SM", width="medium",
                                          help="The approved sales manager account(s) "
                                               "for this region."),
        "ae_name": st.column_config.TextColumn("AE", width="medium"),
        "pace": st.column_config.TextColumn(
            "Pace", width="medium",
            help=f"Projected from dials so far against {cl.MIN_ATTEMPTS} dials per "
                 f"lead. Behind means that rate finishes after the day-"
                 f"{sch.FREEZE_DAY} cutoff."),
        "leads": st.column_config.NumberColumn("Leads", width="small"),
        "started": st.column_config.NumberColumn("Started", width="small"),
        "floor_share": st.column_config.ProgressColumn(
            "Floor met", format="percent", min_value=0.0, max_value=1.0, width="small"),
        "contacted": st.column_config.NumberColumn("Contacted", width="small"),
        "interested": st.column_config.NumberColumn(
            "Interested", width="small",
            help="Companies, not dials: each counts once, from its most recent "
                 "call that recorded an answer."),
        "dials": st.column_config.NumberColumn("Dials", width="small"),
        **outcome_cfg,
        "last_call": st.column_config.TextColumn("Last call", width="small"),
    },
)

# --------------------------------------------------------------------------
# one AE's leads
# --------------------------------------------------------------------------
st.subheader("One AE's leads")
pick = st.selectbox("AE", ae["ae_id"].tolist(), key="team_ae",
                    format_func=lambda a: ae.set_index("ae_id").at[a, "ae_name"])
one = lead[lead["ae_id"] == pick].copy()
one["last_call"] = [tf.human(x) if x else "" for x in one["last_at"]]
FILTERS = {"All": None, "Not started": ("Not started",), "In progress": ("In progress",),
           "Done": ("Floor met", "Contacted"), "Interested": ()}
f = st.segmented_control("Show", list(FILTERS), default="All", key="team_filter")
if f == "Interested":
    one = one[one["interested"]]
elif FILTERS.get(f or "All"):
    one = one[one["status"].isin(FILTERS[f])]
st.dataframe(
    one[["company_name", "registration_number", "district", "province", "status", "interest",
         "attempts", "last_outcome", "last_call"]],
    hide_index=True, width="stretch",
    column_config={
        "company_name": st.column_config.TextColumn("Company", width="large"),
        "registration_number": st.column_config.TextColumn("Business ID", width="medium"),
        "district": st.column_config.TextColumn("District", width="small"),
        "province": st.column_config.TextColumn("Province", width="small"),
        "status": st.column_config.TextColumn("Status", width="small"),
        "interest": st.column_config.TextColumn("Interest", width="small",
                                                help="From the most recent call that recorded an answer."),
        "attempts": st.column_config.NumberColumn("Tries", width="small"),
        "last_outcome": st.column_config.TextColumn("Last outcome", width="medium"),
        "last_call": st.column_config.TextColumn("Last call", width="small"),
    },
)
st.caption(f":material/visibility: Read-only · :material/database: "
           f"{L.snapshot_label(meta, len(df))}")
