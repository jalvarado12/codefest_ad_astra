"""
Reference chunker for the CODEFEST Etapa 1 pipeline.

Pipeline:
    registro
        -> limpieza
        -> separación en bloques
        -> clasificación TITLE / PARAGRAPH
        -> agrupación por secciones
        -> generación de chunks

The chunker preserves:
    - document structure
    - section titles
    - complete sentences
    - total word count
    - maximum chunk size

Chunks are packed at sentence granularity under two caps at once: MAX_WORDS
(Sec. 9.2's return limit) and MAX_TOKENS (the encoder's input limit, Sec. 4.3).
A chunk closes *before* the sentence that would overflow either cap, which is
what Sec. 3.3 prescribes verbatim: "si se fija un tamano maximo de n tokens, el
corte efectivo debe retroceder al final de la ultima oracion completa que quepa
dentro de ese limite".

A sentence is never cut. A unit that overflows on its own is segmented by the
escalera (clauses, pipes, list markers, newlines); whatever still overflows
after all five levels is emitted intact and over the cap, because Sec. 3.3 is a
"Requisito obligatorio" and Sec. 4.3 only asks that fragments be *designed* not
to exceed the limit. The encoder truncates those; nothing else in the delivery
breaks.

The section title is included in the chunk text and metadata.
"""

import re
import unicodedata



# CONFIGURATION
MAX_WORDS = 250

# 512 encoder ceiling - 4 tokens for the "passage: " prefix - 2 special tokens.
# Counters passed as contar_tokens must therefore count raw content only, with
# no prefix and no special tokens.
MAX_TOKENS = 506



# CLEANING
def clean(text):
    """
    Normalize text before structural processing.

    - NFC Unicode normalization
    - remove control characters
    - normalize spaces
    - normalize excessive blank lines
    """
    text = unicodedata.normalize("NFC", text)

    text = "".join(
        ch
        for ch in text
        if ch == "\n"
        or ch == "\t"
        or not unicodedata.category(ch).startswith("C")
    )

    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()



# BLOCK IDENTIFICATION
def separar_bloques(texto):
    """
    Separate a document into blocks using blank lines.
    """
    texto = clean(texto)

    bloques = [
        bloque.strip()
        for bloque in texto.split("\n\n")
        if bloque.strip()
    ]

    return bloques



# BLOCK CLASSIFICATION
def clasificar_bloques(bloques):
    """
    Classify blocks as TITLE or PARAGRAPH.

    Rules:

    1. The first block is always TITLE.

    2. Short blocks without final punctuation are TITLE.

    3. Short questions are TITLE.

    4. Very short blocks are TITLE.

    5. Blocks longer than 20 words are PARAGRAPH.

    The output uses the keys expected by the rest of the pipeline:
        - text
        - type
    """

    bloques_clasificados = []

    for i, bloque in enumerate(bloques):
        texto = bloque.strip()

        # Rule 1: first block is always the title
        if i == 0:
            tipo = "TITLE"

        else:

            palabras = texto.split()
            n_palabras = len(palabras)

            # Default
            tipo = "PARAGRAPH"

            # Short block without final punctuation
            if n_palabras <= 15:
                if not texto.endswith(
                    (".", "!", "?", ";", ":")
                ):
                    tipo = "TITLE"

            # Short question
            if n_palabras <= 20 and texto.endswith("?"):
                tipo = "TITLE"

            # Very short block
            if n_palabras <= 10:
                tipo = "TITLE"

            # Long blocks are paragraphs
            if n_palabras > 20:
                tipo = "PARAGRAPH"

        bloques_clasificados.append({
            "text": texto,
            "type": tipo
        })

    return bloques_clasificados



# SECTION IDENTIFICATION
def agrupar_secciones(bloques_clasificados):
    """
    Group paragraphs under their corresponding title.

    Returns a list with the structure:

        [
            {
                "title": "...",
                "content": [
                    {"text": "...", "type": "PARAGRAPH"},
                    ...
                ]
            },
            ...
        ]

    A section starts whenever a TITLE is encountered.
    """

    secciones = []

    titulo_actual = None
    contenido_actual = []

    for bloque in bloques_clasificados:
        if bloque["type"] == "TITLE":

            # Save previous section
            if titulo_actual is not None:
                secciones.append({
                    "title": titulo_actual,
                    "content": contenido_actual
                })

            titulo_actual = bloque["text"]
            contenido_actual = []

        else:
            contenido_actual.append(bloque)

    # Save final section
    if titulo_actual is not None:
        secciones.append({
            "title": titulo_actual,
            "content": contenido_actual
        })

    return secciones



# SENTENCE SEGMENTATION
# Words that end in a period without ending a sentence. Splitting after one of
# these would leave an incomplete sentence on both sides, which Sec. 3.3 forbids.
_ABREVIATURAS = {
    "sr", "sra", "srta", "dr", "dra", "prof", "ing", "lic", "av", "avda",
    "art", "arts", "no", "núm", "num", "pág", "págs", "pag", "pags", "pp",
    "etc", "vs", "fig", "figs", "tab", "cf", "ej", "aprox", "máx", "mín",
    "inc", "ltd", "co", "corp", "st", "mr", "mrs", "ms", "jr", "vol",
    "ed", "eds", "al", "ee", "uu", "ss", "cap", "sec", "ref", "op", "cit",
    "depto", "dpto", "tel", "esq", "ing", "adm", "gob", "univ",
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
    texto = texto.strip()

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
# Every offender measured in the corpus (abbreviation tables, figure captions,
# org charts, "| URL: ... |" field runs) is reached by one of these.
_ESCALERA = [
    re.compile(r'(?<=[;:])\s+'),                                   # clauses
    re.compile(r'\s*\|\s*'),                                       # pipe runs
    re.compile(r'\n(?=\s*(?:[-*•·—–]|\(?\d+[.)]|[a-zA-Z][.)])\s)'),  # list markers
    re.compile(r'\n+'),                                            # newlines
    re.compile(r'(?<=,)\s+'),                                      # commas
]


def _segmentar(unidad, cabe):
    """
    Reduce an oversized unit to pieces that fit, walking the escalera.

    Returns the unit untouched when no level splits it. That unit then goes
    into the index over the cap and the encoder truncates it -- the deliberate
    choice from Sec. 3.3 ("Requisito obligatorio", absolute) over Sec. 4.3
    ("deben disenarse", a design obligation).
    """
    if cabe(unidad):
        return [unidad]

    for separador in _ESCALERA:
        piezas = [p.strip() for p in separador.split(unidad) if p.strip()]

        if len(piezas) < 2:
            continue

        salida = []

        for pieza in piezas:
            salida.extend(_segmentar(pieza, cabe))

        return salida

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
    """Join units back into chunk text, keeping the paragraph break whenever
    the source block changes."""
    if not piezas:
        return ""

    salida = [piezas[0]]

    for pieza, bloque, previo in zip(piezas[1:], bloques[1:], bloques[:-1]):
        salida.append("\n\n" if bloque != previo else " ")
        salida.append(pieza)

    return "".join(salida)


def _unidades(bloques_seccion, tokens_de):
    """Flatten a section into atomic units: sentences, with any oversized one
    run through the escalera."""
    def cabe(texto):
        return (
            len(texto.split()) <= MAX_WORDS
            and tokens_de(texto) <= MAX_TOKENS
        )

    unidades = []

    for i, bloque in enumerate(bloques_seccion):
        for oracion in separar_oraciones(bloque["text"]):
            for unidad in _segmentar(oracion, cabe):
                unidades.append({
                    "text": unidad,
                    "n_words": len(unidad.split()),
                    "n_tokens": tokens_de(unidad),
                    "bloque": i
                })

    return unidades


def generar_chunks_seccion(titulo, bloques_seccion, contar_tokens=None):
    """
    Generate chunks for a single section.

    The title is included in the first chunk of the section and counted in both
    budgets. A sentence is never split across chunks.

    contar_tokens is a callable text -> int counting raw content tokens (no
    "passage: " prefix, no special tokens). None leaves the token cap inactive.
    """
    tokens_de = _contador(contar_tokens)
    unidades = _unidades(bloques_seccion, tokens_de)

    chunks = []

    piezas = []
    bloques = []
    palabras = 0
    tokens = 0

    # ponytail: token budget is summed per unit rather than re-tokenising the
    # joined chunk on every candidate. Independent tokenisation loses the
    # cross-boundary subword merges, so the sum over-counts slightly -- it errs
    # toward smaller chunks, never toward blowing the ceiling. Re-tokenise the
    # candidate string if chunk sizes ever need to be tight.
    if titulo:
        piezas.append(titulo)
        bloques.append(None)
        palabras = len(titulo.split())
        tokens = tokens_de(titulo)

    def vacio():
        """Nothing but the title has landed in the accumulator yet."""
        return not piezas or (len(piezas) == 1 and bloques[0] is None)

    def cerrar():
        chunks.append({
            "title": titulo,
            "text": _unir(piezas, bloques),
            "n_words": palabras
        })

    for unidad in unidades:
        desborda = (
            palabras + unidad["n_words"] > MAX_WORDS
            or tokens + unidad["n_tokens"] > MAX_TOKENS
        )

        if desborda:
            # A title-only accumulator has nothing to emit. The title is our own
            # addition, not a Tabla-1 obligation, so rather than push the chunk
            # over the cap it is dropped here; it survives in chunk metadata.
            if not vacio():
                cerrar()

            piezas, bloques = [], []
            palabras = tokens = 0

        piezas.append(unidad["text"])
        bloques.append(unidad["bloque"])
        palabras += unidad["n_words"]
        tokens += unidad["n_tokens"]

    if not vacio():
        cerrar()

    return chunks


def generar_chunks(secciones, contar_tokens=None):
    """
    Generate chunks for all document sections.
    """

    todos_los_chunks = []

    for seccion in secciones:
        titulo = seccion["title"]
        bloques_seccion = seccion["content"]

        chunks_seccion = generar_chunks_seccion(
            titulo,
            bloques_seccion,
            contar_tokens
        )

        todos_los_chunks.extend(chunks_seccion)

    return todos_los_chunks



# DOCUMENT PROCESSING
def procesar_documento(registro, contar_tokens=None):
    """
    Process one JSONL document through the complete chunking pipeline.

    Expected input fields:
        - doc_id
        - fuente
        - texto

    Returns:

        {
            "doc_id": ...,
            "fuente": ...,
            "n_words_original": ...,
            "n_bloques": ...,
            "n_secciones": ...,
            "n_chunks": ...,
            "chunks": [...]
        }
    """

  
    # 1. Document information
    doc_id = registro.get("doc_id")
    fuente = registro.get("fuente")
    texto = registro.get("texto", "")


    # 2. Clean and separate blocks
    bloques = separar_bloques(texto)


    # 3. Classify blocks
    bloques_clasificados = clasificar_bloques(
        bloques
    )

    
    # 4. Group blocks into sections
    secciones = agrupar_secciones(
        bloques_clasificados
    )


    # 5. Generate chunks
    chunks = generar_chunks(
        secciones,
        contar_tokens
    )


    # 6. Add chunk identifiers
    for i, chunk in enumerate(chunks):
        chunk["chunk_id"] = (
            f"{doc_id}-chunk-{i:04d}"
        )

        chunk["posicion"] = i

 
    # 7. Document statistics
    n_words_original = len(
        texto.split()
    )


    # 8. Return complete result
    return {
        "doc_id": doc_id,
        "fuente": fuente,
        "n_words_original": n_words_original,
        "n_bloques": len(bloques),
        "n_secciones": len(secciones),
        "n_chunks": len(chunks),
        "chunks": chunks
    }



# SELF-CHECK
def _demo():
    """The smallest set of assertions that fails if the dual cap, the sentence
    boundary rule or the escalera breaks. Run: python chunker.py"""

    # -- sentence splitting does not cut on abbreviations, initials, decimals --
    assert separar_oraciones("El Dr. Ruiz llego. Se fue.") == \
        ["El Dr. Ruiz llego.", "Se fue."]
    assert separar_oraciones("Crecio 3.5 puntos. Luego cayo.") == \
        ["Crecio 3.5 puntos.", "Luego cayo."]
    assert separar_oraciones("Segun J. Smith el dato es firme.") == \
        ["Segun J. Smith el dato es firme."]
    assert separar_oraciones("") == []

    # -- a chunk closes BEFORE the sentence that would overflow ---------------
    oracion = "Palabra " + " ".join(["palabra"] * 99) + "."
    bloques = [{"text": " ".join([oracion] * 6), "type": "PARAGRAPH"}]
    chunks = generar_chunks_seccion("Titulo", bloques)

    assert chunks, "no chunks produced"
    assert all(c["n_words"] <= MAX_WORDS for c in chunks), \
        [c["n_words"] for c in chunks]

    # every sentence survives whole, in order, across the chunk boundaries
    unido = " ".join(c["text"] for c in chunks)
    assert unido.count(oracion) == 6, unido.count(oracion)

    # -- the token cap binds independently of the word cap -------------------
    corto = [{"text": "Uno dos tres. Cuatro cinco seis. Siete ocho nueve.",
              "type": "PARAGRAPH"}]
    caro = generar_chunks_seccion("", corto, contar_tokens=lambda t: 200 * len(t.split()))
    assert len(caro) == 3, [c["text"] for c in caro]

    # -- escalera reaches a pipe run that no sentence boundary touches -------
    campos = " | ".join(f"URL: recurso-{i}" for i in range(300))
    piezas = _segmentar(campos, lambda t: len(t.split()) <= MAX_WORDS)
    assert len(piezas) > 1, "escalera failed to split a pipe run"
    assert all(len(p.split()) <= MAX_WORDS for p in piezas)

    # -- residue that no level splits is returned intact, never cut ----------
    atomica = " ".join(["x"] * 400)
    assert _segmentar(atomica, lambda t: len(t.split()) <= MAX_WORDS) == [atomica]

    # -- end to end ----------------------------------------------------------
    doc = procesar_documento({
        "doc_id": "DOC-001",
        "fuente": "prueba.pdf",
        "texto": "Titulo de prueba\n\n" + " ".join([oracion] * 6),
    })
    assert doc["n_chunks"] == len(doc["chunks"])
    assert all(c["chunk_id"].startswith("DOC-001-chunk-") for c in doc["chunks"])
    assert [c["posicion"] for c in doc["chunks"]] == list(range(doc["n_chunks"]))

    print(f"OK  {len(chunks)} chunks, max {max(c['n_words'] for c in chunks)} words")


if __name__ == "__main__":
    _demo()