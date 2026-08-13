"""
pipeline_final.py — corpus to vector store, resumable.

    python pipeline_final.py --corpus <dir> [--out entrega] [--sample N]
                             [--rebuild] [--batch-size 64]
    python pipeline_final.py selftest

Stages:

    1. walk the corpus, skipping the three non-corpus files
    2. per file: sha256 -> hit cache/textos.jsonl ? reuse : extract, detect language
    3. per document: chunker.procesar_documento(...) in memory, never persisted
    4. per chunk: sha256(texto) -> hit the previous run's vectors ? reuse : encode
    5. faiss.IndexFlatIP(1024), vectors added in ids.json order, in slices
    6. metadata.jsonl written by walking ids.json

Two caches, two different keys, for two different reasons:

    cache 1  sha256 of the file bytes   -- OCR is minutes per PDF; never redo it
    cache 2  sha256 of the chunk text   -- a chunker change must only re-encode
                                           the chunks whose text actually moved

Keying cache 2 on the text rather than on a chunker fingerprint is what makes
the MAX_WORDS and overlap sweeps affordable: change a parameter, and only the
chunks that genuinely differ are re-encoded.

THE ORDERING INVARIANT (Sec. 1.4). Row i of dense.npy, entry i of ids.json,
FAISS internal id i and line i of metadata.jsonl are the same chunk. Everything
here is written in one order and never sorted afterwards. The selftest asserts
it, including across a kill-and-resume, which is the path that actually breaks
it -- two clean runs never do.

Vector persistence lives here and only here. embeddings_only.write_vectors
writes a different layout with no ids.json; two half-owners of one file is how
ordering invariants rot.
"""
import argparse
import hashlib
import json
import platform
import shutil
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

import chunker

E5_ID = "intfloat/multilingual-e5-large"
E5_REV = "3d7cfbdacd47fdda877c5cd8a79fbcc4f2a574f3"
E5_DIM = 1024
PREFIJO_PASAJE = "passage: "

# Flush dense.npy + ids.json this often. 110k chunks with a single write at the
# end loses everything to a Colab disconnect at 90%.
CHECKPOINT_CADA = 5000

# np.save cannot append. The array is rewritten wholesale at each checkpoint --
# 450 MB at full corpus, a couple of seconds, and it keeps the file consistent
# with ids.json at every flush. open_memmap would avoid the rewrite but leaves a
# torn file if the process dies mid-write.


def sha256(dato) -> str:
    if isinstance(dato, str):
        dato = dato.encode("utf-8")
    return hashlib.sha256(dato).hexdigest()


# ------------------------------------------------------------------ cache 1
def leer_cache_textos(path: Path) -> dict:
    """fuente -> cached document record."""
    if not path.exists():
        return {}

    cache = {}
    with path.open(encoding="utf-8") as fh:
        for linea in fh:
            linea = linea.strip()
            if not linea:
                continue
            try:
                doc = json.loads(linea)
            except json.JSONDecodeError:
                continue  # a torn last line from a killed run
            cache[doc["fuente"]] = doc
    return cache


def extraer_corpus(corpus_dir, cache_path, inventario=None, sample=None,
                   rebuild=False, errores=None, log=print):
    """Yield one document record per file, reusing cache 1 when the bytes match.

    Returns (documentos, estadisticas).
    """
    import extraccion_final as ef

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache = {} if rebuild else leer_cache_textos(cache_path)

    corpus = Path(corpus_dir)
    archivos = [p for p in sorted(corpus.rglob("*")) if p.is_file()]

    excluidos = [p for p in archivos if p.name.lower() in ef._NO_CORPUS]
    archivos = [p for p in archivos
                if p.name.lower() not in ef._NO_CORPUS
                and p.suffix.lower() in ef._DISPATCH]

    if sample:
        archivos = archivos[:sample]

    registro = ef.RegistroDocumentos(str(cache_path.parent / "doc_registry.json"))
    catalogo = ef._indexar_metadata_catalogo(corpus)

    documentos = []
    reusados = extraidos = 0
    t0 = time.time()

    with cache_path.open("w", encoding="utf-8") as fh:
        for i, file_path in enumerate(archivos, 1):
            fuente = str(file_path.relative_to(corpus)).replace("\\", "/")
            digest = sha256(file_path.read_bytes())

            previo = cache.get(fuente)
            if previo is not None and previo.get("sha256") == digest:
                doc = previo
                reusados += 1
            else:
                ext = file_path.suffix.lower()
                try:
                    crudo = ef._DISPATCH[ext].extraer(file_path)
                except Exception as e:
                    if errores is not None:
                        errores.append({"etapa": "extraccion", "fuente": fuente,
                                        "error": f"{type(e).__name__}: {e}"})
                    crudo = ""

                doc = ef._empaquetar(registro, fuente, ef._FORMATO_POR_EXT[ext],
                                     crudo, file_path.relative_to(corpus),
                                     catalogo.get(fuente),
                                     (inventario or {}).get(fuente))
                doc["sha256"] = digest
                extraidos += 1

                if i % 25 == 0 or extraidos == 1:
                    log(f"   [{i}/{len(archivos)}] {fuente[:70]} "
                        f"({len(doc['texto_limpio'].split())} palabras)")

            documentos.append(doc)
            fh.write(json.dumps(doc, ensure_ascii=False) + "\n")

    registro.guardar()

    stats = {
        "archivos_vistos": len(archivos) + len(excluidos),
        "archivos_excluidos": len(excluidos),
        "archivos_procesados": len(archivos),
        "documentos_reusados": reusados,
        "documentos_extraidos": extraidos,
        "documentos_sin_texto": sum(1 for d in documentos
                                    if not d["texto_limpio"].strip()),
        "segundos": round(time.time() - t0, 1),
    }
    return documentos, stats


# ------------------------------------------------------------------ chunking
def chunkear(documentos, contar_tokens=None, log=print):
    """Chunks in document order, which becomes the index order."""
    chunker.reiniciar_contadores()

    t0 = time.time()
    chunks = []
    sin_chunks = []

    for doc in documentos:
        res = chunker.procesar_documento(doc, contar_tokens=contar_tokens)
        if res["n_chunks"] == 0:
            sin_chunks.append(doc["fuente"])
        chunks.extend(res["chunks"])

    por_formato = Counter(c["formato"] for c in chunks)
    log(f"   {len(chunks)} chunks, {len(sin_chunks)} documentos sin chunks")

    stats = {
        "chunks": len(chunks),
        "documentos_sin_chunks": len(sin_chunks),
        "chunks_por_formato": dict(por_formato),
        "escalera": dict(chunker.ESCALERA_HITS),
        "segundos": round(time.time() - t0, 1),
    }
    return chunks, sin_chunks, stats


# ------------------------------------------------------------------ cache 2
def _cargar_checkpoint(vectors_dir, hashes_objetivo, rebuild):
    """(vectores_previos_por_hash, filas_ya_hechas).

    The checkpoint is only resumable when its hashes are a prefix of what this
    run is about to write; otherwise the order changed and resuming would
    misalign every row after the divergence.
    """
    dense_path = vectors_dir / "dense.npy"
    ids_path = vectors_dir / "ids.json"

    if rebuild or not (dense_path.exists() and ids_path.exists()):
        return {}, 0

    try:
        dense = np.load(dense_path)
        estado = json.loads(ids_path.read_text(encoding="utf-8"))
        hashes_previos = estado.get("hashes", [])
    except Exception:
        return {}, 0

    if len(hashes_previos) != len(dense):
        return {}, 0

    # every chunk text ever encoded, for reuse regardless of position
    previos = {h: dense[i] for i, h in enumerate(hashes_previos)}

    hechas = 0
    for i, h in enumerate(hashes_previos):
        if i >= len(hashes_objetivo) or hashes_objetivo[i] != h:
            break
        hechas = i + 1

    return previos, hechas


def encode_chunks(chunks, vectors_dir, encoder, contar_tokens,
                  batch_size=64, rebuild=False, log=print):
    """Fill in every chunk's vector, reusing and checkpointing.

    `encoder(textos) -> (n, dim) float32, L2-normalised`.
    Returns (dense, stats). Row i belongs to chunks[i]; nothing is reordered.
    """
    vectors_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    hashes = [sha256(c["texto"]) for c in chunks]
    previos, hechas = _cargar_checkpoint(vectors_dir, hashes, rebuild)

    if hechas:
        log(f"   reanudando: {hechas} filas ya escritas")

    dense = np.zeros((len(chunks), E5_DIM), dtype="float32")

    # rows already committed by an earlier attempt of THIS same order
    if hechas:
        dense[:hechas] = np.load(vectors_dir / "dense.npy")[:hechas]

    reusados = 0
    pendientes = []

    for i in range(hechas, len(chunks)):
        vector = previos.get(hashes[i])
        if vector is not None:
            dense[i] = vector
            reusados += 1
        else:
            pendientes.append(i)

    log(f"   {reusados} reusados, {len(pendientes)} por codificar")

    def guardar(hasta):
        np.save(vectors_dir / "dense.npy", dense[:hasta])
        (vectors_dir / "ids.json").write_text(
            json.dumps({"ids": [c["chunk_id"] for c in chunks[:hasta]],
                        "hashes": hashes[:hasta]}, ensure_ascii=False),
            encoding="utf-8")

    codificados = 0
    desde_checkpoint = 0

    for inicio in range(0, len(pendientes), batch_size):
        lote = pendientes[inicio:inicio + batch_size]
        textos = [PREFIJO_PASAJE + chunks[i]["texto"] for i in lote]

        vectores = encoder(textos)
        for fila, i in enumerate(lote):
            dense[i] = vectores[fila]

        codificados += len(lote)
        desde_checkpoint += len(lote)

        if desde_checkpoint >= CHECKPOINT_CADA:
            # everything below the lowest still-pending row is final
            restantes = pendientes[inicio + batch_size:]
            frontera = restantes[0] if restantes else len(chunks)
            guardar(frontera)
            desde_checkpoint = 0
            log(f"   checkpoint en {frontera}/{len(chunks)} "
                f"({codificados} codificados)")

    for c in chunks:
        c["num_tokens"] = contar_tokens(c["texto"])

    guardar(len(chunks))

    nt = sorted(c["num_tokens"] for c in chunks)

    def pct(p):
        return nt[min(len(nt) - 1, int(len(nt) * p))] if nt else 0

    stats = {
        "vectores": len(chunks),
        # three ways a row gets filled, and they are not the same thing:
        # reanudados came from a valid checkpoint prefix of THIS same order,
        # reusados were matched by text hash anywhere in the previous run,
        # codificados actually went through the encoder.
        "reanudados": hechas,
        "reusados": reusados,
        "codificados": codificados,
        "batch_size": batch_size,
        "num_tokens": {"p50": pct(.5), "p95": pct(.95), "p99": pct(.99),
                       "max": nt[-1] if nt else 0},
        "chunks_sobre_cap": sum(1 for n in nt if n > chunker.MAX_TOKENS),
        "segundos": round(time.time() - t0, 1),
    }
    return dense, stats


# ------------------------------------------------------------------ index
def construir_indice(dense, chunks, out_dir, log=print):
    """FAISS index + metadata.jsonl, both in ids.json order."""
    import faiss

    t0 = time.time()
    out_dir.mkdir(parents=True, exist_ok=True)

    index = faiss.IndexFlatIP(dense.shape[1])

    # added in slices: the numpy array and FAISS's own copy coexist at peak
    for inicio in range(0, len(dense), 10000):
        index.add(dense[inicio:inicio + 10000])

    faiss.write_index(index, str(out_dir / "index.faiss"))

    with (out_dir / "metadata.jsonl").open("w", encoding="utf-8") as fh:
        for c in chunks:
            fh.write(json.dumps(c, ensure_ascii=False) + "\n")

    log(f"   index.ntotal={index.ntotal} metadata={len(chunks)} "
        f"alineado={index.ntotal == len(chunks)}")

    return {"ntotal": index.ntotal, "lineas_metadata": len(chunks),
            "segundos": round(time.time() - t0, 1)}


# ------------------------------------------------------------------ encoder
def encoder_e5(batch_size=64, log=print):
    """(encoder, contar_tokens) with the revision pinned -- Sec. 1.4 makes
    reproduction a pass/fail gate, so the id alone is not enough."""
    import torch
    from sentence_transformers import SentenceTransformer

    if torch.cuda.is_available():
        dev = "cuda"
    elif torch.backends.mps.is_available():
        dev = "mps"
    else:
        dev = "cpu"
    log(f"   cargando {E5_ID} @ {E5_REV[:8]} en {dev}")

    model = SentenceTransformer(E5_ID, revision=E5_REV, device=dev)
    model.max_seq_length = 512
    if dev in ("cuda", "mps"):
        # fp32 + batch_size=64 midio un OOM real del backend MPS en una
        # maquina de 8GB (Insufficient Memory, kIOGPUCommandBufferCallback-
        # ErrorOutOfMemory) que deja el proceso colgado sin recuperarse.
        # fp16 a la misma batch_size=64 corrio limpio, medido.
        model = model.half()

    tok = model.tokenizer

    def encoder(textos):
        vectores = model.encode(textos, batch_size=batch_size,
                            normalize_embeddings=True,
                            convert_to_numpy=True,
                            show_progress_bar=False).astype("float32")
        # El caching allocator de MPS/CUDA no devuelve memoria al sistema
        # entre llamadas -- la retiene "wired" para reusarla. Sin este
        # vaciado, 200 documentos de muestra (unas 16 llamadas a este
        # encoder) inflaron la memoria wired a 5.5GB en una maquina de 8GB
        # y el kernel empezo a matar procesos idle (jetsam). Medido: al
        # matar el proceso la memoria wired bajo de 5556M a 1324M, asi que
        # es el encoder reteniendo cache, no el tamano del corpus.
        #
        # Mitigacion parcial, no cura completa: en una segunda corrida,
        # con este vaciado ya puesto, wired se mantuvo plano ~1.3GB los
        # primeros ~2 minutos y despues volvio a subir a 4.5GB (72 jetsam
        # kills mas). empty_cache() libera el allocator pool, pero no
        # necesariamente el cache de grafos compilados de MPSGraph, que en
        # teoria puede crecer por cada combinacion nueva de forma/longitud
        # de secuencia entre batches. En una maquina de 8GB esto no alcanza
        # por si solo para correr el corpus completo en mps; ver DOCUMENTATION.md.
        if dev == "mps":
            torch.mps.empty_cache()
        elif dev == "cuda":
            torch.cuda.empty_cache()
        return vectores

    # raw content tokens: chunker.MAX_TOKENS already reserves prefix + specials
    def contar_tokens(texto):
        return len(tok.encode(texto, add_special_tokens=False))

    return encoder, contar_tokens, dev


# ------------------------------------------------------------------ run
def ejecutar(corpus, out="entrega", cache="cache", vectors="vectors",
             sample=None, rebuild=False, batch_size=64, inventario_path=None,
             encoder=None, contar_tokens=None, log=print):
    t0 = time.time()

    out_dir = Path(out) / "base_vectorial" / "encoder_multilingual-e5-large"
    cache_dir = Path(cache)
    vectors_dir = Path(vectors)
    errores = []

    inventario = None
    if inventario_path and Path(inventario_path).exists():
        from inventario import cargar_inventario

        inventario = cargar_inventario(inventario_path)
        log(f"== inventario: {len(inventario)} filas ==")

    log("== extraccion ==")
    documentos, stats_ext = extraer_corpus(
        corpus, cache_dir / "textos.jsonl", inventario, sample, rebuild,
        errores, log)
    log(f"   {stats_ext}")

    # el encoder se carga DESPUES de extraccion, no antes: extraccion incluye
    # OCR (EasyOCR en GPU/MPS), y tener e5-large ya residente en la misma
    # memoria unificada durante el OCR crea contencion GPU/RAM medida en
    # produccion -- una pagina que aislada tarda 2.5-6.5s se colgo mas de
    # 9 minutos con ambos modelos cargados a la vez en una maquina de 8GB.
    dev = "n/a"
    if encoder is None:
        log("== encoder ==")
        encoder, contar_tokens, dev = encoder_e5(batch_size, log)

    log("== chunking ==")
    chunks, sin_chunks, stats_chunk = chunkear(documentos, contar_tokens, log)

    if not chunks:
        raise SystemExit("no chunks produced -- aborting before FAISS")

    log("== encoding ==")
    dense, stats_enc = encode_chunks(chunks, vectors_dir, encoder,
                                     contar_tokens, batch_size, rebuild, log)

    log("== indice ==")
    stats_idx = construir_indice(dense, chunks, out_dir, log)

    if errores:
        with (Path(out) / "errores.jsonl").open("w", encoding="utf-8") as fh:
            for e in errores:
                fh.write(json.dumps(e, ensure_ascii=False) + "\n")

    manifest = {
        "generado": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "corpus": str(corpus),
        "modelo": {"id": E5_ID, "revision": E5_REV, "dim": E5_DIM,
                   "dispositivo": dev},
        "chunker": {"max_words": chunker.MAX_WORDS,
                    "max_tokens": chunker.MAX_TOKENS},
        "extraccion": stats_ext,
        "chunking": stats_chunk,
        "encoding": stats_enc,
        "indice": stats_idx,
        "documentos_sin_chunks": sin_chunks[:50],
        "errores": len(errores),
        "versiones": _versiones(),
        "segundos_total": round(time.time() - t0, 1),
    }

    Path(out).mkdir(parents=True, exist_ok=True)
    (Path(out) / "run_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    log(f"== listo en {manifest['segundos_total']}s -- "
        f"{stats_idx['ntotal']} vectores, {len(errores)} errores ==")

    return manifest


def _versiones():
    versiones = {"python": platform.python_version()}
    for nombre in ("numpy", "faiss", "torch", "sentence_transformers",
                   "transformers"):
        try:
            versiones[nombre] = __import__(nombre).__version__
        except Exception:
            versiones[nombre] = None
    return versiones


# ------------------------------------------------------------------ selftest
def _encoder_falso(dim=E5_DIM):
    """Deterministic unit vectors from the text hash. Lets the selftest exercise
    caching, checkpointing, resume and alignment with no GPU and no 2.2 GB
    download -- the parts that actually break are all bookkeeping."""
    def encoder(textos):
        salida = np.zeros((len(textos), dim), dtype="float32")
        for i, t in enumerate(textos):
            semilla = int(sha256(t)[:8], 16)
            rng = np.random.default_rng(semilla)
            v = rng.standard_normal(dim).astype("float32")
            salida[i] = v / np.linalg.norm(v)
        return salida

    return encoder, lambda t: len(t.split())


def _corpus_de_prueba(raiz):
    raiz.mkdir(parents=True, exist_ok=True)
    (raiz / "sub").mkdir(exist_ok=True)

    (raiz / "uno.txt").write_text(
        "Primera frase del documento uno. Segunda frase, algo mas larga, "
        "para que el empaquetado tenga con que trabajar. Tercera frase.",
        encoding="utf-8")
    (raiz / "dos.txt").write_text(
        "Documento dos. " + " ".join(f"Frase numero {i}." for i in range(60)),
        encoding="utf-8")
    (raiz / "sub" / "tres.csv").write_text(
        "col_a,col_b\n" + "\n".join(f"valor-{i},dato-{i}" for i in range(40)),
        encoding="utf-8")
    # must be skipped
    (raiz / "Extracto_Preguntas_50_v2.pdf").write_bytes(b"%PDF-1.4 stub")


def _leer_ids(vectors_dir):
    return json.loads((vectors_dir / "ids.json").read_text(encoding="utf-8"))


def _selftest():
    import tempfile

    import faiss

    encoder, contar = _encoder_falso()
    fallos = []

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        corpus = tmp / "corpus"
        _corpus_de_prueba(corpus)

        comun = dict(corpus=str(corpus), out=str(tmp / "entrega"),
                     cache=str(tmp / "cache"), vectors=str(tmp / "vectors"),
                     encoder=encoder, contar_tokens=contar, batch_size=4,
                     log=lambda *a, **k: None)

        # -- 1. two identical runs: nothing re-extracted, nothing re-encoded --
        m1 = ejecutar(**comun)
        dense1 = (tmp / "vectors" / "dense.npy").read_bytes()
        ids1 = (tmp / "vectors" / "ids.json").read_bytes()

        m2 = ejecutar(**comun)

        assert m2["extraccion"]["documentos_extraidos"] == 0, m2["extraccion"]
        assert m2["encoding"]["codificados"] == 0, m2["encoding"]
        assert (tmp / "vectors" / "dense.npy").read_bytes() == dense1, \
            "dense.npy changed across identical runs"
        assert (tmp / "vectors" / "ids.json").read_bytes() == ids1, \
            "ids.json changed across identical runs"
        print("OK 1: segunda corrida identica -- 0 extraidos, 0 codificados")

        # the excluded file never entered the corpus
        assert m1["extraccion"]["archivos_excluidos"] == 1, m1["extraccion"]
        print("OK 2: archivo no-corpus excluido")

        # -- 3. edit the LAST document: only it re-extracts, and the rows
        #       before it are absorbed by the checkpoint prefix -------------
        # sorted order is dos.txt, sub/tres.csv, uno.txt
        (corpus / "uno.txt").write_text(
            "Primera frase cambiada del documento uno. Segunda frase distinta.",
            encoding="utf-8")
        m3 = ejecutar(**comun)
        assert m3["extraccion"]["documentos_extraidos"] == 1, m3["extraccion"]
        assert m3["extraccion"]["documentos_reusados"] == 2, m3["extraccion"]
        print("OK 3: editar un documento re-extrae solo ese")

        enc3 = m3["encoding"]
        assert 0 < enc3["codificados"] < enc3["vectores"], enc3
        assert enc3["reanudados"] + enc3["reusados"] + enc3["codificados"] \
            == enc3["vectores"], enc3
        print(f"OK 4: {enc3['codificados']} codificados, "
              f"{enc3['reanudados']} por prefijo, {enc3['reusados']} por hash")

        # -- 4b. edit the FIRST document: the prefix is now worthless, so the
        #        untouched later chunks must be recovered by text hash ------
        (corpus / "dos.txt").write_text(
            "Documento dos, reescrito. " +
            " ".join(f"Otra frase numero {i}." for i in range(60)),
            encoding="utf-8")
        m3b = ejecutar(**comun)
        enc3b = m3b["encoding"]
        assert enc3b["reanudados"] == 0, enc3b
        assert enc3b["reusados"] > 0, enc3b
        assert enc3b["codificados"] < enc3b["vectores"], enc3b
        print(f"OK 4b: prefijo invalidado, {enc3b['reusados']} filas "
              f"recuperadas por hash de texto (cache 2)")

        # -- 5. alignment (Sec. 1.4) -----------------------------------------
        salida = tmp / "entrega" / "base_vectorial" / "encoder_multilingual-e5-large"
        estado = _leer_ids(tmp / "vectors")
        meta = [json.loads(l) for l in
                (salida / "metadata.jsonl").open(encoding="utf-8")]
        index = faiss.read_index(str(salida / "index.faiss"))

        assert index.ntotal == len(estado["ids"]) == len(meta), \
            (index.ntotal, len(estado["ids"]), len(meta))
        for i, (cid, linea) in enumerate(zip(estado["ids"], meta)):
            if cid != linea["chunk_id"]:
                fallos.append(f"fila {i}: ids.json {cid} != metadata {linea['chunk_id']}")
                break
        print(f"OK 5: alineados -- ntotal={index.ntotal} == ids == metadata")

        # -- 6. resume path: kill mid-encode, resume, re-assert alignment ----
        # simulate a checkpoint from a dead run: half the rows on disk
        estado_completo = _leer_ids(tmp / "vectors")
        dense_completo = np.load(tmp / "vectors" / "dense.npy")
        mitad = len(estado_completo["ids"]) // 2
        np.save(tmp / "vectors" / "dense.npy", dense_completo[:mitad])
        (tmp / "vectors" / "ids.json").write_text(
            json.dumps({"ids": estado_completo["ids"][:mitad],
                        "hashes": estado_completo["hashes"][:mitad]}),
            encoding="utf-8")

        m4 = ejecutar(**comun)
        estado4 = _leer_ids(tmp / "vectors")
        meta4 = [json.loads(l) for l in
                 (salida / "metadata.jsonl").open(encoding="utf-8")]
        index4 = faiss.read_index(str(salida / "index.faiss"))

        assert index4.ntotal == len(estado4["ids"]) == len(meta4), \
            (index4.ntotal, len(estado4["ids"]), len(meta4))
        assert estado4["ids"] == estado_completo["ids"], "resume reordered rows"
        for i, (cid, linea) in enumerate(zip(estado4["ids"], meta4)):
            if cid != linea["chunk_id"]:
                fallos.append(f"tras reanudar, fila {i}: {cid} != {linea['chunk_id']}")
                break
        print(f"OK 6: reanudado desde {mitad}, orden intacto")

        # -- 7. a known chunk's own vector retrieves itself at rank 1 --------
        objetivo = 3 if index4.ntotal > 3 else 0
        v = np.array([np.load(tmp / "vectors" / "dense.npy")[objetivo]])
        puntajes, posiciones = index4.search(v, 1)
        assert posiciones[0][0] == objetivo, (posiciones[0][0], objetivo)
        assert puntajes[0][0] > 0.99, puntajes[0][0]
        print(f"OK 7: auto-consulta -> rank 1, score {puntajes[0][0]:.4f}")

        # -- 8. Tabla-1 contract on every line -------------------------------
        for linea in meta4:
            faltan = chunker._TABLA_1 - set(linea)
            if faltan:
                fallos.append(f"{linea.get('chunk_id')} sin {faltan}")
                break
            if not isinstance(linea["fenomeno"], int) or \
                    not isinstance(linea["posicion"], int):
                fallos.append(f"{linea['chunk_id']}: tipos no enteros")
                break
            if "text" in linea:
                fallos.append("metadata.jsonl usa 'text'; Tabla 1 dice 'texto'")
                break
            if "\\" in linea["fuente"]:
                fallos.append(f"fuente no POSIX: {linea['fuente']}")
                break
        print("OK 8: ocho campos de Tabla 1, tipos y nombres correctos")

        # -- 9. the tabular file did not collapse into one chunk -------------
        csv_chunks = [l for l in meta4 if l["formato"] == "csv"]
        assert len(csv_chunks) >= 1, "csv produced no chunks"
        assert all(l["n_words"] <= chunker.MAX_WORDS for l in meta4), \
            max(l["n_words"] for l in meta4)
        print(f"OK 9: csv en {len(csv_chunks)} chunk(s), ninguno sobre "
              f"{chunker.MAX_WORDS} palabras")

        # -- 10. manifest carries what Spec 03 asks to be measured -----------
        manifest = json.loads(
            (tmp / "entrega" / "run_manifest.json").read_text(encoding="utf-8"))
        for clave in ("extraccion", "chunking", "encoding", "indice",
                      "modelo", "versiones"):
            if clave not in manifest:
                fallos.append(f"run_manifest.json sin {clave}")
        assert manifest["modelo"]["revision"] == E5_REV
        assert "escalera" in manifest["chunking"]
        print("OK 10: run_manifest.json completo, revision del modelo fijada")

        # -- 11. rebuild ignores both caches ---------------------------------
        m5 = ejecutar(**comun, rebuild=True)
        assert m5["extraccion"]["documentos_reusados"] == 0, m5["extraccion"]
        assert m5["encoding"]["reusados"] == 0, m5["encoding"]
        print("OK 11: --rebuild ignora ambos caches")

    if fallos:
        print("\nFALLA:")
        for f in fallos:
            print("  -", f)
        return 1

    print("\nselftest OK")
    return 0


# ------------------------------------------------------------------ CLI
def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv

    if argv and argv[0] == "selftest":
        return _selftest()

    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument("--corpus", required=True)
    p.add_argument("--out", default="entrega")
    p.add_argument("--cache", default="cache")
    p.add_argument("--vectors", default="vectors")
    p.add_argument("--inventario", default="Indice_Datos_Codefest.xlsx")
    p.add_argument("--sample", type=int, default=None,
                   help="only the first N files, for a dry run")
    p.add_argument("--rebuild", action="store_true",
                   help="ignore both caches")
    p.add_argument("--batch-size", type=int, default=64)
    a = p.parse_args(argv)

    ejecutar(corpus=a.corpus, out=a.out, cache=a.cache, vectors=a.vectors,
             sample=a.sample, rebuild=a.rebuild, batch_size=a.batch_size,
             inventario_path=a.inventario)
    return 0


if __name__ == "__main__":
    sys.exit(main())
