# Tasks: 03 · Crear solicitud (canal formulario)

**Feature**: `003-crear-solicitud` · **Estado**: ✅ Implementada · **Migrada de**: `docs/specs/03-crear-solicitud.md`

## Tareas

- [x] Tests unitarios: máquina de estados, hash/token (cambiar un byte del payload invalida), BR-12.
- [x] `RefundRepository` (lock, conteos), `RefundService`, schemas, rutas, handlers problem+json, middleware `trace_id`.
- [x] Tests API con Postgres real: flujo feliz, 422 inelegible (+ fila en `eligibility_checks`), token vencido/ajeno/alterado, supervisor → 403, **20 POST concurrentes** con la misma clave (`asyncio.gather`, sesiones separadas) → 1 solicitud, misma clave con otro cuerpo → 409, doble solicitud por compra → 422 `ACTIVE_REFUND_EXISTS`, cancelar y volver a solicitar dentro de ventana, cancelar con versión vieja → 409, BOLA en `/refunds/{id}`.
- [x] Test de contrato del §5.
