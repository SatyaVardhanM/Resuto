"""
scripts/admin.py — Manage user access in the Google Sheet.

Usage (from project root, with core/gsheets_creds.json + SHEET_ID set):
    python scripts/admin.py list
    python scripts/admin.py approve <email>
    python scripts/admin.py reject  <email>
    python scripts/admin.py revoke  <email>
    python scripts/admin.py pending <email>     # put back into review
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.license import (list_users, set_status,
                          STATUS_APPROVED, STATUS_REJECTED,
                          STATUS_REVOKED, STATUS_PENDING)

R = "\033[0m"; G = "\033[92m"; RE = "\033[91m"; Y = "\033[93m"; B = "\033[1m"
COLOURS = {STATUS_APPROVED: G, STATUS_REJECTED: RE,
           STATUS_REVOKED: Y, STATUS_PENDING: Y}


def cmd_list():
    users = list_users()
    if not users:
        print("No registered users.")
        return
    print(f"\n{B}{'NAME':<26}{'EMAIL':<34}{'STATUS':<10}{'HOST':<20}LAST LOGIN{R}")
    print("─" * 110)
    for u in users:
        s = str(u.get("status", "") or STATUS_PENDING).lower()
        print(f"{str(u.get('full_name', ''))[:25]:<26}"
              f"{str(u.get('email', ''))[:33]:<34}"
              f"{COLOURS.get(s, '')}{s:<10}{R}"
              f"{str(u.get('last_hostname', ''))[:19]:<20}"
              f"{str(u.get('last_login', ''))[:19]}")
    print()


def cmd_set(email: str, status: str):
    if not email:
        print(f"Usage: admin.py {status} <email>")
        return
    if set_status(email, status):
        print(f"{G}{email} -> {status}{R}")
    else:
        print(f"{RE}Not found: {email}{R}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(0)
    cmd = args[0].lower()
    arg = args[1] if len(args) > 1 else ""
    actions = {
        "list":    cmd_list,
        "approve": lambda: cmd_set(arg, STATUS_APPROVED),
        "reject":  lambda: cmd_set(arg, STATUS_REJECTED),
        "revoke":  lambda: cmd_set(arg, STATUS_REVOKED),
        "pending": lambda: cmd_set(arg, STATUS_PENDING),
    }
    actions.get(cmd, lambda: print("Unknown command:", cmd))()
