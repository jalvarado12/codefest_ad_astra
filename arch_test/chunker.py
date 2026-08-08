"""Reference chunker for the architecture comparison harness.

REFERENCE ONLY. This is not the team's final submission chunker -- it exists so
that architectures A, B and C are all fed byte-identical chunks, which is the
only property the comparison actually needs from it. It implements the CODEFEST
Etapa 1 spec requirements that affect chunk boundaries (section 3.3 linguistic
completeness, section 3.4 mandatory metadata) and nothing more.

Guarantees:
  - no chunk contains a partial sentence (cuts land only on sentence ends)
  - every chunk is <= max_tokens as counted by the caller's tokenizer
  - a single sentence longer than max_tokens is emitted alone and flagged,
    never split mid-sentence
"""

import re
import unicodedata

# Sentence terminators followed by whitespace + an opening character. Handles
# ES/EN/PT: closing quotes/brackets may sit between the terminator and the space,
# and Spanish inverted marks may open the next sentence.
_SENT_END = re.compile(
    r"""(?<=[.!?…])          # terminator
        ["'»”’\)\]]*  # optional closers
        \s+                        # the break itself
        (?=[\"'«“¿¡(\[—-]?[A-ZÀ-Ü0-9])""",
    re.VERBOSE,
)

# Abbreviations whose trailing period is not a sentence end (ES/EN/PT).
_ABBREV = {
    "sr", "sra", "srta", "dr", "dra", "prof", "ing", "lic", "gral", "cnel",
    "ee", "uu", "ee.uu", "etc", "vs", "art", "num", "no", "pag", "pp", "fig",
    "cap", "aprox", "aprox", "mr", "mrs", "ms", "jr", "st", "inc", "ltd",
    "eg", "ie", "cf", "al", "ed", "eds", "vol", "min", "max", "seg",
}


def split_sentences(text):
    """Split into sentences on ES/EN/PT terminators, respecting abbreviations."""
    parts = []
    start = 0
    for m in _SENT_END.finditer(text):
        cut = m.start()
        head = text[start:cut]
        # last word before the period; if it's a known abbreviation, don't cut
        tail_word = re.split(r"[\s(\[]", head.strip())[-1].rstrip(".!?…").lower()
        if tail_word in _ABBREV or (len(tail_word) == 1 and tail_word.isalpha()):
            continue
        if head.strip():
            parts.append(head.strip())
        start = m.end()
    rest = text[start:].strip()
    if rest:
        parts.append(rest)
    return parts


def clean(text):
    """Spec section 2.2: NFC, strip control chars, collapse redundant whitespace."""
    text = unicodedata.normalize("NFC", text)
    text = "".join(ch for ch in text if ch == "\n" or ch == "\t" or not unicodedata.category(ch).startswith("C"))
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def chunk_document(text, doc_id, fuente, formato, fenomeno, count_tokens,
                   max_tokens=480, min_tokens=32):
    """Chunk one document into spec-compliant fragments.

    max_tokens defaults to 480, not 512: both encoders under test add special
    tokens and multilingual-e5 prepends a "passage: " prefix, so the budget has
    to leave headroom or chunks get silently truncated at encode time -- which
    would corrupt the very comparison this harness exists to run.

    Returns a list of metadata dicts carrying every field in spec Table 1.
    """
    sentences = split_sentences(clean(text))
    chunks, buf, buf_tokens = [], [], 0

    def flush():
        nonlocal buf, buf_tokens
        if not buf:
            return
        body = " ".join(buf)
        chunks.append(body)
        buf, buf_tokens = [], 0

    for sent in sentences:
        n = count_tokens(sent)
        if n > max_tokens:
            # Oversized single sentence: emit alone rather than break section 3.3.
            flush()
            chunks.append(sent)
            continue
        if buf_tokens + n > max_tokens:
            flush()
        buf.append(sent)
        buf_tokens += n
    flush()

    # Merge a runt tail into its predecessor when it still fits -- a 5-token
    # trailing chunk is noise in the index and skews per-chunk timings.
    if len(chunks) >= 2 and count_tokens(chunks[-1]) < min_tokens:
        merged = chunks[-2] + " " + chunks[-1]
        if count_tokens(merged) <= max_tokens:
            chunks[-2:] = [merged]

    out = []
    for i, body in enumerate(chunks):
        n = count_tokens(body)
        out.append({
            "doc_id": doc_id,
            "chunk_id": f"{doc_id}-chunk-{i:04d}",
            "fuente": fuente,
            "formato": formato,
            "fenomeno": fenomeno,
            "posicion": i,
            "num_tokens": n,
            "texto": body,
            "oversized": n > max_tokens,  # extra field; spec allows extras
        })
    return out


def _demo():
    """Self-check: the properties the comparison depends on."""
    words = lambda s: len(s.split())

    es = ("La congestión de la órbita baja terrestre es un riesgo creciente. "
          "El Dr. Pérez lo advirtió en 2024, junto a EE.UU. y otros actores. "
          "¿Qué implica para la sostenibilidad orbital? Nadie lo sabe aún.")
    sents = split_sentences(es)
    assert len(sents) == 4, sents
    assert "Dr. Pérez" in sents[1] and "EE.UU." in sents[1], sents[1]
    assert sents[2].startswith("¿Qué"), sents[2]

    # No sentence may straddle a chunk boundary (spec 3.3).
    long_text = " ".join(f"Esta es la oración número {i} del documento de prueba." for i in range(200))
    cs = chunk_document(long_text, "DOC-001", "test.pdf", "pdf", 2, words, max_tokens=40)
    assert len(cs) > 1
    for c in cs:
        assert c["num_tokens"] <= 40 or c["oversized"]
        assert c["texto"].endswith("."), c["texto"][-40:]
    # every sentence survives exactly once, in order
    rejoined = " ".join(c["texto"] for c in cs)
    assert rejoined == long_text.strip(), "chunking lost or reordered text"

    # Oversized single sentence is emitted whole, never cut.
    giant = "palabra " * 100 + "final."
    cs = chunk_document(giant, "DOC-002", "t.html", "html", 1, words, max_tokens=40)
    assert len(cs) == 1 and cs[0]["oversized"] and cs[0]["num_tokens"] > 40

    # Mandatory metadata fields (spec Table 1) all present.
    required = {"doc_id", "chunk_id", "fuente", "formato", "fenomeno", "posicion", "num_tokens", "texto"}
    assert required <= set(cs[0]), required - set(cs[0])

    print("chunker self-check OK")


if __name__ == "__main__":
    _demo()
