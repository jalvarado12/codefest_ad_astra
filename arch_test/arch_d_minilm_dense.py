"""Architecture D -- all-MiniLM-L6-v2, dense only. The cheap floor.

  index     FAISS IndexFlatIP over L2-normalized 384-dim vectors
  retrieval single dense search, no fusion
  cost      far under 1x: 22M parameters against e5-large's 560M, and a vector
            2.7x smaller

Spec-valid (encoder-only BERT distillation, Apache-2.0, on the Hub), so it is
eligible -- but it is English-only and trained at 256 tokens against a ~450-token
chunk budget on an ES/EN/PT corpus. This run exists to put a number on how much
that costs, not to argue it will win. Expect it to establish the floor; if it
lands close to A, the corpus is easier than assumed and that is worth knowing.

Run on Colab:  python arch_d_minilm_dense.py [--chunk] [--batch-size 32]
"""

from harness import cli

if __name__ == "__main__":
    cli("D")
