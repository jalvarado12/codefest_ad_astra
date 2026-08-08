# Embedding-Model Selection Report — CODEFEST AD ASTRA 2026, Etapa 1

Prepared as a research-analyst deliverable for the Universidad de los Andes / Fuerza Aeroespacial Colombiana project. **Trade-offs only — no implementation choices and no final model recommendation.** Every candidate below is confirmed encoder-only (BERT-family, bidirectional), open-source, self-hostable, and downloadable from HuggingFace under a stated license. Every spec and benchmark figure is traced to a live source found during this research; where a claim cannot be sourced (e.g., Portuguese-specific retrieval quality for models not covered by a PT benchmark), it is flagged explicitly rather than asserted.

---

## Scope, method, and cross-cutting cautions

**What is in scope:** only the embedding/encoding step — which encoder turns a text chunk into a vector. Reranking, FAISS index type, chunking, and the knowledge-graph bonus are mentioned only where they explain an embedding trade-off.

**Hard filters applied to every candidate:**
- *Encoder-only (§4.2/§8.3):* Decoder/autoregressive embedders — Qwen3-Embedding (Qwen3 backbone), e5-mistral-7b-instruct (Mistral-7B), NV-Embed-v2 (Llama-3.1-8B), Llama-Embed-Nemotron-8B, KaLM-Embedding-Gemma3 — are **excluded** as banned, regardless of leaderboard strength. They appear only as benchmark context.
- *Open-source & self-hostable (§4.3):* Proprietary/API-only models (OpenAI text-embedding-3, Cohere Embed-v3/v4, Google Gemini/"GE2"/text-embedding-4, Voyage) are **excluded as candidates** and appear only as prose benchmark context. Per one 2026 leaderboard snapshot (Ailog, July 2026), Cohere embed-v4 scores 65.2 and OpenAI text-3-large 64.6 on MTEB — cited purely to situate the open models, never as options.
- *License preference (§4.3):* Apache 2.0 / MIT / CC BY preferred. One otherwise-strong candidate (jina-embeddings-v3) is CC BY-NC 4.0 (non-commercial) and is flagged as a license caution rather than a clean fit.

**Cross-cutting caution — multilingual averages mislead on Portuguese.** The single most important research finding for this project: a model's MMTEB multilingual *average* does **not** reliably predict its Portuguese retrieval quality. The MTEB-PT / MTEB-BR benchmark papers (arXiv 2607.04071 and 2607.04581, 2026) evaluate 17–93 models on *native* Brazilian-Portuguese tasks and find "model rankings in Portuguese are strongly task-dependent, with multilingual strength not transferring uniformly across task families." They document dramatic divergences — e.g., Llama-Embed-Nemotron-8B ranks 3rd of 55 on the multilingual board but 49th on MTEB-PT, "the largest such divergence," concentrated specifically in retrieval. MTEB-PT also finds "models with stronger long-context capacity are particularly advantageous on longer-input tasks such as retrieval and reranking." **Practical consequence:** for any model below whose retrieval scores come only from MIRACL or CLEF, Portuguese is *inferred, not measured* — because MIRACL's 18 languages include Spanish but **not** Portuguese, and CLEF (as used in the Arctic evaluations) averages German/English/Spanish/French/Italian — again no Portuguese. This is flagged per-model.

**Cross-cutting caution — input-length ceiling (§4.3).** Encoders reduce any input up to their token ceiling to one fixed-dimension vector via pooling, so chunk length is bounded by the encoder's max tokens, not by dimensionality. Several candidates advertise 8192-token context (BGE-M3, GTE, Arctic v2, Jina v3) — far above the ~512 practical ceiling in the spec — meaning most of that capacity would go unused here. Models whose native ceiling is ~512 (the E5 family, nomic-v2-moe) map most naturally onto the spec's chunk budget.

**Cross-cutting note — normalization (§8.2).** For every candidate below, the model card's reference code applies L2 normalization before cosine/inner-product similarity; all are meant to be used normalized. This was confirmed per model, not assumed.

---

# DELIVERABLE 1 — Embedding-Models Survey (11 candidates)

## Comparison table

| # | Model | Encoder backbone | Dim | Max tokens | Pooling | Normalize | License | Params | Dense-retrieval benchmark (source, date) | ES / EN / PT coverage note |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | BAAI/bge-m3 | XLM-RoBERTa-large | 1024 | 8192 | CLS | Yes | MIT | ~568M | MIRACL nDCG@10 SOTA at release; on Arctic paper table BGE-M3 MIRACL 0.678 / BEIR-style MTEB-R 0.488 (arXiv 2412.04506, 2024) | 100+ langs, genuine cross-lingual; ES in MIRACL, **PT not benchmarked on MIRACL** |
| 2 | intfloat/multilingual-e5-large | XLM-RoBERTa-large | 1024 | 512 | Mean | Yes | MIT | 560M | MIRACL nDCG@10 66.5 (avg 16 langs), MTEB-multi 61.5 (mE5 report, arXiv 2402.05672, 2024) | ES + PT in 100-lang list; ES in MIRACL, PT inferred; **"query:"/"passage:" prefixes required** |
| 3 | intfloat/multilingual-e5-base | XLM-RoBERTa-base | 768 | 512 | Mean | Yes | MIT | ~278M | MIRACL nDCG@10 62.3 (mE5 report, 2024) | Same coverage; weaker |
| 4 | intfloat/multilingual-e5-small | Multilingual-MiniLM-L12 | 384 | 512 | Mean | Yes | MIT | 118M | MIRACL nDCG@10 60.8 (mE5 report, 2024) | Explicit low-resource degradation warning |
| 5 | Alibaba-NLP/gte-multilingual-base | mGTE encoder (BERT+RoPE+GLU) | 768 (MRL 128–768) | 8192 | CLS | Yes | Apache 2.0 | ~305M | MTEB-R 0.511, MIRACL 0.621, CLEF 0.479 nDCG@10 (Arctic paper, arXiv 2412.04506, 2024) | 70+ langs; **ES/PT not named individually on card** |
| 6 | Snowflake/snowflake-arctic-embed-l-v2.0 | XLM-R (bge-m3-retromae) | 1024 (MRL 256) | 8192 | CLS | Yes | Apache 2.0 | 568M total/303M non-emb | BEIR 55.6, MIRACL 55.8, CLEF 54.3 nDCG@10 (HF card, Dec 2024) | CLEF incl. **ES**; PT not in CLEF/MIRACL → inferred |
| 7 | Snowflake/snowflake-arctic-embed-m-v2.0 | gte-multilingual-base | 768 (MRL 256) | 8192 | CLS | Yes | Apache 2.0 | ~305M | BEIR 55.4, MIRACL 55.2, CLEF 51.7 nDCG@10 (HF card, Dec 2024) | Same caveat |
| 8 | jina-embeddings-v3 | Jina-XLM-RoBERTa + LoRA | 1024 (MRL 32–1024) | 8192 | Mean | Yes | **CC BY-NC 4.0** | 570M | MTEB-multi avg 65.52 (Jina paper, arXiv 2409.10173, 2024) | 30 langs incl. ES/PT; **NON-COMMERCIAL license** |
| 9 | nomic-ai/nomic-embed-text-v2-moe | MoE XLM-R (nomic-bert) | 768 (MRL 256) | 512 | Mean | Yes | Apache 2.0 | 475M total/305M active | BEIR 52.86, MIRACL 65.80 (HF card, 2025) | ~100 langs incl. es/pt tags; **"search_query:"/"search_document:" prefixes required** |
| 10 | sentence-transformers/paraphrase-multilingual-mpnet-base-v2 | XLM-RoBERTa-base (SBERT) | 768 | ~128 | Mean | Yes | Apache 2.0 | 278M | Symmetric-STS baseline; not retrieval-optimized | 50+ langs incl. ES/PT |
| 11 | sentence-transformers/LaBSE | BERT dual-encoder | 768 | 512 | CLS | Yes | Apache 2.0 | 470M | MTEB-multi 45.2 (mE5 report, 2024) — weak on retrieval | 109 langs; bitext-aligned, **not retrieval-tuned** |

*(Boundary case discussed but not counted in the 11: EmbeddingGemma-300M — see end of Deliverable 1.)*

## Per-model narratives

### 1. BAAI/bge-m3 — MIT
The de-facto multilingual retrieval workhorse and a strong reference point for this project. XLM-RoBERTa-large backbone; 1024-dim dense output from a **normalized [CLS] token**; up to 8192 tokens; MIT license (confirmed in an embedding-license appendix, arXiv 2605.22202). It uniquely emits dense, sparse (lexical), and multi-vector (ColBERT-style) representations in one forward pass — directly relevant to Deliverable 2's hybrid patterns, since it gives "hybrid for one model's cost." Trained on ~1.2B pairs across up to 194 languages, it delivers *genuine* cross-lingual alignment (a query in one language retrieves passages in another), which is exactly the §1.1/§10.1 requirement. **ES is covered by MIRACL; PT is in the 100+ language set but not in MIRACL, so PT retrieval quality is inferred, not directly measured.** On the Arctic comparison table its MIRACL is very strong (0.678) but its out-of-domain CLEF (0.410) and MTEB-R (0.488) are lower than the Arctic models — a hint of MIRACL-tuning. Cons: 8192-token context is far above the §4.3 ceiling (unused capacity); larger memory footprint (~2.3GB fp16); measured ~31 ms/query on CPU in a 2026 multilingual benchmark (arXiv 2605.23618).

### 2. intfloat/multilingual-e5-large — MIT
XLM-RoBERTa-large, 560M params, 1024-dim, **mean pooling**, L2-normalized, **512-token ceiling** — a near-exact fit for the §4.3 chunk budget. MIRACL nDCG@10 of 66.5 averaged over 16 languages, and it "significantly outperforms mDPR" on that benchmark (mE5 technical report, arXiv 2402.05672). **Requires "query:" / "passage:" prefixes** — a mandatory usage detail; omitting them degrades quality. ES and PT are both in the 100-language list (ES benchmarked via MIRACL, PT inferred). A documented quirk: cosine scores cluster in 0.7–1.0 because of the low InfoNCE temperature (0.01), so only *relative* order matters — harmless for NDCG@10/F1@3 ranking. Overall the best-understood, cleanest-fit multilingual dense baseline for this task.

### 3–4. multilingual-e5-base / -small — MIT
Same recipe, smaller: base is XLM-RoBERTa-base, 768-dim, ~278M params (MIRACL 62.3); small is Multilingual-MiniLM-L12, 384-dim, 118M params (MIRACL 60.8). Both keep the 512-token ceiling and prefix convention. `small` is the cheapest genuinely cross-lingual option — 384-dim vectors cut FAISS memory ~2.7× versus 1024-dim, and it runs comfortably on CPU. Trade-off: an **explicit low-resource degradation warning** on the card; Portuguese (relatively high-resource) is likely acceptable but is unverified for retrieval. Strong candidates where the team's limited compute (§4.3) dominates the decision.

### 5. Alibaba-NLP/gte-multilingual-base — Apache 2.0
mGTE encoder-only model (BERT + RoPE + GLU), **305M params, 768-dim CLS pooling** (Matryoshka-elastic 128–768), 8192 tokens, Apache 2.0, L2-normalized for cosine (confirmed on the HF card: `F.normalize(..., p=2)` and `normalize_embeddings=True`). The card advertises "a 10× increase in inference speed" over decoder-based embedders and lower hardware requirements — attractive under §4.3. Arctic's comparison reports MTEB-R 0.511, MIRACL 0.621, CLEF 0.479. Supports 70+ languages; **ES and PT are not named individually on the card — flag as a caution** (broad multilingual coverage confirmed, per-language PT quality unverified). No prefixes required. Excellent compute/quality balance; also emits sparse vectors.

### 6. Snowflake/snowflake-arctic-embed-l-v2.0 — Apache 2.0
Built on **BAAI/bge-m3-retromae (XLM-RoBERTa encoder)**; 568M total / 303M non-embedding params; **1024-dim CLS pooling** (MRL truncation to 256, "≈3% quality degradation"), 8192 tokens, Apache 2.0. Explicitly engineered to remove the usual English-vs-multilingual trade-off. NDCG@10: **BEIR 55.6, MIRACL 55.8, CLEF 54.3**. Crucially, **CLEF here averages German/English/Spanish/French/Italian — so Spanish is directly benchmarked**, a genuine advantage for this project; **Portuguese is in neither CLEF nor MIRACL-4, so PT is inferred.** Uses a "query:" prefix on queries only. Snowflake's blog reports >100 docs/sec on an NVIDIA A10 and sub-10 ms query encoding — practical on budget hardware. Its designers also warn that some competitors may have "overfit the MIRACL training data," reinforcing that MIRACL scores can flatter a model.

### 7. Snowflake/snowflake-arctic-embed-m-v2.0 — Apache 2.0
Same training recipe on the **gte-multilingual-base backbone**; ~305M params; 768-dim (MRL 256); 8192 tokens. BEIR 55.4, MIRACL 55.2, CLEF 51.7. With 256-dim truncation, reported quality drops only ~1.8–3% (e.g., BEIR 55.4→54.4) while cutting storage ~3×. The smaller sibling for tighter compute, with the same ES-benchmarked / PT-inferred caveat.

### 8. jina-embeddings-v3 — CC BY-NC 4.0 (LICENSE CAUTION)
Jina-XLM-RoBERTa backbone + RoPE + five task-specific LoRA adapters (<3% added params), 570M params, 1024-dim (Matryoshka 32–1024), 8192 tokens, mean pooling. Its multilingual MTEB average (65.52) reportedly beats text-embedding-3-large, multilingual-e5-large-instruct, and Cohere-embed-multilingual-v3.0 on aggregate MTEB (Jina paper, arXiv 2409.10173). Covers 30 languages including ES and PT. **The blocking issue is the license: CC BY-NC 4.0 (non-commercial).** This does *not* match the §4.3 preferred set (Apache/MIT/CC BY) and the "NC" restriction may disqualify it depending on how the jury classifies a competition deployment. Present it as technically strong but **license-restricted — the team must confirm acceptability before considering it a real candidate.**

### 9. nomic-ai/nomic-embed-text-v2-moe — Apache 2.0
The first general-purpose **Mixture-of-Experts** text-embedding encoder: 8 experts, top-2 routing, **475M total / 305M active** params, XLM-R-derived (nomic-bert), **768-dim, MEAN pooling** (MRL to 256), L2-normalized. Native ceiling is only **512 tokens** — a natural fit for §4.3. HF-card scores: **BEIR 52.86, MIRACL 65.80**. ~100 languages including `es` and `pt` tags; trained on 1.6B pairs; fully open (weights, code, and training data released). **Requires "search_query:" / "search_document:" prefixes.** The MoE design gives near-large-model quality at ~305M active-parameter inference cost — appealing under limited compute, at the price of a larger 475M on-disk footprint and `trust_remote_code=True`.

### 10. sentence-transformers/paraphrase-multilingual-mpnet-base-v2 — Apache 2.0
XLM-RoBERTa-base SBERT model, 768-dim, mean pooling, 50+ languages incl. ES/PT, Apache 2.0. Very cheap, fast, low storage. **But it was trained for *symmetric* paraphrase/STS similarity, and its effective input is short (~128 tokens).** In practice it underperforms E5/BGE/GTE on *asymmetric* short-query → long-passage retrieval, which is exactly the task here. Include as a lightweight sanity-check baseline, explicitly flagged as **not retrieval-optimized**.

### 11. sentence-transformers/LaBSE — Apache 2.0 (RETRIEVAL CAUTION)
BERT dual-encoder, 768-dim [CLS], L2-normalized, 109 languages, Apache 2.0. World-class at *bitext mining* (Tatoeba 83.7% P@1; XTREME 95.0%). **But it was trained to align parallel sentences — a symmetric objective — not to rank short queries against long passages.** Its MTEB multilingual score is 45.2 (mE5 report), far below the E5/BGE family. A 2026 benchmarking study (arXiv 2605.23618) states plainly that despite >10M HuggingFace downloads, routine use of LaBSE "as a general-purpose multilingual retrieval model … is a systematic error with measurable quality consequences," and further notes LaBSE's bitext retrieval is *asymmetric* (non-English→English works better than the reverse). Include as a cross-lingual-alignment reference point, **flagged as a poor dense-retrieval choice.**

### Boundary case (not counted) — EmbeddingGemma-300M
Encoder-only with bidirectional attention, 768-dim, mean pooling + two-stage linear projection, 8192 tokens, ~308M params, 100+ languages, top open model <500M params on MTEB. **However, its architecture is "an encoder-only transformer model adapted from a pretrained 300M decoder-only Gemma 3 model"** (EmbeddingGemma paper, arXiv 2509.20354) via the T5Gemma encoder-decoder recipe. Because §4.2 bans decoder/generative lineage and instructs exclusion where encoder-only status "cannot be confirmed," EmbeddingGemma is presented as a **flagged boundary case, not a clean candidate** — the jury must rule on whether "encoder derived from a decoder base" satisfies the rule. (The same reasoning is why Qwen3-Embedding, e5-mistral, NV-Embed, and Llama-Embed-Nemotron are excluded outright.)

---

# DELIVERABLE 2 — Multi-Embedding Architectures Report

## (a) Performance expectations: multi-encoder + fusion vs a single-encoder baseline

The spec's reference design (§4.4/§8.4) is unambiguous: **each encoder builds its own independent FAISS index, and results are combined *after* search at the score or rank level — CombSUM, CombMNZ, or Reciprocal Rank Fusion (RRF).** This is the default architecture family because it sidesteps dimensionality mismatch entirely: a 768-dim encoder's vectors never occupy the same space as a 1024-dim encoder's vectors, so no projection or reconciliation is needed.

What the cited literature says about gains and costs:

- **Fusion helps most when the fused runs use *different* underlying techniques (dense vs. sparse).** BEIR (Thakur et al., NeurIPS 2021, arXiv 2104.08663) shows a hybrid improving nDCG@10 **from 43.42 (BM25 alone) to 52.59 — a +9.17-point absolute gain**. A separate practitioner synthesis reports dense+sparse fusion worth roughly **+8–15% MRR on MS MARCO and +5–12% nDCG across BEIR** versus dense alone.

- **Fusing two *dense* retrievers gives smaller, less reliable gains.** The LED study (arXiv 2208.13661) found "the ensemble of two dense retrievers is not as performant as that of one dense and one lexicon-aware retriever," and can be worse than a single strong model — because two similar dense encoders make correlated errors.

- **RRF is the most robust fusion rule for constrained settings.** It requires no score normalization, is insensitive to differing score scales / list lengths / missing candidates, and emphasizes top ranks via 1/(k+rank) with k≈60. The originating study (Cormack, Clarke & Buettcher, "Reciprocal Rank Fusion outperforms Condorcet and individual Rank Learning Methods," SIGIR 2009) reports that RRF at k=60 "consistently yields better results than any individual system, and better results than the standard method Condorcet Fuse," beating Condorcet, CombMNZ and the best single system by ~4–5% on average (e.g., TREC Robust MAP .3686 for RRF vs .3652 Condorcet, .3575 CombMNZ). A cross-lingual study (arXiv 2107.13751) corroborates: "RRF-based systems outperform other fusion methods significantly," while CombSUM is competitive only for lexical pre-selection and CombMNZ/ISR lag.

- **CombSUM/CombMNZ depend critically on score normalization** — "poor normalization can lead to one system's scores dominating the fusion." Weighted CombSUM lets you deliberately upweight the stronger encoder, at the cost of a tuning parameter.

- **Fusion can *harm* an already-strong retriever.** A 2026 study (arXiv 2604.03676, "Are LLM-Based Retrievers Worth Their Cost?") found linear fusion with BM25 lifting mid-tier dense models — "it improves BGE from 13.7 to 17.2 (+3.5 nDCG@10), Instructor-L from 14.2 to 19.1 (+4.9), and SFR-Mistral from 18.3 to 23.1 (+4.8)" — but *dropping* strong ones: "GTE-Qwen2 drops from 23.3 to 21.3 under Linear (–2.0), and ReasonIR drops from 24.1 to 21.2 (–2.9)," because BM25's lexical signal "conflicts with the dense retriever's semantic ranking when the dense model is already effective."

- **Cost.** Each added index multiplies indexing time and storage by ~1×, and roughly *doubles* query-time retrieval latency per additional index (one ANN search per index); the RRF/CombSUM merge step itself is negligible. Multiple sources note query latency "may double relative to single-list retrieval."

**Bottom line for a compute-limited team:** multi-encoder fusion is most defensible when it pairs *complementary signal types* (a dense encoder + a lexical/sparse signal), where the evidence for gains is strongest and most consistent. Stacking two similar dense encoders tends to yield diminishing — sometimes negative — returns at roughly double the cost. This is the trade-off the human team must weigh; it is not a recommendation.

## (b)/(c) Architectures — how each handles differing dimensionality, expected performance vs. baseline, and operational cost

### Architecture A — Single strong multilingual dense encoder (baseline)
One encoder, one FAISS index. **Dimensionality:** trivial — a single space, no reconciliation. **Performance:** the reference point against which all others are judged; with a top multilingual encoder this is already competitive. **Cost:** 1× indexing, 1× storage, 1× latency — the lowest-compute option, best aligned with §4.3.

### Architecture B — Two dense encoders, independent indices + RRF (the spec's reference design)
E.g., a 1024-dim encoder and a 768-dim encoder, each with its own FAISS index; the two ranked lists are merged by RRF. **Dimensionality:** fully decoupled — RRF consumes only ranks, so 768 vs. 1024 never interact (this is precisely why §4.4 prefers it). **Performance vs. baseline:** modest and uncertain per the two-dense-encoder evidence above; gains materialize only if the two encoders are genuinely diverse (different training data/objectives). **Cost:** 2× indexing, 2× storage, ~2× query latency; negligible merge cost; no normalization needed.

### Architecture C — Two dense encoders + CombSUM / CombMNZ (score-level, weighted or unweighted)
Same topology as B, but fusing *scores* rather than ranks. **Dimensionality:** decoupled at the score level, but **requires per-index score normalization** because each model's cosine distribution differs (recall mE5's 0.7–1.0 clustering). **Performance:** CombSUM can match RRF when normalization is good but is fragile when it isn't; **weighted** CombSUM lets the team deliberately favor the stronger/ES-and-PT-better encoder. **Cost:** as B, plus a normalization/tuning step and the risk that a mis-scaled index dominates the fusion.

### Architecture D — Dense + sparse (BM25 / SPLADE) hybrid + RRF (industry-standard adjacent pattern)
A dense-encoder index plus a lexical index (BM25 or learned-sparse SPLADE), fused by RRF or convex combination. BM25 is not a HuggingFace encoder, so it sits at the edge of this project's scope, but it is the **best-evidenced fusion pattern** (BEIR: BM25 43.42 → hybrid 52.59 nDCG@10, +9.17 points; arXiv 2104.08663) and it recovers exact-match / identifier / acronym queries — highly relevant for defense/space terminology (e.g., "LEO," orbital designators, unit names) that dense models can blur. **Dimensionality:** no vector-space conflict — sparse is a separate representation. **Efficiency note:** because BGE-M3 emits dense *and* sparse from a single forward pass, this hybrid is achievable at nearly one model's cost, avoiding a second full encoder. **Cost:** the sparse index is cheap to build and query; adds one retrieval pass. **Caveat:** the arXiv 2604.03676 evidence shows this can *hurt* an already-strong dense model, so it is not automatically beneficial.

### Architecture E — Per-language routing to language-specialized encoders
Route ES/PT/EN queries (and/or chunks) to the encoder that handles each best — e.g., a Portuguese-adapted encoder for PT content, a strong multilingual encoder otherwise — each writing to its own index. Motivated directly by the MTEB-PT/BR finding that multilingual averages under-predict Portuguese retrieval and that Portuguese benefits from language-specific adaptation. **Dimensionality:** each encoder owns its index; routing/fusion prevents cross-dimension mixing. **Performance:** potentially higher PT recall, but it **risks breaking cross-lingual alignment** — the §1.1 requirement that a Spanish query retrieve a Portuguese document depends on query and target passage sharing one aligned space, which per-language routing can violate. **Cost:** multiple indices plus routing logic; only worthwhile if PT quality is demonstrably the bottleneck.

### Architecture F — Vector-level combination (clearly-labeled ALTERNATIVE, NOT the default)
Shared projection heads, learned alignment, or dimensionality reduction (e.g., PCA) to force multiple encoders' outputs into **one shared vector space** and a single index. **Dimensionality:** this is the *only* family that directly reconciles different output dimensions — and exactly the approach §4.4 says to avoid by default. **Performance:** "projection fusion" is an active but unproven research direction (e.g., arXiv 2604.13728 compares rank fusion vs projection fusion) and has not been shown to beat simple rank fusion for this kind of task; it also adds a training/fitting stage and can distort a well-calibrated space. **Cost:** requires learning/fitting the projection, is brittle, and couples the encoders. **Presented, per the spec, as a researched alternative — explicitly not the assumed norm.**

## (d) Closing comparison — multi-encoder vs single-encoder

| Dimension | Single-encoder (A) | Multi-encoder + fusion (B–E) |
|---|---|---|
| Retrieval quality | Strong, well-understood baseline | +≈9 nDCG points on BEIR *if* signals are complementary (dense+sparse, D); small or **negative** if two similar dense encoders (B/C) |
| Dimensionality handling | Trivial (one space) | Solved cleanly by independent indices + rank/score fusion; vector-level (F) is the brittle exception |
| Compute — indexing | 1× | N× encoders |
| Storage | 1× (mitigable via MRL truncation to 256) | N× (each index; MRL helps) |
| Query latency | 1× | ~N× ANN searches + negligible fusion |
| Robustness | Single point of failure; simplest to tune | RRF robust to score-scale differences and needs no normalization; but fusion can degrade an already-strong retriever |
| Fit for limited compute (§4.3) | Best | Justified only when the added signal is genuinely complementary |

**Interpretation (trade-off summary, not a recommendation).** The research consistently points the same way: for a compute-limited team, a single strong multilingual dense encoder is the natural baseline, and the highest-confidence *upgrade* is adding a *complementary* signal — a sparse/lexical index fused via RRF — rather than a second similar dense encoder. RRF is the lowest-friction fusion rule (no normalization, robust to scale). The vector-level alternative (F) should be treated as experimental. Two open questions the team must resolve with their own eval on the actual ES/EN/PT corpus, because published benchmarks do not settle them: (1) whether Portuguese retrieval quality holds for models whose scores come only from MIRACL/CLEF (neither benchmarks PT), and (2) whether their chosen dense encoder is already strong enough that BM25 fusion would help or hurt. The final architecture and model choice remain the human team's decision.

---

## Sourcing notes and known limitations
- **Confirmed live (accessed Aug 5, 2026)** via HuggingFace model cards: gte-multilingual-base (768-dim, 8192 tok, CLS, Apache 2.0, 305M), nomic-embed-text-v2-moe (768-dim/512 tok, mean, Apache 2.0, 475M/305M, BEIR 52.86 / MIRACL 65.80), arctic-embed-l-v2.0 (1024-dim/8192 tok, CLS, Apache 2.0, on bge-m3-retromae).
- **Benchmark comparability caveat:** MTEB v2 (2026) scores are not directly comparable to MTEB v1; multilingual (MMTEB), English (MTEB-R), MIRACL, CLEF, and BEIR are distinct boards. Where possible, retrieval-subset figures (MIRACL/BEIR/CLEF nDCG@10) are cited rather than general MTEB averages, per §4.3/§10.2. Cross-model number comparisons should be treated as indicative, not exact.
- **Portuguese is the weakest-sourced axis by design:** MIRACL (18 langs) and the CLEF set used by Arctic (DE/EN/ES/FR/IT) both exclude Portuguese, so PT retrieval quality for models 1, 5, 6, 7 is *inferred*. The MTEB-PT/MTEB-BR papers (2026) are the appropriate PT-specific evidence and should be consulted directly before finalizing.
- **Excluded and why:** decoder-derived embedders (Qwen3-Embedding, e5-mistral-7b-instruct, NV-Embed-v2, Llama-Embed-Nemotron-8B, KaLM-Embedding-Gemma3) — banned architecture (§4.2); proprietary/API-only (OpenAI, Cohere, Google, Voyage) — not self-hostable (§4.3); EmbeddingGemma — decoder-ancestry boundary case, flagged not counted; jina-embeddings-v3 — included but flagged for its non-commercial CC BY-NC 4.0 license.