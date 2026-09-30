import pytest, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── Cost tracking / estimates ─────────────────────────────────────
def test_costs_estimate_and_tracking(tmp_path, monkeypatch):
    import types as _t
    import core.costs as c
    monkeypatch.setattr(c, "_db_path", lambda: str(tmp_path / "api_usage.db"))

    assert abs(c.cost_of("claude-sonnet-4-5", 1_000_000, 0) - 3.0) < 1e-9
    assert abs(c.cost_of("claude-haiku-4-5-20251001", 0, 1_000_000) - 5.0) < 1e-9

    est = c.estimate_run(5, roles=2)          # no history → default rates
    assert est["jobs"] == 10 and not est["learned"]
    assert est["typical"] == round(10 * c.DEFAULT_PER_JOB, 2)
    assert c.estimate_run(0)["per_role"] == 20   # 0 = unlimited → cap

    # Two runs: $1.00 for 4 resumes, $0.50 for 1 → $0.30 per resume
    c.set_run("r1")
    c.record("claude-sonnet-4-5", _t.SimpleNamespace(input_tokens=0, output_tokens=66_667))
    c.record("claude-sonnet-4-5", _t.SimpleNamespace(input_tokens=0, output_tokens=0))
    c.finish_run("r1", 4)
    c.set_run("r2")
    c.record("claude-sonnet-4-5", _t.SimpleNamespace(input_tokens=0, output_tokens=33_333))
    c.finish_run("r2", 1)
    pj = c.per_job_cost()
    assert pj["learned"] and abs(pj["typical"] - 0.30) < 0.001


def test_credit_error_stops_run(monkeypatch, tmp_path):
    anthropic = pytest.importorskip("anthropic")
    if not isinstance(getattr(anthropic, "__file__", None), str):
        pytest.skip("real anthropic SDK not installed (stubbed by another test)")
    from anthropic.resources.messages import Messages
    import core.costs as c
    monkeypatch.setattr(c, "_db_path", lambda: str(tmp_path / "api_usage.db"))
    monkeypatch.setattr(c, "_installed", False)

    def fake_create(self, *a, **k):
        raise RuntimeError("Error code: 400 - Your credit balance is too low to "
                           "access the Anthropic API.")
    monkeypatch.setattr(Messages, "create", fake_create)
    c.install_usage_tracking(fail_fast=True)

    swallowed = False
    with pytest.raises(c.CreditBalanceError):
        try:
            Messages.create(None, model="claude-sonnet-4-5", max_tokens=5, messages=[])
        except Exception:          # the bot's per-job handlers look like this
            swallowed = True
    assert not swallowed
