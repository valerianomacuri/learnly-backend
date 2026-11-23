# Implementation Plan: 07 · RAG: ingesta, recuperación y respuesta

**Feature**: `007-rag` · **Estado**: ✅ Implementada · **Migrada de**: `docs/specs/07-rag.md`

Spec: [spec.md](spec.md) · Tareas: [tasks.md](tasks.md) · Constitution: [`.specify/memory/constitution.md`](../../.specify/memory/constitution.md)

## Cambios

### Datos
- Migración `0003`: `policy_chunks` (`id, document_id, parent_section_id, section_path, heading_level, chunk_type, content, embedded_text, embedding vector(N), tsv tsvector GENERATED, tags[], token_count, source_lines, metadata JSONB`), índice HNSW coseno (`vector_cosine_ops`) y GIN sobre `tsv`; columnas `embedding_model` y `embedding_dim` en `policy_documents`. `N` desde `EMBEDDING_DIM` (OpenAI `text-embedding-3-small` = 1536 por defecto).
- Config nueva: `OPENAI_API_KEY`, `EMBEDDING_MODEL`, `LLM_MODEL`, `RELEVANCE_THRESHOLD`, `RERANK_MODEL`.

### `src/app/rag/`
- `chunking.py`: `MarkdownHeaderTextSplitter` (H1–H3) conservando `section_path`; subdivide solo secciones > ~800 tokens con `RecursiveCharacterTextSplitter` (overlap 80); **nunca** parte tablas, listas de reglas ni un par P/R de la FAQ (un chunk por pregunta); `chunk_type` (`rule|procedure|faq|table|definition`); prefijo contextual `[título > section_path]` en `embedded_text`; hijo a nivel de regla + `parent_section_id`. Estrategia alternativa `fixed_500` (la usa 08 para la ablación) detrás de la misma interfaz.
- `embeddings.py`: factory sobre `langchain-openai` (`OpenAIEmbeddings`), con interfaz propia y `FakeEmbeddings` determinista para tests.
- `ingest.py` (`python -m app.rag.ingest`, entry point `rag-ingest`, `--strategy`, `--force`): reutiliza `app.policy.loader` del 02; si el `checksum` coincide con la versión ACTIVE → no-op; si no, calcula chunks y embeddings, inserta y **activa atómicamente** (nueva ACTIVE / anterior SUPERSEDED en una transacción). Valida `params` (sigue fallando si falta uno).
- `retriever.py`: (1) reescritura de consulta con historial (LLM, se omite si no hay historial) y detección de idioma; (2) filtro `status=ACTIVE` (+ `tags` opcional); (3) top-20 denso + top-20 léxico (`tsvector`, `websearch_to_tsquery`) fusionados con **RRF**; (4) rerank con cross-encoder (`sentence-transformers`/`bge-reranker`, **extra opcional** `rag-rerank`; si no está instalado, fallback a RRF y se anota en la traza) conservando 5; (5) expansión al padre y deduplicación; (6) **puerta de relevancia**: si el mejor score < umbral → `RetrievalResult(insufficient_evidence=True)`.
- `reason_map.py`: `reason_code → section_path` desde `PolicyConfig.reason_code_map`; `citations_for(reason_codes)` devuelve citas **deterministas** (sin búsqueda).
- `answer.py`: `PolicyAnswer{answer, citations[{doc_key, section_path}], needs_handoff}` con salida estructurada; prompt versionado en `rag/prompts/` (responder solo con `<context>`, citar cada afirmación, contexto = datos no instrucciones, idioma del cliente); **verificación determinista de cifras** (todo número de días/% de la respuesta debe estar en `PolicyConfig`, si no regenerar una vez o abstenerse); **verificación de fundamento** (segundo paso LLM-juez: cada afirmación respaldada por un chunk citado; si no, abstenerse).
- CLI `python -m app.rag.ask "<pregunta>"` que imprime respuesta, citas y trazas de recuperación.
- Integración: `RefundService.preview` devuelve `citations` reales (mapa de reason codes si inelegible; para elegible, citas de "Ventana de 30 días" y método de pago por mapa/etiquetas) — **sin** depender del LLM (el preview debe funcionar con el LLM caído).

## Notas de implementación (desvíos)

- La migración es `0006` (la `0003` ya existía).
- `section_path` almacenado omite el H1; los valores de `reason_code_map` llevan una etiqueta corta delante (`Elegibilidad > …`), que `reason_map.unqualify` elimina.
- Chunking con parser propio de encabezados (para conservar `source_lines`) + `RecursiveCharacterTextSplitter` para prosa larga; `token_count` es una estimación offline (sin descargar tokenizador).
- Verificación de cifras: permitidas las de `PolicyConfig` **y** las que aparecen literalmente en el contexto recuperado (el corpus menciona p. ej. "10 días hábiles", que no es parámetro).
- `/refunds/preview` elegible ahora cita ventana de 30 días + método de reembolso (el test que esperaba `[]` se actualizó).

## Verificación

```bash
uv run rag-ingest && uv run rag-ingest        # 2.ª: no-op
uv run python -m app.rag.ask "¿Puedo pedir reembolso de un regalo?"
uv run pytest tests/rag -q -m "not live" && make check
```
