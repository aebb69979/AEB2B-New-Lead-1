"""Preflight: does the Google setup actually work?

    python check_setup.py <service-account-key.json> <drive_folder_id> [identity_sheet_id]

Run this after steps 2-4 of the setup guide and before deploying. Every failure
it reports is one you would otherwise meet as a blank page at step 8, where the
cause is three services away from the symptom.

It only reads. Nothing is created, changed or shared.
"""
from __future__ import annotations

import functools
print = functools.partial(print, flush=True)  # progress must show when piped

import json
import re
import sys

OK, BAD, WARN = "  [ ok ]", "  [FAIL]", "  [warn]"
SNAPSHOT_RE = re.compile(r"^m(\d+)_leads_(\d{4}-\d{2}-\d{2})\.csv$")
FOLDER_MIME = "application/vnd.google-apps.folder"
SHEET_MIME = "application/vnd.google-apps.spreadsheet"


def main(key_path: str, folder_id: str, sheet_id: str | None = None) -> int:
    try:
        info = json.loads(open(key_path, encoding="utf-8").read())
    except (OSError, json.JSONDecodeError) as e:
        print(f"{BAD} cannot read the key file: {e}")
        return 1

    robot = info.get("client_email", "?")
    print(f"\nservice account : {robot}")
    print(f"project         : {info.get('project_id', '?')}")
    for f in ("private_key", "client_email", "token_uri"):
        if not info.get(f):
            print(f"{BAD} key JSON is missing {f!r} — re-download it")
            return 1

    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
    from utils.leads import prefer_ipv4_if_broken
    if prefer_ipv4_if_broken():
        print(f"{WARN} IPv6 is advertised but unreachable here — using IPv4.")
        print("       Without this the first Google call stalls about 75 seconds")
        print("       and looks like a hang.")

    print("  … authenticating", flush=True)
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2.service_account import Credentials
    creds = Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/drive.readonly",
                      "https://www.googleapis.com/auth/spreadsheets"])
    sess = AuthorizedSession(creds)
    failures = 0

    # ---- the folder itself -------------------------------------------------
    print("\n--- Drive folder ---")
    r = sess.get(f"https://www.googleapis.com/drive/v3/files/{folder_id}",
                 params={"fields": "id,name,mimeType", "supportsAllDrives": True})
    if r.status_code == 404:
        print(f"{BAD} folder not found, or not shared with {robot}")
        print("       Share the folder with that address as Viewer.")
        return 1
    if r.status_code == 403:
        print(f"{BAD} 403 — is the Drive API enabled in this project?")
        print(f"       {r.text[:160]}")
        return 1
    r.raise_for_status()
    meta = r.json()
    print(f"{OK} reachable: {meta['name']!r}")
    if meta.get("mimeType") != FOLDER_MIME:
        print(f"{BAD} that id is not a folder — it is {meta.get('mimeType')}")
        return 1

    # ---- what is in it -----------------------------------------------------
    q = (f"'{folder_id}' in parents and trashed = false")
    r = sess.get("https://www.googleapis.com/drive/v3/files",
                 params={"q": q, "fields": "files(id,name,mimeType,size)",
                         "pageSize": 200, "supportsAllDrives": True,
                         "includeItemsFromAllDrives": True})
    r.raise_for_status()
    files = r.json().get("files", [])
    snaps = [f for f in files if SNAPSHOT_RE.match(f["name"])]
    folders = [f for f in files if f["mimeType"] == FOLDER_MIME]
    others = [f for f in files if f not in snaps and f not in folders]

    print(f"       {len(files)} item(s) directly inside")
    if snaps:
        print(f"{OK} {len(snaps)} lead snapshot(s) the app will use:")
        for f in sorted(snaps, key=lambda x: x["name"], reverse=True):
            print(f"         {f['name']}")
    else:
        print(f"{BAD} no m<N>_leads_<date>.csv here — the app will show nothing")
        failures += 1
        if folders:
            # the most common setup error: the id points one level too high
            print(f"       This folder contains sub-folders: "
                  f"{', '.join(f['name'] for f in folders)}")
            print("       The search is NOT recursive. If the snapshots are in one of")
            print("       those, use THAT folder's id instead.")
    if others:
        print(f"       ignored (not snapshots): "
              f"{', '.join(f['name'] for f in others[:6])}")

    # a password-hash sheet inside the read-scope folder is a future accident
    ident_here = [f for f in others
                  if f["mimeType"] == SHEET_MIME and "identity" in f["name"].lower()]
    if ident_here:
        print(f"{WARN} {ident_here[0]['name']!r} is INSIDE the leads folder.")
        print("       It holds password hashes and needs Editor, while this folder")
        print("       is shared as Viewer and may later be shared with an analyst.")
        print("       Move it out — a sibling of this folder is fine.")

    # ---- the identity sheet ------------------------------------------------
    if sheet_id:
        print("\n--- identity sheet ---")
        r = sess.get(f"https://www.googleapis.com/drive/v3/files/{sheet_id}",
                     params={"fields": "id,name,mimeType,capabilities/canEdit",
                             "supportsAllDrives": True})
        if r.status_code == 404:
            print(f"{BAD} not found, or not shared with {robot}")
            failures += 1
        else:
            r.raise_for_status()
            m = r.json()
            print(f"{OK} reachable: {m['name']!r}")
            if m.get("mimeType") != SHEET_MIME:
                print(f"{BAD} not a Google Sheet — it is {m.get('mimeType')}")
                print("       A .xlsx uploaded to Drive is not the same thing;")
                print("       open it and use File > Save as Google Sheets.")
                failures += 1
            elif not m.get("capabilities", {}).get("canEdit"):
                print(f"{BAD} shared as Viewer — accounts cannot be written")
                print("       Re-share with the service account as EDITOR.")
                failures += 1
            else:
                print(f"{OK} writable — accounts can be stored")
    else:
        print(f"\n{WARN} no identity_sheet_id given; skipping that check")

    print()
    if failures:
        print(f"{BAD} {failures} problem(s) above. Fix them before deploying.")
        return 1
    print(f"{OK} Google setup looks good. Continue with step 5.")
    return 0


if len(sys.argv) < 3:
    print(__doc__)
    raise SystemExit(2)
raise SystemExit(main(*sys.argv[1:4]))
