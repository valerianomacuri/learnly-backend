# Tasks: 08 · Evals del RAG y ablación

**Feature**: `008-rag-evals` · **Estado**: ✅ Implementada (migrada a RAGAS; abstención 0,899 queda bajo su umbral, ver plan) · **Migrada de**: `docs/specs/08-rag-evals.md`

## Tareas (versión con jueces propios, hecha)

- [x] Redactar el set dorado (usar los documentos reales de `policies/`; revisar a mano cada `expected_sections`).
- [x] Implementar métricas con funciones puras + tests unitarios de las métricas (casos sintéticos).
- [x] Runner, reporte y umbrales; modo `--offline`.
- [x] Corpus envenenado y test del escenario.
- [x] Ejecutar ablación y guardar el reporte; si estructural no gana, investigar antes de seguir (es parte del argumento del challenge).
- [x] Marcar los evals con claves reales como `live` y fuera de la suite por defecto.

## Migración a RAGAS

- [x] Añadir `ragas` como dependencia del grupo `dev` (no del runtime de la API); fijar versión y verificar la API de wrappers LangChain.
- [x] Hacer async el protocolo `Judge` y pasarle `question` (`faithfulness(question, answer, chunks)`); adaptar `HeuristicJudge` y `score_case`.
- [x] `rag/evals/ragas_judge.py`: `RagasJudge` con `Faithfulness` (respuesta vs. chunks recuperados) y precisión de citas como `Faithfulness` por cita (solo el chunk citado; cita no recuperada = 0).
- [x] Métricas informativas nuevas: `LLMContextPrecisionWithReference`, `LLMContextRecall` (referencia = `expected_answer_facts`) y `ResponseRelevancy`; omitidas en casos `should_abstain`. Añadirlas a `CaseResult`, `summary()` y `report.LABELS`.
- [x] Conectar `RagasJudge` en `run.py` (modo completo); `--offline` queda con `HeuristicJudge`.
- [x] Eliminar `LLMJudge`, `FAITHFULNESS_PROMPT`, `CITATION_PROMPT` y los `*Verdict` de `judges.py`.
- [x] Tests unitarios del adaptador con un LLM falso (mapeo a `SingleTurnSample`, media por cita) (`tests/rag_evals/test_ragas_judge.py`); el test `live` existente (`test_rag_evals_live.py`) ya corre RAGAS en modo completo.
- [x] Corrida base: RAGAS 0,881 vs. juez propio 0,998 (mismo set); `[full]` recalibrado a faithfulness ≥ 0,85 y citas ≥ 0,80.
- [x] Re-ejecutar la ablación (`structural` y `fixed_500`) y actualizar `docs/adr/0004-chunking.md` con las métricas RAGAS.
- [x] Actualizar el párrafo "RAG evals" de `CLAUDE.md` y el estado en `specs/README.md`; marcar la feature ✅.
