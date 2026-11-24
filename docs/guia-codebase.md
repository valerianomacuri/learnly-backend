# Guía del codebase: `learnly-backend`

Mapa para orientarse en el repositorio y saber qué hace cada archivo. Para el *porqué* de cada decisión ver [`CLAUDE.md`](../CLAUDE.md) (arquitectura detallada), [`docs/adr/`](adr/) y [`specs/README.md`](../specs/README.md).

> Sustituye a la sección "Project structure" del README (que solo lista carpetas). Si añades o renombras archivos, actualiza la tabla correspondiente.

## 1. Qué es

**Learnly Refunds**: backend de reembolsos de una plataforma de cursos.

- **API REST** (FastAPI, `/api/v1`) para clientes (comprar/consultar, pedir reembolso) y supervisores (cola, aprobar/rechazar, notas, SSE en vivo).
- **Motor de elegibilidad determinista** (reglas BR-01…BR-10) cuyos umbrales salen del front-matter de `policies/*.md`.
- **RAG** sobre las políticas (pgvector + búsqueda léxica) con respuestas citadas y verificadas.
- **Agente de soporte** (LangGraph) con herramientas acotadas y confirmación humana (HITL) antes de escribir.
- **Worker de payouts** (outbox transaccional) que ejecuta los reembolsos aprobados.
- Observabilidad (logs JSON, OpenTelemetry, Prometheus, LangSmith) y evals (RAG y agente).

Stack: Python 3.13, FastAPI, SQLAlchemy 2 async, Alembic, PostgreSQL + pgvector, Redis, LangGraph/LangChain, OpenAI, uv.

## 2. Mapa de la raíz

| Ruta | Qué contiene |
| --- | --- |
| `src/app/` | Todo el código de la aplicación (sección 4). |
| `tests/` | Suite pytest (sección 5). |
| `migrations/` | Alembic: `env.py`, `script.py.mako` y `versions/` (sección 6). |
| `policies/` | Políticas de reembolso en Markdown con front-matter YAML: **fuente única de los umbrales** y corpus del RAG. |
| `specs/` | Specs por feature (GitHub Spec Kit): `NNN-nombre/{spec,plan,tasks}.md`, índice en `specs/README.md` (000–013 hechas, siguiente 014). |
| `docs/` | `challenge.md` (enunciado), `rubrica.md`, `demo.md`, `demo-sin-llm.md`, `adr/` (8 decisiones de arquitectura), y esta guía. |
| `evals/thresholds.toml` | Umbrales de aprobación de los evals (`[full]`, `[offline]`, `[agent]`). |
| `reports/` | Informes generados por los evals (`rag-evals-*`, `agent-evals-*`); `first-run/` guarda la primera corrida. |
| `ops/` | Observabilidad: `prometheus.yml`, `alerts.yml`, `otel-collector.yml`, `grafana/` (dashboard + provisioning). |
| `docker/` | `frontend.Dockerfile`, `nginx.conf`, `initdb/` (crea BD de test, habilita pgvector). |
| `.github/workflows/ci.yml` | CI: lint, tipos, tests, evals. |
| `.specify/` | Spec Kit: `memory/constitution.md` (principios y definición de hecho; gana sobre cualquier spec), `templates/`, scripts. |
| `.claude/skills/speckit-*` | Skills de Claude Code para el flujo Spec Kit. |

Archivos sueltos de la raíz:

| Archivo | Para qué |
| --- | --- |
| `pyproject.toml` | Dependencias, entry points (`seed`, `policies-load`, `payouts-worker`, `rag-ingest`), config de pytest (marcadores `live`, `redis`), ruff y mypy estricto. |
| `uv.lock`, `.python-version` | Versiones fijadas (uv, Python 3.13). |
| `alembic.ini` | Config de Alembic. |
| `Makefile` | Atajos: `install db up down migrate seed run test cov lint typecheck rag-evals agent-evals evals demo check`. |
| `Dockerfile`, `docker-compose.yml` | Imagen de la API y stack: `db`, `redis`, `init` (migra, carga políticas, ingesta RAG, seed), `api`, `worker`, `frontend`; perfil `observability`: `otel-collector`, `prometheus`, `grafana`. |
| `.env`, `.env.example` | Variables de entorno (el `.env` real no se versiona). |
| `.pre-commit-config.yaml` | Hooks de lint/format. |
| `README.md` | Puesta en marcha y referencia de desarrollo. |
| `CLAUDE.md` | Notas de arquitectura para agentes/colaboradores (muy detallado, por spec). |

Carpetas generadas que se pueden ignorar: `.venv`, `.mypy_cache`, `.ruff_cache`, `.pytest_cache`, `.hypothesis`, `.coverage`, `__pycache__`.

## 3. Arquitectura en capas

```
HTTP ──► api/routes ──► services ──► repositories ──► models ──► PostgreSQL
            │              │
            │              └──► domain  (reglas puras: sin SQLAlchemy ni FastAPI)
            └─ schemas (DTOs Pydantic en la frontera)

agent/  (LangGraph) ──► tools ──► services      # nunca importa fastapi ni app.api
rag/    (pipeline)  ──► repositories/models     # nunca importa fastapi ni app.services
workers/payouts     ──► outbox ──► gateway
```

Reglas clave (verificadas por `tests/unit/test_architecture.py`):

- Dependencia en un solo sentido: `routes → services → repositories → models`.
- `domain/` es puro (no importa SQLAlchemy, FastAPI ni `app.services`).
- Una transacción por petición: `core/database.get_session` hace commit al final y rollback ante excepción; repositorios solo `flush`.
- Errores de servicio (`services/errors.py`) se traducen en `main.py`; las violaciones de dominio (`domain/errors.py`) salen como `application/problem+json`.
- **Autorización**: roles y permisos se leen de la BD en cada petición; el recurso ajeno se comporta como inexistente (404).
- **Auditoría**: `refund_events` es la única tabla de auditoría (append-only por trigger).

## 4. `src/app/` archivo por archivo

### 4.1 Raíz

| Archivo | Qué hace |
| --- | --- |
| `main.py` | `create_app()`: lifespan (valida política ACTIVE, abre Redis, arranca el `AgentRuntime` si hay `OPENAI_API_KEY`), CORS, middleware `X-Request-ID`/`trace_id`, handlers de errores, `/health` y `/healthz`, monta `ops` en la raíz y el resto bajo `/api/v1`. |
| `__init__.py` | Marca el paquete. |

### 4.2 `api/` — capa HTTP

| Archivo | Qué hace |
| --- | --- |
| `deps.py` | Cableado por petición: `get_principal` (JWT → `Principal` desde BD), guard `requires(Perm.X)`, `rate_limit(...)` (429/503), y fábricas de servicios (`get_refund_service`, …). |
| `problems.py` | Render RFC 9457 `application/problem+json` con `trace_id` (+ `Retry-After` en 429). |
| `routes/auth.py` | `POST /auth/login`: email+password → JWT. |
| `routes/users.py` | `POST/GET /users`, `GET/PATCH /users/{id}`. |
| `routes/me.py` | `GET /me`: perfil, roles y permisos del llamante. |
| `routes/purchases.py` | `GET /purchases` (paginado por cursor), `GET /purchases/{id}`, `GET /purchases/{id}/refund-eligibility`. |
| `routes/refunds.py` | Cliente: `POST /refunds/preview`, `POST /refunds` (idempotente, 5/h), `GET /refunds`, `GET /refunds/{id}`, `GET /refunds/{id}/events`, `POST /refunds/{id}/cancel` (`If-Match`). |
| `routes/supervisor.py` | Supervisor: cola `GET /supervisor/refunds`, `stats`, detalle, `approve`/`reject` (`If-Match`), `retry-payout`, notas (`GET/POST`), eventos y `GET /supervisor/refunds/stream` (SSE). |
| `routes/policies.py` | `GET /policies` y `GET /policies/{doc_key}` (versión ACTIVE + Markdown). |
| `routes/chat.py` | Chat con el agente: hilos, mensajes (SSE `token|tool_call|tool_result|interrupt|final|error`), `resume` (HITL) y `pending-interrupt`. |
| `routes/ops.py` | `/readyz` (BD, Redis, `policy_chunks`) y `/metrics` (Prometheus, token opcional). Ocultos del OpenAPI. |
| `routes/__init__.py` | Agrega los routers en `api_router`. |

### 4.3 `core/` — infraestructura transversal

| Archivo | Qué hace |
| --- | --- |
| `config.py` | `Settings` (pydantic-settings, validadas al arrancar) y `get_settings()`. Umbrales de política **no** están aquí. |
| `database.py` | Engine/sessionmaker async y `get_session` (commit/rollback por petición). |
| `security.py` | `Principal`, `create_access_token`, `decode_access_token` (claims: `sub`, `iat`, `exp`). |
| `passwords.py` | Hash/verificación Argon2id (pwdlib). |
| `permissions.py` | Catálogo RBAC: `RoleName` (customer/supervisor) y `Perm` (`recurso:acción`). |
| `pagination.py` | Cursores opacos keyset `(timestamp, id)` / `(valor, id)`. |
| `privacy.py` | `mask_email`, `redact`, `RedactingFilter` e `install_log_privacy()` (PII fuera de logs). |
| `logging.py` | Logs JSON con `trace_id`/`user_id` por context-var (`configure_logging`). |
| `metrics.py` | Colectores Prometheus de negocio (solicitudes, rechazos, decisiones, payouts muertos, LLM). |
| `telemetry.py` | OpenTelemetry (FastAPI + SQLAlchemy, OTLP/HTTP; apagado por defecto, nunca lanza). |
| `redis.py` | `open_redis` y `ThreadLock` (una ejecución del grafo por hilo). |
| `rate_limit.py` | `SlidingWindowLimiter` (script Lua sobre ZSET, reloj de Redis). |
| `untrusted.py` | `wrap_untrusted`/`defang`: delimita texto no confiable en prompts. |

### 4.4 `domain/` — reglas de negocio puras

| Archivo | Qué hace |
| --- | --- |
| `enums.py` | Enums del dominio (estados de reembolso, canal, actor, método de pago, …). |
| `policy_config.py` | `PolicyConfig` inmutable: parámetros ejecutables de la política activa. |
| `eligibility.py` | `RefundPolicyEngine` (BR-01…BR-10): acumula todos los `reason_codes`; reloj inyectado. |
| `state_machine.py` | Transiciones válidas del reembolso y quién puede dispararlas. |
| `confirmation.py` | Token de confirmación firmado (HMAC sobre `payload_hash`, BR-13). |
| `reason.py` | BR-12: el motivo debe ser una explicación real. |
| `errors.py` | `DomainProblem` y subclases (`code`, `status`, `reason_codes`) → problem+json. |

### 4.5 `models/` — ORM SQLAlchemy (un archivo por agregado)

| Archivo | Entidades |
| --- | --- |
| `base.py` | `Base` con convención de nombres de constraints, `IdMixin` (UUID), `TimestampMixin`, `str_enum` (VARCHAR + CHECK). |
| `user.py` | `User`. |
| `rbac.py` | `Role`, `Permission` (many-to-many con usuarios/roles). |
| `course.py` | `Course`. |
| `purchase.py` | `Purchase`, `CourseProgress`. |
| `refund.py` | `RefundRequest`, `RefundEvent` (append-only por trigger), `RefundInternalNote`, `RefundPayout`, `CreditLedgerEntry`. |
| `eligibility.py` | `EligibilityCheck` (cada verificación se persiste). |
| `outbox.py` | `OutboxEvent` (outbox transaccional). |
| `chat.py` | `ChatThread` (propiedad del hilo; mensajes viven en el checkpointer). |
| `policy.py` | `PolicyDocument` (versión, checksum, contenido, estado). |
| `policy_chunk.py` | `PolicyChunk` (embedding pgvector HNSW + `tsvector`). |

### 4.6 `repositories/` — acceso a datos (uno por entidad)

| Archivo | Qué hace |
| --- | --- |
| `base.py` | `BaseRepository[Model]`: get/list/add/delete (solo `flush`). |
| `scope.py` | `restrict(stmt, principal, Perm.X_READ_ANY)`: scoping por propietario. |
| `user.py` | Usuarios y `get_principal` (roles/permisos desde BD). |
| `purchase.py` | Compras (solo el dueño; paginación keyset). |
| `refund_request.py` | Solicitudes del cliente, locks `FOR UPDATE`, búsqueda por idempotency key. |
| `refund_event.py` | Eventos de auditoría. |
| `refund_note.py` | Notas internas del supervisor. |
| `supervisor_refund.py` | Cola/estadísticas del supervisor sin scoping (`QueueQuery`, `DecisionStats`). |
| `eligibility_check.py` | Persistencia de verificaciones. |
| `payout.py`, `credit_ledger.py`, `outbox.py` | Payouts, libro de créditos y outbox (`SKIP LOCKED`). |
| `chat_thread.py` | Hilos de chat. |
| `policy.py` | Política ACTIVE, `get_active_config()` (cacheada por checksum) y cifras confiables para el RAG. |

### 4.7 `schemas/` — DTOs Pydantic

| Archivo | Contenido |
| --- | --- |
| `base.py` | `RequestDTO`, `ReadDTO`. |
| `auth.py`, `user.py`, `me.py` | Login/token, usuarios (sin campo de password en lectura), perfil. |
| `purchase.py` | Compras, curso, progreso, página (`Money` = Decimal → número JSON). |
| `eligibility.py` | `EligibilityRead` (sin `warnings`). |
| `refund.py` | Preview/creación/lectura de reembolsos, citas, payout, timeline del cliente. |
| `supervisor.py` | Vistas del supervisor (nunca reutiliza las del cliente): cola, detalle, notas, estadísticas. |
| `policy.py` | Resumen y lectura de políticas. |
| `chat.py` | Hilos, mensajes, `ResumeRequest`. |

### 4.8 `services/` — lógica de aplicación

| Archivo | Qué hace |
| --- | --- |
| `errors.py` | `NotFoundError`, `ConflictError`, `ForbiddenError`, `InvalidInputError` y problemas de reembolso (`RefundNotEligible`, `VersionConflict`, `SelfApprovalForbidden`, …). |
| `authz.py` | `require_permission` / `require_any_permission`. |
| `rbac.py` | `ensure_rbac`: sincroniza el catálogo de roles/permisos a la BD. |
| `auth_service.py` | Login (verificación con hash ficticio anti-timing; el fallo se *devuelve*, no se lanza). |
| `user_service.py`, `me_service.py` | Usuarios y perfil. |
| `purchase_service.py` | Compras, ventana de reembolso, cursor keyset. |
| `eligibility_service.py` | Reúne hechos, ejecuta el motor y siempre escribe `eligibility_checks`. |
| `refund_service.py` | **Punto único** de reembolsos: `preview`, `create_request` (idempotente, locks), `cancel`, lecturas. |
| `refund_event_service.py` | Única vía para escribir `refund_events` (+ `pg_notify`, `trace_id`). |
| `supervisor_refund_service.py` | Cola, `_decide` (permiso → lock → autoaprobación → estado → `If-Match` → nota), `retry_payout`, notas, stats. |
| `refund_stream.py` | SSE del supervisor sobre `LISTEN/NOTIFY` (una conexión psycopg por cliente). |
| `chat_service.py` | Propiedad de hilos de chat. |

### 4.9 `policy/` — carga de políticas

| Archivo | Qué hace |
| --- | --- |
| `loader.py` | Parsea `policies/*.md` (front-matter validado + checksum) y construye `PolicyConfig`. |
| `load.py` | `uv run policies-load`: idempotente por checksum, activación atómica. |

### 4.10 `rag/` — recuperación y respuesta

| Archivo | Qué hace |
| --- | --- |
| `chunking.py` | Chunking estructural por encabezados (y alternativa `fixed_500`). |
| `embeddings.py` | Puerto `Embedder`, `OpenAIEmbedder`, `FakeEmbeddings` determinista. |
| `ingest.py` | `uv run rag-ingest`: no-op si nada cambió; embebe y reemplaza chunks en una transacción. |
| `retriever.py` | Híbrido denso + léxico, RRF, reranker opcional, expansión al padre y umbral de relevancia. |
| `answer.py` | `PolicyAnswerer`: respuesta con citas, verificación de cifras y juez de grounding; se abstiene con `needs_handoff`. |
| `llm.py` | Puerto `ChatModel` + `FakeChatModel`. |
| `reason_map.py` | `citations_for`: códigos de razón → secciones de política (sin LLM). |
| `ask.py` | CLI `python -m app.rag.ask "<pregunta>"`. |
| `prompts/policy_answer_v1.md`, `prompts/grounding_judge_v1.md` | Prompts versionados. |
| `evals/` | `golden.py` (79 preguntas), `metrics.py` (puro), `judges.py`, `fakes.py` (offline), `runner.py`, `report.py`, `run.py` (`python -m app.rag.evals.run`). |

### 4.11 `agent/` — agente LangGraph

| Archivo | Qué hace |
| --- | --- |
| `graph.py` | Define el grafo y sus aristas (`entry_router → guard_input → classify_intent → … → guard_output → respond`). |
| `state.py` | `SupportState` (sin `customer_id`/`role`: la identidad se inyecta por config). |
| `runtime.py` | `AgentRuntime`: grafo compilado + checkpointer + ejecución con tracer y presupuesto. |
| `deps.py` | Dependencias inyectadas a los nodos (reloj, factorías, etc.). |
| `principal.py` | `AgentPrincipal`: el cliente autenticado con permisos reducidos (`AGENT_PERMISSIONS`). |
| `llm.py` | `get_chat_model()` (OpenAI T=0 con resiliencia), `FakeChatModel`, `LangChainChat`. |
| `prompts.py` | Prompts del agente (el texto del cliente es dato, no instrucción). |
| `sse.py` | Formato SSE y traducción del stream del grafo. |
| `budget.py` | `UsageCallback` y `TokenBudget` (Redis + fallback en memoria). |
| `resilience.py` | `CircuitBreaker`, reintentos/backoff, `ResilientChatModel`, `LLMUnavailableError`. |
| `tracing.py` | Trazas LangSmith opt-in con scrubbing de PII. |

**`agent/nodes/`** — un módulo por nodo:

| Archivo | Rol |
| --- | --- |
| `entry_router.py` | Reinicia campos por turno y primer enrutado. |
| `guard_input.py` | Límite de longitud, inyección, enmascarado de PII. |
| `classify_intent.py` / `keyword_classifier.py` | Intención con salida estructurada; respaldo por palabras clave tras 2 fallos. |
| `policy_qa.py` | Pregunta de política vía `rag.answer`. |
| `refund_status.py` | Sub-agente ReAct acotado (presupuesto 6) sobre herramientas de lectura. |
| `collect_refund_info.py` | Resuelve compra y motivo (BR-12) o los pide. |
| `check_eligibility.py` | Herramienta 4; ramifica elegible / no elegible. |
| `explain_ineligible.py` | Narra los códigos con citas deterministas. |
| `prepare_confirmation.py` | Construye la tarjeta de confirmación (herramienta 5). |
| `request_confirmation.py` | HITL: `interrupt(card)` como primera sentencia. |
| `create_refund.py` | Herramienta 6, solo tras confirmación (idempotency key derivada). |
| `cancel_refund.py` | Flujo de cancelación con confirmación (herramienta 9). |
| `handoff.py` | Escalado a humano (herramienta 10). |
| `guard_output.py` | Filtra notas internas, ids ajenos, cifras no respaldadas. |
| `respond.py` | Único nodo que escribe el mensaje del asistente. |

**`agent/tools/`** — adaptadores finos sobre servicios:

| Archivo | Herramientas |
| --- | --- |
| `base.py` | `ToolSpec`, `ToolResult{ok,data,error}`, `run_tool` (nunca lanza). |
| `registry.py` | `ALL_TOOLS` y allowlist explícita `LLM_BOUND_TOOLS`. |
| `policy.py` | 1: `search_refund_policy`. |
| `purchases.py` | 2 y 3: compras propias. |
| `eligibility.py` | 4 y 5: elegibilidad y preparación (solo nodos deterministas). |
| `refunds_write.py` | 6 y 9: crear/cancelar (solo tras HITL). |
| `refunds.py` | 7 y 8: solicitudes propias. |
| `escalate.py` | 10: `escalate_to_human`. |

**`agent/guardrails/`**: `injection.py` (heurísticas + juez), `input.py` (juez LLM para mensajes dudosos), `pii.py` (`mask_pii`), `output.py` (`filter_output`).
**`agent/evals/`**: `runner.py`, `run.py` (`python -m app.agent.evals.run [--fake]`), `fakes.py`, `report.py`.

### 4.12 `workers/` y `cli/`

| Archivo | Qué hace |
| --- | --- |
| `workers/payouts.py` | `PayoutWorker` (`payouts-worker [--once]`): reclama outbox con `SKIP LOCKED`, efectos BR-23 en savepoint, backoff, 3 intentos y luego `FAILED`/`DEAD`. |
| `workers/gateway.py` | Puerto `PaymentGateway` y `SimulatedGateway` (idempotente; errores transitorios/permanentes). |
| `cli/seed.py` | `uv run seed [--reset] [--no-tokens]`: cursos, usuarios y los 13 casos del reto (fechas relativas a `now()`). |

## 5. `tests/`

| Ruta | Qué cubre |
| --- | --- |
| `conftest.py` | Fixtures: `session_maker` (BD de test + RBAC + políticas), `client`, `make_user`, `make_course`, `make_purchase`, `make_refund`, `embedder`, `ingested` (RAG), `redis_client`, `broken_redis`. |
| `unit/` | Puros: `test_architecture` (reglas de capas), `test_eligibility`, `test_state_machine`, `test_confirmation`, `test_permissions`, `test_security`, `test_passwords`, `test_pagination`, logs JSON y privacidad. |
| `test_*.py` (raíz) | API/integración: auth, authz, me, purchases, elegibilidad, refunds, supervisor (listados, decisiones, stream), payouts, políticas, seed, chat (+HITL), ops, constraints de BD, trazas, `test_flow` (E2E). |
| `security/` | `permissions_table.py` (tabla viva de quién llama a cada ruta), `test_route_inventory` (falla si una ruta no está en la tabla), BOLA, privacidad, rate limit. |
| `agent/` | Guardrails, herramientas, registro, trayectorias, HITL, escritura, resiliencia, tracing, clasificador; `agent_helpers.py` arma un runtime con `InMemorySaver`; `test_agent_live.py` (marca `live`). |
| `agent_evals/` | `scripts.jsonl` (30 conversaciones), `adversarial.jsonl` (46 casos) y sus tests. |
| `rag/` | Chunking, ingesta, retriever, respuesta, citas, reason map; `test_live.py`. |
| `rag_evals/` | `golden.jsonl`, corpus envenenado `poisoned/`, métricas y corrida offline. |

Los tests `live` (OpenAI real) se excluyen por defecto; los de `redis` se saltan si no hay Redis.

## 6. `migrations/`

| Archivo | Cambio |
| --- | --- |
| `env.py` | Entorno Alembic (metadata de `models`). |
| `versions/0001_initial_auth_and_rbac_schema.py` | Usuarios, roles, permisos. |
| `versions/0002_domain_schema.py` | Dominio de reembolsos (cursos, compras, solicitudes, eventos con trigger append-only, índice único parcial de solicitud activa, …). |
| `versions/0003_refund_request_hash.py` | `request_hash` para creación idempotente. |
| `versions/0004_payout_worker.py` | Revocación de acceso en compras y un crédito de ledger por reembolso. |
| `versions/0005_policy_content.py` | `title` y `content` Markdown en `policy_documents`. |
| `versions/0006_rag_chunks.py` | `policy_chunks` (pgvector + tsvector) y metadatos de embedding. |

## 7. Políticas, specs, ADRs y ops

- **`policies/`**: `refund-eligibility`, `refund-faq`, `refund-methods`, `refund-process`, `refund-status-and-timelines`, `special-cases`. El front-matter fija los umbrales; el cuerpo alimenta el RAG.
- **`specs/`**: 000 fundamentos · 001 modelo/seed · 002 policy engine · 003 crear solicitud · 004 API supervisor · 005 payout worker · 006 frontend base · 007 RAG · 008 RAG evals · 009 agente lectura · 010 agente HITL · 011 frontend chat · 012 seguridad/resiliencia · 013 observabilidad/CI.
- **`docs/adr/`**: 0001 elegibilidad determinista · 0002 escrituras fuera del LLM · 0003 idempotencia y outbox · 0004 chunking · 0005 versionado de políticas · 0006 `refund_events` como única auditoría · 0007 Vite en vez de Next.js · 0008 LangSmith.
- **`ops/`**: reglas de alerta, dashboard Grafana y colector OTel (perfil `observability` de compose).

## 8. Sigue el hilo: flujos de punta a punta

**a) Crear un reembolso (`POST /api/v1/refunds`)**
`api/routes/refunds.py` → `api/deps.py` (`requires`, `rate_limit`) → `services/refund_service.py` (`create_request`: lock de la compra, idempotencia) → `services/eligibility_service.py` → `domain/eligibility.py` (+ `policy_config.py`) → `domain/state_machine.py` → `repositories/refund_request.py` / `eligibility_check.py` → `services/refund_event_service.py` → `models/refund.py`.

**b) Decisión del supervisor y pago**
`api/routes/supervisor.py` → `services/supervisor_refund_service.py` (`_decide`) → crea `refund_payouts` + `outbox` → `workers/payouts.py` → `workers/gateway.py` → efectos (compra `REFUNDED`, ledger) → eventos → `LISTEN/NOTIFY` → `services/refund_stream.py` (SSE al dashboard).

**c) Turno de chat con confirmación humana**
`api/routes/chat.py` → `agent/runtime.py` → `agent/graph.py`: `entry_router` → `guard_input` → `classify_intent` → `collect_refund_info` → `check_eligibility` → `prepare_confirmation` → `request_confirmation` (interrupt) → `POST /chat/threads/{id}/resume` → `create_refund` → `guard_output` → `respond` → `agent/sse.py`.

**d) Pregunta de política**
`classify_intent` → `agent/nodes/policy_qa.py` → `rag/retriever.py` → `rag/answer.py` (cifras + juez) → `guard_output` → `respond`.

## 9. "Quiero cambiar X → abro Y"

| Quiero… | Toca |
| --- | --- |
| Añadir un endpoint | `schemas/` (DTO) → `services/` → `repositories/` si hay SQL → `api/routes/` → registrarlo en `api/routes/__init__.py` → entrada en `tests/security/permissions_table.py`. |
| Añadir un permiso o rol | `core/permissions.py` (+ se sincroniza con `services/rbac.ensure_rbac`) y la tabla de seguridad. |
| Cambiar un umbral (ventana, % de progreso, …) | Front-matter en `policies/*.md` → `uv run policies-load` (y `rag-ingest`). No hay umbrales en `config.py`. |
| Añadir una regla de elegibilidad | `domain/eligibility.py`, `domain/enums.py` (reason code), mapa de citas en la política (`reason_code_map`), `tests/unit/test_eligibility.py`. |
| Cambiar estados del reembolso | `domain/enums.py`, `domain/state_machine.py`, migración nueva (los enums son VARCHAR + CHECK). |
| Añadir una columna/tabla | `models/` → `uv run alembic revision --autogenerate` → `migrations/versions/` → `alembic check`. |
| Nueva herramienta del agente | `agent/tools/` + `registry.py`; si escribe, solo nodo determinista tras HITL (no en `LLM_BOUND_TOOLS`); revisar `tests/agent/test_tool_registry.py`. |
| Nuevo nodo o ruta del grafo | `agent/nodes/`, `agent/graph.py`, `agent/state.py`. |
| Cambiar un prompt | `agent/prompts.py` o `rag/prompts/*.md`; correr los evals. |
| Nuevo setting / variable de entorno | `core/config.py`, `.env.example`, README. |
| Nueva métrica o alerta | `core/metrics.py`, `ops/alerts.yml`, `ops/grafana/learnly.json`. |
| Nueva feature completa | Flujo Spec Kit en `specs/` (siguiente: 014). |

## 10. Comandos útiles

```bash
uv sync                               # instalar
docker compose up -d db redis         # solo datos (o `make db`)
uv run alembic upgrade head           # migrar
uv run policies-load                  # cargar políticas
uv run rag-ingest                     # ingesta RAG (requiere OPENAI_API_KEY)
uv run seed [--reset]                 # datos de desarrollo
uv run uvicorn app.main:app --reload  # API
uv run payouts-worker [--once]        # worker de pagos
uv run pytest                         # tests (sin `live`)
uv run ruff check . && uv run ruff format --check . && uv run mypy
python -m app.rag.evals.run --offline # evals RAG sin red
python -m app.agent.evals.run --fake  # evals del agente sin LLM
make check                            # lint + tipos + tests + evals
docker compose up -d --build          # stack completo (`--profile observability` añade métricas)
```
