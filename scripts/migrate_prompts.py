# migrate_prompts.py
from core.config import AI_MODEL
"""
One-time setup script for EXISTING users.

What it does in one run:
  1. Reads your existing resume_data.xml
  2. Asks Claude to generate prompts personalised to YOUR background
  3. Seeds the two generic prompts
  4. Stores everything in prompts.db

You run this ONCE. After that, run the main bot normally with run.bat.
Never run this again unless you want to regenerate your prompts after
updating your resume_data.xml.

NEW USERS: do not run this. Run resume_intake.py instead --
it builds your XML and generates your prompts in one go.

Usage:
  python migrate_prompts.py
"""

import os
import sys
import anthropic


def main():
    print("\n" + "=" * 60)
    print("  [FIX] One-Time Prompt Setup")
    print("  Generating your personalised AI prompts.")
    print("=" * 60)

    # -- Check API key --------------------------------------------
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("\n  [ERR] ANTHROPIC_API_KEY is not set.")
        print("     Set it before running:")
        print("     PowerShell:  $env:ANTHROPIC_API_KEY = 'your-key-here'")
        print("     CMD:         set ANTHROPIC_API_KEY=your-key-here")
        print("     Then run this script again.\n")
        sys.exit(1)

    print("\n  [KEY] Checking API key...")
    try:
        client = anthropic.Anthropic(api_key=api_key)
        client.messages.create(
            model=AI_MODEL,
            max_tokens=5,
            messages=[{"role": "user", "content": "ping"}],
        )
        print("  [OK] API key works.\n")
    except anthropic.AuthenticationError:
        print("  [ERR] API key rejected. Check the key and try again.\n")
        sys.exit(1)
    except Exception as e:
        print(f"  [ERR] API connection error: {e}\n")
        sys.exit(1)

    # -- Load profile from resume_data.xml ------------------------
    script_dir = os.path.dirname(os.path.abspath(__file__))

    # Try to import my_profile to load the XML the same way the bot does
    sys.path.insert(0, script_dir)
    try:
        from core.profile import load_profile_from_xml
        profile = load_profile_from_xml()
        print(f"  [OK] Profile loaded: {profile.get('name', 'Unknown')}\n")
    except FileNotFoundError as e:
        print(f"\n  [ERR] Could not find resume_data.xml: {e}")
        print("     Make sure resume_data.xml exists in the project folder.")
        print("     If you have not created it yet, run resume_intake.py instead.\n")
        sys.exit(1)
    except Exception as e:
        print(f"\n  [ERR] Could not load profile: {e}\n")
        sys.exit(1)

    # -- Check if prompts already exist ---------------------------
    from api.prompts import (
        all_prompts_ready, setup_all_prompts, list_prompts, DB_FILE
    )

    if all_prompts_ready():
        print("  (i)  All prompts already exist in prompts.db.")
        choice = input(
            "\n  Options:\n"
            "    [k] keep -- do nothing, already set up\n"
            "    [r] regenerate -- rebuild prompts from your current profile\n"
            "  Your choice [k / r]: "
        ).strip().lower()

        if choice not in ("r", "regenerate"):
            print("\n  [OK] Nothing changed. Your setup is already complete.\n")
            sys.exit(0)

        overwrite = True
        print()
    else:
        overwrite = False

    # -- Generate and store all prompts ---------------------------
    print("  [...] Generating your personalised prompts from your profile...")
    print("     This takes about 30 seconds...\n")

    result = setup_all_prompts(profile, client, overwrite=overwrite)

    # -- Summary --------------------------------------------------
    prompts = list_prompts()
    print(f"\n{'='*60}")
    print(f"  [OK] Setup complete.")
    print(f"     Prompts created : {result['created']}")
    print(f"     Prompts skipped : {result['skipped']} (already existed)")
    print(f"     Database        : {DB_FILE}")
    print(f"\n  Prompts stored ({len(prompts)} total):")
    for p in prompts:
        print(f"     * {p['name']}")
    print(f"\n  You can now run the main bot with run.bat.")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
