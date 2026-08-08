"""Draft, pool and confirm the validation query set -- and enforce the gate.

The gate exists because LLM-drafted relevance judgments scored against the very
retrievers that helped produce them is circular: the comparison would measure
agreement with the draft, not retrieval quality. So:

    validation_draft.json      status "DRAFT"      never scored against
    validation_confirmed.json  status "CONFIRMED"  the only file harness.py reads

`confirm` refuses to promote a draft that a human has not actually been through:
it requires a reviewer name and requires every query to carry an explicit
reviewed flag. There is no --force.

Workflow:
  1. python validation.py worksheet   -> candidate chunks to write queries from
  2. (author queries; save as validation_draft.json, marked DRAFT)
  3. python validation.py pool        -> proposes candidate relevant chunks per
                                         query by pooling retrievers (DRAFT)
  4. HUMAN reviews and edits the draft, sets reviewed: true per query
  5. python validation.py confirm --reviewer "Name"
"""

import argparse
import json
import random
from collections import Counter, defaultdict
from pathlib import Path

DATA = Path(__file__).parent / "data"
DRAFT = DATA / "validation_draft.json"
CONFIRMED = DATA / "validation_confirmed.json"


def worksheet(chunks_path=DATA / "chunks.jsonl", per_cell=6, seed=20260805,
              out=DATA / "validation_worksheet.json"):
    """Sample candidate chunks, stratified over phenomenon x language.

    These are the chunks a query author reads to write realistic queries -- the
    point is that every phenomenon and every language gets covered rather than
    18 queries all landing on Spanish phenomenon-3 documents.
    """
    rng = random.Random(seed)
    chunks = [json.loads(l) for l in Path(chunks_path).read_text(encoding="utf-8").splitlines() if l.strip()]
    cells = defaultdict(list)
    for c in chunks:
        if c["num_tokens"] >= 120:  # too short to support a meaningful query
            cells[(c["fenomeno"], c.get("idioma", "?"))].append(c)
    picked = []
    for cell in sorted(cells):
        pool = cells[cell]
        rng.shuffle(pool)
        seen_docs, take = set(), []
        for c in pool:  # spread across documents, not 6 chunks of one report
            if c["doc_id"] in seen_docs:
                continue
            seen_docs.add(c["doc_id"])
            take.append({"cell": f"fenomeno{cell[0]}-{cell[1]}", "chunk_id": c["chunk_id"],
                         "doc_id": c["doc_id"], "fuente": c["fuente"],
                         "texto": c["texto"][:1200]})
            if len(take) == per_cell:
                break
        picked.extend(take)
    Path(out).write_text(json.dumps(picked, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"worksheet: {len(picked)} candidate chunks across {len(cells)} cells -> {out}")
    return picked


def pool_candidates(draft_path=DRAFT, depth=20):
    """Add DRAFT candidate relevant chunks per query by pooling all three retrievers.

    Standard TREC-style pooling: the union of what the systems retrieve is what a
    human judges. It biases toward what the systems can find -- which is exactly
    why the output stays DRAFT and a human must review it before anything is
    scored.
    """
    import numpy as np

    from harness import DATA as HDATA, SparseIndex, build_faiss, load_chunks, rrf
    from encoders import BGEM3, E5Dense

    draft = json.loads(Path(draft_path).read_text(encoding="utf-8"))
    chunks = load_chunks()
    e5_dense = np.load(HDATA / "e5_dense.npy")
    bge_dense = np.load(HDATA / "bge_dense.npy")
    import pickle
    bge_sparse = pickle.loads((HDATA / "bge_sparse.pkl").read_bytes())

    e5, bge = E5Dense(), BGEM3()
    idx_e5, idx_bge = build_faiss(e5_dense), build_faiss(bge_dense)
    sparse_index = SparseIndex(bge_sparse)

    for q in draft["queries"]:
        qe5 = e5.encode_queries([q["texto"]], batch_size=1)
        qbd, qbs = bge.encode([q["texto"]])
        a = [int(i) for i in idx_e5.search(qe5, depth)[1][0] if i >= 0]
        b_dense = [int(i) for i in idx_bge.search(qbd, depth)[1][0] if i >= 0]
        b_sparse, _ = sparse_index.search(qbs[0], depth)
        pooled = rrf([a, b_dense, b_sparse])[:depth]
        q["candidates_DRAFT"] = [
            {"chunk_id": chunks[i]["chunk_id"], "doc_id": chunks[i]["doc_id"],
             "fuente": chunks[i]["fuente"], "relevancia_DRAFT": None,
             "texto": chunks[i]["texto"][:600]}
            for i in pooled]
        q["reviewed"] = False
    draft["status"] = "DRAFT"
    Path(draft_path).write_text(json.dumps(draft, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"pooled {depth} DRAFT candidates for {len(draft['queries'])} queries -> {draft_path}")


def check(doc, require_confirmed=False):
    """Schema and stratification check. Returns list of problems (empty == fine)."""
    problems = []
    queries = doc.get("queries", [])
    if not queries:
        problems.append("no queries")
    ids = [q.get("query_id") for q in queries]
    if len(set(ids)) != len(ids):
        problems.append("duplicate query_id")
    for q in queries:
        if not q.get("texto", "").strip():
            problems.append(f"{q.get('query_id')}: empty query text")
        rel = {c["chunk_id"]: c.get("relevancia") for c in q.get("relevantes", [])}
        if not rel:
            problems.append(f"{q.get('query_id')}: no relevance judgments")
        for cid, r in rel.items():
            if not isinstance(r, int) or not 0 <= r <= 3:
                problems.append(f"{q.get('query_id')}/{cid}: relevancia must be int 0-3, got {r!r}")
        if not q.get("documentos_relevantes"):
            problems.append(f"{q.get('query_id')}: no document-level judgments (fuente list)")
    langs = Counter(q.get("idioma") for q in queries)
    phens = Counter(q.get("fenomeno") for q in queries)
    if len(langs) < 3:
        problems.append(f"languages not covered (need es/en/pt, have {dict(langs)})")
    if len(phens) < 3:
        problems.append(f"phenomena not covered (need 1/2/3, have {dict(phens)})")
    if require_confirmed:
        if doc.get("status") != "CONFIRMED":
            problems.append(f"status is {doc.get('status')!r}, not CONFIRMED")
        if not doc.get("confirmed_by"):
            problems.append("confirmed_by is empty")
        unreviewed = [q["query_id"] for q in queries if not q.get("reviewed")]
        if unreviewed:
            problems.append(f"unreviewed queries: {unreviewed}")
    return problems


def confirm(reviewer, draft_path=DRAFT, out=CONFIRMED):
    """Promote DRAFT to CONFIRMED. Refuses if a human has not marked every query reviewed."""
    import datetime
    doc = json.loads(Path(draft_path).read_text(encoding="utf-8"))
    unreviewed = [q["query_id"] for q in doc["queries"] if not q.get("reviewed")]
    if unreviewed:
        raise SystemExit(f"REFUSED: {len(unreviewed)} queries not marked reviewed: {unreviewed}\n"
                         f"A human must review each query's judgments and set reviewed: true.")
    doc["status"] = "CONFIRMED"
    doc["confirmed_by"] = reviewer
    doc["confirmed_at"] = datetime.date.today().isoformat()
    problems = check(doc, require_confirmed=True)
    if problems:
        raise SystemExit("REFUSED:\n  " + "\n  ".join(problems))
    Path(out).write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"CONFIRMED by {reviewer}: {len(doc['queries'])} queries -> {out}")


def _demo():
    """Self-check on the gate logic -- the one thing here that must not fail open."""
    good = {"status": "CONFIRMED", "confirmed_by": "someone", "queries": [
        {"query_id": "v01", "texto": "riesgos de la basura espacial", "idioma": "es",
         "fenomeno": 2, "reviewed": True, "relevantes": [{"chunk_id": "c1", "relevancia": 3}],
         "documentos_relevantes": ["a.pdf"]},
        {"query_id": "v02", "texto": "military AI adoption", "idioma": "en", "fenomeno": 1,
         "reviewed": True, "relevantes": [{"chunk_id": "c2", "relevancia": 2}],
         "documentos_relevantes": ["b.pdf"]},
        {"query_id": "v03", "texto": "dinâmicas territoriais", "idioma": "pt", "fenomeno": 3,
         "reviewed": True, "relevantes": [{"chunk_id": "c3", "relevancia": 1}],
         "documentos_relevantes": ["c.pdf"]}]}
    assert check(good, require_confirmed=True) == [], check(good, require_confirmed=True)

    draftish = json.loads(json.dumps(good))
    draftish["status"] = "DRAFT"
    assert any("not CONFIRMED" in p for p in check(draftish, require_confirmed=True))

    unrev = json.loads(json.dumps(good))
    unrev["queries"][0]["reviewed"] = False
    assert any("unreviewed" in p for p in check(unrev, require_confirmed=True))

    monoling = json.loads(json.dumps(good))
    monoling["queries"] = monoling["queries"][:1]
    assert any("languages not covered" in p for p in check(monoling))

    badrel = json.loads(json.dumps(good))
    badrel["queries"][0]["relevantes"][0]["relevancia"] = "high"
    assert any("relevancia must be int" in p for p in check(badrel))

    print("validation gate self-check OK")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("worksheet"); w.add_argument("--per-cell", type=int, default=6)
    sub.add_parser("pool").add_argument("--depth", type=int, default=20)
    c = sub.add_parser("confirm"); c.add_argument("--reviewer", required=True)
    v = sub.add_parser("check"); v.add_argument("path")
    sub.add_parser("selftest")
    a = ap.parse_args()

    if a.cmd == "worksheet":
        worksheet(per_cell=a.per_cell)
    elif a.cmd == "pool":
        pool_candidates(depth=a.depth)
    elif a.cmd == "confirm":
        confirm(a.reviewer)
    elif a.cmd == "check":
        doc = json.loads(Path(a.path).read_text(encoding="utf-8"))
        problems = check(doc, require_confirmed=doc.get("status") == "CONFIRMED")
        print("\n".join(problems) if problems else "OK")
    elif a.cmd == "selftest":
        _demo()


if __name__ == "__main__":
    main()
