# Specs de implementación — Learnly Customer Support (Refunds)

Fuente: [`challenge.md`](../../challenge.md). Estos specs convierten el challenge en trabajo ejecutable sobre la base que ya existe (FastAPI + SQLAlchemy 2 async + Postgres/pgvector, auth JWT + RBAC; ver [`CLAUDE.md`](../../CLAUDE.md)). Cada spec se implementa en orden; cada uno deja el sistema en verde (`uv run pytest`, `ruff`, `mypy`).

| # | Spec | Entrega | Depende de |
| --- | --- | --- | --- |
| 01 | [REST API + frontend customer/supervisor](01-rest-api-and-frontend.md) | Dominio, BD, endpoints de la sección 5 (salvo chat), worker de payouts, SSE del supervisor, UI de formulario manual y dashboard | base existente |
| 02 | [Tools con permisos + agente simple](02-ai-tools-and-agent-loop.md) | Las 10 tools con matriz de permisos y un agentic loop ReAct de solo lectura | 01 |
| 03 | [RAG de políticas + evaluaciones](03-rag-policies-and-evals.md) | Ingesta, recuperación híbrida, respuesta con citas, tool `search_refund_policy`, evals y ablación | 01, 02 |
| 04 | [Grafo LangGraph + checkpoint + estado](04-langgraph-graph-and-checkpointing.md) | `SupportState`, grafo con HITL, `AsyncPostgresSaver`, API de chat SSE, widget de chat | 01, 02, 03 |

Dependencias duras entre specs (puntos de acople que hay que respetar):

- 01 define `PolicyConfig` y lo lee del documento ACTIVE `refund-eligibility` en `policy_documents.params`; como la ingesta aún no existe, el seed inserta un documento *bootstrap* (`policies/bootstrap/`). 03 lo reemplaza por la ingesta completa de los 6 documentos sin cambiar la interfaz.
- 01 deja `policy_documents`/`policy_chunks` creadas (chunks vacíos) y `domain/citations.py` con el mapa `reason_code → section_path`; 03 fija el mapa definitivo, añade índices (migración `0003`) y puebla los chunks.
- 02 llama a los servicios de 01 (`PurchaseService`, `RefundService`, `RefundPolicyEngine`) con un `Principal(acting_via="agent")`; 03 completa la tool `search_refund_policy`; 04 orquesta las tools de 02.

## Decisiones transversales

| Tema | Decisión |
| --- | --- |
| Ubicación de IA | Todo lo de IA vive en `src/app/ai/` (`llm.py`, `tools/`, `agent/`, `rag/`, `graph/`, `guardrails/`, `prompts/`, `evals/`). Dominio y REST siguen en `services/ repositories/ models/ schemas/ api/`. `app/ai` puede importar `services`; **nunca** al revés. |
| Proveedor LLM | OpenAI vía `langchain-openai`. Modelos por variable de entorno (`LLM_MODEL`, `LLM_MODEL_FAST`, `EMBEDDING_MODEL`, `EMBEDDING_DIM`). Todo detrás de `app/ai/llm.py`; cambiar de proveedor no toca dominio. |
| Frontend | Se mantiene Vite + React 19 (`learnly-frontend`). **Estilos: Tailwind CSS v4** (`@tailwindcss/vite`); sin librerías de componentes con CSS propio. |
| Errores | `application/problem+json` (RFC 9457) con `type`, `title`, `status`, `detail`, `code`, `reason_codes`, `trace_id`. Se conserva `detail`, por lo que los tests existentes siguen valiendo. |
| Identidad | El JWT sigue llevando solo `sub` (+`iat`, `exp`). El rol y los permisos se leen de la BD en cada request (desviación consciente del challenge, que los pone en el token: así un cambio de rol surte efecto sin token nuevo). |
| Python | 3.13 (el challenge dice 3.12; el repo ya usa 3.13). |
| Transacciones | Se respeta la regla del repo: un commit por request en `get_session`; un resultado que **debe persistir** (p. ej. `eligibility_checks` de un intento rechazado) se *devuelve*, no se lanza como excepción, porque la excepción hace rollback. |
| Tiempo | Todo en UTC. Cualquier lógica con fechas recibe un `Clock` inyectable (`app/core/clock.py`). |
| Rate limit | En memoria (por proceso) en esta fase; documentado como limitación. Redis queda para después. |

## Fuera de estos specs (fase posterior)

- **Todo el monitoreo/observabilidad** se agrupa en un futuro `05-observability`: `/metrics` Prometheus, métricas de negocio y alertas de SLA, OpenTelemetry, trazas por nodo en Langfuse/LangSmith, logs JSON estructurados. Lo único que se conserva por ser contractual: un `trace_id` simple (request-id por middleware) en los errores y en `refund_events`, y `/healthz` + `/readyz` básicos (DB y vector store).
- Redis (rate limit distribuido, locks), docker-compose completo con worker y frontend, ADRs, extensiones opcionales del challenge (SSE `Last-Event-ID`, reembolsos parciales, rollout gradual de políticas, caché semántica, emails).

## Mapa de trazabilidad (challenge → spec)

| Challenge | Spec |
| --- | --- |
| §2 Roles y permisos | 01 (catálogo `Perm`), 02 (matriz tool → permiso) |
| §3 Modelo de datos | 01 |
| §4 BR-01..BR-24 y máquina de estados | 01 (BR-01..24); 04 (BR-12/13 en el flujo de chat) |
| §5 Endpoints | 01 (todo salvo `/chat/*`), 04 (`/chat/*`) |
| §6 Grafo | 04 |
| §7 Tools | 02 (+ `search_refund_policy` real en 03) |
| §8 RAG y evaluación | 03 |
| §9 Requisitos transversales | repartidos; observabilidad → fase posterior |
| §10 Estructura, entregables | 01–04; ADRs y compose completo → fase posterior |

## Glosario

- **HITL**: human-in-the-loop; confirmación explícita del cliente antes de una escritura.
- **Principal**: identidad autenticada con permisos (`app/core/security.py`); el agente usa el del customer con `acting_via="agent"`.
- **Outbox**: tabla de eventos que se escriben en la misma transacción que el cambio de negocio y que un worker entrega después.
- **BOLA/IDOR**: acceso a un recurso ajeno por id; aquí siempre responde 404.
