import os
import sys
import json
import shutil
import subprocess
import pytest

# Import ai_mcp module
import ai_mcp

def test_execute_command_mcp():
    res = ai_mcp.execute_command("echo hello_improvements_test")
    assert "hello_improvements_test" in res
    assert "success" in res

def test_rlm_context_pool():
    ai_mcp.append_to_context_pool("unique_rlm_test_marker_12345")
    search_res = ai_mcp.search_context("unique_rlm_test_marker_12345")
    assert "unique_rlm_test_marker_12345" in search_res
    
    snippet_res = ai_mcp.get_context_snippet(0)
    assert snippet_res is not None
    assert "Error" not in snippet_res or "out of range" in snippet_res

def test_structured_query():
    sample_text = "line1: alpha\nline2: beta\nline3: gamma\nline4: delta"
    res = ai_mcp.structured_query(sample_text, filter_expr="gamma|delta", aggregate="count")
    assert res.strip() == "2"

def test_multi_agent_orchestration():
    spawn_res = ai_mcp.spawn_agent("test_subagent", "Perform test task", tools=["execute_command"])
    assert "Spawned agent" in spawn_res
    
    agents_list = ai_mcp.list_agents()
    assert "test_subagent" in agents_list

def test_session_report():
    report_res = ai_mcp.session_report(success=True, failure_modes=[], notes="Test run verified")
    assert "logged successfully" in report_res

def test_token_counter():
    tokens = ai_mcp.count_tokens("gpt-4", "Hello world from ai-buddy token counter!")
    assert int(tokens) > 0

def test_git_denylist():
    local_ai = os.path.join(os.getcwd(), "ai")
    assert os.path.exists(local_ai)
    
    # Test --trim-threshold flag
    proc = subprocess.run([local_ai, "--help"], capture_output=True, text=True)
    assert "--trim-threshold" in proc.stdout

def test_shared_library_linkage():
    shared_lib = os.path.join(os.getcwd(), "libremote_harness.so")
    assert os.path.exists(shared_lib)
    
    test_bin = os.path.join(os.getcwd(), "test_remote_harness")
    if os.path.exists(test_bin):
        proc = subprocess.run(["ldd", test_bin], capture_output=True, text=True)
        assert "libremote_harness.so" in proc.stdout

def test_modular_source_files():
    for f in ["ai_git.c", "ai_terminal.c", "ai_session.c", "ai_git.h", "ai_terminal.h", "ai_session.h"]:
        path = os.path.join(os.getcwd(), f)
        assert os.path.exists(path), f"Missing modular file {f}"
