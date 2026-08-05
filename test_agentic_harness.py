import subprocess
import json
import os
import pytest

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
                        if val.startswith('"') and val.endswith('"'):
                            val = val[1:-1]
                        elif val.startswith("'") and val.endswith("'"):
                            val = val[1:-1]
                        env[key] = val
    return env

def run_ai(args, stdin_data=None, env=None):
    base_env = os.environ.copy()
    active_env = get_active_backend_env()
    base_env.update(active_env)
    
    if env is not None:
        for k, v in env.items():
            if v is None:
                base_env.pop(k, None)
            else:
                base_env[k] = v
    
    res = subprocess.run(["./ai"] + args, input=stdin_data, capture_output=True, text=True, env=base_env)
    return res

def test_agentic_think_multiple():
    # Test that the model can use think multiple times for self-correction or multi-step planning
    res = run_ai(["-y", "Plan a step-by-step strategy to count to 3. Then execute it. Use the think tool to reflect before completing the task."])
    assert res.returncode == 0
    # It should have succeeded, we check stdout
    assert len(res.stdout) > 0

def test_agentic_vault_and_memory():
    # Test a simple RAG / memory usage
    run_ai(["-y", "Save my favorite color, which is magenta, to memory using save_memory."])
    res = run_ai(["-y", "What is my favorite color based on your memory?"])
    assert res.returncode == 0
    assert "magenta" in res.stdout.lower()

def test_agentic_remote_command():
    # Test that the execute_remote_command tool is accessible
    res = subprocess.run(["python3", "ai_mcp.py", "list-tools"], capture_output=True, text=True)
    tools = json.loads(res.stdout)
    tool_names = [t["function"]["name"] for t in tools]
    assert "execute_remote_command" in tool_names

def test_agentic_complex_file_manipulation(tmp_path):
    # The model must write a python script, run it, and verify the output.
    script_path = tmp_path / "hello.py"
    prompt = f"Write a python script at {script_path} that prints 'Hello Complex World', execute it using execute_command, and verify the output. If it succeeds, respond with exactly 'ALL_GOOD'."
    res = run_ai(["-y", prompt])
    assert res.returncode == 0
    assert "ALL_GOOD" in res.stdout
    assert script_path.exists()
    assert "Hello Complex World" in script_path.read_text()

def test_agentic_error_recovery():
    # The model must try a failing command and recover
    prompt = "Run the command `this_command_does_not_exist_123`. When it fails, use the think tool to reflect on the error, then just reply with 'RECOVERED'."
    res = run_ai(["-y", prompt])
    assert res.returncode == 0
    assert "RECOVERED" in res.stdout

def test_agentic_multi_tool_chain(tmp_path):
    # Test reading, transforming, and writing
    input_file = tmp_path / "input.txt"
    output_file = tmp_path / "output.txt"
    input_file.write_text("apple banana cherry")
    
    prompt = f"Read the file at {input_file}. Count the number of words in it. Write that number into {output_file}."
    res = run_ai(["-y", prompt])
    assert res.returncode == 0
    assert output_file.exists()
    assert output_file.read_text().strip() == "3"

def test_agentic_advanced_reflection(tmp_path):
    # Test self-correction on script failure
    script_path = tmp_path / "buggy.py"
    script_path.write_text("print(1/0)")
    
    prompt = f"Execute the python script at {script_path}. It will fail. Use the edit_file tool to fix it so it prints 'fixed', then run it again. Reply with 'FIXED_SCRIPT' when done."
    res = run_ai(["-y", prompt])
    assert res.returncode == 0
    assert "FIXED_SCRIPT" in res.stdout
    assert "print('fixed')" in script_path.read_text()
