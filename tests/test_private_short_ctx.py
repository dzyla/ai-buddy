import sys
import os
import json
import pytest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ai_mcp


def test_private_mode_mcp_skips_metrics_and_ledger(monkeypatch, tmp_path):
    metrics_file = tmp_path / "metrics.jsonl"
    ledger_file = tmp_path / "ledger.jsonl"
    
    monkeypatch.setenv("INFER_PRIVATE_MODE", "1")
    monkeypatch.setattr(ai_mcp, "_ledger_path", lambda: str(ledger_file))
    
    # log_metric should do nothing
    ai_mcp.log_metric("read_file", 10.5, success=True)
    assert not metrics_file.exists()
    
    # record_failure should do nothing
    rec, msg = ai_mcp.record_failure("read_file", "path=missing.txt", "No such file")
    assert rec is False
    assert msg == ""
    assert not ledger_file.exists()
    
    # record_recovery should do nothing
    res = ai_mcp.record_recovery("read_file", "path=fixed.txt", "No such file")
    assert res == ""
    assert not ledger_file.exists()


def test_private_mode_normal_behavior(monkeypatch, tmp_path):
    ledger_file = tmp_path / "ledger.jsonl"
    monkeypatch.delenv("INFER_PRIVATE_MODE", raising=False)
    monkeypatch.setattr(ai_mcp, "_ledger_path", lambda: str(ledger_file))
    
    rec, msg = ai_mcp.record_failure("read_file", "path=foo.txt", "File not found")
    assert ledger_file.exists()
