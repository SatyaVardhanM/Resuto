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
    # Current role
    if any(w in text for w in ["present", "current", "now", "ongoing"]):
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


def _skill_overlap_score(job_bullets: list, job_title: str,
                          jd_skills: list) -> float:
    """
    Score 0.0-1.0: how many JD required skills appear in this job's content.
    Pure string matching — no LLM needed.
    """
    if not jd_skills:
        return 0.5
    haystack = " ".join([job_title or ""] + (job_bullets or [])).lower()
    matched = sum(1 for s in jd_skills if s.lower() in haystack)
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

        if score >= 0.65:   budget = 4
        elif score >= 0.50: budget = 3
        elif score >= 0.35: budget = 2
        elif score >= 0.20: budget = 1
        else:               budget = 0

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