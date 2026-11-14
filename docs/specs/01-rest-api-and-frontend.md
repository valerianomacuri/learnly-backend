# Spec 01 — REST API completa + frontend (customer y supervisor)

Fuente: `challenge.md` §2, §3, §4, §5, §9 (consistencia, autorización, auditoría, límites, testing) y "Frontend mínimo". Los endpoints `/chat/*` y el widget de chat **no** están aquí (spec 04).

## 1. Objetivo

Dejar funcionando de punta a punta, sin IA, el flujo: el customer ve sus compras, simula elegibilidad, solicita un reembolso por formulario (preview → confirmación → creación idempotente), lo sigue y puede cancelarlo; el supervisor lo revisa en un dashboard, lo aprueba/rechaza con control de concurrencia, y un worker ejecuta el pago contra un gateway simulado. Todo con una única puerta de dominio (`RefundService.create`) que luego usarán las tools del agente (D3).

## 2. Alcance

**Dentro:** modelos y migración, catálogo de permisos, `RefundPolicyEngine`, máquina de estados, servicios, repositorios, schemas, routers, errores problem+json, idempotencia, `confirmation_token`, worker de payouts + outbox, SSE del supervisor (LISTEN/NOTIFY), rate limit en memoria, seeds con los 13 casos borde, frontend con Tailwind, tests.

**Fuera:** chat (04), RAG real e ingesta (03; aquí solo un documento *bootstrap* para `PolicyConfig` y el visor), Redis, métricas/OTel/Langfuse, pagos reales, emails.

## 3. Estructura de archivos

Se respeta `api/routes → services → repositories → models`. El motor de elegibilidad es **puro** (sin SQLAlchemy ni FastAPI) y vive en `app/domain/`.

```text
src/app/
  core/
    clock.py              # Clock protocol + SystemClock (inyectable en tests)
    problems.py           # ProblemError base, handlers problem+json, RequestValidationError → 422
    request_id.py         # middleware: X-Request-ID → trace_id (contextvar)
    rate_limit.py         # ventana deslizante en memoria + dependencia FastAPI
    confirmation.py       # emisión/verificación del confirmation_token (JWT HS256, typ=refund_confirmation)
    pagination.py         # cursor keyset opaco (base64 de {sort_value,id})
    permissions.py        # (editar) nuevos Perm y ROLE_PERMISSIONS
  domain/
    reasons.py            # ReasonCode, Warning (StrEnum)
    policy_config.py      # PolicyConfig (pydantic, frozen)
    policy_engine.py      # RefundPolicyEngine.evaluate(...) -> EligibilityResult (puro)
    refund_state.py       # RefundStatus, transiciones permitidas, can_transition()
    citations.py          # REASON_SECTIONS: reason_code → (doc_key, section_path); valores provisionales, 03 los fija
    masking.py            # mask_email()
  models/                 # course, purchase, course_progress, refund_request, refund_event,
                          # refund_internal_note, refund_payout, credit_ledger, outbox_event,
                          # eligibility_check, chat_thread, policy_document, policy_chunk
  repositories/           # una por agregado (purchase, refund, event, note, payout, outbox,
                          # eligibility_check, credit_ledger, policy)
  schemas/                # purchase, refund, supervisor, policy, common (Page[T], Problem)
  services/
    purchase_service.py
    eligibility_service.py   # evalúa con el engine + persiste eligibility_checks
    refund_service.py        # preview / create / cancel / approve / reject
    payout_service.py        # retry (supervisor) + apply_payout_result (worker)
    supervisor_service.py    # cola, detalle, stats, notas
    policy_service.py        # visor + proveedor de PolicyConfig
    events.py                # RefundEventWriter (append-only + pg_notify)
  api/routes/             # me.py purchases.py refunds.py supervisor.py policies.py health.py
  workers/
    payouts.py            # python -m app.workers.payouts
    gateway.py            # PaymentGateway protocol + SimulatedGateway
  cli/seed.py             # (editar) datos de dominio + policy bootstrap
policies/bootstrap/refund-eligibility.md   # front-matter con params; 03 lo reemplaza
```

## 4. Permisos (extensión de `core/permissions.py`)

Convención existente `<resource>:<action>`, `*_any` / `*_own`.

| Perm | Customer | Supervisor |
| --- | --- | --- |
| `purchase:read_own` | ✔ | – |
| `eligibility:simulate` | ✔ | – |
| `refund:create` | ✔ | – (BR-11) |
| `refund:read_own` | ✔ | – |
| `refund:read_any` | – | ✔ |
| `refund:cancel_own` | ✔ | – |
| `refund:decide` | – | ✔ |
| `payout:retry` | – | ✔ |
| `note:read` / `note:write` | – | ✔ |
| `policy:read` | ✔ | ✔ |
| `chat:use` (declarado aquí, usado en 04) | ✔ | – |

Reglas: `requires(Perm.X)` en el router (403 si falta); el servicio re-verifica con `require_permission` (patrón actual). El acceso a recursos ajenos del customer se resuelve con `restrict(...)` → **404**, nunca 403. Los endpoints `/supervisor/*` exigen `refund:read_any` o el permiso específico: un customer recibe 403 en todos.

## 5. Modelo de datos

Reutilizar `IdMixin`, `TimestampMixin` y `str_enum` (VARCHAR + CHECK, **máx. 20 caracteres** por valor: todos los enums de abajo caben). Migración Alembic `0002_refund_domain.py` (autogenerate + revisión manual; `alembic check` debe quedar limpio). Los campos del challenge se mantienen; abajo solo lo que se añade o se precisa.

| Tabla | Añadidos / precisiones sobre `challenge.md` §3 |
| --- | --- |
| `courses` | tal cual |
| `purchases` | + `access_revoked_at timestamptz null` (BR-23: revocar acceso). Índice `(customer_id, purchased_at desc)` |
| `course_progress` | PK = `purchase_id`; `progress_pct` con CHECK 0..100 |
| `refund_requests` | + `policy_snapshot jsonb` (volcado de `PolicyConfig` usado; junto con `policy_version` cumple D8) · + `request_hash text` (sha256 del cuerpo canónico para idempotencia) · + `flags text[]` (p. ej. `HIGH_REFUND_FREQUENCY`) · `version int default 1` con `version_id_col` del mapper · `UNIQUE(customer_id, idempotency_key)` · **índice único parcial** `UNIQUE(purchase_id) WHERE status IN ('PENDING_APPROVAL','APPROVED','PROCESSING','FAILED')` · índices `(status, created_at)`, `(customer_id, created_at desc)` |
| `eligibility_checks` | `source`: `PREVIEW`, `ELIGIBILITY`, `CREATE`, `AGENT`; `reason_codes text[]`; `details jsonb` (= `EligibilityResult`) |
| `refund_events` | + `trace_id text`, + `acting_for uuid null` (customer en nombre del cual actúa el agente). **Append-only**: función + trigger `BEFORE UPDATE OR DELETE` que lanza excepción. El DDL se adjunta al modelo con `event.listen(table, "after_create", DDL(...))` para que **también** exista cuando los tests usan `Base.metadata.create_all` |
| `refund_internal_notes` | tal cual; solo supervisor |
| `refund_payouts` | `UNIQUE(refund_id)`; `status`: `PENDING`, `COMPLETED`, `FAILED`; `idempotency_key = refund_id` |
| `credit_ledger` | append-only (mismo trigger); saldo = `SUM(delta)`; `UNIQUE(refund_id)` evita doble crédito |
| `outbox_events` | `topic` (`refund.approved`, `refund.payout_retry`, `refund.notify`), `status`: `PENDING`, `DONE`, `DEAD`; `attempts`; `available_at`; índice parcial `(available_at) WHERE status='PENDING'` |
| `chat_threads` | tabla creada aquí, usada en 04 (`status`: `ACTIVE`, `NEEDS_HUMAN`, `CLOSED`) |
| `policy_documents` | + `body text` (markdown completo, para el visor), `embedding_model`, `embedding_dim` (nulos hasta 03). `UNIQUE(doc_key) WHERE status='ACTIVE'` |
| `policy_chunks` | creada con `embedding vector(…)` y `tsv`; **vacía** hasta 03. La dimensión se fija en 03; aquí la columna se declara con `Vector(1536)` configurable vía constante `EMBEDDING_DIM` en `core/config.py` |
| `users` | revisar `models/user.py`; el challenge pide `status`: si no existe, añadir `status` (`ACTIVE`/`SUSPENDED`) y que `get_principal` rechace suspendidos |

### Enums

`RefundStatus`: `PENDING_APPROVAL, APPROVED, REJECTED, CANCELLED, PROCESSING, COMPLETED, FAILED` · `ReasonCategory` (7 del challenge) · `RefundMethod`: `ORIGINAL_PAYMENT, PLATFORM_CREDITS` · `Channel`: `CHAT, MANUAL_FORM` · `PaymentMethod`, `PurchaseType`, `PurchaseStatus` (`ACTIVE, REFUNDED`) · `ActorType`: `CUSTOMER, SUPERVISOR, AGENT, SYSTEM`.

### Decisión a registrar (desvío de BR-04)

El challenge define "solicitud activa" como `PENDING_APPROVAL | APPROVED | PROCESSING`. Una solicitud `FAILED` (pago fallido, reintentable por el supervisor) **también bloquea** nuevas solicitudes sobre la misma compra; si no, un cliente podría duplicar el reembolso mientras el supervisor reintenta. El índice parcial y el engine usan el conjunto ampliado (`ACTIVE_STATUSES`). Documentarlo en el docstring del engine.

## 6. Dominio

### 6.1 `PolicyConfig` (`domain/policy_config.py`)

```python
class PolicyConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    version: str
    refund_window_days: int = Field(gt=0)      # 30
    max_progress_pct: int = Field(ge=0, le=100) # 30
    abuse_block_12m: int = Field(gt=0)          # 5
    abuse_flag_12m: int = Field(gt=0)           # 3  (debe ser < abuse_block_12m)
    confirmation_ttl_min: int = Field(gt=0)     # 10
    sla_hours: int = Field(gt=0)                # 48
```

`PolicyService.get_active_config()` lee `policy_documents.params` del documento ACTIVE `refund-eligibility` (cache en proceso invalidada por `checksum`). El seed inserta el documento *bootstrap* (`policies/bootstrap/refund-eligibility.md`, versión `2026-10-01`). Si no hay documento ACTIVE → 503 `POLICY_UNAVAILABLE`; fuera de `ENVIRONMENT=test` el `lifespan` falla al arrancar. Spec 03 reemplaza el bootstrap por la ingesta completa sin cambiar esta interfaz.

### 6.2 `RefundPolicyEngine` (`domain/policy_engine.py`) — D1

Función pura, sin IO, sin LLM:

```python
@dataclass(frozen=True, slots=True)
class PurchaseFacts:
    id: UUID; owner_id: UUID; amount: Decimal; currency: str
    payment_method: PaymentMethod; purchase_type: PurchaseType
    purchased_at: datetime; status: PurchaseStatus
    progress_pct: int; certificate_issued_at: datetime | None

@dataclass(frozen=True, slots=True)
class CustomerHistory:
    completed_refunds_12m: int
    purchase_refund_statuses: frozenset[RefundStatus]   # solicitudes previas de ESA compra

def evaluate(purchase: PurchaseFacts | None, caller_id: UUID, history: CustomerHistory,
             config: PolicyConfig, now: datetime) -> EligibilityResult: ...
```

`EligibilityResult` es el modelo del challenge §7 (`extra="forbid"`: `eligible, reason_codes, warnings, days_since_purchase, refund_window_days, progress_pct, refund_method, amount, policy_version, checked_at`). Reglas:

| Regla | Implementación |
| --- | --- |
| BR-01 | `purchase is None` **o** `owner_id != caller_id` → `[PURCHASE_NOT_FOUND]` y **corta** (no evalúa nada más para no filtrar datos) |
| BR-02 | `now - purchased_at <= timedelta(days=window)` (inclusivo, UTC; `now` y `purchased_at` deben ser aware, si no `ValueError`) |
| BR-03 | `status == ACTIVE` si no `ALREADY_REFUNDED` |
| BR-04 | alguna solicitud previa en `ACTIVE_STATUSES` → `ACTIVE_REFUND_EXISTS` |
| BR-05 | alguna `REJECTED` → `PREVIOUSLY_REJECTED`; `CANCELLED` no bloquea |
| BR-06 | `progress_pct <= max_progress_pct` si no `CONSUMPTION_LIMIT_EXCEEDED` |
| BR-07 | `certificate_issued_at is None` si no `CERTIFICATE_ISSUED` |
| BR-08 | `SUBSCRIPTION` → `SUBSCRIPTION_NOT_ELIGIBLE`; `THIRD_PARTY` → `THIRD_PARTY_PURCHASE` |
| BR-09 | `GIFT`/`CREDITS`/`APPLE_IAP`/`GOOGLE_IAP` → `PLATFORM_CREDITS`; `CARD`/`PAYPAL` → `ORIGINAL_PAYMENT`. Si `purchase_type == GIFT` gana sobre `payment_method`. No bloquea |
| BR-10 | `completed_refunds_12m >= abuse_block_12m` → `REFUND_ABUSE_LIMIT`; `>= abuse_flag_12m` → warning `HIGH_REFUND_FREQUENCY` |

Se acumulan **todos** los códigos que fallen (orden fijo = orden de la tabla) tras BR-01. `eligible = not reason_codes`. `refund_method`/`amount` solo se rellenan si la compra es del caller. `days_since_purchase = floor((now - purchased_at).total_seconds() / 86400)`. Los warnings **no** se exponen al customer (ni al agente): el schema de respuesta del customer los omite; solo el detalle del supervisor los incluye.

### 6.3 Máquina de estados (`domain/refund_state.py`)

```text
PENDING_APPROVAL → APPROVED | REJECTED | CANCELLED
APPROVED         → PROCESSING            (worker toma el outbox)
PROCESSING       → COMPLETED | FAILED
FAILED           → PROCESSING            (solo retry del supervisor)
REJECTED, CANCELLED, COMPLETED: terminales
```

`can_transition(src, dst)`; `RefundService` solo cambia estado a través de `_transition(refund, dst, actor, event_type, payload)`, que valida, escribe el `refund_event` y emite `pg_notify`. Transición inválida → `ConflictError` con `code=INVALID_STATE` (409).

## 7. Servicios (reglas de negocio)

Convención del repo: los servicios reciben `Principal`, hacen flush y no commit. Nuevos errores en `services/errors.py` (todos mapeados a problem+json en `core/problems.py`):

| Error | HTTP | `code` |
| --- | --- | --- |
| `DomainValidationError(code, detail)` | 422 | `REASON_REQUIRED`, `CONFIRMATION_REQUIRED`, `CONFIRMATION_INVALID`, `DECISION_NOTE_REQUIRED` |
| `RefundNotEligible(reason_codes)` | 422 | `REFUND_NOT_ELIGIBLE` |
| `ConflictError(code)` | 409 | `INVALID_STATE`, `VERSION_CONFLICT`, `IDEMPOTENCY_CONFLICT` |
| `ForbiddenError(code)` | 403 | `SELF_APPROVAL_FORBIDDEN`, … |
| `PreconditionRequired` | 428 | `PRECONDITION_REQUIRED` (falta `If-Match`) |
| `RateLimited(retry_after)` | 429 | `RATE_LIMITED` + header `Retry-After` |

### 7.1 `RefundService.preview` (`POST /refunds/preview`)

1. Cargar compra con `get_scoped` (ajena → engine devuelve `PURCHASE_NOT_FOUND`).
2. Validar razón (BR-12): categoría del enum; `reason_text` 20–1000 caracteres tras `strip()` y ≥ 3 palabras distintas (case-insensitive) → si no, `DomainValidationError(REASON_REQUIRED)`. La validación es del servicio (no del schema) para devolver `REASON_REQUIRED` y no un 422 genérico.
3. `engine.evaluate(...)`; guardar `eligibility_checks(source=PREVIEW)` y **devolver** (200 con `eligible: false`, no lanzar).
4. Si es elegible: `citations` (en 01: lista fija desde el mapa `reason_code → section_path` y la sección de ventana; en 03 vendrán del retriever) y `confirmation_token`.

**Token** (`core/confirmation.py`): JWT HS256 con `sub=customer_id`, `typ="refund_confirmation"`, `hash`, `iat`, `exp = now + confirmation_ttl_min`. `hash = sha256(json canónico(sort_keys, sin espacios) de {purchase_id, reason_category, reason_text(strip), amount("49.99"), refund_method})`. Verificación: firma, `typ`, `sub == caller`, no vencido (→ `CONFIRMATION_INVALID`), `hash` recalculado igual al del cuerpo (BR-13).

### 7.2 `RefundService.create` — puerta única (D3)

```python
async def create(self, principal, data: RefundCreateData, *, idempotency_key: str,
                 proof: ConfirmationProof, channel: Channel,
                 chat_thread_id: UUID | None = None) -> CreateOutcome
# CreateOutcome = Created(refund, replayed: bool) | NotEligible(result)   (se DEVUELVE, no se lanza)
# ConfirmationProof(hash: str, confirmed_at: datetime)   ← el router lo construye desde el token; el grafo (04) desde el interrupt
```

Orden dentro de la transacción del request:

1. `require_permission(REFUND_CREATE)` (BR-11 → 403).
2. Validar razón (BR-12). Sin `proof` → `CONFIRMATION_REQUIRED` (BR-13).
3. Cargar compra scoped; si no existe → `NotEligible([PURCHASE_NOT_FOUND])` + `eligibility_checks(source=CREATE)`.
4. `SELECT … FOR UPDATE` de la fila de la compra (serializa solicitudes concurrentes sobre la misma compra).
5. Idempotencia (BR-15): buscar `(customer_id, idempotency_key)`. Existe y `request_hash` igual → `Created(replayed=True)`; distinto → `ConflictError(IDEMPOTENCY_CONFLICT)`. `request_hash = sha256` del cuerpo canónico `{purchase_id, reason_category, reason_text}`.
6. Reevaluar elegibilidad dentro de la transacción (BR-14, BR-16: el `now` es el de creación). Si no elegible → guardar `eligibility_checks(source=CREATE)` y devolver `NotEligible` (el router responde 422 **sin** excepción, para que el commit persista la auditoría; D6).
7. Recalcular el hash de confirmación con el monto y método **que acaba de calcular el engine**; si ≠ `proof.hash` → `CONFIRMATION_INVALID`.
8. `INSERT refund_requests` dentro de `begin_nested()` (savepoint): `status=PENDING_APPROVAL`, `amount/currency` copiados de la compra, `refund_method` del engine, `eligibility_snapshot`, `policy_version`, `policy_snapshot`, `confirmed_at/confirmation_hash`, `flags` (warnings del engine), `idempotency_key`, `request_hash`, `channel`. `IntegrityError` por `uq_customer_idempotency` (carrera) → re-leer y aplicar el paso 5; por el índice parcial de compra activa → `NotEligible([ACTIVE_REFUND_EXISTS])`.
9. Evento `REFUND_CREATED` (`actor_type = AGENT` si `principal.acting_via == "agent"`, si no `CUSTOMER`; `acting_for`, `trace_id`) + evento de notificación en outbox (`refund.notify`).

`days_since_purchase` y la ventana se miden hasta `created_at` (BR-16). El rate limit (5/hora por customer, solo canal formulario; el chat usa el suyo) se aplica como dependencia del router.

### 7.3 `cancel` (`POST /refunds/{id}/cancel`)

`refund:cancel_own`; cargar scoped (ajena → 404); `If-Match` obligatorio (428 si falta); solo desde `PENDING_APPROVAL` (409 `INVALID_STATE`); versión distinta → 409 `VERSION_CONFLICT`; evento `REFUND_CANCELLED` (BR-24). La confirmación humana del cancel en chat es del grafo (04); en la UI web se confirma con un modal.

### 7.4 `approve` / `reject` (`SupervisorService`/`RefundService`)

Orden de validaciones (cada una con su código): permiso `refund:decide` (403) → cargar con `refund:read_any` (404) → `customer_id == supervisor_id` → 403 `SELF_APPROVAL_FORBIDDEN` (BR-20) → estado `PENDING_APPROVAL` si no 409 `INVALID_STATE` (BR-17, inmutabilidad) → `If-Match == version` si no 409 `VERSION_CONFLICT` (BR-18) → reject: `decision_note` ≥ 10 caracteres tras `strip()` si no 422 `DECISION_NOTE_REQUIRED` (BR-19).

Escritura: `status`, `decided_by/at`, `decision_note`; el `UPDATE` incluye `WHERE version = :v` (mapper `version_id_col`); `StaleDataError` → 409 `VERSION_CONFLICT` (dos supervisores simultáneos: uno 200, otro 409). Al aprobar, **en la misma transacción**: `outbox_events(topic="refund.approved", payload={refund_id})` y evento `REFUND_APPROVED`. Aprobar el día 35 es válido (BR-16). Al rechazar: evento `REFUND_REJECTED` y outbox `refund.notify`.

### 7.5 Payouts (`PayoutService` + worker) — D7, BR-22/23

`python -m app.workers.payouts [--once]` (loop asyncio; en Windows con `SelectorEventLoop`):

1. Poll de `outbox_events WHERE status='PENDING' AND available_at <= now() ORDER BY available_at FOR UPDATE SKIP LOCKED LIMIT n` (también despierta con `LISTEN outbox`).
2. `refund.approved` / `refund.payout_retry`: transición `APPROVED|FAILED → PROCESSING`, upsert de `refund_payouts(status=PENDING)`; fuera de la transacción de BD se llama a `PaymentGateway.pay(method, amount, idempotency_key=refund_id)` (`SimulatedGateway`: éxito por defecto; falla determinista si `refund.amount` termina en `.13` o con variable `GATEWAY_FAIL_RATE` para demos).
3. Éxito → **una sola transacción**: payout `COMPLETED` + `provider_ref`, refund `COMPLETED`, `purchases.status=REFUNDED` y `access_revoked_at=now`, si `PLATFORM_CREDITS` insertar `credit_ledger(delta=+amount, refund_id)` (único por refund → idempotente), eventos, outbox `DONE`.
4. Fallo → `attempts += 1`, `last_error`, `available_at = now + 2^attempts * base` (backoff; base configurable, 1 s en tests). Tras 3 intentos: payout `FAILED`, refund `FAILED`, outbox `DEAD` (dead-letter, consultable) y evento `PAYOUT_FAILED`.
5. `POST /supervisor/refunds/{id}/retry-payout` (`payout:retry`): solo `FAILED` (409 si no) → crea `outbox_events(refund.payout_retry)`, resetea `attempts`, evento `PAYOUT_RETRY_REQUESTED`; **202**.
6. Reejecutar el mismo outbox es seguro (idempotencia por `refund_id` en gateway, `credit_ledger` y `refund_payouts`).

### 7.6 Supervisor

- **Cola** `GET /supervisor/refunds`: filtros `status`, `sla_breached`, `high_risk` (`'HIGH_REFUND_FREQUENCY' = ANY(flags)`), `channel`, `reason_category`, `created_from/to`, `q` (substring en nombre/email del customer), `order=asc|desc` por `created_at`, `limit` (≤100) + `cursor`. `sla_breached` es **calculado** (`status='PENDING_APPROVAL' AND now() - created_at > sla_hours`), no una columna: no necesita job y no puede quedar desactualizado (BR-21; nunca hay auto-aprobación).
- **Detalle** `GET /supervisor/refunds/{id}`: razón, `eligibility_snapshot` (incluye warnings), compra, progreso, historial del customer (conteos 12 m, solicitudes previas con estado), `flags`, citas guardadas en el snapshot, notas. Datos personales mínimos: nombre y `mask_email` (`l***@gmail.com`); el supervisor **no** tiene endpoint de compras ni de usuarios fuera de esta vista.
- **Notas** `GET/POST /supervisor/refunds/{id}/notes`: `body` 1–2000; evento `NOTE_ADDED` **sin** el texto en el payload (el timeline público no debe filtrar su contenido).
- **Timeline** `GET /supervisor/refunds/{id}/events`: completo. En `/refunds/{id}/events` (customer): se excluyen eventos `NOTE_ADDED` y se **proyectan** los payloads con una allowlist por tipo de evento (sin ids de supervisor, sin `decision_note` interna: el cliente sí ve `decision_note` de un rechazo porque es la justificación pública; las notas internas jamás).
- **Stats** `GET /supervisor/stats`: `pending`, `sla_breached`, `avg_decision_minutes`, `approval_rate`, `top_reasons[5]`, ventana por `?days=30` (default).
- **Stream** `GET /supervisor/refunds/stream` (SSE): conexión dedicada psycopg con `LISTEN refund_events`; `refund_events` hace `pg_notify('refund_events', json{refund_id,type,status})` **dentro** de la transacción (se entrega solo en commit). Heartbeat cada 15 s; cierra al desconectar el cliente. Autenticación por header `Authorization` (el cliente web usa `fetch-event-source`).

## 8. API (routers)

Base `/api/v1`. Todos los endpoints del challenge §5 con los códigos de respuesta indicados, más:

- `GET /me` → `{id, name, email, role, credit_balance}` (saldo = `SUM(credit_ledger.delta)`). (`/users/{id}` existente se conserva.)
- Paginación: `?limit=&cursor=` → `{items: [...], next_cursor: str | null}` (keyset sobre `(sort_value, id)`; cursor opaco, inválido → 422).
- Cabeceras: `Idempotency-Key` (1–128 caracteres, obligatorio en `POST /refunds`, 422 si falta), `If-Match` (entero, acepta `3` o `"3"`; 428 si falta, 422 si no es entero), `X-Request-ID` (entrada/salida; si falta se genera), `Idempotent-Replayed: true` en repeticiones.
- `POST /refunds` → 201 con `RefundRead`; `NotEligible` → 422 problem+json `{code:"REFUND_NOT_ELIGIBLE", reason_codes, detail, trace_id}`.
- `GET /policies`, `GET /policies/{doc_key}` → solo ACTIVE: `{doc_key, title, version, effective_date, body}`; sin LLM.
- `GET /healthz` (público), `GET /readyz` (DB; vector store: `SELECT 1 FROM policy_chunks LIMIT 1` no falla y la extensión `vector` existe).
- Schemas Pydantic con `extra="forbid"` en entrada; los de salida del customer **no** incluyen `decided_by`, `flags`, `warnings`, notas ni datos de otros usuarios. `response_model` siempre explícito.
- CORS: `CORS_ORIGINS` (lista) en `Settings`; en dev el frontend usa el proxy de Vite (`/api → http://localhost:8000`).
- Rate limit: dependencia `rate_limit("create_refund", 5, 3600)` por `principal.user_id` → 429 `RATE_LIMITED` + `Retry-After`. Limitación documentada: es por proceso.

## 9. Seeds (`uv run seed`)

Extender `cli/seed.py` (idempotente; ids determinísticos con `sid()`; `--reset` borra también los datos de dominio de los usuarios seed). Crear cursos, compras y progreso para `ann`/`bob` cubriendo la tabla de "Datos semilla" del challenge (13 casos). Notas:

- Las fechas se siembran **relativas a `now`**. El caso "día 30 menos 1 segundo" se siembra como `30 d − 1 h` (un segundo se vencería durante el propio seed); el borde exacto se prueba en unit tests con `Clock` inyectado.
- "Frecuencia alta" y "límite de abuso" se siembran con 3 y 5 solicitudes `COMPLETED` históricas de un tercer customer (`carol`, `dave`) para no contaminar a `ann`.
- Incluir un `PENDING_APPROVAL` con SLA vencido (`created_at = now − 50 h`), uno reciente, uno `FAILED` y uno `COMPLETED` para que el dashboard tenga datos en todos los estados.
- Insertar el documento *bootstrap* de política ACTIVE.
- La salida del seed imprime ejemplos `curl` de las nuevas rutas.

## 10. Frontend (`learnly-frontend`)

### 10.1 Setup

- Dependencias: `tailwindcss`, `@tailwindcss/vite`, `react-router`, `@tanstack/react-query`, `@microsoft/fetch-event-source`; dev: `vitest`, `@testing-library/react`, `@testing-library/user-event`, `msw`, `openapi-typescript`.
- **Tailwind v4**: `@import "tailwindcss";` en `src/index.css`, plugin en `vite.config.ts`; borrar `App.css` y los assets de plantilla. Tokens en `@theme` (color de marca, `--font-sans`); modo claro/oscuro con `dark:` y `prefers-color-scheme`. Sin CSS a mano salvo tokens.
- `vite.config.ts`: `server.proxy['/api'] → http://localhost:8000`. `VITE_API_BASE_URL` opcional (default `/api/v1`).
- Script `npm run gen:api`: `openapi-typescript http://localhost:8000/openapi.json -o src/api/schema.d.ts` (tipos generados del backend, sin duplicar contratos a mano).

### 10.2 Estructura

```text
src/
  api/        client.ts (fetch + Bearer + problem+json → ApiError{status,code,reasonCodes,traceId}),
              auth.ts, purchases.ts, refunds.ts, supervisor.ts, policies.ts, sse.ts (fetch-event-source)
  auth/       AuthProvider.tsx (token en sessionStorage, /me al cargar), RequireRole.tsx, useAuth.ts
  components/ Button, Badge (StatusBadge, SlaBadge, RiskBadge), Modal, Table, EmptyState, ErrorBanner,
              Spinner, Toast, ConfirmationCard (compartida con la tarjeta HITL del chat en 04), Timeline,
              Money, DateTime
  features/
    customer/    PurchasesPage, PurchaseDetailPage, RefundFormPage, MyRefundsPage, RefundDetailPage
    supervisor/  QueuePage, RefundReviewPage, StatsStrip, PoliciesPage, modales Approve/Reject/Retry
    auth/        LoginPage
  routes.tsx  (customer: /purchases, /purchases/:id, /refunds/new?purchase=, /refunds, /refunds/:id;
              supervisor: /supervisor, /supervisor/refunds/:id, /supervisor/policies; /login; 403/404)
```

Navegación por rol: tras el login el customer va a `/purchases` y el supervisor a `/supervisor`; `RequireRole` redirige si el rol no coincide. El supervisor **no** ve ninguna ruta de chat ni el formulario de solicitud.

### 10.3 Comportamiento por pantalla

**Customer**
- *Mis compras*: lista con filtros (`status`, "dentro de ventana"), badge de días restantes, carga incremental con cursor, botón "Solicitar reembolso" deshabilitado visualmente si ya hay solicitud activa (el servidor sigue siendo la autoridad).
- *Formulario manual*: elegir compra (o venir con `?purchase=`), `reason_category` (select), `reason_text` con contador 20–1000. "Ver vista previa" → `POST /refunds/preview`: muestra elegibilidad, monto, método, citas, fecha límite. Si `eligible: false` se muestran los `reason_codes` traducidos a texto humano (diccionario `reasonCodeMessages`) y no se permite continuar. Si es elegible: botón "Confirmar solicitud" abre **modal de confirmación** (mismo contenido que la tarjeta del chat: `ConfirmationCard`); al aceptar → `POST /refunds` con `Idempotency-Key = crypto.randomUUID()` generado **una vez por intento de envío** y reutilizado en reintentos del mismo modal (doble clic / reintento por red no duplica). Token vencido (`CONFIRMATION_INVALID`) → se vuelve a pedir preview automáticamente con aviso. 422 `REFUND_NOT_ELIGIBLE` en la creación → mostrar motivos. 429 → mensaje con `Retry-After`.
- *Mis reembolsos / detalle*: estado, `decision_note` si fue rechazado, timeline público, botón "Cancelar" (solo `PENDING_APPROVAL`, modal de confirmación, envía `If-Match: version`; 409 → refrescar y avisar).

**Supervisor**
- *Cola*: tabla con filtros (estado, SLA vencido, alto riesgo, canal, razón, fechas, búsqueda por cliente), orden por antigüedad, cursor, fila con badges `SLA` (rojo) y `Riesgo`. Barra de `stats` arriba. **Actualización en vivo**: suscripción SSE; al recibir un evento se invalida la query de la cola (TanStack Query) y se muestra un toast discreto; si el SSE cae, reintento con backoff y banner "Reconectando…".
- *Detalle/revisión*: razón, compra y progreso, `eligibility_snapshot` legible (reglas evaluadas), historial del cliente, flags, citas, notas internas (crear/listar), timeline completo. Acciones: **Aprobar** (nota opcional) y **Rechazar** (modal, nota obligatoria ≥ 10 con validación en cliente y servidor). Cada acción envía `If-Match`. **409 `VERSION_CONFLICT`/`INVALID_STATE`** → banner visible "Otro supervisor ya decidió esta solicitud" y recarga del detalle. Si `customer_id == yo` (el payload lo permite) los botones se ocultan y se explica. `FAILED` → botón "Reintentar pago".
- *Políticas*: visor de `GET /policies` (markdown renderizado de forma segura: usar `react-markdown` **sin** HTML crudo).

### 10.4 Requisitos de calidad

- Estados de carga, vacío y error en toda lista; errores de red distintos de errores de dominio; ningún `any`; `npm run lint` y `npm run build` (`tsc -b`) en verde.
- Accesibilidad básica: modales con foco atrapado y `Esc`, botones con texto accesible, contraste AA en badges, formularios con `label`.
- Tests (Vitest + Testing Library + MSW): login y guard por rol; preview inelegible bloquea el envío; reutilización de `Idempotency-Key` en reintento; 409 del supervisor muestra el banner; rechazo sin nota válida no envía.

## 11. Plan de tareas (en orden)

- [ ] **T1** Infra transversal: `clock`, `problems` (handlers para los errores existentes + nuevos, `RequestValidationError`), `request_id`, `pagination`, `rate_limit`; actualizar tests existentes que dependan del formato de error (siguen teniendo `detail`).
- [ ] **T2** Dominio puro: `reasons`, `policy_config`, `policy_engine`, `refund_state`, `masking` + unit tests (Hypothesis en bordes de ventana).
- [ ] **T3** Modelos + migración `0002` + trigger append-only (`DDL` en el modelo) + `alembic check` limpio; ampliar `conftest.py` con fixtures de dominio (`make_purchase`, `make_refund`, `FakeClock`, política bootstrap).
- [ ] **T4** Permisos nuevos + `ensure_rbac` + repositorios.
- [ ] **T5** `PolicyService` + bootstrap + `EligibilityService`; endpoints `/me`, `/purchases*`, `/purchases/{id}/refund-eligibility`, `/policies*`, `/healthz`, `/readyz`.
- [ ] **T6** `RefundService` (preview/create/cancel) + `confirmation` + endpoints customer de reembolsos + eventos.
- [ ] **T7** `SupervisorService` + approve/reject/notes/events/stats + endpoints.
- [ ] **T8** Outbox + `PayoutService` + worker + `SimulatedGateway` + `retry-payout`.
- [ ] **T9** SSE del supervisor (LISTEN/NOTIFY).
- [ ] **T10** Seeds de dominio + README/CLAUDE.md actualizados (comandos, rutas, worker).
- [ ] **T11** Frontend: setup Tailwind + cliente API + auth + vistas customer.
- [ ] **T12** Frontend: dashboard del supervisor (cola, detalle, modales, SSE) + visor de políticas + tests de UI.

## 12. Criterios de aceptación y tests

| Área | Criterio (verificable) | Test |
| --- | --- | --- |
| Engine | Bordes `30 d − 1 s` y `30 d` elegibles; `30 d + 1 s` → `OUTSIDE_REFUND_WINDOW`; los 13 casos semilla producen el resultado esperado; ajena → solo `[PURCHASE_NOT_FOUND]` | `tests/unit/test_policy_engine.py` (+ Hypothesis) |
| Estados | Toda transición fuera de la tabla falla; terminales no avanzan | `tests/unit/test_refund_state.py` |
| Idempotencia | 20 `POST /refunds` concurrentes con la misma clave → 1 fila y 20 respuestas con el mismo `id`; misma clave con cuerpo distinto → 409 `IDEMPOTENCY_CONFLICT` | `tests/test_refund_concurrency.py` (`asyncio.gather`, Postgres real) |
| Concurrencia supervisor | Dos aprobaciones simultáneas → un 200 y un 409 `VERSION_CONFLICT` | idem |
| Compra única | Dos solicitudes sobre la misma compra → la segunda `ACTIVE_REFUND_EXISTS` (también bajo carrera); `FAILED` también bloquea | idem |
| Inelegible | `POST /refunds` fuera de plazo → 422 `REFUND_NOT_ELIGIBLE` con `reason_codes`, **no** crea solicitud y **sí** deja `eligibility_checks` (verifica que el commit ocurrió) | `tests/test_refunds_api.py` |
| Confirmación | Sin token → `CONFIRMATION_REQUIRED`; token de otra compra/razón/monto o vencido → `CONFIRMATION_INVALID`; token de otro usuario → inválido | idem |
| Razón | `reason_text` corto, repetitivo o categoría inválida → `REASON_REQUIRED` | idem |
| BOLA/IDOR | Customer A recibe 404 en compras, solicitudes, eventos y cancelación de B; cualquier `/supervisor/*` → 403 para customer; supervisor → 403 en `POST /refunds` | `tests/test_authz_refunds.py` |
| Supervisor | Auto-aprobación 403; rechazo con nota < 10 → 422; decidir dos veces → 409 `INVALID_STATE`; falta `If-Match` → 428 | `tests/test_supervisor_api.py` |
| Privacidad | Respuestas del customer no contienen `decided_by`, `flags`, `warnings` ni notas; el supervisor ve email enmascarado; payloads del timeline público pasan la allowlist | idem |
| Auditoría | `UPDATE`/`DELETE` sobre `refund_events` y `credit_ledger` falla a nivel de BD | `tests/test_audit_triggers.py` |
| Payout | Éxito → `COMPLETED`, compra `REFUNDED`, acceso revocado, `credit_ledger` solo para `PLATFORM_CREDITS`, todo atómico; 3 fallos → `FAILED` + outbox `DEAD`; `retry-payout` → 202 y vuelve a `PROCESSING`; reprocesar el mismo outbox no duplica | `tests/test_payout_worker.py` (gateway falso, backoff 0) |
| SLA | Solicitud de 50 h aparece con `sla_breached=true` y el filtro la devuelve; nunca cambia de estado sola | `tests/test_supervisor_listings.py` (ampliar) |
| SSE | Un `approve` emite un evento al stream del supervisor; un customer no puede abrirlo (403) | `tests/test_supervisor_stream.py` |
| Rate limit | La 6.ª creación en una hora → 429 con `Retry-After` | `tests/test_rate_limit.py` |
| Calidad | `uv run ruff check`, `ruff format --check`, `mypy` (strict) verdes; cobertura de `domain/` ≥ 85 %; `alembic upgrade head` y `alembic check` limpios; frontend `lint`, `build` y tests verdes | CI local |
| Demo | `uv run seed` → login como `ann`, previsualizar, solicitar, entrar como supervisor, ver la solicitud en vivo, aprobar, ver `COMPLETED` tras correr el worker | manual (guion en README) |
