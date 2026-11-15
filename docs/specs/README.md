# Specs de implementación: Learnly Refunds

Fuente: [`../challenge.md`](../challenge.md). Se implementa **un spec por sesión, en orden**. Cada spec deja el sistema funcional y demostrable.

## Estado

| # | Spec | Estado | Hito |
| --- | --- | --- | --- |
| 00 | [Fundamentos y convenciones](00-fundamentos.md) | ✅ | |
| 01 | [Modelo de datos, semilla y lectura de compras](01-modelo-datos-semilla.md) | ✅ | |
| 02 | [Política activa y RefundPolicyEngine](02-policy-engine.md) | ⬜ | |
| 03 | [Crear solicitud (canal formulario)](03-crear-solicitud.md) | ⬜ | |
| 04 | [API del supervisor](04-supervisor-api.md) | ⬜ | |
| 05 | [Outbox y worker de pagos](05-payout-worker.md) | ⬜ | **Backend completo sin LLM** |
| 06 | [Frontend: formulario y dashboard](06-frontend-base.md) | ⬜ | **Producto usable sin chat** |
| 07 | [RAG: ingesta, recuperación, respuesta](07-rag.md) | ⬜ | |
| 08 | [Evals del RAG y ablación](08-rag-evals.md) | ⬜ | |
| 09 | [Agente: tools y grafo de lectura](09-agente-lectura.md) | ⬜ | |
| 10 | [Agente: flujo de reembolso con HITL](10-agente-hitl.md) | ⬜ | |
| 11 | [Frontend del chat](11-frontend-chat.md) | ⬜ | **Flujo completo con chat** |
| 12 | [Seguridad, límites y resiliencia](12-seguridad-resiliencia.md) | ⬜ | |
| 13 | [Observabilidad, evals del agente, CI y docs](13-observabilidad-ci-docs.md) | ⬜ | **Entregable final** |

Marcar ⬜ → 🟨 (en curso) → ✅ (definición de hecho cumplida).

## Cómo trabajar cada spec

1. `/clear` y pedir: "implementa `docs/specs/NN-….md`". Para 03, 07, 09 y 10 empezar en modo plan.
2. Tests primero para reglas de dominio.
3. Correr la sección **Verificación** del spec y el smoke manual.
4. Cumplir la **Definición de hecho** (abajo), marcar ✅ aquí y, si hay git, un commit por spec.
5. Si una decisión cambia al implementar, **editar el spec siguiente en el momento**.

## Definición de hecho (común a todo spec)

- `uv run pytest`, `uv run ruff check . && uv run ruff format --check .`, `uv run mypy` y `uv run alembic check` en verde (si el spec toca BD, `alembic upgrade head` desde una BD vacía también).
- Los criterios de aceptación del spec verificados (test automático o el comando de demo).
- `CLAUDE.md` y `README.md` actualizados con lo nuevo (comandos, arquitectura, variables de entorno).
- Estado marcado en este índice.

## Plantilla de spec

```md
# NN · Título
**Depende de:** … · **Refs:** challenge §…, BR-…
## Objetivo
## Alcance / Fuera de alcance
## Cambios (modelo y migración · endpoints · servicios/dominio · archivos)
## Tareas (checklist, tests primero)
## Criterios de aceptación
## Verificación (comandos exactos)
```

## Decisiones de reconciliación (challenge ↔ proyecto)

| Tema | Challenge | Proyecto | Decisión |
| --- | --- | --- | --- |
| Python | 3.12 | 3.13 | Mantener 3.13 |
| JWT | claims `sub` + `role` | solo `sub`; rol/permisos leídos de BD en cada request | Mantener lo actual; `role` sale de BD |
| RBAC | `require_role` | permisos `<recurso>:<acción>` + `requires(Perm.X)` | Extender `Perm` y `ROLE_PERMISSIONS` |
| Frontend | Next.js | Vite + React 19 | Vite + **Tailwind CSS** + React Router + TanStack Query |
| Layout | `app/{api,domain,agent,rag,workers,db}` | `src/app/{api,services,repositories,models,schemas,core}` | Se conserva; se añaden `domain/`, `agent/`, `rag/`, `workers/` bajo `src/app` |
| LLM | no fijado | — | **OpenAI** (`langchain-openai`) tras una factory; `OPENAI_API_KEY`, `LLM_MODEL`, `EMBEDDING_MODEL` por env; LLM falso en tests |
| Redis | rate limit y locks | no existe | Se añade en el spec 12 |
| Ingesta | `python -m app.rag.ingest` | — | Entry point en `pyproject` como `seed` |
| Auditoría | `refund_events` append-only | `AuditEvent`/`AuditService` **eliminados** | `refund_events` es la única auditoría; no se reintroduce tabla genérica; login no se audita |
| Aislamiento de dominio | — | servicios reciben `Principal` | Engine y máquina de estados en `domain/`: puros, sin I/O ni SQLAlchemy |

## Matriz de trazabilidad (cada elemento del challenge → un spec)

| Elemento | Spec |
| --- | --- |
| Tablas §3: users/courses/purchases/course_progress/eligibility_checks/refund_requests/refund_events/internal_notes/payouts/credit_ledger/outbox/chat_threads/policy_documents | 01 |
| `policy_chunks` | 07 |
| BR-01…BR-10 (elegibilidad), D1, D8 | 02 |
| BR-11…BR-16, BR-24, D3, D4, D6 | 03 |
| BR-17…BR-21, permisos del supervisor | 04 |
| BR-22, BR-23, D7 | 05 |
| Endpoints `GET /me`, `/purchases*` | 01 |
| `GET /purchases/{id}/refund-eligibility` | 02 |
| `/refunds*` | 03 |
| `/supervisor/*` (salvo stream) | 04 (`retry-payout` en 05) |
| `/supervisor/refunds/stream`, `/policies*` | 06 |
| `/healthz`, `/readyz` | 01 (`/health` ya existe; alias) y 13 (readiness completa) · `/metrics` 13 |
| `/chat/*` | 09 (hilos, mensajes) y 10 (`resume`, `pending-interrupt`) |
| RAG §8 | 07 (pipeline) y 08 (evals, ablación) |
| Tools #1–5,7,8,10 | 09 · Tools #6, #9 (escritura) | 10 |
| Nodos del grafo | 09: `entry_router`, `guard_input`, `classify_intent`, `policy_qa`, `refund_status`, `handoff`, `guard_output`, `respond` · 10: `collect_refund_info`, `check_eligibility`, `explain_ineligible`, `request_confirmation`, `create_refund`, `cancel_refund_flow` |
| Frontend: formulario, dashboard | 06 · chat | 11 |
| Rate limit, presupuesto de tokens, guardrails, injection, BOLA, circuit breaker | 12 |
| Observabilidad, métricas de negocio, evals del agente, CI, ADRs, README, compose completo | 13 |
| Datos semilla (13 casos) | 01 |
