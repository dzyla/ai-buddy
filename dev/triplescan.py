#!/usr/bin/env python3
import re
src = open("ai-backend").read().splitlines()
pat = re.compile(r'env_vars\.get\("(\w+)",\s*os\.environ\.get\("(\w+)"')
n = 0
for i, l in enumerate(src, 1):
    m = pat.search(l)
    if m:
        n += 1
        print(i, ":", l.strip()[:110])
print("total:", n)
