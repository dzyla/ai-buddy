import re
p = "/home/dzyla/Code/ai-buddy/ai.c"
s = open(p, encoding="latin-1").read()
edits = []

# FIX A: raise default max_tokens
old = "static int   max_tokens_val         = 8192;  /* Prevent infinite reasoning loops */"
assert s.count(old) == 1, "maxtok anchor=%d" % s.count(old)
new = ("static int   max_tokens_val         = 32768; /* Completion budget. 8192 is too small for 35B "
       "models that emit long native reasoning_content: it truncates mid-reasoning before the "
       "model ever emits its action tool call, so tasks end with zero artifacts. 32768 lets "
       "reasoning + the tool call both fit. Override via INFER_MAX_TOKENS. */")
s = s.replace(old, new, 1)
edits.append("FIX A max_tokens 8192->32768: ok")

# FIX B: rewrite the truncated-response nudge as a single clean statement.
start = s.find('Your last response was cut off ')
assert start != -1, "nudge start not found"
openx = s.rfind('messages_json = append_message(messages_json,', 0, start)
assert openx != -1, "append open not found before nudge"
end = s.find('has_more = 1;', start)
assert end != -1, "has_more not found after nudge"
clean = ('Your last response was cut off by the token limit. This means you wrote an over-long '
         'chain of thought and consumed the whole output budget WITHOUT emitting a tool call - '
         'reasoning alone never produces an artifact, and that is exactly why you are stuck. '
         'NEXT TIME: write at most 2-3 short sentences of reasoning, then IMMEDIATELY emit ONE '
         'action tool call (write_file to create your code file, or execute_command to build/run). '
         'Do NOT call task_complete until the deliverable actually exists. Repeat: short reasoning '
         'then one action tool call, right now.')
stmt = ('messages_json = append_message(messages_json,\n'
        '    "{\\"role\\":\\"user\\",\\"content\\":\\"' + clean + '\\"}");\n'
        '                                                    ')
s = s[:openx] + stmt + s[end:]
edits.append("FIX B truncation nudge rewritten: ok")

out = p + ".new"
open(out, "w", encoding="latin-1").write(s)
print("\n".join(edits))
print("bytes ->", out, len(s))
