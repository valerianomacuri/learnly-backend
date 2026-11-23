# Tasks: 05 · Outbox y worker de pagos

**Feature**: `005-payout-worker` · **Estado**: ✅ Implementada · **Migrada de**: `docs/specs/05-payout-worker.md`

## Tareas

- [x] Tests primero (con gateway falso): éxito → `COMPLETED` + compra `REFUNDED` + ledger si créditos; fallo → reintentos con backoff (reloj inyectable) → `FAILED`; retry manual → `COMPLETED`; **doble ejecución del mismo evento no duplica ledger ni pago**; dos workers concurrentes procesan cada evento una sola vez (`SKIP LOCKED`).
- [x] Atomicidad: si falla la inserción del ledger, la compra no queda `REFUNDED` (rollback del pago local).
- [x] Aprobar escribe outbox en la misma transacción (test forzando error tras el UPDATE → ni estado ni outbox).
- [x] Worker CLI + servicio `docker compose` `worker` (perfil dev) y documentación en README.
- [x] Tras aprobar, la compra ya no es elegible (`ALREADY_REFUNDED`) — test de integración con el engine.
