import re
p = "/home/dzyla/Code/ai-buddy/ai.c"
s = open(p, encoding="latin-1").read()

# Anchor on the END of the PLAN phase line (escaped \n + closing quote) and insert a
# complete new adjacent C string literal after it -> REUSE BEFORE WRITING discipline.
old = "Identify what could go wrong BEFORE acting.\\n\""
assert s.count(old) == 1, "plan anchor=%d" % s.count(old)
new = (old + '\n'
       '    "  - REUSE BEFORE WRITING: do NOT reinvent the wheel. Before implementing any nontrivial '
       'component (a file-format parser, an algorithm, a library routine), SEARCH for an existing, '
       'maintained library first - `pkg.go.dev` / GitHub / PyPI / crates.io - and build AROUND it '
       '(e.g. `go get`, `pip install`). Hand-write only what no good package covers. '
       'Name the package you reused and its import path in your final summary.\\n"')
s = s.replace(old, new, 1)

out = p + ".new"
open(out, "w", encoding="latin-1").write(s)
print("REUSE BEFORE WRITING added; bytes ->", out, len(s))
