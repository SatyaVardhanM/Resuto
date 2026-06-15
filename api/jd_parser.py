# api/jd_parser.py
# Stage 2: Ultra-cheap JD metadata extraction
# Extracts only what matters: title, top skills, mission summary
# Uses Haiku — cheap, fast, adequate for extraction

import sys as _sys, os as _os
if not getattr(_sys, "frozen", False):
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import json
import re
import anthropic
from core.config import AI_MODEL_FAST

try:
    from core.logger import log, log_debug, log_warn
except Exception:
    def log(m,*a): pass
    def log_debug(m,*a): pass
    def log_warn(m,*a): pass

# Compiled once — strips HR boilerplate before LLM sees it
_BOILER = re.compile(
    r"\b(equal opportunity|eeo|we are a|join our team|competitive salary"
    r"|great benefits|free coffee|our culture|about us|who we are"
    r"|why work here|perks|what we offer|we offer)\b.*?(?=\n|$)",
    re.IGNORECASE)
_HTML   = re.compile(r"<[^>]+>")
_SPACES = re.compile(r"[ \t]{2,}")

# Regex catches domain-specific acronyms Haiku might miss
# e.g. CKYC, CERSAI, STIBO, SWIFT, HIPAA, GDPR
_ACRONYM_RE = re.compile(r'[A-Z]{3,7}')
_ACRONYM_BLACKLIST = {
    "AND","THE","FOR","USA","GMT","UTC","URL","API","SQL","CSS",
    "HTML","JSON","REST","HTTP","AWS","GCP","PDF","XML","CSV",
    "MBA","LLC","INC","LTD","CEO","CTO","CIO","JOB","PTO",
    "EOE","EEO","ADA","OSHA","USD","EUR","GBP","KPI","ROI",
    "SLA","POC","MVP","UAT","QA","CI","CD","OOP","TDD","BDD",
}

def extract_niche_acronyms(text: str) -> list:
    """
    Deterministically extract domain-specific acronyms from raw JD or resume text.
    These are passed as CRITICAL KEYWORDS to Stage 2 LLM to prevent them being dropped.
    e.g. CKYC, CERSAI, STIBO, SWIFT — things Haiku may not recognise.
    """
    found = set(_ACRONYM_RE.findall(text or ""))
    return sorted(found - _ACRONYM_BLACKLIST)


_SYSTEM = (
    "Extract job metadata. "
    "Return ONLY minified JSON. No markdown. No explanation."
)

_SCHEMA = (
    '{"title":"job title","skills":["up to 7 required hard skills/tech keywords"],'
    '"seniority":"junior|mid|senior|lead","domain":"industry/domain in 2 words",'
    '"mission":"primary role purpose in max 12 words",'
    '"years_required":0}'
)


def _strip_jd(text: str, max_chars: int = 1500) -> str:
    text = _HTML.sub(" ", text)
    text = _BOILER.sub("", text)
    text = _SPACES.sub(" ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:max_chars]


def extract_jd_metadata(job: dict, job_description: str,
                        client: anthropic.Anthropic) -> dict:
    """
    Stage 2: Extract structured JD metadata using Haiku.

    Returns:
        {
            "title":          "Software Engineer",
            "skills":         ["Python", "Django", "PostgreSQL", ...],
            "seniority":      "mid",
            "domain":         "fintech backend",
            "mission":        "build scalable payment processing APIs",
            "years_required": 3,
            "raw_title":      original job title from listing
        }

    On any failure returns a safe fallback dict so pipeline continues.
    """
    clean_jd = _strip_jd(job_description)

    # Extract niche acronyms before LLM sees text — guarantees nothing is dropped
    niche_acronyms = extract_niche_acronyms(job_description + " " + job.get("title",""))
    acronym_hint   = ("CRITICAL KEYWORDS (must appear in skills list if relevant): "
                      + ", ".join(niche_acronyms[:15]) + "\n") if niche_acronyms else ""

    prompt = (
        "Job title: " + job.get("title", "") + "\n"
        "Company: "   + job.get("company", "") + "\n\n"
        + clean_jd + "\n\n"
        + acronym_hint
        + "Extract metadata. Schema:\n" + _SCHEMA
    )

    try:
        resp = client.messages.create(
            model=AI_MODEL_FAST,
            max_tokens=250,
            temperature=0,
            timeout=15.0,
            system=_SYSTEM,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = resp.content[0].text.strip()
        if not raw:
            raise ValueError("Empty response from Haiku")
        # Extract JSON if wrapped in markdown or text
        if "```" in raw:
            raw = raw.split("```")[1].lstrip("json").strip()
        # Find JSON object boundaries
        start = raw.find("{")
        end   = raw.rfind("}") + 1
        if start >= 0 and end > start:
            raw = raw[start:end]
        elif start >= 0:
            raw = raw[start:] + "}"
        meta = json.loads(raw)
        meta["raw_title"]       = job.get("title", "")
        meta["niche_acronyms"]  = niche_acronyms   # passed to bullet budget + resume gen
        log_debug("JD metadata: title=%s skills=%s acronyms=%s" % (
            meta.get("title","?"), meta.get("skills",[]), niche_acronyms[:5]))
        return meta
    except Exception as e:
        log_warn("JD metadata extraction failed: %s — using fallback" % e)
        # Fallback: even if LLM fails, regex still extracts acronyms
        fallback_acronyms = extract_niche_acronyms(job_description + " " + job.get("title",""))
        return {
            "title":           job.get("title", ""),
            "skills":          fallback_acronyms[:7],   # use acronyms as skill proxies
            "seniority":       "mid",
            "domain":          "",
            "mission":         "",
            "years_required":  0,
            "raw_title":       job.get("title", ""),
            "niche_acronyms":  fallback_acronyms,
        }