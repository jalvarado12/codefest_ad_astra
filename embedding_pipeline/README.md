# embedding_pipeline

> **Dos archivos, dos públicos.** `embedding_pipeline.py` es el pipeline
> completo con su suite de pruebas (chunking -> encoding -> FAISS -> retrieval
> -> scoring) y vive únicamente en la rama `embedding` -- no se sube a
> `master`. `embeddings_only.py` es un recorte más pequeño de la misma lógica
> de encoders, sin chunking, sin indexado FAISS/sparse, sin fusión RRF y sin
> scoring -- **solo texto de entrada, vectores de salida** -- y ese es el
> archivo que va a `master`. Ver "¿Qué archivo necesito?" más abajo.

Un script autocontenido: chunking -> encoding -> índice FAISS/sparse ->
retrieval -> scoring, para las arquitecturas **A** (multilingual-e5-large
dense), **B** (BAAI/bge-m3 híbrido dense+sparse) y **C** (e5 dense + bge-m3
sparse) -- las tres finalistas en `final-architecture-decision-report.md`.

Es un **port**, no un rediseño: cada función de `embedding_pipeline.py` está
tomada de `arch_test/{chunker,encoders,harness,metrics}.py` y fusionada en un
solo archivo para que el proceso de embedding no quede orquestado a través de
cinco imports. Se descartaron dos cosas a propósito:

- **Arquitecturas D/E** (los brazos de prueba con all-MiniLM-L6-v2) -- fuera
  de alcance, solo A/B/C son finalistas.
- **Extracción multi-formato del corpus** (PDF/HTML/CSV/XLSX/OCR/PBF). Esa
  lógica vive en la rama `master`, bajo `extraccion/`
  (`generar_documentos()`, un pipeline con patrón Adapter), y es propiedad de
  esa rama. Este script no la duplica -- ver "Contrato de entrada" más abajo
  para el punto de unión entre ambas.

## Qué hay aquí

| Archivo | Propósito |
|---|---|
| `embedding_pipeline.py` | El pipeline completo (chunking, encoding, índice FAISS/sparse, retrieval, scoring) + `selftest`/`verify`. Solo en la rama `embedding`. |
| `embeddings_only.py` | Solo las dos clases de encoders (`E5Dense`, `BGEM3`) + una CLI para codificar un JSONL de textos a `.npy`/`.pkl`. Sin chunking, sin FAISS, sin scoring. Va a `master`. |
| `requirements.txt` | Dependencias completas, para `embedding_pipeline.py` (`sentence-transformers`, `FlagEmbedding`, `faiss-cpu`, `psutil`). |
| `requirements_embeddings_only.txt` | Dependencias reducidas, para `embeddings_only.py` (solo `sentence-transformers`, `FlagEmbedding` -- sin `faiss-cpu`). |

## ¿Qué archivo necesito?

- ¿Vas a construir o consultar un índice FAISS real, necesitas el chunker con
  cortes solo en límites de oración, o quieres los self-checks
  `selftest`/`verify`? Usa **`embedding_pipeline.py`** (en `embedding`).
- ¿Ya tienes el texto troceado (chunked) desde otro lado y solo necesitas los
  vectores dense/sparse, sin arrastrar la dependencia de FAISS/chunking? Usa
  **`embeddings_only.py`** (en `master`):

  ```bash
  python embeddings_only.py selftest                     # sin GPU, sin descargas
  python embeddings_only.py encode --model e5 --mode passage \
      --in chunks.jsonl --out-dir out/
  python embeddings_only.py encode --model bge --in chunks.jsonl --out-dir out/
  ```

  Ambas llamadas a `encode` leen el mismo `--text-field texto` (por defecto)
  del JSONL de entrada; `e5` necesita `--mode query`/`passage` (el prefijo
  obligatorio según la especificación), `bge` siempre devuelve ambas cabezas
  en un solo pase, sin importar el modo.

## Contrato de entrada

Un archivo JSONL, un documento ya extraído por línea:

```json
{"doc_id": "DOC-0001", "fuente": "debris_report.pdf", "formato": "pdf",
 "fenomeno": 2, "texto_limpio": "..."}
```

Este esquema coincide con el que produce (`yield`)
`extraccion.pipeline.generar_documentos()` en `master`. (`"texto"` también se
acepta como clave alternativa para documentos generados fuera de ese
pipeline.) Apunta `build` a un volcado JSONL de la salida de ese generador y
el script se encarga del resto -- chunking, encoding, indexado, retrieval,
scoring.

## Entorno

Está pensado para Colab (Linux + GPU). **No** se puede importar en una
máquina Windows local: `sentence-transformers`/`FlagEmbedding` arrastran
`pyarrow`, lo que dispara una política de Application Control de Windows
(`DLL load failed while importing lib`). `python embedding_pipeline.py
selftest` es el único subcomando que corre en cualquier entorno -- usa
encoders stub y no toca ninguna librería de ML.

```bash
pip install -r requirements.txt
```

## Arquitecturas

| Arch | Dense | Sparse | Costo | Notas |
|---|---|---|---|---|
| A | e5-large | -- | 1x | Línea base; los prefijos obligatorios `query:`/`passage:` se aplican por construcción. |
| B | bge-m3 | bge-m3 | ~1x | Ambas cabezas salen de UN solo pase hacia adelante, fusionadas internamente con RRF. |
| C | e5-large | bge-m3 | ~2x | Dos pases de modelo completos; sin atajos de fusión. |

## CLI

```bash
# Self-check de cableado -- sin GPU, sin descarga de modelos, corre en cualquier entorno
python embedding_pipeline.py selftest

# Chequeo de sanidad con modelos reales (descarga los pesos reales) -- solo Colab
python embedding_pipeline.py verify

# Build: chunking + encoding + índice FAISS, escrito en el layout de entrega de la spec
python embedding_pipeline.py build A --docs documentos.jsonl --chunk \
    --out-dir base_vectorial/encoder_A

# Query: retrieval + formateo de salida (+ scoring, si existe un set de validación CONFIRMED)
python embedding_pipeline.py query A --queries validation_confirmed.json

# Query sin un set de validación confirmado (sin NDCG/F1, solo salida con forma de resultados)
python embedding_pipeline.py query A --unscored --queries queries.json
```

## Salidas

- `data/chunks.jsonl` -- metadata de los chunks (campos de la Tabla 1 de la
  spec); el orden de las líneas es el orden de inserción, que también es el
  orden de los ids internos de FAISS.
- `<out-dir>/index.faiss`, `<out-dir>/metadata.jsonl` -- el layout de entrega
  `base_vectorial/encoder_<nombre>/`, cargable con un simple
  `faiss.read_index()`.
- `data/results_<arch>.jsonl` -- una línea por query, con la forma de
  `resultados.jsonl` (`documents` top-3, `fragments` top-10, `<=250`
  palabras, respetando límites de oración).
- `data/summary.json`, `data/timings_*.json` -- costo de indexado, latencia
  de query, NDCG@10/F1@3 cuando se proporcionó un set de validación
  confirmado.

## Verificado en Colab (10-08-2026, sesión T4 vía la CLI de `colab`)

- `selftest`: cableado de A/B/C, round-trip de persistencia del índice --
  correcto.
- `verify`: carga real de e5-large + bge-m3, encoding, normalización L2,
  sanidad cross-lingual / sparse -- correcto.
- `build A` + `query A` contra un corpus sintético de 3 documentos: se
  escribieron un `index.faiss` + `metadata.jsonl` reales; el retrieval
  ordenó correctamente el documento de basura espacial primero para una
  query de basura espacial, y el documento de IA/defensa primero para una
  query de IA/defensa.

## Relación con `arch_test/`

`arch_test/{chunker,encoders,harness,metrics}.py` siguen siendo el harness de
comparación de 5 arquitecturas (A-E) que produjo los números detrás de la
decisión de shortlist -- se conservan como registro histórico. Este
directorio es la herramienta de un solo archivo, solo-finalistas (A/B/C),
para construir y consultar realmente un índice para la entrega. No se borró
ni modificó nada bajo `arch_test/` al separar esto.
