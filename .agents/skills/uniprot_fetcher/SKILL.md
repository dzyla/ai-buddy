---
name: uniprot_fetcher
description: CRITICAL — when fetching protein data, UniProt details, or amino acid sequences: Guidelines for UniProt REST API, format selection, and JS bypass.
triggers:
  - uniprot
  - protein entry
  - protein sequence
  - protein function
  - uniprot API
  - protein database
---

# UniProt Data Fetching

UniProt's website is JavaScript-rendered and blocks plain HTTP scrapers. Use the strategies below in priority order.

## Strategy 1 — UniProt REST API (preferred, always works)

UniProt exposes a full REST API that returns JSON or plain text without any JS rendering.

### Search proteins

```
https://rest.uniprot.org/uniprotkb/search?query=<QUERY>&format=json&size=5
```

- `format`: `json`, `tsv`, `fasta`, `txt`, `xml`
- `fields`: comma-separated field names to include (e.g. `accession,gene_names,protein_name,organism_name,sequence`)
- `size`: number of results (default 25, max 500)

Example — find human TP53:
```
https://rest.uniprot.org/uniprotkb/search?query=tp53+AND+organism_id:9606&format=json&fields=accession,gene_names,protein_name,sequence&size=3
```

### Fetch a single entry by accession

```
https://rest.uniprot.org/uniprotkb/<ACCESSION>.json
https://rest.uniprot.org/uniprotkb/<ACCESSION>.fasta
https://rest.uniprot.org/uniprotkb/<ACCESSION>.txt       # full flat-file
```

Examples:
```
https://rest.uniprot.org/uniprotkb/P04637.json   # TP53 human
https://rest.uniprot.org/uniprotkb/P04637.fasta
```

### Key JSON fields in a UniProt entry

| Field path | Content |
|---|---|
| `primaryAccession` | Accession (e.g. P04637) |
| `uniProtkbId` | Entry name (e.g. P53_HUMAN) |
| `proteinDescription.recommendedName.fullName.value` | Full protein name |
| `genes[].geneName.value` | Gene name(s) |
| `organism.scientificName` | Species |
| `sequence.value` | Amino acid sequence |
| `sequence.length` | Length in AA |
| `comments` | Function, subcellular location, disease, etc. |
| `features` | Active sites, domains, PTMs, variants |
| `dbReferences` | Cross-refs to PDB, Pfam, GO, etc. |

### Fetch features (annotations)

```
https://rest.uniprot.org/uniprotkb/<ACCESSION>?fields=accession,ft_domain,ft_act_site,ft_binding,ft_mod_res,ft_variant&format=json
```

### Fetch cross-references (PDB structures, GO terms, etc.)

```
https://rest.uniprot.org/uniprotkb/<ACCESSION>?fields=accession,xref_pdb,xref_go,xref_pfam&format=json
```

## Strategy 2 — fetch_webpage_js (for browsing the web UI)

When you need to read the UniProt **web page** rather than structured API data:

```
fetch_webpage_js("https://www.uniprot.org/uniprotkb/P04637/entry", wait_for="networkidle")
```

Use `fetch_webpage_js` (not `fetch_webpage`) for all `uniprot.org` URLs — the site is a React SPA that returns blank content to plain HTTP requests.

## Strategy 3 — Help and API docs page

```
fetch_webpage_js("https://www.uniprot.org/help/api_queries", wait_for="networkidle")
```

## Common search query syntax

| Goal | Query |
|---|---|
| Protein by name | `insulin` |
| Reviewed only (Swiss-Prot) | `reviewed:true` |
| Human proteins | `organism_id:9606` |
| By gene name | `gene:brca1` |
| By disease | `cc_disease:cancer` |
| By PDB cross-ref | `database:pdb` |
| Combine | `gene:tp53 AND organism_id:9606 AND reviewed:true` |

## Implementation pattern

Always prefer the REST API over the web UI for structured data. Use `execute_command` with `curl` as an alternative:

```bash
curl -s "https://rest.uniprot.org/uniprotkb/P04637.json" | python3 -m json.tool | head -80
curl -s "https://rest.uniprot.org/uniprotkb/P04637.fasta"
curl -s "https://rest.uniprot.org/uniprotkb/search?query=tp53+AND+organism_id:9606&format=tsv&fields=accession,gene_names,protein_name&size=5"
```

## Decision tree

```
Need UniProt data?
├── Structured data (sequence, features, cross-refs) → REST API (fetch_webpage or curl)
├── Search across proteins → REST search endpoint
├── Read the web entry page → fetch_webpage_js
└── Read UniProt help/docs → fetch_webpage_js
```
