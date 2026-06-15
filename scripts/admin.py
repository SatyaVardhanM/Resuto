"""
scripts/admin.py — Manage user access via Google Sheet.

Usage (from project root):
    python scripts/admin.py list
    python scripts/admin.py revoke <machine_id>
    python scripts/admin.py approve <machine_id>
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.license import list_users, revoke_access, notify_user_revoked, \
                         _get_sheet, _find_row, _write_to_sheet, \
                         _generate_license_key, _write_cache

R="\033[0m"; G="\033[92m"; RE="\033[91m"; Y="\033[93m"; C="\033[96m"; B="\033[1m"
SC = {"approved":G,"denied":RE,"revoked":Y,"timeout":Y}

def cmd_list():
    users = list_users()
    if not users:
        print("No registered users."); return
    print(f"\n{B}{'NAME':<28}{'EMAIL':<28}{'PHONE':<18}{'STATUS':<10}{'ID':<26}REGISTERED{R}")
    print("─"*120)
    for u in users:
        s = u.get("status","")
        print(f"{u.get('name',''):<28}{u.get('email',''):<28}"
              f"{u.get('phone',''):<18}{SC.get(s,'')}{s:<10}{R}"
              f"{C}{u.get('machine_id','')[:24]}{R} "
              f"{u.get('registered_at','')[:16]}")
    print()

def cmd_revoke(mid):
    if not mid: print("Usage: admin.py revoke <machine_id>"); return
    if revoke_access(mid):
        notify_user_revoked(mid)
        print(f"{G}Revoked: {mid}{R}")
    else:
        print(f"{RE}Not found: {mid}{R}")

def cmd_approve(mid):
    if not mid: print("Usage: admin.py approve <machine_id>"); return
    try:
        ws = _get_sheet()
        row_idx, row = _find_row(ws, mid)
        if not row_idx:
            print(f"{RE}Not found: {mid}{R}"); return
        name  = row.get("name","")
        email = row.get("email","")
        phone = row.get("phone","")
        key   = _generate_license_key(mid)
        _write_to_sheet(mid, name, email, phone, key, "approved")
        print(f"{G}Approved: {mid}{R}")
    except Exception as e:
        print(f"{RE}Error: {e}{R}")

if __name__ == "__main__":
    args = sys.argv[1:]
    if not args: print(__doc__); sys.exit()
    cmd = args[0].lower()
    arg = args[1] if len(args) > 1 else ""
    {"list": lambda: cmd_list(),
     "revoke":  lambda: cmd_revoke(arg),
     "approve": lambda: cmd_approve(arg)}.get(cmd, lambda: print("Unknown:", cmd))()