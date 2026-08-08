"""Architecture E -- all-MiniLM-L6-v2 dense + BGE-M3 sparse, RRF-fused.

  index     FAISS IndexFlatIP over MiniLM's 384-dim dense + inverted index over
            BGE-M3's lexical head
  retrieval both retrievers to depth 100, fused with RRF (k0=60)
  cost      ~1x plus change: BGE-M3 dominates, MiniLM's pass is nearly free --
            but two models stay resident at query time

The interesting question here is whether BGE-M3's multilingual sparse head
carries the Spanish and Portuguese retrieval that MiniLM's English-only dense
side drops. If D is far behind A but E is not far behind B, the lexical head is
doing the multilingual work and the dense side is cheaper than it looks.

Run on Colab:  python arch_e_minilm_plus_bgem3_sparse.py [--chunk] [--batch-size 32]
"""

from harness import cli

if __name__ == "__main__":
    cli("E")
