import re
p = "/home/dzyla/Code/ai-buddy/ai.c"
s = open(p, encoding="latin-1").read()

# anchor on the end of the EXECUTION phase line (escaped \n + closing quote of the
# string literal), and insert a complete new adjacent C string literal after it.
old = 'never retry the identical failing call.\\n"'
assert s.count(old) == 1, "anchor count=%d" % s.count(old)
new = (old + '\n'
       '    "  - THINK DISCIPLINE: keep every `think` to a MAXIMUM of 2-3 short sentences. '
       'NEVER write your code, plan, or analysis in full inside `think` - that wastes the whole '
       'output budget and stalls the task. Write code to a file with write_file, then build/run it '
       'with execute_command. A task with zero write_file/execute_command calls after a few turns '
       'is failing - stop thinking and ACT.\\n"')
s = s.replace(old, new, 1)

out = p + ".new"
open(out, "w", encoding="latin-1").write(s)
print("prompt directive added; bytes ->", out, len(s))
