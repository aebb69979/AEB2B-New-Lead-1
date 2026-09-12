"""Authentication primitives. No Streamlit, no Google — pure functions, so the
security-critical parts can be tested without credentials or a browser.

The app URL is public (Streamlit Community Cloud serves every app openly, and its
viewer restriction only understands Google accounts, which these Outlook users do
not have). So this module is the only thing between the internet and the lead
data, and four controls are load-bearing rather than nice-to-have:

  corporate domain allowlist   without it anyone can create pending accounts and
                               you moderate spam instead of approving AEs
  lockout on failed logins     a public login form is otherwise a free
                               brute-force target
  identical error text         or the form enumerates who works at the company
  bcrypt + pepper              the hash sheet lives in a personal Google Drive;
                               a pepper held outside it means a leaked sheet is
                               not enough on its own

ON PASSWORDS: AEs choose their own, so some will reuse a work password and a
breach here reaches further than this app. That is the cost of self-service
signup, and hashing properly is what contains it. Streamlit's own guidance is to
use OIDC (`st.login`) instead; that needs an app registration in the company's
Microsoft tenant, which was not available. `verify_login` is deliberately the
single entry point so swapping to OIDC later touches one function.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
import unicodedata

EMAIL_DOMAIN = "truecorp.co.th"
MIN_PASSWORD_LEN = 12
MAX_FAILED = 5
LOCKOUT_SECONDS = 15 * 60
SESSION_TTL_SECONDS = 12 * 60 * 60

# One message for every failure mode a stranger could probe. "No such account"
# and "wrong password" must be indistinguishable or the form becomes a directory.
LOGIN_FAILED_MSG = "Email or password is incorrect."

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# --------------------------------------------------------------------------
# email
# --------------------------------------------------------------------------
def normalize_email(raw: str | None) -> str | None:
    """Lowercased, trimmed, NFKC-normalised. None if it is not an email.

    Normalising matters for the allowlist: without it 'A.B@Truecorp.co.th' and
    'a.b@truecorp.co.th' are two different accounts for the same person.
    """
    if not raw:
        return None
    e = unicodedata.normalize("NFKC", str(raw)).strip().lower()
    return e if _EMAIL_RE.match(e) else None


def is_corporate(email: str | None, domain: str = EMAIL_DOMAIN) -> bool:
    e = normalize_email(email)
    return bool(e) and e.rsplit("@", 1)[1] == domain.lower()


# --------------------------------------------------------------------------
# passwords
# --------------------------------------------------------------------------
def password_problem(pw: str | None) -> str | None:
    """None if acceptable, else why not.

    Length only. Composition rules ("one uppercase, one symbol") reliably
    produce worse passwords -- people satisfy them with Password1! -- so the
    floor is length and nothing else.
    """
    if not pw:
        return "Enter a password."
    if len(pw) < MIN_PASSWORD_LEN:
        return f"Password must be at least {MIN_PASSWORD_LEN} characters."
    if pw.strip() == "":
        return "Password cannot be only spaces."
    return None


def _peppered(password: str, pepper: str) -> bytes:
    """HMAC the password with the pepper before bcrypt sees it.

    Two reasons this is not plain concatenation. bcrypt silently truncates its
    input at 72 bytes, so a long password plus a pepper could lose the pepper
    entirely; and HMAC gives a fixed 32-byte digest regardless of input length.
    Base64 keeps it to 44 printable characters, comfortably inside the limit.
    """
    digest = hmac.new(pepper.encode(), password.encode("utf-8"), hashlib.sha256).digest()
    return base64.b64encode(digest)


def hash_password(password: str, pepper: str, rounds: int = 12) -> str:
    import bcrypt
    return bcrypt.hashpw(_peppered(password, pepper), bcrypt.gensalt(rounds=rounds)).decode()


def verify_password(password: str, stored_hash: str, pepper: str) -> bool:
    import bcrypt
    if not stored_hash:
        return False
    try:
        return bcrypt.checkpw(_peppered(password, pepper), stored_hash.encode())
    except (ValueError, TypeError):
        return False


# --------------------------------------------------------------------------
# lockout
# --------------------------------------------------------------------------
def is_locked(locked_until, now: float | None = None) -> tuple[bool, int]:
    """(locked, seconds remaining). Accepts an epoch float, a numeric string, or
    an empty cell, because this comes back from a spreadsheet."""
    now = now if now is not None else time.time()
    try:
        until = float(locked_until or 0)
    except (TypeError, ValueError):
        return False, 0
    return (True, int(until - now)) if until > now else (False, 0)


def next_lockout(failed_attempts: int, now: float | None = None) -> tuple[int, float]:
    """(new failed count, locked_until). Locks once the count reaches MAX_FAILED
    and resets the counter, so the next lock needs another full run of failures."""
    now = now if now is not None else time.time()
    n = int(failed_attempts or 0) + 1
    return (0, now + LOCKOUT_SECONDS) if n >= MAX_FAILED else (n, 0.0)


# --------------------------------------------------------------------------
# sessions
# --------------------------------------------------------------------------
# Streamlit's session_state does not survive a page refresh, and the app runs
# inside a cross-origin iframe where cookies are third-party and get blocked or
# partitioned. So the session is a signed token the Cloudflare shell keeps in its
# own first-party localStorage and replays on the URL.
#
# `nonce` is copied from the account record. Bumping it there invalidates every
# token already issued to that person -- which is how you revoke someone without
# waiting for expiry.
def issue_session(email: str, nonce: str, secret: str,
                  ttl: int = SESSION_TTL_SECONDS, now: float | None = None) -> str:
    now = now if now is not None else time.time()
    payload = {"e": email, "n": nonce, "x": int(now + ttl)}
    raw = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).rstrip(b"=")
    sig = hmac.new(secret.encode(), raw, hashlib.sha256).digest()
    return f"{raw.decode()}.{base64.urlsafe_b64encode(sig).rstrip(b'=').decode()}"


def read_session(token: str | None, secret: str, now: float | None = None) -> dict | None:
    """Verified claims, or None. Never raises on malformed input -- this reads
    straight off a query string that anyone can type."""
    if not token or "." not in token:
        return None
    now = now if now is not None else time.time()
    raw, _, sig = token.partition(".")
    try:
        expect = hmac.new(secret.encode(), raw.encode(), hashlib.sha256).digest()
        given = base64.urlsafe_b64decode(sig + "=" * (-len(sig) % 4))
        if not hmac.compare_digest(expect, given):        # constant time
            return None
        claims = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    except (ValueError, TypeError, json.JSONDecodeError):
        return None
    if not isinstance(claims, dict) or int(claims.get("x", 0)) <= now:
        return None
    return {"email": claims.get("e"), "nonce": claims.get("n"), "expires": claims.get("x")}


def new_nonce() -> str:
    return secrets.token_urlsafe(8)
