"""`ai` command line + INFER_* environment -> Config, exactly as ai.c's main() resolves them.

parse(argv, environ) is pure: it returns the Config and, in Config.env_updates, the variables ai.c
exports for child processes (the engine applies them to os.environ)."""
import os
from dataclasses import dataclass, field

# Flags ai.c skips when it joins the remaining arguments into the prompt (no value / one value).
_PROMPT_SKIP = {"-i", "--interactive", "-y", "--yes", "-c", "--continue", "--plan", "--manual", "--auto",
                "-q", "--quiet", "-n", "--no-tools", "-v", "--version", "--preserve-thinking"}
_PROMPT_SKIP_VALUE = {"-m", "--model", "-t", "--temperature", "-p", "--top-p", "-k", "--top-k", "--min-p",
                      "--reasoning", "--reasoning-effort", "--mode", "-f", "--file", "-g", "--goal"}
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")
PLACEHOLDER_PROMPT = "Answer or help with the following:"


def _truthy(v):
    return v is not None and (v == "1" or v.lower() == "true")


def _atoi(s):
    """C atoi: leading whitespace, optional sign, digits; 0 when none."""
    s = (s or "").lstrip()
    i = 1 if s[:1] in "+-" else 0
    j = i
    while j < len(s) and s[j].isdigit():
        j += 1
    try:
        return int(s[:j]) if j > i else 0
    except ValueError:
        return 0


def _atof(s):
    """C atof on the longest numeric prefix; 0.0 when none."""
    s = (s or "").strip()
    for end in range(len(s), 0, -1):
        try:
            return float(s[:end])
        except ValueError:
            continue
    return 0.0


def _resume_value(args, i):
    """-r/--resume takes the next arg only when it looks like a session reference."""
    if i + 1 < len(args):
        nxt = args[i + 1]
        if nxt[:1] != "-" and (nxt.startswith("sess_") or nxt == "last" or ".json" in nxt):
            return nxt
    return None


def is_image_file(path):
    return os.path.splitext(path)[1].lower() in IMAGE_EXTS and os.path.exists(path)


@dataclass
class Options:
    """Per-request sampling fields (ai.c statics read when each payload is built)."""
    temperature: float = -1.0
    top_p: float = -1.0
    top_k: int = -1
    min_p: float = -1.0
    max_tokens: int = 32768
    step_max_tokens: int = 32768      # per-step completion cap (INFER_STEP_MAX_TOKENS), see sent_max_tokens
    repetition_guard: bool = True     # stream repetition detector (INFER_REPETITION_GUARD=0 disables)
    frequency_penalty: float = 0.10
    presence_penalty: float = 0.05
    reasoning_effort: str = None
    reasoning_budget: int = -1
    preserve_thinking: bool = False
    keep_reasoning: int = 2           # INFER_KEEP_REASONING: reasoning kept on the last N assistant msgs; -1 all
    keep_reasoning_block: int = 4     # INFER_KEEP_REASONING_BLOCK: hysteresis block (aitools.sessions.stripped_in_turn)
    tool_choice: str = "required"

    def sent_max_tokens(self):
        """max_tokens put on the wire: the smaller positive of max_tokens and the step cap (ai.c
        does the same); 0 when both are off."""
        caps = [v for v in (self.max_tokens, self.step_max_tokens) if v > 0]
        return min(caps) if caps else 0

    def apply_preset(self, name):
        """apply_mode_preset(): True when `name` is a known preset."""
        n = (name or "").lower()
        if n == "xhigh":
            t, p, k, mp, pres, eff = 1.0, 0.95, 20, 0.0, 0.0, "xhigh"
        elif n in ("normal", "medium"):
            t, p, k, mp, pres, eff = 1.0, 0.95, 20, 0.0, 0.0, "medium"
        elif n == "low":
            t, p, k, mp, pres, eff = 1.0, 0.95, 20, 0.0, 0.0, "low"
        elif n in ("instruct", "chat"):
            t, p, k, mp, pres, eff = 0.7, 0.80, 20, 0.0, 1.5, "none"
        else:
            return False
        self.reasoning_effort, self.temperature, self.top_p = eff, t, p
        self.top_k, self.min_p, self.presence_penalty = k, mp, pres
        return True


@dataclass
class Budget:
    """trim/stub thresholds and the per-tool output cap (JSON chars), see apply_context_budget."""
    trim: int = 100000
    stub: int = 250000
    max_tool: int = 65536
    trim_explicit: bool = False
    stub_explicit: bool = False
    max_tool_explicit: bool = False

    TOOL_FLOOR, TOOL_CEIL = 4000, 262144

    def apply(self, context_window, tools_len, max_tokens=32768):
        """Derive all three limits from a known context window, in both directions (tighter than
        the defaults on a small window, looser on a long one); explicit values win and an unknown
        window keeps the defaults. Same arithmetic as ai.c apply_context_budget.
        Returns a warning line when the tool catalog alone uses over half the window, else None."""
        if context_window <= 0:
            return None
        ctx_chars = context_window * 3
        reserve = min(context_window // 4, max(8192, max_tokens)) * 3      # reply reserve
        budget = max(ctx_chars - tools_len - reserve, 8000)
        if not self.trim_explicit:
            self.trim = budget * 8 // 10
        if not self.stub_explicit:
            self.stub = budget * 95 // 100
        if not self.max_tool_explicit:
            self.max_tool = min(max(budget // 4, self.TOOL_FLOOR), self.TOOL_CEIL)
        if tools_len > ctx_chars // 2:
            return ("\033[33m[ai] warning: the tool catalog (%d KB) uses over half of the %d-token context "
                    "window; use --short-ctx or INFER_TOOL_PROFILE=core.\033[0m\n" % (tools_len // 1024, context_window))
        return None


@dataclass
class Config:
    argv: list = field(default_factory=list)
    model: str = ""
    cmd_file: str = None
    interactive: bool = False
    permission_mode: int = 0          # 0 auto, 1 plan, 2 manual
    continue_until_done: bool = False
    quiet: bool = False
    no_tools: bool = False
    dry_run: bool = False
    git_enabled: bool = False
    git_explicit: bool = False
    notify: bool = False
    agents_enabled: bool = True
    session_file: str = None
    background: bool = False
    commit_msg: str = None
    copy: bool = True
    private: bool = False
    short_ctx: bool = False
    hide_details: bool = False
    raw_output: bool = False
    resume: str = None                # None: no resume; "": last session; else id / path
    goal: str = None
    mode_preset: str = None
    context_window: int = 0           # INFER_CONTEXT_WINDOW (explicit), else detected
    ctx_size_hint: int = 0            # LLAMA_CTX_SIZE: server total --ctx-size, an upper bound only
    task_timeout: int = 1800
    image_path: str = None
    prompt: str = ""
    opts: Options = field(default_factory=Options)
    budget: Budget = field(default_factory=Budget)
    env_updates: dict = field(default_factory=dict)   # name -> value, or None to unset
    preset_ok: bool = None


def parse(argv, environ=None, stdin_tty=False):
    """Resolve argv (without the program name) + environment like ai.c main()."""
    env = dict(os.environ if environ is None else environ)
    args = list(argv)
    c = Config(argv=args)
    upd = c.env_updates

    def getenv(k):
        return upd[k] if k in upd else env.get(k)

    # first pass: -m / -f / -n
    i = 0
    cmd_model = None
    while i < len(args):
        a = args[i]
        if a in ("-m", "--model"):
            if i + 1 < len(args):
                cmd_model = args[i + 1]
                i += 1
        elif a in ("-f", "--file"):
            if i + 1 < len(args):
                c.cmd_file = args[i + 1]
                i += 1
        elif a in ("-n", "--no-tools"):
            c.no_tools = True
        i += 1
    c.model = cmd_model if cmd_model else (env.get("INFER_MODEL") or "")

    # detailed pass
    i = 0
    while i < len(args):
        a = args[i]
        has_next = i + 1 < len(args)
        if a in ("-i", "--interactive"):
            c.interactive = True
        elif a in ("-y", "--yes", "-a", "--auto-approve", "--auto"):
            c.permission_mode = 0
        elif a == "--plan":
            c.permission_mode = 1
        elif a == "--manual":
            c.permission_mode = 2
        elif a in ("-c", "--continue"):
            c.continue_until_done = True
        elif a in ("-q", "--quiet"):
            c.quiet = True
        elif a in ("-n", "--no-tools"):
            c.no_tools = True
        elif a == "--dry-run":
            c.dry_run = True
        elif a == "--no-git":
            c.git_enabled, c.git_explicit = False, True
        elif a == "--git":
            c.git_enabled, c.git_explicit = True, True
        elif a == "--notify":
            c.notify = True
        elif a == "--no-notifications":
            c.notify = False
        elif a == "--no-agents":
            c.agents_enabled = False
        elif a == "--session" and has_next:
            c.session_file = args[i + 1]
            i += 1
        elif a in ("--bg", "--background"):
            c.background = True
        elif a == "--commit-msg" and has_next:
            c.commit_msg = args[i + 1]
            i += 1
        elif a == "--no-copy":
            c.copy = False
        elif a in ("--private", "--incognito", "-P"):
            c.private, c.git_enabled = True, False
            upd["INFER_PRIVATE_MODE"] = "1"
        elif a in ("--short-ctx", "--short-context"):
            c.short_ctx = True
            c.budget.trim, c.budget.max_tool = 16000, 4000
            upd["INFER_SHORT_CTX"] = "1"
            upd["INFER_SESSION_RECAP"] = "0"
            if has_next and args[i + 1][:1].isdigit():
                c.budget.trim, c.budget.trim_explicit = _atoi(args[i + 1]), True
                i += 1
        elif a == "--trim-threshold" and has_next:
            c.budget.trim, c.budget.trim_explicit = _atoi(args[i + 1]), True
            i += 1
        elif a == "--tokenizer" and has_next:
            upd["INFER_TOKENIZER"] = args[i + 1]
            i += 1
        elif a in ("--hide-details", "--collapsed"):
            c.hide_details = True
        elif a in ("--expanded", "--details"):
            c.hide_details = False
        elif a in ("--raw", "--raw-output"):
            upd["INFER_RAW_OUTPUT"] = "1"
        elif a in ("-r", "--resume"):
            v = _resume_value(args, i)
            if v is not None:
                c.resume = v
                i += 1
            else:
                c.resume = ""
        elif a in ("-g", "--goal") and has_next:
            c.goal = args[i + 1]
            i += 1
        elif a in ("-t", "--temperature") and has_next:
            c.opts.temperature = _atof(args[i + 1])
            i += 1
        elif a in ("-p", "--top-p") and has_next:
            c.opts.top_p = _atof(args[i + 1])
            i += 1
        elif a in ("-k", "--top-k") and has_next:
            c.opts.top_k = _atoi(args[i + 1])
            i += 1
        elif a == "--min-p" and has_next:
            c.opts.min_p = _atof(args[i + 1])
            i += 1
        elif a in ("--reasoning", "--reasoning-effort") and has_next:
            c.opts.reasoning_effort = args[i + 1]
            i += 1
        elif a in ("--budget", "--reasoning-budget") and has_next:
            c.opts.reasoning_budget = _atoi(args[i + 1])
            i += 1
        elif a == "--preserve-thinking":
            c.opts.preserve_thinking = True
        elif a == "--mode" and has_next:
            c.mode_preset = args[i + 1]
            i += 1
        elif a in ("-m", "--model", "-f", "--file") and has_next:
            i += 1
        i += 1

    if _truthy(getenv("INFER_HIDE_DETAILS")):
        c.hide_details = True
    if _truthy(getenv("INFER_AUTO_APPROVE")):
        c.permission_mode = 0
    perm = (getenv("INFER_PERMISSION_MODE") or "").lower()
    if perm:
        c.permission_mode = {"plan": 1, "manual": 2, "auto": 0}.get(perm, c.permission_mode)
    if _truthy(getenv("INFER_QUIET")):
        c.quiet = True
    r = getenv("INFER_RESUME")
    if _truthy(r):
        if c.resume is None:
            c.resume = ""
    elif r:
        if c.resume is None:
            c.resume = r
    if _truthy(getenv("INFER_CONTINUE")):
        c.continue_until_done = True
    upd["INFER_AUTO_APPROVE"] = "1" if c.permission_mode == 0 else None
    upd["INFER_QUIET"] = "1" if c.quiet else None
    upd["INFER_CONTINUE"] = "1" if c.continue_until_done else None

    o = c.opts
    if getenv("INFER_TEMPERATURE"):
        o.temperature = _atof(getenv("INFER_TEMPERATURE"))
    if getenv("INFER_TOP_P"):
        o.top_p = _atof(getenv("INFER_TOP_P"))
    if getenv("INFER_TOP_K"):
        o.top_k = _atoi(getenv("INFER_TOP_K"))
    if getenv("INFER_MIN_P"):
        o.min_p = _atof(getenv("INFER_MIN_P"))
    if getenv("INFER_REASONING_EFFORT") and not o.reasoning_effort:
        o.reasoning_effort = getenv("INFER_REASONING_EFFORT")
    if getenv("INFER_REASONING_BUDGET") and o.reasoning_budget < 0:
        o.reasoning_budget = _atoi(getenv("INFER_REASONING_BUDGET"))
    if _truthy(getenv("INFER_PRESERVE_THINKING")):
        o.preserve_thinking = True
    from aitools.sessions import keep_block_from_env, keep_reasoning_from_env
    o.keep_reasoning = keep_reasoning_from_env(o.preserve_thinking,
                                               {"INFER_KEEP_REASONING": getenv("INFER_KEEP_REASONING")})
    o.keep_reasoning_block = keep_block_from_env({"INFER_KEEP_REASONING_BLOCK": getenv("INFER_KEEP_REASONING_BLOCK")})
    if getenv("INFER_MAX_TOKENS"):
        o.max_tokens = _atoi(getenv("INFER_MAX_TOKENS"))
        o.step_max_tokens = o.max_tokens      # an explicit completion budget wins over the default cap
    if getenv("INFER_STEP_MAX_TOKENS"):
        o.step_max_tokens = _atoi(getenv("INFER_STEP_MAX_TOKENS"))
    if getenv("INFER_REPETITION_GUARD") in ("0", "false", "off"):
        o.repetition_guard = False
    # Penalties: the client value always goes on the wire (> 0) and overrides the server's own
    # --presence-penalty, so without an INFER_* value a non-neutral server-profile value
    # (LLAMA_*_PENALTY from ai-backend) is sent instead of the client default.
    for attr, mine, server in (("frequency_penalty", "INFER_FREQ_PENALTY", "LLAMA_FREQUENCY_PENALTY"),
                               ("presence_penalty", "INFER_PRESENCE_PENALTY", "LLAMA_PRESENCE_PENALTY")):
        if getenv(mine):
            setattr(o, attr, _atof(getenv(mine)))
        elif getenv(server) and _atof(getenv(server)) > 0.0:
            setattr(o, attr, _atof(getenv(server)))
    if c.mode_preset:
        c.preset_ok = o.apply_preset(c.mode_preset)

    # INFER_CONTEXT_WINDOW is an explicit override; LLAMA_CTX_SIZE (the server's TOTAL --ctx-size,
    # split across --parallel slots, maybe stale) only caps the per-slot n_ctx detected from /props.
    if getenv("INFER_CONTEXT_WINDOW"):
        c.context_window = _atoi(getenv("INFER_CONTEXT_WINDOW"))
    if getenv("LLAMA_CTX_SIZE"):
        c.ctx_size_hint = _atoi(getenv("LLAMA_CTX_SIZE"))
    if getenv("INFER_TASK_TIMEOUT"):
        c.task_timeout = _atoi(getenv("INFER_TASK_TIMEOUT"))
    b = c.budget
    if getenv("INFER_MAX_TOOL_OUTPUT"):
        b.max_tool, b.max_tool_explicit = _atoi(getenv("INFER_MAX_TOOL_OUTPUT")), True
    if getenv("INFER_TRIM_THRESHOLD"):
        b.trim, b.trim_explicit = _atoi(getenv("INFER_TRIM_THRESHOLD")), True
    g = getenv("INFER_AUTO_COMMIT")
    if g is not None and not c.git_explicit:
        c.git_enabled = _truthy(g)
    if getenv("INFER_STUB_THRESHOLD"):
        b.stub, b.stub_explicit = _atoi(getenv("INFER_STUB_THRESHOLD")), True
    if _truthy(getenv("INFER_PRIVATE_MODE")):
        c.private, c.git_enabled = True, False
    if _truthy(getenv("INFER_SHORT_CTX")):
        c.short_ctx = True
        if not b.trim_explicit:
            b.trim = 16000
        if not b.max_tool_explicit:
            b.max_tool = 4000
        b.trim_explicit = b.max_tool_explicit = True
        upd["INFER_SESSION_RECAP"] = "0"
        if getenv("INFER_TOOL_PROFILE") is None:
            upd["INFER_TOOL_PROFILE"] = "core"
    tc = getenv("INFER_TOOL_CHOICE")
    if tc in ("auto", "required"):
        o.tool_choice = tc
    c.raw_output = getenv("INFER_RAW_OUTPUT") is not None

    if len(args) < 1 and stdin_tty:
        c.interactive = True

    c.image_path = c.cmd_file if (c.cmd_file and is_image_file(c.cmd_file)) else None
    if not c.image_path:
        c.image_path = _find_image_arg(args)
    c.prompt = _join_prompt(args, c.image_path)
    return c


def _find_image_arg(args):
    i = 0
    while i < len(args):
        a = args[i]
        if a in _PROMPT_SKIP or a in ("-r", "--resume"):
            i += 1
            continue
        if a in _PROMPT_SKIP_VALUE and i + 1 < len(args):
            i += 2
            continue
        if is_image_file(a):
            return a
        i += 1
    return None


def _join_prompt(args, image_path):
    words = []
    i = 0
    while i < len(args):
        a = args[i]
        if a in _PROMPT_SKIP:
            i += 1
            continue
        if a in _PROMPT_SKIP_VALUE and i + 1 < len(args):
            i += 2
            continue
        if a in ("-r", "--resume"):
            i += 2 if _resume_value(args, i) is not None else 1
            continue
        if image_path and a == image_path:
            i += 1
            continue
        words.append(a)
        i += 1
    return " ".join(words)


def interactive_fallback_ok(args):
    """ai.c: when -i cannot reach /dev/tty, a one-shot run still happens if any real prompt arg exists."""
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-i", "--interactive", "-y", "--yes", "-c", "--continue", "--plan", "--manual", "--auto",
                 "-q", "--quiet", "-n", "--no-tools", "-r", "--resume", "-v", "--version", "--preserve-thinking"):
            i += 1
            continue
        if a in ("-m", "--model", "-t", "--temperature", "-p", "--top-p", "-k", "--top-k", "--min-p",
                 "--reasoning", "--reasoning-effort", "-f", "--file") and i + 1 < len(args):
            i += 2
            continue
        return True
    return False
