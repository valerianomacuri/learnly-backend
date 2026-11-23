# Implementation Plan: 13 · Observabilidad, evals del agente, CI y documentación

**Feature**: `013-observabilidad-ci-docs` · **Estado**: ✅ Implementada · **Migrada de**: `docs/specs/13-observabilidad-ci-docs.md`

Spec: [spec.md](spec.md) · Tareas: [tasks.md](tasks.md) · Constitution: [`.specify/memory/constitution.md`](../../.specify/memory/constitution.md)

## Cambios

### Observabilidad
- **Logs JSON** (`core/logging.py`) con `trace_id`, `user_id` (id, nunca email) y filtro de PII (12). `trace_id` propagado API → servicio → grafo (`config["configurable"]["request_id"]`) → worker (guardado en `outbox_events.payload`) → `refund_events.payload`.
- **OpenTelemetry** en API (FastAPI + SQLAlchemy instrumentation) y worker; exportador OTLP configurable (apagado por defecto).
- **Trazas del grafo por nodo** con **LangSmith** (`TRACING_PROVIDER=none|langsmith`, por defecto `none`: sin clave no hay llamadas de red; tests y CI siempre en `none`). Aditivo: un `LangChainTracer` explícito se adjunta en `AgentRuntime._run` junto a `UsageCallback`; no se usa el modo global `LANGSMITH_TRACING` (trazaría RAG/evals y filtraría PII) y no se modifican nodos, tools ni grafo.
  - Privacidad (12): el cliente se crea con `hide_inputs`/`hide_outputs` que aplican `mask_pii` y eliminan token de confirmación, hash y notas internas.
  - Metadatos: `request_id` (= `refund_events.payload.trace_id`), `thread_id`, `user_id` (id, nunca email) y `refund_id` (best-effort en `create_refund`); nodo y tool salen como spans; tokens y costo los calcula LangSmith desde `usage_metadata`.
  - Fail-open: un fallo del tracer se loguea y nunca rompe la petición.
  - Criterio: dado un `refund_id`, se reconstruye conversación → nodo → tool → decisión (`refund_events.payload.trace_id` ↔ traza, filtro `metadata.request_id`).
- **`/healthz`** (liveness), **`/readyz`** (DB, Redis, vector store con timeout corto; 503 si falla), **`/metrics`** Prometheus (interno, protegido por token de servicio o red interna).
- **Métricas de negocio** (`prometheus-client`): solicitudes por canal, rechazos de elegibilidad por `reason_code`, tasa de confirmación en el HITL, tasa de aprobación, tiempo hasta decisión (histograma), SLA vencidos (gauge), outbox pendiente/DEAD, latencia/costo del LLM. Reglas de alerta de ejemplo (`ops/alerts.yml`): SLA vencidos > umbral, DEAD > 0. Dashboard Grafana JSON opcional.

### Evals del agente (`tests/agent_evals/scripts.jsonl`)
30 guiones de conversación: `{id, turns:[…], expected_intent, expected_tools_allowed, expected_outcome (refund_created|ineligible_explained|abstained|handoff|cancelled|…)}`. Runner `python -m app.agent.evals.run` (modo `--fake` con `FakeChatModel` para CI; modo real opcional). Métricas: intención correcta ≥ 95 %, **cero** llamadas a tools no autorizadas, finalización de tarea ≥ 90 %. Se une a `make evals`.

### CI (`.github/workflows/ci.yml`)
Jobs: `lint` (ruff, mypy strict), `unit`, `api` (servicio Postgres+pgvector y Redis), `graph` (LLM falso), `rag-evals --offline`, `agent-evals --fake`, `alembic upgrade head` + `alembic check`, frontend (`lint`, `test`, `build`). Cobertura ≥ 85 % en `domain/`. `pre-commit` (ruff, ruff-format, mypy) documentado. CI en verde bloquea el merge (branch protection documentada).

### Compose y DX
`docker-compose.yml`: `db` (pgvector), `redis`, `api`, `worker`, `frontend` (build estático servido por nginx o `vite preview`), perfil `observability` opcional para OTel collector/Prometheus/Grafana (LangSmith es SaaS o self-host aparte; variables opcionales `TRACING_PROVIDER`, `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT`, `LANGSMITH_ENDPOINT` en `.env.example`/README); job de inicio `migrate` → `policies-load/rag-ingest` → `seed`. `docker compose up` desde cero deja todo con datos semilla. `Makefile`: `up`, `down`, `test`, `evals`, `demo`.

### Documentación
- `docs/adr/` con **5 ADRs mínimos** (contexto, decisión, alternativas, consecuencias): `0001-elegibilidad-determinista`, `0002-tools-de-escritura-fuera-del-llm`, `0003-idempotencia-y-outbox`, `0004-estrategia-de-chunking` (con los números de la ablación del 08), `0005-versionado-de-politicas`. Opcionales: `0006-refund-events-unica-auditoria`, `0007-vite-en-lugar-de-nextjs`, `0008-langsmith-para-trazas` (alternativas: Langfuse, solo OTel; consecuencia: los datos salen a un SaaS → opt-in + scrub de PII).
- `README.md` raíz del proyecto con diagrama de arquitectura (Mermaid), `docker compose up`, variables de entorno, tabla de comandos y **guía de demo de 3 minutos** (`docs/demo.md`): chat → tarjeta → confirmar → supervisor aprueba → worker → completado; luego el caso inelegible y el 409 con dos supervisores.
- Revisión final: matriz de trazabilidad del índice (cada fila cubierta), rúbrica del §10 autoevaluada en `docs/rubrica.md`.

## Verificación

```bash
docker compose down -v && docker compose up -d --build && curl -s localhost:8000/readyz
make test && make evals && make lint typecheck
curl -s localhost:8000/metrics | grep refund_
```
