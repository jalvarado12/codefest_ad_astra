"""Architecture A -- multilingual-e5-large, dense only.

  index     FAISS IndexFlatIP over L2-normalized 1024-dim vectors (= exact cosine)
  retrieval single dense search, no fusion
  cost      1x: one forward pass per chunk, one model resident at query time

The baseline the other four are measured against. Its 512-token native ceiling
matches the chunk budget exactly, and the mandatory "query: " / "passage: "
prefixes are applied inside E5Dense so they cannot be forgotten here.

Run on Colab:  python arch_a_e5_dense.py [--chunk] [--batch-size 32]
"""

from harness import cli

if __name__ == "__main__":
    cli("A")
