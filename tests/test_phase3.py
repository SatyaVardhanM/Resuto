"""
tests/test_phase3.py — the per-job Applied / Skip / Stop step and GUI wiring.
Run: python -m pytest tests -q
"""
import ast
import asyncio
import builtins
import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import tests.test_fixes  # noqa: F401,E402  (installs GUI/API stubs)

try:
    import playwright.async_api  # noqa: F401
except ImportError:            # tests don't drive a real browser
    import types
    _pw = types.ModuleType("playwright"); _api = types.ModuleType("playwright.async_api")
    _api.BrowserContext = object; _api.Page = object; _api.async_playwright = None
    _pw.async_api = _api
    sys.modules["playwright"] = _pw; sys.modules["playwright.async_api"] = _api


class _Page:
    url = "about:blank"
    async def goto(self, url, **k): self.url = url
    async def bring_to_front(self): pass


class _Ctx:
    def __init__(self): self.pages = [_Page()]
    async def new_page(self): return _Page()


def _run_apply(monkeypatch, capsys, answer):
    import backend.browser as b
    import db.tracker as t
    outcomes = []
    monkeypatch.setattr(t, "mark_job_outcome", lambda rid, st: outcomes.append((rid, st)))
    _real_sleep = asyncio.sleep
    monkeypatch.setattr(b.asyncio, "sleep", lambda *_: _real_sleep(0))
    monkeypatch.setattr(builtins, "input", lambda *_: answer)
    job = {"id": 7, "job_title": "QA Engineer", "company": "Acme",
           "job_url": "https://www.linkedin.com/jobs/view/7/",
           "pdf_path": r"C:\out\Acme\resume.pdf", "match_score": 81}
    res = asyncio.run(b.apply_to_job(_Ctx(), job, 2, 4))
    out = capsys.readouterr().out
    line = [l for l in out.splitlines() if l.startswith("BOT_APPLY:")][0]
    return res, outcomes, json.loads(line[len("BOT_APPLY:"):])


def test_apply_card_answers_match_bot(monkeypatch, capsys):
    """The exact strings the GUI buttons send are accepted by the bot."""
    res, outc, data = _run_apply(monkeypatch, capsys, "applied")
    assert res == "applied" and outc == [(7, "applied")]
    assert data["opened"] and data["index"] == 2 and data["total"] == 4
    assert data["score"] == 81 and data["resume"].endswith("resume.pdf")

    res, outc, _ = _run_apply(monkeypatch, capsys, "skip")
    assert res == "skipped" and outc == [(7, "skipped")]

    res, outc, _ = _run_apply(monkeypatch, capsys, "stop")
    assert res == "stop" and outc == []


def test_gui_buttons_send_valid_answers():
    """Parse run_view.py: the apply-card buttons send applied/skip/stop."""
    src = open(os.path.join(ROOT, "frontend", "views", "run_view.py"), encoding="utf-8").read()
    assert '"applied"' in src and '"skip"' in src and '"stop"' in src
    assert "BOT_APPLY:" in src and "BOT_WAITING" not in src


def test_every_private_attribute_used_is_defined():
    """Catches bugs like self._run_frame (used, never created)."""
    files = glob.glob(os.path.join(ROOT, "frontend", "*.py")) + \
            glob.glob(os.path.join(ROOT, "frontend", "views", "*.py"))
    used, defined = {}, set()
    for f in files:
        tree = ast.parse(open(f, encoding="utf-8").read())
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                defined.add(node.name)
            elif isinstance(node, ast.ClassDef):
                for b in node.body:
                    if isinstance(b, ast.Assign):
                        defined.update(t.id for t in b.targets if isinstance(t, ast.Name))
                    elif isinstance(b, ast.AnnAssign) and isinstance(b.target, ast.Name):
                        defined.add(b.target.id)
            elif (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                  and node.value.id == "self"):
                if isinstance(node.ctx, ast.Store):
                    defined.add(node.attr)
                else:
                    used.setdefault(node.attr, set()).add(os.path.basename(f))
    missing = {k: v for k, v in used.items() if k.startswith("_") and k not in defined}
    assert not missing, missing


def test_no_method_shadowed_between_app_mixins():
    """Two mixins defining the same method = one silently wins (MRO)."""
    files = ["app.py", "views/run_view.py", "views/history_view.py",
             "views/stats_view.py", "views/settings_view.py"]
    names = {"App", "RunMixin", "HistoryMixin", "StatsMixin", "SettingsMixin"}
    seen = {}
    for f in files:
        tree = ast.parse(open(os.path.join(ROOT, "frontend", f), encoding="utf-8").read())
        for cls in (n for n in tree.body if isinstance(n, ast.ClassDef) and n.name in names):
            for n in cls.body:
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    seen.setdefault(n.name, []).append(cls.name)
    dupes = {k: v for k, v in seen.items() if len(v) > 1}
    assert not dupes, dupes


def _run_loop(monkeypatch, answers, continue_answer, n_jobs=3, max_jobs=1):
    import backend.orchestrator as o
    import backend.browser as b
    o._STATE.update(stop=False, applied=0, skipped=0)
    seen = []
    async def fake_apply(context, job, idx, total, applied=None, limit=None):
        seen.append((idx, total, applied, limit))
        return answers.pop(0)
    asked = []
    async def fake_continue(limit, left):
        asked.append((limit, left))
        return continue_answer
    monkeypatch.setattr(b, "apply_to_job", fake_apply)
    monkeypatch.setattr(o, "_ask_continue_after_limit", fake_continue)
    jobs = [{"id": i} for i in range(1, n_jobs + 1)]
    total = asyncio.run(o._apply_queue_loop(None, jobs, 0, max_jobs, False))
    return total, seen, asked, dict(o._STATE)


def test_limit_reached_can_continue_through_queue(monkeypatch):
    # skip, apply (limit 1 reached) → asked → continue → apply the last one
    total, seen, asked, st = _run_loop(monkeypatch, ["skipped", "applied", "applied"], True, 3)
    assert total == 2 and asked == [(1, 1)]
    assert [s[0] for s in seen] == [1, 2, 3] and seen[0][3] == 1   # limit shown on card
    assert st["applied"] == 2 and st["skipped"] == 1


def test_limit_reached_finish_stops(monkeypatch):
    total, seen, asked, st = _run_loop(monkeypatch, ["applied"], False, 3)
    assert total == 1 and asked == [(1, 2)] and len(seen) == 1


def test_bot_parser_has_apply_queue():
    from backend.orchestrator import build_arg_parser
    a, _ = build_arg_parser().parse_known_args(["--gui", "--apply-queue"])
    assert a.apply_queue


# ── One job at a time: decide BEFORE the resume is made ───────────
def _run_decide(monkeypatch, capsys, answer):
    import backend.browser as b
    import db.tracker as t
    outcomes = []
    monkeypatch.setattr(t, "mark_job_outcome", lambda rid, st: outcomes.append((rid, st)))
    _real_sleep = asyncio.sleep
    monkeypatch.setattr(b.asyncio, "sleep", lambda *_: _real_sleep(0))
    monkeypatch.setattr(builtins, "input", lambda *_: answer)
    job = {"id": 9, "job_title": "SDET", "company": "Beta",
           "job_url": "https://www.linkedin.com/jobs/view/9/",
           "match_score": 77, "ai_reason": "Strong C# overlap"}
    res = asyncio.run(b.decide_job(_Ctx(), job, 1, 3, applied=0, limit=5))
    out = capsys.readouterr().out
    line = [l for l in out.splitlines() if l.startswith("BOT_DECIDE:")][0]
    return res, outcomes, json.loads(line[len("BOT_DECIDE:"):])


def test_decide_card_answers_match_bot(monkeypatch, capsys):
    res, outc, data = _run_decide(monkeypatch, capsys, "tailor")
    assert res == "tailor" and outc == []                 # nothing marked yet
    assert data["score"] == 77 and data["limit"] == 5 and "resume" not in data
    res, outc, _ = _run_decide(monkeypatch, capsys, "skip")
    assert res == "skipped" and outc == [(9, "skipped")]
    res, outc, _ = _run_decide(monkeypatch, capsys, "finish")
    assert res == "finish" and outc == []


def test_gui_handles_decide_step():
    src = open(os.path.join(ROOT, "frontend", "views", "run_view.py"), encoding="utf-8").read()
    assert "BOT_DECIDE:" in src and '"tailor"' in src and '"finish"' in src


def test_one_at_a_time_tailors_only_after_decision():
    """In the orchestrator, resume generation must come after decide_job."""
    src = open(os.path.join(ROOT, "backend", "orchestrator.py"), encoding="utf-8").read()
    i_dec = src.find("decide_job(")
    assert i_dec > 0
    i_gen = src.find("batch_generate_resumes,", i_dec)   # run_in_executor call
    assert i_gen > i_dec
    assert 'decision != "tailor"' in src or "decision == \"tailor\"" in src \
        or src.find("continue", i_dec) < i_gen
