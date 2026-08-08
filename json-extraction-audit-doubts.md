# json_extract.py audit — doubts for review

No code changed. This is a read-only re-derivation of extraction against all 964
files in `corpus_by_type/json/`, built to check `json_extract.py` against the
actual corpus rather than only the 17 synthetic self-check fixtures.

**Method**: ran `json_extract.extract_json()` unmodified over all 964 files
(fresh log at `arch_test/data/extraccion_json.jsonl`, 938 tier-1, 0 tier-2,
26 tier-3 — matches prior census). Then, for each file, ran five independent
diagnostic checks against the real harvest/assemble logic (not shipped code,
audit-only, output at `arch_test/data/audit_flags.jsonl`):

- A. strings inside `NOISE`-classified keys that never reach the output anywhere
- B. blob-role text dropped file-wide because a paragraph list exists elsewhere
  (the `has_para` rule in `_assemble` is file-scoped, not per-field)
- C. same normalized metadata key hit more than once with different values
  (`meta.setdefault` keeps only the first)
- D. metadata values truncated at `TRUNC_META` (300 chars)
- E. near-duplicate (ratio > 0.9, not byte-identical) chunks surviving the
  exact-string dedup in `_assemble`

Every flagged file was then opened and read against its raw JSON to separate
real defects from audit false positives. Findings below, ranked by what I'd
actually want a decision on.

---

## 1. Six catalog/registry files pass as tier-1 "documents" — RESOLVED

**Decision**: don't index the catalog as its own document, don't tier-3/drop it
either — salvage its per-entry metadata (title, date, authors, country, tags)
by matching each entry to the real file it already describes, and save that
under `catalog/`. Built as `catalog_metadata.py` (root project dir), generic
over corpus root — does not hardcode `corpus_by_type/` (that's a local
per-extension convenience copy from `sort_by_extension.py` and won't exist in
production; the script walks the real ADL layout, e.g.
`CORPUS CODEFEST AD ASTRA 2026/`).

Run: `python catalog_metadata.py "CORPUS CODEFEST AD ASTRA 2026" --out catalog`
→ `catalog/catalog_metadata.jsonl`, one row per matched entry:
`{"catalogo": <source catalog file>, "fuente": <matched real file's relative path>, "metadata_catalogo": <full catalog entry>}`.

All 20 known catalog/registry files (14 tier-3 + 6 tier-1-leak) processed,
358 entries matched:

| catalog | matched / total |
|---|---:|
| `RESDAL_catalog-2.json` | 95/95 |
| `ceeep_catalogo.json` | 80/80 |
| `sipri_full_catalogo.json` | 50/50 |
| `resdal_catalogo.json` | 45/46 |
| `DAIO_catalog-2.json` | 33/35 |
| `mapp_catalogo.json` | 30/31 |
| `ceobs_full_catalogo.json` | 10/20 (other 10 are generic "Latest updates" feed stubs, no real metadata to salvage anyway) |
| `MAPPOEA_mapp-catalog.json` | 10/78 (68 are genuine 404/503 dead scrapes — correctly unmatched, nothing to enrich) |
| `AMAZONUW_tiles-index.json`, `DEFENSA21_catalog-2.json`, and the `*_registro.json` files | 0 (tile-fetch / RSS-status / hash-dedup records — no document-shaped entries in them to begin with) |

**Not yet wired in**: the main pipeline (`json_extract.py`/`corpus.py`) still
needs to (a) exclude these 20 filenames from being walked as documents at
all, and (b) look up `catalog/catalog_metadata.jsonl` by `fuente` when
building each real document's metadata, merging in `metadata_catalogo`. Left
for the indexing-integration step, not done here.

<details><summary>original finding (superseded by the decision above)</summary>

The corpus has ~20 catalog/registry/index JSON files (scraper bookkeeping: one
entry per downloaded PDF, fields like `title`/`url`/`status`/`scraped_at`, no
body prose). 14 of them correctly fail as tier-3 (`JsonSinTexto`) — already
known and classified in `json-schema-survey.md`.

**Six were missed by that earlier review because they pass tier-1 clean**,
concatenating every entry's `title` field into a word count above the 30-word
floor. They look, to the pipeline, exactly like a legitimate "list of several
short articles" (self-check fixture #8 covers that case on purpose). There is
no signal in `json_extract.py` that tells the two apart.

| file | tier-1 word count | what the "document" actually is |
|---|---:|---|
| `RESDAL_catalog-2.json` | 578 | 95 chapter/country PDF titles, e.g. "Atlas 2024 ESP – Colombia" |
| `ceeep_catalogo.json` | 1047 | article title index |
| `sipri_full_catalogo.json` | 520 | article title index |
| `DAIO_catalog-2.json` | 248 | title+subtitle index |
| `MAPPOEA_mapp-catalog.json` | 117 | title index |
| `ceobs_full_catalogo.json` | 109 | title index |

Cross-checked all six against the real files on disk, matching each catalog
entry's filename/URL/JSON-pointer field (normalized for `_`/`-` drift) to
`corpus_by_type/`:

| catalog | entries | matched to a real file already in the corpus |
|---|---:|---:|
| `RESDAL_catalog-2.json` | 95 | **95/95** — every `filename` matches a real PDF under `corpus_by_type/pdf/` (e.g. `01_ESP_El_Marco_Legal.pdf` catalog-side ↔ `RESDAL_01-esp-el-marco-legal.pdf` on disk) |
| `ceeep_catalogo.json` | 80 | ~80/80 — 82 real per-article `CEEEP_issue##-##-<slug>.json` files exist on disk, each already a full tier-1 document with real body text |
| `DAIO_catalog-2.json` | 35 | ~32/35 — 32 real `DAIO_study####-<slug>.pdf` files on disk, catalog references line up 1:1 with them by study number |
| `sipri_full_catalogo.json` | 50 | 39/50 confirmed (rest likely naming-slug drift, not re-chased) — each entry's `json` field points at a real per-article file that mostly exists on disk as its own document |
| `ceobs_full_catalogo.json` | 20 | 17/20 confirmed, same pattern |
| `MAPPOEA_mapp-catalog.json` | 78 | different case — see below |

RESDAL, ceeep, DAIO, SIPRI, CEOBS: confirmed redundant. Every one of these
catalogs is an index over content that is **also** present in the corpus as
its own separate, real document (PDF or per-article JSON) — the catalog adds
no unique retrievable content, only a duplicate title-salad "document."

**`MAPPOEA_mapp-catalog.json` is a different case, worth flagging separately**:
of its 78 entries, only 10 have `status: 200`; 63 are `404` and 5 are `503`
(68/78 have `size_bytes: 0` — the download never succeeded). So this
"document"'s 117-word tier-1 text is mostly a list of titles for PDFs that
were **never actually retrieved**, not a redundant copy of real content
elsewhere. Its inclusion as a pseudo-document is the same F1@3/NDCG@10 risk
as the others, but there is no equivalent "the real thing is indexed
elsewhere so this is harmless clutter" excuse — most of what it names isn't
in the corpus in any form.

**Why this matters for scoring**: each of these six becomes its own `doc_id`
with `fuente` = the catalog filename (e.g. `RESDAL_catalog-2.json`), which
almost certainly is not a `fuente` the evaluator's ground truth ever names —
any retrieval hit on it is a pure F1@3 false positive. Its "text" is a title
run-on, not prose, so it should score low on NDCG@10 relevance, but a
title-salad chunk can still surface on keyword/lexical overlap (e.g. a country
name) for an unrelated query and cost a rank slot.

**Not fixed.** Options to decide between: filter files whose shape is
"list of objects that are almost entirely META-role fields, no BODY-role
field, above a size threshold" before tier-1 acceptance; or filter by filename
pattern (`*catalog*`, `*catalogo*`, `*registro*`, `*index*` — imperfect,
already missed `DEFENSA21_articulos-2.json` by name); or accept the six as
in-corpus noise and let retrieval mostly ignore them.

---
</details>

## 2. `excerpt`/`resumen`/summary fields duplicate the article opening — MEDIUM

`BODY_OTHER` (which includes `excerpt`, `resumen`, `summary`, `abstract`) is
treated as always-distinct prose, kept alongside the body unconditionally.
For several scraper families it is actually a CMS-generated teaser that
repeats the article's opening sentence(s) near-verbatim, then diverges (cut at
a different length, or continuing differently) — so `_assemble`'s exact-string
dedup never catches it.

Confirmed on `ATLCOUNCIL_01-event-recap-data-salon-episode-1.json`:
`excerpt` and `body_paragraphs[0]` are identical for the first ~360 characters,
then diverge. Both get kept as two separate chunks of near-identical content.

Affects 121 of 964 files (~12.5%), concentrated by source:

| prefix | files |
|---|---:|
| ATLCOUNCIL | 57 |
| CSIS | 33 |
| SWF | 12 |
| SIPRI | 7 |
| ESA | 6 |
| CEOBS | 3 |
| MAPPOEA, RESDAL, sipri | 1 each |

**Impact**: wastes chunk/token budget on near-duplicate content rather than
corrupting meaning — lower severity than #1, but real and corpus-wide, and the
existing "484 files body_text/body_paragraphs" dedupe precedent (handled by
the `has_para` blob-drop) doesn't cover cross-field near-duplicates.

**Not fixed.** Would need a near-duplicate check (containment / ratio
threshold) between `BODY_OTHER` strings and the assembled body text, not just
exact-match dedup.

---

## 3. `tags`/`topics` metadata truncated mid-list at 300 chars — LOW, likely fine

240 files hit `TRUNC_META` (300 chars): 186 on `tags`, 54 on `topics`, 1 on
`authors`. Long tag/topic lists get cut mid-tag rather than mid-word-boundary-
aware. Checked the `authors` case (`ATLCOUNCIL_07-welcoming-our-geotech-
fellows.json`): 26 names joined by `", "`, confirmed cut — the last handful
of names are silently dropped from metadata.

**Why this is probably not worth fixing**: `tags`/`topics` are not in the
spec's mandatory metadata fields (`doc_id`, `chunk_id`, `fuente`, `formato`,
`fenomeno`, `posicion`, `num_tokens`, `texto`) — they're extras. The spec's
allowed post-filters are `fenomeno`, language, date, and score — not tags. A
truncated tag list only matters if the submission ends up filtering or
displaying on it.

---

## Checked and found clean (no action, listed so they're not re-litigated)

- **NOISE-key prose loss (check A)**: 0 files. Every string classified under
  a `NOISE` key (`images`, `links`, `pdf_links`, ...) in the real corpus is
  either genuinely non-prose or duplicated elsewhere. The tier-1 decision to
  never traverse `NOISE` keys does not lose real content on this corpus.
- **File-wide blob-drop on `has_para` (check B)**: 20 files flagged by the
  automated heuristic, all verified false positives — in every case the
  dropped `body_text` blob is `body_paragraphs` re-joined with a different
  separator (whole-string similarity ratio 0.91–1.00 against the kept text
  once compared correctly, i.e. as a whole rather than per-paragraph). The
  file-scoped drop rule is doing the right thing everywhere it fired.
- **Meta key collisions (check C)**: all 12 files that hit this are catalog
  files (subsumed by finding #1) — a 95-entry catalog naturally repeats `url`,
  `status`, etc. 95 times, and only the first is kept as metadata for the
  catalog's own (undesirable) pseudo-document. No collision was found on a
  real content document, so `meta.setdefault` silently keeping only the first
  hit is not currently costing a real document its correct metadata.
- **tier-2 never fires on real data** (0 of 964) — the rescue path is only
  exercised by the synthetic self-check fixtures, never by the actual corpus.
  Not a defect, just worth knowing the real corpus never needs it.

---

## Reproduce

```
python json_extract.py --run corpus_by_type/json --log arch_test/data/extraccion_json.jsonl
```
regenerates the base extraction log used above (938/26/0 split). The five
diagnostic checks were a throwaway audit script (not part of the repo), run
against that log plus direct re-parsing of each raw file; every number above
was then manually verified against the source JSON, not taken as-is from the
heuristics.
