"""Deterministic synthetic fixtures for eval tasks (task field `generate`).

Large inputs (a 400 KB notebook, a 500-row CSV) are generated at setup time from a seed instead
of being embedded in the task JSON. Same spec -> byte-identical output on every machine.
"""
import random

_PEOPLE = ["kim", "okafor", "lindqvist", "tanaka", "moreau", "silva", "novak", "haddad"]
_ACTIONS = [
    "ran SDS-PAGE on fractions {a}-{b}; main band at the expected size, minor smear in lane {a}",
    "equilibrated the Superdex column in {n} mM HEPES pH 7.4 and loaded {a} mg of sample",
    "centrifuged the lysate at {n}000 x g for 30 min; pellet discarded, supernatant kept on ice",
    "measured A280 of pool {a}: {b}.{n} mg/mL, A260/A280 within the usual range",
    "set up {n} sitting-drop crystallisation plates, 1:1 ratio, reservoir {a} uL",
    "thawed aliquot {a} from the -80 box, spun 10 min, no visible precipitate",
    "cleaned the cold-room bench and restocked pipette tips (boxes of {n})",
    "calibrated the pH meter against the 4.00/7.00 buffers before preparing buffer lot {a}{b}",
    "grids screened on the Glacios: ice thickness acceptable on {n} of 12 squares",
    "ordered primers for construct {a}; expected delivery in {b} working days",
]


def lab_notebook(seed, chars, needles):
    """Notebook of one-line entries, ~`chars` characters. `needles` = [(depth 0..1, text), ...]
    are spliced in as entries at that depth. Returns (text, {needle text: 1-based line number})."""
    rnd = random.Random(seed)
    lines, size, i = [], 0, 0
    while size < chars:
        i += 1
        a, b, n = rnd.randint(2, 40), rnd.randint(2, 9), rnd.randint(2, 30)
        act = rnd.choice(_ACTIONS).format(a=a, b=b, n=n)
        line = "[2026-%02d-%02d %02d:%02d] entry %05d (%s) %s" % (
            rnd.randint(1, 9), rnd.randint(1, 28), rnd.randint(8, 19), rnd.randint(0, 59),
            i, rnd.choice(_PEOPLE), act)
        lines.append(line)
        size += len(line) + 1
    where = {}
    for depth, text in sorted(needles):
        pos = int(len(lines) * depth)
        lines.insert(pos, "[2026-09-30 09:00] entry %05d (%s) %s" % (90000 + pos, "admin", text))
        where[text] = pos + 1
    # insertion shifts earlier-computed positions for later needles; recompute honestly
    for text in where:
        where[text] = next(k for k, l in enumerate(lines, 1) if l.endswith(text))
    return "\n".join(lines) + "\n", where


def sales_rows(seed, n):
    rnd = random.Random(seed)
    rows = []
    for i in range(1, n + 1):
        rows.append((i, rnd.choice("ABCDE"), rnd.choice(["active", "active", "inactive"]),
                     "%d.%02d" % (rnd.randint(5, 900), rnd.randint(0, 99))))
    return rows


def sales_csv(seed, n):
    return "id,group,status,value\n" + "".join("%d,%s,%s,%s\n" % r for r in sales_rows(seed, n))


def generate(spec):
    """One `generate` entry -> (relative path, text)."""
    kind = spec["kind"]
    if kind == "lab_notebook":
        return spec["path"], lab_notebook(spec["seed"], spec["chars"],
                                          [(d, t) for d, t in spec["needles"]])[0]
    if kind == "sales_csv":
        return spec["path"], sales_csv(spec["seed"], spec["rows"])
    raise ValueError("unknown generator %r" % kind)
