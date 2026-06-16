# linkedin_bot.py
import sys as _sys, os as _os
if not getattr(_sys, "frozen", False):
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import asyncio
import os
import random
from playwright.async_api import BrowserContext
from core.profile import MY_PROFILE

try:
    from core.logger import log, log_warn, log_error, log_debug
except Exception:
    def log(m, *a): pass
    def log_warn(m, *a): pass
    def log_error(m, *a, **k): pass
    def log_debug(m, *a): pass

# Frozensets for O(1) URL-segment lookups used throughout this file
_LOGGED_IN_SEGMENTS  = frozenset({"/feed", "/mynetwork", "/jobs", "/messaging"})
_LOGGED_OUT_SEGMENTS = frozenset({"login", "checkpoint", "authwall"})


def get_chrome_profile_path() -> str:
    """
    Returns the Chrome profile path for the bot.

    Priority:
    1. custom path stored in local_settings.json → chrome_profile_path
    2. default: BotChromeProfile/ next to the exe (or project root)

    The profile keeps LinkedIn logged in between runs.
    Never share your main Chrome profile — Chrome locks profiles
    to one process, which causes launch failures.
    """
    import sys as _s, json as _j

    # 1. Check local_settings.json for custom path
    try:
        if getattr(_s, "frozen", False):
            settings_file = os.path.join(
                os.path.dirname(_s.executable), "local_settings.json")
        else:
            settings_file = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "local_settings.json")

        if os.path.exists(settings_file):
            with open(settings_file, encoding="utf-8") as _f:
                data = _j.loads(_f.read())
            custom = data.get("chrome_profile_path", "").strip()
            if custom:
                os.makedirs(custom, exist_ok=True)
                return custom
    except Exception:
        pass

    # 2. Default path next to exe / project root
    if getattr(_s, "frozen", False):
        project_dir = os.path.dirname(_s.executable)
    else:
        import sys as _sys4
        project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if not os.path.isdir(os.path.join(project_dir, "backend")):
            project_dir = os.path.dirname(os.path.abspath(_sys4.executable))

    profile_dir = os.path.join(project_dir, "BotChromeProfile")
    os.makedirs(profile_dir, exist_ok=True)
    return profile_dir


def _get_bundled_chromium() -> str:
    """
    When running as exe, Chromium is bundled inside the app.

    On Windows: lives next to the exe in data/playwright_driver/
    On Mac:     the .app bundle is READ-ONLY so we copy Chromium to
                ~/Library/Application Support/Resuto/ on
                first run, then use it from there.

    Returns the full path to the Chromium executable, or "" if not found.
    """
    import sys as _sys, glob as _glob, platform as _plat
    if not getattr(_sys, "frozen", False):
        return ""

    system = _plat.system()

    if system == "Darwin":
        # On Mac the .app bundle itself is read-only.
        # We copy the bundled Chromium to a writable location on first run.
        support_dir = os.path.join(
            os.path.expanduser("~"), "Library",
            "Application Support", "Resuto", "chromium")
        chrome_path = os.path.join(support_dir, "Chromium")

        if not os.path.exists(chrome_path):
            # First run — copy from inside the .app bundle
            # sys.executable = .../Resuto.app/Contents/MacOS/Resuto
            # bundled files   = .../Resuto.app/Contents/MacOS/app/
            bundle_base = os.path.join(
                os.path.dirname(_sys.executable), "app")
            pattern = os.path.join(
                bundle_base, "data", "playwright_driver",
                "package", ".local-browsers",
                "chromium-*", "chrome-mac",
                "Chromium.app", "Contents", "MacOS", "Chromium")
            matches = _glob.glob(pattern)
            if matches:
                import shutil as _sh
                os.makedirs(support_dir, exist_ok=True)
                _sh.copy2(matches[0], chrome_path)
                # Make it executable — Mac strips execute bits on copy
                import stat as _stat
                os.chmod(chrome_path,
                         os.stat(chrome_path).st_mode | _stat.S_IEXEC
                         | _stat.S_IXGRP | _stat.S_IXOTH)

        return chrome_path if os.path.exists(chrome_path) else ""

    elif system == "Windows":
        # Windows exe is in a writable folder — use directly
        base = os.path.dirname(_sys.executable)
        pattern = os.path.join(base, "data", "playwright_driver",
                               "package", ".local-browsers",
                               "chromium-*", "chrome-win", "chrome.exe")
        matches = _glob.glob(pattern)
        return matches[0] if matches else ""

    else:
        # Linux
        base = os.path.dirname(_sys.executable)
        pattern = os.path.join(base, "data", "playwright_driver",
                               "package", ".local-browsers",
                               "chromium-*", "chrome-linux", "chrome")
        matches = _glob.glob(pattern)
        path = matches[0] if matches else ""
        if path and os.path.exists(path):
            import stat as _stat
            os.chmod(path,
                     os.stat(path).st_mode | _stat.S_IEXEC
                     | _stat.S_IXGRP | _stat.S_IXOTH)
        return path


def get_chrome_executable():
    """Return Chrome path — bundled Chromium first, then system Chrome, then None."""
    import platform as _plat, shutil as _sh

    # In exe: use bundled Chromium first
    bundled = _get_bundled_chromium()
    if bundled and os.path.exists(bundled):
        return bundled
    system = _plat.system()
    if system == "Windows":
        username = os.getenv("USERNAME", "User")
        candidates = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            fr"C:\Users\{username}\AppData\Local\Google\Chrome\Application\chrome.exe",
        ]
    elif system == "Darwin":
        candidates = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            os.path.expanduser(
                "~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        ]
    else:
        candidates = [
            "/usr/bin/google-chrome",
            "/usr/bin/google-chrome-stable",
            "/usr/bin/chromium-browser",
            "/usr/bin/chromium",
        ]
        for name in ("google-chrome", "google-chrome-stable", "chromium"):
            found = _sh.which(name)
            if found:
                return found
    for path in candidates:
        if os.path.exists(path):
            return path
    return None


async def create_logged_in_context(playwright):
    """
    Launches Chrome with the bot's dedicated profile.

    Because the profile is dedicated to the bot, there is no need to
    close your everyday Chrome - they no longer share a profile.

    On the first ever run the profile is empty, so the bot opens the
    LinkedIn login page and waits for you to log in once. After that
    the session persists in BotChromeProfile/ and future runs start
    already logged in.

    If the browser cannot be launched at all, the bot STOPS with a
    clear message - it does NOT silently fall back to a logged-out
    window (that only causes confusion).
    """
    profile_path = get_chrome_profile_path()
    chrome_exe   = get_chrome_executable()

    # ── Clear stale Chrome lock files ────────────────────────────
    # Chrome writes a SingletonLock (and SingletonCookie / SingletonSocket)
    # to the profile folder when it opens. If the previous bot session
    # crashed or was force-killed the lock stays on disk and the next
    # launch fails with "Could not launch the browser".
    # Safe to delete: Chrome always recreates them on startup.
    for lock_name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        lock_path = os.path.join(profile_path, lock_name)
        try:
            if os.path.exists(lock_path) or os.path.islink(lock_path):
                os.unlink(lock_path)   # unlink handles symlinks on Mac/Linux
        except OSError:
            pass   # may fail on Windows if Chrome is actually still running

    launch_args = [
        "--disable-extensions",
        "--disable-blink-features=AutomationControlled",
        "--no-default-browser-check",
        "--no-first-run",
        "--disable-popup-blocking",
    ]

    print("\n   [...] Launching the bot's Chrome profile...")

    context = None
    # Prefer the real Chrome executable; fall back to bundled Chromium.
    for label, exe in (("Chrome", chrome_exe), ("Chromium", None)):
        kwargs = dict(
            user_data_dir       = profile_path,
            headless            = False,
            slow_mo             = 30,
            timeout             = 30000,
            args                = launch_args,
            ignore_default_args = ["--enable-automation", "--no-sandbox"],
        )
        if exe:
            kwargs["executable_path"] = exe
        try:
            context = await playwright.chromium.launch_persistent_context(**kwargs)
            log("Browser launched: %s" % label)
            print(f"   [OK] {label} launched with the bot profile.")
            break
        except Exception:
            print(f"   [WARN]  {label} not available, trying next option...")

    if context is None:
        log_error("Could not launch browser — all options failed")
        raise RuntimeError(
            "Could not launch the browser.\n"
            "      * If a previous bot run is still open, close it.\n"
            "      * Make sure Chrome is installed.\n"
            "      Then run again."
        )

    page = await _setup_page(context)

    # Check whether this profile is already logged into LinkedIn.
    print("   [CHECK] Checking LinkedIn session...")
    try:
        await page.goto("https://www.linkedin.com/feed/",
                         wait_until="domcontentloaded", timeout=30000)
    except Exception:
        pass

    if "login" in page.url or "/uas/" in page.url or "authwall" in page.url:
        print("\n   [AUTH] Please log into LinkedIn in the browser window.")
        print("      This is a one-time step - the bot profile will")
        print("      stay logged in for future runs.")
        print("      Waiting up to 3 minutes...")
        logged_in = False
        for _ in range(180):
            if any(seg in page.url for seg in _LOGGED_IN_SEGMENTS):
                logged_in = True
                break
            await asyncio.sleep(1)
        if logged_in:
            log("LinkedIn: logged in successfully")
            print("   [OK] Logged in - session saved for next time.\n")
        else:
            log_error("LinkedIn login timed out")
            await context.close()
            raise RuntimeError(
                "LinkedIn login was not completed in time.\n"
                "      Run again and log in when the window opens."
            )
    else:
        log("LinkedIn: already logged in")
    print("   [OK] Already logged into LinkedIn.\n")

    return context, context, page


async def _setup_page(context):
    # Give Chrome a moment to finish restoring the previous session tabs
    await asyncio.sleep(2)
    pages = context.pages

    # Close blank tabs restored from the previous session.
    # Chrome restores all open tabs on relaunch — this produces about:blank
    # tabs before any real content loads. Close everything except the first page.
    for p in pages[1:]:
        try:
            url = p.url or ""
            if url in ("", "about:blank", "chrome://newtab/"):
                await p.close()
        except Exception:
            pass

    pages = context.pages
    if not pages:
        page = await context.new_page()
    else:
        page = pages[0]
        # If the first page is also blank, navigate away immediately
        if not page.url or page.url in ("about:blank", "chrome://newtab/"):
            pass   # linkedin goto will handle it
    await page.bring_to_front()
    return page


def get_common_field(label: str) -> str:
    label = label.lower()
    p     = MY_PROFILE
    if "first"      in label: return p["name"].split()[0]
    if "last"       in label: return p["name"].split()[-1]
    if "email"      in label: return p["email"]
    if "phone"      in label or "mobile"    in label: return p["phone"]
    if "city"       in label or "location"  in label: return p["location"]
    if "linkedin"   in label: return p["linkedin"]
    if "github"     in label: return p["github"]
    if "salary"     in label: return "80000"
    if "experience" in label or "years"     in label: return "3"
    if "website"    in label or "portfolio" in label: return p["linkedin"]
    return ""


async def _human_pause(min_ms=300, max_ms=800):
    await asyncio.sleep(random.uniform(min_ms / 1000, max_ms / 1000))


async def linkedin_apply(context: BrowserContext, job: dict, pdf_path: str) -> tuple:
    pages = context.pages
    page = pages[0] if pages else None
    
    if not page:
        return (False, {"failure_reason": "No active page"})
    
    if job.get("easy_apply"):
        return await _easy_apply_same_page(page, job, pdf_path)
    else:
        return await _external_apply_same_page(page, job, pdf_path)


# -- ROBUST MODAL FINDER -----------------------------------------

async def _find_easy_apply_modal(page, max_wait_seconds=10):
    """
    Robust modal detection - tries many selectors and verifies modal is real.
    Returns (modal_element, selector_used) or (None, None).
    """
    print(f"   [...] Searching for Easy Apply modal (up to {max_wait_seconds}s)...")
    
    # Comprehensive list of modal selectors LinkedIn uses
    selectors = [
        # Specific Easy Apply selectors
        'div.jobs-easy-apply-modal',
        '.jobs-easy-apply-modal',
        'div[data-test-modal][role="dialog"]',
        'div.artdeco-modal[role="dialog"]',
        
        # By aria-label
        'div[aria-labelledby*="easy-apply"]',
        'div[aria-labelledby*="apply"]',
        'div[aria-label*="Apply"]',
        'div[aria-label*="apply"]',
        
        # Generic dialog/modal
        'div[role="dialog"]',
        '.artdeco-modal',
        
        # Content classes
        '.jobs-easy-apply-content',
        'div.artdeco-modal__content',
        
        # By data attributes
        'div[data-test-modal]',
        'div[data-modal]',
    ]
    
    # Poll for up to max_wait_seconds
    for attempt in range(max_wait_seconds * 2):  # Check every 500ms
        for selector in selectors:
            try:
                elements = await page.query_selector_all(selector)
                for el in elements:
                    if not el:
                        continue
                    
                    # Check if visible
                    is_visible = await el.is_visible()
                    if not is_visible:
                        continue
                    
                    # Check if it has meaningful content (not empty)
                    text = await el.inner_text()
                    if not text or len(text.strip()) < 10:
                        continue
                    
                    # Verify it looks like an apply modal (has apply-related text)
                    text_lower = text.lower()
                    if any(kw in text_lower for kw in [
                        "apply", "submit", "next", "review", "contact info",
                        "resume", "additional questions", "phone", "email"
                    ]):
                        print(f"   [OK] Modal found via: {selector}")
                        print(f"   [>>] Preview: {text[:100].strip()}...")
                        return (el, selector)
            except Exception:
                continue
        
        # Wait a bit and try again
        await asyncio.sleep(0.5)
        if attempt % 4 == 0 and attempt > 0:
            print(f"   [...] Still searching... ({attempt // 2}s)")
    
    return (None, None)


# -- Easy Apply --------------------------------------------------

async def _easy_apply_same_page(page, job: dict, pdf_path: str) -> tuple:
    debug_info = {
        "easy_apply_detected": True,
        "easy_apply_button_found": False,
        "button_selector_used": "",
        "button_text": "",
        "modal_opened": False,
        "modal_selector_used": "",
        "steps_completed": 0,
        "fields_filled": 0,
        "resume_uploaded": False,
        "submit_button_found": False,
        "submit_selector_used": "",
        "submission_confirmed": False,
        "error_messages": "",
        "failure_step": 0,
        "failure_reason": "",
        "screenshot_path": "",
    }

    try:
        print("   [!] Easy Apply: Looking for button...")
        
        # Check if already applied
        already_selectors = [
            ".artdeco-inline-feedback--success",
            "button:has-text('Applied')",
            ".jobs-apply-button--applied",
        ]
        for selector in already_selectors:
            if await page.query_selector(selector):
                print("   [WARN]  Already applied")
                debug_info["failure_reason"] = "Already applied"
                return (False, debug_info)

        # Find Easy Apply button
        easy_apply_selectors = [
            'button.jobs-apply-button:has-text("Easy Apply")',
            'button[aria-label*="Easy Apply"]',
            'button:has-text("Easy Apply")',
            '.jobs-apply-button:has-text("Easy Apply")',
        ]

        easy_apply_btn = None
        for selector in easy_apply_selectors:
            try:
                easy_apply_btn = await page.query_selector(selector)
                if easy_apply_btn and await easy_apply_btn.is_visible():
                    btn_text = await easy_apply_btn.inner_text()
                    debug_info["easy_apply_button_found"] = True
                    debug_info["button_selector_used"] = selector
                    debug_info["button_text"] = btn_text
                    print(f"   [OK] Found: '{btn_text}'")
                    break
            except Exception:
                continue

        if not easy_apply_btn:
            debug_info["failure_reason"] = "Button not found"
            print("   [ERR] No Easy Apply button")
            screenshot_path = await _save_screenshot(page, "no_button", job)
            debug_info["screenshot_path"] = screenshot_path
            return (False, debug_info)

        # Click button
        await easy_apply_btn.scroll_into_view_if_needed()
        await _human_pause(500, 1000)
        
        print("   [MOUSE]  Clicking Easy Apply...")
        await easy_apply_btn.click()
        
        # Wait a moment for animation
        await asyncio.sleep(3)

        # ROBUST MODAL DETECTION
        modal, modal_selector = await _find_easy_apply_modal(page, max_wait_seconds=15)

        if not modal:
            debug_info["failure_reason"] = "Modal not found after click"
            print("   [ERR] Modal didn't open or couldn't be detected")
            print("   [IMG] Saving screenshot for debugging...")
            screenshot_path = await _save_screenshot(page, "no_modal", job)
            debug_info["screenshot_path"] = screenshot_path
            
            # Print all visible dialogs for debugging
            try:
                all_dialogs = await page.query_selector_all('[role="dialog"], .artdeco-modal, div[data-test-modal]')
                print(f"   [>>] Found {len(all_dialogs)} possible dialog elements on page:")
                for i, dlg in enumerate(all_dialogs[:5]):
                    try:
                        visible = await dlg.is_visible()
                        text = (await dlg.inner_text())[:80] if visible else "(hidden)"
                        cls = await dlg.get_attribute("class") or ""
                        print(f"      [{i+1}] visible={visible} class='{cls[:50]}' text='{text}'")
                    except Exception:
                        pass
            except Exception:
                pass
            
            return (False, debug_info)

        debug_info["modal_opened"] = True
        debug_info["modal_selector_used"] = modal_selector

        # Wait for modal content to fully render
        await asyncio.sleep(2)

        # -- HUMAN-IN-THE-LOOP: hand control to the user ---------
        # The bot has opened Easy Apply and loaded the tailored
        # resume path. It now STOPS and waits for you to review,
        # fill, and submit the application yourself. Nothing is
        # auto-filled and nothing is auto-submitted.
        print("\n" + "=" * 58)
        print("   [WAIT]   YOUR TURN -- Easy Apply is open in the browser")
        print("=" * 58)
        print(f"   Job    : {job.get('title')} @ {job.get('company')}")
        print(f"   Resume : {pdf_path}")
        print("        (upload this file when the form asks for a resume)")
        print()
        print("   [>>] Review and submit the application yourself in the")
        print("      browser window. The bot will wait here.")
        print()
        print("   When you're done, type one of these and press Enter:")
        print("      [d] done      - application submitted, go to next job")
        print("      [s] skip      - didn't apply, go to next job")
        print("      [q] quit      - stop the bot entirely")
        print("=" * 58)

        loop = asyncio.get_event_loop()
        while True:
            answer = (await loop.run_in_executor(
                None, input, "   Your choice [d / s / q]: "
            )).strip().lower()

            if answer in ("d", "done"):
                debug_info["submission_confirmed"] = True
                debug_info["steps_completed"] = 1
                debug_info["failure_reason"] = ""
                print("   [OK] Marked as applied. Moving to next job...\n")
                return (True, debug_info)

            elif answer in ("s", "skip"):
                debug_info["submission_confirmed"] = False
                debug_info["failure_reason"] = "User skipped at confirmation"
                print("   [SKIP]  Skipped. Moving to next job...\n")
                return (False, debug_info)

            elif answer in ("q", "quit"):
                debug_info["submission_confirmed"] = False
                debug_info["failure_reason"] = "User quit at confirmation"
                print("   [>>] Stopping the bot.\n")
                raise KeyboardInterrupt("User chose to quit")

            else:
                print("   [WARN]  Please type 'd', 's', or 'q'.")

    except KeyboardInterrupt:
        # User chose 'quit' at the confirmation prompt -- re-raise so
        # main.py can stop the run cleanly.
        raise

    except Exception as e:
        debug_info["failure_reason"] = str(e)
        print(f"   [ERR] Error: {e}")
        screenshot_path = await _save_screenshot(page, "error", job)
        debug_info["screenshot_path"] = screenshot_path

    return (False, debug_info)


async def _external_apply_same_page(page, job: dict, pdf_path: str) -> tuple:
    debug_info = {"easy_apply_detected": False, "failure_reason": "External"}
    print("   [WEB] External job")
    return (True, debug_info)


async def _save_screenshot(page, reason: str, job: dict) -> str:
    try:
        from core.settings import out_path as _out
        debug_dir = _out("debug")
        os.makedirs(debug_dir, exist_ok=True)
        clean = "".join(c for c in job.get("company","")[:20]
                        if c.isalnum() or c in (" ", "-", "_")).strip()
        path = os.path.join(debug_dir, "%s_%s.png" % (reason, clean))
        await page.screenshot(path=path, full_page=True)
        log("Screenshot saved: %s" % path)
        print("   [IMG] %s" % path)
        return path
    except Exception as e:
        log_warn("Screenshot failed: %s" % e)
        return ""


# -- Phase 3: guided applying ------------------------------------

# Optional queue for GUI-driven input (ReviewWindow sets this)
_GUI_INPUT_QUEUE = None

async def _ask_user_choice(prompt: str, valid: tuple) -> str:
    """
    Asks the user for input without blocking the async loop.
    If _GUI_INPUT_QUEUE is set, reads from it (ReviewWindow mode).
    Otherwise reads from stdin (normal bot subprocess mode).
    """
    loop = asyncio.get_event_loop()

    if _GUI_INPUT_QUEUE is not None:
        # ReviewWindow mode — wait for GUI to put an answer in the queue
        print(prompt, flush=True)
        try:
            answer = await loop.run_in_executor(
                None, _GUI_INPUT_QUEUE.get, True, 120)  # 2min timeout
            return answer.strip().lower() if answer.strip().lower() in valid else valid[-1]
        except Exception:
            return valid[-1]  # timeout or closed — safe quit

    while True:
        print(prompt, flush=True)
        try:
            answer = (await loop.run_in_executor(None, input, "")).strip().lower()
        except (EOFError, OSError):
            return valid[0]
        if answer in valid:
            return answer
        print(f"   [WARN]  Please type one of: {', '.join(valid)}")

async def minimize_browser(context) -> None:
    """
    Minimize the browser window via CDP instead of closing it.
    The browser stays alive for the next run.
    Called after all roles are processed.
    """
    try:
        for page in context.pages:
            try:
                cdp = await context.new_cdp_session(page)
                # Get the windowId for this page
                target_info = await cdp.send("Target.getTargetInfo", {})
                target_id = target_info.get("targetInfo", {}).get("targetId", "")
                if target_id:
                    win = await cdp.send("Browser.getWindowForTarget",
                                         {"targetId": target_id})
                    win_id = win.get("windowId")
                    if win_id:
                        await cdp.send("Browser.setWindowBounds", {
                            "windowId": win_id,
                            "bounds": {"windowState": "minimized"}
                        })
                        await cdp.detach()
                        return   # one page is enough
                await cdp.detach()
            except Exception:
                continue
    except Exception:
        pass   # graceful — if CDP fails, browser just stays open


async def guided_apply_session(context: BrowserContext, jobs: list,
                               reapply: bool = False) -> dict:
    """
    Phase 3: opens each ready job in the browser, one at a time, and
    waits for the user to apply and confirm. The browser window is
    kept open the whole time and is NOT closed between jobs.

    'jobs' is a list of dicts from tracker.get_jobs_ready_to_apply()
    (or get_reapply_candidates() when reapply=True). Each needs at
    least: id, company, job_title, job_url, and pdf_path (pdf_path
    may be empty for re-apply candidates).

    Updates the database per job via tracker.mark_job_outcome.
    Returns a summary dict.
    """
    from db import tracker

    total = len(jobs)
    if total == 0:
        return {"applied": 0, "skipped": 0, "total": 0}

    header = "Re-application review" if reapply else "Phase 3 -- Apply to matched jobs"
    print(f"\n{'='*60}")
    print(f"[>>] {header}")
    print(f"   {total} job(s) to review. The browser stays open throughout.")
    print(f"{'='*60}")

    page = await _setup_page(context)

    applied = 0
    skipped = 0

    for i, job in enumerate(jobs, start=1):
        company = job.get("company", "Unknown company")
        title   = job.get("job_title", "Unknown role")
        url     = job.get("job_url", "")
        pdf     = job.get("pdf_path", "") or ""

        print(f"\n{'-'*60}")
        print(f"   [{i}/{total}] {title} @ {company}")
        # Signal GUI: is this the last job?
        if i == total:
            print("[BOT_LAST_JOB]")
        if reapply and job.get("days_ago", -1) >= 0:
            print(f"   You applied to this {job['days_ago']} day(s) ago.")
        print(f"{'-'*60}")

        if not url:
            print("   [WARN]  No job URL on record -- skipping.")
            skipped += 1
            continue

        # Open the job in the kept-open browser
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            err_str = str(e).lower()
            # Browser was closed by user — stop the session cleanly
            if any(x in err_str for x in ("context was destroyed", "target closed",
                                           "browser has been closed", "connection closed")):
                print("\n   [OK] Browser was closed -- ending session.")
                break
            print(f"   [WARN]  Could not open the job page: {e}")
            skipped += 1
            continue

        print("\n   [WAIT]   YOUR TURN -- the job is open in the browser")
        if pdf:
            print(f"   Resume : {pdf}")
            print("        (upload this file when the form asks for a resume)")
        print("   [>>] Apply in the browser window. The bot will wait here.")
        print()
        is_last = (i == total)
        next_lbl = "finish" if is_last else "go to next"
        print("   When done, type and press Enter:")
        print(f"      [d] done   - applied, {next_lbl}")
        print(f"      [s] skip   - did not apply, {next_lbl}")
        if not is_last:
            print("      [q] quit   - stop the session")
        else:
            print("      [q] quit   - same as finish")

        choice = await _ask_user_choice(
            "   Your choice [d / s / q]: ", ("d", "s", "q", "done", "skip", "quit")
        )

        if choice in ("d", "done"):
            if reapply:
                # A re-apply is a NEW application event - log a fresh
                # 'applied' row rather than editing the old one, so the
                # history shows both applications. (Re-apply candidates
                # have no 'id' - they are keyed by job_url.)
                tracker.log_application(
                    job={
                        "company":  job.get("company", ""),
                        "title":    job.get("job_title", ""),
                        "location": job.get("location", ""),
                        "url":      job.get("job_url", ""),
                    },
                    status="applied",
                    relevance={
                        "match_score":   job.get("match_score", 0),
                        "skill_overlap": job.get("skill_overlap", 0),
                        "reason":        job.get("ai_reason", "Re-application."),
                    },
                    notes="Re-application via re-apply review",
                )
                # Clear any old 'skip for now' so it is not re-offered
                tracker.set_skip_status(job["job_url"], 1)
            else:
                # Phase 3: update the matched row to 'applied'
                tracker.mark_job_outcome(job["id"], "applied")
            applied += 1
            log("Applied: %s @ %s" % (title, company))
            print("   [OK] Marked as applied.")
        elif choice in ("s", "skip"):
            if reapply:
                # In re-apply review, a skip asks how to remember it
                print("   Skip this previously-applied job:")
                print("      [n] for now    - ask again next run")
                print("      [f] forever    - never ask again")
                sk = await _ask_user_choice(
                    "   [n / f]: ", ("n", "", "now", "forever")
                )
                tracker.set_skip_status(job["job_url"], 0 if sk in ("n", "now") else 1)
                print("   [SKIP]  Noted.")
            else:
                tracker.mark_job_outcome(job["id"], "skipped")
                print("   [SKIP]  Marked as skipped.")
            log("Skipped: %s @ %s" % (title, company))
            skipped += 1
        else:  # quit
            print("   [>>] Stopping the session. Remaining jobs are untouched.")
            break

    print(f"\n{'='*60}")
    print(f"[>>] {header} complete -- {applied} applied, {skipped} skipped")
    print(f"{'='*60}")
    return {"applied": applied, "skipped": skipped, "total": total}