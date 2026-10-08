"""trader/advisor.py: a failed `ai` run (non-zero exit, e.g. the model server is down) is a
failure, never a briefing: callers then use the deterministic pre-market fallback."""
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from trader import advisor  # noqa: E402

BRIEFING = {"macro_sentiment": {"label": "NEUTRAL", "score": 0.1, "key_headlines": []},
            "top_buy_candidates": [{"ticker": "ABC", "score": 70, "price": 10.0, "stop_loss": 9.0,
                                    "take_profit_1": 12.0}],
            "top_risk_warnings": []}


def _fake_ai(tmp_path, code, stdout, stderr):
    p = tmp_path / "ai"
    p.write_text("#!/bin/sh\nprintf '%%s' '%s'\nprintf '%%s\\n' '%s' >&2\nexit %d\n" % (stdout, stderr, code))
    p.chmod(0o755)
    return str(p)


def _setup(monkeypatch, tmp_path, code, stdout, stderr):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("INFER_TEST_MODE", raising=False)
    monkeypatch.setattr(advisor.AgentAdvisor, "invoke_local_llm", classmethod(lambda cls, *a, **k: None))
    monkeypatch.setattr(advisor, "find_ai_binary", lambda: _fake_ai(tmp_path, code, stdout, stderr))


def test_nonzero_exit_is_an_invocation_error(monkeypatch, tmp_path):
    long_text = "Partial briefing text that looks long enough to pass the length checks " * 2
    _setup(monkeypatch, tmp_path, 1, long_text,
           "\x1b[1;31m[ai] model server unavailable (HTTP 503): model will not fit in VRAM\x1b[0m")
    res = advisor.AgentAdvisor.invoke_agent("brief me", timeout=5)
    assert res.startswith("(AI Agent invocation error: exit 1: [ai] model server unavailable")
    assert "\x1b" not in res


def test_premarket_briefing_falls_back_when_the_agent_fails(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, 1, "x" * 200, "[ai] cannot reach the model server")
    out = advisor.AgentAdvisor.review_premarket_briefing(BRIEFING)
    assert out == advisor.AgentAdvisor.generate_algorithmic_premarket_briefing(BRIEFING)


def test_successful_run_still_returns_stdout(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, 0, "A real briefing from the agent with enough words in it.", "")
    assert advisor.AgentAdvisor.invoke_agent("brief me", timeout=5).startswith("A real briefing")
