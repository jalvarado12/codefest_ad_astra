# Spec 02 — Migration

Concrete changes from the current state (Spec 01) to the target architecture. Ordered by
dependency. Every step ends with a runnable check.

Revised 2026-08-12 after an external audit. Claims from that audit were re-verified against
the authoritative PDF (`CODEFEST_2026-1.pdf`, 24 pp) and against the corpus; adopted findings
are marked **[A]**, and the one rejected finding is argued in Step 2.

---

## Step 0 — File hygiene — **mostly done**

- ~~`chunker.py` is 0 bytes. Delete it and rename `chunker (1).py` → `chunker.py`~~ — done.
- ~~`.gitignore`: `cache/`, `vectors/`, `entrega/`~~ — done.
- **`requirements.txt` is still unpinned and still omits `torch` and `sentence-transformers`.
  STILL OPEN**, and it is the one Step 0 item that is graded.
- **[A] Pin `requirements.txt`.** No version is currently pinned, and `torch` and
  `sentence-transformers` are missing entirely. §1.4 makes reproduction a pass/fail gate, so
  pin exact versions and record the model **revision**, not just its id.

---

## Step 1 — `chunker.py`: strip to chunking only — **DONE 2026-08-13**

**Decision 3:** block classification and cleaning are extraction concerns already handled in
`extraccion_final.py`.

> **As built.** `clean()`, `clasificar_bloques()` and `agrupar_secciones()` removed;
> `separar_bloques()` is now a plain `"\n\n"` split with no cleaning. Packing is at **sentence**
> granularity under a **dual cap** — `MAX_WORDS = 250` and `MAX_TOKENS = 506` — closing a chunk
> before the sentence that would overflow either, which is §3.3's own prescription. All eight
> Tabla-1 fields are stamped with the spec's names. The escalera has per-level counters
> (`ESCALERA_HITS`) surfaced in `run_manifest.json`. Residue is emitted intact rather than
> hard-cut; see Spec 03 §5 for why §4.3 yields to §3.3 there.
>
> Measured on the sample corpus: chunks over the encoder ceiling **29.1% → 0%**, indexed text
> silently truncated **43.8% → 0%**, max chunk 8,947 → 504 tokens, escalera residue 0.
>
> **Item 5 (overlap) was NOT built.** It remains open; see Spec 03 §B4.
>
> **One trap this uncovered.** Removing the classifier is not optional tidying. While it was
> still in place, a whole-file CSV was classified as a TITLE, and a title-only section produced
> no chunks under the new packer — three csv/xlsx documents silently disappeared from the index.
> Heuristic classification fails by deleting data, not by erroring.

### Remove

| Function | Reason |
|---|---|
| `clean()` | Cleaning. `extraccion_final.clean_text()` does this **and** strips repeated-line boilerplate. Decision 9. |
| `clasificar_bloques()` | Structure detection by word-count guesswork — any block ≤10 words became a `TITLE`. Decision 3. |
| `agrupar_secciones()` | Depends entirely on the above. |

### Keep and rework

`separar_bloques()` reduces to splitting on `\n\n`. `generar_chunks_seccion()` is the real
chunker. `procesar_documento()` stays as the API.

### Add

**1. All eight Tabla-1 fields on every chunk.** Currently `{title, text, n_words, chunk_id,
posicion}`; missing `doc_id`, `fuente`, `formato`, `fenomeno`; `texto` is named `text`;
`num_tokens` is `n_words`. `procesar_documento` must accept `formato` and `fenomeno` and stamp
all eight with the spec's names. `num_tokens` is filled at embed time (Step 4).

> **[A] Field-name trap.** `metadata.jsonl` uses **`texto`** (Tabla 1); `resultados.jsonl`
> uses **`text`** (Tabla 2). Different files, different key names. Assert both explicitly.

**2. `chunk_id` as `{i:05d}`.** Decision 8.

**3. Sentence splitting for oversized units. [MEASURED AS CRITICAL]** Port `split_sentences()`
from `origin/embedding:arch_test/chunker.py` (ES/EN/PT terminators, abbreviation list, tested).
Use only when a block exceeds `MAX_WORDS`.

> **Why this is now the highest-priority fix.** The end-to-end run on real data measured
> `num_tokens` against the 512-token encoder ceiling: **p95 = 1,141, p99 = 5,098, max = 8,947**.
> **94 of 326 chunks (29%) exceed the ceiling, and 44% of all indexed text is silently
> discarded by the encoder.** `MAX_WORDS` governs packing, but a single block larger than it is
> currently emitted whole — one 3,000-word paragraph becomes one 3,000-word chunk.
> This is a §4.3 violation and it produces no error: retrieval still returns plausible results
> from the surviving first 512 tokens while half the corpus is unsearchable. Of the over-ceiling
> chunks, 75 are PDF and 16 JSON — this fix addresses those; fix 1 in Step 2 addresses the CSV.
> **Re-measure after both land; target zero chunks over 512.**

**4. Secondary boundaries — instrumented, then trimmed. [A]** Decision 10 specified five
levels. Measured incidence is 0.105% (PDF) / 0.722% (JSON) / 0.045% (CSV), and Step 2's `\n\n`
fix dissolves the CSV case *before* the ladder is reached — so levels 3 and 4 are likely dead
on arrival.

```
1. sentence terminators   . ! ? …     (split_sentences)
2. clause boundaries      ;  :        (a clause ending at ';' is a complete phrase)
3. field separator        |
4. list markers           • - 1. a)   at line start
5. single newlines
6. hard-cut at word 250               (logged; a data problem, per user ruling)
```

**Count hits per level during the Step 6 dry run and delete every level that fires zero
times before the Colab run.** Sentences → newlines → hard-cut is the likely survivor.

Justification for the ladder existing at all — every measured offender is an extraction
artifact, not prose:

| Source | units | >250 w | worst | offenders |
|---|---|---|---|---|
| PDF (20) | 3,820 | 4 (0.105%) | 491 w | abbreviation tables, captions, org charts — newline-separated |
| JSON (60) | 1,800 | 13 (0.722%) | 386 w | long policy prose — clauses apply |
| CSV (5) | 239,700 | 107 (0.045%) | 7,193 w | `\| URL: ... \|` field runs |

**5. Overlap — 1–2 whole sentences, prose only.** Decision 2. Seed each chunk with the last
1–2 complete sentences of the previous one (~10–20% of budget); overlap counts toward the 250
and must be whole sentences (§3.3). Skip for `csv/xlsx/pbf`. This makes the strategy a hybrid
of §3.2's "por párrafo", "jerárquica" and "semántica con superposición" — permitted, but §3.2
**requires** it be justified explicitly in the technical document.

**6. Titles: catalog only. [A]** Decision 3 removed heuristic titles. The intended replacement
was `^#{1,6} ` markers from HTML and Markdown — but the corpus contains **zero HTML and zero
Markdown files** (inventory `Tipo`: JSON 954, PDF 759, Otro 74, CSV 26, Imagen 8, Excel 4,
Texto 1). That branch is dead code; do not write it. Titles come solely from the 313
catalog-matched documents (Step 2 item 5). Everything else has no title, which is correct.

### Check

`python chunker.py` self-check:

1. No chunk exceeds `MAX_WORDS` except a logged hard-cut.
2. Rejoining chunks reproduces the input, minus overlap duplication.
3. All eight Tabla-1 fields present, correct names, correct types.
4. Overlap: consecutive prose chunks share 1–2 whole sentences; csv/xlsx/pbf share none.
5. A 5,000-row CSV yields many chunks, not one.
6. **[A]** Per-level ladder counters are exposed for the Step 6 measurement.

---

## Step 2 — `extraccion_final.py` — **DONE 2026-08-13**

> **As built.** Fixes 1–7 all landed. Row joins go through the named constant `_UNION_FILAS`
> (`"\n\n"`) in `CSVExtractor`, `ExcelExtractor` and `PBFExtractor`. `PBFExtractor` now emits
> **one document per `.pbf`** and is registered in `_DISPATCH` like every other adapter, so the
> tileset grouping helpers (`_agrupar_tilesets_pbf`, `_localizar_raiz_tileset`) are gone.
> `_empaquetar` adds `nombre_archivo`, `idioma` (seeded, ≥50 words, `None` otherwise),
> `adl_doc_id` from the inventory, and flattened `catalogo_*` scalars. `_NO_CORPUS` excludes the
> three non-corpus files. `fuente` is normalised to POSIX at packing time.
>
> Verified on the sample corpus: 25 documents, **0 errors**, and the worst offender — a 25 KB
> CSV that was one 4,170-word / 8,947-token chunk — is now 23 chunks of ≤250 words.
>
> **`catalogo_*` is built but unverified**: the 25-file sample contains none of the 20 catalog
> files, so zero chunks carried those fields. It needs a full-corpus run to exercise.

**1. Row joins → `\n\n` [CONFIRMED ON REAL DATA]** in `CSVExtractor`, `ExcelExtractor`, and
`PBFExtractor`'s element lines. Without it every CSV/XLSX is a single block and a single chunk
— worst measured 7,104,513 words — which the encoder truncates to 512 tokens, leaving the file
effectively absent from the vector while still present in `metadata.jsonl`. §2.1 prescribes the
fix: *"Cada fila puede tratarse como una unidad de fragmentación independiente."*

> Observed in the end-to-end run: a 25 KB CSV produced a **single 4,170-word / 8,947-token
> chunk**, of which the encoder read 512 tokens — about 94% of that file discarded. It was the
> worst offender in the entire sample index. See Step 1 fix 3 for the companion measurement.

**2. One document per `.pbf`. [A] — behaviour change, flagged**

Currently a tileset collapses to one document whose `fuente` is a *directory*, matching no
inventory row. §2.3 is explicit: *"un documento corresponde a un archivo individual provisto
por ADL"*, and the inventory lists all 73 `.pbf` tiles as separate rows with their own
`DOC_ID`. Under §10.2.1 (matching on `fuente`), a directory-named document scores zero on
every PBF item in the ground truth.

Counter-argument, stated honestly: §2.1's *"quedarse con una sola versión para no duplicar la
data"* only makes sense **across** files, since one `.pbf` is one tile at one zoom level. The
spec is in genuine tension with itself here.

Resolved in favour of §2.3 on risk asymmetry: 73 near-duplicate documents cost precision;
one unmatchable document scores zero. **Emit one document per `.pbf`, deduplicating elements
within each file.** Measure cross-tile duplication during the dry run and report it.

**3. `fuente` stays the relative POSIX path — audit finding rejected. [rejected]**

The audit proposed `fuente` = bare basename, on the grounds that Tabla 1 says *"Nombre o URL"*
and that "basename is unique for every non-PBF row; all 186 collisions are PBF tiles."

The wording is right; the supporting fact is not. Measured: of 186 rows sharing a basename,
**72 are PBF and 114 are PDFs** across 47 distinct names — CSET Georgetown reports where the
same filename exists under `pdfs/Reports` and `pdfs/Translation`. Those are different
documents (an original and its translation). Basename would collapse 114 documents into 47
ambiguous keys.

Decisive asymmetry: **path → name is derivable; name → path is not.** A grader matching on
name or suffix still finds the name inside the path. So emit all three, since §3.4 permits
extras and the unique one is the graded field:

| field | value | why |
|---|---|---|
| `fuente` | `F1_.../pdfs/Reports/CSET_....pdf` | unique for all 1,826 rows; the graded key |
| `nombre_archivo` | `CSET_....pdf` | **[A]** covers an exact-basename grader |
| `adl_doc_id` | `F1-CSET-014` | **[A]** ADL's own id, free to carry |

Normalise to forward slashes.

**4. Language detection.** Decision 7: `DetectorFactory.seed = 0`, once per document on
`texto_limpio`, `None` under ~50 words. Chunks inherit.

**5. Exclude non-corpus files:** `Extracto_Preguntas_50_v2.pdf` (the evaluation queries),
`Indice_Datos_Codefest.xlsx`, and `FASE ORDENADA CODEFEST.xlsx` (admin file inside
`F3_Dinamicas_Territoriales/`, absent from the inventory, found by Step 3).

**6. Flatten catalog scalars.** Decision 12: promote `title`, `year`, `country`, `authors`
into per-chunk `catalogo_*` fields for the 313 matched documents. Full `metadata_catalogo`
stays document-level in `cache/textos.jsonl`.

**7. [A] Route extraction failures to `errores.jsonl`.** `on_error` already exists but the
plan never said what consumes it. One bad PDF must not kill a 90-minute run.

### Check — [A] mechanical, not eyeballed

Step 2 deliberately changes CSV/XLSX/PBF output, so "re-run the parity harness" fails by
design. Define it as **normalised equality**: for those three formats
`old.replace("\n", "\n\n") == new`; byte-parity for all others. Then assert every `fuente` is
POSIX; `idioma` set or explicitly `None`; no excluded file present; 73 PBF documents emitted.

---

## Step 3 — Inventory reconciliation — **DONE 2026-08-13**

> **As built.** `inventario.py`. `cargar_inventario()` returns `fuente -> row` keyed on
> `Carpeta` + `Nombre estandarizado`; `reconciliar(corpus_dir)` returns matches, inventory rows
> with no file, and files with no row; `python inventario.py <corpus_dir>` prints the report and
> `python inventario.py selftest` checks the key against the real spreadsheet. Column lookup is
> accent- and case-insensitive so an encoding wobble cannot silently produce an empty
> reconciliation. `generar_documentos(..., inventario=...)` consumes it to stamp `adl_doc_id`.
>
> On the 25-file sample: **25 matched, 0 files without a row.** Run again on the full corpus —
> that direction (rows without files) is only meaningful when the tree is complete.
>
> **Correction to the figure below:** the 186 basename collisions span **59** distinct names,
> not 47. The conclusion is unchanged and if anything stronger.

Measured baseline (2026-08-12), so the check starts from known ground:

| | count |
|---|---|
| Inventory rows | 1,826 |
| Unique `Carpeta` + `Nombre estandarizado` | 1,826 — **the join key** |
| Unique `Nombre estandarizado` alone | 1,699 — 186 collisions (72 PBF, **114 PDF**) |
| File paths on disk | 1,848 |
| Inventory rows present on disk | 1,826 (all) |
| Disk paths absent from the inventory | 22 |

Those 22: 9 `.DS_Store`, ~10 catalog/registry JSONs (already in `CATALOG_FILES`), and the 3
non-corpus files above.

**3a — filesystem reconciliation.** Before extraction. Join on
`Carpeta + "/" + Nombre estandarizado`, **never the basename**. Report both directions;
expect zero unexplained entries after the exclusions.

**3b — `fuente` validation.** After Step 4, on real output. Every `fuente` must equal an
inventory path. With Step 2 item 2, the PBF exemption disappears — all 73 tiles now match
rows directly. The only remaining exemption is **empty documents**, which never reach
`metadata.jsonl`.

**What it does not buy:** consistency with ADL's naming, not proof the grader matches. §10.2.1
says *"el archivo original provisto por ADL"* without specifying the form. Carrying all three
keys (item 3) is the hedge.

**`fenomeno` from the inventory**, mapping `F1`/`F2`/`F3` → `1`/`2`/`3`. Authoritative, and it
covers files the path regex misses.

---

## Step 4 — `pipeline_final.py` (new) — **DONE 2026-08-13**

> **As built.** Both caches, checkpointing, resume, `errores.jsonl`, `run_manifest.json`, and an
> 11-check `selftest` that runs entirely offline against a deterministic fake encoder — no GPU,
> no 2.2 GB download — so the bookkeeping that actually breaks is exercised on every commit.
>
> Two deviations from the sketch below, both deliberate:
>
> - **Encoding is owned here, with the revision pinned.** `embeddings_only.E5Dense` loads
>   `SentenceTransformer(E5_MODEL)` with **no `revision=`**, and §1.4 makes reproduction a
>   pass/fail gate. Depending on it would import that gap.
> - **`np.save` wholesale at each checkpoint**, not `open_memmap`. The spec says pick one and
>   say so: a wholesale rewrite keeps `dense.npy` and `ids.json` consistent at every flush,
>   whereas a memmap left mid-write by a dead process leaves a torn file. 450 MB is seconds.
>
> `ids.json` carries `hashes` alongside `ids`, which is what makes the two reuse paths
> distinguishable — a valid **checkpoint prefix** of the current order versus a **text-hash**
> match anywhere in the previous run. The manifest reports `reanudados`, `reusados` and
> `codificados` separately; conflating them hides a broken cache behind a plausible total.

```
1. walk corpus (excluding the 3 non-corpus files)
2. per file: sha256 -> hit cache/textos.jsonl? reuse : extract, detect language, write
3. per cached doc: chunker.procesar_documento(...) in memory   # never persisted
4. select chunks whose sha256(texto) is absent from ids.json
5. encode with E5Dense.encode_passages, batch 64
     stamp num_tokens from the live tokenizer
     checkpoint dense.npy + ids.json every ~5k chunks
6. faiss.IndexFlatIP(1024), add in ids.json order, in slices
7. metadata.jsonl by walking ids.json
```

Flags: `--corpus`, `--encoder`, `--sample N`, `--rebuild`, `--out`, `--batch-size`.

### [A] Cache 2 keyed on `sha256(chunk text)`, not `chunk_id` + fingerprint

The fingerprint scheme re-encoded all ~110k chunks on **any** chunker or parameter change.
Keying on the text hash means only genuinely changed chunks re-encode, the whole
fingerprint-wipe mechanism disappears, and Spec 03's overlap / `MAX_WORDS` sweeps become
cheap. Simpler and cheaper. `ids.json` still stores `chunk_id` in row order — the ordering
invariant is untouched.

### [A] Mechanical details the previous draft glossed

- **`batch_size`**: `E5Dense.encode_passages` defaults to 8. On a T4 with fp16, e5-large runs
  several times faster at 64–128. Default 64, expose the flag. The "well under an hour"
  estimate assumes this.
- **`np.save` cannot append.** Use `np.lib.format.open_memmap` for incremental writes, or
  rewrite the array wholesale (450 MB, seconds). Pick one and say so in the code.
- **`index.add` in slices**, not one call — the numpy array and FAISS's copy coexist at peak
  (~900 MB).
- **`write_vectors` ownership.** `embeddings_only.write_vectors` writes a flat
  `{model_key}_dense.npy` with no `ids.json` and no append, so it does **not** fit this layout.
  `pipeline_final` owns vector persistence; `write_vectors` is unused here. Two half-owners of
  one file is how ordering invariants rot.
- **Checkpointing.** 110k chunks with a single append at the end loses everything to a Colab
  disconnect at 90%. Flush every ~5k and resume from `len(ids)`.

### [A] `run_manifest.json`, written once per run

Files walked / excluded / extracted; OCR-triggered count; documents with zero chunks; chunks
per format; hard-cut occurrences; per-ladder-level hit counts; `num_tokens`
p50/p95/p99/max; wall time per stage; library versions; model revision.

This single file supplies every number Spec 03 asks to be measured, plus the raw material for
the technical document. Stage timing matters most for OCR — 50 PDFs at minutes each is the
long pole, and without timing you cannot tell a stuck run from a slow one.

### Check — `python pipeline_final.py selftest`

1. Two identical runs → second reports `0 extracted, 0 encoded`; `dense.npy` and `ids.json`
   byte-identical.
2. Edit one document → only it re-extracts.
3. Change chunk text → only affected chunks re-encode; unchanged ones reuse vectors.
4. **Alignment (§1.4):** for every `i`, `ids.json["ids"][i]` == line `i`'s `chunk_id` in
   `metadata.jsonl`; `index.ntotal == len(ids) == line count`.
5. **[A] Resume path.** Encode half, kill, resume — then re-assert 4. Two clean full runs do
   not exercise the path that actually corrupts ordering.
6. `faiss.read_index()`, query a known chunk's own vector → rank 1, ≈1.0.
7. Every line carries all eight Tabla-1 fields with `texto` (not `text`), integers as integers.
8. Every `fuente` is POSIX and appears in the inventory.

---

## Step 5 — `generador.py` — **BUILT AND VERIFIED 2026-08-12**

Filename mandated by §1.4. Must reproduce standalone: *"Si no es posible reproducir los
resultados, se excluirá de la evaluación."*

> **Status: done.** `generador.py` and `colab_generador.sh` are at the repo root.
> `python generador.py selftest` passes all 11 checks (exit 0), re-run in the main repo rather
> than taken on trust.
>
> Two executor agents built this independently in separate worktrees, and the comparison
> caught a real defect. Both pinned the same model revision
> (`3d7cfbdacd47fdda877c5cd8a79fbcc4f2a574f3`, each verified live against the HF API) and both
> independently added a stub-encoder flag — necessary because `sentence-transformers` cannot
> import on this box, so it is the only way to exercise the standalone subprocess path locally.
>
> They diverged on sentence splitting. The winning implementation ports the abbreviation-aware
> `split_sentences` from `origin/embedding:arch_test/chunker.py`; the other used a plain regex
> split. On `El Dr. Perez lo advirtio en 2024, junto a EE.UU. y otros actores.` the plain
> version cuts after `El Dr.`, **violating §3.3**. Both selftests passed — the losing fixtures
> simply contained no abbreviations. Keep the abbreviation list; do not "simplify" it.
>
> Deliberate deviation from the literal CLI below: a stub-encoder flag, defaulting off so the
> graded invocation is unchanged.
>
> Known spec ambiguity, not implemented: §9.2.1's clause about concatenating adjacent
> sub-250-word fragments is permissive (*"puede concatenarse"*), not mandatory, and nothing
> tests it.

### [A] Self-contained — the delivery must not import from the repo

`entrega/` ships `generador.py` plus the artifacts. Importing `E5Dense` from
`embedding_pipeline/embeddings_only.py`, which is **not delivered**, fails the §1.4 gate on a
clean checkout. **Inline the ~30-line E5 wrapper into `generador.py`.** Pin the model id *and*
revision; note the ~2.2 GB HuggingFace download in the technical document.

### CLI

```
python generador.py --index ... --metadata ... --queries Extracto_Preguntas_50_v2.pdf \
                    --out entrega/resultados.jsonl --k 50 --threshold -1.0
python generador.py selftest
```

### `cargar_consultas(path)`

Isolated so a different delivered format costs one edit. PDF: PyMuPDF text, regex
`^(q\d{3})\s+(.*)$`, join continuation lines. Also accept `.jsonl` / `.csv`. Assert exactly 50,
`q001`–`q050`, no duplicates.

### `cargar_metadata(path)`

One `json.loads` per line, kept as a **positional list** — position *is* the FAISS id. Never
key it by `chunk_id`; that discards the invariant the design rests on.

### Search

`index.search(qvec, k)` with `k = 50`.

> **[A] Guard `-1`.** FAISS returns `-1` when fewer than `k` neighbours exist, and `meta[-1]`
> silently returns the *last* chunk rather than raising. `if idx < 0: continue` before every
> lookup, with a test.

Query encoding uses the **`"query: "`** prefix (`encode_queries`), never `encode_passages`.
Wrong prefix degrades every result with no visible error.

### `agregar_documentos(hits) -> 3 doc_ids`

Decision 4: **max-pooling** — document score is its single best chunk. Sum was rejected on
measured grounds: with tables indexed, one CSV can contribute hundreds of top-50 chunks and
win on count alone.

> **[A] Deterministic tie-break.** Sort by `(-score, chunk_id)`, here and in fragment
> selection. The index is built fp16-on-GPU; a grader re-running on CPU gets fp32 and
> near-ties reorder. This is the difference between "reproduces" and "mostly reproduces".

### `seleccionar_fragmentos(hits) -> 10 fragments`

Emit `{rank, chunk_id, doc_id, text}` in score order. After Step 1 essentially every chunk is
≤250 words, so this is mostly pass-through; keep the §9.2.1 split path for the residue —
sub-fragments keep the **original `chunk_id`** and each takes its own rank slot. Word count is
`len(text.split())`; the spec says *palabras*.

> **[A] Bound the widen-`k` retries.** "Fewer than 3 documents → widen `k` and retry" has no
> ceiling and loops forever on a small index or a query whose hits are all one document. Cap
> at `index.ntotal`, then pad deterministically and log it.

### Threshold θ

Decision 6: tunable, **default `-1.0`** — not `0.0`. Cosine on normalised vectors spans
`[-1, 1]`, so `0.0` silently prunes every negative-similarity hit and is not "off". **[A]**
Prune below θ, then backfill from the unfiltered ranking so the 3/10 quota never breaks
(§9.3.2).

### Check — `python generador.py selftest`

1. Exactly 50 lines; exactly 3 `documents` and 10 `fragments`; ranks `1..3` / `1..10`.
2. Every `fragments[i].text` ≤ 250 words, including after splitting.
3. Sub-fragments of one chunk report the parent `chunk_id`.
4. Queries route through `encode_queries`.
5. Loader returns exactly `q001`–`q050`.
6. Max-pooling: one 0.9 chunk outranks fifty 0.7 chunks.
7. θ high enough to filter everything → still exactly 3 and 10.
8. **[A]** FAISS `-1` → no crash, no wrong chunk.
9. **[A]** `k` capped at `ntotal` terminates.
10. **[A]** Output uses `text`; `metadata.jsonl` uses `texto`. Assert both key names.
11. **[A] Standalone delivery:** copy `entrega/` to an empty directory, run it there, assert
    50 valid lines. This is literally the §1.4 gate.

---

## Step 6 — Local dry run

`--sample 40`, stratified across formats and F1/F2/F3, including a catalog file and a tier-3
document. Chunk boundaries here are byte-identical to Colab's, since chunking is word-based.

**Sample away from the 50 OCR PDFs** unless deliberately testing that path — at a measured
83 s/page they dominate the wall clock of an otherwise fast rehearsal. Prefer running this
after Colab pass A (Step 7), when the text cache already holds every OCR result.

**[A] Two measurements that must happen here, not after Colab:**

1. **`num_tokens` distribution.** §4.3: *"los fragmentos deben diseñarse para no superar dicho
   límite"* (512 tokens). `MAX_WORDS` is the one parameter whose change invalidates every
   downstream artifact, so if p99 approaches 512, lower it **before** the full encode.
2. **Ladder level hit counts** — delete every level that fires zero times (Step 1 item 4).

**[A] Also before Colab: inspect the 7.1M-word CSV.** It is ~43% of tabular volume and ~26%
of the entire index. If it is a coordinate dump rather than prose it does not belong in a
semantic index, and removing it cuts a quarter of the encode. Minutes of looking; do it first.

---

## Step 7 — Colab, in two passes

**Corrected 2026-08-12: the dependency runs the other way round.** An earlier draft said
"carry `cache/textos.jsonl` to Colab", which assumes the cache is built locally first. OCR is
measured at **83 s/page over 613 pages ≈ 14 hours on this CPU** — absurd for a file a T4
produces in about an hour. The cache is an *output* of Colab, not an input to it.

### Pass A — extraction on GPU

Run `pipeline_final.py` extraction only, with `gpu_ocr=True`. No code change needed:
`PDFExtractor.__init__` already accepts `gpu_ocr: bool = False` and threads it into
`_obtener_lector_easyocr`. Just set the flag.

Produces `cache/textos.jsonl`; download it. A few hundred MB of text, gzips well.

**GPU time is measured at full coverage, not estimated: 6.81 s/page on a T4, all 613 pages of
all 50 files in 69.5 min — a 12.2× speedup over the 83 s/page CPU baseline.** Per-file records
in `specs/ocr-gpu-results.jsonl`. Zero empty results, zero errors, 300,631 words extracted.

**Operational warnings, all hit for real during that run:**

- `MSYS_NO_PATHCONV=1` is required or Git Bash rewrites `/content/...` into
  `C:/Program Files/Git/content/...` and every upload 500s. With it set, `PYTHONPATH` for the
  `_winshim` must be given in **Windows form**, since it stops converting too.
- The contents API does **not** create intermediate directories — `mkdir` the remote path
  first or uploads 500.
- A single 327 MB zip upload was reset by the proxy. Upload per file instead: ~3 MB median,
  ~4 s each, and a failure costs one file.
- **The VM can be torn down without warning**, wiping `/content`. Write results incrementally
  (one line per file) and pull them down periodically; the job then resumes by skipping
  completed work. This design is what made the run recoverable.
- **A vanished session does not mean the run failed.** During the OCR pass the session went
  404 and the results file disappeared, which looked like a mid-run recycle at 27/50. The job
  had in fact completed all 50 files and the polling download simply raced the teardown.
  Always check the job's own stdout before concluding a run died.
- After teardown the dead session lingers as an **orphaned GPU assignment** that `colab stop`
  cannot kill by name or id, and new T4 requests fail with
  `TooManyAssignmentsError: Precondition Failed` (412). Clear it from the Colab web UI
  (Runtime → Manage sessions). Budget for this; it recurs on this project.

### Local — everything downstream

With the cache in hand, chunking, self-checks, `generador.py` and the Step 6 dry run all run
locally at **zero OCR cost and no GPU**. This is the point of splitting: the chunker is still
being fixed, so the expensive artifact should be pinned and stable while chunking churns.

The `sha256` key makes the cache environment-independent, so moving it between machines is
safe by construction.

### Pass B — encode and index on GPU

Real `multilingual-e5-large`, batch 64, full corpus → `dense.npy`, `ids.json`, `index.faiss`,
`metadata.jsonl`. Wipe only `vectors/`; never the text cache. Then run `generador.py` against
the delivered artifacts.

**[A] Ship `doc_registry.json`** with the delivery, or state that `doc_id`s are reproducible
only from the same sorted walk. `extraccion_final.py:514` sorts `rglob`, so assignment *is*
deterministic — a real reproducibility property worth claiming.

---

## Order

```
0 hygiene ─► 1 chunker ─► 2 extractor ─► 3a inventory ─► 4 pipeline_final ─► 3b fuente check
                                             │                                      │
                                             │                    5 generador ──────┤
                                             ▼                                      │
                              7A Colab: extract on GPU                              │
                                 └─► cache/textos.jsonl ──────────────► 6 dry run ◄─┘
                                                                             │
                                                                             ▼
                                                        7B Colab: encode + index
```

Steps 1 and 2 are independent. Step 5 needs only `metadata.jsonl`'s shape, so it can be built
against a synthetic index in parallel.

**Pass 7A can start as soon as Step 2 is done** — extraction does not depend on the chunker,
so the 14-hour-equivalent OCR work overlaps with chunker development instead of blocking it.
Step 6 then runs locally against a cache that already contains every OCR result.
