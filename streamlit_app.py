"""Lead app for account executives — sign in, see your leads.

    ../venv/bin/streamlit run app_ae/streamlit_app.py

Deployed to Streamlit Community Cloud behind a Cloudflare shell (web/index.html).
The app URL is public — Community Cloud's viewer restriction only understands
Google accounts and these AEs are on Outlook — so the sign-in below is the only
thing between the internet and the lead data.

Step 3 of the build: identity and a read-only lead view. Nothing writes yet, on
purpose: an attempt logged against the wrong ae_id would be worse than no
attempt logged at all, so isolation gets proven before anything can be created.
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
    is what makes a sign-in survive a browser refresh. Without this the token
    lives only in the iframe's URL, which the shell rebuilds from scratch.

    Harmless when the app is opened directly: window.parent is window, and the
    message goes nowhere.
    """
    payload = json.dumps({"type": "m1-session", "token": token})
    st.html(
        f"<script>try{{window.parent.postMessage({payload}, '*');}}catch(e){{}}</script>",
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

pages = [st.Page("app_pages/my_leads.py", title="My leads", icon=":material/list:",
                 default=True)]
if is_admin:
    pages.append(st.Page("app_pages/admin.py", title="Admin",
                         icon=":material/admin_panel_settings:"))

with st.sidebar:
    st.subheader(account.get("display_name") or account["email"])
    _ids = L.parse_ae_ids(account.get("ae_id"))
    st.caption("Territory " + (", ".join(_ids) if _ids else "— none assigned"))
    if is_admin:
        st.caption(":material/shield: Administrator")
    if st.button("Sign out", icon=":material/logout:", width="stretch"):
        _sign_out()
        st.rerun()

nav = st.navigation(pages, position="top" if is_admin else "hidden")
nav.run()
