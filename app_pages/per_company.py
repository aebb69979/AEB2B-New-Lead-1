"""One company at a time: everything known about it, and where a call is logged.

ONE PAGE, NOT FORTY. `st.Page` objects are built at startup, so a page per lead
would mean rebuilding navigation on every sign-in, a forty-item menu, and a
different menu per AE. Navigation belongs INSIDE the view: Prev / Next, a
jump-to selector, and a row click from Summary.

The logging form is the reason this app exists. Every field an AE fills is
recorded next to a SERVER-stamped `logged_at`, which is what the shared
spreadsheet could never give: there, call times were typed at end of day and
rows were edited in place, so neither the timing nor the history survived.
"""
import datetime as dt
import uuid

import streamlit as st

from utils import bootstrap as boot
from utils import calllog as cl
from utils import view as V

ctx = V.current()
mine, meta, state = ctx["mine"], ctx["meta"], ctx["state"]
account = ctx["account"]
log = boot.get_call_log()
n = len(mine)

# --------------------------------------------------------------------------
# which lead
# --------------------------------------------------------------------------
ids = [str(x) for x in mine["lead_id"]]

# A lead_id handed over from Summary (row click), or `?lead=` on a fresh load.
#
# ONCE, THOUGH. The page writes the current lead back to `?lead=` at the bottom
# of every run, so reading it unconditionally here made the URL the authority
# over the position: a click on Next moved lead_ix, the rerun read the stale
# param, and the index snapped straight back. It looked like the button did
# nothing. So the param is consumed on the first run of this page and is a
# deep-link entry point only; after that lead_ix owns the position.
wanted = st.session_state.pop("open_lead", None)
if wanted is None and not st.session_state.get("_lead_deeplinked"):
    wanted = st.query_params.get("lead")
st.session_state["_lead_deeplinked"] = True
if wanted in ids:
    st.session_state["lead_ix"] = ids.index(wanted)
ix = min(max(int(st.session_state.get("lead_ix", 0)), 0), n - 1)
st.session_state["lead_ix"] = ix

# A KEYED WIDGET IGNORES `index=`. Once "lead_jump" exists in session_state,
# Streamlit restores that value and the index argument is dead -- so a button
# that moved lead_ix would be silently undone by the selector snapping back on
# the very next line. Position therefore lives in lead_ix, the selector is
# pushed to match it before it is drawn, and the selector reports its own
# changes through a callback, which runs BEFORE the rerun that would overwrite
# them. Clamping also covers the month selector changing how many leads exist.
if st.session_state.get("lead_jump") != ix:
    st.session_state["lead_jump"] = ix


def _jumped() -> None:
    st.session_state["lead_ix"] = st.session_state["lead_jump"]


prog = V.progress_for(mine)


def _label(i: int) -> str:
    """'7. บจ.ตัวอย่าง · 2/3' -- how far through the attempt floor this lead is.

    Always n/3, never a bare dash: "0/3" says there is a floor and this lead has
    not started against it, which is the thing worth seeing in a list of forty.
    """
    p = prog.get(ids[i], {})
    mark = f"{p.get('attempts', 0)}/{cl.MIN_ATTEMPTS}"
    if p.get("floor_met"):
        mark += " ✓"        # floor cleared, or contact made and the sequence ended
    return f"{i + 1}. {mine.iloc[i]['company_name']} · {mark}"


bar = st.container(horizontal=True, vertical_alignment="bottom")
with bar:
    if st.button(":material/chevron_left:", disabled=ix == 0, help="Previous lead"):
        st.session_state["lead_ix"] = ix - 1
        st.rerun()
    if st.button(":material/chevron_right:", disabled=ix >= n - 1, help="Next lead"):
        st.session_state["lead_ix"] = ix + 1
        st.rerun()
    st.selectbox(f"Lead {ix + 1} of {n}", options=list(range(n)),
                 format_func=_label, key="lead_jump", on_change=_jumped)

lead = mine.iloc[ix]
lead_id = str(lead["lead_id"])
# Keeps the company in the URL, so a reload or a shared-with-yourself link lands
# back on it. It is a lead_id, not an ae_id: it grants nothing. The row still
# has to be in `mine`, which came from the session's verified territory.
st.query_params["lead"] = lead_id

# --------------------------------------------------------------------------
# the company
# --------------------------------------------------------------------------
st.header(lead["company_name"])
sub = [str(lead.get(c, "")) for c in ("district", "province") if str(lead.get(c, "")).strip()]
st.caption(" · ".join([x for x in [str(lead.get("name_en", "")).strip()] + sub if x])
           or "—")


def _rows(pairs):
    """Only the fields that actually have a value. A screen of 'N/A' hides the
    three fields that matter."""
    return [(k, str(v).strip()) for k, v in pairs if str(v).strip() and str(v) != "nan"]


left, right = st.columns(2)
with left:
    with st.container(border=True):
        st.markdown("**Contact**")
        got = _rows([("Phone", lead.get("phone")), ("Email", lead.get("email")),
                     ("LINE", lead.get("line_id")), ("Website", lead.get("website")),
                     ("Facebook", lead.get("facebook"))])
        for k, v in got:
            st.markdown(f"{k} · **{v}**" if k == "Phone" else f"{k} · {v}")
        if not got:
            st.caption("No contact details found for this company.")
with right:
    with st.container(border=True):
        st.markdown("**Company**")
        for k, v in _rows([("Registered capital", lead.get("registered_capital")),
                           ("Address", lead.get("address")),
                           ("Sub-district", lead.get("sub_district")),
                           ("District", lead.get("district")),
                           ("Province", lead.get("province"))]):
            st.markdown(f"{k} · {v}")

desc = str(lead.get("business_description", "")).strip()
if desc and desc != "nan":
    with st.container(border=True):
        st.markdown("**Business**")
        st.write(desc)

# --------------------------------------------------------------------------
# attempts so far
# --------------------------------------------------------------------------
st.subheader("Call log")
V.window_note(state, st.caption)

if log is None:
    st.warning("Call logging is not configured yet — `call_log_sheet_id` is "
               "missing from the app's secrets, so there is nowhere to write. "
               "Ask an administrator; the lead list still works.",
               icon=":material/report:")
    st.stop()

history = cl.latest_attempts(log.rows(), lead_id)
done = len(history)
st.caption(f"{done} of {cl.MIN_ATTEMPTS} minimum attempts"
           + (" · contact made" if any(h.get("disposition") == cl.CONNECTED
                                       for h in history) else ""))
st.progress(min(done / cl.MIN_ATTEMPTS, 1.0))

if history:
    st.dataframe(
        [{"#": h["attempt_seq"], "Called": h.get("called_at_reported", ""),
          "Channel": h.get("channel", ""), "Outcome": h.get("disposition", ""),
          "Interest": h.get("interest_outcome", ""), "Notes": h.get("notes", ""),
          "Logged by": h.get("ae_email", "")}
         for h in history],
        hide_index=True, width="stretch",
        column_config={"Notes": st.column_config.TextColumn(width="large")})

# --------------------------------------------------------------------------
# log an attempt
# --------------------------------------------------------------------------
next_seq = cl.next_attempt_seq(log.rows(), lead_id)
prior = {int(h["attempt_seq"]): h for h in history}

# Deliberately NOT st.form: the interest question only makes sense once the
# disposition says contact was made, and a form cannot react until it submits.
with st.container(border=True):
    # Correcting is logging the same attempt number again -- the append-only log
    # takes the later row. Offering it here is what makes that claim true for an
    # AE; without it the only fix would be asking someone to edit the sheet,
    # which is exactly the habit this app replaces.
    seq = next_seq
    if prior:
        seq = st.selectbox(
            "Attempt", [next_seq] + sorted(prior, reverse=True),
            format_func=lambda s: (f"New — attempt {s}" if s == next_seq
                                   else f"Correct attempt {s}"),
            key=f"seq_{lead_id}")
    was = prior.get(seq, {})
    st.markdown(f"**{'Correct' if was else 'Log'} attempt {seq}**")

    # Keys carry the attempt number so switching between new and a correction
    # re-initialises every widget from that attempt rather than keeping whatever
    # was half-typed for the other one.
    k = f"{lead_id}_{seq}"
    when = str(was.get("called_at_reported", "")) if was else ""
    try:
        prev_dt = dt.datetime.strptime(when, "%Y-%m-%d %H:%M")
    except ValueError:
        prev_dt = dt.datetime.now().replace(second=0, microsecond=0)

    c1, c2, c3 = st.columns([1, 1, 1])
    d = c1.date_input("Call date", value=prev_dt.date(), key=f"d_{k}",
                      format="YYYY-MM-DD")
    t = c2.time_input("Call time", value=prev_dt.time(), key=f"t_{k}", step=300)
    ch = c3.selectbox("Channel", cl.CHANNELS, key=f"ch_{k}",
                      index=cl.CHANNELS.index(was["channel"])
                      if was.get("channel") in cl.CHANNELS else 0)

    disp = st.radio("Outcome", cl.DISPOSITIONS, key=f"disp_{k}",
                    index=cl.DISPOSITIONS.index(was["disposition"])
                    if was.get("disposition") in cl.DISPOSITIONS else None)
    interest = ""
    if disp == cl.CONNECTED:
        interest = st.radio("Interest", cl.INTEREST, key=f"int_{k}", horizontal=True,
                            index=cl.INTEREST.index(was["interest_outcome"])
                            if was.get("interest_outcome") in cl.INTEREST else None) or ""
    notes = st.text_area("Notes", value=str(was.get("notes", "") or ""), key=f"n_{k}",
                         height=80,
                         placeholder="Anything worth remembering for the next call.")

    # One id per pending attempt. A double-click, or a rerun that lands mid-write,
    # then resolves to the same row rather than a duplicate dial in the data.
    uk = f"uuid_{k}"
    st.session_state.setdefault(uk, str(uuid.uuid4()))

    if st.button("Save attempt", type="primary", key=f"save_{k}",
                 disabled=disp is None):
        row = cl.new_row(
            lead_id=lead_id, ae_id=account["ae_id"], ae_email=account["email"],
            batch=meta.get("name", ""), attempt_seq=seq,
            called_at_reported=f"{d.isoformat()} {t.strftime('%H:%M')}",
            channel=ch, disposition=disp, interest_outcome=interest,
            notes=notes.strip(), row_uuid=st.session_state[uk])
        fresh = log.append(row)
        for key in (uk, f"n_{k}", f"disp_{k}", f"int_{k}", f"seq_{lead_id}"):
            st.session_state.pop(key, None)
        st.toast("Attempt saved" if fresh else "Already saved",
                 icon=":material/check:")
        st.rerun()
    if disp is None:
        st.caption("Choose an outcome to save.")

st.caption(":material/info: A mistake is fixed by logging the same attempt "
           "again — the later entry is the one that counts. Nothing is ever "
           "overwritten, so the history stays intact.")
