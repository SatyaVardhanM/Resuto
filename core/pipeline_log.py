# core/pipeline_log.py
# Structured transaction logging for the 4-stage pipeline.
# Every job gets a transaction_id. Every stage logs its status.
# If a resume comes out wrong, look up the tx_id → know exactly where it broke.

import sys as _sys, os as _os
if not getattr(_sys, "frozen", False):
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import uuid
import json
import time
from datetime import datetime

try:
    from core.logger import log, log_debug, log_error
except Exception:
    def log(m,*a): pass
    def log_debug(m,*a): pass
    def log_error(m,*a,**k): pass


class PipelineTransaction:
    """
    Tracks one job through all 4 stages.
    Attach to a job dict as job["_tx"] for full traceability.

    Usage:
        tx = PipelineTransaction(job_title, company)
        tx.stage2_done(jd_metadata)
        tx.stage3_done(bullet_budget)
        tx.stage4a_done(relevance)
        tx.stage4b_done(tokens_used, validation_passed)
        tx.log_summary()
    """

    def __init__(self, job_title: str, company: str):
        self.tx_id       = "tx_" + uuid.uuid4().hex[:8]
        self.job_title   = job_title
        self.company     = company
        self.started_at  = time.time()
        self.stages      = {}

    def stage2_done(self, jd_metadata: dict):
        self.stages["stage_2"] = {
            "status":   "SUCCESS",
            "keywords": jd_metadata.get("skills", []),
            "acronyms": jd_metadata.get("niche_acronyms", []),
            "mission":  jd_metadata.get("mission",""),
            "seniority":jd_metadata.get("seniority",""),
        }
        log_debug("[%s] Stage 2 done: %d keywords, %d acronyms" % (
            self.tx_id,
            len(jd_metadata.get("skills",[])),
            len(jd_metadata.get("niche_acronyms",[]))))

    def stage2_failed(self, error: str):
        self.stages["stage_2"] = {"status": "FAILED", "error": str(error)[:200]}
        log_error("[%s] Stage 2 failed: %s" % (self.tx_id, error))

    def stage3_done(self, bullet_budget: dict):
        budgets = {b["company"]: b["budget"]
                   for b in bullet_budget.get("budgets", [])}
        self.stages["stage_3"] = {
            "status":         "SUCCESS",
            "bullet_budgets": budgets,
            "total_bullets":  bullet_budget.get("total_bullets", 0),
        }
        log_debug("[%s] Stage 3 done: %d total bullets — %s" % (
            self.tx_id, bullet_budget.get("total_bullets",0), budgets))

    def stage4a_done(self, relevance: dict):
        self.stages["stage_4a"] = {
            "status":      "SUCCESS",
            "is_relevant": relevance.get("is_relevant"),
            "score":       relevance.get("match_score", 0),
            "reason":      relevance.get("reason","")[:100],
        }
        log_debug("[%s] Stage 4a done: relevant=%s score=%s" % (
            self.tx_id,
            relevance.get("is_relevant"),
            relevance.get("match_score",0)))

    def stage4b_done(self, tokens_used: int, validation_passed: bool,
                     retries: int = 0):
        elapsed = round(time.time() - self.started_at, 1)
        self.stages["stage_4b"] = {
            "status":           "SUCCESS",
            "response_tokens":  tokens_used,
            "validation_passed":validation_passed,
            "retries":          retries,
            "total_elapsed_sec":elapsed,
        }
        log("[%s] Pipeline complete: %ds | tokens=%d | valid=%s | retries=%d" % (
            self.tx_id, elapsed, tokens_used, validation_passed, retries))

    def log_summary(self):
        """Write full transaction JSON to log for post-mortem debugging."""
        summary = {
            "tx_id":     self.tx_id,
            "job":       "%s @ %s" % (self.job_title, self.company),
            "started":   datetime.fromtimestamp(self.started_at).strftime("%H:%M:%S"),
            "stages":    self.stages,
        }
        log_debug("TX SUMMARY: %s" % json.dumps(summary, indent=2))
        return summary