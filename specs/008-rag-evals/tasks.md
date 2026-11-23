# Tasks: 08 · Evals del RAG y ablación

**Feature**: `008-rag-evals` · **Estado**: ✅ Implementada · **Migrada de**: `docs/specs/08-rag-evals.md`

## Tareas

- [x] Redactar el set dorado (usar los documentos reales de `policies/`; revisar a mano cada `expected_sections`).
- [x] Implementar métricas con funciones puras + tests unitarios de las métricas (casos sintéticos).
- [x] Runner, reporte y umbrales; modo `--offline`.
- [x] Corpus envenenado y test del escenario.
- [x] Ejecutar ablación y guardar el reporte; si estructural no gana, investigar antes de seguir (es parte del argumento del challenge).
- [x] Marcar los evals con claves reales como `live` y fuera de la suite por defecto.
