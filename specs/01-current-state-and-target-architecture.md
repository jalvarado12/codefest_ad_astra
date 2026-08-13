# Spec 01 — Current state and target architecture

CODEFEST AD ASTRA 2026, Etapa 1. Status as of 2026-08-12.

---

## Part A — What exists today

### Verified and done

**`extraccion_final.py`** — self-contained extraction pipeline. Merges the former
`extraccion/` package (`adaptadores`, `pipeline`, `registro`, `texto_utils`) plus
`json_extract.py` and `catalog_metadata.py` into one file with no cross-file imports.

Verified against the full corpus this session:

| Extractor | Compared | Coverage | Result |
|---|---|---|---|
| `JSONExtractor` | 964 | all JSON | 938 identical, 26 both `JsonSinTexto`, 0 mismatch |
| `CSVExtractor` | 26 | all CSV | 26 identical |
| `ExcelExtractor` | 6 | all XLSX | 6 identical |
| `TextoExtractor` | 1 | all TXT | 1 identical |
| `ImagenExtractor` | 8 | all JPG | 8 identical |
| `PDFExtractor` | 15 | sample of 760 | 15 identical |
| `PBFExtractor` | 1 tileset (73 files) | all PBF | 1 identical |

Both built-in self-checks pass: `self-check OK (17 fixtures)` and
`self-check OK (catalog: filename match, title-fallback match, dead-link non-match, doc attach)`.

Public contract:

```python
generar_documentos(input_dir, registry_path="doc_registry.json", on_error=None)
  -> Iterator[{doc_id, fuente, formato, fenomeno, texto_limpio, metadata_catalogo}]
```

Behaviour worth knowing:

- OCR is a **fallback**, not a default. `PDFExtractor.extraer` reads the text layer first and
  only calls `_ocr_fallback` when the result is under `UMBRAL_TEXTO_VACIO` (50 chars).
  Measured: **50 of 760 PDFs (6.6%)** trigger it. Detection is per document at runtime; no
  file list is ever hardcoded. `ImagenExtractor` OCRs unconditionally, which is correct.
- The 20 files in `CATALOG_FILES` return `""` by design and are registered with
  `texto_limpio=""` rather than indexed as pseudo-documents.
- `metadata_catalogo` attaches salvaged catalog entries to the real document they describe:
  313 documents, 358 entries.
- `RegistroDocumentos` persists `fuente -> DOC-NNNN` in `doc_registry.json` and never
  reissues an id.

**`embedding_pipeline/embeddings_only.py`** — `E5Dense` and `BGEM3` wrappers. `E5Dense`
applies the mandatory `"query: "` / `"passage: "` prefixes by construction and exposes
`count_tokens()`. `encode_texts()` and `write_vectors()` handle the batch path. Its
`selftest` runs anywhere with stub encoders; `verify` needs the real libraries.

**`chunker.py`** — the team's chunker, arrived 2026-08-12. Audited, then rewritten to chunking
only on 2026-08-13: cleaning and classification removed, sentence packing under a dual
word/token cap, five-level escalera with counters, all eight Tabla-1 fields. Self-check passes.
See Spec 02 Step 1 and Spec 00 §7a-bis. The 0-byte / `chunker (1).py` filename problem is long
resolved.

### Reference material, not corpus

Both live in the corpus directory *and* the working directory (identical md5). Both must be
excluded from indexing.

- **`Indice_Datos_Codefest.xlsx`** — sheet `Inventario de Archivos`, 1,826 rows: ADL's own
  `DOC_ID`, `Fenómeno`, `Observatorio`, `Nombre estandarizado`, `Carpeta`, `Tipo`. Sheet
  `Resumen por Fenomeno` confirms 459 / 479 / 888 and 759 PDFs.
- **`Extracto_Preguntas_50_v2.pdf`** — the 50 evaluation queries, 3 pages, format
  `qNNN <question>` wrapped across lines.
- **`FASE ORDENADA CODEFEST.xlsx`** — admin file inside `F3_Dinamicas_Territoriales/`, absent
  from the inventory. Found by the Step 3 reconciliation; must be excluded or it gets indexed
  as a corpus spreadsheet.

### Counting: files ≠ inventory rows ≠ documents

These three differ and conflating them produces wrong estimates:

| | count |
|---|---|
| File paths on disk | 1,848 |
| Unique basenames | 1,713 (186 inventory rows share a name — all PBF tiles) |
| Inventory rows | 1,826, all present on disk |
| Disk paths absent from the inventory | 22 (9 `.DS_Store`, ~10 catalog JSONs, 3 non-corpus files) |
| **Documents the pipeline emits** | ~1,826 minus the 3 non-corpus files; 20 catalog files yield *empty* documents that never reach `metadata.jsonl` |

**Changed 2026-08-12:** PBF tilesets no longer collapse. §2.3 defines a document as *"un
archivo individual provisto por ADL"* and the inventory lists all 73 `.pbf` tiles as separate
rows, so the pipeline now emits **one document per tile** with dedup inside each file. The
previous single-tileset document had a directory for its `fuente`, matching no inventory row —
under §10.2.1 that scores zero on every PBF item. See Spec 02 Step 2 item 2 for the §2.1
counter-argument and why it was resolved this way.

The join key for any inventory check is `Carpeta + "/" + Nombre estandarizado`, which is
unique across all 1,826 rows. The bare basename is **not** unique.

### Not started

~~`pipeline_final.py`, `generador.py`~~ — both built and self-tested (2026-08-12 and
2026-08-13). `inventario.py` added for Step 3.

Remaining: the technical report, the optional knowledge graph, chunk overlap, and a
full-corpus run.

---

## Part B — Constraints the architecture must satisfy

From `ad_astra.md`. These are not design preferences; violating any of them costs points or
disqualifies the entry.

| § | Constraint |
|---|---|
| 5.1 | FAISS is mandatory for the vector index |
| 1.4 | Deliver `index.faiss` (`faiss.write_index()`) + `metadata.jsonl`, **line order = FAISS internal ids** |
| 1.4 | Deliver `resultados.jsonl`, `generador.py` (exact filename), `informe_tecnico.pdf` (≤8 pp) |
| 8.3 | No generative/decoder models anywhere in retrieval |
| 3.3 | No chunk may contain an incomplete sentence or phrase; cuts only at sentence boundaries |
| 3.4 | Eight mandatory chunk fields (Tabla 1); extras permitted |
| 9.2 | Returned fragments ≤ 250 **words** |
| 9.3.1 | Exactly 50 lines, exactly 3 `documents`, exactly 10 `fragments` per line |
| 9.3.2 | Missing fields, wrong array lengths, or >250-word fragments are penalised or discarded |
| 10.2.1 | **Document ground-truth matching is on `fuente`, not `doc_id`** |
| 2.2 | Detect and mark each document's predominant language |

§10.2.1 is the one that reorders priorities. `doc_id` is internal traceability; `fuente` in
`metadata.jsonl` is the actual grading key for F1@3, half the leaderboard. It is currently a
Windows path with backslashes.

---

## Part C — Target architecture

```
  CORPUS (1,848 paths, minus 3 non-corpus files and .DS_Store)
        │
        ▼
  extraccion_final.py ──────────────► cache/textos.jsonl
   text layer, OCR fallback,          {doc_id, fuente, formato, fenomeno,
   cleaning, language, catalog         idioma, sha256, texto_limpio,
                                        metadata_catalogo}
        │                                    ▲
        │                          cache 1: skip on unchanged sha256
        ▼
  chunker.py  (in memory, every run, deterministic)
   blocks ─► pack to 250 words ─► 1–2 sentence overlap (prose only)
        │
        ▼  chunks with all 8 Tabla-1 fields
        │
  embeddings_only.E5Dense.encode_passages()
        │                                    ▲
        │                          cache 2: encode only unseen chunk_ids
        ▼
  vectors/multilingual-e5-large/
    dense.npy   float32 [n,1024] L2-normalised
    ids.json    {"fingerprint": ..., "ids": [chunk_id, ...]}
        │
        ▼
  faiss.IndexFlatIP  ──► entrega/base_vectorial/encoder_multilingual-e5-large/
                            index.faiss
                            metadata.jsonl     (walk ids.json, one line per row)
        │
        ▼
  generador.py  +  Extracto_Preguntas_50_v2.pdf
        │
        ▼
  entrega/resultados.jsonl   (50 lines, 3 docs + 10 fragments each)
```

### The ordering invariant

`ids.json` is the single source of truth for order. For every `i`:

```
ids.json["ids"][i]  ==  FAISS internal id i  ==  dense.npy row i  ==  metadata.jsonl line i
```

`metadata.jsonl` is **generated** by walking `ids.json`, never maintained by hand. This is
what §1.4 grades and it is the one invariant with a dedicated test.

### The two caches

**Cache 1 — text.** Keyed on `sha256` of source bytes. Guards extraction, which matters most
for the ~50 OCR PDFs (minutes each). A full text-layer pass over all 760 PDFs is ~5 minutes,
so this is a convenience for the OCR subset rather than the backbone. Since one `.pbf` is now
one document, tiles hash individually like every other file.

**Cache 2 — vectors.** Keyed on `sha256` of the **chunk text**. As built in `pipeline_final.py`.

~~Keyed on `chunk_id`, with a `fingerprint` guard: `sha256(chunker source) + MAX_WORDS +
encoder`.~~ The fingerprint scheme was dropped: it re-encoded every chunk on *any* chunker or
parameter change, which is exactly the change being made most often. Hashing the text instead
means only genuinely changed chunks re-encode, the whole wipe-on-mismatch mechanism disappears,
and the overlap / `MAX_WORDS` sweeps become affordable. The hazard the fingerprint was there to
prevent — positional chunk ids being stable names for unstable content — is handled at the
root: chunks are never persisted, and the vector key never mentions the id.

`ids.json` stores `hashes` next to `ids`, which keeps two reuse paths distinguishable: a valid
**checkpoint prefix** of the current run's order, and a **text-hash** match anywhere in a
previous run. `run_manifest.json` reports `reanudados`, `reusados` and `codificados` separately,
because a total alone would hide a dead cache.

### Delivery layout (§1.4)

```
entrega/
  resultados.jsonl
  generador.py
  informe_tecnico.pdf                      (not in scope — see Spec 03)
  base_vectorial/
    encoder_multilingual-e5-large/
      index.faiss
      metadata.jsonl
```

### Chunk record (Tabla 1 + extras)

```json
{
  "doc_id": "DOC-0042", "chunk_id": "DOC-0042-chunk-00007",
  "fuente": "F2_Seguridad_Entorno_Espacial/CSIS/pdfs/CSIS_space-threat.pdf",
  "formato": "pdf", "fenomeno": 2, "posicion": 7,
  "num_tokens": 331, "texto": "...",
  "nombre_archivo": "CSIS_space-threat.pdf", "adl_doc_id": "F2-CSIS-014",
  "idioma": "es", "n_words": 248, "title": "Capacidades antisatélite",
  "catalogo_year": "2024", "catalogo_country": "US", "catalogo_authors": "..."
}
```

First eight are mandatory (§3.4); the rest are permitted extras. `num_tokens` is a real
tokenizer count stamped at embed time; `n_words` is what the chunker budgeted against.

**Three document keys, deliberately.** §10.2.1 matches the ground truth on `fuente`, and
Tabla 1 describes it as *"Nombre o URL del archivo original"* — but the bare name is **not
unique**: 114 PDF rows share 47 names (CSET reports appearing under both `pdfs/Reports` and
`pdfs/Translation`), plus 72 PBF tiles. The relative POSIX path is unique across all 1,826
rows, and path → name is derivable while name → path is not. So `fuente` carries the path, and
`nombre_archivo` and `adl_doc_id` cover the other two plausible grader keys at no cost.

**Note on `formato`:** Tabla 1 reads *"pdf, html o md"*, but §1.3 lists all seven provided
formats, so the enum is illustrative. We emit `json/csv/xlsx/imagen/pbf` as well. Do not remap
— that would be false and would break §8.7 `formato` filters. Record the reading in the
technical document.

---

## Part D — Scale

Projections after the Spec 02 changes, from measured word volumes.

| Source | files | words | est. chunks | share |
|---|---|---|---|---|
| CSV + XLSX | **30** | 16,732,001 | ~66,900 | 61% |
| PDF | 759 | — | ~38,000 | 35% |
| JSON | 954 | — | ~4,600 | 4% |
| **Total** | | | **~110,000** | plus ~15% on prose from overlap |

Corrected 2026-08-12: the corpus has **4** XLSX files, not 6 — `Indice_Datos_Codefest.xlsx`
and `FASE ORDENADA CODEFEST.xlsx` are admin files on the exclusion list. They hold 1,773 words
between them, so the volume and share figures are unaffected; only the file count changes.

Of the tabular volume, **one CSV holds 7.1M words — ~43% of it, ~26% of the whole index.**
Spec 02 Step 6 inspects that single file before the Colab run.

Consequences: `dense.npy` ≈ 450 MB, `metadata.jsonl` ≈ 165 MB, embedding well under an hour
on a T4, flat search trivially fast at this size. The concern is composition, not cost — 32
files supplying the majority of the index. Decision taken: index them now, measure later
(Spec 03, item 1).
