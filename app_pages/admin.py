"""Approvals and account administration. Visible only to admin_emails."""
import pandas as pd
import streamlit as st

from utils import accounts as acc
from utils import bootstrap as boot
from utils import identity as idy
from utils.store import APPROVED, DISABLED, PENDING

store = boot.get_store()
st.header("Admin")

for problem in boot.setup_problems():
    st.warning(problem, icon=":material/warning:")

records = store.all_records()
pending = [r for r in records if str(r.get("status", "")).lower() == PENDING]

st.subheader(f"Pending requests ({len(pending)})")
if not pending:
    st.caption("Nothing waiting.")
for r in pending:
    with st.container(border=True):
        c1, c2, c3 = st.columns([3, 2, 1])
        c1.markdown(f"**{r.get('display_name') or '—'}**")
        c1.caption(r["email"])
        ae_id = c2.text_input(
            "Territory (ae_id)", key=f"ae_{r['email']}", placeholder="90008404",
            help="The employee number from the AE crosswalk. For someone taking "
                 "over a batch already published under a leaver's id, enter BOTH, "
                 "comma separated — 90008404, 90099999 — so they can finish the "
                 "handover and still get their own leads next month.")
        c3.write("")
        if c3.button("Approve", key=f"ok_{r['email']}", type="primary",
                     width="stretch"):
            res = acc.approve(store, r["email"], ae_id)
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
    cols = [c for c in ("email", "display_name", "ae_id", "status",
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
else:
    st.caption("No accounts yet.")
