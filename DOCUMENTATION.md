# The CODEFEST AD ASTRA 2026 retrieval system — complete documentation

This document explains the whole system to somebody who has never seen it, and who does not
need to already know what an embedding or a vector index is. It covers what the system does,
why each stage exists, what every decision was weighed against, what is built today, and what
is deliberately left undone.

It is not a README. Reading it end to end should leave you able to defend every design choice
in the pipeline, and to find the weak spots without being told where they are.

---

## Table of contents

1. [What the competition asks for](#1-what-the-competition-asks-for)
2. [The core idea, for a newcomer](#2-the-core-idea-for-a-newcomer)
3. [Why there are two programs, not one](#3-why-there-are-two-programs-not-one)
4. [The corpus we were given](#4-the-corpus-we-were-given)
5. [Part I — The indexing pipeline](#5-part-i--the-indexing-pipeline)
6. [Part II — The generator](#6-part-ii--the-generator)
7. [The delivery](#7-the-delivery)
8. [Current state: what exists, what is tested, what does not exist](#8-current-state)
9. [Technical debt, with reasons](#9-technical-debt-with-reasons)
10. [How to run everything](#10-how-to-run-everything)
11. [Glossary](#11-glossary)

---

## 1. What the competition asks for

CODEFEST AD ASTRA 2026, Stage 1. We are given a corpus of roughly 1,800 documents about three
themes ("fenómenos"):

- **F1** — artificial intelligence and strategic capabilities
- **F2** — space environment security
- **F3** — territorial dynamics

We must build a **knowledge base** that, given a natural-language question, returns the
documents and passages that answer it. Then 50 questions (`q001`–`q050`, in Spanish, English
and Portuguese) are run against it, and our answers are compared to a hidden ground truth.

Two scores, weighted equally:

- **NDCG@10** — how good our ten returned passages are, and in what order
- **F1@3** — how good our three returned documents are, as a set (order ignored)

### The single most important constraint

**No generative AI anywhere in the retrieval path** (spec §8.3). No GPT, no Claude, no Llama.
Not for re-ranking, not for rewriting the question, not for filtering, not for summarising.

This is not a stylistic preference — it changes the entire architecture. We cannot "ask a model
which document is relevant". Everything must be done with **encoder models** (which convert
text to numbers but generate nothing) and **arithmetic**.

### The other hard rules

| Rule | Where | Consequence |
|---|---|---|
| FAISS is mandatory for the vector index | §5.1 | No alternative library |
| `metadata.jsonl` line order must equal FAISS's internal ids | §1.4 | The invariant the whole design protects |
| Exactly 50 lines, exactly 3 documents, exactly 10 fragments per line | §9.3.1 | Wrong counts are discarded |
| Every returned fragment ≤ 250 **words** | §9.2 | Words, not tokens |
| No chunk may contain an incomplete sentence | §3.3 | Constrains where text may be cut |
| Eight mandatory metadata fields per chunk | §3.4 | Listed in §5.7 below |
| Ground truth matches on `fuente`, **not** `doc_id` | §10.2.1 | Explained in §5.4 — easy to get catastrophically wrong |

---

## 2. The core idea, for a newcomer

### The problem with searching by keyword

If somebody asks *"¿Qué riesgos genera la basura espacial?"* and a document says *"orbital
debris poses collision hazards"*, keyword search finds nothing. No words match. The document is
in English, the question in Spanish, and even within one language people phrase things
differently.

### Embeddings: turning meaning into coordinates

An **encoder model** reads a piece of text and outputs a list of numbers — here, 1,024 of them.
That list is called an **embedding** or **vector**. You can think of it as coordinates in a
1,024-dimensional space.

The useful property: the model is trained so that **texts with similar meaning land near each
other**, regardless of wording or language. "Basura espacial" and "orbital debris" end up close
together. "Space debris" and "coffee budget" end up far apart.

So the search becomes geometric: convert the question to a vector, then find the nearest
document vectors. No keyword matching involved.

### Measuring "near"

We use **cosine similarity** — the angle between two vectors, ignoring their length. It runs
from −1 (opposite) through 0 (unrelated) to 1 (identical direction).

A convenient trick: if you first **normalise** every vector to length 1, cosine similarity
becomes exactly the **dot product**, which is far cheaper to compute. That is why the pipeline
normalises everything and uses FAISS's `IndexFlatIP` (IP = inner product = dot product). Spec
§8.2 spells out this equivalence.

### Why documents are cut into chunks

A 189-page PDF cannot be one vector. Two reasons:

1. **A hard technical limit.** The encoder reads at most 512 tokens (roughly 350–400 words) at
   once. Anything beyond that is silently discarded — not an error, just gone.
2. **A meaning limit.** Even if it fit, one vector averaging 189 pages would represent nothing
   in particular. Vectors are precise only when the text is about one thing.

So documents are split into **chunks** of a few hundred words, and each chunk gets its own
vector. The spec calls these *fragmentos*.

### What FAISS is

A library for storing millions of vectors and answering *"which stored vectors are nearest to
this one?"* in milliseconds. It stores **only numbers** — no text, no filenames. It hands back
integer positions like "row 4,712".

That leads directly to the single most important structural fact in this system.

### The invariant everything depends on

FAISS says *"row 4,712 is the best match"*. It has no idea what that text says. Something must
map 4,712 back to the actual chunk.

That something is **`metadata.jsonl`** — a text file with one JSON object per line, holding
each chunk's text and metadata. The mapping is **position**: FAISS row 4,712 is line 4,712.

```
FAISS internal id  i
      ↕
dense.npy          row i        (the raw vectors)
      ↕
ids.json           entry i      (which chunk_id that row is)
      ↕
metadata.jsonl     line i       (that chunk's text and metadata)
```

If these ever drift out of alignment, the system returns confident, well-formatted, completely
wrong answers — with no error message. **Nothing else in this design is defended as carefully
as this ordering**, and §1.4 grades it directly.

The defence is simple: `metadata.jsonl` is never written by hand or assembled separately. It is
**generated by walking `ids.json` in order**. There is exactly one source of truth for order.

---

## 3. Why there are two programs, not one

| Program | Runs | Job |
|---|---|---|
| `pipeline_final.py` | Once, by us, before submission | corpus → chunks → vectors → `index.faiss` + `metadata.jsonl` |
| `generador.py` | By the judges | question → search the index → `resultados.jsonl` |

The split is not our invention — §1.4 item 4 says `generador.py` is a script that
***"utilice el índice"***, uses the index. The index is delivered *alongside* it as a separate
artifact.

This matters practically. Building the index takes about 70 minutes of GPU time and needs the
25 GB corpus. If `generador.py` rebuilt it, every judge would need the whole corpus and a GPU
just to score one entry. Instead, the expensive work happens once on our side, and the judges
receive a finished index plus a small script that reads it.

**Consequence:** `generador.py` must be *self-contained*. It cannot import anything else from
this repository, because only it and the artifacts get delivered. Its encoder wrapper is
inlined for exactly this reason.

---

## 4. The corpus we were given

Three numbers that are all different, and confusing them produces wrong estimates:

| Count | Value | What it is |
|---|---|---|
| File paths on disk | 1,848 | everything, including junk |
| Rows in ADL's inventory | 1,826 | what ADL considers documents |
| Unique filenames | 1,713 | **filenames are not unique** |

By format: **964 JSON**, **760 PDF**, **73 PBF** map tiles, **26 CSV**, **8 JPG**, **6 XLSX**
(only 4 are corpus content), **1 TXT**. And notably: **zero HTML, zero Markdown**, despite the
spec discussing both.

Three files sit inside the corpus tree but are **not** corpus content and must be excluded:

- `Extracto_Preguntas_50_v2.pdf` — the 50 evaluation questions. Indexing this would pollute
  retrieval with text that mirrors the queries.
- `Indice_Datos_Codefest.xlsx` — ADL's own inventory of the corpus.
- `FASE ORDENADA CODEFEST.xlsx` — an admin spreadsheet, found only by reconciling disk against
  the inventory.

### The filename trap

114 PDFs share a filename with another PDF — 47 distinct names, mostly CSET reports that exist
in both a `Reports/` and a `Translation/` folder. Same name, different documents.

This is not trivia. It caused a real measurement error during development: a test harness built
a `{filename: path}` dictionary, later files silently overwrote earlier ones, and the resulting
number was wrong for hours before anyone noticed. It is also the reason for a design decision
in §5.4.

---

## 5. Part I — The indexing pipeline

Overall flow:

```
corpus ──► extract ──► clean ──► identify ──► chunk ──► embed ──► FAISS ──► metadata.jsonl
             │                                                       │
        cache/textos.jsonl                            vectors/<enc>/{dense.npy, ids.json}
        (skips OCR on re-runs)                        (skips re-encoding on re-runs)
```

### 5.1 Extraction — seven formats, seven strategies

Implemented in `extraccion_final.py`, a single self-contained file. Each format gets an
adapter with one job: produce plain text. The design is the **Adapter pattern** — the
orchestrator never knows what format it is handling.

**PDF.** Read the embedded text layer with PyMuPDF, in `"blocks"` mode rather than plain text.
Blocks preserve reading order and naturally separate headers and footers, which the cleaner
then removes because they repeat identically across pages.

But **50 of the 760 PDFs are scans** — photographs of paper, with no text layer. For these the
adapter falls back to **OCR** (optical character recognition): render each page as an image and
have EasyOCR read the letters.

The fallback is decided **per document at runtime**, by checking whether the text layer yielded
fewer than 50 characters. No list of filenames is hardcoded anywhere. This matters because OCR
is enormously expensive:

| | seconds per page | 613 pages |
|---|---|---|
| CPU | 83 | ~14 hours |
| Colab T4 GPU | **6.81** | **69.5 minutes** |

Both figures are measured, not estimated. The 12.2× gap is why extraction runs on GPU, and is
the single biggest cost decision in the project.

**JSON.** By far the largest group (964 files) and the most variable. The spec (§2.1) says to
interpret the object and explicitly select the fields holding article text, keeping descriptive
fields (`url`, `date`, `authors`) as metadata rather than mixing them into the body.

Real corpus JSON does not follow one schema, so extraction runs in tiers:

- **Tier 1** — harvest known text-bearing field names (`title`, `body_text`, `body_paragraphs`,
  `abstract`, and Spanish/Portuguese equivalents), skipping known noise containers (`images`,
  `links`, `pdf_links`).
- **Tier 2** — if tier 1 produced under 30 words, sweep again with the noise filters relaxed.
- **Tier 3** — still nothing usable: raise `JsonSinTexto` and let the document through with
  empty text.

One subtlety worth understanding: 485 of 848 article files carry **both** `body_text` and
`body_paragraphs`, where the former is the latter joined together. Emitting both would double
the document. The paragraph list wins, because §2.1 asks for paragraph order to be preserved
and the list is where the real boundaries are.

**CSV and XLSX.** Read the header, then each row becomes `columna: valor | columna2: valor2`,
so every value keeps its column name as context. Empty cells are dropped.

**Images.** OCR unconditionally. If the result is under 30 characters the image is decorative
and returns empty rather than OCR noise.

**PBF** (Mapbox vector map tiles). Decode each tile, walk its layers and features, and turn
attributes into `atributo: valor` text.

**TXT/MD.** Passed through; Markdown headings are left intact as structural signals.

### 5.2 Cleaning

One shared function, applied to every adapter's output:

1. Remove control characters and zero-width spaces
2. Normalise Unicode to NFC (so accented characters have one canonical encoding)
3. **Remove repeated lines** — a line that is short and appears three or more times is page
   furniture (headers, footers, nav menus), not content
4. Collapse redundant whitespace

Step 3 is why PDF extraction uses block mode: headers become their own blocks, repeat
identically, and get stripped automatically.

### 5.3 The catalog problem

Twenty of the JSON files are not documents at all — they are the scraper's own bookkeeping:
lists of URLs, hashes and HTTP status codes for files it downloaded. Indexed naively, they
become fake documents made of title-salad.

They are recognised by name and forced to empty text. But their **contents are still useful**:
each entry describes a real corpus file, often with metadata that file itself lacks (author,
country, year). So a matching step pairs each catalog entry with the real document it describes
and attaches that metadata to it — 313 documents enriched, 358 entries matched.

Interestingly, **only 10 of those 20 catalog files appear in ADL's inventory**. The other 10 are
not ADL documents at all, which settles a question about them (see §9, debt 6).

### 5.4 Identity: `doc_id` versus `fuente`

Every document gets a `doc_id` like `DOC-0042`, assigned once and persisted in
`doc_registry.json` so it never changes between runs.

**But `doc_id` is not what gets graded.** §10.2.1:

> *"a nivel de documento el emparejamiento con el ground truth se realiza a través del campo
> `fuente` (archivo original provisto por ADL), no del `doc_id` arbitrario asignado por el
> equipo."*

The ground truth matches on **`fuente`** — the original ADL file. `doc_id` is internal
bookkeeping. So `fuente` correctness directly determines F1@3, which is half the score, and a
mistake there is invisible to every other test.

What should `fuente` contain? Table 1 describes it as *"Nombre o URL del archivo original"*,
which sounds like a filename. But as §4 showed, **filenames are not unique** — 114 documents
would collapse into 47 ambiguous keys.

The resolution rests on an asymmetry: **a path contains the name, but a name cannot be
recovered from a path.** So:

| Field | Value | Purpose |
|---|---|---|
| `fuente` | `F1_IA.../pdfs/Reports/CSET_....pdf` | unique across all 1,826 rows; the graded key |
| `nombre_archivo` | `CSET_....pdf` | covers a grader matching on bare filename |
| `adl_doc_id` | `F1-CSET-014` | ADL's own identifier, free to carry |

The spec permits extra fields, so all three ship. Paths use forward slashes — a Windows
backslash path would be a silent, total F1@3 failure.

### 5.5 Chunking

Handled by `chunker.py`. The strategy is **paragraph packing with a word budget**:

1. Split the cleaned text into blocks on blank lines
2. Pack consecutive blocks into a chunk until adding the next would exceed **250 words**
3. Never split a paragraph across chunks

**Why 250 words?** Because §9.2 caps *returned* fragments at 250 words. If every chunk already
respects that limit, the generator can return chunks verbatim and never needs to split anything
at query time. One number, chosen once, deletes an entire subsystem downstream.

**Why words and not tokens?** Tokens would be the natural unit, since the encoder's limit is in
tokens. But counting tokens requires loading the tokenizer, and the tokenizer does not install
on the development machine. Counting words instead means **chunk boundaries are identical
everywhere** — laptop and GPU server produce byte-identical chunks. The token count is still
recorded, measured with the real tokenizer at embedding time, when it is loaded anyway.

**Sentence completeness (§3.3).** No chunk may contain a partial sentence. Paragraph packing
satisfies this automatically, since a paragraph boundary is always also a sentence boundary.
The exception is a single paragraph longer than 250 words, which needs splitting at sentence
boundaries — and that requires knowing what a sentence *is*.

This turns out to be a real trap. A naive split on `.` breaks `El Dr. Pérez` into two
"sentences". The correct splitter carries an abbreviation list (`Dr.`, `Sra.`, `EE.UU.`,
`etc.`, `vs.`, …) and handles Spanish inverted punctuation. Two independent implementations
were written during development; the one without the abbreviation list passed all its own tests
and was still wrong, because its test fixtures happened to contain no abbreviations.

### 5.6 Embedding

Model: **`intfloat/multilingual-e5-large`**, pinned to revision
`3d7cfbdacd47fdda877c5cd8a79fbcc4f2a574f3`. Encoder-only, self-hosted, Apache-2.0, and
multilingual — which matters because the corpus and the questions span Spanish, English and
Portuguese. Output: 1,024 dimensions.

**The prefix rule.** This model family requires a literal prefix on every input:

- `"passage: "` for text being indexed
- `"query: "` for a question being searched

Using the wrong one produces no error and no crash — just quietly worse results everywhere. It
is the single easiest way to lose the whole score, so the code makes the two paths separate
functions rather than a flag.

Vectors are L2-normalised at encoding time, so cosine similarity equals the dot product.

### 5.7 What a chunk record looks like

```json
{
  "doc_id": "DOC-0042",
  "chunk_id": "DOC-0042-chunk-00007",
  "fuente": "F2_Seguridad_Entorno_Espacial/CSIS/pdfs/CSIS_space-threat.pdf",
  "formato": "pdf",
  "fenomeno": 2,
  "posicion": 7,
  "num_tokens": 331,
  "texto": "…",

  "nombre_archivo": "CSIS_space-threat.pdf",
  "adl_doc_id": "F2-CSIS-014",
  "idioma": "es",
  "n_words": 248,
  "title": "Capacidades antisatélite",
  "catalogo_year": "2024"
}
```

The first eight are mandatory (§3.4 Table 1). The rest are permitted extras that either enable
metadata filtering or preserve traceability.

`chunk_id` uses five digits (`00007`) because one CSV produces roughly 28,000 chunks and four
digits would stop sorting correctly past 9,999.

### 5.8 The FAISS index

`IndexFlatIP` — "flat" meaning exhaustive: every query is compared against every vector. That
sounds slow, but at ~110,000 vectors it takes milliseconds, and §5.2 of the spec explicitly
endorses a flat index at this scale. Approximate indexes (IVF, HNSW) trade exactness for speed
we do not need.

Vectors are inserted in `ids.json` order, then `metadata.jsonl` is written by walking that same
order. The invariant holds by construction rather than by discipline.

### 5.9 Caching, and what is deliberately *not* cached

Two caches, guarding the two expensive stages:

| Cache | Key | Guards |
|---|---|---|
| `cache/textos.jsonl` | SHA-256 of the source file's bytes | extraction, especially the ~14 CPU-hours of OCR |
| `vectors/<encoder>/` | SHA-256 of the chunk **text** | GPU encoding |

**Chunking is deliberately not cached.** It is deterministic and takes milliseconds, so caching
it would buy nothing — and would create a real hazard. Chunk ids are positional
(`DOC-0042-chunk-00007`), so they are *stable names for unstable content*: if a document is
re-extracted slightly differently, chunk 7 keeps its name but holds different text, and a
shortened document leaves orphan chunks that nothing ever overwrites. Treating chunks as
derived state, regenerated wholesale every run, makes that entire class of bug impossible.

Keying the vector cache on chunk **text** rather than chunk id follows the same logic: only
genuinely changed text is re-encoded, and changing the chunking strategy costs exactly what it
should.

### 5.10 Where Colab fits, and why the direction matters

Extraction needs a GPU (OCR). Encoding needs a GPU. Everything between them does not.

The correct order is therefore:

1. **Colab pass A** — extraction on GPU → produces `cache/textos.jsonl` → download it
2. **Locally** — chunk, test, iterate, at zero OCR cost and no GPU
3. **Colab pass B** — encode and index on GPU

The text cache is Colab's **output**, not its input. An earlier draft of the plan had this
backwards, which would have meant 14 hours of local CPU OCR to produce a file the GPU makes in
about an hour. The cache's SHA-256 key is environment-independent, so moving it between
machines is safe by construction.

---

## 6. Part II — The generator

`generador.py`. Input: an index, a metadata file, and 50 questions. Output: `resultados.jsonl`.
It must run standalone with no repository imports.

### 6.1 Loading the questions

The questions arrive as a 3-page PDF, formatted `qNNN <question text>` with questions wrapped
across lines. The loader extracts text with PyMuPDF, matches `^(q\d{3})\s+(.*)$`, and joins
continuation lines until the next `qNNN`.

It is isolated in one function, and also accepts `.jsonl` and `.csv`, so a different delivered
format costs a single edit. It asserts exactly 50 unique ids, `q001`–`q050`.

### 6.2 Loading the metadata

`metadata.jsonl` is read into a **list, indexed by position** — because position *is* the FAISS
id. It is deliberately *not* keyed by `chunk_id`, which would throw away the ordering the whole
design protects.

### 6.3 Searching

The question is encoded with the **`"query: "`** prefix and searched against the index for the
top `k` (default 50) nearest chunks. `k` is far above the 10 we need, for two reasons:
document-level aggregation needs many chunks to group, and some may be filtered later.

**A trap worth naming.** When you ask FAISS for more neighbours than it holds, it pads the
result with `-1`. In Python, `metadata[-1]` is not an error — it is the *last* element. Without
a guard, an exhausted search silently returns the final chunk of the corpus as a confident
match. The code checks `if idx < 0: continue` before every lookup.

### 6.4 From chunks to documents (§8.6)

FAISS returns chunks; the answer needs **3 documents**. Chunks are grouped by `doc_id` and each
document is scored. Three strategies are permitted:

| Strategy | Document score | Behaviour |
|---|---|---|
| **Max-pooling** ← chosen | its single best chunk | one precise passage wins |
| Sum | sum of all its retrieved chunks | rewards broad coverage |
| Weighted mean | average, rank-weighted | in between |

**Max-pooling is chosen, and the reason is concrete.** Around 61% of the index is tabular data
from 30 CSV/XLSX files. Under sum-aggregation, a single large CSV contributing hundreds of
mediocre chunks to the top-50 would outrank a document with one excellent passage, on chunk
count alone. Max-pooling is structurally immune to how many chunks a document happens to have.

The implementation is neater than it sounds: hits are sorted globally by `(-score, chunk_id)`,
so the **first occurrence** of any `doc_id` in that order is automatically its best chunk.
Deduplicating in place gives max-pooling *and* the deterministic tie-break for free.

**Why the tie-break matters.** The index is built in fp16 on a GPU; a judge re-running on CPU
gets fp32 and slightly different similarity values. Near-ties can reorder. Sorting by
`(-score, chunk_id)` makes the output stable across environments — the difference between
"reproduces" and "mostly reproduces", and §1.4 makes reproduction a pass/fail gate.

### 6.5 Selecting the ten fragments

Walk the sorted hits, emitting `{rank, chunk_id, doc_id, text}` until ten are collected.

If a chunk exceeds 250 words it is split into sub-fragments at sentence boundaries. Per §9.2.1,
**all sub-fragments keep the original `chunk_id`** and each occupies its own rank slot — the
spec explicitly blesses this, since relevance is judged on `text` and `chunk_id` serves only
traceability.

Word counting is `len(text.split())`. The spec says *palabras*; words and tokens differ by
roughly 1.3×, and the penalty is on words.

### 6.6 The threshold, and why it can never break the schema

An optional similarity floor θ discards weak matches. It is a **parameter defaulting to
`-1.0`**, which is genuinely "off" — note that `0.0` would *not* be, since cosine spans
[−1, 1] and zero would silently prune every negative-similarity hit.

The danger: §9.3.2 requires *exactly* 3 documents and *exactly* 10 fragments. A threshold that
filters too aggressively would produce a short list and get the line discarded. So θ prunes,
and then **backfills from the unfiltered ranking** whenever that would leave a quota short. The
filter can narrow results; it can never break the contract.

The same reasoning applies to widening `k`: if the top-50 does not contain three distinct
documents, `k` grows — but bounded at `index.ntotal`, then padded deterministically, so a small
index cannot cause an infinite loop.

### 6.7 The output

One JSON object per line, 50 lines exactly:

```json
{"query_id": "q001",
 "documents": [{"rank": 1, "doc_id": "DOC-0042"},
               {"rank": 2, "doc_id": "DOC-0017"},
               {"rank": 3, "doc_id": "DOC-0091"}],
 "fragments": [{"rank": 1, "chunk_id": "DOC-0042-chunk-00007",
                "doc_id": "DOC-0042", "text": "…"}]}
```

**A subtle trap:** `metadata.jsonl` uses the key **`texto`** (Table 1, Spanish); `resultados.jsonl`
uses **`text`** (Table 2, English). Different files, different key names, and no error if you
confuse them — just a discarded submission.

---

## 7. The delivery

```
entrega/
  resultados.jsonl          50 lines, one per question
  generador.py              standalone, no repo imports
  informe_tecnico.pdf       ≤8 pages, design justification
  base_vectorial/
    encoder_multilingual-e5-large/
      index.faiss           via faiss.write_index()
      metadata.jsonl        one line per chunk, in FAISS id order
```

The indexing pipeline is **not** part of the delivery. Only these four items are graded, and an
unexpected extra file in a validated tree is risk without reward. The pipeline is described in
the technical report instead, which is exactly what §1.4 item 3 asks for.

---

## 8. Current state

### Built and verified

**`extraccion_final.py`** — complete. Verified against the entire corpus with byte-for-byte
parity against the previous implementation: 964 JSON (938 identical, 26 correctly rejected,
0 mismatches), all CSV, XLSX, TXT, JPG, a 15-PDF sample, and the PBF tileset. Both built-in
self-checks pass.

**`generador.py`** — built, and its 11-check selftest passes: schema, word cap, sub-fragment
parentage, prefix routing, query loading, max-pooling behaviour, threshold backfill, the FAISS
`-1` guard, bounded retries, the field-name trap, and standalone delivery from an empty
directory.

**OCR throughput** — measured at full coverage: all 50 scanned PDFs, all 613 pages, 6.81 s/page
on a T4, 12.2× faster than CPU, zero empty results, zero errors.

**The whole flow, end to end, on real data.** A 25-file stratified sample (11 PDF including 3
scanned, 8 JSON, 2 CSV, 2 JPG, 1 XLSX, 1 TXT, all three fenómenos) was run through
extraction → chunking → real e5-large on a T4 → FAISS → `generador.py` invoked as a subprocess
against the artifacts it produced:

| | |
|---|---|
| Extraction | 25/25 documents, **0 errors**, 381 s (mostly OCR) |
| Chunks | 326 from 23 non-empty documents (2 images correctly yielded nothing) |
| Encoding | 326 × 1024 vectors in **6 s** |
| Index alignment | `index.ntotal == metadata lines == 326` ✓ |
| `resultados.jsonl` | **VALID** against §9.3.1/§9.3.2 |

Retrieval behaved sensibly: 16 distinct documents and 98 distinct chunks across the 50
questions, results spread over all three fenómenos, the most frequent rank-1 document winning
15/50 (well below a domination threshold), and fragment lengths capped at exactly 250 words —
confirming the generator's splitter works on genuinely oversized real chunks.

This is what surfaced debt item 8 below, which is the single most consequential finding in the
project so far.

### Delivered but not yet fixed

**`chunker.py`** — the team's chunker, audited. It works, but needs four changes before it is
spec-compliant (all detailed in `specs/02-migration.md` Step 1):

1. It emits only 5 of the 8 mandatory Table 1 fields
2. CSV/XLSX collapse into a **single chunk per file** — worst case 7.1 million words in one
   chunk, of which the encoder sees the first 512 tokens
3. Oversized blocks are never split, so 35% of PDF chunks exceed 250 words
4. No overlap between chunks

### Does not exist yet

**`pipeline_final.py`** — the orchestrator. Its core has been prototyped
(`_gentest/build_index_gpu.py`) to produce real artifacts for end-to-end testing, but the real
thing, with caching and resume, is not written.

**`informe_tecnico.pdf`** — not started. It is graded, and §3.2 makes justifying the chunking
strategy a hard requirement.

---

## 9. Technical debt, with reasons

Ordered by likely cost to the score. Every item is a deliberate decision, not an oversight.

**1. Tabular content would be 61% of the index.** 30 CSV/XLSX files hold 16.7 million words →
about 67,000 of ~110,000 chunks. One CSV alone is ~26% of the whole index. Row text competes
with prose for every result slot.
*Decision: index them for now, measure the damage after the first real run.* If tabular chunks
crowd out prose, a `formato` post-filter is one line, since the field is already in the
metadata.

**2. No chunk overlap.** Chunks are cut at paragraph boundaries with nothing carried across. An
answer that straddles a boundary retrieves poorly. Overlap is the most common recall win in
retrieval systems and §3.2 explicitly permits it.
*Decision: implement 1–2 sentences of overlap on prose formats only* — repeating CSV rows would
add nothing and inflate an already table-heavy index.

**3. Block classification is crude.** The chunker labels any block of ≤10 words a title, so
`"El riesgo es alto."` becomes a section header. The thresholds are unvalidated constants.
*Decision: remove it entirely* — structure detection belongs to extraction, and titles should
come from real markers or the catalog, never from word-count guesses.

**4. Document aggregation is untested.** Max-pooling is chosen on sound reasoning, but there is
no ground truth to validate it against. It determines F1@3, which is half the score.

**5. Empty documents can never be retrieved.** Catalog files and a handful of failed scrapes
produce zero chunks, so they are absent from the index entirely.
*Decision: leave them out.* This was investigated thoroughly. Two rescue proposals were tested
and both rejected: the sub-threshold text turns out to be site navigation menus (CENIA), a
date-stamped label (SWF newsletters), or a meta-description that names figures without
containing any (ESA). Ten of the twenty catalog files are not even in ADL's inventory, so they
cannot appear in the ground truth. **The principle: a chunk must carry information that could
*answer* a question, not merely signal that a document on the topic exists.**

**6. No post-filters yet.** Similarity threshold and metadata filters are permitted (§8.7) and
plumbed, but unjustifiable without validation data.

**7. Language detection is advisory.** `langdetect` is seeded for reproducibility and runs per
document with a word floor, abstaining rather than guessing on short text. It is still
unreliable on code-mixed documents, and every chunk inherits the document's label.

**8. MEASURED: 44% of indexed text never reaches a vector.** This was a theoretical risk until
the end-to-end run measured it, and the answer is far worse than expected.

`num_tokens` against the 512-token encoder ceiling, on a real 326-chunk index:

| p50 | p95 | p99 | max |
|---|---|---|---|
| 295 | 1,141 | 5,098 | **8,947** |

**94 of 326 chunks (29%) exceed 512 tokens**, and **71,821 of 163,815 tokens — 44% of all
indexed text — is silently discarded** by the encoder.

The cause is items 1 and the chunker's oversized-block path together: `MAX_WORDS = 250`
controls *packing*, but a single block larger than that is emitted whole. One 3,000-word
paragraph becomes one 3,000-word chunk. A CSV becomes one 4,170-word chunk.

This is a §4.3 violation, and it is invisible without measuring: retrieval still returns
valid, plausible answers because the surviving first 512 tokens are real text. Nothing errors.
Nearly half the corpus is simply not searchable.

*It also proves the two pending chunker fixes are load-bearing rather than tidying.* After
they land, re-measure; target p99 under 512, and lower `MAX_WORDS` if it still runs close —
before the full encode, since that parameter invalidates every downstream artifact.

**9. Flat index, no approximate alternative evaluated.** Deliberate — §5.2 endorses flat at this
scale and it stays fast at 110,000 vectors.

**10. Architectures B and C deferred.** Only the single-encoder dense architecture is being
built. A BGE-M3 dense+sparse hybrid is rated better-supported by the team's own research, and
is cheap to add later: the text cache is encoder-independent, so adding a second encoder
re-encodes but never re-extracts.

---

## 10. How to run everything

```bash
# Component self-checks — no GPU, no model download, no network
python extraccion_final.py            # 17 JSON fixtures + catalog matching
python generador.py selftest          # 11 checks incl. standalone delivery

# Index a corpus (once pipeline_final.py exists)
python pipeline_final.py index --corpus "CORPUS CODEFEST AD ASTRA 2026" --encoder e5

# Produce the answers
python generador.py \
  --index    entrega/base_vectorial/encoder_multilingual-e5-large/index.faiss \
  --metadata entrega/base_vectorial/encoder_multilingual-e5-large/metadata.jsonl \
  --queries  Extracto_Preguntas_50_v2.pdf \
  --out      entrega/resultados.jsonl

# On a Colab T4
./colab_generador.sh
```

### Environment traps that will cost you an afternoon

- **`sentence-transformers` does not import on the Windows development machine** — pyarrow
  trips an Application Control policy. All local testing therefore uses stub encoders; real
  encoding happens on Colab.
- **Git Bash rewrites `/content/...` into `C:/Program Files/Git/content/...`** on its way to
  the Colab CLI. Set `MSYS_NO_PATHCONV=1`.
- **…but that also stops *local* paths converting**, so with it set, local paths must be
  relative. Absolute local paths break.
- **The Colab contents API does not create directories**, and uploading into a nested path
  fails even after `mkdir`. Ship one zip and unpack it on the VM.
- **A 327 MB upload gets reset** by the proxy. Per-file, or one modest zip.
- **The Colab VM can be recycled mid-run**, and the dead session then holds the GPU quota as an
  orphan that the CLI cannot kill. Clear it from the web UI.
- **A vanished session is not proof the run failed.** One OCR run went 404 with its output file
  gone and looked dead at 27/50; it had actually completed all 50 files, and the polling
  download simply raced the teardown. Read the job's own stdout before concluding anything.

---

## 11. Glossary

**Chunk / fragmento** — a piece of a document, a few hundred words, encoded as one vector.

**Cosine similarity** — angle between two vectors, −1 to 1. On normalised vectors it equals the
dot product.

**Embedding / vector** — a list of numbers (1,024 here) representing a text's meaning, such
that similar meanings are geometrically close.

**Encoder** — a model that turns text into an embedding. It generates no text, which is why it
is permitted where generative models are not.

**FAISS** — Facebook AI Similarity Search. Stores vectors, finds nearest neighbours fast.
Mandatory here.

**F1@3** — the score for the 3 returned documents, treated as a set.

**Fenómeno** — one of the three corpus themes (1, 2, 3).

**`fuente`** — the original ADL file a chunk came from. **The key the ground truth matches on.**

**IndexFlatIP** — a FAISS index that compares against every vector exactly, using inner
product.

**NDCG@10** — the score for the 10 returned fragments, rewarding relevance *and* good ordering.

**Normalisation** — scaling a vector to length 1, so dot product equals cosine similarity.

**OCR** — optical character recognition; reading text from an image of a page.

**Token** — the sub-word unit an encoder actually counts. Roughly 1.3 tokens per word. The
encoder ceiling is 512.
