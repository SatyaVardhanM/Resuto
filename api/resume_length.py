"""
api/resume_length.py — keep tailored resumes to a page limit.

Setting (Settings → Resume length, stored as "resume_length"):
    "auto" → 1 page under 5 years of experience, up to 2 pages from 5 years
    "1"    → 1 page
    "2"    → up to 2 pages

How a resume is fitted (gentlest first) — only the tailored COPY for one
job is changed, never the user's profile:
    1. leave out the least relevant bullets of older roles
       (a role is never removed — it keeps title, company and dates)
    2. then project bullets, then whole projects (last listed first)
    3. then bullets of the two most recent roles, down to a minimum

Page count: a DOCX does not know its page count, so the layout is
ESTIMATED from the amount of text (same fonts/margins as build_resume_docx).
When a PDF exists, its real page count is used to trim further.
"""
import copy
import math

# Layout of build_resume_docx: Letter page, 0.5" top/bottom, 0.7" sides
PAGE_HEIGHT_PT   = 720.0          # 10 in usable height
LINE_PT          = 13.1           # 11 pt text incl. line spacing
CHARS_PER_LINE   = 95             # full-width 11 pt line (7.1 in)
BULLET_CHARS     = 88             # indented bullet line
SAFETY           = 1.03           # estimate slightly high → fewer overflows
FIT_MARGIN       = 0.97           # aim a little under the limit (page breaks
                                  # waste a few lines the estimate can't see)

YEARS_FOR_TWO_PAGES = 5.0


# ── Settings ──────────────────────────────────────────────────────
def get_setting() -> str:
    try:
        from core.settings import get_settings
        v = str(get_settings().get("resume_length", "auto")).strip().lower()
    except Exception:
        v = "auto"
    return v if v in ("auto", "1", "2") else "auto"


def target_pages(profile: dict, setting: str = None) -> int:
    setting = setting or get_setting()
    if setting == "1":
        return 1
    if setting == "2":
        return 2
    years = float((profile or {}).get("years_experience") or 0)
    return 2 if years >= YEARS_FOR_TWO_PAGES else 1


def describe_target(profile: dict, setting: str = None) -> str:
    setting = setting or get_setting()
    t = target_pages(profile, setting)
    label = {"auto": "Auto", "1": "1 page", "2": "Up to 2 pages"}[setting]
    return "%s → %s" % (label, "1 page" if t == 1 else "up to 2 pages")


# ── Estimation ────────────────────────────────────────────────────
def _lines(text: str, per_line: int) -> int:
    text = str(text or "").strip()
    return max(1, math.ceil(len(text) / float(per_line))) if text else 0


def estimate_points(tailored: dict, profile: dict) -> float:
    """Estimated height in points of the DOCX build_resume_docx would make."""
    t, p = tailored or {}, profile or {}
    h = 0.0
    h += 22 * 1.2 + 2                                   # name
    if (t.get("title") or p.get("headline") or p.get("title")):
        h += 12 * 1.2 + 2                               # title line
    contact = "  |  ".join(str(p.get(k, "")) for k in
                           ("email", "phone", "location", "linkedin", "github")
                           if p.get(k))
    h += _lines(contact, 100) * 12.6 + 4

    def heading():
        return 10 + 2 + 11 * 1.25 + 2

    if t.get("summary"):
        h += heading() + _lines(t["summary"], CHARS_PER_LINE) * LINE_PT + 6

    skills = t.get("skills_grouped") or {}
    if skills:
        h += heading()
        for cat, items in skills.items():
            txt = "%s: %s" % (cat, ", ".join(map(str, items or [])))
            h += _lines(txt, CHARS_PER_LINE) * LINE_PT + 4

    exp = t.get("experience") or []
    if exp:
        h += heading()
        for job in exp:
            h += 6 + 1 + LINE_PT
            for b in job.get("bullets") or []:
                h += _lines(b, BULLET_CHARS) * LINE_PT + 2
            if (job.get("tech_stack") or "").strip():
                h += _lines(job["tech_stack"], 110) * 11.8 + 6

    projects = t.get("projects") or []
    if projects:
        h += heading()
        for pr in projects:
            h += 6 + 1 + LINE_PT
            pts = pr.get("bullets") or ([pr["description"]] if pr.get("description") else [])
            for b in pts:
                h += _lines(b, BULLET_CHARS) * LINE_PT + 2

    edu = p.get("education") or []
    if edu:
        h += heading() + len(edu) * (4 + 1 + LINE_PT)

    vol = p.get("volunteer") or []
    if vol:
        h += heading()
        for v in vol:
            h += 4 + 1 + LINE_PT + _lines(v.get("description", ""), BULLET_CHARS) * LINE_PT + 2
    return h * SAFETY


def estimate_pages(tailored: dict, profile: dict) -> float:
    return estimate_points(tailored, profile) / PAGE_HEIGHT_PT


def profile_as_tailored(profile: dict) -> dict:
    """The whole profile laid out as one resume (for the 'too long' check)."""
    p = profile or {}
    return {
        "summary":        p.get("summary", ""),
        "skills_grouped": p.get("skills") or {},
        "experience":     [dict(j) for j in (p.get("experience") or [])],
        "projects":       [dict(x) for x in (p.get("projects") or [])],
    }


def profile_overview(profile: dict) -> dict:
    exp = (profile or {}).get("experience") or []
    return {
        "years":   float((profile or {}).get("years_experience") or 0),
        "roles":   len(exp),
        "bullets": sum(len(j.get("bullets") or []) for j in exp),
        "pages":   round(estimate_pages(profile_as_tailored(profile), profile), 1),
    }


def pdf_page_count(path: str):
    """Real page count of a PDF, or None if not a readable PDF."""
    if not path or not str(path).lower().endswith(".pdf"):
        return None
    try:
        from pypdf import PdfReader
        return len(PdfReader(path).pages)
    except Exception:
        return None


# ── Trimming ──────────────────────────────────────────────────────
def _next_cut(t: dict, strict: bool):
    """Pick the next thing to leave out. Returns a description or None."""
    exp = t.get("experience") or []
    # 1. older roles (3rd and older): down to 0 bullets, oldest first
    for i in range(len(exp) - 1, 1, -1):
        if exp[i].get("bullets"):
            b = exp[i]["bullets"].pop()
            return ("bullet", exp[i].get("company", "?"), b)
    # 2. projects: bullets down to 1, then whole projects (last first)
    projects = t.get("projects") or []
    for pr in reversed(projects):
        if len(pr.get("bullets") or []) > 1:
            b = pr["bullets"].pop()
            return ("bullet", pr.get("name", "project"), b)
    if projects:
        pr = projects.pop()
        return ("project", pr.get("name", "project"), "")
    # 3. two most recent roles: down to 3 / 2 bullets, then 2 / 1 if strict
    mins = (2, 1) if strict else (3, 2)
    for i in (1, 0):
        if i < len(exp) and len(exp[i].get("bullets") or []) > mins[i]:
            b = exp[i]["bullets"].pop()
            return ("bullet", exp[i].get("company", "?"), b)
    return None


def fit_to_pages(tailored: dict, profile: dict, target: int,
                 extra_cuts: int = 0) -> tuple:
    """
    Return (fitted_copy, left_out) where left_out is a list of
    (kind, where, text). The input dict is not modified.
    extra_cuts: cut this many more items after the estimate fits
    (used when the real PDF still came out too long).
    """
    t = copy.deepcopy(tailored or {})
    for job in t.get("experience") or []:
        job["bullets"] = list(job.get("bullets") or [])
    for pr in t.get("projects") or []:
        if pr.get("bullets"):
            pr["bullets"] = list(pr["bullets"])
    left_out = []

    def over():
        return estimate_pages(t, profile) > target * FIT_MARGIN

    for strict in (False, True):
        while over():
            cut = _next_cut(t, strict)
            if cut is None:
                break
            left_out.append(cut)
    for _ in range(max(0, extra_cuts)):
        cut = _next_cut(t, True)
        if cut is None:
            break
        left_out.append(cut)
    return t, left_out


def summarize_left_out(left_out: list) -> str:
    """'4 bullets (2 from TSS, 2 from Acme), 1 project (Chatbot)'."""
    if not left_out:
        return "nothing left out"
    bullets, projects = {}, []
    for kind, where, _ in left_out:
        if kind == "project":
            projects.append(where)
        else:
            bullets[where] = bullets.get(where, 0) + 1
    parts = []
    if bullets:
        n = sum(bullets.values())
        parts.append("%d bullet%s (%s)" % (
            n, "" if n == 1 else "s",
            ", ".join("%d from %s" % (c, w) for w, c in bullets.items())))
    if projects:
        parts.append("%d project%s (%s)" % (
            len(projects), "" if len(projects) == 1 else "s", ", ".join(projects)))
    return "left out " + ", ".join(parts)
