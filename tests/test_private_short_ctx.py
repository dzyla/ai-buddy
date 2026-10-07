import sys
import os
import json
import pytest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from aitools import state  # noqa: E402
import ai_mcp


def test_private_mode_mcp_skips_metrics_and_ledger(monkeypatch, tmp_path):
    metrics_file = tmp_path / "metrics.jsonl"
    monkeypatch.setenv("HOME", str(tmp_path))  # never touch the real ~/.config/ai

    monkeypatch.setenv("INFER_PRIVATE_MODE", "1")
    
    # log_metric should do nothing
    ai_mcp.log_metric("read_file", 10.5, success=True)
    assert not metrics_file.exists()
    
    # record_failure should do nothing
    rec, msg = ai_mcp.record_failure("read_file", "path=missing.txt", "No such file")
    assert rec is False
    assert msg == ""
    assert state.ledger_count() == 0
    
    # record_recovery should do nothing
    res = ai_mcp.record_recovery("read_file", "path=fixed.txt", "No such file")
    assert res == ""
    assert state.ledger_count() == 0


def test_private_mode_normal_behavior(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("INFER_PRIVATE_MODE", raising=False)

    rec, msg = ai_mcp.record_failure("read_file", "path=foo.txt", "File not found")
    assert state.ledger_count(kind="failure") == 1
