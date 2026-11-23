# Implementation Plan: 01 · Modelo de datos, semilla y lectura de compras

**Feature**: `001-modelo-datos-semilla` · **Estado**: ✅ Implementada · **Migrada de**: `docs/specs/01-modelo-datos-semilla.md`

Spec: [spec.md](spec.md) · Tareas: [tasks.md](tasks.md) · Constitution: [`.specify/memory/constitution.md`](../../.specify/memory/constitution.md)

## Cambios

### Modelos (`src/app/models/`, usar `IdMixin`, `TimestampMixin`, `str_enum`)
Un archivo por agregado: `course.py`, `purchase.py` (`Purchase`, `CourseProgress`), `refund.py` (`RefundRequest`, `RefundEvent`, `RefundInternalNote`, `RefundPayout`, `CreditLedgerEntry`), `eligibility.py` (`EligibilityCheck`), `outbox.py`, `chat.py` (`ChatThread`), `policy.py` (`PolicyDocument`). Campos exactamente como el challenge §3. Puntos finos:
- `refund_requests`: índice único **parcial** sobre `purchase_id` donde `status IN ('PENDING_APPROVAL','APPROVED','PROCESSING')`; `UNIQUE(customer_id, idempotency_key)`; `version int` con `version_id_col` de SQLAlchemy **no** (el bloqueo es explícito con `If-Match`); CHECK `char_length(reason_text) BETWEEN 20 AND 1000`.
- `RefundStatus`, `ReasonCategory`, `RefundMethod`, `Channel`, `PaymentMethod`, `PurchaseType`, `PurchaseStatus`, `ActorType`: enums en `app/domain/enums.py` (puros), columnas con `str_enum`.
- `refund_events`: **trigger** `BEFORE UPDATE OR DELETE` que lanza excepción. Definirlo en la migración y en `Base.metadata` vía `DDL` para que los tests (`create_all`) también lo tengan.
- `eligibility_checks.reason_codes`: `ARRAY(String)`; `details`: JSONB.
- `credit_ledger`: sin UPDATE/DELETE por convención (saldo = SUM(delta)); `outbox_events.status`: `PENDING|DONE|DEAD`.
- `refund_internal_notes` y `refund_events.payload` nunca se serializan en schemas de customer.

### Repositorios y servicios
- `PurchaseRepository` (subclase de `BaseRepository`), usar `scope.restrict` con `Perm.PURCHASE_READ_FOR_REVIEW`? **No**: en este spec la lectura de compras es solo del dueño; el supervisor ve compras vía el detalle de solicitud (spec 04). Filtrar por `customer_id == principal.user_id` y devolver 404 si es ajena.
- `PurchaseService.list(principal, status, within_window, cursor, limit)` con paginación por cursor (`purchased_at, id`), `get(principal, id)`; calcula `refund_deadline = purchased_at + 30 d` **provisionalmente leyendo una constante** que el spec 02 reemplaza por `PolicyConfig` (marcar con `# TODO(spec-02)`).
- `RefundEventService.append(refund_id, type, actor, payload)`: única vía de escritura de eventos (la usan 03–05, 10).
- Esquemas en `schemas/purchase.py`, `schemas/me.py` (`MeRead`: perfil, rol, saldo de créditos = SUM del ledger).

### Endpoints (`/api/v1`)
| Ruta | Permiso | Respuestas |
| --- | --- | --- |
| `GET /me` | autenticado | 200 |
| `GET /purchases?status&within_window&cursor&limit` | `purchase:read_own` | 200 |
| `GET /purchases/{id}` (con progreso y `refund_deadline`) | `purchase:read_own` | 200, 404 (inexistente **o ajena**) |
| `GET /healthz` | público | 200 (`/health` se mantiene) |

### Semilla (`cli/seed.py`, idempotente, `--reset`)
Añadir cursos y usuarios nuevos (≥2 customers, 2 supervisors) y los 13 casos del challenge §9 como compras de `cust1`, con `purchased_at` **relativo a `now()`** (ej. "día 30 menos 1 s" se recalcula en cada seed). Los casos que requieren historial (doble solicitud, frecuencia alta = 3 reembolsos, abuso = 5) usan un customer propio cada uno para no contaminarse. Imprimir una tabla `caso → purchase_id → resultado esperado` (la reutiliza el spec 02 como fixture de tests).

## Decisiones tomadas al implementar

- **`cust1` = `ann`**: se conservan los customers `ann` y `bob` del seed existente (no se renombran para no dejar filas huérfanas). `ann` es dueña de los casos; `bob` es el "otro customer" (compra ajena). Los tres customers con historial son `double-request`, `high-frequency` y `abuse-limit`. En la verificación el token es `$ANN`.
- **Borde exacto = día 30 menos 10 min** (no 1 s): con 1 s la compra caducaría antes de poder lanzar un curl. El segundo exacto lo cubre el engine con reloj inyectado (spec 02). Constante `EDGE_MARGIN` en el seed.
- **`refund_events.created_at` usa `clock_timestamp()`** (no `now()`): varios eventos de una misma transacción deben conservar su orden en el timeline.
- **`seed --reset` desactiva el trigger solo dentro de su transacción** para borrar los eventos de la semilla (el DDL es transaccional: si algo falla vuelve a quedar activo). Es lo único que borra `refund_events`.
- **`eligibility_checks.purchase_id` sin FK**: para poder registrar `PURCHASE_NOT_FOUND` (compra inexistente o ajena).
- **Contrato de dinero**: JSON numérico (`49.99`) como en el challenge; en Python se mantiene `Decimal`.
- `MeRead` devuelve `roles` (lista), no un único `role`: un usuario puede tener varios.
- Error nuevo `InvalidInputError` → 422 (cursor inválido).
- La migración copia el SQL del trigger en vez de importarlo de los modelos.

## Verificación

```bash
docker compose up -d db && uv run alembic upgrade head && uv run alembic check
uv run seed
uv run pytest -q && make lint typecheck
# humo (token impreso por el seed)
curl -H "Authorization: Bearer $ANN" localhost:8000/api/v1/purchases
curl -H "Authorization: Bearer $ANN" localhost:8000/api/v1/purchases/<id-de-otro-customer>   # 404 (caso 13 de la tabla del seed)
```
