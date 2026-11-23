# Tasks: 07 · RAG: ingesta, recuperación y respuesta

**Feature**: `007-rag` · **Estado**: ✅ Implementada · **Migrada de**: `docs/specs/07-rag.md`

## Tareas

- [x] Tests de chunking con los 6 documentos reales: ningún chunk parte tabla/FAQ; `section_path` correcto; prefijo contextual presente.
- [x] Ingesta idempotente (segunda corrida no-op), activación atómica (fallo a mitad no deja dos ACTIVE), parámetros faltantes.
- [x] Retriever sobre Postgres real con `FakeEmbeddings`: RRF correcto, puerta de relevancia, expansión al padre.
- [x] `answer.py` con LLM falso: verificación de cifras ("son 60 días, no?" → corrige a 30 o se abstiene), abstención, citas válidas.
- [x] Mapa `reason_code → section_path`: todos los códigos del §3 resuelven a un `section_path` existente en el corpus (test que falla si un documento renombra el encabezado).
- [x] Prueba manual con OpenAI real (`tests/rag/test_live.py`, `@pytest.mark.live`; excluida por defecto).
