"""
core/costs.py — Claude API cost tracking and run-cost estimates.

Anthropic has no API for reading your credit balance, so Resuto:
  1. records the token usage of every Claude call (response.usage),
  2. converts it to dollars with the model prices below,
  3. learns the average cost per generated resume from your recent runs,
  4. shows an estimate before each run so you can compare it with the
     balance shown on the Anthropic billing page.

It also turns Anthropic's "credit balance is too low" error into
CreditBalanceError so the bot stops at the first billing failure instead
of failing every remaining job.

Data: <output dir>/api_usage.db  (tables: usage, runs)
"""
import os
import sys
import sqlite3
import threading
from datetime import datetime

BILLING_URL = "https://platform.claude.com/settings/billing"

# $ per million tokens (input, output) — https://platform.claude.com/docs/en/about-claude/pricing
# Matched by substring of the model name; unknown models use Sonnet prices.
PRICES = {
    "haiku":  (1.0, 5.0),
    "sonnet": (3.0, 15.0),
}
_DEFAULT_PRICE = PRICES["sonnet"]

# Starting estimate per generated resume, INCLUDING the jobs scanned and
# rejected on the way (relevance checks) — replaced by your real average
# once a few runs have been recorded.
DEFAULT_PER_JOB      = 0.20
DEFAULT_PER_JOB_HIGH = 0.40
LEARN_FROM_LAST_RUNS = 10

CREDIT_MSG = ("Your Anthropic credit balance is too low. "
              "Add credits at " + BILLING_URL + " and start the run again.")


class CreditBalanceError(BaseException):
    """
    Raised when Anthropic rejects a call for lack of credit.
    Deliberately a BaseException: the bot has many broad `except Exception`
    blocks (per job, per step) that would otherwise swallow it and keep
    trying — every further call would fail the same way.
    """


_lock        = threading.Lock()
_CURRENT_RUN = ""
_installed   = False


# ── Storage ───────────────────────────────────────────────────────
def _db_path() -> str:
    try:
        from core.settings import get_output_dir
        base = get_output_dir()
    except Exception:
        base = "output"
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "api_usage.db")


def _connect():
    conn = sqlite3.connect(_db_path(), timeout=10)
    conn.execute("""CREATE TABLE IF NOT EXISTS usage (
        ts TEXT, run_id TEXT, stage TEXT, model TEXT,
        input_tokens INTEGER, output_tokens INTEGER, cost REAL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS runs (
        run_id TEXT PRIMARY KEY, jobs INTEGER, finished_at TEXT)""")
    return conn


# ── Pricing ───────────────────────────────────────────────────────
def price_for(model: str) -> tuple:
    m = (model or "").lower()
    for key, price in PRICES.items():
        if key in m:
            return price
    return _DEFAULT_PRICE


def cost_of(model: str, input_tokens: int, output_tokens: int,
            cache_write: int = 0, cache_read: int = 0) -> float:
    p_in, p_out = price_for(model)
    return ((input_tokens or 0) * p_in
            + (cache_write or 0) * p_in * 1.25
            + (cache_read or 0) * p_in * 0.10
            + (output_tokens or 0) * p_out) / 1_000_000


# ── Recording ─────────────────────────────────────────────────────
def set_run(run_id: str) -> None:
    """Tag every following Claude call with this run id."""
    global _CURRENT_RUN
    _CURRENT_RUN = run_id or ""


def record(model: str, usage, stage: str = "") -> float:
    """Store one call's token usage. Returns its cost in dollars."""
    if usage is None:
        return 0.0
    inp  = int(getattr(usage, "input_tokens", 0) or 0)
    out  = int(getattr(usage, "output_tokens", 0) or 0)
    cw   = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
    cr   = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
    cost = cost_of(model, inp, out, cw, cr)
    with _lock:
        conn = _connect()
        try:
            conn.execute("INSERT INTO usage VALUES (?,?,?,?,?,?,?)",
                         (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                          _CURRENT_RUN, stage, model or "",
                          inp + cw + cr, out, cost))
            conn.commit()
        finally:
            conn.close()
    return cost


def finish_run(run_id: str, jobs: int) -> None:
    """Record how many resumes a run generated (used to learn cost per job)."""
    if not run_id:
        return
    with _lock:
        conn = _connect()
        try:
            conn.execute("INSERT OR REPLACE INTO runs VALUES (?,?,?)",
                         (run_id, int(jobs or 0),
                          datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            conn.commit()
        finally:
            conn.close()


def run_cost(run_id: str) -> float:
    conn = _connect()
    try:
        row = conn.execute("SELECT COALESCE(SUM(cost),0) FROM usage WHERE run_id=?",
                           (run_id,)).fetchone()
        return float(row[0] or 0)
    finally:
        conn.close()


# ── Estimates ─────────────────────────────────────────────────────
def per_job_cost() -> dict:
    """
    Average cost per generated resume over the last runs that produced one
    (scanning + relevance checks for rejected jobs are included in the run
    total, so they are spread over the resumes).
    """
    try:
        conn = _connect()
        try:
            rows = conn.execute(f"""
                SELECT r.jobs, COALESCE(SUM(u.cost), 0)
                FROM runs r LEFT JOIN usage u ON u.run_id = r.run_id
                WHERE r.jobs > 0
                GROUP BY r.run_id
                ORDER BY r.finished_at DESC
                LIMIT {int(LEARN_FROM_LAST_RUNS)}""").fetchall()
        finally:
            conn.close()
    except Exception:
        rows = []
    jobs = sum(r[0] for r in rows)
    cost = sum(r[1] for r in rows)
    if jobs >= 3 and cost > 0:
        avg = cost / jobs
        return {"typical": avg, "high": avg * 2, "learned": True, "runs": len(rows)}
    return {"typical": DEFAULT_PER_JOB, "high": DEFAULT_PER_JOB_HIGH,
            "learned": False, "runs": len(rows)}


def estimate_run(max_jobs: int, roles: int = 1) -> dict:
    """
    Estimated cost of a run. max_jobs is PER ROLE (each searched role can
    match up to max_jobs jobs); 0 = unlimited → the MAX_JOBS_PER_RUN cap.
    """
    try:
        from core.config import MAX_JOBS_PER_RUN
    except Exception:
        MAX_JOBS_PER_RUN = 20
    try:
        mj = int(max_jobs)
    except (TypeError, ValueError):
        mj = 5
    if mj <= 0:
        mj = MAX_JOBS_PER_RUN
    mj    = min(mj, MAX_JOBS_PER_RUN)
    roles = max(1, int(roles or 1))
    pj    = per_job_cost()
    jobs  = mj * roles
    return {
        "jobs":       jobs,
        "per_role":   mj,
        "roles":      roles,
        "typical":    round(jobs * pj["typical"], 2),
        "high":       round(jobs * pj["high"], 2),
        "per_job":    pj["typical"],
        "learned":    pj["learned"],
        "runs":       pj["runs"],
    }


# ── Automatic tracking of every Claude call ───────────────────────
def is_credit_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return ("credit balance is too low" in msg
            or ("billing" in msg and "credit" in msg))


def _caller_module() -> str:
    """Module that made the Claude call (e.g. 'api.resume_gen')."""
    f = sys._getframe(2)
    while f is not None:
        name = f.f_globals.get("__name__", "")
        if not (name.startswith("anthropic") or name == __name__):
            return name
        f = f.f_back
    return ""


def install_usage_tracking(fail_fast: bool = True) -> None:
    """
    Wrap anthropic's Messages.create once so every call is recorded.
    fail_fast=True (bot process): a credit error raises CreditBalanceError.
    fail_fast=False (GUI process): errors pass through unchanged.
    """
    global _installed
    if _installed:
        return
    try:
        from anthropic.resources.messages import Messages
    except Exception:
        return
    original = Messages.create

    def create(self, *args, **kwargs):
        try:
            resp = original(self, *args, **kwargs)
        except Exception as e:
            if fail_fast and is_credit_error(e):
                raise CreditBalanceError(CREDIT_MSG) from e
            raise
        try:
            record(kwargs.get("model") or getattr(resp, "model", ""),
                   getattr(resp, "usage", None), _caller_module())
        except Exception:
            pass   # tracking must never break a run
        return resp

    Messages.create = create
    _installed = True
