# Feature Specification: 08 · Evals del RAG y ablación

**Feature**: `008-rag-evals` · **Estado**: ✅ Implementada · **Migrada de**: `docs/specs/08-rag-evals.md`

**Depende de:** 07 · **Refs:** challenge §8 (Evaluación del RAG), §9 (Testing y evals)

## Objetivo

Medir el RAG con un set dorado y umbrales que fallan el CI, y justificar el chunking estructural con una ablación contra tamaño fijo de 500 tokens.

## Alcance

- Dentro: set dorado, métricas, reporte, ablación, `make evals` (parte RAG).
- Fuera: evals del agente (13).

## Criterios de aceptación

- `make evals` imprime la tabla de métricas y falla si baja de un umbral.
- El documento envenenado nunca produce "90 días".
- La ablación documenta la diferencia estructural vs 500 tokens con números.
- Modo `--offline` corre en CI sin red ni clave.
