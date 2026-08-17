#!/usr/bin/env python3
"""Second pass: fix the five residual env triples (bare two-arg form)."""
import re, sys

PATH = "/home/dzyla/ai-buddy/ai-backend"

REPLS = [
    (1023, 'ctx_override = env_vars.get("LLAMA_CTX_SIZE", os.environ.get("LLAMA_CTX_SIZE"))',
             'ctx_override = get_cfg(env_vars, "LLAMA_CTX_SIZE")'),
    (1030, 'mtp_setting = env_vars.get("LLAMA_MTP", os.environ.get("LLAMA_MTP"))',
           'mtp_setting = get_cfg(env_vars, "LLAMA_MTP")'),
    (1045, 'legacy_draft = env_vars.get("LLAMA_DRAFT_MODEL_PATH", os.environ.get("LLAMA_DRAFT_MODEL_PATH"))',
           'legacy_draft = get_cfg(env_vars, "LLAMA_DRAFT_MODEL_PATH")'),
    (1144, 'n_glx = env_vars.get("LLAMA_N_GPU_LAYERS", os.environ.get("LLAMA_N_GPU_LAYERS"))',
           'n_glx = get_cfg(env_vars, "LLAMA_N_GPU_LAYERS")'),
    (1166, 'tensor_split = env_vars.get("LLAMA_TENSOR_SPLIT", os.environ.get("LLAMA_TENSOR_SPLIT"))',
           'tensor_split = get_cfg(env_vars, "LLAMA_TENSOR_SPLIT")'),
]

s = open(PATH).read().splitlines(keepends=True)

if not any(l.startswith("def get_cfg(") for l in s):
    sys.exit("get_cfg helper missing — first run envscan.py")

for ln, old, new in REPLS:
    line = s[ln - 1]
    if old not in line:
        sys.exit(f"MISMATCH at line {ln}:\n{line}")
    s[ln - 1] = line.replace(old, new, 1)

open(PATH, "w").write("".join(s))

v = open(PATH).read()
pat = re.compile(r'env_vars\.get\("(\w+)",\s*os\.environ\.get\("(\w+)"')
residue = pat.findall(v)
if residue:
    sys.exit(f"triple residue: {residue}")
if "def get_cfg(" not in v:
    sys.exit("helper missing after rewrite")
print("done; get_cfg calls:", v.count("get_cfg("))
