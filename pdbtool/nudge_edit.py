import re
p = "/home/dzyla/Code/ai-buddy/ai.c"
s = open(p, encoding="latin-1").read()

# Strengthen the "response cut off by token limit" nudge so a small model that just
# burned its budget on a giant think (the observed baseline failure) is steered to a
# SHORT think + a concrete ACTION, instead of looping into more giant thoughts.
old = 'by the token limit. Call task_complete now with your current '
assert s.count(old) == 1, "old nudge count=%d" % s.count(old)
new = ('by the token limit. This is almost always because you wrote an over-long '
       "'think' block or dumped too much into one response, which stalls small local "
       'models and stops them from ever reaching the deliverable. Do NOT write another '
       'long think. Next: keep \'think\' to a MAXIMUM of 3 short sentences, then make ONE '
       'concrete ACTION tool call (write_file to create your code file, or execute_command '
       'to build/run). If you have already completed the deliverable, call task_complete '
       'with your summary instead.')
s = s.replace(old, new, 1)

out = p + ".new"
open(out, "w", encoding="latin-1").write(s)
print("truncation nudge replaced; bytes ->", out, len(s))
