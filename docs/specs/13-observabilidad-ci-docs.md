# 13 · Observabilidad, evals del agente, CI y documentación

**Depende de:** 12 · **Refs:** challenge §9 (observabilidad, métricas, testing, calidad, configuración), §10 (estructura, entregables, rúbrica) · **Entregable final**

## Objetivo
Cerrar el challenge: trazabilidad de punta a punta, métricas de negocio, evals del agente, CI con umbrales, `docker compose up` completo y documentación con ADRs.

## Alcance
- Dentro: logs/trazas/métricas, readiness, evals del agente, CI, compose completo, ADRs, README y demo.
- Fuera: extensiones opcionales (Last-Event-ID, reembolsos parciales, rollout de políticas, caché semántica, emails).

## Cambios

### Observabilidad
- **Logs JSON** (`core/logging.py`) con `trace_id`, `user_id` (id, nunca email) y filtro de PII (12). `trace_id` propagado API → servicio → grafo (`config["configurable"]["request_id"]`) → worker (guardado en `outbox_events.payload`) → `refund_events.payload`.
- **OpenTelemetry** en API (FastAPI + SQLAlchemy instrumentation) y worker; exportador OTLP configurable (apagado por defecto).
- **Trazas del grafo por nodo** con Langfuse (o LangSmith, elegir uno vía `TRACING_PROVIDER`): callback de LangChain por run, con tokens y costo; metadatos `refund_id`, `thread_id`, `node`, `tool`. Criterio: dado un `refund_id`, se reconstruye conversación → nodo → tool → decisión (`refund_events.payload.trace_id` ↔ traza).
- **`/healthz`** (liveness), **`/readyz`** (DB, Redis, vector store con timeout corto; 503 si falla), **`/metrics`** Prometheus (interno, protegido por token de servicio o red interna).
- **Métricas de negocio** (`prometheus-client`): solicitudes por canal, rechazos de elegibilidad por `reason_code`, tasa de confirmación en el HITL, tasa de aprobación, tiempo hasta decisión (histograma), SLA vencidos (gauge), outbox pendiente/DEAD, latencia/costo del LLM. Reglas de alerta de ejemplo (`ops/alerts.yml`): SLA vencidos > umbral, DEAD > 0. Dashboard Grafana JSON opcional.

### Evals del agente (`tests/agent_evals/scripts.jsonl`)
30 guiones de conversación: `{id, turns:[…], expected_intent, expected_tools_allowed, expected_outcome (refund_created|ineligible_explained|abstained|handoff|cancelled|…)}`. Runner `python -m app.agent.evals.run` (modo `--fake` con `FakeChatModel` para CI; modo real opcional). Métricas: intención correcta ≥ 95 %, **cero** llamadas a tools no autorizadas, finalización de tarea ≥ 90 %. Se une a `make evals`.

### CI (`.github/workflows/ci.yml`)
Jobs: `lint` (ruff, mypy strict), `unit`, `api` (servicio Postgres+pgvector y Redis), `graph` (LLM falso), `rag-evals --offline`, `agent-evals --fake`, `alembic upgrade head` + `alembic check`, frontend (`lint`, `test`, `build`). Cobertura ≥ 85 % en `domain/`. `pre-commit` (ruff, ruff-format, mypy) documentado. CI en verde bloquea el merge (branch protection documentada).

### Compose y DX
`docker-compose.yml`: `db` (pgvector), `redis`, `api`, `worker`, `frontend` (build estático servido por nginx o `vite preview`), `langfuse` (+ su Postgres) en perfil `observability`; job de inicio `migrate` → `policies-load/rag-ingest` → `seed`. `docker compose up` desde cero deja todo con datos semilla. `Makefile`: `up`, `down`, `test`, `evals`, `demo`.

### Documentación
- `docs/adr/` con **5 ADRs mínimos** (contexto, decisión, alternativas, consecuencias): `0001-elegibilidad-determinista`, `0002-tools-de-escritura-fuera-del-llm`, `0003-idempotencia-y-outbox`, `0004-estrategia-de-chunking` (con los números de la ablación del 08), `0005-versionado-de-politicas`. Opcionales: `0006-refund-events-unica-auditoria`, `0007-vite-en-lugar-de-nextjs`.
- `README.md` raíz del proyecto con diagrama de arquitectura (Mermaid), `docker compose up`, variables de entorno, tabla de comandos y **guía de demo de 3 minutos** (`docs/demo.md`): chat → tarjeta → confirmar → supervisor aprueba → worker → completado; luego el caso inelegible y el 409 con dos supervisores.
- Revisión final: matriz de trazabilidad del índice (cada fila cubierta), rúbrica del §10 autoevaluada en `docs/rubrica.md`.

## Tareas
- [ ] Logging JSON + `trace_id` extremo a extremo + test (misma id en logs, evento y traza simulada).
- [ ] OTel y exportador; Langfuse con callback; test de que los prompts/trazas no contienen emails ni notas internas (reusar el test del 12 sobre el callback).
- [ ] `/readyz` con dependencias y tests (cada dependencia caída → 503 con detalle).
- [ ] Métricas de negocio + tests de que los contadores se incrementan en los flujos clave.
- [ ] 30 guiones del agente + runner + umbrales en `evals/thresholds.toml`.
- [ ] CI, pre-commit, compose completo; probar `docker compose up` desde cero en una máquina limpia.
- [ ] ADRs, README, demo, rúbrica; marcar todos los specs ✅.

## Criterios de aceptación
- `docker compose up` desde un clon limpio levanta todo con datos semilla; el guion de demo se completa en ≤ 3 min.
- `make test` y `make evals` verdes; CI verde con los umbrales (Recall@5 ≥ 0,90; intención ≥ 95 %; 0 tools no autorizadas; cobertura dominio ≥ 85 %).
- Dado un `refund_id`, se llega a la traza del run que lo creó.
- 5 ADRs completos y README con diagrama.

## Verificación
```bash
docker compose down -v && docker compose up -d --build && curl -s localhost:8000/readyz
make test && make evals && make lint typecheck
curl -s localhost:8000/metrics | grep refund_
```
