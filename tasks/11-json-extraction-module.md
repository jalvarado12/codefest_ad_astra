# Task 11 — Submission-grade JSON extraction module

**Status: DONE (2026-08-06, executed in-session).** `json_extract.py` built, 17 self-check fixtures pass,
`corpus.py:_json` delegates to it. Run over the real corpus: 938/964 at tier 1, 0 parse failures.
See `json-schema-survey.md` for the census and `json-extraction-iteration-log.md` iteration 4 for the four
revisions the real data forced.

## Goal

Build `json_extract.py`: a deterministic, tiered extractor that turns any `.json` file in the corpus into
clean body text plus document metadata, regardless of its key names or nesting, and that records what it
did for every file so coverage can be audited.

## Context

The corpus's `.json` files do not share one structure (§1.3: "artículos y páginas web estructurados").
§2.1 prescribes explicit field selection, order-preserving concatenation, and keeping descriptive fields
(url, date, authors, tags) as metadata rather than body text — but names field names only "por ejemplo",
so adaptive handling is assumed. §1.4 requires `generador.py` to reproduce results exactly, making
determinism a hard constraint.

The existing extractor at `arch_test/corpus.py:67-102` has seven audited defects, four of them silent-drop
paths where a document produces no text and no record of why. The full audit is in
`json-schema-survey.md`; the accepted design and the alternatives that were set aside are in
`json-extraction-strategy.md`. **Read those two documents first — they carry the reasoning this task
implements, so it does not need re-deriving.**

One thing this task cannot rely on: **the ADL corpus is not on this machine.** The alias table below is
therefore an untested guess, and that is exactly why the module records provenance. Task 12 turns those
records into the real schema survey once the corpus lands. Build for that: the logging is not
instrumentation to be added later, it is the deliverable's audit trail.

## Graphify queries to run first

N/A — no graph exists for this project. Read the files listed below directly; do not go searching.

## Read only these files

- `json-extraction-strategy.md` — whole file. This is the specification.
- `json-schema-survey.md` — the audit section, for the seven defects being closed.
- `ad_astra.md` lines 262-269 (§2.1, JSON handling) and 290-308 (§2.2 cleaning, §2.3 document identity).
- `arch_test/corpus.py:67-102` — `_json`, the extractor being replaced.
- `arch_test/corpus.py:161-176` — `extract()`. Its 30-word floor at line 174 is the constant the tier-2
  trigger must reuse; do not introduce a second number.
- `arch_test/chunker.py` — `clean()` only, to see what normalization already happens downstream so this
  module does not duplicate it.

## Create/modify exactly these files

- **Create** `json_extract.py` (repo root).
- **Modify** `arch_test/corpus.py:67-102` — replace the body of `_json` with a delegation to
  `extract_json`, keeping its existing `(text, meta)` return shape and routing `traza` to the provenance
  log. Do not leave a second copy of the extraction logic behind.

## Detailed instructions

Implement exactly the design in `json-extraction-strategy.md`. In summary, so the shape is clear:

```python
extract_json(path) -> (texto: str, meta: dict, traza: dict)
# raises JsonIlegible(path, detalle)  — could not be parsed at all
# raises JsonSinTexto(path, traza)    — parsed, but yielded no usable body
```

1. **Stage L, load.** Read bytes; decode `utf-8-sig` with `errors="replace"`. `json.loads`; on
   `JSONDecodeError`, retry as NDJSON — every non-blank line must parse — wrapping results in a list and
   setting `carga="ndjson"`. Both fail → `JsonIlegible` carrying the decoder's own message.
2. **Stage R, root normalization.** A top-level list, or a list of articles under one key, is a
   multi-article file. It stays **one document** (§2.3). Concatenate articles in file order, blank-line
   separated; collect per-article descriptive fields into `meta["articulos"]`; keep document-level `url`
   and `date` first-wins so downstream metadata stays flat.
3. **Stage 1, alias harvest.** The TITLE / BODY / META tables and the walk rules are given verbatim in
   the strategy document. Match keys lowercased and accent-stripped. Iterate dicts in insertion order.
   The two rules that close the silent-drop defects: a body key with a **dict** value recurses, and a
   body key with a **list** value recurses into any non-`str` element instead of skipping it. Append every
   emitting key's dotted path to `traza["claves_texto"]`.
4. **Stage 2, filtered fallback.** Trigger on `len(texto.split()) < 30` — import or otherwise share
   `extract()`'s constant, do not retype it. Harvest string leaves in document order, excluding keys
   already consumed as META, strings matching URL / ISO-8601 date / pure-numeric / hex-or-UUID /
   MIME-type patterns, and strings under 40 characters. Dedupe exact repeats. Set `tier=2` and record a
   warning.
5. **Stage 3, explicit failure.** Still under 30 words → `JsonSinTexto` with the full `traza`, including
   the file's top-level key set.
6. **Provenance.** Append every `traza` to `arch_test/data/extraccion_json.jsonl`, one object per line.
7. **Census mode.** `python json_extract.py --resumen <log.jsonl>` prints: documents per tier, most
   common `claves_texto` paths, META keys seen, and the full top-level key set of every tier-3 failure.
   This output is what fills the census tables in `json-schema-survey.md`.

## Do NOT

- Do **not** reorder title ahead of body. §2.1 says "respetando su orden de aparición"; emission follows
  traversal order even where title-first would read better.
- Do **not** add an LLM, a model call, a network call, or anything clock- or filesystem-order dependent.
  §1.4 requires byte-identical reproduction, and a decoder model in the indexing path is a jury risk under
  §4.2/§8.3. This was considered and rejected — see the alternatives section of the strategy document.
- Do **not** let any code path return empty or whitespace-only text without raising. The four recorded
  states are the coverage guarantee.
- Do **not** emit META-role values as body text on any path, including the tier-2 fallback. That is
  defect 6 and it is the reason the fallback is filtered.
- Do **not** add alias keys that were not in the strategy document's tables. Speculative additions are
  Task 12's job, and only from observed evidence.
- Do **not** widen scope to PDF, HTML, CSV, XLSX, image or PBF extraction, to chunking, or to anything
  downstream of "produces clean text plus metadata per document".
- Do **not** commit or push. Leave the work as uncommitted working-tree edits. (This directory is not a
  git repository, so there is nothing to commit to in any case.)

## Acceptance criteria

`python json_extract.py` runs an assert-based self-check over inline fixtures — no test framework, no
fixture files — covering at minimum:

| Fixture | Expected |
|---|---|
| `{"title": ..., "body_text": ...}` | tier 1, both fields in order |
| `{"headline": ..., "content": ..., "author": ...}` | tier 1; author in `meta`, not in body |
| `{"article": {"title": ..., "paragraphs": [...]}}` | tier 1; paragraphs joined in order |
| `{"content": {"blocks": [...]}}` | tier 1 via recursion — closes defect 1 |
| `{"body_paragraphs": [{"text": ...}, ...]}` | tier 1 via recursion — closes defect 2 |
| BOM-prefixed file | loads — closes defect 4 |
| NDJSON file | loads, `carga == "ndjson"` — closes defect 5 |
| Top-level list of articles | one document, articles in file order |
| Title present, body missing | tier 2 fires on the word floor — closes defect 3 |
| Metadata only, no prose | raises `JsonSinTexto` |
| Truncated / invalid JSON | raises `JsonIlegible` |

Plus two assertions across all fixtures: **no fixture leaks a url or a date into body text** (§2.1), and
**running the same fixture twice yields identical output** (§1.4).

## Verify

- `python json_extract.py` — self-check passes.
- `python arch_test/corpus.py --help` — still imports cleanly after the delegation edit. Note that
  `corpus.py` imports `chunker`, which is import-safe on this machine; do not attempt to run anything that
  touches `encoders.py`, whose `pyarrow` dependency is blocked by a Windows Application Control policy.

## Update graphify / CLAUDE.md

Add `json_extract.py` to the `arch_test/` file table's neighbourhood in CLAUDE.md — noting it is
submission-grade rather than reference-only, that `corpus.py:_json` delegates to it, and that its
provenance log at `arch_test/data/extraccion_json.jsonl` is the input to Task 12's census.
