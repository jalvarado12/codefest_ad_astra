# Executor prompt — CODEFEST Etapa 1 pipeline

Copy everything below the line into the executor agent. It assumes no prior conversation.

---

## Role

You are implementing an already-designed, already-audited pipeline for the CODEFEST AD ASTRA
2026 Etapa 1 challenge (a retrieval / knowledge-base task). The design phase is **finished**.
Your job is to write the code the specs describe, verify it, and report — not to redesign it.

Working directory: `C:\Users\User\Downloads\AD_Astra\codefest_ad_astra`
Corpus: `C:\Users\User\Downloads\AD_Astra\CORPUS CODEFEST AD ASTRA 2026`

## Read first, in this order

| File | Why |
|---|---|
| `specs/00-measurements.md` | Every number the design rests on, with its method. **Do not re-measure anything recorded here.** |
| `specs/01-current-state-and-target-architecture.md` | What exists, what it must become, the binding constraints |
| `specs/02-migration.md` | **Your work order.** Steps 0–6, each with a runnable check |
| `specs/03-next-steps-and-debt.md` | Known debt and deferred work — context, not tasks |
| `pipeline-plan.md` | One-page index and the settled decision table |

The challenge spec itself is `C:\Users\User\Downloads\CODEFEST_2026-1.pdf` (24 pp). A markdown
conversion, verified verbatim-identical on every clause checked, is on the `embedding` branch:
`git show origin/embedding:ad_astra.md`. Section numbers cited in the specs (§1.4, §3.3, §9.2)
refer to it.

## What you are building

```
corpus → extraccion_final.py → cache/textos.jsonl → chunker.py (in memory)
                                      ↓
                    vectors/<enc>/{dense.npy, ids.json}
                                      ↓
                              faiss.IndexFlatIP
                                      ↓
        entrega/base_vectorial/encoder_<enc>/{index.faiss, metadata.jsonl}
                                      ↓
                     generador.py → entrega/resultados.jsonl
```

Files to create: `pipeline_final.py`, `generador.py`.
Files to modify: `chunker.py`, `extraccion_final.py`, `requirements.txt`.
Everything else is generated and gitignored.

## Non-negotiable constraints

These lose points or disqualify. They are quoted from the spec, not inferred.

1. **FAISS is mandatory** (§5.1). `index.faiss` written with `faiss.write_index()`.
2. **`metadata.jsonl` line order must equal FAISS internal ids** (§1.4). This is the one
   invariant with a dedicated test: for every `i`, `ids.json["ids"][i]` == FAISS id `i` ==
   `dense.npy` row `i` == `metadata.jsonl` line `i`. Generate `metadata.jsonl` by walking
   `ids.json`; never maintain it by hand.
3. **No generative/decoder models anywhere** (§8.3). No LLM reranking, query expansion,
   filtering or summarisation. Encoders and arithmetic only.
4. **`resultados.jsonl`: exactly 50 lines, exactly 3 `documents`, exactly 10 `fragments`, every
   fragment ≤250 words** (§9.3.1/§9.3.2). Violations are *"penalizados o descartados"*.
5. **Eight mandatory chunk fields** (§3.4 Tabla 1): `doc_id`, `chunk_id`, `fuente`, `formato`,
   `fenomeno`, `posicion`, `num_tokens`, `texto`. Extras are permitted.
6. **`generador.py` must reproduce standalone** (§1.4) — *"Si no es posible reproducir los
   resultados, se excluirá de la evaluación."* It may not import from the repo. Inline what it
   needs.
7. **Field-name trap:** `metadata.jsonl` uses `texto` (Tabla 1); `resultados.jsonl` uses `text`
   (Tabla 2). Different files, different key names. Assert both.

## Decisions already settled — implement, do not relitigate

Full list in `pipeline-plan.md`. The ones most likely to tempt you:

- **`fuente` carries the relative POSIX path**, with `nombre_archivo` and `adl_doc_id` as
  extras. An audit argued for the bare basename; it was rejected because basenames are **not
  unique** (114 PDF rows share 47 names). Do not "simplify" this.
- **One document per `.pbf`**, not per tileset (§2.3).
- **Max-pooling** for document aggregation. Not sum — a single CSV would win every query.
- **Empty documents stay empty.** Investigated and closed: the sub-threshold text is
  navigation menus and meta-descriptions. Do not synthesise chunks for them.
- **Chunking budgets words, not tokens.** This keeps local and Colab boundaries identical. Do
  not inject a tokenizer into chunking. `num_tokens` is stamped at embed time.
- **The chunker does chunking only.** Cleaning and block classification were deliberately
  removed and belong to `extraccion_final.py`. Do not restore them.
- **LlamaIndex is not used.** It was considered and cut as redundant with `metadata.jsonl`.

## Environment facts

- Windows 11, Python 3.10. `faiss` and `langdetect` installed; `llama_index` is not, and is
  not needed.
- **`sentence-transformers` and `FlagEmbedding` do not import on this box** — pyarrow trips a
  Windows Application Control policy. Real encoding is Colab-only. Everything you build must
  therefore run locally with a **stub encoder**, and the self-checks must not need real models.
- **OCR is measured at 83 s/page on this CPU** (6 files, 1–8 pages, ±10%). 50 PDFs totalling
  613 pages lack a text layer ⇒ **~14 hours for a full local pass. Never trigger one.**

### The GPU split — read Step 7 before planning any full run

Extraction runs on Colab with `gpu_ocr=True` (**pass A**) and produces `cache/textos.jsonl`.
Everything downstream — chunking, self-checks, `generador.py`, the dry run — then runs locally
against that cache at zero OCR cost. Encoding and indexing go back to GPU (**pass B**).

The text cache is an *output* of Colab, not an input to it. An earlier draft had this
backwards. Its `sha256` key is environment-independent, so moving it between machines is safe.

Pass A depends only on Step 2, not on the chunker, so it can overlap with chunker work. If you
need extracted text locally before pass A exists, use `--sample` on files that are **not** in
the 50-PDF OCR set.

## If you are running in a git worktree

`git worktree add` copies **tracked** files only. The `specs/` directory, `pipeline-plan.md`
and `Extracto_Preguntas_50_v2.pdf` are all **untracked** in the main repo, so a fresh worktree
will not contain them. Copy them in from
`C:\Users\User\Downloads\AD_Astra\codefest_ad_astra` before starting, and do not modify the
originals. Both executor agents hit this independently.

## Repo gotchas, already verified

- `chunker.py` at the root is **0 bytes**. The real chunker is `chunker (1).py` — delete the
  empty file and rename. The parenthesised name breaks `import chunker`.
- `documentos_easyocr.jsonl` is **0 bytes** despite its name.
- `requirements.txt` pins no versions and omits `torch` and `sentence-transformers`.
- `E5Dense.encode_passages` defaults to `batch_size=8`; use 64.
- Three non-corpus files sit inside the corpus tree and must be excluded:
  `Extracto_Preguntas_50_v2.pdf`, `Indice_Datos_Codefest.xlsx`,
  `F3_Dinamicas_Territoriales/FASE ORDENADA CODEFEST.xlsx`.

## How to work

Follow `specs/02-migration.md` Steps 0 → 6 in order. Steps 1 and 2 are independent; Step 5
(`generador.py`) needs only `metadata.jsonl`'s *shape* and can be built against a synthetic
index.

For each step:

1. Implement exactly what the spec says.
2. Write the step's self-check as specified there — `assert`-based, in a `selftest`
   subcommand, no test framework, stub encoders where a model would be needed.
3. **Run it.** Report the real output, including failures. Never report a check as passing
   without having run it.
4. Move on only when it passes.

Match the existing house style: self-contained files, Spanish identifiers and comments in the
extraction layer, `assert`-based `selftest` subcommands (see `extraccion_final.py`,
`chunker.py`, `embedding_pipeline/embeddings_only.py` for the pattern).

## Stop and ask when

- A spec instruction is ambiguous or contradicts another spec.
- You believe a spec decision is **wrong**. Say so with evidence and stop; do not quietly
  implement something different. Several audits have already changed this design, and finding
  a real defect is more valuable than finishing the step.
- A measurement in `00-measurements.md` disagrees with what you observe.
- Any step needs GPU, network, or a real model download.

## Do not

- Run the Colab full pass (Step 7) or any full-corpus OCR.
- `git commit`, `git push`, or touch the `embedding` branch.
- Start `informe_tecnico.pdf`. Explicitly out of scope.
- Build the §7 knowledge graph, or architectures B/C.
- Re-run measurements already in `00-measurements.md`.

## Definition of done

Steps 0–5 implemented, every self-check written and **passing with output shown**, plus:

- `python chunker.py` — chunk-boundary and Tabla-1 checks pass
- `python pipeline_final.py selftest` — caching, resume, alignment, round-trip pass
- `python generador.py selftest` — schema, 250-word cap, `-1` guard, standalone-delivery pass
- Step 3a inventory reconciliation run, output reported
- A short written summary: what was built, what each check proved, what surprised you, and
  anything you think the specs got wrong

Then stop and wait. Step 6 (dry run) is a gate the user runs and reviews, not an automatic
continuation.
