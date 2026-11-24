# Feature Specification: 08 · Evals del RAG y ablación

**Feature**: `008-rag-evals` · **Estado**: ✅ Implementada (migrada a RAGAS) · **Migrada de**: `docs/specs/08-rag-evals.md`

**Depende de:** 07 · **Refs:** challenge §8 (Evaluación del RAG), §9 (Testing y evals)

## Objetivo

Medir el RAG con un set dorado y umbrales que fallan el CI, y justificar el chunking estructural con una ablación contra tamaño fijo de 500 tokens. Las métricas de calidad de generación (faithfulness, precisión de citas, relevancia y calidad del contexto) las calcula **RAGAS**; no se mantienen jueces LLM ni prompts propios.

## Alcance

- Dentro: set dorado, métricas (RAGAS + métricas deterministas propias), reporte, ablación, `make evals` (parte RAG).
- Fuera: evals del agente (13).

## Criterios de aceptación

- `make evals` imprime la tabla de métricas y falla si baja de un umbral.
- Faithfulness y precisión de citas se calculan con RAGAS; el repo no contiene jueces LLM ni prompts de juez propios.
- Recall@5/MRR, abstención correcta, cifras vs `PolicyConfig`, fugas del documento envenenado y latencia siguen siendo métricas deterministas propias (RAGAS no las cubre).
- El documento envenenado nunca produce "90 días".
- La ablación documenta la diferencia estructural vs 500 tokens con números, incluidas las métricas RAGAS.
- Modo `--offline` corre en CI sin red ni clave (no usa RAGAS: requiere un LLM).
