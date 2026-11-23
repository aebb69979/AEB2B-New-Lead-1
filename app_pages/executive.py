"""Executive summary: the lead sources side by side. Administrators only.

Read-only. Each source is its own file with its own call log, so each column
below is computed from that source's log alone. Vacant territories are left
out: nobody is calling them, and counting their leads would drag every rate.
The DBD column is the newest monthly batch, as on its Team progress page.
"""
import altair as alt
import pandas as pd
import streamlit as st

from utils import bootstrap as boot
from utils import calllog as cl
from utils import view as V

account = st.session_state["account"]
if not (str(account["email"]).lower() in boot.admin_emails()
        and not st.session_state.get("viewing_as")):
    st.error("This page is for administrators.", icon=":material/block:")
    st.stop()

st.header("Executive summary")

WRONG = "wrong_number"
DISCONNECTED = "disconnected"
CODE = {d: d[d.rfind("(") + 1:-1] for d in cl.DISPOSITIONS}


def _rate(num, den):
    return num / den if den else None


@st.cache_data(ttl=120, show_spinner="Reading call logs…")
def _sources() -> list[tuple[str, pd.DataFrame | None, list[dict] | None, str]]:
    out = []
    months = boot.get_months()
    if months:
        df, meta = boot.get_leads(months[0]["id"])
        out.append(("DBD leads", df, boot.get_call_log(boot.MAIN),
                    meta["label"] if meta else ""))
    else:
        out.append(("DBD leads", None, None, ""))
    for name, loader, section in (("BMA-East industrial", boot.get_industrial_leads, boot.INDUSTRIAL),
                                  ("SME B2B leads", boot.get_sme_leads, boot.SME)):
        df, meta = loader()
        out.append((name, df, boot.get_call_log(section), (meta or {}).get("label", "")))
    # Rows, not the store object, so the cached result is plain data.
    return [(n, df, log.rows() if log is not None else None, lbl) for n, df, log, lbl in out]


def _stats(df: pd.DataFrame, rows: list[dict]) -> dict:
    team = df[~df["ae_name"].str.contains("vacant", case=False)]
    ids = {str(i) for i in team["lead_id"]}
    prog = cl.lead_progress(rows, list(ids))
    started = {i for i, p in prog.items() if p["attempts"]}
    contacted = {i for i, p in prog.items() if p["connected"]}
    interested = {i for i, p in prog.items() if V.interest_of(p) == "Interest"}

    attempts = [r for r in cl.latest_attempts(rows) if str(r.get("lead_id")) in ids]
    phone = [r for r in attempts if str(r.get("channel") or "phone") == "phone"]
    bad = lambda r: any(k in str(r.get("disposition")) for k in (WRONG, DISCONNECTED))  # noqa: E731
    phone_leads = {str(r["lead_id"]) for r in phone}
    bad_leads = {str(r["lead_id"]) for r in phone if bad(r)}
    mix = {c: sum(1 for r in attempts if str(r.get("disposition")) == d)
           for d, c in CODE.items()}
    return {
        "AEs": team["ae_id"].nunique(), "Leads": len(ids), "Started": len(started),
        "Contacted": len(contacted), "Interested": len(interested),
        "Dials": len(attempts), "Phone leads": len(phone_leads),
        "Bad-number leads": len(bad_leads),
        "Phone dials": len(phone), "Bad-number dials": sum(1 for r in phone if bad(r)),
        "mix": mix,
    }


cols, rates, mixes = {}, {}, {}
notes = []
for name, df, rows, label in _sources():
    if df is None:
        notes.append(f"{name}: not published.")
        continue
    if rows is None:
        notes.append(f"{name}: no call-log sheet configured, so no call stats.")
        rows = []
    s = _stats(df, rows)
    cols[name] = s
    rates[name] = {
        "Started": _rate(s["Started"], s["Leads"]),
        "Contact rate": _rate(s["Contacted"], s["Started"]),
        "Interest rate": _rate(s["Interested"], s["Started"]),
        "Pitch success": _rate(s["Interested"], s["Contacted"]),
        "Incorrect phone no. (leads)": _rate(s["Bad-number leads"], s["Phone leads"]),
        "Incorrect phone no. (dials)": _rate(s["Bad-number dials"], s["Phone dials"]),
    }
    mixes[name] = {k: _rate(v, s["Dials"]) or 0.0 for k, v in s["mix"].items()}
    cols[name]["Dials per started lead"] = _rate(s["Dials"], s["Started"])

for n in notes:
    st.caption(f":material/info: {n}")
if not cols:
    st.info("No lead source is published.", icon=":material/inbox:")
    st.stop()

# --------------------------------------------------------------------------
# headline: the incorrect-number rate, one tile per source
# --------------------------------------------------------------------------
st.subheader("Incorrect phone numbers")
st.caption("จากบริษัทที่โทรทางโทรศัพท์แล้ว สัดส่วนบริษัทที่มีการโทรอย่างน้อยหนึ่งครั้งได้ผลเป็น "
           "เบอร์ผิด หรือ เบอร์ถูกยกเลิก (ไม่รวมพื้นที่ที่ยังว่างอยู่)")
tiles = st.container(horizontal=True)
for name, r in rates.items():
    v = r["Incorrect phone no. (leads)"]
    tiles.metric(name, "—" if v is None else f"{v:.0%}", border=True,
                 help="บริษัทที่โทรทางโทรศัพท์แล้ว อย่างน้อยหนึ่งครั้งได้ผล เบอร์ผิด หรือ "
                      "เบอร์ถูกยกเลิก ÷ บริษัทที่โทรทางโทรศัพท์ทั้งหมด")

# --------------------------------------------------------------------------
# side by side
# --------------------------------------------------------------------------
st.subheader("Source comparison")
COUNTS = ["AEs", "Leads", "Started", "Contacted", "Interested", "Dials"]
table = pd.DataFrame(
    {n: {**{k: s[k] for k in COUNTS},
         "Dials per started lead": s["Dials per started lead"],
         **rates[n]} for n, s in cols.items()})
pct = [k for k in table.index if k in next(iter(rates.values()))]
shown = table.astype(object)
for k in table.index:
    for n in table.columns:
        v = table.at[k, n]
        shown.at[k, n] = ("—" if v is None or pd.isna(v)
                          else f"{v:.0%}" if k in pct
                          else f"{v:.1f}" if k == "Dials per started lead"
                          else f"{int(v):,}")
st.dataframe(shown, width="stretch")
st.caption("Started = บริษัทที่โทรแล้วอย่างน้อยหนึ่งครั้ง ÷ บริษัททั้งหมด "
           "Contact rate และ Interest rate คิดจากบริษัทที่เริ่มโทรแล้ว "
           "ส่วน Pitch success = บริษัทที่สนใจ ÷ บริษัทที่ติดต่อได้ "
           "โดยนับสถานะสนใจของแต่ละบริษัทครั้งเดียว จากการโทรครั้งล่าสุดที่มีการบันทึกคำตอบ")

# --------------------------------------------------------------------------
# what the dials ended in
# --------------------------------------------------------------------------
st.subheader("How dials ended")
st.caption("สัดส่วนของการโทรแต่ละครั้ง (ไม่ใช่จำนวนบริษัท) แยกตามผลการโทร คิดจากจำนวนการโทรทั้งหมดของแต่ละแหล่งลีด "
           "ดังนั้นแท่งทั้งหมดของแต่ละแหล่งรวมกันได้ 100% (นับทุกช่องทางการติดต่อ "
           "ไม่เฉพาะโทรศัพท์ และไม่รวมพื้นที่ที่ยังว่างอยู่)")
mix = (pd.DataFrame(mixes).rename_axis("Outcome").reset_index()
       .melt(id_vars="Outcome", var_name="Source", value_name="Share"))
mix["Outcome"] = mix["Outcome"].str.replace("_", " ").str.capitalize()
# Fixed at 0 and not zoomable or pannable: a share cannot be negative, and a
# dragged axis would show a region that cannot hold data.
chart = (alt.Chart(mix).mark_bar()
         .encode(y=alt.Y("Outcome:N", title=None, sort=list(dict.fromkeys(mix["Outcome"]))),
                 x=alt.X("Share:Q", title="Share of dials", axis=alt.Axis(format="%"),
                         scale=alt.Scale(domain=[0, max(float(mix["Share"].max()), 0.05) * 1.1],
                                         nice=True, clamp=True)),
                 yOffset="Source:N",
                 color=alt.Color("Source:N", title=None),
                 tooltip=["Source", "Outcome", alt.Tooltip("Share:Q", format=".1%")])
         .properties(height=420))
st.altair_chart(chart, width="stretch")
st.caption(f":material/visibility: Read-only · DBD column: "
           f"{next((l for n, _, _, l in _sources() if n == 'DBD leads'), '') or 'n/a'}")
