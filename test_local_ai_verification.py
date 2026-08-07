#!/usr/bin/env python3
"""
Integration test for local AI to verify all improvements.md changes.
"""

import os
import sys
import subprocess
import pytest

def test_local_ai_time_awareness():
    local_ai = os.path.join(os.getcwd(), "ai")
    res = subprocess.run([local_ai, "-n", "What is the current year according to your system prompt context?"],
                         capture_output=True, text=True, timeout=60)
    assert res.returncode == 0
    # Should reference 2026
    assert "2026" in res.stdout or "2026" in res.stderr

def test_local_ai_tool_execution():
    local_ai = os.path.join(os.getcwd(), "ai")
    res = subprocess.run([local_ai, "-y", "Use execute_command to print 'LOCAL_AI_TEST_PASSED'"],
                         capture_output=True, text=True, timeout=60)
    assert res.returncode == 0
    assert "LOCAL_AI_TEST_PASSED" in res.stdout or "LOCAL_AI_TEST_PASSED" in res.stderr

def test_local_ai_structured_query():
    local_ai = os.path.join(os.getcwd(), "ai")
    res = subprocess.run([local_ai, "-y", "Use structured_query to count lines in Makefile starting with C"],
                         capture_output=True, text=True, timeout=60)
    assert res.returncode == 0

if __name__ == "__main__":
    pytest.main([__file__])
