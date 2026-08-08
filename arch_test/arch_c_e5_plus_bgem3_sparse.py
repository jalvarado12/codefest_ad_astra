"""Architecture C -- multilingual-e5-large dense + BGE-M3 sparse, RRF-fused.

  index     FAISS IndexFlatIP over e5's 1024-dim dense + inverted index over
            BGE-M3's lexical head
  retrieval both retrievers to depth 100, fused with RRF (k0=60)
  cost      ~2x: two full forward passes per chunk, two models resident at
            query time

The pairing with no supporting benchmark: it takes A's dense side and B's
sparse side on the theory that each is the stronger half. Charged both passes
even when it reads them from cache -- see harness.indexing_cost.

Run on Colab:  python arch_c_e5_plus_bgem3_sparse.py [--chunk] [--batch-size 32]
"""

from harness import cli

if __name__ == "__main__":
    cli("C")
