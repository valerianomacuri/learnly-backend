# Feature Specification: 13 · Observabilidad, evals del agente, CI y documentación

**Feature**: `013-observabilidad-ci-docs` · **Estado**: ✅ Implementada · **Migrada de**: `docs/specs/13-observabilidad-ci-docs.md`

**Depende de:** 12 · **Refs:** challenge §9 (observabilidad, métricas, testing, calidad, configuración), §10 (estructura, entregables, rúbrica) · **Entregable final**

## Objetivo

Cerrar el challenge: trazabilidad de punta a punta, métricas de negocio, evals del agente, CI con umbrales, `docker compose up` completo y documentación con ADRs.

## Alcance

- Dentro: logs/trazas/métricas, readiness, evals del agente, CI, compose completo, ADRs, README y demo.
- Fuera: extensiones opcionales (Last-Event-ID, reembolsos parciales, rollout de políticas, caché semántica, emails).

## Criterios de aceptación

- `docker compose up` desde un clon limpio levanta todo con datos semilla; el guion de demo se completa en ≤ 3 min.
- `make test` y `make evals` verdes; CI verde con los umbrales (Recall@5 ≥ 0,90; intención ≥ 95 %; 0 tools no autorizadas; cobertura dominio ≥ 85 %).
- Dado un `refund_id`, se llega a la traza del run que lo creó.
- Con `TRACING_PROVIDER=none` (default) la suite existente (specs 01-12) pasa sin modificaciones.
- 5 ADRs completos y README con diagrama.
