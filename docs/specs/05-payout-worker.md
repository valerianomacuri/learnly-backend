# 05 · Outbox y worker de pagos

**Depende de:** 04 · **Refs:** challenge §4 (BR-22, BR-23), D7, §9 (consistencia, resiliencia)

## Objetivo
Aprobar y pagar son operaciones separadas. Al aprobar se escribe un evento de outbox en la misma transacción; un worker ejecuta el pago contra un gateway simulado con idempotencia y reintentos. **Hito: el backend queda completo y funcional sin LLM.**

## Alcance
- Dentro: outbox, worker, gateway simulado, `retry-payout`, efectos de `COMPLETED` (compra `REFUNDED`, acceso revocado, `credit_ledger`).
- Fuera: notificaciones por email (extensión opcional); métricas (13).

## Cambios

- **Outbox:** en `approve` (spec 04) insertar `outbox_events(topic="refund.payout", payload={refund_id}, status=PENDING, available_at=now)` y `refund_payouts(status=PENDING, idempotency_key=str(refund_id), amount, method)` en la **misma** transacción que el cambio de estado. Si falla algo, no queda ni estado ni evento.
- **Gateway** (`app/workers/gateway.py`): `PaymentGateway` protocolo `pay(idempotency_key, method, amount, currency) -> provider_ref`; `SimulatedGateway` determinista (éxito por defecto; fallos configurables por env/`FAIL_RATE` y por un parámetro en tests; misma `idempotency_key` devuelve el mismo `provider_ref` sin pagar de nuevo).
- **Worker** (`python -m app.workers.payouts`, entry point `payouts-worker`; `--once` para tests): bucle que toma eventos `PENDING` con `available_at <= now` usando `SELECT … FOR UPDATE SKIP LOCKED`; transiciona `APPROVED → PROCESSING`; llama al gateway; éxito → `COMPLETED` y efectos BR-23 **atómicos por pago** (`purchase.status = REFUNDED`, revocar acceso — modelar como `course_enrollments.revoked_at` o campo en `purchases`, decidir mínimo —, si `PLATFORM_CREDITS` insertar `credit_ledger(+amount)`); error transitorio → reintento con backoff exponencial (`attempts`, `available_at = now + base * 2^n`, máx. 3, `last_error`); agotados → `FAILED`, outbox `DEAD` (dead-letter). Cada paso escribe `refund_events` (`actor_type=SYSTEM`).
- **Idempotencia:** si el worker muere tras pagar y antes de confirmar, la reejecución con la misma `idempotency_key` no duplica el pago ni el crédito (el ledger referencia `refund_id` con restricción única `(refund_id)` para créditos de reembolso).
- **`POST /supervisor/refunds/{id}/retry-payout`** (`refund:retry_payout`): solo `FAILED` (409 si no); `FAILED → PROCESSING`, nuevo outbox, 202.
- Al ser el hito sin LLM, ampliar `GET /me` (saldo de créditos) y el detalle del customer para mostrar `payout.status`.

## Tareas
- [ ] Tests primero (con gateway falso): éxito → `COMPLETED` + compra `REFUNDED` + ledger si créditos; fallo → reintentos con backoff (reloj inyectable) → `FAILED`; retry manual → `COMPLETED`; **doble ejecución del mismo evento no duplica ledger ni pago**; dos workers concurrentes procesan cada evento una sola vez (`SKIP LOCKED`).
- [ ] Atomicidad: si falla la inserción del ledger, la compra no queda `REFUNDED` (rollback del pago local).
- [ ] Aprobar escribe outbox en la misma transacción (test forzando error tras el UPDATE → ni estado ni outbox).
- [ ] Worker CLI + servicio `docker compose` `worker` (perfil dev) y documentación en README.
- [ ] Tras aprobar, la compra ya no es elegible (`ALREADY_REFUNDED`) — test de integración con el engine.

## Criterios de aceptación
- Aprobar → worker → `COMPLETED`; `purchase.status = REFUNDED`; saldo de créditos sube solo para `PLATFORM_CREDITS`.
- Gateway que falla 3 veces → `FAILED`; `retry-payout` lo completa.
- Doble ejecución del mismo outbox no duplica pagos ni créditos.
- Ciclo completo demostrable solo con API (sin LLM): preview → crear → aprobar → worker → completado.

## Verificación
```bash
uv run pytest tests/test_payouts_*.py -q && make check
uv run payouts-worker --once
# humo extremo a extremo por curl (guion en docs/demo-sin-llm.md): crear → aprobar → worker → GET /refunds/{id} == COMPLETED
```
