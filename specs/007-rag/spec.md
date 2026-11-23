# Feature Specification: 07 · RAG: ingesta, recuperación y respuesta

**Feature**: `007-rag` · **Estado**: ✅ Implementada · **Migrada de**: `docs/specs/07-rag.md`

**Depende de:** 02 (política y `PolicyConfig`), 03 (preview) · **Refs:** challenge §8 (corpus, ingesta, recuperación, generación y verificación), D2

## Objetivo

Pipeline de RAG de alta precisión sobre los 6 documentos de `policies/`: ingesta idempotente con chunking estructural, recuperación híbrida con rerank y puerta de relevancia, y respuesta con citas verificadas. Demostrable por CLI y con citas reales en `/refunds/preview`.

## Alcance

- Dentro: `policy_chunks`, `app.rag.ingest`, retriever, `answer.py`, mapeo `reason_code → section_path`, integración de citas en preview.
- Fuera: métricas y set dorado (08); uso desde el agente (09/10); reranker pesado en producción (ver nota).

## Criterios de aceptación

- `rag-ingest` dos veces seguidas: la segunda no escribe nada.
- `rag.ask "¿Cuántos días tengo para pedir un reembolso?"` responde 30 días con cita a `Elegibilidad > Ventana de 30 días`.
- Pregunta fuera de alcance ("¿cuál es la capital de Francia?") → abstención + `needs_handoff`.
- Todos los `reason_code` resuelven a una cita existente.
- `/refunds/preview` devuelve citas con el LLM y la API de embeddings deshabilitados.
