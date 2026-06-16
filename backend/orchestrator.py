# backend/orchestrator.py
import sys as _sys, os as _os
# Nuitka does not set sys.frozen — detect compiled mode by checking __file__
# In compiled mode __file__ might not exist or be the exe path
try:
    _src_root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    if _os.path.isdir(_src_root) and _src_root not in _sys.path:
        _sys.path.insert(0, _src_root)
except Exception:
    pass

import os
import sys
import argparse
import asyncio
import json

# Heavy imports wrapped so failures give a clear error message
try:
    import anthropic
except ImportError as _e:
    raise ImportError(f"anthropic not installed or bundled: {_e}") from _e

# playwright imported lazily inside main() — avoids crash on module load
async_playwright = None   # populated at runtime

# These are imported lazily inside main() and run_applications()
# to avoid module-level import failures in Nuitka standalone builds
continuous_job_search    = None  # imported in run_applications()
create_logged_in_context = None  # imported in main()
minimize_browser         = None  # imported in main()
check_job_relevance      = None  # imported in run_applications()
print_relevance_report   = None  # imported in run_applications()
# New pipeline stage modules — imported with fallback so old installs
# continue to work even if these files are missing.
try:
    from api.jd_parser import extract_jd_metadata, extract_niche_acronyms
    _HAS_JD_PARSER = True
except ImportError:
    _HAS_JD_PARSER = False
    def extract_jd_metadata(job, jd, client):
        return {"title": job.get("title",""), "skills": [],
                "seniority": "mid", "domain": "", "mission": "",
                "years_required": 0, "niche_acronyms": [],
                "raw_title": job.get("title","")}
    def extract_niche_acronyms(text): return []

try:
    from api.bullet_budget import calculate_bullet_budget
    _HAS_BULLET_BUDGET = True
except ImportError:
    _HAS_BULLET_BUDGET = False
    def calculate_bullet_budget(profile, jd_meta):
        return {"budgets": [], "total_bullets": 0, "jd_skills": []}

try:
    from core.pipeline_log import PipelineTransaction
    _HAS_PIPELINE_LOG = True
except ImportError:
    _HAS_PIPELINE_LOG = False
    class PipelineTransaction:
        def __init__(self, *a, **k): self.tx_id = "no-log"
        def stage2_done(self, *a, **k): pass
        def stage2_failed(self, *a, **k): pass
        def stage3_done(self, *a, **k): pass
        def stage4a_done(self, *a, **k): pass
        def stage4b_done(self, *a, **k): pass
        def log_summary(self): pass
from db.tracker import show_stats
from core.settings import get_settings, out_path
from core.config import AI_MODEL, MAX_JOBS_PER_RUN, DEFAULT_LOCATION
from core.logger import log, log_warn, log_error, log_section, log_debug, LOG_FILE

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

# Set at main() start — used by run_applications()
SESSION_START = ""

# Max jobs to SCAN before giving up — prevents infinite loops
# when all jobs are irrelevant. Set to job_cap × 8.
MAX_SCAN_MULTIPLIER = 8   # 8x cap — stops before LinkedIn page restarts


# -- AI Role Suggester -------------------------------------------

def suggest_roles(profile: dict) -> list:
    all_skills = []
    for items in profile.get("skills", {}).values():
        all_skills.extend(items)

    years   = profile.get("years_experience") or "unknown"
    summary = profile.get("summary", "")
    edu     = profile.get("education", [])
    edu_str = ", ".join(
        "%s at %s" % (d.get("degree", ""), d.get("school", ""))
        for d in edu if d.get("degree")
    ) or "not specified"

    prompt = (
        "You are a senior career advisor.\n"
        "Analyse this candidate and suggest ALL roles they qualify for on LinkedIn.\n"
        "Include direct, adjacent and transferable roles.\n"
        "Do NOT restrict by years of experience.\n\n"
        "Experience  : %s years\n"
        "Education   : %s\n"
        "Summary     : %s\n"
        "Skills      : %s\n\n"
        "Return ONLY a JSON array of 10-15 realistic LinkedIn job search terms,\n"
        "ordered best match first. No markdown, no explanation:\n"
        '["Role 1", "Role 2", ...]'
    ) % (years, edu_str, summary, json.dumps(all_skills))

    try:
        message = client.messages.create(
            model=AI_MODEL,
            max_tokens=1000,
            timeout=60.0,
            messages=[{"role": "user", "content": prompt}]
        )
        response_text = message.content[0].text.strip()
        if "```" in response_text:
            parts = response_text.split("```")
            response_text = parts[1] if len(parts) > 1 else parts[0]
            if response_text.startswith("json"):
                response_text = response_text[4:]
        roles = json.loads(response_text.strip())
        return [str(r).strip() for r in roles if r]
    except Exception as e:
        print("   [WARN] Claude API error suggesting roles: %s" % e)
        return [
            "Software Engineer", "Backend Developer", "Full Stack Developer",
            "Python Developer", "API Developer", "Cloud Engineer",
            "DevOps Engineer", "Data Engineer", "AI Engineer", "Web Developer",
        ]


def display_roles(roles: list) -> None:
    print("\n" + "=" * 50)
    print("[AI] AI Career Advisor -- Roles You Can Apply For")
    print("=" * 50)
    print("\nBased on your profile (no experience restrictions):\n")
    for i, role in enumerate(roles, 1):
        print("   [%2d]  %s" % (i, role))
    print()


def get_role_choice(roles: list) -> list:
    display_roles(roles)
    print("-" * 50)
    print("  [A]  ALL suggested roles")
    print("  [S]  A SPECIFIC role")
    print("-" * 50)

    while True:
        try:
            mode = input("\n[>>] Your choice (A / S): ").strip().upper()
        except (EOFError, OSError):
            return []

        if mode == "A":
            print("\n   [OK] Searching all %d roles:" % len(roles))
            for r in roles:
                print("      * %s" % r)
            return roles

        elif mode == "S":
            print("\n  * Type a NUMBER to pick from the list")
            print("  * Type a CUSTOM ROLE name")
            print("  * Press ENTER for top suggestion")
            while True:
                try:
                    choice = input("\n[>>] Your choice: ").strip()
                except (EOFError, OSError):
                    return []
                if not choice:
                    print("   [OK] Using: %s" % roles[0])
                    return [roles[0]]
                if choice.isdigit():
                    idx = int(choice) - 1
                    if 0 <= idx < len(roles):
                        print("   [OK] Selected: %s" % roles[idx])
                        return [roles[idx]]
                    print("   [ERR] Enter 1-%d" % len(roles))
                    continue
                print("   [OK] Custom: %s" % choice)
                return [choice]
        else:
            print("   [ERR] Type A or S")


# ── One-by-one mode protocol ──────────────────────────────────────
# Orchestrator prints:  "BOT_WAITING: title | company | url | score"
# UI detects this line → shows [Applied] [Skip] buttons
# User clicks → UI sends "APPLIED" or "SKIP" via stdin
# Orchestrator reads stdin → continues

BOT_WAITING_PREFIX = "BOT_WAITING:"
BOT_RESPONSE_APPLIED = "APPLIED"
BOT_RESPONSE_SKIP    = "SKIP"


def _wait_for_user_action(job: dict, loop) -> str:
    """
    Print job details to stdout so UI can show Applied/Skip buttons.
    Block reading stdin until user responds.
    Returns "APPLIED" or "SKIP".
    """


    title   = job.get("job_title", "Unknown")
    company = job.get("company",   "Unknown")
    url     = job.get("job_url",   "")
    score   = job.get("match_score", 0)

    # Signal UI to show buttons
    print("%s %s | %s | %s | %s" % (
        BOT_WAITING_PREFIX, title, company, url, score), flush=True)

    # Read user action from stdin (UI sends APPLIED or SKIP)
    try:
        action = input().strip().upper()
        if action not in (BOT_RESPONSE_APPLIED, BOT_RESPONSE_SKIP):
            action = BOT_RESPONSE_SKIP   # default to skip on invalid input
    except (EOFError, KeyboardInterrupt):
        action = BOT_RESPONSE_SKIP

    return action


def get_apply_mode() -> str:
    print("\n" + "-" * 50)
    print("[>>] Application Type")
    print("-" * 50)
    print("\n  [1]  Easy Apply only")
    print("  [2]  All jobs (Easy Apply + External)\n")
    print("-" * 50)

    while True:
        try:
            choice = input("\n[>>] Your choice (1 / 2): ").strip()
        except (EOFError, OSError):
            return None
        if choice == "1":
            print("   [OK] Easy Apply only selected")
            return "easy_apply"
        elif choice == "2":
            print("   [OK] All jobs selected")
            return "all"
        else:
            print("   [ERR] Please type 1 or 2")


# -- Application Runner ------------------------------------------

async def run_applications(
    browser, context, page,
    job_keyword, job_location,
    max_jobs, unlimited, applied_total,
    search_role, apply_mode,
    my_profile,
    global_seen: set = None,
    application_mode: str = "continuous",
    gui_filters: dict = None,
) -> int:
    import db.tracker as tracker
    from api.resume_gen import batch_generate_resumes
    from backend.browser import guided_apply_session

    # Lazy imports — module-level stubs are None; resolve here
    from backend.scraper import continuous_job_search
    from api.filter import check_job_relevance, print_relevance_report

    job_cap   = min(max_jobs if not unlimited else MAX_JOBS_PER_RUN, MAX_JOBS_PER_RUN)
    scan_cap  = job_cap * MAX_SCAN_MULTIPLIER   # FIX Issue 3: total scan limit

    log_section("PHASE 1 — Scanning: %s" % job_keyword)
    print("\n" + "=" * 60)
    print("[>>] PHASE 1 -- Scanning & filtering: '%s'" % job_keyword)
    print("   Will collect up to %d matching jobs (scan cap: %d)."
          % (job_cap, scan_cap))
    print("=" * 60)

    # Use global_seen across all role searches — prevents re-analysing same jobs
    # across different search terms in the same session
    if global_seen is None:
        global_seen = set()
    already_seen_this_run = global_seen   # shared reference, not a copy

    # Load confirmed applied/matched jobs for permanent skip
    try:
        applied_urls = tracker.load_applied_urls()
    except Exception:
        applied_urls = set()
    log("Applied jobs (permanent skip): %d | Session seen: %d" % (
        len(applied_urls), len(already_seen_this_run)))
    matched_count = 0
    scanned_count = 0

    loop = asyncio.get_event_loop()

    try:
        async for job in continuous_job_search(
            page, job_keyword, job_location, apply_mode,
            gui_filters=gui_filters,
        ):

            # FIX Issue 3: break on scan cap to prevent infinite loop
            if scanned_count >= scan_cap:
                print("\n   [WARN] Scan cap (%d) reached with only %d matched."
                      % (scan_cap, matched_count))
                print("   Stopping scan — try different roles or check your")
                print("   relevance prompt with: python scripts/update_prompts.py")
                log_warn("Scan cap hit: %d scanned, %d matched for '%s'"
                         % (scanned_count, matched_count, job_keyword))
                break

            url        = job.get("url", "").strip()
            title_short = job.get("title", "Unknown")[:50]

            # Step 1: Only skip jobs we APPLIED to (confirmed via DB)
            # Or jobs seen ALREADY IN THIS RUN (same card appearing twice)
            job_keys = tracker.job_identity_keys(url)

            # Dedup by job_id/url AND by title+company (catches re-posted same role)
            title_company_key = "%s|%s" % (
                job.get("title","").lower().strip(),
                job.get("company","").lower().strip())
            if job_keys & already_seen_this_run or title_company_key in already_seen_this_run:
                log_debug("Duplicate in this run: %s — skipping" % title_short)
                print("     <- Duplicate: %s" % title_short, flush=True)
                continue

            already_seen_this_run.add(title_company_key)

            if job_keys & applied_urls:
                log("Already applied: %s — skipping" % title_short)
                print("     <- Already applied: %s" % title_short, flush=True)
                continue

            already_seen_this_run.update(job_keys)

            # Step 2: Check LinkedIn DOM for applied status
            if job.get("already_applied"):
                log("LinkedIn shows Applied: %s — skipping" % title_short)
                print("     <- LinkedIn: already applied — skipping", flush=True)
                try:
                    db_id = tracker.save_scanning_job(job)
                    tracker.update_job_relevance(
                        db_id, job,
                        {"is_relevant": True, "match_score": 100,
                         "reason": "Already applied on LinkedIn"},
                        search_role=job_keyword, apply_mode="applied")
                except Exception:
                    pass
                continue

            # visited_count tracks total jobs seen (prevents infinite scroll)
            # scanned_count tracks jobs that went through Claude API
            # Only scanned_count matters for the cap — pre-filtered don't use API

            db_row_id = -1   # initialized before any conditional use
            job_desc = job.get("description", "")
            if not job_desc:
                job_desc = "%s at %s in %s" % (
                    job.get("title",""), job.get("company",""), job.get("location",""))
            if len(job_desc) > 3000:
                job_desc = job_desc[:3000] + "..."

            # Check if already applied on LinkedIn — skip without API call
            if job.get("already_applied"):
                log("LinkedIn: already applied to '%s' @ %s — skipping" % (
                    title_short, job.get("company","")))
                print("     <-  Already applied on LinkedIn — skipping.")
                # Mark in DB if we have a row
                if db_row_id and db_row_id > 0:
                    try:
                        tracker.update_job_relevance(
                            db_row_id, job,
                            {"is_relevant": True, "match_score": 100,
                             "reason": "Already applied on LinkedIn"},
                            search_role=job_keyword, apply_mode="applied")
                    except Exception:
                        pass
                already_seen_this_run.update(job_keys)
                continue

            apply_type = job.get("apply_type", "unknown")
            log("Apply type: %s" % apply_type)

            # Print FIRST — GUI gets signal immediately regardless of DB state
            print("\n   * %s @ %s" % (job.get("title"), job.get("company")), flush=True)

            # Write to DB — wrapped so a DB error never kills the scan loop
            job["search_role"] = job_keyword
            job["description"] = job_desc
            already_seen_this_run.update(job_keys)
            try:
                db_row_id = tracker.save_scanning_job(job)
            except Exception as _dbe:
                log_warn("save_scanning_job failed: %s" % _dbe)
                db_row_id = -1

            # Pre-filter: title must relate to search term
            def _title_relevant(job_title: str, search: str) -> bool:
                import re as _re

                # Acronym → full form expansion (applied to both search and title)
                _ACRONYMS = {
                    r"\bml\b":  "machine learning",
                    r"\bai\b":  "artificial intelligence",
                    r"\bdl\b":  "deep learning",
                    r"\bnlp\b": "natural language processing",
                    r"\bcv\b":  "computer vision",
                    r"\bui\b":  "user interface",
                    r"\bux\b":  "user experience",
                    r"\bqa\b":  "quality assurance",
                    r"\bba\b":  "business analyst",
                    r"\bfe\b":  "front end",
                    r"\bbe\b":  "back end",
                    r"\bsre\b": "site reliability",
                }

                # Semantic domain groups — SINGLE WORDS only (after split+expand)
                # Any word from the same group means search and title are related
                _DOMAINS = [
                    # AI / ML / Data Science domain
                    {"machine","learning","neural","intelligence","artificial",
                     "vision","computer","nlp","language","generative","llm",
                     "deep","reinforcement","diffusion","perception","recognition",
                     "prediction","classification","inference","embedding",
                     "transformer","bert","gpt","detection","segmentation"},
                    # Frontend domain
                    {"frontend","react","angular","vue","javascript","typescript",
                     "html","css","ui","ux","interface","experience","web","dom"},
                    # Backend / systems domain
                    {"backend","api","server","microservice","distributed",
                     "database","service","rest","grpc","kafka","redis"},
                    # Data / analytics domain
                    {"data","analytics","warehouse","pipeline","etl","spark",
                     "hadoop","tableau","reporting","visualization"},
                    # DevOps / infrastructure domain
                    {"devops","infrastructure","kubernetes","docker","terraform",
                     "ci","cd","deployment","reliability","sre","platform"},
                    # Mobile domain
                    {"mobile","ios","android","flutter","swift","kotlin"},
                ]

                def _expand(text):
                    t = text.lower()
                    # Normalize separators
                    t = t.replace("/", " ").replace("-", " ")
                    for pat, repl in _ACRONYMS.items():
                        t = _re.sub(pat, repl, t)
                    return t

                s_exp = _expand(search)
                t_exp = _expand(job_title)

                filler = {"engineer","developer","programmer","specialist",
                          "senior","sr","junior","jr","lead","principal",
                          "staff","remote","role","position","job","the",
                          "and","or","for","with","of","in","at","a","an",
                          "expert","experienced","mid","level","ii","iii"}

                s_words = set(s_exp.split()) - filler
                t_words = set(t_exp.split()) - filler

                if not s_words:
                    return True

                # Direct word match
                if s_words & t_words:
                    return True

                # Semantic domain match — search and title in same domain group
                for domain in _DOMAINS:
                    s_in = bool(s_words & domain)
                    t_in = bool(t_words & domain)
                    if s_in and t_in:
                        return True

                return False

            if not _title_relevant(job.get("title",""), job_keyword):
                log("Pre-filter: SKIP \'%s\' — title not related to search \'%s\'" % (
                    title_short, job_keyword))
                print("     <-  Pre-filter skip: title not related to search term.")
                # Update DB so GUI filters it from Recent/History
                if db_row_id and db_row_id > 0:
                    try:
                        tracker.update_job_relevance(
                            db_row_id, job,
                            {"is_relevant": False, "match_score": 0,
                             "reason": "Pre-filter: title not related to search term"},
                            search_role=job_keyword, apply_mode=apply_mode)
                    except Exception:
                        pass
                continue

            # Create transaction tracker — follows this job through all stages
            _tx = PipelineTransaction(job.get("title",""), job.get("company",""))
            job["_tx"] = _tx

            # Stage 2: Extract JD metadata (Haiku) with fallback
            log_debug("[%s] Stage 2: JD metadata extraction" % _tx.tx_id)
            try:
                jd_metadata = await loop.run_in_executor(
                    None, extract_jd_metadata, job, job_desc, client)
                _tx.stage2_done(jd_metadata)
            except Exception as _e2:
                _tx.stage2_failed(str(_e2))
                # Patch 2 fallback: regex acronyms as skill proxies
                from api.jd_parser import extract_niche_acronyms
                jd_metadata = {
                    "title": job.get("title",""), "skills": extract_niche_acronyms(job_desc)[:7],
                    "seniority":"mid","domain":"","mission":"","years_required":0,
                    "niche_acronyms": extract_niche_acronyms(job_desc),
                }
            job["_jd_metadata"] = jd_metadata

            # Stage 3: Bullet budget — pure Python, zero tokens, zero failure risk
            bullet_plan = calculate_bullet_budget(my_profile, jd_metadata)
            job["_bullet_budget"] = bullet_plan
            _tx.stage3_done(bullet_plan)
            log_debug("[%s] Stage 3: %d total bullets" % (_tx.tx_id, bullet_plan["total_bullets"]))

            scanned_count += 1   # only count jobs that hit the Claude API
            log("Checking relevance: %s @ %s" % (title_short, job.get("company","")))
            print("   [AI]  Checking relevance: %s..." % title_short)

            try:
                relevance = await asyncio.wait_for(
                    loop.run_in_executor(
                        None,
                        check_job_relevance,
                        my_profile, job, job_desc, job_keyword,
                        jd_metadata
                    ),
                    timeout=200.0
                )
                score = relevance.get("match_score", 0)
                log("Relevance done: %s score=%s%% relevant=%s" % (
                    title_short, score, relevance.get("is_relevant")))
                if "_tx" in job: job["_tx"].stage4a_done(relevance)
                print("   [AI]  Done -- score: %s%%" % score)
                # Update DB immediately with result — GUI sees it now
                tracker.update_job_relevance(
                    db_row_id, job, relevance,
                    search_role=job_keyword, apply_mode=apply_mode)

            except asyncio.TimeoutError:
                log_warn("TIMEOUT 200s: %s" % title_short)
                print("   [WARN] Relevance check timed out — skipping %s" % title_short)
                tracker.update_job_relevance(
                    db_row_id, job,
                    {"is_relevant": False, "match_score": 0,
                     "reason": "Relevance check timed out"},
                    search_role=job_keyword, apply_mode=apply_mode)
                continue

            except anthropic.AuthenticationError:
                # Auth errors stop the whole run
                raise

            except Exception as e:
                log_error("Per-job error for %s: %s" % (title_short, e))
                print("   [ERR] Error for %s: %s — skipping" % (title_short, e))
                _row = locals().get("db_row_id", -1)
                if _row and _row > 0:
                    try:
                        tracker.update_job_relevance(
                            _row, job,
                            {"is_relevant": False, "match_score": 0,
                             "reason": "Error: %s" % str(e)[:100]},
                            search_role=job_keyword, apply_mode=apply_mode)
                    except Exception:
                        pass
                continue

            print_relevance_report(relevance, job)

            if not relevance.get("is_relevant"):
                continue   # DB already updated by update_job_relevance above

            job["description"] = job_desc
            matched_count += 1
            log("Matched (%d/%d): %s" % (matched_count, job_cap, title_short))
            print("     [OK] Matched (%d/%d) -- queued for resume generation"
                  % (matched_count, job_cap))

            if matched_count >= job_cap:
                print("\n   [OK] Cap reached (%d) — stopping scan." % job_cap)
                log("Scan complete: cap reached %d/%d" % (matched_count, job_cap))
                break

    except anthropic.AuthenticationError:
        print("\n" + "=" * 60)
        print("   [STOP] Run stopped -- Claude API key failed mid-run.")
        print("=" * 60)
        return applied_total

    except Exception as e:
        err = str(e)
        if any(m in err for m in [
            "Target page, context or browser has been closed",
            "Browser closed", "Connection closed", "Session closed",
        ]):
            print("\n[>>] Browser closed during scan -- continuing with jobs found.")
        else:
            log_error("Scan loop error: %s" % e)
            print("\n[ERR] Error during scan: %s" % e)

    if matched_count == 0:
        print("\n   No new matching jobs found this run.")
        print("   Scanned %d jobs total." % scanned_count)

        # Try keyword fallbacks — generated generically from the search term
        try:
            from core.config import MIN_NEW_JOBS_BEFORE_FALLBACK
            from backend.scraper import expand_keyword
            fallbacks = expand_keyword(job_keyword)
            if fallbacks:
                log("No matches for '%s' — trying %d generated fallbacks" % (
                    job_keyword, len(fallbacks)))
                log("Fallbacks: %s" % fallbacks)
                print("\n   No matches found. Trying related search terms...")
                for fb_keyword in fallbacks:
                    log("Fallback search: '%s'" % fb_keyword)
                    print("\n   [FALLBACK] Trying: '%s'..." % fb_keyword)
                    fb_matched = await run_applications(
                        browser, context, page,
                        fb_keyword, job_location,
                        max_jobs, unlimited, applied_total,
                        fb_keyword, apply_mode, my_profile)
                    if fb_matched and fb_matched >= MIN_NEW_JOBS_BEFORE_FALLBACK:
                        log("Fallback '%s' found %d matches — stopping" % (
                            fb_keyword, fb_matched))
                        matched_count += fb_matched
                        break
                    log("Fallback '%s': %d matches" % (fb_keyword, fb_matched or 0))
            else:
                log("No fallbacks generated for '%s'" % job_keyword)
        except Exception as _fbe:
            log_warn("Keyword fallback error: %s" % _fbe)
    else:
        log("Phase 1 complete: %d matched / %d scanned" % (matched_count, scanned_count))
    print("\n[OK] Phase 1 complete -- %d job(s) matched, %d scanned."
          % (matched_count, scanned_count))

    # -- PHASE 2 + 3 -- Resume generation and applying -----------
    if matched_count == 0:
        return applied_total

    if application_mode == "one_at_a_time":
        # ── One-by-one mode ──────────────────────────────────────
        # Generate resume for ONE job at a time, pause for user action
        print("\n[>>] One-by-one mode: generating and applying one job at a time.")
        remaining = max_jobs if not unlimited else 9999
        matched_jobs = tracker.get_jobs_ready_to_apply.__wrapped__() \
            if hasattr(tracker.get_jobs_ready_to_apply, "__wrapped__") \
            else None

        # Get all matched jobs not yet actioned
        import db.tracker as _t2
        conn = _t2._connect()
        try:
            all_matched = [dict(r) for r in conn.execute(
                f"SELECT * FROM {_t2.TABLE} WHERE status = \'matched\'"
            ).fetchall()]
        except Exception:
            all_matched = []
        finally:
            conn.close()

        for job in all_matched:
            if remaining <= 0:
                print("[>>] Applied to %d job(s). Limit reached." % max_jobs)
                break

            # Generate resume for this single job
            print("\n[>>] Generating resume for: %s @ %s"
                  % (job.get("job_title","?"), job.get("company","?")))
            await loop.run_in_executor(
                None,
                batch_generate_resumes,
                my_profile,
                {"docx": out_path("resumes", "docx"),
                 "pdf":  out_path("resumes", "pdf")},
                SESSION_START,
                [job["id"]],   # generate for this job only
            )

            # Refresh job with resume paths
            conn2 = _t2._connect()
            try:
                row = conn2.execute(
                    f"SELECT * FROM {_t2.TABLE} WHERE id = ?", (job["id"],)
                ).fetchone()
                job = dict(row) if row else job
            finally:
                conn2.close()

            # Pause — wait for user to click Applied or Skip
            action = await loop.run_in_executor(
                None, _wait_for_user_action, job, loop)

            if action == BOT_RESPONSE_APPLIED:
                # Apply to this job
                result = await guided_apply_session(
                    context, [job], reapply=False)
                applied = result.get("applied", 0)
                if applied:
                    applied_total += 1
                    remaining     -= 1
                    print("[>>] Applied. Remaining: %d" % remaining)
                else:
                    print("[>>] Apply failed — job not counted.")
            else:
                # Skip — mark as skipped, count unchanged
                tracker.mark_job_outcome(job["id"], "skipped")
                print("[>>] Skipped. Remaining: %d" % remaining)

    else:
        # ── Continuous mode (original behavior) ──────────────────
        await loop.run_in_executor(
            None,
            batch_generate_resumes,
            my_profile,
            {"docx": out_path("resumes", "docx"),
             "pdf":  out_path("resumes", "pdf")},
            SESSION_START,
        )

        ready_jobs = tracker.get_jobs_ready_to_apply()
        if ready_jobs:
            print("\n" + "=" * 60)
            print("   %d job(s) have resumes ready." % len(ready_jobs))
            print("=" * 60)
            print("   Ready to start applying now? [y / n]: ", flush=True)
            answer = (await loop.run_in_executor(None, input, "")).strip().lower()
            if answer in ("y", "yes"):
                result = await guided_apply_session(context, ready_jobs, reapply=False)
                applied_total += result["applied"]
            else:
                print("   [OK] No problem -- the resumes are saved. Run again when ready.")

    return applied_total


# -- Main --------------------------------------------------------

async def run_phase2_only(job_ids: list = None, gui_mode: bool = True):
    """
    Phase 2 only mode — called from History tab Generate Resume button.
    Skips LinkedIn scanning entirely, generates resumes for specific jobs.
    """
    from datetime import datetime as _dt
    global SESSION_START
    SESSION_START = _dt.now().strftime("%Y-%m-%d %H:%M:%S")

    # Lazy imports — resolved here so module-level import never crashes
    try:
        from backend.scraper import continuous_job_search
        from backend.browser import create_logged_in_context, minimize_browser
        from api.filter   import check_job_relevance, print_relevance_report
    except ImportError as _ie:
        print(f"[!!] Import failed in main(): {_ie}", flush=True)
        log_error("Import failed in main(): %s" % _ie)
        raise
    log_section("Bot started — Phase 2 only mode")
    log("Session start: %s" % SESSION_START)
    log("Target job IDs: %s" % (job_ids or "all unprocessed matched"))

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("[ERR] ANTHROPIC_API_KEY not set")
        log_error("Phase 2 only: no API key")
        return

    # Load profile
    try:
        from core.settings import get_output_dir, get_resume_data_path
        from core.profile import load_profile_from_xml
        xml_path = get_resume_data_path()
        if not xml_path or not _os.path.exists(xml_path):
            print("[ERR] Profile XML not found: %s" % xml_path)
            log_error("Phase 2 only: XML not found at %s" % xml_path)
            return
        my_profile = load_profile_from_xml(xml_path)
        log("Profile loaded: %s" % my_profile.get("name","?"))
    except Exception as e:
        log_error("Phase 2 only: profile load failed: %s" % e)
        return

    # Build output dirs
    try:

        base        = get_output_dir()
        output_dirs = {
            "docx": _os.path.join(base, "resumes", "docx"),
            "pdf":  _os.path.join(base, "resumes", "pdf"),
        }
        _os.makedirs(output_dirs["docx"], exist_ok=True)
        _os.makedirs(output_dirs["pdf"],  exist_ok=True)
    except Exception as e:
        log_error("Phase 2 only: output dir setup failed: %s" % e)
        return

    # Run Phase 2 for specified job IDs
    from api.resume_gen import batch_generate_resumes
    log("Phase 2 only: generating resumes for job_ids=%s" % job_ids)
    results = batch_generate_resumes(
        my_profile, output_dirs,
        job_ids=job_ids)

    log("Phase 2 only complete: %s" % results)
    print("[>>] Phase 2 only complete: %s generated, %s failed" % (
        results.get("generated",0), results.get("failed",0)))


async def main(gui_args=None):
    from datetime import datetime as _dt
    global SESSION_START
    SESSION_START = _dt.now().strftime("%Y-%m-%d %H:%M:%S")
    log_section("Bot started")
    log("Log file: %s" % LOG_FILE)
    log("Session start: %s" % SESSION_START)

    # One-time patch — runs in milliseconds, idempotent
    # Cleans any duplicate rows from before the upsert fix
    try:
        from db.tracker import cleanup_duplicate_rows
        _cleaned = cleanup_duplicate_rows()
        if _cleaned["removed"] > 0:
            log("Startup: removed %d duplicate DB rows" % _cleaned["removed"])
            print("[DB] Cleaned %d duplicate job row(s)." % _cleaned["removed"])
    except Exception as _e:
        log("Startup cleanup skipped: %s" % _e)

    gui_mode = gui_args is not None

    print("\n[AI] Job Application Automation System")
    print("=" * 50)

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("\n[ERR] ANTHROPIC_API_KEY not found!")
        return

    print("\n[KEY] Checking your Claude API key...")
    try:
        client.messages.create(
            model=AI_MODEL, max_tokens=5, timeout=60.0,
            messages=[{"role": "user", "content": "ping"}],
        )
        print("   [OK] API key is valid.\n")
    except anthropic.AuthenticationError:
        print("   [ERR] API key rejected.")
        return
    except Exception as e:
        print("   [ERR] Could not verify API key: %s" % e)
        return

    get_settings()

    # Load profile — let load_profile_from_xml() handle path resolution
    # It reads from local_settings.json via get_resume_data_path() internally
    from core.profile import load_profile_from_xml
    from core.settings import get_resume_data_path, _settings_file, _exe_dir
    xml_path = get_resume_data_path()
    log("Settings file: %s" % _settings_file())
    log("Exe dir: %s" % _exe_dir())
    log("XML path resolved to: %s" % xml_path)
    log("XML exists: %s" % os.path.isfile(xml_path))
    try:
        my_profile = load_profile_from_xml(xml_path)
        print("[OK] Profile loaded: %s" % my_profile.get("name", "unknown"))
        log("Profile loaded: %s" % my_profile.get("name", ""))
    except FileNotFoundError as e:
        my_profile = {}
        log_warn("Profile not found at %s: %s" % (xml_path, e))
        print("[WARN] resume_data.xml not found at: %s" % xml_path)
        print("       Put your resume_data.xml in the data/ folder next to the exe.")
    except Exception as e:
        my_profile = {}
        log_error("Profile load error at %s: %s" % (xml_path, e))
        print("[WARN] Could not load profile: %s" % e)

    from api.prompts import (all_prompts_ready, setup_all_prompts,
                              personal_prompts_match_profile,
                              get_profile_hash, store_profile_hash,
                              delete_prompt, PROMPT_RELEVANCE_CHECK,
                              PROMPT_RESUME_TAILOR)

    prompts_exist   = all_prompts_ready()
    profile_changed = my_profile and not personal_prompts_match_profile(my_profile)

    if not prompts_exist or profile_changed:
        if profile_changed and prompts_exist:
            print("[...] Profile has changed — regenerating personalised prompts...")
            log("Profile changed — regenerating personal prompts")
            delete_prompt(PROMPT_RELEVANCE_CHECK)
            delete_prompt(PROMPT_RESUME_TAILOR)
        else:
            print("[...] First-time setup: generating personalised AI prompts...")
        try:
            result = setup_all_prompts(my_profile, client, overwrite=False)
            if my_profile:
                store_profile_hash(get_profile_hash(my_profile))
            print("   [OK] Prompts ready (%d created, %d skipped).\n"
                  % (result["created"], result["skipped"]))
        except Exception as e:
            print("   [ERR] Could not generate prompts: %s" % e)
            return
    else:
        log("Prompts match current profile — skipping regeneration")

    import db.tracker as tracker
    leftover = tracker.has_unfinished_run()
    if leftover:
        print("[WARN]  Found %d job(s) from a previous unfinished run." % leftover)
        if gui_mode:
            if gui_args.clear_runs:
                result = tracker.clear_unfinished_run()
                print("   [CLN] Cleared %d leftover job(s).\n" % result["rows"])
            else:
                print("   [OK] Keeping them.\n")
        else:
            try:
                choice = input("   Clear them? [y / n]: ").strip().lower()
            except (EOFError, OSError):
                choice = "n"
            if choice in ("y", "yes"):
                result = tracker.clear_unfinished_run()
                print("   [CLN] Cleared %d leftover job(s).\n" % result["rows"])

    # Roles
    if gui_mode and gui_args.roles:
        selected_roles = gui_args.roles
        print("[AI] Roles: %s" % ", ".join(selected_roles))
    elif gui_mode:
        print("\n[...] Analysing your profile with Claude...\n")
        selected_roles = suggest_roles(my_profile)
        print("[AI] Using %d suggested roles:" % len(selected_roles))
        for r in selected_roles:
            print("      * %s" % r)
    else:
        print("\n[...] Analysing your profile with Claude...\n")
        suggested_roles = suggest_roles(my_profile)
        selected_roles  = get_role_choice(suggested_roles)

    if gui_mode:
        apply_mode       = gui_args.mode
        application_mode = getattr(gui_args, "application_mode", "continuous")

        # Build gui_filters from GUI selections — override config defaults
        _exp_map  = {"1":"internship","2":"entry","3":"associate","4":"mid_senior","5":"director","6":"executive"}
        _jt_map   = {"F":"full_time","P":"part_time","C":"contract","T":"temporary","I":"internship","V":"volunteer"}
        _raw_exp  = getattr(gui_args, "exp_levels",  []) or []
        _raw_jt   = getattr(gui_args, "job_types",   []) or []
        _raw_wp   = getattr(gui_args, "workplace",   []) or []
        _wp_map   = {"1":"on_site","2":"remote","3":"hybrid"}

        gui_filters = {
            "exp_levels": [_exp_map[e] for e in _raw_exp if e in _exp_map] or None,
            "job_types":  [_jt_map[j] for j in _raw_jt if j in _jt_map]  or None,
            "workplace":  [_wp_map[w] for w in _raw_wp if w in _wp_map]   or None,
        }
        # Remove None values so get_active_filters uses config defaults for unset
        gui_filters = {k:v for k,v in gui_filters.items() if v}
        if gui_filters:
            log("GUI filter overrides: %s" % gui_filters)
        max_jobs         = gui_args.max_jobs      # must be defined before one_at_a_time check
        unlimited        = max_jobs == 0
        print("[OK] Apply mode: %s" % apply_mode)
        print("[OK] Application mode: %s" % application_mode)

        # In one-at-a-time mode, check if total limit already reached
        if application_mode == "one_at_a_time" and not unlimited:
            import db.tracker as _t
            total_so_far = _t.count_applied_total()
            if total_so_far >= max_jobs:
                print("[>>] One-at-a-time: already applied to %d/%d jobs. Limit reached."
                      % (total_so_far, max_jobs))
                return
            remaining_jobs = max_jobs - total_so_far
            print("[>>] One-at-a-time: %d/%d applied so far. %d remaining."
                  % (total_so_far, max_jobs, remaining_jobs))
    else:
        apply_mode       = get_apply_mode()
        application_mode = "continuous"   # CLI always runs continuous

    if gui_mode:
        job_location = gui_args.location or DEFAULT_LOCATION
    else:
        try:
            job_location = input("[LOC] Location? (Enter = 'United States'): ").strip()
        except (EOFError, OSError):
            job_location = ""
        try:
            max_jobs_input = input("[NUM] How many to apply? (0=unlimited, Enter=5): ").strip()
        except (EOFError, OSError):
            max_jobs_input = "5"
        if not job_location:
            job_location = DEFAULT_LOCATION
        max_jobs  = int(max_jobs_input) if max_jobs_input.isdigit() else 5
        unlimited = max_jobs == 0

    print("\n" + "=" * 50)
    print("[OK] Ready!")
    if len(selected_roles) == 1:
        print("   Role       : %s" % selected_roles[0])
    else:
        print("   Roles      : %d roles" % len(selected_roles))
        for r in selected_roles:
            print("                * %s" % r)
    print("   Location   : %s" % job_location)
    print("   Apply Mode : %s"
          % ("Easy Apply only" if apply_mode == "easy_apply" else "All jobs"))
    print("   Target     : %s"
          % ("Unlimited" if unlimited else "%d applications" % max_jobs))
    print("=" * 50)

    if not gui_mode:
        confirm = input("\n[>>]  Press Enter to start or 'exit' to cancel: ").strip().lower()
        if confirm == "exit":
            return

    out_path("resumes", "docx")
    out_path("resumes", "pdf")

    # Pre-load python DLL + add exe dir to DLL search path
    # Fixes: LoadLibraryExW _greenlet.pyd "module not found" in Nuitka standalone
    try:
        import ctypes as _ct, sys as _sys2, os as _os2
        _exe_dir = _os2.path.dirname(_sys2.executable)

        # Add exe directory to Windows DLL search path (Python 3.8+)
        if hasattr(_os2, "add_dll_directory"):
            _os2.add_dll_directory(_exe_dir)
            # Also add any subdirs that contain .pyd files
            for _sd in _os2.listdir(_exe_dir):
                _sdp = _os2.path.join(_exe_dir, _sd)
                if _os2.path.isdir(_sdp) and any(
                    f.endswith(".pyd") for f in _os2.listdir(_sdp)
                ):
                    _os2.add_dll_directory(_sdp)

        # Pre-load python DLL into process cache
        _py_dll = "python%d%d.dll" % (_sys2.version_info.major, _sys2.version_info.minor)
        for _loc in [_exe_dir,
                     _os2.path.join(_exe_dir, "greenlet"),
                     _os2.path.join(_os2.environ.get("SystemRoot","C:\\Windows"), "System32")]:
            _full = _os2.path.join(_loc, _py_dll)
            if _os2.path.exists(_full):
                _ct.CDLL(_full)
                log("Pre-loaded %s from %s" % (_py_dll, _loc))
                break
    except Exception as _dll_e:
        log_warn("DLL pre-load warning: %s" % _dll_e)

    # Inject pure-Python greenlet stub if C extension unavailable
    # This lets playwright import succeed — async mode never calls greenlet at runtime
    try:
        import greenlet as _gl_test
        _gl_test.getcurrent   # test it works
    except (ImportError, OSError, AttributeError):
        import sys as _sys3, types as _types
        _gl_stub = _types.ModuleType("greenlet")
        _gl_stub.__version__ = "stub"

        class _GreenletExit(BaseException): pass
        class _GreenletError(Exception): pass
        class _Greenlet:
            def __init__(self, run=None, parent=None):
                self.run    = run
                self.parent = parent
                self._dead  = False
            def switch(self, *a, **k):
                return self.run(*a, **k) if self.run else None
            def __call__(self, *a, **k):
                return self.switch(*a, **k)
            def throw(self, t=None, v=None, tb=None):
                raise (t or _GreenletExit)()
            @property
            def dead(self): return self._dead
            gr_frame = None

        # getcurrent() must return a callable stub instance (not None)
        # playwright calls getcurrent()(...) treating greenlets as callables
        _current_stub = _Greenlet()
        _current_stub._dead = False

        _gl_stub.greenlet        = _Greenlet
        _gl_stub.GreenletExit    = _GreenletExit
        _gl_stub.error           = _GreenletError
        _gl_stub.getcurrent      = lambda: _current_stub
        _gl_stub.settrace        = lambda cb: None
        _gl_stub.gettrace        = lambda: None
        _gl_stub.GREENLET_USE_CONTEXT_VARS = False
        _sys3.modules["greenlet"]            = _gl_stub
        _sys3.modules["greenlet._greenlet"]  = _gl_stub
        log("Greenlet C extension unavailable — using pure-Python stub (async mode OK)")

    # Lazy imports of backend modules — resolved here inside main()
    # Module-level stubs are None; we set real functions here
    try:
        from backend.scraper import continuous_job_search
        from backend.browser import create_logged_in_context, minimize_browser
        from api.filter   import check_job_relevance, print_relevance_report
        log("Backend modules imported successfully")
    except Exception as _imp_e:
        print("[!!] Backend import failed: %s" % _imp_e, flush=True)
        log_error("Backend import failed: %s" % _imp_e)
        raise RuntimeError("Backend import failed: %s" % _imp_e) from _imp_e

    # Lazy import — only load playwright when bot actually runs
    try:
        from playwright.async_api import async_playwright
    except (ImportError, Exception) as _e:
        print("[!!] Playwright import failed: %s" % _e, flush=True)
        print("[!!] Fix: Run 'resuto.exe --install-browsers' from Command Prompt", flush=True)
        print("[!!] Or install manually: pip install playwright && playwright install chromium", flush=True)
        raise RuntimeError(
            "Playwright not available.\n"
            "Run: resuto.exe --install-browsers\n"
            "Or reinstall Resuto to trigger automatic browser download.\n"
            "Details: %s" % _e
        ) from _e

    async with async_playwright() as playwright:
        # Debug: verify create_logged_in_context is the real function not None
        if create_logged_in_context is None:
            raise RuntimeError(
                "create_logged_in_context is None — browser import failed silently. "
                "Check backend/browser.py imports."
            )
        log("Launching browser via create_logged_in_context...")
        browser, context, page = await create_logged_in_context(playwright)

        print("\n" + "=" * 50)
        print("[>>] Bot running -- close browser to stop anytime")
        print("=" * 50)

        applied_total = 0
        _session_seen = set()   # shared dedup set — persists across all role searches

        prev_applied = 0   # track applications per role for one-at-a-time mode
        for role_index, job_keyword in enumerate(selected_roles, 1):
            # One at a time mode — stop after first successful application
            if application_mode == "one_at_a_time" and applied_total > prev_applied:
                print("\n[>>] One-at-a-time mode: applied to 1 job. Click Start Bot for next.")
                break

            if not unlimited and applied_total >= max_jobs:
                print("\n[>>] Target of %d reached!" % max_jobs)
                break

            if len(selected_roles) > 1:
                print("\n\n" + "#" * 50)
                print("# Role %d/%d: %s" % (role_index, len(selected_roles), job_keyword))
                remaining    = max_jobs - applied_total if not unlimited else "inf"
                prev_applied = applied_total   # track for one-at-a-time check
                print("# Applied: %d  |  Remaining: %s" % (applied_total, remaining))
                print("#" * 50)

            applied_total = await run_applications(
                browser       = browser,
                context       = context,
                page          = page,
                job_keyword   = job_keyword,
                job_location  = job_location,
                max_jobs      = max_jobs,
                unlimited     = unlimited,
                applied_total = applied_total,
                search_role   = job_keyword,
                apply_mode    = apply_mode,
                my_profile    = my_profile,
                global_seen   = _session_seen,  # shared across all roles
                gui_filters   = gui_filters if gui_mode else None,
            )

        print("\n" + "=" * 50)
        print("[OK] Session complete.")
        print("[BOT_IDLE]")
        print("=" * 50, flush=True)

        await minimize_browser(context)

        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, sys.stdin.readline)
        except Exception:
            pass

        try:
            await browser.close()
        except Exception:
            pass

    print("\n" + "=" * 50)
    print("[DB] Final Summary:")
    print("   Roles searched : %d" % len(selected_roles))
    print("   Jobs applied   : %d" % applied_total)
    show_stats()
    print("\n[OK] Done!\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--gui",          action="store_true")
    parser.add_argument("--location",     default="")
    parser.add_argument("--max-jobs",     type=int, default=5)
    parser.add_argument("--mode",         default="easy_apply",
                        choices=["easy_apply", "all"])
    parser.add_argument("--roles",        nargs="*", default=[])
    parser.add_argument("--clear-runs",   action="store_true")
    parser.add_argument("--exp-levels",   nargs="*", default=[])
    parser.add_argument("--job-types",    nargs="*", default=[])
    parser.add_argument("--workplace",    nargs="*", default=[])
    parser.add_argument("--phase2-only",  action="store_true",
                        help="Skip Phase 1, run Phase 2 only")
    parser.add_argument("--job-ids",      nargs="+", type=int, default=[],
                        help="Specific DB job IDs to generate resumes for")
    args, _ = parser.parse_known_args()

    # Phase 2 only mode — manual resume generation from History tab
    if args.phase2_only:
        asyncio.run(run_phase2_only(
            job_ids=args.job_ids or None,
            gui_mode=args.gui))
    else:
        asyncio.run(main(gui_args=args if args.gui else None))