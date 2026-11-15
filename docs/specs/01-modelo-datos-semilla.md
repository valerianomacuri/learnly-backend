# 01 · Modelo de datos, semilla y lectura de compras

**Depende de:** 00 · **Refs:** challenge §3, §5 (compras), §9 (datos semilla, auditoría)

## Objetivo
Crear todo el esquema transaccional del dominio, la semilla de los 13 casos borde y los endpoints de lectura de compras con aislamiento por dueño. Demostrable con `uv run seed` + curl.

## Alcance
- Dentro: modelos, migración `0002`, trigger append-only, semilla, `GET /me`, `GET /purchases`, `GET /purchases/{id}`, alias `/healthz`.
- Fuera: `policy_chunks` (spec 07), lógica de elegibilidad (02), escritura de solicitudes (03). Las tablas de pago/outbox/créditos se crean ya pero no se usan hasta 05.

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

## Tareas
- [ ] Enums de dominio + modelos + `models/__init__.py`.
- [ ] Migración `0002` (autogenerate, revisar a mano; trigger y índice parcial a mano). `alembic upgrade head` desde BD vacía y `alembic check`.
- [ ] Test de BD: trigger rechaza UPDATE y DELETE en `refund_events`; índice parcial rechaza dos solicitudes activas para la misma compra pero permite una nueva tras `CANCELLED`.
- [ ] Repos/servicios/rutas de compras; tests de cursor, filtros y **BOLA** (customer A pide compra de B → 404, idéntico al inexistente).
- [ ] Fixtures de dominio en `tests/conftest.py` (`make_course`, `make_purchase(days_ago=, progress=, ...)`).
- [ ] Semilla y `tests/test_seed.py` ampliado.

## Criterios de aceptación
- Intentar modificar un `refund_events` falla **a nivel de base de datos**.
- 404 idéntico (mismo cuerpo) para compra inexistente y ajena.
- `uv run seed` es idempotente y `--reset` recrea los 13 casos con fechas relativas.
- Un supervisor recibe 403 en `GET /purchases` (no es su recurso).

## Verificación
```bash
docker compose up -d db && uv run alembic upgrade head && uv run alembic check
uv run seed
uv run pytest -q && make lint typecheck
# humo (token impreso por el seed)
curl -H "Authorization: Bearer $CUST1" localhost:8000/api/v1/purchases
curl -H "Authorization: Bearer $CUST1" localhost:8000/api/v1/purchases/<id-de-otro-customer>   # 404
```
