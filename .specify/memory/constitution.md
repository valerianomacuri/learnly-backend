# Learnly Refunds Constitution

Fuente de estos principios: `docs/challenge.md`, `specs/000-fundamentos/` (migrada desde `docs/specs/00-fundamentos.md`), las decisiones de `docs/adr/` y las reglas de `CLAUDE.md`. Ante un conflicto, la constitution manda sobre cualquier spec.

## Core Principles

### I. La elegibilidad la decide código determinista, no el LLM
`RefundPolicyEngine` (`domain/eligibility.py`) es una función pura con reloj inyectado que evalúa BR-01…BR-10 y acumula todos los `reason_codes`. El LLM solo explica y cita; no puede cambiar un veredicto. Un intento inelegible no crea solicitud: 422 con `reason_codes` y una fila en `eligibility_checks`. Los umbrales salen del front-matter de `policies/*.md` (única fuente de verdad), no de settings. (ADR 0001, 0005)

### II. Las acciones que mueven dinero nunca están al alcance del LLM
Solo las tools de lectura entran en `LLM_BOUND_TOOLS`. Las de escritura las invocan nodos del grafo, con argumentos del estado y tras un `interrupt()` con confirmación explícita del cliente. La identidad (`user_id`, `role`, `thread_id`) se inyecta en `config["configurable"]`; ninguna tool recibe `customer_id`. El agente no aprueba, paga ni escribe notas. (ADR 0002)

### III. Consistencia: idempotencia, bloqueo y outbox
`POST /refunds` exige `Idempotency-Key` (única por customer) y bloquea la compra `FOR UPDATE`; hay una sola solicitud activa por compra. Aprobar y pagar son operaciones separadas conectadas por outbox; decisiones concurrentes se resuelven con `If-Match`/`version`. (ADR 0003)

### IV. `refund_events` es la única auditoría
Append-only (trigger en BD), escrito solo vía `RefundEventService.append`, con `trace_id` en el payload. No se reintroduce una tabla de auditoría genérica. Solo los tipos en `PUBLIC_EVENT_TYPES` llegan al cliente. (ADR 0006)

### V. Arquitectura en capas y dominio puro
`api/routes → services → repositories → models`. `domain/` no importa SQLAlchemy, FastAPI ni `services/`; está impuesto por `tests/unit/test_architecture.py` y no se debilita. Los servicios reciben un `Principal`; RBAC con permisos `<recurso>:<acción>` y `requires(Perm.X)`.

### VI. Tests primero para reglas de dominio (NON-NEGOTIABLE)
Las reglas de negocio (elegibilidad, máquina de estados, confirmación, razón) se prueban antes de implementarse. La concurrencia e idempotencia se prueban contra Postgres real, no con mocks. El LLM es falso en tests; las evals del RAG y del agente tienen umbrales en `evals/thresholds.toml`.

### VII. Privacidad y observabilidad por defecto
Logs JSON con `trace_id` y `user_id` (nunca email) y filtro de PII. Un customer nunca ve `internal_notes` ni eventos internos; recurso ajeno = 404 (anti-BOLA). Trazas de LLM (LangSmith) opt-in y siempre con scrub de PII; OpenTelemetry apagado por defecto. (ADR 0008)

## Stack y restricciones técnicas

- Python 3.13 con `uv`; FastAPI + SQLAlchemy 2 async + Alembic; PostgreSQL con pgvector; Redis (rate limit y locks); LangGraph/LangChain con OpenAI detrás de una factory.
- Errores `application/problem+json` con `code`, `reason_codes`, `detail` y `trace_id`; esquemas con `extra="forbid"`.
- El frontend es otro repositorio (`../learnly-frontend`, Vite + React 19, ADR 0007); los contratos se comparten vía OpenAPI.
- No se versionan secretos (`.env`). Los scripts de seed se niegan a correr con `ENVIRONMENT=production`.

## Flujo de trabajo y definición de hecho

Toda feature sigue Spec Kit: `/speckit-specify` → `/speckit-plan` → `/speckit-tasks` → `/speckit-implement`, en `specs/NNN-nombre/`. Una feature está hecha cuando:

1. `uv run pytest`, `uv run ruff check . && uv run ruff format --check .`, `uv run mypy` (strict) y `uv run alembic check` están en verde; si toca BD, `alembic upgrade head` desde una BD vacía también.
2. Los criterios de aceptación de la spec están verificados (test automático o comando de demo).
3. `CLAUDE.md` y `README.md` reflejan lo nuevo (comandos, arquitectura, variables de entorno).
4. El estado queda marcado en `specs/README.md` y hay un commit por feature.

Si una decisión cambia durante la implementación, se actualizan de inmediato las specs posteriores afectadas y, si es una decisión de diseño duradera, se añade un ADR en `docs/adr/`.

## Governance

Esta constitution prevalece sobre las demás prácticas. Las enmiendas se hacen con `/speckit-constitution`, se versionan aquí y deben revisar plantillas y specs dependientes. Todo plan debe pasar un "Constitution Check" contra los principios I–VII.

**Version**: 1.0.0 | **Ratified**: 2026-11-23 | **Last Amended**: 2026-11-23
