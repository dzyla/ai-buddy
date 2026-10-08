"""Native tool handlers: one `h_<tool>(arguments) -> str` per tool, routed by registry.HANDLERS.

Bodies are the former `call-tool` if/elif branches; each returns exactly what the branch printed
(registry adds the trailing newline). Handlers resolve tool functions through this module's
globals at call time, so rebinding a name on the `ai_mcp` shim reaches them."""
import sys
import os
import json
import subprocess
from .browser import (  # noqa: F401
    browser,
    computer_control,
)
from .files import (  # noqa: F401
    edit_file,
    list_directory,
    patch_file,
    read_file,
    search_files,
    write_file,
)
from .history import (  # noqa: F401
    get_session,
    get_zulip_chat,
    list_sessions,
    rebuild_history_index,
    search_history,
    search_zulip_chats,
)
from .memory import (  # noqa: F401
    _load_context_pool,
    append_to_context_pool,
    get_context_snippet,
    recall,
    remember,
    save_memory,
    search_context,
    vault_backlinks,
    vault_read,
    vault_search,
    vault_write,
)
from .misc import (  # noqa: F401
    _resolve_ai_bin,
    clarify,
    get_clipboard,
    get_environment,
    get_system_status,
    list_agents,
    resume_agent,
    session_report,
    spawn_agent,
    structured_query,
    todo,
)
from .research import (  # noqa: F401
    arxiv_search,
    pubmed_research_round,
    pubmed_search,
)
from .scheduler import (  # noqa: F401
    list_scheduled_tasks,
    schedule_task,
    set_reminder,
    unschedule_task,
)
from .selfimprove import (  # noqa: F401
    skill_create,
    skill_note,
    skill_update,
)
from .shell import (  # noqa: F401
    check_process_status,
    execute_command,
    js_playground,
    list_processes,
    python_playground,
    start_background_process,
    stop_process,
)
from .web import (  # noqa: F401
    fetch_smart,
    fetch_webpage,
    fetch_webpage_js,
    web_search,
)

# Per-agent and per-URL output caps for parallel tools
_AGENT_OUTPUT_CAP = 10 * 1024       # 10 KB per sub-agent result
_PARALLEL_FETCH_CAP = 10 * 1024     # 10 KB per fetched URL


def h_think(arguments):
    # Handled natively in C; this is a safety fallback
    return '{"ok": true}'


def h_task_complete(arguments):
    # Handled natively in C; this is a safety fallback
    return '{"ok": true}'


def h_execute_remote_command(arguments):
    host = arguments.get("host", "")
    cmd = arguments.get("command", "")
    if not host or not cmd:
        return json.dumps({"error": "Missing host or command"})
    else:
        try:
            res = subprocess.run(["ssh", host, cmd], capture_output=True, text=True, timeout=120)
            out = res.stdout + res.stderr
            if res.returncode != 0:
                out = f"[Command Failed with exit status {res.returncode}]\n" + out
            return out if out else "[Command Success, no output]"
        except Exception as e:
            return json.dumps({"error": str(e)})


def h_remote_exec(arguments):
    def ssh_cmd_base(user, host, port):
        """Build SSH command with key or password auth."""
        if password:
            return f"sshpass -p '{password}' ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -p {port}"
        return f"ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -p {port}"

    action = arguments.get("action", "connect")
    host = arguments.get("host", "")
    port = arguments.get("port", 22)
    user = arguments.get("user", "")
    password = arguments.get("pass", "")
    cmd = arguments.get("command", "")
    timeout = arguments.get("timeout", 120)

    if action == "connect":
        if not host or not user:
            return json.dumps({"error": "host and user required for connect"})
        else:
            try:
                ssh_base = ssh_cmd_base(user, host, port)
                res = subprocess.run(f"{ssh_base} {user}@{host} 'echo Connected'", shell=True, capture_output=True, text=True, timeout=timeout)
                if res.returncode == 0:
                    return json.dumps({"status": "connected", "host": host, "user": user})
                else:
                    return json.dumps({"error": f"Failed to connect: {res.stderr}"})
            except Exception as e:
                return json.dumps({"error": str(e)})

    elif action == "disconnect":
        return json.dumps({"status": "disconnected"})

    elif action == "discover":
        try:
            ssh_base = ssh_cmd_base(user, host, port)
            res = subprocess.run(f"{ssh_base} {user}@{host} 'cat /etc/hosts | grep -v localhost | grep -v ::1'", shell=True, capture_output=True, text=True, timeout=timeout)
            if res.returncode == 0:
                nodes = [line.split()[1] for line in res.stdout.strip().split('\n') if len(line.split()) >= 2]
                return json.dumps({"discovered_nodes": nodes, "total": len(nodes)})
            else:
                return json.dumps({"error": "Discovery failed"})
        except Exception as e:
            return json.dumps({"error": str(e)})

    elif action == "status":
        try:
            ssh_base = ssh_cmd_base(user, host, port)
            res = subprocess.run(f"{ssh_base} {user}@{host} 'uname -a; echo ---; nproc; echo ---; free -h | head -2; echo ---; df -h / | tail -1'", shell=True, capture_output=True, text=True, timeout=timeout)
            if res.returncode == 0:
                return res.stdout if res.stdout else "[No output]"
            else:
                return json.dumps({"error": "Status failed"})
        except Exception as e:
            return json.dumps({"error": str(e)})

    elif action == "exec":
        if not cmd:
            return json.dumps({"error": "command required for exec"})
        else:
            try:
                ssh_base = ssh_cmd_base(user, host, port)
                res = subprocess.run(f"{ssh_base} {user}@{host} '{cmd}'", shell=True, capture_output=True, text=True, timeout=timeout)
                out = res.stdout + res.stderr
                if res.returncode != 0:
                    out = f"[Command Failed with exit status {res.returncode}]\n" + out
                return out if out else "[Command Success, no output]"
            except Exception as e:
                return json.dumps({"error": str(e)})

    elif action == "mount":
        if not cmd:
            return json.dumps({"error": "command required for mount (format: '<remote_path> <local_mount>')"})
        else:
            try:
                key = "~/.ssh/id_ed25519" if os.path.exists("~/.ssh/id_ed25519") else "~/.ssh/id_rsa"
                mount_cmd = f"sshfs -o IdentityFile={key} -p {port} {user}@{host}:{cmd} /tmp/mnt_remote"
                res = subprocess.run(mount_cmd, shell=True, capture_output=True, text=True, timeout=timeout)
                if res.returncode == 0:
                    return json.dumps({"status": "mounted", "mount_point": "/tmp/mnt_remote"})
                else:
                    return json.dumps({"error": f"Mount failed: {res.stderr}"})
            except Exception as e:
                return json.dumps({"error": str(e)})

    elif action == "jobs":
        try:
            ssh_base = ssh_cmd_base(user, host, port)
            res = subprocess.run(f"{ssh_base} {user}@{host} 'squeue -u {user} -o \"%i %u %j %T %t %M %N %L %v %c %m %n\"'", shell=True, capture_output=True, text=True, timeout=timeout)
            if res.returncode == 0:
                return res.stdout if res.stdout else "No jobs found"
            else:
                return json.dumps({"error": "Job listing failed"})
        except Exception as e:
            return json.dumps({"error": str(e)})

    elif action == "submit":
        if not cmd:
            return json.dumps({"error": "command required for submit"})
        else:
            try:
                ssh_base = ssh_cmd_base(user, host, port)
                submit_cmd = f"{ssh_base} {user}@{host} 'bash -s' << 'SCRIPT'\n{cmd}\nSCRIPT"
                res = subprocess.run(submit_cmd, shell=True, capture_output=True, text=True, timeout=timeout)
                if res.returncode == 0:
                    return res.stdout if res.stdout else "[Job submitted successfully]"
                else:
                    return json.dumps({"error": f"Submit failed: {res.stderr}"})
            except Exception as e:
                return json.dumps({"error": str(e)})

    else:
        return json.dumps({"error": f"Unknown action: {action}"})


def h_check_time(arguments):
    import datetime
    now = datetime.datetime.now()
    return now.strftime("%A, %Y-%m-%d %H:%M:%S")


def h_schedule_task(arguments):
    task_id = arguments.get("task_id")
    prompt = arguments.get("prompt")
    interval_seconds = arguments.get("interval_seconds", 300)
    run_once = arguments.get("run_once", False)
    # Optional safety guardrails — passed via extra so schedule_task stores them in the JSON
    extra_sched = {}
    if "max_runs" in arguments and arguments["max_runs"]:
        extra_sched["max_runs"] = int(arguments["max_runs"])
    if "ttl_hours" in arguments and arguments["ttl_hours"]:
        extra_sched["ttl_hours"] = float(arguments["ttl_hours"])
    result = schedule_task(task_id, prompt, interval_seconds, run_once=run_once,
                           extra=extra_sched or None)
    return result


def h_set_reminder(arguments):
    result = set_reminder(
        message=arguments.get("message"),
        when=arguments.get("when"),
        delay_seconds=arguments.get("delay_seconds"),
        zulip_to=arguments.get("zulip_to"),
        zulip_stream=arguments.get("zulip_stream"),
        zulip_topic=arguments.get("zulip_topic"),
        task_id=arguments.get("task_id"),
    )
    return result


def h_unschedule_task(arguments):
    task_id = arguments.get("task_id")
    result = unschedule_task(task_id)
    return result


def h_list_scheduled_tasks(arguments):
    result = list_scheduled_tasks()
    return result


def h_arxiv_search(arguments):
    query = arguments.get("query")
    max_results = arguments.get("max_results", 5)
    result = arxiv_search(query, max_results)
    return result


def h_list_directory(arguments):
    path = arguments.get("path", ".")
    result = list_directory(path)
    return result


def h_web_search(arguments):
    query = arguments.get("query", "")
    result = web_search(query)
    return result


def h_fetch_webpage(arguments):
    url = arguments.get("url", "")
    result = fetch_webpage(url)
    return result


def h_fetch_smart(arguments):
    url = arguments.get("url", "")
    result = fetch_smart(url)
    return result


def h_fetch_webpage_js(arguments):
    url = arguments.get("url", "")
    wait_for = arguments.get("wait_for", "networkidle")
    result = fetch_webpage_js(url, wait_for=wait_for)
    return result


def h_save_memory(arguments):
    content = arguments.get("content", "")
    result = save_memory(content, replace=_truthy(arguments.get("replace", False)),
                         pinned=_truthy(arguments.get("pinned", False)))
    return result


def _truthy(v):
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return bool(v)


def h_search_memory(arguments):
    from . import search
    query = str(arguments.get("query") or "").strip()
    if not query:
        return "Error: search_memory needs a non-empty query."
    try:
        k = int(arguments.get("k") or 8)
    except (TypeError, ValueError):
        k = 8
    try:
        results, info = search.search(query, sources=arguments.get("sources"), since=arguments.get("since"), k=k,
                                      loose=True)  # the tool's looser gate; auto-recall stays strict
    except ValueError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error searching memory: {e}"
    return search.format_results(results, info["vector"])


def h_remember(arguments):
    content = arguments.get("content", "")
    metadata = arguments.get("metadata", "")
    result = remember(content, metadata=metadata)
    return result


def h_recall(arguments):
    query = arguments.get("query", "")
    result = recall(query)
    return result


def h_execute_command(arguments):
    cmd = arguments.get("command", "")
    timeout = arguments.get("timeout", 120)
    result = execute_command(cmd, timeout=timeout)
    return result


def h_get_context_snippet(arguments):
    idx = arguments.get("index", 0)
    result = get_context_snippet(idx)
    return result


def h_search_context(arguments):
    query = arguments.get("query", "")
    result = search_context(query)
    return result


def h_structured_query(arguments):
    target = arguments.get("target", "")
    filter_expr = arguments.get("filter_expr", arguments.get("filter"))
    transform = arguments.get("transform")
    aggregate = arguments.get("aggregate")
    result = structured_query(target, filter_expr=filter_expr, transform=transform, aggregate=aggregate)
    return result


def h_spawn_agent(arguments):
    name = arguments.get("name", "subagent")
    prompt = arguments.get("prompt", "")
    tools = arguments.get("tools")
    persistent = arguments.get("persistent", True)
    budget = arguments.get("budget")
    result = spawn_agent(name, prompt, tools=tools, persistent=persistent, budget=budget)
    return result


def h_resume_agent(arguments):
    agent_id = arguments.get("agent_id", "")
    msg = arguments.get("message", "")
    budget = arguments.get("budget")
    result = resume_agent(agent_id, msg, budget=budget)
    return result


def h_list_agents(arguments):
    result = list_agents()
    return result


def h_session_report(arguments):
    success = arguments.get("success", True)
    failure_modes = arguments.get("failure_modes")
    notes = arguments.get("notes", "")
    result = session_report(success=success, failure_modes=failure_modes, notes=notes)
    return result


def h_list_processes(arguments):
    result = list_processes()
    return result


def h_start_background_process(arguments):
    try:
        res = start_background_process(arguments.get("command", ""), arguments.get("log_file"))
        return res
    except Exception as e:
        return f"Error in start_background_process: {e}"


def h_check_process_status(arguments):
    try:
        res = check_process_status(arguments.get("pid"), arguments.get("log_file"))
        return res
    except Exception as e:
        return f"Error in check_process_status: {e}"


def h_stop_process(arguments):
    try:
        res = stop_process(arguments.get("pid"))
        return res
    except Exception as e:
        return f"Error in stop_process: {e}"


def h_read_file(arguments):
    path = arguments.get("path", "")
    start_line = arguments.get("start_line", None)
    end_line = arguments.get("end_line", None)
    result = read_file(path, start_line=start_line, end_line=end_line)
    return result


def h_write_file(arguments):
    path = arguments.get("path", "")
    content = arguments.get("content", "")
    result = write_file(path, content)
    return result


def h_edit_file(arguments):
    path = arguments.get("path", "")
    search_content = arguments.get("search_content", "")
    replace_content = arguments.get("replace_content", "")
    result = edit_file(path, search_content, replace_content)
    return result


def h_patch_file(arguments):
    path = arguments.get("path", "")
    hunks = arguments.get("hunks")
    search = arguments.get("search")
    replace = arguments.get("replace")
    result = patch_file(path, hunks=hunks, search=search, replace=replace)
    return result


def h_get_environment(arguments):
    result = get_environment()
    return result


def h_delegate_task(arguments):
    tasks = arguments.get("tasks")
    budget = arguments.get("budget")
    if not isinstance(tasks, list):
        # Accept legacy single-task call gracefully
        single_task = arguments.get("task", "")
        tasks = [single_task] if single_task else []

    if not tasks:
        return "Error: delegate_task requires 'tasks' array with at least one item."
    else:
        try:
            import concurrent.futures
            ai_bin = _resolve_ai_bin()

            task_timeout = 300
            env_timeout = os.environ.get("INFER_TASK_TIMEOUT")
            if env_timeout:
                try:
                    task_timeout = int(env_timeout)
                except ValueError:
                    pass

            n = len(tasks)
            print(f"[delegate_task] Starting {n} parallel agent(s)...", file=sys.stderr, flush=True)

            def run_single_agent(t_desc, idx):
                cmd_args = [ai_bin, "-y", "-q"]
                if budget is not None and str(budget).strip():
                    cmd_args.extend(["--budget", str(budget).strip()])
                cmd_args.append(t_desc)
                try:
                    proc = subprocess.run(
                        cmd_args,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        timeout=task_timeout
                    )
                    out = (proc.stdout or "").strip()
                    # Only surface stderr when the agent failed
                    if proc.returncode != 0:
                        err = (proc.stderr or "").strip()
                        if err:
                            out = f"[exit {proc.returncode}] {out}\n[stderr]: {err[:500]}"
                    if not out:
                        out = f"Agent #{idx+1} completed with no output (exit {proc.returncode})."
                    # Cap per-agent output
                    if len(out) > _AGENT_OUTPUT_CAP:
                        out = out[:_AGENT_OUTPUT_CAP] + f"\n... [truncated at {_AGENT_OUTPUT_CAP//1024} KB]"
                    return idx, out
                except subprocess.TimeoutExpired:
                    return idx, f"Error: agent #{idx+1} timed out after {task_timeout}s."
                except Exception as ex:
                    return idx, f"Error in agent #{idx+1}: {ex}"

            results_map = {}
            with concurrent.futures.ThreadPoolExecutor(max_workers=n) as executor:
                futures = {executor.submit(run_single_agent, t, i): i for i, t in enumerate(tasks)}
                for future in concurrent.futures.as_completed(futures):
                    try:
                        idx, res = future.result()
                        results_map[idx] = res
                        print(f"[delegate_task] Agent #{idx+1}/{n} done.", file=sys.stderr, flush=True)
                    except Exception as e:
                        idx = futures[future]
                        results_map[idx] = f"Error in thread for agent #{idx+1}: {e}"

            combined = ""
            for i in range(n):
                label = f"--- Agent {i+1}/{n} ---\n" if n > 1 else ""
                combined += label + results_map.get(i, "") + "\n\n"
            return combined.strip()
        except Exception as e:
            return f"Error in delegate_task: {e}"


def h_parallel_fetch(arguments):
    import concurrent.futures
    urls = arguments.get("urls", [])
    if not urls:
        return "Error: parallel_fetch requires 'urls' array with at least one URL."
    else:
        n = len(urls)
        print(f"[parallel_fetch] Fetching {n} URL(s) concurrently...", file=sys.stderr, flush=True)

        def _fetch_one(url, idx):
            try:
                text = fetch_webpage(url)
                if len(text) > _PARALLEL_FETCH_CAP:
                    text = text[:_PARALLEL_FETCH_CAP] + f"\n... [truncated at {_PARALLEL_FETCH_CAP//1024} KB]"
                print(f"[parallel_fetch] {idx+1}/{n} done: {url[:60]}", file=sys.stderr, flush=True)
                return idx, text
            except Exception as ex:
                return idx, f"Error fetching {url}: {ex}"

        results_map = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=n) as executor:
            futures = {executor.submit(_fetch_one, u, i): i for i, u in enumerate(urls)}
            for future in concurrent.futures.as_completed(futures):
                idx, res = future.result()
                results_map[idx] = res

        combined = ""
        for i in range(n):
            combined += f"=== URL {i+1}: {urls[i]} ===\n{results_map.get(i, '')}\n\n"
        return combined.strip()


def h_load_skill(arguments):
    import re as _re
    skill_name = arguments.get("name", "").strip()
    skill_dirs = [
        os.path.join(os.getcwd(), ".agents", "skills"),
        os.path.join(os.path.expanduser("~"), ".config", "ai", "skills"),
    ]
    if not skill_name:
        # List mode: return index of name + description for every available skill
        index = "Available skills (call load_skill(name) to read full guidance):\n"
        seen = set()
        for base in skill_dirs:
            if not os.path.isdir(base):
                continue
            for entry in sorted(os.listdir(base)):
                if entry in seen:
                    continue
                skill_path = os.path.join(base, entry, "SKILL.md")
                if os.path.isfile(skill_path):
                    try:
                        with open(skill_path, "r", encoding="utf-8", errors="replace") as f:
                            header = f.read(512)
                        m = _re.search(r'^description:\s*(.+)', header, _re.MULTILINE)
                        desc = m.group(1).strip() if m else "(no description)"
                    except Exception:
                        desc = "(unreadable)"
                    index += f"- {entry}: {desc}\n"
                    seen.add(entry)
        return index if seen else "No skills found."
    else:
        # Load mode: return full content of named skill
        for base in skill_dirs:
            skill_path = os.path.join(base, skill_name, "SKILL.md")
            if os.path.isfile(skill_path):
                try:
                    with open(skill_path, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read()
                    return f"[Skill: {skill_name}]\n{content}"
                except Exception as e:
                    return f"Error reading skill '{skill_name}': {e}"
        available = []
        for base in skill_dirs:
            if os.path.isdir(base):
                available.extend(os.listdir(base))
        return f"Skill '{skill_name}' not found. Call load_skill() with no argument to list available skills."


def h_computer_control(arguments):
    try:
        result = computer_control(arguments)
        return result
    except Exception as e:
        return f"Error in computer_control: {e}"


def h_pubmed_research_round(arguments):
    try:
        result = pubmed_research_round(
            query=arguments.get("query", ""),
            known_dois=arguments.get("known_dois"),
            start_date=arguments.get("start_date"),
            end_date=arguments.get("end_date"),
        )
        return result
    except Exception as e:
        return f"Error in pubmed_research_round: {e}"


def h_get_system_status(arguments):
    try:
        result = get_system_status()
        return result
    except Exception as e:
        return f"Error in get_system_status: {e}"


def h_get_clipboard(arguments):
    try:
        result = get_clipboard()
        return result
    except Exception as e:
        return f"Error in get_clipboard: {e}"


def h_learn_rule(arguments):
    try:
        rule_text = arguments.get("rule_text", "")
        if not rule_text:
            return "Error: rule_text required"
        else:
            rules_file = os.path.expanduser("~/.config/ai/rules.txt")
            os.makedirs(os.path.dirname(rules_file), exist_ok=True)
            with open(rules_file, "a") as f:
                f.write(rule_text.strip() + "\n")
            return f"Rule successfully saved to {rules_file}"
    except Exception as e:
        return f"Error in learn_rule: {e}"


def h_reset_context(arguments):
    return "Context reset initiated. ai.c will process this and clear the message array."


def h_vault_write(arguments):
    try:
        res = vault_write(arguments.get("title", ""), arguments.get("content", ""), arguments.get("links", ""))
        return res
    except Exception as e:
        import traceback
        return f"[SYSTEM EXCEPTION INTERCEPTED]\nAn internal error occurred in the middleware during tool execution:\n{str(e)}\n{traceback.format_exc()}\n\n[GRAPH ENFORCEMENT]\nYou must pause, recalculate your approach, and try a different strategy."


def h_vault_read(arguments):
    try:
        res = vault_read(arguments.get("title", ""))
        return res
    except Exception as e:
        import traceback
        return f"[SYSTEM EXCEPTION INTERCEPTED]\nAn internal error occurred in the middleware during tool execution:\n{str(e)}\n{traceback.format_exc()}\n\n[GRAPH ENFORCEMENT]\nYou must pause, recalculate your approach, and try a different strategy."


def h_vault_search(arguments):
    try:
        res = vault_search(arguments.get("query", ""))
        return res
    except Exception as e:
        import traceback
        return f"[SYSTEM EXCEPTION INTERCEPTED]\nAn internal error occurred in the middleware during tool execution:\n{str(e)}\n{traceback.format_exc()}\n\n[GRAPH ENFORCEMENT]\nYou must pause, recalculate your approach, and try a different strategy."


def h_vault_backlinks(arguments):
    try:
        res = vault_backlinks(arguments.get("title", ""))
        return res
    except Exception as e:
        import traceback
        return f"[SYSTEM EXCEPTION INTERCEPTED]\nAn internal error occurred in the middleware during tool execution:\n{str(e)}\n{traceback.format_exc()}\n\n[GRAPH ENFORCEMENT]\nYou must pause, recalculate your approach, and try a different strategy."


def h_pubmed_search(arguments):
    try:
        result = pubmed_search(
            query=arguments.get("query", ""),
            top_k=arguments.get("top_k", 10),
            start_date=arguments.get("start_date"),
            end_date=arguments.get("end_date"),
            high_quality_only=arguments.get("high_quality_only", True),
        )
        return result
    except Exception as e:
        return f"Error in pubmed_search: {e}"


def h_present_plan(arguments):
    # Native approval flow is handled in ai.c; this is a safety fallback for
    # direct ai_mcp.py invocations.
    plan = arguments.get("plan", "")
    return '{"ok": true, "note": "present_plan acknowledged (handled natively by the agent loop in ai.c). If you are in plan mode, the user will be asked to approve or reject this plan."}'


def h_search_files(arguments):
    try:
        return search_files(
            pattern=arguments.get("pattern", ""),
            path=arguments.get("path", "."),
            target=arguments.get("target", "content"),
            file_glob=arguments.get("file_glob"),
            limit=arguments.get("limit", 50),
            context=arguments.get("context", 0),
            output_mode=arguments.get("output_mode", "content"),
        )
    except Exception as e:
        return f"Error in search_files: {e}"


def h_todo(arguments):
    try:
        return todo(todos=arguments.get("todos"),
                   merge=bool(arguments.get("merge", False)))
    except Exception as e:
        return f"Error in todo: {e}"


def h_clarify(arguments):
    try:
        return clarify(
            question=arguments.get("question", ""),
            choices=arguments.get("choices"),
            multi_select=bool(arguments.get("multi_select", False)),
            timeout_seconds=arguments.get("timeout_seconds", 0),
        )
    except Exception as e:
        return f"Error in clarify: {e}"


def h_browser(arguments):
    try:
        return browser(
            action=arguments.get("action", ""),
            url=arguments.get("url"),
            selector=arguments.get("selector"),
            value=arguments.get("value"),
            key=arguments.get("key"),
            expr=arguments.get("expr"),
            wait_ms=arguments.get("wait_ms"),
            max_chars=arguments.get("max_chars"),
            path=arguments.get("path"),
        )
    except Exception as e:
        return f"Error in browser: {e}"


def h_append_to_context_pool(arguments):
    try:
        append_to_context_pool(arguments.get("entry", ""))
        pool = _load_context_pool()
        return "Context pool entry stored (id: %d)." % (len(pool) - 1)
    except Exception as e:
        return f"Error in append_to_context_pool: {e}"


def h_search_history(arguments):
    try:
        return search_history(query=arguments.get("query", ""), limit=arguments.get("limit", 8))
    except Exception as e:
        return f"Error in search_history: {e}"


def h_list_sessions(arguments):
    try:
        return list_sessions(limit=arguments.get("limit", 12))
    except Exception as e:
        return f"Error in list_sessions: {e}"


def h_get_session(arguments):
    try:
        return get_session(session_id=arguments.get("session_id", ""), max_chars=arguments.get("max_chars", 4000))
    except Exception as e:
        return f"Error in get_session: {e}"


def h_rebuild_history_index(arguments):
    try:
        return rebuild_history_index()
    except Exception as e:
        return f"Error in rebuild_history_index: {e}"


def h_search_zulip_chats(arguments):
    try:
        return search_zulip_chats(
            query=arguments.get("query", ""),
            limit=arguments.get("limit", 10),
            error_only=arguments.get("error_only", False)
        )
    except Exception as e:
        return f"Error in search_zulip_chats: {e}"


def h_get_zulip_chat(arguments):
    try:
        return get_zulip_chat(session_id=arguments.get("session_id", ""))
    except Exception as e:
        return f"Error in get_zulip_chat: {e}"


def h_skill_create(arguments):
    try:
        return skill_create(
            name=arguments.get("name", ""),
            description=arguments.get("description", ""),
            content=arguments.get("content", ""),
        )
    except Exception as e:
        return f"Error in skill_create: {e}"


def h_skill_update(arguments):
    try:
        return skill_update(
            name=arguments.get("name", ""),
            note=arguments.get("note", ""),
        )
    except Exception as e:
        return f"Error in skill_update: {e}"


def h_skill_note(arguments):
    try:
        return skill_note(
            name=arguments.get("name", ""),
            note=arguments.get("note", ""),
        )
    except Exception as e:
        return f"Error in skill_note: {e}"


def h_python_playground(arguments):
    try:
        return python_playground(
            code=arguments.get("code", ""),
            timeout=arguments.get("timeout", 30)
        )
    except Exception as e:
        return f"Error in python_playground: {e}"


def h_js_playground(arguments):
    try:
        return js_playground(
            code=arguments.get("code", ""),
            timeout=arguments.get("timeout", 30)
        )
    except Exception as e:
        return f"Error in js_playground: {e}"
