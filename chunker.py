"""
Chunker for the CODEFEST Etapa 1 pipeline. Chunking only.

Cleaning and structure detection are extraction concerns and live in
`extraccion_final.py` (Decisions 3 and 9): `clean_text()` there normalises
Unicode, strips control characters and removes repeated-line boilerplate, and
every adapter separates its own logical units with a blank line. This module
takes the already-clean text and does one thing: turn it into chunks.

Chunks are packed at sentence granularity under two caps at once, MAX_WORDS
(Sec. 9.2's return limit) and MAX_TOKENS (the encoder's input limit, Sec. 4.3).
A chunk closes *before* the sentence that would overflow either cap, which is
what Sec. 3.3 prescribes verbatim: "si se fija un tamano maximo de n tokens, el
corte efectivo debe retroceder al final de la ultima oracion completa que quepa
dentro de ese limite".

A sentence is never cut. A unit that overflows on its own is segmented by the
escalera (clauses, pipes, list markers, newlines, commas); whatever still
overflows after all five levels is emitted intact and over the cap, because
Sec. 3.3 is a "Requisito obligatorio" while Sec. 4.3 only asks that fragments be
*designed* not to exceed the limit. The encoder truncates those; nothing else in
the delivery breaks. ESCALERA_HITS counts what each level caught so dead levels
can be deleted after the dry run.

Every chunk carries the eight Tabla-1 fields under the spec's own names --
note `texto` here, against `text` in resultados.jsonl (Tabla 2).
"""

import re


# CONFIGURATION
MAX_WORDS = 250

# 512 encoder ceiling - 4 tokens for the "passage: " prefix - 2 special tokens.
# Counters passed as contar_tokens must therefore count raw content only, with
# no prefix and no special tokens.
MAX_TOKENS = 506

# Decision 2: seed each chunk with the last N complete sentences of the previous
# one, so an answer straddling a chunk boundary is retrievable from both sides.
# The overlap counts toward both caps and must be whole sentences (§3.3), which
# is free here because the packer's units already are.
OVERLAP_ORACIONES = 1

# ...but not for tabular formats. Rows are independent records; repeating one
# adds no context, and tabular content is already projected to dominate the
# index. §3.2 permits the hybrid, it does not require applying it uniformly.
FORMATOS_SIN_OVERLAP = frozenset({"csv", "xlsx", "pbf"})



# BLOCK SEPARATION
def separar_bloques(texto):
    """
    Split already-clean text into blocks on blank lines.

    Every extractor emits its logical units blank-line separated: one CSV row,
    one spreadsheet row, one PDF paragraph, one PBF element. No cleaning happens
    here -- extraccion_final.clean_text() is the only cleaner (Decision 9).
    """
    return [b.strip() for b in (texto or "").split("\n\n") if b.strip()]



# SENTENCE SEGMENTATION
# Words that end in a period without ending a sentence. Splitting after one of
# these would leave an incomplete sentence on both sides, which Sec. 3.3 forbids.
_ABREVIATURAS = {
    "sr", "sra", "srta", "dr", "dra", "prof", "ing", "lic", "av", "avda",
    "art", "arts", "no", "núm", "num", "pág", "págs", "pag", "pags", "pp",
    "etc", "vs", "fig", "figs", "tab", "cf", "ej", "aprox", "máx", "mín",
    "inc", "ltd", "co", "corp", "st", "mr", "mrs", "ms", "jr", "vol",
    "ed", "eds", "al", "ee", "uu", "ss", "cap", "sec", "ref", "op", "cit",
    "depto", "dpto", "tel", "esq", "adm", "gob", "univ",
}

# Sentence-final punctuation, any closing quotes or brackets, then whitespace.
_FIN_ORACION = re.compile(r'[.!?…]+["»”\'’)\]]*(\s+)')

# The word immediately before that punctuation.
_PALABRA_PREVIA = re.compile(r'([^\W\d_]+|\d+)[.!?…]+["»”\'’)\]]*$')

# Opening punctuation, then the first meaningful character of the next sentence.
_INICIO_ORACION = re.compile(r'["«“¿¡(\[\'‘]*(\w)')


def _es_abreviatura(texto, fin):
    """True when the period at `fin` closes an abbreviation, an initial or a
    decimal rather than a sentence."""
    m = _PALABRA_PREVIA.search(texto[:fin])

    if not m:
        return False

    palabra = m.group(1)

    return (
        palabra.lower() in _ABREVIATURAS
        or len(palabra) == 1
        or palabra.isdigit()
    )


def _inicia_oracion(texto, pos):
    """True when a new sentence plausibly starts at `pos`: capital or digit,
    optionally behind opening punctuation."""
    m = _INICIO_ORACION.match(texto, pos)

    if not m:
        return False

    ch = m.group(1)

    return ch.isupper() or ch.isdigit()


def separar_oraciones(texto):
    """
    Split a block into sentences.

    Deliberately conservative: a candidate boundary is only taken when the
    preceding word is not an abbreviation and the following text looks like a
    sentence start. Missing a boundary costs a longer unit, which the escalera
    handles; inventing one cuts a sentence in half and breaks Sec. 3.3.
    """
    texto = (texto or "").strip()

    if not texto:
        return []

    oraciones = []
    inicio = 0

    for m in _FIN_ORACION.finditer(texto):
        fin_texto = m.end() - len(m.group(1))
        corte = m.end()

        if _es_abreviatura(texto, fin_texto):
            continue

        if not _inicia_oracion(texto, corte):
            continue

        pieza = texto[inicio:fin_texto].strip()

        if pieza:
            oraciones.append(pieza)

        inicio = corte

    cola = texto[inicio:].strip()

    if cola:
        oraciones.append(cola)

    return oraciones



# OVERSIZED UNIT SEGMENTATION
# The five-level escalera, in order of how much meaning the cut destroys.
_ESCALERA = [
    ("clausulas", re.compile(r'(?<=[;:])\s+')),
    ("pipes", re.compile(r'\s*\|\s*')),
    ("marcadores", re.compile(r'\n(?=\s*(?:[-*•·—–]|\(?\d+[.)]|[a-zA-Z][.)])\s)')),
    ("saltos", re.compile(r'\n+')),
    ("comas", re.compile(r'(?<=,)\s+')),
]

# Per-level hit counters. Step 1 check 6: measure during the dry run and delete
# every level that fires zero times before committing to the full corpus run.
ESCALERA_HITS = {nombre: 0 for nombre, _ in _ESCALERA}
ESCALERA_HITS["residuo"] = 0


def reiniciar_contadores():
    """Zero the escalera counters. Call once per run before chunking."""
    for k in ESCALERA_HITS:
        ESCALERA_HITS[k] = 0


def _segmentar(unidad, cabe):
    """
    Reduce an oversized unit to pieces that fit, walking the escalera.

    Returns the unit untouched when no level splits it, counted as `residuo`.
    That unit goes into the index over the cap and the encoder truncates it --
    the deliberate choice of Sec. 3.3 ("Requisito obligatorio", absolute) over
    Sec. 4.3 ("deben disenarse", a design obligation).
    """
    if cabe(unidad):
        return [unidad]

    for nombre, separador in _ESCALERA:
        piezas = [p.strip() for p in separador.split(unidad) if p.strip()]

        if len(piezas) < 2:
            continue

        ESCALERA_HITS[nombre] += 1

        salida = []

        for pieza in piezas:
            salida.extend(_segmentar(pieza, cabe))

        return salida

    ESCALERA_HITS["residuo"] += 1

    return [unidad]



# CHUNK GENERATION
def _contador(contar_tokens):
    """Memoised token counter. None disables the token cap entirely, which is
    what the tests and any tokenizer-free run want."""
    if contar_tokens is None:
        return lambda texto: 0

    cache = {}

    def contar(texto):
        n = cache.get(texto)

        if n is None:
            n = cache[texto] = contar_tokens(texto)

        return n

    return contar


def _unir(piezas, bloques):
    """Join units back into chunk text, keeping the blank line wherever the
    source block changes so paragraph structure survives."""
    if not piezas:
        return ""

    salida = [piezas[0]]

    for pieza, bloque, previo in zip(piezas[1:], bloques[1:], bloques[:-1]):
        salida.append("\n\n" if bloque != previo else " ")
        salida.append(pieza)

    return "".join(salida)


def _unidades(bloques, tokens_de):
    """Flatten blocks into atomic units: sentences, with any oversized one run
    through the escalera."""
    def cabe(texto):
        return (
            len(texto.split()) <= MAX_WORDS
            and tokens_de(texto) <= MAX_TOKENS
        )

    unidades = []

    for i, bloque in enumerate(bloques):
        for oracion in separar_oraciones(bloque):
            for unidad in _segmentar(oracion, cabe):
                unidades.append({
                    "texto": unidad,
                    "n_words": len(unidad.split()),
                    "n_tokens": tokens_de(unidad),
                    "bloque": i,
                })

    return unidades


# A unit that ends in sentence-final punctuation. Only these may be carried as
# overlap: an escalera piece is a fragment of an unsplittable unit, and seeding
# the next chunk with one would open it mid-sentence.
_FIN_UNIDAD = re.compile(r'[.!?…]["»”\'’)\]]*$')


def _semilla(actuales, overlap, siguiente):
    """The trailing units to carry into the next chunk.

    Contiguous and sentence-final, and trimmed from the front until the seed
    plus the unit that just overflowed both fit -- otherwise the overlap would
    push that unit out again and the packer would make no progress.
    """
    if overlap <= 0 or not actuales:
        return []

    if not _FIN_UNIDAD.search(actuales[-1]["texto"]):
        return []

    cand = list(actuales[-overlap:])

    while cand:
        palabras = sum(u["n_words"] for u in cand) + siguiente["n_words"]
        tokens = sum(u["n_tokens"] for u in cand) + siguiente["n_tokens"]

        if palabras <= MAX_WORDS and tokens <= MAX_TOKENS:
            break

        cand.pop(0)

    return cand


def generar_chunks(bloques, contar_tokens=None, overlap=0):
    """
    Pack blocks into chunks under both caps at once.

    Returns [{texto, n_words, num_tokens}]; identity fields are stamped by
    procesar_documento. A sentence is never split across two chunks.

    `overlap` carries that many trailing sentences into the next chunk
    (Decision 2). It counts toward both caps, so it costs index size rather
    than correctness.
    """
    tokens_de = _contador(contar_tokens)
    unidades = _unidades(bloques, tokens_de)

    chunks = []

    actuales = []
    palabras = 0
    tokens = 0

    # ponytail: the token budget is summed per unit rather than re-tokenising
    # the joined chunk on every candidate. Independent tokenisation loses the
    # cross-boundary subword merges, so the sum over-counts slightly -- it errs
    # toward smaller chunks, never toward blowing the ceiling. Re-tokenise the
    # candidate string if chunk sizes ever need to be tight.
    def cerrar():
        chunks.append({
            "texto": _unir([u["texto"] for u in actuales],
                           [u["bloque"] for u in actuales]),
            "n_words": palabras,
            "num_tokens": tokens,
        })

    for unidad in unidades:
        desborda = (
            palabras + unidad["n_words"] > MAX_WORDS
            or tokens + unidad["n_tokens"] > MAX_TOKENS
        )

        if desborda and actuales:
            cerrar()
            actuales = _semilla(actuales, overlap, unidad)
            palabras = sum(u["n_words"] for u in actuales)
            tokens = sum(u["n_tokens"] for u in actuales)

        actuales.append(unidad)
        palabras += unidad["n_words"]
        tokens += unidad["n_tokens"]

    if actuales:
        cerrar()

    return chunks



# DOCUMENT PROCESSING
def procesar_documento(registro, contar_tokens=None, overlap=None):
    """
    Chunk one extracted document.

    `registro` is what `extraccion_final.generar_documentos()` yields:
        doc_id, fuente, formato, fenomeno, texto_limpio, metadata_catalogo

    Optional fields are carried through when present: `idioma` and any
    `catalogo_*` scalars flattened by extraction (Decision 12).

    `overlap` defaults to OVERLAP_ORACIONES for prose and 0 for csv/xlsx/pbf.
    Pass an explicit integer to override, including 0 to disable.

    Returns the document summary plus its chunks, each carrying the eight
    Tabla-1 fields. `num_tokens` is real when contar_tokens is supplied and 0
    otherwise; the indexing step stamps it definitively at embed time.
    """
    doc_id = registro.get("doc_id")
    fuente = (registro.get("fuente") or "").replace("\\", "/")
    formato = registro.get("formato")
    fenomeno = registro.get("fenomeno")
    texto = registro.get("texto_limpio", registro.get("texto", ""))

    extra = {k: v for k, v in registro.items() if k.startswith("catalogo_")}

    if registro.get("idioma") is not None:
        extra["idioma"] = registro["idioma"]

    if overlap is None:
        overlap = 0 if formato in FORMATOS_SIN_OVERLAP else OVERLAP_ORACIONES

    bloques = separar_bloques(texto)
    chunks = generar_chunks(bloques, contar_tokens, overlap)

    for i, chunk in enumerate(chunks):
        chunk.update(extra)
        chunk["doc_id"] = doc_id
        chunk["chunk_id"] = f"{doc_id}-chunk-{i:05d}"
        chunk["fuente"] = fuente
        chunk["nombre_archivo"] = fuente.rsplit("/", 1)[-1]
        chunk["formato"] = formato
        # Tabla 1 types fenomeno as an integer; 0 means "outside the three".
        chunk["fenomeno"] = fenomeno if fenomeno is not None else 0
        chunk["posicion"] = i

    return {
        "doc_id": doc_id,
        "fuente": fuente,
        "formato": formato,
        "n_words_original": len(texto.split()),
        "n_bloques": len(bloques),
        "n_chunks": len(chunks),
        "chunks": chunks,
    }



# SELF-CHECK
_TABLA_1 = {"doc_id", "chunk_id", "fuente", "formato",
            "fenomeno", "posicion", "num_tokens", "texto"}


def _demo():
    """The smallest set of assertions that fails if the dual cap, the sentence
    boundary rule, the escalera or the Tabla-1 contract breaks.

    Run: python chunker.py
    """

    # -- sentence splitting does not cut on abbreviations, initials, decimals --
    assert separar_oraciones("El Dr. Ruiz llego. Se fue.") == \
        ["El Dr. Ruiz llego.", "Se fue."]
    assert separar_oraciones("Crecio 3.5 puntos. Luego cayo.") == \
        ["Crecio 3.5 puntos.", "Luego cayo."]
    assert separar_oraciones("Segun J. Smith el dato es firme.") == \
        ["Segun J. Smith el dato es firme."]
    assert separar_oraciones("") == []

    # -- cleaning is NOT this module's job: blocks come through verbatim ------
    assert separar_bloques("Uno\n\n\nDos") == ["Uno", "Dos"]
    assert separar_bloques("") == []

    # -- a chunk closes BEFORE the sentence that would overflow ---------------
    oracion = "Palabra " + " ".join(["palabra"] * 99) + "."
    chunks = generar_chunks([" ".join([oracion] * 6)])

    assert chunks, "no chunks produced"
    assert all(c["n_words"] <= MAX_WORDS for c in chunks), \
        [c["n_words"] for c in chunks]

    # every sentence survives whole, in order, across the chunk boundaries
    unido = " ".join(c["texto"] for c in chunks)
    assert unido.count(oracion) == 6, unido.count(oracion)

    # -- the token cap binds independently of the word cap -------------------
    caro = generar_chunks(["Uno dos tres. Cuatro cinco seis. Siete ocho nueve."],
                          contar_tokens=lambda t: 200 * len(t.split()))
    assert len(caro) == 3, [c["texto"] for c in caro]

    # -- escalera reaches a pipe run that no sentence boundary touches -------
    # Pipes are level 3: clauses (; :) come first per Step 1's ladder order, so
    # this fixture deliberately carries no colon.
    reiniciar_contadores()
    campos = " | ".join(f"recurso-{i}" for i in range(300))
    piezas = _segmentar(campos, lambda t: len(t.split()) <= MAX_WORDS)
    assert len(piezas) > 1, "escalera failed to split a pipe run"
    assert all(len(p.split()) <= MAX_WORDS for p in piezas)
    assert ESCALERA_HITS["pipes"] > 0, ESCALERA_HITS

    # a real CSV field run carries colons, so clauses catch it one level earlier
    reiniciar_contadores()
    con_colon = " | ".join(f"URL: recurso-{i}" for i in range(300))
    piezas = _segmentar(con_colon, lambda t: len(t.split()) <= MAX_WORDS)
    assert all(len(p.split()) <= MAX_WORDS for p in piezas)
    assert ESCALERA_HITS["clausulas"] > 0 and ESCALERA_HITS["residuo"] == 0, ESCALERA_HITS

    # -- residue that no level splits is returned intact, never cut ----------
    reiniciar_contadores()
    atomica = " ".join(["x"] * 400)
    assert _segmentar(atomica, lambda t: len(t.split()) <= MAX_WORDS) == [atomica]
    assert ESCALERA_HITS["residuo"] == 1, ESCALERA_HITS

    # -- a blank-line separated table yields many chunks, not one ------------
    # This is the shape extraccion_final must emit for csv/xlsx/pbf (Step 2
    # fix 1); with single newlines it collapses to one block and one chunk.
    tabla = "\n\n".join(f"Year: {2000+i} | Count: {i * 137}" for i in range(400))
    doc = procesar_documento({"doc_id": "DOC-0001", "fuente": r"a\b\t.csv",
                              "formato": "csv", "fenomeno": 2,
                              "texto_limpio": tabla})
    assert doc["n_chunks"] > 1, f"table collapsed into {doc['n_chunks']} chunk(s)"
    assert all(c["n_words"] <= MAX_WORDS for c in doc["chunks"])
    assert "Year: 2399" in " ".join(c["texto"] for c in doc["chunks"]), \
        "tail of the table was dropped"

    # -- Tabla-1 contract ----------------------------------------------------
    for c in doc["chunks"]:
        faltantes = _TABLA_1 - set(c)
        assert not faltantes, faltantes
        assert isinstance(c["fenomeno"], int) and isinstance(c["posicion"], int)
        assert "text" not in c, "Tabla 1 names the field 'texto', not 'text'"

    assert doc["fuente"] == "a/b/t.csv", doc["fuente"]
    assert doc["chunks"][0]["nombre_archivo"] == "t.csv"
    assert [c["posicion"] for c in doc["chunks"]] == list(range(doc["n_chunks"]))
    assert doc["chunks"][7]["chunk_id"] == "DOC-0001-chunk-00007"

    # -- overlap (Decision 2) ------------------------------------------------
    frases = [f"Frase numero {i} con relleno suficiente para pesar algo."
              for i in range(60)]
    prosa = [" ".join(frases)]

    sin = generar_chunks(prosa, overlap=0)
    con = generar_chunks(prosa, overlap=1)

    # overlap duplicates text, so the total grows; the chunk count need not,
    # since one repeated sentence rarely tips a chunk over on its own
    assert sum(c["n_words"] for c in con) > sum(c["n_words"] for c in sin), \
        (sum(c["n_words"] for c in sin), sum(c["n_words"] for c in con))
    assert len(con) >= len(sin), (len(sin), len(con))
    assert all(c["n_words"] <= MAX_WORDS for c in con), \
        [c["n_words"] for c in con]

    # each chunk after the first opens with the previous chunk's last sentence
    for previo, actual in zip(con, con[1:]):
        ultima = separar_oraciones(previo["texto"])[-1]
        assert actual["texto"].startswith(ultima), \
            (ultima[:60], actual["texto"][:60])

    # the overlap repeats text but loses none of it
    for f in frases:
        assert any(f in c["texto"] for c in con), f

    # tabular formats get no overlap, prose does, by default
    tabla = "\n\n".join(f"Year: {2000+i} | Count: {i}." for i in range(200))
    doc_csv = procesar_documento({"doc_id": "D", "fuente": "a.csv",
                                  "formato": "csv", "fenomeno": 1,
                                  "texto_limpio": tabla})
    doc_pdf = procesar_documento({"doc_id": "D", "fuente": "a.pdf",
                                  "formato": "pdf", "fenomeno": 1,
                                  "texto_limpio": prosa[0]})
    sin_csv = procesar_documento({"doc_id": "D", "fuente": "a.csv",
                                  "formato": "csv", "fenomeno": 1,
                                  "texto_limpio": tabla}, overlap=0)
    assert doc_csv["n_chunks"] == sin_csv["n_chunks"], "csv must not overlap"
    assert doc_pdf["n_chunks"] == len(con), "pdf must overlap by default"

    # a non-sentence-final unit is never carried: the escalera piece below has
    # no terminator, so seeding on it would open a chunk mid-sentence
    pieza = {"texto": "sin terminador", "n_words": 2, "n_tokens": 2, "bloque": 0}
    assert _semilla([pieza], 1, pieza) == []

    # -- optional passthrough ------------------------------------------------
    con_extra = procesar_documento({"doc_id": "DOC-0002", "fuente": "x.pdf",
                                    "formato": "pdf", "fenomeno": None,
                                    "idioma": "es", "catalogo_title": "T",
                                    "texto_limpio": "Una frase corta."})
    assert con_extra["chunks"][0]["idioma"] == "es"
    assert con_extra["chunks"][0]["catalogo_title"] == "T"
    assert con_extra["chunks"][0]["fenomeno"] == 0, "None fenomeno must become 0"

    print(f"OK  {len(chunks)} chunks from the packer, "
          f"{doc['n_chunks']} from the table, escalera={ESCALERA_HITS}")


if __name__ == "__main__":
    _demo()
