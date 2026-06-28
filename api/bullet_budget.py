# api/bullet_budget.py
# Stage 3: Pure Python bullet budget calculator — zero LLM tokens
# Assigns bullet counts to each job based on recency + skill relevance
# Prevents hallucination by giving Claude an exact constraint per job

import re
from datetime import datetime

try:
    from core.logger import log_debug
except Exception:
    def log_debug(m,*a): pass


def _recency_score(duration: str) -> float:
    """
    Score 0.0-1.0 based on how recent the role was.
    Current/recent roles score higher.
    """
    if not duration:
        return 0.5
    text = duration.lower()
    # Current role — check all common "still working here" indicators
    if any(w in text for w in ["present", "current", "now", "ongoing",
                                "today", "till date", "to date", "-", "–"]):
        # Only current if the dash/hyphen is trailing (e.g. "2022 -")
        if "-" in text or "–" in text:
            # Check if end date is missing or is "present"
            parts = [p.strip() for p in text.replace("–","-").split("-")]
            last = parts[-1].strip() if parts else ""
            if not last or any(w in last for w in
                               ["present","current","now","ongoing","today",""]):
                return 1.0
        else:
            return 1.0
    # Try to extract end year
    years = re.findall(r"\b(20\d{2})\b", text)
    if years:
        end_year = max(int(y) for y in years)
        current_year = datetime.now().year
        age = current_year - end_year
        if age <= 1:   return 1.0
        if age <= 2:   return 0.85
        if age <= 3:   return 0.70
        if age <= 5:   return 0.50
        if age <= 8:   return 0.30
        return 0.15
    return 0.5


# Semantic skill synonyms — abstract JD terms → concrete keywords
_SKILL_SYNONYMS = {
    "backend development":    ["api", "rest", "server", "backend", "service", "endpoint",
                               ".net", "python", "java", "node", "sql", "database"],
    "api design":             ["api", "rest", "endpoint", "json", "http", "swagger",
                               "graphql", "grpc", "webhook"],
    "system architecture":    ["architect", "design", "microservice", "scalab", "distribut",
                               "infrastructure", "pipeline", "module"],
    "database management":    ["sql", "database", "db", "query", "schema", "postgres",
                               "mysql", "oracle", "mongodb", "redis"],
    "scalable services":      ["scalab", "load", "performance", "throughput", "concurrent",
                               "pipeline", "record", "million", "batch"],
    "code review":            ["review", "pr", "pull request", "quality", "standard",
                               "best practice", "mentor"],
    "production troubleshooting": ["debug", "fix", "resolve", "incident", "issue",
                                   "production", "timeout", "error", "bug", "root cause"],
    "frontend development":   ["react", "angular", "vue", "html", "css", "javascript",
                               "ui", "component", "frontend"],
    "cloud":                  ["aws", "azure", "gcp", "cloud", "s3", "lambda", "ec2"],
    "devops":                 ["ci/cd", "docker", "kubernetes", "jenkins", "pipeline",
                               "deploy", "git", "github actions"],
}

def _skill_overlap_score(job_bullets: list, job_title: str,
                          jd_skills: list) -> float:
    """
    Score 0.0-1.0: semantic match of JD skills against job content.
    Uses synonym expansion for abstract skill terms like "backend development".
    """
    if not jd_skills:
        return 0.5
    haystack = " ".join([job_title or ""] + (job_bullets or [])).lower()
    matched = 0
    for skill in jd_skills:
        skill_l = skill.lower()
        # Direct match
        if skill_l in haystack:
            matched += 1
            continue
        # Synonym match
        synonyms = _SKILL_SYNONYMS.get(skill_l, [])
        if not synonyms:
            # Try partial: "backend development" → match "backend" or "development"
            parts = skill_l.split()
            synonyms = parts
        if any(syn in haystack for syn in synonyms):
            matched += 1
    return matched / len(jd_skills)


def calculate_bullet_budget(profile: dict, jd_metadata: dict) -> dict:
    """
    Stage 3: Assign bullet counts to each job — pure Python, zero LLM tokens.

    Budget scale:
        4 bullets: recent + highly relevant  (recency > 0.7 AND overlap > 0.5)
        3 bullets: recent OR highly relevant
        2 bullets: moderate match
        1 bullet:  older/less relevant — keep for continuity
        0 bullets: very old + no relevance — omit entirely

    Returns:
        {
            "budgets": [
                {"company": "TSS", "title": "...", "bullets": 4},
                {"company": "...", "title": "...", "bullets": 2},
            ],
            "total_bullets": 10,
            "jd_skills": [...],
        }
    """
    jd_skills    = jd_metadata.get("skills", [])
    experience   = profile.get("experience", [])
    budgets      = []
    total        = 0

    for job in experience:
        company  = job.get("company", "")
        title    = job.get("title", "")
        duration = job.get("duration", "") or job.get("dates", "")
        bullets  = job.get("bullets", [])

        recency  = _recency_score(duration)
        overlap  = _skill_overlap_score(bullets, title, jd_skills)

        # Combined score weighted: recency 40%, overlap 60%
        score = (recency * 0.4) + (overlap * 0.6)

        if score >= 0.65:   budget = 5
        elif score >= 0.50: budget = 4
        elif score >= 0.35: budget = 3
        elif score >= 0.20: budget = 2
        else:               budget = 1   # always at least 1 for continuity

        # Primary job (first in list) always gets at least 3 bullets
        # regardless of score — it's the most important role
        if not budgets and budget < 3:
            budget = 3

        budgets.append({
            "company":  company,
            "title":    title,
            "duration": duration,
            "budget":   budget,
            "score":    round(score, 2),
        })
        total += budget

        log_debug("Bullet budget: %s @ %s → %d bullets (score=%.2f)" % (
            title, company, budget, score))

    result = {
        "budgets":       budgets,
        "total_bullets": total,
        "jd_skills":     jd_skills,
    }
    log_debug("Total bullet budget: %d bullets across %d jobs" % (
        total, len(budgets)))
    return result