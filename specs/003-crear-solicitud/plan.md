# Implementation Plan: 03 · Crear solicitud (canal formulario)

**Feature**: `003-crear-solicitud` · **Estado**: ✅ Implementada · **Migrada de**: `docs/specs/03-crear-solicitud.md`

Spec: [spec.md](spec.md) · Tareas: [tasks.md](tasks.md) · Constitution: [`.specify/memory/constitution.md`](../../.specify/memory/constitution.md)

## Cambios

### Dominio (`domain/`)
- `state_machine.py`: transiciones permitidas `PENDING_APPROVAL → {APPROVED, REJECTED, CANCELLED}`, `APPROVED → PROCESSING`, `PROCESSING → {COMPLETED, FAILED}`, `FAILED → PROCESSING`; `transition(current, target, actor_role)` lanza `InvalidTransition`. Tests exhaustivos de la matriz 7×7.
- `confirmation.py`: `payload_hash({purchase_id, reason_category, reason_text, amount, refund_method})` (JSON canónico → sha256) y `ConfirmationToken` firmado HMAC (`CONFIRMATION_SECRET`) con `customer_id`, `hash`, `exp` (TTL de `PolicyConfig.confirmation_ttl_min`). `verify(token, payload, customer_id, now)` → `CONFIRMATION_REQUIRED` (ausente) / `CONFIRMATION_INVALID` (expirado, firma, hash, otro customer).
- `reason.py`: validación BR-12 (enum, 20–1000 chars, ≥3 palabras distintas) → `REASON_REQUIRED`.

### Servicio (`services/refund_service.py`)
```python
async def preview(principal, req) -> PreviewResult        # no escribe refund; sí eligibility_checks
async def create_request(principal, req, *, idempotency_key, confirmation_proof, channel, chat_thread_id=None) -> RefundRead
async def cancel(principal, refund_id, *, if_match: int, ...) -> RefundRead
```
Orden en `create_request` (una transacción): rol/permiso `refund:create` (BR-11) → idempotencia → razón (BR-12) → **reevaluar elegibilidad con lock** (`SELECT … FOR UPDATE` sobre la compra) (BR-14) → verificar token contra el payload recomputado (BR-13) → insertar con `eligibility_snapshot`, `policy_version`, `confirmed_at`, `confirmation_hash`, `refund_method` fijado por el engine → `refund_event(CREATED, actor CUSTOMER|AGENT)`. Medida de ventana hasta `created_at` (BR-16).
- **Idempotencia (BR-15):** tabla/columna `idempotency_key` única por customer + `request_hash` del cuerpo. Misma clave+mismo cuerpo → respuesta original (misma fila, 201→200 o 201 repetido; decidir y documentar); distinto cuerpo → 409 `IDEMPOTENCY_CONFLICT`; carrera simultánea resuelta por la restricción única (capturar `IntegrityError` y releer).
- **Inelegible (D6, BR-14):** no se crea; responde 422 con `reason_codes`. El registro en `eligibility_checks` **debe persistir aunque la petición falle** (el `get_session` hace rollback ante excepción): escribirlo con una sesión/transacción independiente o devolver un resultado y que la ruta lo convierta en 422 **después** del commit. Documentar la elección en el código y en `CLAUDE.md`.
- **Cancelar (BR-24):** solo dueño, solo `PENDING_APPROVAL`, `If-Match: <version>` (409 `VERSION_CONFLICT` si no coincide, 409 si ya no está pendiente); `version += 1`; evento `CANCELLED`.
- Cada transición escribe `refund_events` vía `RefundEventService` (spec 01). Sin notificaciones reales: `outbox_events(topic="notification")` opcional, se difiere al 05.

### API
| Ruta | Permiso | Respuestas |
| --- | --- | --- |
| `POST /refunds/preview` | `refund:simulate` | 200 (`eligible` true/false + `confirmation_token` solo si elegible), 422 |
| `POST /refunds` (headers `Idempotency-Key`, body con `confirmation_token`) | `refund:create` | 201, 403, 409, 422 |
| `GET /refunds?status&cursor` | `refund:read_own` | 200 |
| `GET /refunds/{id}` | `refund:read_own` | 200, 404 (ajena = inexistente); **sin notas internas** ni `eligibility_snapshot.warnings` |
| `GET /refunds/{id}/events` | `refund:read_own` | timeline público (filtra eventos internos) |
| `POST /refunds/{id}/cancel` (`If-Match`) | `refund:cancel_own` | 200, 404, 409 |

- Errores `application/problem+json` con `type`, `code`, `reason_codes`, `detail`, `trace_id` (middleware que genera/propaga `X-Request-ID` como `trace_id`). Registrar `RefundNotEligible`, `IdempotencyConflict`, `VersionConflict`, `ConfirmationRequired/Invalid`, `ReasonRequired`, `InvalidTransition` en `main.py` y `services/errors.py`. Los handlers existentes (404/409/403) pasan a este formato **solo para rutas nuevas** o se migran todos (decidir y anotar).
- `schemas/refund.py` con `extra="forbid"`; el contrato de ejemplo del challenge §5 es el golden test.

## Verificación

```bash
uv run pytest tests/test_refunds_*.py tests/unit/test_state_machine.py tests/unit/test_confirmation.py -q
make check
# humo
curl -X POST -H "Authorization: Bearer $CUST1" -H 'Content-Type: application/json' localhost:8000/api/v1/refunds/preview -d '{"purchase_id":"…","reason_category":"NOT_AS_DESCRIBED","reason_text":"El curso promete proyectos reales y solo hay teoría básica."}'
curl -X POST … /api/v1/refunds -H "Idempotency-Key: $(uuidgen)" -d '{…,"confirmation_token":"…"}'
```
