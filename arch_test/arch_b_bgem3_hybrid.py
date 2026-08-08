"""Architecture B -- BGE-M3 native dense + sparse hybrid, RRF-fused.

  index     FAISS IndexFlatIP over 1024-dim dense + inverted index over the
            lexical (sparse) head
  retrieval both retrievers to depth 100, fused with RRF (k0=60)
  cost      ~1x: both representations come out of ONE forward pass, and one
            model is resident at query time

The sparse head is what recovers exact-term matches -- LEO, orbital
designators, unit names -- that dense embeddings blur, and the two heads are
co-trained by self-knowledge distillation rather than bolted together here.

Run on Colab:  python arch_b_bgem3_hybrid.py [--chunk] [--batch-size 32]
"""

from harness import cli

if __name__ == "__main__":
    cli("B")
