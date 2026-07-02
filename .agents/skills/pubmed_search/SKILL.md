---
name: pubmed_search
description: CRITICAL — when searching biomedical literature, scientific papers, or using PubMed: Guidelines for multi-round semantic search and chronological synthesis.
---

# PubMed Literature Research Skill

## Core rule

**Always use `pubmed_research_round`, never `pubmed_search` directly.**

`pubmed_research_round` fetches full abstracts from the API, reads them in Python, and returns only the key opening + closing sentences per paper. Your context stays clean. Each call takes ~1.5 s.

---

## Research loop

```
Round 1: call pubmed_research_round(query="<broad descriptive sentence>")
         read digest → collect DOIs, spot themes and gaps
Round 2: call pubmed_research_round(query="<gap-filling sentence>", known_dois=[...])
         read digest → add new DOIs, update gaps
Round 3: call pubmed_research_round(query="<deeper angle>", known_dois=[...all so far...])
         if <3 new papers → saturation reached → synthesise
```

Run 2 queries in parallel on round 1 if you have two clear angles. After 3–4 rounds (~30–40 unique papers) write the final review.

---

## Writing queries — semantic embedding search

Longer descriptive sentences find far better results than short keywords.

**Rule**: write the query as *"what would the opening sentence of the ideal abstract say?"*

| Bad | Good |
|-----|------|
| `uromodulin kidney` | `"Uromodulin (Tamm-Horsfall protein) is secreted exclusively by thick ascending limb epithelial cells, where it polymerises into filaments that protect against urinary tract infection and stone formation"` |
| `KRAS resistance` | `"Acquired resistance to KRAS G12C covalent inhibitors sotorasib and adagrasib arises through secondary KRAS mutations and bypass RTK signalling that reactivates the MAPK pathway"` |

- Minimum ~60 chars, optimal 80–300 chars
- Each round must target a **different conceptual angle**
- No MeSH syntax or boolean operators

---

## Saturation — stop when:
- `known_dois` overlap ≥ 60 % of new results, OR
- fewer than 3 new papers in a round, OR
- 4 rounds completed

---

## Final synthesis

The digest returns papers **sorted oldest → newest**. Use that order to build a chronological narrative in the synthesis — show how the field evolved, what early work established, what later work overturned or refined, and where it stands today.

```markdown
## Literature Review: <Topic>

**Coverage**: <N> unique papers | <M> rounds | PubMed / BioRxiv / MedRxiv / arXiv | <oldest year>–<newest year>

### Background
<Why this topic matters; what the pre-literature state of knowledge was>

### Historical Development
Trace the arc of the field chronologically, using paper years to anchor the narrative.
Typical structure:
- **Early work (before ~2010)**: what foundational discoveries established
- **Middle period (~2010–2018)**: key technological or conceptual shifts, what changed and why
- **Recent advances (2019–present)**: what the latest papers add, what was overturned or refined

Use inline citations: [Author et al., YEAR].

### Key Themes
#### <Theme 1>
<Synthesis — note how thinking on this theme evolved over time>

#### <Theme 2>
...

### Points of Controversy or Uncertainty
<Where papers disagree; methodological debates; unresolved questions>

### Open Questions and Future Directions

### References (chronological)
| Year | Title | Authors | Journal | Source | DOI | Link |
|------|-------|---------|---------|--------|-----|------|
| 2005 | ... | ... | ... | PubMed | 10.x/y | [→](https://doi.org/10.x/y) |
| 2011 | ... | ... | ... | PubMed | 10.x/y | [→](https://doi.org/10.x/y) |
```

---

## Example — uromodulin

```python
# Round 1a — mechanism/biology
pubmed_research_round(
  query="Uromodulin also known as Tamm-Horsfall protein is exclusively expressed by thick ascending limb epithelial cells and polymerises into gel-forming filaments that protect the kidney against ascending urinary tract infections and calcium oxalate stone nucleation"
)

# Round 1b — clinical/biomarker (parallel with 1a)
pubmed_research_round(
  query="Urinary and serum uromodulin as biomarkers of renal tubular mass and function in patients with chronic kidney disease and their association with eGFR decline cardiovascular outcomes and mortality"
)

# Round 2 — gap: UMOD gene mutations (collect DOIs from round 1 first)
pubmed_research_round(
  query="Autosomal dominant mutations in the UMOD gene cause misfolding and ER retention of uromodulin leading to tubulointerstitial nephropathy with hyperuricemia and progressive renal failure",
  known_dois=["10.1038/ki.2011.134", "10.1097/MNH.0000000000000885", ...]
)

# Round 3 — gap: GWAS common variants
pubmed_research_round(
  query="Common variants near the UMOD locus identified by genome-wide association studies modulate urinary uromodulin excretion and associate with chronic kidney disease risk blood pressure and gout in population cohorts",
  known_dois=[...all collected...],
  start_date="2015-01-01"
)
# → synthesise
```
