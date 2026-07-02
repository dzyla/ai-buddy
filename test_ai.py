import subprocess
import json
import os
import pytest

# Ensure the local binary is built before running tests
@pytest.fixture(scope="session", autouse=True)
def build_ai_binary():
    print("\n[Test Setup] Compiling local ai binary...")
    res = subprocess.run(["gcc", "-O2", "-o", "./ai", "ai.c", "cJSON.c", "-lcurl"], capture_output=True, text=True)
    assert res.returncode == 0, f"Compilation failed: {res.stderr}"
    print("[Test Setup] Syncing skills to local config...")
    # Sync skills to ~/.config/ai/skills as install.sh does
    skills_src = "./.agents/skills"
    skills_dst = os.path.expanduser("~/.config/ai/skills")
    os.makedirs(skills_dst, exist_ok=True)
    if os.path.exists(skills_src):
        subprocess.run(f"cp -r {skills_src}/. {skills_dst}/", shell=True)
    yield

def get_active_backend_env():
    env = {}
    env_file = os.path.expanduser("~/.local/share/ai/env")
    if os.path.isfile(env_file):
        with open(env_file, "r") as f:
            for line in f:
                line = line.strip()
                if line.startswith("export "):
                    line = line[7:]
                    if "=" in line:
                        key, val = line.split("=", 1)
                        # strip quotes if any
                        if val.startswith('"') and val.endswith('"'):
                            val = val[1:-1]
                        elif val.startswith("'") and val.endswith("'"):
                            val = val[1:-1]
                        env[key] = val
    return env

def run_ai(args, stdin_data=None, env=None):
    # Retrieve env vars from active backend config
    base_env = os.environ.copy()
    
    # Overwrite with active config from ~/.local/share/ai/env to override stale terminal environment
    active_env = get_active_backend_env()
    base_env.update(active_env)
    
    if env is not None:
        for k, v in env.items():
            if v is None:
                base_env.pop(k, None)
            else:
                base_env[k] = v
    
    # Run binary
    res = subprocess.run(["./ai"] + args, input=stdin_data, capture_output=True, text=True, env=base_env)
    return res

def test_version_flag():
    res = run_ai(["-v"])
    assert res.returncode == 0
    assert "ai" in res.stdout

def test_help_flag():
    res = run_ai(["-h"])
    assert res.returncode == 0
    assert "Usage: ai" in res.stdout
    assert "--interactive" in res.stdout
    assert "--yes" in res.stdout
    assert "--continue" in res.stdout

def test_continue_flag():
    res = run_ai(["-c", "-n", "Respond with exactly SUCCESS."])
    assert res.returncode == 0
    assert "SUCCESS" in res.stdout

def test_continue_env_var():
    res = run_ai(["-n", "Respond with exactly SUCCESS."], env={"INFER_CONTINUE": "1"})
    assert res.returncode == 0
    assert "SUCCESS" in res.stdout

def test_missing_env_vars():
    # Clear INFER env vars and HOME so it cannot load from profiles
    env = {
        "INFER_BASE_URL": None,
        "INFER_API_KEY": None,
        "INFER_MODEL": None,
        "HOME": "/nonexistent"
    }
    res = run_ai(["-n", "hello"], env=env)
    assert res.returncode != 0
    assert "Error: missing required environment variables" in res.stderr

def test_ai_mcp_list_tools():
    res = subprocess.run(["python3", "ai_mcp.py", "list-tools"], capture_output=True, text=True)
    assert res.returncode == 0
    tools = json.loads(res.stdout)
    assert isinstance(tools, list)
    tool_names = [t["function"]["name"] for t in tools]
    assert "read_file" in tool_names
    assert "write_file" in tool_names
    assert "edit_file" in tool_names
    assert "list_directory" in tool_names
    assert "load_skill" in tool_names

def test_ai_mcp_load_skill_list():
    res = subprocess.run([
        "python3", "ai_mcp.py", "call-tool", "load_skill", "load_skill", "{}"
    ], capture_output=True, text=True)
    assert res.returncode == 0
    assert "Available skills" in res.stdout
    assert "deep_research" in res.stdout
    assert "karpathy_guidelines" in res.stdout

def test_ai_mcp_load_skill_specific():
    res = subprocess.run([
        "python3", "ai_mcp.py", "call-tool", "load_skill", "load_skill", '{"name": "karpathy_guidelines"}'
    ], capture_output=True, text=True)
    assert res.returncode == 0
    assert "[Skill: karpathy_guidelines]" in res.stdout
    assert "guidelines" in res.stdout.lower()

def test_ai_mcp_load_skill_not_found():
    res = subprocess.run([
        "python3", "ai_mcp.py", "call-tool", "load_skill", "load_skill", '{"name": "non_existent_skill_xyz"}'
    ], capture_output=True, text=True)
    assert res.returncode == 0
    assert "Skill 'non_existent_skill_xyz' not found" in res.stdout

def test_no_tools_mode():
    res = run_ai(["-n", "Respond with exactly the word SUCCESS."])
    assert res.returncode == 0
    assert "SUCCESS" in res.stdout

def test_stdin_pipe_no_tools():
    res = run_ai(["-n", "Verify the following word matches SUCCESS:"], stdin_data="SUCCESS")
    assert res.returncode == 0
    assert "SUCCESS" in res.stdout or "yes" in res.stdout.lower() or "match" in res.stdout.lower()

def test_ai_mcp_delegate_task():
    # Test delegate_task calling the ai binary recursively
    env = get_active_backend_env()
    env["INFER_BIN_PATH"] = os.path.abspath("./ai")
    res = subprocess.run([
        "python3", "ai_mcp.py", "call-tool", "delegate_task", "delegate_task",
        '{"tasks": ["Respond with exactly SUCCESS."]}'
    ], capture_output=True, text=True, env=env)
    assert res.returncode == 0
    assert "SUCCESS" in res.stdout

def test_ai_mcp_edit_file_fuzzy(tmp_path):
    # Test that fuzzy edit targets the matched slice and doesn't break other lines
    test_file = tmp_path / "test_fuzzy.txt"
    # We create a file with a block of text, having duplicate contents but different trailing whitespaces
    content = "foo\n\nbar\n\nfoo \n"  # note the second foo has a trailing space
    test_file.write_text(content)
    
    # We call edit_file with search_content "foo " (with space) to replace it with "baz"
    res = subprocess.run([
        "python3", "ai_mcp.py", "call-tool", "edit_file", "edit_file",
        json.dumps({
            "path": str(test_file),
            "search_content": "foo  ",
            "replace_content": "baz"
        })
    ], capture_output=True, text=True)
    
    assert res.returncode == 0
    assert "File successfully edited" in res.stdout
    
    # The file should have replaced the first "foo" with "baz" and left the second "foo " intact
    new_content = test_file.read_text()
    assert new_content == "baz\n\nbar\n\nfoo \n"

def test_scheduler_tools_listed():
    res = subprocess.run(["python3", "ai_mcp.py", "list-tools"], capture_output=True, text=True)
    assert res.returncode == 0
    tools = json.loads(res.stdout)
    tool_names = [t["function"]["name"] for t in tools]
    assert "schedule_task" in tool_names
    assert "unschedule_task" in tool_names
    assert "list_scheduled_tasks" in tool_names

def test_scheduler_tool_calls():
    # First, list to ensure empty or normal state
    res = subprocess.run([
        "python3", "ai_mcp.py", "call-tool", "list_scheduled_tasks", "list_scheduled_tasks", "{}"
    ], capture_output=True, text=True)
    assert res.returncode == 0
    
    # Now, schedule a dummy task
    res = subprocess.run([
        "python3", "ai_mcp.py", "call-tool", "schedule_task", "schedule_task",
        '{"task_id": "test_dummy_task", "prompt": "Respond with SUCCESS", "interval_seconds": 60}'
    ], capture_output=True, text=True)
    assert res.returncode == 0
    assert "Successfully scheduled task" in res.stdout
    
    # List tasks again and verify it is there
    res = subprocess.run([
        "python3", "ai_mcp.py", "call-tool", "list_scheduled_tasks", "list_scheduled_tasks", "{}"
    ], capture_output=True, text=True)
    assert res.returncode == 0
    assert "test_dummy_task" in res.stdout
    assert "Respond with SUCCESS" in res.stdout
    
    # Now, unschedule it
    res = subprocess.run([
        "python3", "ai_mcp.py", "call-tool", "unschedule_task", "unschedule_task",
        '{"task_id": "test_dummy_task"}'
    ], capture_output=True, text=True)
    assert res.returncode == 0
    assert "Successfully unscheduled/cancelled task" in res.stdout
    
    # Verify it is no longer listed
    res = subprocess.run([
        "python3", "ai_mcp.py", "call-tool", "list_scheduled_tasks", "list_scheduled_tasks", "{}"
    ], capture_output=True, text=True)
    assert res.returncode == 0
    assert "test_dummy_task" not in res.stdout
