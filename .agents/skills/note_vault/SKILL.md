---
name: note_vault
description: CRITICAL — when the user asks to take notes, keep a notebook, remember a fact/decision/method across sessions, or build a personal knowledge base ("note that", "add to my notes", "what did I write about X", Obsidian-style links): use the vault_* tools.
---

# `note_vault`

The vault is a personal, searchable knowledge base of markdown notes stored in
`~/.config/ai/vault/` and indexed with SQLite FTS5 (Obsidian-style `[[wiki-links]]`).
It is the right home for durable knowledge that should outlive a single
conversation: methods, decisions, sample-prep recipes, lab protocols, key facts.

## Tools

- `vault_write(title, content, links="A,B")` — create/overwrite a note.
  Comma-separated `links` are appended as an Obsidian `[[Link]]` footer, which
  is what `vault_backlinks` searches for.
- `vault_read(title)` — read a note back (title without `.md` is fine).
- `vault_search(query)` — full-text search across all notes (FTS5), top 10 with
  200-char previews.
- `vault_backlinks(title)` — which notes link TO this one (the Obsidian
  backlinks view).

## When to use which store

- **vault** — structured, revisitable notes (a protocol, a decision log, a
  paper summary). Long-lived, human-readable files you can open in Obsidian.
- **remember/recall** (`~/.config/ai` memory DB) — short atomic facts the model
  should keep in mind ("user's sample buffer is 10mM HEPES").
- **skill_note/skill_create** — *procedures* ("how to run X"). Skills are for
  reusable workflows; the vault is for knowledge and records.
- **search_history / get_session** — *what happened* in past conversations.

## Conventions

- Titles are filenames: short, specific, no slashes ("BoltzGen-Campaign-2026-08").
- Write notes in plain markdown; keep them self-contained (a future-you reading
  them cold should understand the context).
- Link related notes with `links=` so backlinks work; check `vault_backlinks`
  before overwriting a note that others point to.
- Update a note in place (`vault_write` with the same title) rather than
  accumulating near-duplicate titles — search dedupes by content, not title.
- Cite sources (paper ids, URLs) inside the note when it records findings.

## Workflow

1. **Before writing** — `vault_search(topic)` to see if a note already exists;
   extend it rather than duplicating.
2. **Write** with `links=` pointing at related notes.
3. **Verify** — `vault_read` or `vault_search` to confirm it landed and is findable.
