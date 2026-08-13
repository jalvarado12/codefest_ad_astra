# CODEFEST AD ASTRA 2026 — Etapa 1: Base de Conocimiento

Pipeline de recuperación semántica para el reto (Universidad de los Andes /
Fuerza Aeroespacial Colombiana). El corpus vive en
`CORPUS CODEFEST AD ASTRA 2026/` y agrupa documentos de los tres fenómenos
(IA y capacidades estratégicas, seguridad del entorno espacial, dinámicas
territoriales en América Latina) en PDF, JSON, CSV/XLSX, TXT/MD, HTML,
imágenes y PBF.

La documentación completa del sistema está en **`DOCUMENTATION.md`**. Las
especificaciones de diseño y migración, en `specs/`.

## Instalación

```bash
pip install -r requirements.txt
```

El OCR usa **EasyOCR** (paquete de pip, con pesos que se descargan en el primer
uso). Ya no se necesita Tesseract instalado a nivel de sistema.

## Los cuatro programas

| Archivo | Qué hace |
|---|---|
| `extraccion_final.py` | Corpus → texto limpio. Un adaptador por formato, patrón Adapter, todo en memoria. |
| `chunker.py` | Texto limpio → chunks. Solo chunking. |
| `pipeline_final.py` | Orquestador: extrae, chunkea, codifica, indexa. Con caché y reanudación. |
| `generador.py` | Índice + consultas → `resultados.jsonl`. Se entrega y debe correr solo. |

Más `inventario.py`, que reconcilia el corpus contra el inventario de 1.826
filas de ADL y aporta el `adl_doc_id`.

## Uso

```bash
# Autochequeos: sin GPU, sin descargar el modelo, sin red
python extraccion_final.py
python chunker.py
python pipeline_final.py selftest
python generador.py selftest
python inventario.py selftest

# Reconciliar el corpus contra el inventario
python inventario.py "CORPUS CODEFEST AD ASTRA 2026"

# Indexar. --sample N para una corrida de prueba; los dos cachés
# hacen que repetir salga barato.
python pipeline_final.py --corpus "CORPUS CODEFEST AD ASTRA 2026" --batch-size 64

# Responder las 50 consultas
python generador.py \
  --index    entrega/base_vectorial/encoder_multilingual-e5-large/index.faiss \
  --metadata entrega/base_vectorial/encoder_multilingual-e5-large/metadata.jsonl \
  --queries  Extracto_Preguntas_50_v2.pdf \
  --out      entrega/resultados.jsonl

# Todo en una GPU de Colab, incluidos autochequeos y validación
./_gentest/run_step7.sh
```

`pipeline_final.py` deja un `run_manifest.json` junto a la entrega con los
tiempos por etapa, los contadores de la escalera de segmentación, los
percentiles de `num_tokens`, las versiones de las librerías y la revisión
fijada del modelo.

## Extracción

`extraccion_final.py` es un solo archivo autocontenido. `generar_documentos()`
hace `yield` de un documento por archivo:

```python
from extraccion_final import generar_documentos

for doc in generar_documentos("CORPUS CODEFEST AD ASTRA 2026"):
    # doc_id, fuente, nombre_archivo, formato, fenomeno, idioma,
    # texto_limpio, metadata_catalogo, adl_doc_id, catalogo_*
    ...
```

Un archivo es un documento, `.pbf` incluido: el inventario lista los 73 tiles
como filas propias (§2.3), y un documento por tileset no calzaría con ninguna.

Los formatos tabulares (CSV, XLSX, PBF) separan sus filas con **línea en
blanco**, no con salto de línea. El chunker corta bloques en `"\n\n"`, así que
con un solo salto cada archivo tabular llegaba como un bloque único y salía
como un chunk único — el peor caso medido fueron 4.170 palabras en un chunk,
del cual el encoder leía 512 tokens.

## Chunking

`chunker.py` hace chunking y nada más: la limpieza y la detección de estructura
son responsabilidad de la extracción.

Empaqueta **oraciones** bajo **dos topes a la vez** — 250 palabras (§9.2) y 506
tokens (512 menos el prefijo `"passage: "` y los tokens especiales, §4.3) — y
cierra el chunk *antes* de la oración que desbordaría cualquiera de los dos.
Eso es literalmente lo que pide §3.3: *"el corte efectivo debe retroceder al
final de la última oración completa que quepa dentro de ese límite"*.

Una oración nunca se parte. Las unidades que igual desbordan (tablas de
abreviaturas, pies de figura, corridas de campos `| URL: … |`) pasan por una
escalera de cinco niveles; lo que sobrevive se emite intacto y por encima del
tope, porque §3.3 es *"Requisito obligatorio"* y §4.3 solo pide que los
fragmentos se *diseñen* para no superarlo.

## Los scripts viejos

Los `procesar_*.py`, `ocr_*.py`, `json_extract.py` y el paquete `extraccion/`
quedaron **superados** por `extraccion_final.py`. Se conservan como referencia
histórica.

## OCR y device (extracción y encoding)

`extraccion_final.py` ya no llama a EasyOCR con los valores por defecto de la
librería: `canvas_size=1280`, `batch_size=16`, `workers=0` y un tope de
render por página (`OCR_MAX_LADO_PX=2000`) para nunca rasterizar más píxeles
de los que el OCR va a usar. `workers=0` es una medición A/B, no un default
dejado así porque sí: `workers=2` dio 20.88s/página contra 2.75s/página con
`workers=0` (7.6x peor), porque en macOS `DataLoader(num_workers>0)` usa
`spawn`, y `readtext()` se llama una vez por página — el arranque de
subprocesos nunca se amortiza.

`pipeline_final.py` ahora detecta `mps` (Apple Silicon) además de `cuda` y
`cpu`, y castea a fp16 también en `mps` — en fp32 con `batch_size=64` se
midió un OOM real del backend MPS en una máquina de 8GB que cuelga el
proceso en vez de fallar limpio. El encoder también se carga *después* de la
extracción, no antes: tener e5-large ya cargado durante el OCR generó
contención GPU/RAM medida (una página de 2.5–6.5s tardó más de 9 minutos con
ambos modelos en memoria a la vez).

Con este tuning, la extracción del corpus completo (~1.835 archivos, todos los
formatos, OCR incluido) bajó a **22 minutos totales**, contra el estimado
previo de 69.5 minutos solo para el OCR de las 613 páginas escaneadas. El
resultado queda en `cache/textos.jsonl`.

## Pendiente

- `informe_tecnico.pdf` — es calificado, y §3.2 exige justificar explícitamente
  la estrategia de chunking.
- Chunking, encoding e indexación sobre el corpus completo: la extracción ya
  corrió completa, pero lo demás (conteo de chunks, retrieval, la tabla
  25→475 chunks) sigue medido solo sobre la muestra estratificada de 25
  archivos en `DOCUMENTATION.md`.
- Grafo de conocimiento (componente bonus).
