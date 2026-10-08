"""The agent loop (port of ai.c main()): setup, one task per user turn, interactive colon-commands.

main(argv) is the process entry (`python3 -m agent [--frontend-fd N] args...`). Engine.run() keeps
ai.c's order of side effects so both engines send equivalent requests; every intentional line of
output goes to the front-end as an event (protocol.py)."""
import dataclasses
import json
import os
import signal
import subprocess
import sys
import threading
import time

from . import cli, llm, messages as M, native, session, tools
from .protocol import Channel, Local

PLACEHOLDER = cli.PLACEHOLDER_PROMPT
SI_MAX_TRACKED = 32
MODE_NAMES = ("auto", "plan", "manual")


def _cut_bytes(s, n):
    """C snprintf into char[n]: at most n-1 bytes (a cut UTF-8 sequence is dropped)."""
    b = (s or "").encode("utf-8", "surrogateescape")
    if len(b) < n:
        return s or ""
    return b[:n - 1].decode("utf-8", "ignore")


def _env_int(name, default):
    v = os.environ.get(name)
    if not v:
        return default
    n = native._atoi(v)
    return n


def is_context_overflow(err):
    """A 400 saying the prompt exceeds the server's context (llama.cpp, vLLM, OpenAI wording)."""
    e = (err or "").lower()
    return "context size" in e or "context length" in e or "exceed_context_size" in e


class Engine:
    def __init__(self, ch, argv):
        self.ch = ch
        self.argv = [M.clean(a) for a in argv]
        self.session_id = os.environ.get("INFER_SESSION_ID") or "sess_%d_%d" % (int(time.time()), os.getpid())
        self.messages = []
        self.system_message = None
        self.think_since_action = 0
        self.plan_approved = False
        self.plan_remaining = 0
        self.plan_note = None
        self.session_tokens = 0
        self.ctx_retry_used = False     # one trim-and-retry per context-overflow 400
        self.runaway_failures = 0       # consecutive steps whose reply was aborted twice by the guard
        self.server_failed = False      # a request failed because the model server is down/unreachable
        self.force_trim_to = 0          # set when usage.prompt_tokens > 80% of n_ctx
        self.turn_count = 0
        self.tool_count = 0             # tool calls this session (the answer footer / Ctrl-O redraw)
        self.si_failed = {}
        self.state_log = ""
        self.state_status = 0
        self.esc = ch.interrupted
        self.client = None
        self.runner = tools.ToolRunner(self.err, self.esc)
        ch.on_interrupt(self._on_interrupt)
        ch.on_set(self._on_set)

    # ── output helpers ──
    def err(self, s):
        self.ch.send("err", s=s)

    def out(self, s):
        self.ch.send("out", s=s)

    def info(self, title, body):
        self.ch.send("info", title=title, body=body)

    def _on_interrupt(self):
        if self.client is not None:
            self.client.interrupt()
        self.runner.interrupt()

    def _on_set(self, key, value):
        if not hasattr(self, "cfg"):
            return
        if key == "permission_mode":
            try:
                self._set_mode(int(value), announce=False)
            except (TypeError, ValueError):
                pass
        elif key == "auto_approve":
            self._set_mode(0 if value else 2, announce=False)
        elif key == "hide_details":
            self.cfg.hide_details = bool(value)

    def _set_mode(self, mode, announce=True):
        self.cfg.permission_mode = mode % 3
        if self.cfg.permission_mode == 0:
            os.environ["INFER_AUTO_APPROVE"] = "1"
        else:
            os.environ.pop("INFER_AUTO_APPROVE", None)
        self.ch.send("status", s="mode", mode=self.cfg.permission_mode)
        if announce:
            self.out("\033[2mpermission mode: %s\033[0m\n" % MODE_NAMES[self.cfg.permission_mode])

    # ── setup ──
    def _resolve_endpoint(self):
        env_url = os.environ.get("INFER_BASE_URL") or ""
        env_key = os.environ.get("INFER_API_KEY") or ""
        model = self.cfg.model
        p_url, p_key, p_model = load_from_profiles()
        env_url = env_url or p_url
        env_key = env_key or p_key
        model = model or p_model
        if model:
            det = detect_model_url(model)
            if det:
                env_url = det
        if not env_url or not env_key or not model:
            self.err("Error: missing required environment variables.\n")
            if not env_url:
                self.err("Please set INFER_BASE_URL environment variable.\n")
            if not env_key:
                self.err("Please set INFER_API_KEY environment variable.\n")
            if not model:
                self.err("Please set INFER_MODEL environment variable or use -m/--model flag.\n")
            return False
        os.environ["INFER_BASE_URL"], os.environ["INFER_API_KEY"], os.environ["INFER_MODEL"] = env_url, env_key, model
        self.cfg.model = model
        api = env_url + ("" if env_url.endswith("/") else "/") + "chat/completions"
        self.client = llm.Client(api, env_key, model, self.cfg.opts, debug=os.environ.get("INFER_DEBUG") is not None,
                                 log=self.err)
        return True

    def run(self):
        cfg = self.cfg = cli.parse(self.argv, os.environ, stdin_tty=os.isatty(0))
        if not self._resolve_endpoint():
            return 1
        for k, v in cfg.env_updates.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        if cfg.mode_preset:
            if cfg.preset_ok:
                self.err("\033[2m[ai] mode preset '%s' applied\033[0m\n" % cfg.mode_preset)
            else:
                self.err("\033[33m[ai] warning: unknown --mode '%s' (xhigh/normal/low/instruct); using "
                         "defaults\033[0m\n" % cfg.mode_preset)

        known = cfg.context_window or cfg.ctx_size_hint      # min(hint, /props) <= hint
        if 0 < known <= 32768 and "INFER_TOOL_PROFILE" not in os.environ:
            os.environ["INFER_TOOL_PROFILE"] = "core"
        self.tools = None if cfg.no_tools else tools.catalog()

        # behind the front-end, the parent (the C binary) holds the same stdin pipe: it is not the writer
        self.pipe_writer = session.pipe_writer(exclude=() if self.ch.headless else (os.getppid(),))
        pipe_in = session.read_stdin()
        if cfg.cmd_file and not cli.is_image_file(cfg.cmd_file):
            try:
                with open(cfg.cmd_file, "rb") as f:
                    fbuf = f.read().decode("utf-8", "replace")
            except OSError as e:
                self.err("Error: cannot open '%s': %s\n" % (cfg.cmd_file, e.strerror))
                return 1
            pipe_in = "%s\n\n%s" % (fbuf, pipe_in) if pipe_in else fbuf
            if not self.pipe_writer:
                self.pipe_writer = os.path.basename(cfg.cmd_file)
        self.stdin_tty = os.isatty(0)
        if cfg.interactive and not self.stdin_tty:
            try:
                os.close(os.open("/dev/tty", os.O_RDWR))
                self.stdin_tty = True       # the front-end reads the terminal from /dev/tty
            except OSError:
                if not cli.interactive_fallback_ok(self.argv) and not pipe_in:
                    self.err("Error: cannot start interactive mode because stdin is not a terminal and "
                             "/dev/tty cannot be opened.\n")
                    return 1
                cfg.interactive = False
        if self.ch.headless:
            cfg.interactive = False
        self.ch.send("config", interactive=cfg.interactive, quiet=cfg.quiet, hide_details=cfg.hide_details,
                     permission_mode=cfg.permission_mode, model=cfg.model, session_id=self.session_id,
                     stdin_tty=self.stdin_tty)

        prompt = cfg.prompt
        if not prompt:
            if pipe_in:
                prompt = PLACEHOLDER
            elif not cfg.interactive:
                self.err("Usage: ai [-i|--interactive] [-y|--yes] [\"prompt\"] [path/to/image.png]\n")
                return 1
        self.pipe_in = pipe_in
        self.current_prompt = prompt

        mem_query = pipe_in if (pipe_in and prompt == PLACEHOLDER) else prompt
        self.sysprompt = M.SystemPrompt(cfg, self.err)
        self.system_message = self.sysprompt.build(mem_query, prompt)
        self.messages = [self.system_message]
        if cfg.resume is not None:
            self.messages += session.transcript(cfg.resume, self.err)
        run_query = False
        if prompt or pipe_in or self.pipe_writer:
            self.messages.append(M.first_user_message(prompt, pipe_in, self.pipe_writer, cfg.image_path, self.err))
            run_query = True

        if cfg.context_window == 0:
            detected = llm.detect_context_window(self.client)
            hint = cfg.ctx_size_hint
            cfg.context_window = hint if hint > 0 and (detected <= 0 or hint < detected) else detected
            if 0 < cfg.context_window <= 32768 and "INFER_TOOL_PROFILE" not in os.environ \
                    and not cfg.no_tools and self.tools:
                os.environ["INFER_TOOL_PROFILE"] = "core"
                lean = tools.catalog()
                if lean:
                    self.tools = lean
        # measured like ai.c: strlen of the `list-tools` line (json.dumps default separators + "\n")
        tools_len = len(json.dumps(self.tools)) + 1 if self.tools else 0
        warn = cfg.budget.apply(cfg.context_window, tools_len, cfg.opts.max_tokens)
        if warn:
            self.err(warn)
        # Tools size their output to the effective cap (not INFER_MAX_TOOL_OUTPUT: a child `ai`
        # would take that as an explicit user setting).
        os.environ["INFER_EFFECTIVE_MAX_TOOL_OUTPUT"] = str(cfg.budget.max_tool)
        if os.environ.get("INFER_DEBUG") or os.environ.get("INFER_PRINT_BUDGET"):
            b = cfg.budget
            self.err("[ai] context budget: n_ctx=%d tools=%d trim=%d stub=%d max_tool=%d\n"
                     % (cfg.context_window, tools_len, b.trim, b.stub, b.max_tool))

        if cfg.interactive and not run_query:
            self.out("\033[1;35mai\033[0m \033[2m\u00b7 session %s \u00b7 type :help for commands\033[0m\n\n"
                     % self.session_id)
        first, code = True, 0
        try:
            while True:
                if cfg.interactive and (not run_query or not first):
                    run_query = self._interactive_turn()
                    if run_query is None:
                        break
                first = False
                if run_query:
                    try:
                        self._task()
                    except Exception as e:
                        # an engine bug must not lose the conversation: report, keep the session
                        import traceback
                        self.err("\n\033[1;31m[ai] internal error in the Python engine: %s: %s\033[0m\n%s"
                                 % (e.__class__.__name__, e, traceback.format_exc()))
                        code = 1
                        if not cfg.interactive:
                            break
                if not cfg.interactive:
                    break
        finally:
            self._finish()
        if self.server_failed and not cfg.interactive:
            code = code or 1      # the model server was down / failed: never a silent success
        return code

    def _finish(self):
        session.save(self.messages, self.session_id, self.cfg.private)
        session.prune()
        self.runner.close()
        self.err("\033[2m  session ended \u00b7 resume: ai -r %s\033[0m\n" % self.session_id)

    # ── interactive ──
    def _interactive_turn(self):
        """Prompt once. True: a task should run; False: nothing to run; None: leave."""
        cfg = self.cfg
        if self._req_size() > cfg.budget.trim * 3:
            self.err("\033[2m[ai] Context is large (%d KB). Auto-compacting...\033[0m\n" % (self._req_size() // 1024))
            self._compact(cfg.budget.trim * 6 // 10)
        self.turn_count += 1
        pct = 0
        if cfg.context_window > 0 and self.session_tokens > 0:
            pct = int(100.0 * self.session_tokens / cfg.context_window)
        self.ch.send("read", ctx_pct=pct, mode=cfg.permission_mode)
        while True:
            ev = self.ch.next_input()
            if ev is None or ev.get("ev") == "eof":
                return None
            if ev.get("ev") in ("input", "command"):
                break
        line = ev.get("s") or ""
        if line in ("exit", "quit", ":quit", ":exit"):
            return None
        if not line:
            return False
        if ev.get("ev") == "command" or line.startswith(":"):
            handled = self._colon(line)
            if handled is not None:
                return handled
        self.messages.append({"role": "user", "content": line})
        self.current_prompt = line
        # Ruling 19: the memory section follows the CURRENT request, not the session's first prompt.
        self.system_message = self.sysprompt.build(line)
        if self.messages and self.messages[0].get("role") == "system":
            self.messages[0] = self.system_message
        return True

    def _wire(self):
        """The history as sent: old reasoning stripped (INFER_KEEP_REASONING); self.messages and
        the saved session keep all of it."""
        o = self.cfg.opts
        return M.request_view(self.messages, o.keep_reasoning, o.keep_reasoning_block)

    def _req_size(self):
        return M.size(self._wire())

    def _trim(self, target):
        return M.trim(self.messages, target, self.err, keep_reasoning=self.cfg.opts.keep_reasoning)

    def _compact(self, target):
        before = self._req_size()
        if target <= 0 or target >= before:
            target = before // 2
        target = max(target, 2000)
        out = self._trim(target)
        o = self.cfg.opts
        if out is self.messages or M.size(M.request_view(out, o.keep_reasoning, o.keep_reasoning_block)) >= before:
            self.err("\033[1;33m[ai] Compact: nothing could be removed (history already minimal).\033[0m\n")
            return False
        self.messages = out
        return True

    def _colon(self, line):
        """Engine-side colon-commands. Returns False when handled (no task), True when it queued a
        task (:agents), None when unknown (the line is a normal user message)."""
        cfg = self.cfg
        if line == ":compact":
            if self._compact(cfg.budget.trim // 3):
                self.info("Context Compacted", "Older messages trimmed (system prompt and latest request kept) "
                          "\u2014 continuing with reduced context.")
            return False
        if line == ":clear":
            self.messages = [self.system_message] if self.system_message else []
            self.info("Conversation Cleared", "Starting fresh with no prior context.")
            return False
        if line == ":mode":
            o = cfg.opts
            self.out("  \033[2mcurrent sampling (this session):\033[0m\n"
                     "  temperature=%s  top_p=%s  top_k=%s  min_p=%s\n"
                     "  presence_penalty=%.2f  frequency_penalty=%.2f  reasoning_effort=%s\n"
                     "  presets: xhigh (deep) \u00b7 normal (medium) \u00b7 low (fast) \u00b7 instruct (no CoT)\n"
                     % ("set" if o.temperature >= 0 else "default", "set" if o.top_p >= 0 else "default",
                        "set" if o.top_k > 0 else "default", "set" if o.min_p >= 0 else "default",
                        o.presence_penalty, o.frequency_penalty, o.reasoning_effort or "(default)"))
            return False
        if line.startswith(":mode "):
            preset = line[6:].lstrip(" ")[:31]
            o = cfg.opts
            if o.apply_preset(preset):
                self.info("Mode Switched", "Sampling preset '%s' active for the next turns of this session.\n"
                          "  temperature=%.2f  top_p=%.2f  top_k=%d  min_p=%.1f\n"
                          "  presence_penalty=%.2f  frequency_penalty=%.2f  reasoning_effort=%s"
                          % (preset, o.temperature, o.top_p, o.top_k, o.min_p, o.presence_penalty,
                             o.frequency_penalty, o.reasoning_effort))
            else:
                self.info("Unknown Mode", "Presets: :mode xhigh \u00b7 :mode normal \u00b7 :mode low \u00b7 :mode instruct")
            return False
        if line == ":budget":
            b = cfg.opts.reasoning_budget
            msg = ("Current reasoning thinking budget: %d tokens." % b if b > 0 else
                   "Reasoning thinking budget: 0 tokens (thinking disabled / instant answer)." if b == 0 else
                   "Reasoning thinking budget: unrestricted (no token cap).")
            self.info("Thinking Budget", msg)
            return False
        if line.startswith(":budget "):
            src = line[8:].lstrip(" ")
            if src.lower() in ("off", "unlimited", "none") or src == "-1":
                cfg.opts.reasoning_budget = -1
                self.info("Thinking Budget Cleared", "Reasoning thinking budget set to unrestricted.")
            elif src[:1].isdigit():
                cfg.opts.reasoning_budget = native._atoi(src)
                self.info("Thinking Budget Updated", "Reasoning thinking budget set to %d tokens for this session."
                          % cfg.opts.reasoning_budget)
            else:
                self.info("Invalid Budget", "Usage: :budget <tokens|off> (e.g. :budget 4096, :budget 8192, :budget off)")
            return False
        if line == ":commit":
            native.git_commit("user-commit", cfg.commit_msg, self.err)
            self.info("Git Committed", "Changes staged and committed to git.")
            return False
        if line == ":undo":
            subprocess.run("git -C . log -1 --format=%h 2>/dev/null | xargs -I{} git -C . reset --soft HEAD~1 "
                           "2>/dev/null", shell=True)
            self.info("Git Undo", "Last commit undone (changes kept staged).")
            return False
        if line in (":git-diff", ":git-status"):
            cmd = "git -C . diff --stat 2>/dev/null" if line == ":git-diff" else "git -C . status -s 2>/dev/null"
            res = subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout
            title, empty = (("Git Diff", "No staged changes.") if line == ":git-diff"
                            else ("Git Status", "Working tree clean."))
            if res:
                self.out("\n\033[36m\033[1;36m%s:\033[0m\n%s\033[0m\n" % (title, res))
            else:
                self.out("\033[2m%s\033[0m\n" % empty)
            return False
        if line == ":notify":
            cfg.notify = not cfg.notify
            self.out("\033[36mOS notifications %s\033[0m\n" % ("enabled" if cfg.notify else "disabled"))
            return False
        if line in (":private", ":incognito"):
            cfg.private = not cfg.private
            if cfg.private:
                cfg.git_enabled = False
                os.environ["INFER_PRIVATE_MODE"] = "1"
                self.info("Private Mode ON", "No session history, metrics, or telemetry will be saved to disk.")
            else:
                os.environ.pop("INFER_PRIVATE_MODE", None)
                self.info("Private Mode OFF", "Session logging and persistence restored.")
            return False
        if line == ":agents":
            return self._agents_md()
        if line == ":status":
            ctx = self._req_size()
            t = cfg.budget.trim
            s = "\n\033[1;36mSession status:\033[0m\n  Model          : %s\n  Context size   : %d KB\n" % (cfg.model, ctx // 1024)
            if cfg.context_window > 0:
                s += "  Context window : %d tokens\n" % cfg.context_window
            s += ("  Trim threshold : %d bytes\n  Auto-compact at: %d bytes (~%.0f KB)\n  :compact needed: %s\n"
                  "  Auto-approve   : %s\n\n" % (t, t * 3, t * 3.0 / 1024, "YES (recommended)" if ctx > t * 2 else "no",
                                               "\033[1;33mON\033[0m (Shift-Tab to disable)" if cfg.permission_mode == 0
                                               else "off"))
            self.out(s)
            return False
        if line == ":auto":
            self._set_mode(cfg.permission_mode + 1)
            return False
        if line == ":memory":
            mem = M.memory_section(None)
            if mem:
                self.out("\n\033[1;36mPersistent memory:\033[0m\n%s\n\n" % mem)
            else:
                self.out("\033[2m[no persistent memory saved yet]\033[0m\n")
            return False
        return None

    def _agents_md(self):
        self.err("\033[2m[ai] Scanning project for agents.md generation...\033[0m\n")
        cmd = ("find . -maxdepth 2 -type f \\( -name '*.py' -o -name '*.js' -o -name '*.ts' "
               "-o -name '*.rs' -o -name '*.c' -o -name '*.cpp' -o -name '*.h' -o -name '*.go' "
               "-o -name '*.java' -o -name '*.rb' -o -name '*.sh' -o -name '*.md' -o -name '*.toml' "
               "-o -name '*.yaml' -o -name '*.yml' -o -name '*.json' -o -name '*.txt' "
               "-o -name 'Makefile' -o -name 'CMakeLists.txt' -o -name 'Cargo.toml' "
               "-o -name 'go.mod' -o -name 'package.json' -o -name 'requirements.txt' "
               "-o -name 'Dockerfile' -o -name 'docker-compose.yml' "
               "-o -name '*.sql' -o -name '*.html' -o -name '*.css' \\) "
               "! -path '*/node_modules/*' ! -path '*/.git/*' ! -path '*/__pycache__/*' "
               "! -path '*/venv/*' ! -path '*/.venv/*' ! -path '*/.tox/*' "
               "-printf '%y %s %p\\n' 2>/dev/null | sort")
        scan = subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout
        prompt = ("Run the command: write_file {\"path\":\"./AGENTS.md\", \"content\": ...} with the content you "
                  "generate below.\n\nHere is the project file scan:\n```\n%s\n```\n\nPlease generate an AGENTS.md "
                  "that includes: a concise project structure overview (highlighting the main entry points and "
                  "architecture), a clear description of how to build and run the project, key rules the agent "
                  "should follow based on the codebase conventions, and a workflow section. Tailor the content "
                  "to this specific project." % scan)
        self.messages.append({"role": "user", "content": prompt[:8191]})
        self.err("\033[2m[ai] Sending project scan to AI for AGENTS.md generation...\033[0m\n")
        return True

    # ── one task ──
    def _step_limit(self):
        if self.cfg.continue_until_done:
            return 999999
        v = _env_int("INFER_STEP_LIMIT", 0)
        if not self.stdin_tty:
            return v if v > 0 else 60
        return v if v > 0 else 30

    def _task(self):
        cfg = self.cfg
        step_limit = self._step_limit()
        self.think_count = 0
        self.si_failed = {}
        self.runner.clear()
        self.state_log, self.state_status = "", 0
        self.plan_approved = False
        self.esc.clear()
        self.last_tool = None
        self.same_tool_count = 0
        length_nudges = 0
        timeout_fired = False
        task_start = time.monotonic()
        loop_count = 0
        has_more = True
        self.ch.send("status", s="agent_start")
        try:
            while True:
                while has_more and loop_count < step_limit:
                    loop_count += 1
                    r = self._iteration(loop_count, step_limit, length_nudges)
                    has_more, loop_count, length_nudges, stop = r
                    if stop:
                        break
                    if cfg.task_timeout > 0 and has_more:
                        elapsed = time.monotonic() - task_start
                        if elapsed > cfg.task_timeout and timeout_fired:
                            self.err("\033[1;33m[ai] task timeout: no task_complete after the final iteration; "
                                     "stopping.\033[0m\n")
                            has_more = False
                        elif elapsed > cfg.task_timeout:
                            self.err("\033[1;33m[ai] task timeout (%.0fs / %ds limit). Forcing task_complete.\033[0m\n"
                                     % (elapsed, cfg.task_timeout))
                            self.messages.append({"role": "user", "content": "[TIMEOUT] Maximum task duration reached. "
                                                  "You MUST call task_complete NOW with your current best answer. "
                                                  "No more tool calls."})
                            loop_count = step_limit - 1
                            timeout_fired = True
                if has_more and loop_count >= step_limit:
                    if self.stdin_tty:
                        rep = self.ch.approve("continue", steps=loop_count)
                        if rep.get("ok"):
                            step_limit += 30
                            continue
                    if not os.isatty(1):
                        self.out("Task execution reached step limit (%d steps) without explicit task_complete. "
                                 "Completed operations have been saved to session.\n" % loop_count)
                    self.err("\033[1;33m  task stopped after %d steps\033[0m\n" % loop_count)
                break
        finally:
            self.ch.send("status", s="agent_end")

    def _request(self, loop_count):
        """One chat request (with the profile-URL fallback on connect failure)."""
        cfg = self.cfg
        self.esc.clear()
        self.ch.send("status", s="request")

        def on_chunk(kind, s):
            if kind == "content":
                self.ch.send("text", s=s)
            elif kind == "reasoning":
                self.ch.send("thinking", s=s, stream=True)
            else:
                self.ch.send("status", s="toolcall")
        guard = self._guard_factory()
        t0 = time.monotonic()
        res = self.client.chat(self._wire(), self.tools, on_chunk=on_chunk, debug_tag=loop_count, guard=guard)
        if res.get("connect_failed") and not res.get("interrupted"):
            p_url, p_key, p_model = load_from_profiles()
            if p_url:
                api = p_url + ("" if p_url.endswith("/") else "/") + "chat/completions"
                if api != self.client.api_url:
                    if self.client.debug:
                        self.err("Warning: Connection to environment endpoint %s failed.\n" % self.client.api_url)
                    self.client.api_url = api
                    if p_key:
                        self.client.api_key = p_key
                        os.environ["INFER_API_KEY"] = p_key
                    if p_model:
                        self.client.model = cfg.model = p_model
                        os.environ["INFER_MODEL"] = p_model
                    os.environ["INFER_BASE_URL"] = p_url
                    if cfg.context_window == 0:
                        cfg.context_window = llm.detect_context_window(self.client)
                    self.ch.send("status", s="request")
                    t0 = time.monotonic()
                    res = self.client.chat(self._wire(), self.tools, on_chunk=on_chunk, debug_tag=loop_count,
                                           guard=guard)
        if res.get("guard") and not res.get("interrupted") and not self.esc.is_set():
            res = self._recover_runaway(res, loop_count, on_chunk, guard, t0)
        res["elapsed"] = time.monotonic() - t0
        self.ch.send("status", s="response", reasoning=res["message"].get("reasoning_content") or "")
        return res

    def _guard_factory(self):
        o = self.cfg.opts
        if o.step_max_tokens <= 0 and not o.repetition_guard and o.reasoning_budget <= 0:
            return None
        # a server-side reasoning budget is enforced by the server; 2x it is the client backstop
        r_cap = 2 * o.reasoning_budget if o.reasoning_budget > 0 else 0
        return lambda: llm.RunawayGuard(o.step_max_tokens, o.repetition_guard, reasoning_max_tokens=r_cap)

    def _recover_runaway(self, res, loop_count, on_chunk, guard, t0):
        """The stream was aborted (repetition loop or step token cap): drop the partial reply,
        nudge, retry once with presence_penalty >= 1.5 and low reasoning effort. A second abort
        ends the step: the model sees an error message on its next request."""
        o = self.cfg.opts
        trip = res["guard"]
        self._log_guard(trip, 1, t0)
        retry = dataclasses.replace(o, presence_penalty=max(o.presence_penalty, RUNAWAY_PRESENCE),
                                    reasoning_effort=o.reasoning_effort
                                    if (o.reasoning_effort or "").lower() in ("none", "low", "minimal") else "low")
        self.err("\n\033[1;33m[ai] runaway generation stopped (%s); retrying with presence_penalty %.1f, "
                 "reasoning %s.\033[0m\n" % (_describe_trip(trip), retry.presence_penalty, retry.reasoning_effort))
        self.messages.append({"role": "user", "content": RUNAWAY_NUDGE if trip.get("kind") == "repetition"
                              else RUNAWAY_CAP_NUDGE})
        self.ch.send("status", s="request")
        res = self.client.chat(self._wire(), self.tools, on_chunk=on_chunk, debug_tag=loop_count, opts=retry,
                               guard=guard)
        if not res.get("guard") or res.get("interrupted") or self.esc.is_set():
            return res
        trip = res["guard"]
        self._log_guard(trip, 2, t0)
        self.err("\n\033[1;31m[ai] runaway generation stopped again (%s); ending this step.\033[0m\n"
                 % _describe_trip(trip))
        self.messages.append({"role": "user", "content": RUNAWAY_FAILED % _describe_trip(trip)})
        return {"message": {"role": "assistant", "content": None}, "finish_reason": None, "usage": {},
                "error": None, "interrupted": False, "status": 200, "runaway_failed": True}

    def _log_guard(self, trip, attempt, t0):
        try:
            from aitools import metrics
            metrics.log_metric("generation_guard", (time.monotonic() - t0) * 1000, success=False,
                               error="%s attempt=%d" % (_describe_trip(trip), attempt))
        except Exception:
            pass

    def _iteration(self, loop_count, step_limit, length_nudges):
        """One loop iteration. Returns (has_more, loop_count, length_nudges, stop_inner_loop)."""
        cfg = self.cfg
        # Sizes are of the request (old reasoning stripped), not of the full history.
        if cfg.budget.trim > 0 and self._req_size() > cfg.budget.trim:
            self.messages = self._trim(cfg.budget.trim * 6 // 10)
        # The server's token count said the last prompt was near the window: the ~3 chars/token
        # guess is off for this content, so trim by the measured ratio.
        if self.force_trim_to > 0:
            if self._req_size() > self.force_trim_to:
                self.messages = self._trim(self.force_trim_to)
            self.force_trim_to = 0
        req_size = self._req_size()
        while not self.ch.btw.empty() and not self.esc.is_set():
            note = self.ch.btw.get()
            self.messages.append({"role": "user", "content": "[User note mid-task: %s]" % note})
            self.err("\033[2m[btw] injected: %s\033[0m\n" % note)

        res = self._request(loop_count)
        if self.esc.is_set() or res.get("interrupted"):
            self.err("\n\033[1;31m[ai] Interrupted by user (ESC).\033[0m\n")
            return False, loop_count, length_nudges, True
        if res.get("runaway_failed"):
            self.runaway_failures += 1
            if self.runaway_failures >= RUNAWAY_STEP_CAP:
                self.err("\033[1;31m[ai] the model's replies were aborted as runaway %d steps in a row; stopping "
                         "the task.\033[0m\n" % self.runaway_failures)
                self.runaway_failures = 0
                return False, loop_count, length_nudges, True
            return True, loop_count, length_nudges, False
        self.runaway_failures = 0
        if res.get("status", 0) == 0 and res.get("error"):
            self.err("\033[1;31m%s\033[0m\n" % llm.failure_line(res, self.client.api_url))
            self.server_failed = True
            return True, loop_count, length_nudges, True
        if res.get("status", 200) >= 400:
            e = res.get("error") or "Unknown server error."
            if "model output must contain" in e or "output text or tool calls" in e:
                self.err("\033[1;33m[ai] Warning: model returned empty output \u2014 nudging to call task_complete.\033[0m\n")
                self.messages.append({"role": "user", "content": "Your last response was empty. Please call "
                                      "task_complete now with a summary of what you just did."})
                return True, loop_count, length_nudges, True
            if not self.ctx_retry_used and is_context_overflow(e):
                # Recoverable once: the prompt overflowed the server's context -> trim to half the
                # usual target and retry.
                self.ctx_retry_used = True
                target = min((cfg.budget.trim if cfg.budget.trim > 0 else 100000) * 3 // 10, self._req_size() // 2)
                self.err("\033[1;33m[ai] request exceeded the model's context window; trimming history to ~%d KB "
                         "and retrying once.\033[0m\n" % (target // 1024))
                self.messages = self._trim(target)
                return True, loop_count, length_nudges, False
            if res.get("unavailable") or res.get("status", 0) >= 500:
                self.err("\n\033[1;31m%s\033[0m\n" % llm.failure_line(res, self.client.api_url))
                self.server_failed = True
            else:
                self.err("\n\033[1;31m[ai Error]\033[0m %s\n" % e)
            return False, loop_count, length_nudges, True
        self.ctx_retry_used = False

        usage = res.get("usage") or {}
        if usage:
            self.session_tokens = usage.get("total_tokens", 0)
        pt = int(usage.get("prompt_tokens") or 0)
        n = cfg.context_window
        if n > 0 and pt > n // 10 * 8 and req_size > 0:
            self.force_trim_to = int(req_size * 0.6 * n / pt)
            self.err("\033[2m[ai] prompt used %d of %d context tokens; trimming history to ~%d KB before the next "
                     "request.\033[0m\n" % (pt, n, self.force_trim_to // 1024))
        msg = M.clean_message(res["message"])
        self.messages.append(msg)
        elapsed = res.get("elapsed", 0.0)
        completion_tokens = usage.get("completion_tokens", 0)
        has_more = True
        calls = msg.get("tool_calls") or []
        if calls:
            content = msg.get("content") if isinstance(msg.get("content"), str) else None
            truncated = res.get("finish_reason") == "length"
            for i, call in enumerate(calls):
                if truncated and (i == len(calls) - 1 or not _strict_json_args(call)):
                    # The reply was cut off at the token limit: the last call's arguments are
                    # incomplete (and the JSON repair would "close" them -> a truncated file).
                    fn = call.get("function") or {}
                    self.err("\033[1;33m[ai] tool call %s was cut off at the token limit; not executed.\033[0m\n"
                             % (fn.get("name") or "?"))
                    self.messages.append({"role": "tool", "tool_call_id": call.get("id") or "",
                                          "content": TRUNCATED_CALL_ERROR})
                    continue
                done = self._tool_call(call, content, loop_count, completion_tokens)
                if ((call.get("function") or {}).get("name") or "").strip(" \t\r\n"):
                    content = None  # ai.c hands the turn's text only to the first executed call
                if done:
                    has_more = False
                    break
            if self.esc.is_set():
                self.err("\n\033[1;31m[ai] Task interrupted (ESC). Returning to prompt.\033[0m\n")
                has_more = False
        elif res.get("finish_reason") == "length":
            cap = _env_int("INFER_NUDGE_CAP", 0)
            cap = cap if cap > 0 else 5
            length_nudges += 1
            self.err("\033[1;33m[ai] Warning: model hit token limit \u2014 response truncated. Nudging to complete.\033[0m\n")
            if length_nudges >= cap:
                self.err("\033[1;31m[ai] Model stalled (hit token limit %d consecutive times with no completion). "
                         "Forcing task_complete.\033[0m\n" % length_nudges)
                self.messages.append({"role": "user", "content": "[STALL] Your responses keep hitting the token "
                                      "limit without any progress and without calling task_complete. STOP taking "
                                      "additional actions. Call task_complete NOW with your best summary of work "
                                      "done so far. Do not call any more tools."})
                loop_count = step_limit - 1
                length_nudges = 0
            else:
                self.messages.append({"role": "user", "content": LENGTH_NUDGE})
        else:
            has_more = False
            content = msg.get("content")
            if isinstance(content, str):
                leaked = native.extract_leaked_task_complete(content)
                text = leaked if leaked is not None else content
                session.log_job(self.current_prompt, self.pipe_writer, text, cfg.interactive, self.session_id,
                                cfg.private)
                tps = completion_tokens / elapsed if (elapsed > 0.05 and completion_tokens > 0) else 0.0
                self._final(text, elapsed, tps, fallback_model="ai" if leaked is not None else "model")
                if leaked is not None:
                    return False, loop_count, length_nudges, False
            if not (isinstance(content, str) and content.strip()) and loop_count < 28:
                self.messages.append({"role": "user", "content": "Please call task_complete with your final answer."})
                has_more = True

        if not cfg.quiet and (usage.get("prompt_tokens") or completion_tokens):
            tps = completion_tokens / elapsed if (elapsed > 0.05 and completion_tokens > 0) else 0.0
            if cfg.context_window > 0:
                pct = int(100.0 * usage.get("total_tokens", 0) / cfg.context_window)
                self.err("\n\033[2m  loop %d \u00b7 ctx %d/%d (%d%%) \u00b7 +%d tok \u00b7 %.0f tok/s\033[0m\n"
                         % (loop_count, usage.get("total_tokens", 0), cfg.context_window, pct, completion_tokens, tps))
            else:
                self.err("\n\033[2m  loop %d \u00b7 %d ctx \u00b7 +%d new \u00b7 %.0f tok/s\033[0m\n"
                         % (loop_count, usage.get("prompt_tokens", 0), completion_tokens, tps))
        if self.esc.is_set():
            self.err("\033[1;33m[ai] Task interrupted by user (ESC/Ctrl+C).\033[0m\n")
            self.messages.append({"role": "user", "content": "[INTERRUPTED: Task interrupted by user (ESC/Ctrl+C) "
                                  "during tool execution.]"})
            return False, loop_count, length_nudges, True
        return has_more, loop_count, length_nudges, False

    def _final(self, text, elapsed, tps, fallback_model="ai"):
        rendered = render(text)
        self.ch.send("final", markdown=text, rendered=rendered, model=self.cfg.model or fallback_model,
                     turn=self.turn_count, tools=self.tool_count, elapsed=elapsed, tps=tps)

    # ── one tool call ──
    def _tool_call(self, call, turn_content, loop_count, completion_tokens):
        """Run one tool call and append its tool message. Returns True when the task is done."""
        cfg = self.cfg
        t0 = time.monotonic()
        call_id = call.get("id") or ""
        fn = call.get("function") or {}
        name = fn.get("name") or ""
        args = fn.get("arguments")
        if not isinstance(args, str):
            args = json.dumps(args) if args is not None else ""
        if not name.strip(" \t\r\n"):
            self.messages.append({"role": "tool", "tool_call_id": call_id,
                                  "content": "Error: Model returned an empty tool name."})
            return False
        task_done = False
        output = None
        self.tool_count += 1
        self.ch.send("tool_start", name=name, args=args)

        if self.last_tool == (name, args):
            self.same_tool_count += 1
        else:
            self.same_tool_count = 0
            self.last_tool = (name, args)

        if self.same_tool_count >= 2:
            cap = _env_int("INFER_TOOL_LOOP_CAP", 0)
            cap = cap if cap > 0 else 6
            if self.same_tool_count >= cap:
                self.err("\033[1;31m[ai] Infinite tool loop: identical tool+args called %d times in a row. Aborting "
                         "agent loop and forcing task_complete.\033[0m\n" % self.same_tool_count)
                self.messages.append({"role": "user", "content": "[HARD STOP] You called the exact same tool with "
                                      "the same arguments %d times in a row. You are in an infinite tool loop. "
                                      "Aborting. Call task_complete NOW with your best summary. Do NOT call any more "
                                      "tools." % self.same_tool_count})
                output = '{"ok":true}'
                task_done = True
            else:
                output = ("Error: You are stuck in a loop calling the exact same tool with the exact same arguments. "
                          "Stop and try a different approach, or call task_complete.")
        elif name == "think":
            output = self._think(args)
        elif name == "task_complete":
            self._task_complete(args, turn_content, t0, completion_tokens)
            output = '{"ok":true}'
            task_done = True
        elif name == "present_plan":
            output = self._present_plan(args)
        elif name == "execute_command":
            output = self._execute_command(args)
        else:
            output = self._other_tool(name, args)
        if output is None:
            output = "Error: failed to execute tool"

        if output and ("[SKILL_CREATED" in output or "[SKILL_UPDATED" in output or "[SKILL: updated" in output):
            self.err("\n\033[1;36m[ai] %s\033[0m\n" % output)

        raw_output = output
        if name not in ("think", "task_complete"):
            output = self._decorate(name, args, output, loop_count, t0)

        cap = cfg.budget.max_tool
        out_bytes = output.encode("utf-8", "surrogateescape")
        if len(out_bytes) > cap:                  # bytes, like ai.c's strlen()
            orig = len(out_bytes)
            if orig > cap * 1.5:
                shown = 2000
                suffix = ("\n\n... [CRITICAL ERROR: Tool output was %d bytes! This massively exceeds the safety limit "
                          "of %d bytes.\nTo prevent context window overflow and immediate failure, the output has been "
                          "BLOCKED.\nYOU MUST REDO THIS ACTION using a targeted approach. DO NOT attempt to load large "
                          "datasets into context.\nWrite targeted Python scripts, or use 'head', 'tail', 'grep' to "
                          "filter the data.]" % (orig, cap))
            else:
                shown = cap
                suffix = ("\n\n... [TRUNCATED: Tool output was %d bytes. Capped at %d bytes. The model should decide "
                          "if it wants to use a different tool/command to narrow down the query (e.g. grep, find, "
                          "head/tail, line-range read_file), or run the command with pagination, or proceed with "
                          "the truncated context.]" % (orig, cap))
            output = out_bytes[:shown].decode("utf-8", "ignore") + suffix
        if self._req_size() > cfg.budget.stub:
            output = "[context limit reached \u2014 result omitted to preserve model focus]"

        if name == "reset_context":
            self.err("[ai] Context reset requested \u2014 truncating message history to system prompt.\n")
            self.messages = [self.system_message] if self.system_message else []
            self.messages.append({"role": "user", "content": "[SYSTEM] Context successfully reset. The history has "
                                  "been cleared to preserve reasoning capacity. Please continue your plan from here."})
            return task_done

        if self.plan_note:
            output = self.plan_note + output
            self.plan_note = None
        sc = os.environ.get("INFER_STATE_CONTEXT")
        if self.state_log and name not in ("think", "task_complete") and (sc is None or native._atoi(sc) != 0):
            output = "[CURRENT STATE step %d] %s -> %s  |  %s\n%s" % (
                loop_count, name, "error" if self.state_status else "ok", self.state_log, output)
        self.messages.append({"role": "tool", "tool_call_id": call_id, "name": name, "content": output})

        if raw_output.startswith("[IMAGE_DATA_SUCCESS:"):
            end = raw_output.find("]", 20)
            if end > 0:
                img = M.image_message(raw_output[20:end])
                if img:
                    self.messages.append(img)
        return task_done

    def _decorate(self, name, args, output, loop_count, t0):
        """Error classification, state log, hints, self-improvement, tool box, `name\\n` prefix."""
        is_err = (output.startswith("Error:") or output.startswith("[Command Failed") or output.startswith('{"error"')
                  or "failed (exit" in output or "[SYSTEM WARNING:" in output)
        self.state_status = 1 if is_err else 0
        if name not in native.READONLY:
            self.think_since_action = 0
        sc = os.environ.get("INFER_STATE_CONTEXT")
        if sc is None or native._atoi(sc) != 0:
            entry = "%d:%s=%s;" % (loop_count, name or "?", "ERR" if is_err else "ok")
            entry = _cut_bytes(entry, 120)
            log = self.state_log
            while len((log + entry).encode()) + 1 > 512:
                k = log.find(";")
                if k < 0:
                    log = ""
                    break
                log = log[k + 1:]
            self.state_log = log + entry
        graph, hint = "", ""
        if is_err:
            if "No such file" in output or "FileNotFoundError" in output:
                hint = "\n\n[HINT: File or path not found. Use list_directory to check the directory contents.]"
            elif "Permission denied" in output:
                hint = "\n\n[HINT: Permission denied. Check file permissions or paths.]"
            elif any(x in output for x in ("timed out", "Connection refused", "429", "503")):
                hint = "\n\n[HINT: Network timeout or rate limit. Retry after a brief pause or try an alternative endpoint.]"
            elif "Missing required argument" in output or "Invalid argument type" in output:
                hint = "\n\n[HINT: Tool argument validation failed. Verify required parameters and types before retrying.]"
            elif not output.startswith("[Command Failed"):
                graph = ("\n\n[GRAPH ENFORCEMENT: Middleware intercepted an exception. You must pause, recalculate "
                         "your approach, and try a different strategy.]")

        output = self._self_improve(name, args, output, is_err)

        shown = output + graph + hint
        self.ch.send("tool_end", name=name, ok=not is_err, status="error" if is_err else "ok", preview=shown,
                     elapsed=time.monotonic() - t0, turn=self.turn_count, tools=self.tool_count)
        return "%s\n%s%s%s" % (name, output, graph, hint)

    def _self_improve(self, name, args, output, is_err):
        si_name = name.split("__", 1)[1] if "__" in name else name
        si_name = _cut_bytes(si_name, 128)
        if is_err or "-- failed (exit" in output:
            err_head = _cut_bytes(output, 1000)
            cid = native.chain_id(si_name, output)
            try:
                from aitools.selfimprove import record_failure
                record_failure(si_name, _cut_bytes(args, 400), err_head, "execution", chain_id=cid or None)
            except Exception:
                pass
            if si_name in self.si_failed or len(self.si_failed) < SI_MAX_TRACKED:
                self.si_failed[si_name] = {"args": _cut_bytes(args, 400), "error": _cut_bytes(output, 600),
                                           "chain_id": _cut_bytes(cid or "", 100)}
            try:
                from aitools import state
                from aitools.selfimprove import lessons_for
                with state.lock_budget(1.5):
                    lessons = lessons_for(si_name, err_head)
            except Exception:
                lessons = ""
            if lessons:
                output = "[REMEMBERED FROM PAST SESSIONS (self-improvement)]\n%s\n---\n%s" % (lessons, output)
                self.err("\033[1;36m[ai] surfaced past lesson for '%s' to model\033[0m\n" % si_name)
        else:
            prev = self.si_failed.pop(si_name, None)
            if prev is not None:
                try:
                    from aitools.selfimprove import record_recovery
                    lesson = record_recovery(si_name, _cut_bytes(args, 400), prev["error"], "execution",
                                             chain_id=prev["chain_id"] or None, failed_args=prev["args"])
                except Exception:
                    lesson = None
                if lesson is not None:
                    self.err("\033[1;36m[ai] learned fix for '%s': %s\033[0m\n" % (si_name, (lesson + "\n") if lesson else ""))
        return output

    # ── native tools ──
    def _think(self, args):
        self.think_count += 1
        if self.think_count > 12:
            return ("Error: You have already called the 'think' tool once to plan. Do not call it again. You must "
                    "call 'task_complete' to report your final answer/summary to the user, or call other action tools "
                    "if there is more work to do. Calling 'think' repeatedly causes infinite loops.")
        reasoning = native.arg_text(args, native.THINK_KEYS)
        if reasoning is None:
            return None
        limit = _env_int("INFER_THINK_MAX_CHARS", 0) or 2200
        rlen = len(reasoning.encode("utf-8", "surrogateescape"))
        self.think_since_action += 1
        if rlen > limit:
            reasoning = _cut_bytes(reasoning, limit + 1)
        if not self.cfg.quiet:
            self.ch.send("thinking", s=reasoning, stream=False)
        act = _env_int("INFER_THINK_ACTION_LIMIT", 0)
        act = act if act > 0 else 3
        if rlen > limit:
            return ("Error: Your 'think' reasoning was very long (%d chars) so it was cut to %d chars to save budget. "
                    "Long think blocks are the #1 reason small local models stall and never produce output. Keep "
                    "'think' to a MAXIMUM of 3 short sentences. Do NOT re-type your plan, code, or analysis inside "
                    "thinking. Take your next concrete action with an ACTION tool (write_file to write your code, or "
                    "execute_command to build/run) instead of thinking more." % (rlen, limit))
        if self.think_since_action >= act:
            return ("Error: You have called 'think' %d times in a row without taking any concrete action (writing a "
                    "file or running a command). You are not making progress. STOP thinking. Take ONE concrete action "
                    "NOW with write_file (write your code) or execute_command (build/run). You may call 'think' again "
                    "only AFTER you have acted." % self.think_since_action)
        return '{"ok":true}'

    def _task_complete(self, args, turn_content, t0, completion_tokens):
        summary = native.arg_text(args, native.SUMMARY_KEYS)
        if summary is None:
            return
        final = summary
        if turn_content and turn_content.strip():
            fillers = ("Done.", "Done", "Task complete.", "Task completed.", "Finished.")
            if (any(x in summary for x in ("Small talk", "small talk", "no task", "No task", "standing by",
                                           "Standing by")) or summary in fillers
                    or len(turn_content.encode()) >= len(summary.encode()) + 20):
                final = turn_content
            elif summary not in turn_content and turn_content not in summary:
                final = "%s\n\n%s" % (turn_content, summary)
        cfg = self.cfg
        session.log_job(self.current_prompt, self.pipe_writer, final, cfg.interactive, self.session_id, cfg.private)
        elapsed = time.monotonic() - t0
        tps = completion_tokens / elapsed if (elapsed > 0.05 and completion_tokens > 0) else 0.0
        self._final(final, elapsed, tps)

    def _present_plan(self, args):
        cfg = self.cfg
        plan = _json_get_string(args, "plan")
        if plan is None:
            plan = _json_get_string(args, "summary")
        if plan is None:
            plan = args
        if cfg.permission_mode == 1:
            if os.environ.get("INFER_PLAN_AUTOAPPROVE"):
                approved = 1
            else:
                rep = self.ch.approve("confirm", title="APPROVE PLAN", detail=plan)
                ans = rep.get("answer")
                approved = -1 if ans in ("notty", "eof") else (1 if rep.get("ok") else 0)
                if approved == -1:
                    self.plan_approved = False
                    esc = json.dumps(plan or "", ensure_ascii=False)[1:-1]
                    return ("[PLAN MODE] This is a non-interactive run, so your plan cannot be approved on this "
                            "terminal. STOP making tool calls now. Report your complete plan (investigation, proposed "
                            "changes, commands, and rationale) as your final answer via task_complete. Do not attempt "
                            "any state-changing tool again.\n\nYOUR PLAN TO REPORT TO THE USER:\n%s" % esc)
            self.plan_approved = bool(approved)
            self.plan_remaining = self._plan_budget() if approved else 0
            self.plan_note = None
        else:
            self.plan_approved = False
            approved = cfg.permission_mode == 0
        if approved:
            return ("PLAN APPROVED. You may now proceed to make the proposed changes. Work autonomously until you have "
                    "another question, then present_plan again.")
        return ("PLAN NOT APPROVED. Revise your plan based on the user's feedback and call present_plan again. Do not "
                "make any changes until the plan is approved.")

    @staticmethod
    def _plan_budget():
        v = os.environ.get("INFER_PLAN_STEP_BUDGET")
        return native._atoi(v) if v else 8

    def _plan_consume(self):
        if self.cfg.permission_mode != 1 or not self.plan_approved:
            return
        budget = self._plan_budget()
        if budget <= 0:
            return
        if self.plan_remaining > 0:
            self.plan_remaining -= 1
        if self.plan_remaining <= 0:
            self.plan_approved = False
            self.plan_remaining = 0
            if not self.plan_note:
                self.plan_note = ("[PLAN BUDGET EXHAUSTED] The approved plan's step budget is spent. Do NOT start new "
                                  "state-changing work. Either call present_plan with the next concrete set of changes "
                                  "to receive a new approval, or call task_complete to report progress. Further changes "
                                  "are blocked until a new plan is approved.")

    def _execute_command(self, args):
        cfg = self.cfg
        cmd, timeout = native.command_args(args)
        if cmd is None:
            return "Error: 'command' argument not found"
        if native.is_command_denied(cmd):
            self.err("[ai] Security block: command matches INFER_COMMAND_DENYLIST filter.\n")
            return "Error: Command execution blocked by security denylist filter (INFER_COMMAND_DENYLIST)."
        if cfg.dry_run:
            self.err("[ai] [Dry-run] Would execute: %s\n" % cmd)
            return "[Command Dry-Run Success]\nWould execute: %s" % cmd
        if cfg.permission_mode == 1 and not self.plan_approved:
            return ("Error: [PLAN MODE] You must call present_plan to present your plan and receive approval before "
                    "executing commands or changing anything. Nothing was changed.")
        approved = 1 if (cfg.permission_mode == 0 or self.plan_approved) else 0
        if not approved:
            rep = self.ch.approve("command", command=cmd)
            if rep.get("mode") is not None:
                self._on_set("permission_mode", rep["mode"])
            ans = rep.get("answer")
            if ans in ("y", "auto"):
                approved = 1
            elif ans == "b":
                approved = 2
            elif ans == "esc":
                self.esc.set()
            elif ans in ("notty", "eof"):
                self.err("[ai] Warning: cannot open /dev/tty for confirmation. Skipping command execution for safety.\n")
        if approved == 1:
            self.err("\r\033[2K\033[36m  \u2b21 $ %s\033[0m\n" % cmd)
            if cmd.startswith("sleep ") or " sleep " in cmd or "&& sleep " in cmd or "; sleep " in cmd:
                self.err("\r\033[2K\033[1;31m  \u25b6 sleep command rejected. Enforcing schedule_task.\033[0m\n")
                return "Error: `sleep` is forbidden in execute_command. Use `schedule_task` to wait or check status asynchronously."
            raw, code = native.run_shell("%s 2>&1" % cmd, timeout, self.esc, self.err)
            if code == 130:
                self.esc.set()
            if code == 0:
                out = "$ %s\n%s" % (cmd, raw)
            else:
                out = ("$ %s \u2014 failed (exit %d)\n%s\n[SYSTEM WARNING: Command failed. You MUST use the `think` tool "
                       "to analyze the failure, explain why it failed, and formulate a new plan before executing another "
                       "command.]" % (cmd, code, raw))
            self.runner.invalidate_files()
            if code == 0 and cfg.git_enabled and (cfg.permission_mode == 0 or self.plan_approved):
                native.git_commit("command", cfg.commit_msg, self.err)
            if cfg.permission_mode == 1:
                self._plan_consume()
            return out
        if approved == 2:
            self.err("\r\033[2K\033[36m  \u2b21 $ %s (background)\033[0m\n" % cmd)
            return native.run_background(cmd)
        self.err("[ai] command execution cancelled.\n")
        return "Error: Command execution was cancelled/denied by the user."

    def _other_tool(self, name, args):
        cfg = self.cfg
        if "__" in name:
            server, tool = name.split("__", 1)
        else:
            server, tool = name, name
        gated = tool in native.MUTATING or tool not in native.READONLY
        if cfg.permission_mode != 0 and gated:
            if cfg.permission_mode == 2:
                rep = self.ch.approve("confirm", title=tool, detail=args)
                if not rep.get("ok"):
                    return ("Error: Action denied by user (manual mode). You must get explicit approval before "
                            "changing anything.")
            elif cfg.permission_mode == 1 and not self.plan_approved:
                return ("Error: [PLAN MODE] This action changes state but no plan is approved yet. Call present_plan "
                        "with your proposed changes and get approval before proceeding. Nothing was changed.")
        if cfg.permission_mode == 1 and self.plan_approved and gated:
            self._plan_consume()
        self._status_line(server, tool, args)
        cached = self.runner.cache_get(tool, args)
        if cached is not None:
            self.err("\033[2m[ai] %s: (cached)\033[0m\n" % tool)
            return cached
        out = self.runner.call(server, tool, args)
        if out is not None and not out.startswith("Error"):
            if tool in native.MUTATING:
                self.runner.invalidate_files()
            else:
                self.runner.cache_set(tool, args, out)
        return out

    def _status_line(self, server, tool, args):
        if tool in ("read_file", "write_file", "edit_file", "list_directory"):
            self.err("\033[2m[ai] %s: %s\033[0m\n" % (tool, _json_get_string(args, "path") or "?"))
        elif tool == "web_search":
            self.err("\033[2m[ai] web_search: \"%s\"\033[0m\n" % (_json_get_string(args, "query") or "?"))
        elif tool == "fetch_webpage":
            self.err("\033[2m[ai] fetch_webpage: %s\033[0m\n" % (_json_get_string(args, "url") or "?"))
        elif tool in ("delegate_task", "parallel_fetch"):
            key = "tasks" if tool == "delegate_task" else "urls"
            try:
                n = len([x for x in json.loads(args).get(key) or [] if isinstance(x, str)])
            except (ValueError, AttributeError, TypeError):
                n = 0
            if tool == "parallel_fetch":
                self.err("\033[2m[ai] parallel_fetch: %d URL(s)\033[0m\n" % n)
            elif n > 0:
                self.err("\033[2m[ai] delegate_task: %d parallel agent(s)\033[0m\n" % n)
            else:
                self.err("\033[2m[ai] delegate_task\033[0m\n")
        elif tool == "save_memory":
            self.err("\033[2m[ai] save_memory (%d chars)\033[0m\n" % len((_json_get_string(args, "content") or "").encode()))
        else:
            self.err("\033[2m[ai] %s::%s\033[0m\n" % (server, tool))


RUNAWAY_PRESENCE = 1.5     # presence_penalty floor for the retry after a runaway abort
RUNAWAY_STEP_CAP = 3       # consecutive doubly-aborted steps before the task is stopped
RUNAWAY_NUDGE = ("Your previous reply got stuck repeating itself. Reply now with the next tool call or "
                 "task_complete; keep reasoning brief.")
RUNAWAY_CAP_NUDGE = ("Your previous reply ran past the per-step token limit and was discarded. Reply now with the "
                     "next tool call or task_complete; keep reasoning short and write large files in parts "
                     "(write_file the first part, then append/edit_file the rest).")
TRUNCATED_CALL_ERROR = ("Error: Your tool call was cut off at the token limit and was NOT executed. Write large files "
                        "in parts (write_file the first part, then append/edit_file the rest) and keep reasoning "
                        "short.")


def _strict_json_args(call):
    """The call's arguments parse as JSON as-is (no repair); empty arguments count as {}."""
    a = (call.get("function") or {}).get("arguments")
    if a is None or (isinstance(a, str) and not a.strip()):
        return True
    if not isinstance(a, str):
        return True
    try:
        json.loads(a)
        return True
    except ValueError:
        return False


RUNAWAY_FAILED = ("[GENERATION ABORTED] Your reply was aborted again (%s) and discarded. Do not repeat it: "
                  "make one short, different next step -- a single tool call, or task_complete with your "
                  "best answer so far.")


def _describe_trip(trip):
    if trip.get("kind") == "step_cap":
        return "step token cap of %d reached in %s" % (trip.get("limit", 0), trip.get("channel"))
    if trip.get("kind") == "reasoning_cap":
        return "reasoning token cap of %d (2x the reasoning budget) reached" % trip.get("limit", 0)
    return "repetition in %s, %d-char unit x%d, ~%d tokens" % (
        trip.get("channel"), trip.get("period", 0), trip.get("repeats", 0), trip.get("tokens", 0))


LENGTH_NUDGE = ("Your last response was cut off by the token limit. This means you wrote an over-long chain of thought "
                "and consumed the whole output budget WITHOUT emitting a tool call - reasoning alone never produces an "
                "artifact, and that is exactly why you are stuck. NEXT TIME: write at most 2-3 short sentences of "
                "reasoning, then IMMEDIATELY emit ONE action tool call (write_file to create your code file, or "
                "execute_command to build/run). Do NOT call task_complete until the deliverable actually exists. Repeat: "
                "short reasoning then one action tool call, right now.")

_ALIASES = {"path": ("file", "filepath", "filename", "file_path", "p"),
            "command": ("cmd", "command_line", "CommandLine", "args", "script", "code", "c"),
            "query": ("q", "search", "prompt", "term"),
            "url": ("uri", "link", "address", "u")}


def _json_get_string(args, key):
    """ai.c json_get_string(): a top-level JSON string, raw non-JSON text, the string value of `key`
    (any depth), else of its aliases."""
    try:
        obj = json.loads(args)
    except ValueError:
        return args if args and args[0] not in "{[" else None
    if isinstance(obj, str):
        return obj
    for k in (key,) + _ALIASES.get(key, ()):
        for kk, v in native._walk(obj):
            if kk == k and isinstance(v, str):
                return v
    return None


def render(text):
    """What `ai_mcp.py render-markdown <text>` prints (the C engine shows exactly that)."""
    if not text:
        return ""
    if os.environ.get("INFER_RAW_OUTPUT") == "1":
        return text + "\n"
    try:
        from aitools.render import render_markdown
        return render_markdown(text) + "\n"
    except Exception:
        return text + "\n"


def load_from_profiles():
    """`export INFER_BASE_URL/API_KEY/MODEL=` lines from ~/.bashrc then ~/.zshrc (last wins)."""
    home = os.environ.get("HOME")
    vals = {"INFER_BASE_URL": "", "INFER_API_KEY": "", "INFER_MODEL": ""}
    if not home:
        return "", "", ""
    for name in (".bashrc", ".zshrc"):
        try:
            with open(os.path.join(home, name), encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except OSError:
            continue
        for line in lines:
            line = line.rstrip("\n\r \t")
            for k in vals:
                tag = "export %s=" % k
                p = line.find(tag)
                if p >= 0:
                    v = line[p + len(tag):]
                    if v[:1] in ("\"", "'"):
                        v = v[1:]
                    while len(v) > 1 and v[-1] in ("\"", "'"):
                        v = v[:-1]
                    vals[k] = v
    return vals["INFER_BASE_URL"], vals["INFER_API_KEY"], vals["INFER_MODEL"]


def detect_model_url(model):
    """`<model> status` (snap-style model CLIs) -> its `openai:` endpoint with a trailing slash."""
    try:
        out = subprocess.run("%s status 2>/dev/null" % model, shell=True, capture_output=True, text=True,
                             timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.splitlines():
        k = line.find("openai:")
        if k >= 0:
            url = line[k + 7:].strip().split()[0] if line[k + 7:].strip() else ""
            if url:
                return url if url.endswith("/") else url + "/"
    return None


def main(argv):
    fd = None
    if argv[:1] == ["--frontend-fd"] and len(argv) > 1:
        fd = int(argv[1])
        argv = argv[2:]
    # Every intentional byte of output goes through the channel; a stray print from imported code
    # must not reach the front-end's stdout.
    sys.stdout = sys.stderr
    # Not a child subreaper (unlike ai.c, which is the front-end here and stays one): jobs a command
    # detaches must not become this engine's children, or a signal handler could take them along.
    ch = Channel(fd) if fd is not None else Local()
    if fd is not None:
        threading.Thread(target=_watch_frontend, args=(ch,), daemon=True).start()
    # Ctrl-C during a task reaches the whole foreground group (front-end, engine, running command)
    # like ai.c's exit-on-signal; SIGTERM comes from the front-end. Either way: take the command /
    # tool running right now down with us, never anything else. (A handler, not SIG_IGN: children
    # must not inherit an ignored SIGINT.)
    signal.signal(signal.SIGINT, lambda *a: _die(130))
    signal.signal(signal.SIGTERM, lambda *a: _die(143))
    code = 1
    try:
        code = Engine(ch, argv).run()
    except KeyboardInterrupt:
        code = 130
    except Exception:
        import traceback
        traceback.print_exc()
    finally:
        _FINISHED.set()
        ch.send("done", code=code)
        ch.close()
    return code


_FINISHED = threading.Event()


def _die(code):
    for pid in native.inflight_roots():
        native.kill_tree(pid)
    os._exit(code)


def _watch_frontend(ch):
    """The front-end died (socket EOF while a task runs): stop instead of running on unattended."""
    ch.closed.wait()
    if not _FINISHED.wait(0.5):
        _die(1)
