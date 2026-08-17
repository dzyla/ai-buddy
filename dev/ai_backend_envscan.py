import re, sys

PATH = "/home/dzyla/ai-buddy/ai-backend"
s = open(PATH).read().splitlines(keepends=True)

# --- 1. get_cfg helper (insert before calculate_auto_ctx) ---
HELPER = '''def get_cfg(env, key, default=None):
    """Resolve a config value: env file wins, then process environment, then default.

    Empty values in the env file are treated as unset.
    """
    v = env.get(key)
    if not v:
        v = os.environ.get(key)
    return v if v is not None else default


'''
anchor = 'def calculate_auto_ctx(model_path, vram_free=None'
if not any(l.startswith(anchor) for l in s):
    sys.exit("anchor not found")
idx = next(i for i, l in enumerate(s) if l.startswith(anchor))
s[idx:idx] = [h + "\n" for h in HELPER.splitlines()]

# --- 2. Rule A: all triple patterns ---
triple = re.compile(r'env_vars\.get\("(\w+)",\s*os\.environ\.get\("(\w+)",\s*([^)]*)\)\)')
def rule_a(line):
    def sub(m):
        return f'get_cfg(env_vars, "{m.group(1)}", {m.group(3)})'
    return re.sub(triple, sub, line)

def rule_a_special(line):
    return line.replace(
        'env_vars.get("LLAMA_CTX_SIZE_MAX", os.environ.get("LLAMA_CTX_SIZE_MAX", str(default_max)))',
        'get_cfg(env_vars, "LLAMA_CTX_SIZE_MAX", str(default_max))')

# --- 3. explicit two-level conversions ---
REPLS = [
  (951, 'env_vars.get("INFER_BASE_URL", f"http://localhost:{PORT}/v1/")', 'get_cfg(env_vars, "INFER_BASE_URL", f"http://localhost:{PORT}/v1/")'),
  (2349, 'env_vars.get("INFER_BASE_URL", f"http://localhost:{PORT}/v1/")', 'get_cfg(env_vars, "INFER_BASE_URL", f"http://localhost:{PORT}/v1/")'),
  (952, 'env_vars.get("INFER_MODEL", "llama")', 'get_cfg(env_vars, "INFER_MODEL", "llama")'),
  (2350, 'env_vars.get("INFER_MODEL", "llama")', 'get_cfg(env_vars, "INFER_MODEL", "llama")'),
  (953, 'env_vars.get("LLAMA_MODEL_PATH")', 'get_cfg(env_vars, "LLAMA_MODEL_PATH")'),
  (2351, 'env_vars.get("LLAMA_MODEL_PATH")', 'get_cfg(env_vars, "LLAMA_MODEL_PATH")'),
  (1376, 'env_vars.get("INFER_BASE_URL", "")', 'get_cfg(env_vars, "INFER_BASE_URL", "")'),
  (1432, "env_vars.get('LLAMA_N_GPU_LAYERS', '99')", 'get_cfg(env_vars, "LLAMA_N_GPU_LAYERS", "99")'),
  (1438, 'env_vars.get("LLAMA_CTX_SIZE", "auto")', 'get_cfg(env_vars, "LLAMA_CTX_SIZE", "auto")'),
  (1443, 'env_vars.get("LLAMA_BATCH_SIZE", "4096")', 'get_cfg(env_vars, "LLAMA_BATCH_SIZE", "4096")'),
  (1444, 'env_vars.get("LLAMA_UBATCH_SIZE", "2048")', 'get_cfg(env_vars, "LLAMA_UBATCH_SIZE", "2048")'),
  (1452, 'env_vars.get("LLAMA_MTP", "off")', 'get_cfg(env_vars, "LLAMA_MTP", "off")'),
  (1453, 'env_vars.get("LLAMA_SPEC_TYPE", "draft-mtp")', 'get_cfg(env_vars, "LLAMA_SPEC_TYPE", "draft-mtp")'),
  (1454, 'env_vars.get("LLAMA_SPEC_DRAFT_N_MAX", "1")', 'get_cfg(env_vars, "LLAMA_SPEC_DRAFT_N_MAX", "1")'),
  (1477, 'env_vars.get("INFER_TEMPERATURE", "1.0")', 'get_cfg(env_vars, "INFER_TEMPERATURE", "1.0")'),
  (1478, 'env_vars.get("INFER_TOP_P", "0.95")', 'get_cfg(env_vars, "INFER_TOP_P", "0.95")'),
  (1479, 'env_vars.get("INFER_TOP_K", "20")', 'get_cfg(env_vars, "INFER_TOP_K", "20")'),
  (1480, 'env_vars.get("INFER_MIN_P", "0.0")', 'get_cfg(env_vars, "INFER_MIN_P", "0.0")'),
  (1481, 'env_vars.get("INFER_REASONING_EFFORT", "xhigh")', 'get_cfg(env_vars, "INFER_REASONING_EFFORT", "xhigh")'),
  (1482, 'env_vars.get("LLAMA_REPEAT_PENALTY", "1.0")', 'get_cfg(env_vars, "LLAMA_REPEAT_PENALTY", "1.0")'),
  (1483, 'env_vars.get("LLAMA_PRESENCE_PENALTY", "0.0")', 'get_cfg(env_vars, "LLAMA_PRESENCE_PENALTY", "0.0")'),
  (1484, 'env_vars.get("LLAMA_FREQUENCY_PENALTY", "0.0")', 'get_cfg(env_vars, "LLAMA_FREQUENCY_PENALTY", "0.0")'),
  (1492, 'env_vars.get("LLAMA_ROPE_SCALE")', 'get_cfg(env_vars, "LLAMA_ROPE_SCALE")'),
  (1494, 'env_vars.get("LLAMA_ROPE_SCALING", "yarn")', 'get_cfg(env_vars, "LLAMA_ROPE_SCALING", "yarn")'),
  (1495, 'env_vars.get("LLAMA_YARN_ORIG_CTX", "262144")', 'get_cfg(env_vars, "LLAMA_YARN_ORIG_CTX", "262144")'),
  (2208, 'env_vars.get("LLAMA_CACHE_TYPE_K", "q8_0")', 'get_cfg(env_vars, "LLAMA_CACHE_TYPE_K", "q8_0")'),
  (2209, 'env_vars.get("LLAMA_CACHE_TYPE_V", "q8_0")', 'get_cfg(env_vars, "LLAMA_CACHE_TYPE_V", "q8_0")'),
  (2273, 'env_vars.get("CUDA_VISIBLE_DEVICES")', 'get_cfg(env_vars, "CUDA_VISIBLE_DEVICES")'),
  (2274, 'env_vars.get("LLAMA_TENSOR_SPLIT")', 'get_cfg(env_vars, "LLAMA_TENSOR_SPLIT")'),
]

# NOTE: after step 1 (helper insertion) line numbers shift by +6 (4 lines? helper is 9 lines incl blank).
# Helper block inserted is 9 lines (def + docstring 3 lines + code 4 + blank). Compute shift:
shift = len(HELPER.splitlines())
# anchor is at line 299 -> after insertion, old lines from 299+ have +shift offset... actually anchor itself becomes at idx (old idx) -> everything after anchor shifts by len(HELPER.splitlines())
shift = len(HELPER.splitlines())
old_lines_fixed = False
# The REPLS line numbers are from BEFORE the insertion (they were scanned pre-insertion).
# But the scanner ran on the file BEFORE today's patches? No - the sed scan was post-patch-1451/1140/etc. Line numbers 951 etc are current-file lines pre-helper-insertion. Post-insertion they shift by +shift for lines after the anchor (line ~299... anchor is at 299? calculate_auto_ctx is at ~line 299? earlier reads: 'def calculate_auto_ctx' at ~line 301). All REPL targets are >= 951 > anchor, so they shift by +shift.

for lineno, old, new in REPLS:
    ln = lineno + shift  # helper inserted before all of them
    line = s[ln-1]
    if old not in line:
        sys.exit(f"MISMATCH at shifted line {ln} ({lineno}): {line!r}")
    s[ln-1] = line.replace(old, new)

out = []
for line in s:
    line = rule_a(line)
    line = rule_a_special(line)
    out.append(line)

open(PATH, "w").write("".join(out))

# verify
v = open(PATH).read()
residue = re.findall(r'env_vars\.get\("(\w+)",\s*os\.environ\.get\("(\w+)"', v)
if residue:
    sys.exit(f"triple residue: {residue}")
print("done; get_cfg calls:", v.count("get_cfg("))
