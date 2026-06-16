# api/filter.py
import sys as _sys, os as _os
if not getattr(_sys, "frozen", False):
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import re
import time
from itertools import chain
import anthropic
import json
import os
from api.prompts import get_prompt, PROMPT_RELEVANCE_CHECK
from core.config import AI_MODEL_RELEVANCE

try:
    from core.logger import log, log_warn, log_error, log_debug
except Exception:
    def log(m, *a): pass
    def log_warn(m, *a): pass
    def log_error(m, *a, **k): pass
    def log_debug(m, *a): pass

try:
    from core.config import CPT_SCREENING
except Exception:
    CPT_SCREENING = {"screen_sponsorship": False, "blocking_phrases": [],
                     "friendly_phrases": [], "skip_blocked": True}

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

# Compiled once at import time
_INTERNSHIP_RE = re.compile(
    r"intern(ship)?|co[-\s]?op|coop|trainee|apprentice|"
    r"new\s+grad(uate)?|entry[-\s]?level|graduate\s+program|"
    r"early\s+career|student",
    re.IGNORECASE,
)

_LOGGED_IN_SEGMENTS  = frozenset(("/feed", "/mynetwork", "/jobs"))
_LOGGED_OUT_SEGMENTS = frozenset(("login", "checkpoint", "authwall"))

MAX_RETRIES    = 3
RETRY_TIMEOUT  = 45    # seconds per attempt — 3 × 45 = 135s, well inside 200s
RETRY_WAITS    = [2, 5, 10]  # backoff between attempts

_HTML_RE      = re.compile(r"<[^>]+>")
_URL_RE       = re.compile(r"https?://\S+")
_SPACE_RE     = re.compile(r"[^\S\n]{2,}")
_BOILER_RE    = re.compile(
    r"\b(click here|apply now|equal opportunity|eeo|benefits include"
    r"|we offer|about us|our company|join our team|competitive salary"
    r"|great culture|we are an equal)\b", re.IGNORECASE)

def _clean_jd(text: str, max_chars: int = 1200) -> str:
    """Strip HTML, URLs, boilerplate before sending to Claude — saves ~30% tokens."""
    if not text:
        return ""
    text = _HTML_RE.sub(" ", text)
    text = _URL_RE.sub("", text)
    text = _BOILER_RE.sub("", text)
    text = _SPACE_RE.sub(" ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:max_chars]


def screen_cpt_sponsorship(job: dict, job_description: str) -> dict:
    if not CPT_SCREENING.get("screen_sponsorship"):
        return {"blocked": False, "friendly": False, "matched": [], "note": ""}

    haystack = " ".join([
        job.get("title", ""),
        job.get("company", ""),
        job_description or "",
    ]).lower()

    blocking = [p for p in CPT_SCREENING.get("blocking_phrases", [])
                if p.lower() in haystack]
    friendly = [p for p in CPT_SCREENING.get("friendly_phrases", [])
                if p.lower() in haystack]

    is_blocked  = bool(blocking)
    is_friendly = bool(friendly)

    if is_blocked:
        note = "[WARN]  Likely NOT CPT-eligible -- listing says: " + blocking[0]
    elif is_friendly:
        note = "[OK] CPT/OPT-friendly signal found: " + friendly[0]
    else:
        note = "(i)  No explicit work-authorization statement found"

    return {
        "blocked":  is_blocked,
        "friendly": is_friendly,
        "matched":  blocking + friendly,
        "note":     note,
    }


def is_internship_or_entry(job: dict, job_description: str) -> bool:
    """
    Only flag as internship/entry if the JOB TITLE clearly indicates it.
    Do NOT use body text — "entry-level" in JD body is just a preference,
    not a reason to override specialization gap logic for a senior role.
    """
    title = job.get("title", "")
    return bool(_INTERNSHIP_RE.search(title))


def detect_seniority_gap(job: dict, job_description: str,
                          candidate_years: float = 0.0) -> dict:
    title = (job.get("title", "") or "").lower()
    text  = (job_description or "").lower()

    senior_title_words = [
        "lead ", " lead", "principal", "staff engineer", "staff software",
        "architect", "head of", "director", "vp ", "manager",
        "sr. lead", "senior lead",
    ]
    senior_title = any(w in title for w in senior_title_words)

    required_years = 0
    for m in re.finditer(r"(\d{1,2})\s*\+?\s*years", text):
        try:
            n = int(m.group(1))
            if n > required_years:
                required_years = n
        except ValueError:
            pass

    years_gap = (
        candidate_years > 0
        and (
            required_years >= candidate_years * 2
            or required_years >= candidate_years + 3
        )
    )

    gap = years_gap or senior_title

    if gap:
        bits = []
        if required_years and candidate_years:
            bits.append(
                "role asks for ~%d+ years (candidate has ~%s)"
                % (required_years, candidate_years)
            )
        if senior_title:
            bits.append("title indicates a lead/principal-level role")
        reason = "; ".join(bits) if bits else "role is more senior than candidate"
    else:
        reason = ""

    return {
        "gap":            gap,
        "required_years": required_years,
        "senior_title":   senior_title,
        "reason":         reason,
    }


def _make_skip_result(job: dict, job_description: str, reason: str) -> dict:
    """Return a safe 'skip' result without calling Claude."""
    _cpt_default = {"blocked": False, "friendly": False, "matched": [], "note": ""}
    try:
        _cpt_default = screen_cpt_sponsorship(job, job_description)
    except Exception:
        pass
    return {
        "is_relevant":        False,
        "match_score":        0,
        "skill_overlap":      0,
        "domain_match":       False,
        "transferable":       False,
        "specialization_gap": False,
        "seniority_gap":      False,
        "role_equivalent":    "Could not evaluate",
        "reason":             reason,
        "matched_skills":     [],
        "missing_skills":     [],
        "cpt_screen":         _cpt_default,
        "cpt_blocked":        _cpt_default["blocked"],
        "cpt_friendly":       _cpt_default["friendly"],
        "is_internship":      False,
    }


def check_job_relevance(profile: dict, job: dict, job_description: str,
                        search_role: str = "",
                        jd_metadata: dict = None) -> dict:
    """
    Uses Claude AI to decide if this job is worth applying to.

    search_role: the term the user searched for (e.g. "Machine Learning Engineer")
                 Used to check if the job is actually in the right domain.

    Runs synchronously — must be called via run_in_executor from async code
    so it doesn't block the asyncio event loop.
    """
    # ── Guard: empty profile ──────────────────────────────────────
    if not profile or not profile.get("skills"):
        log_warn("check_job_relevance: profile is empty — skipping AI check")
        return _make_skip_result(job, job_description,
            "Profile not loaded — run resume intake first")

    # CPT context — injected into every relevance check
    # CPT = work authorized, NO sponsorship needed, legal to work immediately
    _CPT_CONTEXT = (
        "WORK AUTHORIZATION: Candidate is on F1 visa with CPT "
        "(Curricular Practical Training). CPT IS work authorization — "
        "candidate does NOT need sponsorship, does NOT need H1B, "
        "and CAN legally work immediately. "
        "Jobs saying 'no sponsorship' or 'must be authorized to work' "
        "are ELIGIBLE — candidate already has authorization. "
        "Only skip if job explicitly requires US Citizenship or Security Clearance."
    )

    all_skills = list(chain.from_iterable(profile["skills"].values()))

    # If JD metadata already extracted (Stage 2), enrich the relevance check
    # with structured JD data — more accurate than raw text matching
    jd_skills   = (jd_metadata or {}).get("skills", [])
    jd_mission  = (jd_metadata or {}).get("mission", "")
    jd_seniority = (jd_metadata or {}).get("seniority", "")

    # ── Build richer experience context ──────────────────────────
    # Compress bullets to pure technical signal — strip filler, keep domain keywords
    # e.g. "Responsible for developing and maintaining..." → "Developed CKYC pipeline C#/.NET SQL"
    _FILLER = re.compile(
        r"(responsible for|worked on|helped|assisted|collaborated|supported"
        r"|involved in|participated in|contributed to|tasked with"
        r"|developed and|designed and|built and|created and|managed and"
        r"|as part of|in order to|so that|which resulted in)",
        re.IGNORECASE)

    def _compress_bullet(b: str) -> str:
        """Keep technical terms and metrics, strip filler verbs/phrases."""
        b = _FILLER.sub("", b)
        b = re.sub(r"\s{2,}", " ", b).strip()
        # Keep only first 80 chars — enough for domain signal
        return b[:80] if len(b) > 80 else b

    experience_lines = []
    for job_exp in profile.get("experience", [])[:4]:
        title   = job_exp.get("title", "")
        company = job_exp.get("company", "")
        dur     = job_exp.get("duration", "") or job_exp.get("dates", "")
        bullets = job_exp.get("bullets", [])
        if not title:
            continue
        line = "%s @ %s (%s)" % (title, company, dur)
        # Add top 2 compressed bullets — pure technical signal
        if isinstance(bullets, list):
            compressed = [_compress_bullet(b) for b in bullets[:2] if b]
            if compressed:
                line += " | " + " · ".join(compressed)
        experience_lines.append(line)

    experience_summary = "\n".join(experience_lines) or "Not specified"

    edu_lines = []
    for deg in profile.get("education", []):
        name   = deg.get("degree") or deg.get("name", "")
        school = deg.get("school", "")
        year   = deg.get("year", "")
        if name:
            edu_lines.append("  - %s — %s (%s)" % (name, school, year))

    base_prompt = get_prompt(PROMPT_RELEVANCE_CHECK)
    if not base_prompt:
        log_warn("check_job_relevance: PROMPT_RELEVANCE_CHECK missing from DB")
        return _make_skip_result(job, job_description,
            "Relevance prompt missing — run update_prompts.py")

    # Compact prompt — static instructions split to system param
    # Dynamic profile+job in user message — both compressed
    prompt = (
        base_prompt
        + "\nCANDIDATE PROFILE:\n"
        + "Exp:%s yrs | %s\n" % (profile.get("years_experience","?"), profile.get("summary","")[:120])
        + "Jobs: " + experience_summary.replace("\n"," | ") + "\n"
        + "Skills: " + ", ".join(all_skills[:30]) + "\n"
        + ("Search: " + search_role + "\n" if search_role else "")
        + "\n" + _CPT_CONTEXT + "\n\n"
        + "JOB: " + job.get("title","") + " @ " + job.get("company","") + "\n"
        + ("Required skills: " + ", ".join(jd_skills[:7]) + "\n" if jd_skills else "")
        + ("Mission: " + jd_mission + "\n" if jd_mission else "")
        + ("Seniority: " + jd_seniority + "\n" if jd_seniority else "")
        + _clean_jd(job_description) + "\n"
        + "Return ONLY valid JSON. No markdown. No explanation. No preamble.\n"
        + '{"is_relevant":bool,"match_score":0-100,"skill_overlap":0-100,'
        + '"domain_match":bool,"transferable":bool,"specialization_gap":bool,'
        + '"role_equivalent":"<10 words>","reason":"1-2 sentences",'
        + '"matched_skills":[],"missing_skills":[]}'
    )

    # ── Call Claude with retry + backoff ──────────────────────────
    log_debug("Full check: prompt_len=%d model=%s" % (len(prompt), AI_MODEL_RELEVANCE))

    last_error = None
    message    = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            # Static instructions → system (cached by Anthropic)
            # Dynamic content (profile + job) → user message
            split   = prompt.find("CANDIDATE PROFILE:")
            sys_txt = prompt[:split].strip()  if split > 0 else ""
            usr_txt = prompt[split:].strip()  if split > 0 else prompt

            message = client.messages.create(
                model=AI_MODEL_RELEVANCE,
                max_tokens=600,  # clean JSON without thinking overhead
                temperature=0,            # classification — no creativity needed
                timeout=float(RETRY_TIMEOUT),
                # No stop sequences — let Claude complete the full JSON
                system=sys_txt if sys_txt else anthropic.NOT_GIVEN,
                messages=[{"role": "user", "content": usr_txt}]
            )
            last_error = None
            break
        except anthropic.AuthenticationError:
            log_error("AUTH ERROR: Claude API key rejected")
            raise
        except Exception as e:
            last_error = e
            log_warn("API attempt %d/%d failed: %s: %s"
                     % (attempt, MAX_RETRIES, type(e).__name__, e))
            if attempt < MAX_RETRIES:
                wait = RETRY_WAITS[attempt - 1]
                print("   [WARN] Claude API attempt %d failed: %s" % (attempt, e))
                print("          Retrying in %ds..." % wait)
                time.sleep(wait)
            else:
                log_error("API FAILED after %d attempts: %s" % (MAX_RETRIES, e))
                print("   [WARN] Claude API failed after %d attempts: %s"
                      % (MAX_RETRIES, e))

    if last_error:
        log_error("All retries exhausted: %s" % last_error)
        return _make_skip_result(
            job, job_description,
            "AI evaluation failed after %d retries — job skipped" % MAX_RETRIES)

    # ── Parse response ────────────────────────────────────────────
    try:
        response_text = message.content[0].text.strip()
        log_debug("Raw response: %s" % response_text[:200])

        # Extract JSON object — find first { and last }
        # Handles any extra text before/after the JSON
        _js = response_text.find("{")
        _je = response_text.rfind("}") + 1
        if _js >= 0 and _je > _js:
            response_text = response_text[_js:_je]

        if "```" in response_text:
            parts = response_text.split("```")
            response_text = parts[1] if len(parts) > 1 else parts[0]
            if response_text.startswith("json"):
                response_text = response_text[4:]

        result = json.loads(response_text.strip())

        is_intern_role = is_internship_or_entry(job, job_description)
        result["is_internship"] = is_intern_role

        overlap          = result.get("skill_overlap",      0)
        match_score      = result.get("match_score",         0)
        realistic_chance = result.get("realistic_chance",    match_score)
        learning_weeks   = result.get("learning_weeks",      0)
        domain_match     = result.get("domain_match",       False)
        transferable     = result.get("transferable",       False)
        spec_gap         = result.get("specialization_gap", False)

        seniority = detect_seniority_gap(
            job, job_description,
            candidate_years=float(profile.get("years_experience") or 0),
        )
        result["seniority_gap"]  = seniority["gap"]
        result["required_years"] = seniority["required_years"]

        # ── Decision engine ───────────────────────────────────────
        # Philosophy: apply if there is a REALISTIC CHANCE of getting
        # a phone screen. Years on JDs are aspirational — skills are
        # the real signal. A hiring manager will overlook a 1-2 year
        # gap for someone who has 85% skill overlap and domain match.
        #
        # TIER 1 — Strong match → always apply
        #   overlap ≥ 70% AND domain_match
        #   Person has the skills. Years don't matter.
        #
        # TIER 2 — Good match → apply
        #   overlap ≥ 60% AND transferable
        #   Adjacent domain, real transferable skills. Worth trying.
        #
        # TIER 3 — Stretch → apply (flagged)
        #   overlap 45-60% AND not full specialization gap
        #   Gap is learnable with 2-4 weeks effort. Desperate market.
        #
        # TIER 4 — Domain mismatch → skip
        #   specialization_gap AND overlap < 45%
        #   Different field entirely. Months of new learning needed.
        #
        # TIER 5 — Leadership gap → skip
        #   senior title (lead/principal/architect) AND overlap < 55%
        #   Not a years gap — genuinely different responsibility level.
        #
        # NOTE: We NEVER skip based on years alone.
        # If Claude marked is_relevant=False only because of years,
        # our engine overrides it when skills are strong enough.

        def _decide():
            # Internship/entry with specialization gap — growth opportunity
            if spec_gap and is_intern_role:
                result["stretch_internship"] = True
                result["reason"] = (
                    "Specialization stretch but entry-level role — "
                    "legitimate growth opportunity. " + result.get("reason", ""))
                return True

            # Hard block: genuine leadership role + low overlap
            if seniority["senior_title"] and overlap < 55:
                result["reason"] = (
                    "Leadership/principal role requires management experience "
                    "beyond current level. " + result.get("reason", ""))
                return False

            # Hard block: domain is completely different
            if spec_gap and overlap < 45:
                result["reason"] = (
                    "Specialization gap — different domain requiring skills "
                    "not in current background. " + result.get("reason", ""))
                return False

            # TIER 1: strong match — always apply regardless of years
            if overlap >= 70 and domain_match:
                if not result.get("is_relevant"):
                    result["reason"] = (
                        "Strong skill match (%d%% overlap) and domain match — "
                        "applying despite JD year requirements. " % overlap
                        + result.get("reason", ""))
                return True

            # TIER 2: good overlap with transferable skills
            if overlap >= 60 and transferable:
                if not result.get("is_relevant"):
                    result["reason"] = (
                        "Good skill overlap (%d%%) with transferable background — "
                        "worth applying. " % overlap
                        + result.get("reason", ""))
                return True

            # TIER 3: stretch — apply if learning curve is short enough
            # "thin line" — if a candidate can bridge the gap with
            # 2-3 months of prep, they deserve the chance to apply
            if 45 <= overlap < 60 and not spec_gap:
                if not result.get("is_relevant"):
                    result["reason"] = (
                        "Stretch role (%d%% overlap) but gap is learnable — "
                        "applying with preparation recommended. " % overlap
                        + result.get("reason", ""))
                return True

            # TIER 3b: realistic_chance override — even lower overlap
            # if Claude says there's a decent realistic chance, trust it
            if realistic_chance >= 45 and learning_weeks <= 8 and not spec_gap:
                if not result.get("is_relevant"):
                    result["reason"] = (
                        "Realistic chance %d%% with ~%d weeks prep — "
                        "worth applying with effort. "
                        % (realistic_chance, learning_weeks)
                        + result.get("reason", ""))
                return True

            # TIER 4: spec gap but short learning curve — marginal apply
            if spec_gap and learning_weeks <= 6 and realistic_chance >= 40:
                result["reason"] = (
                    "Adjacent field — gap bridgeable in ~%d weeks with effort. "
                    % learning_weeks + result.get("reason", ""))
                return True

            # TIER 5: low overlap or full domain mismatch — skip
            if overlap < 45:
                return False

            # Default: trust Claude's judgment for borderline cases
            is_rel = result.get("is_relevant", False)
            if is_rel and overlap < 60:
                result["stretch"] = True   # borderline — flag for preparation
            return is_rel

        result["is_relevant"] = _decide()

        # CPT/OPT screening: only block explicit citizenship/clearance requirements
        # "No sponsorship" phrases are NOT blockers for CPT/OPT holders
        # Claude's prompt already has correct CPT context — trust its decision
        cpt = screen_cpt_sponsorship(job, job_description)
        result["cpt_screen"]   = cpt
        result["cpt_blocked"]  = cpt["blocked"]
        result["cpt_friendly"] = cpt["friendly"]

        if cpt["blocked"] and CPT_SCREENING.get("skip_blocked", True):
            result["is_relevant"] = False
            result["reason"] = (
                "Hard block: job explicitly requires US citizenship or "
                "security clearance — not eligible on F-1. " + result.get("reason", ""))

        log("Relevance result: score=%s relevant=%s reason=%s"
            % (result.get("match_score", 0),
               result.get("is_relevant"),
               result.get("reason", "")[:80]))
        return result

    except anthropic.AuthenticationError:
        print("\n   [ERR] Claude API key rejected mid-run.")
        raise

    except Exception as e:
        log_error("Response parse error: %s | raw: %s" % (e, response_text[:200]))
        print("   [WARN] Could not parse Claude response: %s" % e)
        return _make_skip_result(
            job, job_description,
            "Response parse error — job skipped")


def print_relevance_report(result: dict, job: dict) -> None:
    score   = result.get("match_score",   0)
    overlap = result.get("skill_overlap", 0)
    status  = "[OK] APPLYING" if result.get("is_relevant") else "[SKIP]  SKIPPING"
    filled  = int(score / 5)
    bar     = "#" * filled + " " * (20 - filled)
    equiv   = result.get("role_equivalent", "")

    print("\n   +- AI Career Advisor --------------------------")
    print("   | %s @ %s" % (job.get("title"), job.get("company")))
    print("   | Match  : [%s] %s/100" % (bar, score))
    print("   | Skills : %s%% overlap" % overlap)
    print("   | Domain : %s  Transfer: %s" % (
        "[YES]" if result.get("domain_match")  else "[NO] ",
        "[YES]" if result.get("transferable") else "[NO] "))
    if result.get("specialization_gap"):
        if result.get("stretch_internship"):
            print("   | [NEW] Specialization stretch -- allowed as internship/growth role")
        else:
            print("   | [WARN]  Specialization gap")
    if result.get("seniority_gap") and not result.get("is_internship"):
        ry  = result.get("required_years", 0)
        msg = "   | [WARN]  Seniority gap -- role is more senior than candidate"
        if ry:
            msg += " (asks ~%d+ yrs)" % ry
        print(msg)
    if equiv:
        print("   | Equiv  : %s" % equiv)
    print("   | [OK] %s" % ", ".join(result.get("matched_skills", [])[:5] or ["None"]))
    print("   | [X] %s"  % ", ".join(result.get("missing_skills", [])[:4] or ["None"]))
    cpt = result.get("cpt_screen")
    if cpt and cpt.get("note"):
        print("   | CPT    : %s" % cpt["note"])
    print("   | %s" % result.get("reason", ""))
    print("   +- Decision: %s\n" % status)