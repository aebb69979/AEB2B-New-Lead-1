"""Lead app for account executives — sign in, see your leads.

    ../venv/bin/streamlit run app_ae/streamlit_app.py

Deployed to Streamlit Community Cloud behind a Cloudflare shell (web/index.html).
The app URL is public — Community Cloud's viewer restriction only understands
Google accounts and these AEs are on Outlook — so the sign-in below is the only
thing between the internet and the lead data.

Two sections. Summary is the batch at a glance with per-lead progress;
Per-company is one company at a time, in full, with the call-logging form. The
batch selector sits in the sidebar rather than on either page, because both
sections must always be looking at the same month -- otherwise an AE could log
a call against a company they were reading in a different batch.

Every write carries the ae_id from the verified session record, never from a
request parameter: an attempt logged against the wrong ae_id would be worse
than no attempt logged at all.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))

from utils import accounts as acc                      # noqa: E402
from utils import bootstrap as boot                    # noqa: E402
from utils import identity as idy
from utils import leads as L                      # noqa: E402

st.set_page_config(page_title="Leads", page_icon=":material/call:", layout="wide")

store = boot.get_store()
SESSION_KEY = "s"      # query param the Cloudflare shell replays


def _token() -> str | None:
    return st.session_state.get("session_token") or st.query_params.get(SESSION_KEY)


def _bridge(token: str | None) -> None:
    """Tell the Cloudflare shell about this session.

    The shell is first-party to its own domain, so its localStorage is not
    partitioned the way a cookie set inside this cross-origin iframe would be.
    It stores what we post here and replays it as `?s=` on the next load, which
    is what makes a sign-in survive a browser refresh.

    window.TOP, not window.parent. `st.html` is not iframed, so this runs in the
    app document -- and Community Cloud serves that document inside a wrapper
    frame of its own. window.parent is that wrapper, which would swallow the
    message; top is the shell however deep the nesting is.

    The target origin is pinned to the shell. With '*' the token goes to
    whatever page is on top, and nothing stops a third-party site framing this
    app. With no shell configured nothing is posted at all: the app still
    works opened directly, it just does not persist across a refresh.
    """
    target = boot.shell_origin()
    if not target:
        return
    payload = json.dumps({"type": "m1-session", "token": token})
    st.html(
        f"<script>try{{window.top.postMessage({payload}, {json.dumps(target)});}}"
        f"catch(e){{}}</script>",
        unsafe_allow_javascript=True,
    )


def _sign_in(token: str) -> None:
    """Put the token where both this session and the shell can find it.

    session_state alone dies on a page refresh, and the app runs in a
    cross-origin iframe where cookies are third-party and get blocked. The query
    param is what the shell copies into its own first-party localStorage and
    replays on the next load.
    """
    st.session_state["session_token"] = token
    st.query_params[SESSION_KEY] = token
    _bridge(token)


def _sign_out() -> None:
    st.session_state.pop("session_token", None)
    if SESSION_KEY in st.query_params:
        del st.query_params[SESSION_KEY]
    _bridge(None)


account = acc.session_account(store, _token(), boot.session_secret())

# --------------------------------------------------------------------------
# signed out
# --------------------------------------------------------------------------
if account is None:
    # Clear the shell's copy on EVERY signed-out render, not just from the
    # sign-out button. The button's own bridge call is followed immediately by
    # st.rerun(), which can stop the script before that element reaches the
    # browser -- so the shell kept the token, replayed it on the next refresh,
    # and the still-valid token signed the AE straight back in. This also
    # drops tokens that expired or were revoked, which are useless to replay.
    _bridge(None)
    _, mid, _ = st.columns([1, 2, 1])
    with mid:
        st.title("Lead app")
        st.caption("Sign in with your True Corp work email.")
        if boot.is_dev():
            st.warning("Development mode — accounts are stored in a local file and "
                       "leads are read from disk. No Google credentials are configured.",
                       icon=":material/construction:")

        tab_in, tab_up = st.tabs(["Sign in", "Request access"])

        with tab_in:
            with st.form("signin"):
                email = st.text_input("Work email", placeholder=f"name.surname@{idy.EMAIL_DOMAIN}")
                pw = st.text_input("Password", type="password")
                if st.form_submit_button("Sign in", type="primary", width="stretch"):
                    r = acc.log_in(store, email, pw, boot.pepper(), boot.session_secret())
                    if r.ok:
                        _sign_in(r.session)
                        st.rerun()
                    else:
                        st.error(r.message)

        with tab_up:
            st.caption("An administrator approves each request and assigns your "
                       "territory before you can sign in.")
            with st.form("signup"):
                e2 = st.text_input("Work email", placeholder=f"name.surname@{idy.EMAIL_DOMAIN}",
                                   key="su_email")
                n2 = st.text_input("Your name", key="su_name")
                p2 = st.text_input("Choose a password", type="password", key="su_pw",
                                   help=f"At least {idy.MIN_PASSWORD_LEN} characters. "
                                        "Please do not reuse a password from another system.")
                if st.form_submit_button("Request access", width="stretch"):
                    r = acc.sign_up(store, e2, p2, boot.pepper(), display_name=n2,
                                    admin_emails=boot.admin_emails())
                    (st.success if r.ok else st.error)(r.message)
    st.stop()

# --------------------------------------------------------------------------
# signed in
# --------------------------------------------------------------------------
st.session_state["account"] = account
_bridge(_token())      # refresh the shell's copy
is_admin = str(account["email"]).lower() in boot.admin_emails()

pages = {
    "Summary": [st.Page("app_pages/my_leads.py", title="My leads",
                        icon=":material/list:", default=True)],
    "Per-company": [st.Page("app_pages/per_company.py", title="Company view",
                            icon=":material/apartment:")],
}
if is_admin:
    pages["Admin"] = [st.Page("app_pages/admin.py", title="Accounts",
                              icon=":material/admin_panel_settings:")]

with st.sidebar:
    st.subheader(account.get("display_name") or account["email"])
    _ids = L.parse_ae_ids(account.get("ae_id"))
    st.caption("Territory " + (", ".join(_ids) if _ids else "— none assigned"))
    if is_admin:
        st.caption(":material/shield: Administrator")

    # Newest first, and the newest is the default. Earlier months stay available
    # so a batch does not disappear from under an AE the moment the next one is
    # published -- unfinished work would go with it.
    _months = boot.get_months()
    if _months:
        _labels = boot.month_options(_months)
        _ids_m = [m["id"] for m in _months]
        _prev = st.session_state.get("picked_month")
        _default = _prev if _prev in _ids_m else _ids_m[0]
        _pick = st.selectbox("Batch", _ids_m, index=_ids_m.index(_default),
                             format_func=lambda i: _labels[i], key="month_select")
        # `_prev is not None` matters: on the first run of a session there is no
        # previous month, and treating that as a change would reset the position
        # and strip `?lead=` from the URL -- which is exactly the case a shared
        # or reloaded deep link arrives in.
        if _prev is not None and _pick != _prev:
            # A lead_id belongs to one batch; carrying the position across would
            # land on the wrong company or on nothing at all.
            st.session_state["picked_month"] = _pick
            st.session_state["lead_ix"] = 0
            st.session_state.pop("lead_jump", None)
            st.session_state.pop("_lead_deeplinked", None)
            st.query_params.pop("lead", None)
        st.session_state["picked_month"] = _pick

    if st.button("Sign out", icon=":material/logout:", width="stretch"):
        _sign_out()
        st.rerun()

nav = st.navigation(pages)
nav.run()
