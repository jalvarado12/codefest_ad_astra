# Pipeline audit — 2026-08-12

Read all four: `pipeline-plan.md`, three specs, and the real spec source (`CODEFEST_2026-1.pdf`, 24 pp — no `ad_astra.md` exists on disk; audited against the PDF). Also checked `embeddings_only.py`, `extraccion_final.py` walk order, `requirements.txt`, and the inventory workbook.

# Blocking first

**B1. `fuente` bet is inverted — highest-value fix in the plan.**
Tabla 1: *"fuente | cadena | **Nombre o URL** del archivo original provisto por ADL."* Inventory column is literally `Nombre estandarizado` = bare filename (`AIINDEX_ai-index-2024-ch1-research-development.pdf`), with `Carpeta` a separate column. Plan puts the **relative path** in `fuente` (the graded key) and the basename in `nombre_archivo` (a field the grader never reads). That is the hedge pointed the wrong way.

Basename is unique for every non-PBF row (1,699 unique names; all 186 collisions are PBF tiles). Swap: `fuente` = basename, `ruta` = POSIX relative path as extra. Also emit `adl_doc_id` — the inventory's `DOC_ID` column is a formula `=F{n}-{CODIGO}-{NNN}`, so ADL's own id is recoverable and free to carry. Three keys, all covered, §3.4 permits extras.

**B2. PBF collapse guarantees F1@3=0 for 73 documents.**
§2.3: *"un documento corresponde a un archivo individual provisto por ADL"*, and the inventory lists each `.pbf` tile as its own row. Plan emits one tileset document whose `fuente` is a **directory** matching no row — then Spec 02 §3b *exempts* that from validation rather than fixing it. §2.1's *"quedarse con una sola versión"* scopes to elements repeating across zoom levels **"por cada archivo"** — dedup inside a file, not collapse across files. Fix: one document per `.pbf`, dedup attributes within each. Minimum fix if you keep the tileset: set its `fuente` to a real member tile name.

**B3. `generador.py` cannot reproduce standalone.**
§1.4: *"Si no es posible reproducir los resultados, se excluirá de la evaluación."* `entrega/` ships only `generador.py` + artifacts, but Step 5 calls `E5Dense()` from `embedding_pipeline/embeddings_only.py`, which is not delivered. Inline the ~30-line E5 wrapper into `generador.py`. Pin model id **and revision**; state the 2.2 GB HF download in the informe.

**B4. FAISS `-1` silently returns the wrong chunk.**
Step 5 mandates metadata as a **positional list** — correct — but FAISS returns `-1` when fewer than `k` neighbors exist, and `meta[-1]` is the last chunk, not an error. Guard `if idx < 0: continue` before every lookup. Test it.

**B5. Unbounded widen-`k` retries.** "Fewer than 3 documents → widen `k` and retry", same for 10 fragments. No ceiling → infinite loop on a small index or a query where all hits are one doc. Bound at `index.ntotal`, then pad deterministically and log.

---

# 1. Efficiency

Sound at the macro level (~110k vectors, flat search trivial), weak at three concrete points.

- **`batch_size=8` default** in `E5Dense.encode_passages`. On a T4 with fp16, e5-large runs 3–5× faster at 64–128. At batch 8 the "well under an hour" claim for 110k chunks is not defensible. Set batch from a flag, default 64.
- **Step 7 wipes `cache/textos.jsonl` on Colab.** Spec 01 justifies cache 1 by *"the ~50 OCR PDFs (minutes each)"* — then Step 7 discards exactly that. Self-inflicted hours of EasyOCR. The sha256 key **is** the staleness guard; carry the text cache to Colab, wipe only `vectors/`.
- **The 7.1M-word CSV is ~43% of tabular volume ≈ 26% of the entire index.** Spec 03 A1 defers inspecting it until after results exist. Invert the order: one file, minutes of looking, before the Colab run. If it's a coordinate dump it should not be in a semantic index at all, and cutting it removes a quarter of the encode.
- Cache 2 keyed on `chunk_id + fingerprint` means **any** chunker or parameter change re-encodes all 110k chunks. Key on `sha256(chunk text)` instead: the fingerprint-wipe machinery disappears and Spec 03 B4's overlap/`MAX_WORDS` sweeps re-encode only what actually changed. Simpler *and* cheaper.
- `np.save` cannot append. Step 4's "append to `dense.npy`" needs `np.lib.format.open_memmap` or a full rewrite (450 MB, seconds — fine, just say which).

# 2. Complexity

Generally well-cut. Decision 3 (strip chunker to chunking) and decision 9 (one cleaner) are the right deletions. Two places carry more machinery than the measurements justify.

- **The five-level segmentation ladder** is built for a measured incidence of 0.105%/0.722%/0.045%, and Step 2's `\n\n` row fix dissolves the CSV case *before* the ladder is reached. Levels 3 (`|`) and 4 (list markers) are probably dead on arrival. Instrument per-level hit counts in the Step 6 dry run and delete every level that fires zero times before the Colab run. Sentences → newlines → hard-cut is likely the whole ladder.
- `embeddings_only.write_vectors` writes `{model_key}_dense.npy` flat, no `ids.json`, no append — it does not fit Step 4's layout. Either adapt it or state plainly that `pipeline_final` writes vectors itself and `write_vectors` is unused. Don't leave two half-owners of the same file.
- Two caches, the ordering invariant, and the adapter note ("flatten at the call site, do not restructure the chunker") are all correctly minimal. No changes.

# 3. Reproducibility

Strongest dimension. Word-based chunking (tokenizer-free → byte-identical local/Colab boundaries), seeded `DetectorFactory`, sorted `rglob` in `extraccion_final.py:514` (doc_id assignment is deterministic — the plan doesn't claim this; it should, it's a real property).

Gaps:

- **Score determinism across environments.** The index is built fp16-on-GPU; a grader re-running `generador.py` on CPU gets fp32 and slightly different similarities. Ties and near-ties reorder. Add an explicit tie-break — sort by `(-score, chunk_id)` in both `agregar_documentos` and `seleccionar_fragmentos`. Cheap, and it's the difference between "reproduces" and "mostly reproduces".
- **`requirements.txt` has zero version pins** and omits `sentence-transformers` and `torch`. §1.4 makes reproduction a pass/fail gate. Pin exact versions; the informe needs the list anyway.
- **θ default `0.0` is not "off".** Cosine on normalized vectors ranges [−1, 1], so `0.0` prunes every negative-similarity hit. Use `-1.0` (or `None`) for genuinely off.
- Ship `doc_registry.json` alongside the delivery, or note that doc_ids are reproducible only from the same sorted walk.

# 4. Scalability

Correctly sized and correctly argued. `IndexFlatIP` at 110k vectors is right; §5.2 says so explicitly and Spec 03 A10 records the reasoning for the informe. `dense.npy` 450 MB, `metadata.jsonl` 165 MB, generador's positional list ~500 MB RAM — all fine on a Colab instance.

The real limits are operational, not algorithmic:

- Peak RAM during index build holds the numpy array **and** the FAISS copy simultaneously (~900 MB). Fine, but add vectors in chunks rather than one `index.add(dense)`.
- Architecture B (BGE-M3) doubles the encode but reuses text and chunks by construction — correctly stated in Spec 03 B1, and the tokenizer-free chunking is what makes the A/B comparison fair. Good.
- Scale table arithmetic is off: the inventory has **4** corpus XLSX rows, not 6 — the other two are `Indice_Datos_Codefest.xlsx` and `FASE ORDENADA CODEFEST.xlsx`, both on the Step 2 exclusion list. "32 CSV/XLSX files" → 30. The 16.7M-word and 61% figures inherit the error, and they are quoted in the graded informe.

# 5. Robustness & fault tolerance

Weakest dimension. B4 and B5 above live here; three more:

- **No checkpointing during the encode.** 110k chunks, single append at the end. A Colab disconnect at 90% loses the whole run. Flush `dense.npy` + `ids.json` every ~5k chunks and resume from `len(ids)`.
- **Empty documents are treated as "nothing to build"** (decision 5). But the 20 catalog files and tier-3 JSON *are* ADL documents; if any lands in the ground truth, those F1@3 points are permanently unreachable. Cheap counter: emit one chunk per such document from its catalog metadata (title, authors, year, source). ~30 chunks total, zero measurable cost, converts a hard recall ceiling into a soft one. Worth reversing.
- Per-file extraction errors go through `on_error`, which exists — but the plan never says what `pipeline_final` does with it. One bad PDF should not kill a 90-minute run. Write `errores.jsonl` (the repo already has that pattern) and continue.

# 6. Observability

Specified as counts to collect (Spec 03 D6) but with no mechanism.

Single highest-value addition: **`run_manifest.json`, written once per run** — files walked / excluded / extracted, OCR-triggered count, docs with zero chunks, chunks per format, hard-cut occurrences, per-ladder-level hit counts, `num_tokens` p50/p95/p99/max, wall time per stage, library versions, model revision. Every number Spec 03 D6 asks for, plus the raw material for the graded `informe_tecnico.pdf`, from one file. Nothing else in the observability budget comes close.

Secondary: stage-level timing is absent, and OCR is the long pole (50 PDFs, minutes each). Log it or you won't know whether the Colab run is stuck or slow.

# 7. Testability

Also strong — every step ends with a runnable check, and the checks assert the right invariants (alignment, ≤250 words, sub-fragment parent `chunk_id`, `encode_queries` vs `encode_passages`, max-pooling behavior). Four gaps:

- **Step 2's check is not mechanical.** Step 2 *deliberately changes* CSV/XLSX/PBF output, so "re-run the parity harness" fails by design and degrades to eyeballing. Define it as normalized equality — `old.replace("\n", "\n\n") == new` for those three, byte-parity for the rest.
- **No resume-path alignment test.** Selftest item 1 covers two identical full runs; the path that actually corrupts ordering is partial-run → interrupt → resume. Add it: encode half, kill, resume, assert `ids.json[i] == metadata line i` and `index.ntotal == len(ids)`.
- **No standalone-delivery test.** Copy `entrega/` to an empty directory, run `generador.py` there, assert it produces 50 valid lines. That is literally the §1.4 gate (B3).
- **Field-name trap untested.** `metadata.jsonl` uses `texto` (Tabla 1); `resultados.jsonl` uses `text` (Tabla 2). Assert both key names explicitly in the two selftests — "no missing fields" won't catch a swap.
- Add to generador selftest: FAISS returns `-1` → no crash, no wrong chunk; and `k` capped at `ntotal` terminates.

# 8. Constraint alignment

Checked clause by clause against the PDF. Correct on: §5.1 FAISS mandatory; §1.4 layout, `write_index`, line-order-equals-FAISS-ids; §8.3 (Spec 03 Part B stays strictly inside arithmetic — the explicit exclusion list is right, and B6's reasoning that prefix variation is thin is correct); §3.3 + §9.2.1 tension resolved by segmentation rather than by breaking one; §9.2.1 sub-fragments sharing a parent `chunk_id` — the spec explicitly blesses this (*"todos ellos comparten el mismo chunk_id; esto es aceptable"*); §8.6 max-pooling named as a permitted strategy; §5.2 flat index endorsed for this corpus size; §9.3.1/9.3.2 exact 3/10/50; §2.2 language marking; §10.2.1 correctly identified as the priority-reordering clause.

Violations and risks, beyond B1/B2/B3:

- **§4.3 — "los fragmentos deben diseñarse para no superar dicho límite" (512 tokens).** The plan budgets words and defers the token measurement to *after* the full Colab encode (Spec 03 A4). That's too late: `MAX_WORDS` is the one parameter whose change invalidates every downstream artifact. Measure the `num_tokens` distribution at the **Step 6 dry run** (`--sample 40` gives the shape) and set `MAX_WORDS` before Colab.
- **§3.4 `formato` enum.** Tabla 1 reads *"Formato del archivo de origen: pdf, html o md."* You emit `json/csv/xlsx/pbf/jpg`. Almost certainly illustrative — §1.3 lists all seven formats as provided — but if an automated validator checks the enum it's a schema penalty. Do **not** remap (that would break §8.7 `formato` filters and be false); note the deviation in the informe as a deliberate reading.
- **No HTML in the corpus.** Inventory `Tipo`: JSON 954, PDF 759, Otro 74, CSV 26, Imagen 8, Excel 4, Texto 1. Step 1 item 6 sources titles from `^#{1,6} ` *"from HTML and Markdown"* — a dead branch. Titles come only from the 313 catalog matches (decision 12). Say that plainly, or delete the marker path.
- **§3.2 justification requirement** — *"Lo que se exige es que la estrategia elegida se justifique explícitamente en el documento técnico"*. Spec 02 flags this correctly. It is a **hard** requirement for a hybrid strategy, not a nicety, and `informe_tecnico.pdf` is not started.
- **§11 Borda** weights NDCG@10 and F1@3 equally. The plan's F1-protective choices (max-pooling over sum, `fuente` correctness) are correctly prioritized under that.

---

**If you do five things:** swap `fuente` to basename (B1); un-collapse PBF (B2); inline E5 into `generador.py` (B3); guard `-1` and bound the retry loops (B4/B5); carry the text cache to Colab and checkpoint the encode (H1/H2). Everything else above is improvement; those five are the difference between scoring and not.
