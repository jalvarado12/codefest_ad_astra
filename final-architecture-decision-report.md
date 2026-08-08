# Final Architecture Shortlist — Embedding/Retrieval Decision Report
### CODEFEST AD ASTRA 2026, Etapa 1 — Universidad de los Andes / Fuerza Aeroespacial Colombiana

**Purpose of this document.** This report compiles the three finalist embedding architectures that emerged from the project's research process, why each made the shortlist, what performance each is expected to deliver, what domain the supporting evidence actually comes from (so the team can judge how far that evidence transfers to ES/EN/PT defense-and-space-security documents), and the full reasoning trail that got us here. It is a decision aid, not a mandate — the final call on which architecture to build is still the team's.

---

## 1. How we got here — the full research trail

This report is the end product of a multi-stage research process. Documenting the path matters because each stage eliminated options for specific, evidence-backed reasons — understanding *why* something was cut is as important as knowing what survived.

### Stage 1 — Broad survey against the project spec
Starting from the project spec's hard constraints — encoder-only (BERT-family) architectures, open-source/self-hostable, ES/EN/PT multilingual, ~512-token practical chunk ceiling, cosine similarity on L2-normalized vectors, dense-retrieval-specific benchmarks (MTEB-retrieval/BEIR/MIRACL, not general MTEB) — we ran a full web-sourced research pass and produced two deliverables:
- **`embedding-models-survey.md`**: 11 open-source, self-hostable, encoder-only candidates (BGE-M3, the multilingual-E5 family, GTE-multilingual, Snowflake Arctic-Embed v2 (l/m), jina-embeddings-v3, nomic-embed-text-v2-moe, paraphrase-multilingual-mpnet, LaBSE), each with sourced specs and an explicit ES/EN/PT coverage note.
- **`multi-embedding-architectures.md`**: six architecture families for combining multiple encoders (single-encoder baseline; two dense encoders + RRF; two dense encoders + CombSUM/CombMNZ; dense+sparse hybrid; per-language routing; vector-level/projection fusion), each with cited evidence on expected gains/costs versus a single-encoder baseline.

Key structural finding from this stage: **decoder-derived "encoder" models were excluded outright** (Qwen3-Embedding, e5-mistral, NV-Embed-v2, Llama-Embed-Nemotron — all banned by the encoder-only rule), and **proprietary APIs were excluded regardless of benchmark strength** (OpenAI, Cohere, Google, Voyage — banned by the self-hosted rule). EmbeddingGemma-300M was flagged as a boundary case (encoder adapted from a decoder-pretrained Gemma 3 base) and not counted as a clean candidate.

Also established at this stage: **Portuguese is a field-wide benchmarking gap.** MIRACL (18 languages) and the CLEF set used in several comparisons both exclude Portuguese, so every model's PT retrieval quality is *inferred* from general multilingual coverage, not directly measured. This caveat carries through every subsequent stage of this report.

### Stage 2 — Narrowing to a direct recommendation
Asked directly for a recommendation (rather than trade-offs only), we proposed three candidate directions, in order of preference:
1. **Single encoder — `multilingual-e5-large`**: lowest risk, native 512-token ceiling matches the spec's chunk budget almost exactly, best-documented model in the survey.
2. **`BGE-M3`'s built-in dense+sparse hybrid**: fusion's benefits (the best-evidenced gain in the literature is dense+sparse, not dense+dense) at close to single-model compute cost, since BGE-M3 emits both representations from one forward pass.
3. **Dense (e5-large) + sparse (external, e.g., BM25) fused via RRF**: flagged as stepping slightly outside pure-HuggingFace-encoder scope.

### Stage 3 — Finding a HuggingFace-native sparse encoder
Asked whether a good sparse option exists *within* HuggingFace (avoiding BM25's scope ambiguity), targeted web search surfaced:
- **`opensearch-project/opensearch-neural-sparse-encoding-multilingual-v1`** — Apache 2.0, BERT-based, 160M params, trained on the MIRACL dataset, inference-free at query time (a tokenizer + IDF lookup, no forward pass needed for queries), with Spanish directly benchmarked (MIRACL nDCG@10 `es` = 0.542, versus BM25's 0.077 on the same language).
- Re-confirmed **BGE-M3's own sparse head** as a second, arguably simpler, option — since it comes from the same forward pass as its dense output, it adds no extra indexing cost.

This replaced option 3 above with two more concrete, evidence-backed sparse choices, both fully within the HuggingFace/encoder-only/open-source constraints.

### Stage 4 — BGE-M3-only hybrid vs. a mixed-model hybrid
Asked to choose between **BGE-M3 dense+sparse (same model)** and **e5-large dense + BGE-M3 sparse (two different models)**, targeted research found:
- BGE-M3's dense and sparse heads are **jointly trained via self-knowledge distillation**, specifically so the two heads reinforce each other. Disabling that joint distillation and training the heads separately **hurts the sparse head by ~17 points of nDCG@10 on MIRACL** (Chen et al., 2024, BGE-M3 technical report, via Emergent Mind synthesis). This is a direct mechanism by which pairing BGE-M3's sparse output with a *different* model's dense output forfeits a designed synergy.
- A financial-domain retrieval study (see Section 3.B below) directly compared BGE-M3 dense, multilingual-e5-large dense, and BGE-M3's own dense+sparse hybrid on the same task and found BGE-M3's self-hybrid outperformed both dense-only baselines.
- No study was found benchmarking the *specific* mixed pairing (e5-large dense + BGE-M3 sparse) against BGE-M3's own hybrid — so the case for mixing model families remains a plausible but **untested hypothesis**, not evidence-backed.

Conclusion of this stage: **BGE-M3 dense+sparse (same model) is the better-supported hybrid**, not the mixed pairing.

### Stage 5 — Compute/time estimation for ~2000 documents
Asked to estimate indexing time across three architectures, we found:
- CPU latency for e5-large and BGE-M3 is nearly identical (~31 ms/item on Apple M-series CPU, unbatched) — Cirillo et al., 22 May 2026.
- BGE-M3 GPU throughput is cited at ~100 docs/sec/GPU for dense+sparse combined (since it's one forward pass) — Chen et al., 2024, via Emergent Mind synthesis.
- **Structural insight carried into this report**: because BGE-M3's sparse output is "free" (same forward pass as dense), a BGE-M3-only hybrid costs the same as running BGE-M3 dense alone. A mixed pairing (e5-large + BGE-M3 sparse) requires *two full model passes* over every chunk — roughly double the indexing compute of either single-model option.

This compute asymmetry is now a first-class factor in the shortlist below, not just a benchmark-quality question.

---

## 2. The three finalist architectures

| | **A — Single Encoder** | **B — BGE-M3 Native Hybrid** | **C — Mixed Hybrid** |
|---|---|---|---|
| Composition | `multilingual-e5-large` dense only | `BGE-M3` dense + `BGE-M3` sparse (same model) | `multilingual-e5-large` dense + `BGE-M3` sparse (two models) |
| Fusion needed | None (single index) | Score/rank fusion (RRF recommended) across dense/sparse outputs of one model | Score/rank fusion (RRF recommended) across two independent models |
| Forward passes per chunk | 1 | 1 | 2 |
| Relative indexing compute | 1× (baseline) | ~1× | ~2× |

---

### A. Single encoder — `intfloat/multilingual-e5-large`

**Why it's a finalist.** It is the lowest-risk, best-documented option in the survey, and its native constraints line up almost exactly with the project's own: a 512-token ceiling against the spec's ~512-token practical chunk limit means no wasted model capacity and no truncation surprises. It requires no fusion logic, no second model to serve, and no score-normalization tuning — the entire failure surface of a retrieval system is one encoder and one FAISS index.

**Specs (recap).** XLM-RoBERTa-large backbone, 560M params, 1024-dim, mean pooling, L2-normalized (cosine via inner product), MIT license, 512-token max input. Requires `"query:"` / `"passage:"` prefixes at inference — a mandatory usage detail, not optional.

**Expected performance.** MIRACL nDCG@10 = 66.5, averaged over 16 languages, "significantly outperforms mDPR" on the same benchmark (Wang et al., 2024, *Multilingual E5 Text Embeddings: A Technical Report*, arXiv 2402.05672). Spanish is part of MIRACL's language set; Portuguese is not, so PT quality is inferred from the model's general 100-language coverage claim rather than directly measured.

**Domain of the underlying training/eval data.** The mE5 training recipe draws on large-scale, broadly-sourced multilingual text pairs (web-mined parallel/near-parallel text, community QA, NLI-style pairs) — a general-web domain, not defense, aerospace, or security-specific. MIRACL's own retrieval collections are Wikipedia-based. **This means none of e5-large's benchmark numbers were measured on anything resembling this project's actual corpus (defense policy documents, space-security literature, territorial-dynamics analysis).** Treat the 66.5 figure as a general multilingual-competence signal, not a domain-matched performance guarantee.

**Compute cost.** Baseline (1×). ~31 ms/item on CPU (Apple M-series, unbatched); ~30 queries/sec on a V100 GPU at batch size 1 (unbatched, so likely conservative — real batched throughput would be higher, though no directly-sourced batched figure was found for this model specifically).

**Known risks.**
- PT quality unverified — same as every other candidate in this report, but worth restating since this is the "no fusion upside" option, meaning there's no sparse/lexical signal to fall back on if dense-only recall is weak on unusual terminology (acronyms, orbital designators, unit names).
- No mechanism for exact-term matching — dense embeddings alone tend to blur exact identifiers, which may matter for defense/space technical vocabulary.

---

### B. BGE-M3 native hybrid — dense + sparse, same model

**Why it's a finalist.** This is the architecture with the strongest *combination* of evidence: the best-supported fusion pattern in the literature is dense+sparse (not dense+dense), BGE-M3 produces both representations from a single forward pass (so the fusion upside comes at negligible extra indexing cost versus option A), and its two heads are specifically co-trained to complement each other.

**Specs (recap).** XLM-RoBERTa-large backbone (dense) / same backbone's lexical-weight output (sparse), 1024-dim dense output (CLS pooling, L2-normalized), sparse output over the model's vocabulary, up to 8192-token input (only ~512 of which the project's chunking would typically use), MIT license, ~568M params.

**Expected performance — general multilingual.** BGE-M3's dense-only MIRACL score is reported at 67.8 nDCG@10, reached via a multi-stage training path that improved from 59.3 → 64.8 → 67.8 across training stages (Chen et al., 2024, *BGE M3-Embedding*, arXiv 2402.03216, cross-referenced via Emergent Mind synthesis). Disabling the self-knowledge distillation that ties the dense and sparse heads together **costs the sparse head ~17 points of nDCG@10 on MIRACL** — direct evidence that the hybrid is a designed, trained-for behavior, not an incidental one.

**Expected performance — domain-specific evidence ("BGE in finance").** The most concrete head-to-head evidence available comes from a **financial information-retrieval study** (arXiv 2511.05000, *Query Generation Pipeline with Enhanced Answerability Assessment for Financial Information Retrieval*), which compared multiple retrieval modes on a banking/finance retrieval task:

| Mode | Model | NDCG@5 |
|---|---|---|
| Sparse only | BM25 | 0.1768 |
| Sparse only | BGE-M3 (sparse) | 0.5722 |
| Dense only | multilingual-e5-large | 0.6165 |
| Dense only | BGE-M3 (dense) | 0.6452 |
| Multi-vector | BGE-M3 (ColBERT-style) | 0.6408 |
| **Hybrid** | **BGE-M3 (dense + sparse)** | **0.6795 (best overall)** |

**This is the domain caveat the team should weigh carefully.** The 0.6795 figure — the strongest evidence for this architecture — comes from **financial/banking retrieval**, not defense, space security, or geopolitical/territorial analysis. It confirms that BGE-M3's hybrid mode beats its own dense-only mode and beats e5-large's dense-only mode *in that domain*. It does **not** confirm the same ordering holds for this project's specific corpus and query style — financial documents have different lexical density, jargon patterns, and query phrasing than defense-and-space-security literature. The direction of the effect (hybrid > dense-only, BGE-M3 > e5-large) is a reasonable prior; the *magnitude* should not be assumed to transfer.

**Compute cost.** ~1× the cost of option A — the sparse output is "free" once the dense forward pass has run. CPU latency is nearly identical to e5-large (30.9 ms vs. 31.0 ms/item, Cirillo et al., 22 May 2026). GPU throughput cited around 100 docs/sec/GPU for dense+sparse combined (Chen et al., 2024, via Emergent Mind synthesis — a secondary-source aggregation, flagged as such since it wasn't independently re-verified against the primary paper's raw tables).

**Known risks.**
- Same PT-inference gap as every other model.
- The best supporting evidence for the hybrid's magnitude of improvement is out-of-domain (finance, not defense/space).
- 8192-token capacity goes almost entirely unused given the spec's ~512-token chunk ceiling — not harmful, just unnecessary overhead in the model's design relative to this task.
- Requires implementing score or rank fusion (RRF recommended per the earlier architecture research) between the dense and sparse outputs — a small but real implementation step versus option A's simplicity.

---

### C. Mixed hybrid — `multilingual-e5-large` dense + `BGE-M3` sparse

**Why it's a finalist (with reservations).** Included specifically because it was the team's original instinct and because the *theoretical* case for it — pairing two independently-trained models might catch more diverse failure modes than one model's self-consistent hybrid — is not unreasonable on its face. It is presented here as the "worth testing if you have spare compute budget" option rather than a primary recommendation, for reasons detailed below.

**Specs.** Combines A's dense output (1024-dim, e5-large) with a *separately computed* BGE-M3 sparse output (vocabulary-dimension sparse vector). Requires running both models over every chunk at indexing time, and fusing their independently-ranked or independently-scored outputs (RRF recommended for the same robustness reasons as in the original architecture research: no score-normalization dependency, insensitive to differing score scales between the two models).

**Expected performance.** No study was found that directly benchmarks this specific pairing. What *is* available is indirect and, on balance, cautionary:
- The self-knowledge-distillation finding (Section 3.B above) shows that BGE-M3's sparse head is tuned to complement *BGE-M3's own dense head specifically* — pairing it with e5-large's dense output forfeits that tuned relationship. There is no equivalent evidence that BGE-M3's sparse head transfers its complementary behavior to a different dense encoder's output.
- More generally, research on multi-retriever fusion (LED study, arXiv 2208.13661) found that fusing two dense retrievers underperforms fusing one dense + one lexicon-aware retriever, because similar dense models make correlated errors — this doesn't directly apply here (one side is sparse, not dense), but it illustrates that fusion gains are not automatic just because two different models are involved.
- The finance-domain study above suggests e5-large's dense output (0.6165) is itself somewhat weaker than BGE-M3's dense output (0.6452) in that domain — meaning this architecture may be pairing a weaker dense signal with the same sparse signal that performed best *alongside its own, stronger, co-trained dense counterpart*.

**Domain of underlying evidence.** Same caveat as options A and B individually — e5-large's numbers come from general multilingual web-text training/eval, BGE-M3's numbers (including the finance data point) come from a mix of Wikipedia-based MIRACL and the finance-domain study. No source evaluates this exact combination in any domain, defense/space or otherwise.

**Compute cost.** ~2× option A or B — this is the architecture's clearest, most confidently-sourced drawback. Two full model forward passes are required over every chunk at indexing time (e5-large's dense pass, BGE-M3's pass to obtain its sparse output), roughly doubling indexing time and requiring both models loaded in memory simultaneously. Under the spec's stated limited-compute constraint, this is a real, quantifiable cost that the other two options don't carry.

**Known risks.**
- Highest compute cost of the three, for a performance benefit that is currently a hypothesis rather than a citation.
- Forfeits BGE-M3's designed dense-sparse synergy without a demonstrated replacement benefit.
- Adds the most implementation complexity (two models to serve, version, and keep in sync) for the least-certain payoff.

---

## 3. Cross-architecture comparison

| Dimension | A: e5-large only | B: BGE-M3 hybrid | C: Mixed hybrid |
|---|---|---|---|
| Implementation complexity | Lowest — one model, one index | Low-medium — one model, one fusion step | Highest — two models, one fusion step |
| Indexing compute (relative) | 1× | ~1× | ~2× |
| Best-sourced performance evidence | MIRACL, general multilingual (Wikipedia-domain) | MIRACL (general) + finance-domain hybrid study | None specific to this pairing |
| Domain match to project corpus | None (general web) | None (general web + finance) | None |
| Exact-term / acronym matching | Weak (dense-only) | Strong (sparse component) | Strong (sparse component) |
| Portuguese retrieval quality | Inferred, unmeasured | Inferred, unmeasured | Inferred, unmeasured |
| License | MIT | MIT | MIT (both components) |
| Fusion tuning required | None | RRF between one model's two heads | RRF between two models' outputs |

---

## 4. Open questions the team still needs to resolve

This report narrows the field to three defensible options — it does not close the decision. Before committing, the team should be aware that:

1. **No architecture here has been evaluated on anything resembling the actual corpus** (AI-in-defense, LEO space debris/security, Latin American territorial dynamics). All performance evidence is either general-multilingual (Wikipedia-domain, via MIRACL) or, for BGE-M3's hybrid specifically, drawn from a financial-retrieval study. A small labeled validation set from the real corpus, run through all three options, would be worth more than any of the cited literature.
2. **Portuguese retrieval quality is unverified for every candidate** — this is a field-wide benchmarking gap (MIRACL and CLEF both exclude PT), not a weakness specific to one model. If PT performance turns out to be the deciding factor, none of the cited benchmarks will settle it; only a direct test will.
3. **Option C's central hypothesis is untested.** If the team has spare compute budget, it would be scientifically useful to run C alongside B on the same validation set — either it confirms the diversity argument, or it confirms this report's caution that mixing models forfeits BGE-M3's trained-in synergy without compensating benefit.
4. **Chunk count, not document count, drives indexing time** for all three options (see the earlier compute-estimation discussion) — actual wall-clock time should be measured on a small sample of the real 2000-document set before finalizing any compute budget.

---

## 5. Source list

- Wang, L. et al. (2024). *Multilingual E5 Text Embeddings: A Technical Report*. arXiv:2402.05672.
- Chen, J., Xiao, S., Zhang, P., Luo, K., Lian, D., Liu, Z. (2024). *BGE M3-Embedding: Multi-Lingual, Multi-Functionality, Multi-Granularity Text Embeddings Through Self-Knowledge Distillation*. arXiv:2402.03216.
- *Query Generation Pipeline with Enhanced Answerability Assessment for Financial Information Retrieval*. arXiv:2511.05000.
- Cormack, G., Clarke, C., Buettcher, S. (2009). *Reciprocal Rank Fusion outperforms Condorcet and individual Rank Learning Methods*. SIGIR 2009.
- *LED: Lexicon-Enlightened Dense Retriever for Large-Scale Retrieval*. arXiv:2208.13661.
- Thakur, N. et al. (2021). *BEIR: A Heterogeneous Benchmark for Zero-shot Evaluation of Information Retrieval Models*. arXiv:2104.08663.
- Cirillo et al. (22 May 2026). *Benchmarking Google Embeddings 2 against Open-Source Models for Multilingual Dense Retrieval and RAG Systems*. arXiv:2605.23618.
- OpenSearch Project. *opensearch-neural-sparse-encoding-multilingual-v1* model card, HuggingFace. Paper: *Towards Competitive Search Relevance For Inference-Free Learned Sparse Retrievers*, arXiv:2411.04403.
- *Beyond Multilingual Averages: MTEB-PT, a Benchmark for Portuguese Sentence Encoders*. arXiv:2607.04071.
- *MTEB-PT: A Text Embedding Benchmark for Brazilian Portuguese*. arXiv:2607.04581.
- BAAI/bge-m3 and intfloat/multilingual-e5-large model cards, HuggingFace (license, pooling, normalization, and usage confirmation).
- Emergent Mind synthesis pages for BGE-M3 and multilingual-e5-large (secondary aggregation of the above primary sources — flagged inline wherever used, not treated as independently primary).

*This document supersedes no prior deliverable — it is a decision-focused summary built on top of `embedding-models-survey.md` and `multi-embedding-architectures.md`, narrowed through the direct Q&A that followed them.*
