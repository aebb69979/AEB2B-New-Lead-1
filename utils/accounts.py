"""Signup, login, approval. The flows, on top of `identity` primitives and a store.

Every function here takes a store, so all of it is testable against
MemoryIdentityStore without Google credentials or a network.

Two rules the UI must not undo:

  * Every failure a stranger can reach returns the SAME message. Wrong password,
    no such account, not yet approved, disabled -- all `LOGIN_FAILED_MSG`. The
    caller gets a `reason` for logging, never for display.
  * `ae_id` comes from the stored record, never from the caller. It is the only
    thing standing between one AE and another's leads.
"""
from __future__ import annotations

import time

from . import identity as idy
from . import timefmt
from .store import APPROVED, DISABLED, PENDING, IdentityStore, blank_record


class Result:
    __slots__ = ("ok", "message", "reason", "session", "record")

    def __init__(self, ok: bool, message: str = "", reason: str = "",
                 session: str | None = None, record: dict | None = None):
        self.ok, self.message, self.reason = ok, message, reason
        self.session, self.record = session, record

    def __repr__(self):
        return f"Result(ok={self.ok}, reason={self.reason!r})"


# --------------------------------------------------------------------------
def sign_up(store: IdentityStore, email: str, password: str, pepper: str,
            display_name: str = "", domain: str = idy.EMAIL_DOMAIN,
            admin_emails: set | frozenset = frozenset()) -> Result:
    """Create a pending account. Approval is a separate, human step.

    Except for the first one. The Admin page is the only way to approve, and it
    is only reachable once signed in, so without this the very first account can
    never be approved and the app is unusable on a fresh deployment. An address
    listed in `admin_emails` comes from the secrets store, which only the
    operator controls, so it is already trusted -- it self-approves.
    """
    e = idy.normalize_email(email)
    if not e:
        return Result(False, "Enter a valid email address.", "bad_email")
    if not idy.is_corporate(e, domain):
        # The app URL is public. Without this, anyone on the internet can fill
        # the approval queue and the admin spends their time deleting strangers.
        return Result(False, f"Use your @{domain} work email.", "wrong_domain")
    problem = idy.password_problem(password)
    if problem:
        return Result(False, problem, "weak_password")
    if store.get(e):
        # Deliberately the same wording as success. Otherwise signup becomes a
        # way to test whether an address exists.
        return Result(True, "Request received. An administrator will review it.",
                      "already_exists")
    rec = blank_record(e, idy.hash_password(password, pepper),
                       idy.new_nonce(), display_name.strip())
    if e in {str(a).strip().lower() for a in admin_emails}:
        rec["status"] = APPROVED
        rec["approved_at"] = timefmt.text()
        store.create(rec)
        return Result(True, "Administrator account created. You can sign in now.",
                      "created_admin")
    store.create(rec)
    return Result(True, "Request received. An administrator will review it.", "created")


def log_in(store: IdentityStore, email: str, password: str, pepper: str,
           session_secret: str, now: float | None = None) -> Result:
    """Verify credentials and issue a session token.

    Note the order: lockout is checked before the password is verified, so a
    locked account costs an attacker nothing to probe and gives them nothing.
    """
    now = now if now is not None else time.time()
    e = idy.normalize_email(email)
    if not e:
        return Result(False, idy.LOGIN_FAILED_MSG, "bad_email")

    rec = store.get(e)
    if not rec:
        # Spend roughly the time a real verify would, so response timing does
        # not reveal which addresses exist.
        idy.verify_password(password or "x", _DUMMY_HASH, pepper)
        return Result(False, idy.LOGIN_FAILED_MSG, "no_account")

    locked, remaining = idy.is_locked(rec.get("locked_until"), now)
    if locked:
        mins = max(1, remaining // 60)
        return Result(False, f"Too many attempts. Try again in {mins} minute(s).",
                      "locked")

    if not idy.verify_password(password or "", rec.get("password_hash", ""), pepper):
        n, until = idy.next_lockout(rec.get("failed_attempts"), now)
        store.update(e, failed_attempts=n, locked_until=(f"{until:.0f}" if until else ""))
        return Result(False, idy.LOGIN_FAILED_MSG, "bad_password")

    status = str(rec.get("status", "")).strip().lower()
    if status == PENDING:
        return Result(False, "Your account is awaiting approval.", "pending")
    if status != APPROVED:
        return Result(False, idy.LOGIN_FAILED_MSG, "disabled")
    if str(rec.get("failed_attempts", "0")) != "0":
        store.update(e, failed_attempts=0, locked_until="")
    token = idy.issue_session(e, str(rec.get("session_nonce", "")), session_secret, now=now)
    return Result(True, "", "ok", session=token, record=rec)


def session_account(store: IdentityStore, token: str | None, session_secret: str,
                    now: float | None = None) -> dict | None:
    """The account a session token belongs to, or None.

    Re-checks the record every time rather than trusting the token alone, so
    approval being revoked, an account being disabled, or the nonce being bumped
    all take effect immediately instead of at expiry.
    """
    claims = idy.read_session(token, session_secret, now)
    if not claims:
        return None
    rec = store.get(str(claims.get("email") or ""))
    if not rec:
        return None
    if str(rec.get("status", "")).strip().lower() != APPROVED:
        return None
    if str(rec.get("session_nonce", "")) != str(claims.get("nonce")):
        return None                                     # revoked
    # A missing ae_id is deliberately NOT a refusal. An administrator is not an
    # AE and has no territory; `leads_for` returns nothing for an empty ae_id, so
    # the page is simply empty rather than the sign-in being impossible.
    return rec


# --------------------------------------------------------------------------
# admin
# --------------------------------------------------------------------------
def approve(store: IdentityStore, email: str, ae_id: str) -> Result:
    e = idy.normalize_email(email)
    if not e or not store.get(e):
        return Result(False, "No such account.", "no_account")
    if not str(ae_id).strip():
        return Result(False, "Assign a territory (ae_id) when approving.", "no_ae_id")
    store.update(e, status=APPROVED, ae_id=str(ae_id).strip(),
                 approved_at=timefmt.text(), failed_attempts=0, locked_until="")
    return Result(True, f"Approved {e} as {ae_id}.", "approved")


def set_status(store: IdentityStore, email: str, status: str) -> Result:
    e = idy.normalize_email(email)
    if not e or not store.get(e):
        return Result(False, "No such account.", "no_account")
    if status not in (PENDING, APPROVED, DISABLED):
        return Result(False, "Unknown status.", "bad_status")
    store.update(e, status=status)
    return Result(True, f"{e} is now {status}.", "status_set")


def revoke_sessions(store: IdentityStore, email: str) -> Result:
    """Log someone out of every device, now. Bumping the nonce invalidates every
    token already issued, because `session_account` compares it on each request."""
    e = idy.normalize_email(email)
    if not e or not store.get(e):
        return Result(False, "No such account.", "no_account")
    store.update(e, session_nonce=idy.new_nonce())
    return Result(True, f"Signed {e} out everywhere.", "revoked")


def unlock(store: IdentityStore, email: str) -> Result:
    e = idy.normalize_email(email)
    if not e or not store.get(e):
        return Result(False, "No such account.", "no_account")
    store.update(e, failed_attempts=0, locked_until="")
    return Result(True, f"Unlocked {e}.", "unlocked")


# A real bcrypt hash of a value nobody knows, used only to keep the timing of a
# "no such account" response similar to a real verification.
_DUMMY_HASH = "$2b$12$K1YkJ7l1s1Wb3Zr2QeUu8uS7mQ0Zq1J8m2wG7wX4kq3nQ1a2b3c4e"
