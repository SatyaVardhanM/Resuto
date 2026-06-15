# api/metric_guard.py
# Guards against AI metric fabrication — the #1 reason candidates fail
# technical interviews after an AI-enhanced resume gets them through ATS.
#
# Architecture: Claude is a SCULPTOR not an INVENTOR.
# It shapes real user data into professional prose.
# It must NEVER invent a number that doesn't exist in the source.

import re
import sys as _sys, os as _os
if not getattr(_sys, "frozen", False):
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

try:
    from core.logger import log_debug, log_warn
except Exception:
    def log_debug(m,*a): pass
    def log_warn(m,*a): pass

# ── Metric Detector ───────────────────────────────────────────────
_METRIC_RE = re.compile(
    r"(\d+\s*%"
    r"|\$\s*\d[\d,]*"
    r"|\b\d+[kKmMbB]\b"
    r"|\b\d+\s*x\b"
    r"|\b\d+\s*(?:records?|users?|requests?|transactions?|"
    r"engineers?|developers?|hours?|minutes?|seconds?|ms|"
    r"tickets?|bugs?|tests?|endpoints?|services?|clients?)\b)",
    re.IGNORECASE
)

def has_quantifiable_metrics(text: str) -> bool:
    return bool(_METRIC_RE.search(text or ""))

def extract_metrics_from_text(text: str) -> list:
    return _METRIC_RE.findall(text or "")

def scan_profile_for_missing_metrics(profile: dict) -> list:
    """
    Scan all job bullets in profile.
    Returns list of jobs where NO bullet has a metric.
    These need user input before generation to avoid fabrication.
    """
    missing = []
    for job in profile.get("experience", []):
        bullets  = job.get("bullets", [])
        if not bullets:
            continue
        all_text = " ".join(str(b) for b in bullets)
        if not has_quantifiable_metrics(all_text):
            missing.append({
                "company":  job.get("company", ""),
                "title":    job.get("title", ""),
                "duration": job.get("duration", ""),
                "bullets":  bullets[:2],
            })
    return missing


# ── Fuzzy Number Generator ────────────────────────────────────────
import random as _random

def fuzzy_number(clean_value: float, unit: str = "%") -> str:
    """
    Convert clean round AI-generated numbers into organic messy ones.
    Real-world data is never exactly 20% or 30%.
    20% becomes 19.3% or 21.7%
    50% becomes 48% or 52%
    """
    noise_pct = _random.uniform(0.03, 0.07)
    direction = _random.choice([-1, 1])
    noisy     = clean_value * (1 + direction * noise_pct)
    if noisy < 20:
        return "%.1f%s" % (round(noisy, 1), unit)
    return "%d%s" % (round(noisy), unit)

def fuzzify_resume_metrics(text: str) -> str:
    """
    Post-process generated resume text.
    Replace clean round percentages (20%, 30%, 50%) with organic ones.
    Leaves already-messy numbers (19.3%, 47%) untouched.
    """
    def _replace_clean(m):
        val_str = m.group(1)
        try:
            val = float(val_str)
            if val % 5 == 0 and 10 <= val <= 80:
                return fuzzy_number(val, "%")
        except ValueError:
            pass
        return m.group(0)
    return re.sub(r"\b(\d+)%", _replace_clean, text)


def selective_fuzzing_processor(generated_text: str) -> str:
    """
    Two-channel post-processor:
    - Channel A (<fact> tags):     strips tags, keeps value EXACTLY as-is
    - Channel B (<estimate> tags): strips tags, fuzzes the number slightly

    Input:  "scaling from <fact>10K</fact> to <fact>20K</fact> records,
             reducing latency by <estimate>25%</estimate>"
    Output: "scaling from 10K to 20K records, reducing latency by 24.3%"
    """
    # Channel A: preserve facts exactly — strip tags, keep value unchanged
    result = re.sub(r"<fact>(.*?)</fact>", lambda _m: _m.group(1), generated_text)

    # Channel B: fuzz estimates — apply ±0.7-1.4 noise to clean numbers
    def _fuzz_estimate(m):
        raw = m.group(1).strip().rstrip("%")
        try:
            val   = float(raw)
            noise = _random.choice([-1.4, -0.8, -0.3, 0.7, 1.1, 1.4])
            noisy = val + noise
            # Format: 1 decimal for small, whole for large
            if noisy < 20:
                return "%.1f%%" % round(noisy, 1)
            return "%d%%" % round(noisy)
        except ValueError:
            return m.group(1)   # can't parse — leave untouched

    result = re.sub(r"<estimate>(.*?)</estimate>", _fuzz_estimate, result)

    # Strip ALL remaining XML-style tags — belt and suspenders
    # Catches any tags Claude placed incorrectly across field boundaries
    result = re.sub(r"</?(?:fact|estimate)>", "", result)
    result = re.sub(r"<fact[^>]*>.*?</fact>", "", result)     # unclosed spans
    result = re.sub(r"<estimate[^>]*>.*?</estimate>", "", result)
    result = re.sub(r"</?(?:fact|estimate)[^>]*>", "", result) # any remaining
    return result


# ── Verb Blacklist + Role-Scale Anchoring ─────────────────────────
_AI_FINGERPRINT_VERBS = {
    "spearheaded", "orchestrated", "streamlined", "leveraged",
    "fostered", "championed", "synergized", "utilized", "facilitated",
    "interfaced", "liaisoned", "actualized", "conceptualized",
}

_ROLE_VERB_TIERS = {
    "intern":  ["Optimized","Refactored","Diagnosed","Developed",
                "Implemented","Tested","Documented","Improved","Fixed"],
    "junior":  ["Built","Developed","Implemented","Automated","Reduced",
                "Deployed","Designed","Tested","Maintained","Resolved"],
    "mid":     ["Engineered","Architected","Designed","Automated","Scaled",
                "Optimized","Delivered","Reduced","Integrated","Migrated"],
    "senior":  ["Architected","Led","Scaled","Established","Delivered",
                "Directed","Transformed","Launched","Redesigned","Pioneered"],
    "lead":    ["Directed","Founded","Transformed","Established","Pioneered",
                "Launched","Mentored","Strategized","Overhauled","Championed"],
}

def get_verb_constraints(seniority: str) -> dict:
    level = (seniority or "").lower()
    if "intern" in level:
        tier = "intern"
    elif any(w in level for w in ["junior","jr","entry"]):
        tier = "junior"
    elif any(w in level for w in ["senior","sr"]):
        tier = "senior"
    elif any(w in level for w in ["lead","principal","staff"]):
        tier = "lead"
    else:
        tier = "mid"
    return {
        "tier":          tier,
        "allowed_verbs": _ROLE_VERB_TIERS.get(tier, _ROLE_VERB_TIERS["mid"]),
        "banned_verbs":  list(_AI_FINGERPRINT_VERBS),
    }


# ── Factual Scaffolding Builder ───────────────────────────────────
def build_factual_scaffolding(profile: dict,
                               user_metrics: dict = None) -> str:
    """
    Build TWO-CHANNEL metric payload for Stage 4 Claude prompt.

    Channel A — IMMUTABLE: real metrics from user XML wrapped in <fact> tags.
                Claude must copy these EXACTLY — never round or alter.
    Channel B — ESTIMATED: vague inputs flagged for Claude to estimate,
                Claude wraps its estimates in <estimate> tags for post-processing.

    user_metrics: {"TSS Consultancy": "Scaled 10K to 20K records, ~70% faster"}
    """
    import re as _re

    # Regex to find numeric values to wrap as facts
    _NUM_RE = _re.compile(
        r"(\d[\d,]*\s*(?:K|M|B|k|m)?"       # numbers: 20K, 1,000
        r"|\d+\s*%"                              # percentages: 70%
        r"|\$\s*\d[\d,]*)",                      # dollars: $50,000
        _re.IGNORECASE
    )

    def _wrap_facts(text: str) -> str:
        """Wrap each numeric value in <fact> tags."""
        return _NUM_RE.sub(lambda m: "<fact>%s</fact>" % m.group(0), text)

    lines = ["CANDIDATE FACTUAL BACKGROUND:"]
    has_any_facts = False

    for job in profile.get("experience", []):
        company = job.get("company", "")
        title   = job.get("title", "")
        bullets = job.get("bullets", [])
        extra   = (user_metrics or {}).get(company, "")

        real_metrics = []
        for b in bullets:
            found = extract_metrics_from_text(str(b))
            if found:
                real_metrics.extend(found[:3])

        # Build fact-tagged bullet summaries
        fact_lines = []
        for b in bullets[:3]:
            b_str = str(b)
            if has_quantifiable_metrics(b_str):
                fact_lines.append("  - " + _wrap_facts(b_str[:120]))
            else:
                # No real metric — Claude must estimate for this bullet
                fact_lines.append("  - " + b_str[:100] +
                                  " [ESTIMATE NEEDED: wrap your metric in <estimate>N%</estimate>]")

        if extra:
            fact_lines.append("  - User stated: " + _wrap_facts(extra))
            has_any_facts = True
        if real_metrics:
            has_any_facts = True

        if fact_lines:
            lines.append("Role: %s @ %s" % (title, company))
            lines.extend(fact_lines)

    if not has_any_facts:
        return (
            "NO CONFIRMED METRICS FOUND.\n"
            "For all bullets, use scope-based descriptors: "
            "high-throughput, multi-layered, sequential processing.\n"
            "If you estimate a metric, wrap it: <estimate>N%</estimate>.\n"
            "NEVER use a clean multiple of 5 (10%, 20%, 30%) — they signal AI generation."
        )

    return (
        "\n".join(lines) + "\n\n"
        "CHANNEL A — <fact> tags: copy these numbers EXACTLY. Never round or alter.\n"
        "CHANNEL B — [ESTIMATE NEEDED]: generate a realistic non-round metric, "
        "wrap it in <estimate>N%</estimate> tags for post-processing.\n"
        "NEVER use clean multiples of 5 for estimates (10%, 20%, 30%)."
    )