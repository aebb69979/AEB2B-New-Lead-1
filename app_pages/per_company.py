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
from utils import contacts as CT
from utils import timefmt as tf
from utils import view as V


def _position(raw, count: int) -> int:
    """Whatever session_state holds, clamped to a row that exists.

    A bare int() here took the whole page down with a ValueError. session_state
    outlives a redeploy: a browser tab open across a code change can carry a
    value written by the previous version of this file, and a position field is
    never worth a crash -- landing on lead 1 is a recoverable annoyance, an
    unusable page is not.

    TypeError as well as ValueError: None raises the first, a bad string and
    NaN the second, and each would otherwise have needed its own guard.
    """
    try:
        i = int(raw)
    except (TypeError, ValueError):
        return 0
    return min(max(i, 0), max(count - 1, 0))


ctx = V.current()
mine, meta, state = ctx["mine"], ctx["meta"], ctx["state"]
account = ctx["account"]
log = boot.get_call_log()
people = boot.get_contacts()
n = len(mine)
# An admin viewing as an AE must not write under the AE's territory.
read_only = bool(st.session_state.get("viewing_as"))

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
ix = _position(st.session_state.get("lead_ix"), n)
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
    st.session_state["lead_ix"] = _position(st.session_state.get("lead_jump"), n)


prog = V.progress_for(mine)
company_status = boot.get_company_status(meta.get("name", ""))


def _label(i: int) -> str:
    """'7. บจ.ตัวอย่าง · 2/3' -- how far through the attempt floor this lead is.

    Always n/3, never a bare dash: "0/3" says there is a floor and this lead has
    not started against it, which is the thing worth seeing in a list of forty.
    """
    p = prog.get(ids[i], {})
    mark = f"{p.get('attempts', 0)}/{cl.MIN_ATTEMPTS}"
    if p.get("floor_met"):
        mark += " ✓"        # floor cleared, or contact made and the sequence ended
    s = company_status.get(ids[i], "")
    tag = f" · {V.STATUS_DOT[s]} {s}" if s else ""
    return f"{i + 1}. {mine.iloc[i]['company_name']} · {mark}{tag}"


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
status = company_status.get(lead_id, "")
title = st.container(horizontal=True, vertical_alignment="center", gap="small")
title.header(lead["company_name"], width="content")
if status:
    st.html(V.STATUS_TAG_CSS)
    title.container(key="status_tag", width="content").badge(
        status, color=V.STATUS_COLOR[status], help=V.STATUS_HELP[status])
sub = [str(lead.get(c, "")) for c in ("district", "province") if str(lead.get(c, "")).strip()]
st.caption(" · ".join([x for x in [str(lead.get("name_en", "")).strip()] + sub if x])
           or "—")


def _rows(pairs):
    """Only the fields that actually have a value. A screen of 'N/A' hides the
    three fields that matter."""
    return [(k, str(v).strip()) for k, v in pairs if str(v).strip() and str(v) != "nan"]


def _money(v):
    """1000000.0 -> '1,000,000 THB'. Anything non-numeric passes through for _rows."""
    try:
        f = float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return v
    return f"{f:,.0f} THB" if f == f else v


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
        # Where the number came from. A number read off a live Business Profile
        # and a number the model recalled are worth different amounts of
        # patience on the phone, and they used to look identical here. Batches
        # published before this existed carry no such column, and say so rather
        # than claiming either way.
        if any(k == "Phone" for k, _ in got):
            if not V.has_provenance(lead):
                st.caption(":material/help: Source not recorded for this batch.")
            elif V.is_verified(lead):
                url = str(lead.get("evidence_url", "")).strip()
                seen = ":material/verified: Read from a public listing"
                st.caption(f"{seen} · [see it]({url})" if url.startswith("http") else seen)
            else:
                st.caption(":material/error_outline: Not confident. Check before relying on it.")
with right:
    with st.container(border=True):
        st.markdown("**Company**")
        for k, v in _rows([("Business ID", lead.get("registration_number")),
                           ("Registered capital", _money(lead.get("registered_capital"))),
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
# contact people
# --------------------------------------------------------------------------
list_phone = str(lead.get("phone", "")).strip()
list_phone = "" if list_phone == "nan" else list_phone
contacts = CT.latest_contacts(people.rows(), lead_id) if people is not None else []
has_list_contact = any(CT.same_number(c.get("contact_info"), list_phone) for c in contacts)


@st.dialog("Contact person")
def _contact_form(current: dict | None = None, prefill: dict | None = None) -> None:
    """Add (current None) or edit one contact. Saving appends a version."""
    c = current or prefill or {}
    k = f"cf_{(current or {}).get('contact_id', 'new')}"
    name = st.text_input("Name *", value=str(c.get("name", "")), key=f"{k}_name",
                         placeholder="Type Unknown if they did not give a name")
    role = st.text_input("Role", value=str(c.get("role", "")), key=f"{k}_role",
                         placeholder="e.g. Owner, Accountant, Receptionist")
    was_t = str(c.get("contact_type", ""))
    ctype = st.selectbox("Contact type *", CT.TYPES, key=f"{k}_type",
                         index=CT.TYPES.index(was_t) if was_t in CT.TYPES else 0,
                         format_func=lambda t: CT.TYPE_LABELS[t])
    other = ""
    if ctype == "other":
        other = st.text_input("Other type *", value=str(c.get("contact_type_other", "")),
                              key=f"{k}_other", placeholder="e.g. WhatsApp, WeChat")
    info = st.text_input("Contact info *", value=str(c.get("contact_info", "")),
                         key=f"{k}_info", placeholder="Number, address or account name")
    note = st.text_area("Note", value=str(c.get("note", "")), key=f"{k}_note", height=80,
                        placeholder="e.g. Best reached after 2 pm; decides on telecom")

    row = st.container(horizontal=True)
    if row.button("Save contact", type="primary", key=f"{k}_save"):
        missing = [f for f, v in (("Name", name), ("Contact info", info),
                                  ("Other type", other if ctype == "other" else "x"))
                   if not str(v).strip()]
        if missing:
            st.error(f"Fill in: {', '.join(missing)}.", icon=":material/error:")
            return
        people.append(CT.new_row(
            contact_id=(current or {}).get("contact_id") or CT.new_contact_id(),
            lead_id=lead_id, ae_id=account["ae_id"], ae_email=account["email"],
            name=name.strip(), role=role.strip(), contact_type=ctype,
            contact_type_other=other.strip(), contact_info=info.strip(),
            note=note.strip(), company_name=str(lead.get("company_name", "")),
            business_id=str(lead.get("registration_number", ""))))
        st.toast("Contact saved", icon=":material/check:")
        st.rerun()
    if current and row.button("Remove contact", icon=":material/delete:", key=f"{k}_rm"):
        people.append(CT.removed_row(
            dict(current, company_name=str(lead.get("company_name", "")),
                 business_id=str(lead.get("registration_number", ""))),
            account["ae_id"], account["email"]))
        st.toast("Contact removed", icon=":material/check:")
        st.rerun()


st.subheader("Contact person")
if people is None:
    st.caption("Contacts are not configured yet: `call_log_sheet_id` is missing.")
else:
    for c in contacts:
        with st.container(border=True, horizontal=True, vertical_alignment="center"):
            with st.container(gap="small"):
                role = str(c.get("role", "")).strip()
                st.markdown(f"**{c.get('name', '')}**" + (f" · {role}" if role else ""))
                st.markdown(f"{CT.type_label(c)} · {c.get('contact_info', '')}")
                if str(c.get("note", "")).strip():
                    st.caption(str(c["note"]))
            if st.button(":material/edit:", key=f"edit_{c['contact_id']}",
                         help="Edit or remove", disabled=read_only):
                _contact_form(current=c)
    if not contacts:
        st.caption("No contact people yet. Add whoever you reach, so the next "
                   "call goes to the right person.")

    actions = st.container(horizontal=True)
    if actions.button("Add contact", icon=":material/person_add:", key="add_contact",
                      disabled=read_only):
        _contact_form()
    if list_phone and not has_list_contact and actions.button(
            f"Add as contact: {list_phone}", icon=":material/call:",
            key="add_list_phone", disabled=read_only,
            help="Starts a contact with the phone number from our list filled in."):
        _contact_form(prefill={"contact_type": "phone", "contact_info": list_phone})

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

# Call-log contact options: blank (not said), then the supplied number until
# someone has been named against it, then the named contacts.
by_id = {c["contact_id"]: c for c in contacts}
contact_opts = [""] + ([CT.LIST_PHONE] if list_phone and not has_list_contact else []) \
    + list(by_id)


def contact_name(cid) -> str:
    cid = str(cid or "")
    if cid == CT.LIST_PHONE:
        return f"Number from list {list_phone}"
    if cid in by_id:
        return CT.label(by_id[cid])
    return "Removed contact" if cid else ""


def _default_contact() -> str:
    """The supplied number, or whoever was named against it."""
    for cid, c in by_id.items():
        if CT.same_number(c.get("contact_info"), list_phone):
            return cid
    return CT.LIST_PHONE if CT.LIST_PHONE in contact_opts else ""
done = len(history)
st.caption(f"{done} of {cl.MIN_ATTEMPTS} minimum attempts"
           + (" · contact made" if any(h.get("disposition") == cl.CONNECTED
                                       for h in history) else ""))
st.progress(min(done / cl.MIN_ATTEMPTS, 1.0))

if history:
    st.dataframe(
        # "Called" is what the AE typed; "Logged" is server-stamped. Showing
        # both is the point -- the gap between them is measurable rather than
        # assumed, which is what the shared spreadsheet could never give.
        [{"#": h["attempt_seq"], "Called": h.get("called_at_reported", ""),
          "Channel": cl.CHANNEL_LABELS.get(h.get("channel", ""), h.get("channel", "")), "Outcome": h.get("disposition", ""),
          "Contact": contact_name(h.get("contact_id")),
          "Interest": h.get("interest_outcome", ""), "Notes": h.get("notes", ""),
          "Logged": tf.human(h.get("logged_at")), "Logged by": h.get("ae_email", "")}
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
        # Bangkok, not the server clock: Community Cloud runs in UTC.
        prev_dt = tf.now().replace(second=0, microsecond=0, tzinfo=None)

    was_c = str(was.get("contact_id", "") or "") if was else _default_contact()
    opts = contact_opts + ([was_c] if was_c and was_c not in contact_opts else [])

    def _contact_picked(k=k):
        """Point Channel at the picked contact's type; the AE can still change it."""
        cid = st.session_state.get(f"ct_{k}")
        t = CT.LIST_PHONE if cid == CT.LIST_PHONE else str(by_id.get(cid, {}).get("contact_type", ""))
        ch_for = "phone" if t == CT.LIST_PHONE else CT.TYPE_TO_CHANNEL.get(t)
        if ch_for:
            st.session_state[f"ch_{k}"] = ch_for

    st.selectbox("Contact", opts, index=opts.index(was_c), key=f"ct_{k}",
                 format_func=lambda c: contact_name(c) or "Not specified",
                 on_change=_contact_picked,
                 help="Whom this attempt was for. Add people under Contact person above.")

    c1, c2, c3 = st.columns([1, 1, 1])
    d = c1.date_input("Call date", value=prev_dt.date(), key=f"d_{k}",
                      format="YYYY-MM-DD")
    # A selectbox, not st.time_input: time_input only renders bare 24-hour, and
    # "19:54 (7:54 PM)" in the box itself is what stops it being read as mm:ss.
    # 5-minute grid plus the default's own minute, so "now" is never rounded.
    prev_t = prev_dt.time()
    times = sorted({dt.time(m // 60, m % 60) for m in range(0, 24 * 60, 5)} | {prev_t})
    t = c2.selectbox("Time of call (HH:MM)", times, index=times.index(prev_t), key=f"t_{k}",
                     format_func=lambda x: f"{x:%H:%M} ({x.strftime('%I:%M %p').lstrip('0')})",
                     help="Time of day the call was made. Type to search, e.g. 14:3.")
    ch = c3.selectbox("Channel", cl.CHANNELS, key=f"ch_{k}",
                      format_func=lambda c: cl.CHANNEL_LABELS.get(c, c),
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
                 disabled=disp is None or read_only):
        row = cl.new_row(
            lead_id=lead_id, ae_id=account["ae_id"], ae_email=account["email"],
            batch=meta.get("name", ""), attempt_seq=seq,
            called_at_reported=f"{d.isoformat()} {t.strftime('%H:%M')}",
            channel=ch, disposition=disp, interest_outcome=interest,
            notes=notes.strip(), row_uuid=st.session_state[uk],
            contact_id=st.session_state.get(f"ct_{k}") or "")
        fresh = log.append(row)
        for key in (uk, f"n_{k}", f"disp_{k}", f"int_{k}", f"seq_{lead_id}", f"ct_{k}"):
            st.session_state.pop(key, None)
        st.toast("Attempt saved" if fresh else "Already saved",
                 icon=":material/check:")
        st.rerun()
    if read_only:
        st.caption(":material/visibility: Viewing as this AE: read-only, nothing is saved.")
    elif disp is None:
        st.caption("Choose an outcome to save.")

st.caption(":material/info: A mistake is fixed by logging the same attempt "
           "again — the later entry is the one that counts. Nothing is ever "
           "overwritten, so the history stays intact.")
