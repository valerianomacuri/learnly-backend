# 07 · RAG: ingesta, recuperación y respuesta

**Depende de:** 02 (política y `PolicyConfig`), 03 (preview) · **Refs:** challenge §8 (corpus, ingesta, recuperación, generación y verificación), D2

## Objetivo
Pipeline de RAG de alta precisión sobre los 6 documentos de `policies/`: ingesta idempotente con chunking estructural, recuperación híbrida con rerank y puerta de relevancia, y respuesta con citas verificadas. Demostrable por CLI y con citas reales en `/refunds/preview`.

## Alcance
- Dentro: `policy_chunks`, `app.rag.ingest`, retriever, `answer.py`, mapeo `reason_code → section_path`, integración de citas en preview.
- Fuera: métricas y set dorado (08); uso desde el agente (09/10); reranker pesado en producción (ver nota).

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

## Tareas
- [ ] Tests de chunking con los 6 documentos reales: ningún chunk parte tabla/FAQ; `section_path` correcto; prefijo contextual presente.
- [ ] Ingesta idempotente (segunda corrida no-op), activación atómica (fallo a mitad no deja dos ACTIVE), parámetros faltantes.
- [ ] Retriever sobre Postgres real con `FakeEmbeddings`: RRF correcto, puerta de relevancia, expansión al padre.
- [ ] `answer.py` con LLM falso: verificación de cifras ("son 60 días, no?" → corrige a 30 o se abstiene), abstención, citas válidas.
- [ ] Mapa `reason_code → section_path`: todos los códigos del §3 resuelven a un `section_path` existente en el corpus (test que falla si un documento renombra el encabezado).
- [ ] Prueba manual con OpenAI real (marcada `@pytest.mark.live`, excluida de CI por defecto).

## Criterios de aceptación
- `rag-ingest` dos veces seguidas: la segunda no escribe nada.
- `rag.ask "¿Cuántos días tengo para pedir un reembolso?"` responde 30 días con cita a `Elegibilidad > Ventana de 30 días`.
- Pregunta fuera de alcance ("¿cuál es la capital de Francia?") → abstención + `needs_handoff`.
- Todos los `reason_code` resuelven a una cita existente.
- `/refunds/preview` devuelve citas con el LLM y la API de embeddings deshabilitados.

## Verificación
```bash
uv run rag-ingest && uv run rag-ingest        # 2.ª: no-op
uv run python -m app.rag.ask "¿Puedo pedir reembolso de un regalo?"
uv run pytest tests/rag -q -m "not live" && make check
```
