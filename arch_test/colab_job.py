"""VM-side driver for the architecture comparison. Runs on Colab, not locally.

Stages, in order, each skipped when its inputs are absent:

  selftest   chunker / metrics / validation-gate self-checks (no models needed)
  verify     load both models, cross-lingual + sparse sanity
  bench      measured throughput on real ~450-token chunks -- the number that
             decides whether the full sample is affordable
  pipeline   sample -> chunk -> encode every model, IF a corpus is present at
             /content/corpus
  score      NDCG@10 / F1@3, IF and ONLY IF a HUMAN-CONFIRMED validation set is
             present. A DRAFT set is never scored -- that is the phase gate.

Usage on the VM:  python colab_job.py [--stages selftest,verify,bench,pipeline,score]
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
DATA = HERE / "data"
CORPUS = Path("/content/corpus")


def banner(text):
    print(f"\n{'=' * 70}\n{text}\n{'=' * 70}", flush=True)


def stage_selftest():
    banner("SELFTEST -- chunker / metrics / validation gate")
    for mod, args in [("chunker.py", []), ("metrics.py", []), ("validation.py", ["selftest"])]:
        r = subprocess.run([sys.executable, str(HERE / mod), *args], capture_output=True, text=True)
        print(f"{mod}: {r.stdout.strip() or r.stderr.strip()}")
        if r.returncode != 0:
            raise SystemExit(f"{mod} self-check FAILED\n{r.stderr}")


def stage_verify():
    banner("VERIFY -- both models load and behave")
    from encoders import _verify
    _verify()


def stage_bench(n=16):
    """Throughput on real full-size chunks, not toy sentences.

    Toy inputs understate cost several-fold, and the whole sample-size decision
    rests on this number, so it is measured on chunks produced by the actual
    chunker from a real document.
    """
    banner("BENCH -- throughput on full-size chunks")
    import gc

    import torch

    from chunker import chunk_document
    from encoders import BGEM3, E5Dense, MiniLMDense

    src = next((p for p in [HERE.parent / "ad_astra.md", Path("/content/ad_astra.md")] if p.exists()), None)
    if src is None:
        print("no source document available for benchmarking; skipped")
        return {}
    text = src.read_text(encoding="utf-8")

    e5 = E5Dense()
    chunks = [c for c in chunk_document(text, "BENCH", src.name, "md", 0, e5.count_tokens)
              if c["num_tokens"] > 300][:n]
    texts = [c["texto"] for c in chunks]
    print(f"benchmarking on {len(texts)} chunks, "
          f"median {sorted(c['num_tokens'] for c in chunks)[len(chunks)//2]} tokens")

    out = {}
    del e5  # reloaded below; the chunker only needed it for its tokenizer
    gc.collect()
    for key, enc, call in [("e5", E5Dense, "passages"), ("bge", BGEM3, "encode"),
                           ("minilm", MiniLMDense, "passages")]:
        model = enc()
        for bs in (8, 32):
            t0 = time.perf_counter()
            if call == "encode":
                model.encode(texts, batch_size=bs)
            else:
                model.encode_passages(texts, batch_size=bs)
            out[f"{key}_ms_per_chunk_bs{bs}"] = round((time.perf_counter() - t0) / len(texts) * 1000, 1)
        del model
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    ms = {k: out[f"{k}_ms_per_chunk_bs32"] for k in ("e5", "bge", "minilm")}
    # Charged per harness.ARCHS: an architecture pays for every model pass it needs.
    per_arch = {"A": ["e5"], "B": ["bge"], "C": ["e5", "bge"],
                "D": ["minilm"], "E": ["minilm", "bge"]}
    for n_chunks in (1000, 3000, 6000):
        proj = {f"{a}_hours": round(n_chunks * sum(ms[k] for k in keys) / 3.6e6, 2)
                for a, keys in per_arch.items()}
        # one pass per distinct model covers every architecture at once
        proj["wall_clock_hours"] = round(n_chunks * sum(ms.values()) / 3.6e6, 2)
        out[f"projection_{n_chunks}_chunks"] = proj
    print(json.dumps(out, indent=1))
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / "benchmark.json").write_text(json.dumps(out, indent=1))
    return out


def stage_pipeline(n_docs=100, batch_size=None):
    banner("PIPELINE -- sample, chunk, encode")
    if not CORPUS.exists():
        print(f"no corpus at {CORPUS}; pipeline skipped.\n"
              f"Upload the ADL corpus there and re-run this stage.")
        return
    import harness
    from corpus import build_manifest, stratified_sample

    manifest = build_manifest(CORPUS)
    sample = stratified_sample(manifest, n_docs)
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / "sample.json").write_text(json.dumps(sample, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"corpus: {len(manifest)} readable documents; sampled {len(sample)}")

    harness.build_chunks()
    chunks = harness.load_chunks()
    timings = {}
    harness.encode_all(chunks, timings, batch_size or harness.DEFAULT_BATCH)
    (DATA / "timings_encode.json").write_text(json.dumps(timings, indent=1))
    print(json.dumps(timings, indent=1))


def stage_score(architectures=("A", "B", "C", "D", "E")):
    banner("SCORE -- gated on a human-confirmed validation set")
    confirmed = DATA / "validation_confirmed.json"
    if not confirmed.exists():
        print("no validation_confirmed.json -- scoring skipped.\n"
              "DRAFT judgments are never scored: the systems under test help produce\n"
              "the draft, so scoring against it unreviewed would be circular.")
        return
    import harness

    queries = harness.load_confirmed(confirmed)
    qrels = harness.to_qrels(queries)
    chunks = harness.load_chunks()
    tpath = DATA / "timings_encode.json"
    timings = json.loads(tpath.read_text()) if tpath.exists() else {}
    enc = harness.encode_all(chunks, timings)

    for arch in architectures:
        harness.merge_summary(arch, harness.run_and_score(arch, queries, chunks, enc, timings, qrels))
    (DATA / "timings.json").write_text(json.dumps(timings, indent=1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stages", default="selftest,verify,bench,pipeline,score")
    ap.add_argument("--architectures", default="A,B,C,D,E")
    ap.add_argument("--n-docs", type=int, default=100)
    ap.add_argument("--batch-size", type=int, default=None)
    args = ap.parse_args()

    sys.path.insert(0, str(HERE))
    stages = args.stages.split(",")
    if "selftest" in stages:
        stage_selftest()
    if "verify" in stages:
        stage_verify()
    if "bench" in stages:
        stage_bench()
    if "pipeline" in stages:
        stage_pipeline(args.n_docs, args.batch_size)
    if "score" in stages:
        stage_score(args.architectures.split(","))
    banner("DONE")


if __name__ == "__main__":
    main()
