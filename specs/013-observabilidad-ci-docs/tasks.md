# Tasks: 13 · Observabilidad, evals del agente, CI y documentación

**Feature**: `013-observabilidad-ci-docs` · **Estado**: ✅ Implementada · **Migrada de**: `docs/specs/13-observabilidad-ci-docs.md`

## Tareas

- [x] Logging JSON + `trace_id` extremo a extremo + test (misma id en logs, evento y traza simulada).
- [x] OTel y exportador; LangSmith tracer (opt-in, fail-open); tests: los runs capturados no contienen emails, tokens ni notas internas (reusar el test del 12), un tracer que falla no afecta la respuesta y con `TRACING_PROVIDER=none` el comportamiento no cambia.
- [x] `/readyz` con dependencias y tests (cada dependencia caída → 503 con detalle).
- [x] Métricas de negocio + tests de que los contadores se incrementan en los flujos clave.
- [x] 30 guiones del agente + runner + umbrales en `evals/thresholds.toml`.
- [x] CI, pre-commit, compose completo; probar `docker compose up` desde cero en una máquina limpia.
- [x] ADRs, README, demo, rúbrica; marcar todos los specs ✅.
