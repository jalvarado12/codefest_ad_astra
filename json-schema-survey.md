# JSON schema survey — corpus `.json` files (§2.1)

**Status: COMPLETE.** Run 2026-08-06 against the real ADL corpus at `corpus_by_type/json/`
(964 files, 13 MB). Supersedes the blocked template that stood here while the corpus was unavailable.

## Method

`python json_extract.py --run corpus_by_type/json` extracts every file and writes one provenance record
per document to `arch_test/data/extraccion_json.jsonl`;
`python json_extract.py --resumen arch_test/data/extraccion_json.jsonl` prints the census below. Every
number here comes from that run, not from inspection of a sample.

## Headline result

| Metric | Value |
|---|---|
| `.json` files in corpus | **964** |
| Parse failures (`JsonIlegible`) | **0** |
| Extracted at tier 1 (alias harvest) | **938** (97.3%) |
| Extracted at tier 2 (filtered fallback) | **0** |
| Flagged tier 3 (`JsonSinTexto`) | **26** (2.7%) |
| Loaded as NDJSON rather than a single JSON value | 0 |
| Distinct top-level key sets | **15** |

938 + 26 = 964. The four states reconcile exactly against the file count, which is the coverage
guarantee: **no file was silently skipped or silently empty.**

Word counts of extracted documents: min 30, p25 92, median 213, p75 617, max 46 272.

## What the corpus actually is

Not a wild mix of hand-authored schemas — it is **scraper output from 20 sources**, and each source emits
one consistent shape. The variety is between sources, not within them.

| Family | Files | Shapes | Top-level keys |
|---|---|---|---|
| ALERTAS (Defensoría, alertas tempranas) | 363 | 1 | `alerta_meta, body_paragraphs, doc_links, fields, pdf_links, title, url` |
| ATLCOUNCIL | 186 | 1 | `authors, body_paragraphs, body_text, date, excerpt, tags, title, url` |
| CSIS | 103 | 2 | above + `images, pdf_links, topics` |
| CEEEP (revista, journal articles) | 80 | 1 | `abstract, authors, date, doi, issue, keywords, pdf_url, title, url` |
| SWF | 56 | 2 | article shape + `external_links, internal_links` |
| INPE | 55 | 1 | article shape + `science_links` |
| SIPRI | 53 | 2 | article shape |
| CEOBS | 20 | 2 | article shape + `categories` |
| ESA | 16 | 1 | article shape + `tags` |
| CENIA | 15 | 1 | `contenido_limitado, images, links, lists, pdf_links, sections, title, url` |
| catalogs / registries (21 singletons) | 21 | 9 | `hashes/urls`, `articulos`, `tile/zoom/x/y`, download logs |

Three of the four illustrative shapes assumed in planning do occur (`title`+`body_text`,
`headline`-style articles, paragraph lists). The `{"article": {...}}` wrapper does **not** appear
anywhere in this corpus.

### Field-name variants actually observed, by role

- **Title role** — `title` (943 files), `sections[].heading` (360), `[].titulo` (185), `[].title` (130),
  `[].subtitle` (23), `[].nombre` (5). No `headline` in this corpus.
- **Body role** — `body_paragraphs` (9 333 paragraph strings across 848 files), `excerpt` (464),
  `sections[].paragraphs` (83), `abstract` (80), `body_text` (485, but see duplication below),
  `alerta_meta.tema_clave` (363, likewise), `lists[]` (21).
- **Metadata role** — `url` 970, `date` 573, `authors` 502, `codigo`/`tipo`/`fecha_emision`/`municipios`
  363 each, `topics` 216, `tags` 203, `pdf_url` 82, `keywords`/`doi`/`issue` 80 each, `categories` 19,
  plus `scraped_at`, `fenomeno`, `fuente`, `year`, `idioma`, `country`, `edition`, `language`.

### Nesting

Overwhelmingly **flat or one level deep**. The nesting that exists is narrow and named:
`alerta_meta.*` (363), `sections[].heading` / `sections[].paragraphs` (15), `metadata.*` and
`content.sections.*` in a single SWF report file. Deep or arbitrary nesting does not occur.

**Retroactive verdict on the recursive traversal:** it was not strictly necessary for this corpus — a
flat-key mapping plus one level would have covered nearly everything. It is cheap insurance rather than a
requirement, and it is what let `content.sections.*` and `body_paragraphs: [{...}]` shapes work without a
per-source special case. Recorded as a negative result, since it is a finding either way.

## Two duplication traps the raw data sets, both caught

1. **`body_text` is `body_paragraphs` re-joined.** 485 of the 848 article files carry both. On a sampled
   ATLCOUNCIL file: `body_text` 6 147 chars, `"\n".join(body_paragraphs)` 6 136 chars, same opening
   sentence. Emitting both would have **doubled every one of those 485 documents** — inflating the chunk
   count, the indexing cost, and the embedding of duplicated passages. The extractor drops the blob when
   a paragraph list is present, which §2.1 independently favours ("si el cuerpo viene como una lista de
   párrafos, se unen preservando ese orden").
2. **`alerta_meta.tema_clave` is an exact copy of `body_paragraphs[1]`** in all 363 ALERTAS files. Caught
   by global exact-duplicate collapse. Note this required *global* dedup — the two strings are not
   adjacent, so the adjacent-only rule originally planned would have missed all 363.

Both are visible in the census: neither `body_text` nor `alerta_meta.tema_clave` appears among the
retained text paths, because in every case they were dropped as duplicates.

## §2.1 compliance check on the real output

- **Descriptive fields out of body text:** 0 violations. 30 documents do contain a `https://` inside
  their text, and all 30 were inspected: every one is a URL embedded in real prose — footnote and
  citation lines (`[1] https://www.worldometers.info/...`) or sentences like
  `"To watch the briefing and webinar on October 21, 2020 visit: https://swfound.org/"`. These are
  article content, not descriptive fields leaking. Stripping them would damage bibliographies.
- **Paragraph order preserved:** emission follows traversal order throughout; no reordering.

## Every tier-3 file, individually

All 26 examined. **None is a missing alias** — the alias table needed no additions after the real run.

**Scraper machinery, genuinely textless (14).** Correctly flagged; these are not documents in the §2.3
sense but artifacts of the download process.

| File | What it is |
|---|---|
| `AMAZONUW_tiles-index.json` | PBF tile download log, 262 entries (`tile`, `zoom`, `x`, `y`, `local_path`) |
| `CSIS_catalog-2.json`, `RUTAN_catalog-2.json`, `DEFENSA21_catalog-2.json`, `mapp_catalogo.json`, `resdal_catalogo.json` | PDF download manifests (url, status, size_bytes) |
| `CEOBS_catalog-2.json`, `SIPRI_catalog-2.json` | counter dicts (`total_publicaciones`, `pdfs_descargados`) |
| `ceeep_registro.json`, `ceobs_full_registro.json`, `mapp_registro.json`, `resdal_registro.json`, `sipri_full_registro.json` | url→local-path maps (`urls`, `hashes`, `articulos`) |
| `DEFENSA21_articulos-2.json` | empty list — the feed scrape returned `status: error` for all 5 feeds |

**Real pages whose scrape captured no content (11).** These *are* documents, but the source JSON holds
nothing to extract. The gap is upstream in the scrape, not in extraction.

| File | Evidence |
|---|---|
| `CENIA_dates.json`, `CENIA_fechas.json`, `CENIA_lineas-de-investigacion.json`, `CENIA_research-lines.json` | `sections: []`, `lists: []`, no body at all |
| `SWF_*-newsletter.json` (7 files) | body is the single boilerplate string `"Explore some of our related publications below."` (47 chars) |

**Real but thin (1).**

| File | Evidence |
|---|---|
| `ESA_space-debris-by-the-numbers.json` | one genuine 17-word paragraph: *"The latest figures related to space debris, provided by ESA's Space Debris Office at ESOC"* — real phenomenon-2 content, below the 30-word floor |

## Alias-table additions made as a result

**None.** The table built from the pre-run census covered 938 of 964 files at tier 1, tier 2 never had to
fire, and no tier-3 file turned out to be hiding text under an unrecognised key.

## Defects the real run exposed

One, in the extractor, found and fixed during this run:

- **The word floor assumed whitespace-delimited script.** `SWF_5-handbook-for-new-actors-in-space-chinese.json`
  holds 503 characters of real Chinese prose but only ~10 whitespace tokens, so `len(text.split())`
  rejected a full page as below the 30-word floor. Fixed by counting CJK ideographs individually
  (`_wc()` in `json_extract.py`); the file now extracts at tier 1. Regression fixture 16 covers it.

## Open item for a human

**12 documents are absent from the index** — the 11 empty scrapes and the 1 thin ESA page. They carry
real titles, URLs and topics, and `ESA_space-debris-by-the-numbers` is on-phenomenon for space debris.
They are excluded by `corpus.extract()`'s existing 30-word floor (`corpus.py:174`), which is global policy
for **all** formats, not a JSON decision. Lowering it, or admitting title-only documents so they remain
retrievable by title for F1@3 on `fuente`, is a corpus-wide call and was deliberately not made here.
