"""NDCG@10 and F1@3 implemented directly from the CODEFEST Etapa 1 spec.

Written from the formulas in ad_astra.md sections 10.2.1 and 10.2.2 rather than
taken from a library, because library defaults differ in ways that would move
the numbers: sklearn's ndcg_score applies its own tie-averaging, and several
IR toolkits use the exponential gain (2^r - 1) instead of the linear gain the
spec actually writes down.

Spec, verbatim:
    DCG@k  = sum_{i=1..k} r_i / log2(i + 1)          (eq. 8)
    NDCG@k = DCG@k / IDCG@k                          (eq. 9)
    P@3    = |D_hat ∩ D*| / 3                        (eq. 11)
    R@3    = |D_hat ∩ D*| / min(|D*|, 3)             (eq. 12)
    F1@3   = 2·P@3·R@3 / (P@3 + R@3)                 (eq. 13)
Final scores are the unweighted mean over the query set (eq. 10, eq. 14).

Two matching rules the spec is explicit about (section 10.2.1 box) and that this
module follows:
  - fragment relevance attaches to the fragment TEXT, chunk_id is traceability
    only. Inside this harness all three architectures are fed byte-identical
    chunks from the same chunker, so chunk_id is a sound proxy for text here --
    it would NOT be against the organisers' ground truth.
  - document matching goes through `fuente` (the original ADL filename), never
    through the team-assigned doc_id.
"""

from math import log2


def dcg_at_k(relevances, k=10):
    """Spec eq. 8. relevances are in the order the system returned them."""
    return sum(r / log2(i + 1) for i, r in enumerate(relevances[:k], start=1))


def ndcg_at_k(ranked_relevances, all_relevances, k=10):
    """Spec eq. 9.

    ranked_relevances: relevance of each returned fragment, in returned order.
    all_relevances:    every non-zero relevance in the ground truth for this
                       query -- needed because the ideal ranking may contain
                       relevant fragments the system never returned.
    """
    idcg = dcg_at_k(sorted(all_relevances, reverse=True), k)
    if idcg == 0:
        return 0.0  # no relevant fragments: nothing to score against
    return dcg_at_k(ranked_relevances, k) / idcg


def f1_at_3(returned_docs, relevant_docs):
    """Spec eqs. 11-13. Set metric: order of returned_docs is ignored."""
    returned, relevant = set(returned_docs), set(relevant_docs)
    if not relevant:
        return 0.0
    hits = len(returned & relevant)
    p = hits / 3
    r = hits / min(len(relevant), 3)
    if p + r == 0:
        return 0.0
    return 2 * p * r / (p + r)


def evaluate_run(results, qrels, k=10):
    """Score one architecture's full run.

    results: {query_id: {"fragments": [chunk_id,...], "documents": [fuente,...]}}
    qrels:   {query_id: {"fragments": {chunk_id: relevance}, "documents": [fuente,...]}}

    Returns (mean NDCG@10, mean F1@3, per-query rows) averaged over the qrels
    query set -- a query the system answered but that has no judgments is not
    scored, and a judged query the system skipped scores 0.
    """
    ndcgs, f1s, rows = [], [], []
    for qid in sorted(qrels):
        gt = qrels[qid]
        run = results.get(qid, {"fragments": [], "documents": []})
        frag_rel = gt["fragments"]
        ranked = [frag_rel.get(cid, 0) for cid in run["fragments"][:k]]
        n = ndcg_at_k(ranked, list(frag_rel.values()), k)
        f = f1_at_3(run["documents"][:3], gt["documents"])
        ndcgs.append(n)
        f1s.append(f)
        rows.append({"query_id": qid, "ndcg@10": round(n, 4), "f1@3": round(f, 4)})
    mean = lambda xs: sum(xs) / len(xs) if xs else 0.0
    return mean(ndcgs), mean(f1s), rows


def _demo():
    """Self-check against values computed by hand from the spec's formulas."""
    # DCG: relevances 3,0,2 -> 3/log2(2) + 0/log2(3) + 2/log2(4) = 3 + 0 + 1 = 4
    assert abs(dcg_at_k([3, 0, 2], 3) - 4.0) < 1e-12

    # IDCG for the same judgments: 3,2 -> 3 + 2/log2(3) = 3 + 1.261859507
    idcg = 3 + 2 / log2(3)
    assert abs(ndcg_at_k([3, 0, 2], [3, 2], 3) - 4.0 / idcg) < 1e-12
    assert abs(ndcg_at_k([3, 0, 2], [3, 2], 3) - 0.93855745) < 1e-8

    # Perfect ranking scores 1.0; empty ground truth scores 0.0
    assert abs(ndcg_at_k([3, 2, 1], [3, 2, 1], 3) - 1.0) < 1e-12
    assert ndcg_at_k([0, 0], [], 10) == 0.0

    # Discount base is 2 and positions start at 1: swapping ranks 1 and 2 must
    # cost score. Guards against an off-by-one that silently inflates results.
    assert dcg_at_k([1, 3], 2) < dcg_at_k([3, 1], 2)

    # Unretrieved relevant fragments still enlarge IDCG -> recall is penalised.
    assert ndcg_at_k([3], [3, 3, 3], 10) < 1.0

    # F1@3: 1 hit, 2 relevant -> P=1/3, R=1/2, F1=0.4
    assert abs(f1_at_3(["a", "b", "c"], ["a", "x"]) - 0.4) < 1e-12
    # all 3 correct with exactly 3 relevant -> 1.0
    assert abs(f1_at_3(["a", "b", "c"], ["a", "b", "c"]) - 1.0) < 1e-12
    # only 1 relevant doc exists and we found it: R capped by min(|D*|,3)=1
    assert abs(f1_at_3(["a", "b", "c"], ["a"]) - 0.5) < 1e-12
    assert f1_at_3(["a", "b", "c"], ["z"]) == 0.0
    # set metric: order must not matter
    assert f1_at_3(["c", "b", "a"], ["a", "x"]) == f1_at_3(["a", "b", "c"], ["a", "x"])

    # end-to-end
    qrels = {"q001": {"fragments": {"c1": 3, "c2": 2}, "documents": ["d1.pdf", "d2.pdf"]}}
    res = {"q001": {"fragments": ["c1", "c9", "c2"], "documents": ["d1.pdf", "dx.pdf", "dy.pdf"]}}
    nd, f1, rows = evaluate_run(res, qrels)
    assert abs(nd - ndcg_at_k([3, 0, 2], [3, 2], 10)) < 1e-12
    assert abs(f1 - 0.4) < 1e-12 and len(rows) == 1
    # a judged query the system never answered scores zero, not skipped
    nd2, f12, _ = evaluate_run({}, qrels)
    assert nd2 == 0.0 and f12 == 0.0

    print("metrics self-check OK (values verified by hand against spec eqs. 8-14)")


if __name__ == "__main__":
    _demo()
