# 04 · API del supervisor

**Depende de:** 03 · **Refs:** challenge §2 (permisos supervisor), §4 (BR-17…BR-21), §5 (dashboard)

## Objetivo
El supervisor revisa la cola y decide (aprobar/rechazar) con control de concurrencia optimista. Aprobar deja la solicitud en `APPROVED`; el pago se ejecuta en el spec 05.

## Alcance
- Dentro: cola con filtros, detalle, approve/reject, notas internas, timeline completo, stats, SLA, flag de riesgo, enmascarado de PII.
- Fuera: outbox/pago (05 — aquí `approve` solo cambia estado y escribe el evento; el spec 05 añade la fila de outbox en la **misma** transacción), SSE (06).

## Cambios

### Servicio (`services/supervisor_refund_service.py`)
- `list_queue(principal, filters)`: filtros `status`, `sla_breached`, `high_risk`, `channel`, `reason_category`, `created_from/to`, `customer` (búsqueda por nombre/email), orden (`created_at`, `amount`, `sla`) y cursor. `sla_breached` = `PENDING_APPROVAL` y `now - created_at > sla_hours` (calculado en consulta, no almacenado; nunca auto-aprueba, BR-21). `high_risk` = snapshot con warning `HIGH_REFUND_FREQUENCY`.
- `get_detail(principal, id)`: razón, `eligibility_snapshot`, compra, progreso, historial del customer (conteos y últimas solicitudes), flags, citas, notas. Datos personales **mínimos**: nombre y **email enmascarado** (`l***@gmail.com`, helper en `core/privacy.py`, reutilizado en 12).
- `approve(principal, id, if_match, note?)` / `reject(principal, id, if_match, note)`: orden → permiso `refund:decide` → cargar con lock → `customer_id != supervisor_id` (403 `SELF_APPROVAL_FORBIDDEN`, BR-20) → estado `PENDING_APPROVAL` (409 si no) → `version == if_match` (409 `VERSION_CONFLICT`, BR-18) → rechazo con nota ≥10 (422, BR-19) → transición por la máquina de estados → `decided_by/at/note`, `version += 1` → `refund_event(APPROVED|REJECTED, actor SUPERVISOR)`. Decisión inmutable (BR-17/regla 3).
- Notas internas: `add_note` / `list_notes` (`note:write`/`note:read`); evento `NOTE_ADDED` **interno** (no visible en el timeline del customer; marcar `visibility` en `refund_events` o filtrar por tipo — decidir en la implementación y probarlo).
- `stats`: pendientes, SLA vencidos, tiempo medio de decisión, tasa de aprobación, top razones (consultas agregadas; sin cache aún).

### Rutas (`api/routes/supervisor.py`, todas con `requires(...)`, prefijo `/supervisor`)
`GET /refunds`, `GET /refunds/{id}`, `POST /refunds/{id}/approve`, `POST /refunds/{id}/reject`, `GET|POST /refunds/{id}/notes`, `GET /refunds/{id}/events`, `GET /stats`. `If-Match` obligatorio en approve/reject (400/428 si falta). El supervisor **no** puede crear solicitudes ni usar `/chat` (ya garantizado por permisos del 00).

## Tareas
- [x] Tests primero: matriz de permisos (customer → 403 en todas las rutas `/supervisor/*`), self-approval, rechazo sin nota/nota corta, re-decisión → 409, `If-Match` viejo → 409.
- [x] **Concurrencia:** dos supervisores aprueban a la vez (sesiones separadas, `asyncio.gather`) → un 200 y un 409.
- [x] Servicio, rutas, schemas (`SupervisorRefundRead` ≠ `RefundRead`), helper de enmascarado.
- [x] Test: las notas nunca aparecen en `GET /refunds/{id}`, `/events` del customer ni en ningún schema de customer (test que serializa todos los schemas de customer y busca el texto de la nota).
- [x] Filtros y cursor con datos de la semilla; test de `sla_breached` con reloj inyectado.
- [x] Ampliar la semilla: solicitudes en distintos estados/edades para que el dashboard tenga datos.

## Criterios de aceptación
- Dos supervisores aprueban simultáneamente la misma solicitud: exactamente un 200 y un 409.
- Customer recibe 403 en cualquier ruta `/supervisor/*`; supervisor sobre su propia solicitud → 403 `SELF_APPROVAL_FORBIDDEN`.
- Rechazar con nota de 9 caracteres → 422.
- El email nunca aparece sin enmascarar en las respuestas del supervisor.

## Verificación
```bash
uv run pytest tests/test_supervisor_*.py -q && make check
curl -H "Authorization: Bearer $SUP1" "localhost:8000/api/v1/supervisor/refunds?status=PENDING_APPROVAL&sla_breached=true"
curl -X POST -H "Authorization: Bearer $SUP1" -H "If-Match: 1" localhost:8000/api/v1/supervisor/refunds/<id>/approve -d '{}'
```
