# Spec 00 — Measurements

Every number the other specs rely on, with how it was obtained. Recorded 2026-08-12 so none of
this has to be re-run. Anything **not** measured is marked as such — do not treat an estimate
here as a fact.

Environment: Windows 11, Python 3.10, CPU only. `faiss` installed; `llama_index` **not**
installed (and no longer needed). Corpus root:
`C:\Users\User\Downloads\AD_Astra\CORPUS CODEFEST AD ASTRA 2026`.

---

## 1. Extractor parity — `extraccion_final.py` vs the old `extraccion/` package

Ran both implementations over the same files, compared output strings exactly.

| Extractor | files | coverage | result |
|---|---|---|---|
| `JSONExtractor` | 964 | all JSON | 938 identical, 26 both `JsonSinTexto`, **0 mismatch** |
| `CSVExtractor` | 26 | all CSV | 26 identical, 0 errors |
| `ExcelExtractor` | 6 | all XLSX | 6 identical, 0 errors |
| `TextoExtractor` | 1 | all TXT | 1 identical |
| `ImagenExtractor` | 8 | all JPG | 8 identical (281 s — EasyOCR) |
| `PDFExtractor` | 15 | sample of 760 | 15 identical (1,969 s for both impls) |
| `PBFExtractor` | 1 tileset / 73 files | all PBF | 1 identical |

Both built-in self-checks pass: `self-check OK (17 fixtures)` and the catalog self-check.

---

## 2. Corpus composition

Inventory = `Indice_Datos_Codefest.xlsx`, sheet `Inventario de Archivos`.

| | value |
|---|---|
| Inventory rows | 1,826 |
| Unique `Carpeta` + `Nombre estandarizado` | **1,826 — unique; this is the join key** |
| Unique `Nombre estandarizado` alone | 1,699 |
| Basename collisions | 186 rows → **72 PBF + 114 PDF** (47 distinct names) |
| File paths on disk | 1,848 |
| Unique basenames on disk | 1,713 |
| Inventory rows missing from disk | **0** |
| Disk paths absent from inventory | 22 |

The 114 PDF collisions are CSET Georgetown reports present under both
`.../CSET_Georgetown/pdfs/Reports` and `.../pdfs/Translation` — different documents, same
filename. **This is why `fuente` carries the path, not the basename.**

The 22 uninventoried paths: 9 `.DS_Store`, 10 catalog/registry JSONs, and 3 non-corpus files —
`Extracto_Preguntas_50_v2.pdf`, `Indice_Datos_Codefest.xlsx`,
`F3_Dinamicas_Territoriales/FASE ORDENADA CODEFEST.xlsx`.

Inventory `Tipo`: JSON 954, PDF 759, **Otro 74**, CSV 26, Imagen 8, **Excel 4**, Texto 1.

On-disk extension counts: pdf 760, json 964, csv 26, xlsx 6, txt 1, jpg 8, pbf 73,
**html 0, md 0, xls 0**, and no other image formats. The zero HTML/Markdown count is why the
`^#{1,6} ` title branch is dead code.

Top-level folders: `F1_IA_y_Capacidades_Estrategicas`, `F2_Seguridad_Entorno_Espacial`,
`F3_Dinamicas_Territoriales`, plus the two root reference files.
`Resumen por Fenomeno`: 459 / 479 / 888 documents, 759 PDFs.

---

## 3. OCR incidence — PDFs without a text layer

Method: PyMuPDF text extraction only (no OCR), flag `len(text.strip()) < 50`.

| | value |
|---|---|
| PDFs scanned | 760 |
| **Lacking a text layer** | **50 (6.6%)** |
| Total pages across those 50 | **613** |
| Scan time for all 760 | 306 s |

Page distribution of the 50: smallest 1 p
(`CSET_center-for-security-and-emerging-technology-5.pdf`), then many 2 p `ALERTAS_informes*`,
largest 38 p (`ALERTAS_informes007.pdf`), also 36 p and 32 p.

OCR is a per-document runtime decision (`UMBRAL_TEXTO_VACIO = 50`, `extraccion_final.py:203`).
**No file list is hardcoded and none should be** — the list above is for cost estimation only.

### OCR cost — resolved. OCR emptiness — **NOT resolved; do not cite this as settled.**

Full `PDFExtractor.extraer` (text layer → OCR fallback), 6 of the 50, CPU. Raw records in
`specs/ocr-test-results.jsonl`.

| File | pages | words after OCR | time | s/page |
|---|---|---|---|---|
| `CSET_center-for-security-and-emerging-technology-5.pdf` | 1 | **3,891** | 0 s | — |
| `ALERTAS_informes006.pdf` | 2 | 594 | 146 s | 73 |
| `ALERTAS_informes009.pdf` | 2 | 724 | 166 s | 83 |
| `ALERTAS_informes011.pdf` | 2 | 749 | 176 s | 88 |
| `ALERTAS_informes043.pdf` | 4 | 1,660 | 331 s | 83 |
| `ALERTAS_informes016.pdf` | 8 | 3,760 | 684 s | 86 |

**Cost: 83 s/page on this CPU**, tight to ±10% across 1–8 page documents. Over 613 pages that
is **~14 hours**, which is why extraction belongs on GPU (Spec 02 Step 7 pass A). This figure
is converged and safe to rely on.

### GPU measurement — Colab Tesla T4, 2026-08-12. **COMPLETE, 100% coverage.**

Replaces the earlier 5–15× guess with measurement. Per-file records:
`specs/ocr-gpu-results.jsonl`. Run via `_ocrjob/ocr_gpu_job.py` with
`PDFExtractor(gpu_ocr=True)` — no code change was needed, the flag already existed.

| | value |
|---|---|
| Files | **50 of 50** |
| Pages | **613 of 613 (100%)** |
| Page sizes | 1 to 38, median 11 — the whole population, no sampling |
| **GPU throughput** | **6.81 s/page** |
| **Speedup vs CPU (83 s/page)** | **12.2×** |
| **Wall clock, full corpus** | **4,172 s = 69.5 min** |
| Empty after OCR | **0** |
| Errors | **0** |
| Words extracted | 300,631 |

**Emptiness: settled, at full coverage.** Every one of the 50 no-text-layer PDFs returned
substantial text. **No PDF in this corpus comes back empty after OCR**, and none joins the
Spec 03 A6 population. The earlier "unverified" caveat is fully discharged — this is no longer
an inference from a sample.

The rate was stable throughout (per-file values mostly 4.7–7.4 s/page; the 13.7 s/page outlier
is a 1-page file paying fixed overhead).

**Recording a mistake in how this was observed.** Mid-run the session returned 404 and the
results file vanished from `/content`, and this document briefly recorded the run as having
died at 27/50. It had not: the job completed all 50 files, printed its summary, and the
session was torn down afterwards — the polling download simply raced the teardown. The full
per-file data was recovered from the job's own stdout. **A disappeared session is not evidence
of a failed run**; check the job's output before concluding anything from a lost VM.

Two real operational hazards remain true and worth budgeting for:

- The VM can be recycled, so results must be written incrementally (one line per file) and the
  job must resume by skipping completed work. That design is what made recovery trivial here.
- After teardown the session lingers as an **orphaned GPU assignment** that `colab stop`
  cannot kill by name or id, and new T4 requests fail with
  `TooManyAssignmentsError: Precondition Failed` (412). Clear it from the Colab web UI
  (Runtime → Manage sessions).

**Emptiness: six files is weak evidence and the sample is biased.** All six returned
substantial text, but they were chosen *by page count* (the smallest first), and a PDF returns
empty after OCR when its pages are image-only with no legible text — a scanned map, a photo, a
blank cover. That is uncorrelated with page count, so this sample does not test the failure
mode at all. Treat "no empties" as **unverified**, not established.

Deliberately not pursued further, because the question is **non-blocking**: decision 5
(Spec 03 A6) leaves empty documents void either way, so an empty PDF changes no code and no
plan. Colab pass A OCRs all 50 regardless and `run_manifest.json` already records
documents-with-zero-chunks, so the answer arrives free at 100% coverage. Spending more CPU here
buys a partial answer to a question a run we are doing anyway answers completely.

**The CSET "anomaly" was a bug in the test harness, not in the pipeline — resolved.**
That row (3,891 words in 0 s) suggested `UMBRAL_TEXTO_VACIO` was mis-detecting. It was not.
Four different PDFs share the basename `CSET_center-for-security-and-emerging-technology-5.pdf`:

| folder | pages | text-layer chars | extractor result |
|---|---|---|---|
| `Data_Snapshot` | 1 | **0** | 354 words, **149.5 s** — genuinely scanned |
| `Formal_Response` | 28 | 78,560 | 9,781 words, 0.1 s |
| `Reports` | 189 | 478,604 | 54,630 words, 0.9 s |
| `Translation` | 12 | 27,883 | 3,891 words, 0.1 s |

The harness built `{p.name: p for p in rglob("*.pdf")}`, so later paths overwrote earlier ones
and it measured the `Translation` copy instead of the scanned `Data_Snapshot` one.

Consequences: **`UMBRAL_TEXTO_VACIO` is correct**, the 50-file / 613-page figure stands, and
the GPU estimate does not shrink. Confirmed independently — re-scanning all 760 PDFs with
`get_text("blocks")` (the method `PDFExtractor` actually uses) rather than plain `get_text()`
finds **0** additional files with a usable text layer.

It is also direct evidence for the `fuente`-carries-the-path decision (Spec 02 Step 2 item 3):
a basename collision silently corrupted a measurement in this very session.

Unmeasured: the 38 p, 36 p and 32 p ALERTAS documents, and GPU throughput.

---

## 4. Chunker behaviour — as delivered, **superseded 2026-08-13**

Historical: this is the paragraph-packing chunker before Step 1 and Step 2 landed. Kept because
it is the baseline every "after" figure is measured against. For current behaviour see §7a-bis.

Real extracted text, that chunker, no fixes applied.

| Format | docs | chunks | >250 words | max chunk | collapsed to 1 block |
|---|---|---|---|---|---|
| PDF (25 sampled) | 25 | 1,249 | **442 (35.4%)** | 1,474 w | 1/25 |
| JSON (40 sampled) | 40 | 191 | 1 (0.5%) | 632 w | 0/40 |
| CSV (all) | 26 | **26** | 23 (88.5%) | **7,104,513 w** | **26/26** |
| XLSX (all 6 incl. admin) | 6 | **6** | 3 (50%) | 208,366 w | **6/6** |
| TXT (all) | 1 | 2 | 1 | 1,421 w | 0/1 |

CSV/XLSX collapse because `extraccion_final` joins rows with `\n` while `separar_bloques`
splits on `\n\n`. PDFs do **not** collapse — an early assumption that they would was wrong;
a 14,636-word PDF produced 131 blocks.

---

## 5. Tabular volume and projected index scale

| | words |
|---|---|
| CSV (26 files) | 16,523,437 |
| XLSX (4 corpus files, admin excluded) | 208,564 |
| **Combined (30 files)** | **16,732,001** → ~66,928 chunks at 250 w |

Per-file XLSX: `AIINDEX_lit-covid-ai-covid-literature-xlsx.xlsx` 208,366; the other three are
15, 162 and 21 words. The two admin spreadsheets hold 1,773 words between them — excluding
them changes the file count (32 → 30) but not the volume.

**One CSV holds 7.1M words — ~43% of tabular volume, ~26% of the projected index.**

Projected total ~110,000 chunks: ~66,900 tabular (61%), ~38,000 PDF (35%), ~4,600 JSON (4%),
plus ~15% on prose from overlap. **The per-format chunk projections for PDF and JSON are
extrapolations from the samples in §4, not measurements.**

### Reprojected 2026-08-13 — the 250 w/chunk assumption was wrong for tabular

The figures above divide by 250, assuming the word cap binds. Measured with the dual-cap
chunker (`_gentest/composicion.py`), it does not: dense `columna: valor | columna: valor` rows
reach the **506-token cap first**, so a tabular chunk holds **178.2 words**, not 250.

| format | words | w/chunk | chunks | share |
|---|---|---|---|---|
| csv | 16,523,437 | 178.2 **measured** | 92,724 | 67.9% |
| pdf | ~9,500,000 | 250 *assumed* | 38,000 | 27.8% |
| json | ~1,150,000 | 250 *assumed* | 4,600 | 3.4% |
| xlsx | 208,564 | 178.2 *csv ratio* | 1,170 | 0.9% |
| **total** | | | **~136,500** | |
| **tabular** | | | **93,894** | **68.8%** |

~24% more chunks than planned, and more table-dominated: 68.8% against the projected 61%.

**Do not extrapolate a ratio from a file smaller than one chunk.** The XLSX sample is a 15-word
spreadsheet, which is 1 chunk at "15 w/chunk"; carrying that forward invented 13,904 XLSX chunks
out of nothing. `composicion.py` now requires ≥3 chunks before trusting a per-format ratio and
falls back to the CSV ratio for other tabular formats.

**Retrieval on a 100% tabular index** (26 chunks, 3 documents, real e5-large): `resultados.jsonl`
**VALID** against §9.3.1/§9.3.2 — the schema holds. The validator's sanity check fires though:
one document takes rank 1 on 26 of 50 queries. Tabular chunks retrieve; they discriminate badly.

### Conditions re-verified with overlap enabled — 2026-08-13

Sample corpus, real extraction, `OVERLAP_ORACIONES = 1`:

| | no overlap | overlap |
|---|---|---|
| chunks | 475 | **531** (+11.8%) |
| tokens p50 / p95 / p99 / max | 357 / 443 / 499 / 504 | 357 / 443 / 489 / **504** |
| chunks over the 506 cap | 0 | **0** |
| words p50 / p95 / max | 229 / 248 / 250 | 229 / 249 / **250** |
| chunks over the §9.2 250 cap | 0 | **0** |
| escalera residue | 0 | **0** |

+11.8% lands inside the "~10–20% on prose" the plan budgeted. Overlap placement is exactly as
specified: **354 prose chunk boundaries carry the previous sentence, 0 tabular ones do.**

**§3.3 spot check.** 8 of 449 prose chunks (1.8%) open with a lowercase letter. Every one was
inspected:

- **7 are PDF running headers** — `"conclusions and recommendations 33\nContinue hydrocarbon…"`,
  `"cross-cutting considerations 21\nExperience-sharing…"`. Complete header lines, not broken
  sentences. No §3.3 issue.
- **1 is an escalera clause piece**, which §3.3 permits on its own wording: it forbids
  *"oraciones o frases incompletas"*, and a clause ending at `;`/`:` is a complete *frase* —
  the same argument Step 1 item 4 uses to make clauses level 2 of the ladder.

**New extraction debt, found by that check.** `remove_repeated_lines` never strips those running
headers, because the page number makes each occurrence unique: a header repeating on 30 pages is
30 distinct lines, each seen once, so it never reaches the `min_repeats=3` threshold. Stripping a
trailing page number before counting would collapse them and remove the lot. Not fixed — it
touches every PDF and deserves its own measurement, since an over-eager rule would eat real
content. Cosmetic for compliance, real for retrieval quality.

---

## 6. Sentence-length distribution — does a real >250-word sentence exist?

Method: `split_sentences()` from `origin/embedding:arch_test/chunker.py` over real extracted
text, counting units over 250 words and **inspecting every offender**.

| Source | units | >250 w | worst | what they are |
|---|---|---|---|---|
| PDF (20) | 3,820 | 4 (0.105%) | 491 w | abbreviation tables, figure captions, org charts — newline-separated, no terminators |
| JSON (60) | 1,800 | 13 (0.722%) | 386 w | long policy prose (CSIS space-force primer) |
| CSV (5) | 239,700 | 107 (0.045%) | 7,193 w | `\| URL: ... \|` pipe-separated field runs |

**No confirmed case of genuine >250-word prose in a single sentence.** Every offender is an
extraction artifact reachable by the Spec 02 boundary ladder.

---

## 7. Catalog and empty-document population

Of the 20 names in `CATALOG_FILES`:

- **In the ADL inventory (10)** — real ADL documents: `AMAZONUW_tiles-index`, `CEOBS_catalog-2`,
  `CSIS_catalog-2`, `DAIO_catalog-2`, `DEFENSA21_articulos-2`, `DEFENSA21_catalog-2`,
  `MAPPOEA_mapp-catalog`, `RESDAL_catalog-2`, `RUTAN_catalog-2`, `SIPRI_catalog-2`.
- **Not in the inventory (10)** — not ADL documents at all: `ceeep_catalogo`, `ceeep_registro`,
  `ceobs_full_catalogo`, `ceobs_full_registro`, `mapp_catalogo`, `mapp_registro`,
  `resdal_catalogo`, `resdal_registro`, `sipri_full_catalogo`, `sipri_full_registro`.

Non-catalog tier-3 JSON, all inventoried, with the text that exists below the 30-word floor:

| Document | words | what the text is |
|---|---|---|
| `CENIA_dates` / `_fechas` / `_lineas-de-investigacion` / `_research-lines` | 28 / 27 / 29 / 29 | title + **site navigation menu** |
| `ESA_space-debris-by-the-numbers` | 22 | title + a meta-description naming figures it does not contain |
| `SWF_*-newsletter` ×7 | 10–14 | `<Month> Newsletter` + `Explore some of our related publications below.` |

Metadata present: `url` on all; SWF also has a real `date` and generic `topics`; ESA's `date`
and `tags` are present but empty. `title` is a **body** field in `json_extract`, not metadata —
it appears in the extracted text, not in `meta`.

Catalog matching (`metadata_catalogo`): 313 documents matched, 358 entries.

Conclusion recorded in Spec 03 A6: **nothing to build.**

---

## 7a. END-TO-END RUN ON T4 — 2026-08-12. **The §4.3 token measurement, and it is bad.**

First true integration test: 25-file stratified sample → `extraccion_final` → `chunker` →
real `multilingual-e5-large` on T4 → `IndexFlatIP` → `generador.py` as a subprocess → validated
`resultados.jsonl`. Sample seeded at `20260812`; artifacts in `_gentest/`.

### It works end to end

| | |
|---|---|
| Documents extracted | 25/25, **0 errors**, 381 s (dominated by OCR on 3 scanned PDFs) |
| Chunks | 326 from 23 non-empty docs (2 images correctly yielded 0 words) |
| Encoding | 326 × 1024 in **6 s** on T4 |
| Index alignment | `index.ntotal == metadata lines == 326`, **aligned** |
| `resultados.jsonl` | **VALID** against §9.3.1/§9.3.2 |

Retrieval sanity: 16 distinct documents and 98 distinct chunks returned across the 50 queries;
formats `pdf 72 / json 24 / txt 2`; fenómenos `1:22, 2:35, 3:41`; top rank-1 document appears
on 15/50 queries (below the 25/50 domination warning). Fragment words `min=2, p50=204,
max=250` — the 250-word cap is enforced exactly, so `generador.py`'s splitter works on real
oversized chunks.

### The finding: 44% of indexed text never reaches a vector

`num_tokens`, measured with the real tokenizer, against the **512-token encoder ceiling**:

| p50 | p95 | p99 | max |
|---|---|---|---|
| 295 | **1,141** | **5,098** | **8,947** |

| | value |
|---|---|
| Chunks over 512 tokens | **94 of 326 = 29%** |
| Tokens silently discarded | **71,821 of 163,815 = 44% of all indexed text** |
| Over-512 by format | pdf 75, json 16, csv 2, txt 1 |
| Words per chunk | p50 195, p95 723, **max 4,170** |

Worst offenders: one CSV chunk at **8,947 tokens / 4,170 words** (94% of it discarded), and the
CSIS space-force primer producing four chunks around 5,000 tokens each.

**Why.** `MAX_WORDS = 250` governs *packing*, but `generar_chunks_seccion` emits any single
block larger than that **whole and unsplit**. So one 3,000-word paragraph becomes one
3,000-word chunk, of which the encoder reads the first ~512 tokens and silently drops the rest.
The CSV case is worse still: rows are joined with `\n` while the chunker splits on `\n\n`, so an
entire file becomes one block and one chunk.

**This is a §4.3 violation** — *"los fragmentos deben diseñarse para no superar dicho límite"* —
and it is invisible without this measurement: retrieval still returns valid, plausible results,
because the surviving first 512 tokens are real text. Nothing errors. Nearly half the corpus is
simply not searchable.

**It confirms both pending chunker fixes are load-bearing, not cleanup:**

- Spec 02 Step 1 **fix 3** (split oversized blocks at sentence boundaries) → addresses the 75
  PDF and 16 JSON chunks.
- Spec 02 Step 2 **fix 1** (`\n\n` row joins) → addresses the CSV chunk.

After both, re-measure. Target: p99 under 512 and zero chunks over the ceiling. If p99 still
approaches 512, lower `MAX_WORDS` — **before** the full-corpus encode, since that parameter
invalidates every downstream artifact.

## 7a-bis. RE-MEASURED after the fixes — 2026-08-13. **Target met.**

Same sample corpus, real extraction, real tokenizer at the pinned revision.

| | p50 | p95 | p99 | max | over 506 | text discarded |
|---|---|---|---|---|---|---|
| before (words only) | 295 | 1,141 | 5,098 | 8,947 | 29.1% | 43.8% |
| after (dual cap) | 357 | 443 | 499 | **504** | **0 (0.000%)** | **0.00%** |

Words: p50 229, p95 248, max 250, **zero** chunks over the §9.2 cap — against 114 of 326 before.
Documents reaching the index: 25 of 25.

**`MAX_WORDS` was not lowered, and lowering it would not have worked.** 100% of the
over-ceiling chunks were in the unsplit-block path, so a smaller packing budget would not have
touched a single one. The fix had to be splitting, not shrinking.

**Escalera hit counts** (Step 1 item 4 asks for these before deciding which levels to delete):

| level | hits |
|---|---|
| 1 clauses `;` `:` | 17 |
| 2 field separator `\|` | 1 |
| 3 list markers | 1 |
| 4 single newlines | 0 |
| 5 commas | 0 |
| residue (emitted intact) | **0** |

Levels 4 and 5 have not fired. 25 files is too thin a sample to retire them on; decide at the
full-corpus dry run. `run_manifest.json` records these on every run.

**A measurement bug found while doing this.** `num_tokens` was counted with
`add_special_tokens=False` and without the `"passage: "` prefix, so every figure in §7a
understates the encoder's real input by 6 tokens. The stored field still counts raw content —
that is what Tabla 1 describes — but the cap now reserves those 6, giving 506 rather than 512.

**Inventory reconciliation** (Step 3), same sample: 1,826 rows, 25 files, **25 matched, 0
files without a row**. The join key `Carpeta` + `Nombre estandarizado` is unique across all
1,826 rows. Correction to Spec 02 Step 3: the 186 basename collisions span **59** distinct
names, not 47.

## 7b. `generador.py` — built and verified 2026-08-12

Two executor agents implemented Step 5 independently in separate worktrees; the better one was
promoted to the repo root and its selftest re-run there rather than taken on trust.

- `python generador.py selftest` → **all 11 checks pass, exit 0**, in the main repo.
- Real `Extracto_Preguntas_50_v2.pdf` parses to **50 contiguous queries q001–q050**, none
  truncated across page or line breaks. UTF-8 verified at codepoint level (`U+00BF` ¿,
  `U+00F3` ó, `U+00E1` á) — mojibake seen in terminal output is the Windows console codepage,
  not the data.
- Model pinned to `intfloat/multilingual-e5-large` revision
  `3d7cfbdacd47fdda877c5cd8a79fbcc4f2a574f3`. **Both agents derived this independently and
  verified it live against the HF API** — strong cross-validation.

**The defect the comparison caught.** The two implementations differed on sentence splitting.
Given `El Dr. Perez lo advirtio en 2024, junto a EE.UU. y otros actores. Nadie lo sabe aun.`:

| implementation | output |
|---|---|
| abbreviation-aware (ported from `arch_test/chunker.py`) — **promoted** | `['El Dr. Perez ... otros actores.', 'Nadie lo sabe aun.']` |
| plain regex split — rejected | `['El Dr.', 'Perez lo advirtio ...', 'Nadie lo sabe aun.']` |

The plain version cuts mid-abbreviation, **violating §3.3**. Both selftests passed; the losing
fixtures simply contained no abbreviations. Keep the abbreviation list.

## 8. Spec source

`ad_astra.md` does not exist on disk — it lives on the `embedding` branch
(`git show origin/embedding:ad_astra.md`). The authoritative PDF is
`C:\Users\User\Downloads\CODEFEST_2026-1.pdf`, 24 pages.

Verified verbatim-identical between the two on: Tabla 1 `fuente` (*"Nombre o URL del archivo
original provisto por ADL"*), §2.1 PBF dedup, §2.3 (*"un documento corresponde a un archivo
individual"*), §4.3 (*"los fragmentos deben diseñarse para no superar dicho límite"*, 512
tokens). The markdown conversion is trustworthy.

Queries file `Extracto_Preguntas_50_v2.pdf`: 3 pages, format `qNNN <question>` wrapped across
lines, Spanish. Grouped by phenomenon (q001–q022 ≈ F1, q023–q032 ≈ F2, q033+ ≈ F3) — a
grouping that must **not** be used as a filter (Spec 03 B3).

---

## 9. Repo state gotchas

- ~~`chunker.py` at the repo root is 0 bytes~~ — resolved; `chunker.py` is the real, rewritten
  chunker.
- `documentos_easyocr.jsonl` is **0 bytes** — contains no results despite the name.
- `requirements.txt` pins **no versions** and omits `torch` and `sentence-transformers`.
  **Still open.** §1.4 makes reproduction pass/fail, so this is a real risk, not tidying.
- `embeddings_only.E5Dense.encode_passages` defaults to `batch_size=8`; use 64 on GPU.
- **`embeddings_only.E5Dense` does not pin the model revision** — it calls
  `SentenceTransformer(E5_MODEL, device=...)` with no `revision=`. `pipeline_final.py`
  therefore owns encoding itself rather than depending on it.
- ~~`sentence-transformers` / `FlagEmbedding` do not import on this Windows box (pyarrow trips
  a Windows Application Control policy), so real encoding is Colab-only.~~ **Wrong, and it
  cost real design decisions.** `pip install pyarrow` fixes the import; both the tokenizer and
  the full e5-large model run locally on CPU. This false belief is the sole reason chunking
  budgeted on words alone, which is what let 44% of the indexed text be silently discarded.
  Verify a claimed environment limitation before designing around it.
