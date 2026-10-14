"""Approvals and account administration. Visible only to admin_emails."""
import pandas as pd
import streamlit as st

from utils import accounts as acc
from utils import bootstrap as boot
from utils import identity as idy
from utils import leads as L
from utils import timefmt as tf
from utils.store import APPROVED, DISABLED, PENDING, REGIONS, is_manager

store = boot.get_store()
st.header("Admin")

for problem in boot.setup_problems():
    st.warning(problem, icon=":material/warning:")

records = store.all_records()
pending = [r for r in records if str(r.get("status", "")).lower() == PENDING]

st.subheader(f"Pending requests ({len(pending)})")
if not pending:
    st.caption("Nothing waiting.")
ROLES = ["AE", "Sales manager"]
TERRITORY_HELP = ("The employee number from the AE crosswalk. For someone taking "
                  "over a batch already published under a leaver's id, enter BOTH, "
                  "comma separated — 90008404, 90099999 — so they can finish the "
                  "handover and still get their own leads next month.")
MANAGER_HELP = ("A sales manager sees every AE whose territory is in these "
                "regions, read-only. They get no territory and cannot log calls.")


def _role_inputs(key: str, rec: dict, where) -> tuple[str, str, list]:
    """Role, territory and regions inputs, prefilled from `rec`."""
    role = where.segmented_control("Role", ROLES, key=f"role_{key}",
                                   default=ROLES[1] if is_manager(rec) else ROLES[0])
    if role == ROLES[1]:
        regs = where.multiselect("Regions", REGIONS, key=f"reg_{key}", help=MANAGER_HELP,
                                 default=[x for x in L.parse_regions(rec.get("regions"))
                                          if x in REGIONS])
        return role, "", regs
    ae_id = where.text_input("Territory (ae_id)", key=f"ae_{key}", placeholder="90008404",
                             value=str(rec.get("ae_id", "")), help=TERRITORY_HELP)
    return role, ae_id, []


def _apply(email: str, role: str, ae_id: str, regs: list):
    return (acc.approve_manager(store, email, regs) if role == ROLES[1]
            else acc.approve(store, email, ae_id))


for r in pending:
    with st.container(border=True):
        c1, c2, c3 = st.columns([3, 2, 1])
        c1.markdown(f"**{r.get('display_name') or '—'}**")
        c1.caption(r["email"])
        role, ae_id, regs = _role_inputs(r["email"], r, c2)
        c3.write("")
        if c3.button("Approve", key=f"ok_{r['email']}", type="primary",
                     width="stretch"):
            res = _apply(r["email"], role, ae_id, regs)
            (st.success if res.ok else st.error)(res.message)
            st.rerun()
        if c3.button("Reject", key=f"no_{r['email']}", width="stretch"):
            res = acc.set_status(store, r["email"], DISABLED)
            st.info(res.message)
            st.rerun()

st.subheader("All accounts")
if records:
    t = pd.DataFrame(records)
    now = pd.Timestamp.now().timestamp()
    t["locked"] = [idy.is_locked(x, now)[0] for x in t.get("locked_until", "")]
    # Rendered through timefmt so rows written before these became readable
    # still show a date rather than a ten-digit number.
    for c in ("created_at", "approved_at"):
        if c in t.columns:
            t[c] = [tf.human(x) for x in t[c]]
    t["role"] = ["Sales manager" if is_manager(x) else "AE" for x in records]
    cols = [c for c in ("email", "display_name", "role", "ae_id", "regions", "status",
                        "created_at", "approved_at",
                        "failed_attempts", "locked") if c in t.columns]
    st.dataframe(t[cols], hide_index=True, width="stretch")

    st.subheader("Manage an account")
    who = st.selectbox("Account", sorted(r["email"] for r in records))
    a, b, c, d = st.columns(4)
    if a.button("Unlock", width="stretch"):
        st.info(acc.unlock(store, who).message); st.rerun()
    if b.button("Sign out everywhere", width="stretch",
                help="Bumps the session nonce, so every token already issued to "
                     "this person stops working immediately."):
        st.info(acc.revoke_sessions(store, who).message); st.rerun()
    if c.button("Disable", width="stretch"):
        st.info(acc.set_status(store, who, DISABLED).message); st.rerun()
    if d.button("Re-enable", width="stretch"):
        st.info(acc.set_status(store, who, APPROVED).message); st.rerun()

    # A container, not a form: the region picker appears when the role switch
    # changes, which needs the rerun a form would hold back until Save.
    with st.container(border=True):
        st.markdown("**Role and territory**")
        st.caption("Changing someone to a sales manager clears their territory; "
                   "changing them to an AE clears their regions.")
        role, ae_id, regs = _role_inputs(f"edit_{who}", store.get(who) or {}, st)
        if st.button("Save", type="primary", key=f"save_{who}"):
            res = _apply(who, role, ae_id, regs)
            (st.success if res.ok else st.error)(res.message)
            if res.ok:
                st.rerun()
else:
    st.caption("No accounts yet.")
