"""
tests/test_fixes.py — regression tests for the Sept 2026 review fixes.
Run from the project root:  python -m pytest tests -q
GUI / network libraries are stubbed so these run headless on any OS.
"""
import json
import os
import sys
import types

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


# ── Stubs for GUI / API libraries ─────────────────────────────────
class _Any:
    def __init__(self, *a, **k): pass
    def __getattr__(self, n): return _Any()
    def __call__(self, *a, **k): return _Any()


def _stub(name, **attrs):
    m = types.ModuleType(name)
    m.__getattr__ = lambda n: _Any
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules.setdefault(name, m)


for _m in ("customtkinter", "tkinter", "tkinter.messagebox",
           "tkinter.filedialog", "tkinter.ttk", "tkinter.font"):
    _stub(_m)
if "anthropic" not in sys.modules:
    try:
        import anthropic  # noqa: F401
    except ImportError:
        _stub("anthropic", Anthropic=_Any, AuthenticationError=type("AE", (Exception,), {}))


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Keep Documents\\Resuto writes inside a temp dir."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    yield


# ── Run tab: bot output no longer crashes the line handler ────────
def test_handle_line_does_not_crash():
    from frontend.views.run_view import RunMixin

    class Fake(RunMixin):
        def __init__(self):
            self.errors, self.bars = [], []
            self._tb_buffer, self._tb_active = [], False
            self._current_phase = 0
            self._apply_prompt_shown = False
            self._run_summary = {}
        def __getattr__(self, n): return _Any()
        def _append_error(self, text): self.errors.append(text)
        def _show_action_bar(self, kind): self.bars.append(kind)

    f = Fake()
    for line in ["[OK] API key is valid.",
                 "   Ready to start applying now? [y / n]: ",
                 "[BOT_IDLE]"]:
        RunMixin._handle_line(f, line)
    assert f.bars == ["yn_apply"]      # the apply prompt now appears

    # A traceback is reported as ONE error, not fragments
    for line in ["Traceback (most recent call last):",
                 '  File "x.py", line 1, in <module>',
                 "    boom()",
                 "ValueError: bad value"]:
        RunMixin._handle_line(f, line)
    assert len(f.errors) == 1 and f.errors[0].endswith("ValueError: bad value")


# ── Regexes: \b instead of literal backspace characters ──────────
def test_no_backspace_characters_in_source():
    for dirpath, _, files in os.walk(ROOT):
        if ".git" in dirpath:
            continue
        for fn in files:
            if fn.endswith(".py"):
                with open(os.path.join(dirpath, fn), "rb") as fh:
                    assert b"\x08" not in fh.read(), fn


def test_acronyms_detected():
    from api.jd_parser import extract_niche_acronyms
    assert extract_niche_acronyms("Experience with CKYC, CERSAI and HIPAA") == \
        ["CERSAI", "CKYC", "HIPAA"]


# ── Bot flags: one parser for source + exe, filters normalised ───
def test_bot_parser_and_filters():
    from backend.orchestrator import build_arg_parser, _normalize_filter_values
    args, _ = build_arg_parser().parse_known_args([
        "--location", "Remote", "--max-jobs", "3", "--mode", "all",
        "--date-posted", "week", "--exp-levels", "entry", "associate",
        "--job-types", "full_time", "--workplace", "remote",
        "--application-mode", "one_at_a_time", "--roles", "QA Engineer", "SDET"])
    assert args.roles == ["QA Engineer", "SDET"]
    assert args.application_mode == "one_at_a_time"
    exp = {"1": "internship", "2": "entry", "3": "associate"}
    assert _normalize_filter_values(args.exp_levels, exp) == ["entry", "associate"]
    assert _normalize_filter_values(["2"], exp) == ["entry"]
    assert _normalize_filter_values([], exp) is None

    p2, _ = build_arg_parser().parse_known_args(["--phase2-only", "--gui", "--job-ids", "7"])
    assert p2.phase2_only and p2.job_ids == [7]


# ── Access control ────────────────────────────────────────────────
class FakeSheet:
    def __init__(self, rows=None, fail_read=False):
        from core import license as lic
        self.headers = lic.HEADERS
        self.rows = rows or []
        self.fail_read = fail_read
        self.appended = []
    def get_all_records(self):
        if self.fail_read:
            raise ConnectionError("connection reset")
        return [dict(zip(self.headers, r)) for r in self.rows]
    def append_row(self, row):
        self.appended.append(row); self.rows.append(row)
    def update_cell(self, r, c, v):
        self.rows[r - 2][c - 1] = v


def _row(email, key_field, status):
    return [email, "Test User", key_field, "[]", "", "", "", "", "", status]


@pytest.fixture
def lic(monkeypatch):
    from core import license as lic
    monkeypatch.setattr(lic, "validate_api_key", lambda k: "ok")
    monkeypatch.setattr(lic, "_notify_admin", lambda *a, **k: True)
    monkeypatch.setattr(lic, "get_ip", lambda: "10.0.0.1")
    return lic


def test_login_requires_approval(lic, monkeypatch):
    key = "sk-ant-test-1"
    sheet = FakeSheet([_row("a@x.com", lic.hash_api_key(key), "pending")])
    monkeypatch.setattr(lic, "_worksheet", lambda: sheet)
    assert lic.login_user("a@x.com", key) == "pending"
    sheet.rows[0][9] = "rejected"
    assert lic.login_user("a@x.com", key) == "rejected"
    sheet.rows[0][9] = "revoked"
    assert lic.login_user("a@x.com", key) == "revoked"
    sheet.rows[0][9] = "approved"
    assert lic.login_user("a@x.com", key) == "ok"


def test_key_change_cannot_take_over_account(lic, monkeypatch):
    sheet = FakeSheet([_row("victim@x.com", lic.hash_api_key("sk-ant-victim"), "approved")])
    monkeypatch.setattr(lic, "_worksheet", lambda: sheet)
    assert lic.login_user("victim@x.com", "sk-ant-attacker") == "key_changed"
    assert lic.update_api_key("victim@x.com", "sk-ant-attacker") == "pending"
    # Attacker still can't get in until the admin approves
    assert lic.login_user("victim@x.com", "sk-ant-attacker") == "pending"


def test_keys_are_hashed_and_legacy_rows_migrate(lic, monkeypatch):
    sheet = FakeSheet([_row("old@x.com", "sk-ant-plain", "approved")])
    monkeypatch.setattr(lic, "_worksheet", lambda: sheet)
    assert lic.login_user("old@x.com", "sk-ant-plain") == "ok"
    assert sheet.rows[0][2] == lic.hash_api_key("sk-ant-plain")

    assert lic.register_user("New User", "New@X.com", "sk-ant-new") == "ok"
    stored = sheet.appended[-1]
    assert stored[2].startswith("sha256:") and "sk-ant-new" not in json.dumps(stored)
    assert stored[9] == "pending"


def test_sheet_read_failure_does_not_duplicate(lic, monkeypatch):
    sheet = FakeSheet([_row("a@x.com", "x", "approved")], fail_read=True)
    monkeypatch.setattr(lic, "_worksheet", lambda: sheet)
    assert lic.register_user("A", "a@x.com", "sk-ant-1") == "network_error"
    assert sheet.appended == []
    assert lic.login_user("a@x.com", "sk-ant-1") == "network_error"


def test_telegram_callback_data_fits():
    from core.license import _email_token
    email = "a.very.long.email.address.for.testing_purposes@some-long-domain.example.com"
    assert len(("approve:" + _email_token(email)).encode()) <= 64   # Telegram limit


# ── Settings: one file, old files merged once ────────────────────
def test_settings_migration(tmp_path, monkeypatch):
    import importlib
    import core.settings as cs
    importlib.reload(cs)
    legacy = tmp_path / "legacy_local_settings.json"
    legacy.write_text(json.dumps({"api_key": "sk-ant-old", "font_size": 16}))
    monkeypatch.setattr(cs, "_legacy_settings_files", lambda: [str(legacy)])
    path = cs._settings_file()
    assert path.startswith(str(tmp_path))
    data = json.loads(open(path, encoding="utf-8").read())
    assert data["api_key"] == "sk-ant-old" and data["font_size"] == 16
    assert not legacy.exists() and (tmp_path / "legacy_local_settings.json.migrated").exists()
    cs.update(api_key=None)
    assert "api_key" not in cs.load_all()


# ── Updater refuses a tampered download ───────────────────────────
def test_updater_rejects_bad_hash(tmp_path, monkeypatch):
    import core.updater as up
    import requests

    class Resp:
        def __init__(self, body): self.body = body; self.text = body.decode(errors="ignore"); self.headers = {"content-length": str(len(body))}
        def raise_for_status(self): pass
        def iter_content(self, chunk_size=1): yield self.body

    def fake_get(url, **k):
        if url.endswith(".sha256"):
            return Resp(b"0" * 64 + b"  Resuto-Setup.exe")
        return Resp(b"not the real installer")

    launched = []
    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(up.subprocess, "Popen", lambda *a, **k: launched.append(a))
    monkeypatch.setattr(up.tempfile, "gettempdir", lambda: str(tmp_path))
    assert up.download_and_install("https://x/Resuto-Setup.exe", "9.9.9",
                                   sha256_url="https://x/Resuto-Setup.exe.sha256") is False
    assert launched == []
    assert up.download_and_install("https://x/Resuto-Setup.exe", "9.9.9") is False


# ── Continuous mode: jobs with resume_ready reach Phase 3 ─────────
def test_ready_jobs_include_resume_ready(tmp_path, monkeypatch):
    import db.tracker as t
    monkeypatch.setattr(t, "DB_FILE", str(tmp_path / "applications.db"))
    job = {"url": "https://www.linkedin.com/jobs/view/123/", "title": "QA",
           "company": "Acme", "description": "jd"}
    rid = t.save_scanning_job(job)
    t.update_job_relevance(rid, job, {"is_relevant": True, "match_score": 80})
    t.mark_resume_ready(rid, "a.docx", "a.pdf")
    ready = t.get_jobs_ready_to_apply()
    assert [r["id"] for r in ready] == [rid]


def test_continuous_mode_apply_skip_loop(monkeypatch):
    """Continuous mode must not crash (remaining was undefined) and must
    show Apply/Skip for each ready job."""
    import asyncio
    import backend.orchestrator as o

    async def fake_search(*a, **k):
        yield {"url": "https://www.linkedin.com/jobs/view/1/", "title": "QA Engineer",
               "company": "Acme", "description": "jd"}

    stubs = {
        "backend.scraper": dict(continuous_job_search=fake_search,
                                expand_keyword=lambda k: []),
        "api.filter": dict(check_job_relevance=lambda *a, **k: {"is_relevant": True,
                                                               "match_score": 90},
                           print_relevance_report=lambda *a, **k: None),
        "api.resume_gen": dict(batch_generate_resumes=lambda *a, **k: {}),
        "backend.browser": dict(apply_to_job=None),   # replaced below
    }
    for name, attrs in stubs.items():
        m = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(m, k, v)
        monkeypatch.setitem(sys.modules, name, m)

    import db.tracker as t
    calls = {"waits": 0, "skipped": []}
    monkeypatch.setattr(t, "load_applied_urls", lambda: set())
    monkeypatch.setattr(t, "save_scanning_job", lambda job: 1)
    monkeypatch.setattr(t, "update_job_relevance", lambda *a, **k: None)
    monkeypatch.setattr(t, "get_jobs_ready_to_apply",
                        lambda: [{"id": 1, "job_title": "QA Engineer", "company": "Acme",
                                  "job_url": "u", "match_score": 90}])
    monkeypatch.setattr(t, "mark_job_outcome",
                        lambda rid, st: calls["skipped"].append((rid, st)))
    monkeypatch.setattr(o, "extract_jd_metadata", lambda *a, **k: {"skills": []})

    async def fake_apply(context, job, idx, total, **k):
        calls["waits"] += 1
        calls["skipped"].append((job["id"], "skipped"))
        return "skipped"
    sys.modules["backend.browser"].apply_to_job = fake_apply

    total = asyncio.run(o.run_applications(
        None, None, None, "QA Engineer", "Remote", 5, False, 0,
        "QA Engineer", "easy_apply", {"experience": []},
        application_mode="continuous"))
    assert total == 0
    assert calls["waits"] == 1 and calls["skipped"] == [(1, "skipped")]
    assert o._STATE["stop"] is False
