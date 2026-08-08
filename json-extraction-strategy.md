# JSON extraction strategy — accepted design

**Iterations to reach this design: 4.** Iterations 1–3 were run against a code audit while the corpus was
unavailable; iteration 4 ran the design against the real 964-file corpus and revised it on what the data
showed. **All definition-of-done criteria now pass** — see "Status" at the end.

Implemented in `json_extract.py`, verified by 17 self-check fixtures and by a full corpus run:
**938 of 964 files extracted at tier 1, 0 parse failures, 26 explicitly flagged and individually
accounted for** in `json-schema-survey.md`.

## Problem

The corpus's `.json` files do not share one structure (spec §1.3: "artículos y páginas web
estructurados"). §2.1 prescribes interpreting the object, selecting explicitly the fields holding each
article's text, concatenating them in order of appearance, joining paragraph lists in order, and keeping
descriptive fields (url, date, authors, tags) as document metadata rather than mixing them into the body.
It names field names only "por ejemplo", so it assumes adaptive handling without specifying it. §1.4
requires `generador.py` to reproduce the team's results exactly, which makes determinism a hard
constraint on the design space rather than a preference.

The existing extractor at `arch_test/corpus.py:67-102` has seven defects, four of them silent-drop
paths. They are enumerated in `json-schema-survey.md`; that audit is the evidence this design answers to.

## Accepted design — tiered extraction with recorded provenance

A new submission-grade module, **`json_extract.py`** at repo root, imported later by
`entrega/generador.py`. `arch_test/corpus.py:_json` is reduced to a two-line delegation to it, so the
comparison harness and the submission cannot drift into two different extractors.

```python
extract_json(path) -> (texto: str, meta: dict, traza: dict)
# raises JsonIlegible(path, detalle)  — could not be parsed at all
# raises JsonSinTexto(path, traza)    — parsed, but yielded no usable body
```

`traza` records `{"tier": 1|2, "carga": "json"|"ndjson", "claves_texto": [...dotted paths...],
"claves_meta": [...], "claves_raiz": [...], "advertencias": [...]}`.

### Stage L — load

1. Read bytes; decode `utf-8-sig` with `errors="replace"`.
2. `json.loads`. On `JSONDecodeError`, retry as NDJSON: every non-blank line must parse as JSON; wrap the
   results in a list and set `carga="ndjson"`.
3. Both fail → raise `JsonIlegible` carrying the decoder's own message.

### Stage R — root normalization

A top-level list, or a list of articles under a single key, is a multi-article file. The file remains
**one document** (§2.3: a document is an individual file provided by ADL). Articles are concatenated in
file order, blank-line separated. Per-article descriptive fields are collected into `meta["articulos"]`,
with document-level `url` and `date` kept first-wins so downstream metadata stays flat.

### Stage 1 — alias harvest over an ordered recursive walk

Keys matched lowercased and accent-stripped, in three roles:

- **TITLE** — title, titulo, título, headline, heading, subject, name, nombre, encabezado
- **BODY** — body_text, body, bodytext, content, contenido, texto, text, full_text, fulltext,
  article_body, articlebody, paragraphs, body_paragraphs, parrafos, párrafos, description, descripcion,
  abstract, resumen, summary, lead, lede, extract
- **META** — url, link, permalink, canonical_url, date, fecha, published, published_at, pubdate,
  datePublished, author, authors, autor, autores, byline, tags, keywords, categories, categoria,
  section, source, fuente, language, idioma, id, doc_id

Walk rules; dicts are iterated in insertion order, which Python guarantees and which makes the walk
deterministic:

- Key in META with a scalar or list-of-scalars value → record into `meta`; do **not** emit as body, do
  **not** recurse.
- Key in TITLE or BODY:
  - `str` value → emit.
  - `list` value → per element, in order: a `str` emits; a `dict` or `list` is **recursed into**,
    collecting strings under this body context.
  - `dict` value → **recurse**.
  - other scalar → ignore.
- Key in neither role → recurse into the value.
- Every emitting key's dotted path is appended to `traza["claves_texto"]`.

The two recursion rules on body-key values are what close defects 1 and 2 — the current code matches the
alias branch, finds neither `str` nor list-of-`str`, appends nothing, and never recurses, so the body
vanishes without a trace.

Emission order is strictly traversal order. §2.1 says "respetando su orden de aparición", so there is no
title-first reordering even where it would read better. Adjacent exact-duplicate strings are collapsed —
`title` and `headline` carrying the same value is a common shape — while non-adjacent duplicates are
kept, which is the safer default when a phrase legitimately recurs.

### Stage 2 — filtered generic harvest, triggered by a word floor

The trigger is `len(texto.split()) < 30`, **not** emptiness. That constant is the same floor `extract()`
already rejects on at `corpus.py:174`, so the fallback trigger and the reject criterion agree by
construction rather than by two independently maintained numbers drifting apart.

Harvest every string leaf in document order, excluding: keys already consumed as META; strings matching
URL, ISO-8601 date, pure-numeric, hex-or-UUID, or MIME-type patterns; and strings under 40 characters,
which drops slugs, ids and UI labels while keeping sentences. Dedupe exact repeats, join, set `tier=2`
and record a warning.

Two things separate this from the fallback it replaces. It fires on partial extraction, not only on total
failure, which is what makes a title-only match visible as a schema miss instead of being misfiled as a
scan failure. And it is filtered, so §2.1's separation of descriptive fields from body text survives into
the fallback path — the current unfiltered harvest concatenates urls and dates straight into the body.

### Stage 3 — explicit failure

Still under 30 words → raise `JsonSinTexto` carrying the full `traza`, including the file's top-level key
set so the failure is itself a survey datapoint. The caller records it in `skipped.json` with that reason.

Every `.json` file therefore ends in exactly one of four recorded states: extracted at tier 1, extracted
at tier 2, flagged `JsonSinTexto`, or flagged `JsonIlegible`. **None is ever silently skipped or silently
empty.** This is the document-coverage guarantee, and it is structural rather than a matter of
remembering to log.

### What happens on a shape the strategy does not recognize

Stated plainly, because this is the question a fixed alias table cannot answer well:

1. Unrecognized **key names** with recognizable prose → tier 2 recovers the text, and the warning plus
   the file's key set are logged for later alias-table extension.
2. Unrecognized **container shapes** (body under an unusual nesting) → the walk recurses through
   unmatched keys, so tier 1 usually still finds the text; if not, tier 2 does.
3. Unrecognized **and** textless, or text below the 30-word floor → tier 3, explicitly flagged, never
   silently dropped.
4. Not valid JSON in either single-value or NDJSON form → `JsonIlegible`, explicitly flagged.

### Revisions the real corpus forced (iteration 4)

Four changes the pre-corpus design did not anticipate. Each is in the module and each has a fixture.

1. **Paragraph lists beat body blobs.** 485 of 848 article files carry `body_text` *and*
   `body_paragraphs`, where the former is the latter re-joined. Emitting both doubled every one of those
   documents. `_assemble()` drops blob-role emissions whenever any paragraph-role emission is present.
   §2.1 favours the same choice independently, since the list carries the real paragraph boundaries.
2. **Duplicate collapse must be global, not adjacent.** All 363 ALERTAS files repeat
   `body_paragraphs[1]` verbatim at `alerta_meta.tema_clave`, and the two are not adjacent. The
   originally planned adjacent-only rule would have missed all 363.
3. **Link and asset containers are never traversed at tier 1.** `images`, `links`, `pdf_links`,
   `doc_links`, `external_links`, `internal_links`, `science_links`, `urls`, `hashes`, `pdfs` and friends
   carry only anchors, alt text and file paths. The `NOISE` set excludes them from tier 1 and admits them
   only at tier 2, where recovering a page from its link text is better than recovering nothing.
4. **The word floor must not assume whitespace-delimited script.** A 503-character Chinese SWF handbook
   counted as ~10 words under `str.split()` and was rejected. `_wc()` counts CJK ideographs individually.

### Strings under unrecognised keys

Tier 1 also keeps strings of **40 characters or more** found under keys in no role, as long as they are
not inside a `NOISE` container and do not match the identifier patterns. That is what recovers CENIA's
`lists[]` and the `[].titulo` / `[].subtitle` fields in catalog-shaped files, without dragging in the
short tags and slugs that a blanket string harvest would.

### The self-survey

Every document's `traza` is appended to `arch_test/data/extraccion_json.jsonl`. A `--resumen` mode reads
that file and prints the census: documents per tier, the most common `claves_texto` paths, the META keys
actually seen, and the full top-level key set of every tier-3 failure.

That output **is** the schema survey — produced from the real corpus by the run that has to happen
anyway, rather than requiring a separate pre-pass over a corpus that does not exist yet. A human reads
it and, if it shows unrecognized shapes, extends the alias table: one frozen constant, one edit, still
deterministic.

### Determinism (§1.4)

Every stage is a pure function of the file's bytes. Dict iteration is insertion order; the regex set is a
frozen module constant; there is no randomness, no network call, no model, no clock or filesystem
dependence beyond reading the file. Identical input yields byte-identical output, which is what
`generador.py`'s reproduction requirement needs.

## Alternatives considered and set aside

**Alias/mapping table only, with a defined fallback to nothing.** Simplest, and the closest reading of
§2.1's "seleccionar explícitamente los campos". Set aside because corpus variety is unmeasured: a fixed
table is an unbounded coverage bet, and the failure mode is a silently empty document. The accepted
design keeps the table as tier 1 and adds a floor beneath it rather than discarding it.

**Generic recursive harvest only — every string leaf above a length threshold.** Robust to arbitrary
nesting and needs no table. Set aside because it violates §2.1 directly: url, date, authors and tags land
in the body text, which the spec explicitly says to keep as metadata instead. It also degrades retrieval
by embedding ids and slugs alongside prose. It survives as tier 2, filtered, where its robustness is
wanted and its indiscriminacy is contained.

**Schema-clustering pre-pass, then one small explicit extractor per shape.** Would give the cleanest
per-shape handling and the best survey. Set aside on two grounds: it requires the corpus, which is not
available, so it cannot be built now; and it produces N extractors to maintain for a one-shot
competition. The provenance log gives the same census for free from a run that is required regardless.

**LLM-assisted field classification.** Rejected on two independent grounds, either sufficient. It cannot
be guaranteed byte-identical across runs, which §1.4's reproduction requirement forbids. And a decoder
model anywhere in the indexing path is at minimum a jury risk under §4.2/§8.3, which this project treats
as disqualifying. Worth naming as an option; not worth treating as a candidate.

## Status against the definition of done

| Criterion | Status | Evidence |
|---|---|---|
| Grounded in a real sample of the corpus's own `.json` files | **Passes** | Full run over all 964 files, not a sample; census in `json-schema-survey.md` |
| Justified against findings, with explicit fallback for unrecognized shapes | Passes | Four revisions in iteration 4 come directly from the data; fallback is tier 2 then explicit tier 3 |
| Descriptive fields kept out of body text, paragraph order preserved (§2.1) | Passes | 0 violations; the 30 in-text URLs were each inspected and are citations inside prose |
| Deterministic, or non-deterministic steps flagged with their risk (§1.4) | Passes | Pure function of file bytes; fixture 15 asserts repeat-run equality |
| No implicit document-coverage gap | Passes | 938 + 26 = 964 exactly; all 26 individually adjudicated |
| Task files numbered correctly against real repo state, self-contained | Passes | 11 and 12, against existing 01–10 |

**All criteria pass.** The grounding criterion, which failed while the corpus was unavailable, was closed
by the run of 2026-08-06.

Two things the run changed rather than confirmed, worth carrying into the informe técnico:

- The corpus is **scraper output from 20 sources, one consistent shape per source** — 15 distinct
  top-level key sets across 964 files, nesting never deeper than one named level. It is far more regular
  than the planning assumption of wide, unpredictable variety. The recursive traversal was therefore
  insurance rather than necessity; that is recorded as a negative result in the survey.
- The real risk was never unrecognised field names. It was **duplication** — `body_text` shadowing
  `body_paragraphs` in 485 files and `tema_clave` shadowing a paragraph in 363 — which a pure alias-table
  reading would have silently doubled into the index.

**One open item is deliberately left to a human** (see the survey's closing section): 12 documents are
excluded by `corpus.extract()`'s 30-word floor at `corpus.py:174`. Eleven are empty scrapes and one,
`ESA_space-debris-by-the-numbers`, is genuinely thin but on-phenomenon. That floor is global policy for
all formats, so changing it is a corpus-wide decision and was not made inside this task.
