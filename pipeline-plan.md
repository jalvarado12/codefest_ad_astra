# Pipeline plan — v4 (all decisions settled)

CODEFEST AD ASTRA 2026, Etapa 1. Updated 2026-08-12.

This file is now the **index**. Detail moved into three specs so each has one job:

| Spec | Answers |
|---|---|
| [`specs/01-current-state-and-target-architecture.md`](specs/01-current-state-and-target-architecture.md) | What exists and is verified today; what we are building toward; the constraints; projected scale |
| [`specs/02-migration.md`](specs/02-migration.md) | Every concrete change, in dependency order, each with a runnable check. Includes the full build spec for `generador.py` |
| [`specs/03-next-steps-and-debt.md`](specs/03-next-steps-and-debt.md) | Debt carried by the decisions, and the retrieval-only levers still available |

---

## Where things stand

**Done and verified.** `extraccion_final.py` — self-contained extraction, byte-parity with the
old `extraccion/` package across the whole corpus (964 JSON, 26 CSV, 6 XLSX, 1 TXT, 8 JPG,
15 sampled PDFs, 1 PBF tileset; zero mismatches), both self-checks passing.

**Arrived and audited.** The team's `chunker.py`. Two blocking defects, one high-severity, and
two decisions worth keeping — see below.

**Not started.** `pipeline_final.py`, `generador.py`, the technical report.

---

## The finding that reordered everything

§10.2.1: document ground-truth matching is on **`fuente`** — *"no del `doc_id` arbitrario
asignado por el equipo"*. So `doc_id` is internal traceability and `DOC-NNNN` is fine, but
`fuente` in `metadata.jsonl` is the grading key for F1@3, half the leaderboard, and it is
currently a Windows path with backslashes.

**Resolution: emit three keys.** Tabla 1 calls `fuente` *"Nombre o URL del archivo original"*,
which reads like a filename — but the bare name is **not unique**: 114 PDF rows share 47 names
(CSET reports under both `pdfs/Reports` and `pdfs/Translation`), plus 72 PBF tiles. The
relative POSIX path is unique across all 1,826 rows, and path → name is derivable while
name → path is not. So `fuente` carries the path, with `nombre_archivo` and `adl_doc_id`
alongside it — §3.4 permits extras, and the unique key is the graded one.

## External audit, 2026-08-12

An audit of these documents was re-verified clause by clause against the authoritative PDF
(`CODEFEST_2026-1.pdf`) and against the corpus. Most of it was correct and is now integrated —
items are tagged **[A]** in Spec 02.

**Adopted:** one document per `.pbf` (§2.3 vs the old tileset collapse); `generador.py` made
self-contained so it can reproduce standalone (§1.4); FAISS `-1` guard; bounded widen-`k`
retries; batch 64; carrying the text cache to Colab; cache 2 keyed on chunk-text hash instead
of a chunker fingerprint; deterministic `(-score, chunk_id)` tie-break; θ default `-1.0`;
pinned requirements; encode checkpointing; `errores.jsonl`; `run_manifest.json`; measuring
`num_tokens` at the dry run rather than after Colab (§4.3 is an obligation); a mechanical
Step 2 check; resume-path and standalone-delivery tests.

**Rejected, with evidence:** `fuente` = bare basename. The audit's supporting claim — *"all
186 collisions are PBF tiles"* — is false; 114 are PDFs. See above.

**Corrected:** the corpus has 4 XLSX files, not 6 (two were admin files), so 30 tabular files,
not 32. The 16.73M-word and 61% figures are unaffected — those two hold 1,773 words between
them.

**Investigated and rejected:** the audit's proposal to reverse decision 5 by emitting a
synthetic chunk per empty document, and a counter-proposal to lower the tier-3 floor and
recover the sub-30-word text. Characterising the population killed both — 10 of the 20 catalog
files are not in the ADL inventory at all, and the text below the floor is a navigation menu
(CENIA), a date-stamped label (SWF), or a meta-description that names figures without
containing any (ESA). **Decision 5 stands: keep void, build nothing.** Full table in Spec 03
A6.

---

## Chunker audit, in one table

Measured on real corpus text, not estimated.

| Format | docs | chunks | >250 words | max chunk | collapsed to 1 block |
|---|---|---|---|---|---|
| PDF (25 sampled) | 25 | 1,249 | **442 (35.4%)** | 1,474 w | 1/25 |
| JSON (40 sampled) | 40 | 191 | 1 (0.5%) | 632 w | 0/40 |
| CSV (all) | 26 | **26** | 23 (88.5%) | **7,104,513 w** | **26/26** |
| XLSX (all) | 6 | **6** | 3 (50%) | 208,366 w | **6/6** |
| TXT (all) | 1 | 2 | 1 | 1,421 w | 0/1 |

**Keep:** `MAX_WORDS = 250` (matches §9.2's return cap exactly, so `generador.py` mostly never
splits) and word counting instead of an injected tokenizer (local and Colab chunk boundaries
become byte-identical).

**Fix:** missing Tabla-1 fields; CSV/XLSX/PBF collapsing to one chunk each; oversized units
never being split. Details in Spec 02, Steps 1–2.

---

## Decisions (settled 2026-08-12)

| # | Decision |
|---|---|
| 1 | **Index the tables** for now. Known problem, tested and handled later. |
| 2 | **Overlap: 1–2 whole sentences, prose formats only.** Skip csv/xlsx/pbf. |
| 3 | **Strip the chunker to chunking only.** Classification and cleaning are extraction, already in `extraccion_final.py`. |
| 4 | **Max-pooling** for document aggregation. Sum is dangerous while tables are indexed. |
| 5 | **Empty documents: nothing to build.** Zero chunks ⇒ absent from the index ⇒ unretrievable. Count them. |
| 6 | **Threshold θ as a parameter**, default off, with a backfill floor so the 3/10 quota never breaks. |
| 7 | **Language: seeded, document-level, ~50-word floor**, `None` rather than a guess. |
| 8 | **`chunk_id` = `{i:05d}`.** |
| 9 | **One cleaner** — the extractor's. |
| 10 | **Oversized units: segment, don't break a rule.** Five boundary levels; residue hard-cut and logged as a data problem. |
| 11 | **`IndexFlatIP` rationale** lives in Spec 03, not here. |
| 12 | **Flatten catalog title + scalars** into chunk metadata for the 313 matched documents. |

On decision 10, the measurement that settled it. §9.2.1 and §3.3 both bind at once, so we
segment rather than pick a rule to break. Scanning the corpus for units over 250 words and
inspecting every offender:

| Source | units | >250 w | worst | offenders are |
|---|---|---|---|---|
| PDF (20) | 3,820 | 4 (0.105%) | 491 w | abbreviation tables, captions, org charts — newline-separated |
| JSON (60) | 1,800 | 13 (0.722%) | 386 w | long policy prose — clause boundaries apply |
| CSV (5) | 239,700 | 107 (0.045%) | 7,193 w | `\| URL: ... \|` field runs |

**No confirmed case of genuine >250-word prose in a single sentence.** Every offender is an
extraction artifact the five-level ladder reaches. Whatever survives is a data problem —
hard-cut, logged, and counted.

---

## Architecture, in brief

```
corpus ─► extraccion_final ─► cache/textos.jsonl ─► chunker (in memory)
                                    ▲                      │
                          cache 1: sha256                  ▼
                                              vectors/<enc>/{dense.npy, ids.json}
                                                           ▲
                                                 cache 2: chunk_id + fingerprint
                                                           │
                                              faiss.IndexFlatIP
                                                           │
                              entrega/base_vectorial/encoder_<enc>/{index.faiss, metadata.jsonl}
                                                           │
                                      generador.py ─► entrega/resultados.jsonl
```

**The invariant, and the only one with a dedicated test:** for every `i`,
`ids.json["ids"][i]` == FAISS internal id `i` == `dense.npy` row `i` == `metadata.jsonl`
line `i`. `metadata.jsonl` is generated by walking `ids.json`, never hand-maintained.

Two caches, guarding the two expensive stages: text (keyed on file `sha256` — the ~50 of 759
PDFs that actually trigger OCR, detected per document at runtime, never hardcoded) and vectors
(keyed on `sha256` of the chunk text, so only genuinely changed chunks re-encode).

**Colab runs in two passes, and the text cache is its output, not its input.** OCR measures
83 s/page on CPU (≈14 h for 613 pages) against **6.81 s/page on a T4 — 12.2× faster, the whole
corpus in 69.5 min**, both measured end to end. So extraction runs on GPU first (pass A) and
the resulting `cache/textos.jsonl` comes back for local chunking and testing at zero OCR cost;
encoding and indexing return to GPU (pass B). An earlier draft had this dependency reversed.

The T4 run also settled the empty-document question **at 100% coverage**: all 50 no-text-layer
PDFs, all 613 pages, **zero empty and zero errors**, 300,631 words extracted. Nothing joins the
Spec 03 A6 population.

Projected scale: **~110,000 chunks**, of which ~66,900 are tabular — **30** files supplying
61% of the index. That is decision 1's known cost. One CSV alone holds 7.1M words, ~26% of
the whole index; Spec 02 Step 6 inspects it before any GPU time.

Counting note: 1,848 file paths on disk, 1,826 inventory rows, 1,713 unique basenames. PBF
tiles are now one document each rather than one tileset, so document count tracks inventory
rows minus the 3 non-corpus files; the 20 catalog files yield empty documents that never reach
`metadata.jsonl`. Files, rows and documents remain three different numbers — see Spec 01.

---

## Scope

**In:** deliverable 1 (`base_vectorial/`), 2 (`resultados.jsonl`), 4 (`generador.py`).
One encoder, architecture A (`multilingual-e5-large`, dense).

**Out:** deliverable 3 (`informe_tecnico.pdf` — graded and still owed), §7 knowledge graph
(bonus), architectures B and C (deferred; the text cache is encoder-independent, so adding B
re-encodes but never re-extracts).

---

## Order of work

```
0 hygiene ─► 1 chunker ─► 2 extractor ─► 3 inventory ─► 4 pipeline_final
                                                              │
                                            5 generador ──────┤
                                                              ▼
                                                  6 local dry run ─► 7 Colab
```

Steps 1 and 2 are independent. Step 5 needs only `metadata.jsonl`'s *shape*, so it can be
built against a synthetic index before the corpus run finishes. Nothing is executed until
approved.
